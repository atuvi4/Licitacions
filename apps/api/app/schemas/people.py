"""Esquemes d'equip huma i titulacions."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import Field

from app.models.enums import AvailabilityStatus, QualificationType
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
