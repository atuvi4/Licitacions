"""Schemas de sortida de l'analisi de plecs.

Regla que travessa tot el fitxer: cada requisit visible arriba amb la seva
cadena de provenance sencera (run, extraccio, revisio, pagina i cita). Si algun
dia algu vol amagar-la per fer la resposta mes lleugera, aquest comentari diu
que no: sense provenance, un requisit extret automaticament no es una
afirmacio, es una opinio.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AnalysisRunStatus,
    AwardEvaluationType,
    EvidenceRole,
    ExtractionConfidence,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
    ReviewStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EvidenceRead(ORMModel):
    id: uuid.UUID
    extraction_id: uuid.UUID
    page_id: uuid.UUID
    tender_document_id: uuid.UUID | None = None
    #: 1-based, com arreu del sistema.
    page_number: int
    quote: str
    char_start: int | None = None
    char_end: int | None = None
    section: str | None = None
    evidence_role: EvidenceRole
    #: Revisio de l'extraccio citada. La interficie hi ha d'anar exactament,
    #: no a l'extraccio vigent: la cita es immutable.
    extraction_revision: int | None = None
    document_name: str | None = None


class RequirementRead(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID
    tender_id: uuid.UUID
    tender_lot_id: uuid.UUID | None = None
    category: RequirementCategory
    obligation: RequirementObligation
    operator: RequirementOperator | None = None
    scope: RequirementScope
    title: str
    description: str | None = None
    value_json: dict[str, Any] | None = None
    raw_lot_labels: list[Any] | None = None
    confidence: ExtractionConfidence
    review_status: ReviewStatus
    review_note: str | None = None
    manual_override_json: dict[str, Any] | None = None
    reviewed_at: datetime | None = None
    has_conflict: bool
    conflict_group: str | None = None
    created_at: datetime
    evidence: list[EvidenceRead] = Field(default_factory=list)


class AwardCriterionRead(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID
    tender_id: uuid.UUID
    tender_lot_id: uuid.UUID | None = None
    category: str | None = None
    name: str
    description: str | None = None
    points: float | None = None
    weight_percent: float | None = None
    #: NULL quan el plec no la dona. No se n'inventa cap.
    formula: str | None = None
    evaluation_type: AwardEvaluationType
    confidence: ExtractionConfidence
    review_status: ReviewStatus
    review_note: str | None = None
    manual_override_json: dict[str, Any] | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    evidence: list[EvidenceRead] = Field(default_factory=list)


class ExtractionRunRead(ORMModel):
    """Metriques i procedencia d'una passada d'analisi."""

    id: uuid.UUID
    tender_id: uuid.UUID
    status: AnalysisRunStatus
    is_current: bool
    input_fingerprint: str
    extractor_version: str
    prompt_version: str
    schema_version: str
    provider: str
    model: str
    started_at: datetime
    completed_at: datetime | None = None

    documents_analyzed: int
    pages_analyzed: int
    chunks_total: int
    chunks_completed: int
    chunks_failed: int
    requirements_extracted: int
    requirements_rejected: int
    award_criteria_extracted: int
    evidence_rejected: int
    validation_errors: int

    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    duration_ms: int | None = None
    #: Documents que no s'han pogut analitzar, amb el motiu. Mai s'inventa el
    #: seu contingut.
    skipped_documents: list[Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


class AnalysisRequest(BaseModel):
    #: Ignora la cache i llanca una passada nova.
    force: bool = False
    #: Seleccio manual. Buida = seleccio automatica (PCAP, PPT i annexos).
    document_ids: list[uuid.UUID] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    #: Cert si s'ha reutilitzat un run anterior sense gastar tokens.
    reused: bool
    run: ExtractionRunRead


class RequirementAnalysisRead(BaseModel):
    """El que necessita la fitxa per pintar la seccio sencera."""

    run: ExtractionRunRead | None = None
    requirements: list[RequirementRead] = Field(default_factory=list)
    award_criteria: list[AwardCriterionRead] = Field(default_factory=list)
    #: Cert si els documents han canviat des de l'analisi vigent.
    is_stale: bool = False


class ReviewRequest(BaseModel):
    """Revisio humana. NO sobreescriu l'output del model.

    `manual_override` es guarda a banda precisament per no perdre que va dir
    l'extractor: sense aixo no es podria auditar mai si el model encertava.
    """

    review_status: ReviewStatus
    note: str | None = None
    manual_override: dict[str, Any] | None = None
