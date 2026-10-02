"""Equip huma.

Nomes informacio professional rellevant per a licitacions. No s'hi guarden
dades personals innecessaries (DNI, adreca particular, dades de salut...).
"""

from __future__ import annotations

import uuid
from datetime import date

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
from app.models.enums import AvailabilityStatus, QualificationType
from app.models.knowledge import KnowledgeOriginMixin, origin_constraints
from app.models.review import JSONDocument


class CompanyPerson(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, KnowledgeOriginMixin, Base
):
    """Una persona de l'equip. Tambe es coneixement actual (0052): els perfils
    ESTRUCTURATS (`profile_codes`, del vocabulari) i la disponibilitat son el que el
    motor compara amb un plec. Disponible no vol dir adscrit: el motor no assigna
    ningu a cap contracte. `professional_profile` es text i no es llegeix."""

    __tablename__ = "company_people"
    __table_args__ = (*origin_constraints(),)

    first_name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    last_name: Mapped[str | None] = mapped_column(sa.String(160))
    role: Mapped[str | None] = mapped_column(sa.String(160))
    department: Mapped[str | None] = mapped_column(sa.String(160))
    professional_profile: Mapped[str | None] = mapped_column(sa.Text)
    #: Perfils del vocabulari (`vocabulary.PROFESSIONAL_PROFILES`). Nul = sense
    #: estructurar. Una persona en pot cobrir mes d'un.
    profile_codes: Mapped[list[str] | None] = mapped_column(JSONDocument)
    years_experience: Mapped[int | None] = mapped_column(sa.Integer)
    availability_status: Mapped[AvailabilityStatus] = mapped_column(
        enum_column(AvailabilityStatus, "availability_status"),
        nullable=False,
        default=AvailabilityStatus.UNKNOWN,
    )
    notes: Mapped[str | None] = mapped_column(sa.Text)

    qualifications: Mapped[list[PersonQualification]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class PersonQualification(UUIDPrimaryKeyMixin, TimestampMixin, ActorMixin, Base):
    __tablename__ = "person_qualifications"
    __table_args__ = (
        sa.CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at >= issued_at",
            name="qualification_dates_order",
        ),
    )

    person_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_people.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[QualificationType] = mapped_column(
        enum_column(QualificationType, "qualification_type"),
        nullable=False,
        default=QualificationType.OTHER,
    )
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    issuer: Mapped[str | None] = mapped_column(sa.String(200))
    credential_number: Mapped[str | None] = mapped_column(sa.String(120))
    issued_at: Mapped[date | None] = mapped_column(sa.Date)
    expires_at: Mapped[date | None] = mapped_column(sa.Date)
    notes: Mapped[str | None] = mapped_column(sa.Text)

    person: Mapped[CompanyPerson] = relationship(back_populates="qualifications")
