"""Certificacions: coincidencia exacta normalitzada, mai equivalencia semantica.

`ISO 9001` i `ISO 9001:2015` son la mateixa norma amb la versio explicitada, i
aixo si que es pot normalitzar. `ISO 27001` i `ENS Alt` NO son equivalents,
encara que a vegades es demanin junts: decidir aixo requereix criteri juridic i
el motor no el te. Si el nom no casa exactament un cop normalitzat, no hi ha
coincidencia.

L'altra decisio important es que vol dir no trobar-la. Amb la seccio de
certificacions **completa**, l'absencia es informacio: l'empresa ha declarat
tot el que te. Amb el perfil incomplet, l'absencia no demostra res.

v6 (`company-compliance-v6`):

* **Nomes compta el que es vigent.** Una certificacio en tramit o retirada
  (`lifecycle`) no acredita res; si nomes hi ha aquella, «no compleix» amb el motiu
  (`CERTIFICATION_NOT_CURRENT`).
* **La norma es llegeix del vocabulari** (`standard_code`, p. ex. `ISO_20000`) i,
  si no n'hi ha, del nom.
* **Diverses normes en un requisit** (`all_of` o operador `all_of`) s'exigeixen
  totes; una llista de la v1 sense aquesta marca continua sent «qualsevol de»
  (operador `one_of`).
* **«O equivalent»** sense la norma, amb la seccio completa: «no compleix», pero
  nomes com a bloqueig POTENCIAL (`potential_only`): si una altra certificacio hi
  equival ho confirma o ho desfa una persona. Igual si la norma hi es en tramit,
  retirada o caducada: el dubte continua sent quina altra hi equival.
* **ENS per categories en tots els perfils** (`basica < mitjana < alta`): igual o
  superior compleix, amb el `level` estructurat primer i el nom com a reserva. Si
  n'hi ha diverses, la que millor acredita (vigent abans que caducada).
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
from app.compliance.specs import CertificationSpec, read_certification
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    CompanyEvidenceKind,
    DocumentCategory,
    EvidenceStrength,
    KnowledgeLifecycle,
    LinkedEntityType,
)

#: `ISO 9001:2015`, `ISO/IEC 27001`, `UNE-EN ISO 14001` -> familia i numero.
_ISO_PATTERN = re.compile(
    r"\b(?:UNE[-\s]?EN[-\s]?)?ISO(?:/IEC)?[-\s]?(\d{4,5})(?:\s*:\s*(\d{4}))?", re.IGNORECASE
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
#: `ISO_9001`, `ISO_20000` del vocabulari global (`certification_standards`).
_STANDARD_ISO = re.compile(r"^ISO_(\d{4,5})$", re.IGNORECASE)
#: Valors estructurats de categoria de l'ENS (vocabulari `basic|medium|high`) i les
#: formes en catala i castella que pot dur un perfil importat.
_ENS_STRUCTURED_LEVELS = {
    "basic": "basica", "basica": "basica", "basico": "basica",
    "medium": "mitjana", "mitjana": "mitjana", "mitja": "mitjana", "media": "mitjana",
    "medio": "mitjana",
    "high": "alta", "alta": "alta", "alt": "alta", "alto": "alta",
}  # fmt: skip
#: Ordre de la forca d'una evidencia, per quedar-se amb la mes feble d'un conjunt.
_STRENGTH_RANK = {
    EvidenceStrength.MISSING: 0,
    EvidenceStrength.SUPPORTING: 1,
    EvidenceStrength.STRONG: 2,
}
#: Entre diverses certificacions ENS que podrien acreditar: la que millor ho fa.
_ENS_RESULT_RANK = {
    AssessmentResult.SATISFIES: 0,
    AssessmentResult.NEEDS_REVIEW: 1,
    AssessmentResult.DOES_NOT_SATISFY: 2,
}
#: Marca d'un «no compleix» que nomes pot ser un bloqueig POTENCIAL fins que una
#: persona el revisi: el dubte es del costat de l'empresa (quina hi equival), no
#: de la font, i verificar el requisit no el confirma (`derive_blocker_status`).
POTENTIAL_ONLY_KEY = "potential_only"


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


def certification_keys(cert: Any) -> set[str]:
    """Les formes canoniques amb que una certificacio de l'empresa pot casar.

    El codi del vocabulari (`standard_code`) es la dada estructurada; el nom, la
    reserva de sempre. Totes dues compten: un perfil importat pot tenir nomes una.
    """
    keys = {normalize_certification_name(getattr(cert, "name", "") or "")}
    code = (getattr(cert, "standard_code", None) or "").strip()
    if code:
        iso = _STANDARD_ISO.match(code)
        keys.add(f"iso{iso.group(1)}" if iso else _NON_ALNUM.sub("", code.casefold()))
    keys.discard("")
    return keys


def is_current(cert: Any) -> bool:
    """Una certificacio en tramit o retirada no acredita res (`lifecycle`).

    Sense el camp (perfils anteriors a la fase 2 del coneixement, proves), es vigent.
    """
    lifecycle = getattr(cert, "lifecycle", None)
    if lifecycle is None:
        return True
    return str(getattr(lifecycle, "value", lifecycle)) == KnowledgeLifecycle.CURRENT.value


def _lifecycle(cert: Any) -> str | None:
    lifecycle = getattr(cert, "lifecycle", None)
    return None if lifecycle is None else str(getattr(lifecycle, "value", lifecycle))


def _is_valid_at(expires_at: date | None, deadline: date | None) -> bool | None:
    """`None` si no ho podem saber."""
    if expires_at is None:
        return None
    if deadline is None:
        return expires_at >= date.today()
    return expires_at >= deadline


def _evidence_of(best: Any, snapshot: ProfileSnapshot) -> tuple[tuple[EvidenceRef, ...], Any]:
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
    return evidence, strength


def _company_value(best: Any) -> dict[str, Any]:
    return {
        "name": best.name,
        "expires_at": str(best.expires_at) if best.expires_at else None,
        "verification_status": str(getattr(best, "verification_status", None)),
    }


def assess_certification(
    requirement: Any,
    snapshot: ProfileSnapshot,
    deadline: date | None = None,
    tender: Any = None,
) -> RuleOutcome:
    """Coincidencia exacta normalitzada, norma a norma.

    Un requisit que nomena l'ENS es llegeix per categories (`assess_ens`), sigui
    quin sigui el perfil d'extraccio. Amb diverses normes, `all_required` diu si
    calen totes o n'hi ha prou amb una.
    """
    from app.compliance.normalize import names_ens

    spec = read_certification(requirement)
    if not spec.names:
        return needs_review(
            RuleCode.CERTIFICATION_EXACT, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )

    required: dict[str, str] = {}
    for name in spec.names:
        key = f"ens|{name.casefold()}" if names_ens(name) else normalize_certification_name(name)
        if key:
            required.setdefault(key, name)
    if not required:
        return needs_review(
            RuleCode.CERTIFICATION_EXACT,
            ExplanationCode.CERTIFICATION_NAME_AMBIGUOUS,
            required=list(spec.names),
        )

    outcomes = [(name, _assess_one(name, spec, snapshot, deadline)) for name in required.values()]
    if len(outcomes) == 1:
        return outcomes[0][1]
    return _combine(outcomes, all_required=spec.all_required)


def _assess_one(
    name: str, spec: CertificationSpec, snapshot: ProfileSnapshot, deadline: date | None
) -> RuleOutcome:
    """Una sola norma contra les certificacions del perfil."""
    from app.compliance.normalize import EnsReading, ens_reading, names_ens

    if names_ens(name):
        level, _ambiguous, or_higher = ens_reading(name)
        return assess_ens(
            EnsReading(
                level=level,
                or_higher=or_higher,
                equivalence_allowed=spec.equivalence_allowed,
                valid_at_deadline=spec.valid_at_deadline,
            ),
            snapshot,
            deadline,
        )

    key = normalize_certification_name(name)
    matches = [cert for cert in snapshot.certifications if key in certification_keys(cert)]
    current = [cert for cert in matches if is_current(cert)]

    if not current and matches:
        lifecycle = sorted({_lifecycle(cert) or "" for cert in matches})
        if spec.equivalence_allowed:
            # «O equivalent»: que aquesta norma estigui en tramit no diu res de les
            # equivalents. Com si no hi fos: «no compleix» nomes potencial o «sense dades».
            return _not_found(name, spec, snapshot, not_current=lifecycle)
        # Consta, pero en tramit o retirada: avui no acredita res, i el perfil ho diu.
        return RuleOutcome(
            result=AssessmentResult.DOES_NOT_SATISFY,
            rule_code=RuleCode.CERTIFICATION_EXACT,
            explanation_code=ExplanationCode.CERTIFICATION_NOT_CURRENT,
            params={"required": name, "found": matches[0].name, "lifecycle": lifecycle},
            mode=AssessmentMode.AUTOMATIC,
        )

    if not current:
        return _not_found(name, spec, snapshot)

    # Es prefereix la que caduca mes tard: es la que millor acredita.
    current.sort(key=lambda c: (c.expires_at is None, c.expires_at or date.min), reverse=True)
    best = current[0]
    evidence, strength = _evidence_of(best, snapshot)
    company_value = _company_value(best)

    if not spec.valid_at_deadline:
        return RuleOutcome(
            result=AssessmentResult.SATISFIES,
            rule_code=RuleCode.CERTIFICATION_EXACT,
            explanation_code=ExplanationCode.CERTIFICATION_FOUND_VALID,
            params={"required": name, "found": best.name},
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
            params={"required": name, "found": best.name},
            mode=AssessmentMode.REVIEW_REQUIRED,
            company_value=company_value,
            evidence=evidence,
            strength=strength,
        )
    params = {
        "required": name,
        "found": best.name,
        "expires_at": str(best.expires_at),
        "deadline": str(deadline) if deadline else None,
    }
    if valid:
        return RuleOutcome(
            result=AssessmentResult.SATISFIES,
            rule_code=RuleCode.CERTIFICATION_EXACT,
            explanation_code=ExplanationCode.CERTIFICATION_FOUND_VALID,
            params=params,
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=strength,
        )
    if spec.equivalence_allowed:
        # Caducada, pero el plec admet equivalents: el bloqueig nomes el confirma
        # una persona (una altra certificacio vigent hi pot equivaler).
        params[POTENTIAL_ONLY_KEY] = True
    return RuleOutcome(
        result=AssessmentResult.DOES_NOT_SATISFY,
        rule_code=RuleCode.CERTIFICATION_EXACT,
        explanation_code=ExplanationCode.CERTIFICATION_FOUND_EXPIRED,
        params=params,
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=strength,
    )


def _not_found(
    name: str,
    spec: CertificationSpec,
    snapshot: ProfileSnapshot,
    *,
    not_current: list[str] | None = None,
) -> RuleOutcome:
    """La norma no consta al perfil com a vigent (`not_current`: en tramit o retirada)."""
    complete = snapshot.is_section_complete("certifications")
    extra: dict[str, Any] = {"not_current": not_current} if not_current else {}
    if spec.equivalence_allowed:
        if complete:
            # «O equivalent» i el perfil, complet, no la te: «no compleix», pero
            # nomes POTENCIAL. Si alguna de les que te hi equival ho decideix una
            # persona (confirma o desfa el bloqueig); el motor no ho pot dir.
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.CERTIFICATION_EXACT,
                explanation_code=ExplanationCode.CERTIFICATION_EQUIVALENT_NOT_DECLARED,
                params={
                    "required": name,
                    "equivalence_allowed": True,
                    "company_certifications": sorted(
                        c.name for c in snapshot.certifications if is_current(c)
                    ),
                    **extra,
                    POTENTIAL_ONLY_KEY: True,
                },
                mode=AssessmentMode.AUTOMATIC,
            )
        return unknown(
            RuleCode.CERTIFICATION_EXACT,
            ExplanationCode.CERTIFICATION_DATA_INCOMPLETE,
            required=name,
            equivalence_allowed=True,
            **extra,
        )
    if complete:
        # El perfil declara totes les certificacions i aquesta no hi es.
        return RuleOutcome(
            result=AssessmentResult.DOES_NOT_SATISFY,
            rule_code=RuleCode.CERTIFICATION_EXACT,
            explanation_code=ExplanationCode.CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE,
            params={"required": name},
            mode=AssessmentMode.AUTOMATIC,
            company_value=None,
        )
    # Perfil incomplet: no tenir-la registrada no vol dir no tenir-la.
    return unknown(
        RuleCode.CERTIFICATION_EXACT,
        ExplanationCode.CERTIFICATION_DATA_INCOMPLETE,
        required=name,
    )


def _combine(outcomes: list[tuple[str, RuleOutcome]], *, all_required: bool) -> RuleOutcome:
    """Diverses normes en un requisit: totes (`all_required`) o qualsevol."""
    norms = [
        {"required": name, "result": str(outcome.result), "explanation": outcome.explanation_code}
        for name, outcome in outcomes
    ]
    satisfied = [(n, o) for n, o in outcomes if o.result == AssessmentResult.SATISFIES]
    failed = [(n, o) for n, o in outcomes if o.result == AssessmentResult.DOES_NOT_SATISFY]
    reviews = [(n, o) for n, o in outcomes if o.result == AssessmentResult.NEEDS_REVIEW]
    evidence = tuple(ref for _, outcome in outcomes for ref in outcome.evidence)

    if not all_required:
        if satisfied:
            _name, outcome = satisfied[0]
            return RuleOutcome(
                result=AssessmentResult.SATISFIES,
                rule_code=RuleCode.CERTIFICATION_SET,
                explanation_code=outcome.explanation_code,
                params={**outcome.params, "all_required": False, "norms": norms},
                mode=AssessmentMode.AUTOMATIC,
                company_value=outcome.company_value,
                evidence=outcome.evidence,
                strength=outcome.strength,
            )
        if len(failed) == len(outcomes):
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.CERTIFICATION_SET,
                explanation_code=ExplanationCode.CERTIFICATION_SOME_MISSING,
                params={
                    "all_required": False,
                    "missing": [n for n, _ in failed],
                    "norms": norms,
                    **_potential_only(failed, all_required=False),
                },
                mode=AssessmentMode.AUTOMATIC,
            )
    else:
        if len(satisfied) == len(outcomes):
            weakest = min(
                (o.strength for _, o in satisfied), key=lambda s: _STRENGTH_RANK.get(s, 0)
            )
            return RuleOutcome(
                result=AssessmentResult.SATISFIES,
                rule_code=RuleCode.CERTIFICATION_SET,
                explanation_code=ExplanationCode.CERTIFICATION_ALL_FOUND,
                params={
                    "all_required": True,
                    "found": [o.params.get("found") for _, o in satisfied],
                    "norms": norms,
                },
                mode=AssessmentMode.AUTOMATIC,
                evidence=evidence,
                strength=weakest,
            )
        if failed:
            # Amb una norma que falta, la resta ja no la salva.
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.CERTIFICATION_SET,
                explanation_code=ExplanationCode.CERTIFICATION_SOME_MISSING,
                params={
                    "all_required": True,
                    "missing": [n for n, _ in failed],
                    "norms": norms,
                    **_potential_only(failed, all_required=True),
                },
                mode=AssessmentMode.AUTOMATIC,
                evidence=evidence,
            )

    # Cap via clara: alguna norma demana revisio o no se'n sap res.
    pending = reviews or [(n, o) for n, o in outcomes if o.result != AssessmentResult.SATISFIES]
    first = pending[0][1] if pending else outcomes[0][1]
    return RuleOutcome(
        result=AssessmentResult.NEEDS_REVIEW if reviews else AssessmentResult.UNKNOWN,
        rule_code=RuleCode.CERTIFICATION_SET,
        explanation_code=first.explanation_code,
        params={"all_required": all_required, "norms": norms},
        mode=AssessmentMode.REVIEW_REQUIRED,
        evidence=evidence,
    )


def _potential_only(
    failed: list[tuple[str, RuleOutcome]], *, all_required: bool
) -> dict[str, Any]:
    """Si el «no compleix» d'un conjunt nomes pot ser potencial.

    Amb totes exigides (`all_of`), un sol «no compleix» definitiu ja ho es: potencial
    nomes si tots ho son. Amb «qualsevol de», el conjunt falla nomes si fallen totes:
    n'hi ha prou que UNA pugui acreditar-se per equivalencia perque sigui potencial.
    """
    marks = [bool((o.params or {}).get(POTENTIAL_ONLY_KEY)) for _, o in failed]
    if failed and (all(marks) if all_required else any(marks)):
        return {POTENTIAL_ONLY_KEY: True}
    return {}


# --------------------------------------------------------------------------- #
# ENS                                                                           #
# --------------------------------------------------------------------------- #


def is_ens_certification(cert: Any) -> bool:
    """Una certificacio de l'empresa que es l'ENS: pel codi del vocabulari o pel nom."""
    from app.compliance.normalize import names_ens

    code = (getattr(cert, "standard_code", None) or "").strip().upper()
    return code == "ENS" or names_ens(getattr(cert, "name", "") or "")


def company_ens_level(cert: Any) -> str | None:
    """La categoria d'una certificacio ENS de l'empresa: `level` primer, el nom despres."""
    from app.compliance.normalize import ens_level, fold_text

    raw = getattr(cert, "level", None)
    if raw:
        mapped = _ENS_STRUCTURED_LEVELS.get(fold_text(str(raw)).strip())
        if mapped:
            return mapped
    level, _ambiguous = ens_level(getattr(cert, "name", "") or "")
    return level


def assess_ens(
    reading: Any,
    snapshot: ProfileSnapshot,
    deadline: date | None = None,
) -> RuleOutcome:
    """L'ENS per categories (RD 311/2022): `basica < mitjana < alta`.

    * categoria del plec desconeguda -> revisio (`CERTIFICATION_LEVEL_NOT_STATED`),
      mai una suposicio;
    * nomes compten les certificacions vigents (`lifecycle`);
    * una certificacio de l'empresa sense categoria llegible (ni `level` ni nom) no
      casa amb cap altra;
    * mateixa categoria -> compleix; una de superior -> tambe compleix (v6,
      `CERTIFICATION_LEVEL_HIGHER_MET`): les categories son acumulatives;
    * nomes una d'inferior -> no compleix (amb el perfil incomplet, «sense dades»).
    """
    from app.compliance.normalize import ENS_ORDER

    certificates = [cert for cert in snapshot.certifications if is_ens_certification(cert)]
    current = [cert for cert in certificates if is_current(cert)]
    names = sorted(cert.name for cert in current)
    if reading.level is None:
        return needs_review(
            RuleCode.ENS_CATEGORY_GTE,
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
    if not current:
        not_current = (
            {"not_current": sorted({_lifecycle(cert) or "" for cert in certificates})}
            if certificates
            else {}
        )
        if certificates and not reading.equivalence_allowed:
            # Consta, pero en tramit o retirada: avui no acredita cap categoria.
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.ENS_CATEGORY_GTE,
                explanation_code=ExplanationCode.CERTIFICATION_NOT_CURRENT,
                params={
                    **params,
                    "found": certificates[0].name,
                    "lifecycle": not_current["not_current"],
                },
                mode=AssessmentMode.AUTOMATIC,
            )
        # Amb «o equivalent», una ENS en tramit es com si no hi fos (`_not_found`).
        if snapshot.is_section_complete("certifications"):
            if reading.equivalence_allowed:
                return RuleOutcome(
                    result=AssessmentResult.DOES_NOT_SATISFY,
                    rule_code=RuleCode.ENS_CATEGORY_GTE,
                    explanation_code=ExplanationCode.CERTIFICATION_EQUIVALENT_NOT_DECLARED,
                    params={
                        **params,
                        **not_current,
                        "equivalence_allowed": True,
                        POTENTIAL_ONLY_KEY: True,
                    },
                    mode=AssessmentMode.AUTOMATIC,
                )
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.ENS_CATEGORY_GTE,
                explanation_code=ExplanationCode.CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE,
                params=params,
                mode=AssessmentMode.AUTOMATIC,
            )
        return unknown(
            RuleCode.ENS_CATEGORY_GTE,
            ExplanationCode.CERTIFICATION_DATA_INCOMPLETE,
            **params,
            **not_current,
        )

    levels = [(cert, ENS_ORDER.get(company_ens_level(cert) or "")) for cert in current]
    known = [(cert, order) for cert, order in levels if order is not None]

    eligible = [(cert, order) for cert, order in known if order >= required]
    if eligible:
        # Totes les d'igual o superior categoria, i la que millor acredita: vigent abans
        # que sense data i que caducada; amb el mateix resultat, la mateixa categoria i,
        # si no, la mes baixa de les superiors (la que acredita amb menys marge).
        candidates = []
        for cert, order in eligible:
            outcome = _ens_found(
                cert,
                snapshot,
                deadline,
                reading,
                params,
                ExplanationCode.CERTIFICATION_FOUND_VALID
                if order == required
                else ExplanationCode.CERTIFICATION_LEVEL_HIGHER_MET,
            )
            expiry = cert.expires_at.toordinal() if cert.expires_at else 0
            candidates.append(
                ((_ENS_RESULT_RANK[outcome.result], order != required, order, -expiry), outcome)
            )
        return min(candidates, key=lambda item: item[0])[1]
    if any(order is None for _, order in levels):
        # Una certificacio sense categoria llegible pot ser la que acredita.
        return needs_review(
            RuleCode.ENS_CATEGORY_GTE,
            ExplanationCode.CERTIFICATION_LEVEL_NOT_STATED,
            side="company",
            **params,
        )
    if not snapshot.is_section_complete("certifications"):
        # Amb el perfil incomplet, l'empresa pot tenir una categoria superior que
        # no ha declarat: com a la resta de regles, «sense dades», no «no compleix».
        return unknown(
            RuleCode.ENS_CATEGORY_GTE,
            ExplanationCode.CERTIFICATION_LEVEL_BELOW,
            section_complete=False,
            **params,
        )
    # Una categoria inferior no es cap «equivalent»; si el plec n'admet, la persona
    # que revisi pot desfer el bloqueig, que per aixo nomes es potencial.
    extra = {POTENTIAL_ONLY_KEY: True} if reading.equivalence_allowed else {}
    return RuleOutcome(
        result=AssessmentResult.DOES_NOT_SATISFY,
        rule_code=RuleCode.ENS_CATEGORY_GTE,
        explanation_code=ExplanationCode.CERTIFICATION_LEVEL_BELOW,
        params={**params, **extra},
        mode=AssessmentMode.AUTOMATIC,
    )


def _ens_found(
    best: Any,
    snapshot: ProfileSnapshot,
    deadline: date | None,
    reading: Any,
    params: dict,
    explanation: str,
) -> RuleOutcome:
    evidence, strength = _evidence_of(best, snapshot)
    company_value = _company_value(best)
    params = {**params, "found": best.name, "found_level": company_ens_level(best)}
    result, mode = AssessmentResult.SATISFIES, AssessmentMode.AUTOMATIC
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
            if reading.equivalence_allowed:
                # Caducada, pero el plec admet equivalents: nomes potencial.
                params[POTENTIAL_ONLY_KEY] = True
    return RuleOutcome(
        result=result,
        rule_code=RuleCode.ENS_CATEGORY_GTE,
        explanation_code=explanation,
        params=params,
        mode=mode,
        company_value=company_value,
        evidence=evidence,
        strength=strength,
    )
