"""Importador guardat de la revisio de Palamos: guardes i pla en DRY-RUN. Zero crides.

Les dades son SINTETIQUES (la licitacio amb forma de Palamos de
`test_review_actions`); l'annex de 44 files es genera aqui amb els estats reals
(38/5/1) i les contraparts que el pla ha de resoldre.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.models.requirements import Requirement
from app.models.review import ReviewItem, ReviewItemRelation, ReviewItemRevision, ReviewItemSource
from tests import test_review_actions as actions_tests
from tests.test_requirement_analysis import PAGE_27

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_palamos_review.py"
_spec = importlib.util.spec_from_file_location("import_palamos_review", _SCRIPT)
importer = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("import_palamos_review", importer)
_spec.loader.exec_module(importer)

analyzable = actions_tests.analyzable
_clean_provider = actions_tests._clean_provider
palamos_like = actions_tests.palamos_like
ORDINARY, INSURANCE, LATENCY = (
    actions_tests.ORDINARY, actions_tests.INSURANCE, actions_tests.LATENCY)
QUOTE = "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros"
PARTIALS = ("M06", "M13", "M18", "M39", "M40")


def _annex_rows(quote=QUOTE, page="27"):
    rows = []
    for n in range(1, 45):
        manual_id = f"M{n:02d}"
        status, related = "MANUAL_GAP", ""
        if manual_id in PARTIALS:
            status, related = "MANUAL_PARTIAL", INSURANCE
        elif manual_id == "M09":
            status, related = "MANUAL_ALREADY_REPRESENTED", ORDINARY
        elif manual_id == "M21":
            related = LATENCY
        rows.append({
            "manual_id": manual_id, "page": page, "section": f"8.{n}", "title": f"T{n}",
            "requirement": "r", "numeric_value_unit": "", "status": status,
            "related_automated_requirement": related, "evidence_quote": quote,
            "review_note": "", "mandatory": "yes", "material": "no",
        })
    return rows


def _write(path: Path, rows) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


async def _set_migration(session, version):
    await session.execute(sa.text(
        "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
    await session.execute(sa.text("DELETE FROM alembic_version"))
    await session.execute(sa.text("INSERT INTO alembic_version VALUES (:v)"), {"v": version})
    await session.commit()


async def _run_id(session, tender_id):
    return await session.scalar(sa.select(Requirement.run_id).where(
        Requirement.tender_id == tender_id).limit(1))


# --------------------------------------------------------------------------- #
# Guardes                                                                       #
# --------------------------------------------------------------------------- #

GOOD = "postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_review_palamos"


@pytest.mark.parametrize(("url", "reason"), [
    (None, "DATABASE_URL"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions", "protegida"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5432/licitacions_review_palamos", "5432"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_review_s3", "protegida"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_v3_probe_palamos", "protegida"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5433/una_altra", "Nomes la base"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5434/licitacions_review_palamos", "Nomes el port"),
])
def test_nomes_la_base_dedicada_al_5433(url, reason):
    with pytest.raises(importer.ImportGuardError, match=reason):
        importer.validate_target_url(url)


def test_la_base_dedicada_passa():
    assert importer.validate_target_url(GOOD).database == "licitacions_review_palamos"


def test_les_constants_son_les_de_palamos():
    assert str(importer.TENDER_ID) == "f37d7031-6d32-404d-8785-b2da7cf6a78e"
    assert str(importer.RUN_ID) == "2941fbec-cefc-4011-8026-79110d9d1938"
    assert importer.EXPECTED_MIGRATION == "0040_review_layer"


async def test_estat_correcte_passa(session, palamos_like):
    tid = palamos_like["tender_id"]
    await _set_migration(session, "0040_review_layer")

    await importer.check_state(session, tender_id=tid, run_id=await _run_id(session, tid))


@pytest.mark.parametrize(("case", "reason"), [
    ("migration", "Esquema"), ("tender", "licitacio"), ("run", "run final no hi es"),
])
async def test_estat_incorrecte_es_refusa(session, palamos_like, case, reason):
    tid = palamos_like["tender_id"]
    run_id = await _run_id(session, tid)
    await _set_migration(session, "0039_award_adjudication_basis" if case == "migration"
                         else "0040_review_layer")
    kwargs = {"tender_id": tid, "run_id": run_id}
    if case == "tender":
        kwargs["tender_id"] = uuid.uuid4()
    if case == "run":
        kwargs["run_id"] = uuid.uuid4()

    with pytest.raises(importer.ImportGuardError, match=reason):
        await importer.check_state(session, **kwargs)


# --------------------------------------------------------------------------- #
# Annex                                                                         #
# --------------------------------------------------------------------------- #


def test_l_annex_valid_passa(tmp_path):
    rows = importer.load_annex(_write(tmp_path / "a.csv", _annex_rows()))
    assert len(rows) == 44


@pytest.mark.parametrize("mutate", ["less", "status", "optional", "duplicate"])
def test_un_annex_que_no_quadra_s_atura(tmp_path, mutate):
    rows = _annex_rows()
    if mutate == "less":
        rows = rows[:-1]
    elif mutate == "status":
        rows[0]["status"] = "MANUAL_PARTIAL"
    elif mutate == "optional":
        rows[3]["mandatory"] = "no"
    else:
        rows[1]["manual_id"] = rows[0]["manual_id"]

    with pytest.raises(importer.ImportGuardError):
        importer.load_annex(_write(tmp_path / "a.csv", rows))


@pytest.mark.parametrize(("value", "pages"), [("22", [22]), ("22-23", [22, 23]),
                                              ("24-25", [24, 25])])
def test_pagines_de_l_annex(value, pages):
    assert importer.pages_of(value) == pages


@pytest.mark.parametrize("value", ["", "p22", "23-22", "22-"])
def test_una_pagina_no_valida_s_atura(value):
    with pytest.raises(importer.ImportGuardError):
        importer.pages_of(value)


# --------------------------------------------------------------------------- #
# Ancoratge                                                                     #
# --------------------------------------------------------------------------- #

PAGE = "7. L’Ajuntament podrà sol·licitar\nl’activació del servei de dades.\nFi."


def test_cita_exacta():
    anchor = importer.anchor_quote(PAGE, "sol·licitar")
    assert (anchor.exact, PAGE[anchor.start:anchor.end]) == (True, "sol·licitar")


def test_cita_amb_salt_de_linia_i_apostrof_tipografic():
    anchor = importer.anchor_quote(PAGE, "sol·licitar l'activació del servei")

    assert anchor.resolved and not anchor.exact
    # Les posicions son de l'ORIGINAL: el tros es el text real de la pagina.
    assert PAGE[anchor.start:anchor.end] == "sol·licitar\nl’activació del servei"


@pytest.mark.parametrize(("quote", "diagnostic"), [
    ("una frase que no hi es", "no es a la pagina"),
    ("", "cita buida"),
])
def test_cita_no_trobada_queda_sense_resoldre(quote, diagnostic):
    anchor = importer.anchor_quote(PAGE, quote)
    assert (anchor.resolved, anchor.start, anchor.end) == (False, None, None)
    assert anchor.diagnostic == diagnostic


def test_cita_ambigua_queda_sense_resoldre():
    anchor = importer.anchor_quote("abc. abc.", "abc")
    assert not anchor.resolved and anchor.diagnostic.startswith("ambigua")


# --------------------------------------------------------------------------- #
# Pla                                                                           #
# --------------------------------------------------------------------------- #


async def _plan(session, palamos_like, rows):
    tid = palamos_like["tender_id"]
    return await importer.build_plan(session, rows, tender_id=tid,
                                     run_id=await _run_id(session, tid),
                                     document_name="PCAP.pdf")


async def _review_counts(session):
    return [await session.scalar(sa.select(sa.func.count()).select_from(model))
            for model in (ReviewItem, ReviewItemSource, ReviewItemRevision, ReviewItemRelation,
                          Requirement)]


async def test_pla_38_5_1_amb_43_creacions_m09_sense_crear_i_m21_related_to(
    session, palamos_like, tmp_path
):
    rows = importer.load_annex(_write(tmp_path / "a.csv", _annex_rows()))
    before = await _review_counts(session)

    plan = await _plan(session, palamos_like, rows)
    summary = importer.summarise(plan, automated_requirements=3)

    assert await _review_counts(session) == before, "el dry-run no escriu res"
    assert summary["status"] == {"MANUAL_GAP": 38, "MANUAL_PARTIAL": 5,
                                 "MANUAL_ALREADY_REPRESENTED": 1}
    assert (summary["manual_creates"], summary["confirms"]) == (43, 1)
    assert summary["relations"] == {"partial_match_of": 5, "related_to": 1}
    assert summary["citations_resolved"] == 44 and summary["citations_exact"] == 44
    assert (summary["mappings_required"], summary["mappings_resolved"]) == (7, 7)
    assert summary["expected_canonical_requirements"] == 3 + 43
    by_id = {p.manual_id: p for p in plan}
    assert by_id["M09"].planned_action.startswith("no_create")
    assert by_id["M21"].relation_type == "related_to"
    assert by_id["M21"].related_requirement_title == LATENCY
    assert all(by_id[m].relation_type == "partial_match_of" for m in PARTIALS)
    assert importer.ready(summary)
    start = PAGE_27.index(QUOTE)
    assert (by_id["M01"].char_start, by_id["M01"].char_end) == (start, start + len(QUOTE))


async def test_una_cita_que_no_hi_es_bloqueja_la_importacio(session, palamos_like):
    rows = _annex_rows()
    rows[4]["evidence_quote"] = "Aquesta frase no surt enlloc"

    summary = importer.summarise(await _plan(session, palamos_like, rows), 3)

    assert summary["citations_unresolved"] == ["M05"]
    assert not importer.ready(summary)


async def test_una_pagina_que_no_existeix_no_s_endevina(session, palamos_like):
    rows = _annex_rows()
    rows[0]["page"] = "99"

    [first, *_] = await _plan(session, palamos_like, rows)

    assert (first.citation_resolved, first.page) == (False, None)
    assert "pagina no trobada" in first.diagnostic


async def test_una_contrapart_ambigua_falla_tancada(session, palamos_like):
    original = palamos_like["rows"][ORDINARY]
    session.add(Requirement(
        run_id=original.run_id, tender_id=original.tender_id, category=original.category,
        title=ORDINARY, dedup_key="duplicat-" + uuid.uuid4().hex[:8]))
    await session.commit()

    summary = importer.summarise(await _plan(session, palamos_like, _annex_rows()), 3)

    assert summary["mappings_unresolved"] == ["M09"]
    assert not importer.ready(summary)


async def test_una_contrapart_que_no_existeix_falla_tancada(session, palamos_like):
    rows = _annex_rows()
    rows[5]["related_automated_requirement"] = "Un titol que no es de cap requisit"

    plan = await _plan(session, palamos_like, rows)

    assert {p.manual_id: p.mapping_status for p in plan}["M06"] == "unresolved_none"


async def test_el_mode_d_escriptura_no_esta_habilitat(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", GOOD)
    args = importer.build_parser().parse_args(
        ["--annex", str(tmp_path / "a.csv"), "--out-dir", str(tmp_path), "--write"])

    assert await importer.run(args) == 2


async def test_la_cli_refusa_una_base_protegida_sense_connectar(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_review_s3")
    args = importer.build_parser().parse_args(
        ["--annex", str(_write(tmp_path / "a.csv", _annex_rows())), "--out-dir", str(tmp_path)])

    assert await importer.run(args) == 2
    assert "protegida" in capsys.readouterr().out
    assert not (tmp_path / "palamos_review_import_plan.csv").exists()
