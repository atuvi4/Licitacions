"""Empreses, preferencies contractuals i resum de dashboard."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Query

from app.api.deps import CompanyCtx, CurrentUser, SessionDep
from app.api.v1.crud import apply_changes
from app.models.enums import AuditAction
from app.models.identity import Company, CompanyMembership
from app.models.profile import CompanyContractPreferences
from app.schemas.company import (
    CompanyRead,
    CompanyUpdate,
    ContractPreferencesRead,
    ContractPreferencesUpdate,
)
from app.schemas.dashboard import DashboardSummary
from app.services import audit
from app.services.dashboard import build_summary

router = APIRouter(tags=["companies"])


@router.get("/companies", response_model=list[CompanyRead], summary="Empreses de l'usuari")
async def list_my_companies(session: SessionDep, user: CurrentUser) -> list[Company]:
    # L'Administracio Tuvi no es usuaria de cap empresa.
    if user.is_superuser:
        return []
    rows = (
        await session.execute(
            sa.select(Company)
            .join(CompanyMembership, CompanyMembership.company_id == Company.id)
            .where(CompanyMembership.user_id == user.id)
            .order_by(Company.legal_name)
        )
    ).scalars().all()
    return list(rows)


@router.get("/companies/{company_id}", response_model=CompanyRead, summary="Dades de l'empresa")
async def get_company(ctx: CompanyCtx) -> Company:
    return ctx.company


@router.patch(
    "/companies/{company_id}", response_model=CompanyRead, summary="Actualitza dades de l'empresa"
)
async def update_company(
    payload: CompanyUpdate, session: SessionDep, ctx: CompanyCtx
) -> Company:
    apply_changes(ctx.company, payload, ctx.user_id)
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="companies",
        entity_id=ctx.company_id,
    )
    await session.commit()
    await session.refresh(ctx.company)
    return ctx.company


@router.get(
    "/companies/{company_id}/summary",
    response_model=DashboardSummary,
    summary="Resum per al dashboard",
)
async def company_summary(
    session: SessionDep,
    ctx: CompanyCtx,
    alert_window_days: int = Query(default=90, ge=1, le=730),
) -> DashboardSummary:
    return await build_summary(session, ctx.company, alert_window_days=alert_window_days)


async def _find_preferences(session, company_id) -> CompanyContractPreferences | None:
    return (
        await session.execute(
            sa.select(CompanyContractPreferences).where(
                CompanyContractPreferences.company_id == company_id
            )
        )
    ).scalar_one_or_none()


async def _get_or_create_preferences(session, company_id) -> CompanyContractPreferences:
    """Nomes des d'una escriptura explicita: crear la fila es una mutacio."""
    prefs = await _find_preferences(session, company_id)
    if prefs is None:
        prefs = CompanyContractPreferences(company_id=company_id)
        session.add(prefs)
        await session.commit()
        await session.refresh(prefs)
    return prefs


def _default_preferences(company_id) -> ContractPreferencesRead:
    """Els valors per defecte del domini, en memoria. Sense fila, sense identificador.

    Els defaults surten del model mateix, perque no puguin divergir del que es
    desaria a la primera escriptura.
    """
    currency = CompanyContractPreferences.__table__.c.currency.default.arg
    return ContractPreferencesRead(company_id=company_id, currency=currency)


@router.get(
    "/companies/{company_id}/contract-preferences",
    response_model=ContractPreferencesRead,
    summary="Capacitat i preferencies contractuals",
)
async def get_contract_preferences(session: SessionDep, ctx: CompanyCtx):
    """Consultar no crea res: si encara no s'han desat, es retornen els defaults.

    Abans la primera lectura inseria la fila. Un GET que escriu es exactament el
    que no pot passar: la fila neix a la primera escriptura (`PUT`).
    """
    prefs = await _find_preferences(session, ctx.company_id)
    return prefs if prefs is not None else _default_preferences(ctx.company_id)


@router.put(
    "/companies/{company_id}/contract-preferences",
    response_model=ContractPreferencesRead,
    summary="Desa capacitat i preferencies contractuals",
)
async def update_contract_preferences(
    payload: ContractPreferencesUpdate, session: SessionDep, ctx: CompanyCtx
) -> CompanyContractPreferences:
    prefs = await _get_or_create_preferences(session, ctx.company_id)
    apply_changes(prefs, payload, ctx.user_id)
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="company_contract_preferences",
        entity_id=prefs.id,
    )
    await session.commit()
    await session.refresh(prefs)
    return prefs
