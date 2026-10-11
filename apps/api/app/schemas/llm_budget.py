"""Contractes de `/platform/llm-budget` (3B-2 §6.7). Nomes l'operador de plataforma."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import LlmBudgetScope


class LlmBudgetScopeRead(BaseModel):
    scope: LlmBudgetScope
    #: Cert per a `canary` i `run` (s'apliquen a cada run); fals per a `pilot_total`.
    per_run: bool
    cap_usd: float
    #: `config` o `authorization`.
    cap_source: str
    authorization_id: uuid.UUID | None = None
    authorized_at: datetime | None = None
    #: `pilot_total`: tot el llibre. `canary`/`run`: el run d'aquell scope que mes ha
    #: gastat (cada linia porta l'scope del run que la va pagar).
    spent_usd: float


class LlmBudgetStatusRead(BaseModel):
    scopes: list[LlmBudgetScopeRead]
    total_spent_usd: float
    #: Cert si cap quota nova que gasti no es pot confirmar fins a una autoritzacio.
    exhausted: bool


class LlmBudgetAuthorizationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: LlmBudgetScope
    cap_usd: Decimal = Field(gt=0, max_digits=12, decimal_places=6)
    #: Per que (p. ex. «aixecament escrit del propietari»). Cap secret.
    note: str = Field(min_length=1, max_length=500)


class LlmBudgetAuthorizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope: LlmBudgetScope
    cap_usd: float
    authorized_by: uuid.UUID | None = None
    note: str
    created_at: datetime
