"""Guard del pas Preparar: cap mutacio ni generacio sense un GO huma vigent.

3B-2 §7.1.6 (decisio 4 del propietari, addendum A18): el pas Preparar es pot
obrir i llegir sense GO, pero tota mutacio o generacio del pas (dossier,
solucio tecnica, oferta economica, paquet de presentacio, tasques i notes de
preparacio, i la resta d'escriptures que pengen del workspace) exigeix una
decisio humana GO vigent.

La decisio vigent es l'ULTIMA registrada (`bid_workspace.latest_decision`):
un NO-GO o un ajornament posteriors a un GO tornen a tancar el pas, i un GO
nou el torna a obrir. L'estat del workspace no hi compta: `go_approved` o
`preparing_bid` nomes son consequencia d'aquesta mateixa decisio.

`assert_workspace_go` es la primera linia de cada endpoint mutador del pas,
just despres de carregar el workspace (que ja comprova la pertinenca a
l'empresa). Els GET no la criden: llegir no demana cap decisio. La llista
explicita dels endpoints guardats viu a `tests/test_workspace_go_gate.py`, que
la compara amb les rutes reals: un mutador nou sense guard fa fallar el test.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.models.enums import WorkspaceDecisionType
from app.models.workspace import BidWorkspace, WorkspaceDecision
from app.services.bid_workspace import latest_decision


class DecisionRequiredError(PermissionDeniedError):
    """403: el pas Preparar nomes s'obre a edicio amb una decisio GO vigent.

    Es un 403 i no un 409: qui ho demana te acces al workspace; el que no te
    es una decisio humana que l'autoritzi a preparar-hi res. Amagar-ho amb un
    404 seria confus, i un 409 diria que l'estat de la feina ho impedeix, quan
    el que ho impedeix es que ningu no ha dit GO.
    """

    code = "decision_required"


async def assert_workspace_go(session: AsyncSession, workspace: BidWorkspace) -> WorkspaceDecision:
    """Retorna la decisio GO vigent, o llanca 403 `decision_required`."""
    decision = await latest_decision(session, workspace.id)
    if decision is None or decision.decision != WorkspaceDecisionType.GO:
        raise DecisionRequiredError(
            "Cal una decisio GO vigent per preparar la candidatura: "
            "el pas Preparar es pot consultar, pero no editar.",
            details={
                "workspace_id": str(workspace.id),
                "current_decision": decision.decision.value if decision else None,
            },
        )
    return decision
