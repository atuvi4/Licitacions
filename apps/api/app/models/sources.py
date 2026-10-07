"""Fonts de dades, execucions d'ingesta i magatzem cru (raw store).

Aquestes taules son la memoria del sistema sobre *que hem rebut* i *quan*,
separada del model canonic (que es la nostra interpretacio d'aixo). Si algun
dia el parser millora, el raw segueix aqui per tornar-lo a processar.

Cap d'aquestes entitats te `company_id`: son dades publiques i globals.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import IngestionStatus, ProcessingStatus, SourceType


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una font de dades externa. Avui nomes PLACSP; despres TED i d'altres."""

    __tablename__ = "sources"
    __table_args__ = (sa.UniqueConstraint("code", name="uq_sources_code"),)

    code: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        enum_column(SourceType, "source_type"), nullable=False, default=SourceType.SYNDICATION_ATOM
    )
    base_url: Mapped[str | None] = mapped_column(sa.String(500))
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    # On es va quedar l'ultima ingesta. El format depen de la font: per a PLACSP
    # es l'URL de l'ultim fitxer ATOM processat i la marca de temps maxima vista.
    checkpoint: Mapped[str | None] = mapped_column(sa.String(1000))
    checkpoint_updated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # --- Salut de la sincronitzacio (0055). Tot `NULL`/0 = encara no s'ha sincronitzat.
    #: El `<updated>` del feed a l'ultima lectura.
    last_feed_updated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_sync_started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_sync_finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: `success` | `partial` | `failed`. Text pla, no un enum.
    last_sync_status: Mapped[str | None] = mapped_column(sa.String(20))
    last_sync_pages: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    last_sync_entries: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    last_sync_tombstones: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    last_sync_error: Mapped[str | None] = mapped_column(sa.Text)
    #: Marca d'aigua de l'`updated` de les entrades ja processades amb exit.
    updated_watermark: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Lease per font: qui sincronitza ara i fins quan.
    sync_lease_owner: Mapped[str | None] = mapped_column(sa.String(120))
    sync_lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Source {self.code}>"


class IngestionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Auditoria d'una execucio d'ingesta.

    Ha de permetre respondre "que va passar a la ingesta de les 06:00?" sense
    haver de mirar logs.
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (sa.Index("ix_ingestion_runs_source_started", "source_id", "started_at"),)

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[IngestionStatus] = mapped_column(
        enum_column(IngestionStatus, "ingestion_status"),
        nullable=False,
        default=IngestionStatus.RUNNING,
    )

    records_fetched: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    records_new: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    records_changed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    records_unchanged: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tenders_created: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tenders_updated: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    # Comptadors de la sincronitzacio radar (0055).
    pages_processed: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    records_superseded: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    records_conflicted: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    tombstones_applied: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    entries_restored: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )

    error_summary: Mapped[str | None] = mapped_column(sa.Text)
    checkpoint_before: Mapped[str | None] = mapped_column(sa.String(1000))
    checkpoint_after: Mapped[str | None] = mapped_column(sa.String(1000))
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    # Que es va demanar (limit, fixture, from-date...). Text pla, per auditoria.
    parameters: Mapped[str | None] = mapped_column(sa.String(500))

    source: Mapped[Source] = relationship(lazy="joined")


class SourceRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una versio concreta d'una entrada rebuda d'una font. IMMUTABLE.

    Si dema la mateixa licitacio arriba amb un contingut diferent, es crea un
    SourceRecord nou; el raw anterior NO es toca. Aixo permet reconstruir que
    sabia el sistema en qualsevol moment.

    El XML original va a l'object storage (`raw_storage_key`); aqui nomes hi ha
    identificadors, metadata i el hash.
    """

    __tablename__ = "source_records"
    __table_args__ = (
        # Idempotencia garantida per la base de dades, no nomes per codi Python:
        # el mateix contingut de la mateixa entrada no es pot inserir dues vegades.
        sa.UniqueConstraint(
            "source_id", "external_id", "raw_hash", name="uq_source_records_content"
        ),
        sa.UniqueConstraint(
            "source_id", "external_id", "revision_number", name="uq_source_records_revision"
        ),
        sa.Index("ix_source_records_source_external", "source_id", "external_id"),
        sa.Index("ix_source_records_raw_hash", "raw_hash"),
        sa.Index("ix_source_records_fetched_at", "fetched_at"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    external_version_id: Mapped[str | None] = mapped_column(sa.String(200))
    revision_number: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    source_url: Mapped[str | None] = mapped_column(sa.String(1000))
    fetched_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    source_published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    raw_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    raw_storage_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    raw_size: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, default=0)
    mime_type: Mapped[str] = mapped_column(sa.String(160), nullable=False, default="application/xml")

    parser_version: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        enum_column(ProcessingStatus, "processing_status"),
        nullable=False,
        default=ProcessingStatus.PENDING,
    )
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL")
    )
    # S'omple despres de normalitzar. Permet llistar les revisions d'un Tender
    # sense taula addicional.
    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tenders.id", ondelete="SET NULL"),
        index=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SourceRecord {self.external_id} rev{self.revision_number}>"
