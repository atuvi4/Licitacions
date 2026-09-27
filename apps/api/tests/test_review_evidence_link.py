"""Evidencia addicional sobre un element existent (`link_source`, S-R4). Zero crides.

Forma de Palamos M09, SINTETICA: el requisit automatic de connectivitat de
nivell 2 te la seva evidencia a la pagina 27 (fa de p31, la nota del
dimensionament) i el revisor hi afegeix la cita de la pagina 28 (fa de p22,
§8.1.1.4, on el plec ho diu de primera ma).
"""

from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.enums import (
    ExtractionQuality,
    ExtractionStatus,
    MembershipRole,
    PageExtractionStatus,
    ReviewSourceRole,
)
from app.models.identity import CompanyMembership, User
from app.models.provenance import TenderSourceRef
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.review import ReviewItem, ReviewItemRevision, ReviewItemSource
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender, TenderDocument
from tests import test_requirement_analysis as base
from tests.conftest import TEST_PASSWORD, login
from tests.test_requirement_analysis import PAGE_27, PAGE_28
from tests.test_requirement_analysis_v3 import _evidence, _requirement, _run_v3

analyzable = base.analyzable
_clean_provider = base._clean_provider

TITLE = "Connectivitat a nivell 2 amb el datacenter pels equipaments municipals"
P31_LINE = ("Cal recordar que al punt 8.1.1 és un requeriment que els equipaments municipals "
            "tinguin connectivitat a nivell 2 amb el datacenter.")
P22_LINE = ("4. Tots els equipaments de l’Ajuntament hauran de disposar de connectivitat a "
            "nivell 2 amb el datacenter on es gestionin les trucades.")


@pytest.fixture
async def m09(session, analyzable, tenant_a, client_a):
    """El requisit automatic (evidencia a p27) i el seu element de revisio d'A."""
    p27, p28 = analyzable["pages"]
    p27.text = PAGE_27 + P31_LINE + "\n"
    p28.text = PAGE_28 + P22_LINE + "\n"
    await session.commit()
    x = analyzable["extraction"].id
    await _run_v3(session, analyzable, {"requirements": [_requirement(
        x, category="technical_requirement", operator=None, title=TITLE, claims=[],
        claims_complete="complete", evidence=[_evidence(x, quote=P31_LINE)],
    )], "award_candidates": []})
    _, company = tenant_a
    tid = analyzable["document"].tender_id
    assert (await client_a.post(_url(company, tid, "/start"))).status_code == 200
    requirement = await session.scalar(sa.select(Requirement).where(Requirement.title == TITLE))
    item_id = await session.scalar(
        sa.select(ReviewItemSource.item_id).where(
            ReviewItemSource.requirement_id == requirement.id))
    start = (PAGE_28 + P22_LINE).index(P22_LINE)
    return {
        "tid": tid, "item_id": item_id, "company": company,
        "citation": {"kind": "extracted_page", "extraction_id": str(x),
                     "page_id": str(p28.id), "char_start": start,
                     "char_end": start + len(P22_LINE), "location_label": "§8.1.1.4"},
    }


def _url(company, tender_id, suffix=""):
    return f"/api/v1/companies/{company.id}/tenders/{tender_id}/review{suffix}"


async def _link(client, m, expected=1, citation=None, company=None, item_id=None, note=None):
    payload = {"citation": citation or m["citation"]}
    if note:
        payload["note"] = note
    return await client.post(
        _url(company or m["company"], m["tid"], f"/items/{item_id or m['item_id']}/actions"),
        json={"action": "link_source", "expected_revision": expected, "payload": payload},
    )


async def _raw(session):
    snapshot = {}
    for model in (Requirement, RequirementEvidence, AwardCriterion, AwardCriterionEvidence,
                  RequirementExtractionRun):
        table = model.__table__
        snapshot[table.name] = [dict(r) for r in (await session.execute(
            sa.select(table).order_by(table.c.id))).mappings()]
    return snapshot


async def _counts(session):
    return [await session.scalar(sa.select(sa.func.count()).select_from(model))
            for model in (ReviewItemSource, ReviewItemRevision, TenderSourceRef)]


# --------------------------------------------------------------------------- #
# M09                                                                           #
# --------------------------------------------------------------------------- #


async def test_m09_la_cita_de_p22_s_afegeix_sense_verificar_res(session, m09, client_a):
    company, tid, item_id = m09["company"], m09["tid"], m09["item_id"]
    before = (await client_a.get(_url(company, tid, f"/requirements/{item_id}"))).json()
    item = await session.get(ReviewItem, item_id)
    accepted_before, status_before = dict(item.accepted_json), item.verification_status
    raw_before = await _raw(session)

    response = await _link(client_a, m09, note="Tambe ho diu la p22")

    assert response.status_code == 200, response.text
    after = response.json()
    assert after["review_revision"] == before["review_revision"] + 1
    assert after["verification_status"] == before["verification_status"]
    assert (after["provenance"], after["provenance_label"]) == (
        "automated", "Extret automàticament")
    assert after["values"] == before["values"]
    # L'evidencia automatica original hi continua, i la cita nova s'hi afegeix.
    assert [(e["page"], e["quote"], e["status_label"]) for e in after["evidence"]] == [
        (27, P31_LINE, before["evidence"][0]["status_label"]),
        (28, P22_LINE, "Citat manualment"),
    ]
    assert after["evidence"][1]["section"] == "§8.1.1.4"
    assert [s["kind"] for s in after["source_refs"]] == ["extracted_requirement", "citation"]
    history = (await client_a.get(_url(company, tid, f"/items/{item_id}/history"))).json()
    assert [h["action"] for h in history] == ["create", "link_source"]
    assert history[-1]["action_label"] == "Evidència afegida"
    assert "confirm" not in [h["action"] for h in history]
    changes = history[-1]["changes"]
    assert changes["page_number"] == 28 and changes["ref_kind"] == "extracted_page"
    assert P22_LINE not in json.dumps(changes), "cap cita a l'historial"
    session.expire_all()
    item = await session.get(ReviewItem, item_id)
    assert (item.accepted_json, item.verification_status) == (accepted_before, status_before)
    assert await _raw(session) == raw_before
    link = await session.scalar(sa.select(ReviewItemSource).where(
        ReviewItemSource.item_id == item_id, ReviewItemSource.source_ref_id.is_not(None)))
    assert link.link_role == ReviewSourceRole.EXTRA_EVIDENCE


async def test_la_mateixa_cita_dues_vegades_es_409(session, m09, client_a):
    first = await _link(client_a, m09)
    counts = await _counts(session)

    again = await _link(client_a, m09, expected=2)

    assert (first.status_code, again.status_code) == (200, 409)
    body = again.json()["error"]
    assert body["code"] == "review_source_already_linked"
    assert body["details"]["source_ref_id"] == first.json()["source_refs"][1]["id"]
    assert await _counts(session) == counts


async def test_una_revisio_desfasada_no_enllaca_res(session, m09, client_a):
    counts = await _counts(session)

    response = await _link(client_a, m09, expected=7)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["current_revision"] == 1
    assert await _counts(session) == counts


@pytest.mark.parametrize("mutate", ["out_of_range", "inverted", "only_start", "random_page"])
async def test_posicions_o_pagines_invalides_es_refusen(session, m09, client_a, mutate):
    citation = dict(m09["citation"])
    if mutate == "out_of_range":
        citation["char_end"] = 999_999
    elif mutate == "inverted":
        citation["char_start"], citation["char_end"] = 10, 5
    elif mutate == "only_start":
        citation.pop("char_end")
    else:
        citation["page_id"] = str(uuid.uuid4())
    counts = await _counts(session)

    response = await _link(client_a, m09, citation=citation)

    assert response.status_code == 422, response.text
    assert await _counts(session) == counts
    assert (await session.get(ReviewItem, m09["item_id"])).revision == 1


async def test_una_pagina_d_una_altra_licitacio_es_refusa(session, m09, client_a):
    tender = await session.get(Tender, m09["tid"])
    other = Tender(source_id=tender.source_id, external_id=f"exp-{uuid.uuid4().hex[:8]}",
                   title="Una altra licitacio")
    session.add(other)
    await session.flush()
    document = TenderDocument(tender_id=other.id, name="Altre.pdf",
                              url="https://example.com/altre.pdf")
    content = TenderDocumentContent(
        sha256="d" * 64, storage_key="tenders/documents/dd/dd/" + "d" * 64 + ".pdf",
        file_size=10, mime_type="application/pdf", detected_extension="pdf",
        is_text_extractable=True)
    session.add_all([document, content])
    await session.flush()
    extraction = TenderDocumentExtraction(
        content_id=content.id, parser_version="pypdf-v1", revision_number=1,
        status=ExtractionStatus.EXTRACTED, quality=ExtractionQuality.GOOD, page_count=1,
        extracted_page_count=1, empty_page_count=0, text_char_count=20, ocr_candidate=False,
        is_current=True)
    session.add(extraction)
    await session.flush()
    page = TenderDocumentPage(extraction_id=extraction.id, page_number=1,
                              text="Text d'una altra licitacio", char_count=26,
                              text_hash="h" * 64, status=PageExtractionStatus.EXTRACTED)
    session.add_all([page, TenderDocumentFile(
        tender_document_id=document.id, content_id=content.id, source_url=document.url,
        version_number=1, is_current=True, download_status="downloaded")])
    await session.commit()
    counts = await _counts(session)

    response = await _link(client_a, m09, citation={
        "kind": "extracted_page", "extraction_id": str(extraction.id), "page_id": str(page.id),
        "char_start": 0, "char_end": 4})

    assert response.status_code == 422
    assert await _counts(session) == counts


async def test_un_element_d_una_altra_empresa_es_404(session, m09, tenant_b, client_b):
    _, company_b = tenant_b
    assert (await client_b.post(_url(company_b, m09["tid"], "/start"))).status_code == 200
    counts = await _counts(session)

    # B demana l'element d'A des de la seva empresa: no existeix per a B.
    response = await _link(client_b, m09, company=company_b)

    assert response.status_code == 404
    assert await _counts(session) == counts


async def test_un_rol_de_lectura_no_pot_afegir_evidencia(session, m09, tenant_a, client_factory):
    _, company = tenant_a
    viewer = User(email=f"v-{uuid.uuid4().hex[:8]}@empresa-a.com",
                  hashed_password=(await session.get(User, tenant_a[0].id)).hashed_password,
                  full_name="Lectora", is_active=True)
    session.add(viewer)
    await session.flush()
    session.add(CompanyMembership(user_id=viewer.id, company_id=company.id,
                                  role=MembershipRole.VIEWER, is_default=True))
    await session.commit()
    client = client_factory()
    await login(client, viewer.email, TEST_PASSWORD)

    assert (await client.get(_url(company, m09["tid"], f"/requirements/{m09['item_id']}"))
            ).status_code == 200
    assert (await _link(client, m09)).status_code == 403


async def test_l_auditoria_nomes_porta_identificadors(session, m09, client_a):
    await _link(client_a, m09, note="Nota amb text del plec")

    log = await session.scalar(sa.select(AuditLog).where(
        AuditLog.entity_type == "review_item", AuditLog.summary.contains("link_source")))
    assert log is not None
    for forbidden in (P22_LINE, TITLE, "Nota amb text", "§8.1.1.4", "postgresql"):
        assert forbidden not in log.summary


async def test_un_fitxer_tambe_es_pot_citar(session, m09, client_a):
    file_id = await session.scalar(sa.select(TenderDocumentFile.id).join(
        TenderDocument, TenderDocument.id == TenderDocumentFile.tender_document_id).where(
        TenderDocument.tender_id == m09["tid"]))

    response = await _link(client_a, m09, citation={
        "kind": "document_file", "file_id": str(file_id), "location_label": "Annex I"})

    assert response.status_code == 200, response.text
    cited = response.json()["evidence"][-1]
    assert (cited["page"], cited["section"], cited["status_label"]) == (
        None, "Annex I", "Citat manualment")


@pytest.mark.postgres
@pytest.mark.parametrize("statement", [
    "UPDATE review_item_revisions SET note = 'reescrit'",
    "DELETE FROM review_item_revisions",
])
async def test_pg_l_historial_continua_sent_nomes_d_afegir(session, m09, client_a, statement):
    from sqlalchemy.exc import DBAPIError

    assert (await _link(client_a, m09)).status_code == 200

    with pytest.raises(DBAPIError, match="nomes d'afegir"):
        await session.execute(sa.text(statement))
    await session.rollback()
