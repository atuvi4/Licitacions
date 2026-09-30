"""Contracte extern del coneixement de l'empresa (fase 2 del motor d'analisi)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.knowledge.snapshot import Entry, KnowledgeSnapshot
from app.models.enums import ObservationStatus, ObservationSubject
from app.schemas.common import APIModel, ORMModel


class ClaimRead(BaseModel):
    record_id: str
    source: str
    value: Any
    as_of: date
    valid_until: date | None
    accreditation_pending: bool
    #: Nomes per a certificacions: linies cobertes i `structured` o
    #: `scope_unknown` (abast sense estructurar: no es dedueix del text).
    scope_service_lines: list[str] | None = None
    scope_status: str | None = None


class HintRead(BaseModel):
    observation_id: str
    statement: str
    observed_on: date | None
    source_ref: str | None


class EntryRead(BaseModel):
    key: str
    #: `current_verified` · `current_declared` · `historic` · `unknown`.
    source: str
    accreditation_pending: bool
    claims: list[ClaimRead]
    #: El que diu l'historic. Nomes informa.
    hints: list[HintRead]


class PendingItemRead(BaseModel):
    kind: str
    subject: str
    key: str
    detail: str
    record_id: str | None
    observation_id: str | None
    superseded_by_current: bool


class KnowledgeSnapshotRead(BaseModel):
    company_id: str
    evaluated_on: date
    fingerprint: str
    certifications: list[EntryRead]
    facts: list[EntryRead]
    services: list[EntryRead]
    solutions: list[EntryRead]
    capabilities: list[EntryRead]
    products: list[EntryRead]
    partners: list[EntryRead]
    #: Que falta validar: dades declarades sense evidencia, en tramit, caducades i
    #: suggeriments de l'historic que ningu no ha revisat.
    pending_validation: list[PendingItemRead]


def _entry(entry: Entry) -> EntryRead:
    return EntryRead(
        key=entry.key,
        source=str(entry.source),
        accreditation_pending=entry.accreditation_pending,
        claims=[
            ClaimRead(
                record_id=c.record_id,
                source=str(c.source),
                value=c.value,
                as_of=c.as_of,
                valid_until=c.valid_until,
                accreditation_pending=c.accreditation_pending,
                scope_service_lines=list(c.scope) if c.scope is not None else None,
                scope_status=c.scope_status,
            )
            for c in entry.claims
        ],
        hints=[
            HintRead(
                observation_id=h.observation_id,
                statement=h.statement,
                observed_on=h.observed_on,
                source_ref=h.source_ref,
            )
            for h in entry.hints
        ],
    )


def snapshot_read(snapshot: KnowledgeSnapshot) -> KnowledgeSnapshotRead:
    return KnowledgeSnapshotRead(
        company_id=snapshot.company_id,
        evaluated_on=snapshot.evaluated_on,
        fingerprint=snapshot.fingerprint,
        certifications=[_entry(e) for e in snapshot.certifications.values()],
        facts=[_entry(e) for e in snapshot.facts.values()],
        services=[_entry(e) for e in snapshot.services.values()],
        solutions=[_entry(e) for e in snapshot.solutions.values()],
        capabilities=[_entry(e) for e in snapshot.capabilities.values()],
        products=[_entry(e) for e in snapshot.products.values()],
        partners=[_entry(e) for e in snapshot.partners.values()],
        pending_validation=[
            PendingItemRead(
                kind=str(p.kind),
                subject=p.subject,
                key=p.key,
                detail=p.detail,
                record_id=p.record_id,
                observation_id=p.observation_id,
                superseded_by_current=p.superseded_by_current,
            )
            for p in snapshot.pending_validation
        ],
    )


class ObservationRead(ORMModel):
    id: uuid.UUID
    historic_bid_id: uuid.UUID | None
    subject: ObservationSubject
    subject_key: str
    statement: str
    proposal: dict[str, Any]
    observed_on: date | None
    source_ref: str | None
    status: ObservationStatus
    reviewed_at: datetime | None
    review_note: str | None
    promoted_entity_type: str | None
    promoted_entity_id: uuid.UUID | None


class PromoteObservation(APIModel):
    note: str | None = Field(default=None, max_length=2000)


class DismissObservation(APIModel):
    note: str = Field(min_length=3, max_length=2000)


class VerifiedRecord(BaseModel):
    kind: str
    record_id: uuid.UUID
    verification_status: str
