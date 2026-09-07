"""Endpoints de la capa documental.

Son dades publiques i globals: cal sessio, pero no pertinenca a cap empresa.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.fixtures.pdf_builder import build_pdf

PCAP = build_pdf(["Plec de clausules administratives particulars. " * 8] * 3)


@pytest.fixture(autouse=True)
def _fake_network(monkeypatch):
    """Tot el router comparteix el mateix origen fals. Cap test toca Internet."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PCAP, headers={"content-type": "application/pdf"})

    from app.services import tender_document_processing as processing

    original = processing.download_document

    async def patched(url, *, client=None, resolver=None):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as fake:
            return await original(url, client=fake, resolver=lambda host, port: ["93.184.216.34"])

    monkeypatch.setattr(processing, "download_document", patched)


async def test_processing_requires_a_session(client, tender_document):
    response = await client.post(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    )
    assert response.status_code == 401


async def test_processes_a_document_and_returns_the_file(client_a, tender_document):
    response = await client_a.post(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reused"] is False
    file = body["file"]
    assert file["download_status"] == "downloaded"
    assert file["version_number"] == 1
    assert file["content"]["mime_type"] == "application/pdf"
    assert file["extraction"]["page_count"] == 3
    assert file["extraction"]["quality"] == "good"


async def test_no_response_ever_leaks_the_storage_key(client_a, tender_document):
    response = await client_a.post(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    )
    assert "storage_key" not in response.text


async def test_processing_twice_reports_reuse(client_a, tender_document):
    base = f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    await client_a.post(base)
    second = await client_a.post(base)

    assert second.json()["reused"] is True


async def test_lists_the_versions_of_a_document(client_a, tender_document):
    await client_a.post(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    )

    response = await client_a.get(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/files"
    )

    assert response.status_code == 200
    files = response.json()
    assert len(files) == 1
    assert files[0]["is_current"] is True


async def test_reads_the_pages_of_the_current_extraction(client_a, tender_document):
    processed = await client_a.post(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    )
    file_id = processed.json()["file"]["id"]

    response = await client_a.get(f"/api/v1/tender-document-files/{file_id}/pages")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [page["page_number"] for page in body["items"]] == [1, 2, 3]
    assert "clausules" in body["items"][0]["text"]


async def test_reads_one_page_by_its_human_number(client_a, tender_document):
    processed = await client_a.post(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    )
    file_id = processed.json()["file"]["id"]
    extraction_id = processed.json()["file"]["extraction"]["id"]

    response = await client_a.get(f"/api/v1/tender-document-extractions/{extraction_id}/pages/2")

    assert response.status_code == 200
    assert response.json()["page_number"] == 2

    missing = await client_a.get(f"/api/v1/tender-document-extractions/{extraction_id}/pages/99")
    assert missing.status_code == 404

    # La pagina 0 no existeix: la numeracio comenca a 1 arreu.
    zero = await client_a.get(f"/api/v1/tender-document-extractions/{extraction_id}/pages/0")
    assert zero.status_code == 422
    assert file_id


async def test_serves_the_original_pdf_inline(client_a, tender_document):
    processed = await client_a.post(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    )
    file_id = processed.json()["file"]["id"]

    response = await client_a.get(f"/api/v1/tender-document-files/{file_id}/original")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content == PCAP


async def test_unknown_identifiers_are_404(client_a, tender_document):
    missing = uuid.uuid4()
    assert (await client_a.get(f"/api/v1/tender-document-files/{missing}")).status_code == 404
    assert (
        await client_a.post(
            f"/api/v1/tenders/{tender_document.tender_id}/documents/{missing}/process"
        )
    ).status_code == 404


async def test_a_document_of_another_tender_is_not_reachable(client_a, tender_document):
    """El tender_id de la ruta ha de coincidir de veritat amb el del document."""
    response = await client_a.post(
        f"/api/v1/tenders/{uuid.uuid4()}/documents/{tender_document.id}/process"
    )
    assert response.status_code == 404


async def test_reextract_without_content_is_a_422(client_a, tender_document):
    response = await client_a.post(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process",
        json={"force_reextract": True},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_content_to_extract"
