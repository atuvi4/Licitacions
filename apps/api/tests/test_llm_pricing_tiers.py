"""Preu per trams segons la longitud del prompt (Claude Haiku 5.5). Cap xarxa, cap BD.

Preu oficial de `claude-haiku-5-5` (Claude Platform): prompt <= 100.000 tokens, $0.10/$0.50
per MTok; > 100.000, $0.50/$2.50. Que es protegeix:

* el tram es decideix per PETICIO, pels seus tokens d'entrada (llibre, cost d'un chunk i cada
  peticio d'una estimacio); una estimacio de diversos chunks suma costos ja tarifats;
* el llindar de l'entorn (100000.0, perque la configuracio tipa els valors com a float) val;
  qualsevol altre llindar estrany (fraccio, NaN, infinit, negatiu, cadena...) falla tancat;
* un tram no valid o incoherent deixa el model sense preu (`price_unknown`): mai a la baixa;
* l'arrodoniment es HALF_UP al microdolar, com el llibre;
* un model sense tram (Haiku 4.5, V1) calcula exactament com abans.
"""

from __future__ import annotations

import math
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from app.core.config import Settings, settings
from app.models.enums import LlmBudgetScope
from app.services import llm_budget
from app.services.llm_budget import TokenGuess

HAIKU_55 = "claude-haiku-5-5"
HAIKU_45 = "claude-haiku-4-5-20251001"
BASE = {"input": 0.10, "output": 0.50}
LONG = {"above_input_tokens": 100_000, "input": 0.50, "output": 2.50}
BASE_PRICE = llm_budget.ModelPrice(Decimal("0.1"), Decimal("0.5"))
LONG_PRICE = llm_budget.ModelPrice(Decimal("0.5"), Decimal("2.5"))


@pytest.fixture(autouse=True)
def prices(monkeypatch):
    monkeypatch.setattr(
        settings, "LLM_MODEL_PRICES", {HAIKU_55: BASE, HAIKU_45: {"input": 1.0, "output": 5.0}}
    )
    monkeypatch.setattr(settings, "LLM_MODEL_LONG_CONTEXT_PRICES", {HAIKU_55: LONG})


def _from_env(field: str, payload: str):
    """El valor tal com arriba de l'entorn: JSON validat pel tipus real del camp."""
    return TypeAdapter(Settings.model_fields[field].annotation).validate_json(payload)


def _cost(input_tokens: int, output_tokens: int = 8_000) -> Decimal | None:
    return llm_budget.chunk_cost(HAIKU_55, input_tokens=input_tokens, output_tokens=output_tokens)


# --------------------------------------------------------------------------- #
# Llindars del tram                                                             #
# --------------------------------------------------------------------------- #


def test_els_tres_llindars_99999_100000_100001():
    assert llm_budget.model_price(HAIKU_55, input_tokens=99_999) == BASE_PRICE
    assert llm_budget.model_price(HAIKU_55, input_tokens=100_000) == BASE_PRICE
    assert llm_budget.model_price(HAIKU_55, input_tokens=100_001) == LONG_PRICE
    # 99.999 x 0,10 + 8.000 x 0,50 = 13.999,9 microdolars -> HALF_UP
    assert _cost(99_999) == Decimal("0.014000")
    assert _cost(100_000) == Decimal("0.014000")
    # 100.001 x 0,50 + 8.000 x 2,50 = 70.000,5 microdolars -> HALF_UP 0,070001 (HALF_EVEN: 0,070000)
    assert _cost(100_001) == Decimal("0.070001")
    # Magnitud del canari (chunk 0 calibrat): ben per sota.
    assert _cost(9_132) == Decimal("0.004913")


@pytest.mark.parametrize("threshold", [100_000, 100_000.0])
def test_el_llindar_enter_i_el_float_enter_son_el_mateix(monkeypatch, threshold):
    monkeypatch.setattr(
        settings,
        "LLM_MODEL_LONG_CONTEXT_PRICES",
        {HAIKU_55: {**LONG, "above_input_tokens": threshold}},
    )
    tier = llm_budget.long_context_price(HAIKU_55)
    assert tier == (100_000, LONG_PRICE)
    assert type(tier[0]) is int
    assert (_cost(100_000), _cost(100_001)) == (Decimal("0.014000"), Decimal("0.070001"))


def test_el_tram_llegit_de_l_entorn_es_valid(monkeypatch):
    parsed = _from_env(
        "LLM_MODEL_LONG_CONTEXT_PRICES",
        '{"claude-haiku-5-5": {"above_input_tokens": 100000, "input": 0.50, "output": 2.50}}',
    )
    assert parsed[HAIKU_55]["above_input_tokens"] == 100000.0  # el parany: arriba com a float
    monkeypatch.setattr(settings, "LLM_MODEL_LONG_CONTEXT_PRICES", parsed)
    monkeypatch.setattr(
        settings,
        "LLM_MODEL_PRICES",
        _from_env("LLM_MODEL_PRICES", '{"claude-haiku-5-5": {"input": 0.10, "output": 0.50}}'),
    )
    assert llm_budget.long_context_price(HAIKU_55) == (100_000, LONG_PRICE)
    assert (_cost(99_999), _cost(100_000), _cost(100_001)) == (
        Decimal("0.014000"),
        Decimal("0.014000"),
        Decimal("0.070001"),
    )


@pytest.mark.parametrize(
    "threshold",
    [100_000.5, math.nan, math.inf, -math.inf, -100_000, -100_000.0, 0, 0.0, "100000", "car",
     None, True, [100_000], llm_budget.MAX_TIER_THRESHOLD_TOKENS + 1, 1e300],
)  # fmt: skip
def test_un_llindar_no_valid_deixa_el_model_sense_preu(monkeypatch, threshold):
    monkeypatch.setattr(
        settings,
        "LLM_MODEL_LONG_CONTEXT_PRICES",
        {HAIKU_55: {**LONG, "above_input_tokens": threshold}},
    )
    assert llm_budget.long_context_price(HAIKU_55) is False
    assert llm_budget.model_price(HAIKU_55) is None
    assert _cost(10, 10) is None
    provider = SimpleNamespace(name="anthropic", model=HAIKU_55)
    monkeypatch.setattr(settings, "LLM_ALLOWED_MODELS", [HAIKU_55])
    assert llm_budget.PRICE_UNKNOWN in llm_budget.provider_failures(provider)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_nan_i_infinit_de_l_entorn_son_invalids(monkeypatch, token):
    parsed = _from_env(
        "LLM_MODEL_LONG_CONTEXT_PRICES",
        f'{{"claude-haiku-5-5": {{"above_input_tokens": {token}, "input": 0.5, "output": 2.5}}}}',
    )
    monkeypatch.setattr(settings, "LLM_MODEL_LONG_CONTEXT_PRICES", parsed)
    assert llm_budget.model_price(HAIKU_55) is None


@pytest.mark.parametrize(
    "bad",
    [{"input": 0.5, "output": 2.5}, {**LONG, "output": "car"}, {**LONG, "input": -1},
     {**LONG, "input": math.nan}, {**LONG, "output": math.inf}, "no es un tram"],
)  # fmt: skip
def test_un_tram_mal_format_deixa_el_model_sense_preu(monkeypatch, bad):
    monkeypatch.setattr(settings, "LLM_MODEL_LONG_CONTEXT_PRICES", {HAIKU_55: bad})
    assert llm_budget.model_price(HAIKU_55) is None
    assert _cost(10, 10) is None


@pytest.mark.parametrize(
    "incoherent",
    [{**LONG, "input": 0.05}, {**LONG, "output": 0.25}, {**LONG, "input": 0.05, "output": 0.25}],
)
def test_un_tram_mes_barat_que_el_base_es_incoherent(monkeypatch, incoherent):
    monkeypatch.setattr(settings, "LLM_MODEL_LONG_CONTEXT_PRICES", {HAIKU_55: incoherent})
    assert llm_budget.long_context_price(HAIKU_55) is False
    assert llm_budget.model_price(HAIKU_55) is None


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_un_preu_base_no_finit_deixa_el_model_sense_preu(monkeypatch, value):
    # Abans, NaN petava en comparar-lo amb 0: ara falla tancat.
    monkeypatch.setattr(settings, "LLM_MODEL_PRICES", {HAIKU_55: {**BASE, "input": value}})
    assert llm_budget.model_price(HAIKU_55) is None


# --------------------------------------------------------------------------- #
# Model sense tram                                                              #
# --------------------------------------------------------------------------- #


def test_un_model_sense_tram_calcula_exactament_com_abans():
    assert llm_budget.chunk_cost(HAIKU_45, input_tokens=6_359, output_tokens=2_462) == Decimal(
        "0.018669"
    )
    assert llm_budget.chunk_cost(HAIKU_45, input_tokens=500_000, output_tokens=0) == Decimal(
        "0.500000"
    )
    assert llm_budget.model_price(HAIKU_45) == llm_budget.model_price(HAIKU_45, input_tokens=10**9)
    guess = TokenGuess(240_000, 24_000, "characters", requests=((80_000, 8_000),) * 3)
    assert llm_budget.estimate_cost(HAIKU_45, guess) == llm_budget.chunk_cost(
        HAIKU_45, input_tokens=240_000, output_tokens=24_000
    )


# --------------------------------------------------------------------------- #
# Estimacions de diversos chunks                                                #
# --------------------------------------------------------------------------- #


def test_una_estimacio_decideix_el_tram_per_peticio_no_per_la_suma():
    # Tres peticions de 40.000: la suma (120.000) supera el llindar, pero cap peticio el supera.
    guess = TokenGuess(120_000, 24_000, "characters", requests=((40_000, 8_000),) * 3)
    per_request = sum(_cost(40_000) for _ in range(3))
    assert llm_budget.estimate_cost(HAIKU_55, guess) == per_request == Decimal("0.024000")


def test_una_estimacio_amb_trams_barrejats_suma_els_costos_tarifats():
    guess = TokenGuess(160_001, 16_000, "characters", requests=((100_001, 8_000), (60_000, 8_000)))
    # 70.000,5 + 10.000 microdolars, arrodonits un sol cop.
    assert llm_budget.estimate_cost(HAIKU_55, guess) == Decimal("0.080001")
    assert llm_budget.estimate_cost(HAIKU_55, guess) == _cost(100_001) + _cost(60_000)


def test_una_estimacio_de_l_historic_fa_servir_la_mitjana_per_peticio():
    # Historic: 3 peticions de mitjana 40.000 -> tram base per a totes.
    guess = TokenGuess(120_000, 24_000, "history", requests=((40_000.0, 8_000.0),) * 3)
    assert llm_budget.estimate_cost(HAIKU_55, guess) == Decimal("0.024000")


def test_sense_preu_l_estimacio_no_te_cost(monkeypatch):
    monkeypatch.setattr(
        settings, "LLM_MODEL_LONG_CONTEXT_PRICES", {HAIKU_55: {**LONG, "input": "x"}}
    )
    guess = TokenGuess(1, 1, "characters", requests=((1, 1),))
    assert llm_budget.estimate_cost(HAIKU_55, guess) is None


# --------------------------------------------------------------------------- #
# Llibre (HALF_UP)                                                              #
# --------------------------------------------------------------------------- #


def test_el_llibre_registra_el_cost_amb_el_tram_de_la_peticio_i_half_up():
    added = []
    session = SimpleNamespace(add=added.append)
    run = SimpleNamespace(id=uuid.uuid4(), provider="anthropic", model=HAIKU_55)

    def record(chunk_index: int, input_tokens: int, output_tokens: int) -> Decimal:
        return llm_budget.record_chunk_spend(
            session,
            run=run,
            chunk_index=chunk_index,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            scope=LlmBudgetScope.RUN,
        )

    costs = (
        record(0, 6_764, 2_462),  # 676,4 + 1.231 = 1.907,4 -> 0,001907
        record(1, 150_000, 1_000),  # tram car: 75.000 + 2.500 -> 0,077500
        record(2, 5, 0),  # 0,5 microdolars -> HALF_UP 0,000001 (HALF_EVEN: 0,000000)
        record(3, 100_001, 0),  # tram car: 50.000,5 -> HALF_UP 0,050001
    )
    assert costs == (
        Decimal("0.001907"),
        Decimal("0.077500"),
        Decimal("0.000001"),
        Decimal("0.050001"),
    )
    assert [row.cost_usd for row in added] == list(costs)
