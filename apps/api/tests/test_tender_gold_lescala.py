"""Gold de REPRESENTABILITAT del costat plec: l'Escala, Telecomunicacions 2027.

No es cap analisi empresa <-> licitacio. La pregunta es una altra: el model de
0048 + 0049 + 0050 pot representar el plec real sense inventar, perdre ni forcar
semantica? El dataset `datasets/analysis/lescala-2026.tender-gold.json` nomes
porta dades publiques derivades dels documents; es carrega per la via interna
(cap router, cap empresa) i es torna a llegir de la base per comparar-ho.

El que falta als documents (el DOC-04, l'anunci del DOUE, el guio de la demo)
ha de CONTINUAR faltant: cap adreca, cap seu FTTH, cap data limit inventada, i
cap correspondencia inventada entre les set seus critiques i els ramals.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.evaluation import tender_gold
from app.knowledge import vocabulary
from app.models.enums import (
    AwardCriterionType,
    AwardEvaluationType,
    NeedType,
    RequirementOrigin,
    RequirementScope,
    RequirementTiming,
    SourceRefKind,
    TerritoryCompleteness,
    TerritoryMatchMode,
)
from app.models.identity import Company
from app.models.knowledge import CapabilityDefinitionRow
from app.models.provenance import RequirementSourceRefLink, TenderSourceRef
from app.models.requirements import AwardCriterion, Requirement
from app.models.tender_scope import (
    ContractFactBlock,
    RequirementAwardLink,
    RequirementCapabilityNeed,
    RequirementCapabilityNeedSourceRefLink,
    RequirementTerritory,
    TenderContractFactSourceRefLink,
    TenderScopeItem,
    TenderScopeItemSourceRefLink,
)
from app.models.tender_structure import (
    AwardCriterionSourceRefLink,
    TenderBlock,
    TenderBlockSourceRefLink,
    TenderSite,
    TenderSiteSourceRefLink,
)
from app.models.tenders import Tender, TenderDocument, TenderLot
from app.services import tender_scope as scope

pytestmark = pytest.mark.asyncio

API_ROOT = Path(__file__).resolve().parents[1]
CASE = "lescala-2026"
CITED_SECTIONS = (
    "blocks",
    "sites",
    "scope_items",
    "award_criteria",
    "requirements",
    "contract_facts",
)
#: Les set seus critiques, pel codi del cataleg unic del DOC-04 (§1).
CRITICAL_SITES = {"U01", "U02", "U03", "U04", "U05", "U06", "U08"}
#: Ramals D02-D11 -> ubicacio (DOC-04 §2) i accessos F01-F15 -> ubicacio (DOC-04 §3).
RAMAL_SITES = {
    "D02": "U02", "D03": "U03", "D04": "U04", "D05": "U05", "D06": "U06",
    "D07": "U07", "D08": "U08", "D09": "U09", "D10": "U24", "D11": "U25",
}  # fmt: skip
FTTH_SITES = {
    "F01": "U10", "F02": "U11", "F03": "U12", "F04": "U13", "F05": "U14",
    "F06": "U15", "F07": "U16", "F08": "U02", "F09": "U03", "F10": "U17",
    "F11": "U18", "F12": "U19", "F13": "U20", "F14": "U21", "F15": "U22",
}  # fmt: skip
#: El DOC-04 real (suplementari) i el fitxer del paquet inicial que se'n feia passar.
DOC04_SHA = "84990c5d8bfb884413d730e1f7ffa94fb1d6ad8dac3796c1504dc00d67dce537"

#: Taules que la fase 3B encara no ha creat.
NOT_YET = (
    "proposal_drafts",
    "proposal_lines",
    "delivery_plans",
)
#: Taules de l'analisi (0051): existeixen, pero carregar o editar un plec no hi escriu res.
DERIVED_0051 = (
    "tender_analysis_runs",
    "tender_fit_assessments",
    "capability_need_assessments",
    "criterion_assessments",
    "delivery_candidates",
    "analysis_findings",
    "analysis_questions",
)
#: Res de l'EMPRESA pot apareixer per carregar un plec: ni coneixement, ni
#: resultats, ni propostes. Tota taula amb aquests prefixos ha de quedar buida.
COMPANY_SIDE_PREFIXES = ("company_", "knowledge_", "solution_", "proposal")
#: Paraules que un gold PUBLIC del plec no pot contenir: coneixement, partners,
#: cobertura, productes i llenguatge analitic d'una empresa concreta.
PRIVATE_WORDS = (
    "indaleccius",
    "banyoles",
    "mont-ras",
    "santa cristina",
    "3cx",
    "freepbx",
    "templus",
    "adamo",
    "sewan",
    "cablem",
    "inatic",
    "partner",
    "risc ",
    "oportunitat",
    "probable",
    "afavoreix",
    "perjudica",
    "satisfies",
    "delivery_coverage",
    "proposta preliminar",
)


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


@pytest.fixture
def dataset() -> dict:
    return tender_gold.load_dataset(CASE)


async def _count(db, model) -> int:
    return await db.scalar(sa.select(sa.func.count()).select_from(model))


async def _table_names(db) -> list[str]:
    return await db.run_sync(lambda s: sa.inspect(s.connection()).get_table_names())


async def _rows(db, table: str) -> int:
    return await db.scalar(sa.text(f'SELECT count(*) FROM "{table}"'))


def _by_code(dataset: dict, section: str) -> dict[str, dict]:
    return {item["code"]: item for item in dataset[section]}


def _citations(dataset: dict) -> set[tuple]:
    return {
        (s["doc"], s["where"], s.get("quote"))
        for section in CITED_SECTIONS
        for item in dataset[section]
        for s in item["sources"]
    }


# --------------------------------------------------------------------------- #
# El dataset: nomes el plec                                                     #
# --------------------------------------------------------------------------- #


async def test_the_dataset_is_public_tender_data_only(dataset):
    assert dataset["schema"] == "tender-gold-v2"
    text = json.dumps(dataset, ensure_ascii=False).lower()
    leaked = [word for word in PRIVATE_WORDS if word in text]
    assert leaked == [], f"el gold public porta dades o llenguatge d'empresa: {leaked}"
    # Tot valor del plec diu d'on surt; cap cita apunta al fitxer que es fa passar pel DOC-04.
    citable = {d["key"] for d in dataset["documents"] if d.get("citable", True)}
    assert "DOC04FILE" not in citable
    for section in CITED_SECTIONS:
        for item in dataset[section]:
            where = f"{section}:{item.get('code') or item.get('key')}"
            assert item["sources"], f"{where} sense font"
            assert all(s["doc"] in citable for s in item["sources"]), where
    # Cap codi que el plec no doni: ni INE als territoris, ni codis sintetics a les seus.
    for requirement in dataset["requirements"]:
        for territory in requirement["territories"]:
            assert territory["code"] is None and territory["code_system"] is None
    for site in dataset["sites"]:
        assert site["code"] is not None or site["name"] is not None
        assert site["code"] != site["name"], "cap nom copiat del codi"
    # La historia no es reescriu: el gold de classificacio del paquet inicial continua dient
    # que hi faltava el DOC-04 (el fitxer amb el seu nom era el DOC-03).
    classification = json.loads(
        (API_ROOT / "datasets" / "classification" / f"{CASE}.gold.json").read_text(encoding="utf-8")
    )
    missing = [f for f in classification["findings"] if f["code"] == "referenced_document_missing"]
    assert [f["detail"]["code"] for f in missing] == ["DOC-04"]
    assert DOC04_SHA not in {d["sha256"] for d in classification["documents"]}
    # El DOC-04 real arriba despres, com a font suplementaria: el gold del plec el cita i el
    # fals es conserva (no citable) amb la referencia al que el substitueix.
    by_key = {d["key"]: d for d in dataset["documents"]}
    assert by_key["DOC04"]["sha256"] == DOC04_SHA and by_key["DOC04"]["declared_code"] == "DOC-04"
    assert by_key["DOC04"]["received"].startswith("suplementari")
    assert by_key["DOC04FILE"]["superseded_by"] == "DOC04" and not by_key["DOC04FILE"]["citable"]
    assert {d["sha256"] for d in dataset["documents"]} == {
        d["sha256"] for d in classification["documents"]
    } | {DOC04_SHA}
    missing_codes = {d["code"] for d in dataset["missing_documents"]}
    assert "DOC-04" not in missing_codes and "DOUE-ANUNCI" in missing_codes
    # La numeracio concreta no es cap document absent: es reservada fins a l'adjudicacio.
    assert "INVENTARI-NUMERACIO" not in missing_codes
    assert any("numeració concreta" in w for w in dataset["withheld"]["reserved_until_award"])
    # 0050 ja ho representa: cap timing retingut.
    assert dataset["withheld"]["timing"] == []


# --------------------------------------------------------------------------- #
# Carregar i tornar a llegir                                                    #
# --------------------------------------------------------------------------- #


async def test_the_lescala_tender_is_representable_without_inventing(db, dataset):
    loaded = await tender_gold.load_tender_gold(db, dataset)
    tender_id, run_id = loaded.tender.id, loaded.run.id
    await db.commit()
    db.expire_all()

    tender = await db.get(Tender, tender_id)
    assert tender is not None
    await db.refresh(tender, attribute_names=["lots", "cpvs"])
    spec = dataset["tender"]

    # --- Expedient: cap lot, 48 mesos, SARA, inici previst, cap data limit inventada.
    assert tender.owner_company_id is None, "el gold es public"
    assert await _count(db, TenderLot) == 0 and tender.lots == []
    assert (tender.duration_months, tender.extension_months) == (48, 0)
    assert tender.regulated_harmonised is True
    assert str(tender.planned_start_date) == "2027-01-01"
    assert tender.deadline_at is None and tender.publication_date is None
    assert tender.base_budget_without_tax == Decimal("204232.75")
    assert tender.estimated_value == Decimal("245079.30")
    assert {c.cpv_code: c.is_main for c in tender.cpvs} == {
        spec["cpv"]["main"]: True,
        **{code: False for code in spec["cpv"]["others"]},
    }

    # --- Blocs: quatre, que sumen el PBL i els 40 punts de preu.
    blocks = {b.code: b for b in await db.scalars(sa.select(TenderBlock))}
    assert set(blocks) == {"B1", "B2", "B3", "B4"}
    assert sum(b.base_price_ex_vat for b in blocks.values()) == Decimal("204232.75")
    assert sum(b.price_points for b in blocks.values()) == 40
    assert all(b.separately_awardable is False for b in blocks.values())
    assert blocks["B2"].billing_terms_json["instalments"] == 48
    assert blocks["B4"].billing_terms_json["instalments"] == 4
    assert blocks["B1"].billing_terms_json is None and blocks["B3"].billing_terms_json is None
    monthly_b1 = Decimal("600.00") + 15 * Decimal("45.00") + 2 * Decimal("150.00")
    assert monthly_b1 * 48 == blocks["B1"].base_price_ex_vat

    # --- Criteris: 40 + 32 + 28 = 100, amb J6 = 7, els trams conservats i, ara, cites.
    criteria = {c.dedup_key: c for c in await db.scalars(sa.select(AwardCriterion))}
    expected_criteria = _by_code(dataset, "award_criteria")
    by_code = {
        code: criteria[tender_gold.dedup_key(CASE, "criterion", code)] for code in expected_criteria
    }
    assert len(criteria) == len(expected_criteria) == 28
    price = sum(c.points for c in by_code.values() if c.criterion_type == AwardCriterionType.PRICE)
    automatic = sum(
        c.points
        for c in by_code.values()
        if c.evaluation_type == AwardEvaluationType.AUTOMATIC
        and c.criterion_type != AwardCriterionType.PRICE
    )
    judgment = sum(
        c.points for c in by_code.values() if c.evaluation_type == AwardEvaluationType.JUDGMENT
    )
    assert (price, automatic, judgment) == (40, 32, 28)
    assert by_code["J6"].points == 7
    assert by_code["T1"].tranches == [
        {"min": 30, "points": 0.67},
        {"min": 40, "points": 1.33},
        {"min": 50, "points": 2},
    ]
    assert by_code["P1"].formula_text == "Pv = [1 − (Ov − Om) / PL] × P"
    for code, expected in expected_criteria.items():
        criterion = by_code[code]
        await db.refresh(criterion, attribute_names=["block_links"])
        linked = {link.block_id for link in criterion.block_links}
        assert sorted(c for c, b in blocks.items() if b.id in linked) == sorted(expected["blocks"])
        assert criterion.run_id == run_id
        assert criterion.timing == RequirementTiming(expected["timing"]), code
    cited_criteria = set(
        await db.scalars(sa.select(AwardCriterionSourceRefLink.award_criterion_id).distinct())
    )
    assert cited_criteria == {c.id for c in by_code.values()}, "tots els criteris citen el plec"
    assert all(
        b.price_points == by_code[f"P{i}"].points
        for i, b in enumerate(sorted(blocks.values(), key=lambda b: b.code), 1)
    )

    # --- Seus: les 25 ubicacions fisiques del cataleg del DOC-04 (§1), pel codi Uxx. Els Dxx
    # i Fxx son identificadors funcionals, no seus.
    sites = list(await db.scalars(sa.select(TenderSite)))
    assert len(sites) == len(dataset["sites"]) == 25
    by_site = {s.code: s for s in sites}
    assert set(by_site) == {f"U{n:02d}" for n in range(1, 26)}
    assert all(s.name and s.address for s in sites)
    assert [s.code for s in sites if s.role.value == "central"] == ["U01"]
    town_hall = by_site["U01"]
    assert town_hall.name == "Ajuntament"
    assert {c for c, s in by_site.items() if s.is_critical is True} == CRITICAL_SITES
    assert all(s.is_critical is False for c, s in by_site.items() if c not in CRITICAL_SITES)
    assert all(s.municipality_code is None and s.municipality_code_system is None for s in sites)
    # Coordenades indicatives, a 6 decimals; el valor complet i la qualitat A/R/P, al gold.
    assert all(s.latitude is not None and s.longitude is not None for s in sites)
    spec_u01 = next(x for x in dataset["sites"] if x["code"] == "U01")
    assert (spec_u01["location_quality"], spec_u01["wgs84"]) == (
        "R",
        "42.12486103647484, 3.132287615058149",
    )
    assert town_hall.latitude == Decimal("42.124861")
    assert {x["location_quality"] for x in dataset["sites"]} == {"A", "R", "P"}
    ramals = {d: by_site[u] for d, u in RAMAL_SITES.items()}

    # --- Abast: els 15 FTTH son quinze elements d'una unitat, un per adreca (DOC-04 §3), i cap
    # agregat que els compti dues vegades.
    items = list(await db.scalars(sa.select(TenderScopeItem)))
    assert len(items) == len(dataset["scope_items"]) == 25
    ftth = [i for i in items if i.service_line_code == "access_ftth"]
    assert len(ftth) == 15 and all(i.quantity == Decimal("1") for i in ftth)
    assert all(i.block_id == blocks["B1"].id for i in ftth)
    assert all(i.unit_base_price_ex_vat == Decimal("45.00") for i in ftth)
    assert {i.specs_json["ftth_id"]: i.site_id for i in ftth} == {
        f: by_site[u].id for f, u in FTTH_SITES.items()
    }
    assert sum(1 for i in ftth if i.specs_json["vpn"]) == 13
    assert {i.specs_json["ftth_id"] for i in ftth if not i.specs_json["vpn"]} == {"F08", "F10"}
    radios = sorted(
        (i for i in items if i.unit == "radioenllaç"), key=lambda i: i.specs_json["path"]
    )
    assert [i.site_id for i in radios] == [by_site["U03"].id, by_site["U05"].id]
    wimax = next(i for i in items if (i.specs_json or {}).get("technology") == "WiMAX")
    assert wimax.site_id == by_site["U23"].id
    ramal_item = next(i for i in items if i.service_line_code == "dedicated_network")
    assert ramal_item.site_id is None and ramal_item.quantity == Decimal("10")
    assert {r["id"]: r["site"] for r in ramal_item.specs_json["ramals"]} == RAMAL_SITES
    assert ramal_item.specs_json["central"] == {"id": "D01", "site": "U01"}
    per_block = {code: sum(1 for i in items if i.block_id == b.id) for code, b in blocks.items()}
    assert per_block == {"B1": 19, "B2": 1, "B3": 3, "B4": 2}
    assert {i.service_line_code for i in items} <= vocabulary.SERVICE_LINE_CODES
    assert "network_electronics" not in {i.service_line_code for i in items}
    switches = next(i for i in items if i.service_line_code == "network_switching")
    assert switches.quantity == Decimal("27") and switches.specs_json["sfp_plus_10gbase_lr"] == 44
    placed = [n for row in switches.specs_json["by_site"] for n in row["units"].values()]
    assert sum(placed) == 27, "la distribucio per ubicacio conserva les 27 unitats"
    assert sum(row.get("sfp_plus", 0) for row in switches.specs_json["by_site"]) == 40
    assert (switches.specs_json["sfp_plus_in_service"], switches.specs_json["sfp_plus_spare"]) == (
        40,
        4,
    )
    assert all(t["reference_model"].endswith("o equivalent") for t in switches.specs_json["types"])
    extensions = next(i for i in items if i.unit == "extensió")
    rows = extensions.specs_json["by_site"]
    assert [sum(r[k] for r in rows) for k in ("normal", "dect", "operator", "analog")] == [
        103,
        14,
        2,
        4,
    ]
    assert [r["label"] for r in rows if r["site"] is None] == ["Turisme"], "U08 o U23: no se sap"
    fibre = next(i for i in items if i.service_line_code == "fiber_deployment")
    assert (fibre.quantity, fibre.unit, fibre.specs_json["splices"]) == (Decimal("4206"), "m", 243)
    assert sum(r["metres_indicative"] for r in fibre.specs_json["ramals"]) == 4206
    assert {r["id"]: r["site"] for r in fibre.specs_json["ramals"]} == RAMAL_SITES
    assert fibre.specs_json["allocation_is_unit_price"] is False
    assert fibre.unit_base_price_ex_vat is None, "les imputacions no son preus unitaris"
    assert sum(Decimal(r["indicative_allocation_ex_vat"]) for r in fibre.specs_json["ramals"]) == (
        Decimal(fibre.specs_json["closed_max_price_ex_vat"])
    )
    trunk = next(i for i in items if i.service_line_code == "voice_trunk")
    assert trunk.specs_json["concrete_numbers_status"] == "reserved_until_award"

    # --- Requisits: atomics, manuals, sense lot, un per codi del gold.
    requirements = {r.dedup_key: r for r in await db.scalars(sa.select(Requirement))}
    expected_requirements = _by_code(dataset, "requirements")
    rq = {
        code: requirements[tender_gold.dedup_key(CASE, "requirement", code)]
        for code in expected_requirements
    }
    assert len(requirements) == len(expected_requirements) == 34
    assert {r["gold_group"] for r in dataset["requirements"]} == {
        *(f"R{n}" for n in range(1, 15)),
        "C1",
    }
    assert all(r.origin == RequirementOrigin.MANUAL and r.run_id is None for r in rq.values())
    assert all(r.scope == RequirementScope.TENDER and r.tender_lot_id is None for r in rq.values())
    for code, expected in expected_requirements.items():
        requirement = rq[code]
        assert requirement.timing == RequirementTiming(expected["timing"]), code
        assert requirement.territory_completeness == TerritoryCompleteness(
            expected["territory_completeness"]
        ), code
        await db.refresh(requirement, attribute_names=["block_links", "territories", "award_links"])
        assert len(requirement.territories) == len(expected["territories"]), code
        linked_blocks = {link.block_id for link in requirement.block_links}
        assert sorted(c for c, b in blocks.items() if b.id in linked_blocks) == sorted(
            expected["blocks"]
        ), code
        linked_criteria = {link.award_criterion_id for link in requirement.award_links}
        assert sorted(c for c, cr in by_code.items() if cr.id in linked_criteria) == sorted(
            expected["criteria"]
        ), code
        assert requirement.proof_schema == "proof_v1" and requirement.proof_json is not None, code
        assert [(p["kind"], p["mandatory"]) for p in requirement.proof_json] == [
            (p["kind"], p["mandatory"]) for p in expected["proof"]
        ], code

    # --- Temporalitat: «abans de formalitzar» es diu; dues fases son dos requisits.
    assert rq["R5a"].timing == RequirementTiming.BEFORE_FORMALIZATION
    assert rq["R5b"].timing == RequirementTiming.AT_EXECUTION
    assert rq["R13a"].timing == RequirementTiming.BEFORE_FORMALIZATION
    assert not [r for r in rq.values() if r.timing == RequirementTiming.UNKNOWN]
    assert expected_requirements["R5a"]["gold_group"] == expected_requirements["R5b"]["gold_group"]

    # --- Completesa territorial: el que el plec dona, tal com ho dona; el que no, `unknown`.
    territories = list(await db.scalars(sa.select(RequirementTerritory)))
    assert len(territories) == sum(len(r["territories"]) for r in dataset["requirements"]) == 64
    assert rq["R1"].territory_completeness == TerritoryCompleteness.NOT_APPLICABLE
    assert rq["R1"].territories == []
    # C1: trama urbana de l'Escala, completa, sense cap codi ni cap dada d'empresa.
    assert rq["C1R"].territory_completeness == TerritoryCompleteness.COMPLETE
    (c1_territory,) = rq["C1R"].territories
    assert (c1_territory.kind.value, c1_territory.label, c1_territory.qualifier) == (
        "municipality",
        "L'Escala",
        "trama urbana",
    )
    assert c1_territory.code is None and c1_territory.code_system is None
    assert c1_territory.site_id is None
    assert rq["C1R"].territory_match_mode == TerritoryMatchMode.ANY
    assert {p["kind"] for p in rq["C1R"].proof_json} == {
        "responsible_declaration",
        "coverage_map",
        "ownership_document",
    }
    assert all(p["mandatory"] for p in rq["C1R"].proof_json)
    # 15 FTTH: llista completa (DOC-04 §3), ancorada a les ubicacions; cap adreca inventada.
    assert rq["R8b"].territory_completeness == TerritoryCompleteness.COMPLETE
    assert {t.site_id for t in rq["R8b"].territories} == {
        by_site[u].id for u in FTTH_SITES.values()
    }
    assert rq["R8b"].territory_match_mode == TerritoryMatchMode.ANY
    assert "territories_pending_document" not in expected_requirements["R8b"]
    # SLA critic i no critic: les set critiques i les divuit restants, per site_id.
    critical = rq["R8e"]
    assert critical.territory_completeness == TerritoryCompleteness.COMPLETE
    assert {t.site_id for t in critical.territories} == {by_site[c].id for c in CRITICAL_SITES}
    assert all(t.kind.value == "address" and t.code is None for t in critical.territories)
    assert rq["R8f"].territory_completeness == TerritoryCompleteness.COMPLETE
    assert {t.site_id for t in rq["R8f"].territories} == {
        s.id for c, s in by_site.items() if c not in CRITICAL_SITES
    }
    # Contingencies: radioenllacos a U03 i U05, WiMAX a U23 (DOC-04 §4).
    assert {t.site_id for t in rq["R8d"].territories} == {
        by_site[c].id for c in ("U03", "U05", "U23")
    }
    # Ramals: els deu destins del DOC-04 §2, per ubicacio.
    for code in ("R8a", "R11a"):
        assert rq[code].territory_completeness == TerritoryCompleteness.COMPLETE
        assert {t.site_id for t in rq[code].territories} == {s.id for s in ramals.values()}
        assert all(t.kind.value == "address" and t.label is None for t in rq[code].territories)

    # --- Necessitats: del plec, mai de cap empresa; J6 = 7 punts repartits en sis escenes demo.
    needs = list(await db.scalars(sa.select(RequirementCapabilityNeed)))
    assert len(needs) == sum(len(r["needs"]) for r in dataset["requirements"])
    demo = [n for n in needs if n.need_type == NeedType.DEMO]
    assert len(demo) == 6 and all(n.award_criterion_id == by_code["J6"].id for n in demo)
    assert sum(n.points for n in demo) == 7 == by_code["J6"].points
    t1 = sorted(n.points for n in needs if n.award_criterion_id == by_code["T1"].id)
    assert t1 == [0.67, 1.33, 2]
    mapped = {n.capability_code for n in needs if n.capability_code}
    assert mapped <= set(vocabulary.CAPABILITIES_BY_CODE)
    unmapped = {n.unmapped_capability for n in needs if n.unmapped_capability}
    assert unmapped and not (unmapped & set(vocabulary.CAPABILITIES_BY_CODE))
    assert await _count(db, CapabilityDefinitionRow) == len(vocabulary.CAPABILITIES), (
        "cap vocabulari nou"
    )
    minimum_pbx = {
        n.capability_code
        for n in needs
        if n.need_type == NeedType.MINIMUM
        and n.requirement_id == rq["R10a"].id
        and n.capability_code
    }
    assert minimum_pbx >= {
        "pbx.concurrent_calls",
        "pbx.ivr_multilevel",
        "pbx.queues",
        "pbx.blf",
        "pbx.call_park",
        "pbx.cdr_export",
        "pbx.recording_policy",
        "pbx.tls_srtp",
        "pbx.fraud_block",
    }

    # --- Enllacos requisit <-> criteri: nomes on hi ha una cara exigible.
    links = list(await db.scalars(sa.select(RequirementAwardLink)))
    assert len(links) == sum(len(r["criteria"]) for r in dataset["requirements"])
    assert {link.award_criterion_id for link in links if link.requirement_id == rq["C1R"].id} == {
        by_code["C1"].id
    }

    # --- Fets contractuals: penalitats amb quatre abasts diferents; cap billing_terms general.
    facts = await scope.facts_of(db, tender.id)
    assert len(facts) == len(dataset["contract_facts"]) == 16
    fact_blocks = await scope.fact_block_ids(db, [f.id for f in facts])
    penalties = [f for f in facts if f.fact_code.value == "penalties"]
    assert len(penalties) == 4
    assert len({frozenset(fact_blocks.get(f.id, [])) for f in penalties}) == 4
    assert [f.fact_code.value for f in facts].count("billing_terms") == 0
    demo_fact = next(f for f in facts if f.fact_code.value == "live_demo")
    assert fact_blocks[demo_fact.id] == [blocks["B3"].id]
    assert sum(s["points"] for s in demo_fact.value_json["scenes"]) == 7
    assert await _count(db, ContractFactBlock) == sum(
        len(f["blocks"]) for f in dataset["contract_facts"]
    )

    # --- Provinenca: tot el que es pot citar te cita; cap cita al fitxer del fals DOC-04.
    refs = list(await db.scalars(sa.select(TenderSourceRef)))
    assert len(refs) == len(_citations(dataset))
    assert all(r.ref_kind == SourceRefKind.DOCUMENT_FILE for r in refs)
    real_hashes = {d["sha256"] for d in dataset["documents"] if d.get("citable", True)}
    fake_doc04 = next(d["sha256"] for d in dataset["documents"] if d["key"] == "DOC04FILE")
    assert {r.content_sha256 for r in refs} <= real_hashes and fake_doc04 not in {
        r.content_sha256 for r in refs
    }
    # El DOC-04 es cita per apartats, no amb una cita generica.
    doc04_sections = {r.location_label for r in refs if r.content_sha256 == DOC04_SHA}
    assert doc04_sections == {f"DOC-04 §{n}" for n in range(1, 13)}, doc04_sections
    assert all(r.location_label for r in refs)
    quoted = [r for r in refs if r.quote]
    assert quoted and all(len(r.quote) < 120 for r in quoted), "cites breus, no paragrafs"
    for link_model, column, entities in (
        (TenderBlockSourceRefLink, TenderBlockSourceRefLink.block_id, blocks.values()),
        (TenderSiteSourceRefLink, TenderSiteSourceRefLink.site_id, sites),
        (TenderScopeItemSourceRefLink, TenderScopeItemSourceRefLink.scope_item_id, items),
        (TenderContractFactSourceRefLink, TenderContractFactSourceRefLink.fact_id, facts),
        (
            RequirementCapabilityNeedSourceRefLink,
            RequirementCapabilityNeedSourceRefLink.need_id,
            needs,
        ),
        (RequirementSourceRefLink, RequirementSourceRefLink.requirement_id, rq.values()),
        (
            AwardCriterionSourceRefLink,
            AwardCriterionSourceRefLink.award_criterion_id,
            by_code.values(),
        ),
    ):
        cited = set(await db.scalars(sa.select(column).distinct()))
        assert {e.id for e in entities} <= cited, f"{link_model.__tablename__}: entitats sense cita"

    # --- Els vuit fitxers del paquet inicial (amb el fals DOC-04) i el DOC-04 real.
    documents = list(await db.scalars(sa.select(TenderDocument)))
    assert len(documents) == 9
    real = [d for d in documents if "DOC-04" in (d.name or "")]
    assert [d.name for d in real] == ["Annex mestre d'inventaris (DOC-04)"]
    assert any("conte el DOC-03" in (d.name or "") for d in documents), "el fals es conserva"
    assert "DOC-04" not in [m["code"] for m in dataset["missing_documents"]]
    assert not any("DOC-04" in w for w in dataset["withheld"]["because_documents_are_missing"])

    # --- Cap fila del costat empresa, cap taula de 0051, cap proposta.
    tables = await _table_names(db)
    for name in NOT_YET:
        assert name not in tables, f"{name} no hauria d'existir encara"
    for name in DERIVED_0051:
        assert name in tables and await _rows(db, name) == 0, f"{name}: el gold no analitza"
    company_side = [t for t in tables if t.startswith(COMPANY_SIDE_PREFIXES) or "proposal" in t]
    assert company_side, "la llista de taules del costat empresa no pot ser buida"
    assert {t: await _rows(db, t) for t in company_side} == {t: 0 for t in company_side}
    assert await _count(db, Company) == 0
