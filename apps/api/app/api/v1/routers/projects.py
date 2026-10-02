"""Projectes executats (experiencia acreditable).

No fa servir la fabrica CRUD generica perque ha de gestionar la relacio N:M
amb serveis dins la mateixa transaccio.
"""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Query, Response, status

from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.crud import get_owned_or_404
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.knowledge import access
from app.models.enums import AuditAction, LinkedEntityType
from app.models.profile import CompanyService
from app.models.projects import (
    CompanyProject,
    CompanyProjectAnnualAmount,
    ProjectCPV,
    project_services,
)
from app.schemas.cpv import ProjectCPVCreate, ProjectCPVRead
from app.schemas.projects import ProjectCreate, ProjectRead, ProjectUpdate
from app.services import audit
from app.services.cpv import ensure_cpv_code
from app.services.entities import delete_links_for_entity
from app.services.knowledge_evidence import (
    require_evidence_if_verified,
    unverify_for_dependent_change,
)

router = APIRouter(prefix="/projects", tags=["experiencia"])


async def _resolve_services(
    session, company_id: uuid.UUID, service_ids: list[uuid.UUID]
) -> list[CompanyService]:
    """Comprova que tots els serveis son de la mateixa empresa."""
    if not service_ids:
        return []
    unique_ids = list(dict.fromkeys(service_ids))
    services = (
        await session.execute(
            sa.select(CompanyService).where(
                CompanyService.id.in_(unique_ids), CompanyService.company_id == company_id
            )
        )
    ).scalars().all()
    if len(services) != len(unique_ids):
        raise ValidationError("Algun dels serveis indicats no existeix en aquesta empresa.")
    return list(services)


def _check_annual_amounts(project: CompanyProject, amounts: list[dict]) -> None:
    """El desglossament ha de cabre al projecte: cap any fora de les dates i cap
    suma per sobre de l'import total (si es coneix)."""
    first = project.start_date.year if project.start_date else None
    last = project.end_date.year if project.end_date else None
    for item in amounts:
        if (first is not None and item["year"] < first) or (
            last is not None and item["year"] > last
        ):
            raise ValidationError(
                "Un import anual cau fora de les dates del projecte.",
                details={"year": item["year"]},
            )
    total = sum((Decimal(str(item["amount"])) for item in amounts), Decimal(0))
    if project.amount is not None and total > project.amount:
        raise ValidationError(
            "Els imports anuals sumen mes que l'import del projecte.",
            details={"annual_total": str(total), "amount": str(project.amount)},
        )


def _set_annual_amounts(project: CompanyProject, amounts: list[dict], user_id) -> bool:
    """Substitueix el desglossament ANY PER ANY (mai esborrar i tornar a inserir el mateix
    any: la unicitat projecte + any ho prohibeix). Cert si ha canviat."""
    wanted = {item["year"]: Decimal(str(item["amount"])) for item in amounts}
    existing = {row.year: row for row in project.annual_amounts}
    changed = False
    for year, row in existing.items():
        if year not in wanted:
            project.annual_amounts.remove(row)
            changed = True
        elif Decimal(row.amount) != wanted[year]:
            row.amount = wanted[year]
            row.updated_by = user_id
            changed = True
    for year in sorted(set(wanted) - set(existing)):
        project.annual_amounts.append(
            CompanyProjectAnnualAmount(
                company_id=project.company_id,
                year=year,
                amount=wanted[year],
                created_by=user_id,
                updated_by=user_id,
            )
        )
        changed = True
    return changed


async def _load_project(session, project_id: uuid.UUID, company_id: uuid.UUID) -> CompanyProject:
    return await get_owned_or_404(session, CompanyProject, project_id, company_id)


@router.get("", response_model=list[ProjectRead], summary="Llista projectes")
async def list_projects(
    session: SessionDep,
    ctx: CompanyCtx,
    q: str | None = Query(default=None, max_length=120, description="Cerca per nom o client"),
    min_amount: Decimal | None = Query(default=None, ge=0),
    ended_after: date | None = Query(default=None),
    service_id: uuid.UUID | None = Query(default=None),
):
    stmt = sa.select(CompanyProject).where(CompanyProject.company_id == ctx.company_id)
    if q:
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(
            sa.or_(
                sa.func.lower(CompanyProject.name).like(pattern),
                sa.func.lower(sa.func.coalesce(CompanyProject.client_name, "")).like(pattern),
            )
        )
    if min_amount is not None:
        stmt = stmt.where(CompanyProject.amount >= min_amount)
    if ended_after is not None:
        stmt = stmt.where(CompanyProject.end_date >= ended_after)
    if service_id is not None:
        stmt = stmt.where(
            CompanyProject.id.in_(
                sa.select(project_services.c.project_id).where(
                    project_services.c.service_id == service_id
                )
            )
        )
    stmt = stmt.order_by(
        CompanyProject.end_date.desc().nullslast(), CompanyProject.created_at.desc()
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


@router.post(
    "", response_model=ProjectRead, status_code=status.HTTP_201_CREATED, summary="Crea projecte"
)
async def create_project(payload: ProjectCreate, session: SessionDep, ctx: CompanyCtx):
    # Coneixement actual (0052): qui nomes consulta no el modifica; verificat nomes
    # amb evidencia enllacada (una alta no en pot tenir).
    access.assert_can_edit(ctx.membership.role)
    data = payload.model_dump(exclude_unset=True)
    service_ids = data.pop("service_ids", None) or []
    annual = data.pop("annual_amounts", None) or []
    services = await _resolve_services(session, ctx.company_id, service_ids)

    project = CompanyProject(
        company_id=ctx.company_id, created_by=ctx.user_id, updated_by=ctx.user_id, **data
    )
    project.services = services
    _check_annual_amounts(project, annual)
    _set_annual_amounts(project, annual, ctx.user_id)
    session.add(project)
    await require_evidence_if_verified(session, project, ctx.company_id)
    await session.commit()
    await session.refresh(project)
    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="company_projects",
        entity_id=project.id,
    )
    await session.commit()
    return project


@router.get("/{project_id}", response_model=ProjectRead, summary="Obte projecte")
async def get_project(project_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    return await _load_project(session, project_id, ctx.company_id)


@router.patch("/{project_id}", response_model=ProjectRead, summary="Actualitza projecte")
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, session: SessionDep, ctx: CompanyCtx
):
    access.assert_can_edit(ctx.membership.role)
    project = await _load_project(session, project_id, ctx.company_id)
    data = payload.model_dump(exclude_unset=True)
    service_ids = data.pop("service_ids", None)
    annual = data.pop("annual_amounts", None)

    for field, value in data.items():
        setattr(project, field, value)
    project.updated_by = ctx.user_id
    # Abans de cap consulta: l'historial dels camps es el que diu que ha canviat.
    await require_evidence_if_verified(session, project, ctx.company_id)

    if service_ids is not None:
        services = await _resolve_services(session, ctx.company_id, service_ids)
        if {s.id for s in services} != {s.id for s in project.services}:
            # Les linies de servei son part del que es va verificar.
            unverify_for_dependent_change(project)
        project.services = services
    if annual is not None:
        _check_annual_amounts(project, annual)
        if _set_annual_amounts(project, annual, ctx.user_id):
            # El desglossament es part del que es va verificar.
            unverify_for_dependent_change(project)
    elif data.keys() & {"start_date", "end_date", "amount"}:
        _check_annual_amounts(
            project, [{"year": a.year, "amount": a.amount} for a in project.annual_amounts]
        )

    await session.commit()
    await session.refresh(project)
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="company_projects",
        entity_id=project.id,
    )
    await session.commit()
    return project


@router.delete(
    "/{project_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Esborra projecte"
)
async def delete_project(
    project_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    access.assert_can_delete(ctx.membership.role)
    project = await _load_project(session, project_id, ctx.company_id)
    await delete_links_for_entity(session, LinkedEntityType.COMPANY_PROJECT, project.id)
    await session.delete(project)
    await audit.record(
        session,
        action=AuditAction.DELETE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="company_projects",
        entity_id=project_id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Alternativa granular a enviar `service_ids` sencer al PATCH.
@router.put(
    "/{project_id}/services/{service_id}",
    response_model=ProjectRead,
    summary="Vincula un servei al projecte",
)
async def attach_service(
    project_id: uuid.UUID, service_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    project = await _load_project(session, project_id, ctx.company_id)
    services = await _resolve_services(session, ctx.company_id, [service_id])
    if services[0].id not in {service.id for service in project.services}:
        project.services.append(services[0])
        await session.commit()
        await session.refresh(project)
    return project


@router.delete(
    "/{project_id}/services/{service_id}",
    response_model=ProjectRead,
    summary="Desvincula un servei del projecte",
)
async def detach_service(
    project_id: uuid.UUID, service_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    project = await _load_project(session, project_id, ctx.company_id)
    project.services = [service for service in project.services if service.id != service_id]
    await session.commit()
    await session.refresh(project)
    return project


# --------------------------------------------------------------------------
# CPV del projecte
# --------------------------------------------------------------------------
# Amb quins CPV es va executar el projecte. Es la peça que, en el futur,
# permetra dir "aquesta empresa ja ha executat contractes d'aquest CPV".


@router.get(
    "/{project_id}/cpvs", response_model=list[ProjectCPVRead], summary="CPV del projecte"
)
async def list_project_cpvs(project_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    project = await _load_project(session, project_id, ctx.company_id)
    return list(project.cpvs)


@router.post(
    "/{project_id}/cpvs",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un CPV al projecte",
)
async def add_project_cpv(
    project_id: uuid.UUID, payload: ProjectCPVCreate, session: SessionDep, ctx: CompanyCtx
):
    project = await _load_project(session, project_id, ctx.company_id)
    await ensure_cpv_code(session, payload.cpv_code, payload.description)

    if payload.cpv_code in {item.cpv_code for item in project.cpvs}:
        raise ConflictError("Aquest CPV ja esta associat al projecte.")

    session.add(ProjectCPV(project_id=project.id, cpv_code=payload.cpv_code))
    await session.commit()
    await session.refresh(project)
    return project


@router.delete(
    "/{project_id}/cpvs/{cpv_code}",
    response_model=ProjectRead,
    summary="Treu un CPV del projecte",
)
async def remove_project_cpv(
    project_id: uuid.UUID, cpv_code: str, session: SessionDep, ctx: CompanyCtx
):
    project = await _load_project(session, project_id, ctx.company_id)
    removed = await session.execute(
        sa.delete(ProjectCPV).where(
            ProjectCPV.project_id == project.id, ProjectCPV.cpv_code == cpv_code
        )
    )
    if removed.rowcount == 0:
        raise NotFoundError("Aquest CPV no esta associat al projecte.")
    await session.commit()
    await session.refresh(project)
    return project
