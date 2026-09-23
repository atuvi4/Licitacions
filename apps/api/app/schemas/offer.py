"""Schemas de l'oferta economica.

La decisio que governa tot aquest fitxer: **"ocult" i "desconegut" no es poden
confondre**. Un cost que no s'ha escrit encara es `unit_cost_ex_vat = null`, i un
cost que aquest rol no pot veure NO es un `null`: es l'absencia del bloc
`internal_economics` sencer. Si tots dos casos arribessin com un `null`, un
`technical` no podria distingir "encara no ho sabem" de "no t'ho diem", i qui
mires la pantalla es pensaria que falta feina feta.

Per aixo els derivats van partits a tots els nivells:

* el que es pot ensenyar sempre —preu ofert, ingressos, IVA, limits del plec— viu
  a `totals`;
* el que nomes veuen `owner`, `admin` i `bid_manager` —cost, marge, markup i els
  comptadors que en parlen— viu a `internal_economics`, que es `null` per a la
  resta.

`can_view_internal_economics` a l'arrel ho diu explicitament, perque la interficie
no ho hagi de deduir de la forma de la resposta.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import PricingBasis


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Peticions                                                                     #
# --------------------------------------------------------------------------- #


class RevisionGuarded(BaseModel):
    """Tota mutacio posterior a la creacio diu sobre quina versio s'ha decidit.

    Sense aixo, dos editors simultanis es sobreescriurien l'un a l'altre i el
    segon no sabria mai que ha esborrat feina del primer.
    """

    expected_revision: int = Field(ge=0)


class EconomicOfferUpdateRequest(RevisionGuarded):
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=4000)


class LotFromTenderLotRequest(RevisionGuarded):
    """A quin lot del plec ens presentem (C-1, cas C)."""

    tender_lot_id: uuid.UUID


class LotUpdateRequest(RevisionGuarded):
    """Nomes la durada. El `label` es una fotografia (C-13) i no viatja mai aqui.

    `duration_months` a `null` NO es el mateix que ometre'l: omes vol dir "no el
    toquis" i `null` vol dir "ja no sabem quant dura". Els distingeix el
    sentinella del servei, no aquest schema.
    """

    duration_months: int | None = Field(default=None, ge=1)


class SectionCreateRequest(RevisionGuarded):
    title: str = Field(min_length=1, max_length=300)


class SectionUpdateRequest(RevisionGuarded):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    position: int | None = Field(default=None, ge=0)


class LineCreateRequest(RevisionGuarded):
    concept: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    pricing_basis: PricingBasis
    requirement_id: uuid.UUID | None = None
    technical_solution_id: uuid.UUID | None = None
    supplier_name: str | None = Field(default=None, max_length=300)
    product_name: str | None = Field(default=None, max_length=300)
    #: Quan aquesta linia no dura el mateix que el lot. Mai zero: un servei que
    #: no dura cap mes no es un servei (C-3).
    duration_months_override: int | None = Field(default=None, ge=1)
    unit_cost_ex_vat: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    offered_unit_price_ex_vat: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=4
    )
    #: Transcrit a ma del plec (C-2): no hi ha extraccio automatica.
    tender_max_unit_price_ex_vat: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=4
    )
    #: En tant per cent (21,00), mai un factor.
    vat_rate: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    notes: str | None = Field(default=None, max_length=4000)


class ToOfferLineRequest(RevisionGuarded):
    """Adoptar una opcio com a linia d'oferta.

    `expected_revision` es el de l'**`EconomicOffer`**, no el de l'opcio: qui
    muta aqui es l'oferta, i l'opcio no es toca.

    `concept` l'escriu qui crea la linia. `WorkspaceSolutionOption` no en te cap
    homoleg, i treure'l del producte o del proveidor seria inventar-se el nom
    d'una linia que anira a una oferta de debo. La resta de camps surten de
    l'opcio, que continua sent-ne la font.
    """

    section_id: uuid.UUID
    concept: str = Field(min_length=1, max_length=500)

    @field_validator("concept")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El concepte no pot ser nomes espais.")
        return value.strip()


class LineUpdateRequest(LineCreateRequest):
    """El mateix, tot opcional, mes la posicio.

    `section_id` no hi es: moure una linia de seccio no es editar-la.
    """

    concept: str | None = Field(default=None, min_length=1, max_length=500)
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    pricing_basis: PricingBasis | None = None
    position: int | None = Field(default=None, ge=0)


# --------------------------------------------------------------------------- #
# Derivats: el que es pot ensenyar i el que no                                  #
# --------------------------------------------------------------------------- #


class TotalsRead(BaseModel):
    """Els numeros que pot veure qualsevol membre.

    `None` vol dir indeterminat, mai zero: si una linia no te preu, el total del
    conjunt no se sap, i els comptadors expliquen per que.
    """

    total_revenue: Decimal | None = None
    vat_amount: Decimal | None = None
    total_with_vat: Decimal | None = None
    line_count: int = 0
    incomplete_revenue_lines: int = 0
    incomplete_vat_lines: int = 0


class InternalTotalsRead(BaseModel):
    """Cost i marge. Nomes per a qui pot veure els numeros de dins (C-6)."""

    total_cost: Decimal | None = None
    margin_amount: Decimal | None = None
    margin_on_sales_pct: Decimal | None = None
    markup_on_cost_pct: Decimal | None = None
    incomplete_cost_lines: int = 0


class LineInternalRead(InternalTotalsRead):
    """A nivell de linia, el cost unitari escrit tambe es informacio de dins."""

    unit_cost_ex_vat: Decimal | None = None
    monthly_equivalent_cost: Decimal | None = None


class LotLimitRead(BaseModel):
    """L'oferta del lot contra el pressupost base sense IVA que publica la font.

    Amb `tender_max` desconegut tot queda a `None`: dir que hi cap seria
    afirmar-ho sobre un pressupost que no coneixem.
    """

    tender_max: Decimal | None = None
    difference_to_maximum: Decimal | None = None
    exceeds_lot_max: bool | None = None


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


class LineRead(ORMModel):
    id: uuid.UUID
    section_id: uuid.UUID
    concept: str
    #: D'on ve la linia, si ve d'una opcio de solucio. **Nomes de lectura**:
    #: no es a cap schema d'escriptura generic, perque la provinenca se
    #: guanya adoptant una opcio i no enviant-ne l'identificador.
    solution_option_id: uuid.UUID | None = None
    requirement_id: uuid.UUID | None = None
    technical_solution_id: uuid.UUID | None = None
    supplier_name: str | None = None
    product_name: str | None = None
    quantity: Decimal
    pricing_basis: PricingBasis
    duration_months_override: int | None = None
    offered_unit_price_ex_vat: Decimal | None = None
    tender_max_unit_price_ex_vat: Decimal | None = None
    vat_rate: Decimal | None = None
    notes: str | None = None
    position: int
    archived_at: datetime | None = None
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    #: Resolta a la capa de servei (C-3): la propia, o la del lot.
    effective_duration_months: int | None = None
    #: Quants periodes multipliquen el preu. `None` quan la durada no se sap.
    periods: Decimal | None = None
    monthly_equivalent_revenue: Decimal | None = None
    #: `None` quan el plec no dona maxim: desconegut no es "no el supera".
    exceeds_unit_max: bool | None = None

    totals: TotalsRead
    internal_economics: LineInternalRead | None = None


class SectionRead(ORMModel):
    id: uuid.UUID
    offer_lot_id: uuid.UUID
    title: str
    position: int
    archived_at: datetime | None = None
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    totals: TotalsRead
    internal_economics: InternalTotalsRead | None = None
    lines: list[LineRead] = Field(default_factory=list)


class LotRead(ORMModel):
    id: uuid.UUID
    tender_lot_id: uuid.UUID | None = None
    label: str
    duration_months: int | None = None
    position: int
    archived_at: datetime | None = None
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    totals: TotalsRead
    internal_economics: InternalTotalsRead | None = None
    limit: LotLimitRead
    sections: list[SectionRead] = Field(default_factory=list)


class EconomicOfferRead(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    currency: str
    notes: str | None = None
    #: El numero que el client ha de tornar a enviar com a `expected_revision`.
    revision: int
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    #: Explicit, per no haver de deduir-ho de si `internal_economics` es `null`.
    can_view_internal_economics: bool = False
    #: Cert nomes al cas C de C-1: plec amb lots i candidatura global.
    can_select_lots: bool = False

    totals: TotalsRead
    internal_economics: InternalTotalsRead | None = None
    lots: list[LotRead] = Field(default_factory=list)


class EconomicOfferCreateResult(BaseModel):
    """`created=False` vol dir que ja hi havia oferta i s'ha obert la que hi era."""

    created: bool
    offer: EconomicOfferRead
