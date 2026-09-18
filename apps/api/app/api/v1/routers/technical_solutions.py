"""Solucio tecnica d'una candidatura: HTTP.

Penja del workspace, que penja de `/companies/{company_id}`: la pertinenca es
comprova a cada crida i les files es filtren sempre per empresa i candidatura.

**No hi ha `/sync`, i no n'hi haura** (C-11). Res d'aqui no dedueix quins
requisits necessiten solucio tecnica: hi ha dos camins de creacio i tots dos els
inicia una persona. `POST ""` escriu una fila manual; `POST "/from-requirement"`
es l'unic cami que lliga una solucio a un requisit. El `source` no viatja mai al
cos de la peticio.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CompanyCtx, SessionDep
from app.models.enums import AuditAction
from app.models.workspace import WorkspaceTechnicalSolution
from app.schemas.workspace import (
    TechnicalSolutionCreateRequest,
    TechnicalSolutionCreateResult,
    TechnicalSolutionFromRequirementRequest,
    TechnicalSolutionRead,
    TechnicalSolutionUpdateRequest,
    TechnicalSolutionValidationRequest,
)
from app.services import audit
from app.services import bid_workspace as workspace_service
from app.services import technical_solution as service

router = APIRouter(
    prefix="/workspaces/{workspace_id}/technical-solutions", tags=["technical-solutions"]
)


async def _read(session: SessionDep, solution: WorkspaceTechnicalSolution):
    """Una fila, amb la vigencia de la validacio resolta."""
    reasons = (await service.stale_reasons_for(session, [solution]))[solution.id]
    return TechnicalSolutionRead.model_validate(solution).model_copy(
        update={"validation_stale": bool(reasons), "validation_stale_reasons": reasons}
    )


@router.get("", response_model=list[TechnicalSolutionRead], summary="Solucions tecniques")
async def list_solutions(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Lectura per a qualsevol membre, `viewer` inclos."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    solutions = await service.list_solutions(session, workspace)
    reasons = await service.stale_reasons_for(session, solutions)
    return [
        TechnicalSolutionRead.model_validate(solution).model_copy(
            update={
                "validation_stale": bool(reasons[solution.id]),
                "validation_stale_reasons": reasons[solution.id],
            }
        )
        for solution in solutions
    ]


@router.post(
    "",
    response_model=TechnicalSolutionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Escriu una solucio tecnica lliure",
)
async def create_solution(
    workspace_id: uuid.UUID,
    payload: TechnicalSolutionCreateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Manual i prou: aquest cami no pot lligar la fila a cap requisit."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    service.assert_can_edit(ctx.membership.role)
    solution = await service.create_manual(
        session,
        workspace,
        title=payload.title,
        proposed_solution=payload.proposed_solution,
        supplier_name=payload.supplier_name,
        product_name=payload.product_name,
        owner_id=payload.owner_id,
        notes=payload.notes,
        status=payload.status,
        user=ctx.user,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="workspace_technical_solution",
        entity_id=solution.id,
        summary=service.audit_summary(solution),
    )
    await session.commit()
    return await _read(session, solution)


@router.post(
    "/from-requirement",
    response_model=TechnicalSolutionCreateResult,
    summary="Defineix una solucio tecnica per a un requisit",
)
async def create_from_requirement(
    workspace_id: uuid.UUID,
    payload: TechnicalSolutionFromRequirementRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """L'unic cami que escriu `source = requirement`.

    Idempotent: assenyalar dues vegades el mateix requisit obre la fila que ja hi
    havia (`created=False`), com fa `create_workspace`.
    """
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    service.assert_can_edit(ctx.membership.role)
    solution, created = await service.create_from_requirement(
        session, workspace, requirement_id=payload.requirement_id, user=ctx.user
    )
    if created:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.CREATE,
            entity_type="workspace_technical_solution",
            entity_id=solution.id,
            summary=service.audit_summary(solution),
        )
        await session.commit()
    return TechnicalSolutionCreateResult(
        created=created, solution=await _read(session, solution)
    )


@router.patch(
    "/{solution_id}",
    response_model=TechnicalSolutionRead,
    summary="Edita una solucio tecnica",
)
async def update_solution(
    workspace_id: uuid.UUID,
    solution_id: uuid.UUID,
    payload: TechnicalSolutionUpdateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Nomes camps de treball. `validated` no s'hi pot escriure mai."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    solution = await service.get_solution(session, workspace, solution_id)
    service.assert_can_edit(ctx.membership.role)
    fields = payload.model_dump(exclude_unset=True)
    solution = await service.update_solution(
        session,
        workspace,
        solution,
        title=fields.get("title"),
        proposed_solution=fields.get("proposed_solution", service.UNSET),
        supplier_name=fields.get("supplier_name", service.UNSET),
        product_name=fields.get("product_name", service.UNSET),
        owner_id=fields.get("owner_id", service.UNSET),
        notes=fields.get("notes", service.UNSET),
        status=fields.get("status"),
        user=ctx.user,
    )
    read = await _read(session, solution)
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="workspace_technical_solution",
        entity_id=solution.id,
        summary=service.audit_summary(solution, stale=read.validation_stale_reasons),
    )
    await session.commit()
    return read


@router.post(
    "/{solution_id}/validation",
    response_model=TechnicalSolutionRead,
    summary="Valida una solucio tecnica",
)
async def validate_solution(
    workspace_id: uuid.UUID,
    solution_id: uuid.UUID,
    payload: TechnicalSolutionValidationRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """La porta humana d'aquesta capa: qui signa, quan i per que.

    Cap regla automatica i cap model hi arriba. Una solucio caducada pot tornar a
    passar per aqui: es reescriu la validacio vigent i l'historic transversal
    queda a l'auditoria.
    """
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    solution = await service.get_solution(session, workspace, solution_id)
    service.assert_can_validate(ctx.membership.role)
    solution = await service.validate_solution(
        session, workspace, solution, note=payload.note, user=ctx.user
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="technical_solution_validation",
        entity_id=solution.id,
        summary=service.audit_summary(solution),
    )
    await session.commit()
    return await _read(session, solution)
