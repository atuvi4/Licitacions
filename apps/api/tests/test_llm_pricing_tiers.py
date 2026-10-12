"""Preu per trams segons la longitud del prompt (Claude Haiku 5.5). Cap xarxa, cap BD.

Preu oficial de `claude-haiku-5-5` (Claude Platform): prompt <= 100.000 tokens, $0.10/$0.50
per MTok; > 100.000, $0.50/$2.50. Que es protegeix:

* el llibre i el cost d'un chunk fan servir el tram de la PETICIO (pels seus tokens d'entrada);
* una estimacio de diversos chunks decideix el tram amb la SUMA: mai subestima;
* un tram mal format deixa el model sense preu (`price_unknown`): mai es calcula a la baixa;
* un model sense tram (Haiku 4.5, V1) calcula exactament com abans.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models.enums import LlmBudgetScope
from app.services import llm_budget

HAIKU_55 = "claude-haiku-5-5"
HAIKU_45 = "claude-haiku-4-5-20251001"
BASE = {"input": 0.10, "output": 0.50}
LONG = {"above_input_tokens": 100_000, "input": 0.50, "output": 2.50}


@pytest.fixture(autouse=True)
def prices(monkeypatch):
    monkeypatch.setattr(
        settings, "LLM_MODEL_PRICES", {HAIKU_55: BASE, HAIKU_45: {"input": 1.0, "output": 5.0}}
    )
    monkeypatch.setattr(settings, "LLM_MODEL_LONG_CONTEXT_PRICES", {HAIKU_55: LONG})


def test_el_tram_es_decideix_pels_tokens_d_entrada_de_la_peticio():
    # <= 100.000: tram base.
    assert llm_budget.chunk_cost(HAIKU_55, input_tokens=100_000, output_tokens=8_000) == Decimal(
        "0.014000"
    )
    # > 100.000: tram car, per a tota la peticio (entrada i sortida).
    assert llm_budget.chunk_cost(HAIKU_55, input_tokens=100_001, output_tokens=8_000) == Decimal(
        "0.070001"
    )
    # Magnituds del canari (chunk 0 calibrat): ben per sota de 100.000.
    assert llm_budget.chunk_cost(HAIKU_55, input_tokens=9_132, output_tokens=8_000) == Decimal(
        "0.004913"
    )


def test_un_model_sense_tram_calcula_exactament_com_abans():
    assert llm_budget.chunk_cost(HAIKU_45, input_tokens=6_359, output_tokens=2_462) == Decimal(
        "0.018669"
    )
    assert llm_budget.chunk_cost(HAIKU_45, input_tokens=500_000, output_tokens=0) == Decimal(
        "0.500000"
    )
    assert llm_budget.model_price(HAIKU_45) == llm_budget.model_price(HAIKU_45, input_tokens=10**9)


@pytest.mark.parametrize(
    "bad",
    [{"input": 0.5, "output": 2.5}, {**LONG, "above_input_tokens": "100000"},
     {**LONG, "above_input_tokens": 0}, {**LONG, "output": "car"}, {**LONG, "input": -1},
     {**LONG, "above_input_tokens": True}],
)  # fmt: skip
def test_un_tram_mal_format_deixa_el_model_sense_preu(monkeypatch, bad):
    monkeypatch.setattr(settings, "LLM_MODEL_LONG_CONTEXT_PRICES", {HAIKU_55: bad})
    assert llm_budget.model_price(HAIKU_55) is None
    assert llm_budget.chunk_cost(HAIKU_55, input_tokens=10, output_tokens=10) is None
    provider = SimpleNamespace(name="anthropic", model=HAIKU_55)
    monkeypatch.setattr(settings, "LLM_ALLOWED_MODELS", [HAIKU_55])
    assert llm_budget.PRICE_UNKNOWN in llm_budget.provider_failures(provider)


def test_una_estimacio_de_diversos_chunks_no_subestima():
    # Tres chunks de 40.000: cadascun aniria al tram base, pero la suma (120.000) supera el
    # llindar i l'estimacio fa servir el tram car: mai per sota del cost real.
    estimate = llm_budget.chunk_cost(HAIKU_55, input_tokens=120_000, output_tokens=24_000)
    real = sum(
        llm_budget.chunk_cost(HAIKU_55, input_tokens=40_000, output_tokens=8_000) for _ in range(3)
    )
    assert estimate >= real


def test_el_llibre_registra_el_cost_amb_el_tram_de_la_peticio():
    added = []
    session = SimpleNamespace(add=added.append)
    run = SimpleNamespace(id=uuid.uuid4(), provider="anthropic", model=HAIKU_55)
    base = llm_budget.record_chunk_spend(
        session,
        run=run,
        chunk_index=0,
        input_tokens=6_764,
        output_tokens=2_462,
        scope=LlmBudgetScope.CANARY,
    )
    long = llm_budget.record_chunk_spend(
        session,
        run=run,
        chunk_index=1,
        input_tokens=150_000,
        output_tokens=1_000,
        scope=LlmBudgetScope.RUN,
    )
    assert (base, long) == (Decimal("0.001907"), Decimal("0.077500"))
    assert [row.cost_usd for row in added] == [base, long]
