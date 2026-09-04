"""Bases compartides dels esquemes Pydantic."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, computed_field

from app.core.config import settings
from app.models.enums import TemporalStatus


class APIModel(BaseModel):
    """Base per a payloads d'entrada. Rebutja camps desconeguts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ORMModel(BaseModel):
    """Base per a respostes construides des de models SQLAlchemy."""

    model_config = ConfigDict(from_attributes=True)


class AuditFields(ORMModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None


def compute_temporal_status(expires_at: date | None) -> TemporalStatus:
    """Estat derivat, mai persistit. Font de veritat unica: `expires_at`."""
    if expires_at is None:
        return TemporalStatus.UNKNOWN
    today = date.today()
    if expires_at < today:
        return TemporalStatus.EXPIRED
    if (expires_at - today).days <= settings.EXPIRY_WARNING_DAYS:
        return TemporalStatus.EXPIRING_SOON
    return TemporalStatus.VALID


class ExpirableMixin(BaseModel):
    """Afegeix `temporal_status` a qualsevol esquema amb `expires_at`."""

    expires_at: date | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def temporal_status(self) -> TemporalStatus:
        return compute_temporal_status(self.expires_at)


class Message(BaseModel):
    message: str
