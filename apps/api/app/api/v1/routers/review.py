"""Revisio canonica per empresa (S-R1): nomes lectura i materialitzacio.

Tot penja de `/companies/{company_id}/tenders/{tender_id}/review`: l'empresa es
explicita a la URL i `get_company_context` hi comprova la pertinenca. Una
licitacio que l'empresa no veu, o un element d'una altra empresa, dona 404.

Aquesta API **no** fa servir els endpoints LEGACY `POST /requirements/{id}/review`
ni `POST /award-criteria/{id}/review`, ni llegeix els camps de revisio que
aquells escriuen: modifiquen files d'extraccio compartides entre empreses.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CompanyCtx, SessionDep
from app.models.enums import AuditAction
from app.schemas.review import CanonicalRequirement, ReviewOverview, ReviewStartResult
from app.services import audit, manual_requirement
from app.services import review_view as service

router = APIRouter(prefix="/tenders/{tender_id}/review", tags=["revisio"])


@router.post("/start", response_model=ReviewStartResult, summary="Inicia la revisio")
async def start_review(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Crea els elements de revisio dels requisits extrets vigents. Idempotent.

    Tornar-ho a cridar no crea res de nou. No toca cap fila d'extraccio.
    """
    manual_requirement.assert_can_edit(ctx.membership.role)
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    result = await service.start_review(
        session, company_id=ctx.company_id, tender_id=tender_id, actor_id=ctx.user.id
    )
    if result.created:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.CREATE,
            entity_type="review",
            entity_id=tender_id,
            summary=f"licitacio={tender_id} elements_creats={result.created}",
        )
    await session.commit()
    return result


@router.get("/overview", response_model=ReviewOverview, summary="Resum de la revisio")
async def review_overview(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    return await service.overview(session, company_id=ctx.company_id, tender_id=tender_id)


@router.get(
    "/requirements",
    response_model=list[CanonicalRequirement],
    summary="Requisits canonics d'aquesta empresa",
)
async def list_review_requirements(
    tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    return await service.list_requirements(
        session, company_id=ctx.company_id, tender_id=tender_id
    )


@router.get(
    "/requirements/{item_id}",
    response_model=CanonicalRequirement,
    summary="Un requisit canonic",
)
async def get_review_requirement(
    tender_id: uuid.UUID, item_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    return await service.get_requirement(
        session, company_id=ctx.company_id, tender_id=tender_id, item_id=item_id
    )
