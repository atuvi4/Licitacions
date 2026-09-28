"""Quina run d'extraccio representa la revisio d'una empresa (S-R7, guarda de rerun).

Una revisio materialitzada sobre la run A enllaça cada element amb una fila
extreta d'A. Si despres la run B passa a ser la vigent, les files de B tenen ids
nous: materialitzar-les crearia un segon element canonic per a cada requisit
ja revisat. Fins que hi hagi reconciliacio, `start` ho refusa amb un 409.

Nomes llegeix. La run vigent es la mateixa de sempre (`current_run_for`).
Requisits i criteris es calculen per separat; els elements manuals (sense font
extreta) no representen cap run.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.enums import ReviewItemKind, ReviewSourceRole
from app.models.requirements import AwardCriterion, Requirement
from app.models.review import ReviewItem, ReviewItemSource
from app.services.requirement_analysis import current_run_for

RECONCILIATION_MESSAGE = (
    "Hi ha una extracció nova. Revisa els canvis abans d'actualitzar la revisió."
)


class ReconciliationRequiredError(ConflictError):
    """La revisio representa una altra run (o mes d'una): no es materialitza res."""

    code = "review_reconciliation_required"


async def current_run_id(session: AsyncSession, tender_id: uuid.UUID) -> uuid.UUID | None:
    run = await current_run_for(session, tender_id)
    return run.id if run is not None else None


#: Per tipus d'element: la columna de la font i la taula extreta on apunta.
_SOURCES = {
    ReviewItemKind.REQUIREMENT: (ReviewItemSource.requirement_id, Requirement),
    ReviewItemKind.AWARD_CRITERION: (ReviewItemSource.award_criterion_id, AwardCriterion),
}


def active_primary_links(
    company_id: uuid.UUID, tender_id: uuid.UUID, kind: ReviewItemKind, *columns
) -> sa.Select:
    """Elements actius d'aquesta empresa amb la seva font PRIMARIA extreta activa.

    L'unica definicio de "font canonica vella": la fan servir la guarda de
    `start` i la previsualitzacio de reconciliacio. Els manuals no hi surten.
    """
    source_column, row = _SOURCES[kind]
    return (
        sa.select(*columns)
        .select_from(ReviewItem)
        .join(ReviewItemSource, ReviewItemSource.item_id == ReviewItem.id)
        .join(row, row.id == source_column)
        .where(
            ReviewItem.company_id == company_id,
            ReviewItem.tender_id == tender_id,
            ReviewItem.kind == kind,
            ReviewItem.archived_at.is_(None),
            ReviewItemSource.company_id == company_id,
            ReviewItemSource.link_role == ReviewSourceRole.PRIMARY,
            ReviewItemSource.unlinked_at.is_(None),
            row.run_id.is_not(None),
        )
    )


async def _represented(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID, kind: ReviewItemKind
) -> set[uuid.UUID]:
    """Runs de les fonts PRIMARIES actives dels elements actius d'aquesta empresa."""
    _column, row = _SOURCES[kind]
    query = active_primary_links(company_id, tender_id, kind, row.run_id).distinct()
    return {run_id for run_id in (await session.scalars(query)).all() if run_id is not None}


async def represented_requirement_run_ids(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> set[uuid.UUID]:
    return await _represented(session, company_id, tender_id, ReviewItemKind.REQUIREMENT)


async def represented_award_run_ids(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> set[uuid.UUID]:
    return await _represented(session, company_id, tender_id, ReviewItemKind.AWARD_CRITERION)


def needs_reconciliation(represented: Iterable[uuid.UUID], current: uuid.UUID | None) -> bool:
    """Cert si la revisio no representa exactament la run vigent.

    Sense elements extrets: res a reconciliar. Sense run vigent: no hi ha res
    a materialitzar i no s'inventa cap estat. Mes d'una run: estat barrejat,
    es tanca sempre.
    """
    runs = set(represented)
    if len(runs) > 1:
        return True
    if not runs or current is None:
        return False
    return runs != {current}


def ensure_reconciled(represented: Iterable[uuid.UUID], current: uuid.UUID | None) -> None:
    """Llança el 409 abans de cap escriptura. Els detalls son nomes ids."""
    runs = set(represented)
    if needs_reconciliation(runs, current):
        raise ReconciliationRequiredError(
            RECONCILIATION_MESSAGE,
            details={
                "current_run_id": str(current) if current is not None else None,
                "represented_run_ids": sorted(str(r) for r in runs),
            },
        )
