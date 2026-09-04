"""Suport minim per a CPV (Common Procurement Vocabulary).

Decisio: `cpv_codes` es un cataleg propi amb el codi com a clau primaria
(el codi CPV es estable i unic), i les entitats s'hi relacionen amb taules
d'enllac. Aixo permet importar el cataleg europeu complet mes endavant sense
tocar cap altra taula, i alhora permet crear codis ad hoc mentre no s'importi.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CompanyOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CPVCode(TimestampMixin, Base):
    __tablename__ = "cpv_codes"

    code: Mapped[str] = mapped_column(sa.String(10), primary_key=True)
    description: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    # Jerarquia opcional: el CPV es jerarquic per prefix, pero no la materialitzem encara.
    parent_code: Mapped[str | None] = mapped_column(sa.String(10), index=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)


class CompanyCPVPreference(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """CPVs d'interes (o explicitament exclosos) per a una empresa."""

    __tablename__ = "company_cpv_preferences"
    __table_args__ = (
        sa.UniqueConstraint("company_id", "cpv_code", name="uq_company_cpv_preferences_company_code"),
    )

    cpv_code: Mapped[str] = mapped_column(
        sa.String(10), sa.ForeignKey("cpv_codes.code", ondelete="CASCADE"), nullable=False, index=True
    )
    priority: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    # `active` i `excluded` son coses diferents: un CPV pot estar temporalment
    # desactivat (no interessa ara) sense ser una exclusio explicita permanent.
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    excluded: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(sa.Text)


class ServiceCPV(TimestampMixin, Base):
    """CPVs tipics d'un servei de l'empresa."""

    __tablename__ = "service_cpvs"

    service_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_services.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cpv_code: Mapped[str] = mapped_column(
        sa.String(10), sa.ForeignKey("cpv_codes.code", ondelete="CASCADE"), primary_key=True
    )
