"""Identitat i multi-tenant: User, Company, CompanyMembership."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import CompanyPlatformStatus, MembershipRole


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(sa.String(200))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    is_superuser: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # Contrasenya temporal (alta per la plataforma): no pot fer res mes fins que la canvia.
    must_change_password: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )

    # Preparat per a MFA. Cap flux l'utilitza encara.
    mfa_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(sa.String(255))

    memberships: Mapped[list[CompanyMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_platform_admin(self) -> bool:
        """Administracio de la plataforma (alta d'empreses). No dona acces a cap empresa."""
        return bool(self.is_superuser)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"


def _in_values(column: str, enum_cls: type) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{member.value}'" for member in enum_cls))


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        sa.CheckConstraint(
            _in_values("platform_status", CompanyPlatformStatus), name="platform_status_valid"
        ),
        # Un pilot sempre te fi, i la fi no es anterior a l'inici.
        sa.CheckConstraint(
            "platform_status <> 'pilot' OR pilot_ends_at IS NOT NULL", name="pilot_has_end"
        ),
        sa.CheckConstraint(
            "pilot_started_at IS NULL OR pilot_ends_at IS NULL "
            "OR pilot_ends_at >= pilot_started_at",
            name="pilot_dates_ordered",
        ),
    )

    legal_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(sa.String(255))
    tax_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    legal_form: Mapped[str | None] = mapped_column(sa.String(120))
    incorporation_date: Mapped[date | None] = mapped_column(sa.Date)
    website: Mapped[str | None] = mapped_column(sa.String(255))
    email: Mapped[str | None] = mapped_column(sa.String(320))
    phone: Mapped[str | None] = mapped_column(sa.String(64))
    country: Mapped[str | None] = mapped_column(sa.String(2))  # ISO 3166-1 alpha-2
    autonomous_region: Mapped[str | None] = mapped_column(sa.String(120))
    province: Mapped[str | None] = mapped_column(sa.String(120))
    city: Mapped[str | None] = mapped_column(sa.String(120))
    postal_code: Mapped[str | None] = mapped_column(sa.String(16))
    address: Mapped[str | None] = mapped_column(sa.String(255))
    description: Mapped[str | None] = mapped_column(sa.Text)
    employee_count: Mapped[int | None] = mapped_column(sa.Integer)

    # --- Administracio Tuvi (0043): l'estat del client i qui en va fer l'alta.
    platform_status: Mapped[CompanyPlatformStatus] = mapped_column(
        enum_column(CompanyPlatformStatus, "company_platform_status"),
        nullable=False,
        default=CompanyPlatformStatus.ACTIVE,
        server_default=CompanyPlatformStatus.ACTIVE.value,
    )
    pilot_started_at: Mapped[date | None] = mapped_column(sa.Date)
    pilot_ends_at: Mapped[date | None] = mapped_column(sa.Date)
    status_changed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    onboarded_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )

    memberships: Mapped[list[CompanyMembership]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Company {self.legal_name}>"


class CompanyMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Relacio N:M entre usuaris i empreses, amb rol.

    Un usuari pot pertanyer a diverses empreses. Els rols estan definits pero
    encara no hi ha matriu de permisos: nomes es comprova la pertinenca.
    """

    __tablename__ = "company_memberships"
    __table_args__ = (sa.UniqueConstraint("user_id", "company_id", name="uq_membership_user_company"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MembershipRole] = mapped_column(
        enum_column(MembershipRole, "membership_role"),
        nullable=False,
        default=MembershipRole.VIEWER,
    )
    is_default: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    company: Mapped[Company] = relationship(back_populates="memberships", lazy="selectin")
