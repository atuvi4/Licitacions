"""Administracio de la plataforma: nomes per a `users.is_superuser`.

Per ara, una sola operacio: donar d'alta una empresa client i el seu
propietari. No es un panell; es la peca minima per poder comencar amb clients
reals sense scripts.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import PlatformAdmin, SessionDep
from app.schemas.platform import (
    CompanyOnboardingRead,
    CompanyOnboardingRequest,
    OnboardedCompanyRead,
    OnboardedOwnerRead,
)
from app.services import platform_onboarding

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
        ),
        owner=OnboardedOwnerRead(
            id=result.owner.id, email=result.owner.email, full_name=result.owner.full_name
        ),
        temporary_password=result.temporary_password,
    )
