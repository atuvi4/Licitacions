"""Endpoints de l'analisi de plecs.

La lectura es publica i global: cal sessio, no pertinenca a cap empresa.
L'analisi es demana per empresa i l'executa el worker (`tests/jobs_support.py`).
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
from app.models.tenders import Tender
from app.services.requirement_analysis import analyze_tender_requirements
from tests.jobs_support import request_analysis
from tests.real_provider import RealNamedFake

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


async def test_analysis_requires_a_session(client, analyzable, tenant_a):
    _, company = tenant_a
    response = await client.post(
        f"/api/v1/companies/{company.id}/tenders/{analyzable['document'].tender_id}"
        "/job-requests/analyze-requirements"
    )
    assert response.status_code == 401


async def test_the_old_synchronous_endpoint_no_longer_analyses(client_a, analyzable):
    """Una peticio HTTP no pot quedar-se oberta minuts: l'endpoint antic es retira."""
    response = await client_a.post(
        f"/api/v1/tenders/{analyzable['document'].tender_id}/analysis/requirements"
    )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "endpoint_moved"
    assert fake.SENT_PAYLOADS == [], "no s'ha cridat el proveidor"


async def test_runs_the_analysis_and_returns_the_run(
    client_a, analyzable, tenant_a, session_factory
):
    fake.queue_response(payload(analyzable["extraction"].id))
    _, company = tenant_a
    tender_id = analyzable["document"].tender_id

    final = await request_analysis(client_a, company.id, tender_id, session_factory)

    body = final.json()
    assert body["status"] == "succeeded"
    assert body["outcome"] == "executed"
    # Amb `LLM_PROVIDER=fake` el run es una simulacio: queda a l'historial, pero mai
    # no es la base vigent (3B-2 §6.3) i la lectura de l'analisi vigent es buida.
    analysis = (await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/requirements")).json()
    assert analysis["run"] is None
    runs = (await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/runs")).json()
    (run,) = runs
    assert run["id"] == body["result_run_id"]
    assert run["status"] == "completed"
    assert (run["is_current"], run["run_basis"], run["needs_review"]) == (
        False,
        "simulation",
        False,
    )
    assert run["requirements_extracted"] == 1
    assert run["provider"] == "fake"
    assert run["prompt_version"] == "requirements-v1"


async def test_reads_requirements_with_full_provenance(
    client_a, analyzable, tenant_a, session_factory
):
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    # La lectura nomes ensenya la run vigent, i nomes una run real ho pot ser
    # (3B-2 §6.3): proveidor de nom real injectat, cap xarxa.
    async with session_factory() as session:
        tender = await session.get(Tender, tender_id)
        run = (
            await analyze_tender_requirements(session, tender, provider=RealNamedFake())
        ).run
        assert run.is_current

    response = await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/requirements")

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["run"]["run_basis"] == "real"
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


async def test_a_tender_without_text_returns_422(client_a, tender_document, tenant_a):
    response = await client_a.post(
        f"/api/v1/companies/{tenant_a[1].id}/tenders/{tender_document.tender_id}"
        "/analysis-quotes"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "nothing_analyzable"


async def test_lists_the_run_history(client_a, analyzable, tenant_a, session_factory, session):
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    await request_analysis(client_a, tenant_a[1].id, tender_id, session_factory)
    # En una licitacio publica, `force` nomes el fa l'operador (3B-2 §6.4): l'empresa
    # rep 422 i el segon run el llanca la via d'operador.
    refused = await client_a.post(
        f"/api/v1/companies/{tenant_a[1].id}/tenders/{tender_id}/analysis-quotes",
        json={"force": True},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "public_tender_canonical_only"
    fake.queue_response(payload(analyzable["extraction"].id))
    await analyze_tender_requirements(
        session, await session.get(Tender, tender_id), force=True, provider=fake.FakeProvider()
    )

    response = await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/runs")

    assert response.status_code == 200
    runs = response.json()
    assert len(runs) == 2
    # Dues simulacions (`LLM_PROVIDER=fake`): cap no es vigent (3B-2 §6.3).
    assert sum(1 for run in runs if run["is_current"]) == 0
    assert {run["run_basis"] for run in runs} == {"simulation"}


async def _review_fields(session, requirement_id):
    session.expire_all()
    row = (
        await session.execute(
            sa.select(
                Requirement.review_status,
                Requirement.review_note,
                Requirement.manual_override_json,
                Requirement.reviewed_by,
                Requirement.reviewed_at,
                Requirement.title,
                Requirement.value_json,
            ).where(Requirement.id == requirement_id)
        )
    ).one()
    return tuple(row)


async def test_legacy_requirement_review_is_gone_and_preserves_the_model_output(
    client_a, analyzable, session, tenant_a, session_factory
):
    """El POST legacy esta retirat (410): no escriu res, l'output del model es intacte."""
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    await request_analysis(client_a, tenant_a[1].id, tender_id, session_factory)

    requirement = await session.scalar(
        sa.select(Requirement).where(Requirement.tender_id == tender_id)
    )
    requirement_id = requirement.id
    before = await _review_fields(session, requirement_id)

    response = await client_a.post(
        f"/api/v1/requirements/{requirement_id}/review",
        json={
            "review_status": "verified",
            "note": "Comprovat contra la pagina 27.",
            "manual_override": {"amount": 500000},
        },
    )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "endpoint_moved"
    assert await _review_fields(session, requirement_id) == before


async def test_legacy_rejecting_a_requirement_is_gone_and_keeps_it(
    client_a, analyzable, session, tenant_a, session_factory
):
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    await request_analysis(client_a, tenant_a[1].id, tender_id, session_factory)
    requirement = await session.scalar(
        sa.select(Requirement).where(Requirement.tender_id == tender_id)
    )
    requirement_id = requirement.id
    before = await _review_fields(session, requirement_id)

    response = await client_a.post(
        f"/api/v1/requirements/{requirement_id}/review",
        json={"review_status": "rejected", "note": "No aplica a aquest lot."},
    )

    assert response.status_code == 410
    assert await _review_fields(session, requirement_id) == before
    assert await session.get(Requirement, requirement_id) is not None


async def test_unknown_identifiers_are_404(client_a, analyzable):
    missing = uuid.uuid4()
    assert (await client_a.get(f"/api/v1/requirements/{missing}")).status_code == 404
    assert (await client_a.get(f"/api/v1/award-criteria/{missing}")).status_code == 404
    assert (
        await client_a.get(f"/api/v1/tenders/{missing}/analysis/requirements")
    ).status_code == 404


async def test_no_response_exposes_a_storage_key(
    client_a, analyzable, tenant_a, session_factory
):
    fake.queue_response(payload(analyzable["extraction"].id))
    tender_id = analyzable["document"].tender_id
    await request_analysis(client_a, tenant_a[1].id, tender_id, session_factory)

    response = await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/requirements")

    assert "storage_key" not in response.text
