"""El probe d'operador de la v3: guardes de base, de mode, de licitacio i de chunks.

Zero crides externes: proveidor fals, SQLite de test, i el cas de PostgreSQL
nomes com a DSN que s'ha de refusar abans de connectar.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.ai import profiles
from app.ai.providers import fake
from app.core.config import settings
from app.models.enums import AnalysisRunStatus
from app.models.requirements import AwardCriterion, Requirement, RequirementExtractionRun
from app.models.tenders import Tender
from tests import test_requirement_analysis as base

analyzable = base.analyzable
_clean_provider = base._clean_provider

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "v3_tender_probe.py"
_spec = importlib.util.spec_from_file_location("v3_tender_probe", _SCRIPT)
probe = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("v3_tender_probe", probe)  # les dataclasses ho necessiten
_spec.loader.exec_module(probe)

OK_DSN = "postgresql+asyncpg://u:p@127.0.0.1:5433/licitacions_v3_probe_palamos"


def _dsn(database: str, port: int | None = 5433) -> str:
    host = f"127.0.0.1:{port}" if port else "127.0.0.1"
    return f"postgresql+asyncpg://u:p@{host}/{database}"


# --------------------------------------------------------------------------- #
# Base de dades                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    ["licitacions", "licitacions_review_s3", "licitacions_benchmark", "licitacions_test",
     "postgres", "template1"],
)
def test_les_bases_canoniques_es_refusen(name):
    with pytest.raises(probe.ProbeError, match="canonica"):
        probe.validate_probe_database(_dsn(name))


@pytest.mark.parametrize(
    "name",
    ["licitacions_copy", "v3_probe_palamos", "licitacions_v3_probe_", "Licitacions_v3_probe_X"],
)
def test_cal_el_prefix_de_base_d_usar_i_llencar(name):
    with pytest.raises(probe.ProbeError, match="licitacions_v3_probe_"):
        probe.validate_probe_database(_dsn(name))


def test_una_base_de_probe_s_accepta():
    assert probe.validate_probe_database(OK_DSN).database == "licitacions_v3_probe_palamos"


@pytest.mark.parametrize(
    ("dsn", "why"),
    [
        (_dsn("licitacions_v3_probe_x", port=5432), "3CX"),
        (_dsn("licitacions_v3_probe_x", port=None), "port"),
        (OK_DSN + "?options=-csearch_path%3Dpublic", "parametres"),
        ("postgresql://u:p@127.0.0.1:5433/licitacions_v3_probe_x", "asyncpg"),
        ("postgresql+asyncpg://u:p@a:5433,b:5433/licitacions_v3_probe_x", "host"),
        (None, "Falta"),
    ],
)
def test_dsns_inequivoques_nomes(dsn, why):
    with pytest.raises(probe.ProbeError, match=why):
        probe.validate_probe_database(dsn)


class _StubSession:
    """Prou sessio per a `check_database`: respon les dues consultes en ordre."""

    def __init__(self, *answers):
        self._answers = list(answers)

    async def scalar(self, _query):
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @asynccontextmanager
    async def begin_nested(self):
        yield


async def test_una_base_endarrerida_falla_tancada_sense_migrar():
    url = probe.validate_probe_database(OK_DSN)

    with pytest.raises(probe.ProbeError, match="alembic upgrade head"):
        await probe.check_database(_StubSession(url.database, "0037_workspace_issues"), url)
    with pytest.raises(probe.ProbeError, match="no a"):
        await probe.check_database(_StubSession("licitacions_review_s3"), url)
    with pytest.raises(probe.ProbeError, match="None"):
        await probe.check_database(_StubSession(url.database, RuntimeError("no table")), url)

    assert await probe.check_database(_StubSession(url.database, probe.alembic_head()), url) == (
        probe.alembic_head()
    )


# --------------------------------------------------------------------------- #
# Modes                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mode", [[], ["--plan-only", "--paid-run"]]
)
def test_no_hi_ha_cap_mode_implicit(mode):
    with pytest.raises(SystemExit):
        probe.build_parser().parse_args(
            ["--database-url", OK_DSN, "--expediente", "2024-3137", *mode]
        )


@pytest.mark.parametrize("provider", ["fake", "fixture"])
async def test_un_proveidor_simulat_no_pot_fer_un_run_pagat(provider, monkeypatch, capsys):
    def _no_db(*_args, **_kwargs):
        raise AssertionError("no s'ha de connectar a cap base")

    monkeypatch.setattr(probe, "create_async_engine", _no_db)
    args = probe.build_parser().parse_args(
        ["--database-url", OK_DSN, "--expediente", "2024-3137", "--provider", provider,
         "--paid-run"]
    )

    assert await probe.run(args) == probe.EXIT_CONFIG
    assert "no llegeix cap plec" in capsys.readouterr().out


async def test_una_base_canonica_s_atura_abans_de_connectar(monkeypatch):
    monkeypatch.setattr(probe, "create_async_engine", lambda *a, **k: pytest.fail("connecta"))
    args = probe.build_parser().parse_args(
        ["--database-url", _dsn("licitacions_review_s3"), "--expediente", "x", "--plan-only"]
    )

    assert await probe.run(args) == probe.EXIT_CONFIG


def test_la_v3_continua_fora_de_profiles():
    assert "v3" not in profiles.PROFILES
    assert profiles.DEFAULT_PROFILE is profiles.V1


# --------------------------------------------------------------------------- #
# Licitacio                                                                    #
# --------------------------------------------------------------------------- #


async def _tender_with(session, analyzable, expediente):
    tender = await session.get(Tender, analyzable["document"].tender_id)
    tender.expediente_number = expediente
    await session.commit()
    return tender


async def test_una_sola_licitacio_per_expedient_exacte(session, analyzable):
    tender = await _tender_with(session, analyzable, "2024-3137")

    found = await probe.resolve_single_tender(session, expediente=" 2024-3137 ")

    assert found.id == tender.id
    with pytest.raises(probe.ProbeError, match="0 licitacions"):
        await probe.resolve_single_tender(session, expediente="2024-313")


async def test_dues_licitacions_amb_el_mateix_expedient_s_aturen(session, analyzable):
    tender = await _tender_with(session, analyzable, "2024-3137")
    session.add(Tender(source_id=tender.source_id, external_id="altre", title="Altre",
                       expediente_number="2024-3137"))
    await session.commit()

    with pytest.raises(probe.ProbeError, match="2 licitacions"):
        await probe.resolve_single_tender(session, expediente="2024-3137")
    found = await probe.resolve_single_tender(
        session, expediente="2024-3137", tender_id=tender.id
    )
    assert found.id == tender.id


async def test_l_organisme_esperat_es_comprova(session, analyzable):
    await _tender_with(session, analyzable, "2024-3137")

    with pytest.raises(probe.ProbeError, match="organisme"):
        await probe.resolve_single_tender(
            session, expediente="2024-3137", expect_buyer="Palamós"
        )


# --------------------------------------------------------------------------- #
# Chunks                                                                       #
# --------------------------------------------------------------------------- #


_CHUNKS = [SimpleNamespace(index=i) for i in range(4)]


def test_sense_seleccio_son_tots_els_chunks():
    assert probe.parse_chunk_selection(None, _CHUNKS) == [0, 1, 2, 3]


def test_seleccio_explicita_ordenada_i_sense_repetits():
    assert probe.parse_chunk_selection("3, 1,3", _CHUNKS) == [1, 3]


@pytest.mark.parametrize("raw", ["9", "a", "", "1,-1"])
def test_seleccio_invalida_es_refusa(raw):
    with pytest.raises(probe.ProbeError):
        probe.parse_chunk_selection(raw, _CHUNKS)


def test_el_canari_minim_cobreix_totes_les_etiquetes():
    located = {"A": [14, 15], "B": [3], "C": [3, 4], "D": [1, 2]}

    assert probe.minimal_cover(located) == [1, 3, 14]
    with pytest.raises(probe.ProbeError, match="Cap chunk"):
        probe.minimal_cover({"A": []})


def test_els_localitzadors_tenen_format_estricte():
    assert probe.parse_locators(["A=technical_terms:31"]) == {"A": ("technical_terms", 31)}
    for bad in ("A=ppt:31", "A=technical_terms", "technical_terms:31"):
        with pytest.raises(probe.ProbeError):
            probe.parse_locators([bad])


# --------------------------------------------------------------------------- #
# Pla: cap proveidor, cap escriptura                                           #
# --------------------------------------------------------------------------- #


async def _counts(session):
    return tuple([
        await session.scalar(sa.select(sa.func.count()).select_from(model))
        for model in (RequirementExtractionRun, Requirement, AwardCriterion)
    ])


async def test_el_pla_no_construeix_proveidor_ni_escriu(session, analyzable, monkeypatch):
    import app.ai.provider as provider_module
    import app.evaluation.benchmark as benchmark

    def _forbidden(*_a, **_k):
        raise AssertionError("el pla no ha de construir cap proveidor")

    monkeypatch.setattr(provider_module, "get_provider", _forbidden)
    monkeypatch.setattr(benchmark, "resolve_provider", _forbidden)
    fake.SENT_PAYLOADS.clear()
    tender = await session.get(Tender, analyzable["document"].tender_id)
    before = await _counts(session)

    plan, chunks = await probe.build_probe_plan(
        session, tender, chunks_raw=None,
        locators={"X": ("administrative_terms", 28)},
        provider_name="anthropic", model="claude-sonnet-5",
    )

    assert await _counts(session) == before
    assert fake.SENT_PAYLOADS == []
    assert plan.provider_calls_made == 0
    assert plan.selected_chunks == [c.index for c in chunks]
    assert plan.max_provider_calls == plan.chunks_total == len(chunks)
    assert plan.located == {"X": [0]}
    assert plan.minimal_canary == [0]
    assert plan.prompt_version == "requirements-v3"
    assert plan.schema_version == "requirements-schema-v3"
    assert plan.output_token_cap == settings.LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS
    assert plan.token_estimate["available"] is False
    assert "PLA v3" in probe.render_plan(plan)


async def test_el_pla_amb_seleccio_nomes_compta_els_triats(session, analyzable, monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    tender = await session.get(Tender, analyzable["document"].tender_id)

    plan, chunks = await probe.build_probe_plan(
        session, tender, chunks_raw="1", locators={}, provider_name="x", model="y"
    )

    assert len(chunks) == 2
    assert plan.selected_chunks == [1]
    assert plan.max_provider_calls == 1
    assert plan.selected[0].pages == "p28-28"


async def test_un_document_d_adjudicacio_atura_el_pla(session, analyzable, monkeypatch):
    doc = SimpleNamespace(document_type="award", document_name="Acta", pages=(), page_count=0)
    monkeypatch.setattr(
        probe, "analysis_input",
        lambda *_a, **_k: _awaitable(SimpleNamespace(documents=[doc], skipped=[])),
    )
    tender = await session.get(Tender, analyzable["document"].tender_id)

    with pytest.raises(probe.ProbeError, match="adjudicacio"):
        await probe.build_probe_plan(
            session, tender, chunks_raw=None, locators={}, provider_name="x", model="y"
        )


async def _awaitable(value):
    return value


def test_l_informe_d_un_run_pagat_nomes_porta_codis():
    run = SimpleNamespace(chunk_coverage={"chunks": [
        {"index": 0, "status": "completed", "error": None, "stop_reason": "tool_use",
         "input_tokens": 9000, "output_tokens": 4000, "normalised": None,
         "rejections": [{"reason": "claim_not_supported"}, {"reason": "claim_not_supported"}]},
        {"index": 1, "status": "failed", "error": "output_truncated",
         "stop_reason": "max_tokens", "input_tokens": 9000, "output_tokens": 8000},
        {"index": 2, "status": "failed", "error": "schema_invalid", "stop_reason": "tool_use"},
        {"index": 3, "status": "failed", "error": "not_selected", "origin": "not_sent"},
    ]})

    rows = probe.chunk_report(run)

    assert rows[0]["rejections"] == {"claim_not_supported": 2}
    assert rows[1]["output_truncated"] is True and rows[1]["schema_invalid"] is False
    assert rows[2]["schema_invalid"] is True
    assert rows[3]["error_code"] == "not_selected"
    assert rows[3]["origin"] == "not_sent"


# --------------------------------------------------------------------------- #
# `only_chunks` a la canonada                                                  #
# --------------------------------------------------------------------------- #


async def test_only_chunks_nomes_envia_els_triats_i_la_represa_completa(
    session, analyzable, monkeypatch
):
    from app.services.requirement_analysis import analyze_tender_requirements

    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    tender = await session.get(Tender, analyzable["document"].tender_id)
    fake.SENT_PAYLOADS.clear()

    canary = (await analyze_tender_requirements(
        session, tender, provider=fake.FakeProvider([{"requirements": []}]),
        profile=profiles.V3, force=True, only_chunks=frozenset({1}),
    )).run

    assert len(fake.SENT_PAYLOADS) == 1
    assert canary.status == AnalysisRunStatus.PARTIAL
    coverage = {c["index"]: c for c in canary.chunk_coverage["chunks"]}
    assert coverage[0]["error"] == "not_selected"
    assert coverage[0]["origin"] == "not_sent", "no enviat, i la cobertura ho diu"
    assert coverage[1]["origin"] == "sent"
    assert coverage[1]["status"] == "completed"

    fake.SENT_PAYLOADS.clear()
    resumed = (await analyze_tender_requirements(
        session, tender, provider=fake.FakeProvider([{"requirements": []}]),
        profile=profiles.V3, resume_from_run_id=canary.id,
    )).run

    assert len(fake.SENT_PAYLOADS) == 1, "nomes el chunk que faltava"
    assert resumed.status == AnalysisRunStatus.COMPLETED


@pytest.mark.parametrize("bad", [frozenset({7}), frozenset()])
async def test_only_chunks_invalid_s_atura_abans_de_cap_run(session, analyzable, bad):
    from app.services.requirement_analysis import analyze_tender_requirements

    tender = await session.get(Tender, analyzable["document"].tender_id)
    fake.SENT_PAYLOADS.clear()

    with pytest.raises(ValueError):
        await analyze_tender_requirements(
            session, tender, provider=fake.FakeProvider([]), profile=profiles.V3,
            force=True, only_chunks=bad,
        )

    assert fake.SENT_PAYLOADS == []
    assert await session.scalar(
        sa.select(sa.func.count()).select_from(RequirementExtractionRun)
    ) == 0


async def test_sense_only_chunks_res_no_canvia(session, analyzable):
    from app.services.requirement_analysis import analyze_tender_requirements

    tender = await session.get(Tender, analyzable["document"].tender_id)
    run = (await analyze_tender_requirements(
        session, tender, provider=fake.FakeProvider([{"requirements": []}]),
    )).run

    assert run.status == AnalysisRunStatus.COMPLETED
    assert all(c["status"] == "completed" for c in run.chunk_coverage["chunks"])



# --------------------------------------------------------------------------- #
# Canari: no seleccionat != fallit                                             #
# --------------------------------------------------------------------------- #

from tests import test_v3_replay_regression as replay  # noqa: E402

palamos_like = replay.palamos_like


@pytest.fixture
def one_page_chunks(monkeypatch):
    """Les cinc pagines de `palamos_like`, un chunk per pagina: 5 chunks."""
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)


async def _canary(session, palamos_like, responses, selected):
    from app.services.requirement_analysis import analyze_tender_requirements

    tender = await session.get(Tender, palamos_like["document"].tender_id)
    fake.SENT_PAYLOADS.clear()
    return (await analyze_tender_requirements(
        session, tender, provider=fake.FakeProvider(responses), profile=profiles.V3,
        force=True, only_chunks=frozenset(selected),
    )).run


def run_codes(run):
    return {c["index"]: c["error"] for c in run.chunk_coverage["chunks"]}


async def test_tres_bons_i_dos_no_seleccionats_no_son_cap_fallada(
    session, palamos_like, one_page_chunks
):
    from app.services.analysis_coverage import coverage_summary

    run = await _canary(session, palamos_like, [{"requirements": []}] * 3, {0, 2, 4})

    assert len(fake.SENT_PAYLOADS) == 3
    assert run.status == AnalysisRunStatus.PARTIAL, "no s'ha llegit tot, i ho diu"
    assert run.error_code == "chunks_not_selected"
    assert (run.chunks_total, run.chunks_completed, run.chunks_failed) == (5, 3, 0)
    assert "fallits" not in (run.error_message or "")
    assert "2 no seleccionats" in run.error_message

    canary = probe.canary_summary(probe.chunk_report(run))
    assert canary == {
        "chunks_total": 5, "attempted": 3, "succeeded": 3, "actual_failures": 0,
        "not_selected": 2, "schema_invalid": 0, "output_truncated": 0,
    }
    summary = coverage_summary(run.chunk_coverage)
    assert (summary["chunks_failed"], summary["chunks_not_selected"]) == (0, 2)
    assert "not_selected" not in summary["errors"]


async def test_la_represa_reaprofita_els_bons_i_envia_la_resta(
    session, palamos_like, one_page_chunks
):
    from app.services.requirement_analysis import analyze_tender_requirements

    canary = await _canary(session, palamos_like, [{"requirements": []}] * 3, {0, 2, 4})
    tender = await session.get(Tender, palamos_like["document"].tender_id)
    fake.SENT_PAYLOADS.clear()

    full = (await analyze_tender_requirements(
        session, tender, provider=fake.FakeProvider([{"requirements": []}] * 2),
        profile=profiles.V3, resume_from_run_id=canary.id,
    )).run

    assert len(fake.SENT_PAYLOADS) == 2, "nomes els 2 no seleccionats"
    assert full.status == AnalysisRunStatus.COMPLETED
    assert full.provider_metadata["reused_chunks"] == [0, 2, 4]
    assert (full.chunks_completed, full.chunks_failed) == (5, 0)


async def test_un_chunk_triat_que_falla_es_una_fallada_i_es_reintenta(
    session, palamos_like, one_page_chunks
):
    from app.ai.provider import SchemaInvalidError, Usage
    from app.services.requirement_analysis import analyze_tender_requirements

    invalid = SchemaInvalidError("x", usage=Usage(input_tokens=10, output_tokens=5))
    canary = await _canary(
        session, palamos_like, [{"requirements": []}, invalid, {"requirements": []}], {0, 2, 4}
    )

    assert run_codes(canary) == {
        0: None, 1: "not_selected", 2: "schema_invalid", 3: "not_selected", 4: None,
    }
    assert (canary.chunks_completed, canary.chunks_failed) == (2, 1)
    assert canary.error_code == "some_chunks_failed"
    assert "1 de 3 chunks fallits" in canary.error_message
    counts = probe.canary_summary(probe.chunk_report(canary))
    assert (counts["attempted"], counts["actual_failures"], counts["schema_invalid"],
            counts["not_selected"]) == (3, 1, 1, 2)

    tender = await session.get(Tender, palamos_like["document"].tender_id)
    fake.SENT_PAYLOADS.clear()
    full = (await analyze_tender_requirements(
        session, tender, provider=fake.FakeProvider([{"requirements": []}] * 3),
        profile=profiles.V3, resume_from_run_id=canary.id,
    )).run

    assert len(fake.SENT_PAYLOADS) == 3, "el fallit i els dos no seleccionats"
    assert full.status == AnalysisRunStatus.COMPLETED


async def test_si_fallen_tots_els_triats_el_codi_ho_diu(session, palamos_like, one_page_chunks):
    from app.ai.provider import SchemaInvalidError, Usage

    invalid = SchemaInvalidError("x", usage=Usage(input_tokens=1, output_tokens=1))
    run = await _canary(session, palamos_like, [invalid, invalid], {1, 3})

    assert run.status == AnalysisRunStatus.FAILED
    assert run.error_code == "all_selected_chunks_failed"
    assert (run.chunks_completed, run.chunks_failed) == (0, 2)


def test_sense_canari_el_resum_de_fallades_no_canvia():
    from app.services.requirement_analysis import failure_summary

    assert failure_summary({1: "schema_invalid"}, 3, None) == (
        "1 de 3 chunks fallits (schema_invalid: 1)."
    )


# --------------------------------------------------------------------------- #
# Secrets                                                                      #
# --------------------------------------------------------------------------- #


def _no_scopes(_name):
    return []


def test_la_clau_ha_de_ser_nomes_de_la_sessio(tmp_path):
    env = tmp_path / ".env"
    nowhere = (tmp_path / "cap",)

    with pytest.raises(probe.ProbeError, match="entorn d'aquest proces"):
        probe.require_session_scoped_key(
            environ={}, persistent_scopes=_no_scopes, dotenv_paths=(env,)
        )
    env.write_text("ANTHROPIC_API_KEY=sk-ant-fals\n", encoding="utf-8")
    with pytest.raises(probe.ProbeError, match=r"\.env"):
        probe.require_session_scoped_key(
            environ={"ANTHROPIC_API_KEY": "x"}, persistent_scopes=_no_scopes,
            dotenv_paths=(env,),
        )
    with pytest.raises(probe.ProbeError, match="User"):
        probe.require_session_scoped_key(
            environ={"ANTHROPIC_API_KEY": "x"}, persistent_scopes=lambda _n: ["User"],
            dotenv_paths=nowhere,
        )
    probe.require_session_scoped_key(
        environ={"ANTHROPIC_API_KEY": "x"}, persistent_scopes=_no_scopes, dotenv_paths=nowhere
    )


def test_cap_error_no_mostra_la_contrasenya():
    url = probe.validate_probe_database(
        "postgresql+asyncpg://postgres:s3cr3t@127.0.0.1:5433/licitacions_v3_probe_palamos"
    )
    raw = f"could not connect to {url.render_as_string(hide_password=False)} (pwd s3cr3t)"

    shown = probe.redact(raw, url)

    assert "s3cr3t" not in shown
    assert "***" in shown


async def test_el_pla_no_imprimeix_la_dsn(session, analyzable):
    tender = await session.get(Tender, analyzable["document"].tender_id)
    plan, _ = await probe.build_probe_plan(
        session, tender, chunks_raw=None, locators={}, provider_name="x", model="y",
        database="licitacions_v3_probe_palamos", alembic_revision="head",
    )

    rendered = probe.render_plan(plan)

    assert "postgresql" not in rendered and "@" not in rendered
