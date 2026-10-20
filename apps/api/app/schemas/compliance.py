"""Schemas de sortida del compliment.

Cada assessment surt amb la cadena sencera: la regla aplicada, el motiu
codificat, el valor que exigeix el plec, el valor que te l'empresa i les dues
evidencies (la del plec i la corporativa). Un resultat sense aixo no es
auditable, i un resultat que no es pot auditar no serveix per decidir si es
presenta una oferta.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    BlockerStatus,
    CompanyEvidenceKind,
    ComplianceRunStatus,
    EligibilityResult,
    EvidenceStrength,
    RecommendationResult,
    RequirementCategory,
    RequirementObligation,
    ReviewStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CompanyEvidenceRead(ORMModel):
    id: uuid.UUID
    entity_type: CompanyEvidenceKind
    entity_id: uuid.UUID
    document_id: uuid.UUID | None = None
    label: str
    strength: EvidenceStrength


class CitationRead(BaseModel):
    """Una cita verificada del plec d'un requisit."""

    evidence_id: uuid.UUID
    tender_document_id: uuid.UUID | None = None
    extraction_id: uuid.UUID | None = None
    extraction_revision: int | None = None
    page_number: int | None = None
    quote: str
    document_name: str | None = None


class RequirementSourceRead(BaseModel):
    """La cita del plec, per poder saltar-hi des de l'assessment."""

    requirement_id: uuid.UUID
    title: str
    category: RequirementCategory
    review_status: ReviewStatus
    tender_document_id: uuid.UUID | None = None
    extraction_id: uuid.UUID | None = None
    extraction_revision: int | None = None
    page_number: int | None = None
    quote: str | None = None
    document_name: str | None = None
    #: TOTES les cites del requisit (la primera es la de dalt). Un grup de
    #: duplicats les ensenya totes: cap cita no es perd per consolidar.
    citations: list[CitationRead] = Field(default_factory=list)


class ConsolidationRead(BaseModel):
    """A quin grup d'exigencia pertany l'avaluacio (`app.compliance.consolidation`)."""

    canonical_requirement_id: uuid.UUID
    is_canonical: bool
    member_requirement_ids: list[uuid.UUID]
    divergent: bool = False


class AssessmentGroupRead(BaseModel):
    """Una exigencia: el canonic i els seus duplicats, resolts com al recompte."""

    canonical_assessment_id: uuid.UUID
    canonical_requirement_id: uuid.UUID
    member_assessment_ids: list[uuid.UUID]
    member_requirement_ids: list[uuid.UUID]
    #: El resultat del grup (el mes prudent dels membres), com al recompte del run.
    effective_result: AssessmentResult
    blocker_status: BlockerStatus
    divergent: bool = False


class AssessmentRead(ORMModel):
    id: uuid.UUID
    assessment_run_id: uuid.UUID
    requirement_id: uuid.UUID
    tender_id: uuid.UUID
    tender_lot_id: uuid.UUID | None = None

    #: El que va decidir el motor. No es toca mai des de la revisio.
    automatic_result: AssessmentResult
    #: El que ha dit una persona, si algu ho ha revisat.
    reviewed_result: AssessmentResult | None = None
    #: El que val: la revisio si existeix, si no el calcul.
    effective_result: AssessmentResult
    assessment_mode: AssessmentMode

    rule_code: str
    explanation_code: str
    explanation_params: dict[str, Any] | None = None
    company_value_json: dict[str, Any] | None = None
    requirement_value_json: dict[str, Any] | None = None

    obligation: RequirementObligation
    blocker_status: BlockerStatus
    evidence_strength: EvidenceStrength

    review_status: ReviewStatus
    review_note: str | None = None
    reviewed_at: datetime | None = None
    #: Qui va revisar. Un veredicte huma sense signatura no es un veredicte
    #: huma: es un resultat que ha canviat i ningu sap per obra de qui.
    reviewed_by: uuid.UUID | None = None
    reviewed_by_name: str | None = None
    calculated_at: datetime

    evidence: list[CompanyEvidenceRead] = Field(default_factory=list)
    requirement: RequirementSourceRead | None = None
    #: Nomes si el requisit te duplicats de la mateixa exigencia.
    consolidation: ConsolidationRead | None = None


class ComplianceRunRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    tender_id: uuid.UUID
    requirement_extraction_run_id: uuid.UUID | None = None
    status: ComplianceRunStatus
    is_current: bool

    rules_version: str
    company_profile_fingerprint: str
    requirements_fingerprint: str

    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None

    requirements_total: int
    mandatory_total: int
    satisfies_count: int
    does_not_satisfy_count: int
    unknown_count: int
    needs_review_count: int
    not_applicable_count: int
    potential_blockers_count: int
    confirmed_blockers_count: int

    #: "Podem acreditar que complim?" Mai es barreja amb el pre-fit.
    overall_result: EligibilityResult
    recommendation: RecommendationResult
    #: Pre-fit del moment del calcul. Es mostra al costat, no fusionat.
    prefit_score: int | None = None

    error_code: str | None = None
    error_message: str | None = None


class ComplianceRequest(BaseModel):
    force: bool = False
    tender_lot_id: uuid.UUID | None = None


class ComplianceResult(BaseModel):
    reused: bool
    run: ComplianceRunRead


class ComplianceRead(BaseModel):
    """El que necessita la fitxa per pintar la seccio sencera."""

    run: ComplianceRunRead | None = None
    assessments: list[AssessmentRead] = Field(default_factory=list)
    #: Una entrada per EXIGENCIA amb mes d'un requisit (duplicats). Els requisits
    #: sols no hi son: la seva avaluacio ja es l'exigencia.
    groups: list[AssessmentGroupRead] = Field(default_factory=list)
    #: El perfil o els requisits han canviat des del calcul.
    is_stale: bool = False


class AssessmentReviewRequest(BaseModel):
    """Revisio humana. NO sobreescriu `automatic_result`."""

    review_status: ReviewStatus
    #: Correccio del veredicte. `None` = confirmar el que deia el motor.
    reviewed_result: AssessmentResult | None = None
    note: str | None = None
