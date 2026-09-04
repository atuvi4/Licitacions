"""Cataleg CPV i CPV de l'empresa.

El cataleg (`cpv_codes`) es global i compartit; encara no s'importa el cataleg
europeu complet, els codis es donen d'alta a mesura que s'utilitzen. Els CPV
d'una empresa, en canvi, son sempre del seu tenant.
"""

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Query, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import CompanyCtx, CurrentUser, SessionDep
from app.api.v1.crud import apply_changes
from app.core.errors import ConflictError, NotFoundError
from app.models.cpv import CompanyCPVPreference, CPVCode
from app.models.enums import AuditAction
from app.schemas.cpv import (
    CPVCodeCreate,
    CPVCodeRead,
    CPVPreferenceCreate,
    CPVPreferenceRead,
    CPVPreferenceUpdate,
)
from app.services import audit
from app.services.cpv import ensure_cpv_code

catalog_router = APIRouter(prefix="/cpv-codes", tags=["cpv"])
preferences_router = APIRouter(prefix="/cpv-preferences", tags=["cpv"])


@catalog_router.get("", response_model=list[CPVCodeRead], summary="Cerca codis CPV")
async def search_cpv_codes(
    session: SessionDep,
    _: CurrentUser,
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = sa.select(CPVCode).where(CPVCode.is_active.is_(True))
    if q:
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(
            sa.or_(
                sa.func.lower(CPVCode.code).like(pattern),
                sa.func.lower(CPVCode.description).like(pattern),
            )
        )
    rows = (await session.execute(stmt.order_by(CPVCode.code).limit(limit))).scalars().all()
    return list(rows)


@catalog_router.post(
    "", response_model=CPVCodeRead, status_code=status.HTTP_201_CREATED, summary="Crea un codi CPV"
)
async def create_cpv_code(payload: CPVCodeCreate, session: SessionDep, _: CurrentUser):
    code = CPVCode(**payload.model_dump())
    session.add(code)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Aquest codi CPV ja existeix.") from exc
    await session.refresh(code)
    return code


def _to_read(preference: CompanyCPVPreference, description: str | None) -> CPVPreferenceRead:
    return CPVPreferenceRead(
        id=preference.id,
        company_id=preference.company_id,
        cpv_code=preference.cpv_code,
        description=description,
        priority=preference.priority,
        active=preference.active,
        excluded=preference.excluded,
        notes=preference.notes,
    )


async def _description_for(session, code: str) -> str | None:
    return (
        await session.execute(sa.select(CPVCode.description).where(CPVCode.code == code))
    ).scalar_one_or_none()


@preferences_router.get("", response_model=list[CPVPreferenceRead], summary="CPV de l'empresa")
async def list_preferences(session: SessionDep, ctx: CompanyCtx):
    rows = (
        await session.execute(
            sa.select(CompanyCPVPreference, CPVCode.description)
            .outerjoin(CPVCode, CPVCode.code == CompanyCPVPreference.cpv_code)
            .where(CompanyCPVPreference.company_id == ctx.company_id)
            .order_by(
                CompanyCPVPreference.excluded,
                CompanyCPVPreference.priority.desc(),
                CompanyCPVPreference.cpv_code,
            )
        )
    ).all()
    return [_to_read(preference, description) for preference, description in rows]


@preferences_router.post(
    "",
    response_model=CPVPreferenceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un CPV a l'empresa",
)
async def create_preference(payload: CPVPreferenceCreate, session: SessionDep, ctx: CompanyCtx):
    data = payload.model_dump()
    description = data.pop("description", None)
    code = await ensure_cpv_code(session, data["cpv_code"], description)

    preference = CompanyCPVPreference(company_id=ctx.company_id, **data)
    session.add(preference)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Aquest CPV ja esta registrat a l'empresa.") from exc

    await session.refresh(preference)
    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="company_cpv_preferences",
        entity_id=preference.id,
    )
    await session.commit()
    return _to_read(preference, code.description)


async def _load_preference(session, preference_id: uuid.UUID, company_id: uuid.UUID):
    preference = (
        await session.execute(
            sa.select(CompanyCPVPreference).where(
                CompanyCPVPreference.id == preference_id,
                CompanyCPVPreference.company_id == company_id,
            )
        )
    ).scalar_one_or_none()
    if preference is None:
        raise NotFoundError("CPV no trobat.")
    return preference


@preferences_router.patch(
    "/{preference_id}", response_model=CPVPreferenceRead, summary="Actualitza un CPV de l'empresa"
)
async def update_preference(
    preference_id: uuid.UUID,
    payload: CPVPreferenceUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    preference = await _load_preference(session, preference_id, ctx.company_id)
    apply_changes(preference, payload, ctx.user_id)
    await session.commit()
    await session.refresh(preference)
    return _to_read(preference, await _description_for(session, preference.cpv_code))


@preferences_router.delete(
    "/{preference_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Elimina un CPV"
)
async def delete_preference(
    preference_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    preference = await _load_preference(session, preference_id, ctx.company_id)
    await session.delete(preference)
    await audit.record(
        session,
        action=AuditAction.DELETE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="company_cpv_preferences",
        entity_id=preference_id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
