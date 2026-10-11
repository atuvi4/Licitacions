"""L'aturada per error fatal (fail-fast) nomes marca els chunks que s'haurien enviat.

Cas real (primer canary, 2026-10-11): un canari d'un sol chunk (`only_chunks={0}`)
va rebre un 401 al chunk 0. El fail-fast va marcar com a `not_sent` els chunks 1 i 2,
que ni tan sols estaven seleccionats, i el run va dir «3 de 3 chunks fallits». La
veritat: 1 enviat i fallit, 2 no seleccionats. El mateix passaria amb un chunk ja
llegit al run d'origen d'una represa: no es torna a enviar mai, i no es cap fallada.
"""

from __future__ import annotations

import pytest

from app.ai.provider import ProviderError
from app.ai.providers import fake
from app.core.config import settings
from app.models.enums import AnalysisRunStatus
from app.models.tenders import Tender
from app.services.requirement_analysis import analyze_tender_requirements
from tests.test_analysis_resume import _req
from tests.test_jobs import _add_extracted_pages


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
def one_page_chunks(monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)


def _http_error(status: int, *, fatal: bool) -> ProviderError:
    return ProviderError(
        f"HTTP {status} del proveidor (authentication_error).",
        "provider_error",
        details={
            "status": status,
            "type": "authentication_error",
            "message": "invalid x-api-key",
            "request_id": "req_011Test",
        },
        fatal=fatal,
    )


async def _tender(session, document) -> Tender:
    return await session.get(Tender, document.tender_id)


async def test_un_canari_que_cau_per_error_fatal_no_compta_els_no_seleccionats(
    session, tender_document, one_page_chunks
):
    await _add_extracted_pages(session, tender_document, pages=3)
    fake.queue_response(_http_error(401, fatal=True))

    run = (
        await analyze_tender_requirements(
            session, await _tender(session, tender_document), only_chunks=frozenset({0})
        )
    ).run

    assert len(fake.SENT_PAYLOADS) == 1
    assert run.status == AnalysisRunStatus.FAILED
    assert (run.chunks_total, run.chunks_completed, run.chunks_failed) == (3, 0, 1)
    assert run.error_code == "all_selected_chunks_failed"
    first, second, third = run.chunk_coverage["chunks"]
    assert first["error"] == "provider_error"
    assert first["provider_error"]["status"] == 401
    assert first["origin"] == "sent"
    assert [second["error"], third["error"]] == ["not_selected", "not_selected"]
    assert "not_sent" not in run.error_message
    assert run.is_current is False


async def test_sense_seleccio_un_error_fatal_continua_marcant_la_resta_not_sent(
    session, tender_document, one_page_chunks
):
    """El comportament d'una analisi sencera no canvia."""
    await _add_extracted_pages(session, tender_document, pages=3)
    fake.queue_response(_http_error(401, fatal=True))

    run = (await analyze_tender_requirements(session, await _tender(session, tender_document))).run

    assert len(fake.SENT_PAYLOADS) == 1
    assert (run.chunks_total, run.chunks_completed, run.chunks_failed) == (3, 0, 3)
    assert [chunk["error"] for chunk in run.chunk_coverage["chunks"]] == [
        "provider_error",
        "not_sent",
        "not_sent",
    ]


async def test_una_represa_que_cau_per_error_fatal_no_marca_not_sent_un_chunk_reaprofitat(
    session, tender_document, one_page_chunks
):
    plec = await _add_extracted_pages(session, tender_document, pages=3)
    extraction_id = plec["extraction"].id
    tender = await _tender(session, tender_document)
    # Run d'origen: el chunk 0 cau (no fatal), els 1 i 2 es llegeixen be.
    fake.queue_many(
        [
            _http_error(500, fatal=False),
            {"requirements": [_req(extraction_id, 2)]},
            {"requirements": [_req(extraction_id, 3)]},
        ]
    )
    source = (await analyze_tender_requirements(session, tender)).run
    assert source.status == AnalysisRunStatus.PARTIAL
    fake.reset()

    # Represa: nomes s'envia el chunk 0, i cau amb un error fatal.
    fake.queue_response(_http_error(401, fatal=True))
    run = (await analyze_tender_requirements(session, tender, resume_from_run_id=source.id)).run

    assert len(fake.SENT_PAYLOADS) == 1
    assert run.chunks_failed == 1
    first, second, third = run.chunk_coverage["chunks"]
    assert first["error"] == "provider_error"
    assert second["origin"] == "reused" and third["origin"] == "reused"
    assert second.get("error") != "not_sent" and third.get("error") != "not_sent"
