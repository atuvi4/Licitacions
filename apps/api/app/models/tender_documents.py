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
    DocumentFileOrigin,
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
    """Una passada de parser sobre un `Content`. **Immutable un cop escrita.**

    Cada execucio real d'extraccio crea una fila nova amb el seu
    `revision_number`, encara que el `content_id` i la `parser_version` siguin
    els mateixos. Les pagines d'una extraccio anterior no es toquen mai.

        Content -> revisio 1 (pypdf-v1)   -> Pages
                -> revisio 2 (pypdf-v1)   -> Pages
                -> revisio 3 (pdfminer-v2)-> Pages

    El motiu no es historic sino de citacio: la fase de requisits apuntara a una
    `Extraction` i a una `Page` concretes, i aquella evidencia no pot canviar
    sota els peus de qui l'ha citada. Per aixo `UNIQUE (content_id,
    revision_number)` i no `(content_id, parser_version)`.

    Nomes una revisio es `is_current`, i nomes hi arriba si ha acabat be: una
    reextraccio fallida no destrona la que ja funcionava.
    """

    __tablename__ = "tender_document_extractions"
    __table_args__ = (
        sa.UniqueConstraint(
            "content_id", "revision_number", name="uq_tender_document_extractions_content_revision"
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
    #: 1-based i monoton per `content_id`, independentment del parser. Identifica
    #: la revisio que una cita futura podra referenciar sense ambiguitat.
    revision_number: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
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

    #: Veredicte de la QA del text: `good`, `warning` o `poor`. Pertany a
    #: AQUESTA revisio: una re-extraccio en crea una de nova amb la seva propia
    #: qualitat, i la d'abans es conserva tal com estava.
    qa_status: Mapped[str | None] = mapped_column(sa.String(10))
    #: Amb quines heuristiques s'ha avaluat. Dues qualitats calculades amb
    #: criteris diferents no son comparables.
    qa_version: Mapped[str | None] = mapped_column(sa.String(40))

    content: Mapped[TenderDocumentContent] = relationship(back_populates="extractions")
    pages: Mapped[list[TenderDocumentPage]] = relationship(
        back_populates="extraction",
        cascade="all, delete-orphan",
        order_by="TenderDocumentPage.page_number",
    )
    qa_issues: Mapped[list[TenderExtractionIssue]] = relationship(
        back_populates="extraction",
        cascade="all, delete-orphan",
        order_by="TenderExtractionIssue.page_number",
    )


class TenderExtractionIssue(UUIDPrimaryKeyMixin, Base):
    """Un senyal que el text extret pot no ser complet.

    No diu res del contingut juridic del plec: diu que **aquesta** passada de
    parser potser s'ha deixat alguna cosa que al PDF es veu. El cas que ho
    motiva: una pagina que anuncia una formula i on la formula es una imatge.

    Va lligat a una `TenderDocumentExtraction` concreta i no al document, perque
    la qualitat es una propietat de com es va llegir, no del document en si. Una
    re-extraccio amb un parser millor genera issues nous sense esborrar els
    antics: es el que permet dir despres si una extraccio va millorar.
    """

    __tablename__ = "tender_extraction_issues"
    __table_args__ = (
        sa.Index("ix_tender_extraction_issues_extraction_id", "extraction_id"),
        sa.Index("ix_tender_extraction_issues_code", "code"),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number >= 1", name="issue_page_is_one_based"
        ),
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: `NULL` quan afecta el document sencer i no una pagina concreta.
    page_number: Mapped[int | None] = mapped_column(sa.Integer)
    code: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: Les xifres que sostenen el senyal, per poder-lo explicar sense recalcular.
    detail: Mapped[dict | None] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    extraction: Mapped[TenderDocumentExtraction] = relationship(back_populates="qa_issues")


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
        # Un sol fitxer vigent per document (3B-2 §2.4, 0058). Qui en fa vigent un
        # de nou ha de destronar l'anterior ABANS (l'index no es diferible).
        sa.Index(
            "uq_tender_document_files_current",
            "tender_document_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
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

    #: Nul quan el fitxer l'ha pujat una persona: no ve de cap URL.
    source_url: Mapped[str | None] = mapped_column(sa.String(1000))
    #: Provinenca del blob. Sense aquest camp, `source_url IS NULL` voldria dir
    #: alhora "pujat" i "descarrega mal registrada".
    origin: Mapped[DocumentFileOrigin] = mapped_column(
        enum_column(DocumentFileOrigin, "document_file_origin"),
        nullable=False,
        default=DocumentFileOrigin.FETCHED,
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
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
