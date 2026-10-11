"""Quina run d'extraccio representa la revisio d'una empresa (S-R7, guarda de rerun).

Una revisio materialitzada sobre la run A enllaça cada element amb una fila
extreta d'A. Si despres la run B passa a ser la vigent, les files de B tenen ids
nous: materialitzar-les crearia un segon element canonic per a cada requisit
ja revisat. Fins que hi hagi reconciliacio, `start` ho refusa amb un 409.

La run «vigent» d'una empresa es la del seu punter si n'ha acceptat una
(`company_tender_requirement_runs`, 3B-2 §6.5) i, si no, la canonica global. Una
run canonica nova no la substitueix: l'empresa l'adopta
(`adopt_requirement_run`, l'unica escriptura d'aquest modul) i aleshores la seva
revisio s'ha de reconciliar. Requisits i criteris es calculen per separat; els
elements manuals (sense font extreta) no representen cap run.

**Reconciliacio oberta (S-R9).** Mentre una reconciliacio NO completada cap a la
run vigent tingui base d'origen en una seccio, aquella seccio continua
bloquejada encara que totes les fonts ja apuntin a la run nova: pot quedar-hi
feina (files noves, retirades) que `start` no ha de resoldre pel seu compte.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.company_runs import CompanyTenderRequirementRun
from app.models.enums import (
    AnalysisRunStatus,
    ReviewItemKind,
    ReviewReconciliationState,
    ReviewSourceRole,
)
from app.models.jobs import JobRequest
from app.models.requirements import AwardCriterion, Requirement, RequirementExtractionRun
from app.models.review import ReviewItem, ReviewItemSource, ReviewReconciliation
from app.models.tenders import Tender
from app.services.manual_requirement import effective_run
from app.services.requirement_analysis import (
    INPUT_CHANGED,
    TRUSTED_RUN_BASES,
    carry_over_in_savepoint,
)

RECONCILIATION_MESSAGE = (
    "Hi ha una extracció nova. Revisa els canvis abans d'actualitzar la revisió."
)


class ReconciliationRequiredError(ConflictError):
    """La revisio representa una altra run (o mes d'una): no es materialitza res."""

    code = "review_reconciliation_required"


async def current_run_id(
    session: AsyncSession, tender_id: uuid.UUID, company_id: uuid.UUID | None = None
) -> uuid.UUID | None:
    """La run que representa la revisio d'aquesta empresa: el seu punter o, si no
    en te, la canonica (3B-2 §6.5). Sense empresa, la canonica global."""
    run = await effective_run(session, tender_id, company_id)
    return run.id if run is not None else None


# --------------------------------------------------------------------------- #
# Adopcio d'un run per una empresa (3B-2 §6.5)                                  #
# --------------------------------------------------------------------------- #


class RequirementRunNotAdoptableError(ConflictError):
    """Aquest run no es pot acceptar: ni es el canonic ni l'ha demanat l'empresa."""

    code = "requirement_run_not_adoptable"


#: Estats d'un run que una empresa pot acceptar.
ADOPTABLE_STATUSES = (AnalysisRunStatus.COMPLETED, AnalysisRunStatus.PARTIAL)


@dataclass(frozen=True, slots=True)
class AdoptionResult:
    pointer: CompanyTenderRequirementRun
    #: La run que l'empresa llegia abans (punter o canonica). `None` si cap.
    previous_run_id: uuid.UUID | None
    #: Cert si la revisio existent de l'empresa representa una altra run: s'ha de
    #: reconciliar pel flux habitual (`review_reconciliation`) abans de continuar.
    reconciliation_required: bool


async def _requested_by(
    session: AsyncSession, company_id: uuid.UUID, run: RequirementExtractionRun
) -> bool:
    """Cert si alguna peticio d'aquesta empresa va donar (o executar) aquest run."""
    links = [JobRequest.result_run_id == run.id]
    if run.work_job_id is not None:
        links.append(JobRequest.work_job_id == run.work_job_id)
    return bool(
        await session.scalar(
            sa.select(
                sa.exists().where(
                    JobRequest.company_id == company_id,
                    JobRequest.tender_id == run.tender_id,
                    sa.or_(*links),
                )
            )
        )
    )


async def adopt_requirement_run(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> AdoptionResult:
    """L'empresa accepta `run_id` com la seva base de requisits. Sense commit.

    Es pot acceptar el run canonic vigent o un run acabat que l'empresa mateixa
    va demanar (p. ex. el seu parcial real, §6.3), sempre que sigui `real` o `gold`
    i no hagi quedat `input_changed` (naixeria obsolet): una simulacio no es mai la
    base de ningu.

    Nomes s'escriu el punter (dada de tenant). En una licitacio PUBLICA no es toca
    cap fila global: les anotacions d'estructura ja les ha portat la promocio
    canonica (§4.3), i una empresa no pot desfer una decisio de l'operador (§5). En
    una licitacio MANUAL (plecs i anotacions del propietari) les de la run efectiva
    anterior passen a la nova (`carry_over_in_savepoint`; cap escriptura de la run
    antiga). La revisio de l'empresa NO es toca: a partir d'ara representa una altra
    run i la guarda de `start` demana la reconciliacio habitual
    (`reconciliation_required`).
    """
    run = await session.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.id == run_id,
            RequirementExtractionRun.tender_id == tender_id,
        )
    )
    if run is None:
        raise NotFoundError("Run de requisits no trobat.")
    if (
        run.status not in ADOPTABLE_STATUSES
        or run.run_basis not in TRUSTED_RUN_BASES
        or run.error_code == INPUT_CHANGED
        or not (run.is_current or await _requested_by(session, company_id, run))
    ):
        raise RequirementRunNotAdoptableError(
            "Aquest run no es pot acceptar: ha de ser una analisi real, acabada i "
            "vigent o demanada per l'empresa.",
            details={"run_id": str(run.id)},
        )

    previous = await effective_run(session, tender_id, company_id)
    pointer = await session.get(CompanyTenderRequirementRun, (company_id, tender_id))
    if pointer is None:
        pointer = CompanyTenderRequirementRun(company_id=company_id, tender_id=tender_id)
        session.add(pointer)
    pointer.run_id = run.id
    pointer.accepted_by = user_id
    pointer.accepted_at = datetime.now(UTC)
    await session.flush()

    previous_id = previous.id if previous is not None else None
    if previous_id is not None and previous_id != run.id:
        owner = await session.scalar(
            sa.select(Tender.owner_company_id).where(Tender.id == tender_id)
        )
        if owner is not None:
            await carry_over_in_savepoint(session, previous_id, run.id)
    represented = await represented_requirement_run_ids(
        session, company_id, tender_id
    ) | await represented_award_run_ids(session, company_id, tender_id)
    return AdoptionResult(
        pointer=pointer,
        previous_run_id=previous_id if previous_id != run.id else None,
        reconciliation_required=needs_reconciliation(represented, run.id),
    )


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


def ensure_reconciled(
    represented: Iterable[uuid.UUID],
    current: uuid.UUID | None,
    *,
    open_reconciliation_id: uuid.UUID | None = None,
) -> None:
    """Llança el 409 abans de cap escriptura. Els detalls son nomes ids.

    `open_reconciliation_id`: la seccio forma part d'una reconciliacio encara
    no completada; es bloqueja encara que les fonts ja siguin de la run vigent.
    """
    runs = set(represented)
    if open_reconciliation_id is not None or needs_reconciliation(runs, current):
        details = {
            "current_run_id": str(current) if current is not None else None,
            "represented_run_ids": sorted(str(r) for r in runs),
        }
        if open_reconciliation_id is not None:
            details["reconciliation_id"] = str(open_reconciliation_id)
        raise ReconciliationRequiredError(RECONCILIATION_MESSAGE, details=details)


async def open_reconciliation(
    session: AsyncSession,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    current: uuid.UUID | None,
) -> ReviewReconciliation | None:
    """La reconciliacio NO completada d'aquesta empresa cap a la run VIGENT.

    Una reconciliacio cap a una run que ja no es la vigent no explica res: si
    les fonts barregen runs, l'estat torna a ser un error (es tanca).
    """
    if current is None:
        return None
    return await session.scalar(
        sa.select(ReviewReconciliation).where(
            ReviewReconciliation.company_id == company_id,
            ReviewReconciliation.tender_id == tender_id,
            ReviewReconciliation.target_run_id == current,
            ReviewReconciliation.state != ReviewReconciliationState.COMPLETED,
        )
    )


def section_baseline(
    reconciliation: ReviewReconciliation | None, kind: ReviewItemKind
) -> set[uuid.UUID]:
    """Les runs d'origen desades d'una seccio (buit si la seccio no hi participa)."""
    if reconciliation is None:
        return set()
    raw = (
        reconciliation.requirement_source_run_ids
        if kind == ReviewItemKind.REQUIREMENT
        else reconciliation.award_source_run_ids
    )
    return {uuid.UUID(str(value)) for value in raw or []}


async def ensure_section_reconciled(
    session: AsyncSession,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    kind: ReviewItemKind,
    current: uuid.UUID | None,
) -> None:
    """La guarda de `start` d'una seccio: rerun sense reconciliar o reconciliacio oberta."""
    represented = await _represented(session, company_id, tender_id, kind)
    reconciliation = await open_reconciliation(session, company_id, tender_id, current)
    pending = reconciliation is not None and bool(section_baseline(reconciliation, kind))
    ensure_reconciled(
        represented, current, open_reconciliation_id=reconciliation.id if pending else None
    )
