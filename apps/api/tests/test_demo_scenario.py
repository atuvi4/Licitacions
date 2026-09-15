"""L'escenari de demo local: les guardes i la licitacio estrella.

No executa l'script ni toca `var/dev.db`. Comprova les dues coses que, si es
trenquen, no es veurien fins que algu obris la demo davant d'una persona: que
l'script es nega a correr fora de l'entorn local, i que la licitacio estrella
segueix sortint candidata amb el perfil del seed i les regles vigents.
"""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.ingestion.placsp.normalizer import normalize
from app.ingestion.placsp.parser import parse_entry, parse_feed
from app.ingestion.placsp.service import PlacspIngestionService
from app.matching.service import MatchingService
from app.models.enums import MatchStatus, TenderStatus
from app.models.identity import Company
from app.models.matching import CompanyTenderMatch
from app.models.tenders import Tender
from app.seed import DEMO_COMPANY, OTHER_COMPANY, seed_into

API_ROOT = Path(__file__).resolve().parents[1]


def _load_demo_scenario():
    spec = importlib.util.spec_from_file_location(
        "demo_scenario_under_test", API_ROOT / "scripts" / "demo_scenario.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_demo_scenario()
TODAY = date(2026, 9, 15)


# --------------------------------------------------------------------------- #
# Guardes                                                                       #
# --------------------------------------------------------------------------- #


@pytest.fixture
def local_env(tmp_path, monkeypatch) -> dict[str, str]:
    """Un entorn que les guardes accepten, apuntant a una dev.db de mentida."""
    dev_db = tmp_path / "var" / "dev.db"
    dev_db.parent.mkdir(parents=True)
    dev_db.write_bytes(b"")
    storage = tmp_path / "var" / "storage"
    monkeypatch.setattr(demo, "DEV_DB", dev_db)
    monkeypatch.setattr(demo, "STORAGE_ROOT", storage)
    return {
        "DATABASE_URL": f"sqlite+aiosqlite:///{dev_db.as_posix()}",
        "ENVIRONMENT": "local",
        "STORAGE_BACKEND": "local",
        "STORAGE_LOCAL_PATH": str(storage),
        "LLM_PROVIDER": "fixture",
        "ANTHROPIC_API_KEY": "",
    }


def test_accepta_lentorn_local(local_env):
    demo.check_environment(local_env)


@pytest.mark.parametrize(
    ("override", "motiu"),
    [
        ({"DATABASE_URL": ""}, "DATABASE_URL explicita"),
        ({"DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5433/licitacions"}, "SQLite"),
        (
            {"DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5433/licitacions_benchmark"},
            "SQLite",
        ),
        ({"DATABASE_URL": "sqlite+aiosqlite:///./var/altra.db"}, "ha d'apuntar"),
        ({"ANTHROPIC_API_KEY": "no-es-una-clau"}, "ANTHROPIC_API_KEY"),
        ({"OPENAI_API_KEY": "no-es-una-clau"}, "OPENAI_API_KEY"),
        ({"LLM_PROVIDER": "anthropic"}, "LLM_PROVIDER=fixture"),
        ({"LLM_PROVIDER": "fake"}, "LLM_PROVIDER=fixture"),
        ({"ENVIRONMENT": "production"}, "ENVIRONMENT=local"),
        ({"STORAGE_BACKEND": "s3"}, "STORAGE_BACKEND=local"),
    ],
)
def test_refusa_tot_el_que_no_es_la_demo_local(local_env, override, motiu):
    with pytest.raises(demo.DemoRefused, match=motiu):
        demo.check_environment({**local_env, **override})


# --------------------------------------------------------------------------- #
# Licitacio estrella                                                            #
# --------------------------------------------------------------------------- #


def _star_entry(today: date = TODAY):
    feed = parse_feed(demo.render_star_feed(today).encode("utf-8"))
    assert len(feed.entries) == 1
    return normalize(parse_entry(feed.entries[0]))


def test_lestrella_es_clarament_demo():
    tender = _star_entry()

    assert tender.external_id == demo.STAR_TENDER
    assert tender.title.startswith("[DEMO]")
    assert tender.expediente_number.startswith("DEMO-")
    assert "(DEMO)" in tender.buyer.name
    # `.invalid` es un domini reservat: l'enllaç no pot portar enlloc real.
    assert tender.source_url.startswith("https://licitacions.demo.invalid/")
    # Cap document a descarregar: l'unic plec es el corpus de desenvolupament.
    assert tender.documents == []


def test_lestrella_te_termini_obert_relatiu_al_dia_que_es_crea():
    tender = _star_entry(TODAY)

    assert tender.status == TenderStatus.OPEN
    assert tender.deadline_at.date() == TODAY + timedelta(days=demo.STAR_DEADLINE_DAYS)
    assert tender.publication_date == TODAY


def test_la_plantilla_no_deixa_cap_marcador_sense_substituir():
    rendered = demo.render_star_feed(TODAY)

    assert "$" not in rendered
    ET.fromstring(rendered.encode("utf-8"))


async def _ingest_star(db, storage, tmp_path, today: date) -> Tender:
    feed = tmp_path / "estrella.atom"
    feed.write_text(demo.render_star_feed(today), encoding="utf-8")
    run = await PlacspIngestionService(db, storage).run(fixture=feed)
    assert run.records_new == 1
    return await db.scalar(sa.select(Tender).where(Tender.external_id == demo.STAR_TENDER))


async def _match(db, company_name: str, tender: Tender) -> CompanyTenderMatch:
    company = await db.scalar(sa.select(Company).where(Company.legal_name == company_name))
    await MatchingService(db).recalculate(company.id)
    return await db.scalar(
        sa.select(CompanyTenderMatch).where(
            CompanyTenderMatch.company_id == company.id,
            CompanyTenderMatch.tender_id == tender.id,
        )
    )


async def test_lestrella_surt_candidata_amb_el_perfil_del_seed(db, storage, tmp_path):
    await seed_into(db)
    tender = await _ingest_star(db, storage, tmp_path, date.today())

    match = await _match(db, DEMO_COMPANY, tender)

    assert match.status == MatchStatus.CANDIDATE
    assert match.score >= 60


async def test_per_a_laltra_empresa_no_es_candidata(db, storage, tmp_path):
    """El contrast entre tenants tambe forma part de la demo."""
    await seed_into(db)
    tender = await _ingest_star(db, storage, tmp_path, date.today())

    match = await _match(db, OTHER_COMPANY, tender)

    assert match is None or match.status != MatchStatus.CANDIDATE
