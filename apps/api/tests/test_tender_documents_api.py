"""Endpoints de la capa documental.

La lectura es fa DINS d'una empresa, com el processat: `visible_to_company`
inclou les licitacions sense propietari, aixi que un plec publicat —com el
d'aquests tests— es llegeix des de qualsevol empresa. El que no es pot fer es
llegir-ne un de privat d'una altra, i aixo ho prova
`test_tender_document_tenancy.py`.

El processat el demana l'empresa i l'executa el worker (`tests/jobs_support.py`).
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.fixtures.pdf_builder import build_pdf
from tests.jobs_support import request_processing

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


def _process_url(company_id, tender_document) -> str:
    return (
        f"/api/v1/companies/{company_id}/tenders/{tender_document.tender_id}"
        "/job-requests/process-documents"
    )


async def _process(client_a, tenant_a, tender_document, session_factory, **options):
    return await request_processing(
        client_a,
        tenant_a[1].id,
        tender_document.tender_id,
        session_factory,
        json={"document_ids": [str(tender_document.id)], **options},
    )


def _scoped(company_id) -> str:
    """Prefix de lectura documental. Sempre dins d'una empresa."""
    return f"/api/v1/companies/{company_id}"


def _docs_url(company_id, tender_document) -> str:
    return (
        f"/api/v1/companies/{company_id}/tenders/{tender_document.tender_id}"
        f"/documents/{tender_document.id}/files"
    )


async def _current_file(client_a, tenant_a, tender_document) -> dict:
    files = await client_a.get(_docs_url(tenant_a[1].id, tender_document))
    assert files.status_code == 200
    return next(item for item in files.json() if item["is_current"])


async def test_processing_requires_a_session(client, tender_document, tenant_a):
    response = await client.post(_process_url(tenant_a[1].id, tender_document))
    assert response.status_code == 401


async def test_the_old_synchronous_endpoint_no_longer_processes(client_a, tender_document):
    response = await client_a.post(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/process"
    )
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "endpoint_moved"


async def test_processes_a_document_and_returns_the_file(
    client_a, tender_document, tenant_a, session_factory
):
    final = await _process(client_a, tenant_a, tender_document, session_factory)

    assert final.json()["status"] == "succeeded"
    assert final.json()["outcome"] == "executed"
    file = await _current_file(client_a, tenant_a, tender_document)
    assert file["download_status"] == "downloaded"
    assert file["version_number"] == 1
    assert file["content"]["mime_type"] == "application/pdf"
    assert file["extraction"]["page_count"] == 3
    assert file["extraction"]["quality"] == "good"


async def test_no_response_ever_leaks_the_storage_key(
    client_a, tender_document, tenant_a, session_factory
):
    final = await _process(client_a, tenant_a, tender_document, session_factory)
    files = await client_a.get(
        _docs_url(tenant_a[1].id, tender_document)
    )
    assert "storage_key" not in final.text
    assert "storage_key" not in files.text


async def test_processing_twice_reports_reuse(
    client_a, tender_document, tenant_a, session_factory
):
    await _process(client_a, tenant_a, tender_document, session_factory)
    second = await _process(client_a, tenant_a, tender_document, session_factory)

    assert second.json()["status"] == "succeeded"
    assert second.json()["outcome"] == "reused"


async def test_lists_the_versions_of_a_document(
    client_a, tender_document, tenant_a, session_factory
):
    await _process(client_a, tenant_a, tender_document, session_factory)

    response = await client_a.get(
        _docs_url(tenant_a[1].id, tender_document)
    )

    assert response.status_code == 200
    files = response.json()
    assert len(files) == 1
    assert files[0]["is_current"] is True


async def test_reads_the_pages_of_the_current_extraction(
    client_a, tender_document, tenant_a, session_factory
):
    await _process(client_a, tenant_a, tender_document, session_factory)
    current = await _current_file(client_a, tenant_a, tender_document)
    file_id = current["id"]

    root = _scoped(tenant_a[1].id)
    response = await client_a.get(f"{root}/tender-document-files/{file_id}/pages")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [page["page_number"] for page in body["items"]] == [1, 2, 3]
    assert "clausules" in body["items"][0]["text"]


async def test_reads_one_page_by_its_human_number(
    client_a, tender_document, tenant_a, session_factory
):
    await _process(client_a, tenant_a, tender_document, session_factory)
    current = await _current_file(client_a, tenant_a, tender_document)
    file_id = current["id"]
    extraction_id = current["extraction"]["id"]

    pages = f"{_scoped(tenant_a[1].id)}/tender-document-extractions/{extraction_id}/pages"
    response = await client_a.get(f"{pages}/2")

    assert response.status_code == 200
    assert response.json()["page_number"] == 2

    missing = await client_a.get(f"{pages}/99")
    assert missing.status_code == 404

    # La pagina 0 no existeix: la numeracio comenca a 1 arreu.
    zero = await client_a.get(f"{pages}/0")
    assert zero.status_code == 422
    assert file_id


async def test_serves_the_original_pdf_inline(client_a, tender_document, tenant_a, session_factory):
    await _process(client_a, tenant_a, tender_document, session_factory)
    current = await _current_file(client_a, tenant_a, tender_document)
    file_id = current["id"]

    root = _scoped(tenant_a[1].id)
    response = await client_a.get(f"{root}/tender-document-files/{file_id}/original")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content == PCAP


async def test_unknown_identifiers_are_404(client_a, tender_document, tenant_a):
    missing = uuid.uuid4()
    root = _scoped(tenant_a[1].id)
    unknown = await client_a.get(f"{root}/tender-document-files/{missing}")
    assert unknown.status_code == 404
    unknown_document = await client_a.post(
        _process_url(tenant_a[1].id, tender_document), json={"document_ids": [str(missing)]}
    )
    assert unknown_document.status_code == 404


async def test_a_document_of_another_tender_is_not_reachable(client_a, tender_document, tenant_a):
    """El document ha de ser de la licitacio de la ruta."""
    response = await client_a.post(
        f"/api/v1/companies/{tenant_a[1].id}/tenders/{uuid.uuid4()}"
        "/job-requests/process-documents",
        json={"document_ids": [str(tender_document.id)]},
    )
    assert response.status_code == 404


async def test_reextract_without_content_is_reported_per_document(
    client_a, tender_document, tenant_a, session, session_factory
):
    """Ja no es un 422 de la peticio: la feina ho escriu a la fila del document.

    Forcar el processat en una licitacio PUBLICA es de l'operador (3B-2 §2.4,
    `public_tender_canonical_only`): aqui la licitacio es de l'empresa.
    """
    from app.models.tenders import Tender

    tender = await session.get(Tender, tender_document.tender_id)
    tender.owner_company_id = tenant_a[1].id
    await session.commit()
    final = await _process(
        client_a, tenant_a, tender_document, session_factory, force_reextract=True
    )

    assert final.json()["status"] == "succeeded"
    assert final.json()["outcome"] == "partial"
