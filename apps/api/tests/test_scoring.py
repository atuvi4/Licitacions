"""QUALITY-01: sistema de puntuacio estructurat, validacio, calcul prudent i reconciliacio.

L'especificacio de L'Escala (`datasets/scoring/lescala_2026_3964.json`) es publica
(plecs oficials). Els
tests que la contrasten amb el TEXT oficial nomes s'executen si les fonts hi son:
* `LESCALA_PAGES_DIR`: carpeta amb el text extret de les pagines (`<n>_<document>.txt`).
* `LESCALA_OFFICIAL_XLSX`: plantilla oficial (.xlsx), llegida amb `xlsx-v2`.
* `LESCALA_AUDIT_DIR`: inventari del run 3 (`inventory/award_criteria.json`) i el mapatge revisat
  (`review/D_criteria_review.json`) per reconciliar els 97 registres reals.
"""

from __future__ import annotations

import copy
import json
import os
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.scoring import (
    Evaluation,
    RuleKind,
    ScoringInputInvalid,
    ScoringInputMissing,
    ScoringRuleMissing,
    SpecError,
    Status,
    automatic_points,
    economic_points,
    judgment_points,
    load_system,
    offered_amount,
    reconcile,
    scene_points,
    score_offer,
    validate_system,
)

SPEC_PATH = Path(__file__).resolve().parents[3] / "datasets" / "scoring" / "lescala_2026_3964.json"
RAW = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
D = Decimal


@pytest.fixture
def escala():
    return load_system(copy.deepcopy(RAW))


# --------------------------------------------------------------------------- #
# 1. Suma exacta i estructura                                                   #
# --------------------------------------------------------------------------- #


def test_l_escala_suma_exactament_100_amb_40_32_28(escala):
    report = validate_system(escala)
    assert report.errors == []
    assert report.not_computable == []
    families = {n.id: n.max_points for n in escala.children(None)}
    assert families == {"ECO": D("40"), "AUT": D("32"), "JV": D("28")}
    leaves = escala.leaves()
    assert sum(n.max_points for n in leaves) == D("100")
    assert len([n for n in escala.children("ECO")]) == 4
    assert len([n for n in escala.children("AUT")]) == 15
    assert len([n for n in escala.children("JV")]) == 9
    assert [n.max_points for n in escala.children("JV.J6")] == [D(x) for x in "111121"]
    # 3 families + 28 subcriteris = 31 nodes de nivell 1-2; + 6 escenes de J6.
    assert len([n for n in escala.nodes if n.evaluation is not Evaluation.SCENE]) == 31


def test_una_suma_incorrecta_es_detecta():
    raw = copy.deepcopy(RAW)
    next(n for n in raw["nodes"] if n["id"] == "AUT.C1")["max_points"] = "6"
    errors = validate_system(load_system(raw)).errors
    assert "AUT: els fills sumen 33, no 32" in errors
    assert "les fulles sumen 101, no 100" in errors


def test_l_especificacio_refusa_camps_inventats():
    raw = copy.deepcopy(RAW)
    raw["nodes"][0]["weight_guess"] = "40%"
    with pytest.raises(SpecError):
        load_system(raw)


# --------------------------------------------------------------------------- #
# 2. Els duplicats no sumen                                                     #
# --------------------------------------------------------------------------- #


def test_els_duplicats_no_afegeixen_punts(escala):
    records = [
        {"id": "a", "points": "5"},
        {"id": "b", "points": "5"},
        {"id": "c", "points": "5"},
        {"id": "d", "points": "4"},
        {"id": "e", "points": None},
    ]
    assignments = {
        "a": {"role": "subcriterion", "nodes": ["AUT.C1"]},
        "b": {"role": "duplicate", "nodes": ["AUT.C1"]},
        "c": {"role": "duplicate", "nodes": ["AUT.C1"]},
        "d": {"role": "subcriterion", "nodes": ["AUT.C2a", "AUT.C2b"]},
        "e": {"role": "reference", "nodes": []},
    }
    result = reconcile(escala, records, assignments)
    assert result.problems == []
    assert result.raw_points_sum == D("19")
    assert result.canonical_points_sum == D("100")
    assert result.view["AUT.C1"] == {"canonical": ["a"], "duplicates": ["b", "c"], "references": []}
    assert result.view["AUT.C2a"]["canonical"] == ["d"] and result.view["AUT.C2b"]["canonical"] == [
        "d"
    ]
    assert result.canonical_records == 3 and result.by_role["duplicate"] == 2


def test_un_registre_amb_punts_que_no_quadren_es_marca(escala):
    result = reconcile(
        escala, [{"id": "x", "points": "3"}], {"x": {"role": "subcriterion", "nodes": ["AUT.C1"]}}
    )
    assert result.problems == ["x: 3 punts, pero AUT.C1 en val 5"]


# --------------------------------------------------------------------------- #
# 3. Formules contra les fonts oficials                                         #
# --------------------------------------------------------------------------- #


def test_exemple_oficial_de_la_formula_economica(escala):
    """DOC-03 p4 i Economia!A15: PL1=75.600; B=5%; Ov=71.820; Om=70.000; P=8 => Pv=7,81."""
    ov = offered_amount(escala, "ECO.B1", D("5"))
    assert ov == D("71820.00")
    assert economic_points(escala, "ECO.B1", ov, D("70000.00")) == D("7.81")


def test_formula_economica_limits(escala):
    assert economic_points(escala, "ECO.B4", D("20000.00"), D("20000.00")) == D(
        "20"
    )  # millor oferta: P
    assert economic_points(escala, "ECO.B2", D("90000.00"), D("10000.00")) == D("0")  # mai negativa
    with pytest.raises(ScoringInputInvalid):
        economic_points(escala, "ECO.B1", D("69000.00"), D("70000.00"))  # Ov < Om no te sentit
    with pytest.raises(ScoringInputInvalid):
        offered_amount(escala, "ECO.B1", D("-1"))
    with pytest.raises(ScoringInputInvalid):
        offered_amount(escala, "ECO.B1", D("5.12345"))  # mes de 4 decimals
    assert offered_amount(escala, "ECO.B3", D("12.5")) == D("57183.00")


def test_nivells_i_si_no_segons_el_pcap(escala):
    assert automatic_points(escala, "AUT.C3", {"levels": [15]}) == D("1")
    assert automatic_points(escala, "AUT.T1", {"levels": [40]}) == D("1.33")
    assert automatic_points(escala, "AUT.T1", {"levels": [30, 40]}) == D("0")  # mes d'una opcio
    with pytest.raises(ScoringInputInvalid):
        automatic_points(escala, "AUT.T1", {"levels": [35]})  # cap nivell intermedi inventat
    assert automatic_points(escala, "AUT.E2", {"yes": True}) == D("3")
    assert automatic_points(escala, "AUT.E2", {"yes": False}) == D("0")
    with pytest.raises(ScoringInputMissing):
        automatic_points(escala, "AUT.C1", {"yes": True})  # C1 requereix acreditacio
    assert automatic_points(escala, "AUT.C1", {"yes": True, "accredited": False}) == D("0")
    assert automatic_points(escala, "AUT.C1", {"yes": True, "accredited": True}) == D("5")


def test_judici_i_escenes_nomes_amb_valors_de_la_rubrica(escala):
    assert judgment_points(escala, "JV.J8", D("3")) == D("3")
    with pytest.raises(ScoringInputInvalid):
        judgment_points(escala, "JV.J1", D("2.5"))
    assert scene_points(escala, "JV.J6.S5", "minor") == D("1.00")
    assert scene_points(escala, "JV.J6.S1", "complete") == D("1.00")
    with pytest.raises(ScoringInputInvalid):
        scene_points(escala, "JV.J6.S1", "video")


# --------------------------------------------------------------------------- #
# 5-8. Camps presents o pendents, llindars separats, procedencia, cap puntuacio sense regla #
# --------------------------------------------------------------------------- #


def test_cada_fulla_te_la_regla_verificada_del_seu_tipus(escala):
    kinds = {
        Evaluation.ECONOMIC_FORMULA: RuleKind.FORMULA,
        Evaluation.YES_NO: RuleKind.YES_NO,
        Evaluation.LEVELS: RuleKind.LEVELS,
        Evaluation.JUDGMENT: RuleKind.RUBRIC,
        Evaluation.SCENE: RuleKind.SCENES,
    }
    for leaf in escala.leaves():
        rules = [
            escala.rule(r) for r in leaf.rules if escala.rule(r).kind is kinds[leaf.evaluation]
        ]
        assert rules and all(r.status is Status.VERIFIED for r in rules), leaf.id


def test_una_formula_absent_queda_marcada_i_no_es_calcula():
    raw = copy.deepcopy(RAW)
    next(r for r in raw["rules"] if r["id"] == "R.ECO.PV")["status"] = "pending"
    system = load_system(raw)
    report = validate_system(system)
    assert "ECO.B1: regla R.ECO.PV pending" in report.not_computable
    with pytest.raises(ScoringRuleMissing) as caught:
        economic_points(system, "ECO.B1", D("71820"), D("70000"))
    assert "ECO.B1: regla formula pending" in caught.value.missing


def test_el_llindar_d_anormalitat_es_una_regla_separada_que_no_puntua(escala):
    rule = escala.rule("R.ABNORMAL")
    assert rule.kind is RuleKind.THRESHOLD and rule.status is Status.VERIFIED
    assert rule.params == {
        "one_offer_pct": D("25"),
        "two_offers_pct": D("20"),
        "three_plus_pct": D("10"),
        "recalc_above_mean_pct": D("10"),
        "min_offers_recalc": D("3"),
    }
    assert all("R.ABNORMAL" not in n.rules for n in escala.nodes)


def test_tota_xifra_i_formula_te_procedencia_auditable(escala):
    for item in list(escala.nodes) + list(escala.rules):
        assert item.sources, item.id
        for source in item.sources:
            assert source.source in escala.documents
            assert source.page is not None or source.locator is not None, item.id
    for document in escala.documents.values():
        assert re.fullmatch(r"[0-9a-f]{64}", document["sha256"])


def test_una_oferta_sense_totes_les_regles_no_rep_puntuacio():
    raw = copy.deepcopy(RAW)
    next(r for r in raw["rules"] if r["id"] == "R.AUT.T1")["status"] = "conflict"
    with pytest.raises(ScoringRuleMissing) as caught:
        score_offer(load_system(raw), _complete_offer())
    assert caught.value.missing == ["AUT.T1: regla levels conflict"]


def test_una_oferta_sense_les_puntuacions_de_la_mesa_no_rep_puntuacio(escala):
    offer = _complete_offer()
    offer.pop("judgment")
    with pytest.raises(ScoringInputMissing) as caught:
        score_offer(escala, offer)
    assert len(caught.value.missing) == 8  # J1-J5, J7-J9


def test_una_oferta_completa_es_calcula_i_no_supera_100(escala):
    result = score_offer(escala, _complete_offer())
    assert result.by_node["ECO.B1"] == D("7.81")
    assert result.total <= D("100")
    assert len(result.by_node) == len(escala.leaves())


def _complete_offer() -> dict:
    return {
        "economic": {
            "ECO.B1": {"discount": "5", "lowest_admitted": "70000.00"},
            "ECO.B2": {"discount": "10", "lowest_admitted": "34493.47"},
            "ECO.B3": {"discount": "0", "lowest_admitted": "60000.00"},
            "ECO.B4": {"discount": "2", "lowest_admitted": "24455.58"},
        },
        "automatic": {
            **{
                k: {"yes": True}
                for k in (
                    "AUT.C2a",
                    "AUT.C2b",
                    "AUT.C4a",
                    "AUT.C4b",
                    "AUT.E1a",
                    "AUT.E1b",
                    "AUT.E2",
                    "AUT.E3",
                    "AUT.T2",
                    "AUT.T3",
                    "AUT.F1",
                    "AUT.F2",
                )
            },
            "AUT.C1": {"yes": True, "accredited": True},
            "AUT.C3": {"levels": [20]},
            "AUT.T1": {"levels": [50]},
        },
        "judgment": {
            "JV.J1": "3",
            "JV.J2": "2",
            "JV.J3": "1",
            "JV.J4": "2",
            "JV.J5": "2",
            "JV.J7": "1",
            "JV.J8": "4",
            "JV.J9": "3",
        },
        "scenes": {
            "JV.J6.S1": "complete",
            "JV.J6.S2": "complete",
            "JV.J6.S3": "minor",
            "JV.J6.S4": "complete",
            "JV.J6.S5": "minor",
            "JV.J6.S6": "none",
        },
    }


# --------------------------------------------------------------------------- #
# Contrast amb les fonts oficials (opcional)                                    #
# --------------------------------------------------------------------------- #

PAGES_DIR = os.environ.get("LESCALA_PAGES_DIR")
OFFICIAL_XLSX = os.environ.get("LESCALA_OFFICIAL_XLSX")
AUDIT_DIR = os.environ.get("LESCALA_AUDIT_DIR")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _pdf_pages(name: str) -> dict[int, str]:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    path = next(p for p in Path(PAGES_DIR).iterdir() if safe in p.name)
    text = path.read_text(encoding="utf-8")
    return {
        int(n): _squash(body)
        for n, body in re.findall(r"=+ PAGE (\d+) =+\n(.*?)(?=\n=+ PAGE |\Z)", text, re.S)
    }


@pytest.mark.skipif(not (PAGES_DIR and OFFICIAL_XLSX), reason="fonts oficials no disponibles")
def test_cada_cita_es_literal_a_la_pagina_o_cel_la_oficial(escala):
    from app.services import office_text

    pdf = {
        key: _pdf_pages(doc["name"].split(".pdf")[0])
        for key, doc in escala.documents.items()
        if key != "XLSX"
    }
    xlsx = {
        p.text.split("\n")[0].removeprefix("# Full: "): _squash(p.text)
        for p in office_text.extract_xlsx(Path(OFFICIAL_XLSX).read_bytes()).pages
    }
    missing = []
    for item in list(escala.nodes) + list(escala.rules):
        for source in item.sources:
            if source.source == "XLSX":
                sheet = source.locator.split("!")[0]
                found = _squash(source.quote) in xlsx.get(sheet, "")
            else:
                found = _squash(source.quote) in pdf[source.source].get(source.page, "")
            if not found:
                missing.append(
                    f"{item.id}: {source.source} {source.page or source.locator}: "
                    f"{source.quote[:60]}"
                )
    assert missing == []
    every = " ".join(xlsx.values())
    assert "35 punts" not in every and "— 25 punts" not in every


@pytest.mark.skipif(not AUDIT_DIR, reason="inventari del run 3 no disponible")
def test_reconciliacio_dels_97_registres_reals_de_l_escala(escala):
    records = json.loads(
        (Path(AUDIT_DIR) / "inventory" / "award_criteria.json").read_text(encoding="utf-8")
    )
    mapping = json.loads(
        (Path(AUDIT_DIR) / "review" / "D_criteria_review.json").read_text(encoding="utf-8")
    )["records"]
    assignments = {r["id"]: {"role": r["role"], "nodes": _nodes(r["node"])} for r in mapping}
    result = reconcile(escala, records, assignments)
    assert result.records == 97
    assert result.by_role == {"main": 3, "subcriterion": 25, "reference": 2, "duplicate": 67}
    assert result.raw_points_sum == D("635") and result.canonical_points_sum == D("100")
    assert result.nodes_missing == []
    assert result.problems == []


def _nodes(node: str) -> list[str]:
    """Claus del mapatge revisat: «AUT.C2a+AUT.C2b» (fusio), «ECO+AUT» (referencia).

    «ECO+AUT» es una referencia procedimental a dues families, sense punts propis.
    """
    return [] if not node or node == "none" else node.split("+")
