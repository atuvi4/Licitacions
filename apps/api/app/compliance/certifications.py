"""Certificacions: coincidencia exacta normalitzada, mai equivalencia semantica.

`ISO 9001` i `ISO 9001:2015` son la mateixa norma amb la versio explicitada, i
aixo si que es pot normalitzar. `ISO 27001` i `ENS Alt` NO son equivalents,
encara que a vegades es demanin junts: decidir aixo requereix criteri juridic i
el motor no el te. Si el nom no casa exactament un cop normalitzat, no hi ha
coincidencia.

L'altra decisio important es que vol dir no trobar-la. Amb la seccio de
certificacions **completa**, l'absencia es informacio: l'empresa ha declarat
tot el que te. Amb el perfil incomplet, l'absencia no demostra res.
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
    DocumentCategory,
    EvidenceStrength,
    LinkedEntityType,
)

#: `ISO 9001:2015`, `ISO/IEC 27001`, `UNE-EN ISO 14001` -> familia i numero.
_ISO_PATTERN = re.compile(
    r"\b(?:UNE[-\s]?EN[-\s]?)?ISO(?:/IEC)?[-\s]?(\d{4,5})(?:\s*:\s*(\d{4}))?", re.IGNORECASE
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_certification_name(raw: str) -> str:
    """Forma canonica per comparar.

    La versio de la norma (`:2015`) s'ignora: un plec que demana ISO 9001
    accepta ISO 9001:2015. El que NO es toca es el numero de norma.
    """
    if not raw:
        return ""
    text = raw.strip()
    iso = _ISO_PATTERN.search(text)
    if iso:
        return f"iso{iso.group(1)}"
    return _NON_ALNUM.sub("", text.casefold())


def _is_valid_at(expires_at: date | None, deadline: date | None) -> bool | None:
    """`None` si no ho podem saber."""
    if expires_at is None:
        return None
    if deadline is None:
        return expires_at >= date.today()
    return expires_at >= deadline


def assess_certification(
    requirement: Any, snapshot: ProfileSnapshot, deadline: date | None
) -> RuleOutcome:
    value = requirement.value_json or {}
    required_name = (value.get("name") or "").strip()
    if not required_name:
        return needs_review(
            RuleCode.CERTIFICATION_EXACT, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )

    required_key = normalize_certification_name(required_name)
    if not required_key:
        return needs_review(
            RuleCode.CERTIFICATION_EXACT,
            ExplanationCode.CERTIFICATION_NAME_AMBIGUOUS,
            required=required_name,
        )

    matches = [
        cert
        for cert in snapshot.certifications
        if normalize_certification_name(cert.name) == required_key
    ]

    if not matches:
        if snapshot.is_section_complete("certifications"):
            # El perfil declara totes les certificacions i aquesta no hi es.
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.CERTIFICATION_EXACT,
                explanation_code=ExplanationCode.CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE,
                params={"required": required_name},
                mode=AssessmentMode.AUTOMATIC,
                company_value=None,
            )
        # Perfil incomplet: no tenir-la registrada no vol dir no tenir-la.
        return unknown(
            RuleCode.CERTIFICATION_EXACT,
            ExplanationCode.CERTIFICATION_DATA_INCOMPLETE,
            required=required_name,
        )

    requires_validity = bool(value.get("valid_at_deadline"))
    # Es prefereix la que caduca mes tard: es la que millor acredita.
    matches.sort(key=lambda c: (c.expires_at is None, c.expires_at or date.min), reverse=True)
    best = matches[0]

    documents = snapshot.documents_for(LinkedEntityType.COMPANY_CERTIFICATION, best.id)
    certificate = next(
        (d for d in documents if d.category == DocumentCategory.CERTIFICATION), None
    )
    best_document = certificate or (documents[0] if documents else None)
    strength = (
        EvidenceStrength.STRONG
        if certificate
        else (EvidenceStrength.SUPPORTING if best_document else EvidenceStrength.MISSING)
    )
    evidence = (
        EvidenceRef(
            kind=CompanyEvidenceKind.CERTIFICATION,
            entity_id=best.id,
            label=f"{best.name}" + (f" (caduca {best.expires_at})" if best.expires_at else ""),
            strength=strength,
            document_id=best_document.id if best_document else None,
        ),
    )
    company_value = {
        "name": best.name,
        "expires_at": str(best.expires_at) if best.expires_at else None,
        "verification_status": str(best.verification_status),
    }

    if not requires_validity:
        return RuleOutcome(
            result=AssessmentResult.SATISFIES,
            rule_code=RuleCode.CERTIFICATION_EXACT,
            explanation_code=ExplanationCode.CERTIFICATION_FOUND_VALID,
            params={"required": required_name, "found": best.name},
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=strength,
        )

    valid = _is_valid_at(best.expires_at, deadline)
    if valid is None:
        # Sense data de caducitat no podem afirmar que sigui vigent al termini.
        return RuleOutcome(
            result=AssessmentResult.NEEDS_REVIEW,
            rule_code=RuleCode.CERTIFICATION_EXACT,
            explanation_code=ExplanationCode.CERTIFICATION_FOUND_NO_EXPIRY,
            params={"required": required_name, "found": best.name},
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
            evidence=evidence,
            strength=strength,
        )
    if valid:
        return RuleOutcome(
            result=AssessmentResult.SATISFIES,
            rule_code=RuleCode.CERTIFICATION_EXACT,
            explanation_code=ExplanationCode.CERTIFICATION_FOUND_VALID,
            params={
                "required": required_name,
                "found": best.name,
                "expires_at": str(best.expires_at),
                "deadline": str(deadline) if deadline else None,
            },
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=strength,
        )
    return RuleOutcome(
        result=AssessmentResult.DOES_NOT_SATISFY,
        rule_code=RuleCode.CERTIFICATION_EXACT,
        explanation_code=ExplanationCode.CERTIFICATION_FOUND_EXPIRED,
        params={
            "required": required_name,
            "found": best.name,
            "expires_at": str(best.expires_at),
            "deadline": str(deadline) if deadline else None,
        },
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=strength,
    )
