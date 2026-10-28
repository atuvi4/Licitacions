"""Model canonic de licitacions.

Aquest model es NOSTRE, no de PLACSP. Els noms de camp no imiten l'XML de
CODICE: hi ha un normalitzador entremig precisament perque dema TED o una
plataforma autonomica puguin alimentar les mateixes taules.

Gairebe totes aquestes entitats son PUBLIQUES i GLOBALS. L'excepcio es
`Tender.owner_company_id`: una licitacio introduida a ma per una empresa nomes
la veu aquella empresa. `NULL` vol dir publica, que es el que son totes les que
arriben d'una font sindicada. La relacio empresa-licitacio (match, workspace)
continua sent una taula a part.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    ContractType,
    DocumentContributionStatus,
    ProcedureType,
    TenderDocumentOrigin,
    TenderDocumentType,
    TenderStatus,
)
from app.models.review import in_values

MONEY = sa.Numeric(18, 2)


class Buyer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Organ de contractacio.

    La identitat es `(source_id, external_identifier)`. Per a PLACSP,
    l'identificador es l'ID_PLATAFORMA de l'organ: el NIF no serveix com a
    identitat perque un mateix ajuntament te diversos organs de contractacio
    amb el mateix NIF.

    No es fa cap fuzzy matching de noms: si la font no dona identificador
    fiable, es crea una entitat nova i s'assumeix el duplicat.
    """

    __tablename__ = "buyers"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "external_identifier", name="uq_buyers_source_external"),
        sa.Index("ix_buyers_name", "name"),
        sa.Index("ix_buyers_tax_identifier", "tax_identifier"),
        sa.Index("ix_buyers_owner_company_id", "owner_company_id"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    #: Qui el pot veure, amb la mateixa regla que `Tender.owner_company_id`:
    #: `NULL` es public i canonic, amb valor es privat de l'empresa que el va
    #: introduir a ma. Un organisme public es informacio publica, pero el que
    #: una empresa escriu al seu wizard no ho es fins que algu ho valida, i
    #: publicar-ho al cataleg global en silenci seria decidir-ho per ella.
    owner_company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")
    )
    external_identifier: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    tax_identifier: Mapped[str | None] = mapped_column(sa.String(64))
    # Codi DIR3 de l'administracio, quan la font el dona.
    registry_identifier: Mapped[str | None] = mapped_column(sa.String(64))
    buyer_type: Mapped[str | None] = mapped_column(sa.String(120))
    activity: Mapped[str | None] = mapped_column(sa.String(120))
    website: Mapped[str | None] = mapped_column(sa.String(500))
    buyer_profile_url: Mapped[str | None] = mapped_column(sa.String(1000))
    address: Mapped[str | None] = mapped_column(sa.String(500))
    city: Mapped[str | None] = mapped_column(sa.String(200))
    postal_code: Mapped[str | None] = mapped_column(sa.String(16))
    country_code: Mapped[str | None] = mapped_column(sa.String(2))
    nuts_code: Mapped[str | None] = mapped_column(sa.String(10))
    # Jerarquia administrativa tal com la publica la font ("Sector Publico >
    # Entidades Locales > ..."), sense interpretar.
    hierarchy: Mapped[str | None] = mapped_column(sa.String(1000))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Buyer {self.name}>"


class Tender(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un expedient de licitacio."""

    __tablename__ = "tenders"
    __table_args__ = (
        sa.UniqueConstraint("source_id", "external_id", name="uq_tenders_source_external"),
        sa.Index("ix_tenders_publication_date", "publication_date"),
        sa.Index("ix_tenders_deadline_at", "deadline_at"),
        sa.Index("ix_tenders_buyer_id", "buyer_id"),
        sa.Index("ix_tenders_status", "status"),
        sa.Index("ix_tenders_procedure_type", "procedure_type"),
        sa.Index("ix_tenders_contract_type", "contract_type"),
        sa.Index("ix_tenders_estimated_value", "estimated_value"),
        sa.Index("ix_tenders_expediente_number", "expediente_number"),
        sa.Index("ix_tenders_owner_company_id", "owner_company_id"),
        sa.Index("ix_tenders_first_seen_at", "first_seen_at"),
        sa.Index("ix_tenders_source_updated_at", "source_updated_at"),
        sa.CheckConstraint(
            "estimated_value IS NULL OR estimated_value >= 0",
            name="tender_estimated_value_positive",
        ),
        sa.CheckConstraint(
            "duration_months IS NULL OR duration_months >= 0", name="tender_duration_not_negative"
        ),
        sa.CheckConstraint(
            "extension_months IS NULL OR extension_months >= 0",
            name="tender_extension_not_negative",
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    #: Qui la pot veure. `NULL` es publica; amb valor, nomes aquella empresa.
    #:
    #: Viu aqui i no a `Source` a proposit: la font es PROVINENCA —d'on han
    #: sortit les dades— i barrejar-hi autoritzacio faria que esborrar una
    #: empresa arrossegues la font i, amb ella, licitacions d'altri.
    owner_company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE")
    )
    external_id: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    # Numero d'expedient que dona l'organisme. NO es identificador unic: dos
    # organismes diferents poden fer servir el mateix (verificat a dades reals).
    expediente_number: Mapped[str | None] = mapped_column(sa.String(200))

    # Text lliure de la font (`ProcurementProject/Name`), sense limit: n'hi ha de
    # 1.999 caracters. Text des de 0057; mai es trunca.
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)

    buyer_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("buyers.id", ondelete="SET NULL")
    )

    procedure_type: Mapped[ProcedureType] = mapped_column(
        enum_column(ProcedureType, "procedure_type"),
        nullable=False,
        default=ProcedureType.UNKNOWN,
    )
    contract_type: Mapped[ContractType] = mapped_column(
        enum_column(ContractType, "contract_type"), nullable=False, default=ContractType.UNKNOWN
    )
    status: Mapped[TenderStatus] = mapped_column(
        enum_column(TenderStatus, "tender_status"), nullable=False, default=TenderStatus.UNKNOWN
    )
    # Codi original de la font, sense traduir. Serveix per depurar el mapatge.
    source_status_code: Mapped[str | None] = mapped_column(sa.String(50))

    # Valor estimat del contracte (inclou prorrogues i modificacions previstes).
    estimated_value: Mapped[Decimal | None] = mapped_column(MONEY)
    # Pressupost base de licitacio.
    base_budget: Mapped[Decimal | None] = mapped_column(MONEY)
    base_budget_without_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="EUR")

    publication_date: Mapped[date | None] = mapped_column(sa.Date)
    deadline_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    place_of_execution: Mapped[str | None] = mapped_column(sa.String(500))
    nuts_code: Mapped[str | None] = mapped_column(sa.String(10), index=True)
    country_code: Mapped[str | None] = mapped_column(sa.String(2))

    source_url: Mapped[str | None] = mapped_column(sa.String(1000))
    latest_source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("source_records.id", ondelete="SET NULL", use_alter=True),
    )
    #: Quantes vegades la FONT ha publicat aquest expedient (`SourceRecord`). NO
    #: es el nombre de revisions del cicle de vida: `tender_revisions` en te de
    #: propies, que compten tambe el que aprenem nosaltres —per exemple baixar
    #: un plec i saber-ne el SHA—. Son dues histories diferents i no es poden
    #: presentar totes dues com "versions": ho feien, i la pantalla deia
    #: "Versions 1" al costat de "Versio 2".
    revision_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    first_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    last_changed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # --- Estructura del contracte (0048). Tot `NULL` = no se sap. ------------
    #: Durada del contracte en mesos, sense prorrogues.
    duration_months: Mapped[int | None] = mapped_column(sa.Integer)
    #: Prorrogues previstes, en mesos.
    extension_months: Mapped[int | None] = mapped_column(sa.Integer)
    #: Tri-estat: `True` = regulacio harmonitzada (SARA), `False` = el plec diu
    #: expressament que no, `NULL` = no se sap. Sense cap `False` per defecte.
    regulated_harmonised: Mapped[bool | None] = mapped_column(sa.Boolean)
    #: Inici previst de l'execucio, si el plec el dona.
    planned_start_date: Mapped[date | None] = mapped_column(sa.Date)

    # --- Provenance canonica i cerca (0055). ---------------------------------
    #: El `updated` de la font de l'entrada aplicada. Un `updated` anterior no regressa.
    source_updated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Presencia a la font (tombstone). NO es un estat: no canvia `status`.
    source_removed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Provenance especifica de la font, claus tancades (res de PLACSP com a columna).
    source_metadata: Mapped[dict | None] = mapped_column(
        sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True), "postgresql")
    )
    #: Text de cerca normalitzat a l'aplicacio (NFKD, sense diacritics, casefold).
    search_text: Mapped[str | None] = mapped_column(sa.Text)

    buyer: Mapped[Buyer | None] = relationship(lazy="joined")
    lots: Mapped[list[TenderLot]] = relationship(
        back_populates="tender",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TenderLot.lot_number",
    )
    cpvs: Mapped[list[TenderCPV]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", lazy="selectin"
    )
    documents: Mapped[list[TenderDocument]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", lazy="selectin"
    )
    #: Estructura del plec (0048): blocs no necessariament adjudicables i seus.
    blocks: Mapped[list[TenderBlock]] = relationship(  # noqa: F821
        back_populates="tender",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TenderBlock.code",
    )
    sites: Mapped[list[TenderSite]] = relationship(  # noqa: F821
        back_populates="tender",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TenderSite.code",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tender {self.expediente_number or self.external_id}>"


class TenderLot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Lot d'un expedient. Entitat consultable, no un JSON dins de Tender:
    el matching futur sovint es fara a nivell de lot."""

    __tablename__ = "tender_lots"
    __table_args__ = (
        sa.UniqueConstraint("tender_id", "external_lot_id", name="uq_tender_lots_tender_external"),
        sa.Index("ix_tender_lots_tender_id", "tender_id"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    external_lot_id: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    lot_number: Mapped[int | None] = mapped_column(sa.Integer)
    # Text lliure de la font, sense limit (pagina 9 del pilot: 1.017 i 1.076
    # caracters). Text des de 0057; mai es trunca.
    title: Mapped[str | None] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text)

    estimated_value: Mapped[Decimal | None] = mapped_column(MONEY)
    base_budget: Mapped[Decimal | None] = mapped_column(MONEY)
    base_budget_without_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="EUR")

    place_of_execution: Mapped[str | None] = mapped_column(sa.String(500))
    nuts_code: Mapped[str | None] = mapped_column(sa.String(10))
    deadline_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(sa.String(50))
    #: Quan el lot va deixar de ser al feed. La fila NO s'esborra: una
    #: candidatura pot apuntar-hi, i esborrar-la se l'enduria per davant amb
    #: totes les seves decisions. Marcar-ho permet dir-ho a qui hi treballava.
    removed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    @property
    def is_removed(self) -> bool:
        return self.removed_at is not None

    tender: Mapped[Tender] = relationship(back_populates="lots")
    cpvs: Mapped[list[TenderLotCPV]] = relationship(
        back_populates="lot", cascade="all, delete-orphan", lazy="selectin"
    )


class TenderCPV(TimestampMixin, Base):
    """CPV d'un expedient.

    Ull amb la confusio: aixo es *que es* una licitacio. `CompanyCPVPreference`
    es *que interessa* a una empresa. Son coses diferents i no s'han de barrejar.
    """

    __tablename__ = "tender_cpvs"
    __table_args__ = (sa.Index("ix_tender_cpvs_cpv_code", "cpv_code"),)

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    cpv_code: Mapped[str] = mapped_column(
        sa.String(10), sa.ForeignKey("cpv_codes.code", ondelete="CASCADE"), primary_key=True
    )
    is_main: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    tender: Mapped[Tender] = relationship(back_populates="cpvs")


class TenderLotCPV(TimestampMixin, Base):
    """CPV d'un lot concret."""

    __tablename__ = "tender_lot_cpvs"
    __table_args__ = (sa.Index("ix_tender_lot_cpvs_cpv_code", "cpv_code"),)

    tender_lot_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="CASCADE"), primary_key=True
    )
    cpv_code: Mapped[str] = mapped_column(
        sa.String(10), sa.ForeignKey("cpv_codes.code", ondelete="CASCADE"), primary_key=True
    )
    is_main: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)

    lot: Mapped[TenderLot] = relationship(back_populates="cpvs")


class TenderDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Metadata d'un document publicat d'una licitacio.

    En aquesta fase NOMES es guarda la URL i la metadata: no es descarrega el
    fitxer, no s'extreu text i no s'analitza res. Baixar plecs massivament
    costa temps i diners i nomes te sentit despres d'un pre-filtre.
    """

    __tablename__ = "tender_documents"
    __table_args__ = (
        sa.UniqueConstraint("tender_id", "url", name="uq_tender_documents_tender_url"),
        sa.Index("ix_tender_documents_tender_id", "tender_id"),
        # Paquet documental public (0058). La coherencia la imposa la base. Des de 0065,
        # `portal` (el connector del portal oficial) es com el feed: el governa una font i no
        # passa per la verificacio de l'operador.
        sa.CheckConstraint(in_values("origin", TenderDocumentOrigin), name="origin_valid"),
        sa.CheckConstraint(
            in_values("contribution_status", DocumentContributionStatus),
            name="contribution_status_valid",
        ),
        sa.CheckConstraint(
            "(contribution_status = 'not_applicable') = "
            "(origin IN ('feed', 'owner_upload', 'portal'))",
            name="contribution_matches_origin",
        ),
        sa.CheckConstraint(
            "(verified_at IS NOT NULL) = (contribution_status = 'verified')",
            name="verified_at_matches_status",
        ),
        sa.CheckConstraint(
            "(origin IN ('feed', 'portal')) = (governing_source_id IS NOT NULL)",
            name="feed_has_governing_source",
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    tender_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="SET NULL")
    )
    external_id: Mapped[str | None] = mapped_column(sa.String(200))
    name: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    document_type: Mapped[TenderDocumentType] = mapped_column(
        enum_column(TenderDocumentType, "tender_document_type"),
        nullable=False,
        default=TenderDocumentType.OTHER,
    )
    source_document_type_code: Mapped[str | None] = mapped_column(sa.String(50))
    url: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(sa.String(160))
    published_at: Mapped[date | None] = mapped_column(sa.Date)
    #: Absencia observada en una publicacio completa posterior (0055). Mai delete fisic.
    removed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("source_records.id", ondelete="SET NULL")
    )

    # --- Paquet documental public (0058) ------------------------------------
    #: Qui l'ha fet arribar. Els documents d'abans de 0058 son `feed` (o
    #: `owner_upload` si la URL es `upload://`).
    origin: Mapped[TenderDocumentOrigin] = mapped_column(
        enum_column(TenderDocumentOrigin, "tender_document_origin"),
        nullable=False,
        default=TenderDocumentOrigin.FEED,
        server_default=TenderDocumentOrigin.FEED.value,
    )
    #: Source ownership: el connector que governa el document. NOMES els `feed` i
    #: els `portal` en tenen (0065); la sincronitzacio d'una font nomes toca els seus.
    governing_source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id", ondelete="RESTRICT")
    )
    contribution_status: Mapped[DocumentContributionStatus] = mapped_column(
        enum_column(DocumentContributionStatus, "document_contribution_status"),
        nullable=False,
        default=DocumentContributionStatus.NOT_APPLICABLE,
        server_default=DocumentContributionStatus.NOT_APPLICABLE.value,
    )
    contributed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    contributed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Pagina oficial d'on s'ha tret (perfil del contractant, PSCP).
    contribution_source_url: Mapped[str | None] = mapped_column(sa.String(1000))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    verification_note: Mapped[str | None] = mapped_column(sa.String(500))
    #: Alies: mateixos bytes vigents que un altre document del mateix tender.
    #: Mai se selecciona per a l'analisi.
    duplicate_of_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_documents.id", ondelete="SET NULL")
    )
    #: Retirada per l'operador (la del feed continua sent `removed_at` sense actor).
    retired_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    retired_reason: Mapped[str | None] = mapped_column(sa.String(200))

    tender: Mapped[Tender] = relationship(back_populates="documents")


@sa.event.listens_for(TenderDocument, "before_insert")
def _default_origin(mapper, connection, target: TenderDocument) -> None:
    """Valor per defecte TRANSITORI d'`origin` per als qui creen documents d'una font.

    Les Tasks 2-3 de 3B-2 posen `origin` i `governing_source_id` explicitament (la
    ingesta `placsp/service.py` ja ho fa des de la Task 2); el listener existeix
    perque els altres creadors (`tender_document_upload.py`, `tender_gold.py` i
    `scripts/seed_fixture_plec.py`) segueixin funcionant sense conixer les columnes
    noves. Nomes cobreix feed i owner_upload; mai inventa un origen d'operador:

    * `origin` informat: no es toca res.
    * URL `upload://` -> `owner_upload` (sense font governant).
    * URL `operator-upload://` -> error (cal `origin` explicit).
    * resta -> `feed`, amb `governing_source_id` = font de la licitacio, tret que
      la font sigui MANUAL (error: un document de licitacio manual no es de cap feed).
    """
    if target.origin is not None:
        return
    url = target.url or ""
    if url.startswith("operator-upload://"):
        raise ValueError("origin obligatori per a documents d'operador")
    if url.startswith("upload://"):
        target.origin = TenderDocumentOrigin.OWNER_UPLOAD
        return
    from app.models.enums import SourceType
    from app.models.sources import Source

    source_id, source_type = connection.execute(
        sa.select(Tender.source_id, Source.source_type)
        .join(Source, Source.id == Tender.source_id)
        .where(Tender.id == target.tender_id)
    ).one()
    if source_type == SourceType.MANUAL:
        raise ValueError("origin obligatori per a documents d'operador")
    target.origin = TenderDocumentOrigin.FEED
    if target.governing_source_id is None:
        target.governing_source_id = source_id
