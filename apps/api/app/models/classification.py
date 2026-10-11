"""Classificacio documental: que es cada document d'una licitacio.

Capa 3 del motor d'analisi. **No coneix la recepcio**: treballa sobre els
`TenderDocument` de la licitacio, vinguin del canal que vinguin. L'unic lligam
amb la capa 2 es informatiu (`trigger_reception_id`: quina entrega la va
provocar).

* `tender_document_classification_runs`: una passada del classificador sobre
  **tots** els documents de la licitacio. Immutable; nomes una es vigent per
  (empresa, licitacio).
* `tender_document_role_suggestions`: el rol que va proposar per a cada
  document, amb la confianca i els senyals.
* `tender_document_classification_findings`: el que no quadrava del conjunt.
* `tender_document_role_decisions`: el rol que ha fixat una PERSONA. Penja del
  document, no de la passada: una reclassificacio (arriba un document nou) no
  esborra el que algu ja ha decidit. Des de 0059 porta la versio del fitxer
  (`content_sha256`) i es l'override de l'empresa sobre la classificacio global
  (`app/models/global_classification.py`): una versio nova la deixa sense efecte.

Tot es de **tenant**: la classificacio es la lectura que en fa una empresa, com
la revisio de requisits.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    ClassificationFindingCode,
    ClassificationTrigger,
    DocumentRole,
    FindingSeverity,
    RoleConfidence,
    TenderDocumentType,
)
from app.models.review import JSONDocument, in_values


class TenderDocumentClassificationRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tender_document_classification_runs"
    __table_args__ = (
        sa.CheckConstraint(in_values("trigger", ClassificationTrigger), name="trigger_valid"),
        sa.CheckConstraint("document_count >= 0", name="count_not_negative"),
        sa.UniqueConstraint(
            "id", "company_id", name="uq_tender_document_classification_runs_id_company"
        ),
        sa.Index(
            "uq_tender_document_classification_runs_current",
            "company_id",
            "tender_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
        ),
        sa.Index(
            "ix_tender_document_classification_runs_scope", "company_id", "tender_id", "created_at"
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    classifier_version: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: SHA-256 dels documents classificats (id, contingut i nom) i de la versio.
    #: Mateixa empremta = mateix resultat: no cal tornar-ho a fer.
    input_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    trigger: Mapped[ClassificationTrigger] = mapped_column(
        enum_column(ClassificationTrigger, "classification_trigger"), nullable=False
    )
    #: Quina entrega la va provocar, si en va ser una. Nomes informatiu.
    trigger_reception_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_receptions.id", ondelete="SET NULL"),
    )
    document_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class TenderDocumentRoleSuggestion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tender_document_role_suggestions"
    __table_args__ = (
        sa.CheckConstraint(in_values("role", DocumentRole), name="role_valid"),
        sa.CheckConstraint(in_values("confidence", RoleConfidence), name="confidence_valid"),
        sa.CheckConstraint(
            in_values("document_type", TenderDocumentType), name="document_type_valid"
        ),
        sa.UniqueConstraint(
            "run_id", "tender_document_id", name="uq_tender_document_role_suggestions_document"
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "company_id"],
            [
                "tender_document_classification_runs.id",
                "tender_document_classification_runs.company_id",
            ],
            ondelete="CASCADE",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    tender_document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[DocumentRole] = mapped_column(
        enum_column(DocumentRole, "document_role"), nullable=False
    )
    confidence: Mapped[RoleConfidence] = mapped_column(
        enum_column(RoleConfidence, "role_confidence"), nullable=False
    )
    #: Com el tractaria l'analisi si ningu no decideix res: nomes surt del rol
    #: amb confianca alta i si no es una copia.
    document_type: Mapped[TenderDocumentType] = mapped_column(
        enum_column(TenderDocumentType, "tender_document_type"), nullable=False
    )
    #: Rol pel nom, rol pel contingut, linia de titol, codi intern, copia de...
    signals: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)


class TenderDocumentClassificationFinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tender_document_classification_findings"
    __table_args__ = (
        sa.CheckConstraint(in_values("code", ClassificationFindingCode), name="code_valid"),
        sa.CheckConstraint(in_values("severity", FindingSeverity), name="severity_valid"),
        sa.ForeignKeyConstraint(
            ["run_id", "company_id"],
            [
                "tender_document_classification_runs.id",
                "tender_document_classification_runs.company_id",
            ],
            ondelete="CASCADE",
        ),
        sa.Index("ix_tender_document_classification_findings_run", "run_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[ClassificationFindingCode] = mapped_column(
        enum_column(ClassificationFindingCode, "classification_finding_code"), nullable=False
    )
    severity: Mapped[FindingSeverity] = mapped_column(
        enum_column(FindingSeverity, "finding_severity"), nullable=False
    )
    #: El document afectat, si n'hi ha un (un document citat que falta no en te).
    tender_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_documents.id", ondelete="CASCADE")
    )
    #: L'altre document implicat: l'original d'una copia, per exemple.
    related_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_documents.id", ondelete="CASCADE")
    )
    detail: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)


class TenderDocumentRoleDecision(UUIDPrimaryKeyMixin, Base):
    """El rol que una persona ha fixat per a un document. Un per empresa i document.

    Canviar-lo reescriu la fila: l'historial de qui ho va canviar i quan es a
    l'auditoria. El que va proposar el classificador no es toca mai.
    """

    __tablename__ = "tender_document_role_decisions"
    __table_args__ = (
        sa.CheckConstraint(in_values("role", DocumentRole), name="role_valid"),
        sa.UniqueConstraint(
            "company_id", "tender_document_id", name="uq_tender_document_role_decisions_document"
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    tender_document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[DocumentRole] = mapped_column(
        enum_column(DocumentRole, "document_role"), nullable=False
    )
    #: La versio del fitxer sobre la qual es va decidir (0059). Un fitxer nou deixa
    #: la decisio sense efecte ("decisio d'una versio anterior", `stale_override`).
    content_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
