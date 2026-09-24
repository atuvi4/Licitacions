"""Opcions de solució: proveïdor, producte, cotització i tria (S3-S7).

Una **opció** és una via possible per resoldre una part de la candidatura:
Orange o Sewan, Yealink o Jabra. Diu qui la serveix, què és, quanta en cal, què
costa i en quin punt és la cotització. Quan una persona en tria una, ho diu aquí.

Dues coses que aquesta taula **no** és:

* **No és una línia d'oferta.** No hi ha `unit_price`, ni marge, ni markup. El
  preu de venda viu a `EconomicOfferLine` i prou. Tenir-lo a tots dos llocs
  voldria dir que un dia dirien coses diferents i ningú no sabria quina mana.
* **No és un catàleg de proveïdors.** `supplier_name` i `product_name` són text
  lliure, sense master ni normalització. Un CRM no és el que falta per preparar
  una licitació.

`unit_cost_ex_vat` és **economia interna**: el que ens costa a nosaltres. Segueix
el contracte d'Sprint 2 —viatja dins d'`internal_economics` o no viatja—, perquè
"no ho pots veure" i "encara no ho sabem" no es poden confondre.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    CompanyOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)
from app.models.enums import OptionSelection, PricingBasis, QuoteStatus


class WorkspaceSolutionOption(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """Una via possible, amb el seu proveïdor, el seu cost i la seva tria."""

    __tablename__ = "workspace_solution_options"
    __table_args__ = (
        # Els valors dels enums els imposa la BASE, no només Python.
        # `enum_column()` es materialitza com un VARCHAR sense CHECK (SQLAlchemy
        # només el crea amb `create_constraint=True`), i sense aquests dos
        # constraints un INSERT que no passi per l'ORM hi podria escriure
        # qualsevol cadena que capigués al VARCHAR.
        sa.CheckConstraint(
            "quote_status IN ({})".format(
                ", ".join(f"'{member.value}'" for member in QuoteStatus)
            ),
            name="quote_status_valid",
        ),
        sa.CheckConstraint(
            "selection IN ({})".format(
                ", ".join(f"'{member.value}'" for member in OptionSelection)
            ),
            name="selection_valid",
        ),
        # Descartar sense dir per què deixaria l'alternativa morta i inexplicable:
        # d'aquí a sis mesos ningú no sabria si es va mirar o es va oblidar.
        sa.CheckConstraint(
            "selection <> 'rejected' OR selection_reason IS NOT NULL",
            name="rejected_has_reason",
        ),
        sa.Index("ix_workspace_solution_options_workspace", "workspace_id", "archived_at"),
        sa.Index("ix_workspace_solution_options_solution", "technical_solution_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: La solució tècnica que aquesta opció materialitza, si n'hi ha. `SET NULL`:
    #: si la solució desapareix, l'opció no. Què es va cotitzar i a qui continua
    #: sent cert.
    technical_solution_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "workspace_technical_solutions.id",
            ondelete="SET NULL",
            name="fk_solution_options_technical_solution",
        ),
    )
    #: El requisit que respon. També `SET NULL`: una extracció nova no pot
    #: esborrar una cotització que algú va demanar de veritat.
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "requirements.id",
            ondelete="SET NULL",
            name="fk_solution_options_requirement",
        ),
    )

    #: Text lliure, sense catàleg i sense FK. Veure el docstring del mòdul.
    supplier_name: Mapped[str | None] = mapped_column(sa.String(300))
    product_name: Mapped[str | None] = mapped_column(sa.String(300))
    description: Mapped[str | None] = mapped_column(sa.Text)

    #: Quantitat d'aquesta configuració. És una propietat de l'OPCIÓ: no ve de
    #: cap dimensió ni actualitza cap línia econòmica (això és 0034 i després).
    quantity: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4))
    unit: Mapped[str | None] = mapped_column(sa.String(50))

    #: **Economia interna.** El que ens costa, sense IVA. Mai el preu de venda.
    #: Més escala que la quantitat: un cost unitari pot tenir cèntims de cèntim
    #: i arrodonir-lo aquí es multiplicaria per la quantitat més avall.
    unit_cost_ex_vat: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))

    #: El mateix enum que l'oferta: la semàntica de com es factura no canvia
    #: perquè la miri una taula diferent.
    pricing_basis: Mapped[PricingBasis | None] = mapped_column(
        enum_column(PricingBasis, "pricing_basis")
    )
    #: Només d'aquesta opció. No recalcula cap oferta.
    duration_months_override: Mapped[int | None] = mapped_column(sa.Integer)

    quote_status: Mapped[QuoteStatus] = mapped_column(
        enum_column(QuoteStatus, "quote_status"), nullable=False
    )
    #: La data que declara la cotització. No se'n dedueix cap: si ningú no l'ha
    #: escrita, no la sabem.
    quote_date: Mapped[date | None] = mapped_column(sa.Date)
    quote_reference: Mapped[str | None] = mapped_column(sa.String(200))
    #: URL o referència lliure. No es descarrega ni es parseja res.
    quote_source: Mapped[str | None] = mapped_column(sa.String(1000))

    selection: Mapped[OptionSelection] = mapped_column(
        enum_column(OptionSelection, "option_selection"), nullable=False
    )
    selection_reason: Mapped[str | None] = mapped_column(sa.Text)
    selected_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_solution_options_selector"),
    )
    selected_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: Notes de dins. Van amb l'economia interna, no amb la fitxa pública.
    internal_note: Mapped[str | None] = mapped_column(sa.Text)

    #: Arxivar, mai esborrar: una alternativa descartada explica una decisió.
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: Testimoni de CAS. Puja a cada escriptura real.
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceSolutionOption {self.supplier_name} {self.selection}>"
