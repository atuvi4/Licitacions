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
    KnowledgeLifecycle,
    KnowledgeOrigin,
    RegistrationType,
    VerificationStatus,
)
from app.models.knowledge import KnowledgeOriginMixin, origin_constraints
from app.models.review import JSONDocument, in_values

MONEY = sa.Numeric(18, 2)


class CompanyCertification(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """Una certificacio de l'empresa. Tambe es coneixement actual (fase 2 del motor).

    La procedencia fa servir el que ja hi havia: `verification_status` es la
    verificacio i `expires_at` fa de `valid_until`. S'hi afegeix `knowledge_origin`,
    `as_of` i `lifecycle` (una certificacio `in_progress` no acredita res), i la
    norma i el nivell del vocabulari global (`ENS` + `medium`) i l'abast
    estructurat (`scope_service_lines`), que son el que permet comparar-la amb un
    plec sense dependre de text lliure.
    """

    __tablename__ = "company_certifications"
    __table_args__ = (
        sa.CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at >= issued_at",
            name="certification_dates_order",
        ),
        sa.CheckConstraint(
            in_values("knowledge_origin", KnowledgeOrigin), name="knowledge_origin_valid"
        ),
        sa.CheckConstraint(in_values("lifecycle", KnowledgeLifecycle), name="lifecycle_valid"),
        sa.CheckConstraint(
            "level IS NULL OR standard_code IS NOT NULL", name="level_needs_standard"
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
    standard_code: Mapped[str | None] = mapped_column(
        sa.String(40),
        # Nom explicit: el de la convencio passaria dels 63 caracters de PostgreSQL.
        sa.ForeignKey(
            "certification_standards.code",
            ondelete="RESTRICT",
            name="fk_company_certifications_standard_code",
        ),
    )
    level: Mapped[str | None] = mapped_column(sa.String(20))
    #: Linies de servei que cobreix el certificat, del vocabulari global. Nul =
    #: abast sense estructurar: el motor no el dedueix del text de `scope`, que es
    #: queda com a nota.
    scope_service_lines: Mapped[list[str] | None] = mapped_column(JSONDocument)
    knowledge_origin: Mapped[KnowledgeOrigin] = mapped_column(
        enum_column(KnowledgeOrigin, "knowledge_origin"),
        nullable=False,
        default=KnowledgeOrigin.DECLARED,
        server_default=KnowledgeOrigin.DECLARED.value,
    )
    as_of: Mapped[date] = mapped_column(
        sa.Date, nullable=False, default=date.today, server_default=sa.text("CURRENT_DATE")
    )
    lifecycle: Mapped[KnowledgeLifecycle] = mapped_column(
        enum_column(KnowledgeLifecycle, "knowledge_lifecycle"),
        nullable=False,
        default=KnowledgeLifecycle.CURRENT,
        server_default=KnowledgeLifecycle.CURRENT.value,
    )


class CompanyRegistration(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, KnowledgeOriginMixin, Base
):
    """ROLECE, RELI, classificacions empresarials, registres professionals, registres
    sectorials d'operadors (`sector_operator_registry`, amb l'emissor a `issuer`).

    Tambe es coneixement actual (0052). L'abast ESTRUCTURAT (`scope_service_lines`)
    es l'unic que el motor compara: sense estructurar, l'abast es desconegut i el
    text de `notes` no es llegeix. `expires_at` fa de vigencia.
    """

    __tablename__ = "company_registrations"
    __table_args__ = (
        sa.CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at >= issued_at",
            name="registration_dates_order",
        ),
        *origin_constraints(),
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
    #: Abast estructurat (0052): linies de servei del vocabulari que la inscripcio
    #: cobreix. Nul = no estructurat (desconegut), que no es «cap».
    scope_service_lines: Mapped[list[str] | None] = mapped_column(JSONDocument)


class CompanyInsurance(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, KnowledgeOriginMixin, Base
):
    """Una polissa. Tambe es coneixement actual (0052): el motor compara els seus
    `limits` estructurats amb el plec; `expires_at` fa de vigencia. Un canvi als
    limits es un canvi material de la polissa (en desfa la verificacio)."""

    __tablename__ = "company_insurances"
    __table_args__ = (
        sa.CheckConstraint(
            "expires_at IS NULL OR starts_at IS NULL OR expires_at >= starts_at",
            name="insurance_dates_order",
        ),
        *origin_constraints(),
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
