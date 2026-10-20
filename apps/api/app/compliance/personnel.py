"""Equip huma, titulacions i registres.

Tres regles amb el mateix criteri de fons: coincidencia exacta normalitzada, i
revisio quan el plec no estructura prou. Cap equivalencia de titulacions: decidir
si un grau en telematica cobreix "enginyer de telecomunicacions" es una lectura
normativa, no una comparacio de cadenes.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import (
    EvidenceRef,
    ExplanationCode,
    RuleCode,
    RuleOutcome,
    needs_review,
    unknown,
)
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    CompanyEvidenceKind,
    EvidenceStrength,
    LinkedEntityType,
    RegistrationType,
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(raw: str | None) -> str:
    if not raw:
        return ""
    return _NON_ALNUM.sub(" ", raw.casefold()).strip()


# --------------------------------------------------------------------------- #
# Titulacions                                                                   #
# --------------------------------------------------------------------------- #


def assess_qualification(
    requirement: Any, snapshot: ProfileSnapshot, deadline: date | None
) -> RuleOutcome:
    value = requirement.value_json or {}
    required = (value.get("name") or value.get("qualification") or "").strip()
    if not required:
        return needs_review(
            RuleCode.QUALIFICATION_EXACT, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )

    key = normalize_name(required)
    if not key:
        return needs_review(
            RuleCode.QUALIFICATION_EXACT,
            ExplanationCode.QUALIFICATION_AMBIGUOUS,
            required=required,
        )

    matches = [q for q in snapshot.qualifications if normalize_name(q.name) == key]
    if not matches:
        if snapshot.is_section_complete("team"):
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.QUALIFICATION_EXACT,
                explanation_code=ExplanationCode.QUALIFICATION_NOT_FOUND_PROFILE_COMPLETE,
                params={"required": required},
                mode=AssessmentMode.AUTOMATIC,
            )
        return unknown(
            RuleCode.QUALIFICATION_EXACT,
            ExplanationCode.QUALIFICATION_UNKNOWN,
            required=required,
        )

    minimum_people = value.get("minimum_people")
    minimum_people = minimum_people if isinstance(minimum_people, int) else 1

    valid = [
        q
        for q in matches
        if q.expires_at is None or deadline is None or q.expires_at >= deadline
    ]
    holders = {q.person_id for q in valid}

    evidence = tuple(
        EvidenceRef(
            kind=CompanyEvidenceKind.QUALIFICATION,
            entity_id=q.id,
            label=f"{q.name}",
            strength=(
                EvidenceStrength.STRONG
                if snapshot.documents_for(LinkedEntityType.PERSON_QUALIFICATION, q.id)
                else EvidenceStrength.MISSING
            ),
            document_id=next(
                (
                    d.id
                    for d in snapshot.documents_for(LinkedEntityType.PERSON_QUALIFICATION, q.id)
                ),
                None,
            ),
        )
        for q in valid
    )
    strength = (
        EvidenceStrength.STRONG
        if any(e.strength == EvidenceStrength.STRONG for e in evidence)
        else EvidenceStrength.MISSING
    )
    company_value = {
        "holders": len(holders),
        "required_people": minimum_people,
        "qualification": required,
    }

    if len(holders) >= minimum_people:
        return RuleOutcome(
            result=AssessmentResult.SATISFIES,
            rule_code=RuleCode.QUALIFICATION_EXACT,
            explanation_code=ExplanationCode.QUALIFICATION_FOUND,
            params=company_value,
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=strength,
        )
    if not snapshot.is_section_complete("team"):
        return RuleOutcome(
            result=AssessmentResult.UNKNOWN,
            rule_code=RuleCode.QUALIFICATION_EXACT,
            explanation_code=ExplanationCode.PERSONNEL_DATA_INCOMPLETE,
            params=company_value,
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
            evidence=evidence,
            strength=strength,
        )
    return RuleOutcome(
        result=AssessmentResult.DOES_NOT_SATISFY,
        rule_code=RuleCode.QUALIFICATION_EXACT,
        explanation_code=ExplanationCode.PERSONNEL_COUNT_NOT_MET,
        params=company_value,
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=strength,
    )


# --------------------------------------------------------------------------- #
# Nombre de persones                                                            #
# --------------------------------------------------------------------------- #


def assess_personnel(requirement: Any, snapshot: ProfileSnapshot) -> RuleOutcome:
    value = requirement.value_json or {}
    minimum = value.get("minimum_people") or value.get("count")
    if not isinstance(minimum, int) or minimum < 1:
        return needs_review(RuleCode.PERSONNEL_COUNT, ExplanationCode.REQUIREMENT_VALUE_MISSING)

    # Si el plec demana un perfil concret, aixo es una titulacio, no un
    # recompte: no es pot resoldre comptant caps.
    if value.get("name") or value.get("qualification"):
        return assess_qualification(requirement, snapshot, None)

    total = len(snapshot.people)
    company_value = {"people": total, "required_people": minimum}
    evidence = tuple(
        EvidenceRef(
            kind=CompanyEvidenceKind.PERSON,
            entity_id=person.id,
            label=f"{person.first_name} {person.last_name or ''}".strip(),
            strength=EvidenceStrength.SUPPORTING,
        )
        for person in snapshot.people[:10]
    )

    if total >= minimum:
        return RuleOutcome(
            result=AssessmentResult.SATISFIES,
            rule_code=RuleCode.PERSONNEL_COUNT,
            explanation_code=ExplanationCode.PERSONNEL_COUNT_MET,
            params=company_value,
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=EvidenceStrength.SUPPORTING if evidence else EvidenceStrength.MISSING,
        )
    if not snapshot.is_section_complete("team"):
        return RuleOutcome(
            result=AssessmentResult.UNKNOWN,
            rule_code=RuleCode.PERSONNEL_COUNT,
            explanation_code=ExplanationCode.PERSONNEL_DATA_INCOMPLETE,
            params=company_value,
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
        )
    return RuleOutcome(
        result=AssessmentResult.DOES_NOT_SATISFY,
        rule_code=RuleCode.PERSONNEL_COUNT,
        explanation_code=ExplanationCode.PERSONNEL_COUNT_NOT_MET,
        params=company_value,
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
    )


# --------------------------------------------------------------------------- #
# Registres i classificacio empresarial                                         #
# --------------------------------------------------------------------------- #

_REGISTRY_HINTS: tuple[tuple[re.Pattern[str], RegistrationType], ...] = (
    (re.compile(r"rolece", re.I), RegistrationType.ROLECE),
    (re.compile(r"\breli\b", re.I), RegistrationType.RELI),
    # v5: el registre d'operadors (CNMC), nomes quan el plec el nomena. El
    # normalitzador de format lliure hi arriba amb `type=sector_operator_registry`.
    (
        re.compile(
            r"sector_operator_registry|\bCNMC\b|registre d.operadors|registro de operadores",
            re.I,
        ),
        RegistrationType.SECTOR_OPERATOR_REGISTRY,
    ),
    (re.compile(r"clasificaci[oó]n|classificaci[oó]", re.I),
     RegistrationType.BUSINESS_CLASSIFICATION),
)


def infer_registration_type(raw: str | None) -> RegistrationType | None:
    if not raw:
        return None
    for pattern, kind in _REGISTRY_HINTS:
        if pattern.search(raw):
            return kind
    return None


def assess_registration(
    requirement: Any, snapshot: ProfileSnapshot, deadline: date | None
) -> RuleOutcome:
    value = requirement.value_json or {}
    required_name = (value.get("name") or requirement.title or "").strip()
    required_type = infer_registration_type(value.get("type") or required_name)

    # Grup / subgrup / categoria: comparar-ho demana camps estructurats que el
    # perfil encara no modela. Text lliure NO serveix per decidir classificacio.
    if any(value.get(key) for key in ("group", "subgroup", "category", "grupo", "subgrupo")):
        return needs_review(
            RuleCode.REGISTRATION_EXACT,
            ExplanationCode.REGISTRATION_CLASSIFICATION_REVIEW,
            group=value.get("group") or value.get("grupo"),
            subgroup=value.get("subgroup") or value.get("subgrupo"),
            category=value.get("category"),
        )

    if required_type is None:
        return needs_review(
            RuleCode.REGISTRATION_EXACT,
            ExplanationCode.RULE_NOT_AUTOMATABLE,
            required=required_name[:200],
        )

    matches = [reg for reg in snapshot.registrations if reg.type == required_type]
    if not matches:
        if snapshot.is_section_complete("registrations"):
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.REGISTRATION_EXACT,
                explanation_code=ExplanationCode.REGISTRATION_NOT_FOUND_PROFILE_COMPLETE,
                params={"required_type": str(required_type)},
                mode=AssessmentMode.AUTOMATIC,
            )
        return unknown(
            RuleCode.REGISTRATION_EXACT,
            ExplanationCode.REGISTRATION_DATA_INCOMPLETE,
            required_type=str(required_type),
        )

    matches.sort(key=lambda r: (r.expires_at is None, r.expires_at or date.min), reverse=True)
    best = matches[0]
    documents = snapshot.documents_for(LinkedEntityType.COMPANY_REGISTRATION, best.id)
    evidence = (
        EvidenceRef(
            kind=CompanyEvidenceKind.REGISTRATION,
            entity_id=best.id,
            label=f"{best.name} ({best.type})",
            strength=EvidenceStrength.STRONG if documents else EvidenceStrength.MISSING,
            document_id=documents[0].id if documents else None,
        ),
    )
    company_value = {
        "type": str(best.type),
        "name": best.name,
        "expires_at": str(best.expires_at) if best.expires_at else None,
    }

    if best.expires_at is not None and deadline is not None and best.expires_at < deadline:
        return RuleOutcome(
            result=AssessmentResult.DOES_NOT_SATISFY,
            rule_code=RuleCode.REGISTRATION_EXACT,
            explanation_code=ExplanationCode.REGISTRATION_FOUND_EXPIRED,
            params={**company_value, "deadline": str(deadline)},
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=evidence[0].strength,
        )
    return RuleOutcome(
        result=AssessmentResult.SATISFIES,
        rule_code=RuleCode.REGISTRATION_EXACT,
        explanation_code=ExplanationCode.REGISTRATION_FOUND_VALID,
        params=company_value,
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=evidence[0].strength,
    )
