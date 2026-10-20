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

Per que l'aprovacio final te un actor propi i no simplement "huma": perque el
cami natural, quan s'implementi la presentacio, sera afegir codi a prop de la
decisio de GO. Si `HUMAN_DECISION` pogues escriure `approved_for_submission` o
`submitted`, aquell codi compilaria i passaria els tests. Amb un actor separat,
no.

`submitted` segueix sent inassolible: cap funcio del sistema l'escriu, i el nom
nomes s'anomena en aquest modul i a `enums.py`.
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
    #: L'aprovacio d'un PAQUET concret per presentar. Es mes estricta que una
    #: decisio de Go/No-Go: alla s'aprova anar-hi, aqui s'aprova exactament que
    #: s'envia. L'unica funcio que el fa servir es
    #: `submission_package.approve_package`.
    HUMAN_FINAL_APPROVAL = "human_final_approval"


#: Estats que NOMES pot escriure una persona. Cap actor `SYSTEM` no hi arriba.
HUMAN_ONLY_STATUSES = frozenset(
    {S.GO_APPROVED, S.NO_GO_APPROVED, S.APPROVED_FOR_SUBMISSION, S.SUBMITTED}
)

#: Estats que exigeixen l'aprovacio final humana, mes estricta que una decisio de
#: Go/No-Go. Aprovar un paquet concret per presentar i presentar-lo son dos actes
#: diferents, i tots dos demanen aquesta signatura.
FINAL_APPROVAL_STATUSES = frozenset({S.APPROVED_FOR_SUBMISSION, S.SUBMITTED})

#: Candidatura que ja no es oberta: presentada, retirada o amb un NO-GO aprovat.
#: Nomes es llegeix (Avui, el Radar); no es cap transicio. El mateix conjunt que
#: `isOpenWorkspace` del web (`components/shell/counts.ts`): si un canvia, l'altre tambe.
CLOSED_STATUSES = frozenset({S.SUBMITTED, S.WITHDRAWN, S.NO_GO_APPROVED})

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
        {
            S.READY_FOR_FINAL_REVIEW,
            # Una decisio humana nova torna el workspace a l'estat que implica,
            # encara que sigui enrere: el que val es l'ultima cosa que ha dit una
            # persona, no el punt mes avancat on hagi arribat la feina.
            S.GO_APPROVED,
            S.NO_GO_APPROVED,
            S.AWAITING_DECISION,
            S.WITHDRAWN,
        }
    ),
    # D'aqui se surt cap amunt aprovant un paquet, o cap enrere si el dossier
    # deixa d'estar complet.
    S.READY_FOR_FINAL_REVIEW: frozenset(
        {
            S.APPROVED_FOR_SUBMISSION,
            S.PREPARING_BID,
            S.GO_APPROVED,
            S.NO_GO_APPROVED,
            S.AWAITING_DECISION,
            S.WITHDRAWN,
        }
    ),
    # L'unic estat des del qual `submitted` es assolible. Tambe pot retrocedir:
    # si el paquet aprovat queda obsolet, la candidatura torna a preparar-se i
    # cal aprovar una revisio nova. L'aprovacio antiga no es mou d'on es.
    S.APPROVED_FOR_SUBMISSION: frozenset(
        {
            S.SUBMITTED,
            S.READY_FOR_FINAL_REVIEW,
            S.PREPARING_BID,
            S.NO_GO_APPROVED,
            S.AWAITING_DECISION,
            S.WITHDRAWN,
        }
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


def is_submitted(status: WorkspaceStatus) -> bool:
    """Aquesta candidatura ja s'ha presentat?

    NOMES `submitted`. `approved_for_submission` vol dir que una persona ha
    aprovat un paquet concret, que es una cosa diferent i anterior; confondre-les
    seria dir a algu que ja ha presentat una oferta que no ha sortit d'aqui.

    Existeix perque la resta del codi no hagi d'anomenar `SUBMITTED`: qui vol
    saber-ho pregunta aqui. Aixi el nom queda confinat en aquest modul i el test
    estructural que ho vigila segueix sent estricte.
    """
    return status is S.SUBMITTED


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
