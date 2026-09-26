"""L'arnes de benchmark: mesura el que se li demana, i no mesura res mes.

Un benchmark que es pot executar contra la base equivocada, amb un proveidor que
no llegeix cap plec o amb un perfil que ningu no ha triat, no produeix una
mesura: produeix una xifra amb aparenca de mesura. Aixo es el que hi ha aqui.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai.profiles import DEFAULT_PROFILE, V1, V2
from app.evaluation import benchmark
from app.evaluation.dataset import GoldDataset
from app.evaluation.dataset_v2 import GoldDatasetV2, load_gold
from app.evaluation.runner import evaluate_dataset, run_case
from app.models.tenders import Tender

BENCHMARK_DSN = "postgresql+asyncpg://u:p@127.0.0.1:5433/licitacions_benchmark"


class _StubProvider:
    """Un proveidor que no s'arriba a cridar: nomes ha de tenir nom i model."""

    name = "anthropic"
    model = "claude-test"

    def is_available(self) -> bool:
        return True


def gold(tender_external_id: str | None = "https://font/inexistent") -> GoldDataset:
    return GoldDataset.model_validate(
        {
            "dataset_id": "gold-de-prova",
            "version": 1,
            "created_at": date.today().isoformat(),
            "description": "Un cas, per comprovar que el perfil queda registrat.",
            "cases": [
                {
                    "case_id": "cas-1",
                    "tender_external_id": tender_external_id,
                    "requirements": [
                        {
                            "gold_id": "req-1",
                            "category": "certification",
                            "obligation": "mandatory",
                            "document_name": "PCAP.pdf",
                            "page_number": 3,
                            "quote": "Certificat ISO 14001 vigent.",
                            "annotation_status": "verified",
                            "reviewed_by": "persona",
                        }
                    ],
                }
            ],
        }
    )


# --------------------------------------------------------------------------- #
# El gold v2.1 es carrega tal com esta escrit                                   #
# --------------------------------------------------------------------------- #


def test_gold_v2_1_loads_with_its_own_schema():
    """Carregar-lo amb el lector v1 fallava; convertir-lo seria perdre la v2."""
    dataset = load_gold("gold-v2.1")

    assert isinstance(dataset, GoldDatasetV2)
    assert dataset.dataset_id == "gold-v2.1"
    assert dataset.gold_schema_version == "gold-schema-v2"
    assert len(dataset.cases) == 11
    assert dataset.total_requirements == 14
    assert dataset.total_award_criteria == 3


def test_loading_gold_v2_1_keeps_statuses_and_review_metadata():
    dataset = load_gold("gold-v2.1")
    requisits = [r for case in dataset.cases for r in case.requirements]
    criteris = [a for case in dataset.cases for a in case.award_criteria]

    assert {r.annotation_status.value for r in requisits} == {"verified", "needs_review"}
    assert all(r.reviewed_by for r in requisits), "la revisio humana no es pot perdre"
    assert any(r.review_note for r in requisits)
    # Camps que nomes existeixen a la v2: si es perdessin, no hi hauria res a mesurar.
    assert any(r.modality for r in requisits)
    assert any(r.origin.value == "proposal" for r in requisits)
    assert any(r.unresolved for r in requisits)
    assert criteris and all(a.document_name for a in criteris)
    assert dataset.status_counts()["counts_for_metrics"] >= 1


def test_the_benchmark_describes_the_v2_dataset_without_touching_it():
    dataset = load_gold("gold-v2.1")
    text = benchmark.describe_dataset(dataset)
    assert "gold-v2.1" in text and "Gold v2" in text


# --------------------------------------------------------------------------- #
# Seleccio de perfil                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("demanat", "esperat"),
    [("V1", "tender-requirements-v1"), ("v1", "tender-requirements-v1"),
     ("V2", "tender-requirements-v2"), ("v2", "tender-requirements-v2")],
)
def test_the_requested_profile_selects_its_extractor(demanat, esperat):
    assert benchmark.resolve_profile(demanat).extractor_version == esperat


def test_an_unknown_profile_is_refused():
    with pytest.raises(benchmark.BenchmarkConfigError, match="v1, v2"):
        benchmark.resolve_profile("v3")


def test_the_default_profile_does_not_change():
    """Mesurar no promou. Si aixo falla, algu ha canviat el producte sense dir-ho."""
    assert DEFAULT_PROFILE is V1


# --------------------------------------------------------------------------- #
# Extraction Contract v3: nomes amb --experimental-v3                           #
# --------------------------------------------------------------------------- #


def test_sense_perfil_ni_flag_el_benchmark_mesura_v1():
    args = benchmark.build_parser().parse_args(["--dataset", "gold-v2.1", "--plan-only"])

    assert args.profile is None and args.experimental_v3 is False
    assert benchmark.resolve_profile(args.profile, experimental_v3=args.experimental_v3) is V1


def test_la_v3_nomes_es_mesura_amb_el_flag():
    from app.ai.profiles import PROFILES, V3

    args = benchmark.build_parser().parse_args(
        ["--dataset", "gold-v2.1", "--experimental-v3", "--plan-only"]
    )

    assert benchmark.resolve_profile(args.profile, experimental_v3=args.experimental_v3) is V3
    assert benchmark.resolve_profile("v3", experimental_v3=True) is V3
    assert "v3" not in PROFILES, "el flag no afegeix la v3 als perfils que resolen els jobs"


def test_demanar_v3_per_nom_diu_com_fer_ho():
    with pytest.raises(benchmark.BenchmarkConfigError, match="--experimental-v3"):
        benchmark.resolve_profile("v3")


@pytest.mark.parametrize("other", ["v1", "V2"])
def test_el_flag_v3_no_es_combina_amb_un_altre_perfil(other):
    with pytest.raises(benchmark.BenchmarkConfigError, match="no es pot combinar"):
        benchmark.resolve_profile(other, experimental_v3=True)


async def test_el_flag_v3_no_salta_la_guarda_del_proveidor_simulat(tmp_path, capsys):
    """Triar la v3 no relaxa res: amb un proveidor fals, tampoc no es mesura."""
    runs = tmp_path / "runs"
    args = benchmark.build_parser().parse_args(
        ["--dataset", "gold-v2.1", "--experimental-v3", "--provider", "fake",
         "--database-url", BENCHMARK_DSN, "--output", str(runs)]
    )

    assert await benchmark.run(args) == 2
    assert "no llegeix cap plec" in capsys.readouterr().out
    assert not runs.exists()


async def test_el_flag_v3_sense_paid_run_no_paga(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(benchmark, "resolve_provider", lambda name, **_: _StubProvider())
    runs = tmp_path / "runs"
    args = benchmark.build_parser().parse_args(
        ["--dataset", "gold-v2.1", "--experimental-v3",
         "--database-url", BENCHMARK_DSN, "--output", str(runs)]
    )

    assert await benchmark.run(args) == benchmark.EXIT_PAID_RUN_NOT_CONFIRMED
    assert "TE COST" in capsys.readouterr().out
    assert not runs.exists()
    assert DEFAULT_PROFILE.extractor_version == "tender-requirements-v1"


async def test_the_pipeline_receives_the_requested_profile(db, tender_document, monkeypatch):
    """El perfil ha d'arribar a l'extraccio, no quedar-se al resum."""
    from app.evaluation import runner

    vistos = []

    async def _fake_analyze(session, tender, **kwargs):
        vistos.append(kwargs.get("profile"))
        raise RuntimeError("no cal analitzar res: nomes mirem quin perfil arriba")

    async def _documents(session, tender_id):
        return [SimpleNamespace(pages=[])], []

    monkeypatch.setattr(runner, "analyze_tender_requirements", _fake_analyze)
    monkeypatch.setattr(runner, "collect_analyzable_documents", _documents)
    tender = await db.get(Tender, tender_document.tender_id)
    case = gold(tender_external_id=tender.external_id).cases[0]

    for profile in (V1, V2):
        await run_case(db, case, _StubProvider(), profile=profile)
    await run_case(db, case, _StubProvider())

    assert vistos == [V1, V2, DEFAULT_PROFILE]


@pytest.mark.parametrize("profile", [V1, V2], ids=["v1", "v2"])
async def test_the_summary_records_the_profile_and_its_versions(db, profile):
    summary, _ = await evaluate_dataset(db, gold(), provider=_StubProvider(), profile=profile)

    assert summary.profile == profile.name
    assert summary.extractor_version == profile.extractor_version
    assert summary.schema_version == profile.schema_version
    assert summary.prompt_version == profile.prompt_version
    assert summary.as_dict()["profile"] == profile.name


# --------------------------------------------------------------------------- #
# Aillament: on NO es pot mesurar                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql+asyncpg://u:p@127.0.0.1:5433/licitacions",
        "postgresql+asyncpg://u:p@127.0.0.1:5433/licitacions_test",
        "postgresql+asyncpg://u:p@127.0.0.1:5433/postgres",
        f"{BENCHMARK_DSN}?options=-csearch_path%3Dpublic",
        "postgresql+asyncpg://u:p@/licitacions_benchmark",
        "postgresql://u:p@127.0.0.1:5433/licitacions_benchmark",
        "",
        None,
    ],
    ids=["desenvolupament", "tests", "postgres", "parametres", "sense-host", "sense-asyncpg",
         "buida", "absent"],
)
def test_the_benchmark_refuses_databases_that_are_not_its_own(dsn):
    with pytest.raises(benchmark.BenchmarkConfigError):
        benchmark.resolve_benchmark_database(dsn)


def test_a_dedicated_database_is_accepted():
    url = benchmark.resolve_benchmark_database(BENCHMARK_DSN)
    assert url.database == benchmark.DEFAULT_BENCHMARK_DATABASE


# --------------------------------------------------------------------------- #
# Un proveidor simulat no mesura                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["fake", "fixture"])
def test_a_simulated_provider_is_refused(name):
    with pytest.raises(benchmark.BenchmarkConfigError, match="no llegeix cap plec"):
        benchmark.resolve_provider(name)


async def test_the_cli_refuses_to_measure_with_a_fake_provider(tmp_path, capsys):
    runs = tmp_path / "runs"
    args = benchmark.build_parser().parse_args(
        ["--dataset", "gold-v2.1", "--profile", "v2", "--provider", "fake",
         "--database-url", BENCHMARK_DSN, "--output", str(runs)]
    )

    assert await benchmark.run(args) == 2
    assert "no llegeix cap plec" in capsys.readouterr().out
    assert not runs.exists(), "cap artefacte: no s'ha mesurat res"


async def test_the_cli_stops_when_there_is_no_corpus(tmp_path, engine, capsys, monkeypatch):
    """Sense licitacions a la base no hi ha res a analitzar, i es diu abans de gastar."""

    class _NoDispose:
        """El motor dels tests: el benchmark no l'ha de tancar."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        async def dispose(self):
            return None

    monkeypatch.setattr(benchmark, "resolve_provider", lambda name, **_: _StubProvider())
    monkeypatch.setattr(benchmark, "create_async_engine", lambda url: _NoDispose(engine))
    runs = tmp_path / "runs"
    args = benchmark.build_parser().parse_args(
        ["--dataset", "gold-v2.1", "--profile", "v1", "--paid-run",
         "--database-url", BENCHMARK_DSN, "--output", str(runs)]
    )

    assert await benchmark.run(args) == 4
    assert "no hi ha corpus per mesurar" in capsys.readouterr().out
    assert not runs.exists()


# --------------------------------------------------------------------------- #
# Manifest                                                                      #
# --------------------------------------------------------------------------- #


def manifest(**overrides) -> benchmark.RunManifest:
    data = {
        "benchmark_id": benchmark.new_benchmark_id("gold-v2.1", V2),
        "started_at": "2026-09-14T10:00:00+00:00",
        "git_commit": benchmark.git_commit(),
        "dataset_id": "gold-v2.1",
        "dataset_version": 5,
        "gold_schema_version": "gold-schema-v2",
        "provider": "anthropic",
        "model": "claude-test",
        "profile": V2.name,
        "extractor_version": V2.extractor_version,
        "prompt_version": V2.prompt_version,
        "schema_version": V2.schema_version,
        "rules_version": "company-compliance-v3",
        "database": "licitacions_benchmark",
        "cases_requested": 11,
        "cases_with_corpus": 11,
    }
    return benchmark.RunManifest(**{**data, **overrides})


def test_the_manifest_says_what_was_measured_and_with_what(tmp_path: Path):
    path = benchmark.write_manifest(tmp_path, manifest())
    desat = path.read_text(encoding="utf-8")

    for camp in ("benchmark_id", "git_commit", "dataset_id", "dataset_version", "provider",
                 "model", "profile", "extractor_version", "schema_version", "rules_version",
                 "started_at", "database"):
        assert f'"{camp}"' in desat
    assert "u:p@" not in desat, "el manifest no pot portar credencials"


def test_a_run_never_overwrites_another(tmp_path: Path):
    benchmark.write_manifest(tmp_path, manifest())
    with pytest.raises(benchmark.BenchmarkConfigError, match="no se sobreescriu"):
        benchmark.write_manifest(tmp_path, manifest())


def test_two_runs_of_the_same_profile_get_different_ids():
    assert benchmark.new_benchmark_id("gold-v2.1", V2) != benchmark.new_benchmark_id(
        "gold-v2.1", V2
    )
    assert benchmark.new_benchmark_id("gold-v2.1", V2).startswith("gold-v2.1-v2-")
