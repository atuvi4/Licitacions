"""Identitat i multi-tenant: User, Company, CompanyMembership."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import MembershipRole


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(sa.String(200))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    is_superuser: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # Preparat per a MFA. Cap flux l'utilitza encara.
    mfa_enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(sa.String(255))

    memberships: Mapped[list[CompanyMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "companies"

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
