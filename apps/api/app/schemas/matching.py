"""Esquemes del pre-fit.

Terminologia deliberada: aixo es un **pre-fit** calculat amb metadades, no un
Go/No-Go. Els noms dels camps ho han de reflectir perque ningu no els confongui
mes endavant.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import ExclusionKind, MatchStatus
from app.schemas.common import ORMModel
from app.schemas.tenders import TenderListItem


class MatchFactor(BaseModel):
    """Una dimensio avaluada, amb el motiu estructurat que la justifica."""

    dimension: str
    result: str
    reason_code: str
    weight: int
    score: float | None = None
    max_score: int
    company_value: str | None = None
    source_value: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class MatchBase(ORMModel):
    id: uuid.UUID
    score: int | None = None
    status: MatchStatus
    exclusion_kind: ExclusionKind | None = None
    exclusion_reason_code: str | None = None
    cpv_score: float | None = None
    geographic_score: float | None = None
    economic_score: float | None = None
    timing_score: float | None = None
    data_coverage: float
    factors: list[MatchFactor] = []
    rules_version: str
    calculated_at: datetime


class LotMatchRead(MatchBase):
    tender_lot_id: uuid.UUID
    lot_number: int | None = None
    lot_title: str | None = None


class TenderMatchRead(MatchBase):
    tender_id: uuid.UUID
    best_lot_id: uuid.UUID | None = None
    candidate_lot_count: int
    total_lot_count: int
    # Calculat en llegir, comparant amb l'estat actual del perfil i la licitacio.
    is_stale: bool = False
    lot_matches: list[LotMatchRead] = []


class TenderMatchListItem(BaseModel):
    """Fila del feed personalitzat: la licitacio amb el seu pre-fit."""

    tender: TenderListItem
    match: TenderMatchRead | None = None


class MatchSummary(BaseModel):
    """Metriques per al dashboard. Cap d'elles parla de guanyar ni de presentar."""

    total_tenders: int
    actionable: int
    candidates: int
    high_fit: int
    low_fit: int
    excluded: int
    closed: int
    insufficient_data: int
    stale: int
    rules_version: str
    last_calculated_at: datetime | None = None


class RecalculationResult(BaseModel):
    tenders_evaluated: int
    lots_evaluated: int
    created: int
    updated: int
    duration_ms: int
    rules_version: str
