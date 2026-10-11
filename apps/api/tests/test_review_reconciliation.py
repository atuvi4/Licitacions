"""Previsualitzacio de reconciliacio (S-R8): nomes lectura, cap escriptura.

Revisio sobre la run A + run B vigent -> que CALDRIA fer. Tot SINTETIC, amb el
proveidor fals i zero crides externes. Les files extretes les produeix la
canonada v3 real; nomes quan cal un valor que el contracte v3 no pot expressar
(`ms` no es una unitat de claim) es retoca la fila de B a la base de test.
"""

from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa

from app.ai.contract_v3 import VALUE_SCHEMA_V3
from app.ai.providers import fake
from app.models.enums import AwardCriterionType, RequirementOperator, ReviewItemKind
from app.models.provenance import TenderSourceRef
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.review import ReviewItem, ReviewItemSource
from app.schemas.review import AcceptedValue, ManualRequirementCreate
from app.services import review_actions
from app.services import review_reconciliation as rec
from app.services.requirement_analysis import analyze_tender_requirements
from app.services.review_awards import award_snapshot_of
from tests import test_requirement_analysis as base
from tests.real_provider import RealNamedFake
from tests.test_requirement_analysis import PAGE_27, PAGE_28, QUOTE_27, tender_of
from tests.test_requirement_analysis_v3 import V3, _evidence, _requirement, _run_v3_real
from tests.test_review_rerun_guard import _act, _items, _legacy_item, _state, _url

analyzable = base.analyzable
_clean_provider = base._clean_provider

VOLUM = "Volum anual de negoci minim"
LATENCY = "Latència màxima"
GUARANTEE = "Garantia dels equips"
TERM = "Termini d'implantació"
LATENCY_LINE = "Latència màx. entre dos punts en serveis de dades <70 ms Greu"
GUARANTEE_LINE = "Els equips hauran de tenir garantia del fabricant"
TERM_LINE = "Termini d'implantació del servei de 30 dies naturals"
PRICE_LINE = "Oferta econòmica: fins a 80 punts segons la fórmula"
QUALITY_LINE = "Criteris de qualitat: fins a 20 punts en total"
MEMORY_LINE = "Memòria tècnica del servei: fins a 20 punts"


def _volum(x):
    return _requirement(x)


def _latency(x, *, page=27, title=LATENCY):
    return _requirement(x, category="technical_requirement", operator=None, title=title,
                        claims=[], claims_complete="partial",
                        evidence=[_evidence(x, eid="e2", page=page, quote=LATENCY_LINE)])


def _guarantee(x):
    return _requirement(x, category="technical_requirement", operator=None, title=GUARANTEE,
                        claims=[], claims_complete="partial",
                        evidence=[_evidence(x, eid="e3", page=28, quote=GUARANTEE_LINE)])


def _term(x):
    return _requirement(x, category="deadline", operator=None, title=TERM,
                        claims=[], claims_complete="partial",
                        evidence=[_evidence(x, eid="e4", page=28, quote=TERM_LINE)])


@pytest.fixture
async def tender(session, analyzable):
    """SINTETIC: dues pagines amb les files de latencia, garantia i termini."""
    first, second = analyzable["pages"]
    first.text = PAGE_27 + LATENCY_LINE + "\n" + PRICE_LINE + "\n" + QUALITY_LINE + "\n"
    second.text = (PAGE_28 + LATENCY_LINE + "\n" + GUARANTEE_LINE + "\n" + TERM_LINE + "\n"
                   + MEMORY_LINE + "\n")
    await session.commit()
    return {"analyzable": analyzable, "x": analyzable["extraction"].id,
            "tender_id": analyzable["document"].tender_id}


async def _run(session, tender, requirements, *, first=False):
    payload = {"requirements": requirements, "award_candidates": []}
    if first:
        return (await _run_v3_real(session, tender["analyzable"], payload)).id
    fake.queue_response(payload)
    doc = tender["analyzable"]["document"]
    # Una run canonica NOVA sobre el mateix corpus: un altre model real (identitat
    # d'analisi diferent, cap reaprofitament). No `force`: en una licitacio publica
    # un run forcat mai es vigent (3B-2 §6.4). La reconciliacio no depen del model.
    # Un model per run, pinat a `tests/conftest.py` (`PINNED_TEST_MODELS`).
    tender["reruns"] = tender.get("reruns", 0) + 1
    run = (await analyze_tender_requirements(
        session, await tender_of(session, doc), profile=V3,
        provider=RealNamedFake(model=f"claude-test-rerun-{tender['reruns']}"))).run
    assert run.is_current
    return run.id


async def _preview(client, company, tender_id):
    response = await client.get(_url(company, tender_id, "/reconciliation"))
    assert response.status_code == 200, response.text
    return response.json()


async def _start(client, company, tender_id, suffix="/start"):
    response = await client.post(_url(company, tender_id, suffix))
    assert response.status_code == 200, response.text
    return response.json()


async def _row(session, run_id, title):
    return await session.scalar(sa.select(Requirement).where(
        Requirement.run_id == run_id, Requirement.title == title))


def _by_class(section, name):
    return [m for m in section["matches"] if m["match_class"] == name]


async def _full_state(session):
    """L'estat de revisio + extraccio + cites, per demostrar zero escriptures."""
    state = await _state(session)
    for model in (TenderSourceRef, Requirement, RequirementEvidence, AwardCriterion,
                  AwardCriterionEvidence, RequirementExtractionRun):
        state[model.__tablename__] = await session.scalar(
            sa.select(sa.func.count()).select_from(model))
    return state


# --------------------------------------------------------------------------- #
# Estats sense aparellament                                                     #
# --------------------------------------------------------------------------- #


async def test_sense_run_vigent_ho_diu_explicitament(session, analyzable, tenant_a, client_a):
    _, company = tenant_a
    body = await _preview(client_a, company, analyzable["document"].tender_id)

    assert (body["state"], body["current_run_id"], body["plan_hash"]) == (
        "no_current_run", None, None)
    assert body["requirements"]["state"] == body["awards"]["state"] == "no_current_run"


async def test_no_iniciada_no_materialitza_res(session, tender, tenant_a, client_a):
    user, company = tenant_a
    await _run(session, tender, [_volum(tender["x"]), _guarantee(tender["x"])], first=True)
    await review_actions.create_manual_requirement(
        session, company_id=company.id, tender_id=tender["tender_id"], user=user,
        data=ManualRequirementCreate(title="Requisit manual", category="other"))
    await session.commit()
    before = await _full_state(session)

    body = await _preview(client_a, company, tender["tender_id"])

    assert (body["state"], body["plan_hash"]) == ("not_started", None)
    section = body["requirements"]
    assert (section["state"], section["canonical_item_count"],
            section["current_source_count"]) == ("not_started", 0, 2)
    assert body["awards"]["state"] == "not_started"
    assert await _full_state(session) == before


async def test_al_dia_sense_pla(session, tender, tenant_a, client_a):
    _, company = tenant_a
    run_a = await _run(session, tender, [_volum(tender["x"])], first=True)
    await _start(client_a, company, tender["tender_id"])

    body = await _preview(client_a, company, tender["tender_id"])

    assert (body["state"], body["plan_hash"], body["current_run_id"]) == (
        "up_to_date", None, str(run_a))
    assert body["requirements"]["represented_run_ids"] == [str(run_a)]
    assert body["requirements"]["counts"] == {
        "exact_auto": 0, "review_candidate": 0, "ambiguous": 0, "new": 0, "removed": 0}
    assert body["awards"]["state"] == "not_started"


# --------------------------------------------------------------------------- #
# Classes                                                                       #
# --------------------------------------------------------------------------- #


async def test_copia_de_represa_es_exact_auto_u_a_u(session, tender, tenant_a, client_a):
    _, company = tenant_a
    x = tender["x"]
    rows = [_volum(x), _latency(x), _guarantee(x)]
    run_a = await _run(session, tender, rows, first=True)
    await _start(client_a, company, tender["tender_id"])
    run_b = await _run(session, tender, rows)

    body = await _preview(client_a, company, tender["tender_id"])

    assert body["state"] == "new_extraction_available"
    section = body["requirements"]
    assert section["state"] == "new_extraction_available"
    assert section["represented_run_ids"] == [str(run_a)]
    assert section["counts"] == {
        "exact_auto": 3, "review_candidate": 0, "ambiguous": 0, "new": 0, "removed": 0}
    assert body["awards"]["state"] == "not_started"  # seccions independents
    old_ids = {m["old_source"]["source_id"] for m in section["matches"]}
    new_ids = {m["new_source"]["source_id"] for m in section["matches"]}
    assert len(old_ids) == len(new_ids) == 3 and not old_ids & new_ids
    for match in section["matches"]:
        assert match["match_label"] == "Coincidència exacta"
        assert match["new_source"]["run_id"] == str(run_b)
        assert not any(match["source_diff"].values())
        assert not any(match["accepted_diff"].values())
        assert match["requires_reverification"] is False
    assert len(body["plan_hash"]) == 64


async def test_un_altre_contracte_d_extraccio_no_es_mai_exact_auto(
    session, tender, tenant_a, client_a
):
    """Files identiques pero d'un altre prompt: com a molt candidates, mai automatiques."""
    _, company = tenant_a
    x = tender["x"]
    rows = [_volum(x), _guarantee(x)]
    await _run(session, tender, rows, first=True)
    await _start(client_a, company, tender["tender_id"])
    run_b = await session.get(RequirementExtractionRun, await _run(session, tender, rows))
    run_b.prompt_version = "requirements-altre"  # SINTETIC
    await session.commit()

    section = (await _preview(client_a, company, tender["tender_id"]))["requirements"]

    assert (section["counts"]["exact_auto"], section["counts"]["review_candidate"]) == (0, 2)


async def test_pagina_moguda_es_candidata_no_exacta(session, tender, tenant_a, client_a):
    _, company = tenant_a
    x = tender["x"]
    await _run(session, tender, [_volum(x), _latency(x, page=27)], first=True)
    await _start(client_a, company, tender["tender_id"])
    await _run(session, tender, [_volum(x), _latency(x, page=28)])

    section = (await _preview(client_a, company, tender["tender_id"]))["requirements"]

    assert (section["counts"]["exact_auto"], section["counts"]["review_candidate"]) == (1, 1)
    [candidate] = _by_class(section, "review_candidate")
    assert candidate["item"]["accepted_title"] == LATENCY
    assert [loc["page"] for loc in candidate["old_source"]["locations"]] == [27]
    assert [loc["page"] for loc in candidate["new_source"]["locations"]] == [28]
    assert candidate["source_diff"]["evidence_changed"] is True
    assert candidate["source_diff"]["values_changed"] is False
    assert candidate["requires_reverification"] is False


async def test_una_font_vella_i_dues_noves_es_ambigu(session, tender, tenant_a, client_a):
    _, company = tenant_a
    x = tender["x"]
    await _run(session, tender, [_volum(x), _latency(x, page=27)], first=True)
    await _start(client_a, company, tender["tender_id"])
    run_b = await _run(session, tender, [
        _volum(x), _latency(x, page=28), _latency(x, page=27, title="Latència entre punts")])

    section = (await _preview(client_a, company, tender["tender_id"]))["requirements"]

    assert section["counts"] == {
        "exact_auto": 1, "review_candidate": 0, "ambiguous": 1, "new": 0, "removed": 0}
    [ambiguous] = section["ambiguous"]
    assert ambiguous["reason"] == "multiple_candidates"
    expected = {str((await _row(session, run_b, LATENCY)).id),
                str((await _row(session, run_b, "Latència entre punts")).id)}
    assert {s["source_id"] for s in ambiguous["candidate_sources"]} == expected
    assert [i["accepted_title"] for i in ambiguous["items"]] == [LATENCY]


async def test_nou_i_retirat_sense_comptar_els_manuals(session, tender, tenant_a, client_a):
    user, company = tenant_a
    x = tender["x"]
    await _run(session, tender, [_volum(x), _guarantee(x)], first=True)
    await _start(client_a, company, tender["tender_id"])
    await review_actions.create_manual_requirement(
        session, company_id=company.id, tender_id=tender["tender_id"], user=user,
        data=ManualRequirementCreate(title="Requisit manual", category="other"))
    await session.commit()
    run_b = await _run(session, tender, [_volum(x), _term(x)])

    section = (await _preview(client_a, company, tender["tender_id"]))["requirements"]

    assert section["counts"] == {
        "exact_auto": 1, "review_candidate": 0, "ambiguous": 0, "new": 1, "removed": 1}
    assert section["canonical_item_count"] == 2  # el manual no hi es
    [removed] = section["removed"]
    assert (removed["item"]["accepted_title"], removed["item"]["verification_status"],
            removed["item"]["provenance"]) == (GUARANTEE, "unreviewed", "automated")
    assert removed["old_source"]["title"] == GUARANTEE
    [new] = section["new"]
    source = new["source"]
    assert (source["source_id"], source["title"], source["category"],
            source["category_label"]) == (
        str((await _row(session, run_b, TERM)).id), TERM, "deadline", "Termini")
    assert source["obligation_label"] == "Obligatori"
    assert [loc["page"] for loc in source["locations"]] == [28]
    titles = json.dumps(section)
    assert "Requisit manual" not in titles


# --------------------------------------------------------------------------- #
# Diferencies i reverificacio                                                   #
# --------------------------------------------------------------------------- #


async def _corrected_then_rerun(session, tender, client_a, company, value):
    """A: latencia sense valor; l'empresa la corregeix a < 70 ms; B l'extreu amb `value`."""
    x = tender["x"]
    await _run(session, tender, [_volum(x), _latency(x)], first=True)
    await _start(client_a, company, tender["tender_id"])
    item = next(i for i in await _items(session, company, ReviewItemKind.REQUIREMENT)
                if i.accepted_json["title"] == LATENCY)
    await _act(client_a, company, tender["tender_id"], item.id, "correct", 1, {
        "values": [{"key": "latencia", "operator": "lt", "value": 70, "unit_label": "ms"}]})
    run_b = await _run(session, tender, [_volum(x), _latency(x)])
    # SINTETIC: el contracte v3 no te `ms`; es simula una extraccio B que el porta.
    row = await _row(session, run_b, LATENCY)
    row.operator = RequirementOperator.LT
    row.value_schema = VALUE_SCHEMA_V3
    row.value_json = {"claims": [{"id": "latencia", "kind": "quantity", "value": value,
                                  "unit": "ms"}]}
    await session.commit()
    section = (await _preview(client_a, company, tender["tender_id"]))["requirements"]
    return next(m for m in section["matches"] if m["item"]["accepted_title"] == LATENCY)


async def test_la_correccio_humana_coincideix_amb_la_nova_extraccio(
    session, tender, tenant_a, client_a
):
    _, company = tenant_a
    match = await _corrected_then_rerun(session, tender, client_a, company, 70)

    assert match["match_class"] == "review_candidate"
    assert (match["item"]["has_human_correction"], match["item"]["provenance"]) == (
        True, "automated_corrected")
    assert match["source_diff"]["values_changed"] is True  # A no en tenia cap
    assert match["accepted_diff"]["values_changed"] is False
    assert match["requires_reverification"] is False
    assert match["new_source"]["values"] == [
        {"key": "latencia", "operator": "lt", "value": 70, "unit_label": "ms"}]


async def test_la_correccio_humana_contradiu_la_nova_extraccio(
    session, tender, tenant_a, client_a
):
    _, company = tenant_a
    match = await _corrected_then_rerun(session, tender, client_a, company, 60)

    assert match["accepted_diff"]["values_changed"] is True
    assert match["requires_reverification"] is True
    item = next(i for i in await _items(session, company, ReviewItemKind.REQUIREMENT)
                if i.accepted_json["title"] == LATENCY)
    assert [(v["value"], v["source"]) for v in item.accepted_json["values"]] == [
        (70, "reviewer")]  # ni l'instantania ni l'estat es toquen


async def test_una_correccio_sobre_una_font_identica_no_demana_reverificar(
    session, tender, tenant_a, client_a
):
    """Represa: la font no canvia; la persona ja va decidir contra aquesta mateixa font."""
    _, company = tenant_a
    x = tender["x"]
    rows = [_volum(x), _latency(x)]
    await _run(session, tender, rows, first=True)
    await _start(client_a, company, tender["tender_id"])
    item = next(i for i in await _items(session, company, ReviewItemKind.REQUIREMENT)
                if i.accepted_json["title"] == LATENCY)
    await _act(client_a, company, tender["tender_id"], item.id, "correct", 1, {
        "values": [{"key": "latencia", "operator": "lt", "value": 70, "unit_label": "ms"}]})
    await _run(session, tender, rows)

    section = (await _preview(client_a, company, tender["tender_id"]))["requirements"]

    match = next(m for m in section["matches"] if m["item"]["accepted_title"] == LATENCY)
    assert match["match_class"] == "exact_auto"
    assert match["item"]["has_human_correction"] is True
    assert match["accepted_diff"]["values_changed"] is True  # la correccio es veu
    assert not any(match["source_diff"].values())
    assert match["requires_reverification"] is False


def test_regla_de_reverificacio():
    assert rec.requires_reverification(True, True) is True
    assert rec.requires_reverification(True, False) is False
    assert rec.requires_reverification(False, True) is False
    assert rec.requires_reverification(False, False) is False


# --------------------------------------------------------------------------- #
# Hash del pla                                                                  #
# --------------------------------------------------------------------------- #


async def test_hash_estable_i_desfasat_per_una_revisio(session, tender, tenant_a, client_a):
    _, company = tenant_a
    x = tender["x"]
    await _run(session, tender, [_volum(x), _guarantee(x)], first=True)
    await _start(client_a, company, tender["tender_id"])
    await _run(session, tender, [_volum(x), _term(x)])

    first = (await _preview(client_a, company, tender["tender_id"]))["plan_hash"]
    second = (await _preview(client_a, company, tender["tender_id"]))["plan_hash"]
    item = (await _items(session, company, ReviewItemKind.REQUIREMENT))[0]
    await _act(client_a, company, tender["tender_id"], item.id, "note", item.revision,
               {"note": "Revisat"})
    third = (await _preview(client_a, company, tender["tender_id"]))["plan_hash"]

    assert first == second
    assert third != first


# --------------------------------------------------------------------------- #
# Criteris d'adjudicacio                                                        #
# --------------------------------------------------------------------------- #

AWARDS = (
    ("Preu", 80.0, AwardCriterionType.PRICE, PRICE_LINE, 27),
    ("Qualitat", 20.0, AwardCriterionType.QUALITY, QUALITY_LINE, 27),
    ("Memòria tècnica", 20.0, AwardCriterionType.QUALITY, MEMORY_LINE, 28),
)


async def _awards(session, tender, run_id, *, keys=None, price=80.0):
    """Criteris amb cites. Amb `keys`, copia les claus d'A (com una represa)."""
    pages = {p.page_number: p for p in tender["analyzable"]["pages"]}
    doc = tender["analyzable"]["document"]
    created = {}
    for index, (name, points, kind, quote, page) in enumerate(AWARDS):
        criterion = AwardCriterion(
            run_id=run_id, tender_id=tender["tender_id"], name=name,
            points=price if name == "Preu" else points, criterion_type=kind,
            dedup_key=keys[name] if keys else uuid.uuid4().hex, source_chunk_index=index,
            adjudication_basis_json=None)
        session.add(criterion)
        await session.flush()
        start = pages[page].text.index(quote)
        session.add(AwardCriterionEvidence(
            award_criterion_id=criterion.id, extraction_id=tender["x"],
            page_id=pages[page].id, tender_document_id=doc.id, page_number=page,
            quote=quote, char_start=start, char_end=start + len(quote)))
        created[name] = criterion.dedup_key
    await session.commit()
    return created


async def _award_scenario(session, tender, client_a, company, *, price_b):
    x = tender["x"]
    run_a = await _run(session, tender, [_volum(x)], first=True)
    keys = await _awards(session, tender, run_a)
    await _start(client_a, company, tender["tender_id"], "/award-criteria/start")
    roles = {"Preu": "scored", "Qualitat": "parent_total", "Memòria tècnica": "scored"}
    for item in await _items(session, company, ReviewItemKind.AWARD_CRITERION):
        await _act(client_a, company, tender["tender_id"], item.id, "set_award_role", 1,
                   {"role": roles[item.accepted_json["name"]]})
    run_b = await _run(session, tender, [_volum(x)])
    await _awards(session, tender, run_b, keys=keys, price=price_b)
    return run_b


async def test_criteris_copiats_son_exactes_i_no_toquen_els_papers(
    session, tender, tenant_a, client_a
):
    _, company = tenant_a
    await _award_scenario(session, tender, client_a, company, price_b=80.0)
    before = await _full_state(session)

    body = await _preview(client_a, company, tender["tender_id"])

    awards = body["awards"]
    assert awards["state"] == "new_extraction_available"
    assert awards["counts"] == {
        "exact_auto": 3, "review_candidate": 0, "ambiguous": 0, "new": 0, "removed": 0}
    assert {m["item"]["accepted_title"]: m["item"]["award_role"] for m in awards["matches"]} \
        == {"Preu": "scored", "Qualitat": "parent_total", "Memòria tècnica": "scored"}
    assert awards["scoring"] == {"scored_count": 2, "total_points": 100.0,
                                 "scoring_reconciliation_pending": True}
    assert await _full_state(session) == before  # cap element nou, cap paper canviat


async def test_punts_canviats_no_son_exactes(session, tender, tenant_a, client_a):
    _, company = tenant_a
    await _award_scenario(session, tender, client_a, company, price_b=70.0)

    awards = (await _preview(client_a, company, tender["tender_id"]))["awards"]

    assert (awards["counts"]["exact_auto"], awards["counts"]["review_candidate"]) == (2, 1)
    [price] = _by_class(awards, "review_candidate")
    assert (price["old_source"]["points"], price["new_source"]["points"]) == (80.0, 70.0)
    assert price["source_diff"]["points_changed"] is True
    assert price["accepted_diff"]["points_changed"] is True
    assert price["requires_reverification"] is True
    assert price["item"]["award_role"] == "scored"


# --------------------------------------------------------------------------- #
# Estat barrejat, tenancy, zero escriptures, guarda                             #
# --------------------------------------------------------------------------- #


async def test_estat_barrejat_es_tanca(session, tender, tenant_a, client_a):
    _, company = tenant_a
    x = tender["x"]
    run_a = await _run(session, tender, [_volum(x)], first=True)
    await _awards(session, tender, run_a)
    await _start(client_a, company, tender["tender_id"])
    await _start(client_a, company, tender["tender_id"], "/award-criteria/start")
    run_b = await _run(session, tender, [_volum(x), _term(x)])
    await _awards(session, tender, run_b)
    await _legacy_item(session, company, tender["tender_id"],
                       requirement_id=(await _row(session, run_b, TERM)).id)
    criterion_b = await session.scalar(
        sa.select(AwardCriterion).where(AwardCriterion.run_id == run_b).limit(1))
    await _legacy_item(session, company, tender["tender_id"], award_criterion_id=criterion_b.id)
    legacy = await session.scalar(
        sa.select(ReviewItem).join(ReviewItemSource, ReviewItemSource.item_id == ReviewItem.id)
        .where(ReviewItemSource.award_criterion_id == criterion_b.id))
    legacy.accepted_json = award_snapshot_of(criterion_b).model_dump(mode="json")
    await session.commit()

    body = await _preview(client_a, company, tender["tender_id"])

    assert (body["state"], body["plan_hash"]) == ("mixed_source_runs", None)
    for section in (body["requirements"], body["awards"]):
        assert section["state"] == "mixed_source_runs"
        assert section["represented_run_ids"] == sorted([str(run_a), str(run_b)])
        assert not section["matches"] and not section["new"]
    assert body["awards"]["scoring"]["scoring_reconciliation_pending"] is True


async def test_cada_empresa_veu_nomes_la_seva_previsualitzacio(
    session, tender, tenant_a, tenant_b, client_a, client_b
):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    x = tender["x"]
    await _run(session, tender, [_volum(x), _guarantee(x)], first=True)
    await _start(client_a, company_a, tender["tender_id"])
    await _run(session, tender, [_volum(x), _term(x)])
    await _start(client_b, company_b, tender["tender_id"])

    body_a = await _preview(client_a, company_a, tender["tender_id"])
    body_b = await _preview(client_b, company_b, tender["tender_id"])

    assert body_a["state"] == "new_extraction_available"
    assert (body_b["state"], body_b["plan_hash"]) == ("up_to_date", None)
    ids_a = {str(i.id) for i in await _items(session, company_a, ReviewItemKind.REQUIREMENT)}
    text_b = json.dumps(body_b)
    assert not any(i in text_b for i in ids_a)
    assert (await client_b.get(_url(company_a, tender["tender_id"], "/reconciliation"))
            ).status_code in (403, 404)


async def test_gets_repetits_no_escriuen_i_start_continua_bloquejat(
    session, tender, tenant_a, client_a
):
    _, company = tenant_a
    x = tender["x"]
    run_a = await _run(session, tender, [_volum(x), _guarantee(x)], first=True)
    await _awards(session, tender, run_a)
    await _start(client_a, company, tender["tender_id"])
    await _start(client_a, company, tender["tender_id"], "/award-criteria/start")
    run_b = await _run(session, tender, [_volum(x), _term(x)])
    await _awards(session, tender, run_b)
    before = await _full_state(session)

    hashes = {(await _preview(client_a, company, tender["tender_id"]))["plan_hash"]
              for _ in range(3)}

    assert len(hashes) == 1
    assert await _full_state(session) == before
    for suffix in ("/start", "/award-criteria/start"):
        response = await client_a.post(_url(company, tender["tender_id"], suffix))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "review_reconciliation_required"
    assert await _full_state(session) == before


# --------------------------------------------------------------------------- #
# Classificador pur i normalitzacio                                             #
# --------------------------------------------------------------------------- #


def _src(key, anchors=(), family="f"):
    return rec.Source(id=uuid.uuid4(), exact_key=key, anchors=frozenset(anchors), family=family)


def test_signatura_exacta_repetida_bloqueja_exact_auto():
    one_old = rec.Old(uuid.uuid4(), _src("k"))
    two_new = [_src("k"), _src("k")]
    plan = rec.classify([one_old], two_new, exact_allowed=True)
    assert (len(plan.exact), [a.reason for a in plan.ambiguous]) == (
        0, ["duplicate_exact_signature"])
    assert {n.id for n in plan.ambiguous[0].candidates} == {n.id for n in two_new}

    two_old = [rec.Old(uuid.uuid4(), _src("k")), rec.Old(uuid.uuid4(), _src("k"))]
    plan = rec.classify(two_old, [_src("k")], exact_allowed=True)
    assert not plan.exact and len(plan.ambiguous[0].olds) == 2


def test_sense_contracte_compatible_no_hi_ha_exact_auto():
    old = rec.Old(uuid.uuid4(), _src("k", {"a"}))
    plan = rec.classify([old], [_src("k", {"a"})], exact_allowed=False)
    assert (len(plan.exact), len(plan.candidates)) == (0, 1)


def test_ancoratges_compartits_i_families():
    shared = [rec.Old(uuid.uuid4(), _src("o1", {"a"})), rec.Old(uuid.uuid4(), _src("o2", {"a"}))]
    plan = rec.classify(shared, [_src("n", {"a"})], exact_allowed=True)
    assert [a.reason for a in plan.ambiguous] == ["multiple_candidates"]

    plan = rec.classify([rec.Old(uuid.uuid4(), _src("o", {"a"}, "x"))],
                        [_src("n", {"a"}, "y")], exact_allowed=True)
    assert [a.reason for a in plan.ambiguous] == ["conflicting_family"]

    exact_old = rec.Old(uuid.uuid4(), _src("k", {"a"}))
    orphan = rec.Old(uuid.uuid4(), _src("o", {"a"}))
    plan = rec.classify([exact_old, orphan], [_src("k", {"a"})], exact_allowed=True)
    assert len(plan.exact) == 1
    assert [a.reason for a in plan.ambiguous] == ["candidate_already_matched"]

    plan = rec.classify([rec.Old(uuid.uuid4(), _src("o"))], [_src("n")], exact_allowed=True)
    assert (len(plan.removed), len(plan.new)) == (1, 1)


def test_normalitzacio_explicita():
    assert rec.normalise_text("  Latència\n  MÀX.\tentre ") == "latència màx. entre"
    assert rec.normalise_text("ﬁ") == "fi"  # NFKC
    short = type("E", (), {"quote": "Sí", "tender_document_id": uuid.uuid4(),
                           "extraction_id": uuid.uuid4()})
    assert rec.anchor_keys([short]) == set()
    human = [AcceptedValue(key="latencia", operator="<", value=70, unit_label="ms",
                           source="reviewer")]
    extracted = [AcceptedValue(key="lat_max", operator="lt", value=70.0, unit_label="MS",
                               source="document")]
    assert rec.values_signature(human) == rec.values_signature(extracted)
    other = [AcceptedValue(key="latencia", operator="lt", value=60, unit_label="ms",
                           source="document")]
    assert rec.values_signature(human) != rec.values_signature(other)
    assert QUOTE_27  # la cita per defecte del volum existeix a la pagina 27
