"""Workspace de candidatura: on una empresa decideix si es presenta.

    Company + Tender (+ Lot)  ->  BidWorkspace
                                    |- WorkspaceDecision       (immutable, append-only)
                                    |- WorkspaceChecklistItem
                                    |- WorkspaceEvidenceItem   (el dossier documental)
                                    |- WorkspaceTask
                                    |- WorkspaceTechnicalSolution
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
from typing import Any

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
    TechnicalSolutionSource,
    TechnicalSolutionStatus,
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

    # --- Revisio humana de l'abast tecnic (C-14) ---------------------------- #
    #
    # Sense aixo, zero solucions tecniques voldria dir dues coses alhora: "cap
    # requisit en necessita" i "ningu no s'ho ha mirat encara". Amb una
    # confirmacio humana vigent, zero solucions es una resposta; sense, es una
    # pregunta oberta.
    #
    # Tots tres son NULL per defecte, i NULL vol dir NO revisat: cap workspace
    # existent no queda validat en silenci (C-5).
    technical_scope_reviewed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    technical_scope_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_bid_workspaces_technical_scope_reviewer"
        ),
    )
    #: Sobre QUE es va confirmar. Si l'abast d'ara no hi coincideix, la revisio
    #: deixa de ser vigent; no s'esborra, es deriva en llegir.
    technical_scope_fingerprint: Mapped[str | None] = mapped_column(sa.String(64))

    # Revisio HUMANA del conjunt documental (S3-S3). Mateix patro que l'abast
    # tecnic i pel mateix motiu: que hi hagi plecs no vol dir que algu se'ls
    # hagi mirat, i NULL vol dir "no revisat", mai "correcte".
    #
    # No hi ha cap columna que digui "caducat": es deriva comparant l'empremta
    # d'ara amb la revisada. Guardar-ho voldria dir tenir dues versions del
    # mateix fet, i la desada quedaria enrere a la primera cosa que canvies
    # sense passar per aqui.
    documents_reviewed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    #: Quin conjunt de plecs es va revisar. Veure `document_review.fingerprint_of`.
    documents_reviewed_fingerprint: Mapped[str | None] = mapped_column(sa.String(64))

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
    technical_solutions: Mapped[list[WorkspaceTechnicalSolution]] = relationship(
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
        sa.Index("ix_workspace_decisions_analysis_run", "analysis_run_id"),
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

    #: Decisions v3 (0054). Run d'analisi v3 contra el qual es va decidir.
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_analysis_runs.id",
            ondelete="SET NULL",
            name="fk_workspace_decisions_analysis_run",
        ),
    )
    #: Fotografia versionada del que hi havia a la pantalla. NULL = decisio legacy
    #: (anterior a v3); NO NULL = decisio v3. Les files antigues no es toquen.
    analysis_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(
        sa.JSON(none_as_null=True)
    )

    #: Els avisos que hi havia a la pantalla en aquell moment. Sense aixo,
    #: l'historial no diu si la persona va decidir amb la informacio a la vista.
    confirmed_blockers: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    potential_blockers: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    mandatory_unknown: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    mandatory_needs_review: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Que recomanava el sistema quan la persona va decidir. Nomes context.
    automated_recommendation: Mapped[str | None] = mapped_column(sa.String(40))
    #: Els avisos que hi havia OBERTS a la pantalla quan es va decidir, amb els
    #: seus codis. Els comptadors de sota diuen quants bloquejos hi havia; aixo
    #: diu la resta: que l'analisi era una simulacio, que no hi havia motor
    #: configurat, que l'elegibilitat estava indeterminada o que el que es veia
    #: descrivia un estat anterior de la licitacio.
    #:
    #: Es una FOTOGRAFIA, no una consulta: l'historial l'ha de llegir tal com es
    #: va desar. Reconstruir-la a partir de l'estat d'avui explicaria una decisio
    #: amb informacio que qui la va prendre no tenia.
    #:
    #: `NULL` a les decisions anteriors a aquest camp vol dir "no es va
    #: registrar", que NO es el mateix que "no hi havia cap avis".
    open_warnings: Mapped[list[str] | None] = mapped_column(sa.JSON(none_as_null=True))
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

    # --- Confirmacio humana de la PERTINENÇA ------------------------------- #
    #
    # Triar un document i confirmar que es el document adequat son dues
    # afirmacions diferents. La segona nomes la pot fer una persona quan el
    # sistema no pot establir la vinculacio tot sol, i queda lligada al requisit
    # i al document concrets: substituir el document o canviar materialment el
    # requisit no l'hereta.
    relevance_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    relevance_confirmed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    #: Per que aquest paper acredita aquesta exigencia. Obligatori per confirmar.
    relevance_note: Mapped[str | None] = mapped_column(sa.Text)
    #: Sobre quin document es va signar. Si el document canvia, la confirmacio
    #: deixa de valer encara que la fila segueixi aqui.
    relevance_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL")
    )
    #: Empremta del requisit tal com era en confirmar-lo. Si el plec canvia el
    #: que demana, ningu no ha signat aixo.
    relevance_requirement_fingerprint: Mapped[str | None] = mapped_column(sa.String(64))

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )

    workspace: Mapped[BidWorkspace] = relationship(back_populates="evidence_items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceEvidenceItem {self.status} doc={self.document_id}>"


class WorkspaceTechnicalSolution(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """Com oferirem un requisit concret en aquesta candidatura.

    Es la tercera baula d'una cadena que el sistema no ha de fondre mai:

    * `CompanyRequirementAssessment` respon **"podem complir-ho i acreditar-ho?"**;
    * aixo respon **"com ho oferirem concretament?"**;
    * `WorkspaceEvidenceItem` respon **"amb quin paper ho acreditem?"**;
    * l'oferta economica respondra **"quant costa i quin preu hi posem?"**.

    Complir un requisit i saber com resoldre'l son coses diferents: una empresa
    pot complir de sobres i no haver decidit encara amb quin equip ho fara, i
    pot tenir la solucio claríssima i no complir. Barrejar-ho faria que una de
    les dues respostes tapes l'altra.

    Tres regles del model:

    * **Una solucio per requisit i candidatura.** L'index unic parcial es el que
      fa que sincronitzar dues vegades no dupliqui res; les files manuals porten
      `requirement_id` NULL i no hi entren, com a `WorkspaceChecklistItem`.
    * **`validated` nomes l'escriu una persona.** Queda lligat al requisit i al
      contingut concrets amb dues empremtes: si el plec canvia el que demana o
      algu reescriu la proposta, la validacio deixa de valer sense haver
      d'esborrar res. Es el mateix mecanisme que la confirmacio de pertinenca
      del dossier.
    * **El proveidor i el producte son text.** No hi ha cap cataleg ni cap taula
      de proveidors: aixo queda fora de l'MVP, i una FK ara obligaria a inventar
      entitats per poder escriure un nom.

    La coherencia de `validated` —que porti signatura, data, motiu i les dues
    empremtes— la imposa el servei, no l'esquema: mentre la solucio es un
    esborrany aquests camps han de poder ser buits.
    """

    __tablename__ = "workspace_technical_solutions"
    __table_args__ = (
        # Els valors dels enums els imposa la BASE, no nomes Python.
        # `enum_column()` es materialitza com un VARCHAR sense CHECK (SQLAlchemy
        # nomes el crea amb `create_constraint=True`), i sense aquests dos
        # constraints un INSERT que no passi per l'ORM hi pot escriure
        # qualsevol cadena que capiga al VARCHAR.
        sa.CheckConstraint(
            "status IN ({})".format(
                ", ".join(f"'{member.value}'" for member in TechnicalSolutionStatus)
            ),
            name="status_valid",
        ),
        sa.CheckConstraint(
            "source IN ({})".format(
                ", ".join(f"'{member.value}'" for member in TechnicalSolutionSource)
            ),
            name="source_valid",
        ),
        sa.Index("ix_workspace_technical_solutions_workspace", "workspace_id"),
        sa.Index("ix_workspace_technical_solutions_owner", "owner_id"),
        # Una solucio per requisit dins d'una candidatura. Les manuals tenen
        # `requirement_id` NULL i, com que a SQL NULL no es igual a NULL, no
        # xoquen mai entre elles.
        sa.Index(
            "uq_workspace_technical_solutions_requirement",
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
    #: Quin requisit del plec resol. `None` a les files manuals. Si una
    #: extraccio nova se'l emporta, la solucio es conserva sense vincle: la
    #: feina escrita no la pot esborrar un recalcul.
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="SET NULL")
    )

    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    #: El cos de la proposta: que oferirem i com.
    proposed_solution: Mapped[str | None] = mapped_column(sa.Text)

    #: Text lliure, sense cataleg ni FK (veure el docstring de la classe).
    supplier_name: Mapped[str | None] = mapped_column(sa.String(300))
    product_name: Mapped[str | None] = mapped_column(sa.String(300))

    #: Qui se n'encarrega. Que pertanyi a l'empresa ho comprova el servei, com
    #: passa amb `assigned_to` de les tasques.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )

    status: Mapped[TechnicalSolutionStatus] = mapped_column(
        enum_column(TechnicalSolutionStatus, "technical_solution_status"),
        nullable=False,
        default=TechnicalSolutionStatus.NOT_STARTED,
    )
    #: D'on surt la fila. **Sense valor per defecte, i a proposit:** es
    #: provenance, i un defecte faria que una fila creada per un cami que
    #: s'oblidi de dir-ho passes per "surt d'un requisit del plec" sense que
    #: ningu ho hagi afirmat. L'escriu qui la crea: `requirement` al sync,
    #: `manual` a la creacio a ma.
    source: Mapped[TechnicalSolutionSource] = mapped_column(
        enum_column(TechnicalSolutionSource, "technical_solution_source"),
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(sa.Text)

    # --- Validacio humana --------------------------------------------------- #
    #
    # Tenir una proposta escrita i donar-la per bona son dues afirmacions
    # diferents, i la segona nomes la pot fer una persona.
    validated_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    validated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Per que aquesta solucio resol aquesta exigencia. El servei l'exigira per
    #: validar; aqui es nullable perque un esborrany encara no en te.
    validated_note: Mapped[str | None] = mapped_column(sa.Text)
    #: Empremta del requisit tal com era en validar-lo. SHA-256 en hexadecimal,
    #: la mateixa amplada i el mateix calcul que al dossier.
    validated_requirement_fingerprint: Mapped[str | None] = mapped_column(sa.String(64))
    #: Empremta de la proposta signada. Si algu la reescriu, ningu no ha validat
    #: el que hi ha ara.
    validated_content_fingerprint: Mapped[str | None] = mapped_column(sa.String(64))

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )

    workspace: Mapped[BidWorkspace] = relationship(back_populates="technical_solutions")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceTechnicalSolution {self.status} {self.title[:40]}>"
