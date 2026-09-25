"""Quins requisits te davant aquesta candidatura, i qui ho ha revisat (C-14, C-16).

Aquest modul es **l'unic** que respon "aquest requisit aplica a aquesta
candidatura?". S3 el fa servir per decidir si es pot definir una solucio tecnica
per a un requisit, i S6 per saber de que parla la revisio d'abast. Si la regla
visques en dos llocs, un dia dirien coses diferents i ningu no sabria quina mana.

**Els lots (C-16).** Amb candidatura lligada a un lot, l'abast son els requisits
generals i els d'aquell lot. Sense lots al plec, els de la licitacio. I amb plec
amb lots i candidatura global, els lots als quals ens presentem els diuen els
**blocs de preus actius de l'oferta economica**: es l'unica afirmacio explicita
que existeix sobre a quins lots anem. Assumir-los tots seria inventar-ho.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, PermissionDeniedError
from app.models.enums import MembershipRole, TechnicalSolutionSource
from app.models.identity import User
from app.models.offer import EconomicOffer, EconomicOfferLot
from app.models.requirements import Requirement
from app.models.tenders import TenderLot
from app.models.workspace import BidWorkspace, WorkspaceTechnicalSolution
from app.services import manual_requirement
from app.workspace.checklist import applies_to_workspace_lot
from app.workspace.relevance import requirement_fingerprint
from app.workspace.requirement_routing import TECHNICAL_SOLUTION, route_requirement
from app.workspace.technical_scope import (
    LOT_BOUND,
    NO_LOTS,
    SELECTED_LOTS,
    technical_scope_fingerprint,
)

#: Qui pot afirmar "he revisat l'abast tecnic".
#:
#: Els membres coincideixen avui amb els que editen solucions tecniques, pero es
#: una politica PROPIA i no la mateixa constant: una cosa es poder escriure una
#: solucio i una altra, forca mes gran, es signar que s'ha mirat tot el que el
#: plec demana i que no en falta cap. Si compartissin constant, el dia que una
#: de les dues canvies s'enduria l'altra sense que ningu ho hagues decidit.
TECHNICAL_SCOPE_REVIEW_ROLES = frozenset(
    {
        MembershipRole.OWNER,
        MembershipRole.ADMIN,
        MembershipRole.BID_MANAGER,
        MembershipRole.TECHNICAL,
    }
)


def assert_can_review(role: MembershipRole) -> None:
    """403 i no 404: hi te acces, el que no te es potestat per afirmar-ho."""
    if role not in TECHNICAL_SCOPE_REVIEW_ROLES:
        raise PermissionDeniedError("El teu rol no pot revisar l'abast tecnic.")


class ScopeNotConfirmableError(ConflictError):
    """Encara no se sap a quins lots ens presentem: no hi ha abast que revisar."""

    code = "technical_scope_not_confirmable"


@dataclass(frozen=True, slots=True)
class TechnicalScope:
    """L'abast tecnic d'ara: de quins lots parlem i quins requisits hi entren."""

    lot_mode: str
    #: Els lots del plec que formen la candidatura. Buit si el plec no en te.
    tender_lot_ids: tuple[uuid.UUID, ...]
    #: Els que demanen solucio tecnica (`route_requirement`) i apliquen als lots.
    requirements: tuple[Requirement, ...]
    #: TOTS els requisits vigents que apliquen als lots, demanin solucio o no.
    #: Es la base de l'empremta: el que es va revisar i aprovar es el plec de
    #: la candidatura, i un canvi al DEUC o a la solvencia l'ha de moure igual.
    #: Tambe fa que l'empremta sigui la mateixa que abans del routing: cap
    #: revisio confirmada ni cap paquet congelat no caduca pel sol fet d'haver
    #: afegit la regla.
    lot_requirements: tuple[Requirement, ...] = ()

    @property
    def selectable_lots(self) -> bool:
        """Cas C: hi ha lots per triar i els tria una persona."""
        return self.lot_mode == SELECTED_LOTS

    @property
    def undecided(self) -> bool:
        """Cas C sense cap lot trial: encara no se sap a que ens presentem.

        L'abast no es buit: es desconegut, i per aixo no es pot confirmar.
        """
        return self.selectable_lots and not self.tender_lot_ids


async def resolve(session: AsyncSession, workspace: BidWorkspace) -> TechnicalScope:
    """L'abast tecnic vigent. Cap efecte: nomes llegeix."""
    lot_mode, lot_ids = await _lots(session, workspace)
    lot_requirements = await _requirements(session, workspace, lot_ids, lot_mode)
    return TechnicalScope(
        lot_mode=lot_mode,
        tender_lot_ids=lot_ids,
        requirements=tuple(
            requirement
            for requirement in lot_requirements
            if route_requirement(requirement) == TECHNICAL_SOLUTION
        ),
        lot_requirements=lot_requirements,
    )


async def _lots(
    session: AsyncSession, workspace: BidWorkspace
) -> tuple[str, tuple[uuid.UUID, ...]]:
    if workspace.tender_lot_id is not None:
        return LOT_BOUND, (workspace.tender_lot_id,)

    has_lots = (
        await session.execute(
            sa.select(sa.func.count(TenderLot.id)).where(
                TenderLot.tender_id == workspace.tender_id
            )
        )
    ).scalar_one() > 0
    if not has_lots:
        return NO_LOTS, ()

    # Cas C: mana l'oferta economica. Un lot arxivat ja no hi es.
    rows = await session.scalars(
        sa.select(EconomicOfferLot.tender_lot_id)
        .join(EconomicOffer, EconomicOffer.id == EconomicOfferLot.offer_id)
        .where(
            EconomicOffer.workspace_id == workspace.id,
            EconomicOfferLot.archived_at.is_(None),
            EconomicOfferLot.tender_lot_id.is_not(None),
        )
    )
    return SELECTED_LOTS, tuple(sorted({row for row in rows.all()}, key=str))


async def _requirements(
    session: AsyncSession,
    workspace: BidWorkspace,
    lot_ids: tuple[uuid.UUID, ...],
    lot_mode: str,
) -> tuple[Requirement, ...]:
    """Els requisits VIGENTS que apliquen a aquests lots (tots, sense routing).

    Vigents vol dir el conjunt efectiu: els de l'extraccio vigent MES els
    escrits a ma i no arxivats. Si nomes mires l'extraccio, un requisit que
    algu ha afegit perque el model no el va veure no arribaria mai a l'abast
    tecnic, i el sistema diria que no cal cap solucio per a una cosa que si que
    en necessita.

    Com que l'empremta de l'abast es fa d'aqui, aixo tambe vol dir que crear,
    editar o arxivar un requisit manual fa caducar una revisio ja confirmada,
    que es exactament el que ha de passar.
    """
    effective = await manual_requirement.effective_requirements(
        session, workspace.tender_id
    )
    return tuple(
        requirement
        for requirement in effective.all
        if applies_to_lots(requirement, lot_ids=lot_ids, lot_mode=lot_mode)
    )


def in_scope(
    requirement: Requirement, *, lot_ids: Sequence[uuid.UUID], lot_mode: str
) -> bool:
    """La regla, en un sol lloc.

    Dues condicions. La primera, que el requisit es treballi a Solucio tecnica
    (`route_requirement`): el DEUC, la solvencia o un termini son requisits de
    la candidatura, pero no demanen cap solucio tecnica i no formen part de
    l'abast que es revisa aqui. Es continuen veient a Requisits, Compliment i
    Dossier; nomes no se'n demana solucio.

    La segona, el lot. Un requisit que no diu de quin lot es val per a tots: es
    la mateixa lectura que ja fa `applies_to_workspace_lot` i que fa servir la
    checklist. Inventar l'abast d'un requisit seria pitjor que aplicar-lo.
    """
    if route_requirement(requirement) != TECHNICAL_SOLUTION:
        return False
    return applies_to_lots(requirement, lot_ids=lot_ids, lot_mode=lot_mode)


def applies_to_lots(
    requirement: Requirement, *, lot_ids: Sequence[uuid.UUID], lot_mode: str
) -> bool:
    """Nomes la regla de lots (C-16), sense mirar si demana solucio."""
    if lot_mode != SELECTED_LOTS:
        # Un sol lot (o cap): la regla de sempre serveix tal qual.
        return applies_to_workspace_lot(requirement, lot_ids[0] if lot_ids else None)
    if getattr(requirement, "tender_lot_id", None) is None:
        return True
    return requirement.tender_lot_id in set(lot_ids)


async def scope_fingerprint(
    session: AsyncSession, workspace: BidWorkspace, scope: TechnicalScope | None = None
) -> str:
    """L'empremta de l'abast d'ara, amb la seleccio humana inclosa.

    Es fa de `lot_requirements` (tots els del lot) i no nomes dels que demanen
    solucio: veure `TechnicalScope.lot_requirements`.
    """
    scope = scope or await resolve(session, workspace)
    dins = {requirement.id for requirement in scope.lot_requirements}

    seleccionats = await session.scalars(
        sa.select(WorkspaceTechnicalSolution.requirement_id).where(
            WorkspaceTechnicalSolution.workspace_id == workspace.id,
            WorkspaceTechnicalSolution.source == TechnicalSolutionSource.REQUIREMENT,
            WorkspaceTechnicalSolution.requirement_id.is_not(None),
        )
    )
    return technical_scope_fingerprint(
        lot_mode=scope.lot_mode,
        tender_lot_ids=scope.tender_lot_ids,
        requirements=[
            (
                requirement.id,
                requirement_fingerprint(
                    requirement.category, requirement.title, requirement.value_json
                ),
            )
            for requirement in scope.lot_requirements
        ],
        # Nomes les que assenyalen un requisit que ARA es dins de l'abast: una
        # fila que va quedar fora es historia i no ha de moure l'empremta.
        selected_requirement_ids=[rid for rid in seleccionats.all() if rid in dins],
    )


@dataclass(frozen=True, slots=True)
class ScopeReview:
    """Si algu ha revisat l'abast, i si allo que va revisar segueix sent aixo."""

    reviewed: bool
    stale: bool
    reviewed_at: datetime | None = None
    reviewed_by: uuid.UUID | None = None


async def review_state(
    session: AsyncSession, workspace: BidWorkspace, scope: TechnicalScope | None = None
) -> ScopeReview:
    """Es deriva en llegir, mai es persisteix.

    Guardar "caducada" voldria dir tenir dues versions del mateix fet, i la
    desada quedaria enrere a la primera cosa que canvies sense passar per aqui.
    """
    if workspace.technical_scope_fingerprint is None:
        return ScopeReview(reviewed=False, stale=False)
    actual = await scope_fingerprint(session, workspace, scope)
    return ScopeReview(
        reviewed=True,
        stale=actual != workspace.technical_scope_fingerprint,
        reviewed_at=workspace.technical_scope_reviewed_at,
        reviewed_by=workspace.technical_scope_reviewed_by,
    )


async def confirm(
    session: AsyncSession, workspace: BidWorkspace, *, user: User
) -> tuple[BidWorkspace, bool]:
    """Una persona afirma que ha revisat l'abast tecnic actual.

    Idempotent: reconfirmar exactament el mateix abast no es un fet nou i no
    deixa rastre. Si l'abast ha canviat, es una afirmacio NOVA sobre una altra
    cosa, i per tant s'actualitza qui i quan.
    """
    scope = await resolve(session, workspace)
    if scope.undecided:
        # Cas C sense cap lot trial: els requisits que apliquen depenen d'uns
        # lots que ningu no ha escollit. Confirmar aqui seria signar sobre un
        # abast que encara no existeix.
        raise ScopeNotConfirmableError(
            "Primer cal triar a quins lots es presenta la candidatura."
        )
    fingerprint = await scope_fingerprint(session, workspace, scope)
    if workspace.technical_scope_fingerprint == fingerprint:
        return workspace, False

    workspace.technical_scope_fingerprint = fingerprint
    workspace.technical_scope_reviewed_by = user.id
    workspace.technical_scope_reviewed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(workspace)
    return workspace, True
