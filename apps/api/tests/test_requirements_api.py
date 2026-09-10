"""Endpoints de l'analisi de plecs.

Dades publiques i globals: cal sessio, no pertinenca a cap empresa.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.ai.providers import fake
from app.models.enums import (
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
    TenderDocumentType,
)
from app.models.requirements import Requirement
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)

PAGE = (
    "8.1.2. Solvencia economica.\n"
    "El volumen anual de negocios debera ser al menos de 450.000 euros.\n"
)
QUOTE = "El volumen anual de negocios debera ser al menos de 450.000 euros"


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
async def analyzable(session, tender_document):
    tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS
    content = TenderDocumentContent(
        sha256="f" * 64,
        storage_key="tenders/documents/ff/ff/" + "f" * 64 + ".pdf",
        file_size=1024,
        mime_type="application/pdf",
        detected_extension="pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()
    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="pypdf-v1",
        revision_number=1,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=1,
        extracted_page_count=1,
        text_char_count=len(PAGE),
        is_current=True,
    )
    session.add(extraction)
    await session.flush()
    session.add(
        TenderDocumentPage(
            extraction_id=extraction.id,
            page_number=27,
            text=PAGE,
            char_count=len(PAGE),
            text_hash="h" * 64,
            status=PageExtractionStatus.EXTRACTED,
        )
    )
    session.add(
        TenderDocumentFile(
            tender_document_id=tender_document.id,
            content_id=content.id,
            source_url=tender_document.url,
            version_number=1,
            is_current=True,
            download_status="downloaded",
        )
    )
    await session.commit()
    await session.refresh(extraction)
    return {"document": tender_document, "extraction": extraction}


def payload(extraction_id):
    return {
        "requirements": [
            {
                "category": "economic_solvency",
                "obligation": "mandatory",
                "operator": "gte",
                "title": "Facturacio anual minima de 450.000 EUR",
                "value": {"amount": 450000, "currency": "EUR"},
                "confidence": "high",
                "evidence": [
                    {
                        "extraction_id": str(extraction_id),
                        "page_number": 27,
                        "quote": QUOTE,
                    }
                ],
            }
        ],
        "award_criteria": [],
    }


async def test_analysis_requires_a_session(client, analyzable):
    response = await client.post(
        f"/api/v1/tenders/{analyzable['document'].tender_id}/analysis/requirements"
    )
    assert response.status_code == 401


async def test_runs_the_analysis_and_returns_the_run(client_a, analyzable):
    fake.queue_response(payload(analyzable["extraction"].id))

    response = await client_a.post(
        f"/api/v1/tenders/{analyzable['document'].tender_id}/analysis/requirements"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reused"] is False
    run = body["run"]
    assert run["status"] == "completed"
    assert run["is_current"] is True
    assert run["requirements_extracted"] == 1
    assert run["provider"] == "fake"
    assert run["prompt_version"] == "requirements-v1"


async def test_reads_requirements_with_full_provenance(client_a, analyzable):
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    await client_a.post(f"/api/v1/tenders/{tender_id}/analysis/requirements")

    response = await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/requirements")

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["is_stale"] is False

    requirement = body["requirements"][0]
    assert requirement["category"] == "economic_solvency"
    assert requirement["obligation"] == "mandatory"
    assert requirement["value_json"]["amount"] == 450000

    # §58: la provenance no es opcional.
    cite = requirement["evidence"][0]
    assert cite["page_number"] == 27
    assert cite["quote"]
    assert cite["extraction_id"] == str(analyzable["extraction"].id)
    assert cite["extraction_revision"] == 1
    assert cite["char_start"] is not None and cite["char_end"] is not None


async def test_an_unanalysed_tender_returns_an_empty_analysis(client_a, analyzable):
    response = await client_a.get(
        f"/api/v1/tenders/{analyzable['document'].tender_id}/analysis/requirements"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run"] is None
    assert body["requirements"] == []


async def test_a_tender_without_text_returns_422(client_a, tender_document):
    response = await client_a.post(
        f"/api/v1/tenders/{tender_document.tender_id}/analysis/requirements"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "nothing_analyzable"


async def test_lists_the_run_history(client_a, analyzable):
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    await client_a.post(f"/api/v1/tenders/{tender_id}/analysis/requirements")
    fake.queue_response(payload(analyzable["extraction"].id))
    await client_a.post(
        f"/api/v1/tenders/{tender_id}/analysis/requirements", json={"force": True}
    )

    response = await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/runs")

    assert response.status_code == 200
    runs = response.json()
    assert len(runs) == 2
    assert sum(1 for run in runs if run["is_current"]) == 1, "nomes un run vigent"


async def test_reviewing_a_requirement_preserves_the_model_output(client_a, analyzable, session):
    """§26: corregir NO pot esborrar el que va dir el model."""
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    await client_a.post(f"/api/v1/tenders/{tender_id}/analysis/requirements")

    requirement = await session.scalar(
        sa.select(Requirement).where(Requirement.tender_id == tender_id)
    )
    original_title = requirement.title
    original_value = dict(requirement.value_json)

    response = await client_a.post(
        f"/api/v1/requirements/{requirement.id}/review",
        json={
            "review_status": "verified",
            "note": "Comprovat contra la pagina 27.",
            "manual_override": {"amount": 500000},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["review_status"] == "verified"
    assert body["review_note"] == "Comprovat contra la pagina 27."
    assert body["manual_override_json"] == {"amount": 500000}
    # L'output original es intacte.
    assert body["title"] == original_title
    assert body["value_json"] == original_value
    assert body["reviewed_at"] is not None


async def test_rejecting_a_requirement_keeps_it_auditable(client_a, analyzable, session):
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    await client_a.post(f"/api/v1/tenders/{tender_id}/analysis/requirements")
    requirement = await session.scalar(
        sa.select(Requirement).where(Requirement.tender_id == tender_id)
    )

    response = await client_a.post(
        f"/api/v1/requirements/{requirement.id}/review",
        json={"review_status": "rejected", "note": "No aplica a aquest lot."},
    )

    assert response.status_code == 200
    assert response.json()["review_status"] == "rejected"
    # Segueix existint: rebutjar no es esborrar.
    assert await session.get(Requirement, requirement.id) is not None


async def test_unknown_identifiers_are_404(client_a, analyzable):
    missing = uuid.uuid4()
    assert (await client_a.get(f"/api/v1/requirements/{missing}")).status_code == 404
    assert (await client_a.get(f"/api/v1/award-criteria/{missing}")).status_code == 404
    assert (
        await client_a.get(f"/api/v1/tenders/{missing}/analysis/requirements")
    ).status_code == 404


async def test_no_response_exposes_a_storage_key(client_a, analyzable):
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    await client_a.post(f"/api/v1/tenders/{tender_id}/analysis/requirements")

    response = await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/requirements")

    assert "storage_key" not in response.text
