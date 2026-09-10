"""Model canonic de licitacions.

Aquest model es NOSTRE, no de PLACSP. Els noms de camp no imiten l'XML de
CODICE: hi ha un normalitzador entremig precisament perque dema TED o una
plataforma autonomica puguin alimentar les mateixes taules.

Totes aquestes entitats son PUBLIQUES i GLOBALS: cap d'elles te `company_id`.
La relacio amb una empresa (match, guardat, workspace) sera una taula a part en
una iteracio futura.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import ContractType, ProcedureType, TenderDocumentType, TenderStatus

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
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
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
        sa.CheckConstraint(
            "estimated_value IS NULL OR estimated_value >= 0", name="tender_estimated_value_positive"
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    # Numero d'expedient que dona l'organisme. NO es identificador unic: dos
    # organismes diferents poden fer servir el mateix (verificat a dades reals).
    expediente_number: Mapped[str | None] = mapped_column(sa.String(200))

    title: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
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
    revision_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    first_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    last_changed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

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
    title: Mapped[str | None] = mapped_column(sa.String(1000))
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

    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("source_records.id", ondelete="SET NULL")
    )

    tender: Mapped[Tender] = relationship(back_populates="documents")
