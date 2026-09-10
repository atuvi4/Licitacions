"""Schemas del workspace de candidatura.

La regla de tota aquesta iteracio es visible a `WorkspaceDetail`: `prefit`,
`eligibility`, `recommendation` i `human_decision` son QUATRE camps diferents.
No hi ha cap camp que els resumeixi, perque resumir-los seria decidir per
l'usuari.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AssessmentResult,
    ChecklistItemSource,
    ChecklistItemStatus,
    CompanyEvidenceKind,
    DocumentCategory,
    DocumentValidity,
    DossierCoverage,
    EligibilityResult,
    EvidenceItemSource,
    EvidenceItemStatus,
    RecommendationResult,
    RequirementCategory,
    WorkspaceDecisionType,
    WorkspaceReadiness,
    WorkspaceStatus,
    WorkspaceTaskStatus,
)
from app.schemas.tenders import TenderChangeEventRead


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Entrada                                                                       #
# --------------------------------------------------------------------------- #


class WorkspaceCreateRequest(BaseModel):
    tender_lot_id: uuid.UUID | None = None


class DecisionRequest(BaseModel):
    decision: WorkspaceDecisionType
    #: Obligatori si hi ha avisos oberts; el servei ho comprova i retorna
    #: `decision_reason_required` amb la llista de codis.
    reason: str | None = Field(default=None, max_length=4000)


class ChecklistItemCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    category: RequirementCategory = RequirementCategory.OTHER
    mandatory: bool = False
    assigned_to: uuid.UUID | None = None
    due_at: datetime | None = None
    note: str | None = Field(default=None, max_length=4000)


class ChecklistItemUpdateRequest(BaseModel):
    status: ChecklistItemStatus | None = None
    assigned_to: uuid.UUID | None = None
    due_at: datetime | None = None
    note: str | None = Field(default=None, max_length=4000)


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    mandatory: bool = False
    assigned_to: uuid.UUID | None = None
    due_at: datetime | None = None


class TaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    mandatory: bool | None = None
    status: WorkspaceTaskStatus | None = None
    assigned_to: uuid.UUID | None = None
    due_at: datetime | None = None


class NoteCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


# --------------------------------------------------------------------------- #
# Sortida                                                                       #
# --------------------------------------------------------------------------- #


class PersonRead(BaseModel):
    """Qui pot rebre feina. Els usuaris existents, no cap sistema paral·lel."""

    id: uuid.UUID
    email: str
    full_name: str | None = None


class DecisionRead(ORMModel):
    """Una entrada de l'historial. Immutable: no hi ha cap endpoint que l'editi."""

    id: uuid.UUID
    decision: WorkspaceDecisionType
    decided_by: uuid.UUID
    decided_by_name: str | None = None
    decided_at: datetime
    reason: str | None = None
    compliance_run_id: uuid.UUID | None = None
    requirement_run_id: uuid.UUID | None = None
    prefit_score: int | None = None
    confirmed_blockers: int = 0
    potential_blockers: int = 0
    mandatory_unknown: int = 0
    mandatory_needs_review: int = 0
    automated_recommendation: str | None = None
    deadline_passed: bool = False


class ChecklistItemRead(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    requirement_id: uuid.UUID | None = None
    title: str
    category: RequirementCategory
    mandatory: bool
    status: ChecklistItemStatus
    source: ChecklistItemSource
    assigned_to: uuid.UUID | None = None
    assigned_to_name: str | None = None
    due_at: datetime | None = None
    note: str | None = None
    completed_at: datetime | None = None
    created_at: datetime


class TaskRead(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    description: str | None = None
    mandatory: bool = False
    assigned_to: uuid.UUID | None = None
    assigned_to_name: str | None = None
    due_at: datetime | None = None
    status: WorkspaceTaskStatus
    created_by: uuid.UUID | None = None
    created_at: datetime
    completed_at: datetime | None = None


class NoteRead(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    author_id: uuid.UUID | None = None
    author_name: str | None = None
    text: str
    created_at: datetime


class ComplianceCountersRead(BaseModel):
    """Els numeros de la revisio humana, tal com es mostren a la pantalla.

    El desglossament `mandatory_*` suma exactament `mandatory_total`. Es el que
    es publica en primer pla: un Go/No-Go el decideixen els obligatoris, i una
    taula on les xifres no quadren amb el total nomes genera desconfianca.
    """

    total: int = 0
    satisfies: int = 0
    does_not_satisfy: int = 0
    unknown: int = 0
    needs_review: int = 0
    not_applicable: int = 0
    mandatory_total: int = 0
    mandatory_satisfies: int = 0
    mandatory_does_not_satisfy: int = 0
    mandatory_unknown: int = 0
    mandatory_needs_review: int = 0
    mandatory_not_applicable: int = 0
    potential_blockers: int = 0
    confirmed_blockers: int = 0


class DossierCoverageRead(BaseModel):
    """Cobertura DOCUMENTAL dels obligatoris. No es el compliment."""

    mandatory_total: int = 0
    covered: int = 0
    missing_document: int = 0
    needs_review: int = 0
    not_applicable: int = 0
    expired_documents: int = 0
    optional_total: int = 0
    optional_covered: int = 0


class WorkspaceSummary(ORMModel):
    """Fila de llistat. Prou per decidir on entrar, res mes."""

    id: uuid.UUID
    company_id: uuid.UUID
    tender_id: uuid.UUID
    tender_lot_id: uuid.UUID | None = None
    status: WorkspaceStatus
    tender_title: str | None = None
    expediente_number: str | None = None
    buyer_name: str | None = None
    deadline_at: datetime | None = None
    days_remaining: int | None = None
    deadline_passed: bool = False
    prefit_score: int | None = None
    eligibility: EligibilityResult | None = None
    recommendation: RecommendationResult | None = None
    human_decision: WorkspaceDecisionType | None = None
    analysis_outdated: bool = False
    created_at: datetime


class WorkspaceDetail(BaseModel):
    """La pantalla sencera.

    `prefit_score`, `eligibility`, `recommendation` i `human_decision` es
    publiquen per separat i no hi ha cap camp que els combini.
    """

    id: uuid.UUID
    company_id: uuid.UUID
    tender_id: uuid.UUID
    tender_lot_id: uuid.UUID | None = None
    lot_number: int | None = None
    lot_title: str | None = None
    status: WorkspaceStatus

    tender_title: str | None = None
    expediente_number: str | None = None
    buyer_name: str | None = None
    source_url: str | None = None

    deadline_at: datetime | None = None
    days_remaining: int | None = None
    deadline_passed: bool = False

    #: "Ens interessa?" — metadades de la licitacio.
    prefit_score: int | None = None
    prefit_status: str | None = None
    #: "Podem acreditar que complim?" — requisits del plec.
    eligibility: EligibilityResult | None = None
    #: El que suggereix el sistema. NO es una decisio.
    recommendation: RecommendationResult | None = None
    #: El que ha decidit una persona. `None` = PENDENT.
    human_decision: WorkspaceDecisionType | None = None
    latest_decision: DecisionRead | None = None

    counters: ComplianceCountersRead
    #: Codis dels avisos oberts, per si es decideix amb algun d'ells.
    open_warnings: list[str] = Field(default_factory=list)
    #: Cert si un GO exigiria motiu escrit ara mateix.
    go_requires_reason: bool = False

    current_compliance_run_id: uuid.UUID | None = None
    current_requirement_run_id: uuid.UUID | None = None
    analysis_outdated: bool = False
    outdated_reasons: list[str] = Field(default_factory=list)

    #: Cert quan l'expedient ja no admet presentacio (anul.lat o equivalent).
    tender_actionable: bool = True
    #: Cert quan el lot d'aquesta candidatura ja no es al feed. Per a qui s'hi
    #: presentava es tan definitiu com que caigui l'expedient sencer.
    lot_removed: bool = False
    #: Les dues coses anteriors juntes: si encara te sentit preparar-la.
    workspace_actionable: bool = True
    #: Cert quan la decisio humana no es pot donar per vigent. La decisio NO es
    #: toca ni es torna a signar: el que caduca es la seva vigencia.
    decision_outdated: bool = False
    #: Cert quan la decisio es d'abans que la licitacio tingues historia. No es
    #: pot dir que hagi caducat, pero tampoc que segueixi valida: cal mirar-s'ho.
    decision_basis_unknown: bool = False
    #: Per que la decisio no es vigent. Codis; el text el posa la interficie.
    decision_reasons: list[str] = Field(default_factory=list)
    #: Els canvis materials publicats despres de l'ultima decisio humana.
    changes_since_decision: list[TenderChangeEventRead] = Field(default_factory=list)

    #: Estat del DOSSIER. Ni compliment ni decisio: si tenim els papers a punt.
    readiness: WorkspaceReadiness = WorkspaceReadiness.NOT_STARTED
    readiness_blockers: list[str] = Field(default_factory=list)
    dossier_coverage: DossierCoverageRead | None = None

    checklist: list[ChecklistItemRead] = Field(default_factory=list)
    tasks: list[TaskRead] = Field(default_factory=list)
    notes: list[NoteRead] = Field(default_factory=list)
    decisions: list[DecisionRead] = Field(default_factory=list)
    members: list[PersonRead] = Field(default_factory=list)
    #: Si l'usuari que mira pot signar una decisio. La UI hi amaga els botons;
    #: el servidor ho torna a comprovar igualment.
    can_decide: bool = False

    created_at: datetime
    updated_at: datetime


class WorkspaceCreateResult(BaseModel):
    """`created=False` vol dir que ja existia i s'ha obert el que hi havia."""

    created: bool
    checklist_items_created: int = 0
    workspace: WorkspaceSummary


class ChecklistRegenerateResult(BaseModel):
    created: int
    total: int
    checklist: list[ChecklistItemRead] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Dossier documental (5B)                                                       #
# --------------------------------------------------------------------------- #


class EvidenceItemUpdateRequest(BaseModel):
    """Seleccionar, substituir, descartar o marcar per revisar.

    `clear_document` existeix perque `document_id: None` no es pot distingir de
    "no toquis el document" amb `exclude_unset` sol.
    """

    status: EvidenceItemStatus | None = None
    document_id: uuid.UUID | None = None
    clear_document: bool = False
    notes: str | None = Field(default=None, max_length=4000)


class EvidenceItemCreateRequest(BaseModel):
    requirement_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)


class DossierDocumentRead(BaseModel):
    """El fitxer, amb el que cal per decidir si serveix per a AQUEST plec."""

    id: uuid.UUID
    name: str
    category: DocumentCategory
    mime_type: str
    file_size: int
    #: SHA-256: permet saber si dos requisits fan servir literalment el mateix
    #: fitxer sense comparar noms.
    sha256: str
    issued_at: date | None = None
    expires_at: date | None = None
    uploaded_at: datetime


class DossierItemRead(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    requirement_id: uuid.UUID | None = None
    checklist_item_id: uuid.UUID | None = None
    requirement_title: str | None = None
    requirement_category: RequirementCategory | None = None
    requirement_quote: str | None = None
    #: Que en deia el compliment. Recordatori que cobertura != compliment.
    assessment_result: AssessmentResult | None = None
    mandatory: bool = False
    label: str
    status: EvidenceItemStatus
    source: EvidenceItemSource
    company_evidence_type: CompanyEvidenceKind | None = None
    company_evidence_id: uuid.UUID | None = None
    document: DossierDocumentRead | None = None
    #: Vigencia RESPECTE DEL TERMINI, no d'avui.
    validity: DocumentValidity
    coverage: DossierCoverage | None = None
    selected_by: uuid.UUID | None = None
    selected_by_name: str | None = None
    selected_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class DossierGroupRead(BaseModel):
    group: str
    items: list[DossierItemRead] = Field(default_factory=list)


class ReadinessRead(BaseModel):
    """Estat del dossier i, sobretot, per que. Cap puntuacio opaca."""

    state: WorkspaceReadiness
    blockers: list[str] = Field(default_factory=list)
    #: Explicit: arribar a la revisio final NO es haver presentat res.
    is_submitted: bool = False


class DossierRead(BaseModel):
    workspace_id: uuid.UUID
    workspace_status: WorkspaceStatus
    deadline_at: datetime | None = None
    coverage: DossierCoverageRead
    readiness: ReadinessRead
    groups: list[DossierGroupRead] = Field(default_factory=list)


class DossierSyncResult(BaseModel):
    suggested: int
    gaps: int
    total: int
    dossier: DossierRead
