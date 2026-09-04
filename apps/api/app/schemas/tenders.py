"""Esquemes del feed de licitacions.

Dades publiques: cap d'aquests objectes porta `company_id` ni depen del tenant.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.models.enums import (
    ContractType,
    IngestionStatus,
    ProcedureType,
    ProcessingStatus,
    TenderDocumentType,
    TenderStatus,
)
from app.schemas.common import ORMModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Pagina de resultats. Paginacio server-side per limit/offset.

    S'ha triat limit/offset i no cursor perque el feed s'ordena per camps que
    l'usuari pot canviar (data, import, termini) i perque cal poder saltar a
    una pagina concreta. Amb volums de milions caldra revisar-ho.
    """

    items: list[T]
    total: int
    limit: int
    offset: int


class BuyerSummary(ORMModel):
    id: uuid.UUID
    name: str
    tax_identifier: str | None = None
    city: str | None = None
    nuts_code: str | None = None


class BuyerRead(BuyerSummary):
    external_identifier: str
    registry_identifier: str | None = None
    buyer_type: str | None = None
    activity: str | None = None
    website: str | None = None
    buyer_profile_url: str | None = None
    address: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    hierarchy: str | None = None


class TenderCPVRead(ORMModel):
    cpv_code: str
    is_main: bool
    description: str | None = None


class TenderLotRead(ORMModel):
    id: uuid.UUID
    external_lot_id: str
    lot_number: int | None = None
    title: str | None = None
    description: str | None = None
    estimated_value: Decimal | None = None
    base_budget: Decimal | None = None
    base_budget_without_tax: Decimal | None = None
    currency: str
    place_of_execution: str | None = None
    nuts_code: str | None = None
    deadline_at: datetime | None = None
    cpvs: list[TenderCPVRead] = []


class TenderDocumentRead(ORMModel):
    id: uuid.UUID
    name: str
    document_type: TenderDocumentType
    source_document_type_code: str | None = None
    url: str
    mime_type: str | None = None
    published_at: date | None = None


class TenderListItem(ORMModel):
    """Fila del feed. Prou informacio per decidir si val la pena obrir la fitxa."""

    id: uuid.UUID
    external_id: str
    expediente_number: str | None = None
    title: str
    status: TenderStatus
    procedure_type: ProcedureType
    contract_type: ContractType
    estimated_value: Decimal | None = None
    base_budget: Decimal | None = None
    currency: str
    publication_date: date | None = None
    deadline_at: datetime | None = None
    nuts_code: str | None = None
    place_of_execution: str | None = None
    buyer: BuyerSummary | None = None
    cpvs: list[TenderCPVRead] = []
    lot_count: int = 0
    revision_count: int = 1
    source_code: str | None = None
    # True quan l'expedient ha canviat despres de la primera vegada que el vam veure.
    is_modified: bool = False


class TenderRevisionRead(ORMModel):
    """Una versio rebuda de l'expedient. Es el SourceRecord, no una taula a part."""

    id: uuid.UUID
    revision_number: int
    raw_hash: str
    raw_size: int
    fetched_at: datetime
    source_updated_at: datetime | None = None
    source_url: str | None = None
    parser_version: str
    processing_status: ProcessingStatus
    error_message: str | None = None


class TenderTraceability(BaseModel):
    """Zona tecnica de la fitxa: d'on surt aquesta dada."""

    source_code: str
    source_name: str
    external_id: str
    source_url: str | None = None
    latest_source_record_id: uuid.UUID | None = None
    fetched_at: datetime | None = None
    parser_version: str | None = None
    raw_hash: str | None = None


class TenderDetail(ORMModel):
    id: uuid.UUID
    external_id: str
    expediente_number: str | None = None
    title: str
    description: str | None = None
    status: TenderStatus
    source_status_code: str | None = None
    procedure_type: ProcedureType
    contract_type: ContractType
    estimated_value: Decimal | None = None
    base_budget: Decimal | None = None
    base_budget_without_tax: Decimal | None = None
    currency: str
    publication_date: date | None = None
    deadline_at: datetime | None = None
    place_of_execution: str | None = None
    nuts_code: str | None = None
    country_code: str | None = None
    source_url: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime | None = None
    revision_count: int
    buyer: BuyerRead | None = None
    cpvs: list[TenderCPVRead] = []
    lots: list[TenderLotRead] = []
    documents: list[TenderDocumentRead] = []
    traceability: TenderTraceability | None = None


class IngestionRunRead(ORMModel):
    """Auditoria d'una execucio d'ingesta."""

    id: uuid.UUID
    source_id: uuid.UUID
    status: IngestionStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    records_fetched: int
    records_new: int
    records_changed: int
    records_unchanged: int
    records_failed: int
    tenders_created: int
    tenders_updated: int
    checkpoint_before: str | None = None
    checkpoint_after: str | None = None
    parameters: str | None = None
    error_summary: str | None = None


class TenderFacets(BaseModel):
    """Valors disponibles per als filtres del feed, calculats sobre les dades reals."""

    statuses: list[str]
    procedure_types: list[str]
    contract_types: list[str]
    nuts_codes: list[str] = Field(default_factory=list)
    total_tenders: int
