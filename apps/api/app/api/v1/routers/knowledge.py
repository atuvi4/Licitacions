"""Coneixement de l'empresa (fase 2 del motor d'analisi).

Tot penja de `/companies/{company_id}/knowledge`: l'empresa es explicita a la URL
i `get_company_context` hi comprova la pertinenca. Una observacio o una dada
d'una altra empresa dona 404.

* Llegir la fotografia i les observacions: qualsevol membre.
* Promoure o descartar una observacio: qui decideix (propietari, administracio,
  responsable de licitacions).
* Verificar una dada actual (hi ha evidencia): propietari o administracio.

La declaracio de certificacions i serveis passa pels CRUD que ja existien, ara
amb norma, nivell, linia de servei i procedencia. L'auditoria porta
identificadors i codis, mai el contingut.
"""

from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, SessionDep
from app.core.errors import NotFoundError
from app.models.enums import AuditAction, MembershipRole, ObservationStatus
from app.models.knowledge import KnowledgeObservation
from app.schemas.knowledge import (
    DismissObservation,
    KnowledgeSnapshotRead,
    ObservationRead,
    PromoteObservation,
    VerifiedRecord,
    snapshot_read,
)
from app.services import audit
from app.services import company_knowledge as service

router = APIRouter(prefix="/knowledge", tags=["coneixement de l'empresa"])

DECIDE_ROLES = frozenset({MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.BID_MANAGER})
VERIFY_ROLES = frozenset({MembershipRole.OWNER, MembershipRole.ADMIN})


def _require(role: MembershipRole, allowed: frozenset[MembershipRole]) -> None:
    if role not in allowed:
        # Com a la resta de l'API: dir "no pots" ja seria dir que existeix.
        raise NotFoundError("No es pot fer aquesta accio amb aquest rol.")


@router.get("/snapshot", response_model=KnowledgeSnapshotRead, summary="Fotografia efectiva")
async def get_snapshot(session: SessionDep, ctx: CompanyCtx, on: date | None = Query(default=None)):
    """El que l'empresa es i pot fer a la data `on` (avui, si no es diu), i que
    falta validar."""
    return snapshot_read(await service.company_snapshot(session, ctx.company_id, on=on))


@router.get("/observations", response_model=list[ObservationRead], summary="Observacions")
async def list_observations(
    session: SessionDep, ctx: CompanyCtx, status: ObservationStatus | None = Query(default=None)
):
    query = sa.select(KnowledgeObservation).where(KnowledgeObservation.company_id == ctx.company_id)
    if status is not None:
        query = query.where(KnowledgeObservation.status == status)
    rows = await session.scalars(
        query.order_by(
            KnowledgeObservation.subject,
            KnowledgeObservation.subject_key,
            KnowledgeObservation.id,
        )
    )
    return list(rows)


@router.post(
    "/observations/{observation_id}/promote",
    response_model=ObservationRead,
    summary="Promou una observacio a coneixement actual",
)
async def promote(
    observation_id: uuid.UUID, body: PromoteObservation, session: SessionDep, ctx: CompanyCtx
):
    """Una persona valida el suggeriment. En neix una dada actual DECLARADA (no
    verificada): validar el que deia l'historic no es aportar-ne evidencia."""
    _require(ctx.membership.role, DECIDE_ROLES)
    observation = await service.promote_observation(
        session,
        company_id=ctx.company_id,
        observation_id=observation_id,
        user=ctx.user,
        note=body.note,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="knowledge_observation",
        entity_id=observation.id,
        summary=f"promoguda subjecte={observation.subject} "
        f"entitat={observation.promoted_entity_id}",
    )
    await session.commit()
    return observation


@router.post(
    "/observations/{observation_id}/dismiss",
    response_model=ObservationRead,
    summary="Descarta una observacio",
)
async def dismiss(
    observation_id: uuid.UUID, body: DismissObservation, session: SessionDep, ctx: CompanyCtx
):
    _require(ctx.membership.role, DECIDE_ROLES)
    observation = await service.dismiss_observation(
        session,
        company_id=ctx.company_id,
        observation_id=observation_id,
        user=ctx.user,
        note=body.note,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="knowledge_observation",
        entity_id=observation.id,
        summary=f"descartada subjecte={observation.subject}",
    )
    await session.commit()
    return observation


@router.post(
    "/records/{kind}/{record_id}/verify",
    response_model=VerifiedRecord,
    summary="Marca una dada actual com a verificada",
)
async def verify(kind: str, record_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    _require(ctx.membership.role, VERIFY_ROLES)
    record = await service.verify_record(
        session, company_id=ctx.company_id, kind=kind, record_id=record_id
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type=f"knowledge_{kind}",
        entity_id=record.id,
        summary="verificada",
    )
    await session.commit()
    return VerifiedRecord(
        kind=kind, record_id=record.id, verification_status=str(record.verification_status)
    )
