"""Perfil corporatiu: serveis, ambit geografic i solvencia economica."""

from __future__ import annotations

import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    ActorMixin,
    Base,
    CompanyOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)
from app.models.enums import SpecializationLevel, TerritoryType

MONEY = sa.Numeric(18, 2)


class CompanyService(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """Servei o capacitat que l'empresa pot executar. Totalment configurable."""

    __tablename__ = "company_services"
    __table_args__ = (
        sa.UniqueConstraint("company_id", "name", name="uq_company_services_company_name"),
    )

    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    category: Mapped[str | None] = mapped_column(sa.String(120))
    description: Mapped[str | None] = mapped_column(sa.Text)
    specialization_level: Mapped[SpecializationLevel | None] = mapped_column(
        enum_column(SpecializationLevel, "specialization_level")
    )
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(sa.Text)


class CompanyTerritory(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """Ambit geografic on l'empresa treballa o vol treballar.

    `code` queda lliure per a codis INE, NUTS o qualsevol altra codificacio
    futura sense forcar ara cap jerarquia.
    """

    __tablename__ = "company_territories"
    __table_args__ = (
        sa.UniqueConstraint(
            "company_id", "territory_type", "name", name="uq_company_territories_company_type_name"
        ),
    )

    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    territory_type: Mapped[TerritoryType] = mapped_column(
        enum_column(TerritoryType, "territory_type"), nullable=False
    )
    code: Mapped[str | None] = mapped_column(sa.String(32))
    priority: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    willing_to_work: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(sa.Text)


class CompanyFinancialYear(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """Facturacio anual. Un unic registre per empresa i any."""

    __tablename__ = "company_financial_years"
    __table_args__ = (
        sa.UniqueConstraint("company_id", "year", name="uq_company_financial_years_company_year"),
        sa.CheckConstraint("year >= 1900 AND year <= 2200", name="financial_year_range"),
    )

    year: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    revenue: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="EUR")
    notes: Mapped[str | None] = mapped_column(sa.Text)


class CompanyContractPreferences(
    UUIDPrimaryKeyMixin, TimestampMixin, ActorMixin, Base
):
    """Capacitat i preferencies contractuals (una fila per empresa).

    Aquestes dades alimentaran el futur Fit Economic. En aquesta iteracio nomes
    s'emmagatzemen; no es calcula res.
    """

    __tablename__ = "company_contract_preferences"

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    minimum_interesting_contract: Mapped[Decimal | None] = mapped_column(MONEY)
    preferred_contract_min: Mapped[Decimal | None] = mapped_column(MONEY)
    preferred_contract_max: Mapped[Decimal | None] = mapped_column(MONEY)
    maximum_recommended_contract: Mapped[Decimal | None] = mapped_column(MONEY)
    maximum_assumable_contract: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="EUR")
    notes: Mapped[str | None] = mapped_column(sa.Text)
