"""Abast i semantica del plec (0049) per l'API de tenant.

El que es protegeix:

* **lectures antigues intactes**: un requisit nou llegeix `any`, cap territori,
  `proof_json` nul, cap necessitat ni criteri; l'estructura llegeix llistes buides;
* **anada i tornada** d'elements d'abast, fets contractuals, territoris, proves,
  necessitats i enllaços requisit ↔ criteri, amb les regles del model;
* **els mateixos permisos** que l'estructura de 0048: dades de licitacio amb la
  politica de la licitacio oberta a ma; anotacions de requisit amb la dels requisits;
  una licitacio publica es llegeix i no s'edita per aqui;
* **cap confusio de nivells**: res d'aixo crea serveis, productes, capacitats
  d'empresa, cites de documents d'empresa, blocadors ni avaluacions;
* el **gold sintetic** del lock.

Dades sintetiques: cap expedient, organisme ni empresa real.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.db.base import Base
from app.knowledge import vocabulary
from app.models.credentials import CompanyCertification
from app.models.documents import DocumentLink
from app.models.knowledge import CompanyOperatingFact, SolutionCapability
from app.models.profile import CompanyService
from app.models.provenance import TenderSourceRef
from app.models.requirements import AwardCriterion
from app.models.tender_scope import (
    ContractFactBlock,
    RequirementAwardLink,
    RequirementCapabilityNeed,
    RequirementTerritory,
    TenderContractFact,
    TenderScopeItem,
    TenderScopeItemSourceRefLink,
)
from app.models.tender_structure import TenderBlock
from tests.test_tender_structure_api import (
    ANALYSIS,
    DETAIL,
    STRUCTURE,
    T,
    _block,
    _code,
    _count,
    _criterion,
    _file_ref,
    _requirement,
    _role,
    _run,
    _site,
    _tender,
)

pytestmark = pytest.mark.asyncio

SCOPE = T + "/scope-items"
FACTS = T + "/contract-facts"
SEMANTICS = T + "/requirements/{requirement}/semantics"
NEEDS = T + "/requirements/{requirement}/capability-needs"
NEED = T + "/capability-needs/{need}"

#: Taules que 0050 encara no ha creat: cap resposta d'aquest pas les pot necessitar.
NOT_YET = (
    "tender_analysis_runs",
    "analysis_runs",
    "analysis_findings",
    "findings",
    "assessments",
    "delivery_candidates",
    "proposal_lines",
)
#: Taules de l'EMPRESA que el costat plec no ha de tocar mai.
COMPANY_TABLES = (
    CompanyService,
    SolutionCapability,
    CompanyOperatingFact,
    CompanyCertification,
    DocumentLink,
)


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.commit()


async def _company_counts(db) -> dict[str, int]:
    return {model.__tablename__: await _count(db, model) for model in COMPANY_TABLES}


async def _scope_item(client, company, tender_id, **fields) -> dict:
    response = await client.post(SCOPE.format(company=company.id, tender=tender_id), json=fields)
    assert response.status_code == 201, response.text
    return response.json()


async def _fact(client, company, tender_id, code, value, schema="v1", block_ids=()) -> dict:
    response = await client.post(
        FACTS.format(company=company.id, tender=tender_id),
        json={
            "fact_code": code,
            "value_json": value,
            "value_schema": schema,
            "block_ids": list(block_ids),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _need(client, company, tender_id, requirement_id, **fields) -> dict:
    response = await client.post(
        NEEDS.format(company=company.id, tender=tender_id, requirement=requirement_id), json=fields
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Lectures antigues                                                            #
# --------------------------------------------------------------------------- #


async def test_legacy_reads_are_neutral(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    requirement = await _requirement(client_a, company, tender_id)
    run = await _run(db, tender_id)
    criterion = await _criterion(db, run, "C", 5.0)

    assert (requirement["territory_match_mode"], requirement["territories"]) == ("any", [])
    assert (requirement["proof_json"], requirement["proof_schema"]) == (None, None)
    assert (requirement["capability_needs"], requirement["award_criterion_ids"]) == ([], [])
    structure = (await client_a.get(STRUCTURE.format(company=company.id, tender=tender_id))).json()
    assert (structure["scope_items"], structure["contract_facts"]) == ([], [])
    analysis = (await client_a.get(ANALYSIS.format(tender=tender_id))).json()
    read = next(c for c in analysis["award_criteria"] if c["id"] == str(criterion.id))
    assert read["requirement_ids"] == []
    semantics = (
        await client_a.get(
            SEMANTICS.format(company=company.id, tender=tender_id, requirement=requirement["id"])
        )
    ).json()
    assert semantics == {
        "id": requirement["id"],
        "tender_id": tender_id,
        "territory_match_mode": "any",
        "territory_completeness": "unknown",
        "territories": [],
        "proof_json": None,
        "proof_schema": None,
        "award_criterion_ids": [],
        "capability_needs": [],
    }
    # La fitxa de 0048 continua sencera.
    detail = (await client_a.get(DETAIL.format(tender=tender_id))).json()
    assert "blocks" in detail and "lots" in detail


# --------------------------------------------------------------------------- #
# Elements d'abast                                                             #
# --------------------------------------------------------------------------- #


async def test_scope_items_round_trip_with_the_rules_of_the_model(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    other_id = await _tender(client_a, company, title="Un altre")
    b1 = await _block(client_a, company, tender_id, "B1")
    foreign = await _block(client_a, company, other_id, "B1")
    s1 = await _site(client_a, company, tender_id, "S1", role="central")
    url = SCOPE.format(company=company.id, tender=tender_id)

    ftth = await _scope_item(
        client_a,
        company,
        tender_id,
        service_line_code="access_ftth",
        block_id=b1["id"],
        quantity="15",
        unit="accessos",
        unit_base_price_ex_vat="320.00",
    )
    assert (Decimal(ftth["quantity"]), ftth["unit"], ftth["block_id"], ftth["contract_wide"]) == (
        Decimal("15"),
        "accessos",
        b1["id"],
        False,
    )
    assert Decimal(ftth["unit_base_price_ex_vat"]) == Decimal("320.00") and ftth["citations"] == []
    fibre = await _scope_item(
        client_a,
        company,
        tender_id,
        service_line_code="fiber_deployment",
        quantity="4206.5",
        unit="m",
        site_id=s1["id"],
        specs_json={"fibres": 24},
        specs_schema="scope_specs_v1",
    )
    assert (fibre["contract_wide"], fibre["site_id"], fibre["specs_json"]) == (
        True,
        s1["id"],
        {"fibres": 24},
    )

    for payload, status_code, code in (
        ({"service_line_code": "teleport"}, 422, None),
        ({"service_line_code": "voice_pbx", "quantity": "-1"}, 422, None),
        ({"service_line_code": "voice_pbx", "specs_json": {"a": 1}}, 422, None),
        (
            {"service_line_code": "voice_pbx", "block_id": foreign["id"]},
            422,
            "block_tender_mismatch",
        ),
        (
            {
                "service_line_code": "voice_pbx",
                "block_id": b1["id"],
                "tender_lot_id": str(uuid.uuid4()),
            },
            422,
            None,
        ),
    ):
        response = await client_a.post(url, json=payload)
        assert response.status_code == status_code, (payload, response.text)
        if code:
            assert _code(response) == code

    edited = await client_a.patch(f"{url}/{ftth['id']}", json={"quantity": "16", "block_id": None})
    assert (
        edited.status_code,
        Decimal(edited.json()["quantity"]),
        edited.json()["contract_wide"],
    ) == (
        200,
        Decimal("16"),
        True,
    )
    structure = (await client_a.get(STRUCTURE.format(company=company.id, tender=tender_id))).json()
    assert sorted(i["service_line_code"] for i in structure["scope_items"]) == [
        "access_ftth",
        "fiber_deployment",
    ]
    assert (await client_a.delete(f"{url}/{fibre['id']}")).status_code == 204
    assert await _count(db, TenderScopeItem) == 1


async def test_scope_items_cite_sources_like_everything_else(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    other_id = await _tender(client_a, company, title="Un altre")
    item = await _scope_item(
        client_a,
        company,
        tender_id,
        service_line_code="voice_pbx",
        quantity="123",
        unit="extensions",
    )
    ref = await _file_ref(client_a, company, tender_id, label="Annex tecnic, taula 3")
    foreign_ref = await _file_ref(client_a, company, other_id)
    url = f"{SCOPE.format(company=company.id, tender=tender_id)}/{item['id']}/citations"

    linked = await client_a.post(url, json={"source_ref_id": ref})
    assert (linked.status_code, [c["id"] for c in linked.json()["citations"]]) == (200, [ref])
    assert len((await client_a.post(url, json={"source_ref_id": ref})).json()["citations"]) == 1
    wrong = await client_a.post(url, json={"source_ref_id": foreign_ref})
    assert (wrong.status_code, _code(wrong)) == (422, "source_ref_tender_mismatch")
    assert (await client_a.delete(f"{url}/{ref}")).json()["citations"] == []
    assert (await client_a.post(url, json={"source_ref_id": ref})).status_code == 200
    assert (
        await client_a.delete(f"{SCOPE.format(company=company.id, tender=tender_id)}/{item['id']}")
    ).status_code == 204
    assert await _count(db, TenderScopeItemSourceRefLink) == 0
    assert await db.get(TenderSourceRef, uuid.UUID(ref)) is not None


# --------------------------------------------------------------------------- #
# Fets contractuals                                                            #
# --------------------------------------------------------------------------- #


async def test_contract_facts_round_trip_and_billing_terms_is_contract_wide(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    other_id = await _tender(client_a, company, title="Un altre")
    b1 = await _block(client_a, company, tender_id, "B1")
    b3 = await _block(client_a, company, tender_id, "B3")
    foreign = await _block(client_a, company, other_id, "B1")
    url = FACTS.format(company=company.id, tender=tender_id)

    insurance = await _fact(
        client_a,
        company,
        tender_id,
        "liability_insurance",
        {"amount_eur": 300000},
        block_ids=[b1["id"], b3["id"]],
    )
    assert (set(insurance["block_ids"]), insurance["contract_wide"]) == (
        {b1["id"], b3["id"]},
        False,
    )
    billing = await _fact(client_a, company, tender_id, "billing_terms", {"period": "monthly"})
    assert (billing["block_ids"], billing["contract_wide"]) == ([], True)

    refused = await client_a.post(
        url,
        json={
            "fact_code": "billing_terms",
            "value_json": {"period": "q"},
            "value_schema": "v1",
            "block_ids": [b1["id"]],
        },
    )
    assert (refused.status_code, _code(refused)) == (422, "billing_terms_contract_wide")
    refused = await client_a.patch(f"{url}/{billing['id']}", json={"block_ids": [b1["id"]]})
    assert (refused.status_code, _code(refused)) == (422, "billing_terms_contract_wide")
    assert (
        await client_a.post(
            url, json={"fact_code": "moon_phase", "value_json": {}, "value_schema": "v1"}
        )
    ).status_code == 422
    assert (
        await client_a.post(url, json={"fact_code": "penalties", "value_json": {}})
    ).status_code == 422
    wrong = await client_a.patch(f"{url}/{insurance['id']}", json={"block_ids": [foreign["id"]]})
    assert (wrong.status_code, _code(wrong)) == (422, "block_tender_mismatch")
    assert await _count(db, ContractFactBlock) == 2

    edited = await client_a.patch(
        f"{url}/{insurance['id']}",
        json={"value_json": {"amount_eur": 600000}, "block_ids": [b3["id"]]},
    )
    assert (edited.json()["value_json"], edited.json()["block_ids"]) == (
        {"amount_eur": 600000},
        [b3["id"]],
    )
    structure = (await client_a.get(STRUCTURE.format(company=company.id, tender=tender_id))).json()
    assert [f["fact_code"] for f in structure["contract_facts"]] == [
        "billing_terms",
        "liability_insurance",
    ]
    ref = await _file_ref(client_a, company, tender_id, label="PCAP, clausula 12")
    cited = await client_a.post(f"{url}/{insurance['id']}/citations", json={"source_ref_id": ref})
    assert [c["id"] for c in cited.json()["citations"]] == [ref]
    assert (await client_a.delete(f"{url}/{insurance['id']}")).status_code == 204
    assert (
        await _count(db, TenderContractFact) == 1
        and await db.get(TenderSourceRef, uuid.UUID(ref)) is not None
    )


async def test_contract_fact_scope_conflicts_are_refused_through_the_api(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    b1 = await _block(client_a, company, tender_id, "B1")
    b3 = await _block(client_a, company, tender_id, "B3")
    url = FACTS.format(company=company.id, tender=tender_id)
    first = await _fact(
        client_a, company, tender_id, "penalties", {"v": 1}, block_ids=[b1["id"], b3["id"]]
    )
    await _fact(client_a, company, tender_id, "billing_terms", {"period": "monthly"})

    same_scope = await client_a.post(
        url,
        json={
            "fact_code": "penalties",
            "value_json": {"v": 2},
            "value_schema": "v1",
            "block_ids": [b3["id"], b1["id"]],
        },
    )
    assert (same_scope.status_code, _code(same_scope)) == (422, "contract_fact_scope_conflict")
    second_billing = await client_a.post(
        url, json={"fact_code": "billing_terms", "value_json": {}, "value_schema": "v1"}
    )
    assert (second_billing.status_code, _code(second_billing)) == (
        422,
        "contract_fact_scope_conflict",
    )
    other_scope = await _fact(
        client_a, company, tender_id, "penalties", {"v": 3}, block_ids=[b3["id"]]
    )
    collide = await client_a.patch(
        f"{url}/{other_scope['id']}",
        json={"value_json": {"v": 4}, "block_ids": [b1["id"], b3["id"]]},
    )
    assert (collide.status_code, _code(collide)) == (422, "contract_fact_scope_conflict")
    unchanged = next(
        f
        for f in (
            await client_a.get(STRUCTURE.format(company=company.id, tender=tender_id))
        ).json()["contract_facts"]
        if f["id"] == other_scope["id"]
    )
    assert (unchanged["value_json"], unchanged["block_ids"]) == ({"v": 3}, [b3["id"]])
    recoded = await client_a.patch(f"{url}/{first['id']}", json={"fact_code": "site_visit"})
    assert (recoded.status_code, recoded.json()["fact_code"]) == (200, "site_visit")
    assert await _count(db, TenderContractFact) == 3


async def test_territories_are_deduplicated_through_the_api(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    requirement = await _requirement(client_a, company, tender_id)
    url = SEMANTICS.format(company=company.id, tender=tender_id, requirement=requirement["id"])
    d = {"kind": "municipality", "label": "Municipi D", "code": "17999", "code_system": "INE"}
    body = (
        await client_a.patch(
            url,
            json={
                "territories": [
                    d,
                    {**d, "label": " Municipi  D "},
                    {**d, "qualifier": "trama urbana"},
                ],
                "territory_match_mode": "all",
            },
        )
    ).json()
    assert sorted(t["qualifier"] or "" for t in body["territories"]) == ["", "trama urbana"]
    assert body["territory_match_mode"] == "all" and await _count(db, RequirementTerritory) == 2


# --------------------------------------------------------------------------- #
# Semantica d'un requisit                                                      #
# --------------------------------------------------------------------------- #


async def test_requirement_semantics_territories_proof_and_award_links(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    other_id = await _tender(client_a, company, title="Un altre")
    sites = [await _site(client_a, company, tender_id, f"S{n}") for n in range(1, 4)]
    foreign_site = await _site(client_a, company, other_id, "S9")
    requirement = await _requirement(client_a, company, tender_id)
    run = await _run(db, tender_id)
    c1 = await _criterion(db, run, "C1", 10.0)
    foreign_run = await _run(db, other_id)
    foreign_criterion = await _criterion(db, foreign_run, "Alie", 3.0)
    url = SEMANTICS.format(company=company.id, tender=tender_id, requirement=requirement["id"])

    one = await client_a.patch(
        url,
        json={
            "territories": [
                {
                    "kind": "municipality",
                    "label": "Municipi D",
                    "code": "17999",
                    "code_system": "INE",
                    "qualifier": "trama urbana",
                }
            ],
            "territory_match_mode": "any",
            "proof": [
                {"kind": "responsible_declaration", "text": "Declaracio responsable"},
                {"kind": "coverage_map", "text": "Mapa de cobertura", "mandatory": False},
            ],
            "award_criterion_ids": [str(c1.id)],
        },
    )
    assert one.status_code == 200, one.text
    body = one.json()
    assert (
        body["territory_match_mode"],
        len(body["territories"]),
        body["award_criterion_ids"],
    ) == ("any", 1, [str(c1.id)])
    assert (
        body["territories"][0]["qualifier"] == "trama urbana"
        and body["territories"][0]["site_id"] is None
    )
    assert body["proof_schema"] == "proof_v1" and body["proof_json"][1]["mandatory"] is False
    # El criteri no canvia de punts i ara sap de quin requisit es la cara exigible.
    analysis = (await client_a.get(ANALYSIS.format(tender=tender_id))).json()
    criterion = next(c for c in analysis["award_criteria"] if c["id"] == str(c1.id))
    assert (criterion["points"], criterion["requirement_ids"]) == (10.0, [requirement["id"]])
    read = next(r for r in analysis["requirements"] if r["id"] == requirement["id"])
    assert (read["territory_match_mode"], read["award_criterion_ids"], read["proof_schema"]) == (
        "any",
        [str(c1.id)],
        "proof_v1",
    )

    # Quinze... tres seus, mode `all`; una seu d'una altra licitacio no entra.
    all_sites = await client_a.patch(
        url,
        json={
            "territories": [{"kind": "address", "site_id": s["id"]} for s in sites],
            "territory_match_mode": "all",
        },
    )
    assert (
        all_sites.json()["territory_match_mode"],
        sorted(t["site_id"] for t in all_sites.json()["territories"]),
    ) == ("all", sorted(s["id"] for s in sites))
    wrong = await client_a.patch(
        url, json={"territories": [{"kind": "address", "site_id": foreign_site["id"]}]}
    )
    assert (wrong.status_code, _code(wrong)) == (422, "site_tender_mismatch")
    assert await _count(db, RequirementTerritory) == 3
    assert (
        await client_a.patch(url, json={"territories": [{"kind": "custom"}]})
    ).status_code == 422
    assert (await client_a.patch(url, json={"territory_match_mode": "some"})).status_code == 422

    # `[]` = cap prova identificada; `null` = encara no estructurat; tipus fora de la llista, 422.
    assert (await client_a.patch(url, json={"proof": []})).json()["proof_json"] == []
    assert (await client_a.patch(url, json={"proof": None})).json()["proof_schema"] is None
    assert (
        await client_a.patch(url, json={"proof": [{"kind": "blood_sample", "text": "x"}]})
    ).status_code == 422
    wrong = await client_a.patch(url, json={"award_criterion_ids": [str(foreign_criterion.id)]})
    assert (wrong.status_code, _code(wrong)) == (422, "criterion_tender_mismatch")
    assert (await client_a.patch(url, json={"award_criterion_ids": []})).json()[
        "award_criterion_ids"
    ] == []
    assert await _count(db, RequirementAwardLink) == 0


async def test_capability_needs_round_trip_mapped_and_unmapped(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    requirement = await _requirement(client_a, company, tender_id)
    run = await _run(db, tender_id)
    criterion = await _criterion(db, run, "Millores", 6.0)
    base = NEEDS.format(company=company.id, tender=tender_id, requirement=requirement["id"])

    ivr = await _need(
        client_a,
        company,
        tender_id,
        requirement["id"],
        capability_code="pbx.ivr_multilevel",
        comparator="present",
        need_type="minimum",
    )
    calls = await _need(
        client_a,
        company,
        tender_id,
        requirement["id"],
        capability_code="pbx.concurrent_calls",
        comparator="gte",
        value_json={"number": 50},
        value_schema="need_value_v1",
        need_type="scored_improvement",
        points=2,
        award_criterion_id=str(criterion.id),
    )
    callback = await _need(
        client_a,
        company,
        tender_id,
        requirement["id"],
        unmapped_capability="queue_callback_keep_position",
        comparator="present",
        need_type="demo",
        points=2,
    )
    assert (ivr["tender_id"], ivr["capability_code"], ivr["points"]) == (
        tender_id,
        "pbx.ivr_multilevel",
        None,
    )
    assert (calls["value_json"], calls["award_criterion_id"], calls["points"]) == (
        {"number": 50},
        str(criterion.id),
        2.0,
    )
    assert (callback["capability_code"], callback["unmapped_capability"]) == (
        None,
        "queue_callback_keep_position",
    )
    assert (await db.get(AwardCriterion, criterion.id)).points == 6.0
    assert "queue_callback_keep_position" not in vocabulary.CAPABILITIES_BY_CODE

    for payload in (
        {"comparator": "present", "need_type": "minimum"},
        {
            "capability_code": "pbx.queues",
            "unmapped_capability": "x",
            "comparator": "present",
            "need_type": "minimum",
        },
        {"capability_code": "pbx.teleport", "comparator": "present", "need_type": "minimum"},
        {
            "capability_code": "pbx.queues",
            "comparator": "present",
            "value_json": {"x": 1},
            "value_schema": "v",
            "need_type": "minimum",
        },
        {"capability_code": "pbx.concurrent_calls", "comparator": "gte", "need_type": "minimum"},
        {
            "capability_code": "pbx.queues",
            "comparator": "present",
            "need_type": "minimum",
            "points": -1,
        },
        {"capability_code": "pbx.queues", "comparator": "between", "need_type": "minimum"},
    ):
        assert (await client_a.post(base, json=payload)).status_code == 422, payload

    read = (
        await client_a.get(
            SEMANTICS.format(company=company.id, tender=tender_id, requirement=requirement["id"])
        )
    ).json()
    assert [n["id"] for n in read["capability_needs"]] == [ivr["id"], calls["id"], callback["id"]]
    analysis = (await client_a.get(ANALYSIS.format(tender=tender_id))).json()
    needs = next(r for r in analysis["requirements"] if r["id"] == requirement["id"])[
        "capability_needs"
    ]
    assert [n["comparator"] for n in needs] == ["present", "gte", "present"]

    edited = await client_a.patch(
        NEED.format(company=company.id, tender=tender_id, need=calls["id"]),
        json={"value_json": {"number": 20}, "need_type": "minimum", "points": None},
    )
    assert (edited.json()["value_json"], edited.json()["need_type"], edited.json()["points"]) == (
        {"number": 20},
        "minimum",
        None,
    )
    assert (
        await client_a.patch(
            NEED.format(company=company.id, tender=tender_id, need=calls["id"]),
            json={"capability_code": None},
        )
    ).status_code == 422
    ref = await _file_ref(client_a, company, tender_id, label="PPT, apartat 4.2")
    cited = await client_a.post(
        f"{NEED.format(company=company.id, tender=tender_id, need=callback['id'])}/citations",
        json={"source_ref_id": ref},
    )
    assert [c["id"] for c in cited.json()["citations"]] == [ref]
    assert (
        await client_a.delete(
            NEED.format(company=company.id, tender=tender_id, need=callback["id"])
        )
    ).status_code == 204
    assert (
        await _count(db, RequirementCapabilityNeed) == 2
        and await db.get(TenderSourceRef, uuid.UUID(ref)) is not None
    )


# --------------------------------------------------------------------------- #
# Permisos: exactament els de 0048                                             #
# --------------------------------------------------------------------------- #


async def test_permissions_are_the_existing_ones(client_a, client_b, tenant_a, tenant_b, db):
    user, company = tenant_a
    _, company_b = tenant_b
    tender_id = await _tender(client_a, company)
    requirement = await _requirement(client_a, company, tender_id)
    item = await _scope_item(client_a, company, tender_id, service_line_code="voice_pbx")
    scope_url = SCOPE.format(company=company.id, tender=tender_id)
    facts_url = FACTS.format(company=company.id, tender=tender_id)
    semantics = SEMANTICS.format(
        company=company.id, tender=tender_id, requirement=requirement["id"]
    )
    needs = NEEDS.format(company=company.id, tender=tender_id, requirement=requirement["id"])
    need_payload = {
        "capability_code": "pbx.queues",
        "comparator": "present",
        "need_type": "minimum",
    }

    for response in (
        await client_b.get(
            SEMANTICS.format(company=company_b.id, tender=tender_id, requirement=requirement["id"])
        ),
        await client_b.post(
            SCOPE.format(company=company_b.id, tender=tender_id),
            json={"service_line_code": "voice_pbx"},
        ),
        await client_b.post(
            FACTS.format(company=company_b.id, tender=tender_id),
            json={"fact_code": "penalties", "value_json": {}, "value_schema": "v1"},
        ),
        await client_b.patch(
            SEMANTICS.format(company=company_b.id, tender=tender_id, requirement=requirement["id"]),
            json={"proof": []},
        ),
    ):
        assert response.status_code == 404, response.text

    await _role(db, user, company, "viewer")
    assert (await client_a.get(semantics)).status_code == 200
    for response in (
        await client_a.post(scope_url, json={"service_line_code": "voice_pbx"}),
        await client_a.patch(f"{scope_url}/{item['id']}", json={"unit": "x"}),
        await client_a.post(
            facts_url, json={"fact_code": "penalties", "value_json": {}, "value_schema": "v1"}
        ),
        await client_a.patch(semantics, json={"proof": []}),
        await client_a.post(needs, json=need_payload),
    ):
        assert response.status_code == 404, response.text

    await _role(db, user, company, "technical")
    assert (await client_a.patch(semantics, json={"proof": []})).status_code == 200
    assert (await client_a.post(needs, json=need_payload)).status_code == 201
    assert (
        await client_a.post(scope_url, json={"service_line_code": "voice_pbx"})
    ).status_code == 404
    assert (
        await client_a.post(
            facts_url, json={"fact_code": "penalties", "value_json": {}, "value_schema": "v1"}
        )
    ).status_code == 404

    await _role(db, user, company, "bid_manager")
    assert (
        await client_a.post(scope_url, json={"service_line_code": "voice_pbx"})
    ).status_code == 201
    assert (
        await client_a.post(
            facts_url, json={"fact_code": "penalties", "value_json": {}, "value_schema": "v1"}
        )
    ).status_code == 201
    assert await _count(db, TenderScopeItem) == 2 and await _count(db, TenderContractFact) == 1


async def test_a_public_tender_is_readable_but_only_the_internal_service_writes_it(
    client_a, tenant_a, db
):
    from app.models.enums import ContractFactCode, SourceType
    from app.models.sources import Source
    from app.models.tenders import Tender
    from app.services import tender_scope as scope

    _, company = tenant_a
    source = Source(
        code=f"feed-{uuid.uuid4().hex[:6]}", name="Font sindicada", source_type=SourceType.API
    )
    db.add(source)
    await db.flush()
    tender = Tender(source_id=source.id, external_id=uuid.uuid4().hex, title="Publica")
    db.add(tender)
    await db.commit()
    # Via interna (fixture/extraccio): persisteix.
    await scope.create_scope_item(db, tender, {"service_line_code": "access_ftth", "quantity": 15})
    await scope.create_fact(
        db,
        tender,
        fact_code=ContractFactCode.ENVELOPES,
        value_json={"count": 3},
        value_schema="v1",
        block_ids=[],
    )
    await db.commit()
    # Tenant: llegeix, pero no escriu.
    structure = (await client_a.get(STRUCTURE.format(company=company.id, tender=tender.id))).json()
    assert (len(structure["scope_items"]), len(structure["contract_facts"])) == (1, 1)
    refused = await client_a.post(
        SCOPE.format(company=company.id, tender=tender.id), json={"service_line_code": "voice_pbx"}
    )
    assert (refused.status_code, _code(refused)) == (422, "tender_not_manual")
    refused = await client_a.post(
        FACTS.format(company=company.id, tender=tender.id),
        json={"fact_code": "penalties", "value_json": {}, "value_schema": "v1"},
    )
    assert (refused.status_code, _code(refused)) == (422, "tender_not_manual")
    assert await _count(db, TenderScopeItem) == 1 and await _count(db, TenderContractFact) == 1


# --------------------------------------------------------------------------- #
# Gold sintetic del lock, i cap confusio de nivells                            #
# --------------------------------------------------------------------------- #


async def test_gold_synthetic_scope_and_semantics(client_a, tenant_a, db):
    _, company = tenant_a
    before = await _company_counts(db)
    tender_id = await _tender(client_a, company, title="Contracte sintetic T")
    blocks = {
        code: await _block(client_a, company, tender_id, code) for code in ("B1", "B2", "B3", "B4")
    }
    sites = [await _site(client_a, company, tender_id, "S0", role="central")]
    sites += [
        await _site(client_a, company, tender_id, f"S{n}", role="remote") for n in range(1, 11)
    ]

    # Abast: que es compra, per bloc.
    plan = (
        ("B1", "access_ftth", "15", "accessos"),
        ("B2", "network_switching", "27", "equips"),
        ("B3", "voice_pbx", "123", "extensions"),
        ("B4", "fiber_deployment", "4206", "m"),
    )
    for code, line, quantity, unit in plan:
        await _scope_item(
            client_a,
            company,
            tender_id,
            service_line_code=line,
            block_id=blocks[code]["id"],
            quantity=quantity,
            unit=unit,
        )

    # Requisits.
    r1 = await _requirement(client_a, company, tender_id, title="R1 solvencia")
    r2 = await _requirement(client_a, company, tender_id, title="R2 cobertura al municipi D")
    r3 = await _requirement(client_a, company, tender_id, title="R3 servei a totes les seus")
    run = await _run(db, tender_id)
    c1 = await _criterion(db, run, "C1", 10.0)
    c2 = await _criterion(db, run, "C2", 6.0)
    sem = lambda r: SEMANTICS.format(company=company.id, tender=tender_id, requirement=r["id"])  # noqa: E731
    assert (
        await client_a.patch(
            sem(r1),
            json={
                "proof": [{"kind": "annual_accounts", "text": "Comptes anuals", "mandatory": True}]
            },
        )
    ).status_code == 200
    assert (
        await client_a.patch(
            sem(r2),
            json={
                "territories": [
                    {
                        "kind": "municipality",
                        "label": "Municipi D",
                        "code": "17999",
                        "code_system": "INE",
                    }
                ],
                "territory_match_mode": "any",
                "proof": [
                    {"kind": "responsible_declaration", "text": "Declaracio responsable"},
                    {"kind": "coverage_map", "text": "Mapa de cobertura"},
                    {"kind": "ownership_document", "text": "Titularitat de la xarxa"},
                ],
                "award_criterion_ids": [str(c1.id)],
            },
        )
    ).status_code == 200
    # R3: quinze territoris d'adreca (deu seus del plec i cinc adreces mes), mode `all`.
    r3_territories = [{"kind": "address", "site_id": s["id"]} for s in sites[1:]]
    r3_territories += [{"kind": "address", "label": f"Adreca remota {n}"} for n in range(11, 16)]
    r3_read = (
        await client_a.patch(
            sem(r3), json={"territories": r3_territories, "territory_match_mode": "all"}
        )
    ).json()
    assert (len(r3_read["territories"]), r3_read["territory_match_mode"]) == (15, "all")

    # Necessitats, amb una de sense mapar.
    for fields in (
        {"capability_code": "pbx.ivr_multilevel", "comparator": "present", "need_type": "minimum"},
        {
            "capability_code": "pbx.concurrent_calls",
            "comparator": "gte",
            "value_json": {"number": 20},
            "value_schema": "need_value_v1",
            "need_type": "minimum",
        },
        {
            "capability_code": "pbx.concurrent_calls",
            "comparator": "gte",
            "value_json": {"number": 50},
            "value_schema": "need_value_v1",
            "need_type": "scored_improvement",
            "points": 2,
            "award_criterion_id": str(c2.id),
        },
        {
            "unmapped_capability": "queue_callback_keep_position",
            "comparator": "present",
            "need_type": "demo",
            "points": 2,
        },
    ):
        await _need(client_a, company, tender_id, r3["id"], **fields)

    # Fets: un de no-facturacio sobre B1 + B3; facturacio general sense blocs.
    await _fact(client_a, company, tender_id, "envelopes", {"count": 3})
    await _fact(
        client_a,
        company,
        tender_id,
        "liability_insurance",
        {"amount_eur": 300000},
        block_ids=[blocks["B1"]["id"], blocks["B3"]["id"]],
    )
    await _fact(client_a, company, tender_id, "abnormal_offer_params", {"threshold_pct": 10})
    await _fact(client_a, company, tender_id, "live_demo", {"required": True})
    await _fact(client_a, company, tender_id, "billing_terms", {"period": "monthly"})

    # Lectura: el que s'ha dit, i res mes.
    structure = (await client_a.get(STRUCTURE.format(company=company.id, tender=tender_id))).json()
    assert {i["service_line_code"]: Decimal(i["quantity"]) for i in structure["scope_items"]} == {
        "access_ftth": Decimal("15"),
        "network_switching": Decimal("27"),
        "voice_pbx": Decimal("123"),
        "fiber_deployment": Decimal("4206"),
    }
    facts = {f["fact_code"]: f for f in structure["contract_facts"]}
    assert set(facts) == {
        "envelopes",
        "liability_insurance",
        "abnormal_offer_params",
        "live_demo",
        "billing_terms",
    }
    assert set(facts["liability_insurance"]["block_ids"]) == {
        blocks["B1"]["id"],
        blocks["B3"]["id"],
    }
    assert (
        facts["billing_terms"]["block_ids"] == []
        and facts["billing_terms"]["contract_wide"] is True
    )
    analysis = (await client_a.get(ANALYSIS.format(tender=tender_id))).json()
    by_id = {r["id"]: r for r in analysis["requirements"]}
    assert (
        by_id[r1["id"]]["proof_json"][0]["kind"] == "annual_accounts"
        and by_id[r1["id"]]["territories"] == []
    )
    assert (by_id[r2["id"]]["territory_match_mode"], by_id[r2["id"]]["award_criterion_ids"]) == (
        "any",
        [str(c1.id)],
    )
    assert [p["kind"] for p in by_id[r2["id"]]["proof_json"]] == [
        "responsible_declaration",
        "coverage_map",
        "ownership_document",
    ]
    assert (
        by_id[r3["id"]]["territory_match_mode"],
        len(by_id[r3["id"]]["territories"]),
        len(by_id[r3["id"]]["capability_needs"]),
    ) == ("all", 15, 4)
    criteria = {c["id"]: c for c in analysis["award_criteria"]}
    assert (criteria[str(c1.id)]["points"], criteria[str(c1.id)]["requirement_ids"]) == (
        10.0,
        [r2["id"]],
    )
    assert (criteria[str(c2.id)]["points"], criteria[str(c2.id)]["requirement_ids"]) == (6.0, [])

    # Cap confusio de nivells: res de l'empresa s'ha tocat, i res de 0050 existeix.
    assert await _company_counts(db) == before
    for table in NOT_YET:
        assert table not in Base.metadata.tables, table
    assert await _count(db, TenderBlock) == 4 and await _count(db, RequirementTerritory) == 16
