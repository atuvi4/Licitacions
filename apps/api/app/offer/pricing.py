"""Calcul de l'oferta economica: preus, costos, marges i limits.

Modul **pur**. No importa SQLAlchemy, ni FastAPI, ni cap sessio, ni cap
proveidor: nomes `Decimal` i les dades que li passen. Aixo es el que permet
provar les matematiques senceres sense base de dades, i es la mateixa separacio
que ja fan `app.workspace.dossier` i `app.workspace.manifest`.

Tres regles governen tot el que hi ha aqui, i totes tres corregeixen un defecte
concret del full de calcul que aquest producte substitueix.

**1. `None` no es `0`.** Una linia sense cost unitari no te un cost de zero: no
en te cap. El total del que la conte queda indeterminat i els comptadors diuen
quantes linies hi falten. Sumar-hi zero faria quadrar un marge que ningu no ha
calculat, que es exactament com se signen ofertes a perdua.

**2. Marge i markup son dos numeros diferents.** Amb cost 100 i preu 120, el
markup sobre cost es el 20 % i el marge sobre vendes es el 16,67 %. Es calculen
tots dos, sempre, i cap dels dos es diu nomes "marge".

**3. Una inversio no es un cost mensual.** Una linia `ONE_OFF` de 24.000 EUR
amb una durada de 24 mesos costa 24.000 EUR. `monthly_equivalent_cost` en diu
1.000 EUR/mes, pero es una LECTURA: no entra en cap suma, a cap nivell.

## On es quantitza

La precisio interna de `Decimal` es manté durant tota la multiplicacio: no
s'arrodoneix el producte intermedi de preu x quantitat x periodes, que es on
s'acumularia l'error.

S'arrodoneix, amb `ROUND_HALF_UP`, en dos llocs i prou:

* **els imports, a 2 decimals**, quan es tanca el total d'una linia (preus i
  costos unitaris en poden portar 4, els imports no);
* **els percentatges, a 4 decimals**, quan es tanca el ratio.

L'agregacio suma **els imports ja quantitzats de cada linia**, no els valors en
brut. Es deliberat: un document d'oferta ha de quadrar, i el total d'un lot ha
de ser exactament la suma del que es llegeix a cada fila. Sumar en brut i
quantitzar al final donaria un total que difereix en cèntims del que es veu.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.models.enums import PricingBasis
from app.tenders.amounts import useful_amount

#: Els imports s'exposen sempre a 2 decimals.
MONEY_PLACES = Decimal("0.01")
#: Els percentatges, a 4: amb 2 no es distingiria un 16,66 % d'un 16,67 %.
PERCENT_PLACES = Decimal("0.0001")
#: Els preus i costos UNITARIS, a 4. Es l'escala de `Numeric(18,4)` a la
#: base, i per tant l'unica en que un valor es pot comparar amb el que hi ha
#: desat: qualsevol cosa mes fina es perd en escriure-la.
UNIT_PLACES = Decimal("0.0001")

_MONTHS_PER_YEAR = Decimal(12)
_HUNDRED = Decimal(100)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def _percent(value: Decimal) -> Decimal:
    return value.quantize(PERCENT_PLACES, rounding=ROUND_HALF_UP)


def unit_amount(value: Decimal) -> Decimal:
    """Un preu o un cost unitari, a l'escala amb que es desa.

    Publica perque es la MATEIXA transformacio que aplica la base en desar
    un `Numeric(18,4)`. Comparar un valor de mes precisio amb el que hi ha
    guardat sense passar per aqui donaria diferencies que no existeixen: un
    cost d'opcio de 12,345678 i el seu snapshot de 12,3457 son el mateix
    numero vist a dues escales, no dos numeros.

    `ROUND_HALF_UP` com la resta del modul, que es tambe com arrodoneix
    PostgreSQL en escriure un `numeric`.
    """
    return value.quantize(UNIT_PLACES, rounding=ROUND_HALF_UP)


def proposed_unit_price(
    unit_cost_ex_vat: Decimal | None, target_markup_pct: Decimal | None
) -> Decimal | None:
    """El preu que sortiria d'aplicar l'objectiu al cost.

        preu proposat = cost x (1 + markup / 100)

    **Markup sobre cost**, no marge sobre venda: amb un cost de 100 i un
    markup del 20 %%, el preu proposat es 120, i el marge sobre venda
    d'aquell preu seria el 16,67 %%. Son tres numeros diferents i el modul
    els mante separats.

    `None` si falta qualsevol dels dos: no se sap, i no es zero.
    """
    if unit_cost_ex_vat is None or target_markup_pct is None:
        return None
    return unit_amount(
        unit_cost_ex_vat * (Decimal(1) + target_markup_pct / _HUNDRED)
    )


def price_matches_target(
    offered_unit_price_ex_vat: Decimal | None,
    unit_cost_ex_vat: Decimal | None,
    target_markup_pct: Decimal | None,
) -> bool | None:
    """Si el preu ofert ja es el que proposaria l'objectiu.

    `None` quan falta qualsevol de les tres dades: "no ho sabem" no es "no
    coincideix", i pintar-ho com un desajust faria semblar que hi ha una
    feina pendent que ningu no ha deixat a mitges.

    Es compara a l'escala del preu ofert, no en brut: si no, un preu desat
    com a 120,0000 no coincidiria mai amb un proposat de 120.
    """
    proposed = proposed_unit_price(unit_cost_ex_vat, target_markup_pct)
    if proposed is None or offered_unit_price_ex_vat is None:
        return None
    return unit_amount(offered_unit_price_ex_vat) == proposed


# --------------------------------------------------------------------------- #
# Entrada                                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LineInput:
    """Una linia d'oferta, ja resolta. Cap identificador i cap fila de cap taula.

    `effective_duration_months` arriba **ja calculada**: la herencia lot -> linia
    (C-3) es una decisio de la capa de servei, no d'aqui. `None` vol dir que no
    se sap, i llavors les bases temporals no produeixen cap total.
    """

    pricing_basis: PricingBasis
    quantity: Decimal
    unit_cost_ex_vat: Decimal | None = None
    offered_unit_price_ex_vat: Decimal | None = None
    effective_duration_months: int | None = None
    vat_rate: Decimal | None = None
    #: Maxim del plec per unitat, quan algu l'ha transcrit a ma (C-2).
    tender_max_unit_price_ex_vat: Decimal | None = None


# --------------------------------------------------------------------------- #
# Sortida                                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Totals:
    """Els mateixos numeros a qualsevol nivell: linia, seccio, lot o oferta.

    Els tres comptadors no son estadistica: son el que permet a la interficie
    explicar **per que** un total es `None` en lloc de deixar un buit sense
    motiu.
    """

    total_cost: Decimal | None
    total_revenue: Decimal | None
    margin_amount: Decimal | None
    margin_on_sales_pct: Decimal | None
    markup_on_cost_pct: Decimal | None
    vat_amount: Decimal | None
    total_with_vat: Decimal | None
    line_count: int
    incomplete_cost_lines: int
    incomplete_revenue_lines: int
    incomplete_vat_lines: int


@dataclass(frozen=True, slots=True)
class LineTotals(Totals):
    """El que nomes te sentit a nivell de linia."""

    periods: Decimal | None
    monthly_equivalent_cost: Decimal | None
    monthly_equivalent_revenue: Decimal | None
    #: `None` quan el plec no dona maxim: "no se sap", mai "no el supera".
    exceeds_unit_max: bool | None


@dataclass(frozen=True, slots=True)
class LotLimit:
    """L'oferta d'un lot contra el maxim del plec.

    Amb `tender_max` a `None` —que es el cas real quan la font no publica
    `base_budget_without_tax`, o el publica a `0.00`— tot queda indeterminat. Dir-ne `False` seria
    afirmar que l'oferta cap dins d'un pressupost que no coneixem (C-1).
    """

    tender_max: Decimal | None
    difference_to_maximum: Decimal | None
    exceeds_lot_max: bool | None


# --------------------------------------------------------------------------- #
# Periodes                                                                      #
# --------------------------------------------------------------------------- #


def periods_for(basis: PricingBasis, duration_months: int | None) -> Decimal | None:
    """Quants periodes multiplica el preu unitari.

    `ONE_OFF` i `PER_UNIT` no depenen mai de la durada: al primer la quantitat
    son les unitats lliurades un sol cop, i al segon son ja les unitats de tot
    el contracte. `ANNUAL` es prorrateja (30 mesos = 2,5 anualitats) i no genera
    cap avis per no ser multiple de 12: facturar periodes sencers es una altra
    estructura, no una variant d'aquesta (C-8).
    """
    if basis in (PricingBasis.ONE_OFF, PricingBasis.PER_UNIT):
        return Decimal(1)
    if duration_months is None:
        return None
    if basis is PricingBasis.MONTHLY:
        return Decimal(duration_months)
    return Decimal(duration_months) / _MONTHS_PER_YEAR


# --------------------------------------------------------------------------- #
# Limits                                                                        #
# --------------------------------------------------------------------------- #


def exceeds_unit_max(
    offered_unit_price: Decimal | None, tender_max_unit_price: Decimal | None
) -> bool | None:
    """`None` si falta qualsevol dels dos: desconegut no es "no el supera"."""
    if offered_unit_price is None or tender_max_unit_price is None:
        return None
    return offered_unit_price > tender_max_unit_price


def exceeds_lot_max(total_revenue: Decimal | None, tender_max: Decimal | None) -> bool | None:
    """`None` sense maxim util: un maxim de 0 (o negatiu) no es un maxim, es com NULL."""
    tender_max = useful_amount(tender_max)
    if total_revenue is None or tender_max is None:
        return None
    return total_revenue > tender_max


def lot_limit(total_revenue: Decimal | None, tender_max: Decimal | None) -> LotLimit:
    """Quant queda fins al maxim del lot, i si ja s'ha passat.

    Un maxim publicat a `0.00` no es un maxim valid (`app.tenders.amounts`): queda
    indeterminat com si no hi fos, mai «supera el pressupost» amb una diferencia negativa.
    """
    tender_max = useful_amount(tender_max)
    if total_revenue is None or tender_max is None:
        return LotLimit(tender_max=tender_max, difference_to_maximum=None, exceeds_lot_max=None)
    return LotLimit(
        tender_max=tender_max,
        difference_to_maximum=_money(tender_max - total_revenue),
        exceeds_lot_max=total_revenue > tender_max,
    )


# --------------------------------------------------------------------------- #
# Calcul                                                                        #
# --------------------------------------------------------------------------- #


def _extended(
    unit_amount: Decimal | None, quantity: Decimal, periods: Decimal | None
) -> Decimal | None:
    """Preu o cost unitari x quantitat x periodes, quantitzat un sol cop al final."""
    if unit_amount is None or periods is None:
        return None
    return _money(unit_amount * quantity * periods)


def _margins(
    total_cost: Decimal | None, total_revenue: Decimal | None
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Marge, marge sobre vendes i markup sobre cost.

    Amb cost 100 i preu 120: marge 20, markup 20 %, marge sobre vendes 16,67 %.
    Un denominador zero dona `None`, mai `0` ni `100`: un cost de zero no fa que
    el markup sigui infinit, fa que no en tingui.
    """
    if total_cost is None or total_revenue is None:
        return None, None, None
    margin = total_revenue - total_cost
    on_sales = _percent(margin / total_revenue * _HUNDRED) if total_revenue != 0 else None
    on_cost = _percent(margin / total_cost * _HUNDRED) if total_cost != 0 else None
    return margin, on_sales, on_cost


def line_totals(line: LineInput) -> LineTotals:
    """Tots els numeros d'una linia. Cap efecte, cap consulta."""
    periods = periods_for(line.pricing_basis, line.effective_duration_months)
    total_cost = _extended(line.unit_cost_ex_vat, line.quantity, periods)
    total_revenue = _extended(line.offered_unit_price_ex_vat, line.quantity, periods)
    margin, on_sales, on_cost = _margins(total_cost, total_revenue)

    vat_amount: Decimal | None = None
    total_with_vat: Decimal | None = None
    if total_revenue is not None and line.vat_rate is not None:
        vat_amount = _money(total_revenue * line.vat_rate / _HUNDRED)
        total_with_vat = _money(total_revenue + vat_amount)

    # Lectura, no import: es deriva del total i no hi torna mai (invariant 3).
    months = line.effective_duration_months
    monthly_cost = _money(total_cost / months) if total_cost is not None and months else None
    monthly_revenue = (
        _money(total_revenue / months) if total_revenue is not None and months else None
    )

    return LineTotals(
        total_cost=total_cost,
        total_revenue=total_revenue,
        margin_amount=margin,
        margin_on_sales_pct=on_sales,
        markup_on_cost_pct=on_cost,
        vat_amount=vat_amount,
        total_with_vat=total_with_vat,
        line_count=1,
        incomplete_cost_lines=1 if total_cost is None else 0,
        incomplete_revenue_lines=1 if total_revenue is None else 0,
        incomplete_vat_lines=1 if vat_amount is None else 0,
        periods=periods,
        monthly_equivalent_cost=monthly_cost,
        monthly_equivalent_revenue=monthly_revenue,
        exceeds_unit_max=exceeds_unit_max(
            line.offered_unit_price_ex_vat, line.tender_max_unit_price_ex_vat
        ),
    )


def _sum_or_none(values: list[Decimal | None]) -> Decimal | None:
    """Suma nomes si hi son tots. Un sol `None` fa indeterminat el conjunt."""
    if not values or any(value is None for value in values):
        return None
    total = Decimal("0.00")
    for value in values:
        assert value is not None  # noqa: S101 - garantit pel guard anterior
        total += value
    return total


def aggregate(parts: Sequence[Totals]) -> Totals:
    """Puja un nivell: linia -> seccio -> lot -> oferta.

    La mateixa funcio a cada salt, perque `LineTotals` es un `Totals`. Les parts
    buides —una seccio sense linies— no hi contribueixen gens: ni sumen ni fan
    indeterminat el que les conte.

    L'IVA s'agrega en **imports**, mai en percentatges: la mitjana d'un 21 % i
    un 10 % no es l'IVA de res.
    """
    contributing = [part for part in parts if part.line_count > 0]
    if not contributing:
        return Totals(
            total_cost=None,
            total_revenue=None,
            margin_amount=None,
            margin_on_sales_pct=None,
            markup_on_cost_pct=None,
            vat_amount=None,
            total_with_vat=None,
            line_count=0,
            incomplete_cost_lines=0,
            incomplete_revenue_lines=0,
            incomplete_vat_lines=0,
        )

    total_cost = _sum_or_none([part.total_cost for part in contributing])
    total_revenue = _sum_or_none([part.total_revenue for part in contributing])
    margin, on_sales, on_cost = _margins(total_cost, total_revenue)
    vat_amount = _sum_or_none([part.vat_amount for part in contributing])
    total_with_vat = (
        None if total_revenue is None or vat_amount is None else total_revenue + vat_amount
    )

    return Totals(
        total_cost=total_cost,
        total_revenue=total_revenue,
        margin_amount=margin,
        margin_on_sales_pct=on_sales,
        markup_on_cost_pct=on_cost,
        vat_amount=vat_amount,
        total_with_vat=total_with_vat,
        line_count=sum(part.line_count for part in contributing),
        incomplete_cost_lines=sum(part.incomplete_cost_lines for part in contributing),
        incomplete_revenue_lines=sum(part.incomplete_revenue_lines for part in contributing),
        incomplete_vat_lines=sum(part.incomplete_vat_lines for part in contributing),
    )
