"""Dossier documental d'una candidatura: HTTP.

Penja del workspace, que al seu torn penja de `/companies/{company_id}`: la
pertinenca es comprova a cada crida i les peces del dossier es filtren sempre
per empresa i per workspace.

Res d'aqui no tria cap document. `POST .../sync` PROPOSA a partir del que ja va
fer servir el motor de compliment; triar es un `PATCH` fet per una persona, i
llavors queden escrits `selected_by` i `selected_at`.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter

from app.api.deps import CompanyCtx, SessionDep
from app.models.compliance import CompanyRequirementAssessment
from app.models.enums import AuditAction
from app.models.identity import User
from app.models.requirements import RequirementEvidence
from app.schemas.workspace import (
    DossierCoverageRead,
    DossierDocumentRead,
    DossierGroupRead,
    DossierItemRead,
    DossierRead,
    DossierSyncResult,
    EvidenceItemCreateRequest,
    EvidenceItemUpdateRequest,
    ReadinessRead,
    TaskRead,
)
from app.services import audit
from app.services import bid_workspace as workspace_service
from app.services import workspace_dossier as service
from app.workspace.decision import nothing_to_do_reason
from app.workspace.transitions import is_submitted

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["dossier"])


async def _selector_names(
    session: SessionDep, user_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    ids = {uid for uid in user_ids if uid}
    if not ids:
        return {}
    rows = (
        await session.execute(
            sa.select(User.id, User.full_name, User.email).where(User.id.in_(ids))
        )
    ).all()
    return {row[0]: row[1] or row[2] for row in rows}


async def _assessment_results(
    session: SessionDep, company_id: uuid.UUID, requirement_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Que en deia el compliment de cada requisit.

    Va a la mateixa fila que la cobertura documental precisament perque es vegi
    el cas que interessa: `satisfies` amb el document encara per preparar.
    """
    if not requirement_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(
                CompanyRequirementAssessment.requirement_id,
                sa.func.coalesce(
                    CompanyRequirementAssessment.reviewed_result,
                    CompanyRequirementAssessment.automatic_result,
                ),
            ).where(
                CompanyRequirementAssessment.company_id == company_id,
                CompanyRequirementAssessment.requirement_id.in_(requirement_ids),
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def _quotes(
    session: SessionDep, requirement_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """La cita del plec, per poder saltar a la font des de cada fila."""
    if not requirement_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(RequirementEvidence.requirement_id, RequirementEvidence.quote).where(
                RequirementEvidence.requirement_id.in_(requirement_ids)
            )
        )
    ).all()
    quotes: dict[uuid.UUID, str] = {}
    for requirement_id, quote in rows:
        quotes.setdefault(requirement_id, quote)
    return quotes


async def _dossier_read(
    session: SessionDep, ctx: CompanyCtx, workspace
) -> DossierRead:
    dossier = await service.load_dossier(session, workspace)
    # Autocuracio en llegir: si l'estat guardat deia "llest" i ja no ho es, es
    # corregeix abans de respondre. Es reaprofita la readiness ja calculada.
    await service.reconcile_readiness(session, workspace, readiness=dossier.readiness)
    requirement_ids = {row.item.requirement_id for row in dossier.rows if row.item.requirement_id}
    names = await _selector_names(session, {row.item.selected_by for row in dossier.rows})
    results = await _assessment_results(session, ctx.company_id, requirement_ids)
    quotes = await _quotes(session, requirement_ids)

    groups: list[DossierGroupRead] = []
    for group, rows in dossier.grouped():
        groups.append(
            DossierGroupRead(
                group=group,
                items=[
                    DossierItemRead(
                        id=row.item.id,
                        workspace_id=row.item.workspace_id,
                        requirement_id=row.item.requirement_id,
                        checklist_item_id=row.item.checklist_item_id,
                        requirement_title=(
                            row.checklist_item.title
                            if row.checklist_item
                            else (row.requirement.title if row.requirement else None)
                        ),
                        requirement_category=(
                            row.requirement.category if row.requirement else None
                        ),
                        requirement_quote=quotes.get(row.item.requirement_id),
                        assessment_result=results.get(row.item.requirement_id),
                        mandatory=row.mandatory,
                        label=row.item.label,
                        status=row.item.status,
                        source=row.item.source,
                        company_evidence_type=row.item.company_evidence_type,
                        company_evidence_id=row.item.company_evidence_id,
                        document=(
                            DossierDocumentRead.model_validate(
                                row.document, from_attributes=True
                            )
                            if row.document
                            else None
                        ),
                        validity=row.validity,
                        coverage=dossier.coverage_by_requirement.get(row.item.requirement_id),
                        selected_by=row.item.selected_by,
                        selected_by_name=names.get(row.item.selected_by),
                        selected_at=row.item.selected_at,
                        notes=row.item.notes,
                        created_at=row.item.created_at,
                        updated_at=row.item.updated_at,
                    )
                    for row in rows
                ],
            )
        )

    return DossierRead(
        workspace_id=workspace.id,
        workspace_status=workspace.status,
        deadline_at=dossier.deadline,
        coverage=DossierCoverageRead(
            mandatory_total=dossier.counters.mandatory_total,
            covered=dossier.counters.covered,
            missing_document=dossier.counters.missing_document,
            needs_review=dossier.counters.needs_review,
            not_applicable=dossier.counters.not_applicable,
            expired_documents=dossier.counters.expired_documents,
            optional_total=dossier.counters.optional_total,
            optional_covered=dossier.counters.optional_covered,
        ),
        readiness=_readiness_read(dossier.readiness, workspace),
        groups=groups,
    )


def _readiness_read(readiness, workspace) -> ReadinessRead:
    return ReadinessRead(
        state=readiness.state,
        blockers=list(readiness.blockers),
        # Explicit a la resposta: estar a punt per a la revisio final no vol dir
        # haver presentat res, i la interficie no ho ha de deduir.
        is_submitted=is_submitted(workspace.status),
    )


@router.get("/dossier", response_model=DossierRead, summary="Dossier documental")
async def get_dossier(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    return await _dossier_read(session, ctx, workspace)


@router.post(
    "/dossier/sync",
    response_model=DossierSyncResult,
    summary="Proposa documents a partir del compliment",
)
async def sync_dossier(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Proposa; no tria. Idempotent i sense tocar res del que ja s'hagi decidit."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    outcome = await service.sync_dossier(session, workspace, user_id=ctx.user.id)
    await service.start_bid_preparation(session, workspace)
    await session.commit()
    await session.refresh(workspace)
    run = await workspace_service.current_requirement_run(session, workspace.tender_id)
    return DossierSyncResult(
        suggested=outcome.suggested,
        gaps=outcome.gaps,
        total=outcome.total,
        reason=nothing_to_do_reason(
            created=outcome.suggested + outcome.gaps,
            requirements_available=run.requirements_extracted if run else 0,
        ),
        dossier=await _dossier_read(session, ctx, workspace),
    )


@router.post("/dossier", response_model=DossierRead, summary="Afegeix una evidencia")
async def add_item(
    workspace_id: uuid.UUID,
    payload: EvidenceItemCreateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await service.add_item(
        session,
        workspace,
        requirement_id=payload.requirement_id,
        document_id=payload.document_id,
        notes=payload.notes,
        user=ctx.user,
    )
    return await _dossier_read(session, ctx, workspace)


@router.patch(
    "/dossier/{item_id}", response_model=DossierRead, summary="Selecciona o substitueix"
)
async def update_item(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: EvidenceItemUpdateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Retorna el dossier sencer: canviar una peca canvia cobertura i readiness."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    item = await service.get_item(session, workspace, item_id)
    await service.update_item(
        session,
        workspace,
        item,
        status=payload.status,
        document_id=payload.document_id,
        clear_document=payload.clear_document,
        notes=payload.notes,
        user=ctx.user,
    )
    return await _dossier_read(session, ctx, workspace)


@router.post(
    "/dossier/{item_id}/task", response_model=TaskRead, summary="Crea la tasca del forat"
)
async def create_task(
    workspace_id: uuid.UUID, item_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    """Del forat a la feina: titol deterministic, sense IA, sense duplicats."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    item = await service.get_item(session, workspace, item_id)
    task = await service.create_task_for_gap(session, workspace, item, user=ctx.user)
    return TaskRead.model_validate(task)


@router.get("/readiness", response_model=ReadinessRead, summary="Estat del dossier")
async def get_readiness(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    readiness = await service.reconcile_readiness(session, workspace)
    return _readiness_read(readiness, workspace)


@router.post(
    "/readiness/advance",
    response_model=ReadinessRead,
    summary="Passa a revisio final",
)
async def advance(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """No es pot forcar: si falta alguna cosa, l'error diu exactament que.

    I arribar-hi NO es presentar. `submitted` segueix exigint una aprovacio final
    humana que encara no existeix.
    """
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    readiness = await service.advance_to_final_review(
        session, workspace, user=ctx.user, role=ctx.membership.role
    )
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        entity_type="bid_workspace",
        entity_id=workspace.id,
        summary="Dossier passat a revisio final",
    )
    await session.commit()
    return _readiness_read(readiness, workspace)
