"""Processat d'un document `portal` (Bloc 2, etapa 2): l'MD5 publicat i el host del portal."""

from __future__ import annotations

import hashlib
import uuid

import httpx
import sqlalchemy as sa

from app.ingestion.pscp_portal.parser import download_url, generated_url
from app.models.enums import (
    DocumentContributionStatus,
    DownloadStatus,
    ExtractionStatus,
    TenderDocumentOrigin,
    TenderDocumentType,
)
from app.models.tender_documents import TenderDocumentFile
from app.models.tenders import Tender, TenderDocument
from app.services.document_download import MSWORD, OLE2_GENERIC
from app.services.tender_document_processing import (
    CHECKSUM_MISMATCH,
    _ensure_current_extraction,
    current_extraction_for,
    get_or_create_content,
    permanently_failed,
    process_tender_document,
)
from tests.fixtures.doc_builder import build_cfb, build_doc
from tests.fixtures.pdf_builder import build_pdf
from tests.pscp_portal_support import resolver

PCAP = build_pdf(["Plec de clausules administratives particulars del portal. " * 8] * 2)
OTHER = build_pdf(["Uns altres bytes que no son els publicats al portal. " * 8] * 2)


def serving(*responses: httpx.Response):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return responses[min(len(calls), len(responses)) - 1]

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def pdf(payload: bytes) -> httpx.Response:
    return httpx.Response(200, content=payload, headers={"content-type": "application/pdf"})


async def portal_document(session, tender_document, url: str) -> TenderDocument:
    tender = await session.get(Tender, tender_document.tender_id)
    document = TenderDocument(
        tender_id=tender.id,
        name="Annex.pdf",
        document_type=TenderDocumentType.ADDITIONAL,
        url=url,
        origin=TenderDocumentOrigin.PORTAL,
        governing_source_id=tender.source_id,
        contribution_status=DocumentContributionStatus.NOT_APPLICABLE,
    )
    session.add(document)
    await session.commit()
    return document


def portal_url(payload: bytes) -> str:
    return download_url(uuid.uuid4().int % 10**9, hashlib.md5(payload).hexdigest())


async def test_the_published_bytes_are_stored(session, tender_document):
    document = await portal_document(session, tender_document, portal_url(PCAP))
    client, calls = serving(pdf(PCAP))
    async with client:
        outcome = await process_tender_document(session, document, client=client, resolver=resolver)

    assert outcome.file.download_status == DownloadStatus.DOWNLOADED
    assert outcome.file.content.sha256 == hashlib.sha256(PCAP).hexdigest()
    assert len(calls) == 1


async def test_bytes_without_the_published_md5_are_refused(session, tender_document):
    document = await portal_document(session, tender_document, portal_url(PCAP))
    client, _ = serving(pdf(OTHER))
    async with client:
        outcome = await process_tender_document(session, document, client=client, resolver=resolver)

    assert outcome.file.download_status == DownloadStatus.FAILED
    assert outcome.file.error_code == CHECKSUM_MISMATCH
    assert outcome.file.content_id is None


async def test_a_portal_url_without_md5_is_refused(session, tender_document):
    url = "https://contractaciopublica.cat/portal-api/descarrega-document/12/sense-md5"
    document = await portal_document(session, tender_document, url)
    client, _ = serving(pdf(PCAP))
    async with client:
        outcome = await process_tender_document(session, document, client=client, resolver=resolver)

    assert outcome.file.error_code == CHECKSUM_MISMATCH


async def test_a_portal_download_cannot_leave_the_portal(session, tender_document):
    document = await portal_document(session, tender_document, portal_url(PCAP))
    client, calls = serving(
        httpx.Response(302, headers={"location": "https://mirall.example/annex.pdf"}), pdf(PCAP)
    )
    async with client:
        outcome = await process_tender_document(session, document, client=client, resolver=resolver)

    assert outcome.file.download_status == DownloadStatus.FAILED
    assert outcome.file.error_code == "host_not_official"
    assert len(calls) == 1


async def test_a_feed_document_with_a_portal_url_is_not_checked(session, tender_document):
    """L'MD5 nomes el comprova el connector que governa el document (`portal`)."""
    tender_document.url = portal_url(PCAP)
    await session.commit()
    client, _ = serving(pdf(OTHER))
    async with client:
        outcome = await process_tender_document(
            session, tender_document, client=client, resolver=resolver
        )

    assert outcome.file.download_status == DownloadStatus.DOWNLOADED


async def test_a_generated_document_is_never_downloaded(session, tender_document):
    document = await portal_document(
        session, tender_document, generated_url(str(uuid.uuid4()), "preguntes-respostes")
    )
    data = b"Preguntes i respostes (PSCP)\n"
    content, _ = await get_or_create_content(
        session,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="text/plain",
        detected_extension="txt",
    )
    session.add(
        TenderDocumentFile(
            tender_document_id=document.id,
            content_id=content.id,
            source_url=document.url,
            version_number=1,
            is_current=True,
            download_status=DownloadStatus.DOWNLOADED,
        )
    )
    await session.commit()
    client, calls = serving(pdf(PCAP))
    async with client:
        outcome = await process_tender_document(
            session, document, client=client, resolver=resolver, force_download=True
        )

    assert calls == []
    assert outcome.file.content_id == content.id
    files = await session.scalar(
        sa.select(sa.func.count(TenderDocumentFile.id)).where(
            TenderDocumentFile.tender_document_id == document.id
        )
    )
    assert files == 1


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def test_a_maintenance_page_is_a_checksum_mismatch_not_a_permanent_failure(
    session, tender_document
):
    """Una pagina HTML servida amb 200 no son els bytes publicats: es pot tornar a provar."""
    document = await portal_document(session, tender_document, portal_url(PCAP))
    page = httpx.Response(
        200, content=b"<html>manteniment</html>", headers={"content-type": "text/html"}
    )
    client, _ = serving(page)
    async with client:
        outcome = await process_tender_document(session, document, client=client, resolver=resolver)

    assert outcome.file.error_code == CHECKSUM_MISMATCH
    assert not permanently_failed(document, outcome.file)


async def test_a_published_file_of_an_unsupported_type_fails_once(session, tender_document):
    """Els bytes son els publicats (MD5 bo) pero el tipus no s'admet: no es repeteix."""
    rar = b"Rar!\x1a\x07\x00 dades d'un arxiu comprimit"
    document = await portal_document(session, tender_document, portal_url(rar))
    client, calls = serving(
        httpx.Response(200, content=rar, headers={"content-type": "application/x-rar"})
    )
    async with client:
        first = await process_tender_document(session, document, client=client, resolver=resolver)
        again = await process_tender_document(session, document, client=client, resolver=resolver)

    assert first.file.error_code == "invalid_content"
    assert permanently_failed(document, first.file)
    assert again.reused is True and again.file.id == first.file.id
    assert len(calls) == 1


async def test_a_portal_image_is_stored_without_text(session, tender_document):
    document = await portal_document(session, tender_document, portal_url(PNG))
    client, _ = serving(httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    async with client:
        outcome = await process_tender_document(session, document, client=client, resolver=resolver)

    assert outcome.file.download_status == DownloadStatus.DOWNLOADED
    assert outcome.file.content.mime_type == "image/png"
    extraction = await current_extraction_for(session, outcome.file.content_id)
    assert extraction.status == ExtractionStatus.SKIPPED_UNSUPPORTED


async def test_an_image_from_another_origin_is_still_refused(session, tender_document):
    """Les imatges nomes s'admeten als documents del portal: la resta, com sempre."""
    tender_document.url = "https://example.com/grafic.png"
    await session.commit()
    client, _ = serving(httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    async with client:
        outcome = await process_tender_document(
            session, tender_document, client=client, resolver=resolver
        )

    assert outcome.file.error_code == "invalid_content"


async def test_reused_bytes_stored_as_generic_ole2_become_a_doc(session):
    """Uns bytes .doc desats com a OLE2 generic (abans de `refine_ole2`) es corregeixen quan
    tornen a entrar: pels bytes (flux `WordDocument`), no per la capcalera de qui els envia."""
    data = build_doc("Plec de prescripcions tecniques del servei.")
    sha = hashlib.sha256(data).hexdigest()
    stored, _ = await get_or_create_content(
        session, data=data, sha256=sha, mime_type=OLE2_GENERIC, detected_extension="ole"
    )
    assert stored.is_text_extractable is False

    again, created = await get_or_create_content(
        session, data=data, sha256=sha, mime_type=MSWORD, detected_extension="doc"
    )

    assert created is False and again.id == stored.id
    assert again.mime_type == MSWORD and again.is_text_extractable is True


async def test_a_header_does_not_turn_other_ole2_bytes_into_a_doc(session):
    data = build_cfb({"Workbook": b"\x00" * 5000})
    sha = hashlib.sha256(data).hexdigest()
    await get_or_create_content(
        session, data=data, sha256=sha, mime_type=OLE2_GENERIC, detected_extension="ole"
    )

    again, _ = await get_or_create_content(
        session, data=data, sha256=sha, mime_type=MSWORD, detected_extension="doc"
    )

    assert again.mime_type == OLE2_GENERIC


async def test_stored_generic_ole2_doc_bytes_are_read_as_a_doc_when_processed(session):
    """El cami idempotent tambe ho corregeix: sense tornar-los a baixar ni a pujar."""
    data = build_doc("Plec de prescripcions tecniques del servei de telefonia.")
    content, _ = await get_or_create_content(
        session,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type=OLE2_GENERIC,
        detected_extension="ole",
    )

    extraction, worked = await _ensure_current_extraction(session, content, force=False)

    assert worked is True
    assert content.mime_type == MSWORD
    assert extraction.parser_version == "doc-v2"
    assert extraction.status == ExtractionStatus.EXTRACTED
