"""Classificacio documental (capa 3 del motor d'analisi).

Que es protegeix aqui:

* **no depen del canal**: classifica els documents de la licitacio, vinguin d'un
  ZIP o d'una pujada individual. Si el document que faltava arriba per un altre
  cami, la troballa desapareix;
* **el conjunt, no l'entrega**: cada passada mira tots els documents;
* **el classificador proposa i la persona decideix**: la decisio penja del
  document i sobreviu a les reclassificacions; el que es va proposar no es toca;
* **nomes toca el que gestiona**: un tipus triat a ma en pujar un plec no el
  trepitja cap reclassificacio.

El bessó de l'Escala es compara amb el mateix gold que els documents reals.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.classification import (
    TenderDocumentClassificationRun,
    TenderDocumentRoleDecision,
    TenderDocumentRoleSuggestion,
)
from app.models.enums import DownloadStatus, MembershipRole, TenderDocumentType
from app.models.identity import CompanyMembership
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tenders import TenderDocument
from tests.classification_gold import assert_matches_gold, load_gold
from tests.fixtures.pdf_builder import build_pdf
from tests.fixtures.tender_packages import PCAP, PPT, lescala_twin

pytestmark = pytest.mark.asyncio

CREATE = "/api/v1/companies/{company}/tenders"
BASE = "/api/v1/companies/{company}/tenders/{tender}"

INVENTARI = (
    "Telecomunicacions 2027 | Annex d'inventaris\nPagina 1\n"
    "Annex mestre d'inventaris, ubicacions, FTTH i telefonia\nControl Valor\nCodi DOC-04\n"
    + " ".join(["Seu, adreca, linia i extensio de cada equipament municipal."] * 8)
)


async def _tender(client, company) -> dict:
    body = {"client_token": str(uuid.uuid4()), "title": "Telecomunicacions 2027"}
    response = await client.post(CREATE.format(company=company.id), json=body)
    assert response.status_code == 201, response.text
    return response.json()["tender"]


def _base(company, tender) -> str:
    return BASE.format(company=company.id, tender=tender["id"])


async def _receive_twin(client, company, tender) -> dict:
    response = await client.post(
        _base(company, tender) + "/receptions/zip",
        files={"file": ("Escala.zip", lescala_twin(), "application/zip")},
    )
    assert response.status_code == 201, response.text
    return response.json()["classification"]


async def _upload_single(client, company, tender, name, data, document_type="other") -> dict:
    response = await client.post(
        _base(company, tender) + "/documents",
        files={"file": (name, data, "application/pdf")},
        data={"document_type": document_type},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _by_path(classification: dict) -> dict:
    return {d["path"]: d for d in classification["documents"]}


def _codes(classification: dict) -> list[str]:
    return [f["code"] for f in classification["findings"]]


# --------------------------------------------------------------------------- #
# Gold                                                                         #
# --------------------------------------------------------------------------- #


async def test_the_lescala_twin_matches_the_gold(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    classification = await _receive_twin(client_a, company, tender)
    assert_matches_gold(classification, load_gold("lescala-2026"), check_sha256=False)

    # I es el mateix que torna la lectura, sense tornar a classificar.
    read = (await client_a.get(_base(company, tender) + "/document-classification")).json()
    assert read == classification


async def test_only_what_is_safe_reaches_the_analysis(client_a, tenant_a, db):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    await _receive_twin(client_a, company, tender)

    rows = await db.execute(
        sa.select(TenderDocument.name, TenderDocument.document_type).where(
            TenderDocument.tender_id == uuid.UUID(tender["id"])
        )
    )
    analyzable = sorted(
        name
        for name, document_type in rows
        if document_type
        in (
            TenderDocumentType.ADMINISTRATIVE_TERMS,
            TenderDocumentType.TECHNICAL_TERMS,
            TenderDocumentType.ADDITIONAL,
        )
    )
    # Ni la copia mal pujada (04) ni el full de calcul (confianca mitjana).
    assert analyzable == [
        "03_Quadre_Caracteristiques_i_Clausules_Especifiques.pdf",
        "05_Model_Oferta_Economica_i_Criteris_Automatics.pdf",
        "PCAP _1_.pdf",
        "PPT _1_.pdf",
    ]


# --------------------------------------------------------------------------- #
# Independent del canal                                                        #
# --------------------------------------------------------------------------- #


async def test_documents_uploaded_one_by_one_are_classified_too(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    await _upload_single(client_a, company, tender, "PCAP.pdf", build_pdf([PCAP]))
    await _upload_single(client_a, company, tender, "PPT.pdf", build_pdf([PPT]))

    response = await client_a.post(_base(company, tender) + "/document-classification")

    assert response.status_code == 200, response.text
    classification = response.json()
    assert classification["run"]["trigger"] == "request"
    assert classification["run"]["trigger_reception_id"] is None
    documents = _by_path(classification)
    assert (documents["PCAP.pdf"]["role"], documents["PCAP.pdf"]["document_type"]) == (
        "pcap",
        "administrative_terms",
    )
    assert (documents["PPT.pdf"]["role"], documents["PPT.pdf"]["document_type"]) == (
        "ppt",
        "technical_terms",
    )


async def test_the_missing_document_arriving_by_another_route_clears_the_finding(
    client_a, tenant_a
):
    """El DOC-04 que faltava al ZIP arriba despres, sol. La classificacio es de la
    licitacio, no de l'entrega: la passada seguent ja no el troba a faltar."""
    _, company = tenant_a
    tender = await _tender(client_a, company)
    first = await _receive_twin(client_a, company, tender)
    assert "referenced_document_missing" in _codes(first)

    await _upload_single(client_a, company, tender, "DOC-04 Inventaris.pdf", build_pdf([INVENTARI]))
    second = (await client_a.post(_base(company, tender) + "/document-classification")).json()

    assert second["run"]["id"] != first["run"]["id"]
    assert second["run"]["document_count"] == 9
    assert "referenced_document_missing" not in _codes(second)
    inventory = _by_path(second)["DOC-04 Inventaris.pdf"]
    assert (inventory["role"], inventory["document_type"]) == ("inventory_annex", "additional")
    # La copia mal pujada continua sent-ho: no depen de l'entrega.
    assert "duplicate_text" in _codes(second)


async def test_nothing_changed_means_no_new_run(client_a, tenant_a, db):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    first = await _receive_twin(client_a, company, tender)

    again = (await client_a.post(_base(company, tender) + "/document-classification")).json()

    assert again["run"]["id"] == first["run"]["id"]
    assert (
        await db.scalar(sa.select(sa.func.count()).select_from(TenderDocumentClassificationRun))
        == 1
    )


async def test_an_older_run_is_kept_but_is_no_longer_current(client_a, tenant_a, db):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    first = await _receive_twin(client_a, company, tender)
    await _upload_single(client_a, company, tender, "DOC-04.pdf", build_pdf([INVENTARI]))
    await client_a.post(_base(company, tender) + "/document-classification")

    old = await db.get(TenderDocumentClassificationRun, uuid.UUID(first["run"]["id"]))
    await db.refresh(old)
    assert old.is_current is False
    suggestions = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TenderDocumentRoleSuggestion)
        .where(TenderDocumentRoleSuggestion.run_id == old.id)
    )
    assert suggestions == 8


async def test_a_type_chosen_by_hand_is_never_overwritten(client_a, tenant_a):
    """Qui puja un plec sol i en tria el tipus ha decidit. Reclassificar no ho desfa."""
    _, company = tenant_a
    tender = await _tender(client_a, company)
    await _upload_single(
        client_a,
        company,
        tender,
        "document.pdf",
        build_pdf(["Sense titol. " * 40]),
        document_type="technical_terms",
    )
    await _upload_single(client_a, company, tender, "PCAP.pdf", build_pdf([PCAP]))

    classification = (
        await client_a.post(_base(company, tender) + "/document-classification")
    ).json()
    manual = _by_path(classification)["document.pdf"]
    assert (manual["suggested_role"], manual["document_type"]) == ("other", "technical_terms")


# --------------------------------------------------------------------------- #
# La persona decideix                                                          #
# --------------------------------------------------------------------------- #


async def _decide(client, company, tender, document_id, role):
    return await client.put(
        _base(company, tender) + f"/document-classification/documents/{document_id}/role",
        json={"role": role},
    )


async def test_a_person_fixes_a_role_and_the_suggestion_is_kept(client_a, tenant_a, db):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    classification = await _receive_twin(client_a, company, tender)
    sheet = next(d for d in classification["documents"] if d["path"].endswith(".xlsx"))
    assert (sheet["needs_review"], sheet["document_type"]) == (True, "other")

    response = await _decide(client_a, company, tender, sheet["tender_document_id"], "offer_model")

    assert response.status_code == 200, response.text
    fixed = next(
        d
        for d in response.json()["documents"]
        if d["tender_document_id"] == sheet["tender_document_id"]
    )
    assert (fixed["role"], fixed["role_source"], fixed["suggested_role"]) == (
        "offer_model",
        "person",
        "offer_model",
    )
    assert fixed["suggested_confidence"] == "medium"
    assert (fixed["needs_review"], fixed["document_type"]) == (False, "additional")
    assert fixed["decided_at"] is not None

    audit = await db.scalar(
        sa.select(AuditLog).where(AuditLog.entity_type == "tender_document_role")
    )
    assert audit.entity_id == uuid.UUID(sheet["tender_document_id"])
    assert "rol=offer_model" in audit.summary


async def test_a_decision_survives_a_reclassification(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    classification = await _receive_twin(client_a, company, tender)
    copy = next(d for d in classification["documents"] if d["path"].startswith("04_"))
    await _decide(client_a, company, tender, copy["tender_document_id"], "inventory_annex")

    await _upload_single(client_a, company, tender, "DOC-04.pdf", build_pdf([INVENTARI]))
    again = (await client_a.post(_base(company, tender) + "/document-classification")).json()

    kept = next(
        d for d in again["documents"] if d["tender_document_id"] == copy["tender_document_id"]
    )
    assert (kept["role"], kept["role_source"], kept["document_type"]) == (
        "inventory_annex",
        "person",
        "additional",
    )
    # El classificador continua dient el que veu, i la troballa continua sent certa.
    assert kept["suggested_role"] == "characteristics_table"
    assert "duplicate_text" in _codes(again)


async def test_a_role_for_a_document_of_another_tender_is_not_found(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    other = await _tender(client_a, company)
    document = await _upload_single(client_a, company, other, "PCAP.pdf", build_pdf([PCAP]))
    await _receive_twin(client_a, company, tender)

    response = await _decide(client_a, company, tender, document["id"], "pcap")
    assert response.status_code == 404


async def test_an_unknown_role_is_refused(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    classification = await _receive_twin(client_a, company, tender)
    document_id = classification["documents"][0]["tender_document_id"]
    assert (await _decide(client_a, company, tender, document_id, "contracte")).status_code == 422


# --------------------------------------------------------------------------- #
# Permisos i aillament                                                         #
# --------------------------------------------------------------------------- #


async def test_another_company_sees_nothing(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    tender = await _tender(client_a, company_a)
    classification = await _receive_twin(client_a, company_a, tender)
    document_id = classification["documents"][0]["tender_document_id"]

    base = BASE.format(company=company_b.id, tender=tender["id"])
    assert (await client_b.get(base + "/document-classification")).status_code == 404
    assert (await client_b.post(base + "/document-classification")).status_code == 404
    response = await client_b.put(
        base + f"/document-classification/documents/{document_id}/role", json={"role": "pcap"}
    )
    assert response.status_code == 404


@pytest.mark.parametrize("role", ["technical", "viewer"])
async def test_who_does_not_decide_can_read_but_not_decide(client_a, db, tenant_a, role):
    user, company = tenant_a
    tender = await _tender(client_a, company)
    classification = await _receive_twin(client_a, company, tender)
    await db.execute(
        sa.update(CompanyMembership)
        .where(CompanyMembership.user_id == user.id, CompanyMembership.company_id == company.id)
        .values(role=MembershipRole(role))
    )
    await db.commit()

    read = await client_a.get(_base(company, tender) + "/document-classification")
    assert read.status_code == 200
    assert (
        await client_a.post(_base(company, tender) + "/document-classification")
    ).status_code == 404
    document_id = classification["documents"][0]["tender_document_id"]
    assert (await _decide(client_a, company, tender, document_id, "pcap")).status_code == 404


async def test_a_tender_without_documents_has_no_classification(client_a, tenant_a):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    read = (await client_a.get(_base(company, tender) + "/document-classification")).json()
    assert read == {"run": None, "documents": [], "findings": []}
    run = (await client_a.post(_base(company, tender) + "/document-classification")).json()
    assert run["run"] is None


# --------------------------------------------------------------------------- #
# 3B-2: tenders publics, override ancorat a la versio del fitxer               #
# --------------------------------------------------------------------------- #


async def test_a_public_tender_is_refused_for_reclassify_and_decide(client_a, tenant_a, db):
    """Els endpoints tenant no escriuen mai en un tender public: `tender_not_manual`."""
    from tests.test_global_classification import (
        PCAP_PDF,
        make_public_tender,
        processed_feed_document,
    )

    _, company = tenant_a
    tender = await make_public_tender(db)
    pcap = await processed_feed_document(
        db, tender, "PCAP.pdf", TenderDocumentType.ADMINISTRATIVE_TERMS, PCAP_PDF
    )
    base = BASE.format(company=company.id, tender=tender.id)

    assert (await client_a.get(base + "/document-classification")).status_code == 200
    response = await client_a.post(base + "/document-classification")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tender_not_manual"
    response = await client_a.put(
        base + f"/document-classification/documents/{pcap.id}/role", json={"role": "ppt"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tender_not_manual"
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderDocumentRoleDecision)) == 0
    await db.refresh(pcap)
    assert pcap.document_type == TenderDocumentType.ADMINISTRATIVE_TERMS


async def test_a_decision_is_anchored_to_the_current_file_version(client_a, tenant_a, db):
    _, company = tenant_a
    tender = await _tender(client_a, company)
    classification = await _receive_twin(client_a, company, tender)
    document = classification["documents"][0]
    await _decide(client_a, company, tender, document["tender_document_id"], "pcap")

    decision = await db.scalar(
        sa.select(TenderDocumentRoleDecision).where(
            TenderDocumentRoleDecision.tender_document_id
            == uuid.UUID(document["tender_document_id"])
        )
    )
    assert decision.content_sha256 == document["sha256"]


async def test_a_new_file_version_makes_the_decision_stale_and_visible(client_a, tenant_a, db):
    """Un fitxer nou invalida la decisio: es diu (`stale_override`) i torna a revisio."""
    _, company = tenant_a
    tender = await _tender(client_a, company)
    uploaded = await _upload_single(client_a, company, tender, "PCAP.pdf", build_pdf([PCAP]))
    await client_a.post(_base(company, tender) + "/document-classification")
    document_id = uuid.UUID(uploaded["id"])
    assert (await _decide(client_a, company, tender, uploaded["id"], "ppt")).status_code == 200

    # Una versio nova del fitxer (com faria una nova pujada o descarrega).
    old = await db.scalar(
        sa.select(TenderDocumentFile).where(
            TenderDocumentFile.tender_document_id == document_id,
            TenderDocumentFile.is_current.is_(True),
        )
    )
    content = TenderDocumentContent(
        sha256="d" * 64,
        storage_key="tests/versio-nova.pdf",
        file_size=10,
        mime_type="application/pdf",
        is_text_extractable=True,
    )
    db.add(content)
    await db.flush()
    old.is_current = False
    await db.flush()
    db.add(
        TenderDocumentFile(
            tender_document_id=document_id,
            content_id=content.id,
            version_number=old.version_number + 1,
            is_current=True,
            download_status=DownloadStatus.DOWNLOADED,
            origin=old.origin,
        )
    )
    await db.commit()

    read = (await client_a.get(_base(company, tender) + "/document-classification")).json()
    item = next(d for d in read["documents"] if d["tender_document_id"] == uploaded["id"])
    assert item["sha256"] == "d" * 64
    assert item["role_source"] == "stale_override"
    assert item["role"] != "ppt"  # la decisio antiga ja no mana
    assert item["needs_review"] is True


async def test_no_role_proposal_endpoint_exists():
    """3B-2: cap workflow de propostes de rol (ni tenant ni d'operador)."""
    from app.main import app

    paths = [getattr(route, "path", "") for route in app.routes]
    assert not [p for p in paths if "propos" in p and "role" in p]
    assert not [p for p in paths if "role-proposal" in p or "role_proposal" in p]
