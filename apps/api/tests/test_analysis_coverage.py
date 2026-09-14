"""Cobertura d'analisi: quines pagines va llegir el model, i que se'n dedueix.

El cas que va obligar a escriure aixo: un run del perfil v2 va completar 5 de 8
chunks i el benchmark va comptar com a "requisit perdut pel model" un requisit
que vivia en una pagina que no s'havia analitzat mai. El que es protegeix aqui
es que aquella confusio no pugui tornar.
"""

from __future__ import annotations

import pytest

from app.ai.provider import Usage
from app.evaluation.metrics import EvaluationSummary
from app.evaluation.report import AWARD_METRICS_UNAVAILABLE, warnings_for
from app.evaluation.runner import split_by_coverage
from app.services.analysis_coverage import (
    analyzed_pages,
    build_coverage,
    coverage_summary,
    covers,
)
from app.services.requirement_chunking import AnalyzableDocument, Chunk, PageRef


def _document(name: str, pages: int) -> AnalyzableDocument:
    return AnalyzableDocument(
        tender_document_id=f"doc-{name}",
        document_name=name,
        document_type="administrative_terms",
        extraction_id="ext-1",
        revision_number=1,
        parser_version="p1",
        content_sha256="sha",
        pages=tuple(
            PageRef(page_id=f"p{n}", page_number=n, text=f"text {n}") for n in range(1, pages + 1)
        ),
    )


def _chunks(document: AnalyzableDocument, size: int = 3) -> list[Chunk]:
    return [
        Chunk(index=i, document=document, pages=document.pages[i * size : (i + 1) * size])
        for i in range((len(document.pages) + size - 1) // size)
    ]


class _Gold:
    def __init__(self, gold_id: str, document_name: str, page_number: int) -> None:
        self.gold_id = gold_id
        self.document_name = document_name
        self.page_number = page_number
        self.category = "economic_solvency"


def test_coverage_distingeix_chunk_caigut_de_chunk_analitzat() -> None:
    doc = _document("PCAP", 9)
    coverage = build_coverage(
        _chunks(doc), failures={1: "invalid_structured_output"}, pages_per_chunk=3, overlap_pages=0
    )
    pages = analyzed_pages(coverage)
    assert pages == {"PCAP": {1, 2, 3, 7, 8, 9}}
    # Les pagines del chunk 1 (4, 5, 6) no van arribar al model.
    assert covers(coverage, "PCAP", 3) is True
    assert covers(coverage, "PCAP", 5) is False


def test_pagina_del_solapament_compta_si_algun_chunk_va_be() -> None:
    """Una pagina viu en dos chunks; que en caigui un no vol dir que no es llegis."""
    doc = _document("PCAP", 4)
    chunks = [
        Chunk(index=0, document=doc, pages=doc.pages[0:3]),
        Chunk(index=1, document=doc, pages=doc.pages[2:4], overlap_page_numbers=frozenset({3})),
    ]
    coverage = build_coverage(chunks, failures={1: "boom"}, pages_per_chunk=3, overlap_pages=1)
    assert covers(coverage, "PCAP", 3) is True
    assert covers(coverage, "PCAP", 4) is False


def test_cobertura_desconeguda_no_es_cobertura() -> None:
    """Un run anterior a la instrumentacio no afirma res sobre cap pagina."""
    assert analyzed_pages(None) is None
    assert covers(None, "PCAP", 9) is None
    assert coverage_summary(None) == {"known": False}
    # I un document que no surt al pla de chunks d'aquell run tampoc.
    doc = _document("PCAP", 3)
    coverage = build_coverage(_chunks(doc), failures={}, pages_per_chunk=3, overlap_pages=0)
    assert covers(coverage, "PPT", 1) is None


def test_gold_fora_de_cobertura_queda_fora_de_les_metriques() -> None:
    doc = _document("PCAP", 9)
    coverage = build_coverage(
        _chunks(doc), failures={1: "invalid_structured_output"}, pages_per_chunk=3, overlap_pages=0
    )
    gold = [_Gold("eco-1", "PCAP", 5), _Gold("ins-1", "PCAP", 2)]
    measurable, unmeasurable = split_by_coverage(gold, coverage)

    # `eco-1` cita una pagina que el model no va veure: no es un error del model.
    assert [g.gold_id for g in measurable] == ["ins-1"]
    assert unmeasurable == [
        {
            "gold_id": "eco-1",
            "kind": "requirement",
            "is_critical": False,
            "category": "economic_solvency",
            "document_name": "PCAP",
            "page_number": 5,
            "reason": "page_not_analysed",
        }
    ]


def test_sense_cobertura_coneguda_no_s_exclou_res() -> None:
    """No saber si una pagina es va llegir no es saber que no es va llegir."""
    gold = [_Gold("eco-1", "PCAP", 5)]
    measurable, unmeasurable = split_by_coverage(gold, None)
    assert [g.gold_id for g in measurable] == ["eco-1"]
    assert unmeasurable == []


def test_resum_de_cobertura_per_a_l_informe() -> None:
    doc = _document("PCAP", 9)
    coverage = build_coverage(
        _chunks(doc), failures={1: "invalid_structured_output"}, pages_per_chunk=3, overlap_pages=0
    )
    resum = coverage_summary(coverage)
    assert resum["known"] is True
    assert (resum["chunks_analyzed"], resum["chunks_total"]) == (2, 3)
    assert (resum["pages_analyzed"], resum["pages_total"]) == (6, 9)
    assert resum["pages_missing"] == ["PCAP p.4", "PCAP p.5", "PCAP p.6"]
    assert resum["errors"] == ["invalid_structured_output"]


def test_cobertura_completa_tambe_es_desa() -> None:
    """'Tot analitzat' ha de ser comprovable, no deduit de l'absencia d'errors."""
    doc = _document("PCAP", 6)
    coverage = build_coverage(_chunks(doc), failures={}, pages_per_chunk=3, overlap_pages=0)
    resum = coverage_summary(coverage)
    assert resum["chunks_failed"] == 0
    assert resum["pages_analyzed"] == resum["pages_total"] == 6
    assert covers(coverage, "PCAP", 6) is True


@pytest.mark.parametrize("page", [1, 2, 3])
def test_document_amb_tots_els_chunks_caiguts(page: int) -> None:
    doc = _document("PCAP", 3)
    coverage = build_coverage(
        _chunks(doc), failures={0: "boom"}, pages_per_chunk=3, overlap_pages=0
    )
    # El document surt amb zero pagines: "analitzat, res" i "no hi era" no son
    # el mateix, i nomes el primer permet excloure el gold amb fonament.
    assert analyzed_pages(coverage) == {"PCAP": set()}
    assert covers(coverage, "PCAP", page) is False


def test_el_resum_suma_els_tokens_dels_chunks_caiguts() -> None:
    doc = _document("PCAP", 9)
    usage = {
        0: Usage(input_tokens=1000, output_tokens=400, request_id="r0", stop_reason="tool_use"),
        1: Usage(input_tokens=1200, output_tokens=8000, request_id="r1", stop_reason="max_tokens"),
    }
    coverage = build_coverage(
        _chunks(doc),
        failures={1: "invalid_structured_output", 2: "provider_error"},
        usage=usage,
        pages_per_chunk=3,
        overlap_pages=0,
    )
    resum = coverage_summary(coverage)
    # El chunk 2 no va tenir resposta: no suma res, pero tampoc s'hi inventa un zero.
    assert coverage["chunks"][2]["input_tokens"] is None
    assert (resum["failed_input_tokens"], resum["failed_output_tokens"]) == (1200, 8000)
    assert resum["failed_stop_reasons"] == ["max_tokens"]


def _summary_with(unmeasurable: list[dict]) -> EvaluationSummary:
    summary = EvaluationSummary(
        dataset_id="t", provider="fake", model="m",
        prompt_version="requirements-v1", extractor_version="e", schema_version="s",
    )
    summary.coverage = {"verified": 3, "cases_with_verified": 10}
    summary.unmeasurable = unmeasurable
    return summary


def test_un_run_caigut_no_diu_que_el_gold_no_te_critics() -> None:
    """Denominador buit per cobertura no es denominador buit per gold."""
    summary = _summary_with(
        [
            {"gold_id": "eco-1", "kind": "requirement", "is_critical": True},
            {"gold_id": "award-1", "kind": "award_criterion", "is_critical": False},
        ]
    )
    alerts = warnings_for(summary, [])
    assert not any("No hi ha cap requisit critic al gold" in a for a in alerts)
    assert AWARD_METRICS_UNAVAILABLE not in alerts
    assert any("Recall critic NO mesurat: 1" in a for a in alerts)
    assert any("Criteris d'adjudicacio NO mesurats: 1" in a for a in alerts)


def test_sense_exclusions_el_missatge_de_gold_buit_es_mante() -> None:
    alerts = warnings_for(_summary_with([]), [])
    assert any("No hi ha cap requisit critic al gold" in a for a in alerts)
    assert AWARD_METRICS_UNAVAILABLE in alerts
