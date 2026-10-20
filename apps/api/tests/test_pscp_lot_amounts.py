"""F-06e: el PBL sense IVA i el VEC de lot de la PSCP, i el rejoc sense xarxa (`--replay`).

* Guardes (pures): les drafts noves de cada fila de lot, l'ordre de l'estat (bloqueig >
  `no_value` > guardes noves > `accepted`), `tax_order`, `vec_order`, `lot_net_sum`,
  `lot_vec_sum` (regla estricta, D2), el VEC independent del PBL (D3) i que les drafts de
  sempre no canvien gens.
* Servei: les observacions de sempre surten `unchanged` quan arriben les noves, i una
  segona passada les dona totes `unchanged`.
* Rejoc: la captura d'un run (amb el client fals de sempre) es torna a jugar sense
  xarxa, amb el mateix resultat; un SHA-256 alterat, un fitxer absent o una UUID no
  capturada fan fallar el run sense escriure res.

Cap test no surt a la xarxa.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.ingestion.pscp_open_data import __main__ as cli
from app.ingestion.pscp_open_data import guards as g
from app.ingestion.pscp_open_data import parser as p
from app.ingestion.pscp_open_data.__main__ import amain
from app.ingestion.pscp_open_data.replay import ReplayClient, ReplayError
from app.ingestion.pscp_open_data.service import EXIT_FAILED, EXIT_OK
from app.models.enums import AmountObservationStatus as S
from app.models.enums import TenderAmountKind as K
from app.models.sources import Source
from app.models.tenders import Tender
from tests.test_pscp_open_data import (
    ALL,
    CASES,
    FakeSoda,
    _observations,
    _rows,
    _run,
    _seed,
    _snapshot,
    _source_rows,
    _utc,
)

NET = K.PBL_WITHOUT_TAX
VEC = K.ESTIMATED_VALUE
PBL = K.PBL_WITH_TAX


def _new(ev: g.TenderEvaluation, kind: K) -> list[g.Draft]:
    return [d for d in ev.lot_amount_drafts if d.amount_kind == kind]


def _lot_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if len(row["id_intern"]) > 36]


def _statuses(ev: g.TenderEvaluation, kind: K) -> list[S]:
    return [d.status for d in sorted(_new(ev, kind), key=lambda d: d.source_row_id)]


# --------------------------------------------------------------------------- #
# Guardes (pures)                                                               #
# --------------------------------------------------------------------------- #


def test_cada_fila_de_lot_dona_el_pbl_sense_iva_i_el_vec_amb_la_mateixa_clau():
    snapshot = _snapshot("lots")
    ev = g.evaluate(snapshot, _rows("lots"))
    pbl_lots = [d for d in ev.drafts if d.level == "lot"]
    assert len(ev.lot_amount_drafts) == 2 * len(pbl_lots) == 6
    by_row = {d.source_row_id: d for d in pbl_lots}
    for draft in ev.lot_amount_drafts:
        same = by_row[draft.source_row_id]
        assert draft.level == "lot"
        assert (draft.tender_id, draft.tender_lot_id, draft.source_entity_key) == (
            same.tender_id,
            same.tender_lot_id,
            same.source_entity_key,
        )
        assert draft.source_phase == same.source_phase
        assert draft.rows == same.rows
        assert draft.status == S.ACCEPTED
        validation = draft.validation
        assert validation["amount_kind"] == draft.amount_kind.value
        assert validation["level"] == "lot" and validation["v"] == g.VALIDATION_VERSION
        assert validation["expedient_failed"] == [] and validation["lot_failed"] == []
        assert validation["rows"] == [draft.source_row_id]
        assert validation["no_value_reason"] is None and validation["amount_problem"] is None
    for draft in _new(ev, NET):
        row = draft.rows[0]
        assert draft.field == p.LOT_WITHOUT_TAX
        assert draft.amount == Decimal(row["pressupost_licitacio_sense"])
        assert draft.published_text == row["pressupost_licitacio_sense"]
        assert set(draft.validation["lot_guards"]) == {
            "match",
            "amount",
            "net",
            "tax_order",
            "lot_net_sum",
        }
        assert draft.validation["lot_guards"]["lot_net_sum"]["ok"] is True
        assert draft.cross_check == {
            p.LOT_WITH_TAX: row["pressupost_licitacio_amb"],
            p.LOT_ESTIMATED: row["valor_estimat_contracte"],
        }
    for draft in _new(ev, VEC):
        row = draft.rows[0]
        assert draft.field == p.LOT_ESTIMATED
        assert draft.amount == Decimal(row["valor_estimat_contracte"])
        assert set(draft.validation["lot_guards"]) == {
            "match",
            "amount",
            "vec",
            "vec_order",
            "lot_vec_sum",
        }


def test_les_drafts_de_sempre_no_porten_res_de_nou():
    for name in ALL:
        ev = g.evaluate(_snapshot(name), _rows(name))
        assert all(d.amount_kind == PBL for d in ev.drafts)
        assert all("amount_kind" not in d.validation for d in ev.drafts)
        # Res de nou a nivell d'expedient.
        assert all(d.level == "lot" for d in ev.lot_amount_drafts)
        for draft in ev.drafts:
            if draft.level == "expedient" and "guards" in draft.validation:
                assert "lot_net_sum" not in draft.validation["guards"]
                assert "lot_vec_sum" not in draft.validation["guards"]


def test_expedient_sense_lots_no_dona_cap_import_de_lot():
    for name in ("lescala", "vat0", "zero", "null", "stale"):
        assert g.evaluate(_snapshot(name), _rows(name)).lot_amount_drafts == []


def test_pbl_net_no_publicat_es_no_value_i_el_vec_es_independent():
    """D3: el VEC del lot s'accepta encara que el lot no tingui PBL."""
    rows = _rows("lots")
    lot = _lot_rows(rows)[1]
    lot["pressupost_licitacio_sense"] = None
    ev = g.evaluate(_snapshot("lots"), rows)
    [net] = [d for d in _new(ev, NET) if d.source_row_id == lot["id_intern"]]
    assert net.status == S.NO_VALUE and net.amount is None
    assert net.validation["no_value_reason"] == "null_published"
    # La suma no es pot comprovar: no s'aplica, i no fa caure els altres lots.
    assert net.validation["lot_guards"]["lot_net_sum"] == {
        "ok": None,
        "skipped": "lot_amount_not_published",
    }
    assert sorted(_statuses(ev, NET)) == sorted([S.ACCEPTED, S.ACCEPTED, S.NO_VALUE])
    [vec] = [d for d in _new(ev, VEC) if d.source_row_id == lot["id_intern"]]
    assert vec.status == S.ACCEPTED and vec.amount == Decimal(lot["valor_estimat_contracte"])
    assert vec.validation["lot_guards"]["vec_order"] == {"ok": None}


def test_publicat_a_zero_es_no_value_amb_el_zero_tal_com_es_publica():
    rows = _rows("lots")
    lot = _lot_rows(rows)[0]
    lot["valor_estimat_contracte"] = "0"
    ev = g.evaluate(_snapshot("lots"), rows)
    [vec] = [d for d in _new(ev, VEC) if d.source_row_id == lot["id_intern"]]
    assert vec.status == S.NO_VALUE and vec.amount == Decimal("0")
    assert vec.published_text == "0"
    assert vec.validation["no_value_reason"] == "zero_published"
    assert vec.validation["lot_guards"]["lot_vec_sum"]["skipped"] == "lot_amount_zero"
    assert sorted(_statuses(ev, VEC)) == sorted([S.ACCEPTED, S.ACCEPTED, S.NO_VALUE])
    assert set(_statuses(ev, NET)) == {S.ACCEPTED}


def test_un_valor_no_representable_es_conflict_amb_import_nul_i_el_text():
    rows = _rows("lots")
    lot = _lot_rows(rows)[0]
    lot["pressupost_licitacio_sense"] = "36190.001"
    ev = g.evaluate(_snapshot("lots"), rows)
    [net] = [d for d in _new(ev, NET) if d.source_row_id == lot["id_intern"]]
    assert net.status == S.CONFLICT and net.amount is None
    assert net.published_text == "36190.001"
    assert net.validation["amount_problem"] == "more_than_2_decimals"
    assert "amount" in net.validation["lot_failed"]


def test_tax_order_i_vec_order_al_lot():
    """PBL amb IVA >= PBL sense IVA (igual: IVA 0 %); VEC >= PBL sense IVA - 0,01 (R1)."""
    rows = _rows("lots")
    first, second, third = _lot_rows(rows)
    # La suma es conserva (59510): nomes fallen les guardes del lot.
    second["pressupost_licitacio_sense"] = "4000"  # > 3993 amb IVA
    first["pressupost_licitacio_sense"] = "35490"
    ev = g.evaluate(_snapshot("lots"), rows)
    net = {d.source_row_id: d for d in _new(ev, NET)}
    vec = {d.source_row_id: d for d in _new(ev, VEC)}
    assert net[second["id_intern"]].status == S.CONFLICT
    assert net[second["id_intern"]].validation["lot_failed"] == ["tax_order"]
    assert net[first["id_intern"]].status == S.ACCEPTED
    assert net[third["id_intern"]].status == S.ACCEPTED
    # VEC 3300 < PBL net 4000: R1 al lot.
    assert vec[second["id_intern"]].status == S.CONFLICT
    assert vec[second["id_intern"]].validation["lot_failed"] == ["vec_order"]
    assert vec[first["id_intern"]].status == S.ACCEPTED
    # La draft del PBL amb IVA ja ho veia (guarda de sempre) i no canvia de forma.
    [pbl] = [d for d in ev.drafts if d.source_row_id == second["id_intern"]]
    assert pbl.validation["lot_guards"]["tax_order"] == {"ok": False}


def test_vec_un_centim_per_sota_del_pbl_net_es_tolera():
    rows = _rows("lots")
    first, _, third = _lot_rows(rows)
    third["valor_estimat_contracte"] = "20019.99"  # PBL net 20020
    first["valor_estimat_contracte"] = "36190.01"  # la suma no canvia
    ev = g.evaluate(_snapshot("lots"), rows)
    assert set(_statuses(ev, VEC)) == {S.ACCEPTED}
    third["valor_estimat_contracte"] = "20019.98"
    first["valor_estimat_contracte"] = "36190.02"
    ev = g.evaluate(_snapshot("lots"), rows)
    vec = {d.source_row_id: d for d in _new(ev, VEC)}
    assert vec[third["id_intern"]].validation["lot_failed"] == ["vec_order"]


def test_iva_del_zero_per_cent_al_lot_es_legitim_i_sense_pbl_amb_iva_no_s_aplica():
    rows = _rows("lots")
    first, second, _ = _lot_rows(rows)
    second["pressupost_licitacio_amb"] = second["pressupost_licitacio_sense"]
    ev = g.evaluate(_snapshot("lots"), rows)
    net = {d.source_row_id: d for d in _new(ev, NET)}
    assert net[second["id_intern"]].validation["lot_guards"]["tax_order"] == {"ok": True}
    # Un lot sense PBL amb IVA publicat: la guarda no s'aplica, el net s'accepta.
    rows = _rows("lots")
    first = _lot_rows(rows)[0]
    first["pressupost_licitacio_amb"] = None
    ev = g.evaluate(_snapshot("lots"), rows)
    net = {d.source_row_id: d for d in _new(ev, NET)}
    assert net[first["id_intern"]].validation["lot_guards"]["tax_order"] == {"ok": None}
    assert net[first["id_intern"]].status == S.ACCEPTED


def test_la_suma_dels_pbl_nets_que_no_quadra_posa_tots_els_lots_en_conflict():
    """D1: no sabem quin lot esta malament; el VEC no en pateix."""
    rows = _rows("lots")
    _lot_rows(rows)[0]["pressupost_licitacio_sense"] = "36189"  # -1 EUR, tolerancia 0,03
    ev = g.evaluate(_snapshot("lots"), rows)
    assert set(_statuses(ev, NET)) == {S.CONFLICT}
    for draft in _new(ev, NET):
        assert draft.validation["lot_failed"] == ["lot_net_sum"]
        guard = draft.validation["lot_guards"]["lot_net_sum"]
        assert guard == {
            "ok": False,
            "sum": "59509.00",
            "expedient": "59510.00",
            "difference": "-1.00",
            "tolerance": "0.03",
        }
    assert set(_statuses(ev, VEC)) == {S.ACCEPTED}
    # L'expedient i el PBL amb IVA no canvien.
    assert {d.status for d in ev.drafts} == {S.ACCEPTED}


def test_la_suma_dels_vec_que_no_quadra_posa_tots_els_lots_en_conflict():
    rows = _rows("lots")
    _lot_rows(rows)[2]["valor_estimat_contracte"] = "20021"
    ev = g.evaluate(_snapshot("lots"), rows)
    assert set(_statuses(ev, VEC)) == {S.CONFLICT}
    assert {tuple(d.validation["lot_failed"]) for d in _new(ev, VEC)} == {("lot_vec_sum",)}
    assert set(_statuses(ev, NET)) == {S.ACCEPTED}


def test_regla_estricta_sense_arrodoniment_especial_d2():
    """9f4f79c2: expedient publicat sense decimals (201598) i 7 lots que sumen 201597,91."""
    lots = [p.parse_amount(value) for value in ["28799.70"] * 6 + ["28799.71"]]
    total = sum(a.value for a in lots)
    assert total == Decimal("201597.91")
    guard = g._lot_total(p.parse_amount("201598"), lots)
    assert guard["ok"] is False
    assert guard["difference"] == "-0.09" and guard["tolerance"] == "0.07"
    # Dins de la tolerancia d'un centim per lot, s'accepta.
    assert g._lot_total(p.parse_amount("201597.98"), lots)["ok"] is True


@pytest.mark.parametrize(
    ("expedient", "lot_values", "skipped"),
    [
        ("0", ["1", "2"], "expedient_amount_not_positive"),
        (None, ["1", "2"], "expedient_amount_not_positive"),
        ("3", ["1", None], "lot_amount_not_published"),
        ("3", ["1", "0"], "lot_amount_zero"),
        ("3", ["1", "1.001"], "lot_amount_unrepresentable"),
    ],
)
def test_la_suma_nomes_s_avalua_amb_tots_els_valors_positius(expedient, lot_values, skipped):
    guard = g._lot_total(p.parse_amount(expedient), [p.parse_amount(v) for v in lot_values])
    assert guard == {"ok": None, "skipped": skipped}


def test_una_guarda_d_expedient_que_falla_bloqueja_abans_que_no_value():
    """Ordre: bloqueig (expedient) > `no_value`: un lot sense valor tambe queda en conflict."""
    rows = _rows("lots")
    _lot_rows(rows)[0]["pressupost_licitacio_sense"] = None
    ev = g.evaluate(_snapshot("lots", organ_id="1"), rows)
    assert ev.failed == ["organ"]
    assert set(_statuses(ev, NET)) == {S.CONFLICT}
    assert set(_statuses(ev, VEC)) == {S.CONFLICT}
    for draft in ev.lot_amount_drafts:
        assert draft.validation["expedient_failed"] == ["organ"]
    [blank] = [d for d in _new(ev, NET) if d.amount is None]
    assert blank.validation["no_value_reason"] == "null_published"


def test_sense_lot_nostre_es_conflict_sense_lot():
    ev = g.evaluate(_snapshot("lot_without_db_lot"), _rows("lot_without_db_lot"))
    assert len(ev.lot_amount_drafts) == 4
    for draft in ev.lot_amount_drafts:
        assert draft.status == S.CONFLICT and draft.tender_lot_id is None
        assert "match" in draft.validation["lot_failed"]


def test_contrast_amb_tender_lots_nomes_si_en_tenim_el_valor():
    base = _snapshot("lots")
    rows = _rows("lots")
    ev = g.evaluate(base, rows)
    for draft in _new(ev, NET):
        assert draft.validation["lot_guards"]["net"]["problem"] == "ours_missing"
        assert draft.validation["lot_guards"]["net"]["ok"] is None
    first = _lot_rows(rows)[0]
    lots = list(base.lots)
    lots[0] = g.LotSnapshot(lots[0].id, lots[0].lot_number, Decimal("36190.00"), Decimal("1"))
    ev = g.evaluate(_snapshot("lots", lots=tuple(lots)), rows)
    net = {d.source_row_id: d for d in _new(ev, NET)}
    vec = {d.source_row_id: d for d in _new(ev, VEC)}
    assert net[first["id_intern"]].validation["lot_guards"]["net"]["ok"] is True
    assert net[first["id_intern"]].status == S.ACCEPTED
    assert vec[first["id_intern"]].validation["lot_failed"] == ["vec"]
    assert vec[first["id_intern"]].status == S.CONFLICT


def test_la_validation_nova_es_determinista_i_json_pla():
    first = g.evaluate(_snapshot("superseded"), _rows("superseded"))
    second = g.evaluate(_snapshot("superseded"), list(reversed(_rows("superseded"))))
    assert [d.validation for d in first.lot_amount_drafts] == [
        d.validation for d in second.lot_amount_drafts
    ]
    json.dumps([d.validation for d in first.lot_amount_drafts])


# --------------------------------------------------------------------------- #
# Servei: les observacions de sempre no es mouen                                #
# --------------------------------------------------------------------------- #


def _without_lot_amounts(monkeypatch):
    """El codi d'abans de F-06e: les mateixes drafts, sense les noves."""
    real = g.evaluate

    def evaluate(snapshot, rows):
        ev = real(snapshot, rows)
        ev.lot_amount_drafts = []
        return ev

    monkeypatch.setattr(g, "evaluate", evaluate)
    return real


async def test_les_observacions_de_sempre_surten_unchanged_i_la_segona_passada_tot(
    db, session_factory, tmp_path, monkeypatch
):
    await _seed(db)
    source = _source_rows(ALL)
    real = _without_lot_amounts(monkeypatch)
    code, before = await _run(session_factory, FakeSoda(source), tmp_path / "abans")
    assert code == EXIT_OK, before.get("error")
    existing = {(r.id, r.valid_to) for r in await _observations(db)}
    assert {r.amount_kind for r in await _observations(db)} == {PBL}
    n_existing = len(existing)

    monkeypatch.setattr(g, "evaluate", real)
    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "f06e")
    assert code == EXIT_OK, report.get("error")
    pbl = report["writes"]["by_kind"]["pbl_with_tax"]
    assert pbl["inserted"] == 0 and pbl["closed"] == 0 and pbl["unchanged"] == n_existing
    new = sum(report["writes"]["by_kind"][kind.value]["inserted"] for kind in (NET, VEC))
    assert new > 0
    for kind in (NET, VEC):
        assert report["writes"]["by_kind"][kind.value]["closed"] == 0
    assert report["writes"]["closed"] == 0 and report["writes"]["inserted"] == new
    rows = await _observations(db)
    # Cap de les existents s'ha tancat ni reassignat: mateixos ids, totes vigents.
    assert {(r.id, r.valid_to) for r in rows if r.amount_kind == PBL} == existing
    assert len(rows) == n_existing + new

    code, again = await _run(session_factory, FakeSoda(source), tmp_path / "segona")
    assert code == EXIT_OK
    writes = again["writes"]
    assert writes["inserted"] == 0 and writes["closed"] == 0
    assert writes["unchanged"] == n_existing + new
    for kind in (PBL, NET, VEC):
        assert writes["by_kind"][kind.value]["inserted"] == 0
        assert writes["by_kind"][kind.value]["closed"] == 0
    assert len(await _observations(db)) == n_existing + new


async def test_les_noves_porten_la_mateixa_procedencia_que_el_pbl_del_lot(
    db, session_factory, tmp_path
):
    ids = await _seed(db, ["lots", "superseded"])
    code, report = await _run(
        session_factory, FakeSoda(_source_rows(["lots", "superseded"])), tmp_path
    )
    assert code == EXIT_OK, report.get("error")
    rows = await _observations(db, current_only=True)
    pbl = {r.source_row_id: r for r in rows if r.amount_kind == PBL and r.tender_lot_id is not None}
    news = [r for r in rows if r.amount_kind in (NET, VEC)]
    assert len(news) == 2 * len(pbl) == 12
    for row in news:
        same = pbl[row.source_row_id]
        for column in (
            "tender_id",
            "tender_lot_id",
            "source_entity_key",
            "source_phase",
            "source_dataset_updated_at",
            "retrieved_at",
            "evidence_url",
            "evidence_sha256",
            "raw_storage_key",
            "source_code",
        ):
            assert getattr(row, column) == getattr(same, column), column
        assert row.provenance["rows"] == same.provenance["rows"]
        assert row.provenance["licence"] == same.provenance["licence"]
        published = row.provenance["rows"][0]
        assert published["id_intern"] == row.source_row_id
        field = p.LOT_WITHOUT_TAX if row.amount_kind == NET else p.LOT_ESTIMATED
        assert row.provenance["field"] == field
        assert row.validation_status == S.ACCEPTED and row.amount > 0
        assert "replay" not in row.provenance
    assert {r.tender_id for r in news} == {ids["lots"], ids["superseded"]}
    assert report["evaluation"]["observations"]["lot.pbl_without_tax:accepted"] == 6
    assert report["evaluation"]["observations"]["lot.estimated_value:accepted"] == 6
    assert report["evaluation"]["observations"]["lot:accepted"] == 6


async def test_un_valor_de_lot_nou_que_canvia_nomes_tanca_el_seu_tipus(
    db, session_factory, tmp_path
):
    ids = await _seed(db, ["lots"])
    source = _source_rows(["lots"])
    await _run(session_factory, FakeSoda(source), tmp_path / "1")
    uid = CASES["lots"]["ours"]["pscp_id_intern"]
    lot = _lot_rows(source[uid])[2]
    lot["valor_estimat_contracte"] = "20021"  # la suma del VEC ja no quadra
    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_OK, report.get("error")
    by_kind = report["writes"]["by_kind"]
    assert by_kind["estimated_value"]["closed_changed"] == 3
    assert by_kind["estimated_value"]["inserted_by_status"] == {"conflict": 3}
    assert by_kind["pbl_without_tax"]["closed"] == 0
    # El PBL amb IVA d'aquell lot ja desava el VEC publicat com a contrast (guarda `vec`
    # de sempre): es reescriu nomes aquell, com abans de F-06e.
    assert by_kind["pbl_with_tax"]["closed_changed"] == 1
    assert by_kind["pbl_with_tax"]["closed"] == 1
    current = await _observations(db, current_only=True)
    assert {r.validation_status for r in current if r.amount_kind == VEC} == {S.CONFLICT}
    assert {
        r.validation_status for r in current if r.amount_kind == NET and r.tender_id == ids["lots"]
    } == {S.ACCEPTED}


# --------------------------------------------------------------------------- #
# Rejoc (`--replay`)                                                            #
# --------------------------------------------------------------------------- #


async def _capture(db, session_factory, tmp_path, names=ALL) -> Path:
    """Un run de debo amb el client fals (dry-run): el directori d'informe es la captura."""
    await _seed(db, names)
    capture = tmp_path / "captura"
    code, report = await _run(session_factory, FakeSoda(_source_rows(names)), capture, dry_run=True)
    assert code == EXIT_OK, report.get("error")
    assert await _observations(db) == []
    return capture


def _report(out: Path) -> dict:
    return json.loads((out / "report.json").read_text(encoding="utf-8"))


def _comparable(report: dict) -> dict:
    """El que ha de ser identic entre el run original i el rejoc."""
    writes = {k: v for k, v in report["writes"].items() if k not in ("dry_run", "committed")}
    return {
        "evaluation": report["evaluation"],
        "writes": writes,
        "universe": report["universe"],
        "unexpected_rows": report["unexpected_rows"],
        "attribution": report["attribution"],
        "rows_updated_at": report["dataset_metadata"]["start"]["rows_updated_at"],
    }


@pytest.fixture
def no_network(monkeypatch):
    """El client real no s'ha d'instanciar mai en rejoc."""

    def refuse(*args, **kwargs):
        raise AssertionError("SodaClient instanciat en mode --replay")

    monkeypatch.setattr(cli, "SodaClient", refuse)


async def test_el_rejoc_dona_el_mateix_resultat_que_el_client_fals(
    db, session_factory, tmp_path, no_network
):
    capture = await _capture(db, session_factory, tmp_path)
    original = _report(capture)
    out = tmp_path / "rejoc"
    code = await amain(
        ["--replay", str(capture), "--out", str(out)], session_factory=session_factory
    )
    assert code == EXIT_OK
    replayed = _report(out)
    assert _comparable(replayed) == _comparable(original)
    assert replayed["replay"] is True and original["replay"] is False
    manifest_sha = hashlib.sha256((capture / "raw_manifest.json").read_bytes()).hexdigest()
    assert replayed["replay_source"]["raw_manifest_sha256"] == manifest_sha
    assert replayed["replay_source"]["dir"] == str(capture.resolve())
    assert replayed["requests"] == 0 and replayed["network_requests"] == 0
    assert replayed["replayed_requests"] == original["requests"]
    assert replayed["dry_run"] is True and replayed["writes"]["committed"] is False
    assert await _observations(db) == []
    # No es torna a desar cap resposta: el manifest del rejoc apunta a la captura.
    assert not (out / "pscp_open_data").exists()
    entries = json.loads((out / "raw_manifest.json").read_text(encoding="utf-8"))
    assert entries and all(entry["replay"] is True for entry in entries)
    log = json.loads((out / "fetch_log.json").read_text(encoding="utf-8"))
    assert log and all(entry["network"] is False for entry in log)


@pytest.mark.parametrize("extra", [["--batch-size", "1"], ["--page-limit", "2"]])
async def test_el_rejoc_no_depen_dels_lots_ni_de_la_paginacio(
    db, session_factory, tmp_path, no_network, extra
):
    capture = await _capture(db, session_factory, tmp_path)
    out = tmp_path / "rejoc"
    code = await amain(
        ["--replay", str(capture), "--out", str(out), *extra], session_factory=session_factory
    )
    assert code == EXIT_OK
    assert _comparable(_report(out)) == _comparable(_report(capture))


async def test_el_rejoc_amb_apply_apunta_a_la_captura_original(
    db, session_factory, tmp_path, no_network
):
    capture = await _capture(db, session_factory, tmp_path, ["lots", "lescala"])
    manifest = json.loads((capture / "raw_manifest.json").read_text(encoding="utf-8"))
    by_key = {entry["key"]: entry for entry in manifest}
    manifest_sha = hashlib.sha256((capture / "raw_manifest.json").read_bytes()).hexdigest()
    out = tmp_path / "rejoc"
    code = await amain(
        ["--replay", str(capture), "--out", str(out), "--apply"], session_factory=session_factory
    )
    assert code == EXIT_OK
    rows = await _observations(db)
    assert {r.amount_kind for r in rows} == {PBL, NET, VEC}
    for row in rows:
        entry = by_key[row.raw_storage_key]
        assert row.evidence_sha256 == entry["sha256_original"]
        assert _utc(row.retrieved_at) == datetime.fromisoformat(entry["retrieved_at"])
        assert (capture / row.raw_storage_key).exists()
        assert row.provenance["replay"] == {
            "dir": str(capture.resolve()),
            "raw_manifest_sha256": manifest_sha,
        }
        assert (
            row.provenance["rows_updated_at"]
            == _report(capture)["dataset_metadata"]["start"]["rows_updated_at"]
        )
    # Un segon rejoc sobre el que ja s'ha escrit: tot `unchanged`.
    code = await amain(
        ["--replay", str(capture), "--out", str(tmp_path / "rejoc2"), "--apply"],
        session_factory=session_factory,
    )
    assert code == EXIT_OK
    writes = _report(tmp_path / "rejoc2")["writes"]
    assert writes["inserted"] == 0 and writes["closed"] == 0
    assert writes["unchanged"] == len(rows)


@pytest.mark.parametrize("damage", ["altered", "missing", "manifest"])
async def test_una_captura_alterada_falla_sense_escriure_res(
    db, session_factory, tmp_path, no_network, damage
):
    capture = await _capture(db, session_factory, tmp_path, ["lots", "lescala"])
    manifest = json.loads((capture / "raw_manifest.json").read_text(encoding="utf-8"))
    batch = next(entry for entry in manifest if "batch_" in entry["key"])
    target = capture / batch["key"]
    if damage == "altered":
        document = json.loads(target.read_bytes())
        document[0]["pressupost_licitacio_sense"] = "1"
        target.write_text(json.dumps(document), encoding="utf-8")
    elif damage == "missing":
        target.unlink()
    else:
        (capture / "raw_manifest.json").unlink()
    out = tmp_path / "rejoc"
    code = await amain(
        ["--replay", str(capture), "--out", str(out), "--apply"], session_factory=session_factory
    )
    assert code == EXIT_FAILED
    assert await _observations(db) == []
    report = _report(out)
    assert report["replay"] is True and report["exit_code"] == EXIT_FAILED
    assert "ReplayError" in report["error"]
    assert report["network_requests"] == 0


async def test_una_uuid_no_capturada_falla_sense_escriure_res(
    db, session_factory, tmp_path, no_network
):
    capture = await _capture(db, session_factory, tmp_path, ["lots", "lescala"])
    source = Source(code=f"pscp-{uuid.uuid4().hex[:8]}", name="nova")
    db.add(source)
    await db.flush()
    db.add(
        Tender(
            source_id=source.id,
            external_id="ext-nova",
            title="nova",
            source_metadata={"pscp_id_intern": str(uuid.uuid4()), "placsp_organ_id": "1"},
        )
    )
    await db.commit()
    out = tmp_path / "rejoc"
    code = await amain(
        ["--replay", str(capture), "--out", str(out), "--apply"], session_factory=session_factory
    )
    assert code == EXIT_FAILED
    report = _report(out)
    assert "no es pot rejugar el que no es va capturar" in report["error"]
    assert report["requests"] == 0 and "writes" not in report
    assert await _observations(db) == []


async def test_una_uuid_demanada_sense_files_continua_sense_files(
    db, session_factory, tmp_path, no_network
):
    """`vat0` es demana a la captura pero la font (falsa) no en torna cap fila."""
    await _seed(db, ["lescala", "vat0"])
    capture = tmp_path / "captura"
    code, _ = await _run(
        session_factory, FakeSoda(_source_rows(["lescala"])), capture, dry_run=True
    )
    assert code == EXIT_OK
    out = tmp_path / "rejoc"
    code = await amain(
        ["--replay", str(capture), "--out", str(out)], session_factory=session_factory
    )
    assert code == EXIT_OK
    vat0 = (await db.execute(sa.select(Tender.id).where(Tender.title == "vat0"))).scalar_one()
    assert _report(out)["evaluation"]["not_found"] == [str(vat0)]


async def test_el_rejoc_no_admet_un_client_injectat(tmp_path, session_factory):
    with pytest.raises(ValueError, match="--replay"):
        await amain(
            ["--replay", str(tmp_path), "--out", str(tmp_path / "o")],
            session_factory=session_factory,
            client=object(),
        )


async def test_el_client_de_rejoc_verifica_abans_de_res(tmp_path):
    (tmp_path / "raw_manifest.json").write_text(
        json.dumps([{"key": "../fora.json", "sha256_stored": "x"}]), encoding="utf-8"
    )
    with pytest.raises(ReplayError, match="no admesa"):
        async with ReplayClient(tmp_path):
            pass


def test_l_ordre_del_cli_amb_replay():
    args = cli.build_parser().parse_args(["--replay", "c", "--out", "o", "--apply"])
    assert args.replay == Path("c") and args.apply is True
    copy.deepcopy(args)  # cap estat ocult
