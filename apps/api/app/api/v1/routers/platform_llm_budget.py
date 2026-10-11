"""Pressupost d'LLM de la plataforma (3B-2 §6.7, decisio 5). Nomes l'operador.

* `GET /platform/llm-budget`: per scope (`canary`, `run`, `pilot_total`), el cap
  vigent, d'on surt (configuracio o autoritzacio) i el gastat segons el llibre.
* `POST /platform/llm-budget/authorizations`: una autoritzacio nova fixa el cap del
  seu scope i reobre la despesa si un run s'havia aturat per pressupost.

La logica viu a `services/llm_budget`; aquest router nomes la publica.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import PlatformAdmin, SessionDep
from app.schemas.llm_budget import (
    LlmBudgetAuthorizationIn,
    LlmBudgetAuthorizationRead,
    LlmBudgetStatusRead,
)
from app.services import llm_budget

router = APIRouter(prefix="/platform/llm-budget", tags=["platform"])


@router.get(
    "",
    response_model=LlmBudgetStatusRead,
    summary="Caps vigents i despesa d'LLM per scope",
)
async def read_llm_budget(_: PlatformAdmin, session: SessionDep) -> LlmBudgetStatusRead:
    return LlmBudgetStatusRead.model_validate(await llm_budget.budget_status(session))


@router.post(
    "/authorizations",
    response_model=LlmBudgetAuthorizationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registra una autoritzacio de pressupost (cap nou per a un scope)",
)
async def create_llm_budget_authorization(
    payload: LlmBudgetAuthorizationIn, admin: PlatformAdmin, session: SessionDep
) -> LlmBudgetAuthorizationRead:
    authorization = await llm_budget.authorize(
        session,
        scope=payload.scope,
        cap_usd=payload.cap_usd,
        user_id=admin.id,
        note=payload.note,
    )
    return LlmBudgetAuthorizationRead.model_validate(authorization)
