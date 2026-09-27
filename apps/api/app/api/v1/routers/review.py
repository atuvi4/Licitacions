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

from fastapi import APIRouter, Query, status

from app.api.deps import CompanyCtx, SessionDep
from app.models.enums import AuditAction
from app.schemas.review import (
    CanonicalRelation,
    CanonicalRequirement,
    ManualRequirementCreate,
    RelationArchiveResult,
    RelationCreate,
    ReviewActionRequest,
    ReviewHistoryEntry,
    ReviewOverview,
    ReviewStartResult,
)
from app.services import audit
from app.services import review_actions as actions
from app.services import review_relations as relations
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
    """Confirmar, corregir, descartar, parcial, nota, canvi d'estat o afegir evidencia.

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
        user=ctx.user,
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


# --------------------------------------------------------------------------- #
# Relacions (S-R3)                                                              #
# --------------------------------------------------------------------------- #


async def _relation_read(session, company_id, relation_id, item_id) -> CanonicalRelation:
    """La relacio tal com es veu des de l'element `from` de la peticio."""
    found = await relations.relations_of(
        session, company_id=company_id, item_ids=[item_id], include_archived=True
    )
    return next(r for r in found.get(item_id, []) if r.relation_id == relation_id)


@router.post(
    "/relations",
    response_model=CanonicalRelation,
    status_code=status.HTTP_201_CREATED,
    summary="Relaciona dos elements de revisio",
)
async def create_review_relation(
    tender_id: uuid.UUID, payload: RelationCreate, session: SessionDep, ctx: CompanyCtx
):
    """`from <relacio> to`. Mateixa empresa, mateixa licitacio, elements actius.

    Un cicle de `parent_of`, `duplicate_of` o `supersedes` es 422; una relacio
    que ja existeix, 409. No crea cap revisio ni toca cap element.
    """
    actions.assert_can_review(ctx.membership.role)
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    relation = await relations.create_relation(
        session,
        company_id=ctx.company_id,
        tender_id=tender_id,
        from_item_id=payload.from_item_id,
        to_item_id=payload.to_item_id,
        relation_type=payload.relation_type,
        note=payload.note,
        actor_id=ctx.user.id,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="review_relation",
        entity_id=relation.id,
        summary=(
            f"licitacio={tender_id} relacio={relation.id} tipus={relation.relation_type.value} "
            f"de={relation.from_item_id} a={relation.to_item_id}"
        ),
    )
    await session.commit()
    return await _relation_read(session, ctx.company_id, relation.id, relation.from_item_id)


@router.post(
    "/relations/{relation_id}/archive",
    response_model=RelationArchiveResult,
    summary="Arxiva una relacio (sense esborrar-la)",
)
async def archive_review_relation(
    tender_id: uuid.UUID, relation_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    """Idempotent: arxivar-la de nou torna `changed=false` i no s'audita dues vegades."""
    actions.assert_can_review(ctx.membership.role)
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    relation, changed = await relations.archive_relation(
        session,
        company_id=ctx.company_id,
        tender_id=tender_id,
        relation_id=relation_id,
        actor_id=ctx.user.id,
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.ARCHIVE,
            entity_type="review_relation",
            entity_id=relation.id,
            summary=(
                f"licitacio={tender_id} relacio={relation.id} "
                f"tipus={relation.relation_type.value} arxivada"
            ),
        )
    await session.commit()
    return RelationArchiveResult(
        changed=changed,
        relation=await _relation_read(session, ctx.company_id, relation.id, relation.from_item_id),
    )


@router.get(
    "/items/{item_id}/relations",
    response_model=list[CanonicalRelation],
    summary="Relacions d'un element (sortints i entrants)",
)
async def review_item_relations(
    tender_id: uuid.UUID,
    item_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    include_archived: bool = Query(default=False),
):
    await service.load_visible_tender(session, ctx.company_id, tender_id)
    await service.get_requirement(
        session, company_id=ctx.company_id, tender_id=tender_id, item_id=item_id
    )
    found = await relations.relations_of(
        session, company_id=ctx.company_id, item_ids=[item_id], include_archived=include_archived
    )
    return found.get(item_id, [])
