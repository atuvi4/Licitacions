"""Estructura del plec (0048) per l'API: metadades del contracte, blocs, seus i
a que aplica cada requisit o criteri.

El que es protegeix:

* **retrocompatibilitat**: una licitacio que no en diu res llegeix `null` a les
  quatre columnes, `timing = unknown` i llistes buides; cap lectura antiga canvia;
* **anada i tornada** de metadades (tri-estat), blocs i seus, amb les regles del
  model (codi unic per expedient, lot del mateix expedient, diners en decimal);
* **N:M amb blocs**: cap enllac = tot el contracte; un conjunt que es substitueix;
  un bloc d'una altra licitacio no entra; **els punts no es reparteixen**;
* **temporalitat** sense cap consequencia de compliment;
* **les mateixes autoritzacions** que la resta de dades estructurades d'una
  licitacio oberta a ma: ni mes, ni menys;
* **cites** amb el patro de `tender_source_refs`;
* el **gold sintetic** del lock, i que res d'aixo crea cap element de 0049/0050.

Dades sintetiques: cap expedient, organisme ni empresa real.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.db.base import Base
from app.models.audit import AuditLog
from app.models.enums import AnalysisRunStatus, MembershipRole, RequirementCategory, SourceType
from app.models.identity import CompanyMembership
from app.models.provenance import TenderSourceRef
from app.models.requirements import AwardCriterion, Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tender_lifecycle import TenderRevision
from app.models.tender_structure import (
    AwardCriterionBlock,
    RequirementBlock,
    TenderBlock,
    TenderBlockSourceRefLink,
    TenderSite,
)
from app.models.tenders import Tender, TenderLot

pytestmark = pytest.mark.asyncio

COMPANY = "/api/v1/companies/{company}"
TENDERS = COMPANY + "/tenders"
T = TENDERS + "/{tender}"
STRUCTURE = T + "/structure"
BLOCKS = T + "/blocks"
SITES = T + "/sites"
REQUIREMENTS = T + "/requirements"
REQ_STRUCTURE = REQUIREMENTS + "/{requirement}/structure"
CRIT_STRUCTURE = T + "/award-criteria/{criterion}/structure"
FILE_REF = T + "/source-refs/document-file"
DETAIL = "/api/v1/tenders/{tender}"
ANALYSIS = "/api/v1/tenders/{tender}/analysis/requirements"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_BYTES = b"PK\x03\x04" + b"\x01" * 64

#: Taules que 0050 encara no ha creat: res del que fa l'estructura les pot necessitar.
#: (Les de 0049 —abast, territoris, necessitats, fets— ja existeixen i son del plec.)
NOT_YET = (
    "tender_analysis_runs",
    "analysis_runs",
    "analysis_findings",
    "findings",
    "assessments",
    "delivery_candidates",
)


def _code(response) -> str:
    return response.json()["error"]["code"]


async def _tender(client, company, title="Serveis de comunicacions") -> str:
    response = await client.post(
        TENDERS.format(company=company.id), json={"client_token": str(uuid.uuid4()), "title": title}
    )
    assert response.status_code == 201, response.text
    return response.json()["tender"]["id"]


async def _requirement(
    client, company, tender_id, title="Assegurança de responsabilitat civil"
) -> dict:
    response = await client.post(
        REQUIREMENTS.format(company=company.id, tender=tender_id),
        json={
            "title": title,
            "category": "insurance",
            "obligation": "mandatory",
            "scope": "tender",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _block(client, company, tender_id, code, **fields) -> dict:
    response = await client.post(
        BLOCKS.format(company=company.id, tender=tender_id),
        json={"code": code, "name": f"Bloc {code}", **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _site(client, company, tender_id, code, role="remote", **fields) -> dict:
    response = await client.post(
        SITES.format(company=company.id, tender=tender_id),
        json={"code": code, "name": f"Seu {code}", "role": role, **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _run(db, tender_id: str) -> RequirementExtractionRun:
    """Una run d'extraccio vigent de debo, per penjar-hi criteris i un requisit antic."""
    run = RequirementExtractionRun(
        tender_id=uuid.UUID(tender_id),
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="v",
        prompt_version="v",
        schema_version="v",
        provider="fake",
        model="fake",
        is_current=True,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _criterion(db, run, name, points) -> AwardCriterion:
    criterion = AwardCriterion(
        run_id=run.id, tender_id=run.tender_id, name=name, points=points, dedup_key=uuid.uuid4().hex
    )
    db.add(criterion)
    await db.commit()
    await db.refresh(criterion)
    return criterion


async def _extracted_requirement(db, run, title="Requisit antic") -> Requirement:
    requirement = Requirement(
        tender_id=run.tender_id,
        run_id=run.id,
        category=RequirementCategory.INSURANCE,
        title=title,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.commit()
    await db.refresh(requirement)
    return requirement


async def _role(db, user, company, role: str) -> None:
    await db.execute(
        sa.update(CompanyMembership)
        .where(CompanyMembership.user_id == user.id, CompanyMembership.company_id == company.id)
        .values(role=MembershipRole(role))
    )
    await db.commit()


async def _file_ref(client, company, tender_id, label="Annex A") -> str:
    uploaded = await client.post(
        T.format(company=company.id, tender=tender_id) + "/documents",
        files={"file": (f"preus-{uuid.uuid4().hex[:4]}.xlsx", XLSX_BYTES, XLSX)},
    )
    assert uploaded.status_code == 201, uploaded.text
    response = await client.post(
        FILE_REF.format(company=company.id, tender=tender_id),
        json={
            "tender_document_file_id": uploaded.json()["file"]["file_id"],
            "location_label": label,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _count(db, model) -> int:
    return await db.scalar(sa.select(sa.func.count()).select_from(model))


# --------------------------------------------------------------------------- #
# Retrocompatibilitat                                                          #
# --------------------------------------------------------------------------- #


async def test_a_tender_without_structure_reads_unknown_everywhere(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    requirement = await _requirement(client_a, company, tender_id)

    detail = (await client_a.get(DETAIL.format(tender=tender_id))).json()
    assert {
        k: detail[k]
        for k in (
            "duration_months",
            "extension_months",
            "regulated_harmonised",
            "planned_start_date",
        )
    } == {
        "duration_months": None,
        "extension_months": None,
        "regulated_harmonised": None,
        "planned_start_date": None,
    }
    assert (detail["blocks"], detail["sites"]) == ([], [])
    # La lectura antiga continua sencera.
    assert (
        detail["title"] == "Serveis de comunicacions"
        and "lots" in detail
        and "traceability" in detail
    )

    structure = (await client_a.get(STRUCTURE.format(company=company.id, tender=tender_id))).json()
    assert structure == {
        "tender_id": tender_id,
        "duration_months": None,
        "extension_months": None,
        "regulated_harmonised": None,
        "planned_start_date": None,
        "blocks": [],
        "sites": [],
        # 0049 hi afegeix l'abast i els fets, buits a les licitacions que no en tenen.
        "scope_items": [],
        "contract_facts": [],
    }

    analysis = (await client_a.get(ANALYSIS.format(tender=tender_id))).json()
    read = next(r for r in analysis["requirements"] if r["id"] == requirement["id"])
    assert (read["timing"], read["block_ids"]) == ("unknown", [])
    assert requirement["timing"] == "unknown" and requirement["block_ids"] == []


# --------------------------------------------------------------------------- #
# Metadades del contracte                                                      #
# --------------------------------------------------------------------------- #


async def test_contract_metadata_round_trips_is_tristate_and_creates_no_revision(
    client_a, tenant_a, db
):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    url = STRUCTURE.format(company=company.id, tender=tender_id)
    revisions = await _count(db, TenderRevision)

    response = await client_a.patch(
        url,
        json={
            "duration_months": 48,
            "extension_months": 12,
            "regulated_harmonised": True,
            "planned_start_date": "2027-01-01",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (
        body["duration_months"],
        body["extension_months"],
        body["regulated_harmonised"],
        body["planned_start_date"],
    ) == (
        48,
        12,
        True,
        "2027-01-01",
    )
    detail = (await client_a.get(DETAIL.format(tender=tender_id))).json()
    assert (detail["duration_months"], detail["regulated_harmonised"]) == (48, True)

    # Tri-estat: `false` es una afirmacio i `null` torna a «no se sap»; ometre no toca.
    assert (await client_a.patch(url, json={"regulated_harmonised": False})).json()[
        "regulated_harmonised"
    ] is False
    assert (await client_a.patch(url, json={"regulated_harmonised": None})).json()[
        "regulated_harmonised"
    ] is None
    assert (await client_a.patch(url, json={"extension_months": 0})).json()["duration_months"] == 48
    assert (await client_a.patch(url, json={})).json()["duration_months"] == 48

    # Mesos negatius: no entren ni per l'esquema.
    assert (await client_a.patch(url, json={"duration_months": -1})).status_code == 422
    assert (await client_a.patch(url, json={"extension_months": -6})).status_code == 422
    assert (await client_a.patch(url, json={"expediente_number": "x"})).status_code == 422

    # Cap revisio de la licitacio: no son el que publica l'organ. Si auditoria.
    assert await _count(db, TenderRevision) == revisions
    summaries = list(
        await db.scalars(
            sa.select(AuditLog.summary).where(
                AuditLog.entity_type == "tender", AuditLog.summary.contains("estructura=")
            )
        )
    )
    assert len(summaries) >= 1 and "duration_months" in summaries[0]


# --------------------------------------------------------------------------- #
# Blocs i seus                                                                 #
# --------------------------------------------------------------------------- #


async def test_blocks_crud_with_the_rules_of_the_model(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    other_id = await _tender(client_a, company, title="Un altre expedient")
    url = BLOCKS.format(company=company.id, tender=tender_id)

    b1 = await _block(
        client_a, company, tender_id, "B1", base_price_ex_vat="1000.50", price_points=20
    )
    assert b1["base_price_ex_vat"] == "1000.50" and b1["price_points"] == 20.0
    # Tri-estat: no s'ha dit si s'adjudica a part.
    assert b1["separately_awardable"] is None and b1["citations"] == []
    assert isinstance((await db.get(TenderBlock, uuid.UUID(b1["id"]))).base_price_ex_vat, Decimal)
    b2 = await _block(
        client_a,
        company,
        tender_id,
        "B2",
        separately_awardable=False,
        billing_terms_json={"per_site": True},
        billing_schema="monthly",
    )
    assert b2["separately_awardable"] is False and b2["billing_schema"] == "monthly"

    # Codi unic dins de l'expedient; el mateix codi en un altre expedient, si.
    duplicate = await client_a.post(url, json={"code": "B1", "name": "Repetit"})
    assert (duplicate.status_code, _code(duplicate)) == (409, "tender_block_code_taken")
    assert (await _block(client_a, company, other_id, "B1"))["code"] == "B1"
    assert (
        await client_a.post(url, json={"code": "B3", "name": "x", "base_price_ex_vat": "-1"})
    ).status_code == 422

    # Un lot: nomes del mateix expedient.
    lot = TenderLot(tender_id=uuid.UUID(other_id), external_lot_id="L1", lot_number=1)
    db.add(lot)
    await db.commit()
    wrong = await client_a.patch(f"{url}/{b1['id']}", json={"tender_lot_id": str(lot.id)})
    assert (wrong.status_code, _code(wrong)) == (422, "lot_tender_mismatch")

    # Correccio parcial: nomes el que arriba.
    edited = await client_a.patch(
        f"{url}/{b1['id']}", json={"name": "Bloc 1 (fibra)", "code": "B1b"}
    )
    assert (edited.status_code, edited.json()["name"], edited.json()["code"]) == (
        200,
        "Bloc 1 (fibra)",
        "B1b",
    )
    assert edited.json()["base_price_ex_vat"] == "1000.50"
    clash = await client_a.patch(f"{url}/{b1['id']}", json={"code": "B2"})
    assert (clash.status_code, _code(clash)) == (409, "tender_block_code_taken")

    detail = (await client_a.get(DETAIL.format(tender=tender_id))).json()
    assert [b["code"] for b in detail["blocks"]] == ["B1b", "B2"]

    assert (await client_a.delete(f"{url}/{b2['id']}")).status_code == 204
    assert (await client_a.get(f"{url}/{b2['id']}")).status_code in (404, 405)
    assert [
        b["code"]
        for b in (
            await client_a.get(STRUCTURE.format(company=company.id, tender=tender_id))
        ).json()["blocks"]
    ] == ["B1b"]
    assert (await client_a.delete(f"{url}/{uuid.uuid4()}")).status_code == 404


async def test_sites_crud_role_and_critical_independent_location_optional(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    url = SITES.format(company=company.id, tender=tender_id)

    central = await _site(
        client_a, company, tender_id, "S0", role="central", address="Plaça Major 1"
    )
    remote = await _site(
        client_a,
        company,
        tender_id,
        "S1",
        role="remote",
        is_critical=True,
        municipality_code="17001",
        municipality_code_system="INE",
        latitude="42.123456",
        longitude="3.1",
    )
    # Sense dir-ho, no se sap: `null`, mai `false` (0050).
    assert (central["role"], central["is_critical"]) == ("central", None)
    assert (remote["role"], remote["is_critical"]) == ("remote", True)
    # Ubicacio: el que no es diu queda a null; res no es dedueix.
    assert (central["municipality_code"], central["latitude"], central["longitude"]) == (
        None,
        None,
        None,
    )
    assert remote["address"] is None
    assert (remote["municipality_code"], remote["municipality_code_system"]) == ("17001", "INE")
    assert central["municipality_code_system"] is None
    # Un codi sense sistema es queda sense sistema: no s'infereix INE ni res.
    bare = await _site(client_a, company, tender_id, "S9", role="remote", municipality_code="17062")
    assert (bare["municipality_code"], bare["municipality_code_system"]) == ("17062", None)
    assert (
        await client_a.patch(f"{url}/{bare['id']}", json={"municipality_code_system": "INE"})
    ).json()["municipality_code_system"] == "INE"
    assert (
        await client_a.patch(f"{url}/{bare['id']}", json={"municipality_code_system": None})
    ).json()["municipality_code_system"] is None
    assert (await client_a.delete(f"{url}/{bare['id']}")).status_code == 204
    assert (Decimal(remote["latitude"]), Decimal(remote["longitude"])) == (
        Decimal("42.123456"),
        Decimal("3.1"),
    )

    duplicate = await client_a.post(url, json={"code": "S1", "name": "x", "role": "remote"})
    assert (duplicate.status_code, _code(duplicate)) == (409, "tender_site_code_taken")
    assert (
        await client_a.post(url, json={"code": "S2", "name": "x", "role": "hub"})
    ).status_code == 422
    assert (
        await client_a.post(
            url, json={"code": "S2", "name": "x", "role": "remote", "latitude": "95"}
        )
    ).status_code == 422

    edited = await client_a.patch(
        f"{url}/{remote['id']}", json={"is_critical": False, "address": "Carrer Nou 3"}
    )
    assert (edited.json()["is_critical"], edited.json()["address"], edited.json()["role"]) == (
        False,
        "Carrer Nou 3",
        "remote",
    )
    detail = (await client_a.get(DETAIL.format(tender=tender_id))).json()
    assert [(s["code"], s["role"]) for s in detail["sites"]] == [
        ("S0", "central"),
        ("S1", "remote"),
    ]

    assert (await client_a.delete(f"{url}/{central['id']}")).status_code == 204
    assert await _count(db, TenderSite) == 1


# --------------------------------------------------------------------------- #
# Requisits i criteris: blocs N:M i temporalitat                               #
# --------------------------------------------------------------------------- #


async def test_requirement_blocks_attach_detach_timing_and_cross_tender(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    other_id = await _tender(client_a, company, title="Un altre")
    b1 = await _block(client_a, company, tender_id, "B1")
    b3 = await _block(client_a, company, tender_id, "B3")
    foreign = await _block(client_a, company, other_id, "B1")
    requirement = await _requirement(client_a, company, tender_id)
    url = REQ_STRUCTURE.format(company=company.id, tender=tender_id, requirement=requirement["id"])

    attached = await client_a.patch(url, json={"block_ids": [b1["id"]]})
    assert attached.status_code == 200, attached.text
    assert (attached.json()["block_ids"], attached.json()["contract_wide"]) == ([b1["id"]], False)
    both = await client_a.patch(url, json={"block_ids": [b1["id"], b3["id"], b1["id"]]})
    assert set(both.json()["block_ids"]) == {b1["id"], b3["id"]}
    assert await _count(db, RequirementBlock) == 2

    # Un bloc d'una altra licitacio no entra, i no es toca res.
    wrong = await client_a.patch(url, json={"block_ids": [b1["id"], foreign["id"]]})
    assert (wrong.status_code, _code(wrong)) == (422, "block_tender_mismatch")
    assert await _count(db, RequirementBlock) == 2

    # Temporalitat, sense tocar els blocs; i nomes els valors de l'enum.
    timed = await client_a.patch(url, json={"timing": "at_execution"})
    assert (timed.json()["timing"], set(timed.json()["block_ids"])) == (
        "at_execution",
        {b1["id"], b3["id"]},
    )
    assert (await client_a.patch(url, json={"timing": "someday"})).status_code == 422

    analysis = (await client_a.get(ANALYSIS.format(tender=tender_id))).json()
    read = next(r for r in analysis["requirements"] if r["id"] == requirement["id"])
    assert (read["timing"], set(read["block_ids"])) == ("at_execution", {b1["id"], b3["id"]})

    # Treure'n un, i tornar a tot el contracte.
    only = await client_a.patch(url, json={"block_ids": [b3["id"]]})
    assert only.json()["block_ids"] == [b3["id"]]
    wide = await client_a.patch(url, json={"block_ids": []})
    assert (wide.json()["block_ids"], wide.json()["contract_wide"], wide.json()["timing"]) == (
        [],
        True,
        "at_execution",
    )
    assert await _count(db, RequirementBlock) == 0

    # Un requisit extret tambe s'anota: es estructura del plec, no una reescriptura de la run.
    run = await _run(db, tender_id)
    legacy = await _extracted_requirement(db, run)
    legacy_url = REQ_STRUCTURE.format(company=company.id, tender=tender_id, requirement=legacy.id)
    assert (await client_a.patch(legacy_url, json={"block_ids": [b1["id"]]})).json()[
        "block_ids"
    ] == [b1["id"]]
    # I esborrar el bloc deixa el requisit, sense enllac.
    assert (
        await client_a.delete(f"{BLOCKS.format(company=company.id, tender=tender_id)}/{b1['id']}")
    ).status_code == 204
    assert (await client_a.patch(legacy_url, json={})).json()["block_ids"] == []
    assert await db.get(Requirement, legacy.id) is not None


async def test_criterion_blocks_do_not_split_the_points(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    b1 = await _block(client_a, company, tender_id, "B1")
    b2 = await _block(client_a, company, tender_id, "B2")
    run = await _run(db, tender_id)
    c1 = await _criterion(db, run, "Qualitat del servei", 10.0)
    c2 = await _criterion(db, run, "Millores tecniques", 6.0)

    one = await client_a.patch(
        CRIT_STRUCTURE.format(company=company.id, tender=tender_id, criterion=c1.id),
        json={"block_ids": [b1["id"]], "timing": "at_submission"},
    )
    assert one.status_code == 200, one.text
    assert (one.json()["block_ids"], one.json()["points"], one.json()["timing"]) == (
        [b1["id"]],
        10.0,
        "at_submission",
    )
    two = await client_a.patch(
        CRIT_STRUCTURE.format(company=company.id, tender=tender_id, criterion=c2.id),
        json={"block_ids": [b1["id"], b2["id"]]},
    )
    # Sis punts sobre dos blocs: sis punts, no 3 + 3.
    assert (set(two.json()["block_ids"]), two.json()["points"], two.json()["contract_wide"]) == (
        {b1["id"], b2["id"]},
        6.0,
        False,
    )
    assert (await db.get(AwardCriterion, c2.id)).points == 6.0
    assert await _count(db, AwardCriterionBlock) == 3

    analysis = (await client_a.get(ANALYSIS.format(tender=tender_id))).json()
    read = next(c for c in analysis["award_criteria"] if c["id"] == str(c2.id))
    assert (read["points"], set(read["block_ids"]), read["timing"]) == (
        6.0,
        {b1["id"], b2["id"]},
        "unknown",
    )
    assert (
        await client_a.patch(
            CRIT_STRUCTURE.format(company=company.id, tender=tender_id, criterion=uuid.uuid4()),
            json={"timing": "ambiguous"},
        )
    ).status_code == 404


# --------------------------------------------------------------------------- #
# Permisos: exactament els que ja hi havia                                     #
# --------------------------------------------------------------------------- #


async def test_permissions_are_the_existing_ones(client_a, client_b, tenant_a, tenant_b, db):
    user, company = tenant_a
    _, company_b = tenant_b
    tender_id = await _tender(client_a, company)
    b1 = await _block(client_a, company, tender_id, "B1")
    requirement = await _requirement(client_a, company, tender_id)
    structure = STRUCTURE.format(company=company.id, tender=tender_id)
    blocks = BLOCKS.format(company=company.id, tender=tender_id)
    req = REQ_STRUCTURE.format(company=company.id, tender=tender_id, requirement=requirement["id"])

    # Una altra empresa ni la veu: 404 a tot, i cap fila nova.
    for response in (
        await client_b.get(STRUCTURE.format(company=company_b.id, tender=tender_id)),
        await client_b.patch(
            STRUCTURE.format(company=company_b.id, tender=tender_id), json={"duration_months": 1}
        ),
        await client_b.post(
            BLOCKS.format(company=company_b.id, tender=tender_id), json={"code": "X", "name": "x"}
        ),
        await client_b.patch(
            REQ_STRUCTURE.format(
                company=company_b.id, tender=tender_id, requirement=requirement["id"]
            ),
            json={"block_ids": []},
        ),
    ):
        assert response.status_code == 404, response.text
    assert await _count(db, TenderBlock) == 1

    # Qui nomes consulta: llegeix, no escriu (la mateixa resposta que la resta de la licitacio).
    await _role(db, user, company, "viewer")
    assert (await client_a.get(structure)).status_code == 200
    for response in (
        await client_a.patch(structure, json={"duration_months": 1}),
        await client_a.post(blocks, json={"code": "B9", "name": "x"}),
        await client_a.patch(f"{blocks}/{b1['id']}", json={"name": "y"}),
        await client_a.delete(f"{blocks}/{b1['id']}"),
        await client_a.post(
            SITES.format(company=company.id, tender=tender_id),
            json={"code": "S", "name": "x", "role": "central"},
        ),
        await client_a.patch(req, json={"timing": "at_submission"}),
    ):
        assert response.status_code == 404, response.text
    assert await _count(db, TenderBlock) == 1 and await _count(db, TenderSite) == 0

    # El perfil tecnic anota requisits (com ja escrivia requisits) pero no edita la
    # licitacio (com ja no la podia editar): les dues politiques, intactes.
    await _role(db, user, company, "technical")
    assert (
        await client_a.patch(req, json={"timing": "at_submission", "block_ids": [b1["id"]]})
    ).status_code == 200
    assert (await client_a.post(blocks, json={"code": "B9", "name": "x"})).status_code == 404
    assert (await client_a.patch(structure, json={"duration_months": 1})).status_code == 404

    await _role(db, user, company, "bid_manager")
    assert (await client_a.post(blocks, json={"code": "B9", "name": "x"})).status_code == 201
    assert (await client_a.patch(structure, json={"duration_months": 1})).status_code == 200


async def test_a_public_tender_is_readable_but_not_editable_here(client_a, tenant_a, db):
    """La unica via existent per editar dades estructurades es la licitacio
    oberta a ma: una de publica es llegeix i no s'hi escriu (422, com fins ara)."""
    _, company = tenant_a
    source = Source(
        code=f"feed-{uuid.uuid4().hex[:6]}", name="Font sindicada", source_type=SourceType.API
    )
    db.add(source)
    await db.flush()
    tender = Tender(source_id=source.id, external_id=uuid.uuid4().hex, title="Publica")
    db.add(tender)
    await db.commit()
    url = STRUCTURE.format(company=company.id, tender=tender.id)
    assert (await client_a.get(url)).json()["blocks"] == []
    refused = await client_a.patch(url, json={"duration_months": 12})
    assert (refused.status_code, _code(refused)) == (422, "tender_not_manual")
    refused = await client_a.post(
        BLOCKS.format(company=company.id, tender=tender.id), json={"code": "B1", "name": "x"}
    )
    assert (refused.status_code, _code(refused)) == (422, "tender_not_manual")


# --------------------------------------------------------------------------- #
# Cites                                                                        #
# --------------------------------------------------------------------------- #


async def test_blocks_and_sites_cite_sources_with_the_existing_pattern(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    other_id = await _tender(client_a, company, title="Un altre")
    b1 = await _block(client_a, company, tender_id, "B1")
    s1 = await _site(client_a, company, tender_id, "S1", role="central")
    ref = await _file_ref(client_a, company, tender_id, label="Annex de preus, full 2")
    foreign_ref = await _file_ref(client_a, company, other_id)
    blocks = BLOCKS.format(company=company.id, tender=tender_id)
    sites = SITES.format(company=company.id, tender=tender_id)

    linked = await client_a.post(f"{blocks}/{b1['id']}/citations", json={"source_ref_id": ref})
    assert linked.status_code == 200, linked.text
    assert [c["id"] for c in linked.json()["citations"]] == [ref]
    assert linked.json()["citations"][0]["location_label"] == "Annex de preus, full 2"
    # Idempotent, i la mateixa cita pot sostenir una seu.
    assert (
        len(
            (
                await client_a.post(f"{blocks}/{b1['id']}/citations", json={"source_ref_id": ref})
            ).json()["citations"]
        )
        == 1
    )
    assert [
        c["id"]
        for c in (
            await client_a.post(f"{sites}/{s1['id']}/citations", json={"source_ref_id": ref})
        ).json()["citations"]
    ] == [ref]
    structure = (await client_a.get(STRUCTURE.format(company=company.id, tender=tender_id))).json()
    assert (
        structure["blocks"][0]["citations"][0]["id"] == ref
        and structure["sites"][0]["citations"][0]["id"] == ref
    )

    wrong = await client_a.post(
        f"{blocks}/{b1['id']}/citations", json={"source_ref_id": foreign_ref}
    )
    assert (wrong.status_code, _code(wrong)) == (422, "source_ref_tender_mismatch")
    assert (
        await client_a.post(
            f"{blocks}/{b1['id']}/citations", json={"source_ref_id": str(uuid.uuid4())}
        )
    ).status_code == 404

    # Treure l'enllac no esborra la cita; esborrar el bloc tampoc.
    unlinked = await client_a.delete(f"{blocks}/{b1['id']}/citations/{ref}")
    assert (unlinked.status_code, unlinked.json()["citations"]) == (200, [])
    assert (
        await client_a.post(f"{blocks}/{b1['id']}/citations", json={"source_ref_id": ref})
    ).status_code == 200
    assert (await client_a.delete(f"{blocks}/{b1['id']}")).status_code == 204
    assert await _count(db, TenderBlockSourceRefLink) == 0
    assert await db.get(TenderSourceRef, uuid.UUID(ref)) is not None


# --------------------------------------------------------------------------- #
# Gold sintetic del lock                                                       #
# --------------------------------------------------------------------------- #


async def test_gold_synthetic_contract_structure(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await _tender(client_a, company, title="Contracte sintetic T")
    base = T.format(company=company.id, tender=tender_id)

    # Metadades.
    meta = await client_a.patch(
        f"{base}/structure",
        json={
            "duration_months": 48,
            "regulated_harmonised": True,
            "planned_start_date": "2027-03-01",
        },
    )
    assert meta.status_code == 200

    # Quatre blocs i onze seus (una central, deu remotes, dues critiques).
    blocks = {
        code: await _block(client_a, company, tender_id, code) for code in ("B1", "B2", "B3", "B4")
    }
    sites = [await _site(client_a, company, tender_id, "S0", role="central")]
    for n in range(1, 11):
        sites.append(
            await _site(
                client_a, company, tender_id, f"S{n}", role="remote", is_critical=n in (3, 7)
            )
        )
    assert len(sites) == 11
    # Dues seus amb municipi: una amb el sistema dit, l'altra nomes amb el codi.
    located = await client_a.patch(
        f"{base}/sites/{sites[1]['id']}",
        json={"municipality_code": "17062", "municipality_code_system": "INE"},
    )
    assert (located.json()["municipality_code"], located.json()["municipality_code_system"]) == (
        "17062",
        "INE",
    )
    bare = await client_a.patch(
        f"{base}/sites/{sites[2]['id']}", json={"municipality_code": "17062"}
    )
    assert (bare.json()["municipality_code"], bare.json()["municipality_code_system"]) == (
        "17062",
        None,
    )
    assert (
        sum(s["role"] == "central" for s in sites) == 1
        and sum(s["role"] == "remote" for s in sites) == 10
    )
    assert [s["code"] for s in sites if s["is_critical"]] == ["S3", "S7"]

    # Requisits R1 (tot el contracte), R2 (B1), R3 (B1 + B3), i un d'antic.
    r1 = await _requirement(client_a, company, tender_id, title="R1 solvencia")
    r2 = await _requirement(client_a, company, tender_id, title="R2 fibra al bloc 1")
    r3 = await _requirement(client_a, company, tender_id, title="R3 manteniment")
    run = await _run(db, tender_id)
    legacy = await _extracted_requirement(db, run, title="Requisit d'una run anterior a la 0048")
    plan = {
        r1["id"]: ([], "at_submission"),
        r2["id"]: ([blocks["B1"]["id"]], "at_execution"),
        r3["id"]: ([blocks["B1"]["id"], blocks["B3"]["id"]], "ambiguous"),
    }
    for requirement_id, (block_ids, timing) in plan.items():
        response = await client_a.patch(
            REQ_STRUCTURE.format(company=company.id, tender=tender_id, requirement=requirement_id),
            json={"block_ids": block_ids, "timing": timing},
        )
        assert response.status_code == 200, response.text

    # Criteris: C1 a B1, C2 a B1 + B2, sense dividir punts.
    c1 = await _criterion(db, run, "C1", 10.0)
    c2 = await _criterion(db, run, "C2", 6.0)
    for criterion, block_codes in ((c1, ["B1"]), (c2, ["B1", "B2"])):
        response = await client_a.patch(
            CRIT_STRUCTURE.format(company=company.id, tender=tender_id, criterion=criterion.id),
            json={"block_ids": [blocks[c]["id"] for c in block_codes]},
        )
        assert response.status_code == 200, response.text

    # Lectura: el que s'ha dit, i res mes.
    detail = (await client_a.get(DETAIL.format(tender=tender_id))).json()
    assert (
        detail["duration_months"],
        detail["extension_months"],
        detail["regulated_harmonised"],
        detail["planned_start_date"],
    ) == (
        48,
        None,
        True,
        "2027-03-01",
    )
    assert [b["code"] for b in detail["blocks"]] == ["B1", "B2", "B3", "B4"]
    assert len(detail["sites"]) == 11
    systems = {s["code"]: s["municipality_code_system"] for s in detail["sites"]}
    assert (systems["S1"], systems["S2"], systems["S0"]) == ("INE", None, None)
    analysis = (await client_a.get(ANALYSIS.format(tender=tender_id))).json()
    by_id = {r["id"]: r for r in analysis["requirements"]}
    assert (by_id[r1["id"]]["block_ids"], by_id[r1["id"]]["timing"]) == ([], "at_submission")
    assert (by_id[r2["id"]]["block_ids"], by_id[r2["id"]]["timing"]) == (
        [blocks["B1"]["id"]],
        "at_execution",
    )
    assert (set(by_id[r3["id"]]["block_ids"]), by_id[r3["id"]]["timing"]) == (
        {blocks["B1"]["id"], blocks["B3"]["id"]},
        "ambiguous",
    )
    assert (by_id[str(legacy.id)]["block_ids"], by_id[str(legacy.id)]["timing"]) == ([], "unknown")
    criteria = {c["id"]: c for c in analysis["award_criteria"]}
    assert (criteria[str(c1.id)]["block_ids"], criteria[str(c1.id)]["points"]) == (
        [blocks["B1"]["id"]],
        10.0,
    )
    assert (set(criteria[str(c2.id)]["block_ids"]), criteria[str(c2.id)]["points"]) == (
        {blocks["B1"]["id"], blocks["B2"]["id"]},
        6.0,
    )

    # Res d'aixo ha creat cap element de 0049/0050: ni tan sols existeixen les taules.
    for table in NOT_YET:
        assert table not in Base.metadata.tables, table
    assert await _count(db, RequirementBlock) == 3 and await _count(db, AwardCriterionBlock) == 3
