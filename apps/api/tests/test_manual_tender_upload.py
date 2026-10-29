"""Pujar un plec a una licitacio oberta a ma (S3-S2).

Pujar no es una pipeline nova: es una ENTRADA nova a la que ja hi havia. Els
bytes acaben al mateix `TenderDocumentContent` deduplicat per SHA-256, el
parser es el mateix i l'extraccio es guarda igual. L'unica cosa que canvia es
d'on venen els bytes, i aixo ho diu `origin`.

Dues distincions que aquests tests protegeixen:

* **guardat no es analitzat**. Un DOCX es guarda be i no se n'extreu text. Dir-ne
  error seria mentir sobre el fitxer; dir-ne analitzat seria mentir sobre el
  text. Son dos fets separats i la resposta els separa.
* **poc text no es OCR**. Un PDF escanejat es guarda, s'intenta llegir i el
  resultat honest es que no hi ha text usable. No s'hi inventa cap OCR.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.enums import DocumentFileOrigin, DownloadStatus, ExtractionStatus
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import TenderDocument
from tests.fixtures.doc_builder import build_doc
from tests.fixtures.pdf_builder import build_pdf
from tests.test_office_text import _docx, _p, _xlsx

pytestmark = pytest.mark.asyncio

CREATE = "/api/v1/companies/{company}/tenders"
UPLOAD = "/api/v1/companies/{company}/tenders/{tender}/documents"

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Un DOCX i un XLSX son ZIP: els magic bytes nomes diuen "PK", i el tipus fi
#: surt del que declara qui el puja. Es la mateixa regla que la descarrega.
ZIP_BYTES = b"PK\x03\x04" + b"\x00" * 64
#: Un .doc antic (OLE2): des del Bloc 2 se'n llegeix el text (`doc-v2`).
MSWORD = "application/msword"
#: Un .xls antic (OLE2): format que es guarda pero del qual no s'extreu text.
MSEXCEL = "application/vnd.ms-excel"
OLE2_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
XLSX_ROW = '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'


async def _tender(client, company, **extra) -> dict:
    body = {"client_token": str(uuid.uuid4()), "title": "Serveis de comunicacions"}
    body.update(extra)
    response = await client.post(CREATE.format(company=company.id), json=body)
    assert response.status_code == 201, response.text
    return response.json()["tender"]


async def _upload(client, company, tender_id, *, name, data: bytes, mime: str):
    return await client.post(
        UPLOAD.format(company=company.id, tender=tender_id),
        files={"file": (name, data, mime)},
    )


async def _ok(client, company, tender_id, *, name, data, mime) -> dict:
    response = await _upload(client, company, tender_id, name=name, data=data, mime=mime)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# A. Permisos i aillament                                                       #
# --------------------------------------------------------------------------- #


async def test_the_owner_can_upload_to_their_own_tender(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)

    body = await _ok(
        client_a, company, tender["id"], name="PCAP.pdf", data=build_pdf(["Plec"]), mime=PDF
    )
    assert body["name"] == "PCAP.pdf"
    assert body["file"]["version_number"] == 1


async def test_another_company_cannot_upload_to_it(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    tender = await _tender(client_a, company_a)

    response = await _upload(
        client_b, company_b, tender["id"], name="x.pdf", data=build_pdf(["X"]), mime=PDF
    )
    assert response.status_code == 404


async def test_a_public_tender_does_not_accept_uploads_yet(client_a, tenant_a, tender_document):
    """A Sprint 3 nomes s'hi pugen documents a les licitacions obertes a ma."""
    _, company = tenant_a
    response = await _upload(
        client_a, company, tender_document.tender_id, name="x.pdf",
        data=build_pdf(["X"]), mime=PDF,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tender_not_manual"


async def test_a_user_of_both_companies_cannot_upload_from_the_other_context(
    client_a, client_factory, db, tenant_a, tenant_b
):
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership
    from tests.conftest import TEST_PASSWORD, login

    user, company_a = tenant_a
    _, company_b = tenant_b
    tender = await _tender(client_a, company_a)
    db.add(
        CompanyMembership(user_id=user.id, company_id=company_b.id, role=MembershipRole.OWNER)
    )
    await db.commit()

    http = client_factory()
    await login(http, user.email, TEST_PASSWORD)
    response = await _upload(
        http, company_b, tender["id"], name="x.pdf", data=build_pdf(["X"]), mime=PDF
    )
    assert response.status_code == 404


@pytest.mark.parametrize("role", ["technical", "viewer"])
async def test_who_does_not_decide_does_not_upload(client_a, db, tenant_a, role):
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership

    user, company = tenant_a
    tender = await _tender(client_a, company)
    await db.execute(
        sa.update(CompanyMembership)
        .where(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == company.id,
        )
        .values(role=MembershipRole(role))
    )
    await db.commit()

    response = await _upload(
        client_a, company, tender["id"], name="x.pdf", data=build_pdf(["X"]), mime=PDF
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# B. Metadata del fitxer                                                        #
# --------------------------------------------------------------------------- #


async def test_an_uploaded_file_says_it_was_uploaded(client_a, db, tenant_a):
    """Sense `origin`, un fitxer sense URL voldria dir alhora «pujat» i
    «descarrega mal registrada»."""
    user, company = tenant_a
    tender = await _tender(client_a, company)
    body = await _ok(
        client_a, company, tender["id"], name="PCAP.pdf", data=build_pdf(["Plec"]), mime=PDF
    )

    file = (
        await db.execute(
            sa.select(TenderDocumentFile).where(
                TenderDocumentFile.id == uuid.UUID(body["file"]["file_id"])
            )
        )
    ).scalars().one()
    assert file.origin == DocumentFileOrigin.UPLOADED
    assert file.uploaded_by == user.id
    assert file.source_url is None
    assert file.version_number == 1
    assert file.is_current is True
    assert file.download_status == DownloadStatus.DOWNLOADED
    assert file.original_filename == "PCAP.pdf"


async def test_the_logical_document_hangs_from_the_tender(client_a, db, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    body = await _ok(
        client_a, company, tender["id"], name="PCAP.pdf", data=build_pdf(["Plec"]), mime=PDF
    )

    document = (
        await db.execute(
            sa.select(TenderDocument).where(TenderDocument.id == uuid.UUID(body["id"]))
        )
    ).scalars().one()
    assert document.tender_id == uuid.UUID(tender["id"])
    assert document.name == "PCAP.pdf"


# --------------------------------------------------------------------------- #
# C. Deduplicacio de contingut                                                  #
# --------------------------------------------------------------------------- #


async def test_the_same_bytes_are_stored_once(client_a, db, tenant_a):
    """La deduplicacio es de BYTES. Dos documents logics poden compartir-los."""
    _, company = tenant_a
    tender = await _tender(client_a, company)
    data = build_pdf(["Mateix contingut"])

    first = await _ok(client_a, company, tender["id"], name="a.pdf", data=data, mime=PDF)
    second = await _ok(client_a, company, tender["id"], name="b.pdf", data=data, mime=PDF)

    assert first["file"]["sha256"] == second["file"]["sha256"]
    assert first["id"] != second["id"]

    contents = (
        await db.execute(
            sa.select(TenderDocumentContent).where(
                TenderDocumentContent.sha256 == first["file"]["sha256"]
            )
        )
    ).scalars().all()
    assert len(contents) == 1


async def test_the_same_bytes_are_parsed_once(client_a, db, tenant_a):
    """Si el contingut ja hi era, no es torna a parsejar: l'extraccio es seva."""
    _, company = tenant_a
    tender = await _tender(client_a, company)
    data = build_pdf(["Mateix contingut"])

    first = await _ok(client_a, company, tender["id"], name="a.pdf", data=data, mime=PDF)
    await _ok(client_a, company, tender["id"], name="b.pdf", data=data, mime=PDF)

    content_id = (
        await db.execute(
            sa.select(TenderDocumentContent.id).where(
                TenderDocumentContent.sha256 == first["file"]["sha256"]
            )
        )
    ).scalar_one()
    extractions = (
        await db.execute(
            sa.select(TenderDocumentExtraction).where(
                TenderDocumentExtraction.content_id == content_id
            )
        )
    ).scalars().all()
    assert len(extractions) == 1


# --------------------------------------------------------------------------- #
# D. PDF amb text                                                               #
# --------------------------------------------------------------------------- #


async def test_a_pdf_with_text_is_read(client_a, db, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)

    body = await _ok(
        client_a,
        company,
        tender["id"],
        name="PCAP.pdf",
        data=build_pdf(["Clausula primera. " * 40, "Clausula segona. " * 40]),
        mime=PDF,
    )

    assert body["file"]["text_extraction_supported"] is True
    assert body["file"]["analysis_status"] == "extracted"
    assert body["file"]["page_count"] == 2
    assert body["file"]["text_char_count"] > 0

    pages = (
        await db.execute(
            sa.select(TenderDocumentPage).where(
                TenderDocumentPage.extraction_id == uuid.UUID(body["file"]["extraction_id"])
            )
        )
    ).scalars().all()
    assert [page.page_number for page in pages] == [1, 2]
    assert all("Clausula" in page.text for page in pages)


# --------------------------------------------------------------------------- #
# E. PDF escanejat: es guarda i es diu la veritat                               #
# --------------------------------------------------------------------------- #


async def test_a_pdf_without_usable_text_is_stored_and_says_so(client_a, tenant_a):
    """No hi ha OCR. Dir «analitzat» aqui seria mentir."""
    _, company = tenant_a
    tender = await _tender(client_a, company)

    body = await _ok(
        client_a, company, tender["id"], name="escanejat.pdf", data=build_pdf([""]), mime=PDF
    )

    assert body["stored"] is True
    assert body["file"]["text_extraction_supported"] is True
    assert body["file"]["analysis_status"] == "no_usable_text"
    assert body["file"]["text_char_count"] == 0
    assert body["file"]["ocr_candidate"] is True


# --------------------------------------------------------------------------- #
# F. TXT                                                                        #
# --------------------------------------------------------------------------- #


async def test_a_text_file_is_read_without_any_model(client_a, db, tenant_a):
    """Determinista i sense cap dependencia nova: es text, ja esta."""
    _, company = tenant_a
    tender = await _tender(client_a, company)
    contingut = "Clausula primera.\nObjecte del contracte.\n"

    body = await _ok(
        client_a,
        company,
        tender["id"],
        name="notes.txt",
        data=contingut.encode("utf-8"),
        mime="text/plain",
    )

    assert body["file"]["text_extraction_supported"] is True
    assert body["file"]["analysis_status"] == "extracted"
    assert body["file"]["page_count"] == 1

    pages = (
        await db.execute(
            sa.select(TenderDocumentPage).where(
                TenderDocumentPage.extraction_id == uuid.UUID(body["file"]["extraction_id"])
            )
        )
    ).scalars().all()
    assert len(pages) == 1
    assert "Objecte del contracte." in pages[0].text


# --------------------------------------------------------------------------- #
# G. DOCX i XLSX es llegeixen; un format sense parser es guarda sense text     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,mime,data,parser",
    [
        ("plec.docx", DOCX, _docx(_p("Objecte del contracte.")), "docx-v1"),
        (
            "preus.xlsx",
            XLSX,
            _xlsx([("Preus", XLSX_ROW)], shared=["Objecte del contracte."]),
            "xlsx-v2",
        ),
        ("annex.doc", MSWORD, build_doc("Objecte del contracte.\r"), "doc-v2"),
    ],
    # Sense ids, el nom del test portaria els bytes: a Windows, massa llarg per a l'entorn.
    ids=["docx", "xlsx", "doc"],
)
async def test_an_office_file_is_stored_and_read(
    client_a, db, tenant_a, name, mime, data, parser
):
    """DOCX, XLSX i DOC es llegeixen amb el seu parser (`docx-v1`, `xlsx-v2`, `doc-v2`)."""
    _, company = tenant_a
    tender = await _tender(client_a, company)

    body = await _ok(client_a, company, tender["id"], name=name, data=data, mime=mime)

    assert body["stored"] is True
    assert body["file"]["text_extraction_supported"] is True
    assert body["file"]["analysis_status"] == "extracted"
    extraction = await db.get(TenderDocumentExtraction, uuid.UUID(body["file"]["extraction_id"]))
    assert extraction.parser_version == parser
    pages = (
        await db.execute(
            sa.select(TenderDocumentPage).where(
                TenderDocumentPage.extraction_id == extraction.id
            )
        )
    ).scalars().all()
    assert "Objecte del contracte." in pages[0].text


async def test_a_doc_sent_as_a_generic_type_is_stored_as_a_doc_and_read(client_a, db, tenant_a):
    """Un .doc que el navegador envia com a `application/octet-stream` (Bloc 2): els magic
    bytes diuen OLE2 i el flux `WordDocument` diu .doc. Es desa i es llegeix com a tal."""
    _, company = tenant_a
    tender = await _tender(client_a, company)

    body = await _ok(
        client_a, company, tender["id"], name="annex",
        data=build_doc("Objecte del contracte.\r"), mime="application/octet-stream",
    )

    assert body["file"]["mime_type"] == MSWORD
    assert body["file"]["analysis_status"] == "extracted"
    extraction = await db.get(TenderDocumentExtraction, uuid.UUID(body["file"]["extraction_id"]))
    assert extraction.parser_version == "doc-v2"


async def test_only_an_ole2_with_a_word_stream_becomes_a_doc_on_upload():
    from app.services.tender_document_upload import resolve_upload_mime_type
    from tests.fixtures.doc_builder import build_cfb

    word = build_doc("Hola\r")
    assert resolve_upload_mime_type(word, None) == MSWORD
    assert resolve_upload_mime_type(word, "application/octet-stream") == MSWORD
    # Una declaracio concreta de la familia OLE2 mana, com sempre.
    assert resolve_upload_mime_type(word, MSEXCEL) == MSEXCEL
    sheet = build_cfb({"Workbook": b"\x09\x08" + b"\x00" * 4094})
    assert resolve_upload_mime_type(sheet, None) == "application/x-ole-storage"
    assert resolve_upload_mime_type(OLE2_BYTES, None) == "application/x-ole-storage"


async def test_a_broken_office_file_is_stored_and_its_reading_fails(client_a, db, tenant_a):
    """Un DOCX malmes es guarda igual; la lectura falla i ho diu (no es «no suportat»)."""
    _, company = tenant_a
    tender = await _tender(client_a, company)
    body = await _ok(client_a, company, tender["id"], name="p.docx", data=ZIP_BYTES, mime=DOCX)
    assert body["stored"] is True
    extraction = await db.get(TenderDocumentExtraction, uuid.UUID(body["file"]["extraction_id"]))
    assert extraction.status == ExtractionStatus.FAILED
    assert extraction.error_code == "docx_unreadable"


async def test_an_old_office_file_is_stored_but_not_read(client_a, db, tenant_a):
    """«No analitzable» no es un error de pujada. Son dos fets diferents."""
    _, company = tenant_a
    tender = await _tender(client_a, company)

    body = await _ok(
        client_a, company, tender["id"], name="preus.xls", data=OLE2_BYTES, mime=MSEXCEL
    )

    assert body["stored"] is True
    assert body["file"]["text_extraction_supported"] is False
    assert body["file"]["analysis_status"] == "unsupported"
    assert body["file"]["page_count"] == 0

    pages = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderDocumentPage)
            .join(
                TenderDocumentExtraction,
                TenderDocumentExtraction.id == TenderDocumentPage.extraction_id,
            )
            .where(TenderDocumentExtraction.id == uuid.UUID(body["file"]["extraction_id"]))
        )
    ).scalar_one()
    assert pages == 0


async def test_an_unsupported_format_is_not_a_failed_extraction(client_a, db, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    body = await _ok(
        client_a, company, tender["id"], name="p.xls", data=OLE2_BYTES, mime=MSEXCEL
    )

    extraction = (
        await db.execute(
            sa.select(TenderDocumentExtraction).where(
                TenderDocumentExtraction.id == uuid.UUID(body["file"]["extraction_id"])
            )
        )
    ).scalars().one()
    assert extraction.status == ExtractionStatus.SKIPPED_UNSUPPORTED


# --------------------------------------------------------------------------- #
# H. Formats que no s'accepten                                                  #
# --------------------------------------------------------------------------- #


async def test_html_is_not_a_tender_document(client_a, db, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)

    response = await _upload(
        client_a, company, tender["id"], name="pagina.html",
        data=b"<html><body>hola</body></html>", mime="text/html",
    )
    assert response.status_code == 415

    documents = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderDocument)
            .where(TenderDocument.tender_id == uuid.UUID(tender["id"]))
        )
    ).scalar_one()
    assert documents == 0


async def test_a_rejected_file_leaves_nothing_behind(client_a, db, tenant_a):
    """Ni document logic, ni fitxer, ni contingut."""
    _, company = tenant_a
    tender = await _tender(client_a, company)

    await _upload(
        client_a, company, tender["id"], name="p.pptx",
        data=ZIP_BYTES,
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    files = (
        await db.execute(sa.select(sa.func.count()).select_from(TenderDocumentFile))
    ).scalar_one()
    assert files == 0


async def test_an_empty_file_is_refused(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)

    response = await _upload(client_a, company, tender["id"], name="buit.pdf", data=b"", mime=PDF)
    assert response.status_code == 422


async def test_a_file_that_lies_about_being_a_pdf_is_refused(client_a, tenant_a):
    """Manen els magic bytes, com a la descarrega."""
    _, company = tenant_a
    tender = await _tender(client_a, company)

    response = await _upload(
        client_a, company, tender["id"], name="fals.pdf", data=b"aixo no es un PDF", mime=PDF
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# I. Res a mitges                                                               #
# --------------------------------------------------------------------------- #


async def test_a_storage_failure_leaves_no_document_at_all(
    client_a, db, tenant_a, storage, monkeypatch
):
    """Si els bytes no arriben a guardar-se, no hi ha res que hi apunti."""

    async def exploding(*_args, **_kwargs):
        raise RuntimeError("l'storage no respon")

    monkeypatch.setattr(storage, "upload", exploding)

    _, company = tenant_a
    tender = await _tender(client_a, company)
    with pytest.raises(RuntimeError):
        await _upload(
            client_a, company, tender["id"], name="PCAP.pdf",
            data=build_pdf(["Plec"]), mime=PDF,
        )

    documents = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderDocument)
            .where(TenderDocument.tender_id == uuid.UUID(tender["id"]))
        )
    ).scalar_one()
    contents = (
        await db.execute(sa.select(sa.func.count()).select_from(TenderDocumentContent))
    ).scalar_one()
    assert (documents, contents) == (0, 0)


async def test_no_file_ever_points_at_bytes_that_are_not_there(client_a, db, tenant_a, storage):
    """L'invariant que decideix l'ordre de les escriptures.

    Primer el contingut i despres el que hi apunta. El sentit contrari deixaria
    un document que la interficie considera valid i que no es pot obrir.
    """
    _, company = tenant_a
    tender = await _tender(client_a, company)
    await _ok(client_a, company, tender["id"], name="PCAP.pdf", data=build_pdf(["P"]), mime=PDF)

    rows = (
        await db.execute(
            sa.select(TenderDocumentContent.storage_key).join(
                TenderDocumentFile,
                TenderDocumentFile.content_id == TenderDocumentContent.id,
            )
        )
    ).scalars().all()
    assert rows
    for key in rows:
        assert await storage.download(key)


# --------------------------------------------------------------------------- #
# J. El cicle de vida de la licitacio                                           #
# --------------------------------------------------------------------------- #


async def test_a_new_plec_is_recorded_as_an_arrival(client_a, db, tenant_a):
    """Un plec que arriba queda REGISTRAT, vingui de la font o d'una persona.

    S'hi passa per la MATEIXA porta (`record_revision`) i genera el mateix
    `DOCUMENT_ADDED`. El que aquesta revisio NO vol dir es que el que
    s'enviaria hagi canviat: es tracabilitat, i qui hi ha de reaccionar es
    l'extraccio de requisits. Veure `PACKAGE_STALE_IMPACTS`.

    Aqui tampoc no s'implementa l'obsolescencia DOCUMENTAL de S3-S3
    (`documents_reviewed_*` i els seus findings).
    """
    from app.models.tender_lifecycle import TenderRevision

    _, company = tenant_a
    tender = await _tender(client_a, company)
    await _ok(client_a, company, tender["id"], name="PCAP.pdf", data=build_pdf(["P"]), mime=PDF)

    revisions = (
        await db.execute(
            sa.select(TenderRevision)
            .where(TenderRevision.tender_id == uuid.UUID(tender["id"]))
            .order_by(TenderRevision.revision_number)
        )
    ).scalars().all()
    assert [r.revision_number for r in revisions] == [1, 2]
    assert revisions[1].trigger == "document_processing"
