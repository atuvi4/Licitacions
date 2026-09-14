"""Proteccio de pressupost del benchmark: saber que costaria abans de pagar-ho.

Un run es paga per chunk. El que es protegeix aqui:

* `--plan-only` diu que s'enviaria sense construir ni cridar cap proveidor;
* cap execucio amb proveidor real comenca sense `--paid-run`;
* l'estimacio de volum surt de tokens registrats, i sense historic o sense preu
  configurat no s'inventa cap xifra.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.profiles import V1
from app.ai.provider import StructuredResult, Usage
from app.ai.providers import fake
from app.core.config import settings
from app.evaluation import benchmark, plan, requirements
from app.models.tenders import Tender
from app.services.requirement_analysis import analyze_tender_requirements
from tests import test_requirement_analysis as analysis_tests
from tests.test_benchmark_harness import BENCHMARK_DSN, _StubProvider, gold

# Fixtures compartides amb els tests de la canonada: el plec amb dues pagines de
# text i el proveidor fals net a cada test.
analyzable = analysis_tests.analyzable
_clean_provider = analysis_tests._clean_provider
requirement_payload = analysis_tests.requirement_payload


class _NoDispose:
    """El motor dels tests: el benchmark no l'ha de tancar."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def dispose(self):
        return None


def _no_provider(*_args, **_kwargs):
    raise AssertionError("el pla no pot construir cap proveidor")


# --------------------------------------------------------------------------- #
# --plan-only                                                                   #
# --------------------------------------------------------------------------- #


async def test_plan_only_shows_what_would_be_sent_without_any_provider(
    tmp_path, engine, session, analyzable, capsys, monkeypatch
):
    tender = await session.get(Tender, analyzable["document"].tender_id)
    monkeypatch.setattr(benchmark, "load_gold", lambda _name: gold(tender.external_id))
    monkeypatch.setattr(benchmark, "resolve_provider", _no_provider)
    monkeypatch.setattr(benchmark, "get_provider", _no_provider)
    monkeypatch.setattr(benchmark, "evaluate_dataset", _no_provider)
    monkeypatch.setattr(benchmark, "create_async_engine", lambda url: _NoDispose(engine))
    runs = tmp_path / "runs"
    args = benchmark.build_parser().parse_args(
        ["--dataset", "gold-de-prova", "--profile", "v1", "--plan-only",
         "--database-url", BENCHMARK_DSN, "--output", str(runs)]
    )

    assert await benchmark.run(args) == 0

    out = capsys.readouterr().out
    assert "cas-1: 1 document(s), 2/2 pagines" in out
    assert "1 chunks a enviar" in out
    assert "PCAP.pdf" in out
    # El gold de prova cita la p.3, i el plec nomes en te la 27 i la 28.
    assert "gold fora del pla, NO mesurable: req-1 (PCAP.pdf p.3)" in out
    assert "NO s'ha construit ni cridat cap proveidor" in out
    assert not runs.exists(), "un pla no deixa cap artefacte de run"


async def test_plan_only_without_corpus_says_so(tmp_path, engine, capsys, monkeypatch):
    monkeypatch.setattr(benchmark, "load_gold", lambda _name: gold())
    monkeypatch.setattr(benchmark, "resolve_provider", _no_provider)
    monkeypatch.setattr(benchmark, "create_async_engine", lambda url: _NoDispose(engine))
    args = benchmark.build_parser().parse_args(
        ["--plan-only", "--database-url", BENCHMARK_DSN, "--output", str(tmp_path / "runs")]
    )

    assert await benchmark.run(args) == 4
    assert "SENSE CORPUS" in capsys.readouterr().out


def test_plan_only_and_paid_run_cannot_be_combined():
    with pytest.raises(SystemExit):
        benchmark.build_parser().parse_args(["--plan-only", "--paid-run"])


async def test_the_plan_knows_a_reusable_run_costs_nothing(session, analyzable):
    extraction = analyzable["extraction"]
    fake.queue_response({"requirements": [requirement_payload(extraction.id)]})
    tender = await session.get(Tender, analyzable["document"].tender_id)
    llm = fake.FakeProvider()
    await analyze_tender_requirements(session, tender, provider=llm, profile=V1)
    dataset = gold(tender.external_id)

    reused = await plan.build_plan(
        session, dataset, profile=V1, provider_name=llm.name, model=llm.model
    )
    forced = await plan.build_plan(
        session, dataset, profile=V1, provider_name=llm.name, model=llm.model, force=True
    )

    assert reused.cases[0].reusable_run_id is not None
    assert reused.chunks_to_send == 0
    assert forced.chunks_to_send == 1


async def test_the_estimate_comes_from_recorded_tokens(session, analyzable, monkeypatch):
    extraction = analyzable["extraction"]
    fake.queue_response(
        StructuredResult(
            data={"requirements": [requirement_payload(extraction.id)]},
            usage=Usage(input_tokens=1200, output_tokens=300, request_id="req-1"),
            model="fake-model-v1",
        )
    )
    tender = await session.get(Tender, analyzable["document"].tender_id)
    llm = fake.FakeProvider()
    await analyze_tender_requirements(session, tender, provider=llm, profile=V1)

    monkeypatch.setattr(settings, "LLM_MODEL_PRICES", {})
    result = await plan.build_plan(
        session, gold(tender.external_id), profile=V1, provider_name=llm.name,
        model=llm.model, force=True,
    )
    estimate = result.estimate()

    assert (estimate["input_tokens"], estimate["output_tokens"]) == (1200, 300)
    assert "1 run(s), 1 peticio(ns)" in estimate["basis"]
    assert estimate["cost"] is None, "sense preu configurat no hi ha cost"
    assert "LLM_MODEL_PRICES" in estimate["cost_unavailable_reason"]
    assert "ESTIMACIO" in plan.render_plan(result)

    monkeypatch.setattr(
        settings, "LLM_MODEL_PRICES", {"fake-model": {"input": 1000.0, "output": 2000.0}}
    )
    assert result.estimate()["cost"] == pytest.approx(1.8)


def test_without_history_nothing_is_estimated():
    empty = plan.BenchmarkPlan(provider="anthropic", model="m", profile="v2",
                               prompt_version="requirements-v2",
                               cases=[plan.CasePlan(case_id="c", tender_id="t", chunks=8)])

    estimate = empty.estimate()

    assert estimate["chunks"] == 8
    assert estimate["input_tokens"] is None and estimate["cost"] is None
    assert "no disponible" in plan.render_plan(empty)


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        # Amb cobertura: nomes els chunks que el proveidor va respondre.
        (SimpleNamespace(chunk_coverage={"chunks": [{"input_tokens": 10}, {"input_tokens": None}]},
                         provider_metadata={"request_ids": ["a", "b"]}, chunks_completed=1), 1),
        # Runs anteriors: els request_ids diuen quantes respostes hi va haver.
        (SimpleNamespace(chunk_coverage=None, provider_metadata={"request_ids": ["a", "b", "c"]},
                         chunks_completed=2), 3),
        (SimpleNamespace(chunk_coverage=None, provider_metadata=None, chunks_completed=4), 4),
    ],
    ids=["cobertura", "request-ids", "completats"],
)
def test_requests_are_counted_from_the_best_available_source(run, expected):
    assert plan._request_count(run) == expected


# --------------------------------------------------------------------------- #
# --paid-run                                                                    #
# --------------------------------------------------------------------------- #


def _no_database(*_args, **_kwargs):
    raise AssertionError("sense --paid-run no s'ha de connectar ni mesurar res")


async def test_a_real_run_does_not_start_without_paid_run(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(benchmark, "resolve_provider", lambda name: _StubProvider())
    monkeypatch.setattr(benchmark, "create_async_engine", _no_database)
    monkeypatch.setattr(benchmark, "evaluate_dataset", _no_database)
    runs = tmp_path / "runs"
    args = benchmark.build_parser().parse_args(
        ["--dataset", "gold-v2.1", "--profile", "v2",
         "--database-url", BENCHMARK_DSN, "--output", str(runs)]
    )

    assert await benchmark.run(args) == benchmark.EXIT_PAID_RUN_NOT_CONFIRMED
    out = capsys.readouterr().out
    assert "TE COST" in out and "--paid-run" in out and "--plan-only" in out
    assert not runs.exists()


async def test_the_single_profile_cli_also_requires_paid_run(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(requirements, "load_dataset", lambda _name: gold())
    monkeypatch.setattr(requirements, "resolve_provider", lambda name: _StubProvider())
    monkeypatch.setattr(requirements, "evaluate_dataset", _no_database)
    monkeypatch.setattr(requirements, "SessionFactory", _no_database)
    args = requirements.build_parser().parse_args(["--output", str(tmp_path / "informes")])

    assert await requirements.run(args) == benchmark.EXIT_PAID_RUN_NOT_CONFIRMED
    assert "TE COST" in capsys.readouterr().out
    assert not (tmp_path / "informes").exists()
