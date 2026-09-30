"""Recepcio de documents: per quin canal van arribar, i que se'n va fer.

Capa 2 del motor d'analisi. Dues taules de **tenant**; els documents que en
surten son els de sempre (`tender_documents` i la capa documental, globals).
Aqui no hi ha cap rol ni cap troballa de classificacio: aixo es la capa 3 i no
depen del canal.

* `tender_document_receptions`: una entrega (un ZIP pujat, avui; una carpeta de
  Drive o una descarrega d'una plataforma, dema). Si el canal va entregar un
  contenidor, `container_content_id` hi apunta tal com va arribar.
* `tender_document_reception_items`: cada fitxer de l'entrega, acceptat, omes
  (amb motiu) o obert (un contenidor intern), i el document que en va sortir.

Els elements referencien l'entrega per `(reception_id, company_id)`: res d'una
empresa pot penjar de l'entrega d'una altra.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    ReceptionChannel,
    ReceptionItemStatus,
    ReceptionSkipReason,
    ReceptionStatus,
)
from app.models.review import in_values


class TenderDocumentReception(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tender_document_receptions"
    __table_args__ = (
        sa.CheckConstraint(in_values("channel", ReceptionChannel), name="channel_valid"),
        sa.CheckConstraint(in_values("status", ReceptionStatus), name="status_valid"),
        sa.CheckConstraint(
            "item_count >= 0 AND accepted_count >= 0 AND skipped_count >= 0",
            name="counts_not_negative",
        ),
        sa.UniqueConstraint("id", "company_id", name="uq_tender_document_receptions_id_company"),
        sa.Index("ix_tender_document_receptions_scope", "company_id", "tender_id", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[ReceptionChannel] = mapped_column(
        enum_column(ReceptionChannel, "reception_channel"), nullable=False
    )
    #: Nom per ensenyar: el del ZIP, el de la carpeta...
    label: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    #: Identificador de l'entrega a l'origen (id de carpeta, URL...), si en te.
    reference: Mapped[str | None] = mapped_column(sa.String(1000))
    #: El contenidor tal com va arribar. RESTRICT: un original no es pot perdre.
    container_content_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_contents.id", ondelete="RESTRICT"),
    )
    status: Mapped[ReceptionStatus] = mapped_column(
        enum_column(ReceptionStatus, "reception_status"), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(sa.String(50))
    item_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    received_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class TenderDocumentReceptionItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tender_document_reception_items"
    __table_args__ = (
        sa.CheckConstraint(in_values("status", ReceptionItemStatus), name="status_valid"),
        sa.CheckConstraint(
            "skip_reason IS NULL OR " + in_values("skip_reason", ReceptionSkipReason),
            name="skip_reason_valid",
        ),
        # Omes vol dir amb motiu, i nomes omes.
        sa.CheckConstraint(
            "(status = 'skipped') = (skip_reason IS NOT NULL)", name="skip_reason_iff_skipped"
        ),
        # Nomes un fitxer acceptat dona un document.
        sa.CheckConstraint(
            "status = 'accepted' OR tender_document_id IS NULL", name="document_only_if_accepted"
        ),
        sa.CheckConstraint("position >= 0", name="position_not_negative"),
        sa.UniqueConstraint(
            "reception_id", "position", name="uq_tender_document_reception_items_position"
        ),
        sa.UniqueConstraint("reception_id", "path", name="uq_tender_document_reception_items_path"),
        sa.ForeignKeyConstraint(
            ["reception_id", "company_id"],
            ["tender_document_receptions.id", "tender_document_receptions.company_id"],
            ondelete="CASCADE",
        ),
        sa.Index("ix_tender_document_reception_items_document", "tender_document_id"),
    )

    reception_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: Cami dins de l'entrega, tal com el va escriure qui la va fer (amb accents).
    path: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    packed_size: Mapped[int | None] = mapped_column(sa.BigInteger)
    sha256: Mapped[str | None] = mapped_column(sa.String(64))
    detected_mime_type: Mapped[str | None] = mapped_column(sa.String(160))
    #: Id a l'origen (Drive, URL...). Nul dins d'un ZIP.
    source_reference: Mapped[str | None] = mapped_column(sa.String(1000))
    status: Mapped[ReceptionItemStatus] = mapped_column(
        enum_column(ReceptionItemStatus, "reception_item_status"), nullable=False
    )
    skip_reason: Mapped[ReceptionSkipReason | None] = mapped_column(
        enum_column(ReceptionSkipReason, "reception_skip_reason")
    )
    tender_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_documents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
