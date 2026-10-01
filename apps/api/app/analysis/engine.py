"""El motor PUR (0051): compara l'estructura del plec amb el coneixement actual.

Rep dues vistes ja carregades (`TenderStructureView` i `CompanyView`) i retorna
un `AnalysisResult` amb files per requisit, per necessitat, per criteri, per
element d'abast, troballes i preguntes. Cap sessio, cap xarxa, cap LLM.

Principis que no es negocien:

* «no consta» es `unknown`, mai `does_not_satisfy`;
* l'historic dona context i preguntes, mai `satisfies`;
* una dada declarada pot satisfer, pero queda `not_verified` i demana revisio;
* cap `blocker_confirmed`: el motor nomes diu `blocker_potential`;
* `unknown` mai es `lost`; `lost` nomes amb una dada actual que ho demostri;
* cap recomanacio, cap puntuacio global, cap decisio de presentar-se.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.analysis import points as pts
from app.analysis import territory as terr
from app.analysis.fingerprint import (
    CriterionView,
    NeedView,
    RequirementView,
    ScopeItemView,
    TenderStructureView,
    canonical_json,
)
from app.analysis.registry import ExplanationCode as X
from app.analysis.registry import QuestionCode as Q
from app.analysis.registry import RuleCode as R
from app.knowledge.snapshot import (
    EvaluationResult,
    KnowledgeSnapshot,
    KnowledgeSource,
    evaluate_capability,
    evaluate_certification,
    evaluate_fact,
)
from app.knowledge.vocabulary import CAPABILITIES_BY_CODE, STANDARDS_BY_CODE
from app.models.enums import (
    AnalysisKnowledgeSource as KS,
)
from app.models.enums import (
    AssessmentVerification as V,
)
from app.models.enums import (
    CandidateOrigin,
    CandidateReadiness,
    CandidateType,
    CapabilityValueType,
    CriterionResult,
    DeliveryMode,
    FindingType,
    FitResult,
    NeedType,
    ProofKind,
    ProofStatus,
    QuestionAudience,
    RequirementCategory,
    RequirementObligation,
    RequirementTiming,
    TerritoryCompleteness,
    TerritoryVerdict,
)

PROFILE_CATEGORIES = frozenset(
    {
        RequirementCategory.ECONOMIC_SOLVENCY.value,
        RequirementCategory.TECHNICAL_SOLVENCY.value,
        RequirementCategory.EXPERIENCE.value,
        RequirementCategory.INSURANCE.value,
        RequirementCategory.PERSONNEL.value,
        RequirementCategory.PROFESSIONAL_QUALIFICATION.value,
        RequirementCategory.EQUIPMENT_OR_RESOURCES.value,
        RequirementCategory.BUSINESS_CLASSIFICATION.value,
        RequirementCategory.REGISTRATION.value,
    }
)
CONTRACT_CATEGORIES = frozenset(
    {
        RequirementCategory.CONTRACT_CONDITION.value,
        RequirementCategory.ADMINISTRATIVE_DOCUMENTATION.value,
        RequirementCategory.DEADLINE.value,
    }
)
#: Proves que el model pot demostrar; la resta queden `unknown`.
EVALUABLE_PROOFS = frozenset({ProofKind.CERTIFICATE.value, ProofKind.REGISTRY_INSCRIPTION.value})


# --------------------------------------------------------------------------- #
# Vista de l'empresa                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ServiceClaimView:
    service_id: str
    name: str
    service_line_code: str
    delivery_mode: str | None
    source: str
    coverage: terr.Coverage | None = None


@dataclass(frozen=True, slots=True)
class PartnerRoleView:
    role_id: str
    partner_id: str
    partner_name: str
    role: str
    service_line_code: str | None
    source: str
    coverage: terr.Coverage | None = None


@dataclass(frozen=True, slots=True)
class CompanyView:
    """La fotografia del coneixement mes el que el motor necessita amb id."""

    company_id: str
    snapshot: KnowledgeSnapshot
    services: tuple[ServiceClaimView, ...] = ()
    partner_roles: tuple[PartnerRoleView, ...] = ()

    @property
    def knowledge_fingerprint(self) -> str:
        return self.snapshot.fingerprint

    def services_of(self, service_line: str) -> tuple[ServiceClaimView, ...]:
        return tuple(s for s in self.services if s.service_line_code == service_line)

    def roles_of(self, service_line: str) -> tuple[PartnerRoleView, ...]:
        return tuple(r for r in self.partner_roles if r.service_line_code == service_line)


# --------------------------------------------------------------------------- #
# Sortides                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FitAssessmentOut:
    requirement_id: str
    fit_result: str
    knowledge_source: str
    proof_status: str
    territory_verdict: str
    verification_status: str
    rule_code: str
    explanation_code: str
    support: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NeedAssessmentOut:
    need_id: str
    requirement_id: str
    fit_result: str
    knowledge_source: str
    verification_status: str
    rule_code: str
    explanation_code: str
    support: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CriterionAssessmentOut:
    criterion_id: str
    criterion_result: str
    knowledge_source: str
    proof_status: str
    points_status: str
    max_points: float
    secured_points: float
    at_risk_points: float
    lost_points: float
    not_assessable_points: float
    rule_code: str
    explanation_code: str
    support: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CandidateOut:
    scope_item_id: str
    candidate_type: str
    origin: str
    readiness: str
    territory_verdict: str
    rule_code: str
    explanation_code: str
    company_service_id: str | None = None
    partner_role_id: str | None = None
    area_verdicts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FindingOut:
    finding_type: str
    rule_code: str
    explanation_code: str
    requirement_id: str | None = None
    criterion_id: str | None = None
    scope_item_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuestionOut:
    audience: str
    question_code: str
    rule_code: str
    requirement_id: str | None = None
    criterion_id: str | None = None
    scope_item_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        payload = canonical_json(
            [
                self.audience,
                self.question_code,
                self.requirement_id,
                self.criterion_id,
                self.scope_item_id,
                self.params,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    fit_assessments: tuple[FitAssessmentOut, ...]
    need_assessments: tuple[NeedAssessmentOut, ...]
    criterion_assessments: tuple[CriterionAssessmentOut, ...]
    candidates: tuple[CandidateOut, ...]
    findings: tuple[FindingOut, ...]
    questions: tuple[QuestionOut, ...]


# --------------------------------------------------------------------------- #
# Ajudants                                                                      #
# --------------------------------------------------------------------------- #


def _source(evaluation_source: KnowledgeSource) -> str:
    return {
        KnowledgeSource.CURRENT_VERIFIED: KS.CURRENT_VERIFIED.value,
        KnowledgeSource.CURRENT_DECLARED: KS.CURRENT_DECLARED.value,
        KnowledgeSource.HISTORIC: KS.HISTORICAL.value,
        KnowledgeSource.UNKNOWN: KS.NONE.value,
    }[evaluation_source]


def _verification(source: str) -> str:
    return {
        KS.CURRENT_VERIFIED.value: V.VERIFIED.value,
        KS.CURRENT_DECLARED.value: V.NOT_VERIFIED.value,
        KS.NOT_APPLICABLE.value: V.NOT_APPLICABLE.value,
    }.get(source, V.UNKNOWN.value)


def _combine_sources(sources: Iterable[str]) -> str:
    unique = {s for s in sources if s != KS.NOT_APPLICABLE.value}
    if not unique:
        return KS.NOT_APPLICABLE.value
    if len(unique) == 1:
        return next(iter(unique))
    return KS.MIXED.value


def _coverage_from_claim(coverage: dict[str, Any] | None) -> terr.Coverage | None:
    """La cobertura canonica d'una `Claim` (0047) com a entrada del comparador."""
    if coverage is None:
        return None
    return terr.Coverage(
        completeness=str(coverage.get("completeness", "unknown")),
        areas=tuple(
            terr.Area(
                kind=str(a.get("kind")),
                label=a.get("label"),
                code=a.get("code"),
                code_system=a.get("code_system"),
            )
            for a in coverage.get("areas", [])
        ),
    )


def _areas(requirement: RequirementView) -> tuple[terr.Area, ...]:
    return tuple(
        terr.Area(
            kind=t.kind,
            label=t.label,
            code=t.code,
            code_system=t.code_system,
            qualifier=t.qualifier,
            site_key=t.site_key,
        )
        for t in requirement.territories
    )


#: Claus d'un `value_json` que porten el valor exigit, per ordre de preferencia.
VALUE_KEYS = (
    "value",
    "minutes",
    "hours",
    "channels",
    "numbers",
    "licences",
    "count",
    "days",
    "months",
    "gbps",
    "mbps",
)


def _single_number(value_json: dict[str, Any] | None) -> Decimal | None:
    """El valor numeric exigit: una clau coneguda o, si no, l'unic nombre que hi ha."""
    if not value_json:
        return None

    def number(v: Any) -> Decimal | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        try:
            return Decimal(str(v))
        except InvalidOperation:
            return None

    for key in VALUE_KEYS:
        if key in value_json and number(value_json[key]) is not None:
            return number(value_json[key])
    numbers = [n for n in (number(v) for v in value_json.values()) if n is not None]
    return numbers[0] if len(numbers) == 1 else None


def _need_required(need: NeedView) -> Decimal | str | None:
    code = need.capability_code
    definition = CAPABILITIES_BY_CODE.get(code or "")
    if definition is None:
        return None
    if definition.value_type == CapabilityValueType.BOOLEAN:
        return None
    if definition.value_type == CapabilityValueType.NUMBER:
        return _single_number(need.value_json)
    level = (need.value_json or {}).get("level")
    return str(level) if level is not None else None


# --------------------------------------------------------------------------- #
# Necessitats                                                                   #
# --------------------------------------------------------------------------- #


def assess_need(
    need: NeedView, requirement: RequirementView, company: CompanyView
) -> NeedAssessmentOut:
    if need.capability_code is None:
        return NeedAssessmentOut(
            need_id=need.id,
            requirement_id=requirement.id,
            fit_result=FitResult.UNKNOWN.value,
            knowledge_source=KS.NONE.value,
            verification_status=V.UNKNOWN.value,
            rule_code=R.NEED_UNMAPPED,
            explanation_code=X.CAPABILITY_UNMAPPED,
            support={
                "unmapped_capability": need.unmapped_capability,
                "comparator": need.comparator,
            },
        )
    definition = CAPABILITIES_BY_CODE[need.capability_code]
    required = _need_required(need)
    if definition.value_type != CapabilityValueType.BOOLEAN and required is None:
        return NeedAssessmentOut(
            need_id=need.id,
            requirement_id=requirement.id,
            fit_result=FitResult.UNKNOWN.value,
            knowledge_source=KS.NONE.value,
            verification_status=V.UNKNOWN.value,
            rule_code=R.NEED_CAPABILITY,
            explanation_code=X.NEED_VALUE_NOT_COMPARABLE,
            support={"capability_code": need.capability_code, "value_json": need.value_json},
        )
    evaluation = evaluate_capability(company.snapshot, need.capability_code, required)
    source = _source(evaluation.source)
    if evaluation.result == EvaluationResult.SATISFIES:
        fit = FitResult.SATISFIES.value
        explanation = (
            X.CAPABILITY_SUPPORTED_WITH_LIMITS
            if evaluation.reason == "satisfied_with_limits"
            else X.CAPABILITY_SUPPORTED
        )
    elif evaluation.result == EvaluationResult.DOES_NOT_SATISFY:
        fit = FitResult.DOES_NOT_SATISFY.value
        entry = company.snapshot.capabilities.get(need.capability_code)
        failing = [
            c for c in (entry.claims if entry else ()) if c.record_id in evaluation.record_ids
        ]
        explanation = (
            X.CAPABILITY_NOT_SUPPORTED
            if any(c.value["status"] == "not_supported" for c in failing)
            else X.CAPABILITY_BELOW_REQUIRED
        )
    else:
        fit = FitResult.UNKNOWN.value
        explanation = (
            X.CAPABILITY_HISTORIC_ONLY
            if evaluation.source == KnowledgeSource.HISTORIC
            else X.CAPABILITY_UNKNOWN
        )
    return NeedAssessmentOut(
        need_id=need.id,
        requirement_id=requirement.id,
        fit_result=fit,
        knowledge_source=source,
        verification_status=_verification(source)
        if fit != FitResult.UNKNOWN.value
        else V.UNKNOWN.value,
        rule_code=R.NEED_CAPABILITY,
        explanation_code=explanation,
        support={
            "capability_code": need.capability_code,
            "required": str(required) if required is not None else None,
            "comparator": need.comparator,
            "service_line": definition.service_line_code,
            "record_ids": list(evaluation.record_ids),
            "reason": evaluation.reason,
        },
    )


# --------------------------------------------------------------------------- #
# Proves                                                                        #
# --------------------------------------------------------------------------- #


def _proof_evidence(kind: str, requirement: RequirementView, company: CompanyView) -> bool | None:
    """Cert/fals si el model pot demostrar la prova; `None` si no pot."""
    snapshot = company.snapshot
    value = requirement.value_json or {}
    if kind == ProofKind.CERTIFICATE.value:
        standard = value.get("standard")
        entry = snapshot.certifications.get(standard) if standard else None
        if entry is None or entry.effective is None:
            return None
        return bool(entry.effective.has_evidence)
    if kind == ProofKind.REGISTRY_INSCRIPTION.value:
        fact_key = value.get("operating_fact")
        entry = snapshot.facts.get(fact_key) if fact_key else None
        if entry is None or entry.effective is None:
            return None
        return bool(entry.effective.has_evidence)
    return None


def assess_proof(requirement: RequirementView, company: CompanyView) -> tuple[str, dict[str, Any]]:
    if requirement.proof_json is None:
        return ProofStatus.UNKNOWN.value, {"items": None}
    if not requirement.proof_json:
        return ProofStatus.NOT_REQUIRED.value, {"items": []}
    items = [p for p in requirement.proof_json if p.get("mandatory", True)] or list(
        requirement.proof_json
    )
    verdicts: list[dict[str, Any]] = []
    for item in items:
        kind = str(item.get("kind"))
        evidence = _proof_evidence(kind, requirement, company)
        if kind not in EVALUABLE_PROOFS or evidence is None:
            status = ProofStatus.UNKNOWN.value
        else:
            status = ProofStatus.SUFFICIENT.value if evidence else ProofStatus.MISSING.value
        verdicts.append({"kind": kind, "status": status})
    statuses = {v["status"] for v in verdicts}
    if statuses == {ProofStatus.SUFFICIENT.value}:
        overall = ProofStatus.SUFFICIENT.value
    elif ProofStatus.SUFFICIENT.value in statuses:
        overall = ProofStatus.PARTIAL.value
    elif ProofStatus.MISSING.value in statuses:
        overall = ProofStatus.MISSING.value
    else:
        overall = ProofStatus.UNKNOWN.value
    return overall, {"items": verdicts}


# --------------------------------------------------------------------------- #
# Territori d'un requisit                                                       #
# --------------------------------------------------------------------------- #


def _service_lines_of(requirement: RequirementView, tender: TenderStructureView) -> set[str]:
    """Les linies de servei que toca el requisit: per les necessitats mapades o,
    si no en te, pels elements d'abast dels seus blocs."""
    lines = {
        CAPABILITIES_BY_CODE[n.capability_code].service_line_code
        for n in requirement.needs
        if n.capability_code and n.capability_code in CAPABILITIES_BY_CODE
    }
    if lines:
        return lines
    if requirement.block_codes:
        return {
            item.service_line_code
            for item in tender.scope_items
            if item.block_code in requirement.block_codes
        }
    return set()


def coverage_for_requirement(
    requirement: RequirementView, tender: TenderStructureView, company: CompanyView
) -> tuple[terr.Coverage | None, str | None]:
    """Quina cobertura de l'empresa es compara: la del fet que el requisit llegeix
    o, si no, la del servei actual de l'unica linia que el requisit toca."""
    fact_key = (requirement.value_json or {}).get("operating_fact")
    if fact_key:
        entry = company.snapshot.facts.get(fact_key)
        claim = entry.effective if entry else None
        return (_coverage_from_claim(claim.coverage) if claim else None), f"fact:{fact_key}"
    lines = _service_lines_of(requirement, tender)
    if len(lines) == 1:
        line = next(iter(lines))
        services = company.services_of(line)
        if services:
            return services[0].coverage, f"service:{line}"
    return None, None


def _territory_explanation(comparison: terr.TerritoryComparison) -> str:
    if comparison.verdict == TerritoryVerdict.NOT_APPLICABLE.value:
        return X.TERRITORY_NOT_APPLICABLE
    if comparison.verdict == TerritoryVerdict.MATCHED.value:
        return X.TERRITORY_MATCHED
    if comparison.verdict == TerritoryVerdict.UNMATCHED.value:
        return X.TERRITORY_UNMATCHED
    if comparison.reason == "list_unavailable":
        return X.TERRITORY_LIST_UNAVAILABLE
    return X.TERRITORY_UNKNOWN


def assess_territory(
    requirement: RequirementView, tender: TenderStructureView, company: CompanyView
) -> tuple[terr.TerritoryComparison, str | None]:
    coverage, basis = coverage_for_requirement(requirement, tender, company)
    return (
        terr.compare_territories(
            territories=_areas(requirement),
            completeness=requirement.territory_completeness,
            match_mode=requirement.territory_match_mode,
            coverage=coverage,
        ),
        basis,
    )


# --------------------------------------------------------------------------- #
# Requisits                                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Core:
    fit: str
    source: str
    rule: str
    explanation: str
    support: dict[str, Any] = field(default_factory=dict)


def _fact_core(requirement: RequirementView, company: CompanyView) -> _Core:
    key = requirement.value_json["operating_fact"]
    expected = bool(requirement.value_json.get("expected", True))
    evaluation = evaluate_fact(company.snapshot, key, expected)
    source = _source(evaluation.source)
    support = {
        "operating_fact": key,
        "expected": expected,
        "record_ids": list(evaluation.record_ids),
    }
    if evaluation.result == EvaluationResult.SATISFIES:
        explanation = (
            X.FACT_TRUE_VERIFIED if source == KS.CURRENT_VERIFIED.value else X.FACT_TRUE_DECLARED
        )
        return _Core(FitResult.SATISFIES.value, source, R.FACT_REQUIREMENT, explanation, support)
    if evaluation.result == EvaluationResult.DOES_NOT_SATISFY:
        return _Core(
            FitResult.DOES_NOT_SATISFY.value,
            source,
            R.FACT_REQUIREMENT,
            X.FACT_CONTRADICTS,
            support,
        )
    explanation = (
        X.FACT_HISTORIC_ONLY if evaluation.source == KnowledgeSource.HISTORIC else X.FACT_UNKNOWN
    )
    return _Core(FitResult.UNKNOWN.value, source, R.FACT_REQUIREMENT, explanation, support)


def _certification_core(requirement: RequirementView, company: CompanyView) -> _Core:
    value = requirement.value_json or {}
    standard = value.get("standard")
    if not standard or standard not in STANDARDS_BY_CODE:
        return _Core(
            FitResult.UNKNOWN.value,
            KS.NONE.value,
            R.CERTIFICATION_REQUIREMENT,
            X.CERT_STANDARD_NOT_STRUCTURED,
            {"standard": standard},
        )
    evaluation = evaluate_certification(company.snapshot, standard, value.get("level"))
    source = _source(evaluation.source)
    support = {
        "standard": standard,
        "level_required": value.get("level"),
        "record_ids": list(evaluation.record_ids),
        "reason": evaluation.reason,
    }
    if evaluation.result == EvaluationResult.SATISFIES:
        explanation = (
            X.CERT_CURRENT_VERIFIED
            if source == KS.CURRENT_VERIFIED.value
            else X.CERT_CURRENT_DECLARED
        )
        return _Core(
            FitResult.SATISFIES.value, source, R.CERTIFICATION_REQUIREMENT, explanation, support
        )
    if evaluation.result == EvaluationResult.DOES_NOT_SATISFY:
        return _Core(
            FitResult.DOES_NOT_SATISFY.value,
            source,
            R.CERTIFICATION_REQUIREMENT,
            X.CERT_LEVEL_BELOW,
            support,
        )
    explanation = (
        X.CERT_HISTORIC_ONLY if evaluation.source == KnowledgeSource.HISTORIC else X.CERT_UNKNOWN
    )
    return _Core(FitResult.UNKNOWN.value, source, R.CERTIFICATION_REQUIREMENT, explanation, support)


def _needs_core(requirement: RequirementView, needs: list[NeedAssessmentOut]) -> _Core:
    minimum_ids = {n.id for n in requirement.needs if n.need_type == NeedType.MINIMUM.value}
    minimum = [n for n in needs if n.need_id in minimum_ids]
    support = {"needs": [{"need_id": n.need_id, "fit": n.fit_result} for n in minimum]}
    if not minimum:
        return _Core(
            FitResult.UNKNOWN.value,
            KS.NONE.value,
            R.CAPABILITY_NEEDS_REQUIREMENT,
            X.NEEDS_NONE_MINIMUM,
            support,
        )
    results = {n.fit_result for n in minimum}
    source = _combine_sources(n.knowledge_source for n in minimum)
    if FitResult.DOES_NOT_SATISFY.value in results:
        return _Core(
            FitResult.DOES_NOT_SATISFY.value,
            source,
            R.CAPABILITY_NEEDS_REQUIREMENT,
            X.NEEDS_CONTRADICTED,
            support,
        )
    if results == {FitResult.SATISFIES.value}:
        return _Core(
            FitResult.SATISFIES.value,
            source,
            R.CAPABILITY_NEEDS_REQUIREMENT,
            X.NEEDS_ALL_SATISFIED,
            support,
        )
    unknown_sources = _combine_sources(
        n.knowledge_source for n in minimum if n.fit_result == FitResult.UNKNOWN.value
    )
    return _Core(
        FitResult.UNKNOWN.value,
        unknown_sources,
        R.CAPABILITY_NEEDS_REQUIREMENT,
        X.NEEDS_UNKNOWN,
        support,
    )


def assess_requirement(
    requirement: RequirementView, tender: TenderStructureView, company: CompanyView
) -> tuple[FitAssessmentOut, list[NeedAssessmentOut], terr.TerritoryComparison]:
    needs = [assess_need(n, requirement, company) for n in requirement.needs]
    value = requirement.value_json or {}

    if value.get("operating_fact"):
        core = _fact_core(requirement, company)
    elif requirement.category == RequirementCategory.CERTIFICATION.value:
        core = _certification_core(requirement, company)
    elif requirement.needs:
        core = _needs_core(requirement, needs)
    elif requirement.category in PROFILE_CATEGORIES:
        core = _Core(
            FitResult.UNKNOWN.value,
            KS.NONE.value,
            R.PROFILE_DATA_REQUIREMENT,
            X.PROFILE_DATA_NOT_IN_SNAPSHOT,
        )
    elif requirement.category in CONTRACT_CATEGORIES:
        core = _Core(
            FitResult.NOT_APPLICABLE.value,
            KS.NOT_APPLICABLE.value,
            R.CONTRACT_CONDITION_REQUIREMENT,
            X.CONTRACT_CONDITION_NOT_COMPARABLE,
        )
    else:
        core = _Core(
            FitResult.UNKNOWN.value,
            KS.NONE.value,
            R.UNSTRUCTURED_REQUIREMENT,
            X.NO_STRUCTURE_TO_COMPARE,
        )

    comparison, basis = assess_territory(requirement, tender, company)
    proof, proof_support = assess_proof(requirement, company)

    fit, explanation = core.fit, core.explanation
    if fit == FitResult.SATISFIES.value and comparison.verdict == TerritoryVerdict.UNMATCHED.value:
        fit = FitResult.DOES_NOT_SATISFY.value
        explanation = (
            X.FACT_TRUE_TERRITORY_UNMATCHED
            if core.rule == R.FACT_REQUIREMENT
            else X.TERRITORY_UNMATCHED
        )
    elif fit == FitResult.SATISFIES.value and proof in (
        ProofStatus.MISSING.value,
        ProofStatus.PARTIAL.value,
    ):
        fit = FitResult.UNKNOWN.value
        explanation = X.PROOF_MISSING_FOR_CLAIM

    if fit == FitResult.NOT_APPLICABLE.value:
        verification = V.NOT_APPLICABLE.value
    elif core.source in (KS.CURRENT_VERIFIED.value, KS.CURRENT_DECLARED.value):
        verification = _verification(core.source)
    else:
        verification = V.UNKNOWN.value
    support = {
        **core.support,
        "core_fit": core.fit,
        "territory": {
            "rule": R.TERRITORY_IDENTITY,
            "explanation": _territory_explanation(comparison),
            "verdict": comparison.verdict,
            "reason": comparison.reason,
            "basis": basis,
            "areas": comparison.as_dicts(),
        },
        "proof": {"rule": R.PROOF_ITEMS, **proof_support},
    }
    assessment = FitAssessmentOut(
        requirement_id=requirement.id,
        fit_result=fit,
        knowledge_source=core.source,
        proof_status=proof,
        territory_verdict=comparison.verdict,
        verification_status=verification,
        rule_code=core.rule,
        explanation_code=explanation,
        support=support,
    )
    return assessment, needs, comparison


# --------------------------------------------------------------------------- #
# Criteris i punts                                                              #
# --------------------------------------------------------------------------- #


def _points_kwargs(buckets: pts.Buckets) -> dict[str, float]:
    return {f"{bucket}_points": value for bucket, value in buckets.as_dict().items()}


def _bucket_for(fit: str, source: str, explanation: str | None = None) -> str:
    """Un compromis automatic: `secured` nomes amb dada actual verificada; `lost`
    nomes amb una incompatibilitat actual explicita (la solucio declara que NO ho
    fa); tota la resta (declarat, per sota del valor d'avui, desconegut, historic)
    es `at_risk`: un compromis d'oferta encara obert. Una capacitat actual inferior
    a un tram superior no el fa perdre: es pot ampliar com a compromis."""
    if fit == FitResult.SATISFIES.value:
        return "secured" if source == KS.CURRENT_VERIFIED.value else "at_risk"
    if fit == FitResult.DOES_NOT_SATISFY.value and explanation == X.CAPABILITY_NOT_SUPPORTED:
        return "lost"
    return "at_risk"


def _threshold_of(need: NeedView) -> Any:
    number = _single_number(need.value_json)
    if number is not None:
        return float(number)
    level = (need.value_json or {}).get("level")
    return level


def _criterion_result(buckets: pts.Buckets, max_points: float) -> str:
    tol = pts.TOLERANCE
    if max_points <= tol:
        return CriterionResult.NOT_ASSESSABLE.value
    if abs(buckets.secured - max_points) <= tol:
        return CriterionResult.ACHIEVABLE.value
    if abs(buckets.lost - max_points) <= tol:
        return CriterionResult.NOT_ACHIEVABLE.value
    if abs(buckets.not_assessable - max_points) <= tol:
        return CriterionResult.NOT_ASSESSABLE.value
    return CriterionResult.NEEDS_REVIEW.value


def _points_explanation(status: str) -> str:
    return {
        "secured": X.CRITERION_SECURED,
        "at_risk": X.CRITERION_AT_RISK,
        "lost": X.CRITERION_LOST,
        "mixed": X.CRITERION_MIXED,
        "not_assessable": X.CRITERION_NOT_ASSESSABLE_UNKNOWN,
    }[status]


def assess_criterion(
    criterion: CriterionView,
    tender: TenderStructureView,
    needs_by_criterion: dict[str, list[tuple[NeedView, NeedAssessmentOut]]],
    fits_by_key: dict[str, tuple[RequirementView, FitAssessmentOut]],
) -> tuple[CriterionAssessmentOut, list[QuestionOut]]:
    max_points = float(criterion.points or 0.0)
    questions: list[QuestionOut] = []
    scored = [
        (need, out)
        for need, out in needs_by_criterion.get(criterion.key, [])
        if need.need_type == NeedType.SCORED_IMPROVEMENT.value
    ]
    linked = [
        fits_by_key[key]
        for key in tender_requirement_keys_for(criterion, tender)
        if key in fits_by_key
    ]
    proof = ProofStatus.NOT_REQUIRED.value
    support: dict[str, Any] = {"max_points": max_points}

    if criterion.evaluation_type == "judgment":
        demo = [
            out
            for need, out in needs_by_criterion.get(criterion.key, [])
            if need.need_type == NeedType.DEMO.value
        ]
        buckets = pts.all_in("not_assessable", max_points)
        support["demo_needs"] = [{"need_id": o.need_id, "fit": o.fit_result} for o in demo]
        return (
            CriterionAssessmentOut(
                criterion_id=criterion.id,
                criterion_result=CriterionResult.NOT_ASSESSABLE.value,
                knowledge_source=_combine_sources(o.knowledge_source for o in demo)
                if demo
                else KS.NOT_APPLICABLE.value,
                proof_status=proof,
                points_status=pts.points_status(buckets, max_points),
                max_points=max_points,
                **_points_kwargs(buckets),
                rule_code=R.CRITERION_JUDGEMENT,
                explanation_code=X.CRITERION_NOT_ASSESSABLE_JUDGEMENT,
                support=support,
            ),
            questions,
        )
    if criterion.criterion_type == "price":
        buckets = pts.all_in("not_assessable", max_points)
        return (
            CriterionAssessmentOut(
                criterion_id=criterion.id,
                criterion_result=CriterionResult.NOT_ASSESSABLE.value,
                knowledge_source=KS.NOT_APPLICABLE.value,
                proof_status=proof,
                points_status=pts.points_status(buckets, max_points),
                max_points=max_points,
                **_points_kwargs(buckets),
                rule_code=R.CRITERION_PRICE,
                explanation_code=X.CRITERION_NOT_ASSESSABLE_PRICE,
                support=support,
            ),
            questions,
        )

    if scored:
        rule = R.CRITERION_FROM_NEEDS
        sources = [out.knowledge_source for _, out in scored]
        support["needs"] = [
            {"need_id": out.need_id, "fit": out.fit_result, "threshold": _threshold_of(need)}
            for need, out in scored
        ]
        if criterion.tranches and len(criterion.tranches) == len(scored):
            by_threshold = {_threshold_of(need): out for need, out in scored}

            def outcome(threshold: Any) -> str:
                out = by_threshold.get(
                    float(threshold) if isinstance(threshold, (int, float)) else threshold
                )
                if out is None:
                    return "at_risk"
                return _bucket_for(out.fit_result, out.knowledge_source, out.explanation_code)

            buckets = pts.allocate_tranches(max_points, criterion.tranches, outcome)
        else:
            buckets_of = [
                _bucket_for(out.fit_result, out.knowledge_source, out.explanation_code)
                for _, out in scored
            ]
            if "lost" in buckets_of:
                bucket = "lost"
            elif all(b == "secured" for b in buckets_of):
                bucket = "secured"
            else:
                bucket = "at_risk"
            buckets = pts.all_in(bucket, max_points)
        source = _combine_sources(sources)
    elif linked:
        rule = R.CRITERION_FROM_REQUIREMENT
        requirement, fit = linked[0]
        proof = fit.proof_status
        support["requirement_id"] = requirement.id
        support["requirement_fit"] = fit.fit_result
        source = fit.knowledge_source
        if fit.fit_result == FitResult.DOES_NOT_SATISFY.value:
            if criterion.timing == RequirementTiming.AT_SUBMISSION.value:
                buckets = pts.all_in("lost", max_points)
            else:
                buckets = pts.all_in("at_risk", max_points)
                support["timing_not_conclusive"] = criterion.timing
                questions.append(
                    QuestionOut(
                        audience=QuestionAudience.BUYER.value,
                        question_code=Q.BUYER_CLARIFY_CRITERION_TIMING,
                        rule_code=rule,
                        criterion_id=criterion.id,
                        params={"name": criterion.name},
                    )
                )
        else:
            buckets = pts.all_in(_bucket_for(fit.fit_result, fit.knowledge_source), max_points)
    else:
        # Compromis automatic sense estructura comparable: obert, en risc. Mai perdut
        # ni assegurat per absencia de dada.
        buckets = pts.all_in("at_risk", max_points)
        return (
            CriterionAssessmentOut(
                criterion_id=criterion.id,
                criterion_result=_criterion_result(buckets, max_points),
                knowledge_source=KS.NONE.value,
                proof_status=proof,
                points_status=pts.points_status(buckets, max_points),
                max_points=max_points,
                **_points_kwargs(buckets),
                rule_code=R.CRITERION_UNSTRUCTURED,
                explanation_code=X.CRITERION_OPEN_COMMITMENT,
                support=support,
            ),
            questions,
        )

    status = pts.points_status(buckets, max_points)
    explanation = _points_explanation(status)
    if "timing_not_conclusive" in support:
        explanation = X.CRITERION_TIMING_NOT_CONCLUSIVE
    return (
        CriterionAssessmentOut(
            criterion_id=criterion.id,
            criterion_result=_criterion_result(buckets, max_points),
            knowledge_source=source,
            proof_status=proof,
            points_status=status,
            max_points=max_points,
            **_points_kwargs(buckets),
            rule_code=rule,
            explanation_code=explanation,
            support=support,
        ),
        questions,
    )


def tender_requirement_keys_for(criterion: CriterionView, tender: TenderStructureView) -> list[str]:
    return [r.key for r in tender.requirements if criterion.key in r.criterion_keys]


# --------------------------------------------------------------------------- #
# Vies de prestacio                                                             #
# --------------------------------------------------------------------------- #


def scope_item_territory_context(
    item: ScopeItemView, tender: TenderStructureView
) -> tuple[tuple[terr.Area, ...], str | None, str, tuple[str, ...]]:
    """Els territoris que corresponen a un element d'abast, nomes per correspondencia
    DEMOSTRABLE amb l'estructura actual: un requisit tecnic del mateix bloc (o de tot
    el contracte) amb almenys una necessitat mapada a una capacitat de la MATEIXA
    linia de servei que l'element. Compartir el bloc no basta: un bloc te elements
    independents, i el territori desconegut dels FTTH no es el del radioenllac.

    Sense cap correspondencia es retorna `None` com a completesa: el candidat queda
    `unknown` sense cap area. Extensio futura: una taula explicita
    `requirement_scope_links` faria la correspondencia sense aquesta heuristica.
    """
    relevant: list[RequirementView] = []
    for r in tender.requirements:
        if r.category != RequirementCategory.TECHNICAL_REQUIREMENT.value:
            continue
        if r.block_codes and item.block_code not in r.block_codes:
            continue
        lines = {
            CAPABILITIES_BY_CODE[n.capability_code].service_line_code
            for n in r.needs
            if n.capability_code and n.capability_code in CAPABILITIES_BY_CODE
        }
        if item.service_line_code in lines:
            relevant.append(r)
    if not relevant:
        return (), None, "all", ()
    basis = tuple(sorted(r.key for r in relevant))
    completeness = {r.territory_completeness for r in relevant}
    if completeness == {TerritoryCompleteness.NOT_APPLICABLE.value}:
        return (), TerritoryCompleteness.NOT_APPLICABLE.value, "all", basis
    if TerritoryCompleteness.UNKNOWN.value in completeness:
        overall = TerritoryCompleteness.UNKNOWN.value
    elif TerritoryCompleteness.PARTIAL.value in completeness:
        overall = TerritoryCompleteness.PARTIAL.value
    else:
        overall = TerritoryCompleteness.COMPLETE.value
    areas: dict[tuple, terr.Area] = {}
    for r in relevant:
        for t in r.territories:
            areas.setdefault(
                t.identity(),
                terr.Area(t.kind, t.label, t.code, t.code_system, t.qualifier, t.site_key),
            )
    return tuple(areas.values()), overall, "all", basis


def _readiness(verdict: str, default: str) -> str:
    if verdict in (TerritoryVerdict.MATCHED.value, TerritoryVerdict.NOT_APPLICABLE.value):
        return default
    if verdict == TerritoryVerdict.UNMATCHED.value:
        return CandidateReadiness.UNAVAILABLE.value
    return CandidateReadiness.PENDING_COVERAGE.value


def assess_delivery(
    item: ScopeItemView, tender: TenderStructureView, company: CompanyView
) -> tuple[list[CandidateOut], list[FindingOut], list[QuestionOut]]:
    areas, completeness, mode, _basis = scope_item_territory_context(item, tender)
    rule = R.DELIVERY_FROM_SERVICES
    candidates: list[CandidateOut] = []
    findings: list[FindingOut] = []
    questions: list[QuestionOut] = []

    def verdict_for(coverage: terr.Coverage | None) -> terr.TerritoryComparison:
        if completeness is None:
            return terr.TerritoryComparison(
                TerritoryVerdict.UNKNOWN.value, "no_requirement_correspondence"
            )
        return terr.compare_territories(
            territories=areas, completeness=completeness, match_mode=mode, coverage=coverage
        )

    services = company.services_of(item.service_line_code)
    if not services:
        comparison = verdict_for(None)
        candidates.append(
            CandidateOut(
                scope_item_id=item.id,
                candidate_type=CandidateType.SUBCONTRACT_UNKNOWN.value,
                origin=CandidateOrigin.CURRENT_KNOWLEDGE.value,
                readiness=CandidateReadiness.PENDING_VALIDATION.value,
                territory_verdict=comparison.verdict,
                rule_code=rule,
                explanation_code=X.DELIVERY_NO_SERVICE,
                area_verdicts=comparison.as_dicts(),
            )
        )
        findings.append(
            FindingOut(
                FindingType.DELIVERY_GAP.value,
                R.DELIVERY_GAP,
                X.NO_AVAILABLE_DELIVERY,
                scope_item_id=item.id,
                details={"service_line": item.service_line_code},
            )
        )
        questions.append(
            QuestionOut(
                QuestionAudience.COMPANY.value,
                Q.COMPANY_DECLARE_DELIVERY_MODE,
                rule,
                scope_item_id=item.id,
                params={"service_line": item.service_line_code},
            )
        )
        return candidates, findings, questions

    for service in services:
        if service.delivery_mode == DeliveryMode.OWN.value:
            comparison = verdict_for(service.coverage)
            candidates.append(
                CandidateOut(
                    scope_item_id=item.id,
                    candidate_type=CandidateType.OWN_CURRENT.value,
                    origin=CandidateOrigin.CURRENT_KNOWLEDGE.value,
                    readiness=_readiness(comparison.verdict, CandidateReadiness.AVAILABLE.value),
                    territory_verdict=comparison.verdict,
                    rule_code=rule,
                    explanation_code=X.DELIVERY_OWN_CURRENT,
                    company_service_id=service.service_id,
                    area_verdicts=comparison.as_dicts(),
                )
            )
        elif service.delivery_mode in (
            DeliveryMode.PARTNER.value,
            DeliveryMode.SUBCONTRACT.value,
            DeliveryMode.RESALE.value,
        ):
            roles = company.roles_of(item.service_line_code)
            if not roles:
                comparison = verdict_for(None)
                candidates.append(
                    CandidateOut(
                        scope_item_id=item.id,
                        candidate_type=CandidateType.SUBCONTRACT_UNKNOWN.value,
                        origin=CandidateOrigin.CURRENT_KNOWLEDGE.value,
                        readiness=CandidateReadiness.PENDING_PARTNER.value,
                        territory_verdict=comparison.verdict,
                        rule_code=rule,
                        explanation_code=X.DELIVERY_NO_PARTNER_ROLE,
                        company_service_id=service.service_id,
                        area_verdicts=comparison.as_dicts(),
                    )
                )
                findings.append(
                    FindingOut(
                        FindingType.DELIVERY_GAP.value,
                        R.DELIVERY_GAP,
                        X.NO_AVAILABLE_DELIVERY,
                        scope_item_id=item.id,
                        details={
                            "service_line": item.service_line_code,
                            "delivery_mode": service.delivery_mode,
                        },
                    )
                )
                questions.append(
                    QuestionOut(
                        QuestionAudience.COMPANY.value,
                        Q.COMPANY_DECLARE_PARTNER,
                        rule,
                        scope_item_id=item.id,
                        params={"service_line": item.service_line_code},
                    )
                )
            for role in roles:
                comparison = verdict_for(role.coverage)
                candidates.append(
                    CandidateOut(
                        scope_item_id=item.id,
                        candidate_type=CandidateType.PARTNER_CURRENT.value,
                        origin=CandidateOrigin.CURRENT_KNOWLEDGE.value,
                        readiness=_readiness(
                            comparison.verdict, CandidateReadiness.AVAILABLE.value
                        ),
                        territory_verdict=comparison.verdict,
                        rule_code=rule,
                        explanation_code=X.DELIVERY_PARTNER_CURRENT,
                        company_service_id=service.service_id,
                        partner_role_id=role.role_id,
                        area_verdicts=comparison.as_dicts(),
                    )
                )
        else:
            comparison = verdict_for(service.coverage)
            candidates.append(
                CandidateOut(
                    scope_item_id=item.id,
                    candidate_type=CandidateType.SUBCONTRACT_UNKNOWN.value,
                    origin=CandidateOrigin.CURRENT_KNOWLEDGE.value,
                    readiness=CandidateReadiness.PENDING_VALIDATION.value,
                    territory_verdict=comparison.verdict,
                    rule_code=rule,
                    explanation_code=X.DELIVERY_MODE_UNKNOWN,
                    company_service_id=service.service_id,
                    area_verdicts=comparison.as_dicts(),
                )
            )
            questions.append(
                QuestionOut(
                    QuestionAudience.COMPANY.value,
                    Q.COMPANY_DECLARE_DELIVERY_MODE,
                    rule,
                    scope_item_id=item.id,
                    params={"service_line": item.service_line_code},
                )
            )

    if not any(c.readiness == CandidateReadiness.AVAILABLE.value for c in candidates) and all(
        c.readiness == CandidateReadiness.UNAVAILABLE.value for c in candidates
    ):
        findings.append(
            FindingOut(
                FindingType.DELIVERY_GAP.value,
                R.DELIVERY_GAP,
                X.NO_AVAILABLE_DELIVERY,
                scope_item_id=item.id,
                details={"service_line": item.service_line_code},
            )
        )
    if any(c.readiness == CandidateReadiness.PENDING_COVERAGE.value for c in candidates):
        questions.append(
            QuestionOut(
                QuestionAudience.COMPANY.value,
                Q.COMPANY_CONFIRM_COVERAGE,
                rule,
                scope_item_id=item.id,
                params={"service_line": item.service_line_code},
            )
        )
    return candidates, findings, questions


# --------------------------------------------------------------------------- #
# Troballes i preguntes d'un requisit                                           #
# --------------------------------------------------------------------------- #


def requirement_findings(
    requirement: RequirementView,
    fit: FitAssessmentOut,
    needs: list[NeedAssessmentOut],
    company: CompanyView,
) -> tuple[list[FindingOut], list[QuestionOut]]:
    findings: list[FindingOut] = []
    questions: list[QuestionOut] = []
    rid = requirement.id
    mandatory = requirement.obligation == RequirementObligation.MANDATORY.value
    details = {
        "fit": fit.fit_result,
        "explanation": fit.explanation_code,
        "category": requirement.category,
    }

    if fit.fit_result == FitResult.DOES_NOT_SATISFY.value and mandatory:
        # La incompatibilitat queda al fit_result; la consequencia depen de QUAN s'exigeix.
        if requirement.timing == RequirementTiming.AT_SUBMISSION.value:
            findings.append(
                FindingOut(
                    FindingType.BLOCKER_POTENTIAL.value,
                    R.BLOCKER_POTENTIAL,
                    X.MANDATORY_DOES_NOT_SATISFY,
                    requirement_id=rid,
                    details={**details, "timing": requirement.timing},
                )
            )
        else:
            findings.append(
                FindingOut(
                    FindingType.REVIEW_REQUIRED.value,
                    R.REVIEW_REQUIRED,
                    X.MANDATORY_DOES_NOT_SATISFY_LATER,
                    requirement_id=rid,
                    details={**details, "timing": requirement.timing},
                )
            )
            if requirement.timing in (
                RequirementTiming.AMBIGUOUS.value,
                RequirementTiming.UNKNOWN.value,
            ):
                questions.append(
                    QuestionOut(
                        QuestionAudience.BUYER.value,
                        Q.BUYER_CLARIFY_REQUIREMENT_TIMING,
                        R.REVIEW_REQUIRED,
                        requirement_id=rid,
                        params={"title": requirement.title},
                    )
                )
    if (
        fit.fit_result == FitResult.SATISFIES.value
        and fit.verification_status == V.NOT_VERIFIED.value
    ):
        findings.append(
            FindingOut(
                FindingType.REVIEW_REQUIRED.value,
                R.REVIEW_REQUIRED,
                X.DECLARED_NOT_VERIFIED,
                requirement_id=rid,
                details=details,
            )
        )
    if fit.territory_verdict == TerritoryVerdict.UNMATCHED.value:
        findings.append(
            FindingOut(
                FindingType.TERRITORY_GAP.value,
                R.TERRITORY_GAP,
                X.TERRITORY_UNMATCHED,
                requirement_id=rid,
                details={"territory": fit.support.get("territory")},
            )
        )
    if fit.proof_status in (ProofStatus.MISSING.value, ProofStatus.PARTIAL.value):
        findings.append(
            FindingOut(
                FindingType.EVIDENCE_GAP.value,
                R.EVIDENCE_GAP,
                X.PROOF_MISSING,
                requirement_id=rid,
                details={"proof": fit.support.get("proof")},
            )
        )
        for item in (fit.support.get("proof") or {}).get("items") or []:
            if item.get("status") == ProofStatus.MISSING.value:
                questions.append(
                    QuestionOut(
                        QuestionAudience.COMPANY.value,
                        Q.COMPANY_PROVIDE_PROOF,
                        R.EVIDENCE_GAP,
                        requirement_id=rid,
                        params={"proof_kind": item["kind"], "title": requirement.title},
                    )
                )

    if fit.fit_result == FitResult.UNKNOWN.value:
        actionable = fit.rule_code in (
            R.FACT_REQUIREMENT,
            R.CERTIFICATION_REQUIREMENT,
            R.PROFILE_DATA_REQUIREMENT,
            R.CAPABILITY_NEEDS_REQUIREMENT,
        )
        if actionable:
            findings.append(
                FindingOut(
                    FindingType.MISSING_INFORMATION.value,
                    R.MISSING_INFORMATION,
                    X.DATA_MISSING,
                    requirement_id=rid,
                    details=details,
                )
            )
        if (
            fit.rule_code == R.FACT_REQUIREMENT
            and fit.explanation_code != X.PROOF_MISSING_FOR_CLAIM
        ):
            questions.append(
                QuestionOut(
                    QuestionAudience.COMPANY.value,
                    Q.COMPANY_CONFIRM_FACT,
                    R.MISSING_INFORMATION,
                    requirement_id=rid,
                    params={
                        "fact": requirement.value_json["operating_fact"],
                        "title": requirement.title,
                    },
                )
            )
        elif fit.rule_code == R.CERTIFICATION_REQUIREMENT and fit.explanation_code in (
            X.CERT_UNKNOWN,
            X.CERT_HISTORIC_ONLY,
        ):
            questions.append(
                QuestionOut(
                    QuestionAudience.COMPANY.value,
                    Q.COMPANY_DECLARE_CERTIFICATION,
                    R.MISSING_INFORMATION,
                    requirement_id=rid,
                    params={"standard": (requirement.value_json or {}).get("standard")},
                )
            )
        elif fit.rule_code == R.PROFILE_DATA_REQUIREMENT:
            questions.append(
                QuestionOut(
                    QuestionAudience.COMPANY.value,
                    Q.COMPANY_PROVIDE_PROFILE_DATA,
                    R.MISSING_INFORMATION,
                    requirement_id=rid,
                    params={"category": requirement.category, "title": requirement.title},
                )
            )

    for need, out in zip(requirement.needs, needs, strict=True):
        if out.fit_result == FitResult.UNKNOWN.value and need.capability_code:
            line = CAPABILITIES_BY_CODE[need.capability_code].service_line_code
            questions.append(
                QuestionOut(
                    QuestionAudience.COMPANY.value,
                    Q.COMPANY_DECLARE_CAPABILITY,
                    R.NEED_CAPABILITY,
                    requirement_id=rid,
                    params={"capability": need.capability_code, "service_line": line},
                )
            )

    if (
        fit.territory_verdict == TerritoryVerdict.UNKNOWN.value
        and requirement.territory_completeness == TerritoryCompleteness.UNKNOWN.value
        and not requirement.territories
    ):
        findings.append(
            FindingOut(
                FindingType.MISSING_INFORMATION.value,
                R.MISSING_INFORMATION,
                X.TERRITORY_LIST_UNAVAILABLE,
                requirement_id=rid,
                details={"territory_completeness": "unknown"},
            )
        )
        questions.append(
            QuestionOut(
                QuestionAudience.BUYER.value,
                Q.BUYER_PROVIDE_TERRITORY_LIST,
                R.MISSING_INFORMATION,
                requirement_id=rid,
                params={"title": requirement.title},
            )
        )
    return findings, questions


def criterion_findings(
    criterion: CriterionView,
    out: CriterionAssessmentOut,
    needs: list[tuple[NeedView, NeedAssessmentOut]],
) -> list[FindingOut]:
    findings: list[FindingOut] = []
    cid = criterion.id
    if out.points_status in ("at_risk", "mixed") and out.at_risk_points > pts.TOLERANCE:
        findings.append(
            FindingOut(
                FindingType.REVIEW_REQUIRED.value,
                R.REVIEW_REQUIRED,
                X.DECLARED_NOT_VERIFIED,
                criterion_id=cid,
                details={"at_risk_points": out.at_risk_points},
            )
        )
    demo_unknown = [
        o
        for n, o in needs
        if n.need_type == NeedType.DEMO.value and o.fit_result == FitResult.UNKNOWN.value
    ]
    if criterion.evaluation_type == "judgment" and demo_unknown:
        findings.append(
            FindingOut(
                FindingType.WARNING.value,
                R.DEMO_DEPENDENCY,
                X.DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES,
                criterion_id=cid,
                details={
                    "points": out.max_points,
                    "unknown_needs": [o.need_id for o in demo_unknown],
                },
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# Tot junt                                                                      #
# --------------------------------------------------------------------------- #


def analyze(tender: TenderStructureView, company: CompanyView) -> AnalysisResult:
    fits: list[FitAssessmentOut] = []
    need_outs: list[NeedAssessmentOut] = []
    findings: list[FindingOut] = []
    questions: list[QuestionOut] = []
    fits_by_key: dict[str, tuple[RequirementView, FitAssessmentOut]] = {}
    needs_by_criterion: dict[str, list[tuple[NeedView, NeedAssessmentOut]]] = {}

    for requirement in sorted(tender.requirements, key=lambda r: r.key):
        fit, needs, _ = assess_requirement(requirement, tender, company)
        fits.append(fit)
        need_outs.extend(needs)
        fits_by_key[requirement.key] = (requirement, fit)
        for need, out in zip(requirement.needs, needs, strict=True):
            if need.criterion_key:
                needs_by_criterion.setdefault(need.criterion_key, []).append((need, out))
        f, q = requirement_findings(requirement, fit, needs, company)
        findings.extend(f)
        questions.extend(q)

    criteria_outs: list[CriterionAssessmentOut] = []
    for criterion in sorted(tender.criteria, key=lambda c: c.key):
        out, q = assess_criterion(criterion, tender, needs_by_criterion, fits_by_key)
        criteria_outs.append(out)
        questions.extend(q)
        findings.extend(
            criterion_findings(criterion, out, needs_by_criterion.get(criterion.key, []))
        )

    candidates: list[CandidateOut] = []
    for item in sorted(tender.scope_items, key=lambda i: canonical_json(i.canonical())):
        c, f, q = assess_delivery(item, tender, company)
        candidates.extend(c)
        findings.extend(f)
        questions.extend(q)

    unique: dict[str, QuestionOut] = {}
    for question in questions:
        unique.setdefault(question.dedupe_key, question)
    return AnalysisResult(
        fit_assessments=tuple(fits),
        need_assessments=tuple(need_outs),
        criterion_assessments=tuple(criteria_outs),
        candidates=tuple(candidates),
        findings=tuple(findings),
        questions=tuple(unique[k] for k in sorted(unique)),
    )


def summary(result: AnalysisResult, tender: TenderStructureView) -> dict[str, Any]:
    """Vectors factuals de la run. Cap puntuacio global, cap recomanacio."""
    mandatory = {
        r.id for r in tender.requirements if r.obligation == RequirementObligation.MANDATORY.value
    }
    fits = [f for f in result.fit_assessments if f.requirement_id in mandatory]
    count = lambda value: sum(1 for f in fits if f.fit_result == value)  # noqa: E731
    return {
        "mandatory_satisfies": count(FitResult.SATISFIES.value),
        "mandatory_unknown": count(FitResult.UNKNOWN.value),
        "mandatory_does_not_satisfy": count(FitResult.DOES_NOT_SATISFY.value),
        "mandatory_not_applicable": count(FitResult.NOT_APPLICABLE.value),
        "blocker_potential_count": sum(
            1 for f in result.findings if f.finding_type == FindingType.BLOCKER_POTENTIAL.value
        ),
        "review_required_count": sum(
            1 for f in result.findings if f.finding_type == FindingType.REVIEW_REQUIRED.value
        ),
        "secured_points": round(sum(c.secured_points for c in result.criterion_assessments), 6),
        "at_risk_points": round(sum(c.at_risk_points for c in result.criterion_assessments), 6),
        "lost_points": round(sum(c.lost_points for c in result.criterion_assessments), 6),
        "not_assessable_points": round(
            sum(c.not_assessable_points for c in result.criterion_assessments), 6
        ),
        "max_points": round(sum(c.max_points for c in result.criterion_assessments), 6),
        "delivery": {
            "available": sum(
                1 for c in result.candidates if c.readiness == CandidateReadiness.AVAILABLE.value
            ),
            "pending": sum(
                1
                for c in result.candidates
                if c.readiness
                in (
                    CandidateReadiness.PENDING_COVERAGE.value,
                    CandidateReadiness.PENDING_PARTNER.value,
                    CandidateReadiness.PENDING_VALIDATION.value,
                )
            ),
            "unavailable": sum(
                1 for c in result.candidates if c.readiness == CandidateReadiness.UNAVAILABLE.value
            ),
        },
        "questions": {
            "buyer": sum(1 for q in result.questions if q.audience == QuestionAudience.BUYER.value),
            "company": sum(
                1 for q in result.questions if q.audience == QuestionAudience.COMPANY.value
            ),
        },
    }


def as_json(value: Any) -> Any:
    """Per a `support_json`: tot serialitzable, Decimal com a text."""
    return json.loads(canonical_json(value))
