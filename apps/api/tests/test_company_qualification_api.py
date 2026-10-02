"""Qualificacio i mitjans com a coneixement actual (0052): API, base de dades i anàlisi.

* MODEL: CHECK i FK de les columnes noves (fets quantitatius, equips, origen).
* CONEIXEMENT: verificat nomes amb evidencia, edicio material -> pendent, validacio
  de perfils, abast i calibratge, equips declarats pel servei de coneixement.
* ANALISI: R1/R2/R5/R4/R3/C3 llegits de claus estructurades del plec; «no consta» es
  `unknown`; una oferta historica no es cap referencia d'execucio; la run caduca
  nomes amb un canvi material.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.analysis.registry import ExplanationCode as X
from app.analysis.registry import QuestionCode as Q
from app.knowledge import vocabulary
from app.models.credentials import CompanyInsurance, CompanyInsuranceLimit, CompanyRegistration
from app.models.enums import (
    AwardCriterionType,
    AwardEvaluationType,
    EquipmentType,
    FindingType,
    HistoricBidDecision,
    HistoricBidResult,
    InsuranceLimitKind,
    NeedComparator,
    NeedType,
    OperatingFactKey,
    RequirementCategory,
    RequirementObligation,
    RequirementTiming,
    TerritoryCompleteness,
)
from app.models.knowledge import CompanyOperatingFact, HistoricBid
from app.models.people import CompanyPerson
from app.models.profile import CompanyFinancialYear
from app.models.projects import CompanyProject
from app.models.requirements import RequirementExtractionRun
from app.models.resources import CompanyEquipment
from app.services import tender_analysis as analysis
from app.services import tender_scope as scope
from tests import test_tender_scope_models as m
from tests.fixtures.company_twin import build_company_twin
from tests.test_company_knowledge_permissions_api import _role
from tests.test_company_knowledge_records_api import _link_evidence

pytestmark = pytest.mark.asyncio

COMPANY = "/api/v1/companies/{company}"
TODAY = date.today()


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.commit()


def _code(response) -> str:
    return response.json()["error"]["code"]


async def _snapshot(client, company) -> dict:
    response = await client.get(f"{COMPANY.format(company=company.id)}/knowledge/snapshot")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# MODEL                                                                         #
# --------------------------------------------------------------------------- #


async def _rejected(db, statement=None, add=None) -> None:
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            if add is not None:
                db.add(add)
                await db.flush()
            else:
                await db.execute(statement)


async def test_the_database_enforces_the_new_shapes(db, tenant_a):
    _, company = tenant_a
    # Un nombre nomes als fets quantitatius, i mai negatiu.
    await _rejected(db, add=CompanyOperatingFact(
        company_id=company.id, key=OperatingFactKey.SUPPORT_24X7, value=True,
        value_number=Decimal(3),
    ))  # fmt: skip
    await _rejected(db, add=CompanyOperatingFact(
        company_id=company.id, key=OperatingFactKey.IPV4_ASSIGNABLE_COUNT, value=True,
        value_number=Decimal(-1),
    ))  # fmt: skip
    # Una data de calibratge demana `calibration_required = true`; com a minim una unitat.
    await _rejected(db, add=CompanyEquipment(
        company_id=company.id, equipment_type=EquipmentType.OTDR,
        calibration_valid_until=TODAY,
    ))  # fmt: skip
    await _rejected(db, add=CompanyEquipment(
        company_id=company.id, equipment_type=EquipmentType.OTDR, quantity=0,
    ))  # fmt: skip
    ok = CompanyEquipment(
        company_id=company.id, equipment_type=EquipmentType.OLTS, serial_reference="S-1"
    )
    db.add(ok)
    await db.flush()
    # Unicitat de serie per empresa; enums tancats (CHECK) a PostgreSQL i a SQLite.
    await _rejected(db, add=CompanyEquipment(
        company_id=company.id, equipment_type=EquipmentType.OTDR, serial_reference="S-1",
    ))  # fmt: skip
    sql_id, value = (
        ("CAST(:id AS uuid)", str(ok.id))
        if db.bind.dialect.name == "postgresql"
        else (":id", ok.id.hex)
    )
    for column, bad in (
        ("equipment_type", "laser"),
        ("access_mode", "borrowed"),
        ("verification_status", "trusted"),
        ("lifecycle", "eternal"),
    ):
        await _rejected(
            db,
            sa.text(f"UPDATE company_equipment SET {column} = :v WHERE id = {sql_id}").bindparams(
                v=bad, id=value
            ),
        )
    year = CompanyFinancialYear(company_id=company.id, year=2024, revenue=Decimal("1.50"))
    db.add(year)
    await db.flush()
    assert (year.knowledge_origin, year.verification_status, year.lifecycle) == (
        "declared",
        "pending_verification",
        "current",
    )
    assert year.revenue == Decimal("1.50"), "imports Decimal, no float"
    year_sql, year_id = (
        ("CAST(:id AS uuid)", str(year.id))
        if db.bind.dialect.name == "postgresql"
        else (":id", year.id.hex)
    )
    await _rejected(
        db,
        sa.text(
            f"UPDATE company_financial_years SET knowledge_origin = 'guessed' WHERE id = {year_sql}"
        ).bindparams(id=year_id),
    )
    await db.rollback()


# --------------------------------------------------------------------------- #
# CONEIXEMENT                                                                   #
# --------------------------------------------------------------------------- #


async def test_a_financial_year_is_verified_only_with_evidence_and_unverified_by_edits(
    client_a, tenant_a
):
    _, company = tenant_a
    base = f"{COMPANY.format(company=company.id)}/financial-years"
    refused = await client_a.post(
        base, json={"year": 2025, "revenue": "150000", "verification_status": "verified"}
    )
    # Una alta no porta verificacio: la unica porta es `/verify`.
    assert refused.status_code == 422 and _code(refused) != "evidence_required"
    created = await client_a.post(base, json={"year": 2025, "revenue": "150000"})
    assert created.status_code == 201, created.text
    year = created.json()
    assert (year["knowledge_origin"], year["verification_status"], year["lifecycle"]) == (
        "declared",
        "pending_verification",
        "current",
    )
    records = f"{COMPANY.format(company=company.id)}/knowledge/records/financial_year"
    no_evidence = await client_a.post(f"{records}/{year['id']}/verify")
    assert no_evidence.status_code == 422 and _code(no_evidence) == "evidence_required"
    await _link_evidence(client_a, company, "company_financial_year", year["id"])
    by_patch = await client_a.patch(
        f"{base}/{year['id']}", json={"verification_status": "verified"}
    )
    assert by_patch.status_code == 422, "PATCH no verifica: nomes `/verify`"
    assert (await client_a.post(f"{records}/{year['id']}/verify")).status_code == 200
    verified = await client_a.get(f"{base}/{year['id']}")
    assert verified.json()["verification_status"] == "verified"
    # Una nota no canvia el que s'afirma; l'import, si.
    noted = await client_a.patch(f"{base}/{year['id']}", json={"notes": "comptes dipositats"})
    assert noted.json()["verification_status"] == "verified"
    edited = await client_a.patch(f"{base}/{year['id']}", json={"revenue": "150001"})
    assert edited.json()["verification_status"] == "pending_verification"
    snapshot = await _snapshot(client_a, company)
    assert [e["key"] for e in snapshot["financial_years"]] == ["2025"]


async def test_profiles_scopes_and_types_come_from_the_vocabulary(client_a, tenant_a):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    unknown_profile = await client_a.post(
        f"{base}/people", json={"first_name": "Persona", "profile_codes": ["astronaut"]}
    )
    assert unknown_profile.status_code == 422
    person = await client_a.post(
        f"{base}/people",
        json={"first_name": "Persona", "profile_codes": ["voip_specialist", "project_manager"]},
    )
    assert person.status_code == 201, person.text
    assert person.json()["profile_codes"] == ["project_manager", "voip_specialist"]
    bad_scope = await client_a.post(
        f"{base}/registrations",
        json={"name": "Registre", "type": "sector_operator_registry", "scope_service_lines": ["x"]},
    )
    assert bad_scope.status_code == 422
    registration = await client_a.post(
        f"{base}/registrations",
        json={
            "name": "Registre d'operadors",
            "type": "sector_operator_registry",
            "issuer": "Un regulador",
            "scope_service_lines": ["voice_trunk", "access_ftth"],
        },
    )
    assert registration.status_code == 201, registration.text
    assert registration.json()["scope_service_lines"] == ["access_ftth", "voice_trunk"]
    vocabulary_read = (await client_a.get(f"{base}/knowledge/vocabulary")).json()
    assert {p["code"] for p in vocabulary_read["professional_profiles"]} == (
        vocabulary.PROFESSIONAL_PROFILE_CODES
    )
    assert {t["code"] for t in vocabulary_read["equipment_types"]} == {
        t.value for t in EquipmentType
    }
    snapshot = await _snapshot(client_a, company)
    assert {e["key"] for e in snapshot["profiles"]} == {"project_manager", "voip_specialist"}
    assert [e["key"] for e in snapshot["registrations"]] == ["sector_operator_registry"]


async def test_equipment_is_declared_verified_and_unverified_like_any_knowledge(client_a, tenant_a):
    _, company = tenant_a
    records = f"{COMPANY.format(company=company.id)}/knowledge/records"
    inconsistent = await client_a.post(
        f"{records}/equipment",
        json={"equipment_type": "otdr", "calibration_valid_until": TODAY.isoformat()},
    )
    assert inconsistent.status_code == 422
    created = await client_a.post(
        f"{records}/equipment",
        json={
            "equipment_type": "otdr",
            "access_mode": "owned",
            "serial_reference": "OTDR-1",
            "calibration_required": True,
            "calibration_valid_until": (TODAY + timedelta(days=30)).isoformat(),
            "verification_status": "verified",
        },
    )
    assert created.status_code == 422, "una declaracio no porta verificacio"
    created = await client_a.post(
        f"{records}/equipment",
        json={
            "equipment_type": "otdr",
            "access_mode": "owned",
            "serial_reference": "OTDR-1",
            "calibration_required": True,
            "calibration_valid_until": (TODAY + timedelta(days=30)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    equipment_id = created.json()["id"]
    assert created.json()["knowledge_origin"] == "declared"
    refused = await client_a.post(f"{records}/equipment/{equipment_id}/verify")
    assert refused.status_code == 422 and _code(refused) == "evidence_required"
    await _link_evidence(client_a, company, "company_equipment", equipment_id)
    assert (await client_a.post(f"{records}/equipment/{equipment_id}/verify")).status_code == 200
    entry = (await _snapshot(client_a, company))["equipment"][0]
    assert (entry["key"], entry["source"]) == ("otdr", "current_verified")
    # Renovar el calibratge es un canvi material: cal tornar a verificar.
    await client_a.patch(
        f"{records}/equipment/{equipment_id}",
        json={"calibration_valid_until": (TODAY + timedelta(days=400)).isoformat()},
    )
    entry = (await _snapshot(client_a, company))["equipment"][0]
    assert entry["source"] == "current_declared"
    listed = (await client_a.get(records)).json()
    assert [e["serial_reference"] for e in listed["equipment"]] == ["OTDR-1"]


async def test_a_quantitative_fact_carries_its_number_and_a_boolean_one_does_not(
    client_a, tenant_a
):
    _, company = tenant_a
    facts = f"{COMPANY.format(company=company.id)}/knowledge/records/operating_fact"
    without = await client_a.post(facts, json={"key": "ipv4_assignable_count", "value": True})
    assert without.status_code == 422
    boolean = await client_a.post(
        facts, json={"key": "support_24x7", "value": True, "value_number": 2}
    )
    assert boolean.status_code == 422
    created = await client_a.post(
        facts, json={"key": "ipv4_assignable_count", "value": True, "value_number": 20}
    )
    assert created.status_code == 201, created.text
    assert Decimal(str(created.json()["value_number"])) == 20
    claim = next(
        e
        for e in (await _snapshot(client_a, company))["facts"]
        if e["key"] == "ipv4_assignable_count"
    )["claims"][0]
    assert claim["value"] is True


async def test_children_changes_unverify_the_policy_and_the_project(client_a, tenant_a, db):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    policy = (await client_a.post(f"{base}/insurances", json={"type": "civil_liability"})).json()
    await _link_evidence(client_a, company, "company_insurance", policy["id"])
    verify = f"{base}/knowledge/records/insurance/{policy['id']}/verify"
    assert (await client_a.post(verify)).status_code == 200
    limit = await client_a.post(
        f"{base}/insurances/{policy['id']}/limits",
        json={"kind": "employer_per_victim", "amount": "150000"},
    )
    assert limit.status_code == 201, limit.text
    policy = (await client_a.get(f"{base}/insurances/{policy['id']}")).json()
    assert policy["verification_status"] == "pending_verification"

    services = [
        (
            await client_a.post(f"{base}/services", json={"name": n, "service_line_code": line})
        ).json()
        for n, line in (("Xarxa", "dedicated_network"), ("Veu", "voice_pbx"))
    ]
    project = (
        await client_a.post(
            f"{base}/projects",
            json={"name": "Referencia", "amount": "50000", "service_ids": [services[0]["id"]]},
        )
    ).json()
    await _link_evidence(client_a, company, "company_project", project["id"])
    verify = f"{base}/knowledge/records/project/{project['id']}/verify"
    assert (await client_a.post(verify)).status_code == 200
    # Els serveis del cataleg son legacy des de 0053: canviar-los no desverifica res.
    legacy = await client_a.patch(
        f"{base}/projects/{project['id']}", json={"service_ids": [services[1]["id"]]}
    )
    assert legacy.json()["verification_status"] == "verified"
    assert legacy.json()["service_line_codes"] == []
    # Les linies acreditades si.
    moved = await client_a.patch(
        f"{base}/projects/{project['id']}", json={"service_line_codes": ["voice_pbx"]}
    )
    assert moved.json()["verification_status"] == "pending_verification"


async def test_a_viewer_cannot_write_qualification_knowledge(client_a, tenant_a, db):
    user, company = tenant_a
    await _role(db, user, company, "viewer")
    base = COMPANY.format(company=company.id)
    for path, payload in (
        ("financial-years", {"year": 2024, "revenue": "1"}),
        ("people", {"first_name": "Persona"}),
        ("registrations", {"name": "Registre"}),
        ("insurances", {"type": "civil_liability"}),
        ("projects", {"name": "Referencia"}),
        ("knowledge/records/equipment", {"equipment_type": "otdr"}),
    ):
        response = await client_a.post(f"{base}/{path}", json=payload)
        assert response.status_code == 403, (path, response.text)


# --------------------------------------------------------------------------- #
# ANALISI                                                                       #
# --------------------------------------------------------------------------- #

RC_LIMITS = {"per_claim": "300000", "annual_aggregate": "300000", "employer_per_victim": "150000"}


async def _requirement(db, tender, title, category, value_json, timing, proof):
    r = await m._requirement(db, tender, title=title)
    r.category = category
    r.obligation = RequirementObligation.MANDATORY
    r.value_json = value_json
    r.timing = timing
    scope.set_proof(r, proof)
    await scope.set_territory_completeness(db, r, TerritoryCompleteness.NOT_APPLICABLE)
    await db.flush()
    return r


async def _qualification_tender(db):
    """Un plec sintetic amb els sis llindars de qualificacio, llegits amb claus globals."""
    tender = await m._public_tender(db, title="Plec sintetic de qualificacio")
    # Termini de presentacio: la data de referencia de les finestres (mai el rellotge).
    tender.deadline_at = datetime(TODAY.year, 3, 1, 12, tzinfo=UTC)
    c3 = await m._criterion(db, tender, name="C3 IPv4", points=2.0)
    c3.criterion_type = AwardCriterionType.IMPROVEMENT
    c3.evaluation_type = AwardEvaluationType.AUTOMATIC
    c3.timing = RequirementTiming.AT_SUBMISSION
    c3.tranches = [{"min": 15, "points": 1}, {"min": 20, "points": 2}]
    run = await db.get(RequirementExtractionRun, c3.run_id)
    run.is_current = True
    sub, form = RequirementTiming.AT_SUBMISSION, RequirementTiming.BEFORE_FORMALIZATION
    reqs = {
        "turnover": await _requirement(
            db, tender, "Volum de negoci", RequirementCategory.ECONOMIC_SOLVENCY,
            {"turnover_min_eur": "91904.74", "years": 3}, sub,
            [{"kind": "annual_accounts", "text": "Comptes", "mandatory": True}],
        ),
        "execution": await _requirement(
            db, tender, "Serveis similars", RequirementCategory.TECHNICAL_SOLVENCY,
            {"execution_min_eur": "42888.88", "years": 3,
             "service_lines": ["dedicated_network", "voice_pbx"]}, sub,
            [{"kind": "execution_certificates", "text": "Certificats", "mandatory": True}],
        ),
        "insurance": await _requirement(
            db, tender, "RC", RequirementCategory.INSURANCE,
            {"insurance_type": "civil_liability", "insurance_limits_eur": RC_LIMITS}, form,
            [{"kind": "insurance_policy", "text": "Polissa", "mandatory": True}],
        ),
        "registration": await _requirement(
            db, tender, "Operador", RequirementCategory.REGISTRATION,
            {"registration_type": "sector_operator_registry", "registration_issuer": "CNMC",
             "registration_service_lines": ["voice_trunk", "access_ftth", "dedicated_network"],
             "operating_fact": "telecom_operator_cnmc"}, sub, [],
        ),
        "resources": await _requirement(
            db, tender, "Adscripcio de mitjans", RequirementCategory.PERSONNEL,
            {"required_profiles": {"project_manager": 1, "network_security_specialist": 1,
                                   "voip_specialist": 1, "fiber_certification_specialist": 1},
             "required_equipment": {"otdr": 1, "olts": 1}, "calibration_required": True},
            sub, [],
        ),
        "ipv4": await _requirement(
            db, tender, "IPv4 fixes", RequirementCategory.TECHNICAL_REQUIREMENT,
            {"ipv4_fixed": 10}, RequirementTiming.AT_EXECUTION, [],
        ),
    }  # fmt: skip
    for count, need_type, points, criterion in (
        (10, NeedType.MINIMUM, None, None),
        (15, NeedType.SCORED_IMPROVEMENT, 1, c3.id),
        (20, NeedType.SCORED_IMPROVEMENT, 2, c3.id),
    ):
        await scope.create_need(db, tender, reqs["ipv4"], {
            "capability_code": None, "unmapped_capability": "ipv4_fixes_assignables",
            "comparator": NeedComparator.GTE,
            "value_json": {"operating_fact": "ipv4_assignable_count", "count": count},
            "value_schema": "v1", "need_type": need_type, "points": points,
            "award_criterion_id": criterion,
        })  # fmt: skip
    await scope.set_award_links(db, tender, reqs["ipv4"], [c3.id])
    await db.flush()
    return tender, {k: r.id for k, r in reqs.items()}, c3.id


async def _analyze(db, company, tender_id):
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender_id)).run
    await db.commit()
    await db.refresh(run)
    return run


def _fits(run, ids):
    by_id = {a.requirement_id: a for a in run.fit_assessments}
    return {key: by_id[rid] for key, rid in ids.items()}


def _findings(run, requirement_id) -> set[tuple[str, str]]:
    return {
        (str(f.finding_type), f.explanation_code)
        for f in run.findings
        if f.requirement_id == requirement_id
    }


async def _qualified_twin(db, client, company):
    rows = await build_company_twin(db, company)
    q = rows["qualification"]
    ids = {
        "best_year": q["years"][max(q["years"])].id,
        "network": q["projects"]["network"].id,
        "pbx": q["projects"]["pbx"].id,
        "insurance": q["insurance"].id,
        "registration": q["registration"].id,
        "otdr": q["equipment"]["otdr"].id,
        "ipv4": q["ipv4"].id,
        "years": {y: row.id for y, row in q["years"].items()},
    }
    await db.commit()
    for entity_type, key in (
        ("company_financial_year", "best_year"),
        ("company_project", "network"),
        ("company_project", "pbx"),
        ("company_insurance", "insurance"),
    ):
        await _link_evidence(client, company, entity_type, ids[key])
    await db.commit()
    return ids


async def test_known_sufficient_qualification_is_declared_or_verified(db, tenant_a, client_a):
    _, company = tenant_a
    tender, req, c3 = await _qualification_tender(db)
    tender_id = tender.id
    ids = await _qualified_twin(db, client_a, company)
    run = await _analyze(db, company, tender_id)
    fits = _fits(run, req)
    for key, explanation in (
        ("turnover", X.TURNOVER_MET),
        ("execution", X.EXECUTION_MET),
        ("insurance", X.INSURANCE_MET),
        ("resources", X.RESOURCES_MET),
    ):
        fit = fits[key]
        assert (fit.fit_result, fit.knowledge_source, fit.verification_status) == (
            "satisfies",
            "current_declared",
            "not_verified",
        ), key
        assert fit.explanation_code == explanation, key
    assert fits["turnover"].proof_status == "sufficient"
    # Any de major execucio: Y-2 = 20.000 (xarxa, desglossament anual) + 30.000 (centraleta,
    # d'un sol any). El total plurianual de la xarxa no s'imputa mai al seu darrer any.
    assert set(fits["execution"].support_json["record_ids"]) == {
        str(ids["network"]),
        str(ids["pbx"]),
    }
    assert fits["execution"].support_json["reference_date"] == f"{TODAY.year}-03-01"
    # Abast parcial (2 de 3 linies): unknown + revisio + pregunta, mai bloqueig.
    assert fits["registration"].explanation_code == X.REGISTRATION_SCOPE_NOT_COVERED
    assert ("review_required", X.REGISTRATION_SCOPE_NOT_COVERED) in _findings(
        run, req["registration"]
    )
    assert any(q.question_code == Q.COMPANY_REVIEW_REGISTRATION_SCOPE for q in run.questions)
    assert fits["ipv4"].fit_result == "satisfies"
    c3_out = next(c for c in run.criterion_assessments if c.award_criterion_id == c3)
    assert (c3_out.at_risk_points, c3_out.secured_points) == (2.0, 0.0)
    assert not any(f.finding_type == FindingType.BLOCKER_POTENTIAL for f in run.findings)

    # Verificar (amb evidencia) passa a `current_verified`; les IPv4 verificades asseguren C3.
    records = f"{COMPANY.format(company=company.id)}/knowledge/records"
    assert (
        await client_a.post(f"{records}/financial_year/{ids['best_year']}/verify")
    ).status_code == 200
    await _link_evidence(client_a, company, "company_operating_fact", ids["ipv4"])
    assert (
        await client_a.post(f"{records}/operating_fact/{ids['ipv4']}/verify")
    ).status_code == 200
    # Abast complet: el registre satisfà.
    registration = await db.get(CompanyRegistration, ids["registration"])
    registration.scope_service_lines = ["access_ftth", "dedicated_network", "voice_trunk"]
    await db.commit()
    run = await _analyze(db, company, tender_id)
    fits = _fits(run, req)
    assert (fits["turnover"].knowledge_source, fits["turnover"].verification_status) == (
        "current_verified",
        "verified",
    )
    assert fits["registration"].explanation_code == X.REGISTRATION_MET
    c3_out = next(c for c in run.criterion_assessments if c.award_criterion_id == c3)
    assert (c3_out.points_status, c3_out.secured_points) == ("secured", 2.0)


async def test_known_insufficient_blocks_only_when_due_at_submission(db, tenant_a, client_a):
    _, company = tenant_a
    tender, req, c3 = await _qualification_tender(db)
    tender_id = tender.id
    ids = await _qualified_twin(db, client_a, company)
    for year_id in ids["years"].values():
        (await db.get(CompanyFinancialYear, year_id)).revenue = Decimal("10000")
    limit = await db.scalar(
        sa.select(CompanyInsuranceLimit).where(
            CompanyInsuranceLimit.insurance_id == ids["insurance"],
            CompanyInsuranceLimit.kind == InsuranceLimitKind.EMPLOYER_PER_VICTIM,
        )
    )
    limit.amount = Decimal("100000")
    (await db.get(CompanyEquipment, ids["otdr"])).calibration_valid_until = TODAY - timedelta(
        days=1
    )
    (await db.get(CompanyOperatingFact, ids["ipv4"])).value_number = Decimal("12")
    await db.commit()
    run = await _analyze(db, company, tender_id)
    fits = _fits(run, req)
    # Volum per sota en TOTS els exercicis, a la presentacio: bloqueig potencial.
    assert (fits["turnover"].fit_result, fits["turnover"].explanation_code) == (
        "does_not_satisfy",
        X.TURNOVER_BELOW,
    )
    assert ("blocker_potential", X.MANDATORY_DOES_NOT_SATISFY) in _findings(run, req["turnover"])
    # RC patronal per sota, exigida abans de formalitzar: revisio, no bloqueig.
    assert fits["insurance"].explanation_code == X.INSURANCE_BELOW
    assert ("review_required", X.MANDATORY_DOES_NOT_SATISFY_LATER) in _findings(
        run, req["insurance"]
    )
    assert not any(t == "blocker_potential" for t, _ in _findings(run, req["insurance"]))
    # Calibratge caducat de l'OTDR: incompatibilitat actual + pregunta de renovacio.
    assert fits["resources"].explanation_code == X.EQUIPMENT_CALIBRATION_EXPIRED
    assert ("blocker_potential", X.MANDATORY_DOES_NOT_SATISFY) in _findings(run, req["resources"])
    assert any(q.question_code == Q.COMPANY_RENEW_CALIBRATION for q in run.questions)
    # 12 IPv4: minim de 10 cobert; els trams de 15 i 20 queden oberts, no perduts.
    assert fits["ipv4"].fit_result == "satisfies"
    c3_out = next(c for c in run.criterion_assessments if c.award_criterion_id == c3)
    assert (c3_out.at_risk_points, c3_out.lost_points) == (2.0, 0.0)


async def test_unknown_is_never_does_not_satisfy(db, tenant_a, client_a):
    """Un exercici que falta, una polissa caducada, una prova sense document, una oferta
    historica en lloc d'una referencia: tot `unknown`, cap bloqueig."""
    _, company = tenant_a
    tender, req, _ = await _qualification_tender(db)
    tender_id = tender.id
    rows = await build_company_twin(db, company)
    q = rows["qualification"]
    # Dos exercicis per sota del llindar i el tercer que no consta: no se sap.
    oldest = min(q["years"])
    await db.delete(q["years"][oldest])
    for year, row in q["years"].items():
        if year != oldest:
            row.revenue = Decimal("10000")
    policy = q["insurance"]
    policy.starts_at = TODAY - timedelta(days=400)
    policy.expires_at = TODAY - timedelta(days=1)
    for project in q["projects"].values():
        await db.delete(project)
    db.add(
        HistoricBid(
            company_id=company.id,
            reference="Oferta guanyada d'un altre expedient",
            base_budget=Decimal("900000"),
            service_line_codes=["dedicated_network"],
            bid_date=date(TODAY.year - 1, 5, 1),
            decision=HistoricBidDecision.SUBMITTED,
            result=HistoricBidResult.WON,
        )
    )
    (await db.get(CompanyEquipment, q["equipment"]["olts"].id)).calibration_valid_until = None
    await db.commit()
    run = await _analyze(db, company, tender_id)
    fits = _fits(run, req)
    assert (fits["turnover"].fit_result, fits["turnover"].explanation_code) == (
        "unknown",
        X.TURNOVER_UNKNOWN,
    )
    assert (fits["insurance"].fit_result, fits["insurance"].explanation_code) == (
        "unknown",
        X.INSURANCE_EXPIRED,
    )
    assert (fits["execution"].fit_result, fits["execution"].explanation_code) == (
        "unknown",
        X.EXECUTION_UNKNOWN,
    ), "una oferta historica no es cap referencia d'execucio"
    assert fits["resources"].explanation_code == X.EQUIPMENT_CALIBRATION_UNKNOWN
    for key in ("turnover", "insurance", "execution", "resources"):
        found = _findings(run, req[key])
        assert ("missing_information", X.DATA_MISSING) in found, key
        assert not any(t == "blocker_potential" for t, _ in found), key
    assert any(
        q_.question_code == Q.COMPANY_PROVIDE_PROFILE_DATA and q_.requirement_id == req["turnover"]
        for q_ in run.questions
    )


async def test_declared_without_evidence_is_unknown_with_an_evidence_gap(db, tenant_a):
    _, company = tenant_a
    tender, req, _ = await _qualification_tender(db)
    tender_id = tender.id
    await build_company_twin(db, company)
    await db.commit()
    run = await _analyze(db, company, tender_id)
    fit = _fits(run, req)["turnover"]
    assert (fit.fit_result, fit.proof_status, fit.explanation_code) == (
        "unknown",
        "missing",
        X.PROOF_MISSING_FOR_CLAIM,
    )
    assert ("evidence_gap", X.PROOF_MISSING) in _findings(run, req["turnover"])
    assert any(
        q.question_code == Q.COMPANY_PROVIDE_PROOF and q.requirement_id == req["turnover"]
        for q in run.questions
    )


async def test_a_run_goes_stale_only_on_a_material_qualification_change(db, tenant_a, client_a):
    _, company = tenant_a
    tender, _, _ = await _qualification_tender(db)
    tender_id = tender.id
    ids = await _qualified_twin(db, client_a, company)
    run = await _analyze(db, company, tender_id)
    # Una nota no es material.
    (await db.get(CompanyFinancialYear, ids["best_year"])).notes = "comptes dipositats"
    await db.commit()
    assert await analysis.is_stale(db, run) is False
    # Un limit de la polissa si.
    limit = await db.scalar(
        sa.select(CompanyInsuranceLimit).where(
            CompanyInsuranceLimit.insurance_id == ids["insurance"]
        )
    )
    limit.amount += 1
    await db.commit()
    assert await analysis.is_stale(db, run) is True
    run = await _analyze(db, company, tender_id)
    # Un perfil d'una persona, una referencia nova, un equip retirat: tots materials.
    person = await db.scalar(sa.select(CompanyPerson).where(CompanyPerson.company_id == company.id))
    person.profile_codes = sorted({*(person.profile_codes or []), "field_technician"})
    await db.commit()
    assert await analysis.is_stale(db, run) is True
    run = await _analyze(db, company, tender_id)
    db.add(
        CompanyProject(
            company_id=company.id, name="Una altra referencia", amount=Decimal("1000"),
            end_date=date(TODAY.year - 1, 1, 1),
        )
    )  # fmt: skip
    await db.commit()
    assert await analysis.is_stale(db, run) is True
    run = await _analyze(db, company, tender_id)
    (await db.get(CompanyEquipment, ids["otdr"])).lifecycle = "retired"
    await db.commit()
    assert await analysis.is_stale(db, run) is True
    # La polissa tambe compta per tipus: una de nova d'un altre tipus canvia l'empremta.
    run = await _analyze(db, company, tender_id)
    db.add(CompanyInsurance(company_id=company.id, type="professional_indemnity"))
    await db.commit()
    assert await analysis.is_stale(db, run) is True
    assert uuid.UUID(str(run.id))


async def test_annual_amounts_fit_the_project_and_changing_them_unverifies_it(client_a, tenant_a):
    _, company = tenant_a
    base = f"{COMPANY.format(company=company.id)}/projects"
    project = {"name": "Plurianual", "amount": "90000", "start_date": "2023-02-01",
               "end_date": "2025-11-30"}  # fmt: skip
    outside = await client_a.post(
        base, json={**project, "annual_amounts": [{"year": 2022, "amount": "10"}]}
    )
    assert outside.status_code == 422, "cap any fora de les dates del projecte"
    over = await client_a.post(
        base, json={**project, "annual_amounts": [{"year": 2024, "amount": "90001"}]}
    )
    assert over.status_code == 422, "el desglossament no pot superar l'import"
    twice = await client_a.post(
        base,
        json={**project, "annual_amounts": [{"year": 2024, "amount": "1"},
                                            {"year": 2024, "amount": "2"}]},
    )  # fmt: skip
    assert twice.status_code == 422, "un import per any"
    created = await client_a.post(
        base,
        json={**project, "annual_amounts": [{"year": 2025, "amount": "50000"},
                                            {"year": 2024, "amount": "40000"}]},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    body = created.json()
    assert [(a["year"], Decimal(str(a["amount"]))) for a in body["annual_amounts"]] == [
        (2024, Decimal("40000")),
        (2025, Decimal("50000")),
    ]
    assert body["as_of"] is None, "cap data inventada"
    await _link_evidence(client_a, company, "company_project", body["id"])
    records = f"{COMPANY.format(company=company.id)}/knowledge/records"
    assert (await client_a.post(f"{records}/project/{body['id']}/verify")).status_code == 200
    same = await client_a.patch(
        f"{base}/{body['id']}",
        json={"annual_amounts": [{"year": 2024, "amount": "40000"},
                                 {"year": 2025, "amount": "50000"}]},
    )  # fmt: skip
    assert same.json()["verification_status"] == "verified", "el mateix desglossament no canvia res"
    moved = await client_a.patch(
        f"{base}/{body['id']}", json={"annual_amounts": [{"year": 2025, "amount": "90000"}]}
    )
    assert moved.json()["verification_status"] == "pending_verification"
    shrunk = await client_a.patch(f"{base}/{body['id']}", json={"amount": "1000"})
    assert shrunk.status_code == 422, "l'import no pot quedar per sota del desglossament"
