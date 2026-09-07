"""Invariants d'esquema de la capa documental.

Els que importen: les pagines son 1-based, els plecs son globals (cap
company_id), i els indexs unics parcials fan de lock i de garantia de
deduplicacio.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)


def make_content(sha: str) -> TenderDocumentContent:
    return TenderDocumentContent(
        sha256=sha,
        storage_key=f"tenders/documents/{sha[0:2]}/{sha[2:4]}/{sha}.pdf",
        file_size=1024,
        mime_type="application/pdf",
        detected_extension="pdf",
        is_text_extractable=True,
    )


def make_extraction(
    content_id: uuid.UUID, version: str = "pypdf-v1", revision: int = 1
) -> TenderDocumentExtraction:
    return TenderDocumentExtraction(
        content_id=content_id,
        parser_version=version,
        revision_number=revision,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=1,
        extracted_page_count=1,
        empty_page_count=0,
        text_char_count=100,
        ocr_candidate=False,
        is_current=True,
    )


def test_the_documental_tables_are_global():
    """Si 100 empreses miren la mateixa licitacio, el plec es processa una vegada."""
    for model in (
        TenderDocumentContent,
        TenderDocumentExtraction,
        TenderDocumentPage,
        TenderDocumentFile,
    ):
        assert "company_id" not in model.__table__.columns, f"{model.__name__} no pot ser de tenant"


async def test_the_same_sha256_cannot_be_stored_twice(session):
    sha = "a" * 64
    session.add(make_content(sha))
    await session.commit()

    session.add(make_content(sha))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_the_same_parser_version_can_have_several_revisions(session):
    """Repetir el parser NO sobreescriu: crea una revisio nova.

    Es l'invariant que fa citable una pagina: l'evidencia d'ahir segueix sent
    la d'ahir encara que avui es torni a extreure amb el mateix parser.
    """
    content = make_content("b" * 64)
    session.add(content)
    await session.commit()

    session.add(make_extraction(content.id, "pypdf-v1", revision=1))
    await session.commit()

    second = make_extraction(content.id, "pypdf-v1", revision=2)
    second.is_current = False
    session.add(second)
    await session.commit()

    total = await session.scalar(
        sa.select(sa.func.count())
        .select_from(TenderDocumentExtraction)
        .where(TenderDocumentExtraction.content_id == content.id)
    )
    assert total == 2


async def test_a_revision_number_is_unique_within_a_content(session):
    content = make_content("f" * 64)
    session.add(content)
    await session.commit()

    session.add(make_extraction(content.id, "pypdf-v1", revision=1))
    await session.commit()

    clash = make_extraction(content.id, "pdfminer-v2", revision=1)
    clash.is_current = False
    session.add(clash)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_content_has_at_most_one_current_extraction(session):
    content = make_content("c" * 64)
    session.add(content)
    await session.commit()

    session.add(make_extraction(content.id, "pypdf-v1", revision=1))
    await session.commit()

    session.add(make_extraction(content.id, "pypdf-v2", revision=2))  # tambe is_current=True
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_page_numbers_start_at_one(session):
    content = make_content("d" * 64)
    session.add(content)
    await session.commit()
    extraction = make_extraction(content.id)
    session.add(extraction)
    await session.commit()

    session.add(
        TenderDocumentPage(
            extraction_id=extraction.id,
            page_number=0,
            text="",
            char_count=0,
            text_hash="0" * 64,
            status=PageExtractionStatus.EMPTY,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_page_number_is_unique_within_an_extraction(session):
    content = make_content("e" * 64)
    session.add(content)
    await session.commit()
    extraction = make_extraction(content.id)
    session.add(extraction)
    await session.commit()

    for _ in range(2):
        session.add(
            TenderDocumentPage(
                extraction_id=extraction.id,
                page_number=1,
                text="text",
                char_count=4,
                text_hash="1" * 64,
                status=PageExtractionStatus.EXTRACTED,
            )
        )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_only_one_download_can_be_in_flight_per_document(session, tender_document):
    """Aquest index parcial es el lock de concurrencia: sense Redis i sense cua."""
    for _ in range(2):
        session.add(
            TenderDocumentFile(
                tender_document_id=tender_document.id,
                source_url="https://example.com/pcap.pdf",
                version_number=1,
                is_current=False,
                download_status=DownloadStatus.DOWNLOADING,
                started_at=sa.func.now(),
            )
        )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_two_finished_versions_can_coexist(session, tender_document):
    """El cas B del versionat: contingut canviat, dues versions conservades."""
    for version in (1, 2):
        session.add(
            TenderDocumentFile(
                tender_document_id=tender_document.id,
                source_url="https://example.com/pcap.pdf",
                version_number=version,
                is_current=version == 2,
                download_status=DownloadStatus.DOWNLOADED,
                started_at=sa.func.now(),
            )
        )
    await session.commit()

    total = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentFile))
    assert total == 2
