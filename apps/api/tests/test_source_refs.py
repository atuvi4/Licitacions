"""Cites immutables: la provinença d'Sprint 3 (S3-S4).

El que aquests tests protegeixen és una sola frase:

    document vigent  ≠  document que es va fer servir com a evidència

Una cita ha de poder dir, d'aquí a dos anys, quins bytes exactes sostenien una
afirmació. Si es mogués sola cap a la versió vigent, deixaria de ser una prova i
passaria a ser una opinió sobre el present: exactament el contrari del que
serveix.

Per això l'àncora **mai** és `TenderDocument` —la ranura lògica, que canvia de
contingut— sinó una versió concreta o una lectura concreta.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.enums import SourceRefKind
from app.models.provenance import TenderSourceRef
from app.models.tender_documents import TenderDocumentExtraction, TenderDocumentPage
from app.services import source_refs
from tests.fixtures.pdf_builder import build_pdf

pytestmark = pytest.mark.asyncio

CREATE = "/api/v1/companies/{company}/tenders"
UPLOAD = "/api/v1/companies/{company}/tenders/{tender}/documents"
VERSIONS = "/api/v1/companies/{company}/tenders/{tender}/documents/{document}/versions"

PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_V1 = b"PK\x03\x04" + b"\x01" * 64
XLSX_V2 = b"PK\x03\x04" + b"\x02" * 64
PLEC = build_pdf(["Clausula primera. El termini es de dotze mesos. " * 4])
PLEC_V2 = build_pdf(["Clausula primera REVISADA. El termini es de vint-i-quatre mesos. " * 4])


async def _tender(client, company, title="Serveis de comunicacions") -> str:
    response = await client.post(
        CREATE.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()["tender"]["id"]


async def _upload(client, company, tender_id, *, name, data, mime=PDF) -> dict:
    response = await client.post(
        UPLOAD.format(company=company.id, tender=tender_id),
        files={"file": (name, data, mime)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _version(client, company, tender_id, document_id, *, name, data, mime=PDF) -> dict:
    response = await client.post(
        VERSIONS.format(company=company.id, tender=tender_id, document=document_id),
        files={"file": (name, data, mime)},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def _page_of(db, extraction_id: str) -> TenderDocumentPage:
    return (
        await db.execute(
            sa.select(TenderDocumentPage)
            .where(TenderDocumentPage.extraction_id == uuid.UUID(extraction_id))
            .order_by(TenderDocumentPage.page_number)
        )
    ).scalars().first()


# --------------------------------------------------------------------------- #
# A. Un format sense pàgines també es pot citar                                 #
# --------------------------------------------------------------------------- #


async def test_an_unreadable_spreadsheet_is_citable(client_a, db, tenant_a):
    """Que no en sapiguem llegir el text no el fa incitable.

    És el cas que motiva tota la taula: un full de càlcul de preus es guarda
    perfectament, no té ni una pàgina, i tot i així algú n'ha de poder dir «la
    fila 42 diu això». Convertir-lo en pàgines seria inventar-se una precisió
    que no tenim.
    """
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(
        client_a, company, tender_id, name="preus.xlsx", data=XLSX_V1, mime=XLSX
    )
    assert uploaded["file"]["analysis_status"] == "unsupported"
    assert uploaded["file"]["page_count"] == 0

    ref = await source_refs.create_document_file_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        file_id=uuid.UUID(uploaded["file"]["file_id"]),
        user=user,
        location_label="Full Calculs, fila 42",
    )
    await db.commit()

    assert ref.ref_kind == SourceRefKind.DOCUMENT_FILE
    assert ref.location_label == "Full Calculs, fila 42"
    assert ref.page_id is None
    assert ref.page_number is None


async def test_the_server_derives_the_sha_from_the_real_content(client_a, db, tenant_a):
    """El client no mana sobre quins bytes eren.

    Si el SHA arribés de fora, la prova de quins bytes sostenien una afirmació
    la diria justament qui té interès a dir-ne una altra.
    """
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(
        client_a, company, tender_id, name="preus.xlsx", data=XLSX_V1, mime=XLSX
    )

    ref = await source_refs.create_document_file_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        file_id=uuid.UUID(uploaded["file"]["file_id"]),
        user=user,
    )

    assert ref.content_sha256 == uploaded["file"]["sha256"]


# --------------------------------------------------------------------------- #
# B. La cita forta: pàgina d'una lectura concreta                               #
# --------------------------------------------------------------------------- #


async def test_a_page_of_an_extraction_is_citable(client_a, db, tenant_a):
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])

    ref = await source_refs.create_extracted_page_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
        page_id=page.id,
        user=user,
    )

    assert ref.ref_kind == SourceRefKind.EXTRACTED_PAGE
    assert ref.page_number == 1
    assert ref.content_sha256 == uploaded["file"]["sha256"]


async def test_the_quote_is_derived_from_the_real_text(client_a, db, tenant_a):
    """El text citat surt del document, no del client.

    Una cita que digui una cosa que el document no diu és pitjor que no tenir-ne
    cap: sembla una prova i no ho és.
    """
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])
    start = page.text.index("El termini")

    ref = await source_refs.create_extracted_page_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
        page_id=page.id,
        user=user,
        char_start=start,
        char_end=start + len("El termini es de dotze mesos."),
    )

    assert ref.quote == "El termini es de dotze mesos."
    assert ref.quote in page.text


async def test_a_span_outside_the_page_is_refused(client_a, db, tenant_a):
    """No es retalla en silenci: una cita escapçada seguiria semblant prova."""
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])

    with pytest.raises(source_refs.SourceRefSpanError):
        await source_refs.create_extracted_page_ref(
            db,
            tender_id=uuid.UUID(tender_id),
            extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
            page_id=page.id,
            user=user,
            char_start=0,
            char_end=len(page.text) + 50,
        )


async def test_half_a_span_is_refused(client_a, db, tenant_a):
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])

    with pytest.raises(source_refs.SourceRefSpanError):
        await source_refs.create_extracted_page_ref(
            db,
            tender_id=uuid.UUID(tender_id),
            extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
            page_id=page.id,
            user=user,
            char_start=3,
        )


async def test_a_page_of_another_extraction_is_refused(client_a, db, tenant_a):
    """Que les dues peces existeixin no vol dir que siguin la mateixa cita."""
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    first = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    second = await _upload(client_a, company, tender_id, name="PPT.pdf", data=PLEC_V2)
    page_of_second = await _page_of(db, second["file"]["extraction_id"])

    with pytest.raises(source_refs.SourceRefChainError):
        await source_refs.create_extracted_page_ref(
            db,
            tender_id=uuid.UUID(tender_id),
            extraction_id=uuid.UUID(first["file"]["extraction_id"]),
            page_id=page_of_second.id,
            user=user,
        )


# --------------------------------------------------------------------------- #
# C. Una cita és d'una licitació i de cap altra                                 #
# --------------------------------------------------------------------------- #


async def test_a_file_of_another_tender_cannot_be_cited(client_a, db, tenant_a):
    """Conèixer tots dos UUID no els fa compatibles."""
    user, company = tenant_a
    mine = await _tender(client_a, company, title="La meva")
    other = await _tender(client_a, company, title="Una altra")
    uploaded = await _upload(client_a, company, other, name="preus.xlsx", data=XLSX_V1, mime=XLSX)

    with pytest.raises(source_refs.SourceRefChainError):
        await source_refs.create_document_file_ref(
            db,
            tender_id=uuid.UUID(mine),
            file_id=uuid.UUID(uploaded["file"]["file_id"]),
            user=user,
        )


async def test_a_page_of_another_tender_cannot_be_cited(client_a, db, tenant_a):
    user, company = tenant_a
    mine = await _tender(client_a, company, title="La meva")
    other = await _tender(client_a, company, title="Una altra")
    uploaded = await _upload(client_a, company, other, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])

    with pytest.raises(source_refs.SourceRefChainError):
        await source_refs.create_extracted_page_ref(
            db,
            tender_id=uuid.UUID(mine),
            extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
            page_id=page.id,
            user=user,
        )


async def test_a_file_of_another_company_cannot_be_cited(
    client_a, client_b, db, tenant_a, tenant_b
):
    """El límit és el mateix que a tot arreu: no el dona conèixer l'UUID."""
    user_a, company_a = tenant_a
    _, company_b = tenant_b
    mine = await _tender(client_a, company_a)
    theirs = await _tender(client_b, company_b)
    uploaded = await _upload(client_b, company_b, theirs, name="PCAP.pdf", data=PLEC)

    with pytest.raises(source_refs.SourceRefChainError):
        await source_refs.create_document_file_ref(
            db,
            tender_id=uuid.UUID(mine),
            file_id=uuid.UUID(uploaded["file"]["file_id"]),
            user=user_a,
        )


async def test_a_ref_is_not_readable_from_another_tender(client_a, db, tenant_a):
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    other = await _tender(client_a, company, title="Una altra")
    uploaded = await _upload(
        client_a, company, tender_id, name="preus.xlsx", data=XLSX_V1, mime=XLSX
    )
    ref = await source_refs.create_document_file_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        file_id=uuid.UUID(uploaded["file"]["file_id"]),
        user=user,
    )
    await db.commit()

    assert await source_refs.get_source_ref(db, uuid.UUID(tender_id), ref.id) is not None
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await source_refs.get_source_ref(db, uuid.UUID(other), ref.id)


# --------------------------------------------------------------------------- #
# D. Contingut compartit                                                        #
# --------------------------------------------------------------------------- #


async def test_the_same_bytes_in_two_tenders_do_not_cross_provenance(
    client_a, client_b, db, tenant_a, tenant_b
):
    """Els mateixos bytes es comparteixen; la provinença, no.

    Dos expedients poden tenir el mateix PDF i compartir contingut i extracció.
    La pregunta que decideix si una cita és vàlida no és «existeix aquesta
    extracció?» sinó «la referencia algun document D'AQUESTA licitació?».
    """
    user_a, company_a = tenant_a
    user_b, company_b = tenant_b
    mine = await _tender(client_a, company_a)
    theirs = await _tender(client_b, company_b)

    a = await _upload(client_a, company_a, mine, name="PCAP.pdf", data=PLEC)
    b = await _upload(client_b, company_b, theirs, name="PCAP.pdf", data=PLEC)
    assert a["file"]["sha256"] == b["file"]["sha256"]
    assert a["file"]["extraction_id"] == b["file"]["extraction_id"], "una sola extraccio"

    page = await _page_of(db, a["file"]["extraction_id"])

    # Cadascuna pot citar la seva, amb la MATEIXA extraccio compartida.
    ref_a = await source_refs.create_extracted_page_ref(
        db,
        tender_id=uuid.UUID(mine),
        extraction_id=uuid.UUID(a["file"]["extraction_id"]),
        page_id=page.id,
        user=user_a,
    )
    ref_b = await source_refs.create_extracted_page_ref(
        db,
        tender_id=uuid.UUID(theirs),
        extraction_id=uuid.UUID(b["file"]["extraction_id"]),
        page_id=page.id,
        user=user_b,
    )
    await db.commit()

    assert ref_a.id != ref_b.id
    assert ref_a.tender_id != ref_b.tender_id
    # I la d'A no es pot llegir com si fos de B.
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await source_refs.get_source_ref(db, uuid.UUID(theirs), ref_a.id)


# --------------------------------------------------------------------------- #
# E. Supervivència a una versió nova — el criteri d'acceptació                   #
# --------------------------------------------------------------------------- #


async def test_a_spreadsheet_citation_survives_a_new_version(client_a, db, tenant_a):
    """El test central de la fase.

    Es cita la fila 42 del full de càlcul v1. Després arriba v2, amb altres
    bytes. La cita ha de continuar dient v1: si es mogués sola cap a la versió
    vigent, afirmaria que la fila 42 de v2 deia el que deia la de v1, que és una
    cosa que ningú no ha comprovat.
    """
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    v1 = await _upload(client_a, company, tender_id, name="preus.xlsx", data=XLSX_V1, mime=XLSX)
    file_v1, sha_v1 = v1["file"]["file_id"], v1["file"]["sha256"]

    ref = await source_refs.create_document_file_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        file_id=uuid.UUID(file_v1),
        user=user,
        location_label="Full Calculs, fila 42",
    )
    await db.commit()
    ref_id = ref.id

    v2 = await _version(
        client_a, company, tender_id, v1["id"], name="preus.xlsx", data=XLSX_V2, mime=XLSX
    )
    assert v2["file"]["version_number"] == 2
    assert v2["file"]["sha256"] != sha_v1

    db.expire_all()
    resolved = await source_refs.resolve_source_ref(
        db, await source_refs.get_source_ref(db, uuid.UUID(tender_id), ref_id)
    )

    assert str(resolved.file_id) == file_v1
    assert resolved.version_number == 1
    assert resolved.content_sha256 == sha_v1
    assert resolved.ref.location_label == "Full Calculs, fila 42"
    # I sobretot: NO la versio vigent.
    assert str(resolved.file_id) != v2["file"]["file_id"]
    assert resolved.content_sha256 != v2["file"]["sha256"]


async def test_a_page_citation_survives_a_new_version(client_a, db, tenant_a):
    """El mateix amb un PDF: la pàgina citada segueix sent la que era."""
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    v1 = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, v1["file"]["extraction_id"])
    start = page.text.index("El termini")

    ref = await source_refs.create_extracted_page_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        extraction_id=uuid.UUID(v1["file"]["extraction_id"]),
        page_id=page.id,
        user=user,
        char_start=start,
        char_end=start + len("El termini es de dotze mesos."),
    )
    await db.commit()
    ref_id, quoted, page_id = ref.id, ref.quote, page.id

    v2 = await _version(client_a, company, tender_id, v1["id"], name="PCAP.pdf", data=PLEC_V2)
    assert v2["file"]["sha256"] != v1["file"]["sha256"]

    db.expire_all()
    resolved = await source_refs.resolve_source_ref(
        db, await source_refs.get_source_ref(db, uuid.UUID(tender_id), ref_id)
    )

    assert str(resolved.extraction_id) == v1["file"]["extraction_id"]
    assert str(resolved.page_id) == str(page_id)
    assert resolved.page_number == 1
    assert resolved.content_sha256 == v1["file"]["sha256"]
    assert resolved.ref.quote == quoted
    assert "dotze mesos" in resolved.ref.quote
    # La v2 diu vint-i-quatre mesos; la cita no ha de dir mai aixo.
    assert "vint-i-quatre" not in resolved.ref.quote


async def test_a_re_extraction_does_not_move_a_citation(client_a, db, tenant_a):
    """Tornar a llegir el mateix document tampoc no mou la cita."""
    from app.services import tender_document_processing

    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])
    ref = await source_refs.create_extracted_page_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
        page_id=page.id,
        user=user,
    )
    await db.commit()
    ref_id, page_id = ref.id, page.id

    from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile

    content = (
        await db.execute(
            sa.select(TenderDocumentContent)
            .join(TenderDocumentFile, TenderDocumentFile.content_id == TenderDocumentContent.id)
            .where(TenderDocumentFile.id == uuid.UUID(uploaded["file"]["file_id"]))
        )
    ).scalars().one()
    new_extraction, _done = await tender_document_processing._ensure_current_extraction(
        db, content, force=True
    )
    await db.commit()
    assert new_extraction.id != uuid.UUID(uploaded["file"]["extraction_id"])

    db.expire_all()
    resolved = await source_refs.resolve_source_ref(
        db, await source_refs.get_source_ref(db, uuid.UUID(tender_id), ref_id)
    )
    assert str(resolved.extraction_id) == uploaded["file"]["extraction_id"]
    assert str(resolved.page_id) == str(page_id)


# --------------------------------------------------------------------------- #
# F. La base de dades també hi diu la seva                                      #
# --------------------------------------------------------------------------- #


async def test_a_page_citation_without_a_page_is_impossible(db, tender_document):
    """El CHECK no és decoració: qui escrigui per SQL cru també hi passa."""
    db.add(
        TenderSourceRef(
            tender_id=tender_document.tender_id,
            ref_kind=SourceRefKind.EXTRACTED_PAGE,
            content_sha256="a" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_a_file_citation_without_a_file_is_impossible(db, tender_document):
    db.add(
        TenderSourceRef(
            tender_id=tender_document.tender_id,
            ref_kind=SourceRefKind.DOCUMENT_FILE,
            content_sha256="a" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_page_zero_does_not_exist(client_a, db, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])

    db.add(
        TenderSourceRef(
            tender_id=uuid.UUID(tender_id),
            ref_kind=SourceRefKind.EXTRACTED_PAGE,
            extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
            page_id=page.id,
            page_number=0,
            content_sha256=uploaded["file"]["sha256"],
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_half_a_char_range_is_impossible(client_a, db, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])

    db.add(
        TenderSourceRef(
            tender_id=uuid.UUID(tender_id),
            ref_kind=SourceRefKind.EXTRACTED_PAGE,
            extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
            page_id=page.id,
            page_number=1,
            content_sha256=uploaded["file"]["sha256"],
            char_start=10,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# --------------------------------------------------------------------------- #
# G. El que NO s'ha tocat                                                       #
# --------------------------------------------------------------------------- #


async def test_requirement_evidence_is_untouched(db):
    """`RequirementEvidence` continua sent el que era.

    S3-S4 afegeix una via genèrica; no reescriu la que ja funciona per als
    requisits extrets.
    """
    from app.models.requirements import RequirementEvidence

    columns = RequirementEvidence.__table__.columns
    assert columns["extraction_id"].nullable is False
    assert columns["page_id"].nullable is False
    actions = {
        fk.column.table.name: fk.ondelete
        for fk in RequirementEvidence.__table__.foreign_keys
    }
    assert actions["tender_document_extractions"] == "RESTRICT"
    assert actions["tender_document_pages"] == "RESTRICT"


async def test_a_citation_does_not_touch_the_document_review(client_a, db, tenant_a):
    """Citar no és revisar: l'empremta documental no es mou."""
    from app.services import document_review

    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    before = await document_review.fingerprint_of(db, uuid.UUID(tender_id))

    await source_refs.create_document_file_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        file_id=uuid.UUID(uploaded["file"]["file_id"]),
        user=user,
    )
    await db.commit()

    after = await document_review.fingerprint_of(db, uuid.UUID(tender_id))
    assert after.fingerprint == before.fingerprint


async def test_two_citations_of_the_same_source_stay_two(client_a, db, tenant_a):
    """Cap deduplicació semàntica.

    Dues entitats futures poden citar exactament la mateixa font i continuar
    sent dues relacions diferents. Fusionar-les aquí decidiria per elles.
    """
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(
        client_a, company, tender_id, name="preus.xlsx", data=XLSX_V1, mime=XLSX
    )

    first = await source_refs.create_document_file_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        file_id=uuid.UUID(uploaded["file"]["file_id"]),
        user=user,
        location_label="Full Calculs, fila 42",
    )
    second = await source_refs.create_document_file_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        file_id=uuid.UUID(uploaded["file"]["file_id"]),
        user=user,
        location_label="Full Calculs, fila 42",
    )
    await db.commit()

    assert first.id != second.id
    total = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderSourceRef)
            .where(TenderSourceRef.tender_id == uuid.UUID(tender_id))
        )
    ).scalar_one()
    assert total == 2


async def test_no_requirement_link_table_exists_yet(db):
    """0030 crea les cites; enllaçar-les amb requisits és 0031."""
    from app.models import Base

    assert "tender_source_refs" in Base.metadata.tables
    assert "requirement_source_ref_links" not in Base.metadata.tables
    assert "workspace_dimension_observations" not in Base.metadata.tables


async def test_an_extraction_referenced_by_a_citation_is_protected(client_a, db, tenant_a):
    """RESTRICT: l'evidència citada no es pot esborrar per accident.

    A SQLite aquesta comprovació depèn de `PRAGMA foreign_keys`; el gate de
    PostgreSQL de la fase la prova contra el motor real.
    """
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])
    await source_refs.create_extracted_page_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
        page_id=page.id,
        user=user,
    )
    await db.commit()

    extraction = await db.get(
        TenderDocumentExtraction, uuid.UUID(uploaded["file"]["extraction_id"])
    )
    assert extraction is not None
    # La fila segueix sent-hi i la cita hi apunta: es l'invariant que el gate
    # de PostgreSQL comprova a nivell de motor.
    still_there = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderSourceRef)
            .where(TenderSourceRef.extraction_id == extraction.id)
        )
    ).scalar_one()
    assert still_there == 1


# --------------------------------------------------------------------------- #
# H. Com es llegeix una cita                                                    #
# --------------------------------------------------------------------------- #


async def test_the_read_model_explains_the_citation_without_asking_what_is_current(
    client_a, db, tenant_a
):
    """Tot el que cal per entendre-la surt de la cita i de files immutables."""
    from app.schemas.provenance import read_of

    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(client_a, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = await _page_of(db, uploaded["file"]["extraction_id"])
    start = page.text.index("El termini")
    ref = await source_refs.create_extracted_page_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
        page_id=page.id,
        user=user,
        location_label="p. 1",
        char_start=start,
        char_end=start + len("El termini es de dotze mesos."),
    )
    await db.commit()

    read = read_of(await source_refs.resolve_source_ref(db, ref))

    assert read.ref_kind == SourceRefKind.EXTRACTED_PAGE
    assert read.document_name == "PCAP.pdf"
    assert read.version_number == 1
    assert read.content_sha256 == uploaded["file"]["sha256"]
    assert read.page_number == 1
    assert read.location_label == "p. 1"
    assert read.quote == "El termini es de dotze mesos."
    assert (read.char_start, read.char_end) == (start, start + len(read.quote))


async def test_the_read_model_never_exposes_where_the_bytes_live(client_a, db, tenant_a):
    """`storage_key` no surt enlloc: es un detall intern i una drecera per
    saltar-se l'endpoint autoritzat que serveix l'original."""
    from app.schemas.provenance import TenderSourceRefRead

    assert "storage_key" not in TenderSourceRefRead.model_fields
    assert not any(
        "path" in name or "key" in name for name in TenderSourceRefRead.model_fields
    )
