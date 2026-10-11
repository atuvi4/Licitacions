"""Per que s'ha rebutjat una cita, sense desar cap text del plec.

Cas real (canary 2, 2026-10-11): al chunk 0 el model va retornar 4 requisits i 2 es van
rebutjar amb `quote_not_found`. La cobertura deia nomes l'index i el codi: no es podia saber
si la cita era a una altra pagina, si el model havia canviat lletres (accents) o si no hi era
enlloc. Ara cada rebuig de cita porta la pagina citada i un diagnostic de codis; l'element,
l'obligacio i la categoria. Les regles d'acceptacio NO canvien: una cita no literal es
continua rebutjant.

I les incerteses i notes que retorna el model per chunk (fins ara descartades) queden a la
cobertura nomes com a pagines, sense text.
"""

from __future__ import annotations

import pytest

from app.ai.providers import fake
from app.core.config import settings
from app.models.enums import AnalysisRunStatus
from app.models.requirements import Requirement
from app.models.tenders import Tender
from app.services.evidence_validation import diagnose_missing_quote
from app.services.requirement_analysis import analyze_tender_requirements
from tests.test_jobs import _add_extracted_pages, _page_text


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
def two_pages_one_chunk(monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 2)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)


PAGES = {
    2: "El contractista ha de disposar d'una assegurança de responsabilitat civil.",
    3: "Les avaries critiques s'han d'atendre en un termini maxim de 4 hores.",
}


@pytest.mark.parametrize(
    ("cited", "quote", "expected"),
    [
        (2, "termini maxim de 4 hores", "on_other_page:3"),
        (3, "termini màxim de 4 hores", "accent_variant"),
        (2, "termini màxim de 4 hores", "accent_variant_on_other_page:3"),
        (3, "responsabilitat civil", "on_other_page:2"),
        (2, "una asseguranca de responsabilitat", "accent_variant"),
        (3, "un text que no hi es enlloc", "absent"),
    ],
)
def test_el_diagnostic_d_una_cita_que_no_es_literal(cited, quote, expected):
    assert diagnose_missing_quote(PAGES, cited, quote) == expected


def _requirement(extraction_id, page: int, quote: str, *, obligation="mandatory") -> dict:
    return {
        "category": "economic_solvency",
        "obligation": obligation,
        "title": "Requisit de prova",
        "confidence": "high",
        "evidence": [{"extraction_id": str(extraction_id), "page_number": page, "quote": quote}],
    }


async def test_la_cobertura_diu_per_que_s_ha_rebutjat_sense_cap_text(
    session, tender_document, two_pages_one_chunk
):
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    extraction_id = plec["extraction"].id
    page2 = _page_text(2)
    literal = page2.split("\n")[0]
    accented = literal.replace("solvencia", "solvència")
    fake.queue_response(
        {
            "requirements": [
                _requirement(extraction_id, 2, literal),
                _requirement(extraction_id, 1, literal),  # pagina equivocada
                _requirement(extraction_id, 2, accented, obligation="unknown"),  # lletres canviades
            ],
            "uncertainties": [
                {"note": "La classificacio es determinara mes endavant", "page_number": 2}
            ],
            "document_notes": [{"note": "Text sintetic", "page_number": 1}],
        }
    )

    tender = await session.get(Tender, tender_document.tender_id)
    run = (await analyze_tender_requirements(session, tender)).run

    assert run.status == AnalysisRunStatus.COMPLETED
    assert run.requirements_extracted == 1  # nomes la cita literal: cap regla relaxada
    (chunk,) = run.chunk_coverage["chunks"]
    rejections = chunk["rejections"]
    evidence = [r for r in rejections if r["stage"] == "evidence"]
    items = [r for r in rejections if r["stage"] == "item"]
    assert [(r["index"], r["cited_page"], r["diagnosis"]) for r in evidence] == [
        (1, 1, "on_other_page:2"),
        (2, 2, "accent_variant"),
    ]
    assert [(r["index"], r["obligation"], r["category"]) for r in items] == [
        (1, "mandatory", "economic_solvency"),
        (2, "unknown", "economic_solvency"),
    ]
    assert chunk["notes"] == {"uncertainty_pages": [2], "document_note_pages": [1]}
    # Cap text del plec ni del model a la cobertura.
    flat = str(run.chunk_coverage)
    assert literal not in flat and "determinara" not in flat and "Requisit de prova" not in flat
    assert (
        await session.scalar(
            __import__("sqlalchemy").select(__import__("sqlalchemy").func.count(Requirement.id))
        )
        == 1
    )


async def test_un_chunk_sense_incerteses_no_porta_notes(
    session, tender_document, two_pages_one_chunk
):
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    literal = _page_text(1).split("\n")[0]
    fake.queue_response({"requirements": [_requirement(plec["extraction"].id, 1, literal)]})

    tender = await session.get(Tender, tender_document.tender_id)
    run = (await analyze_tender_requirements(session, tender)).run

    (chunk,) = run.chunk_coverage["chunks"]
    assert chunk["notes"] is None
    assert chunk["rejections"] is None
