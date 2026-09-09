"""Experiencia: comptar i sumar el que es pot acreditar, no jutjar si "s'assembla".

El recompte, els imports i el periode son aritmetica i es resolen sols. La
**similitud** no: "treballs de naturalesa igual o similar" es precisament el que
discuteixen les meses de contractacio, i aqui no s'endevina ni per paraules
clau, ni per embeddings, ni per cap model. Si el plec no dona un criteri objectiu
—codis CPV o categories— la resposta es revisio.

El que la v2 hi afegeix, i abans no es podia representar:

* **import acumulat** contra **import per contracte**. "La suma dels serveis de
  l'any de major execucio >= 21.406,71" no es "cada contracte >= 21.406,71", i
  confondre-ho canviava el veredicte en els dos sentits.
* **agregacio anual**: quan el plec parla de "l'any de major execucio", els
  contractes s'agrupen per any i es compara el millor any, no el total del
  trienni.
* **llindars en percentatge**, que nomes es resolen si la base es llegible.

Un projecte tampoc compta pel fet d'existir: ha de ser acreditable. La politica
d'evidencia viu a `profile.project_evidence_strength`, i no haver-hi document no
es un incompliment sino una carpeta incompleta.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.compliance.aggregation import YearValue, aggregate_years, expected_years
from app.compliance.percentages import resolve_threshold
from app.compliance.profile import ProfileSnapshot, is_completed, project_evidence_strength
from app.compliance.rules import (
    EvidenceRef,
    ExplanationCode,
    RuleCode,
    RuleOutcome,
    needs_review,
    unknown,
)
from app.compliance.specs import ExperienceSpec, read_experience
from app.models.enums import (
    AggregationType,
    AssessmentMode,
    AssessmentResult,
    CompanyEvidenceKind,
    EvidenceStrength,
)


def _project_end(project: Any) -> date | None:
    return project.end_date or project.start_date


def assess_experience(
    requirement: Any,
    snapshot: ProfileSnapshot,
    today: date | None = None,
    tender: Any = None,
) -> RuleOutcome:
    spec = read_experience(requirement)
    if not spec.has_threshold:
        # Sense recompte ni import no hi ha res a comparar.
        return needs_review(
            RuleCode.EXPERIENCE_THRESHOLDS, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )

    if not spec.cpv_codes and not spec.services and spec.similarity_note:
        return needs_review(
            RuleCode.EXPERIENCE_THRESHOLDS,
            ExplanationCode.EXPERIENCE_SIMILARITY_REVIEW,
            similarity_condition=str(spec.similarity_note)[:300],
        )

    reference = today or date.today()
    earliest = (
        date(reference.year - spec.years, reference.month, reference.day)
        if spec.years
        else None
    )

    individual = None
    if spec.min_individual is not None:
        resolution = resolve_threshold(spec.min_individual, tender)
        if resolution.amount is None:
            return needs_review(
                RuleCode.EXPERIENCE_THRESHOLDS,
                ExplanationCode.PERCENTAGE_BASE_UNAVAILABLE,
                scope="min_individual_amount",
                **resolution.explain(),
            )
        individual = resolution.amount
    currency = (spec.min_individual or spec.min_accumulated or None)
    currency = currency.currency if currency is not None else "EUR"

    qualifying = _qualifying_projects(snapshot, spec, earliest, individual, currency)
    accredited = [item for item in qualifying if item[1] != EvidenceStrength.MISSING]
    evidence = tuple(
        EvidenceRef(
            kind=CompanyEvidenceKind.PROJECT,
            entity_id=project.id,
            label=f"{project.name}"
            + (f" ({project.amount} {project.currency})" if project.amount else ""),
            strength=strength,
            document_id=document.id if document else None,
        )
        for project, strength, document in qualifying
    )
    overall = (
        EvidenceStrength.STRONG
        if any(s == EvidenceStrength.STRONG for _, s, _ in qualifying)
        else (EvidenceStrength.SUPPORTING if accredited else EvidenceStrength.MISSING)
    )
    detail: dict[str, Any] = {
        "matching_projects": len(qualifying),
        "accredited_projects": len(accredited),
        "lookback_years": spec.years,
        "currency": currency,
        "cpv_codes": list(spec.cpv_codes),
        "services": list(spec.services),
    }
    if individual is not None:
        detail["min_individual_amount"] = float(individual)

    if spec.min_accumulated is not None:
        return _assess_accumulated(
            spec, snapshot, accredited, qualifying, evidence, overall, detail, reference, tender
        )
    return _assess_count(spec, snapshot, accredited, qualifying, evidence, overall, detail)


def _qualifying_projects(
    snapshot: ProfileSnapshot,
    spec: ExperienceSpec,
    earliest: date | None,
    individual: Decimal | None,
    currency: str,
) -> list[tuple[Any, EvidenceStrength, Any]]:
    qualifying: list[tuple[Any, EvidenceStrength, Any]] = []
    for project in snapshot.projects:
        if not is_completed(project):
            continue
        end = _project_end(project)
        if earliest is not None and (end is None or end < earliest):
            continue
        if individual is not None:
            if project.amount is None:
                continue
            if (project.currency or "EUR").upper() != currency:
                continue
            if Decimal(project.amount) < individual:
                continue
        if spec.cpv_codes and not (snapshot.project_cpv_codes(project) & set(spec.cpv_codes)):
            continue
        if spec.services and not (snapshot.project_service_names(project) & set(spec.services)):
            continue
        strength, document = project_evidence_strength(snapshot, project)
        qualifying.append((project, strength, document))
    return qualifying


def _assess_accumulated(
    spec: ExperienceSpec,
    snapshot: ProfileSnapshot,
    accredited: list[tuple[Any, EvidenceStrength, Any]],
    qualifying: list[tuple[Any, EvidenceStrength, Any]],
    evidence: tuple[EvidenceRef, ...],
    overall: EvidenceStrength,
    detail: dict[str, Any],
    reference: date,
    tender: Any,
) -> RuleOutcome:
    """Import acumulat: sumar, agrupar per any si cal, i comparar.

    Nomes se sumen els projectes acreditables. Un contracte sense document no es
    pot fer valer davant l'organ, i sumar-lo inflaria una xifra que despres no es
    podria justificar.
    """
    resolution = resolve_threshold(spec.min_accumulated, tender)
    if resolution.amount is None:
        return needs_review(
            RuleCode.EXPERIENCE_THRESHOLDS,
            ExplanationCode.PERCENTAGE_BASE_UNAVAILABLE,
            scope="min_accumulated_amount",
            **detail,
            **resolution.explain(),
        )
    threshold = resolution.amount
    currency = spec.min_accumulated.currency
    detail.update({"min_accumulated_amount": float(threshold), **resolution.explain()})
    detail["aggregation"] = str(spec.aggregation)

    usable = [
        (project, strength, document)
        for project, strength, document in accredited
        if project.amount is not None and (project.currency or "EUR").upper() == currency
    ]
    if not usable:
        return unknown(
            RuleCode.EXPERIENCE_THRESHOLDS,
            ExplanationCode.EXPERIENCE_DATA_INCOMPLETE,
            reason="no_accreditable_amounts",
            **detail,
        )

    by_year: dict[int, Decimal] = {}
    for project, _strength, _document in usable:
        end = _project_end(project)
        if end is None:
            continue
        by_year[end.year] = by_year.get(end.year, Decimal(0)) + Decimal(project.amount)
    detail["amount_by_year"] = {str(year): float(total) for year, total in sorted(by_year.items())}

    window = expected_years(reference.year, spec.years) if spec.years else None
    aggregate = aggregate_years(
        [YearValue(year=year, amount=total) for year, total in by_year.items()],
        # Sense agregacio explicita, el plec parla de la suma del periode.
        aggregation=(
            spec.aggregation
            if spec.aggregation != AggregationType.UNKNOWN
            else AggregationType.CUMULATIVE
        ),
        threshold=threshold,
        # Un any sense contractes no es una dada que falti: es un any sense
        # facturacio en aquest ambit, i val zero a la suma.
        window=None if window is None else tuple(year for year in window if year in by_year),
    )
    detail["years_used"] = list(aggregate.years_used)
    if aggregate.value is None:
        return unknown(
            RuleCode.EXPERIENCE_THRESHOLDS,
            ExplanationCode.EXPERIENCE_DATA_INCOMPLETE,
            reason="not_aggregatable",
            **detail,
        )

    detail["accumulated"] = float(aggregate.value)
    company_value = {
        "accumulated": float(aggregate.value),
        "currency": currency,
        "aggregation": str(aggregate.method),
        "amount_by_year": detail["amount_by_year"],
        "accredited_projects": len(usable),
    }
    if aggregate.value >= threshold:
        return _outcome(
            AssessmentResult.SATISFIES,
            ExplanationCode.EXPERIENCE_ACCUMULATED_MET,
            detail,
            company_value,
            evidence,
            overall,
        )
    if len(usable) < len(qualifying):
        # Hi ha contractes que encaixen pero no es poden acreditar: la xifra
        # real pot ser mes alta que la que s'ha pogut sumar.
        return RuleOutcome(
            result=AssessmentResult.UNKNOWN,
            rule_code=RuleCode.EXPERIENCE_THRESHOLDS,
            explanation_code=ExplanationCode.EXPERIENCE_EVIDENCE_MISSING,
            params=detail,
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
            evidence=evidence,
            strength=overall,
        )
    if not snapshot.is_section_complete("experience"):
        return RuleOutcome(
            result=AssessmentResult.UNKNOWN,
            rule_code=RuleCode.EXPERIENCE_THRESHOLDS,
            explanation_code=ExplanationCode.EXPERIENCE_DATA_INCOMPLETE,
            params=detail,
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
            evidence=evidence,
            strength=overall,
        )
    return _outcome(
        AssessmentResult.DOES_NOT_SATISFY,
        ExplanationCode.EXPERIENCE_ACCUMULATED_NOT_MET,
        detail,
        company_value,
        evidence,
        overall,
    )


def _assess_count(
    spec: ExperienceSpec,
    snapshot: ProfileSnapshot,
    accredited: list[tuple[Any, EvidenceStrength, Any]],
    qualifying: list[tuple[Any, EvidenceStrength, Any]],
    evidence: tuple[EvidenceRef, ...],
    overall: EvidenceStrength,
    detail: dict[str, Any],
) -> RuleOutcome:
    required = spec.min_count or 0
    detail["required_projects"] = required
    company_value = dict(detail)

    if len(accredited) >= required:
        return _outcome(
            AssessmentResult.SATISFIES,
            ExplanationCode.EXPERIENCE_COUNT_MET,
            detail,
            company_value,
            evidence,
            overall,
        )
    if len(qualifying) >= required:
        return RuleOutcome(
            result=AssessmentResult.UNKNOWN,
            rule_code=RuleCode.EXPERIENCE_THRESHOLDS,
            explanation_code=ExplanationCode.EXPERIENCE_EVIDENCE_MISSING,
            params=detail,
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
            evidence=evidence,
            strength=overall,
        )
    if not snapshot.is_section_complete("experience"):
        return RuleOutcome(
            result=AssessmentResult.UNKNOWN,
            rule_code=RuleCode.EXPERIENCE_THRESHOLDS,
            explanation_code=ExplanationCode.EXPERIENCE_DATA_INCOMPLETE,
            params=detail,
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
            evidence=evidence,
            strength=overall,
        )
    return _outcome(
        AssessmentResult.DOES_NOT_SATISFY,
        ExplanationCode.EXPERIENCE_INSUFFICIENT,
        detail,
        company_value,
        evidence,
        overall,
    )


def _outcome(
    result: AssessmentResult,
    explanation: str,
    params: dict[str, Any],
    company_value: dict[str, Any],
    evidence: tuple[EvidenceRef, ...],
    strength: EvidenceStrength,
) -> RuleOutcome:
    return RuleOutcome(
        result=result,
        rule_code=RuleCode.EXPERIENCE_THRESHOLDS,
        explanation_code=explanation,
        params=params,
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=strength,
    )
