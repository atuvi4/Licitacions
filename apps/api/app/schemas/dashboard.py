"""Esquemes del resum de dashboard."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel

from app.models.enums import TemporalStatus
from app.schemas.auth import CompanySummary


class CompletenessSection(BaseModel):
    key: str
    label: str
    weight: int
    ratio: float
    points: float
    status: str  # complete | partial | empty
    detail: str
    # Si es True, la dada nomes es considera completa quan te document justificatiu.
    requires_evidence: bool = False


class ProfileCompleteness(BaseModel):
    score: int
    sections: list[CompletenessSection]


class EntityCounts(BaseModel):
    services: int
    territories: int
    financial_years: int
    certifications: int
    registrations: int
    insurances: int
    people: int
    qualifications: int
    cpv_preferences: int
    #: 0 o 1: les preferencies de contracte desades (una fila per empresa). Amb CPV i
    #: territoris, el que decideix si el pre-fit es pot calcular (3B-2 §7.1.4).
    contract_preferences: int = 0
    projects: int
    documents: int
    document_links: int


class ExpiryAlert(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    label: str
    expires_at: date
    days_left: int
    temporal_status: TemporalStatus


class DashboardSummary(BaseModel):
    company: CompanySummary
    completeness: ProfileCompleteness
    counts: EntityCounts
    expiry_alerts: list[ExpiryAlert]
