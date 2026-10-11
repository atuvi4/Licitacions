"""Llibre de despesa d'LLM i autoritzacions de pressupost (0061, 3B-2 §6.7).

Totes dues taules son GLOBALS (`tenant_ops.PUBLIC_TABLES`): la despesa es de la
plataforma, no de cap empresa, i el run que la genera tampoc no en registra cap.

* `llm_spend_ledger`: una linia per chunk que el proveidor ha respost (tambe un
  chunk que despres falla per `output_truncated` o `schema_invalid`: aquells tokens
  es paguen). Nomes d'afegir. Si el run desapareix, la linia es queda (`run_id`
  NUL): la despesa real no s'esborra mai. `cost_usd` sempre te import (la porta
  unica exigeix preu a tot run real) i `budget_scope` diu si el va pagar un canari.
* `llm_budget_authorizations`: cada autoritzacio de l'operador fixa el cap d'un
  scope. El cap vigent es el de l'ultima del scope; sense cap, el de configuracio.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import LlmBudgetScope

#: Dolars amb sis decimals: el cost d'un sol token d'entrada ja en necessita.
USD = sa.Numeric(12, 6)


def _now() -> datetime:
    return datetime.now(UTC)


class LlmSpendLedger(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "llm_spend_ledger"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "chunk_index", name="uq_llm_spend_ledger_run_chunk"),
        sa.CheckConstraint("cost_usd >= 0", name="cost_valid"),
        sa.CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="tokens_valid"),
        sa.CheckConstraint("budget_scope IN ('canary', 'run')", name="budget_scope_valid"),
        sa.Index("ix_llm_spend_ledger_created_at", "created_at"),
    )

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "requirement_extraction_runs.id",
            ondelete="SET NULL",
            name="fk_llm_spend_ledger_run_id",
        ),
    )
    chunk_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Sempre amb import: un run real nomes passa la porta amb el preu del seu model.
    cost_usd: Mapped[Decimal] = mapped_column(USD, nullable=False)
    #: L'scope del run que l'ha pagat: `canary` (una prova de pocs chunks) o `run`.
    budget_scope: Mapped[LlmBudgetScope] = mapped_column(
        enum_column(LlmBudgetScope, "llm_spend_scope", length=20), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), default=_now, nullable=False
    )


class LlmBudgetAuthorization(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "llm_budget_authorizations"
    __table_args__ = (
        sa.CheckConstraint("scope IN ('canary', 'run', 'pilot_total')", name="scope_valid"),
        sa.CheckConstraint("cap_usd > 0", name="cap_valid"),
        sa.Index("ix_llm_budget_authorizations_scope_created", "scope", "created_at"),
    )

    scope: Mapped[LlmBudgetScope] = mapped_column(
        enum_column(LlmBudgetScope, "llm_budget_scope", length=20), nullable=False
    )
    cap_usd: Mapped[Decimal] = mapped_column(USD, nullable=False)
    authorized_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_llm_budget_authorizations_authorized_by"
        ),
    )
    #: Per que (p. ex. «aixecament escrit del propietari, 2026-10-08»). Cap secret.
    note: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), default=_now, nullable=False
    )
