"""Capa documental dels plecs: original immutable i text per pagina.

Quatre entitats i una cadena que no es pot trencar:

    Tender -> TenderDocument -> File(versio) -> Content(sha256) -> Extraction -> Page

Totes son PUBLIQUES i GLOBALS: cap porta `company_id`. Si cent empreses
analitzen la mateixa licitacio, el PDF es baixa i es parseja una sola vegada.
La relacio empresa-requisit sera una taula de tenant, pero d'una altra iteracio.

Per que quatre taules i no una:

* `Content` es el blob adrecat pel SHA-256. Es la unitat de deduplicacio: els
  mateixos bytes = un sol objecte a l'storage, apuntin-hi els documents que
  apuntin.
* `Extraction` es UNA passada de parser sobre aquell blob. Canviar de
  `parser_version` crea una extraccio nova i conserva l'anterior sencera: el
  text ja extret no s'ha de poder perdre per un canvi de biblioteca.
* `Page` penja de l'extraccio, no del contingut, per la mateixa rao.
* `File` es la versio: que s'ha baixat, d'on, quan i amb quin resultat HTTP.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
)

# Els indexs unics parcials s'han de declarar per als dos dialectes: la suite
# corre sobre SQLite i produccio sobre PostgreSQL, i la sintaxi del predicat
# boolea no coincideix.
_IN_FLIGHT = "download_status IN ('pending', 'downloading')"


class TenderDocumentContent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bytes descarregats, identificats pel seu SHA-256.

    No te cap estat d'extraccio: aquesta taula nomes sap fets immutables sobre
    una sequencia de bytes.
    """

    __tablename__ = "tender_document_contents"
    __table_args__ = (sa.UniqueConstraint("sha256", name="uq_tender_document_contents_sha256"),)

    sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Clau interna de l'storage. MAI s'exposa per l'API.
    storage_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    #: Tipus DETECTAT pels magic bytes, no el que declarava el servidor.
    mime_type: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    detected_extension: Mapped[str | None] = mapped_column(sa.String(10))
    is_text_extractable: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    extractions: Mapped[list[TenderDocumentExtraction]] = relationship(
        back_populates="content", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderDocumentContent {self.sha256[:12]}>"


class TenderDocumentExtraction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una passada de parser sobre un `Content`.

    `UNIQUE (content_id, parser_version)`: una versio de parser, una extraccio.
    Un re-extract amb la mateixa versio substitueix les pagines d'aquesta fila,
    perque el parser es determinista i repetir-lo nomes te sentit per recuperar
    un error. Un canvi de versio, en canvi, crea una fila nova.
    """

    __tablename__ = "tender_document_extractions"
    __table_args__ = (
        sa.UniqueConstraint(
            "content_id", "parser_version", name="uq_tender_document_extractions_content_parser"
        ),
        sa.Index("ix_tender_document_extractions_content_id", "content_id"),
        sa.Index(
            "uq_tender_document_extractions_current",
            "content_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
        ),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_contents.id", ondelete="CASCADE"),
        nullable=False,
    )
    parser_version: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    status: Mapped[ExtractionStatus] = mapped_column(
        enum_column(ExtractionStatus, "extraction_status"),
        nullable=False,
        default=ExtractionStatus.PENDING,
    )
    quality: Mapped[ExtractionQuality] = mapped_column(
        enum_column(ExtractionQuality, "extraction_quality"),
        nullable=False,
        default=ExtractionQuality.NOT_APPLICABLE,
    )
    page_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    extracted_page_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    empty_page_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    text_char_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Indici, no prova: poc text NO demostra que calgui OCR.
    ocr_candidate: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    error_code: Mapped[str | None] = mapped_column(sa.String(50))
    error_message: Mapped[str | None] = mapped_column(sa.Text)
    parse_ms: Mapped[int | None] = mapped_column(sa.Integer)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    content: Mapped[TenderDocumentContent] = relationship(back_populates="extractions")
    pages: Mapped[list[TenderDocumentPage]] = relationship(
        back_populates="extraction",
        cascade="all, delete-orphan",
        order_by="TenderDocumentPage.page_number",
    )


class TenderDocumentPage(UUIDPrimaryKeyMixin, Base):
    """El text d'una pagina.

    `page_number` es **1-based**, igual a la base de dades i a la interficie.
    Aquesta decisio no es estetica: la fase seguent haura de poder afirmar
    "solvencia economica, PCAP pagina 27" i que sigui verificable. Amb una capa
    de traduccio entremig, un off-by-one seria invisible fins que algu obris el
    PDF i comptes.
    """

    __tablename__ = "tender_document_pages"
    __table_args__ = (
        sa.UniqueConstraint(
            "extraction_id", "page_number", name="uq_tender_document_pages_extraction_page"
        ),
        sa.CheckConstraint("page_number >= 1", name="page_number_is_one_based"),
        sa.Index("ix_tender_document_pages_extraction_id", "extraction_id"),
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    char_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    text_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[PageExtractionStatus] = mapped_column(
        enum_column(PageExtractionStatus, "page_extraction_status"),
        nullable=False,
        default=PageExtractionStatus.EMPTY,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    extraction: Mapped[TenderDocumentExtraction] = relationship(back_populates="pages")


class TenderDocumentFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una versio fisica d'un `TenderDocument`: que s'ha baixat, d'on i quan.

    Una URL NO es un fitxer immutable per sempre. Un organ pot substituir un
    plec sense canviar l'enllac, i llavors cal conservar les dues versions.

    L'index unic parcial sobre els estats en curs es el lock de concurrencia:
    dos requests simultanis no poden reclamar el mateix document, i el segon
    rep un 409 en comptes de duplicar la feina.
    """

    __tablename__ = "tender_document_files"
    __table_args__ = (
        sa.UniqueConstraint(
            "tender_document_id",
            "version_number",
            name="uq_tender_document_files_document_version",
        ),
        sa.Index("ix_tender_document_files_tender_document_id", "tender_document_id"),
        sa.Index("ix_tender_document_files_content_id", "content_id"),
        sa.Index(
            "uq_tender_document_files_in_flight",
            "tender_document_id",
            unique=True,
            postgresql_where=sa.text(_IN_FLIGHT),
            sqlite_where=sa.text(_IN_FLIGHT),
        ),
    )

    tender_document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RESTRICT i no CASCADE: un blob referenciat no s'ha de poder esborrar.
    content_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_contents.id", ondelete="RESTRICT"),
    )
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("source_records.id", ondelete="SET NULL")
    )

    source_url: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    final_url: Mapped[str | None] = mapped_column(sa.String(1000))
    original_filename: Mapped[str | None] = mapped_column(sa.String(255))
    detected_filename: Mapped[str | None] = mapped_column(sa.String(255))
    declared_mime_type: Mapped[str | None] = mapped_column(sa.String(160))

    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    download_status: Mapped[DownloadStatus] = mapped_column(
        enum_column(DownloadStatus, "download_status"),
        nullable=False,
        default=DownloadStatus.PENDING,
    )
    http_status: Mapped[int | None] = mapped_column(sa.Integer)
    redirect_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    download_ms: Mapped[int | None] = mapped_column(sa.Integer)
    downloaded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Quan s'ha reclamat el processat. Permet detectar processos morts.
    started_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    #: Ultima vegada que una re-descarrega ha confirmat el mateix hash.
    last_checked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(sa.String(50))
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    content: Mapped[TenderDocumentContent | None] = relationship(lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderDocumentFile v{self.version_number} {self.download_status}>"
