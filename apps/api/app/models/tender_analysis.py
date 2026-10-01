"""Analisi d'un plec contra el coneixement actual d'una empresa (0051).

**Dades derivades, no font de veritat.** Tot el que hi ha aqui es reproduible a
partir de l'estructura del plec (0048-0050) i de la fotografia del coneixement
(fase 2) amb la versio de regles que la run fixa. Per aixo:

* una run es **immutable**: l'unica mutacio es el punter `is_current`;
* els resultats pengen de la run (CASCADE) i cauen amb ella;
* una run amb els mateixos inputs (`inputs_hash`) es la mateixa run: no es
  duplica, es reutilitza;
* hi ha com a maxim una run vigent per empresa i licitacio (index unic parcial).

Cap d'aquestes taules es cap recomanacio: diuen que se sap, que no, que queda
`unknown` i quins punts estan assegurats, en risc, perduts o sense avaluar.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
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
from app.models.knowledge import enum_column
from app.models.review import in_values


def _run_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _code() -> Mapped[str]:
    return mapped_column(sa.String(80), nullable=False)


class TenderAnalysisRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tender_analysis_runs"
    __table_args__ = (
        sa.UniqueConstraint(
            "company_id", "tender_id", "inputs_hash", name="uq_tender_analysis_runs_inputs"
        ),
        sa.Index(
            "uq_tender_analysis_runs_current",
            "company_id",
            "tender_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
        ),
        sa.Index("ix_tender_analysis_runs_scope", "company_id", "tender_id", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: Versions de tot el que s'ha llegit. Nuls quan la licitacio no en te.
    tender_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_revisions.id", ondelete="SET NULL")
    )
    extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirement_extraction_runs.id", ondelete="SET NULL"),
    )
    classification_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_classification_runs.id", ondelete="SET NULL"),
    )
    review_revision_hash: Mapped[str | None] = mapped_column(sa.String(64))
    tender_structure_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    knowledge_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    rules_version: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: SHA-256 d'EXACTAMENT els set inputs anteriors.
    inputs_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created_at()

    fit_assessments: Mapped[list[TenderFitAssessment]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="TenderFitAssessment.id"
    )
    need_assessments: Mapped[list[CapabilityNeedAssessment]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="CapabilityNeedAssessment.id"
    )
    criterion_assessments: Mapped[list[CriterionAssessment]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="CriterionAssessment.id"
    )
    delivery_candidates: Mapped[list[DeliveryCandidate]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="DeliveryCandidate.id"
    )
    findings: Mapped[list[AnalysisFinding]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AnalysisFinding.id"
    )
    questions: Mapped[list[AnalysisQuestion]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AnalysisQuestion.id"
    )


class TenderFitAssessment(UUIDPrimaryKeyMixin, Base):
    """Una fila per requisit analitzat. Cap text lliure: codis i `support_json`."""

    __tablename__ = "tender_fit_assessments"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "requirement_id", name="uq_tender_fit_assessments_req"),
        sa.CheckConstraint(in_values("fit_result", FitResult), name="fit_result_valid"),
        sa.CheckConstraint(
            in_values("knowledge_source", AnalysisKnowledgeSource), name="knowledge_source_valid"
        ),
        sa.CheckConstraint(in_values("proof_status", ProofStatus), name="proof_status_valid"),
        sa.CheckConstraint(
            in_values("territory_verdict", TerritoryVerdict), name="territory_verdict_valid"
        ),
        sa.CheckConstraint(
            in_values("verification_status", AssessmentVerification),
            name="verification_status_valid",
        ),
        sa.Index("ix_tender_fit_assessments_requirement", "requirement_id"),
    )

    run_id: Mapped[uuid.UUID] = _run_fk()
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    fit_result: Mapped[FitResult] = mapped_column(
        enum_column(FitResult, "fit_result"), nullable=False
    )
    knowledge_source: Mapped[AnalysisKnowledgeSource] = mapped_column(
        enum_column(AnalysisKnowledgeSource, "analysis_knowledge_source"), nullable=False
    )
    proof_status: Mapped[ProofStatus] = mapped_column(
        enum_column(ProofStatus, "proof_status"), nullable=False
    )
    territory_verdict: Mapped[TerritoryVerdict] = mapped_column(
        enum_column(TerritoryVerdict, "territory_verdict"), nullable=False
    )
    verification_status: Mapped[AssessmentVerification] = mapped_column(
        enum_column(AssessmentVerification, "assessment_verification"), nullable=False
    )
    rule_code: Mapped[str] = _code()
    explanation_code: Mapped[str] = _code()
    #: Nomes referencies i valors comparats: cap text generat.
    support_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON(none_as_null=True))
    created_at: Mapped[datetime] = _created_at()


class CapabilityNeedAssessment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "capability_need_assessments"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "capability_need_id", name="uq_capability_need_assess_need"),
        sa.CheckConstraint(in_values("fit_result", FitResult), name="fit_result_valid"),
        sa.CheckConstraint(
            in_values("knowledge_source", AnalysisKnowledgeSource), name="knowledge_source_valid"
        ),
        sa.CheckConstraint(
            in_values("verification_status", AssessmentVerification),
            name="verification_status_valid",
        ),
        sa.Index("ix_capability_need_assessments_need", "capability_need_id"),
    )

    run_id: Mapped[uuid.UUID] = _run_fk()
    capability_need_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirement_capability_needs.id", ondelete="CASCADE"),
        nullable=False,
    )
    fit_result: Mapped[FitResult] = mapped_column(
        enum_column(FitResult, "fit_result"), nullable=False
    )
    knowledge_source: Mapped[AnalysisKnowledgeSource] = mapped_column(
        enum_column(AnalysisKnowledgeSource, "analysis_knowledge_source"), nullable=False
    )
    verification_status: Mapped[AssessmentVerification] = mapped_column(
        enum_column(AssessmentVerification, "assessment_verification"), nullable=False
    )
    rule_code: Mapped[str] = _code()
    explanation_code: Mapped[str] = _code()
    support_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON(none_as_null=True))
    created_at: Mapped[datetime] = _created_at()


class CriterionAssessment(UUIDPrimaryKeyMixin, Base):
    """Una fila per criteri. Els punts es reparteixen en quatre cubells que sumen
    `max_points`: un criteri per trams pot quedar en mes d'un estat."""

    __tablename__ = "criterion_assessments"
    __table_args__ = (
        sa.UniqueConstraint("run_id", "award_criterion_id", name="uq_criterion_assessments_crit"),
        sa.CheckConstraint(
            in_values("criterion_result", CriterionResult), name="criterion_result_valid"
        ),
        sa.CheckConstraint(
            in_values("knowledge_source", AnalysisKnowledgeSource), name="knowledge_source_valid"
        ),
        sa.CheckConstraint(in_values("proof_status", ProofStatus), name="proof_status_valid"),
        sa.CheckConstraint(in_values("points_status", PointsStatus), name="points_status_valid"),
        sa.CheckConstraint(
            "max_points >= 0 AND secured_points >= 0 AND at_risk_points >= 0 "
            "AND lost_points >= 0 AND not_assessable_points >= 0",
            name="points_not_negative",
        ),
        sa.Index("ix_criterion_assessments_criterion", "award_criterion_id"),
    )

    run_id: Mapped[uuid.UUID] = _run_fk()
    award_criterion_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("award_criteria.id", ondelete="CASCADE"),
        nullable=False,
    )
    criterion_result: Mapped[CriterionResult] = mapped_column(
        enum_column(CriterionResult, "criterion_result"), nullable=False
    )
    knowledge_source: Mapped[AnalysisKnowledgeSource] = mapped_column(
        enum_column(AnalysisKnowledgeSource, "analysis_knowledge_source"), nullable=False
    )
    proof_status: Mapped[ProofStatus] = mapped_column(
        enum_column(ProofStatus, "proof_status"), nullable=False
    )
    points_status: Mapped[PointsStatus] = mapped_column(
        enum_column(PointsStatus, "points_status"), nullable=False
    )
    max_points: Mapped[float] = mapped_column(sa.Float, nullable=False)
    secured_points: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    at_risk_points: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    lost_points: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    not_assessable_points: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    rule_code: Mapped[str] = _code()
    explanation_code: Mapped[str] = _code()
    support_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON(none_as_null=True))
    created_at: Mapped[datetime] = _created_at()


class DeliveryCandidate(UUIDPrimaryKeyMixin, Base):
    """Qui podria prestar un element d'abast, nomes des dels serveis actuals."""

    __tablename__ = "delivery_candidates"
    __table_args__ = (
        sa.CheckConstraint(in_values("candidate_type", CandidateType), name="candidate_type_valid"),
        sa.CheckConstraint(in_values("origin", CandidateOrigin), name="origin_valid"),
        sa.CheckConstraint(in_values("readiness", CandidateReadiness), name="readiness_valid"),
        sa.CheckConstraint(
            in_values("territory_verdict", TerritoryVerdict), name="territory_verdict_valid"
        ),
        # `own_future` nomes pot ser una hipotesi; el coneixement actual mai en crea.
        sa.CheckConstraint(
            "candidate_type <> 'own_future' OR origin = 'hypothesis'",
            name="own_future_is_hypothesis",
        ),
        sa.Index("ix_delivery_candidates_scope_item", "scope_item_id"),
    )

    run_id: Mapped[uuid.UUID] = _run_fk()
    scope_item_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_scope_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_service_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("company_services.id", ondelete="SET NULL")
    )
    partner_role_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("partner_roles.id", ondelete="SET NULL")
    )
    candidate_type: Mapped[CandidateType] = mapped_column(
        enum_column(CandidateType, "candidate_type"), nullable=False
    )
    origin: Mapped[CandidateOrigin] = mapped_column(
        enum_column(CandidateOrigin, "candidate_origin"), nullable=False
    )
    readiness: Mapped[CandidateReadiness] = mapped_column(
        enum_column(CandidateReadiness, "candidate_readiness"), nullable=False
    )
    territory_verdict: Mapped[TerritoryVerdict] = mapped_column(
        enum_column(TerritoryVerdict, "territory_verdict"), nullable=False
    )
    area_verdicts_json: Mapped[list[Any] | None] = mapped_column(sa.JSON(none_as_null=True))
    rule_code: Mapped[str] = _code()
    explanation_code: Mapped[str] = _code()
    created_at: Mapped[datetime] = _created_at()


class AnalysisFinding(UUIDPrimaryKeyMixin, Base):
    """Una troballa amb codi tancat. Mai `blocker_confirmed`: aixo ho decideix algu."""

    __tablename__ = "analysis_findings"
    __table_args__ = (
        sa.CheckConstraint(in_values("finding_type", FindingType), name="finding_type_valid"),
        sa.Index("ix_analysis_findings_run_type", "run_id", "finding_type"),
    )

    run_id: Mapped[uuid.UUID] = _run_fk()
    finding_type: Mapped[FindingType] = mapped_column(
        enum_column(FindingType, "finding_type"), nullable=False
    )
    rule_code: Mapped[str] = _code()
    explanation_code: Mapped[str] = _code()
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="SET NULL")
    )
    criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("award_criteria.id", ondelete="SET NULL")
    )
    scope_item_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_scope_items.id", ondelete="SET NULL")
    )
    details_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON(none_as_null=True))
    created_at: Mapped[datetime] = _created_at()


class AnalysisQuestion(UUIDPrimaryKeyMixin, Base):
    """Que cal preguntar, a qui. El text surt del registre, no d'aqui."""

    __tablename__ = "analysis_questions"
    __table_args__ = (
        sa.CheckConstraint(in_values("audience", QuestionAudience), name="audience_valid"),
        # Deduplicacio determinista: la clau resumeix (audiencia, codi, ancores, params).
        sa.UniqueConstraint("run_id", "dedupe_key", name="uq_analysis_questions_dedupe"),
    )

    run_id: Mapped[uuid.UUID] = _run_fk()
    audience: Mapped[QuestionAudience] = mapped_column(
        enum_column(QuestionAudience, "question_audience"), nullable=False
    )
    question_code: Mapped[str] = _code()
    rule_code: Mapped[str] = _code()
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="SET NULL")
    )
    criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("award_criteria.id", ondelete="SET NULL")
    )
    scope_item_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_scope_items.id", ondelete="SET NULL")
    )
    params_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON(none_as_null=True))
    dedupe_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    created_at: Mapped[datetime] = _created_at()
