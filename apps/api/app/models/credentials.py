"""Acreditacions corporatives: certificacions, registres i assegurances.

Principi: NO es persisteix cap estat temporal derivable de `expires_at`.
Nomes es guarda l'estat de verificacio (una decisio humana). L'estat
`valid | expiring_soon | expired | unknown` es calcula a la capa d'esquemes.
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
from app.models.enums import (
    InsuranceLimitKind,
    InsuranceType,
    RegistrationType,
    VerificationStatus,
)

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
    #: L'organ de contractacio consta com a assegurat addicional. `None` vol dir
    #: que no consta a la fitxa, que NO es el mateix que dir que no hi es.
    additional_insured: Mapped[bool | None] = mapped_column(sa.Boolean)
    notes: Mapped[str | None] = mapped_column(sa.Text)

    #: Els limits declarats de la polissa, cadascun amb el seu tipus.
    limits: Mapped[list[CompanyInsuranceLimit]] = relationship(
        back_populates="insurance", cascade="all, delete-orphan", lazy="selectin"
    )

    def limit_for(self, kind: InsuranceLimitKind) -> CompanyInsuranceLimit | None:
        return next((limit for limit in self.limits if limit.kind == kind), None)


class CompanyInsuranceLimit(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """Un limit concret d'una polissa: total, per sinistre, per victima...

    Existeix com a taula i no com a columnes perque la seva **absencia ha de ser
    llegible**. Amb columnes, un sublimit per victima buit seria indistingible
    d'un sublimit desconegut, i aquesta diferencia decideix si es pot afirmar que
    una empresa compleix una polissa o nomes que no ho sabem.

    Una fila diu "aquesta polissa cobreix aixo, fins aqui". Cap fila vol dir que
    ningu ho ha declarat.
    """

    __tablename__ = "company_insurance_limits"
    __table_args__ = (
        sa.UniqueConstraint(
            "insurance_id", "kind", name="uq_company_insurance_limits_insurance_kind"
        ),
        sa.CheckConstraint("amount >= 0", name="insurance_limit_amount_positive"),
    )

    insurance_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("company_insurances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[InsuranceLimitKind] = mapped_column(
        enum_column(InsuranceLimitKind, "insurance_limit_kind"),
        nullable=False,
        default=InsuranceLimitKind.TOTAL,
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="EUR")
    notes: Mapped[str | None] = mapped_column(sa.Text)

    insurance: Mapped[CompanyInsurance] = relationship(back_populates="limits")
