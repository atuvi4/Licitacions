"""Les transicions d'estat d'un workspace, en un sol lloc.

Abans d'aquest modul l'estat s'escrivia amb una assignacio (`workspace.status =
...`) i l'unica cosa que impedia una transicio impossible era que ningu no
n'hagues escrit el codi. Aixo no es una garantia: es una absencia.

Aqui hi ha dues regles, i totes dues es comproven **a cada canvi d'estat**:

* **Reachability.** `ALLOWED_TRANSITIONS` diu quins estats es poden assolir des
  de quin. El que no hi es, no passa.
* **Autoritat.** Alguns estats no els pot escriure un proces automatic per molt
  que la transicio sigui valida al graf. `submitted` va mes enlla: no el pot
  escriure ni tan sols una decisio GO. Nomes una aprovacio final humana, que
  encara no existeix.

El modul es pur: no toca base de dades ni HTTP, i les seves excepcions no
depenen de FastAPI. Qui les tradueix a codis d'error es la capa de servei.

Per que `submitted` te un actor propi i no simplement "huma": perque el dia que
s'implementi la presentacio, el cami natural sera afegir-hi codi a prop de la
decisio de GO. Si `HUMAN_DECISION` ja pogues escriure `submitted`, aquell codi
compilaria i passaria els tests. Amb un actor separat, no.
"""

from __future__ import annotations

from enum import Enum

from app.models.enums import WorkspaceDecisionType, WorkspaceStatus

S = WorkspaceStatus


class TransitionActor(Enum):
    """Qui intenta canviar l'estat.

    No es un rol d'usuari: es la naturalesa del cami de codi. `SYSTEM` cobreix
    tot el que passa sense que ningu ho hagi premut —recalcular el compliment,
    regenerar la checklist, una recomanacio nova— i per definicio no pot
    aprovar res.
    """

    SYSTEM = "system"
    HUMAN_DECISION = "human_decision"
    #: Reservat. Cap cami del codi el fa servir encara: l'aprovacio final humana
    #: es una fase futura i `submitted` queda inaccessible fins llavors.
    HUMAN_FINAL_APPROVAL = "human_final_approval"


#: Estats que NOMES pot escriure una persona. Cap actor `SYSTEM` no hi arriba.
HUMAN_ONLY_STATUSES = frozenset({S.GO_APPROVED, S.NO_GO_APPROVED, S.SUBMITTED})

#: Estats que exigeixen l'aprovacio final humana, mes estricta que una decisio.
FINAL_APPROVAL_STATUSES = frozenset({S.SUBMITTED})

#: Graf de transicions. Les autotransicions que hi son son deliberades: tornar a
#: aprovar un GO (amb un motiu nou) o ajornar dues vegades son accions reals que
#: han de deixar una entrada mes a l'historial, no un error.
ALLOWED_TRANSITIONS: dict[WorkspaceStatus, frozenset[WorkspaceStatus]] = {
    S.ANALYSIS: frozenset(
        {S.NEEDS_REVIEW, S.AWAITING_DECISION, S.GO_APPROVED, S.NO_GO_APPROVED, S.WITHDRAWN}
    ),
    S.NEEDS_REVIEW: frozenset(
        {S.ANALYSIS, S.AWAITING_DECISION, S.GO_APPROVED, S.NO_GO_APPROVED, S.WITHDRAWN}
    ),
    S.AWAITING_DECISION: frozenset(
        {
            S.ANALYSIS,
            S.NEEDS_REVIEW,
            S.AWAITING_DECISION,
            S.GO_APPROVED,
            S.NO_GO_APPROVED,
            S.WITHDRAWN,
        }
    ),
    # Un GO aprovat es pot revertir a NO-GO o ajornar: canviar d'opinio es
    # legitim i queda a l'historial. El que no pot fer es saltar a `submitted`.
    S.GO_APPROVED: frozenset(
        {
            S.GO_APPROVED,
            S.NO_GO_APPROVED,
            S.AWAITING_DECISION,
            S.PREPARING_BID,
            S.WITHDRAWN,
        }
    ),
    S.NO_GO_APPROVED: frozenset(
        {S.NO_GO_APPROVED, S.GO_APPROVED, S.AWAITING_DECISION, S.WITHDRAWN}
    ),
    S.PREPARING_BID: frozenset(
        {S.READY_FOR_FINAL_REVIEW, S.NO_GO_APPROVED, S.AWAITING_DECISION, S.WITHDRAWN}
    ),
    # L'unic estat des del qual `submitted` es assolible.
    S.READY_FOR_FINAL_REVIEW: frozenset(
        {S.SUBMITTED, S.PREPARING_BID, S.NO_GO_APPROVED, S.WITHDRAWN}
    ),
    # Presentada: nomes es pot retirar. No es torna a "en analisi".
    S.SUBMITTED: frozenset({S.WITHDRAWN}),
    S.WITHDRAWN: frozenset({S.ANALYSIS, S.AWAITING_DECISION}),
}


class WorkspaceTransitionError(Exception):
    """Base de les transicions rebutjades. Sense dependencies d'HTTP."""

    def __init__(self, message: str, *, current: WorkspaceStatus, target: WorkspaceStatus):
        super().__init__(message)
        self.message = message
        self.current = current
        self.target = target


class InvalidTransitionError(WorkspaceTransitionError):
    """La transicio no existeix al graf."""


class AutomaticTransitionForbiddenError(WorkspaceTransitionError):
    """Un cami automatic ha intentat escriure un estat reservat a una persona."""

    def __init__(
        self,
        message: str,
        *,
        current: WorkspaceStatus,
        target: WorkspaceStatus,
        actor: TransitionActor,
    ):
        super().__init__(message, current=current, target=target)
        self.actor = actor


def status_for(decision: WorkspaceDecisionType) -> WorkspaceStatus:
    """L'estat que deixa una decisio humana.

    `defer` no aprova res: deixa el workspace a l'espera de decisio. I cap
    decisio, ni tan sols un GO, no arriba a `submitted`: presentar una oferta es
    un acte posterior i separat.
    """
    return {
        WorkspaceDecisionType.GO: WorkspaceStatus.GO_APPROVED,
        WorkspaceDecisionType.NO_GO: WorkspaceStatus.NO_GO_APPROVED,
        WorkspaceDecisionType.DEFER: WorkspaceStatus.AWAITING_DECISION,
    }[decision]


def is_human_only(status: WorkspaceStatus) -> bool:
    return status in HUMAN_ONLY_STATUSES


def can_transition(
    current: WorkspaceStatus, target: WorkspaceStatus, *, actor: TransitionActor
) -> bool:
    try:
        assert_transition(current, target, actor=actor)
    except WorkspaceTransitionError:
        return False
    return True


def assert_transition(
    current: WorkspaceStatus, target: WorkspaceStatus, *, actor: TransitionActor
) -> None:
    """Rebutja la transicio si no es possible o si l'actor no hi te potestat.

    L'autoritat es comprova ABANS que el graf: quan un proces automatic intenta
    aprovar alguna cosa, el problema no es per on hi arriba, es que ho intenti.
    """
    if target in FINAL_APPROVAL_STATUSES and actor is not TransitionActor.HUMAN_FINAL_APPROVAL:
        raise AutomaticTransitionForbiddenError(
            f"`{target.value}` exigeix l'aprovacio final d'una persona; "
            f"`{actor.value}` no la pot donar.",
            current=current,
            target=target,
            actor=actor,
        )
    if target in HUMAN_ONLY_STATUSES and actor is TransitionActor.SYSTEM:
        raise AutomaticTransitionForbiddenError(
            f"`{target.value}` nomes el pot escriure una persona; cap proces "
            "automatic no pot aprovar ni descartar una candidatura.",
            current=current,
            target=target,
            actor=actor,
        )
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"No es pot passar de `{current.value}` a `{target.value}`.",
            current=current,
            target=target,
        )


def apply_transition(
    current: WorkspaceStatus, target: WorkspaceStatus, *, actor: TransitionActor
) -> WorkspaceStatus:
    """Comprova i retorna l'estat nou. Es l'unica manera d'obtenir-ne un."""
    assert_transition(current, target, actor=actor)
    return target
