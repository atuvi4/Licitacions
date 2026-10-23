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

from app.compliance.certifications import is_current
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
    RequirementCategory,
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
    (re.compile(r"\brelic?\b", re.I), RegistrationType.RELI),
    (re.compile(r"clasificaci[oó]n|classificaci[oó]", re.I),
     RegistrationType.BUSINESS_CLASSIFICATION),
)
#: Els registres de licitadors: inscrit en qualsevol dels dos, inscrit (159.4.a LCSP).
_BIDDER_REGISTRIES = (RegistrationType.RELI, RegistrationType.ROLECE)
#: Claus estructurades de classificacio (v1 i v2 tipat).
_CLASSIFICATION_KEYS = ("group", "subgroup", "category", "grupo", "subgrupo", "categoria")
#: Marca de l'embolcall del motor (`engine._AdaptedRequirement`): el titol del requisit
#: l'ha escrit el model (`product-v2`), i una classificacio nomes es llegeix del text
#: literal, mai del titol.
TITLE_IS_MODEL_ATTRIBUTE = "_title_is_model"


def _lifecycle(registration: Any) -> str | None:
    lifecycle = getattr(registration, "lifecycle", None)
    return None if lifecycle is None else str(getattr(lifecycle, "value", lifecycle))


def _not_current(rule: str, matches: list[Any], params: dict[str, Any]) -> RuleOutcome:
    """Consta, pero en tramit o retirada (`lifecycle`): avui no acredita res (v6)."""
    return RuleOutcome(
        result=AssessmentResult.DOES_NOT_SATISFY,
        rule_code=rule,
        explanation_code=ExplanationCode.REGISTRATION_NOT_CURRENT,
        params={
            **params,
            "name": matches[0].name,
            "lifecycle": sorted({_lifecycle(reg) or "" for reg in matches}),
        },
        mode=AssessmentMode.AUTOMATIC,
    )


def infer_registration_type(raw: str | None) -> RegistrationType | None:
    if not raw:
        return None
    for pattern, kind in _REGISTRY_HINTS:
        if pattern.search(raw):
            return kind
    return None


def infer_registration_types(raw: str | None) -> list[RegistrationType]:
    """Tots els registres que nomena un text, en l'ordre de les pistes (v6).

    «RELI o ROLECE» en nomena dos: n'hi ha prou amb un.
    """
    if not raw:
        return []
    return [kind for pattern, kind in _REGISTRY_HINTS if pattern.search(raw)]


def _expired(registration: Any, deadline: date | None) -> bool:
    return (
        registration.expires_at is not None
        and deadline is not None
        and registration.expires_at < deadline
    )


def _registration_evidence(best: Any, snapshot: ProfileSnapshot) -> tuple[EvidenceRef, ...]:
    documents = snapshot.documents_for(LinkedEntityType.COMPANY_REGISTRATION, best.id)
    return (
        EvidenceRef(
            kind=CompanyEvidenceKind.REGISTRATION,
            entity_id=best.id,
            label=f"{best.name} ({best.type})",
            strength=EvidenceStrength.STRONG if documents else EvidenceStrength.MISSING,
            document_id=documents[0].id if documents else None,
        ),
    )


def assess_registration(
    requirement: Any, snapshot: ProfileSnapshot, deadline: date | None
) -> RuleOutcome:
    value = requirement.value_json or {}
    required_name = (value.get("name") or requirement.title or "").strip()

    if value.get("type") == "registry_any_of":
        # v6, complement `product-v2`: «RELI o ROLECE» es qualsevol dels dos.
        types = [
            RegistrationType(kind)
            for kind in value.get("types") or []
            if kind in {t.value for t in RegistrationType}
        ]
        return _assess_registry_types(types, snapshot, deadline)

    if value.get("type") == "business_classification" or any(
        value.get(key) for key in _CLASSIFICATION_KEYS
    ):
        return assess_classification(requirement, snapshot, deadline)

    types = infer_registration_types(value.get("type") or required_name)
    if (
        getattr(requirement, "_v5_normalized", False) is True
        and value.get("type") == "sector_operator_registry"
    ):
        # Complement `product-v2`: el plec nomena el registre d'operadors (CNMC), pel
        # nom o, des de la v6, per la capacitat legal de prestar serveis de
        # telecomunicacions. Cap altre cami no hi arriba.
        types = [RegistrationType.SECTOR_OPERATOR_REGISTRY]

    if RegistrationType.BUSINESS_CLASSIFICATION in types:
        registries = [kind for kind in types if kind in _BIDDER_REGISTRIES]
        if not registries or _classifications_in(requirement):
            # v6: una classificacio es compara (grup, subgrup, categoria). Pel sol
            # tipus no compleix MAI: era un fals positiu amb el valor nul.
            return assess_classification(requirement, snapshot, deadline)
        # «Inscripcio al ROLECE (amb classificacio)» sense dir quina: el registre.
        types = registries

    if not types:
        return needs_review(
            RuleCode.REGISTRATION_EXACT,
            ExplanationCode.RULE_NOT_AUTOMATABLE,
            required=required_name[:200],
        )
    return _assess_registry_types(types, snapshot, deadline)


def _assess_registry_types(
    types: list[RegistrationType], snapshot: ProfileSnapshot, deadline: date | None
) -> RuleOutcome:
    """Inscrit en algun dels registres (un de sol es el cas de sempre)."""
    rule = RuleCode.REGISTRATION_ANY_OF if len(types) > 1 else RuleCode.REGISTRATION_EXACT
    if not types:
        return needs_review(rule, ExplanationCode.RULE_NOT_AUTOMATABLE)
    required = [str(kind) for kind in types]
    params_required: dict[str, Any] = (
        {"required_types": required} if len(types) > 1 else {"required_type": required[0]}
    )
    found = [reg for reg in snapshot.registrations if reg.type in types]
    # v6: nomes compta el que es vigent (`lifecycle`), com a les certificacions.
    matches = [reg for reg in found if is_current(reg)]
    if found and not matches:
        covered = {reg.type for reg in found}
        if snapshot.is_section_complete("registrations") or all(kind in covered for kind in types):
            return _not_current(rule, found, params_required)
        # «RELI o ROLECE» amb el ROLECE retirat i el perfil incomplet: l'altre hi pot ser
        # sense constar. Tenir-ne un de retirat no fa el veredicte mes dur que no tenir-ne.
        return unknown(
            rule,
            ExplanationCode.REGISTRATION_DATA_INCOMPLETE,
            **params_required,
            not_current=sorted(str(kind) for kind in covered),
        )
    if not matches:
        if snapshot.is_section_complete("registrations"):
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=rule,
                explanation_code=ExplanationCode.REGISTRATION_NOT_FOUND_PROFILE_COMPLETE,
                params=params_required,
                mode=AssessmentMode.AUTOMATIC,
            )
        return unknown(rule, ExplanationCode.REGISTRATION_DATA_INCOMPLETE, **params_required)

    matches.sort(key=lambda r: (r.expires_at is None, r.expires_at or date.min), reverse=True)
    best = matches[0]
    evidence = _registration_evidence(best, snapshot)
    company_value = {
        "type": str(best.type),
        "name": best.name,
        "expires_at": str(best.expires_at) if best.expires_at else None,
    }

    if _expired(best, deadline):
        return RuleOutcome(
            result=AssessmentResult.DOES_NOT_SATISFY,
            rule_code=rule,
            explanation_code=ExplanationCode.REGISTRATION_FOUND_EXPIRED,
            params={**company_value, "deadline": str(deadline)},
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=evidence[0].strength,
        )
    return RuleOutcome(
        result=AssessmentResult.SATISFIES,
        rule_code=rule,
        explanation_code=ExplanationCode.REGISTRATION_FOUND_VALID,
        params={**company_value, **({"required_types": required} if len(types) > 1 else {})},
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=evidence[0].strength,
    )


def assess_registry_documentation(
    snapshot: ProfileSnapshot, deadline: date | None, *, literal_subject: bool
) -> RuleOutcome:
    """La documentacio que aporta qui JA es inscrit al RELI o al ROLECE (v6).

    No exigeix estar-hi inscrit: si l'empresa hi es, pot aportar-ne el certificat
    (compleix); si el perfil complet diu que no, la clausula no li aplica. Si la
    marca «empreses inscrites» nomes surt del titol del model, i no del text literal,
    el «no aplica» seria massa agosarat: revisio, com a la v5.
    """
    rule = RuleCode.REGISTRATION_ANY_OF
    registered = [
        reg
        for reg in snapshot.registrations
        if reg.type in _BIDDER_REGISTRIES and is_current(reg) and not _expired(reg, deadline)
    ]
    if registered:
        registered.sort(
            key=lambda r: (r.expires_at is None, r.expires_at or date.min), reverse=True
        )
        best = registered[0]
        evidence = _registration_evidence(best, snapshot)
        company_value = {
            "type": str(best.type),
            "name": best.name,
            "expires_at": str(best.expires_at) if best.expires_at else None,
        }
        return RuleOutcome(
            result=AssessmentResult.SATISFIES,
            rule_code=rule,
            explanation_code=ExplanationCode.REGISTRATION_DOCUMENTATION_AVAILABLE,
            params=company_value,
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=evidence[0].strength,
        )
    if not snapshot.is_section_complete("registrations"):
        return unknown(
            rule,
            ExplanationCode.REGISTRATION_DATA_INCOMPLETE,
            required_types=[str(kind) for kind in _BIDDER_REGISTRIES],
        )
    if not literal_subject:
        return needs_review(rule, ExplanationCode.REGISTRATION_DOCUMENTATION_ONLY)
    return RuleOutcome(
        result=AssessmentResult.NOT_APPLICABLE,
        rule_code=rule,
        explanation_code=ExplanationCode.REGISTRATION_DOCUMENTATION_NOT_APPLICABLE,
        mode=AssessmentMode.AUTOMATIC,
    )


# --------------------------------------------------------------------------- #
# Classificacio empresarial (v6)                                                #
# --------------------------------------------------------------------------- #


def _classifications_in(requirement: Any) -> list[Any]:
    """Les classificacions que diu el valor o el titol d'un requisit.

    El titol que ha escrit el model (`product-v2`, `TITLE_IS_MODEL_ATTRIBUTE`) no
    compta: una classificacio es llegeix del text literal o no es llegeix (v6).
    """
    from app.compliance.normalize import parse_classifications

    value = requirement.value_json or {}
    # La categoria del requisit ja diu que es una classificacio: «V-4-1» sol es llegeix.
    context_required = (
        getattr(requirement, "category", None) != RequirementCategory.BUSINESS_CLASSIFICATION
    )
    texts = [value.get("name")]
    if not getattr(requirement, TITLE_IS_MODEL_ATTRIBUTE, False):
        texts.append(getattr(requirement, "title", None))
    found: list[Any] = []
    for text in texts:
        for item in parse_classifications(str(text or ""), context_required=context_required):
            if item not in found:
                found.append(item)
    return found


def _required_classification(requirement: Any) -> tuple[Any | None, list[Any]]:
    """La classificacio exigida, i totes les trobades si n'hi ha mes d'una."""
    from dataclasses import replace

    from app.compliance.normalize import parse_classifications

    value = requirement.value_json or {}
    group = value.get("group") or value.get("grupo")
    subgroup = value.get("subgroup") or value.get("subgrupo")
    category = value.get("category") if value.get("category") is not None else value.get(
        "categoria"
    )
    if group and subgroup:
        # Claus estructurades (normalitzador v6, v1, v2 tipat). La categoria pot ser
        # nombre o lletra antiga; es llegeix amb el mateix lector que el text.
        text = f"grup {group} subgrup {subgroup}" + (
            f" categoria {category}" if category not in (None, "") else ""
        )
        parsed = parse_classifications(text)
        if not parsed:
            return None, []
        required = parsed[0]
        if value.get("category_max") is not None:
            # El normalitzador ja ha traduit una lletra antiga amb dues equivalencies.
            required = replace(required, category_max=int(value["category_max"]))
        return required, parsed
    found = _classifications_in(requirement)
    by_subgroup = {(c.group, c.subgroup) for c in found}
    categories = {(c.category, c.category_max) for c in found if c.category is not None}
    if (
        len(by_subgroup) == 1
        and len(categories) <= 1
        and not any(c.category_invalid for c in found)
    ):
        (only_group, only_subgroup), = by_subgroup
        only = next(c for c in found if (c.group, c.subgroup) == (only_group, only_subgroup))
        category, category_max = next(iter(categories)) if categories else (None, None)
        return replace(only, category=category, category_max=category_max), found
    return None, found


def assess_classification(
    requirement: Any, snapshot: ProfileSnapshot, deadline: date | None
) -> RuleOutcome:
    """Grup i subgrup exactes i categoria igual o superior (RD 773/2015).

    La classificacio de l'empresa es llegeix del nom o del numero del registre
    (`V-4-1`, «Grup V, Subgrup 4, Categoria 1»): el perfil no te camps propis. Pel
    sol tipus de registre no es compleix mai. Nomes compta la vigent (`lifecycle`).
    Si el plec dona una lletra antiga amb dues equivalencies (obres F, serveis D),
    una categoria entre la minima i la maxima la revisa una persona.
    """
    from app.compliance.normalize import parse_classifications

    rule = RuleCode.BUSINESS_CLASSIFICATION_GTE
    required, found = _required_classification(requirement)
    classifications = [
        registration
        for registration in snapshot.registrations
        if registration.type == RegistrationType.BUSINESS_CLASSIFICATION
    ]
    if required is None:
        if len(found) > 1 or any(item.category_invalid for item in found):
            return needs_review(
                rule,
                ExplanationCode.CLASSIFICATION_AMBIGUOUS,
                found=[item.as_dict() for item in found],
            )
        if not any(is_current(registration) for registration in classifications):
            # Sense saber quina demana, una empresa que no en te cap de vigent no en
            # compleix cap: el veredicte de sempre (mai «compleix», que no hi ha res a
            # trobar), o «no vigent» si n'hi ha alguna en tramit.
            return _assess_registry_types(
                [RegistrationType.BUSINESS_CLASSIFICATION], snapshot, deadline
            )
        # L'empresa en te alguna, pero el plec no diu quina: no es compara pel tipus.
        return needs_review(rule, ExplanationCode.CLASSIFICATION_NOT_STATED)

    params: dict[str, Any] = {"required": required.as_dict()}
    held: list[tuple[Any, Any]] = []
    unreadable: list[Any] = []
    dormant: list[Any] = []
    for registration in classifications:
        text = " ".join(
            str(part)
            for part in (registration.name, getattr(registration, "registration_number", None))
            if part
        )
        # El tipus del registre ja diu que es una classificacio: «V-4-1» sol es llegeix.
        items = parse_classifications(text, context_required=False)
        if not is_current(registration):
            if any(
                item.group == required.group and item.subgroup == required.subgroup
                for item in items
            ):
                dormant.append(registration)
            continue
        if not items:
            unreadable.append(registration)
        held.extend((registration, item) for item in items)

    same = [
        (registration, item)
        for registration, item in held
        if item.group == required.group and item.subgroup == required.subgroup
    ]
    if not same and dormant and not unreadable:
        # La te, pero en tramit o retirada: avui no acredita res. (Si n'hi ha una de
        # vigent que no es pot llegir, pot ser la que acredita: revisio, mes avall.)
        return _not_current(rule, dormant, params)
    current = [pair for pair in same if not _expired(pair[0], deadline)]
    if same and not current:
        registration = same[0][0]
        return RuleOutcome(
            result=AssessmentResult.DOES_NOT_SATISFY,
            rule_code=rule,
            explanation_code=ExplanationCode.REGISTRATION_FOUND_EXPIRED,
            params={
                **params,
                "name": registration.name,
                "expires_at": str(registration.expires_at),
                "deadline": str(deadline) if deadline else None,
            },
            mode=AssessmentMode.AUTOMATIC,
        )
    if current:
        registration, best = max(current, key=lambda pair: pair[1].category or 0)
        evidence = _registration_evidence(registration, snapshot)
        company_value = {
            "type": str(registration.type),
            "name": registration.name,
            "classification": best.as_dict(),
            "expires_at": str(registration.expires_at) if registration.expires_at else None,
        }
        if required.category is not None and best.category is None:
            # L'empresa te el subgrup pero el perfil no en diu la categoria.
            return needs_review(
                rule,
                ExplanationCode.CLASSIFICATION_COMPANY_UNREADABLE,
                **params,
                held=best.as_dict(),
            )
        top = required.category_max or required.category
        if required.category is None or (best.category or 0) >= (top or 0):
            return RuleOutcome(
                result=AssessmentResult.SATISFIES,
                rule_code=rule,
                explanation_code=ExplanationCode.CLASSIFICATION_MET,
                params={**params, "held": best.as_dict()},
                mode=AssessmentMode.AUTOMATIC,
                company_value=company_value,
                evidence=evidence,
                strength=evidence[0].strength,
            )
        if (best.category or 0) >= required.category:
            # Entre les dues equivalencies d'una lletra antiga: no se sap quina demana.
            return needs_review(
                rule,
                ExplanationCode.CLASSIFICATION_CATEGORY_AMBIGUOUS,
                **params,
                held=best.as_dict(),
            )
        return RuleOutcome(
            result=AssessmentResult.DOES_NOT_SATISFY,
            rule_code=rule,
            explanation_code=ExplanationCode.CLASSIFICATION_CATEGORY_BELOW,
            params={**params, "held": best.as_dict()},
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=evidence[0].strength,
        )
    if unreadable:
        # Pot ser la que acredita, pero no se'n pot llegir el grup ni el subgrup.
        return needs_review(
            rule,
            ExplanationCode.CLASSIFICATION_COMPANY_UNREADABLE,
            **params,
            names=[registration.name for registration in unreadable],
        )
    if snapshot.is_section_complete("registrations"):
        return RuleOutcome(
            result=AssessmentResult.DOES_NOT_SATISFY,
            rule_code=rule,
            explanation_code=ExplanationCode.CLASSIFICATION_NOT_HELD,
            params={**params, "held": [item.as_dict() for _, item in held]},
            mode=AssessmentMode.AUTOMATIC,
        )
    return unknown(
        rule,
        ExplanationCode.CLASSIFICATION_NOT_HELD,
        section_complete=False,
        **params,
    )
