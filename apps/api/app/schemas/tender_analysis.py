"""Lectura d'una analisi (0051). Res s'edita per aqui: son dades derivades."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import (
    AnalysisKnowledgeSource,
    AssessmentVerification,
    CandidateOrigin,
    CandidateReadiness,
    CandidateType,
    CriterionResult,
    FindingType,
    FitResult,
    PointsStatus,
    ProofStatus,
    QuestionAudience,
    TerritoryVerdict,
)
from app.schemas.common import ORMModel


class FitAssessmentRead(ORMModel):
    id: uuid.UUID
    requirement_id: uuid.UUID
    fit_result: FitResult
    knowledge_source: AnalysisKnowledgeSource
    proof_status: ProofStatus
    territory_verdict: TerritoryVerdict
    verification_status: AssessmentVerification
    rule_code: str
    explanation_code: str
    support_json: dict[str, Any] | None = None


class NeedAssessmentRead(ORMModel):
    id: uuid.UUID
    capability_need_id: uuid.UUID
    fit_result: FitResult
    knowledge_source: AnalysisKnowledgeSource
    verification_status: AssessmentVerification
    rule_code: str
    explanation_code: str
    support_json: dict[str, Any] | None = None


class CriterionAssessmentRead(ORMModel):
    id: uuid.UUID
    award_criterion_id: uuid.UUID
    criterion_result: CriterionResult
    knowledge_source: AnalysisKnowledgeSource
    proof_status: ProofStatus
    points_status: PointsStatus
    max_points: float
    secured_points: float
    at_risk_points: float
    lost_points: float
    not_assessable_points: float
    rule_code: str
    explanation_code: str
    support_json: dict[str, Any] | None = None


class DeliveryCandidateRead(ORMModel):
    id: uuid.UUID
    scope_item_id: uuid.UUID
    company_service_id: uuid.UUID | None = None
    partner_role_id: uuid.UUID | None = None
    candidate_type: CandidateType
    origin: CandidateOrigin
    readiness: CandidateReadiness
    territory_verdict: TerritoryVerdict
    area_verdicts_json: list[Any] | None = None
    rule_code: str
    explanation_code: str


class FindingRead(ORMModel):
    id: uuid.UUID
    finding_type: FindingType
    rule_code: str
    explanation_code: str
    requirement_id: uuid.UUID | None = None
    criterion_id: uuid.UUID | None = None
    scope_item_id: uuid.UUID | None = None
    details_json: dict[str, Any] | None = None


class QuestionRead(ORMModel):
    id: uuid.UUID
    audience: QuestionAudience
    question_code: str
    rule_code: str
    requirement_id: uuid.UUID | None = None
    criterion_id: uuid.UUID | None = None
    scope_item_id: uuid.UUID | None = None
    params_json: dict[str, Any] | None = None
    #: Derivat de la plantilla del registre; cap LLM.
    text: str = ""


class AnalysisRunRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    tender_id: uuid.UUID
    tender_revision_id: uuid.UUID | None = None
    extraction_run_id: uuid.UUID | None = None
    classification_run_id: uuid.UUID | None = None
    review_revision_hash: str | None = None
    tender_structure_fingerprint: str
    knowledge_fingerprint: str
    rules_version: str
    inputs_hash: str
    is_current: bool
    created_at: datetime
    #: Vectors factuals; cap puntuacio global ni recomanacio.
    summary: dict[str, Any] = Field(default_factory=dict)
    stale: bool | None = None
    fit_assessments: list[FitAssessmentRead] = Field(default_factory=list)
    need_assessments: list[NeedAssessmentRead] = Field(default_factory=list)
    criterion_assessments: list[CriterionAssessmentRead] = Field(default_factory=list)
    delivery_candidates: list[DeliveryCandidateRead] = Field(default_factory=list)
    findings: list[FindingRead] = Field(default_factory=list)
    questions: list[QuestionRead] = Field(default_factory=list)
