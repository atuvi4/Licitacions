"""Que un document estigui triat no vol dir que acrediti res.

L'auditoria va assignar un titol universitari per acreditar una xifra anual de
negoci i un certificat de bona execucio per acreditar una polissa de
responsabilitat civil. El dossier va dir **«Cobert»** a totes dues coses, la
readiness va passar a «a punt» i el paquet es va congelar sense una sola
friccio. Seleccionar un document i confirmar que es el document adequat son
dues afirmacions diferents, i el sistema en feia una sola.

Aquest modul decideix nomes una cosa: **si la pertinença esta acreditada**. No
torna a comparar imports, ni dates, ni llindars —d'aixo ja se n'ocupa 4C i
duplicar-ho aqui seria tenir dos motors de compliment que un dia diran coses
diferents—. Aqui la pregunta es una altra: *aquest paper te alguna cosa a veure
amb aquesta exigencia?*

Tres respostes, i l'ordre importa:

* **Categoria incompatible** -> revisio. Un CV no acredita una polissa, i dir-ho
  no requereix mirar res mes.
* **Vinculacio amb l'evidencia del compliment** -> acreditada. Si el document
  esta enllaçat a la MATEIXA entitat corporativa que el compliment va citar —la
  polissa concreta, el projecte concret— llavors parlen del mateix i no cal que
  ho confirmi ningu.
* **Confirmacio humana explicita** -> acreditada, amb qui, quan i per que. La
  confirmacio va lligada al requisit i al document: substituir el document o
  canviar materialment el requisit no l'hereta.

La categoria es un **filtre, no una prova**: que un certificat sigui de la
categoria "certification" no vol dir que sigui EL certificat que el plec demana.
Per aixo una categoria compatible tota sola no dona cobertura.

Cap heuristica sobre el nom del fitxer, i cap model. Si el sistema no ho pot
establir, ho diu i ho passa a una persona.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.models.enums import (
    CompanyEvidenceKind,
    DocumentCategory,
    LinkedEntityType,
    RequirementCategory,
)


class RelevanceState:
    """Com de sostinguda esta la pertinença d'un document per a un requisit."""

    #: El document esta enllaçat a l'entitat que el compliment va citar.
    LINKED = "linked_evidence"
    #: Una persona ho ha confirmat, amb motiu.
    CONFIRMED = "human_confirmed"
    #: Categoria compatible, pero res no lliga aquest document amb el requisit.
    UNPROVEN = "relevance_unproven"
    #: La categoria del document no te res a veure amb el que demana el plec.
    INCOMPATIBLE = "category_incompatible"
    #: No sabem que hauria d'acreditar aquesta familia de requisit.
    UNKNOWN_CATEGORY = "category_unknown"
    #: El requisit demana mes documents dels que hi ha triats.
    INSUFFICIENT_SET = "insufficient_set"


#: Quines categories de document poden acreditar cada familia de requisit.
#:
#: Es un filtre gruixut i deliberadament conservador: el que no hi es no queda
#: "permes per defecte", queda en revisio. Afegir una familia nova sense decidir
#: que l'acredita la deixaria passar en silenci, que es exactament el que
#: aquest modul existeix per evitar.
COMPATIBLE_CATEGORIES: dict[RequirementCategory, frozenset[DocumentCategory]] = {
    RequirementCategory.ECONOMIC_SOLVENCY: frozenset({DocumentCategory.FINANCIAL}),
    RequirementCategory.INSURANCE: frozenset({DocumentCategory.INSURANCE}),
    RequirementCategory.CERTIFICATION: frozenset({DocumentCategory.CERTIFICATION}),
    RequirementCategory.REGISTRATION: frozenset({DocumentCategory.REGISTRATION}),
    RequirementCategory.BUSINESS_CLASSIFICATION: frozenset({DocumentCategory.REGISTRATION}),
    RequirementCategory.EXPERIENCE: frozenset({DocumentCategory.PROJECT_EVIDENCE}),
    RequirementCategory.TECHNICAL_SOLVENCY: frozenset(
        {DocumentCategory.PROJECT_EVIDENCE, DocumentCategory.TECHNICAL}
    ),
    RequirementCategory.PERSONNEL: frozenset({DocumentCategory.CV}),
    RequirementCategory.PROFESSIONAL_QUALIFICATION: frozenset({DocumentCategory.CV}),
    RequirementCategory.ADMINISTRATIVE_DOCUMENTATION: frozenset({DocumentCategory.LEGAL}),
}

#: El compliment cita entitats amb `CompanyEvidenceKind`; els documents s'hi
#: enllacen amb `LinkedEntityType`. Son dos vocabularis per a les mateixes
#: coses, i sense aquesta taula la via forta de pertinença no lligava mai.
EVIDENCE_TO_LINK: dict[CompanyEvidenceKind, LinkedEntityType] = {
    CompanyEvidenceKind.FINANCIAL_YEAR: LinkedEntityType.COMPANY_FINANCIAL_YEAR,
    CompanyEvidenceKind.CERTIFICATION: LinkedEntityType.COMPANY_CERTIFICATION,
    CompanyEvidenceKind.REGISTRATION: LinkedEntityType.COMPANY_REGISTRATION,
    CompanyEvidenceKind.INSURANCE: LinkedEntityType.COMPANY_INSURANCE,
    CompanyEvidenceKind.PROJECT: LinkedEntityType.COMPANY_PROJECT,
    CompanyEvidenceKind.PERSON: LinkedEntityType.COMPANY_PERSON,
    CompanyEvidenceKind.QUALIFICATION: LinkedEntityType.PERSON_QUALIFICATION,
}


def link_type_for(evidence_kind: Any) -> LinkedEntityType | None:
    """Amb quin tipus d'enllaç documental es correspon una evidencia corporativa.

    `CompanyEvidenceKind.DOCUMENT` no hi es a proposit: quan el compliment cita
    un document directament, l'entitat JA es el document i la comprovacio es
    d'identitat, no d'enllaç.
    """
    if evidence_kind is None:
        return None
    try:
        kind = CompanyEvidenceKind(str(evidence_kind))
    except ValueError:
        return None
    return EVIDENCE_TO_LINK.get(kind)


#: Els estats que donen cobertura documental. La resta demana una persona.
ESTABLISHED = frozenset({RelevanceState.LINKED, RelevanceState.CONFIRMED})

#: Camps de `value_json` que diuen quants documents fan falta. Si el plec en
#: demana tres i n'hi ha un de triat, tenir la categoria correcta no basta.
_COUNT_FIELDS = ("minimum_projects", "count", "minimum_count")


@dataclass(frozen=True, slots=True)
class RelevanceInput:
    """Tot el que cal per decidir, ja resolt contra la base de dades."""

    requirement_category: RequirementCategory | None
    document_category: DocumentCategory | None
    #: Cert quan el document esta enllaçat a l'entitat corporativa que el
    #: compliment va citar per a aquest requisit.
    linked_to_compliance_evidence: bool = False
    #: Confirmacio humana vigent: ja comprovada contra el document i el requisit
    #: actuals per qui construeix aquesta entrada.
    human_confirmed: bool = False


def relevance_of(data: RelevanceInput) -> str:
    """L'estat de pertinença d'UNA peça. Determinista i sense efectes."""
    if data.human_confirmed:
        # Una persona ho ha mirat i ho ha signat. Mana sobre qualsevol filtre:
        # el sistema no en sap mes que ella.
        return RelevanceState.CONFIRMED

    if data.requirement_category is None:
        return RelevanceState.UNKNOWN_CATEGORY
    allowed = COMPATIBLE_CATEGORIES.get(data.requirement_category)
    if allowed is None:
        return RelevanceState.UNKNOWN_CATEGORY
    if data.document_category is None or data.document_category not in allowed:
        return RelevanceState.INCOMPATIBLE

    if data.linked_to_compliance_evidence:
        return RelevanceState.LINKED
    # Categoria correcta i res mes. No es prou: hi ha cinc certificats a la
    # biblioteca i el plec en demana un de concret.
    return RelevanceState.UNPROVEN


def required_document_count(value: dict[str, Any] | None) -> int:
    """Quants documents demana el requisit, segons el que en sabem.

    Es llegeix del mateix `value_json` que interpreta el motor. Sense una xifra
    explicita s'assumeix un: inventar-ne mes seria bloquejar per prudencia mal
    entesa.
    """
    for field in _COUNT_FIELDS:
        raw = (value or {}).get(field)
        if isinstance(raw, int) and raw > 1:
            return raw
    return 1


def requirement_fingerprint(
    category: Any, title: str | None, value: dict[str, Any] | None
) -> str:
    """Identitat material d'un requisit, per lligar-hi una confirmacio humana.

    Si el plec canvia i el requisit passa a demanar una altra cosa, la
    confirmacio d'abans deixa de valer: es va signar sobre una exigencia que ja
    no hi es. Comparar el text sencer seria massa fragil —un accent el
    canviaria— i comparar nomes l'identificador seria massa laxa; el que
    importa es **que demana**.
    """
    payload = json.dumps(
        {"category": str(category), "title": (title or "").strip(), "value": value or {}},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
