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
from app.compliance.specs import read_certification
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
    requirement: Any,
    snapshot: ProfileSnapshot,
    deadline: date | None = None,
    tender: Any = None,
) -> RuleOutcome:
    """Coincidencia exacta normalitzada; l'equivalencia la decideix una persona.

    Quan el plec diu "o equivalent", no tenir la certificacio exacta deixa de
    poder ser un incompliment: pot ser que l'empresa n'acrediti una que hi
    equivalgui, i decidir-ho es criteri juridic.
    """
    spec = read_certification(requirement)
    if not spec.names:
        return needs_review(
            RuleCode.CERTIFICATION_EXACT, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )

    required_keys = {
        normalize_certification_name(name): name
        for name in spec.names
        if normalize_certification_name(name)
    }
    if not required_keys:
        return needs_review(
            RuleCode.CERTIFICATION_EXACT,
            ExplanationCode.CERTIFICATION_NAME_AMBIGUOUS,
            required=list(spec.names),
        )
    required_name = spec.names[0]

    matches = [
        cert
        for cert in snapshot.certifications
        if normalize_certification_name(cert.name) in required_keys
    ]

    if not matches:
        if spec.equivalence_allowed and snapshot.certifications:
            # "ISO 9001 o equivalent" i l'empresa en te d'altres. Quina hi
            # equival no ho decideix una regla: no tenir l'exacta NO pot
            # convertir-se en un incompliment quan el plec admet equivalents.
            return needs_review(
                RuleCode.CERTIFICATION_EXACT,
                ExplanationCode.CERTIFICATION_EQUIVALENCE_REVIEW,
                required=list(spec.names),
                company_certifications=sorted(c.name for c in snapshot.certifications),
            )
        if spec.equivalence_allowed:
            # Admet equivalents i l'empresa no en te cap de registrada: tampoc
            # es pot afirmar l'incompliment sense saber que hi equivaldria.
            return unknown(
                RuleCode.CERTIFICATION_EXACT,
                ExplanationCode.CERTIFICATION_EQUIVALENCE_REVIEW,
                required=list(spec.names),
            )
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

    requires_validity = spec.valid_at_deadline
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


# --------------------------------------------------------------------------- #
# ENS (nomes per al complement product-v2, v5)                                  #
# --------------------------------------------------------------------------- #


def assess_ens(
    reading: Any,
    snapshot: ProfileSnapshot,
    deadline: date | None = None,
) -> RuleOutcome:
    """L'ENS que nomena un requisit `product-v2`, per categories (RD 311/2022).

    Nomes hi arriba el complement `app.compliance.normalize`: la comparacio de
    noms de sempre (`normalize_certification_name`) no es toca. Les regles:

    * categoria del plec desconeguda -> revisio (`CERTIFICATION_LEVEL_NOT_STATED`),
      mai una suposicio;
    * una certificacio de l'empresa sense categoria llegible no casa amb cap altra;
    * mateixa categoria -> compleix;
    * l'empresa en te una d'inferior -> no compleix;
    * l'empresa en te una de superior -> compleix nomes si el plec diu «o
      superior» / «com a mínim»; si no, revisio (mai «no compleix»).
    """
    from app.compliance.normalize import ENS_ORDER, ens_level, names_ens

    certificates = [cert for cert in snapshot.certifications if names_ens(cert.name)]
    names = sorted(cert.name for cert in certificates)
    if reading.level is None:
        return needs_review(
            RuleCode.CERTIFICATION_EXACT,
            ExplanationCode.CERTIFICATION_LEVEL_NOT_STATED,
            scheme="ENS",
            side="tender",
            company_certifications=names,
        )
    required = ENS_ORDER[reading.level]
    params: dict[str, Any] = {
        "scheme": "ENS",
        "required_level": reading.level,
        "or_higher": reading.or_higher,
        "company_certifications": names,
    }
    if not certificates:
        if reading.equivalence_allowed:
            return unknown(
                RuleCode.CERTIFICATION_EXACT,
                ExplanationCode.CERTIFICATION_EQUIVALENCE_REVIEW,
                **params,
            )
        if snapshot.is_section_complete("certifications"):
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.CERTIFICATION_EXACT,
                explanation_code=ExplanationCode.CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE,
                params=params,
                mode=AssessmentMode.AUTOMATIC,
            )
        return unknown(
            RuleCode.CERTIFICATION_EXACT, ExplanationCode.CERTIFICATION_DATA_INCOMPLETE, **params
        )

    levels = []
    for cert in certificates:
        level, _ambiguous = ens_level(cert.name)
        levels.append((cert, ENS_ORDER.get(level) if level else None))
    known = [(cert, order) for cert, order in levels if order is not None]
    matching = [cert for cert, order in known if order == required]
    higher = [cert for cert, order in known if order > required]
    if matching or (higher and reading.or_higher):
        best = (matching or higher)[0]
        return _ens_found(best, snapshot, deadline, reading, params)
    if higher:
        return needs_review(
            RuleCode.CERTIFICATION_EXACT,
            ExplanationCode.CERTIFICATION_LEVEL_HIGHER_REVIEW,
            **params,
        )
    if any(order is None for _, order in levels):
        # Una certificacio sense categoria llegible pot ser la que acredita.
        return needs_review(
            RuleCode.CERTIFICATION_EXACT,
            ExplanationCode.CERTIFICATION_LEVEL_NOT_STATED,
            side="company",
            **params,
        )
    if reading.equivalence_allowed:
        return needs_review(
            RuleCode.CERTIFICATION_EXACT,
            ExplanationCode.CERTIFICATION_EQUIVALENCE_REVIEW,
            **params,
        )
    return RuleOutcome(
        result=AssessmentResult.DOES_NOT_SATISFY,
        rule_code=RuleCode.CERTIFICATION_EXACT,
        explanation_code=ExplanationCode.CERTIFICATION_LEVEL_BELOW,
        params=params,
        mode=AssessmentMode.AUTOMATIC,
    )


def _ens_found(
    best: Any, snapshot: ProfileSnapshot, deadline: date | None, reading: Any, params: dict
) -> RuleOutcome:
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
        "verification_status": str(getattr(best, "verification_status", None)),
    }
    params = {**params, "found": best.name}
    result, explanation, mode = (
        AssessmentResult.SATISFIES,
        ExplanationCode.CERTIFICATION_FOUND_VALID,
        AssessmentMode.AUTOMATIC,
    )
    if reading.valid_at_deadline:
        valid = _is_valid_at(best.expires_at, deadline)
        if valid is None:
            result, explanation, mode = (
                AssessmentResult.NEEDS_REVIEW,
                ExplanationCode.CERTIFICATION_FOUND_NO_EXPIRY,
                AssessmentMode.REVIEW_REQUIRED,
            )
        elif not valid:
            result, explanation = (
                AssessmentResult.DOES_NOT_SATISFY,
                ExplanationCode.CERTIFICATION_FOUND_EXPIRED,
            )
    return RuleOutcome(
        result=result,
        rule_code=RuleCode.CERTIFICATION_EXACT,
        explanation_code=explanation,
        params=params,
        mode=mode,
        company_value=company_value,
        evidence=evidence,
        strength=strength,
    )
