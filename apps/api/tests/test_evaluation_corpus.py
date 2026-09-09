"""Quin document mira un cas del gold: n'hi ha d'haver una sola resposta.

Aixo existeix per un error real trobat al preflight de 4D.2B. La regla estava
escrita dues vegades —al benchmark i a la pagina de revisio humana— i havien
divergit: una resolia per `tender_external_id` i l'altra per expedient.

L'expedient NO es unic. Al corpus real, "1/2026" el fan servir tres organs de
contractacio diferents. Amb les dues regles divergides, una persona verificava
una cita contra el document d'un organ i el benchmark n'hauria mesurat un altre:
la xifra resultant hauria semblat una mesura del model quan mesurava una
resolucio incorrecta del corpus.
"""

from __future__ import annotations

import uuid

import pytest

from app.evaluation.corpus import resolve_tender
from app.evaluation.dataset import GoldCase
from app.evaluation.review_page import page_texts

pytestmark = pytest.mark.anyio


async def _tender(session, *, external_id: str, expediente: str, title: str):
    from app.models.sources import Source
    from app.models.tenders import Tender

    source = Source(code=f"test-{uuid.uuid4().hex[:8]}", name="Font de prova")
    session.add(source)
    await session.flush()
    tender = Tender(
        source_id=source.id,
        external_id=external_id,
        expediente_number=expediente,
        title=title,
    )
    session.add(tender)
    await session.flush()
    return tender


async def test_the_external_id_wins_over_a_shared_expediente(session):
    """Dos organs amb el mateix numero d'expedient no son el mateix contracte.

    L'identificador a la font es unic; el numero d'expedient el posa cada organ
    pel seu compte. Resoldre per expedient portaria al plec d'una altra
    licitacio, i el gold quedaria verificat contra un document i mesurat contra
    un altre.
    """
    shared = "1/2026"
    other = await _tender(
        session, external_id="https://font/aaa", expediente=shared, title="Retolacio"
    )
    mine = await _tender(
        session, external_id="https://font/bbb", expediente=shared, title="Neteja"
    )
    await session.commit()

    case = GoldCase(case_id="c", expediente_number=shared, tender_external_id="https://font/bbb")
    resolved = await resolve_tender(session, case)

    assert resolved is not None
    assert resolved.id == mine.id
    assert resolved.id != other.id


async def test_without_an_external_id_the_fallback_is_deterministic(session):
    """Sense identificador extern es tria per expedient, pero sempre el mateix.

    Un `SELECT` sense ordre pot retornar files diferents en execucions diferents.
    Amb la mateixa entrada, el benchmark ha de donar el mateix resultat.
    """
    shared = "9/2026"
    await _tender(session, external_id="https://font/x1", expediente=shared, title="Un")
    await _tender(session, external_id="https://font/x2", expediente=shared, title="Dos")
    await session.commit()

    case = GoldCase(case_id="c", expediente_number=shared)
    first = await resolve_tender(session, case)
    second = await resolve_tender(session, case)

    assert first is not None
    assert first.id == second.id


async def test_the_review_page_reads_the_same_tender_as_the_benchmark(session):
    """La cita s'ha de verificar contra el document que despres es mesurara.

    Si la pagina de revisio ensenya el text d'un altre tender homonim, la
    verificacio humana deixa de dir el que sembla que diu.
    """
    from app.evaluation.dataset import GoldDataset, GoldRequirement
    from app.models.enums import RequirementCategory, RequirementObligation

    shared = "1/2026"
    decoy = await _tender(
        session, external_id="https://font/decoy", expediente=shared, title="Retolacio"
    )
    target = await _tender(
        session, external_id="https://font/target", expediente=shared, title="Neteja"
    )
    await _page(session, decoy, "Plec", 5, "text del contracte de retolacio")
    await _page(session, target, "Plec", 5, "text del contracte de neteja")
    await session.commit()

    dataset = GoldDataset(
        dataset_id="tmp",
        version=1,
        created_at=__import__("datetime").date(2026, 1, 1),
        description="",
        cases=[
            GoldCase(
                case_id="c",
                expediente_number=shared,
                tender_external_id="https://font/target",
                requirements=[
                    GoldRequirement(
                        gold_id="r1",
                        category=RequirementCategory.OTHER,
                        obligation=RequirementObligation.MANDATORY,
                        document_name="Plec",
                        page_number=5,
                        quote="neteja",
                    )
                ],
            )
        ],
    )

    texts = await page_texts(session, dataset)

    assert texts[("c", "Plec", 5)] == "text del contracte de neteja"
    assert "retolacio" not in texts[("c", "Plec", 5)]
    assert decoy.id != target.id


async def _page(session, tender, document_name: str, page_number: int, text: str):
    """Un document amb una pagina de text ja extreta."""
    import hashlib

    from app.models.enums import (
        ExtractionQuality,
        ExtractionStatus,
        PageExtractionStatus,
        TenderDocumentType,
    )
    from app.models.tender_documents import (
        TenderDocumentContent,
        TenderDocumentExtraction,
        TenderDocumentFile,
        TenderDocumentPage,
    )
    from app.models.tenders import TenderDocument

    url = f"https://example.com/{uuid.uuid4().hex[:8]}.pdf"
    document = TenderDocument(
        tender_id=tender.id,
        name=document_name,
        document_type=TenderDocumentType.OTHER,
        url=url,
    )
    session.add(document)
    await session.flush()

    digest = hashlib.sha256(text.encode()).hexdigest()
    content = TenderDocumentContent(
        sha256=digest,
        storage_key=f"test/{digest[:16]}.pdf",
        file_size=len(text),
        mime_type="application/pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()

    session.add(
        TenderDocumentFile(
            tender_document_id=document.id,
            content_id=content.id,
            source_url=url,
            version_number=1,
            is_current=True,
            download_status="downloaded",
        )
    )
    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="test",
        revision_number=1,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=1,
        extracted_page_count=1,
        empty_page_count=0,
        text_char_count=len(text),
        ocr_candidate=False,
        is_current=True,
    )
    session.add(extraction)
    await session.flush()
    session.add(
        TenderDocumentPage(
            extraction_id=extraction.id,
            page_number=page_number,
            text=text,
            char_count=len(text),
            text_hash="h" * 64,
            status=PageExtractionStatus.EXTRACTED,
        )
    )
    await session.flush()
