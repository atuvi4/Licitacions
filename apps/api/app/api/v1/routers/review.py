"""Revisio canonica per empresa (S-R1, S-R2).

Tot penja de `/companies/{company_id}/tenders/{tender_id}/review`: l'empresa es
explicita a la URL i `get_company_context` hi comprova la pertinenca. Una
licitacio que l'empresa no veu, o un element d'una altra empresa, dona 404; un
rol que no pot revisar, 403; una revisio desfasada, 409.

Aquesta API **no** fa servir els endpoints LEGACY `POST /requirements/{id}/review`
ni `POST /award-criteria/{id}/review`, ni llegeix els camps de revisio que
aquells escriuen: modifiquen files d'extraccio compartides entre empreses.

L'auditoria de cada escriptura porta nomes identificadors i codis: mai cites,
instantanies ni text del plec.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CompanyCtx, SessionDep
from app.models.enums import AuditAction
from app.schemas.review import (
    CanonicalRequirement,
    ManualRequirementCreate,
    ReviewActionRequest,
    ReviewHistoryEntry,
    ReviewOverview,
    ReviewStartResult,
)
from app.services import audit
from app.services import review_actions as actions
from app.services import review_view as service

router = APIRouter(prefix="/tenders/{tender_id}/review", tags=["revisio"])


@router.post("/start", response_model=ReviewStartResult, summary="Inicia la revisio")
async def start_review(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Crea els elements de revisio dels requisits extrets vigents. Idempotent.

    Tornar-ho a cridar no crea res de nou. No toca cap fila d'extraccio.
    """
    actions.assert_can_review(ctx.membership.role)
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    result = await service.start_review(
        session, company_id=ctx.company_id, tender_id=tender_id, actor_id=ctx.user.id
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="review",
        entity_id=tender_id,
        summary=f"licitacio={tender_id} inici_revisio elements_creats={result.created}",
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


@router.post(
    "/requirements",
    response_model=CanonicalRequirement,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un requisit que l'extraccio no ha trobat",
)
async def create_review_requirement(
    tender_id: uuid.UUID,
    payload: ManualRequirementCreate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Un requisit manual NOMES d'aquesta empresa. No s'escriu a `requirements`."""
    actions.assert_can_review(ctx.membership.role)
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    item = await actions.create_manual_requirement(
        session, company_id=ctx.company_id, tender_id=tender_id, user=ctx.user, data=payload
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="review_item",
        entity_id=item.id,
        summary=(
            f"licitacio={tender_id} element={item.id} accio=create_manual "
            f"cites={len(payload.citations)}"
        ),
    )
    await session.commit()
    return await service.get_requirement(
        session, company_id=ctx.company_id, tender_id=tender_id, item_id=item.id
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


@router.post(
    "/items/{item_id}/actions",
    response_model=CanonicalRequirement,
    summary="Aplica una accio de revisio",
)
async def apply_review_action(
    tender_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ReviewActionRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Confirmar, corregir, descartar, parcial, nota o canvi d'estat.

    `expected_revision` ha de ser la revisio vigent; si no, 409 amb la vigent a
    `details.current_revision` perque el client torni a carregar.
    """
    actions.assert_can_review(ctx.membership.role)
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    item = await actions.apply_review_action(
        session,
        company_id=ctx.company_id,
        tender_id=tender_id,
        item_id=item_id,
        actor_id=ctx.user.id,
        expected_revision=payload.expected_revision,
        action=payload.action,
        payload=payload.payload,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="review_item",
        entity_id=item.id,
        summary=(
            f"licitacio={tender_id} element={item.id} accio={payload.action} "
            f"revisio={item.revision} estat={item.verification_status.value}"
        ),
    )
    await session.commit()
    return await service.get_requirement(
        session, company_id=ctx.company_id, tender_id=tender_id, item_id=item_id
    )


@router.get(
    "/items/{item_id}/history",
    response_model=list[ReviewHistoryEntry],
    summary="Historial de revisio d'un element",
)
async def review_item_history(
    tender_id: uuid.UUID, item_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    return await actions.history(
        session, company_id=ctx.company_id, tender_id=tender_id, item_id=item_id
    )
