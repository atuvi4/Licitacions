"""Escriptura del registre d'auditoria."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.enums import AuditAction


async def record(
    session: AsyncSession,
    *,
    action: AuditAction,
    company_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    summary: str | None = None,
) -> None:
    """Afegeix una entrada d'auditoria a la sessio actual (sense fer commit).

    `summary` ha de contenir nomes text no sensible.
    """
    session.add(
        AuditLog(
            action=action,
            company_id=company_id,
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=(summary or "")[:500] or None,
        )
    )
