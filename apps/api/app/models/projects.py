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
from app.models.knowledge import KnowledgeOriginMixin, origin_constraints
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
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, KnowledgeOriginMixin, Base
):
    """Un projecte executat: una referencia d'execucio. Tambe es coneixement actual
    (0052). NO es cap `historic_bid`: una oferta presentada no acredita res; un
    projecte `completed` amb import i data, si. L'any d'execucio es el de la data
    de fi (o d'inici si no n'hi ha); les linies de servei son les dels serveis
    vinculats."""

    __tablename__ = "company_projects"
    __table_args__ = (
        *origin_constraints(),
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
    #: Imports EXECUTATS per any (0052). Sense files, l'import d'un projecte plurianual
    #: no es pot imputar a cap any concret.
    annual_amounts: Mapped[list[CompanyProjectAnnualAmount]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CompanyProjectAnnualAmount.year",
    )


class CompanyProjectAnnualAmount(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """Import executat d'un projecte en un any (0052). Font de veritat de l'empresa,
    acreditada amb l'evidencia del projecte (el certificat que en dona el
    desglossament). La moneda es la del projecte."""

    __tablename__ = "company_project_annual_amounts"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "year", name="uq_company_project_annual_amounts_year"),
        sa.CheckConstraint("amount >= 0", name="annual_amount_not_negative"),
        sa.CheckConstraint("year >= 1900 AND year <= 2200", name="annual_amount_year_range"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    year: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    project: Mapped[CompanyProject] = relationship(back_populates="annual_amounts")


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
