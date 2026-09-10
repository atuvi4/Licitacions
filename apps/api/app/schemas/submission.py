"""Schemas del paquet de presentacio.

Tot el que surt d'aqui ha de permetre respondre "exactament que estem aprovant":
els documents, els seus hashos, contra quins calculs, amb quin termini i amb quin
`manifest_hash`. Un identificador de paquet sol no ho respondria.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    DocumentValidity,
    PackageApprovalDecision,
    RequirementCategory,
    SubmissionPackageStatus,
    WorkspaceReadiness,
    WorkspaceStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ApprovalRequest(BaseModel):
    decision: PackageApprovalDecision = PackageApprovalDecision.APPROVED_FOR_SUBMISSION
    comment: str | None = Field(default=None, max_length=4000)


class PackageItemRead(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID | None = None
    workspace_evidence_item_id: uuid.UUID | None = None
    requirement_id: uuid.UUID | None = None
    requirement_title: str | None = None
    category: RequirementCategory
    filename: str
    mime_type: str
    file_size: int
    #: El hash dels BYTES que s'aproven, copiat en congelar.
    document_sha256: str
    position: int
    #: Vigencia recalculada contra el termini. `None` si el document ja no hi es.
    validity: DocumentValidity | None = None
    document_expires_at: date | None = None
    #: Cert si el document actual ja no coincideix amb el que es va congelar.
    changed_since_freeze: bool = False


class ApprovalRead(ORMModel):
    """Una entrada de l'historial d'aprovacions. Immutable."""

    id: uuid.UUID
    package_id: uuid.UUID
    package_manifest_hash: str
    decision: PackageApprovalDecision
    approved_by: uuid.UUID
    approved_by_name: str | None = None
    approved_at: datetime
    comment: str | None = None
    readiness_snapshot: WorkspaceReadiness
    blockers_snapshot: list[Any] = Field(default_factory=list)


class PackageCoverageRead(BaseModel):
    mandatory_total: int = 0
    mandatory_covered: int = 0
    document_count: int = 0


class PackageSummary(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    revision_number: int
    status: SubmissionPackageStatus
    manifest_hash: str | None = None
    #: Els 12 primers caracters: el que es llegeix a la pantalla.
    manifest_hash_short: str | None = None
    document_count: int = 0
    mandatory_total: int = 0
    mandatory_covered: int = 0
    deadline_at: datetime | None = None
    frozen_at: datetime | None = None
    created_at: datetime
    is_approved: bool = False
    approved_at: datetime | None = None
    approved_by_name: str | None = None
    #: Cert si la licitacio ha canviat materialment des que es va congelar.
    #: Un paquet aprovat segueix aprovat: aixo diu si encara es presentable.
    tender_changed_since_freeze: bool = False


class PackageDetail(BaseModel):
    """La pantalla de revisio final sencera."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    company_id: uuid.UUID
    revision_number: int
    status: SubmissionPackageStatus
    manifest_hash: str | None = None
    manifest_hash_short: str | None = None

    tender_title: str | None = None
    expediente_number: str | None = None
    lot_number: int | None = None
    deadline_at: datetime | None = None
    days_remaining: int | None = None

    compliance_run_id: uuid.UUID | None = None
    requirement_run_id: uuid.UUID | None = None
    #: Contra quin estat de la licitacio es va congelar. `None` = es d'abans que
    #: la licitacio tingues historia, i llavors no es pot afirmar que quadri.
    tender_revision_number: int | None = None
    tender_normalized_hash_short: str | None = None
    prefit_score: int | None = None
    coverage: PackageCoverageRead

    items: list[PackageItemRead] = Field(default_factory=list)
    approvals: list[ApprovalRead] = Field(default_factory=list)
    approval: ApprovalRead | None = None

    #: Readiness recalculada ARA, no la del dia que es va congelar.
    readiness: WorkspaceReadiness
    readiness_blockers: list[str] = Field(default_factory=list)
    #: Per que aquest paquet ja no descriu el que hi ha. Buit = segueix valid.
    stale_reasons: list[str] = Field(default_factory=list)
    is_stale: bool = False
    #: Cert nomes si el paquet segueix descrivint una presentacio possible.
    #: Independent de `is_approved`: una aprovacio no es desfa mai, pero un
    #: paquet aprovat contra un plec que despres han substituit ja no s'envia.
    coherent_for_submission: bool = False
    #: Cert nomes si TOT quadra ara mateix. La interficie no ho ha de deduir.
    can_approve: bool = False
    #: Si l'usuari que mira te potestat per signar-ho.
    can_sign: bool = False

    workspace_status: WorkspaceStatus
    #: Explicit: aprovar un paquet NO es haver presentat res.
    is_submitted: bool = False

    frozen_at: datetime | None = None
    created_at: datetime


class FreezeResult(BaseModel):
    created: bool
    package: PackageDetail
