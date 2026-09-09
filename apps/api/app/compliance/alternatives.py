"""Vies alternatives: "A o B" es una exigencia, no dues.

Un plec que diu "acredita la solvencia amb la documentacio de l'apartat 7 o,
alternativament, amb un certificat de classificacio" no exigeix les dues coses.
Avaluar cada branca pel seu compte i deixar que qualsevol d'elles bloquegi es
l'error mes car que aquest sistema pot cometre: diria a una empresa que no es pot
presentar a una licitacio a la qual si que pot.

Les regles del grup:

* **una branca compleix** -> la disjuncio esta acreditada. Aixo val encara que el
  grup sigui incomplet: si una via ja acredita el requisit, que n'hi hagi d'altres
  sense trobar no canvia res.
* **totes les branques conegudes incompleixen I consta que el grup es complet**
  -> la disjuncio no s'acredita.
* **qualsevol altra cosa** -> revisio.

La peça delicada es que vol dir "complet", i **no es dedueix del recompte de
branques**. Que l'extractor n'hagi trobat dues no demostra que el plec no en
tingui una tercera; deduir-ho seria convertir una observacio parcial en una
afirmacio tancada, i el preu de l'error es un blocador fals.

Per aixo la completitud es una dada explicita del requisit
(`alternative_group_complete`) i el seu valor per defecte es `None`: no se sap.
Nomes pot ser certa quan l'estructura o l'evidencia demostren que totes les vies
hi son. Mentre no ho sigui, cap branca del grup pot bloquejar res.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.models.enums import AssessmentResult


class GroupOutcome(Enum):
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class Branch:
    """Una via de la disjuncio, amb el que se'n sap."""

    key: Any
    result: AssessmentResult
    #: Que diu aquesta branca sobre si la disjuncio esta sencera. `None` = res.
    group_complete: bool | None = None


@dataclass(frozen=True, slots=True)
class GroupResolution:
    outcome: GroupOutcome
    branches: int
    #: Si consta que totes les vies estan representades.
    complete: bool = False
    satisfied_by: tuple[Any, ...] = ()
    #: Branques amb un veredicte que no decideix res per si sol.
    unresolved: tuple[Any, ...] = ()

    @property
    def blocks(self) -> bool:
        """Si aquesta disjuncio pot arribar a generar un blocador."""
        return self.outcome == GroupOutcome.NOT_SATISFIED


def _is_complete(branches: list[Branch]) -> bool:
    """Nomes es complet si alguna branca ho AFIRMA i cap ho nega.

    No hi ha cap heuristica aqui a proposit. El recompte de branques no hi entra:
    es informacio sobre el que hem vist, no sobre el que hi ha.
    """
    flags = [branch.group_complete for branch in branches]
    return any(flag is True for flag in flags) and not any(flag is False for flag in flags)


def resolve_group(branches: list[Branch] | list[tuple[Any, AssessmentResult]]) -> GroupResolution:
    """El veredicte d'una disjuncio a partir del de cada branca."""
    items = [
        branch if isinstance(branch, Branch) else Branch(key=branch[0], result=branch[1])
        for branch in branches
    ]

    satisfied = tuple(branch.key for branch in items if branch.result == AssessmentResult.SATISFIES)
    complete = _is_complete(items)
    if satisfied:
        # Una via ja acredita el requisit. Que en falti alguna per trobar es
        # irrellevant: el plec demana una, no totes.
        return GroupResolution(
            outcome=GroupOutcome.SATISFIED,
            branches=len(items),
            complete=complete,
            satisfied_by=satisfied,
        )

    unresolved = tuple(
        branch.key
        for branch in items
        if branch.result
        not in (AssessmentResult.DOES_NOT_SATISFY, AssessmentResult.NOT_APPLICABLE)
    )
    if complete and not unresolved:
        return GroupResolution(
            outcome=GroupOutcome.NOT_SATISFIED, branches=len(items), complete=True
        )
    return GroupResolution(
        outcome=GroupOutcome.INCOMPLETE,
        branches=len(items),
        complete=complete,
        unresolved=unresolved,
    )


def resolve_groups(
    items: list[tuple[str, Any, AssessmentResult]]
    | list[tuple[str, Any, AssessmentResult, bool | None]],
) -> dict[str, GroupResolution]:
    """Resol totes les disjuncions d'una avaluacio, indexades per grup."""
    grouped: dict[str, list[Branch]] = {}
    for item in items:
        group, key, result = item[0], item[1], item[2]
        complete = item[3] if len(item) > 3 else None
        grouped.setdefault(group, []).append(
            Branch(key=key, result=result, group_complete=complete)
        )
    return {group: resolve_group(branches) for group, branches in grouped.items()}


def effective_result(
    own_result: AssessmentResult, resolution: GroupResolution | None
) -> AssessmentResult:
    """El resultat que compta per a blocadors i recomptes.

    Una branca no es jutja sola. Si la disjuncio esta acreditada, aquesta branca
    no obstaculitza res encara que ella mateixa no la compleixi; si la disjuncio
    encara pot resoldre's per una altra via —o simplement no sabem si n'hi ha
    d'altres— el que sabem es que cal mirar-ho.
    """
    if resolution is None:
        return own_result
    if resolution.outcome == GroupOutcome.SATISFIED:
        return AssessmentResult.SATISFIES
    if resolution.outcome == GroupOutcome.INCOMPLETE:
        return AssessmentResult.NEEDS_REVIEW
    return own_result


#: Codi d'explicacio de cada desenllaç, per no repartir-lo pel motor.
EXPLANATION_BY_OUTCOME = {
    GroupOutcome.SATISFIED: "ALTERNATIVE_GROUP_SATISFIED",
    GroupOutcome.NOT_SATISFIED: "ALTERNATIVE_GROUP_NOT_SATISFIED",
    GroupOutcome.INCOMPLETE: "ALTERNATIVE_GROUP_INCOMPLETE",
}
