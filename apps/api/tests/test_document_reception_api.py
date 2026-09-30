"""Recepcio de documents pel canal ZIP (capa 2 del motor d'analisi).

Tres coses que aquests tests protegeixen:

* **una entrega hostil no deixa rastre**: es rebutja sencera i no es desa ni el
  contenidor ni cap document;
* **els documents son documents normals**: cada fitxer acceptat es un
  `TenderDocument` amb el seu contingut i la seva extraccio, com si s'hagues
  pujat sol, i l'entrega sencera es UNA revisio de la licitacio;
* **la recepcio no classifica**: nomes registra que ha arribat i demana la
  classificacio de la licitacio, que es una capa a part.
"""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.enums import MembershipRole
from app.models.identity import CompanyMembership
from app.models.reception import TenderDocumentReception, TenderDocumentReceptionItem
from app.models.tender_documents import TenderDocumentContent
from app.models.tender_lifecycle import TenderRevision
from app.models.tenders import TenderDocument
from tests.fixtures.pdf_builder import build_pdf
from tests.fixtures.tender_packages import PCAP, PPT, lescala_twin

pytestmark = pytest.mark.asyncio

CREATE = "/api/v1/companies/{company}/tenders"
RECEPTIONS = "/api/v1/companies/{company}/tenders/{tender}/receptions"


async def _tender(client, company) -> dict:
    body = {"client_token": str(uuid.uuid4()), "title": "Telecomunicacions 2027"}
    response = await client.post(CREATE.format(company=company.id), json=body)
    assert response.status_code == 201, response.text
    return response.json()["tender"]


async def _zip_upload(client, company, tender_id, data: bytes, name: str = "paquet.zip"):
    return await client.post(
        RECEPTIONS.format(company=company.id, tender=tender_id) + "/zip",
        files={"file": (name, data, "application/zip")},
    )


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


async def _count(db, model, *where) -> int:
    return await db.scalar(sa.select(sa.func.count()).select_from(model).where(*where))


# --------------------------------------------------------------------------- #
# El cami normal                                                               #
# --------------------------------------------------------------------------- #


async def test_a_zip_is_received_file_by_file(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)

    response = await _zip_upload(client_a, company, tender["id"], lescala_twin(), "Escala.zip")

    assert response.status_code == 201, response.text
    reception = response.json()["reception"]
    assert (reception["channel"], reception["label"], reception["status"]) == (
        "zip_upload",
        "Escala.zip",
        "completed",
    )
    assert (reception["item_count"], reception["accepted_count"], reception["skipped_count"]) == (
        8,
        8,
        0,
    )
    assert [i["path"] for i in reception["items"]][-2:] == [
        "Resta Documentació/Informe de necessitat.pdf",
        "Resta Documentació/Acord JGL PRP2026-2261.docx",
    ]
    assert all(i["tender_document_id"] for i in reception["items"])
    # La recepcio ja ha demanat la classificacio de la licitacio.
    classification = response.json()["classification"]
    assert classification["run"]["trigger"] == "reception"
    assert classification["run"]["trigger_reception_id"] == reception["id"]
    assert response.json()["classification_failed"] is False


async def test_each_accepted_file_is_a_normal_tender_document(client_a, tenant_a, db):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    items = (await _zip_upload(client_a, company, tender["id"], lescala_twin())).json()[
        "reception"
    ]["items"]

    documents = {
        d.id: d
        for d in await db.scalars(
            sa.select(TenderDocument).where(TenderDocument.tender_id == uuid.UUID(tender["id"]))
        )
    }
    assert len(documents) == 8
    for item in items:
        document = documents[uuid.UUID(item["tender_document_id"])]
        assert document.url.startswith("upload://")


async def test_the_original_container_is_kept_as_it_arrived(client_a, tenant_a, db):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    data = lescala_twin()
    body = (await _zip_upload(client_a, company, tender["id"], data)).json()

    reception = await db.get(TenderDocumentReception, uuid.UUID(body["reception"]["id"]))
    container = await db.get(TenderDocumentContent, reception.container_content_id)
    assert container.sha256 == hashlib.sha256(data).hexdigest()
    assert container.mime_type == "application/zip"


async def test_a_whole_delivery_is_one_revision_of_the_tender(client_a, tenant_a, db):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    tender_id = uuid.UUID(tender["id"])
    before = await _count(db, TenderRevision, TenderRevision.tender_id == tender_id)

    await _zip_upload(client_a, company, tender["id"], lescala_twin())

    assert await _count(db, TenderRevision, TenderRevision.tender_id == tender_id) == before + 1


async def test_the_audit_says_which_channel_and_how_many(client_a, tenant_a, db):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    body = (await _zip_upload(client_a, company, tender["id"], lescala_twin())).json()
    entry = await db.scalar(
        sa.select(AuditLog).where(AuditLog.entity_type == "tender_document_reception")
    )
    assert entry.entity_id == uuid.UUID(body["reception"]["id"])
    assert "canal=zip_upload" in entry.summary and "acceptats=8" in entry.summary
    assert ".pdf" not in entry.summary  # cap nom de fitxer


# --------------------------------------------------------------------------- #
# Fitxers omesos i entregues rebutjades                                        #
# --------------------------------------------------------------------------- #


async def test_skipped_files_are_listed_with_their_reason(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    data = _zip(
        [
            ("PCAP.pdf", build_pdf([PCAP])),
            ("PPT.pdf", build_pdf([PPT])),
            ("__MACOSX/._PCAP.pdf", b"x"),
            ("buit.pdf", b""),
            ("imatge.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20),
            ("fals.pdf", b"no soc cap pdf"),
        ]
    )
    reception = (await _zip_upload(client_a, company, tender["id"], data)).json()["reception"]
    skipped = {
        i["path"]: (i["skip_reason"], i["needs_attention"])
        for i in reception["items"]
        if i["status"] == "skipped"
    }
    # El soroll de l'eina que va fer el ZIP no reclama atencio.
    assert skipped == {
        "__MACOSX/._PCAP.pdf": ("system_file", False),
        "buit.pdf": ("empty", True),
        "imatge.png": ("unsupported_type", True),
        "fals.pdf": ("type_mismatch", True),
    }
    assert (reception["accepted_count"], reception["skipped_count"]) == (2, 4)
    assert all(i["tender_document_id"] is None for i in reception["items"][2:])


@pytest.mark.parametrize(
    ("entries", "reason"),
    [
        ([("../fora.pdf", b"%PDF-1")], "unsafe_path"),
        ([("bomba.pdf", b"\x00" * (8 * 1024 * 1024))], "compression_ratio"),
    ],
)
async def test_a_hostile_package_is_rejected_and_leaves_nothing(
    client_a, tenant_a, db, entries, reason
):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    contents_before = await _count(db, TenderDocumentContent)

    response = await _zip_upload(client_a, company, tender["id"], _zip(entries))

    assert response.status_code == 422
    error = response.json()["error"]
    assert (error["code"], error["details"]["reason"]) == ("archive_rejected", reason)
    assert await _count(db, TenderDocumentReception) == 0
    tender_id = uuid.UUID(tender["id"])
    assert await _count(db, TenderDocument, TenderDocument.tender_id == tender_id) == 0
    assert await _count(db, TenderDocumentContent) == contents_before


async def test_a_pdf_is_not_a_package(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    response = await _zip_upload(client_a, company, tender["id"], build_pdf(["PCAP"]), "PCAP.pdf")
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "not_a_zip"


async def test_the_package_size_is_capped_while_reading(client_a, tenant_a, monkeypatch):
    from app.core.config import settings

    _, company = tenant_a
    tender = await _tender(client_a, company)
    monkeypatch.setattr(settings, "INTAKE_MAX_ARCHIVE_MB", 0)
    response = await _zip_upload(client_a, company, tender["id"], lescala_twin())
    assert response.status_code == 413


# --------------------------------------------------------------------------- #
# Errors a mig cami                                                            #
# --------------------------------------------------------------------------- #


async def test_a_failure_while_storing_is_recorded_not_hidden(client_a, tenant_a, db, monkeypatch):
    """Si peta desant, la recepcio queda `failed` i els documents ja desats hi son.

    Son plecs reals, desats be: esborrar-los amagaria que l'entrega va arribar.
    """
    from app.models.identity import User
    from app.models.tenders import Tender
    from app.reception.zip_channel import delivery_from_zip
    from app.services import document_reception

    user, company = tenant_a
    tender = await _tender(client_a, company)
    real = document_reception.store_uploaded_document
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("storage caigut")
        return await real(*args, **kwargs)

    monkeypatch.setattr(document_reception, "store_uploaded_document", flaky)
    with pytest.raises(RuntimeError):
        await document_reception.receive(
            db,
            tender=await db.get(Tender, uuid.UUID(tender["id"])),
            company_id=company.id,
            user=await db.get(User, user.id),
            delivery=delivery_from_zip(
                lescala_twin(), "Escala.zip", document_reception.zip_limits()
            ),
        )

    reception = await db.scalar(sa.select(TenderDocumentReception))
    await db.refresh(reception)
    assert (reception.status, reception.error_code) == ("failed", "processing_error")
    tender_id = uuid.UUID(tender["id"])
    assert await _count(db, TenderDocument, TenderDocument.tender_id == tender_id) == 3


async def test_a_classification_failure_does_not_undo_the_reception(
    client_a, tenant_a, monkeypatch
):
    from app.services import document_classification

    _, company = tenant_a
    tender = await _tender(client_a, company)

    def boom(_documents):
        raise RuntimeError("classificador caigut")

    monkeypatch.setattr(document_classification, "classify_documents", boom)
    response = await _zip_upload(client_a, company, tender["id"], lescala_twin())

    assert response.status_code == 201
    body = response.json()
    assert body["reception"]["status"] == "completed"
    assert body["classification_failed"] is True
    assert body["classification"]["run"] is None
    # Es pot tornar a demanar quan el classificador torni a funcionar.
    monkeypatch.undo()
    retry = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders/{tender['id']}/document-classification"
    )
    assert retry.status_code == 200
    assert retry.json()["run"]["trigger"] == "request"


# --------------------------------------------------------------------------- #
# Permisos i aillament                                                         #
# --------------------------------------------------------------------------- #


async def test_another_company_can_neither_send_nor_read(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    tender = await _tender(client_a, company_a)
    body = (await _zip_upload(client_a, company_a, tender["id"], lescala_twin())).json()

    assert (await _zip_upload(client_b, company_b, tender["id"], lescala_twin())).status_code == 404
    base = RECEPTIONS.format(company=company_b.id, tender=tender["id"])
    assert (await client_b.get(base)).status_code == 404
    assert (await client_b.get(f"{base}/{body['reception']['id']}")).status_code == 404


async def test_a_public_tender_does_not_accept_deliveries(client_a, tenant_a, tender_document):
    _, company = tenant_a
    response = await _zip_upload(client_a, company, tender_document.tender_id, lescala_twin())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tender_not_manual"


@pytest.mark.parametrize("role", ["technical", "viewer"])
async def test_who_does_not_decide_does_not_send_but_can_read(client_a, db, tenant_a, role):
    user, company = tenant_a
    tender = await _tender(client_a, company)
    body = (await _zip_upload(client_a, company, tender["id"], lescala_twin())).json()
    await db.execute(
        sa.update(CompanyMembership)
        .where(CompanyMembership.user_id == user.id, CompanyMembership.company_id == company.id)
        .values(role=MembershipRole(role))
    )
    await db.commit()

    assert (await _zip_upload(client_a, company, tender["id"], lescala_twin())).status_code == 404
    base = RECEPTIONS.format(company=company.id, tender=tender["id"])
    listing = await client_a.get(base)
    assert listing.status_code == 200
    assert [r["id"] for r in listing.json()] == [body["reception"]["id"]]
    assert (await client_a.get(f"{base}/{body['reception']['id']}")).status_code == 200


# --------------------------------------------------------------------------- #
# La base imposa les regles                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "change",
    [
        {"status": "skipped"},  # omes sense motiu
        {"skip_reason": "empty"},  # motiu sense estar omes
        {"status": "expanded", "skip_reason": None},  # un contenidor obert no dona document
        {"status": "desconegut"},  # estat fora de l'enum
    ],
)
async def test_the_database_refuses_inconsistent_items(client_a, tenant_a, db, change):
    from sqlalchemy.exc import IntegrityError, StatementError

    _, company = tenant_a
    tender = await _tender(client_a, company)
    body = (await _zip_upload(client_a, company, tender["id"], lescala_twin())).json()
    item_id = uuid.UUID(body["reception"]["items"][0]["id"])

    with pytest.raises((IntegrityError, StatementError)):
        await db.execute(
            sa.update(TenderDocumentReceptionItem)
            .where(TenderDocumentReceptionItem.id == item_id)
            .values(**change)
        )
        await db.flush()
    await db.rollback()


async def test_deleting_the_tender_takes_its_receptions_with_it(client_a, tenant_a, db):
    from app.models.classification import TenderDocumentClassificationRun
    from app.models.tenders import Tender

    _, company = tenant_a
    tender = await _tender(client_a, company)
    await _zip_upload(client_a, company, tender["id"], lescala_twin())
    await db.execute(sa.delete(Tender).where(Tender.id == uuid.UUID(tender["id"])))
    await db.commit()
    assert await _count(db, TenderDocumentReception) == 0
    assert await _count(db, TenderDocumentReceptionItem) == 0
    assert await _count(db, TenderDocumentClassificationRun) == 0
