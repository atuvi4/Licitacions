"""Deduplicacio, versionat i concurrencia del processat de plecs.

Aquests tests fixen les regles que costen mes de recuperar si es trenquen: que
cap original es perdi, que els mateixos bytes no es parsegin dues vegades, i
que dos clics simultanis no facin dues descarregues.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
import sqlalchemy as sa

from app.core.errors import NoContentToExtractError, ProcessingInProgressError
from app.models.enums import DownloadStatus, ExtractionQuality, ExtractionStatus
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.services.document_download import OLE2_MAGIC
from app.services.pdf_text import PARSER_VERSION, ExtractionResult
from app.services.tender_document_processing import process_tender_document
from tests.fixtures.pdf_builder import build_corrupt_pdf, build_pdf

PUBLIC_IP = "93.184.216.34"
PCAP = build_pdf(["Plec de clausules administratives particulars. " * 8] * 3)
PCAP_V2 = build_pdf(["Plec REVISAT de clausules administratives particulars. " * 8] * 4)


def resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def serving(payload: bytes, content_type: str = "application/pdf"):
    """Client fals que sempre respon amb aquests bytes."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": content_type})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def process(session, document, payload=PCAP, **kwargs):
    async with serving(payload) as client:
        return await process_tender_document(
            session, document, client=client, resolver=resolver, **kwargs
        )


async def test_processing_downloads_stores_and_extracts(session, tender_document):
    outcome = await process(session, tender_document)

    assert outcome.reused is False
    file = outcome.file
    assert file.download_status == DownloadStatus.DOWNLOADED
    assert file.version_number == 1
    assert file.is_current is True
    assert file.content is not None
    assert file.content.sha256 == hashlib.sha256(PCAP).hexdigest()
    assert file.content.mime_type == "application/pdf"

    extraction = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == file.content_id
        )
    )
    assert extraction.status == ExtractionStatus.EXTRACTED
    assert extraction.parser_version == PARSER_VERSION
    assert extraction.page_count == 3
    assert extraction.quality == ExtractionQuality.GOOD

    pages = (
        await session.scalars(
            sa.select(TenderDocumentPage)
            .where(TenderDocumentPage.extraction_id == extraction.id)
            .order_by(TenderDocumentPage.page_number)
        )
    ).all()
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert "clausules" in pages[0].text


async def test_the_original_bytes_reach_the_storage(session, tender_document, storage):
    outcome = await process(session, tender_document)

    stored = await storage.download(outcome.file.content.storage_key)
    assert stored == PCAP
    assert hashlib.sha256(stored).hexdigest() == outcome.file.content.sha256


async def test_processing_twice_does_not_download_or_parse_again(session, tender_document):
    """Cas A del versionat, i el criteri d'acceptacio 7."""
    first = await process(session, tender_document)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=PCAP, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        second = await process_tender_document(
            session, tender_document, client=client, resolver=resolver
        )

    assert calls == [], "no s'havia de tornar a demanar la URL"
    assert second.reused is True
    assert second.file.id == first.file.id

    total_files = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentFile))
    assert total_files == 1


async def test_force_download_with_identical_bytes_keeps_one_version(session, tender_document):
    """Cas A explicit: es torna a baixar, el hash coincideix, no hi ha versio nova."""
    first = await process(session, tender_document)
    second = await process(session, tender_document, force_download=True)

    assert second.reused is True
    assert second.file.id == first.file.id
    assert second.file.last_checked_at is not None

    total = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentFile))
    assert total == 1


async def test_changed_content_creates_a_new_version_and_keeps_the_old(session, tender_document):
    """Cas B: cap original processat es perd mai."""
    first = await process(session, tender_document)
    second = await process(session, tender_document, payload=PCAP_V2, force_download=True)

    assert second.file.id != first.file.id
    assert second.file.version_number == 2
    assert second.file.is_current is True

    await session.refresh(first.file)
    assert first.file.is_current is False
    assert first.file.content_id is not None, "l'original antic s'ha de conservar"

    contents = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentContent))
    assert contents == 2


async def test_the_same_bytes_under_two_documents_are_stored_and_parsed_once(
    session, tender_document, second_tender_document, storage
):
    """Cas C i la deduplicacio del §27: nivell de dedup = els bytes."""
    first = await process(session, tender_document)

    uploads_before = len(storage.uploads)
    second = await process(session, second_tender_document)

    assert second.file.content_id == first.file.content_id
    assert len(storage.uploads) == uploads_before, "no s'havia de tornar a pujar el blob"

    contents = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentContent))
    extractions = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderDocumentExtraction)
    )
    files = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentFile))

    assert (contents, extractions, files) == (1, 1, 2)


async def test_a_new_parser_version_re_extracts_without_re_downloading(
    session, tender_document, monkeypatch
):
    await process(session, tender_document)

    # La versio vigent ja no es una constant global del processat: la diu
    # `parser_version_for`, que tria segons el tipus del contingut.
    monkeypatch.setattr("app.services.document_parsers.PDF_PARSER_VERSION", "pypdf-v2")
    monkeypatch.setattr("app.services.pdf_text.PARSER_VERSION", "pypdf-v2")

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=PCAP, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        await process_tender_document(session, tender_document, client=client, resolver=resolver)

    assert calls == [], "un canvi de parser no ha de tornar a baixar el PDF"

    versions = (await session.scalars(sa.select(TenderDocumentExtraction.parser_version))).all()
    assert sorted(versions) == ["pypdf-v1", "pypdf-v2"], "l'extraccio anterior s'ha de conservar"

    current = await session.scalar(
        sa.select(TenderDocumentExtraction).where(TenderDocumentExtraction.is_current.is_(True))
    )
    assert current.parser_version == "pypdf-v2"


async def test_force_reextract_creates_a_new_revision_and_keeps_the_old_one(
    session, tender_document
):
    """La garantia que 4B necessita: l'evidencia citada no canvia mai."""
    first = await process(session, tender_document)
    content_id = first.file.content_id

    original = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == content_id
        )
    )
    original_id = original.id
    original_pages = {
        (p.page_number, p.text_hash)
        for p in (
            await session.scalars(
                sa.select(TenderDocumentPage).where(
                    TenderDocumentPage.extraction_id == original_id
                )
            )
        ).all()
    }
    assert len(original_pages) == 3

    await process(session, tender_document, force_reextract=True)

    extractions = (
        await session.scalars(
            sa.select(TenderDocumentExtraction)
            .where(TenderDocumentExtraction.content_id == content_id)
            .order_by(TenderDocumentExtraction.revision_number)
        )
    ).all()
    assert [e.revision_number for e in extractions] == [1, 2]
    assert [e.parser_version for e in extractions] == [PARSER_VERSION, PARSER_VERSION]

    # Nomes la nova es vigent.
    assert [e.is_current for e in extractions] == [False, True]

    # La primera continua consultable, amb les MATEIXES pagines.
    survivors = {
        (p.page_number, p.text_hash)
        for p in (
            await session.scalars(
                sa.select(TenderDocumentPage).where(
                    TenderDocumentPage.extraction_id == original_id
                )
            )
        ).all()
    }
    assert survivors == original_pages, "les pagines historiques no es poden tocar"

    # I la revisio nova te les seves propies pagines, no les comparteix.
    total_pages = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentPage))
    assert total_pages == 6

    # Cap blob nou: es el mateix contingut.
    contents = await session.scalar(sa.select(sa.func.count()).select_from(TenderDocumentContent))
    assert contents == 1


async def test_a_failed_reextraction_does_not_dethrone_the_working_one(
    session, tender_document, monkeypatch
):
    """Si la reextraccio peta, el document no es pot quedar sense text."""
    first = await process(session, tender_document)
    content_id = first.file.content_id

    good = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == content_id,
            TenderDocumentExtraction.is_current.is_(True),
        )
    )
    good_id = good.id

    # El parser retorna una fallada sense llancar, com fa amb un PDF corrupte.
    from app.services import tender_document_processing as processing
    from app.services.pdf_text import PARSER_VERSION as REAL_VERSION

    async def failing(_data):
        return ExtractionResult(
            parser_version=REAL_VERSION,
            status=ExtractionStatus.FAILED,
            quality=ExtractionQuality.FAILED,
            pages=(),
            page_count=0,
            extracted_page_count=0,
            empty_page_count=0,
            text_char_count=0,
            ocr_candidate=False,
            parse_ms=1,
            error_code="corrupt_pdf",
            error_message="simulat",
        )

    async def failing_dispatch(_mime, data):
        return await failing(data)

    monkeypatch.setattr(processing, "extract_text_async", failing_dispatch)
    await process(session, tender_document, force_reextract=True)

    extractions = (
        await session.scalars(
            sa.select(TenderDocumentExtraction)
            .where(TenderDocumentExtraction.content_id == content_id)
            .order_by(TenderDocumentExtraction.revision_number)
        )
    ).all()
    assert len(extractions) == 2, "la fallada tambe queda registrada com a revisio"
    assert extractions[1].status == ExtractionStatus.FAILED
    assert extractions[1].is_current is False, "una fallada no pot ser la vigent"

    current = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == content_id,
            TenderDocumentExtraction.is_current.is_(True),
        )
    )
    assert current.id == good_id
    assert current.page_count == 3

    # Les pagines bones segueixen intactes.
    pages = await session.scalar(
        sa.select(sa.func.count())
        .select_from(TenderDocumentPage)
        .where(TenderDocumentPage.extraction_id == good_id)
    )
    assert pages == 3


async def test_a_pdf_that_failed_for_lack_of_the_aes_library_is_read_once_it_is_there(
    session, tender_document, monkeypatch
):
    """`pdf_crypto_unavailable` no es definitiva. Sense la biblioteca no s'hi torna (no
    s'acumulen revisions fallides); quan el proces ja la te, el seguent processat el llegeix
    sense tornar-lo a baixar."""
    from tests.fixtures.pdf_builder import encrypt_pdf, use_pypdf_without_aes

    data = encrypt_pdf(PCAP, algorithm="AES-128")
    with monkeypatch.context() as without_aes:
        use_pypdf_without_aes(without_aes)
        first = await process(session, tender_document, payload=data)
        again = await process(session, tender_document, payload=data)

    content_id = first.file.content_id
    extractions = (
        await session.scalars(
            sa.select(TenderDocumentExtraction).where(
                TenderDocumentExtraction.content_id == content_id
            )
        )
    ).all()
    assert [(e.status, e.error_code) for e in extractions] == [
        (ExtractionStatus.FAILED, "pdf_crypto_unavailable")
    ]
    assert again.reused is True

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=data, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        third = await process_tender_document(
            session, tender_document, client=client, resolver=resolver
        )

    assert calls == []
    assert third.reused is False
    current = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == content_id,
            TenderDocumentExtraction.is_current.is_(True),
        )
    )
    assert current.status == ExtractionStatus.EXTRACTED
    assert current.error_code is None
    assert current.page_count == 3
    assert current.revision_number == 2


async def _revisions(session, content_id) -> list[TenderDocumentExtraction]:
    return (
        await session.scalars(
            sa.select(TenderDocumentExtraction)
            .where(TenderDocumentExtraction.content_id == content_id)
            .order_by(TenderDocumentExtraction.revision_number)
        )
    ).all()


async def test_a_retry_that_fails_again_replaces_the_failure_and_stops(
    session, tender_document, monkeypatch
):
    """Un AES-256 amb contrasenya d'obertura desat sense l'AES. Amb l'AES, el reintent falla
    pel motiu real (`encrypted_pdf`) i, com que la vigent tampoc no tenia text, la substitueix:
    no es torna a provar a cada processat (abans, una revisio fallida mes cada vegada)."""
    from tests.fixtures.pdf_builder import encrypt_pdf, use_pypdf_without_aes

    data = encrypt_pdf(PCAP, algorithm="AES-256", user_password="secret")
    with monkeypatch.context() as without_aes:
        use_pypdf_without_aes(without_aes)
        first = await process(session, tender_document, payload=data)
    retried = await process(session, tender_document, payload=data)
    later = [await process(session, tender_document, payload=data) for _ in range(3)]

    revisions = await _revisions(session, first.file.content_id)
    assert [(r.error_code, r.is_current) for r in revisions] == [
        ("pdf_crypto_unavailable", False),
        ("encrypted_pdf", True),
    ]
    assert retried.reused is False
    assert all(outcome.reused for outcome in later)


async def test_an_unencrypted_jbig2_pdf_is_not_mistaken_for_a_missing_aes(
    session, tender_document, monkeypatch
):
    """El `DependencyError` del `jbig2dec` no es la falta de l'AES: una sola revisio (amb la
    pagina fallida), i els processats seguents la reaprofiten."""
    import pypdf.filters

    monkeypatch.setattr(pypdf.filters, "JBIG2DEC_BINARY", None)
    data = build_pdf(["Plec de clausules administratives particulars. " * 8] * 3,
                     jbig2_pages=frozenset({2}))
    first = await process(session, tender_document, payload=data)
    later = [await process(session, tender_document, payload=data) for _ in range(3)]

    revisions = await _revisions(session, first.file.content_id)
    assert [(r.status, r.error_code, r.extracted_page_count) for r in revisions] == [
        (ExtractionStatus.EXTRACTED, None, 2)
    ]
    assert all(outcome.reused for outcome in later)


async def test_a_deuc_stored_by_the_old_code_is_read_once_the_aes_is_there(
    session, tender_document, second_tender_document, monkeypatch
):
    """Abans de `pdf_crypto_unavailable`, un PDF AES sense la biblioteca quedava `corrupt_pdf`
    amb el `DependencyError` de l'AES al missatge (els DEUC del pilot). Aquesta forma tambe es
    torna a llegir sola, sense baixar-la; un `corrupt_pdf` de debo, no."""
    from tests.fixtures.pdf_builder import encrypt_pdf, use_pypdf_without_aes

    data = encrypt_pdf(PCAP, algorithm="AES-128")
    with monkeypatch.context() as without_aes:
        use_pypdf_without_aes(without_aes)
        first = await process(session, tender_document, payload=data)
    content_id = first.file.content_id
    await session.execute(
        sa.update(TenderDocumentExtraction)
        .where(TenderDocumentExtraction.content_id == content_id)
        .values(
            error_code="corrupt_pdf",
            error_message="DependencyError: cryptography>=3.1 is required for AES algorithm",
        )
    )
    await session.commit()

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=data, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        again = await process_tender_document(
            session, tender_document, client=client, resolver=resolver
        )

    assert calls == []
    assert again.reused is False
    revisions = await _revisions(session, content_id)
    assert [(r.status, r.is_current) for r in revisions] == [
        (ExtractionStatus.FAILED, False),
        (ExtractionStatus.EXTRACTED, True),
    ]

    # Un `corrupt_pdf` amb un altre motiu continua sent definitiu.
    broken = build_corrupt_pdf()
    stored = await process(session, second_tender_document, payload=broken)
    assert [r.error_code for r in await _revisions(session, stored.file.content_id)] == [
        "corrupt_pdf"
    ]
    assert (await process(session, second_tender_document, payload=broken)).reused is True


async def _doc_stored_before_doc_v1(session, tender_document, monkeypatch, body: bytes):
    """Un .doc desat quan encara no es llegia: `skipped_unsupported`, amb la versio del PDF."""
    from app.core.config import settings
    from app.models.enums import TenderDocumentType
    from app.models.tenders import TenderDocument
    from app.services import document_parsers

    doc = TenderDocument(
        tender_id=tender_document.tender_id,
        name="ANNEX III.doc",
        document_type=TenderDocumentType.OTHER,
        url="https://example.com/annex_iii.doc",
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    with monkeypatch.context() as before_doc_v1:
        before_doc_v1.setattr(
            settings,
            "DOCUMENT_TEXT_EXTRACTION_MIME_TYPES",
            settings.DOCUMENT_TEXT_EXTRACTION_MIME_TYPES.replace(",application/msword", ""),
        )
        versions = dict(document_parsers._OFFICE_VERSIONS)
        versions.pop("application/msword")
        before_doc_v1.setattr(document_parsers, "_OFFICE_VERSIONS", versions)
        async with serving(body, "application/msword") as client:
            first = await process_tender_document(session, doc, client=client, resolver=resolver)

    old = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == first.file.content_id
        )
    )
    assert first.file.content.mime_type == "application/msword"
    assert old.status == ExtractionStatus.SKIPPED_UNSUPPORTED
    assert old.parser_version == PARSER_VERSION
    return doc, first


async def test_an_old_doc_without_text_is_read_with_doc_v1_without_downloading_it_again(
    session, tender_document, monkeypatch
):
    """El cas de Parc Taulí: .doc desats quan encara no es llegien (`skipped_unsupported`,
    versio del PDF). Amb `doc-v1`, el seguent processat els llegeix sense baixar-los."""
    from tests.fixtures.doc_builder import build_doc

    body = build_doc("MODEL D'OFERTA ECONOMICA\r")
    doc, first = await _doc_stored_before_doc_v1(session, tender_document, monkeypatch, body)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=body, headers={"content-type": "application/msword"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        second = await process_tender_document(session, doc, client=client, resolver=resolver)

    assert calls == []
    assert second.reused is False
    current = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == first.file.content_id,
            TenderDocumentExtraction.is_current.is_(True),
        )
    )
    assert (current.status, current.parser_version) == (ExtractionStatus.EXTRACTED, "doc-v1")
    page = await session.scalar(
        sa.select(TenderDocumentPage).where(TenderDocumentPage.extraction_id == current.id)
    )
    assert page.text == "MODEL D'OFERTA ECONOMICA"


async def test_an_old_doc_that_doc_v1_cannot_read_fails_once(
    session, tender_document, monkeypatch
):
    """Si `doc-v1` no el pot llegir, la fallada substitueix el «no suportat» antic (cap dels
    dos no tenia text) i es la vigent: no es torna a provar a cada processat."""
    from tests.fixtures.doc_builder import build_doc

    body = build_doc("MODEL D'OFERTA ECONOMICA\r")[:1024]  # OLE2 truncat
    doc, first = await _doc_stored_before_doc_v1(session, tender_document, monkeypatch, body)

    async with serving(body, "application/msword") as client:
        read = await process_tender_document(session, doc, client=client, resolver=resolver)
        later = [
            await process_tender_document(session, doc, client=client, resolver=resolver)
            for _ in range(3)
        ]

    revisions = await _revisions(session, first.file.content_id)
    assert [(r.status, r.parser_version, r.is_current) for r in revisions] == [
        (ExtractionStatus.SKIPPED_UNSUPPORTED, PARSER_VERSION, False),
        (ExtractionStatus.FAILED, "doc-v1", True),
    ]
    assert revisions[1].error_code == "doc_unreadable"
    assert read.reused is False
    assert all(outcome.reused for outcome in later)


async def test_force_reextract_without_content_is_refused(session, tender_document):
    with pytest.raises(NoContentToExtractError):
        await process(session, tender_document, force_reextract=True)


async def test_a_failed_download_is_recorded_without_losing_a_good_version(
    session, tender_document
):
    good = await process(session, tender_document)

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(failing), follow_redirects=False
    ) as client:
        outcome = await process_tender_document(
            session, tender_document, client=client, resolver=resolver, force_download=True
        )

    assert outcome.file.download_status == DownloadStatus.FAILED
    assert outcome.file.error_code == "http_error"
    assert outcome.file.is_current is False, "una fallada no destrona una versio bona"

    await session.refresh(good.file)
    assert good.file.is_current is True


async def test_an_xml_is_stored_and_read_with_its_own_parser(session, tender_document):
    # La URL ha d'anunciar XML: una URL acabada en .pdf que retorna una altra
    # cosa es invalid_content per disseny (vegeu `resolve_mime_type`).
    from app.models.enums import TenderDocumentType
    from app.models.tenders import TenderDocument

    xml_document = TenderDocument(
        tender_id=tender_document.tender_id,
        name="dades.xml",
        document_type=TenderDocumentType.OTHER,
        url="https://example.com/dades.xml",
    )
    session.add(xml_document)
    await session.commit()
    await session.refresh(xml_document)

    xml = b'<?xml version="1.0"?><doc/>'
    async with serving(xml, "text/xml") as client:
        outcome = await process_tender_document(
            session, xml_document, client=client, resolver=resolver
        )

    assert outcome.file.download_status == DownloadStatus.DOWNLOADED
    assert outcome.file.content.is_text_extractable is True

    extraction = await session.scalar(sa.select(TenderDocumentExtraction))
    assert extraction.status == ExtractionStatus.EXTRACTED
    assert extraction.parser_version == "xml-v1"
    assert extraction.error_code is None


async def test_an_ole2_document_is_stored_but_not_parsed(session, tender_document, storage):
    """Cas real de PLACSP: MIME declarat incorrecte i bytes OLE2 d'un .doc antic.

    L'original s'ha de conservar amb el seu hash. NO pot ser `invalid_content`
    nomes perque el servidor declari malament el tipus.
    """
    from app.models.enums import TenderDocumentType
    from app.models.tenders import TenderDocument

    doc = TenderDocument(
        tender_id=tender_document.tender_id,
        name="ANEXO I",
        document_type=TenderDocumentType.OTHER,
        url="https://example.com/servlet/GetDocumentByIdServlet?id=7",
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    body = OLE2_MAGIC + b"\x00" * 400
    declared = "application/vnd.openxmlformatsofficedocument.wordprocessingml.document"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": declared})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        outcome = await process_tender_document(session, doc, client=client, resolver=resolver)

    file = outcome.file
    assert file.download_status == DownloadStatus.DOWNLOADED, "l'original s'ha de conservar"
    assert file.error_code is None
    assert file.content.sha256 == hashlib.sha256(body).hexdigest()
    assert file.content.mime_type == "application/x-ole-storage"
    assert file.content.is_text_extractable is False
    assert file.declared_mime_type == declared

    # El blob es realment a l'storage i coincideix byte a byte.
    assert await storage.download(file.content.storage_key) == body

    extraction = await session.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == file.content_id
        )
    )
    assert extraction.status == ExtractionStatus.SKIPPED_UNSUPPORTED
    assert extraction.error_code == "unsupported_for_text_extraction"
    assert extraction.quality == ExtractionQuality.NOT_APPLICABLE
    assert extraction.revision_number == 1


async def test_a_second_request_while_one_is_in_flight_gets_a_conflict(session, tender_document):
    """Doble clic: el segon request no duplica la feina."""
    session.add(
        TenderDocumentFile(
            tender_document_id=tender_document.id,
            source_url=tender_document.url,
            version_number=1,
            is_current=False,
            download_status=DownloadStatus.DOWNLOADING,
        )
    )
    await session.commit()

    with pytest.raises(ProcessingInProgressError):
        await process(session, tender_document)


async def test_a_stale_in_flight_row_is_reclaimed(session, tender_document, monkeypatch):
    """Si el proces mor, el document no pot quedar bloquejat per sempre."""
    from datetime import UTC, datetime, timedelta

    from app.core.config import settings

    stale = TenderDocumentFile(
        tender_document_id=tender_document.id,
        source_url=tender_document.url,
        version_number=1,
        is_current=False,
        download_status=DownloadStatus.DOWNLOADING,
        started_at=datetime.now(UTC)
        - timedelta(seconds=settings.DOCUMENT_PROCESSING_STALE_SECONDS + 60),
    )
    session.add(stale)
    await session.commit()

    outcome = await process(session, tender_document)

    assert outcome.file.download_status == DownloadStatus.DOWNLOADED
    await session.refresh(stale)
    assert stale.download_status == DownloadStatus.FAILED
    assert stale.error_code == "stale"
