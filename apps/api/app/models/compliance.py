"""Compliment: que exigeix el plec contra que pot acreditar l'empresa.

A diferencia de 4A/4B, **aixo SI que es dada de tenant**: porta `company_id` i
queda sotmes a l'aillament multi-tenant. El requisit es public i global; la
resposta a "ho acredito?" nomes te sentit per a una empresa concreta.

    Company + Requirement -> CompanyRequirementAssessment -> AssessmentEvidence
                          -> CompanyComplianceRun (agregat)

Tres decisions que sostenen tota la capa:

* **No es guarda `complies = true`.** Es guarda per que: la regla aplicada, el
  codi d'explicacio, el valor del plec, el valor de l'empresa i les evidencies
  corporatives. Un booleà sense motiu no es auditable.
* **La revisio no sobreescriu el calcul.** `automatic_result` i
  `reviewed_result` conviuen; l'efectiu es el segon si existeix. Aixi es pot
  saber sempre si el motor encertava.
* **Un blocador no confirmat no es un NO-GO.** Mentre l'extractor no estigui
  mesurat, un requisit automatic no revisat nomes genera `POTENTIAL`.

Aqui NO es decideix res sobre criteris d'adjudicacio: puntuar no es acreditar.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CompanyOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    BlockerStatus,
    CompanyEvidenceKind,
    ComplianceRunStatus,
    EligibilityResult,
    EvidenceStrength,
    RecommendationResult,
    RequirementObligation,
    ReviewStatus,
)

_COMPLIANCE_IN_FLIGHT = "status IN ('pending', 'running')"


class CompanyComplianceRun(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """Un calcul de compliment d'una empresa sobre una licitacio. **Immutable.**

    Els dos fingerprints diuen contra que es va calcular: el perfil corporatiu i
    els requisits. Si qualsevol dels dos canvia, el run queda obsolet i cal
    recalcular. Els resultats antics NO s'actualitzen en silenci: continuen sent
    la fotografia del que se sabia aquell dia.
    """

    __tablename__ = "company_compliance_runs"
    __table_args__ = (
        sa.Index("ix_company_compliance_runs_company_tender", "company_id", "tender_id"),
        sa.Index(
            "uq_company_compliance_runs_current",
            "company_id",
            "tender_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
        ),
        sa.Index(
            "uq_company_compliance_runs_in_flight",
            "company_id",
            "tender_id",
            unique=True,
            postgresql_where=sa.text(_COMPLIANCE_IN_FLIGHT),
            sqlite_where=sa.text(_COMPLIANCE_IN_FLIGHT),
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: Quina extraccio de requisits s'ha avaluat. Una de nova obliga a recalcular.
    requirement_extraction_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirement_extraction_runs.id", ondelete="SET NULL"),
    )
    status: Mapped[ComplianceRunStatus] = mapped_column(
        enum_column(ComplianceRunStatus, "compliance_run_status"),
        nullable=False,
        default=ComplianceRunStatus.PENDING,
    )

    rules_version: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    #: SHA-256 de les dades del perfil que poden alterar un compliment.
    company_profile_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    requirements_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    requirements_total: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    mandatory_total: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    satisfies_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    does_not_satisfy_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    unknown_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    needs_review_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    not_applicable_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    potential_blockers_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    confirmed_blockers_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: "Podem acreditar que complim?" Mai es barreja amb el pre-fit.
    overall_result: Mapped[EligibilityResult] = mapped_column(
        enum_column(EligibilityResult, "eligibility_result"),
        nullable=False,
        default=EligibilityResult.REVIEW_REQUIRED,
    )
    recommendation: Mapped[RecommendationResult] = mapped_column(
        enum_column(RecommendationResult, "recommendation_result"),
        nullable=False,
        default=RecommendationResult.REVIEW,
    )
    #: Pre-fit vigent quan es va calcular. Es guarda per traçabilitat, no es
    #: fusiona amb l'elegibilitat.
    prefit_score: Mapped[int | None] = mapped_column(sa.Integer)

    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(sa.String(50))
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    assessments: Mapped[list[CompanyRequirementAssessment]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CompanyComplianceRun {self.overall_result} {self.status}>"


class CompanyRequirementAssessment(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """El veredicte sobre UN requisit per a UNA empresa, amb el seu perque."""

    __tablename__ = "company_requirement_assessments"
    __table_args__ = (
        sa.UniqueConstraint(
            "assessment_run_id",
            "requirement_id",
            name="uq_company_requirement_assessments_run_requirement",
        ),
        sa.Index("ix_company_requirement_assessments_run", "assessment_run_id"),
        sa.Index("ix_company_requirement_assessments_company_tender", "company_id", "tender_id"),
        sa.Index("ix_company_requirement_assessments_requirement", "requirement_id"),
    )

    assessment_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_compliance_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    tender_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="SET NULL")
    )

    #: El que ha decidit el motor. NO es toca mai des de la revisio.
    automatic_result: Mapped[AssessmentResult] = mapped_column(
        enum_column(AssessmentResult, "assessment_result"), nullable=False
    )
    #: El que ha dit una persona. `None` mentre ningu no ho hagi revisat.
    reviewed_result: Mapped[AssessmentResult | None] = mapped_column(
        enum_column(AssessmentResult, "assessment_result_reviewed")
    )
    assessment_mode: Mapped[AssessmentMode] = mapped_column(
        enum_column(AssessmentMode, "assessment_mode"),
        nullable=False,
        default=AssessmentMode.AUTOMATIC,
    )

    #: Identificador estable de la regla, versionat. Fa el resultat auditable.
    rule_code: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    #: Codi tancat; el text de la interficie se'n deriva.
    explanation_code: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    explanation_params: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)

    company_value_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    requirement_value_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)

    #: Copia de l'obligatorietat del requisit en el moment del calcul.
    obligation: Mapped[RequirementObligation] = mapped_column(
        enum_column(RequirementObligation, "requirement_obligation"), nullable=False
    )
    blocker_status: Mapped[BlockerStatus] = mapped_column(
        enum_column(BlockerStatus, "blocker_status"), nullable=False, default=BlockerStatus.NONE
    )
    evidence_strength: Mapped[EvidenceStrength] = mapped_column(
        enum_column(EvidenceStrength, "evidence_strength"),
        nullable=False,
        default=EvidenceStrength.MISSING,
    )

    review_status: Mapped[ReviewStatus] = mapped_column(
        enum_column(ReviewStatus, "review_status"), nullable=False, default=ReviewStatus.UNREVIEWED
    )
    review_note: Mapped[str | None] = mapped_column(sa.Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    calculated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    run: Mapped[CompanyComplianceRun] = relationship(back_populates="assessments")
    evidence: Mapped[list[AssessmentEvidence]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def effective_result(self) -> AssessmentResult:
        """El que val: la revisio humana si n'hi ha, si no el calcul."""
        return self.reviewed_result or self.automatic_result

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Assessment {self.rule_code} {self.effective_result}>"


class AssessmentEvidence(UUIDPrimaryKeyMixin, CompanyOwnedMixin, Base):
    """Quina dada corporativa sosté el veredicte.

    Es guarda el tipus i l'identificador com a columnes, no dins d'un text: si
    algun dia cal saber quines avaluacions depenen d'una certificacio concreta,
    ha de ser una consulta, no una cerca de cadenes.
    """

    __tablename__ = "assessment_evidence"
    __table_args__ = (
        sa.Index("ix_assessment_evidence_assessment", "assessment_id"),
        sa.Index("ix_assessment_evidence_entity", "entity_type", "entity_id"),
    )

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_requirement_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[CompanyEvidenceKind] = mapped_column(
        enum_column(CompanyEvidenceKind, "company_evidence_kind"), nullable=False
    )
    #: Sense clau forana: apunta a taules diferents segons `entity_type`.
    entity_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    #: Document acreditatiu, si la dada en te.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL")
    )
    label: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    strength: Mapped[EvidenceStrength] = mapped_column(
        enum_column(EvidenceStrength, "evidence_strength"),
        nullable=False,
        default=EvidenceStrength.MISSING,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    assessment: Mapped[CompanyRequirementAssessment] = relationship(back_populates="evidence")
