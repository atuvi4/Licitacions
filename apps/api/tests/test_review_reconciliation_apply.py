"""Reconciliacio persistent: nomes `exact_auto` (S-R9). Tot SINTETIC, proveidor fals.

Invariant: el MATEIX `review_item.id`. La font primaria vella queda `unlinked_at`
(no s'esborra) i la nova apunta a la fila equivalent de la run desti. L'estat
huma (instantania acceptada, verificacio, paper, relacions, cites afegides,
historial) no es toca. Cada reenllaç = una revisio `reconcile`.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.audit import AuditLog
from app.models.enums import (
    MembershipRole,
    ReviewItemKind,
    ReviewReconciliationState,
    ReviewSourceRole,
)
from app.models.identity import CompanyMembership, User
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.review import (
    ReviewItem,
    ReviewItemRelation,
    ReviewItemRevision,
    ReviewItemSource,
    ReviewReconciliation,
)
from app.schemas.review import ManualRequirementCreate
from app.services import review_actions
from app.services import review_reconciliation_apply as apply_service
from tests import test_review_reconciliation as recon
from tests.conftest import TEST_PASSWORD, login
from tests.test_requirement_analysis_v3 import _evidence
from tests.test_review_reconciliation import (
    GUARANTEE,
    LATENCY,
    PRICE_LINE,
    TERM_LINE,
    VOLUM,
    _awards,
    _guarantee,
    _latency,
    _preview,
    _requirement,
    _row,
    _run,
    _start,
    _term,
    _volum,
)
from tests.test_review_rerun_guard import _act, _items, _legacy_item, _url

analyzable = recon.analyzable
_clean_provider = recon._clean_provider
tender = recon.tender

MEMORY_REQ_LINE = "Memòria tècnica del servei: fins a 20 punts"


def _price(x, title):
    return _requirement(x, category="other", operator=None, title=title, claims=[],
                        claims_complete="partial",
                        evidence=[_evidence(x, eid="e7", page=27, quote=PRICE_LINE)])


def _memory(x):
    return _requirement(x, category="technical_requirement", operator=None,
                        title="Memòria del servei", claims=[], claims_complete="partial",
                        evidence=[_evidence(x, eid="e8", page=28, quote=MEMORY_REQ_LINE)])


async def _apply(client, company, tender_id, target_run_id, plan_hash):
    return await client.post(_url(company, tender_id, "/reconciliation/apply"), json={
        "target_run_id": str(target_run_id), "plan_hash": plan_hash, "mode": "exact_auto"})


async def _review_state(session, company_id=None):
    """Tota la capa de revisio (o la d'una empresa), en columnes comparables."""

    def scoped(model):
        query = sa.select(model.__table__)
        if company_id is not None:
            query = query.where(model.__table__.c.company_id == company_id)
        return query.order_by(model.__table__.c.id)

    state = {}
    for model in (ReviewItem, ReviewItemSource, ReviewItemRevision, ReviewItemRelation,
                  ReviewReconciliation):
        state[model.__tablename__] = [
            dict(r) for r in (await session.execute(scoped(model))).mappings()]
    audits = sa.select(sa.func.count()).select_from(AuditLog)
    if company_id is not None:
        audits = audits.where(AuditLog.company_id == company_id)
    state["audit_logs"] = await session.scalar(audits)
    return state


async def _extraction_state(session):
    state = {}
    for model in (Requirement, RequirementEvidence, AwardCriterion, AwardCriterionEvidence,
                  RequirementExtractionRun):
        table = model.__table__
        state[table.name] = [dict(r) for r in (await session.execute(
            sa.select(table).order_by(table.c.id))).mappings()]
    return state


async def _active_primary_runs(session, company):
    rows = (await session.execute(
        sa.select(ReviewItemSource.item_id,
                  sa.func.coalesce(Requirement.run_id, AwardCriterion.run_id))
        .outerjoin(Requirement, Requirement.id == ReviewItemSource.requirement_id)
        .outerjoin(AwardCriterion, AwardCriterion.id == ReviewItemSource.award_criterion_id)
        .where(ReviewItemSource.company_id == company.id,
               ReviewItemSource.link_role == ReviewSourceRole.PRIMARY,
               ReviewItemSource.unlinked_at.is_(None)))).all()
    return dict(rows)


# --------------------------------------------------------------------------- #
# Escenari nomes exacte                                                         #
# --------------------------------------------------------------------------- #


async def _exact_only(session, tender, client, company, user):
    """A: 3 requisits + 3 criteris revisats a fons. B: copies exactes (ids nous)."""
    x = tender["x"]
    tid = tender["tender_id"]
    rows = [_volum(x), _latency(x), _guarantee(x)]
    run_a = await _run(session, tender, rows, first=True)
    keys = await _awards(session, tender, run_a)
    await _start(client, company, tid)
    await _start(client, company, tid, "/award-criteria/start")
    items = {i.accepted_json["title"]: i for i in
             await _items(session, company, ReviewItemKind.REQUIREMENT)}
    await _act(client, company, tid, items[VOLUM].id, "confirm", 1)
    await _act(client, company, tid, items[LATENCY].id, "correct", 1, {
        "values": [{"key": "latencia", "operator": "lt", "value": 70, "unit_label": "ms"}]})
    p28 = next(p for p in tender["analyzable"]["pages"] if p.page_number == 28)
    start = p28.text.index(TERM_LINE)
    await _act(client, company, tid, items[GUARANTEE].id, "link_source", 1, {"citation": {
        "kind": "extracted_page", "extraction_id": str(x), "page_id": str(p28.id),
        "char_start": start, "char_end": start + len(TERM_LINE)}})
    manual = await review_actions.create_manual_requirement(
        session, company_id=company.id, tender_id=tid, user=user,
        data=ManualRequirementCreate(title="Requisit manual", category="other"))
    await session.commit()
    for source, target, kind in (
        (manual.id, items[VOLUM].id, "duplicate_of"),
        (items[GUARANTEE].id, items[LATENCY].id, "related_to"),
        (items[LATENCY].id, items[VOLUM].id, "partial_match_of"),
    ):
        response = await client.post(_url(company, tid, "/relations"), json={
            "from_item_id": str(source), "to_item_id": str(target), "relation_type": kind})
        assert response.status_code == 201, response.text
    roles = {"Preu": "scored", "Qualitat": "parent_total", "Memòria tècnica": "scored"}
    for item in await _items(session, company, ReviewItemKind.AWARD_CRITERION):
        await _act(client, company, tid, item.id, "set_award_role", 1,
                   {"role": roles[item.accepted_json["name"]]})
    run_b = await _run(session, tender, rows)
    await _awards(session, tender, run_b, keys=keys)
    return {"tid": tid, "run_a": run_a, "run_b": run_b, "manual_id": manual.id}


async def test_nomes_exactes_manté_ids_i_estat_huma_i_completa(
    session, tender, tenant_a, client_a
):
    user, company = tenant_a
    s = await _exact_only(session, tender, client_a, company, user)
    before = await _review_state(session, company.id)
    extraction_before = await _extraction_state(session)
    preview = await _preview(client_a, company, s["tid"])
    assert preview["requirements"]["counts"]["exact_auto"] == 3
    assert preview["awards"]["counts"]["exact_auto"] == 3

    response = await _apply(client_a, company, s["tid"], s["run_b"], preview["plan_hash"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["changed"], body["exact_applied_count"], body["state"]) == (
        True, 6, "completed")
    assert body["applied_plan_hash"] == preview["plan_hash"]
    assert body["remaining_requirements"] == body["remaining_awards"] == {
        "review_candidate": 0, "ambiguous": 0, "new": 0, "removed": 0}
    after = await _review_state(session, company.id)

    # Mateixos elements, mateix estat huma; nomes revisio +1 i metadades d'actualitzacio.
    old_items = {i["id"]: i for i in before["review_items"]}
    new_items = {i["id"]: i for i in after["review_items"]}
    assert old_items.keys() == new_items.keys()
    for item_id, old in old_items.items():
        new = new_items[item_id]
        for column in ("accepted_json", "verification_status", "award_role", "archived_at",
                       "kind", "origin_hint", "created_at", "created_by"):
            assert new[column] == old[column], column
        manual = item_id == s["manual_id"]
        assert new["revision"] == old["revision"] + (0 if manual else 1)
    # Fonts: les velles continuen (tancades), cap esborrada; les noves son de B.
    old_sources = {r["id"]: r for r in before["review_item_sources"]}
    new_sources = {r["id"]: r for r in after["review_item_sources"]}
    assert old_sources.keys() <= new_sources.keys()
    closed = [r for r in new_sources.values() if r["id"] in old_sources
              and r["unlinked_at"] is not None]
    assert len(closed) == 6
    assert all(r["link_role"] == "primary" for r in closed)
    extra = [r for r in new_sources.values() if r["link_role"] == "extra_evidence"]
    assert extra == [r for r in before["review_item_sources"]
                     if r["link_role"] == "extra_evidence"]
    runs = await _active_primary_runs(session, company)
    assert set(runs.values()) == {s["run_b"]} and len(runs) == 6
    # Relacions intactes, reconciliacio desada, extraccio intacta.
    assert after["review_item_relations"] == before["review_item_relations"]
    [row] = after["review_reconciliations"]
    assert (row["state"], row["exact_applied_count"], row["target_run_id"]) == (
        "completed", 6, s["run_b"])
    assert row["requirement_source_run_ids"] == [str(s["run_a"])]
    assert row["award_source_run_ids"] == [str(s["run_a"])]
    assert await _extraction_state(session) == extraction_before
    # Una revisio `reconcile` per element reenllaçat, sense text del plec.
    added = [r for r in after["review_item_revisions"]
             if r["id"] not in {x["id"] for x in before["review_item_revisions"]}]
    assert len(added) == 6 and {r["action"] for r in added} == {"reconcile"}
    assert len({r["item_id"] for r in added}) == 6
    for revision in added:
        payload = revision["payload_json"]
        assert set(payload) == {"kind", "old_source_id", "new_source_id", "old_run_id",
                                "new_run_id", "reconciliation_id", "plan_hash"}
        assert (payload["old_run_id"], payload["new_run_id"]) == (
            str(s["run_a"]), str(s["run_b"]))
        assert revision["accepted_json_after"] == old_items[revision["item_id"]]["accepted_json"]
    assert after["audit_logs"] == before["audit_logs"] + 1


async def test_despres_de_completar_la_lectura_i_start_tornen_a_la_normalitat(
    session, tender, tenant_a, client_a
):
    user, company = tenant_a
    s = await _exact_only(session, tender, client_a, company, user)
    preview = await _preview(client_a, company, s["tid"])
    assert (await _apply(client_a, company, s["tid"], s["run_b"],
                         preview["plan_hash"])).status_code == 200

    listed = {r["title"]: r for r in (await client_a.get(
        _url(company, s["tid"], "/requirements"))).json()}
    assert listed[LATENCY]["provenance"] == "automated_corrected"
    assert listed[LATENCY]["provenance_label"] == "Extret i corregit"
    assert listed[VOLUM]["provenance"] == "automated"
    assert listed[VOLUM]["verification_status"] == "verified"
    assert [v["value"] for v in listed[LATENCY]["values"]] == [70]
    history = (await client_a.get(_url(
        company, s["tid"], f"/items/{listed[VOLUM]['canonical_id']}/history"))).json()
    assert history[-1]["action"] == "reconcile"
    assert history[-1]["action_label"] == "Reconciliat amb la nova extracció"
    after_preview = await _preview(client_a, company, s["tid"])
    assert (after_preview["state"], after_preview["plan_hash"]) == ("up_to_date", None)
    assert after_preview["reconciliation"]["state"] == "completed"
    overview = (await client_a.get(_url(company, s["tid"], "/overview"))).json()
    assert (overview["reconciliation_required"], overview["reconciliation_state"]) == (
        False, None)
    scoring = (await client_a.get(_url(
        company, s["tid"], "/award-criteria?view=scoring"))).json()
    assert (scoring["scored_count"], scoring["total_points"],
            scoring["reconciliation_pending"]) == (2, 100.0, False)
    for suffix in ("/start", "/award-criteria/start"):
        started = await client_a.post(_url(company, s["tid"], suffix))
        assert started.status_code == 200 and started.json()["created"] == 0


async def test_tornar_a_aplicar_el_mateix_pla_no_escriu_res(session, tender, tenant_a, client_a):
    user, company = tenant_a
    s = await _exact_only(session, tender, client_a, company, user)
    plan = (await _preview(client_a, company, s["tid"]))["plan_hash"]
    first = (await _apply(client_a, company, s["tid"], s["run_b"], plan)).json()
    before = await _review_state(session)

    again = await _apply(client_a, company, s["tid"], s["run_b"], plan)

    assert again.status_code == 200
    body = again.json()
    assert (body["changed"], body["exact_applied_count"], body["total_exact_applied"],
            body["state"], body["reconciliation_id"]) == (
        False, 0, 6, "completed", first["reconciliation_id"])
    assert await _review_state(session) == before


# --------------------------------------------------------------------------- #
# Escenari barrejat                                                             #
# --------------------------------------------------------------------------- #


async def _mixed(session, tender, client, company):
    """A: 5 requisits. B: 2 exactes, 1 candidat, 1 ambigu, 1 nou, 1 retirat."""
    x = tender["x"]
    tid = tender["tender_id"]
    run_a = await _run(session, tender, [
        _volum(x), _guarantee(x), _latency(x, page=27), _price(x, "Preu ofert"), _memory(x),
    ], first=True)
    await _start(client, company, tid)
    run_b = await _run(session, tender, [
        _volum(x), _guarantee(x), _latency(x, page=28), _price(x, "Oferta A"),
        _price(x, "Oferta B"), _term(x),
    ])
    return {"tid": tid, "run_a": run_a, "run_b": run_b}


async def test_barrejat_nomes_reenllaça_les_exactes_i_queda_per_revisar(
    session, tender, tenant_a, client_a
):
    _, company = tenant_a
    s = await _mixed(session, tender, client_a, company)
    preview = await _preview(client_a, company, s["tid"])
    assert preview["requirements"]["counts"] == {
        "exact_auto": 2, "review_candidate": 1, "ambiguous": 1, "new": 1, "removed": 1}
    before = await _review_state(session, company.id)

    body = (await _apply(client_a, company, s["tid"], s["run_b"], preview["plan_hash"])).json()

    assert (body["changed"], body["exact_applied_count"], body["state"]) == (
        True, 2, "needs_review")
    assert body["remaining_requirements"] == {
        "review_candidate": 1, "ambiguous": 1, "new": 1, "removed": 1}
    after = await _review_state(session, company.id)
    assert len(after["review_items"]) == len(before["review_items"])  # cap element nou
    assert all(i["archived_at"] is None for i in after["review_items"])  # cap retirat arxivat
    assert len(after["review_item_sources"]) == len(before["review_item_sources"]) + 2
    runs = await _active_primary_runs(session, company)
    assert sorted(runs.values(), key=str).count(s["run_b"]) == 2

    # La lectura continua mostrant la feina pendent, sense caure a `mixed_source_runs`.
    current = await _preview(client_a, company, s["tid"])
    assert current["state"] == "reconciliation_in_progress"
    section = current["requirements"]
    assert section["state"] == "reconciliation_in_progress"
    assert section["already_applied_count"] == 2
    assert section["counts"] == {
        "exact_auto": 0, "review_candidate": 1, "ambiguous": 1, "new": 1, "removed": 1}
    assert current["reconciliation"]["state"] == "needs_review"
    assert current["reconciliation"]["applied_plan_hash"] == preview["plan_hash"]
    assert current["plan_hash"] and current["plan_hash"] != preview["plan_hash"]
    overview = (await client_a.get(_url(company, s["tid"], "/overview"))).json()
    assert (overview["reconciliation_required"], overview["reconciliation_state"]) == (
        True, "needs_review")
    blocked = await client_a.post(_url(company, s["tid"], "/start"))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "review_reconciliation_required"

    # Aplicar el pla vigent (0 exactes) no toca res ni reescriu el pla acceptat.
    snapshot = await _review_state(session)
    again = (await _apply(client_a, company, s["tid"], s["run_b"], current["plan_hash"])).json()
    assert (again["changed"], again["state"], again["applied_plan_hash"]) == (
        False, "needs_review", preview["plan_hash"])
    assert await _review_state(session) == snapshot


async def test_totes_les_fonts_a_b_pero_amb_feina_nova_continua_bloquejat(
    session, tender, tenant_a, client_a
):
    """Totes les fonts ja son de B, pero B te un requisit nou: `start` no l'ha de crear."""
    _, company = tenant_a
    x = tender["x"]
    tid = tender["tender_id"]
    await _run(session, tender, [_volum(x), _guarantee(x)], first=True)
    await _start(client_a, company, tid)
    run_b = await _run(session, tender, [_volum(x), _guarantee(x), _term(x)])
    preview = await _preview(client_a, company, tid)

    body = (await _apply(client_a, company, tid, run_b, preview["plan_hash"])).json()

    assert (body["state"], body["remaining_requirements"]["new"]) == ("needs_review", 1)
    assert set((await _active_primary_runs(session, company)).values()) == {run_b}
    blocked = await client_a.post(_url(company, tid, "/start"))
    assert blocked.status_code == 409
    assert "reconciliation_id" in blocked.json()["error"]["details"]
    assert (await _preview(client_a, company, tid))["state"] == "reconciliation_in_progress"


async def test_cap_coincidencia_exacta_crea_nomes_el_cicle_de_vida(
    session, tender, tenant_a, client_a
):
    _, company = tenant_a
    x = tender["x"]
    tid = tender["tender_id"]
    await _run(session, tender, [_latency(x, page=27)], first=True)
    await _start(client_a, company, tid)
    run_b = await _run(session, tender, [_latency(x, page=28)])
    preview = await _preview(client_a, company, tid)
    assert preview["requirements"]["counts"]["review_candidate"] == 1
    before = await _review_state(session, company.id)

    body = (await _apply(client_a, company, tid, run_b, preview["plan_hash"])).json()

    assert (body["changed"], body["exact_applied_count"], body["state"]) == (
        False, 0, "needs_review")
    after = await _review_state(session, company.id)
    for table in ("review_items", "review_item_sources", "review_item_revisions",
                  "review_item_relations"):
        assert after[table] == before[table], table
    assert len(after["review_reconciliations"]) == 1
    snapshot = await _review_state(session)
    again = (await _apply(client_a, company, tid, run_b, preview["plan_hash"])).json()
    assert (again["changed"], again["reconciliation_id"]) == (False, body["reconciliation_id"])
    assert await _review_state(session) == snapshot


# --------------------------------------------------------------------------- #
# Plans desfasats, permisos, tenancy                                            #
# --------------------------------------------------------------------------- #


async def test_un_pla_desfasat_es_refusa_sense_escriure(session, tender, tenant_a, client_a):
    _, company = tenant_a
    s = await _mixed(session, tender, client_a, company)
    plan = (await _preview(client_a, company, s["tid"]))["plan_hash"]
    item = (await _items(session, company, ReviewItemKind.REQUIREMENT))[0]
    await _act(client_a, company, s["tid"], item.id, "note", item.revision, {"note": "Revisat"})
    before = await _review_state(session)

    response = await _apply(client_a, company, s["tid"], s["run_b"], plan)

    assert response.status_code == 409
    error = response.json()["error"]
    assert (error["code"], error["details"]["reason"]) == ("reconciliation_plan_stale",
                                                           "plan_changed")
    assert await _review_state(session) == before


async def test_si_canvia_la_run_vigent_es_refusa(session, tender, tenant_a, client_a):
    _, company = tenant_a
    s = await _mixed(session, tender, client_a, company)
    plan = (await _preview(client_a, company, s["tid"]))["plan_hash"]
    await _run(session, tender, [_volum(tender["x"])])  # la run C passa a ser la vigent
    before = await _review_state(session)

    response = await _apply(client_a, company, s["tid"], s["run_b"], plan)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "current_run_changed"
    assert await _review_state(session) == before


async def test_no_aplicable_si_esta_al_dia(session, tender, tenant_a, client_a):
    _, company = tenant_a
    run_a = await _run(session, tender, [_volum(tender["x"])], first=True)
    await _start(client_a, company, tender["tender_id"])

    response = await _apply(client_a, company, tender["tender_id"], run_a, "0" * 64)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "reconciliation_not_applicable"


async def test_un_rol_de_lectura_no_pot_aplicar(
    session, tender, tenant_a, client_a, client_factory
):
    user, company = tenant_a
    s = await _mixed(session, tender, client_a, company)
    plan = (await _preview(client_a, company, s["tid"]))["plan_hash"]
    viewer = User(email=f"v-{uuid.uuid4().hex[:8]}@empresa-a.com",
                  hashed_password=(await session.get(User, user.id)).hashed_password,
                  full_name="Lectora", is_active=True)
    session.add(viewer)
    await session.flush()
    session.add(CompanyMembership(user_id=viewer.id, company_id=company.id,
                                  role=MembershipRole.VIEWER, is_default=True))
    await session.commit()
    client = client_factory()
    await login(client, viewer.email, TEST_PASSWORD)

    assert (await client.get(_url(company, s["tid"], "/reconciliation"))).status_code == 200
    assert (await _apply(client, company, s["tid"], s["run_b"], plan)).status_code == 403


async def test_reconcile_no_es_una_accio_de_client(session, tender, tenant_a, client_a):
    _, company = tenant_a
    await _run(session, tender, [_volum(tender["x"])], first=True)
    await _start(client_a, company, tender["tender_id"])
    item = (await _items(session, company, ReviewItemKind.REQUIREMENT))[0]

    response = await client_a.post(
        _url(company, tender["tender_id"], f"/items/{item.id}/actions"),
        json={"action": "reconcile", "expected_revision": 1, "payload": {}})

    assert response.status_code == 422


async def test_una_empresa_reconcilia_i_l_altra_no_canvia(
    session, tender, tenant_a, tenant_b, client_a, client_b
):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    x = tender["x"]
    tid = tender["tender_id"]
    rows = [_volum(x), _guarantee(x)]
    await _run(session, tender, rows, first=True)
    await _start(client_a, company_a, tid)
    await _start(client_b, company_b, tid)
    item_b = (await _items(session, company_b, ReviewItemKind.REQUIREMENT))[0]
    await _act(client_b, company_b, tid, item_b.id, "confirm", 1)
    run_b = await _run(session, tender, rows)
    before_b = await _review_state(session, company_b.id)
    plan = (await _preview(client_a, company_a, tid))["plan_hash"]

    body = (await _apply(client_a, company_a, tid, run_b, plan)).json()

    assert body["state"] == "completed"
    assert await _review_state(session, company_b.id) == before_b
    assert (await _preview(client_b, company_b, tid))["state"] == "new_extraction_available"
    blocked_b = await client_b.post(_url(company_b, tid, "/start"))
    assert blocked_b.status_code == 409
    # B no pot aplicar amb el pla d'A.
    assert (await _apply(client_b, company_b, tid, run_b, plan)).status_code == 409


# --------------------------------------------------------------------------- #
# Barreja sense explicacio: es tanca                                            #
# --------------------------------------------------------------------------- #


async def test_una_barreja_que_la_reconciliacio_no_explica_es_mixed(
    session, tender, tenant_a, client_a
):
    _, company = tenant_a
    x = tender["x"]
    tid = tender["tender_id"]
    run_zero = await _run(session, tender, [_term(x)], first=True)  # run anterior, A0
    await _run(session, tender, [_latency(x, page=27)])  # A
    await _start(client_a, company, tid)
    run_b = await _run(session, tender, [_latency(x, page=28)])
    preview = await _preview(client_a, company, tid)
    assert (await _apply(client_a, company, tid, run_b, preview["plan_hash"])).json()[
        "state"] == "needs_review"
    # SINTETIC: una font d'una run que no es ni la base (A) ni la desti (B).
    await _legacy_item(session, company, tid,
                       requirement_id=(await _row(session, run_zero, "Termini d'implantació")).id)

    current = await _preview(client_a, company, tid)

    assert (current["state"], current["requirements"]["state"]) == (
        "mixed_source_runs", "mixed_source_runs")
    assert (await client_a.post(_url(company, tid, "/start"))).status_code == 409
    refused = await _apply(client_a, company, tid, run_b, current["plan_hash"] or "0" * 64)
    assert refused.status_code == 409


# --------------------------------------------------------------------------- #
# Atomicitat, historial protegit, restriccions                                  #
# --------------------------------------------------------------------------- #


async def test_una_fallada_abans_del_commit_no_deixa_res(
    session, session_factory, tender, tenant_a, client_a, monkeypatch
):
    user, company = tenant_a
    s = await _mixed(session, tender, client_a, company)
    plan = (await _preview(client_a, company, s["tid"]))["plan_hash"]
    before = await _review_state(session)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("fallada injectada despres dels reenllaços")

    monkeypatch.setattr(apply_service.audit, "record", boom)
    async with session_factory() as other:
        with pytest.raises(RuntimeError):
            await apply_service.apply_exact(
                other, company_id=company.id, tender_id=s["tid"], target_run_id=s["run_b"],
                plan_hash=plan, actor_id=user.id)
        await other.rollback()

    assert await _review_state(session) == before


async def test_la_run_vella_continua_protegida_i_hi_ha_una_sola_primaria(
    session, tender, tenant_a, client_a
):
    user, company = tenant_a
    s = await _exact_only(session, tender, client_a, company, user)
    plan = (await _preview(client_a, company, s["tid"]))["plan_hash"]
    assert (await _apply(client_a, company, s["tid"], s["run_b"], plan)).status_code == 200
    item = (await _items(session, company, ReviewItemKind.REQUIREMENT))[0]
    new_row = await session.scalar(sa.select(Requirement).where(
        Requirement.run_id == s["run_b"], Requirement.title == GUARANTEE))

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(ReviewItemSource(item_id=item.id, company_id=company.id,
                                         requirement_id=new_row.id,
                                         link_role=ReviewSourceRole.PRIMARY))
            await session.flush()
    with pytest.raises((IntegrityError, DBAPIError)):
        async with session.begin_nested():
            await session.execute(sa.delete(RequirementExtractionRun).where(
                RequirementExtractionRun.id == s["run_a"]))
    await session.rollback()


def test_estat_final():
    from app.schemas.review_reconciliation import MatchCounts, ReconciliationPreview

    def section(state):
        return {"state": state, "state_label": "x", "represented_run_ids": [],
                "canonical_item_count": 0, "current_source_count": 0,
                "counts": MatchCounts()}

    def preview(req, awd):
        return ReconciliationPreview(
            tender_id=uuid.uuid4(), state="up_to_date", state_label="x",
            current_run_id=None, plan_hash=None,
            requirements=section(req), awards=section(awd))

    done = ReviewReconciliationState.COMPLETED
    pending = ReviewReconciliationState.NEEDS_REVIEW
    assert apply_service.final_state(preview("up_to_date", "not_started")) == done
    assert apply_service.final_state(preview("up_to_date", "up_to_date")) == done
    assert apply_service.final_state(
        preview("reconciliation_in_progress", "up_to_date")) == pending
    assert apply_service.final_state(preview("up_to_date", "mixed_source_runs")) == pending


# --------------------------------------------------------------------------- #
# Nomes PostgreSQL                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.postgres
async def test_dues_persones_amb_el_mateix_pla_a_la_vegada(
    session, session_factory, tender, tenant_a, client_a
):
    user, company = tenant_a
    s = await _exact_only(session, tender, client_a, company, user)
    plan = (await _preview(client_a, company, s["tid"]))["plan_hash"]

    async def one():
        async with session_factory() as own:
            result = await apply_service.apply_exact(
                own, company_id=company.id, tender_id=s["tid"], target_run_id=s["run_b"],
                plan_hash=plan, actor_id=user.id)
            await own.commit()
            return result

    results = await asyncio.gather(one(), one())

    assert sorted(r.changed for r in results) == [False, True]
    assert {r.reconciliation_id for r in results} == {results[0].reconciliation_id}
    active = (await session.execute(
        sa.select(ReviewItemSource.item_id, sa.func.count())
        .where(ReviewItemSource.company_id == company.id,
               ReviewItemSource.link_role == ReviewSourceRole.PRIMARY,
               ReviewItemSource.unlinked_at.is_(None))
        .group_by(ReviewItemSource.item_id))).all()
    assert all(count == 1 for _item, count in active) and len(active) == 6
    reconciles = (await session.execute(
        sa.select(ReviewItemRevision.item_id, sa.func.count())
        .where(ReviewItemRevision.action == "reconcile")
        .group_by(ReviewItemRevision.item_id))).all()
    assert len(reconciles) == 6 and all(count == 1 for _item, count in reconciles)
    assert await session.scalar(sa.select(sa.func.count()).select_from(
        ReviewReconciliation)) == 1


@pytest.mark.postgres
async def test_una_revisio_reconcile_no_es_pot_modificar(session, tender, tenant_a, client_a):
    user, company = tenant_a
    s = await _exact_only(session, tender, client_a, company, user)
    plan = (await _preview(client_a, company, s["tid"]))["plan_hash"]
    assert (await _apply(client_a, company, s["tid"], s["run_b"], plan)).status_code == 200
    revision_id = await session.scalar(sa.select(ReviewItemRevision.id).where(
        ReviewItemRevision.action == "reconcile").limit(1))

    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(sa.update(ReviewItemRevision).where(
                ReviewItemRevision.id == revision_id).values(note="x"))
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(sa.delete(ReviewItemRevision).where(
                ReviewItemRevision.id == revision_id))
    await session.rollback()
