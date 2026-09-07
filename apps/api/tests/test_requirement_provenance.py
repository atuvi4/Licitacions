"""Provenance, cache i resistencia a fallades del run.

Aquests tres blocs son la rao de ser de 4A.1. Si una cita pogues canviar de
significat despres d'haver-se fet, tota la cadena d'evidencia seria decorativa.
"""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa

from app.ai.providers import fake
from app.models.enums import AnalysisRunStatus, ReviewStatus
from app.models.requirements import Requirement, RequirementEvidence, RequirementExtractionRun
from app.models.tender_documents import TenderDocumentExtraction, TenderDocumentPage
from app.models.tenders import Tender
from app.services.requirement_analysis import analyze_tender_requirements
from app.services.tender_document_processing import process_tender_document
from tests.fixtures.pdf_builder import build_pdf

PUBLIC_IP = "93.184.216.34"
CLAUSE = (
    "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros. "
)
PCAP = build_pdf([CLAUSE * 6, "Criterios de adjudicacion. Oferta economica hasta 45 puntos. " * 6])
QUOTE = "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros"


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


def resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def serving(payload: bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "application/pdf"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.fixture
async def processed(session, tender_document):
    """Un document real processat de punta a punta: PDF -> pagines."""
    from app.models.enums import TenderDocumentType

    tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS
    await session.commit()

    async with serving(PCAP) as client:
        await process_tender_document(
            session, tender_document, client=client, resolver=resolver
        )
    return tender_document


async def current_extraction(session):
    return await session.scalar(
        sa.select(TenderDocumentExtraction).where(TenderDocumentExtraction.is_current.is_(True))
    )


def payload_for(extraction_id, page_number):
    return {
        "requirements": [
            {
                "category": "economic_solvency",
                "obligation": "mandatory",
                "operator": "gte",
                "title": "Volum anual de negoci minim de 450.000 EUR",
                "value": {"amount": 450000, "currency": "EUR"},
                "confidence": "high",
                "evidence": [
                    {
                        "extraction_id": str(extraction_id),
                        "page_number": page_number,
                        "quote": QUOTE,
                    }
                ],
            }
        ]
    }


# --------------------------------------------------------------------------- #
# §49 - Provenance                                                              #
# --------------------------------------------------------------------------- #


async def test_a_requirement_keeps_pointing_at_the_revision_that_produced_it(session, processed):
    """El test obligatori: reextreure el document NO mou la cita d'un requisit."""
    extraction_1 = await current_extraction(session)
    assert extraction_1.revision_number == 1

    page = await session.scalar(
        sa.select(TenderDocumentPage).where(
            TenderDocumentPage.extraction_id == extraction_1.id,
            TenderDocumentPage.page_number == 1,
        )
    )
    original_text_hash = page.text_hash

    fake.queue_response(payload_for(extraction_1.id, 1))
    tender = await session.get(Tender, processed.tender_id)
    await analyze_tender_requirements(session, tender)

    cite = await session.scalar(sa.select(RequirementEvidence))
    assert cite.extraction_id == extraction_1.id
    assert cite.page_id == page.id
    assert cite.page_number == 1

    # Ara es reextreu el document: apareix la revisio 2.
    await process_tender_document(session, processed, force_reextract=True)
    extraction_2 = await current_extraction(session)
    assert extraction_2.id != extraction_1.id
    assert extraction_2.revision_number == 2

    # La cita NO s'ha mogut.
    await session.refresh(cite)
    assert cite.extraction_id == extraction_1.id
    assert cite.page_id == page.id
    assert cite.page_number == 1

    # I la pagina que cita segueix existint i dient el mateix.
    await session.refresh(page)
    assert page.text_hash == original_text_hash
    assert QUOTE in page.text

    # La revisio 1 continua consultable encara que ja no sigui la vigent.
    await session.refresh(extraction_1)
    assert extraction_1.is_current is False


# --------------------------------------------------------------------------- #
# §50 - Cache                                                                   #
# --------------------------------------------------------------------------- #


async def test_the_same_input_and_config_reuses_the_run(session, processed):
    extraction = await current_extraction(session)
    fake.queue_response(payload_for(extraction.id, 1))

    tender = await session.get(Tender, processed.tender_id)
    first = await analyze_tender_requirements(session, tender)
    assert first.reused is False

    calls_before = len(fake.SENT_PAYLOADS)
    second = await analyze_tender_requirements(session, tender)

    assert second.reused is True
    assert second.run.id == first.run.id
    assert len(fake.SENT_PAYLOADS) == calls_before, "no s'ha de tornar a cridar el proveidor"
    assert await session.scalar(sa.select(sa.func.count()).select_from(RequirementExtractionRun)) == 1


async def test_force_creates_a_new_run(session, processed):
    extraction = await current_extraction(session)
    fake.queue_response(payload_for(extraction.id, 1))
    tender = await session.get(Tender, processed.tender_id)
    first = await analyze_tender_requirements(session, tender)

    fake.queue_response(payload_for(extraction.id, 1))
    second = await analyze_tender_requirements(session, tender, force=True)

    assert second.reused is False
    assert second.run.id != first.run.id
    await session.refresh(first.run)
    assert first.run.is_current is False
    assert second.run.is_current is True


async def test_a_new_prompt_version_invalidates_the_cache(session, processed, monkeypatch):
    extraction = await current_extraction(session)
    fake.queue_response(payload_for(extraction.id, 1))
    tender = await session.get(Tender, processed.tender_id)
    first = await analyze_tender_requirements(session, tender)

    monkeypatch.setattr(
        "app.services.requirement_analysis.PROMPT_VERSION", "requirements-v2"
    )
    fake.queue_response(payload_for(extraction.id, 1))
    second = await analyze_tender_requirements(session, tender)

    assert second.reused is False
    assert second.run.id != first.run.id
    assert second.run.prompt_version == "requirements-v2"


async def test_a_new_extraction_revision_invalidates_the_cache(session, processed):
    extraction = await current_extraction(session)
    fake.queue_response(payload_for(extraction.id, 1))
    tender = await session.get(Tender, processed.tender_id)
    first = await analyze_tender_requirements(session, tender)

    # Reextreure canvia el fingerprint: els textos analitzats ja no son els mateixos.
    await process_tender_document(session, processed, force_reextract=True)
    new_extraction = await current_extraction(session)
    fake.queue_response(payload_for(new_extraction.id, 1))

    second = await analyze_tender_requirements(session, tender)

    assert second.reused is False
    assert second.run.input_fingerprint != first.run.input_fingerprint


# --------------------------------------------------------------------------- #
# §51 - Fallada                                                                 #
# --------------------------------------------------------------------------- #


async def test_a_failed_run_does_not_dethrone_the_previous_one(session, processed):
    extraction = await current_extraction(session)
    fake.queue_response(payload_for(extraction.id, 1))
    tender = await session.get(Tender, processed.tender_id)
    good = (await analyze_tender_requirements(session, tender)).run
    assert good.requirements_extracted == 1

    requirement_id = (await session.scalar(sa.select(Requirement))).id

    fake.queue_error("el proveidor ha caigut")
    failed = (await analyze_tender_requirements(session, tender, force=True)).run

    assert failed.status == AnalysisRunStatus.FAILED
    assert failed.is_current is False

    await session.refresh(good)
    assert good.is_current is True, "l'analisi anterior segueix sent la vigent"

    # I els requisits antics no s'han esborrat.
    survivor = await session.get(Requirement, requirement_id)
    assert survivor is not None
    assert survivor.run_id == good.id
    assert survivor.review_status == ReviewStatus.UNREVIEWED
