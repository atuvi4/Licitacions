"""Requisits extrets dels plecs, amb l'evidencia que els sosté.

Aquesta capa respon una sola pregunta: **que exigeix el plec?** No respon si
l'empresa ho compleix; aixo necessita el perfil corporatiu i es una altra
iteracio. Per tant, com la capa documental, tot aixo es PUBLIC i GLOBAL: cap
taula porta `company_id`. Si cent empreses miren la mateixa licitacio, el plec
s'analitza una vegada.

    Tender -> RequirementExtractionRun -> Requirement -> RequirementEvidence
                                       -> AwardCriterion -> AwardCriterionEvidence

La cadena d'evidencia acaba sempre en una `TenderDocumentPage` concreta d'una
`TenderDocumentExtraction` concreta. Com que totes dues son immutables (4A.1),
una cita feta avui continuara dient el mateix demà encara que el document es
torni a extreure.

Regla dura: un requisit sense evidencia validada no es persisteix com a
requisit. Nomes compta com a candidat rebutjat a les metriques del Run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
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

# Mateixa tecnica que a la capa documental: l'index unic parcial fa de lock de
# concurrencia sense Redis ni cua.
_ANALYSIS_IN_FLIGHT = "status IN ('pending', 'running')"


class RequirementExtractionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una passada d'extraccio de requisits sobre una licitacio. **Immutable.**

    Un run completat no es sobreescriu mai: tornar a analitzar crea un run nou.
    Nomes el nou passa a `is_current`, i nomes si ha acabat be. Si falla, la
    analisi anterior segueix sent la vigent i els seus requisits no es toquen.

    `input_fingerprint` identifica exactament quins textos s'han analitzat. Amb
    el mateix fingerprint i la mateixa configuracio (extractor, prompt, schema,
    provider i model) no cal tornar a gastar tokens.
    """

    __tablename__ = "requirement_extraction_runs"
    __table_args__ = (
        sa.Index("ix_requirement_extraction_runs_tender_id", "tender_id"),
        sa.Index(
            "uq_requirement_extraction_runs_current",
            "tender_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
        ),
        sa.Index(
            "uq_requirement_extraction_runs_in_flight",
            "tender_id",
            unique=True,
            postgresql_where=sa.text(_ANALYSIS_IN_FLIGHT),
            sqlite_where=sa.text(_ANALYSIS_IN_FLIGHT),
        ),
        # La cache es busca per aquesta combinacio.
        sa.Index(
            "ix_requirement_extraction_runs_cache",
            "tender_id",
            "input_fingerprint",
            "extractor_version",
            "prompt_version",
            "schema_version",
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[AnalysisRunStatus] = mapped_column(
        enum_column(AnalysisRunStatus, "analysis_run_status"),
        nullable=False,
        default=AnalysisRunStatus.PENDING,
    )

    #: SHA-256 de les extraccions usades. Determinista: mateixos textos, mateix valor.
    input_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    prompt_version: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    schema_version: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(120), nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    documents_analyzed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    pages_analyzed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    chunks_total: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    chunks_completed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    chunks_failed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    requirements_extracted: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Candidats descartats: sense evidencia localitzable, duplicats o invalids.
    requirements_rejected: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    award_criteria_extracted: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    validation_errors: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    evidence_rejected: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    input_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    output_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    estimated_cost: Mapped[float | None] = mapped_column(sa.Float)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    #: Identificadors de peticio del proveidor i notes de diagnostic. Mai claus
    #: d'API ni el text dels plecs.
    provider_metadata: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    #: Documents que no s'han pogut analitzar (OLE2, ZIP, sense text...).
    skipped_documents: Mapped[list[Any] | None] = mapped_column(sa.JSON)

    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(sa.String(50))
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    award_criteria: Mapped[list[AwardCriterion]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RequirementExtractionRun {self.status} {self.input_fingerprint[:12]}>"


class Requirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una exigencia del plec, estructurada i amb evidencia.

    `obligation = MANDATORY` NO vol dir "no-go": nomes vol dir que el plec ho
    exigeix. Convertir aixo en un blocador requereix comparar-ho amb l'empresa,
    i aixo es la iteracio seguent. Per aixo aqui no hi ha cap `hard_blocker`.

    `value_json` guarda el valor estructurat segons la categoria (import i
    moneda, anys de mirada enrere, nom de la certificacio...). No s'hi busca una
    ontologia perfecta: es un objecte validat per Pydantic quan la categoria ho
    permet, i lliure quan no.
    """

    __tablename__ = "requirements"
    __table_args__ = (
        sa.Index("ix_requirements_run_id", "run_id"),
        sa.Index("ix_requirements_tender_id", "tender_id"),
        sa.Index("ix_requirements_category", "category"),
        # Deduplicacio determinista dins d'un run: la mateixa clau canonica no
        # s'hi pot repetir. Es calcula al servei, no la decideix el model.
        sa.UniqueConstraint("run_id", "dedup_key", name="uq_requirements_run_dedup"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirement_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: Nomes s'informa si el mapping amb el lot es inequivoc. Mai s'inventa.
    tender_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="SET NULL")
    )

    category: Mapped[RequirementCategory] = mapped_column(
        enum_column(RequirementCategory, "requirement_category"), nullable=False
    )
    obligation: Mapped[RequirementObligation] = mapped_column(
        enum_column(RequirementObligation, "requirement_obligation"),
        nullable=False,
        default=RequirementObligation.UNKNOWN,
    )
    operator: Mapped[RequirementOperator | None] = mapped_column(
        enum_column(RequirementOperator, "requirement_operator")
    )
    scope: Mapped[RequirementScope] = mapped_column(
        enum_column(RequirementScope, "requirement_scope"),
        nullable=False,
        default=RequirementScope.UNKNOWN,
    )

    #: Text per a la interficie. NO es l'evidencia: la font de veritat es
    #: `RequirementEvidence`.
    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    value_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    #: Etiquetes de lot tal com les ha dit el plec, quan no s'han pogut resoldre.
    raw_lot_labels: Mapped[list[Any] | None] = mapped_column(sa.JSON)

    confidence: Mapped[ExtractionConfidence] = mapped_column(
        enum_column(ExtractionConfidence, "extraction_confidence"),
        nullable=False,
        default=ExtractionConfidence.LOW,
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        enum_column(ReviewStatus, "review_status"),
        nullable=False,
        default=ReviewStatus.UNREVIEWED,
    )
    #: Correccio humana. NO sobreescriu mai el que va dir el modelu: es guarda a
    #: banda per no perdre la provenance.
    manual_override_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    review_note: Mapped[str | None] = mapped_column(sa.Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: Dos documents diuen coses diferents sobre el mateix. No es resol sol.
    has_conflict: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    conflict_group: Mapped[str | None] = mapped_column(sa.String(64))

    #: Clau canonica de deduplicacio, calculada pel backend.
    dedup_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Quin chunk l'ha produit. Util per diagnosticar l'extractor.
    source_chunk_index: Mapped[int | None] = mapped_column(sa.Integer)

    run: Mapped[RequirementExtractionRun] = relationship(back_populates="requirements")
    evidence: Mapped[list[RequirementEvidence]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Requirement {self.category} {self.title[:40]}>"


class RequirementEvidence(UUIDPrimaryKeyMixin, Base):
    """On ho diu el plec, exactament.

    `extraction_id` i `page_id` apunten a files immutables, aixi que la cita no
    canvia encara que el document es torni a extreure. `char_start`/`char_end`
    els calcula el backend localitzant el quote dins del text de la pagina: el
    model no els inventa.
    """

    __tablename__ = "requirement_evidence"
    __table_args__ = (
        sa.Index("ix_requirement_evidence_requirement_id", "requirement_id"),
        sa.Index("ix_requirement_evidence_page_id", "page_id"),
        sa.CheckConstraint("page_number >= 1", name="evidence_page_number_is_one_based"),
    )

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_extractions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_pages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tender_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_documents.id", ondelete="SET NULL")
    )
    #: 1-based, com arreu.
    page_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    quote: Mapped[str] = mapped_column(sa.Text, nullable=False)
    char_start: Mapped[int | None] = mapped_column(sa.Integer)
    char_end: Mapped[int | None] = mapped_column(sa.Integer)
    section: Mapped[str | None] = mapped_column(sa.String(200))
    evidence_role: Mapped[EvidenceRole] = mapped_column(
        enum_column(EvidenceRole, "evidence_role"), nullable=False, default=EvidenceRole.PRIMARY
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    requirement: Mapped[Requirement] = relationship(back_populates="evidence")


class AwardCriterion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un criteri de puntuacio del plec.

    Si el plec no dona la formula, `formula` es queda a NULL. No es demana al
    model que n'inventi una de "habitual": aixo seria interpretacio, no
    extraccio.
    """

    __tablename__ = "award_criteria"
    __table_args__ = (
        sa.Index("ix_award_criteria_run_id", "run_id"),
        sa.Index("ix_award_criteria_tender_id", "tender_id"),
        sa.UniqueConstraint("run_id", "dedup_key", name="uq_award_criteria_run_dedup"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirement_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    tender_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="SET NULL")
    )

    category: Mapped[str | None] = mapped_column(sa.String(100))
    name: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    points: Mapped[float | None] = mapped_column(sa.Float)
    weight_percent: Mapped[float | None] = mapped_column(sa.Float)
    formula: Mapped[str | None] = mapped_column(sa.Text)
    evaluation_type: Mapped[AwardEvaluationType] = mapped_column(
        enum_column(AwardEvaluationType, "award_evaluation_type"),
        nullable=False,
        default=AwardEvaluationType.UNKNOWN,
    )
    confidence: Mapped[ExtractionConfidence] = mapped_column(
        enum_column(ExtractionConfidence, "extraction_confidence"),
        nullable=False,
        default=ExtractionConfidence.LOW,
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        enum_column(ReviewStatus, "review_status"),
        nullable=False,
        default=ReviewStatus.UNREVIEWED,
    )
    manual_override_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    review_note: Mapped[str | None] = mapped_column(sa.Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    dedup_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    source_chunk_index: Mapped[int | None] = mapped_column(sa.Integer)

    run: Mapped[RequirementExtractionRun] = relationship(back_populates="award_criteria")
    evidence: Mapped[list[AwardCriterionEvidence]] = relationship(
        back_populates="criterion", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AwardCriterion {self.name[:40]} {self.points}>"


class AwardCriterionEvidence(UUIDPrimaryKeyMixin, Base):
    """Mateixa disciplina de cita que `RequirementEvidence`.

    Es una taula propia i no una d'evidencia polimorfica perque aixi la clau
    forana continua sent real: una evidencia orfe no pot existir.
    """

    __tablename__ = "award_criterion_evidence"
    __table_args__ = (
        sa.Index("ix_award_criterion_evidence_criterion_id", "award_criterion_id"),
        sa.CheckConstraint("page_number >= 1", name="award_evidence_page_number_is_one_based"),
    )

    award_criterion_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("award_criteria.id", ondelete="CASCADE"),
        nullable=False,
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_extractions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_pages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tender_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_documents.id", ondelete="SET NULL")
    )
    page_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    quote: Mapped[str] = mapped_column(sa.Text, nullable=False)
    char_start: Mapped[int | None] = mapped_column(sa.Integer)
    char_end: Mapped[int | None] = mapped_column(sa.Integer)
    section: Mapped[str | None] = mapped_column(sa.String(200))
    evidence_role: Mapped[EvidenceRole] = mapped_column(
        enum_column(EvidenceRole, "evidence_role"), nullable=False, default=EvidenceRole.PRIMARY
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    criterion: Mapped[AwardCriterion] = relationship(back_populates="evidence")
