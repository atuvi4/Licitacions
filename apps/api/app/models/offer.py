"""Oferta economica d'una candidatura: l'estructura, no les xifres calculades.

Modul separat de `workspace.py` a proposit: son quatre taules amb la seva propia
jerarquia i aquell fitxer ja es prou gran.

    BidWorkspace
    └── EconomicOffer          (una per candidatura)
        └── EconomicOfferLot   (un per lot del plec, o un de global)
            └── EconomicOfferSection
                └── EconomicOfferLine

**Aqui no hi ha cap xifra calculada.** Ni totals, ni marges, ni equivalents
mensuals, ni imports d'IVA, ni "supera el maxim". Tot aixo ho calcula
`app.offers.pricing` a partir d'aquestes columnes. Persistir-ne una copia voldria
dir tenir dues versions del mateix numero, i el dia que discrepen no hi hauria
manera de saber quina mana. El que es guarda es el que ha escrit una persona.

**Les dades economiques no s'esborren** (C-9). Lots, seccions i linies porten
`archived_at`: actiu vol dir `archived_at IS NULL`. Els `CASCADE` de les claus
foranes son integritat de l'agregat —si mor la candidatura, no han de quedar
preus orfes—, no una operacio d'usuari: no hi ha cap esborrat exposat.

**La durada mai s'infereix** (C-3). `EconomicOfferLot.duration_months` l'escriu
una persona i es el valor per defecte de les seves linies;
`EconomicOfferLine.duration_months_override` la substitueix quan una linia dura
una altra cosa. Resoldre quina mana es feina d'S5: el motor de calcul nomes rep
la durada efectiva ja resolta.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    ActorMixin,
    Base,
    CompanyOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)
from app.models.enums import PricingBasis

#: Preus i costos UNITARIS, i quantitats. Quatre decimals i no dos com `MONEY`
#: (`Numeric(18, 2)`): un preu unitari de 0,0125 EUR multiplicat per 100.000
#: unitats son 1.250 EUR, i arrodonir-lo abans de multiplicar canviaria el total.
#: L'arrodoniment a dos decimals es fa en TANCAR els imports, i aixo es feina del
#: motor de calcul, no de l'esquema (C-7).
UNIT_MONEY = sa.Numeric(18, 4)

#: Un tipus impositiu en tant per cent: 21,00. Mai un factor (0,21).
VAT_RATE = sa.Numeric(5, 2)

#: Els valors de `PricingBasis`, congelats aqui per al CHECK. Repetits a proposit
#: i no derivats de l'enum: el CHECK ha de dir exactament el mateix que la
#: migracio, que si que els te congelats.
_PRICING_BASES = ("one_off", "monthly", "annual", "per_unit")

#: Una linia activa es la que encara compta. Arxivar-la no l'esborra.
_ACTIVE = "archived_at IS NULL"


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


class EconomicOffer(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base):
    """L'arrel de preus d'una candidatura. N'hi ha una i prou.

    Dues ofertes per a la mateixa candidatura voldrien dir dos preus per a la
    mateixa cosa, i cap manera de saber quin es el que es presenta.

    No s'arxiva: no es una peca de treball que es pugui descartar, es l'arrel.
    El que s'arxiva es el que hi penja.
    """

    __tablename__ = "economic_offers"
    __table_args__ = (
        # Una per candidatura. No es un index parcial: no hi ha cap estat en que
        # dues ofertes convisquin.
        sa.UniqueConstraint("workspace_id", name="uq_economic_offers_workspace"),
        sa.CheckConstraint("revision >= 0", name="revision_non_negative"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, default="EUR")
    notes: Mapped[str | None] = mapped_column(sa.Text)
    #: Compta les modificacions perque S5 pugui refusar una escriptura feta sobre
    #: una versio que ja no hi es (`expected_revision`). S4 nomes guarda el numero:
    #: aqui no l'incrementa ningu.
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    lots: Mapped[list[EconomicOfferLot]] = relationship(
        back_populates="offer", cascade="all, delete-orphan"
    )


class EconomicOfferLot(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """El bloc de preus d'un lot del plec, o el bloc unic si el plec no en te.

    `tender_lot_id` NULL vol dir "tota la licitacio". La coherencia entre el lot
    de la candidatura i els lots oferts la comprova S5 (C-1); aqui nomes hi ha
    l'estructura i la unicitat.
    """

    __tablename__ = "economic_offer_lots"
    __table_args__ = (
        sa.CheckConstraint(
            "duration_months IS NULL OR duration_months >= 1", name="duration_at_least_one"
        ),
        sa.CheckConstraint("position >= 0", name="position_non_negative"),
        sa.Index("ix_economic_offer_lots_offer", "offer_id", "position"),
        # Un lot del plec no pot tenir dos blocs de preus ACTIUS. Parcial en els
        # dos sentits: nomes les files amb lot i nomes les no arxivades, perque
        # arxivar-ne un i tornar a comencar no obligui a reescriure la historia.
        sa.Index(
            "uq_economic_offer_lots_tender_lot",
            "offer_id",
            "tender_lot_id",
            unique=True,
            postgresql_where=sa.text(f"tender_lot_id IS NOT NULL AND {_ACTIVE}"),
            sqlite_where=sa.text(f"tender_lot_id IS NOT NULL AND {_ACTIVE}"),
        ),
        # I un sol bloc global actiu: sense lots al plec hi ha UNA oferta.
        sa.Index(
            "uq_economic_offer_lots_global",
            "offer_id",
            unique=True,
            postgresql_where=sa.text(f"tender_lot_id IS NULL AND {_ACTIVE}"),
            sqlite_where=sa.text(f"tender_lot_id IS NULL AND {_ACTIVE}"),
        ),
    )

    offer_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("economic_offers.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: `RESTRICT` i no `SET NULL`: si el lot del plec pogues desapareixer deixant
    #: la fila, es perdria DE QUIN lot son aquests preus, i un bloc sense lot ja
    #: vol dir una altra cosa (tota la licitacio). Els lots del plec no s'esborren:
    #: quan cauen del feed es marquen amb `removed_at`.
    tender_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="RESTRICT")
    )
    label: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    #: Manual, mai inferida (C-3). Es la durada per defecte de les linies del lot.
    duration_months: Mapped[int | None] = mapped_column(sa.Integer)
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    offer: Mapped[EconomicOffer] = relationship(back_populates="lots")
    sections: Mapped[list[EconomicOfferSection]] = relationship(
        back_populates="lot", cascade="all, delete-orphan"
    )


class EconomicOfferSection(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """Com s'agrupa l'oferta perque una persona la pugui llegir.

    El titol es una DADA i no un enum: «Inversio inicial», «Serveis recurrents»,
    «Equipament» son exemples, no un cataleg tancat. Cada plec s'estructura com
    demana, i fixar-ho a l'esquema obligaria a migrar la base cada cop que un
    organ de contractacio ho presenti d'una altra manera.
    """

    __tablename__ = "economic_offer_sections"
    __table_args__ = (
        sa.CheckConstraint("position >= 0", name="position_non_negative"),
        sa.Index("ix_economic_offer_sections_lot", "offer_lot_id", "position"),
    )

    offer_lot_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("economic_offer_lots.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    lot: Mapped[EconomicOfferLot] = relationship(back_populates="sections")
    lines: Mapped[list[EconomicOfferLine]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )


class EconomicOfferLine(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """Una linia de preu: que oferim, quantes unitats, a quin cost i a quin preu.

    Els diners son `NULL` mentre no se sapiguen, i `NULL` no es `0`: una linia
    sense cost unitari no costa zero, no se'n sap el cost. El motor de calcul ho
    tracta com a indeterminat i ho compta a part; posar-hi zero faria quadrar un
    marge que ningu no ha calculat.

    El maxim del plec (`tender_max_unit_price_ex_vat`) es entrada MANUAL a la V1
    (C-2): no hi ha extraccio ni model de citacio. Si la linia apunta a un
    requisit, la pantalla podra ensenyar-lo al costat, i prou.
    """

    __tablename__ = "economic_offer_lines"
    __table_args__ = (
        sa.CheckConstraint(_in_clause("pricing_basis", _PRICING_BASES), name="basis_valid"),
        # Zero es valid: una linia a mig preparar en te. El que no pot ser es
        # negatiu, que no vol dir res en cap d'aquests camps.
        sa.CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        sa.CheckConstraint(
            "duration_months_override IS NULL OR duration_months_override >= 1",
            name="duration_override_at_least_one",
        ),
        sa.CheckConstraint(
            "unit_cost_ex_vat IS NULL OR unit_cost_ex_vat >= 0", name="unit_cost_non_negative"
        ),
        sa.CheckConstraint(
            "offered_unit_price_ex_vat IS NULL OR offered_unit_price_ex_vat >= 0",
            name="offered_price_non_negative",
        ),
        sa.CheckConstraint(
            "tender_max_unit_price_ex_vat IS NULL OR tender_max_unit_price_ex_vat >= 0",
            name="tender_max_non_negative",
        ),
        sa.CheckConstraint(
            "vat_rate IS NULL OR (vat_rate >= 0 AND vat_rate <= 100)", name="vat_rate_range"
        ),
        sa.CheckConstraint("position >= 0", name="position_non_negative"),
        sa.Index("ix_economic_offer_lines_section", "section_id", "position"),
        sa.Index("ix_economic_offer_lines_requirement", "requirement_id"),
        sa.Index("ix_economic_offer_lines_technical_solution", "technical_solution_id"),
        # Una sola linia VIVA per opcio. Parcial i no un UNIQUE sencer: una
        # linia arxivada conserva la seva provinenca com a historia, i alhora
        # deixa l'opcio lliure per tornar a convertir-se en una linia nova.
        sa.Index(
            "uq_economic_offer_lines_solution_option",
            "solution_option_id",
            unique=True,
            postgresql_where=sa.text(
                "solution_option_id IS NOT NULL AND archived_at IS NULL"
            ),
            sqlite_where=sa.text("solution_option_id IS NOT NULL AND archived_at IS NULL"),
        ),
    )

    section_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("economic_offer_sections.id", ondelete="CASCADE"),
        nullable=False,
    )
    concept: Mapped[str] = mapped_column(sa.String(500), nullable=False)

    #: Vincles opcionals. Si una extraccio nova se'ls emporta, el preu escrit es
    #: conserva sense vincle: el que es perd es la traça, no la feina.
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="SET NULL")
    )
    technical_solution_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        # Nom explicit: el que generaria la convencio passa dels 63 caracters de
        # PostgreSQL i quedaria truncat amb un hash, que no es estable ni es pot
        # anomenar des d'una migracio.
        sa.ForeignKey(
            "workspace_technical_solutions.id",
            ondelete="SET NULL",
            name="fk_economic_offer_lines_technical_solution",
        ),
    )

    #: Text i no claus foranes: el cataleg de proveidors i productes queda fora
    #: de l'MVP (C-10).
    supplier_name: Mapped[str | None] = mapped_column(sa.String(300))
    product_name: Mapped[str | None] = mapped_column(sa.String(300))

    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False)
    pricing_basis: Mapped[PricingBasis] = mapped_column(
        enum_column(PricingBasis, "pricing_basis"), nullable=False
    )
    #: Quan aquesta linia no dura el mateix que el lot. S5 resol quina mana.
    duration_months_override: Mapped[int | None] = mapped_column(sa.Integer)

    unit_cost_ex_vat: Mapped[Decimal | None] = mapped_column(UNIT_MONEY)
    offered_unit_price_ex_vat: Mapped[Decimal | None] = mapped_column(UNIT_MONEY)
    tender_max_unit_price_ex_vat: Mapped[Decimal | None] = mapped_column(UNIT_MONEY)
    #: En tant per cent (21,00), no en factor. `NULL` vol dir que no se sap.
    vat_rate: Mapped[Decimal | None] = mapped_column(VAT_RATE)

    notes: Mapped[str | None] = mapped_column(sa.Text)
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: D'on ve aquesta linia, si ve d'una opcio de solucio (S3-S8).
    #:
    #: **L'opcio es la configuracio viva; la linia es el snapshot adoptat.** Que
    #: despres canviin el proveidor, la quantitat o el cost de l'opcio NO toca
    #: res d'aqui: la xifra que va a l'oferta la va adoptar una persona un dia
    #: concret, i moure-la sola voldria dir canviar una oferta sense que ningu
    #: ho hagues decidit.
    #:
    #: `SET NULL`: si algun dia desapareix l'opcio, es perd la traca, no la
    #: feina. El preu escrit es conserva.
    solution_option_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "workspace_solution_options.id",
            ondelete="SET NULL",
            name="fk_economic_offer_lines_solution_option",
        ),
    )

    section: Mapped[EconomicOfferSection] = relationship(back_populates="lines")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EconomicOfferLine {self.concept[:40]} {self.pricing_basis}>"
