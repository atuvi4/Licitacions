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
* cap recomanacio, cap puntuacio global, cap decisio de presentar-se;
* un forat del paquet documental es `missing_information` del plec, i la seccio que
  deixa `unknown` no dona cap avaluacio positiva (3B-2 §4.4); el motor no infereix
  mai l'estructura que falta.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from app.analysis import points as pts
from app.analysis import territory as terr
from app.analysis.fingerprint import (
    CriterionView,
    NeedView,
    PackageGapView,
    RequirementView,
    ScopeItemView,
    TenderStructureView,
    canonical_json,
    coverage_gaps,
    gap_unknown_sections,
    plain_jsonable,
    processing_gaps,
)
from app.analysis.registry import ExplanationCode as X
from app.analysis.registry import QuestionCode as Q
from app.analysis.registry import RuleCode as R
from app.knowledge.snapshot import (
    EvaluationResult,
    KnowledgeSnapshot,
    KnowledgeSource,
    PendingKind,
    evaluate_capability,
    evaluate_certification,
    evaluate_equipment,
    evaluate_execution,
    evaluate_fact,
    evaluate_insurance,
    evaluate_numeric_fact,
    evaluate_profiles,
    evaluate_registration,
    evaluate_turnover,
)
from app.knowledge.vocabulary import (
    CAPABILITIES_BY_CODE,
    NUMERIC_FACT_KEYS,
    PROFESSIONAL_PROFILE_CODES,
    SERVICE_LINE_CODES,
    STANDARDS_BY_CODE,
)
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
    ClientType,
    CriterionResult,
    DeliveryMode,
    EquipmentType,
    FindingType,
    FitResult,
    InsuranceLimitKind,
    InsuranceType,
    NeedType,
    OperatingFactKey,
    ProofKind,
    ProofStatus,
    QuestionAudience,
    RegistrationType,
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
#: Proves de qualificacio (0052): l'evidencia enllacada a les files que el requisit
#: ha fet servir (exercicis, projectes, polisses, equips).
QUALIFICATION_PROOFS = frozenset(
    {
        ProofKind.ANNUAL_ACCOUNTS.value,
        ProofKind.EXECUTION_CERTIFICATES.value,
        ProofKind.INSURANCE_POLICY.value,
        ProofKind.CALIBRATION_CERTIFICATE.value,
    }
)
#: Proves que el model pot demostrar; la resta queden `unknown`.
EVALUABLE_PROOFS = frozenset(
    {ProofKind.CERTIFICATE.value, ProofKind.REGISTRY_INSCRIPTION.value, *QUALIFICATION_PROOFS}
)
#: Regles de qualificacio (0052): una dada que falta es pregunta a l'empresa.
QUALIFICATION_RULES = frozenset(
    {
        R.TURNOVER_REQUIREMENT,
        R.EXECUTION_REQUIREMENT,
        R.INSURANCE_REQUIREMENT,
        R.REGISTRATION_REQUIREMENT,
        R.RESOURCES_REQUIREMENT,
    }
)
#: D'on falta la dada d'un `missing_information` (`details.missing_from`, v3 C-E2). No te
#: valor per defecte: nomes es posa quan la regla demostra d'on falta; si no, cap camp.
MISSING_FROM_COMPANY = "company"
MISSING_FROM_TENDER = "tender"

#: Motiu d'una comparacio territorial `unknown` perque la seccio de l'estructura que
#: la sostindria es `unknown` per culpa d'un forat del paquet.
SECTION_UNKNOWN_REASON = "package_section_unknown"
#: Seccions que, `unknown` (buides i afectades per un forat), impedeixen que una via de
#: prestacio sigui disponible. Una seccio amb files sempre es `present`: mai suprimeix.
#: Els territoris nomes quan el context territorial de l'element no es `not_applicable`.
DELIVERY_SECTIONS = frozenset({"sites", "territories"})
#: Explicacions d'un fit `unknown` en que la dada que falta per decidir (`DATA_MISSING`) es
#: de l'empresa: la part del plec ja es estructurada (fet, norma, llindar, limits, abast,
#: perfils o equips) i el que no consta, no consta al coneixement de l'empresa (o n'hi
#: falta la prova). Queden fora, sense `missing_from` (`DATA_MISSING_UNATTRIBUTED`): un
#: llindar o una norma del plec que no es poden llegir, les necessitats (barregen
#: capacitats sense codi i valors no comparables) i les dades de perfil que aquest pas no
#: llegeix. A mes, quan l'explicacio depen d'un codi del plec, el codi ha de ser del
#: vocabulari que l'empresa pot declarar (`_declarable`): si no, tampoc no es demostra.
DATA_MISSING_FROM_COMPANY = frozenset(
    {
        X.FACT_UNKNOWN,
        X.FACT_HISTORIC_ONLY,
        X.CERT_UNKNOWN,
        X.CERT_HISTORIC_ONLY,
        X.PROOF_MISSING_FOR_CLAIM,
        X.TURNOVER_UNKNOWN,
        X.EXECUTION_UNKNOWN,
        X.INSURANCE_UNKNOWN,
        X.INSURANCE_EXPIRED,
        X.REGISTRATION_UNKNOWN,
        X.REGISTRATION_SCOPE_UNKNOWN,
        X.REGISTRATION_SCOPE_NOT_COVERED,
        X.REGISTRATION_ONLY_OPERATOR_FACT,
        X.PROFILES_MISSING,
        X.EQUIPMENT_MISSING,
        X.EQUIPMENT_CALIBRATION_UNKNOWN,
    }
)


def _vocabulary(enum: type[StrEnum]) -> frozenset[str]:
    """Els codis d'un enum tancat que identifiquen alguna cosa: `other` no (un «altre»
    del plec i un «altre» de l'empresa no son demostrablement el mateix)."""
    return frozenset(m.value for m in enum if m.value != "other")


_OPERATING_FACTS = _vocabulary(OperatingFactKey)
_REGISTRATION_TYPES = _vocabulary(RegistrationType)
_INSURANCE_TYPES = _vocabulary(InsuranceType)
_INSURANCE_LIMITS = _vocabulary(InsuranceLimitKind)
_EQUIPMENT_TYPES = _vocabulary(EquipmentType)
_CLIENT_TYPES = _vocabulary(ClientType)


def _codes_in(value: Any, vocabulary: frozenset[str]) -> bool:
    """Cert si cada codi (claus d'un dict o elements d'una llista) es del vocabulari."""
    codes = list(value) if isinstance(value, (dict, list, tuple)) else [value]
    return all(isinstance(c, str) and c in vocabulary for c in codes)


def _declarable(explanation: str, value: dict[str, Any]) -> bool:
    """El codi del plec que llegeix aquesta explicacio es del vocabulari que l'empresa pot
    declarar. Sense aixo, «no consta a l'empresa» no es demostra (pot ser que el plec
    demani una cosa que el model no sap expressar)."""
    if explanation == X.FACT_UNKNOWN:
        return _codes_in(value.get("operating_fact"), _OPERATING_FACTS)
    if explanation == X.REGISTRATION_UNKNOWN:
        return _codes_in(value.get("registration_type"), _REGISTRATION_TYPES)
    if explanation == X.INSURANCE_UNKNOWN:
        return _codes_in(value.get("insurance_type"), _INSURANCE_TYPES) and _codes_in(
            value.get("insurance_limits_eur") or {}, _INSURANCE_LIMITS
        )
    if explanation == X.PROFILES_MISSING:
        return _codes_in(value.get("required_profiles") or {}, PROFESSIONAL_PROFILE_CODES)
    if explanation == X.EQUIPMENT_MISSING:
        return _codes_in(value.get("required_equipment") or {}, _EQUIPMENT_TYPES)
    if explanation == X.EXECUTION_UNKNOWN:
        return _codes_in(value.get("service_lines") or [], SERVICE_LINE_CODES) and _codes_in(
            value.get("client_types") or [], _CLIENT_TYPES
        )
    return True


def data_missing_from(requirement: RequirementView, explanation: str) -> str | None:
    """D'on falta la dada d'un `DATA_MISSING` (auditoria v3, C-E2), o `None`."""
    if explanation in DATA_MISSING_FROM_COMPANY and _declarable(
        explanation, requirement.value_json or {}
    ):
        return MISSING_FROM_COMPANY
    return None


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
class RoleDivergenceView:
    """Un document analitzable on el rol efectiu de l'empresa (el seu override) no es
    el global (el que llegeix l'extraccio compartida, opcio A de 3B-2 §3.2)."""

    document_id: str
    document_name: str
    company_role: str | None
    company_document_type: str
    global_role: str | None
    global_document_type: str
    global_source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_name": self.document_name,
            "company_role": self.company_role,
            "company_document_type": self.company_document_type,
            "global_role": self.global_role,
            "global_document_type": self.global_document_type,
            "global_source": self.global_source,
        }


@dataclass(frozen=True, slots=True)
class CompanyView:
    """La fotografia del coneixement mes el que el motor necessita amb id."""

    company_id: str
    snapshot: KnowledgeSnapshot
    services: tuple[ServiceClaimView, ...] = ()
    partner_roles: tuple[PartnerRoleView, ...] = ()
    #: Documents on els rols de l'empresa difereixen dels globals. No entra al
    #: `knowledge_fingerprint`: l'`inputs_hash` ja porta `role_overrides_hash`.
    role_divergences: tuple[RoleDivergenceView, ...] = ()

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
class RiskCause:
    """Per que una part dels punts d'un criteri queda en risc (v3, C-E1): el codi de la
    causa i la necessitat o el requisit enllacat que la porta. No es persisteix: nomes
    alimenta els findings del criteri, un per causa."""

    explanation_code: str
    need_id: str | None = None
    requirement_id: str | None = None


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
    #: Les unitats (necessitat, requisit enllacat o compromis sense estructura) que
    #: realment deixen punts en risc. Buit si no n'hi ha cap.
    risk_causes: tuple[RiskCause, ...] = ()


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


def _assess_fact_need(
    need: NeedView, requirement: RequirementView, company: CompanyView, key: str
) -> NeedAssessmentOut:
    """Una necessitat sense capacitat del vocabulari que es llegeix d'un fet
    QUANTITATIU de l'empresa (0052): `value_json = {"operating_fact": ..., "count": N}`."""
    required = _single_number(need.value_json)
    support: dict[str, Any] = {
        "operating_fact": key,
        "required": str(required) if required is not None else None,
        "comparator": need.comparator,
    }
    if required is None or need.comparator not in ("gte", "lte", "eq"):
        return NeedAssessmentOut(
            need_id=need.id,
            requirement_id=requirement.id,
            fit_result=FitResult.UNKNOWN.value,
            knowledge_source=KS.NONE.value,
            verification_status=V.UNKNOWN.value,
            rule_code=R.NEED_OPERATING_FACT,
            explanation_code=X.NEED_VALUE_NOT_COMPARABLE,
            support=support,
        )
    evaluation = evaluate_numeric_fact(company.snapshot, key, need.comparator, required)
    source = _source(evaluation.source)
    if evaluation.result == EvaluationResult.SATISFIES:
        fit, explanation = FitResult.SATISFIES.value, X.FACT_NUMBER_MEETS
    elif evaluation.result == EvaluationResult.DOES_NOT_SATISFY:
        entry = company.snapshot.facts.get(key)
        negated = bool(entry and entry.effective and entry.effective.value is False)
        fit = FitResult.DOES_NOT_SATISFY.value
        explanation = X.FACT_CONTRADICTS if negated else X.FACT_NUMBER_BELOW
    else:
        fit, explanation = FitResult.UNKNOWN.value, X.FACT_UNKNOWN
    return NeedAssessmentOut(
        need_id=need.id,
        requirement_id=requirement.id,
        fit_result=fit,
        knowledge_source=source,
        verification_status=_verification(source)
        if fit != FitResult.UNKNOWN.value
        else V.UNKNOWN.value,
        rule_code=R.NEED_OPERATING_FACT,
        explanation_code=explanation,
        support={**support, "record_ids": list(evaluation.record_ids), "reason": evaluation.reason},
    )


def _fact_need_key(need: NeedView) -> str | None:
    key = (need.value_json or {}).get("operating_fact")
    if need.capability_code is None and key in NUMERIC_FACT_KEYS:
        return str(key)
    return None


def assess_need(
    need: NeedView, requirement: RequirementView, company: CompanyView
) -> NeedAssessmentOut:
    fact_key = _fact_need_key(need)
    if fact_key is not None:
        return _assess_fact_need(need, requirement, company, fact_key)
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


def _records_evidence(pool: dict[str, Any], record_ids: Iterable[str]) -> bool | None:
    """Cert si TOTES les files usades d'aquest tipus tenen evidencia; `None` si cap."""
    wanted = set(record_ids)
    claims = [c for e in pool.values() for c in e.claims if c.record_id in wanted]
    if not claims:
        return None
    return all(c.has_evidence for c in claims)


def _proof_evidence(
    kind: str,
    requirement: RequirementView,
    company: CompanyView,
    record_ids: Iterable[str] = (),
) -> bool | None:
    """Cert/fals si el model pot demostrar la prova; `None` si no pot."""
    snapshot = company.snapshot
    value = requirement.value_json or {}
    record_ids = tuple(record_ids)
    if kind in QUALIFICATION_PROOFS:
        pool = {
            ProofKind.ANNUAL_ACCOUNTS.value: snapshot.financial_years,
            ProofKind.EXECUTION_CERTIFICATES.value: snapshot.execution_references,
            ProofKind.INSURANCE_POLICY.value: snapshot.insurances,
            ProofKind.CALIBRATION_CERTIFICATE.value: snapshot.equipment,
        }[kind]
        return _records_evidence(pool, record_ids)
    if kind == ProofKind.REGISTRY_INSCRIPTION.value and value.get("registration_type"):
        found = _records_evidence(snapshot.registrations, record_ids)
        if found is not None:
            return found
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


def assess_proof(
    requirement: RequirementView,
    company: CompanyView,
    record_ids: Iterable[str] = (),
    window_unknown: bool = False,
) -> tuple[str, dict[str, Any]]:
    """`window_unknown`: no se sap quines files compten (sense data de referencia, v3
    C-E2), aixi que les proves que depenen de les files usades no es poden avaluar."""
    record_ids = tuple(record_ids)
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
        if window_unknown and kind in QUALIFICATION_PROOFS:
            verdicts.append({"kind": kind, "status": ProofStatus.UNKNOWN.value})
            continue
        evidence = _proof_evidence(kind, requirement, company, record_ids)
        if kind not in EVALUABLE_PROOFS or evidence is None:
            status = ProofStatus.UNKNOWN.value
        else:
            status = ProofStatus.SUFFICIENT.value if evidence else ProofStatus.MISSING.value
        verdicts.append({"kind": kind, "status": status})
    statuses = {v["status"] for v in verdicts}
    if statuses == {ProofStatus.SUFFICIENT.value}:
        overall = ProofStatus.SUFFICIENT.value
    elif window_unknown and ProofStatus.MISSING.value not in statuses:
        # M4: sense finestra, cap prova no consta com a absent; la resta no es pot avaluar.
        # `partial` acabaria en «falta la prova exigida» (`evidence_gap`), que seria fals.
        overall = ProofStatus.UNKNOWN.value
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
    if (
        "territories" in gap_unknown_sections(tender)
        and requirement.territory_completeness != TerritoryCompleteness.NOT_APPLICABLE.value
    ):
        # 3B-2 §4.4: cap requisit porta territoris i el document que falta els
        # aportaria: el `unknown` d'un requisit territorial es per culpa del forat. Un
        # requisit que el plec diu que no es territorial continua `not_applicable`.
        return (
            terr.TerritoryComparison(TerritoryVerdict.UNKNOWN.value, SECTION_UNKNOWN_REASON),
            basis,
        )
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


def _numeric_fact_core(requirement: RequirementView, company: CompanyView, key: str) -> _Core:
    value = requirement.value_json or {}
    comparator = str(value.get("comparator", "gte"))
    required = _decimal(value.get("value", value.get("count")))
    support = {"operating_fact": key, "comparator": comparator, "required": str(required)}
    if required is None or comparator not in ("gte", "lte", "eq"):
        return _Core(
            FitResult.UNKNOWN.value,
            KS.NONE.value,
            R.FACT_REQUIREMENT,
            X.QUALIFICATION_THRESHOLD_NOT_COMPARABLE,
            support,
        )
    evaluation = evaluate_numeric_fact(company.snapshot, key, comparator, required)
    return _qualification_core(
        R.FACT_REQUIREMENT,
        evaluation,
        (X.FACT_NUMBER_MEETS, X.FACT_NUMBER_BELOW, X.FACT_UNKNOWN),
        support,
    )


def _fact_core(requirement: RequirementView, company: CompanyView) -> _Core:
    key = requirement.value_json["operating_fact"]
    if key in NUMERIC_FACT_KEYS:
        return _numeric_fact_core(requirement, company, key)
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


# --------------------------------------------------------------------------- #
# Qualificacio i mitjans (0052)                                                 #
# --------------------------------------------------------------------------- #
#
# Cada familia es dispara per una clau ESTRUCTURADA del `value_json` del requisit
# (lectura del plec amb vocabulari global), mai pel text ni pel nom d'un plec:
#
# * `turnover_min_eur` (+ `years`, 3 per defecte): volum de negoci, millor exercici.
# * `execution_min_eur` (+ `years`, `service_lines`, `client_types`): any de major
#   execucio amb projectes completats. Les ofertes historiques no hi compten.
# * `insurance_type` + `insurance_limits_eur` ({tipus de limit: import}).
# * `registration_type` (+ `registration_service_lines`, `registration_issuer`).
# * `required_profiles` ({perfil: persones}) i/o `required_equipment` ({tipus:
#   unitats}) amb `calibration_required`.


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _counts(value: Any) -> dict[str, int] | None:
    """`{codi: n}` amb n >= 1; una llista de codis val 1 per codi."""
    if isinstance(value, list):
        value = {str(code): 1 for code in value}
    if not isinstance(value, dict) or not value:
        return None
    out: dict[str, int] = {}
    for code, n in value.items():
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            return None
        out[str(code)] = n
    return out


def _qualification_core(
    rule: str,
    evaluation: Any,
    explanations: tuple[str, str, str],
    support: dict[str, Any],
) -> _Core:
    met, below, unknown = explanations
    source = _source(evaluation.source)
    support = {
        **support,
        "record_ids": list(evaluation.record_ids),
        "reason": evaluation.reason,
    }
    if evaluation.result == EvaluationResult.SATISFIES:
        return _Core(FitResult.SATISFIES.value, source, rule, met, support)
    if evaluation.result == EvaluationResult.DOES_NOT_SATISFY:
        return _Core(FitResult.DOES_NOT_SATISFY.value, source, rule, below, support)
    return _Core(FitResult.UNKNOWN.value, source, rule, unknown, support)


def _not_comparable(rule: str, support: dict[str, Any]) -> _Core:
    return _Core(
        FitResult.UNKNOWN.value,
        KS.NONE.value,
        rule,
        X.QUALIFICATION_THRESHOLD_NOT_COMPARABLE,
        support,
    )


def _turnover_core(
    requirement: RequirementView, company: CompanyView, reference: date | None
) -> _Core:
    value = requirement.value_json or {}
    minimum = _decimal(value.get("turnover_min_eur"))
    years = value.get("years", 3)
    support = {
        "turnover_min_eur": value.get("turnover_min_eur"),
        "years": years,
        "reference_date": reference.isoformat() if reference else None,
    }
    if minimum is None or not isinstance(years, int) or years < 1:
        return _not_comparable(R.TURNOVER_REQUIREMENT, support)
    evaluation = evaluate_turnover(company.snapshot, minimum, years, reference_date=reference)
    core = _qualification_core(
        R.TURNOVER_REQUIREMENT,
        evaluation,
        (X.TURNOVER_MET, X.TURNOVER_BELOW, X.TURNOVER_UNKNOWN),
        support,
    )
    if evaluation.reason == "reference_date_missing":
        # «Els 3 ultims exercicis» es relatiu al plec: sense termini, no se sap quins.
        return replace(core, explanation=X.QUALIFICATION_REFERENCE_DATE_MISSING)
    return core


def _execution_core(
    requirement: RequirementView, company: CompanyView, reference: date | None
) -> _Core:
    value = requirement.value_json or {}
    minimum = _decimal(value.get("execution_min_eur"))
    years = value.get("years", 3)
    lines = value.get("service_lines")
    clients = value.get("client_types")
    support = {
        "execution_min_eur": value.get("execution_min_eur"),
        "years": years,
        "service_lines": lines,
        "client_types": clients,
        "reference_date": reference.isoformat() if reference else None,
    }
    if minimum is None or not isinstance(years, int) or years < 1:
        return _not_comparable(R.EXECUTION_REQUIREMENT, support)
    evaluation = evaluate_execution(
        company.snapshot,
        minimum,
        years,
        service_lines=lines,
        client_types=clients,
        reference_date=reference,
    )
    core = _qualification_core(
        R.EXECUTION_REQUIREMENT,
        evaluation,
        (X.EXECUTION_MET, X.EXECUTION_BELOW, X.EXECUTION_UNKNOWN),
        support,
    )
    if evaluation.reason == "reference_date_missing":
        # Sense termini de presentacio no se sap quins son «els darrers anys».
        return replace(core, explanation=X.QUALIFICATION_REFERENCE_DATE_MISSING)
    return core


def _insurance_core(requirement: RequirementView, company: CompanyView) -> _Core:
    value = requirement.value_json or {}
    insurance_type = str(value["insurance_type"])
    raw = value.get("insurance_limits_eur")
    support = {"insurance_type": insurance_type, "insurance_limits_eur": raw}
    limits = (
        {str(k): _decimal(v) for k, v in raw.items()} if isinstance(raw, dict) and raw else None
    )
    if not limits or any(v is None for v in limits.values()):
        return _not_comparable(R.INSURANCE_REQUIREMENT, support)
    evaluation = evaluate_insurance(company.snapshot, insurance_type, limits)  # type: ignore[arg-type]
    core = _qualification_core(
        R.INSURANCE_REQUIREMENT,
        evaluation,
        (X.INSURANCE_MET, X.INSURANCE_BELOW, X.INSURANCE_UNKNOWN),
        support,
    )
    expired = any(
        p.kind == PendingKind.EXPIRED and p.subject == "insurance" and p.key == insurance_type
        for p in company.snapshot.pending_validation
    )
    if core.fit == FitResult.UNKNOWN.value and not evaluation.record_ids and expired:
        # Hi havia una polissa d'aquest tipus, pero ja no es vigent: no se sap si es
        # renovara (no es cap «no»), i es diu per que.
        return replace(core, explanation=X.INSURANCE_EXPIRED)
    return core


def _registration_core(requirement: RequirementView, company: CompanyView) -> _Core:
    value = requirement.value_json or {}
    registration_type = str(value["registration_type"])
    lines = value.get("registration_service_lines")
    issuer = value.get("registration_issuer")
    support = {
        "registration_type": registration_type,
        "registration_service_lines": lines,
        "registration_issuer": issuer,
    }
    evaluation = evaluate_registration(
        company.snapshot, registration_type, service_lines=lines, issuer=issuer
    )
    core = _qualification_core(
        R.REGISTRATION_REQUIREMENT,
        evaluation,
        (X.REGISTRATION_MET, X.REGISTRATION_UNKNOWN, X.REGISTRATION_UNKNOWN),
        support,
    )
    if evaluation.reason == "scope_unknown":
        return replace(core, explanation=X.REGISTRATION_SCOPE_UNKNOWN)
    if evaluation.reason == "scope_not_covered":
        return replace(core, explanation=X.REGISTRATION_SCOPE_NOT_COVERED)
    fact_key = value.get("operating_fact")
    if core.fit == FitResult.UNKNOWN.value and not evaluation.record_ids and fact_key:
        # L'empresa diu que es operadora, pero no hi ha cap inscripcio amb abast: el
        # fet no diu quins serveis te inscrits. Es revisa; no se'n dedueix res.
        entry = company.snapshot.facts.get(str(fact_key))
        claim = entry.effective if entry else None
        if claim is not None and claim.value is True:
            return _Core(
                FitResult.UNKNOWN.value,
                _source(claim.source),
                R.REGISTRATION_REQUIREMENT,
                X.REGISTRATION_ONLY_OPERATOR_FACT,
                {**core.support, "operating_fact": fact_key, "fact_record_id": claim.record_id},
            )
    return core


def _resources_core(requirement: RequirementView, company: CompanyView) -> _Core:
    value = requirement.value_json or {}
    profiles = _counts(value.get("required_profiles")) if "required_profiles" in value else {}
    equipment = _counts(value.get("required_equipment")) if "required_equipment" in value else {}
    calibration = bool(value.get("calibration_required", False))
    support: dict[str, Any] = {
        "required_profiles": profiles,
        "required_equipment": equipment,
        "calibration_required": calibration,
    }
    if profiles is None or equipment is None:
        return _not_comparable(R.RESOURCES_REQUIREMENT, support)
    parts: list[tuple[Any, str]] = []
    if profiles:
        evaluation = evaluate_profiles(company.snapshot, profiles)
        parts.append((evaluation, X.PROFILES_MISSING))
    if equipment:
        evaluation = evaluate_equipment(
            company.snapshot, equipment, calibration_required=calibration
        )
        unknown = (
            X.EQUIPMENT_CALIBRATION_UNKNOWN
            if evaluation.reason == "calibration_unknown"
            else X.EQUIPMENT_MISSING
        )
        parts.append((evaluation, unknown))
    record_ids = [r for evaluation, _ in parts for r in evaluation.record_ids]
    support = {
        **support,
        "record_ids": record_ids,
        "reasons": [evaluation.reason for evaluation, _ in parts],
    }
    sources = [_source(evaluation.source) for evaluation, _ in parts]
    failing = [e for e, _ in parts if e.result == EvaluationResult.DOES_NOT_SATISFY]
    if failing:
        return _Core(
            FitResult.DOES_NOT_SATISFY.value,
            _source(failing[0].source),
            R.RESOURCES_REQUIREMENT,
            X.EQUIPMENT_CALIBRATION_EXPIRED,
            support,
        )
    if all(e.result == EvaluationResult.SATISFIES for e, _ in parts):
        return _Core(
            FitResult.SATISFIES.value,
            _combine_sources(sources),
            R.RESOURCES_REQUIREMENT,
            X.RESOURCES_MET,
            support,
        )
    first_unknown = next(x for e, x in parts if e.result == EvaluationResult.UNKNOWN)
    known = [s for s in sources if s != KS.NONE.value]
    return _Core(
        FitResult.UNKNOWN.value,
        _combine_sources(known) if known else KS.NONE.value,
        R.RESOURCES_REQUIREMENT,
        first_unknown,
        support,
    )


def _with_minimum_needs(core: _Core, needs_core: _Core) -> _Core:
    """Un requisit de mitjans amb necessitats minimes: compleix si totes dues parts
    compleixen; una contradiccio de qualsevol part el contradiu; si no, `unknown`."""
    support = {**core.support, "needs": needs_core.support.get("needs", [])}
    if core.fit == FitResult.DOES_NOT_SATISFY.value:
        return replace(core, support=support)
    if needs_core.fit == FitResult.DOES_NOT_SATISFY.value:
        return replace(needs_core, support={**needs_core.support, **core.support})
    if core.fit == needs_core.fit == FitResult.SATISFIES.value:
        return replace(
            core, source=_combine_sources([core.source, needs_core.source]), support=support
        )
    if core.fit == FitResult.UNKNOWN.value:
        return replace(core, support=support)
    return replace(needs_core, support={**needs_core.support, **core.support})


def qualification_core(
    requirement: RequirementView, company: CompanyView, tender: TenderStructureView
) -> _Core | None:
    """El nucli d'un requisit de qualificacio, si el `value_json` en porta l'estructura.
    Les finestres temporals es resolen amb el termini de presentacio del plec, mai
    amb el rellotge."""
    value = requirement.value_json or {}
    reference = tender.submission_deadline
    if value.get("registration_type"):
        return _registration_core(requirement, company)
    if "turnover_min_eur" in value:
        return _turnover_core(requirement, company, reference)
    if "execution_min_eur" in value:
        return _execution_core(requirement, company, reference)
    if value.get("insurance_type"):
        return _insurance_core(requirement, company)
    if "required_profiles" in value or "required_equipment" in value:
        return _resources_core(requirement, company)
    return None


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
    qualification = qualification_core(requirement, company, tender)

    if qualification is not None:
        core = qualification
        minimum = [n for n in requirement.needs if n.need_type == NeedType.MINIMUM.value]
        if core.rule == R.RESOURCES_REQUIREMENT and minimum:
            core = _with_minimum_needs(core, _needs_core(requirement, needs))
    elif value.get("operating_fact"):
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
    proof, proof_support = assess_proof(
        requirement,
        company,
        core.support.get("record_ids") or (),
        window_unknown=core.explanation == X.QUALIFICATION_REFERENCE_DATE_MISSING,
    )

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


def _is_declared_satisfies(fit: str, source: str) -> bool:
    return fit == FitResult.SATISFIES.value and source == KS.CURRENT_DECLARED.value


def _need_risk_cause(out: NeedAssessmentOut) -> RiskCause:
    """Per que una necessitat `at_risk` deixa punts en risc. Nomes `satisfies` amb dada
    declarada es «dades declarades sense verificar»; `unknown` sense dada actual es «no
    consta cap dada actual»; la resta (p. ex. per sota del valor d'avui) diu la seva propia
    explicacio. `NEED_VALUE_NOT_COMPARABLE` tambe: el motor torna abans de llegir el
    coneixement de l'empresa (el valor del plec no es comparable), aixi que no es pot dir
    que no consti.

    Una necessitat `unknown` amb font ACTUAL (declarada o verificada) te una dada actual que
    no es pot comparar (capacitat sense nombre o nivell, fet quantitatiu sense nombre):
    tampoc no es «no consta» (I1). Un fet quantitatiu diu `CRITERION_NEEDS_NOT_COMPARABLE`;
    la resta (una capacitat: `CAPABILITY_UNKNOWN`), la seva propia explicacio."""
    current = out.knowledge_source in (KS.CURRENT_DECLARED.value, KS.CURRENT_VERIFIED.value)
    if out.explanation_code == X.NEED_VALUE_NOT_COMPARABLE:
        code = out.explanation_code
    elif out.fit_result == FitResult.UNKNOWN.value and current:
        code = (
            X.CRITERION_NEEDS_NOT_COMPARABLE
            if out.explanation_code == X.FACT_UNKNOWN
            else out.explanation_code
        )
    elif out.fit_result == FitResult.UNKNOWN.value:
        code = X.CRITERION_NEEDS_UNKNOWN
    elif _is_declared_satisfies(out.fit_result, out.knowledge_source):
        code = X.DECLARED_NOT_VERIFIED
    else:
        code = out.explanation_code
    return RiskCause(code, need_id=out.need_id)


def _requirement_risk_cause(fit: FitAssessmentOut) -> RiskCause:
    """El mateix per a un requisit enllacat `at_risk` (sense necessitats puntuades)."""
    code = (
        X.DECLARED_NOT_VERIFIED
        if _is_declared_satisfies(fit.fit_result, fit.knowledge_source)
        else fit.explanation_code
    )
    return RiskCause(code, requirement_id=fit.requirement_id)


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
    causes: list[RiskCause] = []
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
            outcomes: list[tuple[str, NeedAssessmentOut | None]] = []

            def outcome(threshold: Any) -> str:
                out = by_threshold.get(
                    float(threshold) if isinstance(threshold, (int, float)) else threshold
                )
                bucket = (
                    "at_risk"
                    if out is None
                    else _bucket_for(out.fit_result, out.knowledge_source, out.explanation_code)
                )
                outcomes.append((bucket, out))
                return bucket

            buckets = pts.allocate_tranches(max_points, criterion.tranches, outcome)
            # Nomes els trams amb increment en risc en son causa. Un tram sense cap
            # necessitat que hi correspongui es un compromis sense estructura comparable.
            increments = pts.tranche_increments(max_points, criterion.tranches)
            causes = [
                _need_risk_cause(out) if out is not None else RiskCause(X.CRITERION_OPEN_COMMITMENT)
                for (bucket, out), (_, increment) in zip(outcomes, increments, strict=True)
                if bucket == "at_risk" and increment > pts.TOLERANCE
            ]
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
            if bucket == "at_risk":
                causes = [
                    _need_risk_cause(out)
                    for (_, out), b in zip(scored, buckets_of, strict=True)
                    if b == "at_risk"
                ]
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
                causes = [
                    RiskCause(X.CRITERION_TIMING_NOT_CONCLUSIVE, requirement_id=requirement.id)
                ]
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
            bucket = _bucket_for(fit.fit_result, fit.knowledge_source)
            buckets = pts.all_in(bucket, max_points)
            if bucket == "at_risk":
                causes = [_requirement_risk_cause(fit)]
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
                risk_causes=(RiskCause(X.CRITERION_OPEN_COMMITMENT),),
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
            risk_causes=tuple(causes),
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
    # 3B-2 §4.4: si no hi ha seus (o territoris) i el document que falta les aportaria,
    # cap via de prestacio pot ser disponible: el veredicte es `unknown`.
    unknown = gap_unknown_sections(tender) & DELIVERY_SECTIONS
    if completeness == TerritoryCompleteness.NOT_APPLICABLE.value:
        # El context territorial de l'element no es territorial: nomes hi compten les seus.
        unknown -= {"territories"}
    section_unknown = bool(unknown)

    def verdict_for(coverage: terr.Coverage | None) -> terr.TerritoryComparison:
        if section_unknown:
            return terr.TerritoryComparison(TerritoryVerdict.UNKNOWN.value, SECTION_UNKNOWN_REASON)
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
    # Amb la seccio `unknown`, el que falta es del plec (troballa del forat), no de
    # l'empresa: no se li demana que confirmi una cobertura que no es pot comparar.
    if not section_unknown and any(
        c.readiness == CandidateReadiness.PENDING_COVERAGE.value for c in candidates
    ):
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

    if fit.explanation_code in (
        X.REGISTRATION_SCOPE_UNKNOWN,
        X.REGISTRATION_SCOPE_NOT_COVERED,
        X.REGISTRATION_ONLY_OPERATOR_FACT,
    ):
        # Operador declarat sense abast suficient: es revisa, no es dedueix res.
        findings.append(
            FindingOut(
                FindingType.REVIEW_REQUIRED.value,
                R.REVIEW_REQUIRED,
                fit.explanation_code,
                requirement_id=rid,
                details=details,
            )
        )
        questions.append(
            QuestionOut(
                QuestionAudience.COMPANY.value,
                Q.COMPANY_REVIEW_REGISTRATION_SCOPE,
                R.REGISTRATION_REQUIREMENT,
                requirement_id=rid,
                params={
                    "registration_type": (requirement.value_json or {}).get("registration_type"),
                    "title": requirement.title,
                },
            )
        )
    if fit.explanation_code in (
        X.EQUIPMENT_CALIBRATION_EXPIRED,
        X.EQUIPMENT_CALIBRATION_UNKNOWN,
    ):
        questions.append(
            QuestionOut(
                QuestionAudience.COMPANY.value,
                Q.COMPANY_RENEW_CALIBRATION,
                R.RESOURCES_REQUIREMENT,
                requirement_id=rid,
                params={"title": requirement.title},
            )
        )

    if fit.fit_result == FitResult.UNKNOWN.value:
        actionable = fit.rule_code in (
            R.FACT_REQUIREMENT,
            R.CERTIFICATION_REQUIREMENT,
            R.PROFILE_DATA_REQUIREMENT,
            R.CAPABILITY_NEEDS_REQUIREMENT,
            *QUALIFICATION_RULES,
        )
        # Sense cap dada de l'empresa, el volum i l'execucio no arriben a mirar el termini
        # (`no_current_data`): si el plec tampoc no el dona, falten totes dues coses.
        no_data_nor_deadline = (
            fit.rule_code in (R.TURNOVER_REQUIREMENT, R.EXECUTION_REQUIREMENT)
            and fit.explanation_code in (X.TURNOVER_UNKNOWN, X.EXECUTION_UNKNOWN)
            and "reference_date" in fit.support
            and fit.support["reference_date"] is None
        )
        if fit.explanation_code == X.QUALIFICATION_REFERENCE_DATE_MISSING or no_data_nor_deadline:
            # v3 (C-E2): el que falta es el termini de presentacio, i falta al plec. Es
            # demana a l'organ. Amb data de l'empresa (`QUALIFICATION_REFERENCE_DATE_MISSING`),
            # cap «falta una dada de l'empresa» ni cap prova (proof `unknown`: no se sap de
            # quins exercicis o anys).
            findings.append(
                FindingOut(
                    FindingType.MISSING_INFORMATION.value,
                    R.MISSING_INFORMATION,
                    X.TENDER_REFERENCE_DATE_MISSING,
                    requirement_id=rid,
                    details={**details, "missing_from": MISSING_FROM_TENDER},
                )
            )
            questions.append(
                QuestionOut(
                    QuestionAudience.BUYER.value,
                    Q.BUYER_PROVIDE_SUBMISSION_DEADLINE,
                    R.MISSING_INFORMATION,
                    requirement_id=rid,
                    params={"title": requirement.title},
                )
            )
        if actionable and fit.explanation_code != X.QUALIFICATION_REFERENCE_DATE_MISSING:
            # «Falta una dada de l'empresa» nomes quan es demostra; si no, sense atribuir.
            missing_from = data_missing_from(requirement, fit.explanation_code)
            findings.append(
                FindingOut(
                    FindingType.MISSING_INFORMATION.value,
                    R.MISSING_INFORMATION,
                    X.DATA_MISSING if missing_from else X.DATA_MISSING_UNATTRIBUTED,
                    requirement_id=rid,
                    details={**details, "missing_from": missing_from} if missing_from else details,
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
        elif fit.rule_code in QUALIFICATION_RULES and fit.explanation_code in (
            X.TURNOVER_UNKNOWN,
            X.EXECUTION_UNKNOWN,
            X.INSURANCE_UNKNOWN,
            X.INSURANCE_EXPIRED,
            X.REGISTRATION_UNKNOWN,
            X.PROFILES_MISSING,
            X.EQUIPMENT_MISSING,
        ):
            questions.append(
                QuestionOut(
                    QuestionAudience.COMPANY.value,
                    Q.COMPANY_PROVIDE_PROFILE_DATA,
                    R.MISSING_INFORMATION,
                    requirement_id=rid,
                    params={"category": requirement.category, "title": requirement.title},
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
        fact_key = _fact_need_key(need)
        if out.fit_result == FitResult.UNKNOWN.value and fact_key is not None:
            questions.append(
                QuestionOut(
                    QuestionAudience.COMPANY.value,
                    Q.COMPANY_CONFIRM_FACT,
                    R.NEED_OPERATING_FACT,
                    requirement_id=rid,
                    params={"fact": fact_key, "title": requirement.title},
                )
            )
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
                # El requisit no porta cap territori i el plec no en dona la llista.
                details={"territory_completeness": "unknown", "missing_from": MISSING_FROM_TENDER},
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
        # Un finding per causa real (v3, C-E1), amb les necessitats (o el requisit
        # enllacat) que la porten. Mai «dades declarades» sense cap dada declarada.
        assert out.risk_causes, ("punts en risc sense causa", criterion.key)
        by_code: dict[str, list[RiskCause]] = {}
        for cause in out.risk_causes:
            by_code.setdefault(cause.explanation_code, []).append(cause)
        for code, causes in by_code.items():
            # `at_risk_points` es el TOTAL en risc del criteri, el mateix a cada finding
            # d'aquest criteri: no es reparteix per causa i no s'ha de sumar entre findings.
            details: dict[str, Any] = {"at_risk_points": out.at_risk_points}
            need_ids = sorted({c.need_id for c in causes if c.need_id is not None})
            requirement_ids = sorted({c.requirement_id for c in causes if c.requirement_id})
            if need_ids:
                details["need_ids"] = need_ids
            if requirement_ids:
                details["requirement_id"] = requirement_ids[0]
            missing = code == X.CRITERION_NEEDS_UNKNOWN
            findings.append(
                FindingOut(
                    FindingType.MISSING_INFORMATION.value
                    if missing
                    else FindingType.REVIEW_REQUIRED.value,
                    R.MISSING_INFORMATION if missing else R.REVIEW_REQUIRED,
                    code,
                    criterion_id=cid,
                    details=details,
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
# Paquet documental i rols (3B-2 §4.4 i §3.2)                                   #
# --------------------------------------------------------------------------- #


def _gap_order(gap: PackageGapView) -> tuple[str, str]:
    return canonical_json(gap.canonical()), canonical_json(gap.detail or {})


def package_findings(tender: TenderStructureView) -> list[FindingOut]:
    """Una `missing_information` (`missing_from=tender`) per forat obert del paquet,
    amb el codi, el detall i les seccions que aportaria el document que falta (o
    `impact_unknown` si no se sap). El motor no infereix mai l'estructura que falta."""
    findings: list[FindingOut] = []
    for gap in sorted(tender.gaps, key=_gap_order):
        would_provide = sorted(set(gap.would_provide))
        findings.append(
            FindingOut(
                FindingType.MISSING_INFORMATION.value,
                R.MISSING_INFORMATION,
                X.PACKAGE_GAP,
                details={
                    "missing_from": MISSING_FROM_TENDER,
                    "gap_code": gap.code,
                    "gap_detail": plain_jsonable(gap.detail or {}),
                    "would_provide": would_provide,
                    "impact_unknown": not would_provide,
                    "producer": gap.producer,
                },
            )
        )
    return findings


def role_divergence_findings(company: CompanyView) -> list[FindingOut]:
    """`ROLE_OVERRIDE_DIVERGES` (informativa): els rols de l'empresa difereixen dels
    globals. L'extraccio compartida llegeix els globals (opcio A); aquesta analisi
    tambe, i ho diu, amb la llista de documents."""
    if not company.role_divergences:
        return []
    documents = sorted(company.role_divergences, key=lambda d: (d.document_name, d.document_id))
    return [
        FindingOut(
            FindingType.WARNING.value,
            R.REVIEW_REQUIRED,
            X.ROLE_OVERRIDE_DIVERGES,
            details={"documents": [d.as_dict() for d in documents]},
        )
    ]


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

    findings.extend(package_findings(tender))
    findings.extend(role_divergence_findings(company))

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
        # 3B-2 §4.4: al paquet documental hi falta algun document (forat de
        # cobertura). Un document present que no es pot llegir no ho es: va a part.
        "package_incomplete": bool(coverage_gaps(tender.gaps)),
        "processing_incomplete": bool(processing_gaps(tender.gaps)),
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
    return plain_jsonable(value)
