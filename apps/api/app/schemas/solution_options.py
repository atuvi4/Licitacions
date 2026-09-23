"""Schemas de les opcions de solucio (S3-S7).

La decisio que governa el fitxer es la mateixa que la de `schemas/offer.py`:
**"ocult" i "desconegut" no es poden confondre.** Un cost que ningu no ha escrit
encara es `unit_cost_ex_vat = null` DINS d'`internal_economics`; un cost que
aquest rol no pot veure es l'absencia del bloc `internal_economics` sencer.

Si tots dos casos arribessin com un `null` al primer nivell, un `technical` no
podria distingir "encara no ho sabem" de "no t'ho diem", i qui mires la pantalla
es pensaria que falta feina que ja esta feta. `can_view_internal_economics` ho
diu explicitament perque la interficie no ho hagi de deduir de la forma de la
resposta.

I per aixo hi ha **dos** schemas d'escriptura i no un de sol amb camps opcionals:
un PATCH public que un `technical` pot enviar no ha de tenir ni tan sols un lloc
on posar el cost. Amb un sol schema, un client que envies el formulari sencer
amb el camp buit el netejaria sense voler-ho i sense veure-ho.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    DimensionStatus,
    OptionSelection,
    PricingBasis,
    QuoteStatus,
)
from app.schemas.common import ORMModel


class OptionDimensionRead(ORMModel):
    """La magnitud de què depèn una opció, resumida.

    Prou per llegir-la sense obrir res: què és, quant val ara i si algú ho ha
    decidit. **Sense les observacions**: cada opció n'arrossegaria desenes i la
    llista es faria impracticable, i per a mirar-les ja hi ha Dimensionament.

    `working_value_num` és el valor d'ARA, llegit de la dimensió. No és cap
    còpia desada a la relació: si ho fos, envelliria.
    """

    id: uuid.UUID
    key: str
    label: str
    unit: str | None = None
    scope_label: str | None = None
    status: DimensionStatus
    working_value_text: str | None = None
    working_value_num: Decimal | None = None
    #: Una magnitud arxivada pot continuar sent una dependència declarada: el
    #: que no es pot és començar-ne una de nova.
    archived_at: datetime | None = None
    revision: int


class OptionInternalRead(BaseModel):
    """El que nomes veuen els rols economics.

    Va en un bloc propi i no escampat per l'arrel: aixi "no t'ho diem" es
    l'absencia d'aquest objecte, i no un `null` indistingible d'un buit.
    """

    #: El que ens costa a nosaltres, sense IVA. Mai el preu de venda.
    unit_cost_ex_vat: Decimal | None = None
    internal_note: str | None = None


class SolutionOptionRead(ORMModel):
    """Una opcio tal com la veu qui prepara la candidatura."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    technical_solution_id: uuid.UUID | None = None
    requirement_id: uuid.UUID | None = None
    #: Si el requisit enllacat continua sent del conjunt vigent. Una opcio no
    #: s'esborra mai perque una extraccio nova se'n emporti el requisit: es diu.
    requirement_is_effective: bool | None = None

    supplier_name: str | None = None
    product_name: str | None = None
    description: str | None = None

    quantity: Decimal | None = None
    unit: str | None = None
    pricing_basis: PricingBasis | None = None
    duration_months_override: int | None = None

    quote_status: QuoteStatus
    quote_date: date | None = None
    quote_reference: str | None = None
    quote_source: str | None = None

    selection: OptionSelection
    selection_reason: str | None = None
    selected_by: uuid.UUID | None = None
    selected_at: datetime | None = None

    archived_at: datetime | None = None
    #: El testimoni de CAS. El seguent escrit ha de portar aquest numero.
    #: Enllacar o desenllacar una magnitud tambe el mou: son configuracio
    #: d'aquesta opcio, no una entitat a part.
    revision: int
    #: De quines magnituds depen. Declarat per una persona; res no ho dedueix.
    dimensions: list[OptionDimensionRead] = Field(default_factory=list)
    #: La linia d'oferta VIVA que va sortir d'aquesta opcio, si n'hi ha.
    #: Serveix perque la interficie no ofereixi crear-ne una segona.
    offer_line_id: uuid.UUID | None = None

    #: `None` per a qui no pot veure cost. Veure el docstring del modul.
    internal_economics: OptionInternalRead | None = None


class SolutionOptionListRead(BaseModel):
    """La llista, amb la resposta a «i jo, puc veure el cost?» ben explicita."""

    can_view_internal_economics: bool = False
    items: list[SolutionOptionRead] = Field(default_factory=list)


class OptionInternalWrite(BaseModel):
    """Economia interna. Nomes `ECONOMIC_INTERNAL_ROLES`."""

    model_config = ConfigDict(extra="forbid")

    unit_cost_ex_vat: Decimal | None = Field(default=None, max_digits=18, decimal_places=6)
    internal_note: str | None = None


class SolutionOptionCreate(BaseModel):
    """Crear una opcio.

    No hi ha `selection`: cap opcio neix triada. `internal_economics` es
    opcional i, si l'envia un rol que no hi te potestat, es un error i no un
    camp que s'ignori en silenci.
    """

    model_config = ConfigDict(extra="forbid")

    technical_solution_id: uuid.UUID | None = None
    requirement_id: uuid.UUID | None = None

    supplier_name: str | None = Field(default=None, max_length=300)
    product_name: str | None = Field(default=None, max_length=300)
    description: str | None = None

    quantity: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    unit: str | None = Field(default=None, max_length=50)
    pricing_basis: PricingBasis | None = None
    duration_months_override: int | None = Field(default=None, ge=0)

    quote_status: QuoteStatus = QuoteStatus.DRAFT
    quote_date: date | None = None
    quote_reference: str | None = Field(default=None, max_length=200)
    quote_source: str | None = Field(default=None, max_length=1000)

    internal_economics: OptionInternalWrite | None = None


class SolutionOptionPublicUpdate(BaseModel):
    """La fitxa publica. **El cost no hi cap**, i es deliberat.

    `extra="forbid"` fa que enviar-hi `unit_cost_ex_vat` sigui un 422 i no un
    camp descartat sense dir res.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)

    technical_solution_id: uuid.UUID | None = None
    requirement_id: uuid.UUID | None = None
    supplier_name: str | None = Field(default=None, max_length=300)
    product_name: str | None = Field(default=None, max_length=300)
    description: str | None = None
    quantity: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    unit: str | None = Field(default=None, max_length=50)
    pricing_basis: PricingBasis | None = None
    duration_months_override: int | None = Field(default=None, ge=0)
    quote_status: QuoteStatus | None = None
    quote_date: date | None = None
    quote_reference: str | None = Field(default=None, max_length=200)
    quote_source: str | None = Field(default=None, max_length=1000)


class SolutionOptionInternalUpdate(OptionInternalWrite):
    """Nomes cost i notes de dins, amb el seu propi CAS."""

    expected_revision: int = Field(ge=0)


class SolutionOptionSelectionWrite(BaseModel):
    """Triar o descartar. Sempre una accio explicita d'una persona."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    selection: OptionSelection
    #: Obligatori per a `rejected`. Ho imposa el servei, no nomes el schema:
    #: una cadena d'espais tampoc no explica res.
    reason: str | None = None


class SolutionOptionArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


class OptionDimensionWrite(BaseModel):
    """Enllacar o desenllacar. El CAS es el de l'OPCIO, no un de nou."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
