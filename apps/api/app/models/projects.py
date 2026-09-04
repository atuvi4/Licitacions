"""Experiencia executada: projectes i els serveis que hi van intervenir.

El model esta pensat perque, en fases futures, es puguin respondre consultes
del tipus: "quants projectes de servei X, superiors a 150.000 EUR, ha executat
aquesta empresa els ultims 3 anys?". Per aixo `amount`, `end_date` i el vincle
amb serveis son columnes de primera classe i estan indexades.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    ActorMixin,
    Base,
    CompanyOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)
from app.models.cpv import CPVCode
from app.models.enums import ClientType, ProjectStatus
from app.models.profile import CompanyService

MONEY = sa.Numeric(18, 2)


project_services = sa.Table(
    "project_services",
    Base.metadata,
    sa.Column(
        "project_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "service_id",
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_services.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)


class CompanyProject(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    __tablename__ = "company_projects"
    __table_args__ = (
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="project_dates_order",
        ),
        sa.CheckConstraint("amount IS NULL OR amount >= 0", name="project_amount_positive"),
        sa.Index("ix_company_projects_company_end_date", "company_id", "end_date"),
        sa.Index("ix_company_projects_company_amount", "company_id", "amount"),
    )

    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    client_name: Mapped[str | None] = mapped_column(sa.String(255))
    client_type: Mapped[ClientType] = mapped_column(
        enum_column(ClientType, "client_type"), nullable=False, default=ClientType.OTHER
    )
    sector: Mapped[str | None] = mapped_column(sa.String(160))
    description: Mapped[str | None] = mapped_column(sa.Text)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="EUR")
    start_date: Mapped[date | None] = mapped_column(sa.Date)
    end_date: Mapped[date | None] = mapped_column(sa.Date)
    location: Mapped[str | None] = mapped_column(sa.String(255))
    status: Mapped[ProjectStatus] = mapped_column(
        enum_column(ProjectStatus, "project_status"),
        nullable=False,
        default=ProjectStatus.COMPLETED,
    )
    reference_contact: Mapped[str | None] = mapped_column(sa.String(255))
    notes: Mapped[str | None] = mapped_column(sa.Text)

    services: Mapped[list[CompanyService]] = relationship(
        secondary=project_services, lazy="selectin"
    )
    cpvs: Mapped[list[ProjectCPV]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )


class ProjectCPV(TimestampMixin, Base):
    """CPVs sota els quals es va executar un projecte.

    Es una taula pont amb entitat propia (i no un `secondary`) perque en el
    futur hi haura d'anar informacio addicional: si el CPV era principal o
    secundari, quin import hi correspon, etc.
    """

    __tablename__ = "project_cpvs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cpv_code: Mapped[str] = mapped_column(
        sa.String(10), sa.ForeignKey("cpv_codes.code", ondelete="CASCADE"), primary_key=True
    )

    project: Mapped[CompanyProject] = relationship(back_populates="cpvs")
    code: Mapped[CPVCode] = relationship(lazy="selectin")

    @property
    def description(self) -> str | None:
        """Descripcio del cataleg, per no haver-la de duplicar aqui."""
        return self.code.description if self.code else None
