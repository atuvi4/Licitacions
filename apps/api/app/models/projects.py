"""Experiencia executada: projectes i els serveis que hi van intervenir.

El model esta pensat perque, en fases futures, es puguin respondre consultes
del tipus: "quants projectes de servei X, superiors a 150.000 EUR, ha executat
aquesta empresa els ultims 3 anys?". Per aixo `amount`, `end_date` i el vincle
amb serveis son columnes de primera classe i estan indexades.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime
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


#: LEGACY (des de 0053). Els serveis del cataleg ACTUAL de l'empresa vinculats a un
#: projecte. Nomes l'escriuen la UI 4C antiga i el seu `service_ids`: la qualificacio,
#: la fotografia i l'analisi llegeixen `company_project_service_lines`. No hi ha
#: sincronitzacio entre les dues: canviar els serveis d'un projecte des de la UI antiga
#: no canvia les linies que acredita.
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
    de fi (o d'inici si no n'hi ha). Les linies de servei que ACREDITA son les de
    `company_project_service_lines` (0053), directament del vocabulari: no depenen de
    cap servei actual de l'empresa (`services` es legacy)."""

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

    #: LEGACY (des de 0053): vegeu `project_services`.
    services: Mapped[list[CompanyService]] = relationship(
        secondary=project_services, lazy="selectin"
    )
    #: Linies de servei que aquesta referencia acredita (0053). Font de veritat.
    service_lines: Mapped[list[CompanyProjectServiceLine]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CompanyProjectServiceLine.service_line_code",
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


    @property
    def service_line_codes(self) -> list[str]:
        return sorted(line.service_line_code for line in self.service_lines)

    def replace_service_lines(self, codes: Iterable[str]) -> bool:
        """Substitueix el conjunt de linies codi per codi (mai esborrar i tornar a
        inserir el mateix: la clau primaria ho prohibiria). Cert si ha canviat."""
        wanted = set(codes)
        changed = False
        for line in list(self.service_lines):
            if line.service_line_code not in wanted:
                self.service_lines.remove(line)
                changed = True
        present = {line.service_line_code for line in self.service_lines}
        for code in sorted(wanted - present):
            self.service_lines.append(CompanyProjectServiceLine(service_line_code=code))
            changed = True
        return changed


class CompanyProjectServiceLine(Base):
    """Una linia de servei del vocabulari que una referencia d'execucio acredita (0053).

    Es una dada HISTORICA del projecte: «aquest contracte era de veu IP», encara que
    avui l'empresa no tingui cap servei de veu IP. Per aixo no passa pel cataleg de
    serveis (`project_services`, legacy). Sense `company_id`: el projecte ja el porta."""

    __tablename__ = "company_project_service_lines"

    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    service_line_code: Mapped[str] = mapped_column(
        sa.String(40), sa.ForeignKey("service_lines.code", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    project: Mapped[CompanyProject] = relationship(back_populates="service_lines")


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
