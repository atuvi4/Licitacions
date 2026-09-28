"""Guarda de rerun (S-R7): una revisio feta sobre la run A no es duplica quan B es la vigent.

Defecte que es tanca (auditoria de rerun): revisio sobre A + run B vigent +
`POST /review/start` = un segon element canonic per a cada requisit ja revisat
(els de B tenen ids nous). El mateix passava amb els criteris d'adjudicacio.

Aquesta guarda NOMES detecta i refusa amb un 409, sense cap escriptura. No
reconcilia res. Tot SINTETIC, proveidor fals, zero crides externes.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.enums import ReviewItemKind, ReviewOriginHint, ReviewSourceRole, ReviewVerification
from app.models.requirements import AwardCriterion, Requirement
from app.models.review import ReviewItem, ReviewItemRelation, ReviewItemRevision, ReviewItemSource
from app.schemas.review import ManualRequirementCreate
from app.services import review_actions, review_runs
from app.services.requirement_analysis import analyze_tender_requirements
from tests import test_requirement_analysis as base
from tests.test_requirement_analysis import PAGE_27, PAGE_28, tender_of
from tests.test_requirement_analysis_v3 import V3, _evidence, _requirement, _run_v3

analyzable = base.analyzable
_clean_provider = base._clean_provider

from app.ai.providers import fake  # noqa: E402

VOLUM = "Volum anual de negoci minim"
LATENCY = "Latència màxima"
GUARANTEE = "Garantia dels equips"
LATENCY_LINE = "Latència màx. entre dos punts en serveis de dades <70 ms Greu"
GUARANTEE_LINE = "Els equips hauran de tenir garantia del fabricant"
NEW_LINE = "Termini d'implantació de 30 dies"
MESSAGE = "Hi ha una extracció nova. Revisa els canvis abans d'actualitzar la revisió."


def _payload(x, *, rerun: bool):
    requirements = [
        _requirement(x),
        _requirement(x, category="technical_requirement", operator=None, title=LATENCY,
                     claims=[], claims_complete="partial",
                     evidence=[_evidence(x, eid="e2", page=28 if rerun else 27,
                                         quote=LATENCY_LINE)]),
        _requirement(x, category="technical_requirement", operator=None, title=GUARANTEE,
                     claims=[], claims_complete="partial",
                     evidence=[_evidence(x, eid="e3", page=28, quote=GUARANTEE_LINE)]),
    ]
    if rerun:
        requirements.append(
            _requirement(x, category="deadline", operator=None, title="Termini d'implantació",
                         claims=[], claims_complete="partial",
                         evidence=[_evidence(x, eid="e4", page=28, quote=NEW_LINE)]))
    return {"requirements": requirements, "award_candidates": []}


async def _awards_on(session, run, names=("Preu", "Qualitat")):
    for index, name in enumerate(names):
        session.add(AwardCriterion(
            run_id=run.id, tender_id=run.tender_id, name=name, points=50.0,
            dedup_key=uuid.uuid4().hex, source_chunk_index=index,
            adjudication_basis_json=None))
    await session.commit()


@pytest.fixture
async def run_a(session, analyzable):
    """SINTETIC: la run A vigent amb tres requisits i dos criteris."""
    first, second = analyzable["pages"]
    first.text = PAGE_27 + LATENCY_LINE + "\n"
    second.text = PAGE_28 + LATENCY_LINE + "\n" + GUARANTEE_LINE + "\n" + NEW_LINE + "\n"
    await session.commit()
    run = await _run_v3(session, analyzable, _payload(analyzable["extraction"].id, rerun=False))
    await _awards_on(session, run)
    return {"tender_id": run.tender_id, "run_id": run.id, "analyzable": analyzable}


async def _rerun(session, run_a):
    """La run B passa a ser la vigent: un requisit mogut i un de nou, criteris nous."""
    analyzable = run_a["analyzable"]
    fake.queue_response(_payload(analyzable["extraction"].id, rerun=True))
    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender, profile=V3, force=True)).run
    await _awards_on(session, run)
    assert run.is_current and run.id != run_a["run_id"]
    return run.id


def _url(company, tender_id, suffix=""):
    return f"/api/v1/companies/{company.id}/tenders/{tender_id}/review{suffix}"


async def _start(client, company, tender_id):
    return await client.post(_url(company, tender_id, "/start"))


async def _start_awards(client, company, tender_id):
    return await client.post(_url(company, tender_id, "/award-criteria/start"))


async def _act(client, company, tender_id, item_id, action, expected, payload=None):
    response = await client.post(
        _url(company, tender_id, f"/items/{item_id}/actions"),
        json={"action": action, "expected_revision": expected, "payload": payload or {}})
    assert response.status_code == 200, response.text
    return response.json()


async def _items(session, company, kind):
    return list((await session.scalars(
        sa.select(ReviewItem).where(ReviewItem.company_id == company.id, ReviewItem.kind == kind)
        .order_by(ReviewItem.created_at, ReviewItem.id)
        .execution_options(populate_existing=True))).all())


async def _state(session):
    """Tot el que una materialitzacio podria tocar, en forma comparable (columnes, no ORM)."""

    async def rows(table, *order):
        result = await session.execute(sa.select(table).order_by(*order))
        return [tuple(r) for r in result.all()]

    items = await rows(
        sa.select(ReviewItem.id, ReviewItem.company_id, ReviewItem.kind,
                  ReviewItem.verification_status, ReviewItem.award_role, ReviewItem.revision,
                  ReviewItem.accepted_json, ReviewItem.archived_at).subquery(), "id")
    sources = await rows(
        sa.select(ReviewItemSource.id, ReviewItemSource.item_id, ReviewItemSource.requirement_id,
                  ReviewItemSource.award_criterion_id, ReviewItemSource.source_ref_id,
                  ReviewItemSource.link_role, ReviewItemSource.unlinked_at).subquery(), "id")
    revisions = await rows(
        sa.select(ReviewItemRevision.id, ReviewItemRevision.item_id,
                  ReviewItemRevision.revision_no).subquery(), "id")
    relations = await rows(
        sa.select(ReviewItemRelation.id, ReviewItemRelation.archived_at).subquery(), "id")
    audits = await session.scalar(sa.select(sa.func.count()).select_from(AuditLog))
    return {"items": items, "sources": sources, "revisions": revisions,
            "relations": relations, "audit_logs": audits}


def _assert_reconciliation_409(response, *, current, represented):
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "review_reconciliation_required"
    assert error["message"] == MESSAGE
    # Nomes ids: cap chunk, prompt, token ni configuracio.
    assert error["details"] == {
        "current_run_id": str(current),
        "represented_run_ids": sorted(str(r) for r in represented),
    }


# --------------------------------------------------------------------------- #
# Primera materialitzacio i idempotencia a la mateixa run                       #
# --------------------------------------------------------------------------- #


async def test_primer_start_i_repeticio_a_la_mateixa_run(session, run_a, tenant_a, client_a):
    _, company = tenant_a
    tid = run_a["tender_id"]

    first = await _start(client_a, company, tid)
    again = await _start(client_a, company, tid)
    awards_first = await _start_awards(client_a, company, tid)
    awards_again = await _start_awards(client_a, company, tid)

    assert first.status_code == 200 and first.json()["created"] == 3
    assert again.status_code == 200
    assert (again.json()["created"], again.json()["already_present"]) == (0, 3)
    assert awards_first.status_code == 200 and awards_first.json()["created"] == 2
    assert awards_again.status_code == 200
    assert (awards_again.json()["created"], awards_again.json()["already_present"]) == (0, 2)
    overview = (await client_a.get(_url(company, tid, "/overview"))).json()
    assert overview["reconciliation_required"] is False
    assert overview["current_run_id"] == str(run_a["run_id"])
    assert overview["represented_requirement_run_ids"] == [str(run_a["run_id"])]
    assert overview["represented_award_run_ids"] == [str(run_a["run_id"])]


# --------------------------------------------------------------------------- #
# Regressio del defecte: rerun + start                                          #
# --------------------------------------------------------------------------- #


async def test_rerun_no_duplica_requisits_ni_toca_l_estat_huma(
    session, run_a, tenant_a, client_a
):
    user, company = tenant_a
    tid, a = run_a["tender_id"], run_a["run_id"]
    assert (await _start(client_a, company, tid)).status_code == 200
    by_title = {i.accepted_json["title"]: i.id for i in
                await _items(session, company, ReviewItemKind.REQUIREMENT)}
    await _act(client_a, company, tid, by_title[VOLUM], "confirm", 1)
    await _act(client_a, company, tid, by_title[LATENCY], "correct", 1, {
        "values": [{"key": "latencia", "operator": "lt", "value": 70, "unit_label": "ms"}],
        "verification_status": "verified"})
    await review_actions.create_manual_requirement(
        session, company_id=company.id, tender_id=tid, user=user,
        data=ManualRequirementCreate(title="Requisit manual", category="other"))
    await session.commit()

    b = await _rerun(session, run_a)
    before = await _state(session)

    response = await _start(client_a, company, tid)

    _assert_reconciliation_409(response, current=b, represented={a})
    after = await _state(session)
    assert after == before
    items = await _items(session, company, ReviewItemKind.REQUIREMENT)
    assert len(items) == 4  # 3 extrets d'A + 1 manual: cap duplicat
    state = {i.accepted_json["title"]: i for i in items}
    assert state[VOLUM].verification_status == ReviewVerification.VERIFIED
    assert state[LATENCY].verification_status == ReviewVerification.VERIFIED
    assert [(v["key"], v["value"], v["source"]) for v in state[LATENCY].accepted_json["values"]] \
        == [("latencia", 70, "reviewer")]
    overview = (await client_a.get(_url(company, tid, "/overview"))).json()
    assert overview["reconciliation_required"] is True
    assert overview["current_run_id"] == str(b)
    assert overview["represented_requirement_run_ids"] == [str(a)]


async def test_rerun_no_duplica_criteris_ni_en_toca_els_papers(
    session, run_a, tenant_a, client_a
):
    _, company = tenant_a
    tid, a = run_a["tender_id"], run_a["run_id"]
    assert (await _start_awards(client_a, company, tid)).status_code == 200
    for item in await _items(session, company, ReviewItemKind.AWARD_CRITERION):
        await _act(client_a, company, tid, item.id, "set_award_role", 1, {"role": "scored"})

    b = await _rerun(session, run_a)
    before = await _state(session)

    response = await _start_awards(client_a, company, tid)

    _assert_reconciliation_409(response, current=b, represented={a})
    assert await _state(session) == before
    awards = await _items(session, company, ReviewItemKind.AWARD_CRITERION)
    assert [(i.award_role, i.revision) for i in awards] == [("scored", 2), ("scored", 2)]
    scoring = (await client_a.get(_url(company, tid, "/award-criteria?view=scoring"))).json()
    assert (scoring["scored_count"], scoring["total_points"]) == (2, 100.0)


async def test_requisits_i_criteris_es_guarden_per_separat(session, run_a, tenant_a, client_a):
    """Revisio de requisits sobre A, criteris mai materialitzats: nomes es bloqueja el que toca."""
    _, company = tenant_a
    tid, a = run_a["tender_id"], run_a["run_id"]
    assert (await _start(client_a, company, tid)).status_code == 200
    b = await _rerun(session, run_a)

    blocked = await _start(client_a, company, tid)
    allowed = await _start_awards(client_a, company, tid)

    _assert_reconciliation_409(blocked, current=b, represented={a})
    assert allowed.status_code == 200 and allowed.json()["created"] == 2
    runs = {c.run_id for c in (await session.scalars(sa.select(AwardCriterion).join(
        ReviewItemSource, ReviewItemSource.award_criterion_id == AwardCriterion.id))).all()}
    assert runs == {b}


# --------------------------------------------------------------------------- #
# Estat barrejat: es tanca sempre                                               #
# --------------------------------------------------------------------------- #


async def _legacy_item(session, company, tid, *, requirement_id=None, award_criterion_id=None):
    """SINTETIC: un element enllaçat a mà a una fila de B (estat que no hauria d'existir)."""
    kind = ReviewItemKind.REQUIREMENT if requirement_id else ReviewItemKind.AWARD_CRITERION
    item = ReviewItem(
        id=uuid.uuid4(), company_id=company.id, tender_id=tid, kind=kind,
        verification_status=ReviewVerification.UNREVIEWED, award_role=None,
        accepted_json={"title": "legacy"}, origin_hint=ReviewOriginHint.EXTRACTION, revision=1)
    session.add(item)
    await session.flush()
    session.add(ReviewItemSource(
        item_id=item.id, company_id=company.id, requirement_id=requirement_id,
        award_criterion_id=award_criterion_id, link_role=ReviewSourceRole.PRIMARY))
    await session.commit()


async def test_estat_barrejat_de_requisits_es_refusa(session, run_a, tenant_a, client_a):
    _, company = tenant_a
    tid, a = run_a["tender_id"], run_a["run_id"]
    assert (await _start(client_a, company, tid)).status_code == 200
    b = await _rerun(session, run_a)
    row_b = await session.scalar(sa.select(Requirement).where(Requirement.run_id == b).limit(1))
    await _legacy_item(session, company, tid, requirement_id=row_b.id)
    before = await _state(session)

    response = await _start(client_a, company, tid)

    _assert_reconciliation_409(response, current=b, represented={a, b})
    assert await _state(session) == before


async def test_estat_barrejat_de_criteris_es_refusa(session, run_a, tenant_a, client_a):
    _, company = tenant_a
    tid, a = run_a["tender_id"], run_a["run_id"]
    assert (await _start_awards(client_a, company, tid)).status_code == 200
    b = await _rerun(session, run_a)
    row_b = await session.scalar(
        sa.select(AwardCriterion).where(AwardCriterion.run_id == b).limit(1))
    await _legacy_item(session, company, tid, award_criterion_id=row_b.id)
    before = await _state(session)

    response = await _start_awards(client_a, company, tid)

    _assert_reconciliation_409(response, current=b, represented={a, b})
    assert await _state(session) == before


# --------------------------------------------------------------------------- #
# Manuals, tenancy, sense run vigent                                            #
# --------------------------------------------------------------------------- #


async def test_una_empresa_nomes_amb_manuals_pot_comencar(session, run_a, tenant_a, client_a):
    user, company = tenant_a
    tid = run_a["tender_id"]
    manual = await review_actions.create_manual_requirement(
        session, company_id=company.id, tender_id=tid, user=user,
        data=ManualRequirementCreate(title="Requisit manual", category="other"))
    await session.commit()
    manual_id, manual_json = manual.id, dict(manual.accepted_json)
    b = await _rerun(session, run_a)

    response = await _start(client_a, company, tid)

    assert response.status_code == 200 and response.json()["created"] == 4
    kept = await session.get(ReviewItem, manual_id, populate_existing=True)
    assert (kept.revision, kept.accepted_json, kept.archived_at) == (1, manual_json, None)
    assert await review_runs.represented_requirement_run_ids(session, company.id, tid) == {b}


async def test_la_guarda_es_per_empresa(session, run_a, tenant_a, tenant_b, client_a, client_b):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    tid, a = run_a["tender_id"], run_a["run_id"]
    assert (await _start(client_a, company_a, tid)).status_code == 200
    assert (await _start_awards(client_a, company_a, tid)).status_code == 200
    b = await _rerun(session, run_a)
    state_a = [(i.id, i.revision, i.accepted_json) for i in
               await _items(session, company_a, ReviewItemKind.REQUIREMENT)]

    first_b = await _start(client_b, company_b, tid)
    again_b = await _start(client_b, company_b, tid)
    awards_b = await _start_awards(client_b, company_b, tid)
    blocked_a = await _start(client_a, company_a, tid)
    blocked_awards_a = await _start_awards(client_a, company_a, tid)

    assert first_b.status_code == 200 and first_b.json()["created"] == 4
    assert again_b.status_code == 200 and again_b.json()["created"] == 0
    assert awards_b.status_code == 200 and awards_b.json()["created"] == 2
    _assert_reconciliation_409(blocked_a, current=b, represented={a})
    _assert_reconciliation_409(blocked_awards_a, current=b, represented={a})
    assert [(i.id, i.revision, i.accepted_json) for i in
            await _items(session, company_a, ReviewItemKind.REQUIREMENT)] == state_a
    assert await review_runs.represented_requirement_run_ids(session, company_b.id, tid) == {b}
    assert await review_runs.represented_award_run_ids(session, company_b.id, tid) == {b}
    overview_b = (await client_b.get(_url(company_b, tid, "/overview"))).json()
    assert overview_b["reconciliation_required"] is False


async def test_sense_run_vigent_no_canvia_res(session, analyzable, tenant_a, client_a):
    _, company = tenant_a
    tid = analyzable["document"].tender_id

    response = await _start(client_a, company, tid)
    awards = await _start_awards(client_a, company, tid)

    assert response.status_code == 200 and response.json()["created"] == 0
    assert awards.status_code == 200 and awards.json()["created"] == 0
    overview = (await client_a.get(_url(company, tid, "/overview"))).json()
    assert (overview["reconciliation_required"], overview["current_run_id"]) == (False, None)


async def test_un_element_arxivat_no_representa_cap_run(session, run_a, tenant_a, client_a):
    _, company = tenant_a
    tid = run_a["tender_id"]
    assert (await _start(client_a, company, tid)).status_code == 200
    for item in await _items(session, company, ReviewItemKind.REQUIREMENT):
        item.archived_at = sa.func.now()
    await session.commit()

    assert await review_runs.represented_requirement_run_ids(session, company.id, tid) == set()


def test_regla_pura_de_reconciliacio():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert review_runs.needs_reconciliation(set(), a) is False
    assert review_runs.needs_reconciliation({a}, a) is False
    assert review_runs.needs_reconciliation({a}, b) is True
    assert review_runs.needs_reconciliation({a, b}, b) is True
    assert review_runs.needs_reconciliation({a, b}, None) is True
    assert review_runs.needs_reconciliation({a}, None) is False
