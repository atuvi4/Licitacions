"""Nomes l'empresa PROPIETARIA escriu en una licitacio oberta a ma (enduriment de tenancy).

Els requisits manuals, les seves cites i els documents d'una licitacio manual
viuen en taules sense `company_id`: la tenancy es la de la licitacio. Des de
0027 una licitacio manual neix amb `owner_company_id`; les anteriors es van
quedar PUBLIQUES (sense propietari). En aquestes, cap empresa no hi pot
escriure: es llegeixen, pero no s'endevina de qui son. Zero crides externes.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.provenance import RequirementSourceRefLink, TenderSourceRef
from app.models.requirements import Requirement
from app.models.tender_documents import TenderDocumentFile, TenderDocumentPage
from app.models.tenders import Tender, TenderDocument
from tests.test_manual_requirements import (
    ARCHIVE,
    CITATIONS,
    ONE,
    PLEC,
    REQUIREMENTS,
    _extracted_requirement,
    _extracted_run,
    _ok,
    _page_ref,
    _tender,
    payload,
)

TENDER = "/api/v1/companies/{company}/tenders/{tender}"
UPLOAD = TENDER + "/documents"
PAGE_REF = TENDER + "/source-refs/extracted-page"
FILE_REF = TENDER + "/source-refs/document-file"
READ_TENDER = "/api/v1/tenders/{tender}"
ANALYSIS = "/api/v1/tenders/{tender}/analysis/requirements"


@pytest.fixture
async def owned(db, client_a, tenant_a):
    """SINTETIC: una licitacio manual d'A amb un requisit, un PDF i una cita."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded, ref_id = await _page_ref(db, client_a, company, tender_id)
    requirement = await _ok(client_a, company, tender_id, source_ref_ids=[ref_id])
    page = (await db.execute(sa.select(TenderDocumentPage).where(
        TenderDocumentPage.extraction_id == uuid.UUID(uploaded["file"]["extraction_id"])))
    ).scalars().first()
    return {"tender_id": tender_id, "requirement_id": requirement["id"], "ref_id": ref_id,
            "uploaded": uploaded, "page_id": str(page.id)}


async def _make_public(db, tender_id: str) -> None:
    """SINTETIC: com una licitacio manual anterior a 0027, sense propietari."""
    tender = await db.get(Tender, uuid.UUID(tender_id))
    tender.owner_company_id = None
    await db.commit()


async def _state(db) -> dict:
    """Tot el que aquestes escriptures podrien tocar, en columnes comparables."""
    state = {}
    for model in (Requirement, RequirementSourceRefLink, TenderSourceRef, TenderDocument,
                  TenderDocumentFile, Tender):
        table = model.__table__
        state[table.name] = [dict(r) for r in (await db.execute(
            sa.select(table).order_by(*table.primary_key.columns))).mappings()]
    state["audit_logs"] = await db.scalar(sa.select(sa.func.count()).select_from(AuditLog))
    return state


async def _every_write(client, company, s) -> dict[str, int]:
    """Totes les escriptures que passen per `load_editable_tender`, amb cossos valids."""
    t, r = s["tender_id"], s["requirement_id"]
    one = ONE.format(company=company.id, tender=t, requirement=r)
    calls = {
        "create": await client.post(REQUIREMENTS.format(company=company.id, tender=t),
                                    json=payload(title="Intrus")),
        "edit": await client.patch(one, json={"title": "Intrus"}),
        "archive": await client.post(ARCHIVE.format(company=company.id, tender=t, requirement=r)),
        "link_citation": await client.post(
            CITATIONS.format(company=company.id, tender=t, requirement=r),
            json={"source_ref_id": s["ref_id"]}),
        "unlink_citation": await client.delete(
            CITATIONS.format(company=company.id, tender=t, requirement=r) + f"/{s['ref_id']}"),
        "page_ref": await client.post(PAGE_REF.format(company=company.id, tender=t), json={
            "extraction_id": s["uploaded"]["file"]["extraction_id"], "page_id": s["page_id"]}),
        "file_ref": await client.post(FILE_REF.format(company=company.id, tender=t), json={
            "tender_document_file_id": s["uploaded"]["file"]["file_id"]}),
        "edit_tender": await client.patch(TENDER.format(company=company.id, tender=t),
                                          json={"expected_revision": 1, "title": "Intrus"}),
        "upload": await client.post(
            UPLOAD.format(company=company.id, tender=t),
            files={"file": ("Intrus.pdf", PLEC, "application/pdf")},
            data={"document_type": "other"}),
    }
    return {name: response.status_code for name, response in calls.items()}


# --------------------------------------------------------------------------- #
# A. La propietaria continua escrivint                                         #
# --------------------------------------------------------------------------- #


async def test_la_propietaria_crea_edita_arxiva_i_cita(db, client_a, tenant_a, owned):
    _, company = tenant_a
    t, r = owned["tender_id"], owned["requirement_id"]
    one = ONE.format(company=company.id, tender=t, requirement=r)

    assert (await client_a.patch(one, json={"title": "Corregit"})).status_code == 200
    unlink = CITATIONS.format(company=company.id, tender=t, requirement=r) + f"/{owned['ref_id']}"
    assert (await client_a.delete(unlink)).status_code in (200, 204)
    relink = await client_a.post(CITATIONS.format(company=company.id, tender=t, requirement=r),
                                 json={"source_ref_id": owned["ref_id"]})
    assert relink.status_code in (200, 201), relink.text
    archive = await client_a.post(ARCHIVE.format(company=company.id, tender=t, requirement=r))
    assert archive.status_code == 200, archive.text
    second = await client_a.post(REQUIREMENTS.format(company=company.id, tender=t),
                                 json=payload(title="Segon"))
    assert second.status_code == 201


# --------------------------------------------------------------------------- #
# B. Una altra empresa, sobre la privada d'A: ni la veu                        #
# --------------------------------------------------------------------------- #


async def test_una_altra_empresa_no_escriu_ni_veu_la_privada(db, client_b, tenant_b, owned):
    _, company_b = tenant_b
    before = await _state(db)

    statuses = await _every_write(client_b, company_b, owned)

    assert set(statuses.values()) == {404}, statuses
    assert await _state(db) == before


# --------------------------------------------------------------------------- #
# C/D. Licitacio manual publica (anterior a 0027): ningu no hi escriu          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("who", ["a", "b"])
async def test_una_manual_publica_no_admet_cap_escriptura(
    db, client_a, client_b, tenant_a, tenant_b, owned, who
):
    await _make_public(db, owned["tender_id"])
    client, (_, company) = (client_a, tenant_a) if who == "a" else (client_b, tenant_b)
    before = await _state(db)

    statuses = await _every_write(client, company, owned)

    assert set(statuses.values()) == {403}, statuses
    response = await client.post(REQUIREMENTS.format(company=company.id, tender=owned["tender_id"]),
                                 json=payload(title="Intrus"))
    assert response.json()["error"]["code"] == "tender_not_owned"
    assert await _state(db) == before


# --------------------------------------------------------------------------- #
# E. La lectura no canvia                                                      #
# --------------------------------------------------------------------------- #


async def test_una_manual_publica_es_continua_llegint(db, client_a, client_b, owned):
    await _make_public(db, owned["tender_id"])

    for client in (client_a, client_b):
        tender = await client.get(READ_TENDER.format(tender=owned["tender_id"]))
        analysis = await client.get(ANALYSIS.format(tender=owned["tender_id"]))
        assert tender.status_code == 200, tender.text
        assert analysis.status_code == 200
        assert [r["id"] for r in analysis.json()["requirements"]] == [owned["requirement_id"]]


async def test_la_privada_d_a_continua_invisible_per_a_b(client_b, owned):
    assert (await client_b.get(READ_TENDER.format(tender=owned["tender_id"]))).status_code == 404


# --------------------------------------------------------------------------- #
# F. Un requisit extret continua sense poder-se editar                         #
# --------------------------------------------------------------------------- #


async def test_un_requisit_extret_es_continua_refusant(db, client_a, tenant_a, owned):
    _, company = tenant_a
    run = await _extracted_run(db, owned["tender_id"])
    extracted = await _extracted_requirement(db, run, owned["tender_id"])
    before = await _state(db)

    response = await client_a.patch(
        ONE.format(company=company.id, tender=owned["tender_id"], requirement=extracted.id),
        json={"title": "Reescrit"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "requirement_not_manual"
    assert await _state(db) == before


async def test_sense_licitacio_propietaria_ni_la_ingesta_publica_no_s_hi_escriu(
    client_a, tenant_a, tender_document
):
    """Una licitacio d'una font (no manual) continua dient `tender_not_manual`, com abans."""
    _, company = tenant_a
    response = await client_a.post(
        REQUIREMENTS.format(company=company.id, tender=tender_document.tender_id), json=payload())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tender_not_manual"
