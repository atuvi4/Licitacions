"""Els limits d'una polissa: total, per sinistre, per victima, agregat anual.

Va a part del CRUD generic perque aixo no es un recurs de l'empresa sino un
recurs **d'una polissa concreta**, i aixo canvia la comprovacio d'acces: no
n'hi ha prou de filtrar per `company_id` del limit, cal que la polissa pare
tambe sigui de l'empresa que demana. Sense aquest pas, un identificador escrit
al cos de la peticio deixaria escriure limits a la polissa d'un altre tenant.

Per aixo totes les operacions passen primer per `_owned_insurance`, que es
l'unic lloc on es resol la polissa. Es una sola porta a proposit: amb dues,
tard o d'hora una es queda sense el filtre.

El motiu de fons per tenir aixo: sense escriptura, la taula de limits existeix
pero cap empresa pot omplir-la, i el compliment respondria per sempre "no consta
el sublimit" davant d'un plec que l'exigeix. Correcte, pero inutil.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.crud import apply_changes, get_owned_or_404
from app.core.errors import ConflictError, NotFoundError
from app.knowledge import access
from app.models.credentials import CompanyInsurance, CompanyInsuranceLimit
from app.models.enums import AuditAction
from app.schemas.credentials import (
    InsuranceLimitCreate,
    InsuranceLimitRead,
    InsuranceLimitUpdate,
)
from app.services import audit
from app.services.knowledge_evidence import unverify_for_dependent_change

router = APIRouter(prefix="/insurances/{insurance_id}/limits", tags=["assegurances"])


async def _owned_insurance(
    session: SessionDep, insurance_id: uuid.UUID, company_id: uuid.UUID
) -> CompanyInsurance:
    """La polissa, nomes si es d'aquesta empresa. L'unica porta d'entrada."""
    return await get_owned_or_404(session, CompanyInsurance, insurance_id, company_id)


async def _owned_limit(
    session: SessionDep, insurance: CompanyInsurance, limit_id: uuid.UUID
) -> CompanyInsuranceLimit:
    limit = await session.scalar(
        sa.select(CompanyInsuranceLimit).where(
            CompanyInsuranceLimit.id == limit_id,
            CompanyInsuranceLimit.insurance_id == insurance.id,
            CompanyInsuranceLimit.company_id == insurance.company_id,
        )
    )
    if limit is None:
        raise NotFoundError("Limit no trobat.")
    return limit


@router.get("", response_model=list[InsuranceLimitRead], summary="Llista els limits")
async def list_limits(insurance_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    insurance = await _owned_insurance(session, insurance_id, ctx.company_id)
    rows = (
        await session.execute(
            sa.select(CompanyInsuranceLimit)
            .where(CompanyInsuranceLimit.insurance_id == insurance.id)
            .order_by(CompanyInsuranceLimit.kind)
        )
    ).scalars().all()
    return list(rows)


@router.post(
    "",
    response_model=InsuranceLimitRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un limit",
)
async def create_limit(
    insurance_id: uuid.UUID,
    payload: InsuranceLimitCreate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    access.assert_can_edit(ctx.membership.role)
    insurance = await _owned_insurance(session, insurance_id, ctx.company_id)
    # Un limit nou canvia el que la polissa afirma: la verificacio ja no val (0052).
    unverify_for_dependent_change(insurance)
    limit = CompanyInsuranceLimit(
        company_id=ctx.company_id,
        insurance_id=insurance.id,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        **payload.model_dump(exclude_unset=True),
    )
    session.add(limit)
    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover - depen del dialecte
        await session.rollback()
        raise ConflictError("Aquesta polissa ja te un limit d'aquest tipus.") from exc
    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type=CompanyInsuranceLimit.__tablename__,
        entity_id=limit.id,
    )
    await session.commit()
    await session.refresh(limit)
    return limit


@router.patch(
    "/{limit_id}", response_model=InsuranceLimitRead, summary="Actualitza un limit"
)
async def update_limit(
    insurance_id: uuid.UUID,
    limit_id: uuid.UUID,
    payload: InsuranceLimitUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    access.assert_can_edit(ctx.membership.role)
    insurance = await _owned_insurance(session, insurance_id, ctx.company_id)
    limit = await _owned_limit(session, insurance, limit_id)
    unverify_for_dependent_change(insurance)
    apply_changes(limit, payload, ctx.user_id)
    try:
        await session.flush()
    except IntegrityError as exc:  # pragma: no cover - depen del dialecte
        await session.rollback()
        raise ConflictError("Aquesta polissa ja te un limit d'aquest tipus.") from exc
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type=CompanyInsuranceLimit.__tablename__,
        entity_id=limit.id,
    )
    await session.commit()
    await session.refresh(limit)
    return limit


@router.delete(
    "/{limit_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Esborra un limit"
)
async def delete_limit(
    insurance_id: uuid.UUID, limit_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    access.assert_can_edit(ctx.membership.role)
    insurance = await _owned_insurance(session, insurance_id, ctx.company_id)
    limit = await _owned_limit(session, insurance, limit_id)
    unverify_for_dependent_change(insurance)
    await session.delete(limit)
    await audit.record(
        session,
        action=AuditAction.DELETE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type=CompanyInsuranceLimit.__tablename__,
        entity_id=limit_id,
    )
    await session.commit()
