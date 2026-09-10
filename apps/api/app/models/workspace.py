"""Workspace de candidatura: on una empresa decideix si es presenta.

    Company + Tender (+ Lot)  ->  BidWorkspace
                                    |- WorkspaceDecision       (immutable, append-only)
                                    |- WorkspaceChecklistItem
                                    |- WorkspaceEvidenceItem   (el dossier documental)
                                    |- WorkspaceTask
                                    |- WorkspaceNote

Tres decisions sostenen aquesta capa:

* **La recomanacio automatica i la decisio humana son dues columnes diferents.**
  El motor de 4C escriu `recommendation`; nomes una persona escriu una
  `WorkspaceDecision`. No hi ha cap cami de codi que converteixi l'una en
  l'altra: `go_approved` i `no_go_approved` NOMES es poden assolir inserint una
  decisio signada per un usuari.
* **Les decisions no s'editen ni s'esborren.** Canviar d'opinio afegeix una fila
  nova; la vigent es la ultima per `decided_at`. Un historial que es pot
  reescriure no serveix per respondre "qui va aprovar aixo i amb quina
  informacio a la vista".
* **Una decisio guarda contra que es va decidir**: run de compliment, run de
  requisits, pre-fit i el recompte de blocadors d'aquell moment. Si despres es
  recalcula qualsevol cosa, la decisio segueix dient el que se sabia el dia que
  es va prendre.

Tot penja de `company_id`: un workspace es dada de tenant, com el compliment.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CompanyOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    ChecklistItemSource,
    ChecklistItemStatus,
    CompanyEvidenceKind,
    EvidenceItemSource,
    EvidenceItemStatus,
    RequirementCategory,
    WorkspaceDecisionType,
    WorkspaceStatus,
    WorkspaceTaskStatus,
)


class BidWorkspace(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """La candidatura d'una empresa a una licitacio (o a un lot concret).

    Unicitat per `company + tender + lot`. Com que `tender_lot_id` pot ser NULL i
    a SQL NULL no es igual a NULL, una sola restriccio unica no evitaria dos
    workspaces "de tot l'expedient". Per aixo hi ha dos index parcials: un per al
    cas amb lot i un altre per al cas sense.
    """

    __tablename__ = "bid_workspaces"
    __table_args__ = (
        sa.Index("ix_bid_workspaces_company_tender", "company_id", "tender_id"),
        sa.Index("ix_bid_workspaces_company_status", "company_id", "status"),
        sa.Index(
            "uq_bid_workspaces_company_tender_lot",
            "company_id",
            "tender_id",
            "tender_lot_id",
            unique=True,
            postgresql_where=sa.text("tender_lot_id IS NOT NULL"),
            sqlite_where=sa.text("tender_lot_id IS NOT NULL"),
        ),
        sa.Index(
            "uq_bid_workspaces_company_tender_nolot",
            "company_id",
            "tender_id",
            unique=True,
            postgresql_where=sa.text("tender_lot_id IS NULL"),
            sqlite_where=sa.text("tender_lot_id IS NULL"),
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: `None` = candidatura a tot l'expedient. Amb lots, un per lot.
    tender_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="CASCADE")
    )

    status: Mapped[WorkspaceStatus] = mapped_column(
        enum_column(WorkspaceStatus, "workspace_status"),
        nullable=False,
        default=WorkspaceStatus.ANALYSIS,
    )

    #: Contra quins calculs treballa el workspace. Si el vigent del sistema ja no
    #: es aquest, l'analisi ha quedat endarrerida i cal dir-ho, no substituir-la.
    current_compliance_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        # Nom explicit i curt: la convencio en generaria un de 67 caracters i
        # PostgreSQL nomes n'admet 63. Truncat, el model i la base de dades
        # deixarien de coincidir.
        sa.ForeignKey(
            "company_compliance_runs.id",
            ondelete="SET NULL",
            name="fk_bid_workspaces_compliance_run",
        ),
    )
    current_requirement_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "requirement_extraction_runs.id",
            ondelete="SET NULL",
            name="fk_bid_workspaces_requirement_run",
        ),
    )

    #: Fotografia del pre-fit quan es va obrir/refrescar el workspace. NO es
    #: fusiona mai amb l'elegibilitat: son dues preguntes diferents.
    prefit_score: Mapped[int | None] = mapped_column(sa.Integer)
    prefit_captured_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: Copia del termini vigent quan es va obrir. El valor autoritatiu segueix
    #: sent el de la licitacio; aixo permet veure si el plec l'ha mogut.
    deadline_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )

    decisions: Mapped[list[WorkspaceDecision]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        order_by="WorkspaceDecision.decided_at.desc()",
    )
    checklist_items: Mapped[list[WorkspaceChecklistItem]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    evidence_items: Mapped[list[WorkspaceEvidenceItem]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[WorkspaceTask]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    notes: Mapped[list[WorkspaceNote]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BidWorkspace {self.status} tender={self.tender_id}>"


class WorkspaceDecision(UUIDPrimaryKeyMixin, CompanyOwnedMixin, Base):
    """Una decisio humana. **Immutable i append-only.**

    No te `updated_at` a proposit: aquesta fila no es modifica mai. Canviar de
    GO a NO-GO afegeix una decisio nova i conserva l'anterior, amb el seu motiu i
    el seu context.

    `decided_by` es NOT NULL: una decisio sense signatura no es una decisio. Si
    l'usuari s'esborra algun dia, la fila ha de quedar igualment atribuida, per
    aixo la FK es RESTRICT i no SET NULL.
    """

    __tablename__ = "workspace_decisions"
    __table_args__ = (
        sa.Index("ix_workspace_decisions_workspace_decided", "workspace_id", "decided_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision: Mapped[WorkspaceDecisionType] = mapped_column(
        enum_column(WorkspaceDecisionType, "workspace_decision_type"), nullable=False
    )
    decided_by: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: Sobre quina revisio de la licitacio es va decidir. Es el que permet dir
    #: despres "aquesta decisio es d'abans que canvies el termini" sense
    #: tocar-la: la decisio no caduca, el que caduca es la seva vigencia, i
    #: aixo es dedueix comparant aquesta revisio amb l'actual.
    tender_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_revisions.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    #: Obligatori quan es decideix amb avisos oberts. Veure `bid_workspace.py`.
    reason: Mapped[str | None] = mapped_column(sa.Text)

    #: Contra que es va decidir. Immutable com la resta de la fila.
    compliance_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "company_compliance_runs.id",
            ondelete="SET NULL",
            name="fk_workspace_decisions_compliance_run",
        ),
    )
    requirement_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "requirement_extraction_runs.id",
            ondelete="SET NULL",
            name="fk_workspace_decisions_requirement_run",
        ),
    )
    prefit_score: Mapped[int | None] = mapped_column(sa.Integer)

    #: Els avisos que hi havia a la pantalla en aquell moment. Sense aixo,
    #: l'historial no diu si la persona va decidir amb la informacio a la vista.
    confirmed_blockers: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    potential_blockers: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    mandatory_unknown: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    mandatory_needs_review: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Que recomanava el sistema quan la persona va decidir. Nomes context.
    automated_recommendation: Mapped[str | None] = mapped_column(sa.String(40))
    deadline_passed: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    workspace: Mapped[BidWorkspace] = relationship(back_populates="decisions")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceDecision {self.decision} by={self.decided_by}>"


class WorkspaceChecklistItem(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """Que cal aportar o preparar, derivat dels requisits del plec.

    `requirement_id` es la traçabilitat: cada item generat sap de quin requisit
    surt i permet saltar a la cita del plec. Els items manuals el porten a NULL.

    La unicitat per `(workspace_id, requirement_id)` es el que fa que regenerar
    no dupliqui. Els items manuals no hi entren perque tots tenen NULL.
    """

    __tablename__ = "workspace_checklist_items"
    __table_args__ = (
        sa.Index("ix_workspace_checklist_items_workspace", "workspace_id"),
        sa.Index(
            "uq_workspace_checklist_items_requirement",
            "workspace_id",
            "requirement_id",
            unique=True,
            postgresql_where=sa.text("requirement_id IS NOT NULL"),
            sqlite_where=sa.text("requirement_id IS NOT NULL"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="SET NULL")
    )

    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    category: Mapped[RequirementCategory] = mapped_column(
        enum_column(RequirementCategory, "requirement_category"),
        nullable=False,
        default=RequirementCategory.OTHER,
    )
    mandatory: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    status: Mapped[ChecklistItemStatus] = mapped_column(
        enum_column(ChecklistItemStatus, "checklist_item_status"),
        nullable=False,
        default=ChecklistItemStatus.PENDING,
    )
    source: Mapped[ChecklistItemSource] = mapped_column(
        enum_column(ChecklistItemSource, "checklist_item_source"),
        nullable=False,
        default=ChecklistItemSource.REQUIREMENT,
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    due_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(sa.Text)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )

    workspace: Mapped[BidWorkspace] = relationship(back_populates="checklist_items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceChecklistItem {self.status} {self.title[:40]}>"


class WorkspaceTask(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """Feina que no surt de cap requisit: trucar al banc, renovar una polissa."""

    __tablename__ = "workspace_tasks"
    __table_args__ = (
        sa.Index("ix_workspace_tasks_workspace", "workspace_id"),
        sa.Index("ix_workspace_tasks_assigned", "assigned_to"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    #: Una tasca obligatoria pendent impedeix que el dossier arribi a la revisio
    #: final. Les que neixen d'un forat del dossier sobre un requisit obligatori
    #: ho son automaticament; la resta, nomes si algu ho diu.
    mandatory: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    due_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[WorkspaceTaskStatus] = mapped_column(
        enum_column(WorkspaceTaskStatus, "workspace_task_status"),
        nullable=False,
        default=WorkspaceTaskStatus.PENDING,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    workspace: Mapped[BidWorkspace] = relationship(back_populates="tasks")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceTask {self.status} {self.title[:40]}>"


class WorkspaceNote(UUIDPrimaryKeyMixin, CompanyOwnedMixin, Base):
    """Una nota de treball. Text pla i prou: no cal cap editor ric."""

    __tablename__ = "workspace_notes"
    __table_args__ = (sa.Index("ix_workspace_notes_workspace", "workspace_id", "created_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    workspace: Mapped[BidWorkspace] = relationship(back_populates="notes")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceNote {self.workspace_id}>"


class WorkspaceEvidenceItem(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """Una peca del dossier: amb QUE acreditarem un requisit concret.

    Es la baula que faltava entre "sabem que complim" (4C) i "aixo es el que
    presentarem". Son dues coses diferents i el sistema no les ha de confondre:
    un requisit pot estar `satisfies` i tenir el dossier `missing` perque encara
    no s'ha preparat el PDF correcte.

    Tres regles del model:

    * **Un document pot cobrir N requisits i un requisit pot necessitar N
      documents.** Per aixo aixo es una taula d'items i no un camp a
      `WorkspaceChecklistItem`. El document NO es copia: `document_id` apunta a
      la biblioteca corporativa que ja existeix.
    * **`suggested` no es `selected`.** El motor pot proposar el document que ja
      va fer servir per avaluar el requisit; nomes una persona el tria, i llavors
      queden escrits `selected_by` i `selected_at`.
    * **La vigencia es respecte de la licitacio, no d'avui.** Un certificat que
      caduca abans del termini no serveix, per molt vigent que sigui ara. Aixo no
      es persisteix: es deriva de `Document.expires_at` i del termini.
    """

    __tablename__ = "workspace_evidence_items"
    __table_args__ = (
        sa.Index("ix_workspace_evidence_items_workspace", "workspace_id"),
        sa.Index("ix_workspace_evidence_items_requirement", "workspace_id", "requirement_id"),
        sa.Index("ix_workspace_evidence_items_document", "document_id"),
        # Un mateix document no es proposa dues vegades per al mateix requisit.
        # Requisits diferents SI que poden compartir document: no hi ha copia.
        sa.Index(
            "uq_workspace_evidence_items_requirement_document",
            "workspace_id",
            "requirement_id",
            "document_id",
            unique=True,
            postgresql_where=sa.text("document_id IS NOT NULL"),
            sqlite_where=sa.text("document_id IS NOT NULL"),
        ),
        # Un sol marcador "encara no tenim document" per requisit: si no,
        # sincronitzar dues vegades ompliria el dossier de forats repetits.
        sa.Index(
            "uq_workspace_evidence_items_requirement_gap",
            "workspace_id",
            "requirement_id",
            unique=True,
            postgresql_where=sa.text("document_id IS NULL AND requirement_id IS NOT NULL"),
            sqlite_where=sa.text("document_id IS NULL AND requirement_id IS NOT NULL"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Quin requisit del plec acredita. `None` nomes per a peces soltes.
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="SET NULL")
    )
    #: L'item de checklist equivalent, quan n'hi ha. Permet passar de "cal
    #: acreditar ISO 27001" al fitxer concret sense tornar a buscar.
    checklist_item_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        # Nom explicit i curt: la convencio en generaria un de 71 caracters.
        sa.ForeignKey(
            "workspace_checklist_items.id",
            ondelete="SET NULL",
            name="fk_workspace_evidence_items_checklist_item",
        ),
    )
    #: El document de la biblioteca corporativa. Referencia, mai copia.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL")
    )

    #: Quina dada corporativa sosté la peca (certificacio, polissa, projecte...).
    #: Es el mateix parell que fa servir `AssessmentEvidence`.
    company_evidence_type: Mapped[CompanyEvidenceKind | None] = mapped_column(
        enum_column(CompanyEvidenceKind, "company_evidence_kind")
    )
    company_evidence_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    #: Text per a la interficie, calculat quan es va crear la peca.
    label: Mapped[str] = mapped_column(sa.String(300), nullable=False, default="")

    status: Mapped[EvidenceItemStatus] = mapped_column(
        enum_column(EvidenceItemStatus, "evidence_item_status"),
        nullable=False,
        default=EvidenceItemStatus.SUGGESTED,
    )
    source: Mapped[EvidenceItemSource] = mapped_column(
        enum_column(EvidenceItemSource, "evidence_item_source"),
        nullable=False,
        default=EvidenceItemSource.COMPLIANCE,
    )

    #: Qui va triar aquest document i quan. Buit mentre sigui una proposta.
    selected_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    selected_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(sa.Text)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )

    workspace: Mapped[BidWorkspace] = relationship(back_populates="evidence_items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceEvidenceItem {self.status} doc={self.document_id}>"
