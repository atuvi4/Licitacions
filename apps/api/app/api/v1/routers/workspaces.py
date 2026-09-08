"""Workspace de candidatura: HTTP.

Tot penja de `/companies/{company_id}`: un workspace es dada de tenant i
`get_company_context` comprova la pertinenca a cada crida. L'endpoint nomes
tradueix HTTP; les regles viuen a `app.workspace` i l'orquestracio a
`app.services.bid_workspace`.

La decisio humana te el seu propi endpoint (`POST .../decisions`) i cap altre
cami pot deixar el workspace en `go_approved` o `no_go_approved`. Aixo es
deliberat: si l'aprovacio fos un camp mes d'un PATCH generic, qualsevol client
—o qualsevol automatisme futur— podria escriure-la sense adonar-se'n.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, SessionDep
from app.core.errors import NotFoundError
from app.models.enums import (
    AuditAction,
    ChecklistItemSource,
    ChecklistItemStatus,
    WorkspaceTaskStatus,
)
from app.models.identity import User
from app.models.tenders import Buyer, Tender
from app.models.workspace import (
    BidWorkspace,
    WorkspaceChecklistItem,
    WorkspaceNote,
    WorkspaceTask,
)
from app.schemas.workspace import (
    ChecklistItemCreateRequest,
    ChecklistItemRead,
    ChecklistItemUpdateRequest,
    ChecklistRegenerateResult,
    ComplianceCountersRead,
    DecisionRead,
    DecisionRequest,
    DossierCoverageRead,
    NoteCreateRequest,
    NoteRead,
    PersonRead,
    TaskCreateRequest,
    TaskRead,
    TaskUpdateRequest,
    WorkspaceCreateRequest,
    WorkspaceCreateResult,
    WorkspaceDetail,
    WorkspaceSummary,
)
from app.services import audit, workspace_dossier
from app.services import bid_workspace as service

tender_router = APIRouter(prefix="/tenders/{tender_id}/workspace", tags=["candidatura"])
router = APIRouter(prefix="/workspaces", tags=["candidatura"])


# --------------------------------------------------------------------------- #
# Utils                                                                         #
# --------------------------------------------------------------------------- #


async def _people_names(
    session: SessionDep, user_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """Nom visible de cada usuari implicat. Sense aixo l'historial son UUIDs."""
    ids = {uid for uid in user_ids if uid}
    if not ids:
        return {}
    rows = (
        await session.execute(
            sa.select(User.id, User.full_name, User.email).where(User.id.in_(ids))
        )
    ).all()
    return {row[0]: row[1] or row[2] for row in rows}


def _decision_read(decision, names: dict[uuid.UUID, str | None]) -> DecisionRead:
    read = DecisionRead.model_validate(decision)
    read.decided_by_name = names.get(decision.decided_by)
    return read


def _checklist_read(
    item: WorkspaceChecklistItem, names: dict[uuid.UUID, str | None]
) -> ChecklistItemRead:
    read = ChecklistItemRead.model_validate(item)
    read.assigned_to_name = names.get(item.assigned_to) if item.assigned_to else None
    return read


def _task_read(task: WorkspaceTask, names: dict[uuid.UUID, str | None]) -> TaskRead:
    read = TaskRead.model_validate(task)
    read.assigned_to_name = names.get(task.assigned_to) if task.assigned_to else None
    return read


def _note_read(note: WorkspaceNote, names: dict[uuid.UUID, str | None]) -> NoteRead:
    read = NoteRead.model_validate(note)
    read.author_name = names.get(note.author_id) if note.author_id else None
    return read


async def _summary(session: SessionDep, workspace: BidWorkspace) -> WorkspaceSummary:
    state = await service.load_state(session, workspace)
    buyer_name = None
    if state.tender.buyer_id:
        buyer_name = await session.scalar(
            sa.select(Buyer.name).where(Buyer.id == state.tender.buyer_id)
        )
    return WorkspaceSummary(
        id=workspace.id,
        company_id=workspace.company_id,
        tender_id=workspace.tender_id,
        tender_lot_id=workspace.tender_lot_id,
        status=workspace.status,
        tender_title=state.tender.title,
        expediente_number=state.tender.expediente_number,
        buyer_name=buyer_name,
        deadline_at=state.deadline_at,
        days_remaining=state.days_remaining,
        deadline_passed=state.deadline_passed,
        prefit_score=state.prefit_score,
        eligibility=state.compliance_run.overall_result if state.compliance_run else None,
        recommendation=state.compliance_run.recommendation if state.compliance_run else None,
        human_decision=state.latest_decision.decision if state.latest_decision else None,
        analysis_outdated=state.analysis_outdated,
        created_at=workspace.created_at,
    )


async def _detail(
    session: SessionDep, ctx: CompanyCtx, workspace: BidWorkspace
) -> WorkspaceDetail:
    state = await service.load_state(session, workspace)
    items = await service.checklist_items(session, workspace.id)
    tasks = await service.workspace_tasks(session, workspace.id)
    notes = await service.workspace_notes(session, workspace.id)
    decisions = await service.decision_history(session, workspace.id)
    members = await service.company_members(session, ctx.company_id)

    names = await _people_names(
        session,
        {item.assigned_to for item in items}
        | {task.assigned_to for task in tasks}
        | {note.author_id for note in notes}
        | {decision.decided_by for decision in decisions},
    )

    buyer_name = None
    if state.tender.buyer_id:
        buyer_name = await session.scalar(
            sa.select(Buyer.name).where(Buyer.id == state.tender.buyer_id)
        )

    warnings = state.warnings
    can_decide = ctx.membership.role in service.DECISION_ROLES
    latest = decisions[0] if decisions else None
    # Readiness del dossier: una tercera dimensio que tampoc no es fusiona amb
    # el compliment ni amb la decisio. "Complim" i "tenim els papers" son coses
    # diferents i s'han de poder llegir per separat.
    dossier = await workspace_dossier.load_dossier(session, workspace)
    await workspace_dossier.reconcile_readiness(
        session, workspace, readiness=dossier.readiness
    )

    return WorkspaceDetail(
        id=workspace.id,
        company_id=workspace.company_id,
        tender_id=workspace.tender_id,
        tender_lot_id=workspace.tender_lot_id,
        lot_number=state.lot.lot_number if state.lot else None,
        lot_title=state.lot.title if state.lot else None,
        status=workspace.status,
        tender_title=state.tender.title,
        expediente_number=state.tender.expediente_number,
        buyer_name=buyer_name,
        source_url=state.tender.source_url,
        deadline_at=state.deadline_at,
        days_remaining=state.days_remaining,
        deadline_passed=state.deadline_passed,
        prefit_score=state.prefit_score,
        prefit_status=state.prefit_status,
        eligibility=state.compliance_run.overall_result if state.compliance_run else None,
        recommendation=state.compliance_run.recommendation if state.compliance_run else None,
        human_decision=latest.decision if latest else None,
        latest_decision=_decision_read(latest, names) if latest else None,
        counters=ComplianceCountersRead(**asdict(state.counters)),
        open_warnings=warnings.codes(),
        go_requires_reason=warnings.any_open,
        current_compliance_run_id=workspace.current_compliance_run_id,
        current_requirement_run_id=workspace.current_requirement_run_id,
        analysis_outdated=state.analysis_outdated,
        outdated_reasons=list(state.outdated_reasons),
        readiness=dossier.readiness.state,
        readiness_blockers=list(dossier.readiness.blockers),
        dossier_coverage=DossierCoverageRead(**asdict(dossier.counters)),
        checklist=[_checklist_read(item, names) for item in items],
        tasks=[_task_read(task, names) for task in tasks],
        notes=[_note_read(note, names) for note in notes],
        decisions=[_decision_read(decision, names) for decision in decisions],
        members=[
            PersonRead(id=member.id, email=member.email, full_name=member.full_name)
            for member in members
        ],
        can_decide=can_decide,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


async def _load_tender(session: SessionDep, tender_id: uuid.UUID) -> Tender:
    tender = await session.get(Tender, tender_id)
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


# --------------------------------------------------------------------------- #
# Creacio i llistat                                                             #
# --------------------------------------------------------------------------- #


@tender_router.post("", response_model=WorkspaceCreateResult, summary="Obre la candidatura")
async def create_workspace(
    tender_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    payload: WorkspaceCreateRequest | None = None,
):
    """Idempotent: si ja existeix, retorna el que hi ha amb `created=false`."""
    tender = await _load_tender(session, tender_id)
    options = payload or WorkspaceCreateRequest()
    outcome = await service.create_workspace(
        session,
        company_id=ctx.company_id,
        tender=tender,
        user_id=ctx.user.id,
        tender_lot_id=options.tender_lot_id,
    )
    if outcome.created:
        await audit.record(
            session,
            action=AuditAction.CREATE,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            entity_type="bid_workspace",
            entity_id=outcome.workspace.id,
            summary="Workspace de candidatura obert",
        )
        await session.commit()
    return WorkspaceCreateResult(
        created=outcome.created,
        checklist_items_created=outcome.checklist_items_created,
        workspace=await _summary(session, outcome.workspace),
    )


@tender_router.get("", response_model=WorkspaceDetail, summary="Candidatura d'una licitacio")
async def get_by_tender(
    tender_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    tender_lot_id: uuid.UUID | None = Query(default=None),
):
    workspace = await service.find_workspace(
        session, ctx.company_id, tender_id, tender_lot_id
    )
    if workspace is None:
        raise NotFoundError("Encara no hi ha cap candidatura per a aquesta licitacio.")
    return await _detail(session, ctx, workspace)


@router.get("", response_model=list[WorkspaceSummary], summary="Candidatures de l'empresa")
async def list_workspaces(
    session: SessionDep,
    ctx: CompanyCtx,
    limit: int = Query(default=50, ge=1, le=200),
):
    workspaces = (
        await session.scalars(
            sa.select(BidWorkspace)
            .where(BidWorkspace.company_id == ctx.company_id)
            .order_by(BidWorkspace.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [await _summary(session, workspace) for workspace in workspaces]


@router.get("/{workspace_id}", response_model=WorkspaceDetail, summary="Workspace")
async def get_workspace(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    workspace = await service.get_workspace(session, ctx.company_id, workspace_id)
    return await _detail(session, ctx, workspace)


# --------------------------------------------------------------------------- #
# Decisio humana                                                                #
# --------------------------------------------------------------------------- #


@router.post(
    "/{workspace_id}/decisions", response_model=WorkspaceDetail, summary="Decisio humana"
)
async def decide(
    workspace_id: uuid.UUID,
    payload: DecisionRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """L'unic cami cap a `go_approved` i `no_go_approved`.

    Exigeix usuari autenticat (`get_current_user`), pertinenca a l'empresa
    (`get_company_context`), rol amb potestat i una accio explicita. Qui i quan
    queden a la fila, que no s'actualitza mai.
    """
    workspace = await service.get_workspace(session, ctx.company_id, workspace_id)
    decision = await service.record_decision(
        session,
        workspace,
        decision=payload.decision,
        user=ctx.user,
        role=ctx.membership.role,
        reason=payload.reason,
    )
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        entity_type="workspace_decision",
        entity_id=decision.id,
        summary=f"Decisio {decision.decision.value} sobre la candidatura",
    )
    await session.commit()
    return await _detail(session, ctx, workspace)


@router.get(
    "/{workspace_id}/decisions",
    response_model=list[DecisionRead],
    summary="Historial de decisions",
)
async def list_decisions(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Del mes recent al mes antic. Cap entrada no s'esborra ni es reescriu."""
    await service.get_workspace(session, ctx.company_id, workspace_id)
    decisions = await service.decision_history(session, workspace_id)
    names = await _people_names(session, {d.decided_by for d in decisions})
    return [_decision_read(decision, names) for decision in decisions]


# --------------------------------------------------------------------------- #
# Checklist                                                                     #
# --------------------------------------------------------------------------- #


@router.post(
    "/{workspace_id}/checklist/regenerate",
    response_model=ChecklistRegenerateResult,
    summary="Reconcilia la checklist amb l'analisi vigent",
)
async def regenerate_checklist(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Afegeix el que falta. No esborra ni sobreescriu res del que ja hi havia."""
    workspace = await service.get_workspace(session, ctx.company_id, workspace_id)
    created = await service.regenerate_checklist(session, workspace, user_id=ctx.user.id)
    items = await service.checklist_items(session, workspace.id)
    names = await _people_names(session, {item.assigned_to for item in items})
    return ChecklistRegenerateResult(
        created=created,
        total=len(items),
        checklist=[_checklist_read(item, names) for item in items],
    )


@router.post(
    "/{workspace_id}/checklist", response_model=ChecklistItemRead, summary="Item manual"
)
async def create_checklist_item(
    workspace_id: uuid.UUID,
    payload: ChecklistItemCreateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await service.get_workspace(session, ctx.company_id, workspace_id)
    await service.assert_assignable(session, ctx.company_id, payload.assigned_to)
    item = WorkspaceChecklistItem(
        company_id=ctx.company_id,
        workspace_id=workspace.id,
        requirement_id=None,
        title=payload.title,
        category=payload.category,
        mandatory=payload.mandatory,
        status=ChecklistItemStatus.PENDING,
        source=ChecklistItemSource.MANUAL,
        assigned_to=payload.assigned_to,
        due_at=payload.due_at,
        note=payload.note,
        created_by=ctx.user.id,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    names = await _people_names(session, {item.assigned_to})
    return _checklist_read(item, names)


@router.patch(
    "/{workspace_id}/checklist/{item_id}",
    response_model=ChecklistItemRead,
    summary="Actualitza un item",
)
async def update_checklist_item(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ChecklistItemUpdateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    await service.get_workspace(session, ctx.company_id, workspace_id)
    item = await session.scalar(
        sa.select(WorkspaceChecklistItem).where(
            WorkspaceChecklistItem.id == item_id,
            WorkspaceChecklistItem.workspace_id == workspace_id,
            WorkspaceChecklistItem.company_id == ctx.company_id,
        )
    )
    if item is None:
        raise NotFoundError("Item no trobat.")

    fields = payload.model_dump(exclude_unset=True)
    if "assigned_to" in fields:
        await service.assert_assignable(session, ctx.company_id, fields["assigned_to"])
    for field, value in fields.items():
        setattr(item, field, value)
    if payload.status is not None:
        service.mark_completion(item, payload.status)

    await session.commit()
    await session.refresh(item)
    # Bloquejar un item obligatori ha de treure el workspace de "llest".
    workspace = await service.get_workspace(session, ctx.company_id, workspace_id)
    await workspace_dossier.reconcile_readiness(session, workspace)
    names = await _people_names(session, {item.assigned_to})
    return _checklist_read(item, names)


# --------------------------------------------------------------------------- #
# Tasques                                                                       #
# --------------------------------------------------------------------------- #


@router.post("/{workspace_id}/tasks", response_model=TaskRead, summary="Crea una tasca")
async def create_task(
    workspace_id: uuid.UUID,
    payload: TaskCreateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await service.get_workspace(session, ctx.company_id, workspace_id)
    await service.assert_assignable(session, ctx.company_id, payload.assigned_to)
    task = WorkspaceTask(
        company_id=ctx.company_id,
        workspace_id=workspace.id,
        title=payload.title,
        description=payload.description,
        mandatory=payload.mandatory,
        assigned_to=payload.assigned_to,
        due_at=payload.due_at,
        status=WorkspaceTaskStatus.PENDING,
        created_by=ctx.user.id,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    await workspace_dossier.reconcile_readiness(session, workspace)
    names = await _people_names(session, {task.assigned_to})
    return _task_read(task, names)


@router.patch(
    "/{workspace_id}/tasks/{task_id}", response_model=TaskRead, summary="Actualitza una tasca"
)
async def update_task(
    workspace_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: TaskUpdateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    await service.get_workspace(session, ctx.company_id, workspace_id)
    task = await session.scalar(
        sa.select(WorkspaceTask).where(
            WorkspaceTask.id == task_id,
            WorkspaceTask.workspace_id == workspace_id,
            WorkspaceTask.company_id == ctx.company_id,
        )
    )
    if task is None:
        raise NotFoundError("Tasca no trobada.")

    fields = payload.model_dump(exclude_unset=True)
    if "assigned_to" in fields:
        await service.assert_assignable(session, ctx.company_id, fields["assigned_to"])
    for field, value in fields.items():
        setattr(task, field, value)
    if payload.status is not None:
        service.mark_completion(task, payload.status)

    await session.commit()
    await session.refresh(task)
    # Reobrir una tasca obligatoria torna a deixar feina pendent.
    workspace = await service.get_workspace(session, ctx.company_id, workspace_id)
    await workspace_dossier.reconcile_readiness(session, workspace)
    names = await _people_names(session, {task.assigned_to})
    return _task_read(task, names)


# --------------------------------------------------------------------------- #
# Notes                                                                         #
# --------------------------------------------------------------------------- #


@router.post("/{workspace_id}/notes", response_model=NoteRead, summary="Escriu una nota")
async def create_note(
    workspace_id: uuid.UUID,
    payload: NoteCreateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await service.get_workspace(session, ctx.company_id, workspace_id)
    note = WorkspaceNote(
        company_id=ctx.company_id,
        workspace_id=workspace.id,
        author_id=ctx.user.id,
        text=payload.text,
        created_at=datetime.now(UTC),
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)
    names = await _people_names(session, {note.author_id})
    return _note_read(note, names)


@router.get("/{workspace_id}/notes", response_model=list[NoteRead], summary="Notes")
async def list_notes(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    await service.get_workspace(session, ctx.company_id, workspace_id)
    notes = await service.workspace_notes(session, workspace_id)
    names = await _people_names(session, {note.author_id for note in notes})
    return [_note_read(note, names) for note in notes]
