"""Accions de revisio per empresa (S-R2). Zero crides externes.

Les files d'extraccio les produeix la canonada v3 real amb el proveidor fals,
sobre una licitacio SINTETICA amb forma de Palamos: un requisit sostingut, un
d'assegurança amb un valor que no es a la cita i un de latencia amb la cita
"<70 ms" pero sense cap valor estructurat (com el chunk 18 real).

Es prova: la semantica de cada accio, el model de requisit manual, la
provinenca derivada, la concurrencia optimista, la tenancy, l'auditoria i que
l'extraccio (incloent-hi els camps de revisio LEGACY) no canvia mai.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.audit import AuditLog
from app.models.enums import MembershipRole, ReviewSourceRole
from app.models.identity import CompanyMembership, User
from app.models.provenance import TenderSourceRef
from app.models.requirements import Requirement, RequirementEvidence
from app.models.review import ReviewItem, ReviewItemRevision, ReviewItemSource
from app.services import review_actions, review_view
from app.services.manual_requirement import effective_requirements
from tests import test_requirement_analysis as base
from tests.conftest import TEST_PASSWORD, login
from tests.test_requirement_analysis import PAGE_27, PAGE_28
from tests.test_requirement_analysis_v3 import _evidence, _requirement, _run_v3

analyzable = base.analyzable
_clean_provider = base._clean_provider

ORDINARY = "Volum anual de negoci minim"
INSURANCE = "Import de l'assegurança de responsabilitat civil"
LATENCY = "Latència màxima en serveis de dades"
LATENCY_LINE = "Latència màx. entre dos punts en serveis de dades <70 ms Greu"
MEMORIA = "Memoria tecnica: hasta 35 puntos"


@pytest.fixture
async def palamos_like(session, analyzable):
    """SINTETIC: la pagina 27 porta tambe una fila de latencia amb forma de Palamos."""
    page = analyzable["pages"][0]
    page.text = PAGE_27 + LATENCY_LINE + "\n"
    await session.commit()
    x = analyzable["extraction"].id
    payload = {"requirements": [
        _requirement(x),
        _requirement(
            x, category="insurance", obligation="informative", modality="informational",
            operator=None, title=INSURANCE,
            claims=[{"id": "import_minim", "kind": "amount", "value": 500000, "unit": "EUR",
                     "anchor": "volumen anual de negocios"}],
            evidence=[_evidence(x, supports=["import_minim"])],
        ),
        _requirement(
            x, category="technical_requirement", operator=None, title=LATENCY,
            claims=[], claims_complete="partial",
            evidence=[_evidence(x, eid="e1", quote=LATENCY_LINE)],
        ),
    ], "award_candidates": []}
    await _run_v3(session, analyzable, payload)
    rows = {r.title: r for r in (await session.scalars(sa.select(Requirement))).all()}
    assert set(rows) == {ORDINARY, INSURANCE, LATENCY}
    return {"tender_id": analyzable["document"].tender_id, "rows": rows,
            "extraction": analyzable["extraction"], "pages": analyzable["pages"]}


def _url(company, tender_id, suffix=""):
    return f"/api/v1/companies/{company.id}/tenders/{tender_id}/review{suffix}"


async def _item_of(session, company, requirement):
    return await session.scalar(
        sa.select(ReviewItem).join(ReviewItemSource, ReviewItemSource.item_id == ReviewItem.id)
        .where(ReviewItem.company_id == company.id,
               ReviewItemSource.requirement_id == requirement.id))


async def _raw_extraction(session):
    requirements = [dict(r) for r in (await session.execute(
        sa.select(Requirement.__table__).order_by(Requirement.__table__.c.id))).mappings()]
    evidence = [dict(r) for r in (await session.execute(
        sa.select(RequirementEvidence.__table__).order_by(
            RequirementEvidence.__table__.c.id))).mappings()]
    return requirements, evidence


async def _started(client, company, tender_id):
    response = await client.post(_url(company, tender_id, "/start"))
    assert response.status_code == 200, response.text


async def _act(client, company, tender_id, item_id, action, expected, payload=None):
    return await client.post(
        _url(company, tender_id, f"/items/{item_id}/actions"),
        json={"action": action, "expected_revision": expected, "payload": payload or {}},
    )


# --------------------------------------------------------------------------- #
# Semantica de cada accio                                                       #
# --------------------------------------------------------------------------- #


async def test_confirm_verifica_sense_tocar_la_instantania(session, palamos_like, tenant_a,
                                                          client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][ORDINARY])
    item_id, before = item.id, dict(item.accepted_json)

    response = await _act(client_a, company, tid, item.id, "confirm", 1, {"note": "Revisat"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["verification_status"], body["review_revision"]) == ("verified", 2)
    assert (body["provenance"], body["provenance_label"]) == (
        "automated", "Extret automàticament")
    session.expire_all()
    assert (await session.get(ReviewItem, item_id)).accepted_json == before


async def test_note_no_canvia_ni_estat_ni_instantania(session, palamos_like, tenant_a, client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][INSURANCE])

    body = (await _act(client_a, company, tid, item.id, "note", 1,
                       {"note": "Pendent de confirmar amb l'organ"})).json()

    assert (body["verification_status"], body["review_revision"]) == ("needs_review", 2)
    history = (await client_a.get(_url(company, tid, f"/items/{item.id}/history"))).json()
    assert [h["action"] for h in history] == ["create", "note"]
    assert history[1]["note"] == "Pendent de confirmar amb l'organ"


@pytest.mark.parametrize(("action", "payload", "status"), [
    ("reject", {"reason": "No es un requisit del licitador"}, "rejected"),
    ("mark_partial", {"note": "Nomes una part coberta"}, "partial"),
    ("set_status", {"status": "needs_review"}, "needs_review"),
    ("set_status", {"status": "rejected", "note": "Duplicat"}, "rejected"),
])
async def test_estats_amb_les_seves_regles(session, palamos_like, tenant_a, client_a,
                                          action, payload, status):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][ORDINARY])

    body = (await _act(client_a, company, tid, item.id, action, 1, payload)).json()

    assert body["verification_status"] == status
    history = (await client_a.get(_url(company, tid, f"/items/{item.id}/history"))).json()
    assert history[-1]["changes"]["status_before"] == "unreviewed"
    assert history[-1]["changes"]["status_after"] == status


@pytest.mark.parametrize(("action", "payload"), [
    ("reject", {}),
    ("reject", {"reason": "   "}),
    ("mark_partial", {}),
    ("note", {"note": ""}),
    ("set_status", {"status": "rejected"}),
    ("set_status", {"status": "partial", "note": "  "}),
    ("set_status", {"status": "aprovat"}),
    ("correct", {}),
    ("correct", {"values": [{"key": "x", "value": float("nan")}]}),
    ("correct", {"values": [{"key": "x", "value": 1, "unit_label": "u" * 33}]}),
    ("correct", {"values": [{"key": "x", "value": [1, 2]}]}),
    ("correct", {"values": [{"key": "x", "value": 1, "source": "document"}]}),
    ("correct", {"title": "x", "inventat": 1}),
    ("correct", {"remove_values": ["no_hi_es"]}),
    ("correct", {"verification_status": "rejected", "values": [{"key": "x", "value": 1}]}),
])
async def test_accions_invalides_son_422_i_no_escriuen(session, palamos_like, tenant_a, client_a,
                                                      action, payload):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][ORDINARY])
    item_id = item.id

    # NaN no es JSON valid: s'envia com a text cru, com faria un client descurat.
    raw = json.dumps({"action": action, "expected_revision": 1, "payload": payload})
    response = await client_a.post(_url(company, tid, f"/items/{item.id}/actions"),
                                   content=raw, headers={"content-type": "application/json"})

    assert response.status_code == 422, response.text
    session.expire_all()
    assert (await session.get(ReviewItem, item_id)).revision == 1
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemRevision).where(
        ReviewItemRevision.item_id == item_id)) == 1


# --------------------------------------------------------------------------- #
# Correccions amb forma de Palamos                                              #
# --------------------------------------------------------------------------- #


async def test_a_latencia_corregida_a_menys_de_70_ms(session, palamos_like, tenant_a, client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][LATENCY])
    before = await _raw_extraction(session)

    response = await _act(client_a, company, tid, item.id, "correct", 1, {
        "values": [{"key": "latencia_dades_max", "operator": "<", "value": 70,
                    "unit_label": "ms"}],
        "note": "Valor present a la cita",
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["values"] == [{
        "key": "latencia_dades_max", "operator": "<", "value": 70, "unit_label": "ms",
        "source_label": "Afirmat pel revisor", "status_label": "Afirmat pel revisor"}]
    assert (body["provenance"], body["provenance_label"]) == ("automated_corrected",
                                                              "Extret i corregit")
    assert body["verification_status"] == "verified"
    assert [e["quote"] for e in body["evidence"]] == [LATENCY_LINE]
    session.expire_all()
    assert await _raw_extraction(session) == before


async def test_c_asseguranca_corregida_a_500000_eur(session, palamos_like, tenant_a, client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][INSURANCE])
    source_id = palamos_like["rows"][INSURANCE].id

    body = (await _act(client_a, company, tid, item.id, "correct", 1, {
        "values": [{"key": "import_minim", "operator": ">=", "value": 500000,
                    "unit_label": "EUR"}],
        "obligation": "mandatory", "modality": "mandatory",
    })).json()

    assert body["values"] == [{
        "key": "import_minim", "operator": ">=", "value": 500000, "unit_label": "EUR",
        "source_label": "Afirmat pel revisor", "status_label": "Afirmat pel revisor"}]
    assert body["obligation_label"] == "Obligatori"
    assert body["provenance"] == "automated_corrected"
    history = (await client_a.get(_url(company, tid, f"/items/{item.id}/history"))).json()
    assert history[-1]["changes"]["fields"] == ["obligation", "modality"]
    assert history[-1]["changes"]["values_set"] == ["import_minim"]
    # L'extraccio continua dient el que va dir el model.
    session.expire_all()
    stored = await session.get(Requirement, source_id)
    assert stored.value_json["claims"][0]["value"] == 500000
    assert stored.obligation.value == "informative"


async def test_una_correccio_que_no_canvia_res_es_refusa(session, palamos_like, tenant_a,
                                                        client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][ORDINARY])

    response = await _act(client_a, company, tid, item.id, "correct", 1, {"title": ORDINARY})

    assert response.status_code == 422
    assert (await client_a.get(_url(company, tid, f"/requirements/{item.id}"))).json()[
        "provenance"] == "automated"


@pytest.mark.parametrize(("value", "unit"), [(70, "ms"), (64, "trucades"), (200, "usuaris"),
                                             (720, "p"), (1.5, "Mbps"), (10, "GB")])
async def test_les_unitats_del_revisor_es_conserven_exactes(session, palamos_like, tenant_a,
                                                           client_a, value, unit):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][ORDINARY])

    body = (await _act(client_a, company, tid, item.id, "correct", 1,
                       {"values": [{"key": "valor", "value": value, "unit_label": unit}]})).json()

    assert {v["key"]: (v["value"], v["unit_label"]) for v in body["values"]}["valor"] == (
        value, unit)


# --------------------------------------------------------------------------- #
# Requisits manuals                                                             #
# --------------------------------------------------------------------------- #


async def test_b_d_requisit_manual_de_64_trucades_sense_cita(session, palamos_like, tenant_a,
                                                            client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    requirements_before = await session.scalar(sa.select(sa.func.count()).select_from(Requirement))

    response = await client_a.post(_url(company, tid, "/requirements"), json={
        "title": "Mínim 64 trucades simultànies",
        "category": "technical_requirement",
        "values": [{"key": "trucades_simultanies", "operator": ">=", "value": 64,
                    "unit_label": "trucades"}],
        "note": "PPT p23, 8.1.1.10",
    })

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["provenance"], body["provenance_label"]) == ("manual", "Afegit manualment")
    assert (body["verification_status"], body["review_revision"]) == ("unreviewed", 1)
    assert (body["evidence_label"], body["evidence"], body["source_refs"]) == (
        "Sense cita", [], [])
    assert body["values"][0]["unit_label"] == "trucades"
    # No va a la taula global de requisits ni al conjunt efectiu.
    assert await session.scalar(
        sa.select(sa.func.count()).select_from(Requirement)) == requirements_before
    effective = await effective_requirements(session, tid)
    assert {r.title for r in effective.all} == {ORDINARY, INSURANCE, LATENCY}
    item = await session.get(ReviewItem, uuid.UUID(body["canonical_id"]))
    assert item.origin_hint.value == "manual_review"
    sources = (await session.scalars(sa.select(ReviewItemSource).where(
        ReviewItemSource.item_id == item.id))).all()
    assert sources == []


async def test_e_requisit_manual_amb_cita_de_pagina(session, palamos_like, tenant_a, client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    page = palamos_like["pages"][1]
    start = PAGE_28.index(MEMORIA)

    response = await client_a.post(_url(company, tid, "/requirements"), json={
        "title": "Memoria tecnica valorada",
        "category": "other",
        "obligation": "scored", "modality": "optional",
        "verification_status": "verified",
        "citations": [{"kind": "extracted_page",
                       "extraction_id": str(palamos_like["extraction"].id),
                       "page_id": str(page.id), "char_start": start,
                       "char_end": start + len(MEMORIA), "location_label": "Clausula 9"}],
    })

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["evidence_label"] == "Citat manualment"
    assert body["evidence"] == [{"document": "PCAP.pdf", "page": 28, "section": "Clausula 9",
                                 "quote": MEMORIA, "status_label": "Citat manualment"}]
    assert body["verification_status"] == "verified"
    assert [s["kind"] for s in body["source_refs"]] == ["citation"]
    link = await session.scalar(sa.select(ReviewItemSource).where(
        ReviewItemSource.item_id == uuid.UUID(body["canonical_id"])))
    assert (link.link_role, link.requirement_id) == (ReviewSourceRole.EXTRA_EVIDENCE, None)


@pytest.mark.parametrize("citation", [
    {"kind": "extracted_page", "extraction_id": str(uuid.uuid4()), "page_id": "PAGE"},
    {"kind": "extracted_page", "extraction_id": "EXTRACTION", "page_id": str(uuid.uuid4())},
    {"kind": "document_file", "file_id": str(uuid.uuid4())},
    {"kind": "extracted_page", "extraction_id": "EXTRACTION", "page_id": "PAGE",
     "char_start": 0, "char_end": 99999},
])
async def test_una_cita_que_no_es_d_aquesta_licitacio_es_refusa(
    session, palamos_like, tenant_a, client_a, citation
):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    citation = {k: (str(palamos_like["pages"][0].id) if v == "PAGE" else
                    str(palamos_like["extraction"].id) if v == "EXTRACTION" else v)
                for k, v in citation.items()}

    response = await client_a.post(_url(company, tid, "/requirements"), json={
        "title": "Amb cita dolenta", "category": "other", "citations": [citation]})

    assert response.status_code in (404, 422), response.text
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItem)) == 0
    assert await session.scalar(sa.select(sa.func.count()).select_from(TenderSourceRef)) == 0


async def test_els_elements_automatics_tenen_una_font_primaria_i_els_manuals_cap(
    session, palamos_like, tenant_a, client_a
):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    await client_a.post(_url(company, tid, "/requirements"),
                        json={"title": "Manual", "category": "other"})

    for item in (await session.scalars(sa.select(ReviewItem))).all():
        primaries = (await session.scalars(sa.select(ReviewItemSource).where(
            ReviewItemSource.item_id == item.id,
            ReviewItemSource.link_role == ReviewSourceRole.PRIMARY))).all()
        expected = 1 if item.origin_hint.value == "extraction" else 0
        assert len(primaries) == expected
        assert all(p.requirement_id is not None for p in primaries)


# --------------------------------------------------------------------------- #
# Concurrencia                                                                  #
# --------------------------------------------------------------------------- #


async def test_una_revisio_desfasada_es_409_i_no_escriu(session, palamos_like, tenant_a,
                                                        client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][ORDINARY])
    note = await _act(client_a, company, tid, item.id, "note", 1, {"note": "u"})
    assert note.status_code == 200

    first = await _act(client_a, company, tid, item.id, "confirm", 2)
    second = await _act(client_a, company, tid, item.id, "reject", 2, {"reason": "no"})

    assert (first.status_code, first.json()["review_revision"]) == (200, 3)
    assert second.status_code == 409
    assert second.json()["error"]["details"]["current_revision"] == 3
    assert second.json()["error"]["code"] == "review_revision_conflict"
    history = (await client_a.get(_url(company, tid, f"/items/{item.id}/history"))).json()
    assert [h["action"] for h in history] == ["create", "note", "confirm"]
    assert (await client_a.get(_url(company, tid, f"/requirements/{item.id}"))).json()[
        "verification_status"] == "verified"


@pytest.mark.postgres
async def test_pg_dues_accions_simultanies_nomes_una_guanya(
    session_factory, session, palamos_like, tenant_a
):
    user, company = tenant_a
    tid = palamos_like["tender_id"]
    await review_view.start_review(session, company_id=company.id, tender_id=tid,
                                   actor_id=user.id)
    await session.commit()
    item_id = (await _item_of(session, company, palamos_like["rows"][ORDINARY])).id

    async with session_factory() as first, session_factory() as second:
        await review_actions.apply_review_action(
            first, company_id=company.id, tender_id=tid, item_id=item_id, actor_id=user.id,
            expected_revision=1, action="confirm", payload={})
        # La segona espera el lock de la primera.
        racing = asyncio.create_task(review_actions.apply_review_action(
            second, company_id=company.id, tender_id=tid, item_id=item_id, actor_id=user.id,
            expected_revision=1, action="reject", payload={"reason": "no"}))
        await asyncio.sleep(0.3)
        assert not racing.done(), "la segona hauria d'esperar el lock"
        await first.commit()
        with pytest.raises(review_actions.StaleRevisionError):
            await racing
        await second.rollback()

    session.expire_all()
    assert (await session.get(ReviewItem, item_id)).revision == 2
    actions = (await session.scalars(sa.select(ReviewItemRevision.action).where(
        ReviewItemRevision.item_id == item_id).order_by(ReviewItemRevision.revision_no))).all()
    assert [a.value for a in actions] == ["create", "confirm"]


# --------------------------------------------------------------------------- #
# Tenancy                                                                       #
# --------------------------------------------------------------------------- #


async def test_tenancy_de_les_accions(session, palamos_like, tenant_a, tenant_b,
                                      client_a, client_b):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    tid = palamos_like["tender_id"]
    requirement = palamos_like["rows"][ORDINARY]
    await _started(client_a, company_a, tid)
    await _started(client_b, company_b, tid)
    item_a = await _item_of(session, company_a, requirement)
    item_b = await _item_of(session, company_b, requirement)

    # A: A corregeix el seu.
    corrected = await _act(client_a, company_a, tid, item_a.id, "correct", 1,
                           {"title": "Titol corregit per A"})
    assert corrected.status_code == 200
    # B: B veu el seu, intacte.
    own_b = (await client_b.get(_url(company_b, tid, f"/requirements/{item_b.id}"))).json()
    assert (own_b["title"], own_b["review_revision"], own_b["provenance"]) == (
        ORDINARY, 1, "automated")
    # C: B no pot llegir ni tocar l'element ni l'historial d'A.
    assert (await client_b.get(_url(company_b, tid, f"/requirements/{item_a.id}"))).status_code \
        == 404
    assert (await client_b.get(_url(company_b, tid, f"/items/{item_a.id}/history"))).status_code \
        == 404
    assert (await _act(client_b, company_b, tid, item_a.id, "confirm", 2)).status_code == 404
    assert (await client_b.get(_url(company_a, tid, f"/items/{item_a.id}/history"))).status_code \
        == 404
    # D/E: un manual d'A no apareix per a B ni a la taula global.
    manual = (await client_a.post(_url(company_a, tid, "/requirements"),
                                  json={"title": "Manual d'A", "category": "other"})).json()
    listed_b = (await client_b.get(_url(company_b, tid, "/requirements"))).json()
    assert manual["canonical_id"] not in {r["canonical_id"] for r in listed_b}
    assert "Manual d'A" not in json.dumps(listed_b)
    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement).where(
        Requirement.title == "Manual d'A")) == 0


async def test_f_una_cita_no_es_pot_penjar_d_un_element_d_una_altra_empresa(
    session, palamos_like, tenant_a, tenant_b, client_a
):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    tid = palamos_like["tender_id"]
    manual = (await client_a.post(_url(company_a, tid, "/requirements"), json={
        "title": "Amb cita", "category": "other",
        "citations": [{"kind": "extracted_page",
                       "extraction_id": str(palamos_like["extraction"].id),
                       "page_id": str(palamos_like["pages"][0].id)}]})).json()
    ref_id = next(s["id"] for s in manual["source_refs"] if s["kind"] == "citation")

    session.add(ReviewItemSource(item_id=uuid.UUID(manual["canonical_id"]),
                                 company_id=company_b.id, source_ref_id=uuid.UUID(ref_id),
                                 link_role=ReviewSourceRole.EXTRA_EVIDENCE))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


# --------------------------------------------------------------------------- #
# Rols, auditoria i immutabilitat                                               #
# --------------------------------------------------------------------------- #


async def test_un_rol_de_lectura_no_pot_revisar(session, palamos_like, tenant_a, client_a,
                                               client_factory):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][ORDINARY])
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

    assert (await client.get(_url(company, tid, "/requirements"))).status_code == 200
    assert (await _act(client, company, tid, item.id, "confirm", 1)).status_code == 403
    assert (await client.post(_url(company, tid, "/requirements"),
                              json={"title": "x", "category": "other"})).status_code == 403
    assert (await client.post(_url(company, tid, "/start"))).status_code == 403


async def test_cada_escriptura_s_audita_sense_contingut(session, palamos_like, tenant_a,
                                                        client_a):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][LATENCY])
    await _act(client_a, company, tid, item.id, "correct", 1,
               {"values": [{"key": "latencia", "operator": "<", "value": 70,
                            "unit_label": "ms"}]})
    await client_a.post(_url(company, tid, "/requirements"),
                        json={"title": "Titol secret del manual", "category": "other"})

    logs = (await session.scalars(sa.select(AuditLog).where(
        AuditLog.company_id == company.id))).all()
    kinds = sorted(log.entity_type for log in logs)
    assert kinds == ["review", "review_item", "review_item"]
    text = " ".join(log.summary or "" for log in logs)
    assert "accio=correct" in text and "accio=create_manual" in text
    for forbidden in (LATENCY_LINE, "Titol secret", LATENCY, "latencia", "postgresql"):
        assert forbidden not in text


async def test_cap_accio_no_toca_l_extraccio_ni_els_camps_legacy(
    session, palamos_like, tenant_a, client_a
):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    before = await _raw_extraction(session)
    items = {t: await _item_of(session, company, r) for t, r in palamos_like["rows"].items()}

    steps = [
        (items[ORDINARY], "confirm", {}),
        (items[ORDINARY], "note", {"note": "n"}),
        (items[ORDINARY], "set_status", {"status": "needs_review"}),
        (items[INSURANCE], "correct", {"values": [{"key": "import_minim", "value": 500000,
                                                   "unit_label": "EUR"}]}),
        (items[INSURANCE], "mark_partial", {"note": "parcial"}),
        (items[LATENCY], "reject", {"reason": "prova"}),
    ]
    revision = {item.id: 1 for item in items.values()}
    for item, action, payload in steps:
        response = await _act(client_a, company, tid, item.id, action, revision[item.id], payload)
        assert response.status_code == 200, response.text
        revision[item.id] += 1
    await client_a.post(_url(company, tid, "/requirements"),
                        json={"title": "Manual", "category": "other"})

    session.expire_all()
    after = await _raw_extraction(session)
    assert after == before
    for row in after[0]:
        assert row["manual_override_json"] is None
        assert row["review_note"] is None
        assert (row["reviewed_by"], row["reviewed_at"]) == (None, None)


@pytest.mark.postgres
@pytest.mark.parametrize("statement", [
    "UPDATE review_item_revisions SET note = 'reescrit'",
    "DELETE FROM review_item_revisions",
])
async def test_pg_despres_de_les_accions_l_historial_continua_sent_nomes_d_afegir(
    session, palamos_like, tenant_a, client_a, statement
):
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    await _started(client_a, company, tid)
    item = await _item_of(session, company, palamos_like["rows"][ORDINARY])
    assert (await _act(client_a, company, tid, item.id, "confirm", 1)).status_code == 200
    count = await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemRevision))

    with pytest.raises(DBAPIError, match="nomes d'afegir"):
        await session.execute(sa.text(statement))
    await session.rollback()
    assert await session.scalar(
        sa.select(sa.func.count()).select_from(ReviewItemRevision)) == count
