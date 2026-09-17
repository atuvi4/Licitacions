"""Matematiques de l'oferta economica. Modul pur: cap sessio, cap ORM, cap HTTP.

L'Excel que aquest producte substitueix barrejava marge i markup, convertia una
inversio inicial en un cost mensual i tractava una casella buida com un zero.
Les tres coses donaven ofertes que semblaven bones i no ho eren. Aquests tests
fixen que aqui no passi:

* **`None` no es `0`.** Una linia sense cost no aporta zero a cap total: fa que
  el total sigui indeterminat i el sistema ha de poder dir quantes linies li
  falten. Sumar-hi zero es exactament la mentida que fa quadrar un marge fals.
* **Marge i markup son dos numeros diferents.** Cost 100 i preu 120 son un
  markup del 20 % i un marge sobre vendes del 16,67 %. Un sol percentatge
  anomenat "marge" es el defecte que venim a corregir.
* **Una inversio no es un cost mensual.** 24.000 EUR a 24 mesos continuen sent
  24.000 EUR de cost; els 1.000 EUR/mes son una lectura, i no entren en cap suma.

Cap d'aquestes funcions sap que existeix una base de dades.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.enums import PricingBasis
from app.offer.pricing import (
    LineInput,
    aggregate,
    exceeds_lot_max,
    exceeds_unit_max,
    line_totals,
    lot_limit,
    periods_for,
)


def line(
    *,
    basis: PricingBasis = PricingBasis.ONE_OFF,
    quantity: str = "1",
    unit_cost: str | None = None,
    offered_price: str | None = None,
    duration: int | None = None,
    vat_rate: str | None = None,
    max_unit_price: str | None = None,
) -> LineInput:
    """Constructor curt per als tests. La durada arriba **ja resolta** (C-3)."""
    return LineInput(
        pricing_basis=basis,
        quantity=Decimal(quantity),
        unit_cost_ex_vat=None if unit_cost is None else Decimal(unit_cost),
        offered_unit_price_ex_vat=None if offered_price is None else Decimal(offered_price),
        effective_duration_months=duration,
        vat_rate=None if vat_rate is None else Decimal(vat_rate),
        tender_max_unit_price_ex_vat=None if max_unit_price is None else Decimal(max_unit_price),
    )


# --------------------------------------------------------------------------- #
# A i B. Marge i markup son coses diferents                                     #
# --------------------------------------------------------------------------- #


def test_cost_100_price_120_dona_markup_20_i_marge_sobre_vendes_16_67():
    totals = line_totals(line(unit_cost="100", offered_price="120"))

    assert totals.margin_amount == Decimal("20.00")
    assert totals.markup_on_cost_pct == Decimal("20.0000")
    assert totals.margin_on_sales_pct == Decimal("16.6667")


def test_vendre_a_perdua_inverteix_els_dos_percentatges_i_no_els_confon():
    """Cost 120 i preu 100: markup -16,67 % i marge sobre vendes -20 %.

    Es el mateix parell de numeros que el cas A, intercanviats. Si algu els
    confongues, aquest test i l'anterior no podrien passar tots dos.
    """
    totals = line_totals(line(unit_cost="120", offered_price="100"))

    assert totals.margin_amount == Decimal("-20.00")
    assert totals.markup_on_cost_pct == Decimal("-16.6667")
    assert totals.margin_on_sales_pct == Decimal("-20.0000")


# --------------------------------------------------------------------------- #
# C i D. Denominador zero: indefinit, no zero                                   #
# --------------------------------------------------------------------------- #


def test_sense_ingres_el_marge_sobre_vendes_es_indefinit():
    totals = line_totals(line(unit_cost="100", offered_price="0"))

    assert totals.total_revenue == Decimal("0.00")
    assert totals.margin_on_sales_pct is None
    assert totals.markup_on_cost_pct == Decimal("-100.0000")


def test_sense_cost_el_markup_es_indefinit_i_no_infinit_ni_cent_per_cent():
    totals = line_totals(line(unit_cost="0", offered_price="120"))

    assert totals.total_cost == Decimal("0.00")
    assert totals.markup_on_cost_pct is None
    assert totals.margin_on_sales_pct == Decimal("100.0000")


# --------------------------------------------------------------------------- #
# E i F. None no es 0                                                           #
# --------------------------------------------------------------------------- #


def test_sense_cost_unitari_el_cost_total_es_indeterminat_i_no_zero():
    totals = line_totals(line(offered_price="120"))

    assert totals.total_cost is None
    assert totals.incomplete_cost_lines == 1
    assert totals.total_revenue == Decimal("120.00")
    assert totals.margin_amount is None


def test_sense_preu_ofertat_l_ingres_es_indeterminat_i_no_zero():
    totals = line_totals(line(unit_cost="100"))

    assert totals.total_revenue is None
    assert totals.incomplete_revenue_lines == 1
    assert totals.total_cost == Decimal("100.00")
    assert totals.margin_amount is None


# --------------------------------------------------------------------------- #
# G. Inversio: el cost real no depen de la durada                               #
# --------------------------------------------------------------------------- #


def test_una_inversio_de_24000_a_24_mesos_segueix_costant_24000():
    totals = line_totals(
        line(basis=PricingBasis.ONE_OFF, unit_cost="24000", offered_price="30000", duration=24)
    )

    assert totals.total_cost == Decimal("24000.00")
    assert totals.monthly_equivalent_cost == Decimal("1000.00")


def test_l_equivalent_mensual_no_entra_en_cap_suma():
    """Dues inversions identiques sumen 48.000, no 2.000."""
    one = line_totals(line(unit_cost="24000", offered_price="30000", duration=24))
    total = aggregate([one, one])

    assert total.total_cost == Decimal("48000.00")


# --------------------------------------------------------------------------- #
# H, I, J, K. Periodes segons la base de facturacio                             #
# --------------------------------------------------------------------------- #


def test_mensual_multiplica_quantitat_preu_i_mesos_amb_decimal_exacte():
    totals = line_totals(
        line(
            basis=PricingBasis.MONTHLY,
            quantity="80",
            unit_cost="4.7125",
            offered_price="6.50",
            duration=24,
        )
    )

    assert totals.periods == Decimal("24")
    assert totals.total_cost == Decimal("9048.00")
    assert totals.total_revenue == Decimal("12480.00")
    assert totals.margin_amount == Decimal("3432.00")
    assert totals.margin_on_sales_pct == Decimal("27.5000")
    assert totals.markup_on_cost_pct == Decimal("37.9310")


def test_anual_a_30_mesos_son_dues_anualitats_i_mitja():
    assert periods_for(PricingBasis.ANNUAL, 30) == Decimal("2.5")


def test_anual_sense_durada_deixa_els_totals_indeterminats_i_no_assumeix_dotze():
    totals = line_totals(
        line(basis=PricingBasis.ANNUAL, unit_cost="1200", offered_price="1500", duration=None)
    )

    assert totals.periods is None
    assert totals.total_cost is None
    assert totals.total_revenue is None


def test_per_unitat_no_multiplica_mai_per_la_durada():
    totals = line_totals(
        line(
            basis=PricingBasis.PER_UNIT,
            quantity="1000",
            unit_cost="2",
            offered_price="3",
            duration=24,
        )
    )

    assert totals.periods == Decimal("1")
    assert totals.total_cost == Decimal("2000.00")


# --------------------------------------------------------------------------- #
# L, M, N, O. Limits: sense maxim, "no se sap", mai "no el passa"               #
# --------------------------------------------------------------------------- #


def test_un_centim_per_sobre_del_preu_unitari_maxim_ja_el_supera():
    assert exceeds_unit_max(Decimal("10.0001"), Decimal("10")) is True


def test_sense_preu_unitari_maxim_el_resultat_es_desconegut_i_no_fals():
    assert exceeds_unit_max(Decimal("10.0001"), None) is None


def test_un_centim_per_sobre_del_maxim_del_lot_ja_el_supera():
    assert exceeds_lot_max(Decimal("240000.01"), Decimal("240000")) is True


def test_sense_maxim_de_lot_el_resultat_es_desconegut_i_no_fals():
    """C-1: `base_budget_without_tax` nul es "maxim no disponible".

    Mai "dins del pressupost". Un `False` aqui seria dir que l'oferta cap en un
    pressupost que no coneixem.
    """
    assert exceeds_lot_max(Decimal("240000.01"), None) is None


def test_la_diferencia_al_maxim_del_lot_tambe_es_desconeguda_sense_maxim():
    known = lot_limit(Decimal("200000.00"), Decimal("240000"))
    unknown = lot_limit(Decimal("200000.00"), None)

    assert known.difference_to_maximum == Decimal("40000.00")
    assert known.exceeds_lot_max is False
    assert unknown.difference_to_maximum is None
    assert unknown.exceeds_lot_max is None


# --------------------------------------------------------------------------- #
# P i Q. Agregacio                                                              #
# --------------------------------------------------------------------------- #


def test_una_linia_sense_cost_deixa_el_cost_agregat_indeterminat_pero_no_l_ingres():
    parts = [
        line_totals(line(unit_cost="100", offered_price="150")),
        line_totals(line(unit_cost="200", offered_price="250")),
        line_totals(line(offered_price="300")),
    ]

    total = aggregate(parts)

    assert total.total_cost is None
    assert total.incomplete_cost_lines == 1
    assert total.line_count == 3
    assert total.total_revenue == Decimal("700.00")
    assert total.margin_amount is None


def test_l_iva_s_agrega_en_imports_i_no_en_percentatges():
    """Dues linies al 21 % i al 10 %: 31,00 EUR d'IVA, no cap 15,5 % mitja."""
    parts = [
        line_totals(line(unit_cost="50", offered_price="100", vat_rate="21")),
        line_totals(line(unit_cost="50", offered_price="100", vat_rate="10")),
    ]

    total = aggregate(parts)

    assert total.vat_amount == Decimal("31.00")
    assert total.total_with_vat == Decimal("231.00")


def test_l_agregacio_de_seccions_a_lot_dona_el_mateix_que_la_de_linies():
    """line -> section -> lot -> offer: la mateixa funcio a cada nivell."""
    first = [line_totals(line(unit_cost="100", offered_price="150"))]
    second = [line_totals(line(unit_cost="200", offered_price="250"))]

    by_section = aggregate([aggregate(first), aggregate(second)])
    flat = aggregate(first + second)

    assert by_section.total_cost == flat.total_cost == Decimal("300.00")
    assert by_section.total_revenue == flat.total_revenue == Decimal("400.00")
    assert by_section.margin_amount == flat.margin_amount == Decimal("100.00")


def test_un_agregat_buit_no_es_zero():
    """Una seccio sense linies no te un cost de zero: no en te cap."""
    total = aggregate([])

    assert total.total_cost is None
    assert total.total_revenue is None
    assert total.line_count == 0


# --------------------------------------------------------------------------- #
# Precisio                                                                      #
# --------------------------------------------------------------------------- #


def test_els_totals_exposats_es_quantitzen_a_dos_decimals():
    totals = line_totals(line(quantity="3", unit_cost="0.3333", offered_price="1.1117"))

    assert totals.total_cost == Decimal("1.00")
    assert totals.total_revenue == Decimal("3.34")
    assert totals.total_cost.as_tuple().exponent == -2


def test_el_total_del_lot_es_la_suma_del_que_es_veu_a_cada_linia():
    """Quantitzar linia a linia i despres sumar, no al reves.

    Un document d'oferta ha de quadrar: si cada linia diu 0,01 i n'hi ha tres,
    el total ha de dir 0,03 encara que la precisio interna digui 0,0294.
    """
    parts = [line_totals(line(unit_cost="0.0098", offered_price="0.0098")) for _ in range(3)]

    total = aggregate(parts)

    assert all(part.total_cost == Decimal("0.01") for part in parts)
    assert total.total_cost == Decimal("0.03")


def test_cap_valor_retornat_es_un_float():
    totals = line_totals(
        line(
            basis=PricingBasis.MONTHLY,
            unit_cost="1",
            offered_price="2",
            duration=12,
            vat_rate="21",
        )
    )

    numbers = [
        totals.total_cost,
        totals.total_revenue,
        totals.margin_amount,
        totals.margin_on_sales_pct,
        totals.markup_on_cost_pct,
        totals.vat_amount,
        totals.total_with_vat,
        totals.monthly_equivalent_cost,
        totals.periods,
    ]
    assert all(isinstance(value, Decimal) for value in numbers)


@pytest.mark.parametrize(
    ("basis", "duration", "expected"),
    [
        (PricingBasis.ONE_OFF, None, Decimal("1")),
        (PricingBasis.ONE_OFF, 24, Decimal("1")),
        (PricingBasis.PER_UNIT, 24, Decimal("1")),
        (PricingBasis.MONTHLY, 24, Decimal("24")),
        (PricingBasis.MONTHLY, None, None),
        (PricingBasis.ANNUAL, 12, Decimal("1")),
        (PricingBasis.ANNUAL, 30, Decimal("2.5")),
        (PricingBasis.ANNUAL, None, None),
    ],
)
def test_taula_de_periodes(basis: PricingBasis, duration: int | None, expected: Decimal | None):
    assert periods_for(basis, duration) == expected
