"""Experiencia: comptar projectes acreditables, no jutjar si "s'assemblen".

Aquesta es la regla mes delicada de tota la capa, i per aixo la que mes es
conte. El recompte, l'import i el periode son aritmetica i es resolen sols. La
**similitud** no: "treballs de naturalesa igual o similar" es precisament el
que discuteixen les meses de contractacio.

Politica v1, deliberadament estreta:

* Si el requisit estructura un criteri objectiu —codis CPV o categories de
  servei— es filtra amb aixo.
* Si nomes diu "similars" en text lliure, es `needs_review`. **No** s'inventa
  cap similitud, ni per paraules clau ni per embeddings ni per LLM.

Un projecte tampoc compta pel fet d'existir: ha de ser acreditable. La politica
d'evidencia viu a `profile.project_evidence_strength`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.compliance.profile import ProfileSnapshot, is_completed, project_evidence_strength
from app.compliance.rules import (
    EvidenceRef,
    ExplanationCode,
    RuleCode,
    RuleOutcome,
    needs_review,
)
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    CompanyEvidenceKind,
    EvidenceStrength,
)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _project_end(project: Any) -> date | None:
    return project.end_date or project.start_date


def assess_experience(
    requirement: Any, snapshot: ProfileSnapshot, today: date | None = None
) -> RuleOutcome:
    value = requirement.value_json or {}
    minimum_projects = value.get("minimum_projects")
    if not isinstance(minimum_projects, int) or minimum_projects < 1:
        # Sense un recompte explicit no hi ha res a comptar.
        return needs_review(
            RuleCode.EXPERIENCE_COUNT_AMOUNT, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )

    # Criteri de similitud: nomes s'accepta si es objectiu.
    required_cpvs = {str(c).strip() for c in (value.get("cpv_codes") or []) if str(c).strip()}
    required_services = {
        str(s).strip().casefold() for s in (value.get("services") or []) if str(s).strip()
    }
    similarity_text = value.get("similarity_condition")
    if not required_cpvs and not required_services and similarity_text:
        # "treballs de naturalesa similar" sense cap criteri estructurat.
        return needs_review(
            RuleCode.EXPERIENCE_COUNT_AMOUNT,
            ExplanationCode.EXPERIENCE_SIMILARITY_REVIEW,
            similarity_condition=str(similarity_text)[:300],
            minimum_projects=minimum_projects,
        )

    minimum_each = _decimal(value.get("minimum_amount_each"))
    currency = (value.get("currency") or "EUR").strip().upper()
    lookback_years = value.get("lookback_years")
    reference = today or date.today()
    earliest: date | None = None
    if isinstance(lookback_years, int) and lookback_years > 0:
        earliest = date(reference.year - lookback_years, reference.month, reference.day)

    qualifying: list[tuple[Any, EvidenceStrength, Any]] = []
    for project in snapshot.projects:
        if not is_completed(project):
            continue
        end = _project_end(project)
        if earliest is not None and (end is None or end < earliest):
            continue
        if minimum_each is not None:
            if project.amount is None:
                continue
            if (project.currency or "EUR").upper() != currency:
                continue
            if Decimal(project.amount) < minimum_each:
                continue
        if required_cpvs and not (snapshot.project_cpv_codes(project) & required_cpvs):
            continue
        if required_services and not (
            snapshot.project_service_names(project) & required_services
        ):
            continue

        strength, document = project_evidence_strength(snapshot, project)
        qualifying.append((project, strength, document))

    # Nomes compten els que es poden acreditar davant de l'organ.
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
    company_value = {
        "matching_projects": len(qualifying),
        "accredited_projects": len(accredited),
        "required_projects": minimum_projects,
        "minimum_amount_each": float(minimum_each) if minimum_each is not None else None,
        "currency": currency,
        "lookback_years": lookback_years,
    }
    overall_strength = (
        EvidenceStrength.STRONG
        if any(s == EvidenceStrength.STRONG for _, s, _ in qualifying)
        else (EvidenceStrength.SUPPORTING if accredited else EvidenceStrength.MISSING)
    )

    if len(accredited) >= minimum_projects:
        return RuleOutcome(
            result=AssessmentResult.SATISFIES,
            rule_code=RuleCode.EXPERIENCE_COUNT_AMOUNT,
            explanation_code=ExplanationCode.EXPERIENCE_COUNT_MET,
            params=company_value,
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=overall_strength,
        )

    # N'hi ha prou de coincidents pero els falta document acreditatiu: aixo no
    # es un incompliment, es una carpeta incompleta.
    if len(qualifying) >= minimum_projects:
        return RuleOutcome(
            result=AssessmentResult.UNKNOWN,
            rule_code=RuleCode.EXPERIENCE_COUNT_AMOUNT,
            explanation_code=ExplanationCode.EXPERIENCE_EVIDENCE_MISSING,
            params=company_value,
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
            evidence=evidence,
            strength=overall_strength,
        )

    if not snapshot.is_section_complete("experience"):
        return RuleOutcome(
            result=AssessmentResult.UNKNOWN,
            rule_code=RuleCode.EXPERIENCE_COUNT_AMOUNT,
            explanation_code=ExplanationCode.EXPERIENCE_DATA_INCOMPLETE,
            params=company_value,
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
            evidence=evidence,
            strength=overall_strength,
        )

    return RuleOutcome(
        result=AssessmentResult.DOES_NOT_SATISFY,
        rule_code=RuleCode.EXPERIENCE_COUNT_AMOUNT,
        explanation_code=ExplanationCode.EXPERIENCE_INSUFFICIENT,
        params=company_value,
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=overall_strength,
    )
