"""El cost d'una execucio: preu configurable, i silenci si no n'hi ha.

El que es protegeix aqui no es l'aritmetica, que es trivial: es que el benchmark
**no publiqui mai un cost inventat**. Sense preu configurat, `estimated_cost` ha
de quedar buit i l'informe ha de dir per que; amb preu, el cost ha de portar al
costat el preu amb que es va calcular, o no es podra auditar despres.
"""

from __future__ import annotations

import pytest

from app.evaluation.metrics import CostMetrics
from app.evaluation.pricing import (
    TOKENS_PER_UNIT,
    ModelPrice,
    PriceConfigError,
    parse_prices,
    price_for,
)


def test_parse_prices_llegeix_la_configuracio() -> None:
    prices = parse_prices({"claude-sonnet-5": {"input": 3.0, "output": 15.0}})
    assert prices["claude-sonnet-5"] == ModelPrice(3.0, 15.0)


@pytest.mark.parametrize(
    "raw",
    [
        {"m": {"input": 3.0}},  # falta 'output'
        {"m": {"input": "car", "output": 1.0}},  # no es un numero
        {"m": 3.0},  # no es un objecte
        {"m": {"input": -1.0, "output": 1.0}},  # negatiu
    ],
)
def test_parse_prices_refusa_configuracio_dolenta(raw: dict) -> None:
    # Millor petar que calcular un cost amb una configuracio que no s'enten.
    with pytest.raises(PriceConfigError):
        parse_prices(raw)


def test_price_for_tria_el_prefix_mes_especific() -> None:
    prices = parse_prices(
        {
            "claude": {"input": 1.0, "output": 1.0},
            "claude-haiku-4-5": {"input": 0.8, "output": 4.0},
        }
    )
    # Un model amb data al nom l'ha de cobrir el prefix mes llarg, no el generic.
    assert price_for("claude-haiku-4-5-20251001", prices) == ModelPrice(0.8, 4.0)
    assert price_for("claude-opus-5", prices) == ModelPrice(1.0, 1.0)
    assert price_for("gpt-4", prices) is None


def test_cost_amb_preu_configurat() -> None:
    cost = CostMetrics(
        input_tokens=1_000_000,
        output_tokens=500_000,
        tenders=2,
        model="claude-sonnet-5",
        price=ModelPrice(3.0, 15.0),
    )
    # 1 M d'entrada a 3 + 0,5 M de sortida a 15 = 10,5
    assert cost.estimated_cost == pytest.approx(10.5)
    data = cost.as_dict(correct_requirements=5)
    assert data["cost_per_tender"] == pytest.approx(5.25)
    assert data["cost_per_correct_requirement"] == pytest.approx(2.1)
    # El preu queda escrit al costat del cost: sense ell no es reproduible.
    assert data["price_input_per_unit"] == 3.0
    assert data["price_output_per_unit"] == 15.0
    assert data["price_unit_tokens"] == TOKENS_PER_UNIT
    assert data["currency"] == "USD"
    assert data["cost_unavailable_reason"] is None


def test_sense_preu_no_hi_ha_cost_i_es_diu_per_que() -> None:
    cost = CostMetrics(
        input_tokens=91_927, output_tokens=34_335, tenders=1, model="claude-sonnet-5"
    )
    data = cost.as_dict(correct_requirements=2)
    # Els tokens s'han de veure igualment: son la mesura que si que tenim.
    assert data["input_tokens"] == 91_927
    assert data["output_tokens"] == 34_335
    assert data["estimated_cost"] is None
    assert data["cost_per_tender"] is None
    assert data["currency"] is None
    assert "claude-sonnet-5" in data["cost_unavailable_reason"]
    assert "LLM_MODEL_PRICES" in data["cost_unavailable_reason"]


def test_cost_zero_no_es_cost_desconegut() -> None:
    """Reaprofitar-ho tot de la cache costa zero, i zero no es 'no se sap'."""
    cost = CostMetrics(tenders=1, model="claude-sonnet-5", price=ModelPrice(3.0, 15.0))
    data = cost.as_dict()
    assert data["estimated_cost"] == 0.0
    assert data["cost_unavailable_reason"] is None
