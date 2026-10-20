"""Adaptador de dades obertes de la PSCP (F-06a, fase 1): guardes, client i servei.

Fixtures reals (redactades) del sondeig del 16-10-2026, reduides al `$select` minim:
`tests/fixtures/pscp_open_data/cases.json` (L'Escala, lots, arrodoniment d'1 centim,
IVA 0 %, publicat a 0, no publicat, BD desactualitzada, BD amb 0.00, fila de fase
previa substituida amb i sense valor, i un expedient amb files de lot que la nostra BD
no te). Els casos sintetics (evidencia no concloent, mes de 2 decimals, organ diferent)
es deriven d'aquestes files canviant un sol camp.

Cap test no surt a la xarxa: el servei rep un `httpx.MockTransport` que respon com
SODA a partir de les fixtures (filtre per UUID, ordre i paginacio).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from app.ingestion.pscp_open_data import SOURCE_CODE, attribution
from app.ingestion.pscp_open_data import client as client_module
from app.ingestion.pscp_open_data import guards as g
from app.ingestion.pscp_open_data import parser as p
from app.ingestion.pscp_open_data.__main__ import amain, build_parser
from app.ingestion.pscp_open_data.client import (
    RequestBudgetExceeded,
    SodaClient,
    SodaFetchError,
    redact,
    rows_url,
)
from app.ingestion.pscp_open_data.service import (
    EXIT_DATASET_CHANGED,
    EXIT_DATASET_DATE_MISSING,
    EXIT_EVALUATION_ABORTED,
    EXIT_EVALUATION_ERRORS,
    EXIT_FAILED,
    EXIT_OK,
    PscpOpenDataService,
    RunOptions,
    too_many_evaluation_errors,
)
from app.models.enums import AmountObservationStatus as S
from app.models.enums import TenderAmountKind as K
from app.models.sources import Source
from app.models.tender_amounts import TenderAmountObservation as Obs
from app.models.tenders import Tender, TenderLot

FIXTURES = Path(__file__).parent / "fixtures" / "pscp_open_data" / "cases.json"
DATA = json.loads(FIXTURES.read_text(encoding="utf-8"))
CASES = DATA["cases"]
ROWS_UPDATED_AT = DATA["rows_updated_at"]
PBL = K.PBL_WITH_TAX
#: F-06e: els imports de lot nous.
NEW_KINDS = (K.PBL_WITHOUT_TAX, K.ESTIMATED_VALUE)


def _utc(moment: datetime | None) -> datetime | None:
    """SQLite torna els `timestamptz` sense zona (en UTC)."""
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _dec(value):
    return None if value is None else Decimal(value)


def _snapshot(name: str, **overrides) -> g.TenderSnapshot:
    ours = CASES[name]["ours"]
    values = {
        "id": uuid.uuid5(uuid.NAMESPACE_URL, name),
        "pscp_id": ours["pscp_id_intern"],
        "organ_id": ours["placsp_organ_id"],
        "net": _dec(ours["base_budget_without_tax"]),
        "vec": _dec(ours["estimated_value"]),
        "lots": tuple(
            g.LotSnapshot(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{name}#{lot['lot_number']}"),
                lot["lot_number"],
                _dec(lot["base_budget_without_tax"]),
                _dec(lot["estimated_value"]),
            )
            for lot in ours["lots"]
        ),
    }
    values.update(overrides)
    return g.TenderSnapshot(**values)


def _rows(name: str) -> list[dict]:
    return copy.deepcopy(CASES[name]["rows"])


def _by(ev: g.TenderEvaluation, level: str) -> list[g.Draft]:
    return [d for d in ev.drafts if d.level == level and not d.validation.get("role")]


def _old_row(ev: g.TenderEvaluation) -> g.Draft:
    [draft] = [d for d in ev.drafts if d.validation.get("role")]
    return draft


# --------------------------------------------------------------------------- #
# Parser                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "value", "problem"),
    [
        ("12190.5", Decimal("12190.5"), None),
        ("247121.63", Decimal("247121.63"), None),
        ("298628", Decimal("298628"), None),
        ("0", Decimal("0"), None),
        ("0.00", Decimal("0"), None),
        (12, Decimal("12"), None),
        (None, None, None),
        ("", None, None),
        ("247121.635", None, "more_than_2_decimals"),
        ("-5.00", None, "negative"),
        ("1e5", None, "not_a_plain_decimal"),
        ("12,50", None, "not_a_plain_decimal"),
        (1.5, None, "unexpected_type"),
        (True, None, "unexpected_type"),
    ],
)
def test_els_imports_es_llegeixen_exactes_o_es_refusen(raw, value, problem):
    parsed = p.parse_amount(raw)
    assert parsed.value == value
    assert parsed.problem == problem
    if isinstance(raw, str) and raw:
        assert parsed.text == raw  # el text tal com es publica


def test_identificador_de_publicacio_nomes_si_l_uuid_es_el_de_la_fila():
    row = _rows("lescala")[0]
    assert p.publication_id(row) == int(row["enllac_publicacio"]["url"].rsplit("/", 1)[1])
    other = dict(row, id_intern=str(uuid.uuid4()))
    assert p.publication_id(other) is None
    assert p.publication_id(dict(row, enllac_publicacio=None)) is None


def test_l_ordre_de_fases_es_explicit():
    order = sorted(p.PHASE_ORDER, key=p.PHASE_ORDER.get)
    assert order[:3] == ["Alerta futura", "Consulta preliminar del mercat", "Anunci previ"]
    assert p.phase_rank("Una fase nova") is None


# --------------------------------------------------------------------------- #
# Guardes (pures) amb les fixtures reals                                        #
# --------------------------------------------------------------------------- #


def test_l_escala_s_accepta_tal_com_es_publica():
    ev = g.evaluate(_snapshot("lescala"), _rows("lescala"))
    [draft] = ev.drafts
    assert ev.shape == "single"
    assert draft.status == S.ACCEPTED
    assert draft.amount == Decimal("247121.63")
    assert draft.published_text == "247121.63"
    assert draft.source_row_id == CASES["lescala"]["ours"]["pscp_id_intern"]
    assert draft.source_entity_key == draft.source_row_id
    assert draft.tender_lot_id is None
    assert draft.validation["failed"] == []
    assert draft.validation["guards"]["net"] == {
        "ok": True,
        "published": "204232.75",
        "ours": "204232.75",
    }
    assert draft.validation["guards"]["vec"]["ok"] is True
    assert draft.validation["guards"]["organ"]["ok"] is True
    # El PBL sense IVA i el VEC publicats queden de contrast, no com a import.
    assert draft.cross_check == {
        "pressupost_licitacio_sense_1": "204232.75",
        "valor_estimat_expedient": "245079.3",
    }


def test_expedient_amb_lots_desa_l_expedient_i_cada_lot():
    snapshot = _snapshot("lots")
    ev = g.evaluate(snapshot, _rows("lots"))
    [expedient] = _by(ev, "expedient")
    lots = _by(ev, "lot")
    uid = snapshot.pscp_id
    assert ev.shape == "lots"
    assert expedient.status == S.ACCEPTED
    assert expedient.source_row_id == f"{uid}_*"
    assert expedient.amount == Decimal("72007.1")
    assert len(expedient.validation["rows"]) == 3
    assert [d.status for d in lots] == [S.ACCEPTED] * 3
    assert {d.tender_lot_id for d in lots} == {lot.id for lot in snapshot.lots}
    assert all(d.source_entity_key.startswith(f"{uid}#lot=") for d in lots)
    assert {d.source_row_id for d in lots} == {r["id_intern"] for r in _rows("lots")}


def test_arrodoniment_d_un_centim_entre_lots_i_expedient_es_tolera():
    ev = g.evaluate(_snapshot("rounding"), _rows("rounding"))
    [expedient] = _by(ev, "expedient")
    lot_sum = expedient.validation["guards"]["lot_sum"]
    assert lot_sum["ok"] is True
    assert lot_sum["difference"] == "0.01"
    assert lot_sum["tolerance"] == "0.03"
    # L'expedient es desa tal com es publica: mai la suma dels lots.
    assert expedient.amount == Decimal(_rows("rounding")[0]["pressupost_licitacio_amb_1"])
    assert {d.status for d in ev.drafts} == {S.ACCEPTED}


def test_la_suma_dels_lots_fora_de_tolerancia_es_conflicte():
    rows = _rows("rounding")
    rows[0]["pressupost_licitacio_amb"] = str(Decimal(rows[0]["pressupost_licitacio_amb"]) + 1)
    ev = g.evaluate(_snapshot("rounding"), rows)
    assert "lot_sum" in ev.failed
    assert {d.status for d in ev.drafts} == {S.CONFLICT}


def test_iva_del_zero_per_cent_es_legitim():
    ev = g.evaluate(_snapshot("vat0"), _rows("vat0"))
    [draft] = ev.drafts
    assert draft.status == S.ACCEPTED
    assert draft.validation["guards"]["tax_order"] == {"ok": True}
    assert draft.amount == Decimal(draft.cross_check["pressupost_licitacio_sense_1"])


def test_publicat_a_zero_es_no_value_amb_el_zero_tal_com_es_publica():
    ev = g.evaluate(_snapshot("zero"), _rows("zero"))
    [draft] = ev.drafts
    assert draft.status == S.NO_VALUE
    assert draft.amount == Decimal("0")
    assert draft.published_text == "0"
    assert draft.validation["no_value_reason"] == "zero_published"


def test_no_publicat_es_no_value_sense_import():
    ev = g.evaluate(_snapshot("null"), _rows("null"))
    [draft] = ev.drafts
    assert draft.status == S.NO_VALUE
    assert draft.amount is None and draft.published_text is None
    assert draft.validation["no_value_reason"] == "null_published"
    # La guarda (b) falla (la BD te 0.00 i la font no publica): no_value hi te precedencia.
    assert "net" in draft.validation["failed"]


@pytest.mark.parametrize("name", ["stale", "stale_zero"])
def test_la_bd_desactualitzada_queda_en_conflicte(name):
    ev = g.evaluate(_snapshot(name), _rows(name))
    [draft] = ev.drafts
    assert draft.status == S.CONFLICT
    assert "net" in draft.validation["failed"]
    assert draft.amount == Decimal(_rows(name)[0]["pressupost_licitacio_amb_1"])


def test_fila_de_fase_previa_substituida_amb_evidencia_concloent():
    ev = g.evaluate(_snapshot("superseded"), _rows("superseded"))
    old = _old_row(ev)
    [expedient] = _by(ev, "expedient")
    assert ev.shape == "mixed"
    assert old.status == S.SUPERSEDED
    assert old.amount == Decimal("970236.39")
    assert old.source_row_id == old.source_entity_key == _snapshot("superseded").pscp_id
    supersession = old.validation["supersession"]
    assert supersession["verdict"] == "superseded"
    assert all(check["ok"] for check in supersession["checks"].values())
    assert sorted(supersession["superseded_by"]) == sorted(
        r["id_intern"] for r in _rows("superseded") if len(r["id_intern"]) > 36
    )
    # Explicada, la fila antiga no impedeix acceptar l'expedient i els lots.
    assert expedient.status == S.ACCEPTED
    assert expedient.validation["guards"]["rows"] == {
        "ok": True,
        "unexplained": [],
        "supersession": "superseded",
    }
    assert {d.status for d in _by(ev, "lot")} == {S.ACCEPTED}


def test_fila_substituida_sense_valor_es_superseded_amb_import_nul():
    """L'estat diu el resultat de la validacio (substituida); l'import, el que es publica
    (res)."""
    ev = g.evaluate(_snapshot("superseded_null"), _rows("superseded_null"))
    old = _old_row(ev)
    assert old.status == S.SUPERSEDED and old.amount is None
    assert old.published_text is None
    assert old.validation["role"] == "superseded_row"
    assert old.validation["supersession"]["verdict"] == "superseded"
    assert {d.status for d in _by(ev, "expedient") + _by(ev, "lot")} == {S.ACCEPTED}


def test_fila_antiga_sense_valor_i_evidencia_no_concloent_es_conflicte_amb_import_nul():
    snapshot = _snapshot("superseded_null")
    rows = _rows("superseded_null")
    _exact(rows, snapshot.pscp_id).update(fase_publicacio="Formalitzaci\u00f3")
    ev = g.evaluate(snapshot, rows)
    old = _old_row(ev)
    assert old.validation["role"] == "unexplained_row"
    assert old.status == S.CONFLICT and old.amount is None
    assert {d.status for d in _by(ev, "expedient") + _by(ev, "lot")} == {S.CONFLICT}


def _exact(rows, uid):
    return next(r for r in rows if r["id_intern"] == uid)


@pytest.mark.parametrize(
    ("change", "check"),
    [
        # Identificador de publicacio mes alt que el de les files de lot.
        (
            lambda row: row["enllac_publicacio"].update(
                url=row["enllac_publicacio"]["url"].rsplit("/", 1)[0] + "/399999999"
            ),
            "publication_id",
        ),
        # Una fase que no es anterior.
        (lambda row: row.update(fase_publicacio="Formalitzaci\u00f3"), "phase"),
        # Una fase fora de l'ordre explicit.
        (lambda row: row.update(fase_publicacio="Fase desconeguda"), "phase"),
        # Actualitzada despres de les files de lot.
        (lambda row: row.update({":updated_at": "2030-01-01T00:00:00.000Z"}), "dates"),
        # Una data d'anunci posterior a la de les files noves.
        (lambda row: row.update(data_publicacio_anunci="2030-01-01T00:00:00.000"), "dates"),
        # Sense cap data de publicacio.
        (lambda row: row.pop("data_publicacio_previ"), "dates"),
    ],
)
def test_evidencia_no_concloent_es_conflicte_mai_descartada(change, check):
    snapshot = _snapshot("superseded")
    rows = _rows("superseded")
    change(_exact(rows, snapshot.pscp_id))
    ev = g.evaluate(snapshot, rows)
    old = _old_row(ev)
    assert old.validation["role"] == "unexplained_row"
    assert old.status == S.CONFLICT
    assert old.validation["supersession"]["verdict"] == "inconclusive"
    assert old.validation["supersession"]["checks"][check]["ok"] is False
    assert "rows" in ev.failed
    assert {d.status for d in _by(ev, "expedient") + _by(ev, "lot")} == {S.CONFLICT}


def test_files_de_lot_sense_lot_a_la_bd_queden_en_conflicte_sense_lot():
    snapshot = _snapshot("lot_without_db_lot")
    ev = g.evaluate(snapshot, _rows("lot_without_db_lot"))
    lots = _by(ev, "lot")
    [expedient] = _by(ev, "expedient")
    assert snapshot.lots == ()
    assert len(lots) == 2
    assert all(d.tender_lot_id is None and d.status == S.CONFLICT for d in lots)
    assert {d.source_entity_key for d in lots} == {
        f"{snapshot.pscp_id}#lot=1",
        f"{snapshot.pscp_id}#lot=2",
    }
    assert all(d.validation["lot_guards"]["match"]["problem"] == "no_lot_in_ours" for d in lots)
    assert expedient.status == S.CONFLICT
    assert expedient.validation["guards"]["lots"]["problem"] == "unexpected_lot_rows"


def test_mes_de_dos_decimals_es_conflicte_amb_import_nul_i_el_text_publicat():
    """No es pot desar sense alterar-lo: `amount` NULL, el text tal com es publica."""
    rows = _rows("lescala")
    rows[0]["pressupost_licitacio_amb_1"] = "247121.635"
    ev = g.evaluate(_snapshot("lescala"), rows)
    [draft] = ev.drafts
    assert draft.status == S.CONFLICT
    assert draft.amount is None
    assert draft.published_text == "247121.635"
    assert draft.validation["amount_problem"] == "more_than_2_decimals"
    assert draft.validation["no_value_reason"] is None
    assert "parse" in ev.failed


@pytest.mark.parametrize("bad", ["1.001", "1e3", "-5"])
def test_un_import_de_lot_no_representable_deixa_tot_l_expedient_en_conflicte(bad):
    """(I-2) Mai no s'accepta l'expedient ni cap lot germa si un lot no es pot llegir."""
    rows = _rows("lots")
    lot_row = next(r for r in rows if len(r["id_intern"]) > 36)
    lot_row["pressupost_licitacio_amb"] = bad
    ev = g.evaluate(_snapshot("lots"), rows)
    lots = _by(ev, "lot")
    [expedient] = _by(ev, "expedient")
    [unreadable] = [d for d in lots if d.validation["amount_problem"]]
    assert unreadable.amount is None and unreadable.published_text == bad
    assert "parse" in ev.failed
    assert expedient.status == S.CONFLICT
    assert {d.status for d in lots} == {S.CONFLICT} and len(lots) == 3
    assert expedient.validation["guards"]["lot_sum"] == {
        "ok": None,
        "skipped": "lot_amount_unrepresentable",
    }


def test_un_lot_sense_import_publicat_no_fa_caure_la_resta():
    """Decisio documentada: un lot NULL (no publicat) es `no_value`; la suma (e) no es
    pot comprovar i no s'aplica, pero la resta d'observacions es valida amb les altres
    guardes."""
    rows = _rows("lots")
    lot_row = next(r for r in rows if len(r["id_intern"]) > 36)
    lot_row["pressupost_licitacio_amb"] = None
    ev = g.evaluate(_snapshot("lots"), rows)
    [expedient] = _by(ev, "expedient")
    assert expedient.status == S.ACCEPTED
    assert expedient.validation["guards"]["lot_sum"] == {
        "ok": None,
        "skipped": "lot_amount_not_published",
    }
    assert sorted(d.status for d in _by(ev, "lot")) == sorted([S.ACCEPTED, S.ACCEPTED, S.NO_VALUE])


def test_expedient_publicat_a_zero_amb_lots_es_no_value_i_els_lots_conflicte():
    rows = _rows("lots")
    for row in rows:
        row["pressupost_licitacio_amb_1"] = "0"
    ev = g.evaluate(_snapshot("lots"), rows)
    [expedient] = _by(ev, "expedient")
    assert expedient.status == S.NO_VALUE and expedient.amount == Decimal("0")
    assert "lot_sum" in ev.failed
    assert {d.status for d in _by(ev, "lot")} == {S.CONFLICT}


def test_identificadors_de_publicacio_iguals_no_demostren_la_substitucio():
    snapshot = _snapshot("superseded")
    rows = _rows("superseded")
    newer = next(r for r in rows if len(r["id_intern"]) > 36)["enllac_publicacio"]["url"]
    _exact(rows, snapshot.pscp_id)["enllac_publicacio"]["url"] = (
        _exact(rows, snapshot.pscp_id)["enllac_publicacio"]["url"].rsplit("/", 1)[0]
        + "/"
        + newer.rsplit("/", 1)[1]
    )
    ev = g.evaluate(snapshot, rows)
    old = _old_row(ev)
    assert old.status == S.CONFLICT
    assert old.validation["supersession"]["checks"]["publication_id"]["ok"] is False


def test_una_fila_nova_sense_updated_at_no_demostra_la_substitucio():
    snapshot = _snapshot("superseded")
    rows = _rows("superseded")
    next(r for r in rows if len(r["id_intern"]) > 36).pop(":updated_at")
    ev = g.evaluate(snapshot, rows)
    old = _old_row(ev)
    assert old.status == S.CONFLICT
    assert old.validation["supersession"]["checks"]["dates"]["updated_at_ok"] is False


def test_una_data_amb_zona_no_atura_res_i_fa_l_evidencia_no_concloent():
    """(m-1) Abans: TypeError en comparar dates amb i sense zona."""
    snapshot = _snapshot("superseded")
    rows = _rows("superseded")
    _exact(rows, snapshot.pscp_id)["data_publicacio_previ"] = "2026-09-09T00:00:00+02:00"
    ev = g.evaluate(snapshot, rows)
    old = _old_row(ev)
    assert old.status == S.CONFLICT
    assert old.validation["supersession"]["checks"]["dates"]["unreadable"] == [
        "data_publicacio_previ"
    ]


def test_un_numero_de_lot_amb_digit_no_ascii_es_conflicte():
    """(m-1) Abans: ValueError a `int("\u00b2")`."""
    rows = _rows("lots")
    next(r for r in rows if len(r["id_intern"]) > 36)["numero_lot"] = "\u00b2"
    ev = g.evaluate(_snapshot("lots"), rows)
    assert {d.status for d in ev.drafts} == {S.CONFLICT}
    [orphan] = [d for d in _by(ev, "lot") if d.tender_lot_id is None]
    assert orphan.validation["lot_guards"]["match"]["problem"] == "no_lot_in_ours"


def test_una_fila_que_no_es_pot_desar_com_a_clau_es_llista_i_la_resta_es_conflicte():
    """(m-1) Un id_intern desmesurat (o amb caracters inesperats) no es desa: es llista,
    i l'expedient i els altres lots queden en conflicte."""
    snapshot = _snapshot("lots")
    rows = _rows("lots")
    rows[-1]["id_intern"] = snapshot.pscp_id + "_" + "x" * 300
    ev = g.evaluate(snapshot, rows)
    assert ev.notes["unkeyable_rows"] == ["id_intern_not_keyable"]
    assert all(len(d.source_row_id) <= 200 for d in ev.drafts)
    assert len(_by(ev, "lot")) == 2
    assert {d.status for d in ev.drafts} == {S.CONFLICT}
    assert ev.drafts[0].validation["guards"]["rows"]["unkeyable"] == 1


def test_un_organ_diferent_es_conflicte():
    ev = g.evaluate(_snapshot("lescala", organ_id="1"), _rows("lescala"))
    assert ev.failed == ["organ"]
    assert ev.drafts[0].status == S.CONFLICT


def test_un_pscp_id_compartit_es_conflicte():
    ev = g.evaluate(_snapshot("lescala", identity_unique=False), _rows("lescala"))
    assert ev.failed == ["identity"]


def test_sense_files_no_hi_ha_observacions():
    ev = g.evaluate(_snapshot("lescala"), [])
    assert ev.shape == "not_found" and ev.drafts == []


def test_la_validation_es_determinista():
    first = g.evaluate(_snapshot("superseded"), _rows("superseded"))
    second = g.evaluate(_snapshot("superseded"), list(reversed(_rows("superseded"))))
    assert [d.validation for d in first.drafts] == [d.validation for d in second.drafts]
    json.dumps([d.validation for d in first.drafts])  # JSON pla


# --------------------------------------------------------------------------- #
# Client                                                                        #
# --------------------------------------------------------------------------- #


def test_la_consulta_fa_igualtat_o_prefix_amb_guio_baix():
    uids = [CASES["lescala"]["ours"]["pscp_id_intern"], CASES["lots"]["ours"]["pscp_id_intern"]]
    url = rows_url(uids, select=p.SELECT_FIELDS, offset=5000, limit=5000)
    query = httpx.URL(url).params
    assert query["$where"] == (
        f"id_intern IN ('{uids[0]}','{uids[1]}') OR starts_with(id_intern,'{uids[0]}_') "
        f"OR starts_with(id_intern,'{uids[1]}_')"
    )
    assert query["$order"] == "id_intern,:id"
    assert query["$offset"] == "5000" and query["$limit"] == "5000"
    selected = query["$select"].split(",")
    assert "identificacio_adjudicatari" not in selected
    assert "denominacio_adjudicatari" not in selected


@pytest.fixture
def no_sleep(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    return waits


async def test_el_client_reintenta_429_i_5xx_amb_espera(no_sleep):
    answers = iter([429, 503, 200])

    def handler(request):
        status = next(answers)
        headers = {"Retry-After": "7"} if status == 429 else {}
        return httpx.Response(status, json=[] if status == 200 else {}, headers=headers)

    async with SodaClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), pause_seconds=0
    ) as soda:
        fetched = await soda.get("https://analisi.transparenciacatalunya.cat/resource/x.json")
    assert fetched.status == 200 and fetched.attempts == 3
    assert [entry["status"] for entry in soda.log] == [429, 503, 200]
    assert no_sleep[0] == 7.0  # Retry-After
    assert no_sleep[1] > 0


async def test_el_client_nomes_fa_get_a_la_font_i_respecta_el_limit(no_sleep):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(404 if "missing" in str(request.url) else 200, json=[])

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with SodaClient(client=transport, pause_seconds=0, max_requests=2) as soda:
        with pytest.raises(SodaFetchError):
            await soda.get("https://example.org/resource/x.json")
        with pytest.raises(SodaFetchError):  # 404: no es reintenta
            await soda.get("https://analisi.transparenciacatalunya.cat/missing.json")
        await soda.get("https://analisi.transparenciacatalunya.cat/resource/x.json")
        with pytest.raises(RequestBudgetExceeded):
            await soda.get("https://analisi.transparenciacatalunya.cat/resource/x.json")
    assert soda.requests_made == 2


async def test_el_client_fa_pausa_entre_peticions(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    transport = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    async with SodaClient(client=transport, pause_seconds=1.0) as soda:
        await soda.get("https://analisi.transparenciacatalunya.cat/a")
        await soda.get("https://analisi.transparenciacatalunya.cat/b")
    assert len(waits) == 1 and 0 < waits[0] <= 1.0


def test_la_redaccio_tapa_nif_contactes_i_adjudicataris():
    document = {
        "columns": [
            {
                "fieldName": "identificacio_adjudicatari",
                "cachedContents": {"top": [{"item": "B12345678"}]},
            },
            {"fieldName": "denominacio_adjudicatari", "cachedContents": {"top": [{"item": "X"}]}},
            {"fieldName": "codi_organ", "cachedContents": {"top": [{"item": "2294119"}]}},
        ],
        "contacte": "algu@example.org",
        "nif": "12345678Z",
        "lliure": "B12345678",
        "import": "247121.63",
    }
    clean, count = redact(document)
    text = json.dumps(clean)
    assert "B12345678" not in text and "12345678Z" not in text and "algu@example.org" not in text
    assert "2294119" in text and "247121.63" in text
    assert count == 5


def test_la_redaccio_tapa_nif_dins_d_un_text_lliure():
    """(m-8) Un NIF/NIE dins d'una frase d'una clau no sensible tambe es tapa; els UUID i
    els testimonis xifrats dels enllacos no es toquen."""
    uid = CASES["lescala"]["ours"]["pscp_id_intern"]
    link = _rows("lescala")[0]["url_json_licitacio"]["url"]
    document = {
        "descripcio": "Adjudicat a B12345678 i a X1234567L (UTE), amb 12345678Z.",
        "id_intern": uid,
        "enllac": link,
    }
    clean, count = redact(document)
    assert count == 3
    assert "B12345678" not in clean["descripcio"] and "X1234567L" not in clean["descripcio"]
    assert "12345678Z" not in clean["descripcio"] and "(UTE)" in clean["descripcio"]
    assert clean["id_intern"] == uid and clean["enllac"] == link


# --------------------------------------------------------------------------- #
# Servei (base de dades): dry-run, idempotencia, historial                      #
# --------------------------------------------------------------------------- #


class FakeSoda:
    """Respon com SODA a partir de les fixtures: filtra per UUID, ordena i pagina."""

    EQUAL = re.compile(r"id_intern='([^']*)'")
    IN = re.compile(r"id_intern IN \(([^)]*)\)")
    PREFIX = re.compile(r"starts_with\(id_intern,'([^']*)'\)")

    def __init__(self, rows: dict[str, list[dict]], rows_updated_at=(ROWS_UPDATED_AT,)):
        self.rows = rows
        self.updated = list(rows_updated_at)
        self.metadata_calls = 0
        self.bodies: dict[str, bytes] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path.startswith("/api/views/"):
            value = self.updated[min(self.metadata_calls, len(self.updated) - 1)]
            self.metadata_calls += 1
            body = json.dumps(
                {
                    "name": "Contractacio publica a Catalunya",
                    "rowsUpdatedAt": value,
                    "license": {"name": "See Terms of Use"},
                    "attribution": "Departament d'Economia i Finances",
                    "columns": [
                        {
                            "fieldName": "identificacio_adjudicatari",
                            "cachedContents": {"top": [{"item": "B12345678"}]},
                        }
                    ],
                }
            ).encode()
        else:
            params = request.url.params
            where = params["$where"]
            equal = set(self.EQUAL.findall(where))
            for listed in self.IN.findall(where):
                equal |= set(re.findall(r"'([^']*)'", listed))
            prefixes = self.PREFIX.findall(where)
            matched = sorted(
                (
                    row
                    for rows in self.rows.values()
                    for row in rows
                    if row["id_intern"] in equal
                    or any(row["id_intern"].startswith(prefix) for prefix in prefixes)
                ),
                key=lambda row: (row["id_intern"], row[":id"]),
            )
            offset = int(params.get("$offset", 0))
            limit = int(params.get("$limit", 1000))
            body = json.dumps(matched[offset : offset + limit]).encode()
        self.bodies[str(request.url)] = body
        return httpx.Response(200, content=body)


def _source_rows(names) -> dict[str, list[dict]]:
    return {CASES[name]["ours"]["pscp_id_intern"]: _rows(name) for name in names}


ALL = [
    "lescala",
    "lots",
    "rounding",
    "vat0",
    "zero",
    "null",
    "stale",
    "stale_zero",
    "superseded",
    "superseded_null",
    "lot_without_db_lot",
]


async def _seed(db, names=ALL) -> dict[str, uuid.UUID]:
    source = Source(code=f"pscp-{uuid.uuid4().hex[:8]}", name="PLACSP de prova")
    db.add(source)
    await db.flush()
    ids = {}
    for name in names:
        ours = CASES[name]["ours"]
        tender = Tender(
            source_id=source.id,
            external_id=f"ext-{name}",
            title=name,
            base_budget_without_tax=_dec(ours["base_budget_without_tax"]),
            estimated_value=_dec(ours["estimated_value"]),
            source_metadata={
                "pscp_id_intern": ours["pscp_id_intern"],
                "placsp_organ_id": ours["placsp_organ_id"],
            },
        )
        db.add(tender)
        await db.flush()
        for lot in ours["lots"]:
            db.add(
                TenderLot(
                    tender_id=tender.id,
                    external_lot_id=str(lot["lot_number"]),
                    lot_number=lot["lot_number"],
                )
            )
        ids[name] = tender.id
    # Una licitacio sense pscp_id_intern: fora de l'univers.
    db.add(Tender(source_id=source.id, external_id="ext-no-pscp", title="sense pscp"))
    await db.commit()
    return ids


async def _run(session_factory, fake, out: Path, **options) -> tuple[int, dict]:
    options.setdefault("dry_run", False)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    async with SodaClient(client=http, pause_seconds=0) as soda:
        return await PscpOpenDataService(session_factory, soda).run(
            RunOptions(out_dir=out, **options)
        )


async def _observations(db, *, current_only=False) -> list[Obs]:
    query = sa.select(Obs).order_by(Obs.created_at, Obs.source_row_id)
    if current_only:
        query = query.where(Obs.valid_to.is_(None))
    db.expire_all()
    return list((await db.execute(query)).scalars().all())


def _expected_statuses() -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in ALL:
        ev = g.evaluate(_snapshot(name), _rows(name))
        for draft in ev.all_drafts:  # F-06e: tambe el PBL sense IVA i el VEC de lot
            counts[draft.status.value] = counts.get(draft.status.value, 0) + 1
    return counts


async def test_dry_run_ho_calcula_tot_i_no_escriu_res(db, session_factory, tmp_path):
    await _seed(db)
    fake = FakeSoda(_source_rows(ALL))
    code, report = await _run(session_factory, fake, tmp_path, dry_run=True)
    assert code == EXIT_OK, report.get("error")
    assert await _observations(db) == []
    writes = report["writes"]
    assert writes["committed"] is False
    assert writes["inserted"] == sum(_expected_statuses().values())
    assert writes["inserted_by_status"] == _expected_statuses()
    assert report["requests"] == 3  # metadades, un lot de 11 UUID, metadades
    assert (tmp_path / "report.json").exists() and (tmp_path / "observations.csv").exists()
    assert report["universe"]["selected"] == len(ALL)


async def test_run_real_desa_amb_procedencia_i_el_segon_no_escriu_res(
    db, session_factory, tmp_path
):
    ids = await _seed(db)
    fake = FakeSoda(_source_rows(ALL))
    code, report = await _run(session_factory, fake, tmp_path / "1")
    assert code == EXIT_OK, report.get("error")
    rows = await _observations(db)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.validation_status.value] = counts.get(row.validation_status.value, 0) + 1
    assert counts == _expected_statuses()
    # F-06e: el PBL sense IVA i el VEC nomes de lot; l'expedient, nomes el PBL amb IVA.
    assert {row.amount_kind.value for row in rows} == {
        "pbl_with_tax",
        "pbl_without_tax",
        "estimated_value",
    }
    assert {
        row.amount_kind.value
        for row in rows
        if row.tender_lot_id is None and "#lot=" not in row.source_entity_key
    } == {"pbl_with_tax"}
    assert {row.source_code for row in rows} == {SOURCE_CODE}

    [escala] = [row for row in rows if row.tender_id == ids["lescala"]]
    assert escala.validation_status == S.ACCEPTED
    assert escala.amount == Decimal("247121.63")
    assert escala.currency == "EUR"
    assert escala.valid_to is None
    assert _utc(escala.source_dataset_updated_at) == datetime.fromtimestamp(ROWS_UPDATED_AT, UTC)
    # Evidencia: la consulta curta i exacta de la fila; la peticio per lots, pel SHA del
    # seu cos original i la resposta redactada (i la URL completa, un sol cop, al manifest).
    uid = CASES["lescala"]["ours"]["pscp_id_intern"]
    assert httpx.URL(escala.evidence_url).params["$where"] == f"id_intern='{uid}'"
    assert len(escala.evidence_url) < 200
    manifest = json.loads((tmp_path / "1" / "raw_manifest.json").read_text(encoding="utf-8"))
    [batch] = [entry for entry in manifest if entry["key"] == escala.raw_storage_key]
    assert batch["sha256_original"] == escala.evidence_sha256
    assert hashlib.sha256(fake.bodies[batch["url"]]).hexdigest() == escala.evidence_sha256
    assert (tmp_path / "1" / escala.raw_storage_key).exists()
    # Cada evidence_url es torna a demanar i porta exactament les files de l'observacio.
    for row in rows:
        assert len(row.evidence_url) <= 1024
        refetched = json.loads(fake(httpx.Request("GET", row.evidence_url)).content)
        assert sorted(r[":id"] for r in refetched) == sorted(
            r[":id"] for r in row.provenance["rows"]
        )
    provenance = escala.provenance
    assert provenance["field"] == "pressupost_licitacio_amb_1"
    assert provenance["published_value"] == "247121.63"
    assert provenance["dataset"] == "ybgg-dgi6"
    assert provenance["rows"][0][":id"] == _rows("lescala")[0][":id"]
    assert provenance["rows"][0][":updated_at"] == _rows("lescala")[0][":updated_at"]
    assert provenance["rows"][0]["codi_organ"] == "2294119"
    assert provenance["rows"][0]["publication_id"] is not None
    assert "url_json_licitacio" in provenance["url_json"]
    assert provenance["licence"]["attribution"] == attribution(
        datetime.fromtimestamp(ROWS_UPDATED_AT, UTC).isoformat()
    )
    assert provenance["cross_check"]["pressupost_licitacio_sense_1"] == "204232.75"

    # Lots desats amb el seu tender_lot_id; el lot sense lot a la BD, sense.
    lots = [
        row
        for row in rows
        if row.tender_id == ids["lots"] and row.tender_lot_id and row.amount_kind == PBL
    ]
    assert len(lots) == 3 and {row.validation_status for row in lots} == {S.ACCEPTED}
    for kind in NEW_KINDS:
        same = [r for r in rows if r.tender_id == ids["lots"] and r.amount_kind == kind]
        assert {r.validation_status for r in same} == {S.ACCEPTED}
        assert {(r.tender_lot_id, r.source_row_id, r.source_entity_key) for r in same} == {
            (r.tender_lot_id, r.source_row_id, r.source_entity_key) for r in lots
        }
    orphans = [row for row in rows if row.tender_id == ids["lot_without_db_lot"]]
    assert {
        (row.tender_lot_id, row.validation_status)
        for row in orphans
        if "#lot=" in row.source_entity_key
    } == {(None, S.CONFLICT)}
    # Les respostes desades no porten el NIF de les metadades.
    for path in (tmp_path / "1").rglob("metadata_*.json"):
        assert "B12345678" not in path.read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "1" / "raw_manifest.json").read_text(encoding="utf-8"))
    assert all(len(entry["sha256_original"]) == 64 for entry in manifest)

    # Segon run, mateixa font: cap escriptura.
    code, again = await _run(session_factory, fake, tmp_path / "2")
    assert code == EXIT_OK
    assert again["writes"]["inserted"] == 0 and again["writes"]["closed"] == 0
    assert again["writes"]["unchanged"] == len(rows)
    assert len(await _observations(db)) == len(rows)


async def test_un_valor_canviat_tanca_l_anterior_i_n_afegeix_un_de_nou(
    db, session_factory, tmp_path
):
    ids = await _seed(db, ["lescala", "stale"])
    source = _source_rows(["lescala", "stale"])
    await _run(session_factory, FakeSoda(source), tmp_path / "1")

    uid = CASES["lescala"]["ours"]["pscp_id_intern"]
    source[uid][0]["pressupost_licitacio_amb_1"] = "247121.64"
    # I la nostra BD es posa al dia per a `stale`: el conflicte passa a acceptat.
    stale = await db.get(Tender, ids["stale"])
    stale.base_budget_without_tax = Decimal(
        source[CASES["stale"]["ours"]["pscp_id_intern"]][0]["pressupost_licitacio_sense_1"]
    )
    stale.estimated_value = Decimal(
        source[CASES["stale"]["ours"]["pscp_id_intern"]][0]["valor_estimat_expedient"]
    )
    await db.commit()

    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_OK, report.get("error")
    assert report["writes"]["closed_changed"] == 2 and report["writes"]["inserted"] == 2
    history = [row for row in await _observations(db) if row.tender_id == ids["lescala"]]
    assert [(row.amount, row.valid_to is None) for row in history] == [
        (Decimal("247121.63"), False),
        (Decimal("247121.64"), True),
    ]
    assert history[0].valid_to == history[1].valid_from
    current = [
        row for row in await _observations(db, current_only=True) if row.tender_id == ids["stale"]
    ]
    assert [row.validation_status for row in current] == [S.ACCEPTED]


async def test_si_el_dataset_canvia_durant_el_run_no_s_escriu_res(db, session_factory, tmp_path):
    await _seed(db, ["lescala"])
    fake = FakeSoda(
        _source_rows(["lescala"]), rows_updated_at=(ROWS_UPDATED_AT, ROWS_UPDATED_AT + 60)
    )
    code, report = await _run(session_factory, fake, tmp_path)
    assert code == EXIT_DATASET_CHANGED
    assert report["writes"] == {"skipped": "dataset_changed_during_run"}
    assert await _observations(db) == []


async def test_una_fila_que_desapareix_es_llista_pero_no_es_tanca(db, session_factory, tmp_path):
    ids = await _seed(db, ["lescala", "vat0"])
    await _run(session_factory, FakeSoda(_source_rows(["lescala", "vat0"])), tmp_path / "1")
    code, report = await _run(session_factory, FakeSoda(_source_rows(["lescala"])), tmp_path / "2")
    assert code == EXIT_OK
    [gone] = report["writes"]["disappeared_not_closed"]
    assert gone["tender_id"] == str(ids["vat0"])
    assert report["writes"]["closed"] == 0
    assert report["evaluation"]["not_found"] == [str(ids["vat0"])]
    current = await _observations(db, current_only=True)
    assert {row.tender_id for row in current} == {ids["lescala"], ids["vat0"]}


async def test_una_altra_fila_acceptada_per_al_mateix_lot_desplaca_l_anterior(
    db, session_factory, tmp_path
):
    ids = await _seed(db, ["lots"])
    source = _source_rows(["lots"])
    await _run(session_factory, FakeSoda(source), tmp_path / "1")
    uid = CASES["lots"]["ours"]["pscp_id_intern"]
    renamed = source[uid][0]["id_intern"]
    source[uid][0]["id_intern"] = uid + "_renumerada"
    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_OK, report.get("error")
    # Cada tipus es una serie a part: el lot desplaca la seva, tipus per tipus.
    assert report["writes"]["by_kind"]["pbl_with_tax"]["closed_displaced"] == 1
    assert report["writes"]["closed_displaced"] == 3
    current = [
        row for row in await _observations(db, current_only=True) if row.tender_id == ids["lots"]
    ]
    assert renamed not in {row.source_row_id for row in current}
    for kind in (PBL, *NEW_KINDS):
        accepted_lots = [
            r
            for r in current
            if r.tender_lot_id and r.validation_status == S.ACCEPTED and r.amount_kind == kind
        ]
        assert len(accepted_lots) == 3


async def test_paginacio_i_cli_amb_tender_i_limit(db, session_factory, tmp_path):
    ids = await _seed(db, ["lescala", "lots", "superseded"])
    fake = FakeSoda(_source_rows(["lescala", "lots", "superseded"]))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    soda = SodaClient(client=http, pause_seconds=0)
    code = await amain(
        [
            "--out",
            str(tmp_path / "t"),
            "--tender",
            str(ids["superseded"]),
            "--page-limit",
            "2",
            "--apply",
        ],
        session_factory=session_factory,
        client=soda,
    )
    assert code == EXIT_OK
    report = json.loads((tmp_path / "t" / "report.json").read_text(encoding="utf-8"))
    assert report["universe"]["selected"] == 1
    assert report["requests"] == 2 + 3  # metadades x2, 4 files en pagines de 2 (+ una buida)
    rows = await _observations(db)
    assert {row.tender_id for row in rows} == {ids["superseded"]}
    superseded = [row for row in rows if row.validation_status == S.SUPERSEDED]
    assert len(superseded) == 1

    fake2 = FakeSoda(_source_rows(["lescala", "lots", "superseded"]))
    soda2 = SodaClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake2)), pause_seconds=0
    )
    code = await amain(
        ["--out", str(tmp_path / "l"), "--limit", "1", "--dry-run"],
        session_factory=session_factory,
        client=soda2,
    )
    assert code == EXIT_OK
    report = json.loads((tmp_path / "l" / "report.json").read_text(encoding="utf-8"))
    assert report["universe"]["selected"] == 1 and report["dry_run"] is True


async def test_el_cli_per_defecte_no_escriu_i_nomes_apply_escriu(db, session_factory, tmp_path):
    """(m-3) Segur per defecte: sense `--apply` es un dry-run (i `--dry-run`, un alias)."""
    await _seed(db, ["lescala"])
    for n, extra in enumerate(([], ["--dry-run"])):
        soda = SodaClient(
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(FakeSoda(_source_rows(["lescala"])))
            ),
            pause_seconds=0,
        )
        code = await amain(
            ["--out", str(tmp_path / f"d{n}"), *extra], session_factory=session_factory, client=soda
        )
        assert code == EXIT_OK
        report = json.loads((tmp_path / f"d{n}" / "report.json").read_text(encoding="utf-8"))
        assert report["dry_run"] is True and report["writes"]["committed"] is False
        assert await _observations(db) == []
    soda = SodaClient(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(FakeSoda(_source_rows(["lescala"])))
        ),
        pause_seconds=0,
    )
    code = await amain(
        ["--out", str(tmp_path / "a"), "--apply"], session_factory=session_factory, client=soda
    )
    assert code == EXIT_OK
    assert len(await _observations(db)) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["--apply", "--dry-run"],
        ["--page-limit", "0"],
        ["--limit", "0"],
        ["--limit", "-3"],
        ["--max-requests", "0"],
        ["--batch-size", "101"],
        ["--batch-size", "0"],
    ],
)
def test_el_cli_refusa_opcions_invalides(argv, tmp_path):
    """(m-7) Abans, `--page-limit 0` feia un bucle fins al limit de peticions."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--out", str(tmp_path), *argv])


async def test_sense_rows_updated_at_no_s_escriu_res(db, session_factory, tmp_path):
    """(m-2) La llicencia exigeix informar de la darrera actualitzacio."""
    await _seed(db, ["lescala"])
    for n, dates in enumerate(((None,), (ROWS_UPDATED_AT, None))):
        fake = FakeSoda(_source_rows(["lescala"]), rows_updated_at=dates)
        code, report = await _run(session_factory, fake, tmp_path / str(n))
        assert code == EXIT_DATASET_DATE_MISSING
        assert report["writes"] == {"skipped": "dataset_rows_updated_at_missing"}
    assert await _observations(db) == []


async def test_una_fallada_a_mitja_consulta_no_escriu_res(db, session_factory, tmp_path):
    """(n-3) La fallada arriba al TERCER lot de la consulta, despres de dos lots bons."""
    names = ["lescala", "lots", "vat0", "zero"]
    await _seed(db, names)
    fake = FakeSoda(_source_rows(names))
    resource_calls = []

    def failing(request):
        if request.url.path.startswith("/resource/"):
            resource_calls.append(str(request.url))
            if len(resource_calls) == 3:
                return httpx.Response(404)
        return fake(request)

    code, report = await _run(session_factory, failing, tmp_path, batch_size=1)
    assert code == EXIT_FAILED
    assert len(resource_calls) == 3
    assert "SodaFetchError" in report["error"]
    assert "writes" not in report
    assert await _observations(db) == []


async def test_una_forma_inesperada_no_atura_el_run(db, session_factory, tmp_path):
    """(m-1) Una data amb zona, un numero de lot estrany o un id desmesurat en una
    licitacio: aquella queda en conflicte (o la fila es llista) i la resta es desa."""
    ids = await _seed(db, ["lescala", "lots", "superseded"])
    source = _source_rows(["lescala", "lots", "superseded"])
    sup = CASES["superseded"]["ours"]["pscp_id_intern"]
    _exact(source[sup], sup)["data_publicacio_previ"] = "2026-09-09T00:00:00+02:00"
    lots_uid = CASES["lots"]["ours"]["pscp_id_intern"]
    source[lots_uid][-1]["id_intern"] = lots_uid + "_" + "x" * 300
    code, report = await _run(session_factory, FakeSoda(source), tmp_path)
    assert code == EXIT_OK, report.get("error")
    assert report["evaluation"]["evaluation_errors"] == []
    current = await _observations(db, current_only=True)
    by_tender: dict = {}
    for row in current:
        by_tender.setdefault(row.tender_id, set()).add(row.validation_status)
    assert by_tender[ids["lescala"]] == {S.ACCEPTED}
    assert by_tender[ids["lots"]] == {S.CONFLICT}
    assert by_tender[ids["superseded"]] == {S.CONFLICT}


# --------------------------------------------------------------------------- #
# (I-1) Una lectura nova mai no deixa vigent una `accepted` que ja no avala      #
# --------------------------------------------------------------------------- #


def _current_accepted(rows, tender_id, kind=None):
    """Les `accepted` d'un tipus (per defecte el PBL amb IVA, el de sempre)."""
    kind = kind or PBL
    return [
        r
        for r in rows
        if r.tender_id == tender_id and r.validation_status == S.ACCEPTED and r.amount_kind == kind
    ]


async def test_lot_retirat_tanca_l_accepted_de_la_clau_antiga(db, session_factory, tmp_path):
    ids = await _seed(db, ["lots"])
    source = _source_rows(["lots"])
    await _run(session_factory, FakeSoda(source), tmp_path / "1")
    first = await _observations(db, current_only=True)
    assert len(_current_accepted(first, ids["lots"])) == 4

    lot = (
        (
            await db.execute(
                sa.select(TenderLot)
                .where(TenderLot.tender_id == ids["lots"])
                .order_by(TenderLot.lot_number)
            )
        )
        .scalars()
        .first()
    )
    removed_lot_id = lot.id
    lot.removed_at = datetime.now(UTC)
    await db.commit()

    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_OK, report.get("error")
    assert report["writes"]["by_kind"]["pbl_with_tax"]["closed_reassigned"] == 1
    assert report["writes"]["closed_reassigned"] == 3  # un per tipus
    assert report["writes"]["disappeared_not_closed"] == []
    current = await _observations(db, current_only=True)
    for kind in (PBL, *NEW_KINDS):
        assert _current_accepted(current, ids["lots"], kind) == []
    assert removed_lot_id not in {r.tender_lot_id for r in current}
    orphans = [r for r in current if r.tender_lot_id is None and "#lot=" in r.source_entity_key]
    assert [r.validation_status for r in orphans] == [S.CONFLICT] * 3


async def test_accepted_passa_a_conflict_quan_canvia_el_valor(db, session_factory, tmp_path):
    ids = await _seed(db, ["lescala"])
    source = _source_rows(["lescala"])
    await _run(session_factory, FakeSoda(source), tmp_path / "1")
    uid = CASES["lescala"]["ours"]["pscp_id_intern"]
    source[uid][0]["pressupost_licitacio_sense_1"] = "204232.76"
    source[uid][0]["pressupost_licitacio_amb_1"] = "247121.64"
    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_OK
    assert report["writes"]["closed_changed"] == 1
    current = await _observations(db, current_only=True)
    assert [(r.validation_status, r.amount) for r in current] == [
        (S.CONFLICT, Decimal("247121.64"))
    ]
    assert _current_accepted(current, ids["lescala"]) == []


async def test_accepted_passa_a_superseded_quan_arriben_els_lots(db, session_factory, tmp_path):
    ids = await _seed(db, ["superseded"])
    uid = CASES["superseded"]["ours"]["pscp_id_intern"]
    lots = (
        (await db.execute(sa.select(TenderLot).where(TenderLot.tender_id == ids["superseded"])))
        .scalars()
        .all()
    )
    for lot in lots:  # abans: la nostra BD encara no tenia els lots
        lot.removed_at = datetime.now(UTC)
    await db.commit()
    source = _source_rows(["superseded"])
    only_exact = {uid: [_exact(source[uid], uid)]}
    await _run(session_factory, FakeSoda(only_exact), tmp_path / "1")
    [first] = await _observations(db, current_only=True)
    assert first.validation_status == S.ACCEPTED and first.source_row_id == uid

    for lot in lots:
        lot.removed_at = None
    await db.commit()
    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_OK, report.get("error")
    current = await _observations(db, current_only=True)
    expedient = [r for r in current if r.tender_lot_id is None]
    assert {(r.source_row_id, r.validation_status) for r in expedient} == {
        (uid, S.SUPERSEDED),
        (f"{uid}_*", S.ACCEPTED),
    }
    assert len(_current_accepted(current, ids["superseded"])) == 4  # expedient + 3 lots


async def test_una_lectura_nova_no_acceptada_tanca_l_accepted_d_una_fila_que_ja_no_hi_es(
    db, session_factory, tmp_path
):
    ids = await _seed(db, ["lescala"])
    uid = CASES["lescala"]["ours"]["pscp_id_intern"]
    source = _source_rows(["lescala"])
    await _run(session_factory, FakeSoda(source), tmp_path / "1")
    # La font substitueix la fila de l'expedient per una fila de lot que la nostra BD no
    # te: la lectura d'ara de l'expedient es `conflict`.
    row = dict(source[uid][0], id_intern=f"{uid}_1", numero_lot="1")
    code, report = await _run(session_factory, FakeSoda({uid: [row]}), tmp_path / "2")
    assert code == EXIT_OK
    assert report["writes"]["closed_superseded_by_reading"] == 1
    current = await _observations(db, current_only=True)
    assert _current_accepted(current, ids["lescala"]) == []
    assert {r.validation_status for r in current} == {S.CONFLICT}


# --------------------------------------------------------------------------- #
# (I-3, n-1) Una licitacio que no es pot avaluar mai no s'empassa                #
# --------------------------------------------------------------------------- #


def _raising_for(monkeypatch, pscp_ids):
    real = g.evaluate

    def evaluate(snapshot, rows):
        if snapshot.pscp_id in pscp_ids:
            int("9" * 5000)  # ValueError: el limit de digits de `int()`
        return real(snapshot, rows)

    monkeypatch.setattr(g, "evaluate", evaluate)


async def test_una_excepcio_en_avaluar_desa_conflict_i_tanca_l_accepted(
    db, session_factory, tmp_path, monkeypatch, caplog
):
    names = ["lescala", "lots", "vat0"]
    ids = await _seed(db, names)
    source = _source_rows(names)
    await _run(session_factory, FakeSoda(source), tmp_path / "1")
    assert len(_current_accepted(await _observations(db, current_only=True), ids["lots"])) == 4

    _raising_for(monkeypatch, {CASES["lots"]["ours"]["pscp_id_intern"]})
    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_EVALUATION_ERRORS
    [error] = report["evaluation"]["evaluation_errors"]
    assert error["tender_id"] == str(ids["lots"]) and error["class"] == "ValueError"
    assert any(
        record.exc_info and "pscp_open_data.evaluate.failed" in record.getMessage()
        for record in caplog.records
    )
    current = await _observations(db, current_only=True)
    lots_now = [r for r in current if r.tender_id == ids["lots"]]
    assert _current_accepted(current, ids["lots"]) == []
    [conflict] = [r for r in lots_now if r.validation_status == S.CONFLICT]
    assert conflict.amount is None and conflict.tender_lot_id is None
    assert conflict.validation["error"]["class"] == "ValueError"
    assert conflict.source_row_id == CASES["lots"]["ours"]["pscp_id_intern"] + "_*"
    assert report["writes"]["closed_changed"] == 1  # l'expedient (mateixa clau)
    assert report["writes"]["by_kind"]["pbl_with_tax"]["closed_unverifiable"] == 3
    assert report["writes"]["closed_unverifiable"] == 9  # els tres lots, de cada tipus
    for kind in NEW_KINDS:
        assert _current_accepted(current, ids["lots"], kind) == []
    # Les altres licitacions no en pateixen.
    assert len(_current_accepted(current, ids["lescala"])) == 1
    assert len(_current_accepted(current, ids["vat0"])) == 1


async def test_massa_errors_d_avaluacio_aturen_el_run_sense_escriure(
    db, session_factory, tmp_path, monkeypatch
):
    await _seed(db)
    source = _source_rows(ALL)
    await _run(session_factory, FakeSoda(source), tmp_path / "1")
    before = [(r.id, r.valid_to) for r in await _observations(db)]
    _raising_for(monkeypatch, {CASES[name]["ours"]["pscp_id_intern"] for name in ALL})
    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_EVALUATION_ABORTED
    assert len(report["evaluation"]["evaluation_errors"]) == len(ALL)
    assert report["writes"] == {"skipped": "too_many_evaluation_errors"}
    assert [(r.id, r.valid_to) for r in await _observations(db)] == before


def test_el_llindar_de_fallada_sistematica():
    assert not too_many_evaluation_errors(1, 3)
    assert not too_many_evaluation_errors(5, 10)
    assert too_many_evaluation_errors(6, 100)
    assert not too_many_evaluation_errors(20, 7859)
    assert too_many_evaluation_errors(21, 7859)


async def test_un_numero_de_lot_de_5000_digits_no_falla_i_no_deixa_accepted(
    db, session_factory, tmp_path
):
    """Abans: `int()` llancava ValueError (limit de 4.300 digits) dins de `evaluate`."""
    ids = await _seed(db, ["lots"])
    source = _source_rows(["lots"])
    await _run(session_factory, FakeSoda(source), tmp_path / "1")
    uid = CASES["lots"]["ours"]["pscp_id_intern"]
    source[uid][0]["numero_lot"] = "1" * 5000
    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_OK, report.get("error")
    assert report["evaluation"]["evaluation_errors"] == []
    current = await _observations(db, current_only=True)
    assert _current_accepted(current, ids["lots"]) == []
    # El lot de la fila no desable, de cada tipus.
    assert report["writes"]["by_kind"]["pbl_with_tax"]["closed_unverifiable"] == 1
    assert report["writes"]["closed_unverifiable"] == 3
    assert report["writes"]["disappeared_not_closed"] == []


async def test_si_cap_fila_no_es_pot_desar_no_es_not_found_i_tanca_l_accepted(
    db, session_factory, tmp_path
):
    """(n-1) La font publica files, pero cap no es desable: lectura en conflicte."""
    ids = await _seed(db, ["lots"])
    source = _source_rows(["lots"])
    await _run(session_factory, FakeSoda(source), tmp_path / "1")
    uid = CASES["lots"]["ours"]["pscp_id_intern"]
    for row in source[uid]:
        row["numero_lot"] = "7" * 150
    code, report = await _run(session_factory, FakeSoda(source), tmp_path / "2")
    assert code == EXIT_OK, report.get("error")
    assert report["evaluation"]["not_found"] == []
    assert report["evaluation"]["shapes"] == {"unreadable": 1}
    current = await _observations(db, current_only=True)
    assert _current_accepted(current, ids["lots"]) == []
    [conflict] = [r for r in current if r.tender_id == ids["lots"]]
    assert conflict.validation_status == S.CONFLICT
    assert conflict.validation["error"] == {
        "class": "unkeyable_rows",
        "message": "numero_lot_too_long",
    }
    assert len(conflict.evidence_url) <= 1024
    assert report["writes"]["disappeared_not_closed"] == []


def test_el_resum_del_cli_mostra_motius_i_desaparegudes(capsys):
    """(n-2) L'operador veu per que s'ha tancat cada observacio i quantes no es tanquen."""
    from app.ingestion.pscp_open_data.__main__ import _print

    _print(
        {
            "dry_run": False,
            "exit_code": 5,
            "requests": 3,
            "evaluation": {"tenders": 2, "observations": {}, "evaluation_errors": [{}]},
            "writes": {
                "inserted": 1,
                "closed": 3,
                "unchanged": 0,
                "closed_changed": 1,
                "closed_reassigned": 0,
                "closed_unverifiable": 2,
                "disappeared_not_closed": [{}, {}],
            },
        }
    )
    out = capsys.readouterr().out
    assert "errors d'avaluacio: 1" in out
    assert "closed_changed" in out and "closed_unverifiable" in out
    assert "closed_reassigned" not in out
    assert "desaparegudes (no tancades): 2" in out
