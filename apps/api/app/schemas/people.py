"""Esquemes d'equip huma i titulacions."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import Field, field_validator

from app.knowledge.vocabulary import PROFESSIONAL_PROFILE_CODES
from app.models.enums import (
    AvailabilityStatus,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    QualificationType,
    VerificationStatus,
)
from app.schemas.common import APIModel, AuditFields, ExpirableMixin


class PersonBase(APIModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=120)
    last_name: str | None = Field(default=None, max_length=160)
    role: str | None = Field(default=None, max_length=160)
    department: str | None = Field(default=None, max_length=160)
    professional_profile: str | None = None
    years_experience: int | None = Field(default=None, ge=0, le=80)
    availability_status: AvailabilityStatus | None = None
    notes: str | None = None
    #: Perfils ESTRUCTURATS del vocabulari (0052). `professional_profile` es text i no
    #: es llegeix. Una persona en pot cobrir mes d'un.
    profile_codes: list[str] | None = Field(default=None, min_length=1)
    #: Procedencia (0052). Sense `verification_status`: verificar nomes es pot per
    #: `/knowledge/records/{kind}/{id}/verify` (propietari o administracio).
    as_of: date | None = None
    lifecycle: KnowledgeLifecycle | None = None

    @field_validator("profile_codes")
    @classmethod
    def _known_profiles(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - PROFESSIONAL_PROFILE_CODES)
        if unknown:
            raise ValueError(f"Perfils desconeguts: {', '.join(unknown)}.")
        return sorted(set(value))


class PersonCreate(PersonBase):
    first_name: str = Field(min_length=1, max_length=120)
    availability_status: AvailabilityStatus = AvailabilityStatus.UNKNOWN


class PersonUpdate(PersonBase):
    pass


class PersonRead(AuditFields):
    first_name: str
    last_name: str | None = None
    role: str | None = None
    department: str | None = None
    professional_profile: str | None = None
    years_experience: int | None = None
    availability_status: AvailabilityStatus
    notes: str | None = None
    profile_codes: list[str] | None = None
    knowledge_origin: KnowledgeOrigin
    as_of: date | None = None
    verification_status: VerificationStatus
    lifecycle: KnowledgeLifecycle


class QualificationBase(APIModel):
    type: QualificationType | None = None
    name: str | None = Field(default=None, min_length=2, max_length=200)
    issuer: str | None = Field(default=None, max_length=200)
    credential_number: str | None = Field(default=None, max_length=120)
    issued_at: date | None = None
    expires_at: date | None = None
    notes: str | None = None


class QualificationCreate(QualificationBase):
    name: str = Field(min_length=2, max_length=200)
    type: QualificationType = QualificationType.OTHER


class QualificationUpdate(QualificationBase):
    pass


class QualificationRead(AuditFields, ExpirableMixin):
    person_id: uuid.UUID
    type: QualificationType
    name: str
    issuer: str | None = None
    credential_number: str | None = None
    issued_at: date | None = None
    notes: str | None = None
