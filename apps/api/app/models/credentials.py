"""Acreditacions corporatives: certificacions, registres i assegurances.

Principi: NO es persisteix cap estat temporal derivable de `expires_at`.
Nomes es guarda l'estat de verificacio (una decisio humana). L'estat
`valid | expiring_soon | expired | unknown` es calcula a la capa d'esquemes.
"""

from __future__ import annotations

from datetime import date
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
from app.models.enums import InsuranceType, RegistrationType, VerificationStatus

MONEY = sa.Numeric(18, 2)


class CompanyCertification(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    __tablename__ = "company_certifications"
    __table_args__ = (
        sa.CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at >= issued_at",
            name="certification_dates_order",
        ),
    )

    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    issuer: Mapped[str | None] = mapped_column(sa.String(200))
    certificate_number: Mapped[str | None] = mapped_column(sa.String(120))
    scope: Mapped[str | None] = mapped_column(sa.Text)
    issued_at: Mapped[date | None] = mapped_column(sa.Date)
    expires_at: Mapped[date | None] = mapped_column(sa.Date)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_column(VerificationStatus, "verification_status"),
        nullable=False,
        default=VerificationStatus.PENDING_VERIFICATION,
    )
    notes: Mapped[str | None] = mapped_column(sa.Text)


class CompanyRegistration(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """ROLECE, RELI, classificacions empresarials, registres professionals..."""

    __tablename__ = "company_registrations"
    __table_args__ = (
        sa.CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at >= issued_at",
            name="registration_dates_order",
        ),
    )

    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    type: Mapped[RegistrationType] = mapped_column(
        enum_column(RegistrationType, "registration_type"),
        nullable=False,
        default=RegistrationType.OTHER,
    )
    registration_number: Mapped[str | None] = mapped_column(sa.String(120))
    issuer: Mapped[str | None] = mapped_column(sa.String(200))
    issued_at: Mapped[date | None] = mapped_column(sa.Date)
    expires_at: Mapped[date | None] = mapped_column(sa.Date)
    notes: Mapped[str | None] = mapped_column(sa.Text)


class CompanyInsurance(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    __tablename__ = "company_insurances"
    __table_args__ = (
        sa.CheckConstraint(
            "expires_at IS NULL OR starts_at IS NULL OR expires_at >= starts_at",
            name="insurance_dates_order",
        ),
    )

    type: Mapped[InsuranceType] = mapped_column(
        enum_column(InsuranceType, "insurance_type"),
        nullable=False,
        default=InsuranceType.OTHER,
    )
    provider: Mapped[str | None] = mapped_column(sa.String(200))
    policy_number: Mapped[str | None] = mapped_column(sa.String(120))
    coverage_description: Mapped[str | None] = mapped_column(sa.Text)
    coverage_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="EUR")
    starts_at: Mapped[date | None] = mapped_column(sa.Date)
    expires_at: Mapped[date | None] = mapped_column(sa.Date)
    notes: Mapped[str | None] = mapped_column(sa.Text)
