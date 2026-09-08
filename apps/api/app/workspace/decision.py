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

    @property
    def any_open(self) -> bool:
        return bool(
            self.confirmed_blockers
            or self.potential_blockers
            or self.mandatory_unknown
            or self.mandatory_needs_review
            or self.deadline_passed
            or self.missing_compliance
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
        return codes


def requires_reason(decision: WorkspaceDecisionType, warnings: DecisionWarnings) -> bool:
    """Nomes el GO amb avisos oberts obliga a escriure el perque.

    Un NO-GO no necessita justificar-se davant de cap avis: descartar-se davant
    d'un problema es precisament el que els avisos suggereixen. Un `defer`
    tampoc: no compromet res.
    """
    return decision == WorkspaceDecisionType.GO and warnings.any_open
