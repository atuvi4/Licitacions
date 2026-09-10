"""Quan una decisio humana exigeix un motiu escrit.

El sistema **no impedeix** tecnicament que una persona aprovi un GO amb avisos
oberts: pot tenir-hi una resposta que el motor no coneix (una polissa que es
signa dema, una UTE, una interpretacio del plec). Prohibir-ho empenyeria la
decisio fora de l'eina, que es on deixa de quedar registrada.

El que si que fa es exigir que ho justifiqui per escrit i deixar-ho a
l'historial al costat dels avisos que hi havia a la pantalla. La friccio va al
lloc correcte: no a bloquejar la persona, sino a que quedi constancia.

Quin estat deixa cada decisio i quines transicions son possibles NO es decideix
aqui: viu a `app.workspace.transitions`, que es l'unica porta cap a
`BidWorkspace.status`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import WorkspaceDecisionType


@dataclass(frozen=True, slots=True)
class DecisionWarnings:
    """El que hi havia obert en el moment de decidir."""

    confirmed_blockers: int = 0
    potential_blockers: int = 0
    mandatory_unknown: int = 0
    mandatory_needs_review: int = 0
    deadline_passed: bool = False
    #: Cap calcul de compliment vigent: s'estaria decidint a cegues.
    missing_compliance: bool = False
    #: No s'ha pogut avaluar cap requisit. "Zero bloquejadors" no vol dir res
    #: aqui: no s'ha mirat res. Es l'avis mes important de tots perque es el
    #: unic que no es veu: la pantalla ensenya zeros, i els zeros semblen bones
    #: noticies.
    undetermined_eligibility: bool = False
    #: L'analisi de requisits no surt de llegir el plec —simulacio o cap motor
    #: configurat—. El que hi hagi a sota pot tenir la forma correcta i no ser
    #: cert.
    unreliable_analysis: bool = False
    #: Hi ha canvis posteriors a l'analisi: el que es mostra descriu un estat
    #: anterior de la licitacio.
    outdated_analysis: bool = False

    @property
    def any_open(self) -> bool:
        return bool(
            self.confirmed_blockers
            or self.potential_blockers
            or self.mandatory_unknown
            or self.mandatory_needs_review
            or self.deadline_passed
            or self.missing_compliance
            or self.undetermined_eligibility
            or self.unreliable_analysis
            or self.outdated_analysis
        )

    def codes(self) -> list[str]:
        """Codis dels avisos oberts. La interficie en deriva el text."""
        codes: list[str] = []
        if self.confirmed_blockers:
            codes.append("confirmed_blockers")
        if self.potential_blockers:
            codes.append("potential_blockers")
        if self.mandatory_unknown:
            codes.append("mandatory_unknown")
        if self.mandatory_needs_review:
            codes.append("mandatory_needs_review")
        if self.deadline_passed:
            codes.append("deadline_passed")
        if self.missing_compliance:
            codes.append("missing_compliance")
        if self.undetermined_eligibility:
            codes.append("undetermined_eligibility")
        if self.unreliable_analysis:
            codes.append("unreliable_analysis")
        if self.outdated_analysis:
            codes.append("outdated_analysis")
        return codes


def requires_reason(decision: WorkspaceDecisionType, warnings: DecisionWarnings) -> bool:
    """Nomes el GO amb avisos oberts obliga a escriure el perque.

    Un NO-GO no necessita justificar-se davant de cap avis: descartar-se davant
    d'un problema es precisament el que els avisos suggereixen. Un `defer`
    tampoc: no compromet res.
    """
    return decision == WorkspaceDecisionType.GO and warnings.any_open


#: Per que una reconciliacio no ha pogut fer res. Son codis: el text el posa la
#: interficie, com amb la resta d'avisos.
NOTHING_TO_DO_NO_REQUIREMENTS = "no_requirements_analysed"
NOTHING_TO_DO_ALREADY_IN_SYNC = "already_in_sync"


def nothing_to_do_reason(*, created: int, requirements_available: int) -> str | None:
    """Explica un 200 que no ha canviat res.

    Sense aixo, "reconcilia" responia correctament i no passava res: ni un
    element nou, ni un missatge, ni una pista. L'usuari havia premut el boto que
    l'avis li demanava premer i es quedava igual, sense saber si el problema era
    el sistema o ell.
    """
    if created:
        return None
    if requirements_available <= 0:
        return NOTHING_TO_DO_NO_REQUIREMENTS
    return NOTHING_TO_DO_ALREADY_IN_SYNC
