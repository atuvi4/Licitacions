"""Administracio de la plataforma: nomes per a `users.is_superuser`.

Per ara, una sola operacio: donar d'alta una empresa client i el seu
propietari. No es un panell; es la peca minima per poder comencar amb clients
reals sense scripts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Query, Response, status

from app.api.deps import PlatformAdmin, SessionDep
from app.models.enums import CompanyPlatformStatus
from app.models.sources import Source
from app.schemas.platform import (
    CompanyOnboardingRead,
    CompanyOnboardingRequest,
    OnboardedCompanyRead,
    OnboardedOwnerRead,
    PlatformCompanyDetail,
    PlatformCompanyList,
    PlatformStatusChangeRequest,
    SourceHealthRead,
)
from app.services import platform_companies, platform_onboarding

router = APIRouter(prefix="/platform", tags=["platform"])


@router.post(
    "/companies",
    response_model=CompanyOnboardingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Dona d'alta una empresa i el seu propietari",
)
async def onboard_company(
    payload: CompanyOnboardingRequest, admin: PlatformAdmin, session: SessionDep, response: Response
) -> CompanyOnboardingRead:
    result = await platform_onboarding.onboard_company(
        session,
        admin=admin,
        legal_name=payload.legal_name,
        trade_name=payload.trade_name,
        tax_id=payload.tax_id,
        owner_email=payload.owner_email,
        owner_full_name=payload.owner_full_name,
        initial_status=CompanyPlatformStatus(payload.initial_status),
        pilot_ends_at=payload.pilot_ends_at,
    )
    # Porta una contrasenya temporal: que cap cache ni proxy la guardi.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return CompanyOnboardingRead(
        company=OnboardedCompanyRead(
            id=result.company.id,
            legal_name=result.company.legal_name,
            trade_name=result.company.trade_name,
            tax_id=result.company.tax_id,
            platform_status=result.company.platform_status,
            pilot_ends_at=result.company.pilot_ends_at,
        ),
        owner=OnboardedOwnerRead(
            id=result.owner.id, email=result.owner.email, full_name=result.owner.full_name
        ),
        temporary_password=result.temporary_password,
    )


@router.get(
    "/companies",
    response_model=PlatformCompanyList,
    summary="Empreses clients, amb recomptes per estat",
)
async def list_companies(
    _: PlatformAdmin,
    session: SessionDep,
    status_filter: Annotated[CompanyPlatformStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> dict:
    return await platform_companies.list_companies(session, status=status_filter, query=q)


@router.get(
    "/companies/{company_id}",
    response_model=PlatformCompanyDetail,
    summary="Fitxa d'una empresa client i vista de seguiment",
)
async def get_company(company_id: uuid.UUID, _: PlatformAdmin, session: SessionDep) -> dict:
    return await platform_companies.get_company(session, company_id)


@router.patch(
    "/companies/{company_id}/status",
    response_model=PlatformCompanyDetail,
    summary="Canvia l'estat del client (pilot, activa, pausada) o allarga el pilot",
)
async def change_company_status(
    company_id: uuid.UUID,
    payload: PlatformStatusChangeRequest,
    admin: PlatformAdmin,
    session: SessionDep,
) -> dict:
    return await platform_companies.change_status(
        session,
        admin=admin,
        company_id=company_id,
        status=payload.status,
        pilot_ends_at=payload.pilot_ends_at,
    )


@router.get(
    "/sources",
    response_model=list[SourceHealthRead],
    summary="Salut de la sincronitzacio de cada font",
)
async def list_sources(_: PlatformAdmin, session: SessionDep) -> list[SourceHealthRead]:
    now = datetime.now(UTC)
    rows = (await session.execute(sa.select(Source).order_by(Source.code))).scalars().all()
    result: list[SourceHealthRead] = []
    for source in rows:
        item = SourceHealthRead.model_validate(source, from_attributes=True)
        until = source.sync_lease_until
        if until is not None and until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        item.lease_active = until is not None and until > now
        result.append(item)
    return result
