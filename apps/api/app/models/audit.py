"""Auditoria basica.

No es event sourcing: nomes un registre append-only d'accions rellevants amb
identificadors. Mai s'hi escriuen dades sensibles ni contingut de documents.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import AuditAction


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        sa.Index("ix_audit_logs_company_created", "company_id", "created_at"),
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[AuditAction] = mapped_column(
        enum_column(AuditAction, "audit_action"), nullable=False
    )
    entity_type: Mapped[str | None] = mapped_column(sa.String(80))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    summary: Mapped[str | None] = mapped_column(sa.String(500))
