"""Criteris d'adjudicacio a la revisio canonica (S-R4). Zero crides externes.

Fixture SINTETICA amb la forma de Palamos: una run v3 real (proveidor fals) i,
sobre ella, els 23 criteris persistits amb els mateixos noms, punts i bases
d'adjudicacio que la run final (preu 80, bloc qualitatiu 20 amb 8 files,
reiteracions de l'Informe, model d'oferta del Sobre 2, 7 informatius i 2
desitjables). Es prova la materialitzacio, els papers, les relacions, la vista
de puntuacio (9 criteris, 100 punts), els avisos, la tenancy, la concurrencia,
que l'extraccio no canvia i l'script d'aplicacio guardat.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.enums import (
    AwardCriterionType,
    AwardEvaluationType,
    MembershipRole,
    ReviewItemKind,
    ReviewRelationType,
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
from app.models.review import ReviewItem, ReviewItemRelation, ReviewItemRevision, ReviewItemSource
from app.services import review_actions, review_awards, review_relations, review_view
from tests import test_requirement_analysis as base
from tests.conftest import TEST_PASSWORD, login
from tests.test_requirement_analysis_v3 import _requirement, _run_v3

analyzable = base.analyzable
_clean_provider = base._clean_provider

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("import_palamos_review")
plan = _load("apply_palamos_award_review")

PRICE_FORMULA = "Vi = Bi × 80 punts / Max(Bs, Bmax)"
#: Cap d'aquests codis interns no pot sortir a la lectura normal.
FORBIDDEN = (
    "formal_award_criterion", "technical_desirable", "candidate_award_criterion",
    "criteria_table", "not_evidenced", "offer_form\"", "declared_kind", "award_class",
    "mechanisms", "declared_informative",
)


def _basis(role: str, name: str) -> dict:
    if name == plan.PRICE:
        return {"contract": "t", "declared_kind": "award_criterion",
                "award_class": "formal_award_criterion", "reasons": ["mechanism_verified"],
                "mechanisms": [{"kind": "points", "check": "verified", "evidence_ids": []},
                               {"kind": "formula", "check": "not_evidenced",
                                "evidence_ids": []}]}
    if role in ("scored", "parent_total"):
        return {"contract": "t", "declared_kind": "award_criterion",
                "award_class": "formal_award_criterion", "reasons": ["mechanism_verified"],
                "mechanisms": [{"kind": "criteria_table", "check": "verified",
                                "evidence_ids": []}]}
    if role == "offer_form_view":
        return {"contract": "t", "declared_kind": "informative",
                "award_class": "formal_award_criterion",
                "reasons": ["mechanism_verified", "kind_overridden_by_evidence"],
                "mechanisms": [{"kind": "offer_form", "check": "verified", "evidence_ids": []}]}
    if role == "desirable":
        return {"contract": "t", "declared_kind": "desirable_feature",
                "award_class": "technical_desirable", "reasons": ["declared_desirable"],
                "mechanisms": []}
    return {"contract": "t", "declared_kind": "informative", "award_class": "informative",
            "reasons": ["declared_informative"], "mechanisms": []}


@pytest.fixture
async def awards(session, analyzable):
    """SINTETIC: una run v3 vigent amb un requisit i els 23 criteris de Palamos."""
    x = analyzable["extraction"]
    run = await _run_v3(session, analyzable,
                        {"requirements": [_requirement(x.id)], "award_candidates": []})
    page = analyzable["pages"][0]
    criteria = {}
    for index, (name, (points, role)) in enumerate(plan.ROLE_PLAN.items()):
        criterion = AwardCriterion(
            run_id=run.id, tender_id=run.tender_id, name=name, points=points,
            formula=PRICE_FORMULA if name == plan.PRICE else None,
            criterion_type=(AwardCriterionType.PRICE if name == plan.PRICE
                            else AwardCriterionType.QUALITY),
            evaluation_type=AwardEvaluationType.AUTOMATIC,
            dedup_key=uuid.uuid4().hex, source_chunk_index=index,
            adjudication_basis_json=_basis(role, name),
        )
        session.add(criterion)
        await session.flush()
        session.add(AwardCriterionEvidence(
            award_criterion_id=criterion.id, extraction_id=x.id, page_id=page.id,
            tender_document_id=analyzable["document"].id, page_number=page.page_number,
            quote=f"cita {index}"))
        criteria[name] = criterion
    await session.commit()
    return {"tender_id": run.tender_id, "run": run, "criteria": criteria}


def _url(company, tender_id, suffix=""):
    return f"/api/v1/companies/{company.id}/tenders/{tender_id}/review{suffix}"


async def _raw_extraction(session):
    snapshot = {}
    for model in (Requirement, RequirementEvidence, AwardCriterion, AwardCriterionEvidence,
                  RequirementExtractionRun):
        table = model.__table__
        snapshot[table.name] = [dict(r) for r in (await session.execute(
            sa.select(table).order_by(table.c.id))).mappings()]
    return snapshot


async def _items(session, company_id):
    rows = (await session.execute(
        sa.select(ReviewItemSource.award_criterion_id, ReviewItemSource.item_id).where(
            ReviewItemSource.company_id == company_id,
            ReviewItemSource.award_criterion_id.is_not(None)))).all()
    return dict(rows)


async def _apply(session, awards, user, company):
    """L'estructura del pla, pel mateix cami que l'script (sense l'empresa QA)."""
    result = await plan.execute_award_review(
        session, company_id=company.id, actor_id=user.id, tender_id=awards["tender_id"],
        batch_id=uuid.uuid4(), expected_requirements=0)
    await session.commit()
    return result


async def _act(client, company, tender_id, item_id, action, expected, payload=None):
    return await client.post(
        _url(company, tender_id, f"/items/{item_id}/actions"),
        json={"action": action, "expected_revision": expected, "payload": payload or {}})


# --------------------------------------------------------------------------- #
# Materialitzacio                                                               #
# --------------------------------------------------------------------------- #


async def test_es_materialitza_una_vegada_sense_paper_i_es_idempotent(session, awards, tenant_a):
    user, company = tenant_a
    tid = awards["tender_id"]

    first = await review_awards.start_award_review(
        session, company_id=company.id, tender_id=tid, actor_id=user.id)
    await session.commit()
    second = await review_awards.start_award_review(
        session, company_id=company.id, tender_id=tid, actor_id=user.id)
    await session.commit()

    assert (first.created, first.already_present, first.total_award_criteria) == (23, 0, 23)
    assert (second.created, second.already_present) == (0, 23)
    items = (await session.scalars(sa.select(ReviewItem))).all()
    assert len(items) == 23
    assert {(i.kind, i.award_role, i.revision, i.origin_hint.value) for i in items} == {
        (ReviewItemKind.AWARD_CRITERION, None, 1, "extraction")}
    sources = (await session.scalars(sa.select(ReviewItemSource))).all()
    assert len(sources) == 23
    assert all(s.link_role == ReviewSourceRole.PRIMARY and s.award_criterion_id
               and s.requirement_id is None for s in sources)
    assert {s.award_criterion_id for s in sources} == {c.id for c in awards["criteria"].values()}
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemRevision)) == 23
    # Materialitzar criteris no toca els requisits de la revisio.
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItem).where(
        ReviewItem.kind == ReviewItemKind.REQUIREMENT)) == 0


async def test_la_instantania_no_diu_verificada_una_formula_sense_prova(session, awards,
                                                                         tenant_a):
    user, company = tenant_a
    await review_awards.start_award_review(
        session, company_id=company.id, tender_id=awards["tender_id"], actor_id=user.id)
    price = awards["criteria"][plan.PRICE]
    item = await session.get(ReviewItem, (await _items(session, company.id))[price.id])

    snap = item.accepted_json
    assert (snap["points"], snap["formula"], snap["formula_status"]) == (
        80.0, PRICE_FORMULA, "not_evidence_verified")
    assert snap["mechanism_status"] == "verified"  # els punts si que hi son provats
    assert item.verification_status.value == "unreviewed"
    informative = awards["criteria"][plan.INFORMATIVE[0]]
    other = await session.get(ReviewItem, (await _items(session, company.id))[informative.id])
    assert (other.accepted_json["mechanism_status"], other.accepted_json["formula_status"]) == (
        "none", None)


# --------------------------------------------------------------------------- #
# Estructura de Palamos                                                         #
# --------------------------------------------------------------------------- #


async def test_la_vista_de_puntuacio_de_palamos_dona_9_criteris_i_100_punts(
    session, awards, tenant_a, client_a
):
    user, company = tenant_a
    tid = awards["tender_id"]
    before = await _raw_extraction(session)
    await _apply(session, awards, user, company)

    scoring = (await client_a.get(_url(company, tid, "/award-criteria?view=scoring"))).json()
    everything = (await client_a.get(_url(company, tid, "/award-criteria?view=all"))).json()

    assert (scoring["scored_count"], scoring["total_points"]) == (9, 100.0)
    assert [c["points"] for c in scoring["criteria"]] == [80, 5, 4, 3, 2, 2, 2, 1, 1]
    assert scoring["criteria"][0]["name"] == plan.PRICE
    assert all(c["counts_in_total"] and c["role"] == "scored" for c in scoring["criteria"])
    assert {c["parent"]["name"] for c in scoring["criteria"][1:]} == {plan.QUALITATIVE}
    assert scoring["criteria"][0]["parent"] is None
    assert len(everything["criteria"]) == 23
    assert sum(c["counts_in_total"] for c in everything["criteria"]) == 9
    roles = {c["name"]: c["role"] for c in everything["criteria"]}
    assert sum(r == "not_scored" for r in roles.values()) == 7
    assert sum(r == "desirable" for r in roles.values()) == 2
    session.expire_all()
    assert await _raw_extraction(session) == before


async def test_pares_reiteracions_i_model_d_oferta_no_compten(session, awards, tenant_a,
                                                               client_a):
    user, company = tenant_a
    tid = awards["tender_id"]
    await _apply(session, awards, user, company)
    rows = {c["name"]: c for c in (await client_a.get(
        _url(company, tid, "/award-criteria?view=all"))).json()["criteria"]}

    parent = rows[plan.QUALITATIVE]
    assert (parent["role"], parent["role_label"], parent["counts_in_total"]) == (
        "parent_total", "Total del bloc", False)
    assert sorted((c["points"] for c in parent["children"]), reverse=True) == [
        5, 4, 3, 2, 2, 2, 1, 1]
    assert sum(c["points"] for c in parent["children"]) == parent["points"] == 20
    assert {r["name"] for r in parent["restatements"]} == {plan.INFORME_QUALITATIVE,
                                                           plan.OFFER_FORM}
    informe = rows[plan.INFORME_QUALITATIVE]
    assert (informe["counts_in_total"], informe["folded_into"]["name"]) == (False,
                                                                           plan.QUALITATIVE)
    offer = rows[plan.OFFER_FORM]
    assert (offer["role"], offer["role_label"], offer["counts_in_total"]) == (
        "offer_form_view", "Model d'oferta", False)
    for restated, original in ((plan.INFORME_MOBILE, plan.MOBILE),
                               (plan.INFORME_CATALAN, plan.CATALAN)):
        # Conserva el paper del plec, pero plegat: no compta dues vegades.
        assert (rows[restated]["role"], rows[restated]["counts_in_total"]) == ("scored", False)
        assert rows[restated]["folded_into"]["name"] == original
        assert [r["name"] for r in rows[original]["restatements"]] == [restated]
    labels = {c["role"]: c["role_label"] for c in rows.values()}
    assert labels == {"scored": "Puntua", "parent_total": "Total del bloc",
                      "offer_form_view": "Model d'oferta", "not_scored": "No puntua",
                      "desirable": "Millora valorable"}


async def test_els_avisos_de_palamos_i_la_formula_del_preu(session, awards, tenant_a, client_a):
    user, company = tenant_a
    tid = awards["tender_id"]
    await _apply(session, awards, user, company)
    body = (await client_a.get(_url(company, tid, "/award-criteria"))).json()

    assert body["view"] == "scoring"
    checks = {c["code"]: c for c in body["checks"]}
    assert set(checks) == {"formula_not_evidence_verified", "restatement_excluded"}
    assert checks["restatement_excluded"]["details"] == {"points_excluded": 2.0}
    assert len(checks["restatement_excluded"]["item_ids"]) == 2
    price = body["criteria"][0]
    assert (price["formula"], price["formula_status"], price["formula_label"]) == (
        PRICE_FORMULA, "not_evidence_verified", "Fórmula pendent de verificar")
    assert checks["formula_not_evidence_verified"]["item_ids"] == [price["canonical_id"]]
    # Assignar papers no verifica res.
    assert {c["verification_status"] for c in body["criteria"]} == {"unreviewed"}
    one = (await client_a.get(_url(company, tid, f"/award-criteria/{price['canonical_id']}")))
    assert one.status_code == 200 and one.json()["formula_status"] == "not_evidence_verified"


async def test_la_lectura_no_ensenya_codis_interns(session, awards, tenant_a, client_a):
    user, company = tenant_a
    await _apply(session, awards, user, company)
    raw = (await client_a.get(_url(company, awards["tender_id"], "/award-criteria?view=all"))
           ).text
    for code in FORBIDDEN:
        assert code not in raw, code
    assert "Puntuació confirmada al document" in raw


async def test_els_avisos_no_reescriuen_punts(session, awards, tenant_a):
    user, company = tenant_a
    tid = awards["tender_id"]
    await review_awards.start_award_review(session, company_id=company.id, tender_id=tid,
                                           actor_id=user.id)
    await session.commit()
    unassigned = await review_awards.list_award_criteria(
        session, company_id=company.id, tender_id=tid, view="scoring")
    assert {c.code for c in unassigned.checks} >= {"role_unassigned", "total_points_mismatch"}
    assert (unassigned.scored_count, unassigned.total_points) == (0, 0)

    await _apply_after_start(session, awards, user, company)
    # Treure un fill del bloc: el total del bloc ja no es la suma i el total no es 100.
    child = (await _items(session, company.id))[awards["criteria"][plan.MOBILE].id]
    relation = await session.scalar(sa.select(ReviewItemRelation).where(
        ReviewItemRelation.to_item_id == child,
        ReviewItemRelation.relation_type == ReviewRelationType.PARENT_OF))
    await review_relations.archive_relation(session, company_id=company.id, tender_id=tid,
                                            relation_id=relation.id, actor_id=user.id)
    informative = (await _items(session, company.id))[
        awards["criteria"][plan.INFORMATIVE[0]].id]
    item = await session.get(ReviewItem, informative)
    await review_actions.apply_review_action(
        session, company_id=company.id, tender_id=tid, item_id=informative, actor_id=user.id,
        expected_revision=item.revision, action="set_award_role", payload={"role": "scored"})
    await session.commit()

    view = await review_awards.list_award_criteria(
        session, company_id=company.id, tender_id=tid, view="scoring")
    checks = {c.code: c for c in view.checks}
    assert checks["parent_total_mismatch"].details == {"parent_points": 20.0,
                                                       "children_sum": 19.0}
    assert checks["scored_without_points"].item_ids == [informative]
    # El fill tret continua puntuant tot sol: 100 punts, pero ara sense pare.
    assert view.total_points == 100.0
    assert (await session.get(AwardCriterion, awards["criteria"][plan.QUALITATIVE].id)
            ).points == 20.0


async def _apply_after_start(session, awards, user, company):
    """L'estructura quan els elements ja estan materialitzats (no nomes via l'script)."""
    tid = awards["tender_id"]
    items = await _items(session, company.id)
    if not items:
        await _apply(session, awards, user, company)
        return
    by_name = {name: items[c.id] for name, c in awards["criteria"].items()}
    for name, (_points, role) in plan.ROLE_PLAN.items():
        item = await session.get(ReviewItem, by_name[name])
        await review_actions.apply_review_action(
            session, company_id=company.id, tender_id=tid, item_id=item.id, actor_id=user.id,
            expected_revision=item.revision, action="set_award_role", payload={"role": role})
    for source, target, kind, note in plan.RELATION_PLAN:
        await review_relations.create_relation(
            session, company_id=company.id, tender_id=tid, from_item_id=by_name[source],
            to_item_id=by_name[target], relation_type=ReviewRelationType(kind), note=note,
            actor_id=user.id)
    await session.commit()


# --------------------------------------------------------------------------- #
# Accio set_award_role                                                          #
# --------------------------------------------------------------------------- #


async def test_set_award_role_afegeix_una_revisio_i_no_verifica(session, awards, tenant_a,
                                                                 client_a):
    user, company = tenant_a
    tid = awards["tender_id"]
    assert (await client_a.post(_url(company, tid, "/award-criteria/start"))).status_code == 200
    item_id = (await _items(session, company.id))[awards["criteria"][plan.PRICE].id]

    response = await _act(client_a, company, tid, item_id, "set_award_role", 1,
                          {"role": "scored", "note": "Preu"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["role"], body["role_label"], body["review_revision"]) == ("scored", "Puntua", 2)
    assert (body["verification_status"], body["counts_in_total"]) == ("unreviewed", True)
    history = (await client_a.get(_url(company, tid, f"/items/{item_id}/history"))).json()
    assert [(h["action"], h["action_label"]) for h in history] == [
        ("create", "Creat"), ("set_award_role", "Canvi de paper")]
    assert history[1]["changes"]["role_before"] is None
    assert history[1]["changes"]["role_after"] == "scored"
    audit = await session.scalar(sa.select(AuditLog).where(
        AuditLog.entity_id == item_id).order_by(AuditLog.created_at.desc()).limit(1))
    assert "paper=scored" in audit.summary and "Preu" not in audit.summary


async def test_set_award_role_valida_i_controla_la_revisio(session, awards, tenant_a, client_a):
    user, company = tenant_a
    tid = awards["tender_id"]
    await client_a.post(_url(company, tid, "/award-criteria/start"))
    await client_a.post(_url(company, tid, "/start"))
    item_id = (await _items(session, company.id))[awards["criteria"][plan.PRICE].id]
    requirement_item = await session.scalar(sa.select(ReviewItem.id).where(
        ReviewItem.kind == ReviewItemKind.REQUIREMENT, ReviewItem.company_id == company.id))

    assert (await _act(client_a, company, tid, item_id, "set_award_role", 1,
                       {"role": "scored"})).status_code == 200
    stale = await _act(client_a, company, tid, item_id, "set_award_role", 1,
                       {"role": "not_scored"})
    same = await _act(client_a, company, tid, item_id, "set_award_role", 2, {"role": "scored"})
    bogus = await _act(client_a, company, tid, item_id, "set_award_role", 2, {"role": "bonus"})
    on_requirement = await _act(client_a, company, tid, requirement_item, "set_award_role", 1,
                                {"role": "scored"})
    correct = await _act(client_a, company, tid, item_id, "correct", 2, {"title": "x"})

    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_revision"] == 2
    assert (same.status_code, bogus.status_code, on_requirement.status_code,
            correct.status_code) == (422, 422, 422, 422)
    assert (await session.get(ReviewItem, requirement_item)).revision == 1


async def test_un_rol_de_lectura_no_pot_assignar_papers(session, awards, tenant_a, client_a,
                                                         client_factory):
    _, company = tenant_a
    tid = awards["tender_id"]
    await client_a.post(_url(company, tid, "/award-criteria/start"))
    item_id = (await _items(session, company.id))[awards["criteria"][plan.PRICE].id]
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

    assert (await client.get(_url(company, tid, "/award-criteria"))).status_code == 200
    assert (await _act(client, company, tid, item_id, "set_award_role", 1,
                       {"role": "scored"})).status_code == 403
    assert (await client.post(_url(company, tid, "/award-criteria/start"))).status_code == 403


@pytest.mark.postgres
async def test_pg_dos_canvis_de_paper_simultanis_nomes_un_guanya(
    session_factory, session, awards, tenant_a
):
    user, company = tenant_a
    tid = awards["tender_id"]
    await review_awards.start_award_review(session, company_id=company.id, tender_id=tid,
                                           actor_id=user.id)
    await session.commit()
    item_id = (await _items(session, company.id))[awards["criteria"][plan.PRICE].id]

    async with session_factory() as first, session_factory() as second:
        await review_actions.apply_review_action(
            first, company_id=company.id, tender_id=tid, item_id=item_id, actor_id=user.id,
            expected_revision=1, action="set_award_role", payload={"role": "scored"})
        racing = asyncio.create_task(review_actions.apply_review_action(
            second, company_id=company.id, tender_id=tid, item_id=item_id, actor_id=user.id,
            expected_revision=1, action="set_award_role", payload={"role": "not_scored"}))
        await asyncio.sleep(0.3)
        assert not racing.done(), "la segona hauria d'esperar el lock"
        await first.commit()
        with pytest.raises(review_actions.StaleRevisionError):
            await racing
        await second.rollback()

    session.expire_all()
    item = await session.get(ReviewItem, item_id)
    assert (item.revision, item.award_role.value) == (2, "scored")


# --------------------------------------------------------------------------- #
# Relacions entre criteris                                                      #
# --------------------------------------------------------------------------- #


async def test_una_reiteracio_de_criteri_es_plega_en_un_sol_desti_i_sense_cicles(
    session, awards, tenant_a
):
    user, company = tenant_a
    tid = awards["tender_id"]
    await review_awards.start_award_review(session, company_id=company.id, tender_id=tid,
                                           actor_id=user.id)
    items = await _items(session, company.id)
    ids = {name: items[c.id] for name, c in awards["criteria"].items()}

    async def relate(a, b, kind="restatement_of"):
        return await review_relations.create_relation(
            session, company_id=company.id, tender_id=tid, from_item_id=ids[a],
            to_item_id=ids[b], relation_type=ReviewRelationType(kind), note=None,
            actor_id=user.id)

    await relate(plan.INFORME_MOBILE, plan.MOBILE)
    with pytest.raises(review_relations.ValidationError, match="ja es plega"):
        await relate(plan.INFORME_MOBILE, plan.CATALAN)
    with pytest.raises(review_relations.ValidationError, match="ja es plega"):
        await relate(plan.INFORME_MOBILE, plan.CATALAN, "duplicate_of")
    with pytest.raises(review_relations.ValidationError, match="cicle"):
        await relate(plan.MOBILE, plan.INFORME_MOBILE)
    await relate(plan.INFORME_CATALAN, plan.CATALAN)
    # Un requisit no es pot relacionar amb un criteri.
    await review_view.start_review(session, company_id=company.id, tender_id=tid,
                                   actor_id=user.id)
    requirement_item = await session.scalar(sa.select(ReviewItem.id).where(
        ReviewItem.kind == ReviewItemKind.REQUIREMENT))
    with pytest.raises(review_relations.ValidationError, match="mateix tipus"):
        await review_relations.create_relation(
            session, company_id=company.id, tender_id=tid, from_item_id=requirement_item,
            to_item_id=ids[plan.PRICE], relation_type=ReviewRelationType.RESTATEMENT_OF,
            note=None, actor_id=user.id)


async def test_un_pare_te_cada_fill_una_sola_vegada(session, awards, tenant_a):
    user, company = tenant_a
    await _apply(session, awards, user, company)
    tid = awards["tender_id"]
    items = await _items(session, company.id)
    parent = items[awards["criteria"][plan.INFORME_QUALITATIVE].id]
    child = items[awards["criteria"][plan.MOBILE].id]

    with pytest.raises(review_relations.ValidationError, match="forma part"):
        await review_relations.create_relation(
            session, company_id=company.id, tender_id=tid, from_item_id=parent,
            to_item_id=child, relation_type=ReviewRelationType.PARENT_OF, note=None,
            actor_id=user.id)


# --------------------------------------------------------------------------- #
# Tenancy                                                                       #
# --------------------------------------------------------------------------- #


async def test_l_empresa_b_no_veu_ni_toca_l_estructura_d_a(session, awards, tenant_a, tenant_b,
                                                           client_a, client_b):
    (user_a, company_a), (_, company_b) = tenant_a, tenant_b
    tid = awards["tender_id"]
    await _apply(session, awards, user_a, company_a)
    price_a = (await _items(session, company_a.id))[awards["criteria"][plan.PRICE].id]

    empty = (await client_b.get(_url(company_b, tid, "/award-criteria?view=all"))).json()
    assert (empty["criteria"], empty["scored_count"]) == ([], 0)
    assert (await client_b.get(_url(company_b, tid, f"/award-criteria/{price_a}"))
            ).status_code == 404
    assert (await _act(client_b, company_b, tid, price_a, "set_award_role", 2,
                       {"role": "not_scored"})).status_code == 404
    assert (await client_b.get(_url(company_b, tid, f"/items/{price_a}/relations"))
            ).status_code == 404
    assert (await client_b.post(_url(company_b, tid, "/award-criteria/start"))
            ).status_code == 200

    own_b = (await client_b.get(_url(company_b, tid, "/award-criteria?view=all"))).json()
    assert len(own_b["criteria"]) == 23
    assert {c["role"] for c in own_b["criteria"]} == {None}
    a_view = (await client_a.get(_url(company_a, tid, "/award-criteria"))).json()
    assert (a_view["scored_count"], a_view["total_points"]) == (9, 100.0)
    # La mateixa fila d'extraccio, dos elements: un per empresa.
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemSource).where(
        ReviewItemSource.award_criterion_id == awards["criteria"][plan.PRICE].id)) == 2


# --------------------------------------------------------------------------- #
# Script d'aplicacio guardat                                                    #
# --------------------------------------------------------------------------- #


def test_el_pla_es_coherent():
    plan.validate_plan()
    assert len(plan.ROLE_PLAN) == 23 and len(plan.RELATION_PLAN) == 12
    assert sum(plan.CHILDREN.values()) == 20
    assert sorted(plan.CHILDREN.values(), reverse=True) == [5, 4, 3, 2, 2, 2, 1, 1]


async def test_l_script_aplica_una_vegada_i_despres_no_escriu(session, awards, tenant_a):
    user, company = tenant_a
    tid = awards["tender_id"]
    assert await plan.award_status(session, company.id, tid) == "none"

    await _apply(session, awards, user, company)
    checks = await plan.verify_award_review(session, company.id, tid)
    plan.assert_verified(checks)
    assert checks["roles"] == {"scored": 11, "parent_total": 2, "offer_form_view": 1,
                               "not_scored": 7, "desirable": 2}
    assert await plan.award_status(session, company.id, tid) == "complete"
    counts = [await session.scalar(sa.select(sa.func.count()).select_from(m))
              for m in (ReviewItem, ReviewItemRevision, ReviewItemRelation, AuditLog)]
    assert counts[:3] == [23, 46, 12]

    with pytest.raises(plan.AlreadyAppliedError):
        await _apply(session, awards, user, company)
    await session.rollback()
    assert [await session.scalar(sa.select(sa.func.count()).select_from(m))
            for m in (ReviewItem, ReviewItemRevision, ReviewItemRelation, AuditLog)] == counts
    revision = await session.scalar(sa.select(ReviewItemRevision).where(
        ReviewItemRevision.action == "set_award_role").limit(1))
    assert revision.payload_json["package"] == plan.PACKAGE


async def test_l_script_ho_desfa_tot_si_falla(session, awards, tenant_a, monkeypatch):
    user, company = tenant_a
    original = review_relations.create_relation
    calls = {"n": 0}

    async def failing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("fallada simulada a la relacio 5")
        return await original(*args, **kwargs)

    monkeypatch.setattr(review_relations, "create_relation", failing)
    before = await _raw_extraction(session)
    with pytest.raises(RuntimeError, match="relacio 5"):
        await plan.execute_award_review(
            session, company_id=company.id, actor_id=user.id, tender_id=awards["tender_id"],
            batch_id=uuid.uuid4(), expected_requirements=0)
    await session.rollback()

    for model in (ReviewItem, ReviewItemSource, ReviewItemRevision, ReviewItemRelation):
        assert await session.scalar(sa.select(sa.func.count()).select_from(model)) == 0
    assert await session.scalar(sa.select(sa.func.count()).select_from(AuditLog).where(
        AuditLog.summary.contains("lot="))) == 0
    assert await _raw_extraction(session) == before


async def test_l_script_s_atura_amb_un_estat_desconegut(session, awards, tenant_a):
    user, company = tenant_a
    tid = awards["tender_id"]
    await review_awards.start_award_review(session, company_id=company.id, tender_id=tid,
                                           actor_id=user.id)
    await session.commit()

    assert await plan.award_status(session, company.id, tid) == "unknown"
    with pytest.raises(plan.base.ImportGuardError, match="no son els del pla"):
        await _apply(session, awards, user, company)
    await session.rollback()


async def test_l_script_exigeix_els_requisits_de_la_importacio(session, awards, tenant_a):
    user, company = tenant_a
    with pytest.raises(plan.base.ImportGuardError, match="requisits de revisio"):
        await plan.execute_award_review(
            session, company_id=company.id, actor_id=user.id, tender_id=awards["tender_id"],
            batch_id=uuid.uuid4())
    await session.rollback()
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItem)) == 0


async def test_l_script_refusa_un_pla_que_no_quadra(session, awards, tenant_a):
    user, company = tenant_a
    criterion = awards["criteria"][plan.MOBILE]
    criterion.points = 2.0
    await session.commit()

    with pytest.raises(plan.base.ImportGuardError, match="punts diferents"):
        await _apply(session, awards, user, company)
    await session.rollback()
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItem)) == 0


async def test_l_estat_de_la_importacio_de_requisits_ignora_els_criteris(session, awards,
                                                                         tenant_a):
    user, company = tenant_a
    await _apply(session, awards, user, company)

    status = await plan.base.import_status(session, company.id, awards["tender_id"],
                                           expected_manual_ids=set(), expected_items=0)
    assert status == "none"


@pytest.mark.parametrize("database", ["licitacions_review_s3", "licitacions_v3_probe_palamos"])
async def test_l_script_refusa_les_bases_protegides(tmp_path, monkeypatch, database):
    monkeypatch.setenv("DATABASE_URL", f"postgresql+asyncpg://postgres@127.0.0.1:5433/{database}")
    args = plan.build_parser().parse_args(["--write", "--out-dir", str(tmp_path)])
    assert await plan.run(args) == 2


async def test_l_script_refusa_el_port_5432(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres@127.0.0.1:5432/licitacions_review_palamos")
    assert await plan.run(plan.build_parser().parse_args(["--write"])) == 2


def test_la_sortida_no_porta_cites(tmp_path):
    from app.schemas.review import AwardCriteriaView, CanonicalAwardCriterion

    row = CanonicalAwardCriterion(
        canonical_id=uuid.uuid4(), name="Criteri preu", description=None, points=80.0,
        role="scored", role_label="Puntua", counts_in_total=True, parent=None,
        mechanism_status="verified", mechanism_label="x", formula=PRICE_FORMULA,
        formula_status="not_evidence_verified", formula_label="y", provenance="automated",
        provenance_label="z", verification_status="unreviewed", verification_label="w",
        evidence=[{"document": "PCAP", "page": 14, "section": None, "quote": "CITA SECRETA",
                   "status_label": "Al document"}],
        documents=["PCAP"], pages=[14], review_revision=2)
    plan.write_outputs(tmp_path, AwardCriteriaView(view="all", criteria=[row], scored_count=1,
                                                   total_points=80.0, checks=[]))
    text = (tmp_path / "palamos_award_structure.csv").read_text(encoding="utf-8")
    assert "CITA SECRETA" not in text and "not_evidence_verified" in text
