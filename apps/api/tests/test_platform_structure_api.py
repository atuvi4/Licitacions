"""Router d'operador `/platform/tenders/{id}/structure` (3B-2 §4, MUST del propietari).

L'operador de plataforma manté l'estructura i l'abast d'una licitacio PUBLICA amb
provenance; els usuaris d'empresa no la poden mutar per cap endpoint. Es protegeix:

* permisos: l'operador escriu sobre una licitacio publica; una empresa rep 403 a
  `/platform/...` i `tender_not_manual` (422) a TOTS els seus endpoints
  d'estructura, d'abast, de cites i de requisits sobre una licitacio publica;
  l'operador sobre una licitacio oberta a ma (o el gold) rep 422
  `tender_not_public` (com els routers germans) i sobre una inexistent, 404;
* cada escriptura de l'operador porta almenys una cita i deixa `origin=operator`,
  `authority=platform_operator` i la persona; un `null` en un camp amb «no se
  sap» es refusa (per aixo hi ha `reset-unknown`); la darrera cita d'una entitat
  no es pot treure sense posar-n'hi una altra;
* les propostes per l'API: sense cita 422, promoure i rebutjar;
* auditoria amb `company_id=None` i cap revisio nova de la licitacio.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.enums import (
    StructureAuthority,
    StructureOrigin,
    StructureProposalStatus,
)
from app.models.requirements import RequirementExtractionRun
from app.models.structure_proposals import TenderStructureProposal
from app.models.tender_lifecycle import TenderRevision
from app.models.tender_scope import RequirementTerritory
from app.models.tender_structure import TenderBlock, TenderBlockSourceRefLink, TenderSite
from app.models.tenders import Tender
from app.services import source_refs
from app.services.manual_tender import get_or_create_manual_source
from tests.conftest import login
from tests.test_platform_onboarding import create_operator
from tests.test_structure_proposals import extracted_requirement, public_tender_with_ref


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    from app.knowledge import vocabulary

    await vocabulary.seed_vocabulary(db)
    await db.commit()


@pytest.fixture
async def operator(db):
    return await create_operator(db)


@pytest.fixture
async def op_client(client_factory, operator):
    http_client = client_factory()
    await login(http_client, operator.email)
    return http_client


def base(tender_id) -> str:
    return f"/api/v1/platform/tenders/{tender_id}/structure"


def _code(response) -> str:
    return response.json()["error"]["code"]


async def _revisions(db, tender_id) -> int:
    return await db.scalar(
        sa.select(sa.func.count())
        .select_from(TenderRevision)
        .where(TenderRevision.tender_id == tender_id)
    )


# --------------------------------------------------------------------------- #
# L'operador escriu amb cita i provenance                                       #
# --------------------------------------------------------------------------- #


async def test_l_operador_escriu_un_bloc_citat_en_una_licitacio_publica(db, op_client, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    revisions = await _revisions(db, tender.id)
    url = f"{base(tender.id)}/blocks"

    # Sense cita, no hi ha cap valor conegut.
    refused = await op_client.post(url, json={"code": "B1", "name": "Bloc 1"})
    assert refused.status_code == 422
    refused = await op_client.post(url, json={"code": "B1", "name": "x", "source_ref_ids": []})
    assert refused.status_code == 422

    created = await op_client.post(
        url,
        json={"code": "B1", "name": "Bloc 1", "price_points": 6, "source_ref_ids": [str(ref.id)]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert [c["id"] for c in body["citations"]] == [str(ref.id)]
    assert body["provenance"]["origin"] == "operator"
    assert body["provenance"]["authority"] == "platform_operator"
    assert body["provenance"]["created_by"] == str(operator.id)
    block = await db.get(TenderBlock, uuid.UUID(body["id"]))
    assert (block.origin, block.authority, block.created_by) == (
        StructureOrigin.OPERATOR,
        StructureAuthority.PLATFORM_OPERATOR,
        operator.id,
    )

    # Un `null` en un camp amb «no se sap» no es cap valor citat.
    patch = f"{url}/{block.id}"
    refused = await op_client.patch(
        patch, json={"separately_awardable": None, "source_ref_ids": [str(ref.id)]}
    )
    assert refused.status_code == 422
    ok = await op_client.patch(
        patch, json={"separately_awardable": True, "source_ref_ids": [str(ref.id)]}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["separately_awardable"] is True
    # Tornar a desconegut: explicit.
    reset = await op_client.post(
        f"{base(tender.id)}/reset-unknown",
        json={
            "entity_kind": "block",
            "entity_id": str(block.id),
            "fields": ["separately_awardable"],
        },
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["changed"] == ["separately_awardable"]
    await db.refresh(block)
    assert block.separately_awardable is None

    read = (await op_client.get(base(tender.id))).json()
    assert [b["code"] for b in read["blocks"]] == ["B1"]

    # Auditoria de plataforma, cap revisio de la licitacio.
    rows = list(
        await db.scalars(
            sa.select(AuditLog).where(
                AuditLog.entity_type == "tender_block", AuditLog.entity_id == block.id
            )
        )
    )
    assert len(rows) == 2
    assert all(r.company_id is None and r.user_id == operator.id for r in rows)
    assert await _revisions(db, tender.id) == revisions

    deleted = await op_client.delete(patch)
    assert deleted.status_code == 204
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderBlock)) == 0


async def test_l_operador_escriu_seus_abast_necessitats_fets_i_territoris(db, op_client, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    requirement = await extracted_requirement(db, tender)
    await db.commit()
    cite = {"source_ref_ids": [str(ref.id)]}
    root = base(tender.id)

    site = await op_client.post(
        f"{root}/sites", json={"name": "Can Maset", "role": "remote", **cite}
    )
    assert site.status_code == 201, site.text
    assert site.json()["is_critical"] is None and site.json()["provenance"]["origin"] == "operator"
    item = await op_client.post(
        f"{root}/scope-items", json={"service_line_code": "access_ftth", "quantity": 15, **cite}
    )
    assert item.status_code == 201, item.text
    need = await op_client.post(
        f"{root}/requirements/{requirement.id}/capability-needs",
        json={"unmapped_capability": "Radioenllac", "comparator": "present",
              "need_type": "minimum", **cite},
    )  # fmt: skip
    assert need.status_code == 201, need.text
    fact = await op_client.post(
        f"{root}/contract-facts",
        json={"fact_code": "envelopes", "value_json": {"count": 3}, "value_schema": "v1", **cite},
    )
    assert fact.status_code == 201, fact.text
    for response in (item, need, fact):
        assert response.json()["provenance"]["origin"] == "operator"
        assert [c["id"] for c in response.json()["citations"]] == [str(ref.id)]

    # Un territori no te taula de cites: la cita queda a una proposta promoguda.
    territory = await op_client.post(
        f"{root}/requirements/{requirement.id}/territories",
        json={"territory": {"kind": "municipality", "label": "L'Escala"},
              "territory_completeness": "complete", **cite},
    )  # fmt: skip
    assert territory.status_code == 201, territory.text
    body = territory.json()
    assert body["territory_completeness"] == "complete"
    (row,) = body["territories"]
    assert row["provenance"]["origin"] == "operator"
    proposal = await db.get(TenderStructureProposal, uuid.UUID(row["provenance"]["proposal_id"]))
    assert (proposal.status, proposal.source_ref_ids) == (
        StructureProposalStatus.PROMOTED,
        [str(ref.id)],
    )
    # Treure el darrer territori d'un conjunt «complet» no quadra.
    refused = await op_client.delete(
        f"{root}/requirements/{requirement.id}/territories/{row['id']}"
    )
    assert (refused.status_code, _code(refused)) == (422, "territory_completeness_conflict")
    assert await db.scalar(sa.select(sa.func.count()).select_from(RequirementTerritory)) == 1

    # Cites genèriques: enllacar una de substituta i treure l'antiga (la cita es queda).
    site_id = site.json()["id"]
    other = await source_refs.create_document_file_ref(
        db, tender_id=tender.id, file_id=ref.tender_document_file_id, location_label="Annex"
    )
    await db.commit()
    link = await op_client.post(
        f"{root}/citations",
        json={"entity_kind": "site", "entity_id": site_id, "source_ref_id": str(other.id)},
    )
    assert link.status_code == 204
    unlink = await op_client.delete(f"{root}/citations/site/{site_id}/{ref.id}")
    assert unlink.status_code == 204
    read = (await op_client.get(root)).json()
    assert [c["id"] for c in read["sites"][0]["citations"]] == [str(other.id)]
    assert len(read["scope_items"]) == 1 and len(read["contract_facts"]) == 1


async def test_les_cites_d_un_patch_s_enllacen_i_la_darrera_no_es_treu(db, op_client):
    tender, ref, _ = await public_tender_with_ref(db)
    other = await source_refs.create_document_file_ref(
        db, tender_id=tender.id, file_id=ref.tender_document_file_id, location_label="PPT §4"
    )
    await db.commit()
    root = base(tender.id)
    created = await op_client.post(
        f"{root}/blocks", json={"code": "B1", "name": "Bloc", "source_ref_ids": [str(ref.id)]}
    )
    block_id = created.json()["id"]

    # Un PATCH valid que no canvia cap valor pero porta una cita nova: s'enllaca.
    same = await op_client.patch(
        f"{root}/blocks/{block_id}", json={"name": "Bloc", "source_ref_ids": [str(other.id)]}
    )
    assert same.status_code == 200, same.text
    assert sorted(c["id"] for c in same.json()["citations"]) == sorted([str(ref.id), str(other.id)])

    # Se'n pot treure una mentre en quedi una altra; la darrera, no.
    first = await op_client.delete(f"{root}/citations/block/{block_id}/{ref.id}")
    assert first.status_code == 204
    last = await op_client.delete(f"{root}/citations/block/{block_id}/{other.id}")
    assert (last.status_code, _code(last)) == (422, "last_citation")
    links = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TenderBlockSourceRefLink)
        .where(TenderBlockSourceRefLink.block_id == uuid.UUID(block_id))
    )
    assert links == 1


async def test_propostes_per_l_api(db, op_client, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    url = f"{base(tender.id)}/proposals"
    refused = await op_client.post(
        url, json={"entity_kind": "block", "payload": {"code": "B1", "name": "x"},
                   "source_ref_ids": []},
    )  # fmt: skip
    assert refused.status_code == 422
    refused = await op_client.post(
        url,
        json={"entity_kind": "block", "payload": {"code": "B1"}, "source_ref_ids": [str(ref.id)]},
    )
    assert (refused.status_code, _code(refused)) == (422, "structure_proposal_payload_invalid")

    created = await op_client.post(
        url,
        json={"entity_kind": "site", "payload": {"code": "S1", "role": "central"},
              "source_ref_ids": [str(ref.id)]},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert (proposal["status"], proposal["natural_key"], proposal["producer"]) == (
        "proposed",
        "code:S1",
        "operator",
    )
    other = await op_client.post(
        url,
        json={"entity_kind": "block", "payload": {"code": "B9", "name": "Bloc dubtos"},
              "source_ref_ids": [str(ref.id)]},
    )  # fmt: skip
    listed = await op_client.get(url, params={"status": "proposed"})
    assert sorted(p["id"] for p in listed.json()) == sorted([proposal["id"], other.json()["id"]])

    promoted = await op_client.post(f"{url}/{proposal['id']}/promote")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["proposal"]["status"] == "promoted"
    site = await db.get(TenderSite, uuid.UUID(promoted.json()["entity_id"]))
    assert (site.origin, site.proposal_id) == (StructureOrigin.PROMOTED, uuid.UUID(proposal["id"]))
    again = await op_client.post(f"{url}/{proposal['id']}/promote")
    assert (again.status_code, _code(again)) == (409, "structure_proposal_not_pending")

    rejected = await op_client.post(f"{url}/{other.json()['id']}/reject", json={"reason": ""})
    assert rejected.status_code == 422
    rejected = await op_client.post(
        f"{url}/{other.json()['id']}/reject", json={"reason": "No consta al plec."}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    audit = list(
        await db.scalars(
            sa.select(AuditLog).where(AuditLog.entity_type == "tender_structure_proposal")
        )
    )
    assert len(audit) == 4 and all(r.company_id is None for r in audit)


# --------------------------------------------------------------------------- #
# Permisos                                                                      #
# --------------------------------------------------------------------------- #


async def test_una_empresa_no_entra_a_la_via_de_l_operador(db, client_a):
    tender, ref, _ = await public_tender_with_ref(db)
    root = base(tender.id)
    for method, path, kwargs in (
        ("get", root, {}),
        ("post", f"{root}/blocks", {"json": {"code": "B1", "name": "x",
                                             "source_ref_ids": [str(ref.id)]}}),
        ("post", f"{root}/proposals", {"json": {"entity_kind": "block",
                                                "payload": {"code": "B1", "name": "x"},
                                                "source_ref_ids": [str(ref.id)]}}),
        ("get", f"{root}/proposals", {}),
        ("post", f"{root}/reset-unknown", {"json": {"entity_kind": "block",
                                                    "entity_id": str(uuid.uuid4()),
                                                    "fields": ["price_points"]}}),
    ):  # fmt: skip
        response = await getattr(client_a, method)(path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderBlock)) == 0
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderStructureProposal)) == 0


async def test_una_empresa_rep_tender_not_manual_a_tots_els_seus_endpoints(db, client_a, tenant_a):
    """Cap endpoint tenant d'estructura, abast o cites muta una licitacio publica."""
    _user, company = tenant_a
    tender, ref, _ = await public_tender_with_ref(db)
    requirement = await extracted_requirement(db, tender)
    await db.commit()
    t = f"/api/v1/companies/{company.id}/tenders/{tender.id}"
    any_id = uuid.uuid4()
    cite = {"json": {"source_ref_id": str(ref.id)}}
    calls = (
        # Estructura (0048)
        ("patch", f"{t}/structure", {"json": {"duration_months": 12}}),
        ("post", f"{t}/blocks", {"json": {"code": "B1", "name": "x"}}),
        ("patch", f"{t}/blocks/{any_id}", {"json": {"name": "x"}}),
        ("delete", f"{t}/blocks/{any_id}", {}),
        ("post", f"{t}/sites", {"json": {"code": "S1", "role": "central"}}),
        ("patch", f"{t}/sites/{any_id}", {"json": {"name": "x"}}),
        ("delete", f"{t}/sites/{any_id}", {}),
        ("patch", f"{t}/requirements/{requirement.id}/structure",
         {"json": {"timing": "at_submission"}}),
        ("patch", f"{t}/award-criteria/{any_id}/structure", {"json": {"timing": "at_submission"}}),
        # Abast (0049)
        ("post", f"{t}/scope-items", {"json": {"service_line_code": "access_ftth"}}),
        ("patch", f"{t}/scope-items/{any_id}", {"json": {"unit": "u"}}),
        ("delete", f"{t}/scope-items/{any_id}", {}),
        ("post", f"{t}/contract-facts", {"json": {"fact_code": "envelopes", "value_json": {},
                                                  "value_schema": "v1"}}),
        ("patch", f"{t}/contract-facts/{any_id}", {"json": {"value_json": {}}}),
        ("delete", f"{t}/contract-facts/{any_id}", {}),
        ("patch", f"{t}/requirements/{requirement.id}/semantics",
         {"json": {"territory_completeness": "not_applicable"}}),
        ("post", f"{t}/requirements/{requirement.id}/capability-needs",
         {"json": {"unmapped_capability": "x", "comparator": "present", "need_type": "minimum"}}),
        ("patch", f"{t}/capability-needs/{any_id}", {"json": {"points": 1}}),
        ("delete", f"{t}/capability-needs/{any_id}", {}),
        # Cites
        ("post", f"{t}/blocks/{any_id}/citations", cite),
        ("delete", f"{t}/blocks/{any_id}/citations/{ref.id}", {}),
        ("post", f"{t}/sites/{any_id}/citations", cite),
        ("delete", f"{t}/sites/{any_id}/citations/{ref.id}", {}),
        ("post", f"{t}/scope-items/{any_id}/citations", cite),
        ("delete", f"{t}/scope-items/{any_id}/citations/{ref.id}", {}),
        ("post", f"{t}/contract-facts/{any_id}/citations", cite),
        ("delete", f"{t}/contract-facts/{any_id}/citations/{ref.id}", {}),
        ("post", f"{t}/capability-needs/{any_id}/citations", cite),
        ("delete", f"{t}/capability-needs/{any_id}/citations/{ref.id}", {}),
        ("post", f"{t}/award-criteria/{any_id}/citations", cite),
        ("delete", f"{t}/award-criteria/{any_id}/citations/{ref.id}", {}),
        # Requisits (requirements.py): cites globals i requisits manuals
        ("post", f"{t}/requirements/{requirement.id}/citations", cite),
        ("delete", f"{t}/requirements/{requirement.id}/citations/{ref.id}", {}),
        ("patch", f"{t}/requirements/{requirement.id}", {"json": {"title": "x"}}),
        ("post", f"{t}/requirements/{requirement.id}/archive", {}),
    )  # fmt: skip
    for method, path, kwargs in calls:
        response = await getattr(client_a, method)(path, **kwargs)
        assert (response.status_code, _code(response)) == (422, "tender_not_manual"), (
            method,
            path,
            response.text,
        )
    for model in (TenderBlock, TenderSite, TenderStructureProposal):
        assert await db.scalar(sa.select(sa.func.count()).select_from(model)) == 0


async def test_l_operador_no_toca_una_licitacio_d_empresa_ni_el_gold(db, op_client, tenant_a):
    _user, company = tenant_a
    source = await get_or_create_manual_source(db)
    manual = Tender(
        source_id=source.id,
        external_id=f"man-{uuid.uuid4().hex[:6]}",
        title="Manual propia",
        owner_company_id=company.id,
    )
    db.add(manual)
    await db.commit()
    from app.evaluation import tender_gold
    from tests.test_tender_gold_lescala import CASE

    gold = await tender_gold.load_tender_gold(db, tender_gold.load_dataset(CASE))
    await db.commit()

    async def counts() -> tuple[int, int]:
        return (
            await db.scalar(sa.select(sa.func.count()).select_from(TenderBlock)),
            await db.scalar(sa.select(sa.func.count()).select_from(TenderStructureProposal)),
        )

    before = await counts()
    # Com els routers germans: d'empresa o gold -> 422 `tender_not_public`; inexistent -> 404.
    for tender_id, expected in (
        (manual.id, (422, "tender_not_public")),
        (gold.tender.id, (422, "tender_not_public")),
        (uuid.uuid4(), (404, "not_found")),
    ):
        root = base(tender_id)
        for method, path, kwargs in (
            ("get", root, {}),
            ("post", f"{root}/blocks", {"json": {"code": "B1", "name": "x",
                                                 "source_ref_ids": [str(uuid.uuid4())]}}),
            ("post", f"{root}/proposals", {"json": {"entity_kind": "block",
                                                    "payload": {"code": "B1", "name": "x"},
                                                    "source_ref_ids": [str(uuid.uuid4())]}}),
            ("get", f"{root}/proposals", {}),
        ):  # fmt: skip
            response = await getattr(op_client, method)(path, **kwargs)
            assert (response.status_code, _code(response)) == expected, (
                tender_id,
                method,
                path,
                response.text,
            )
    assert await counts() == before


async def test_una_cita_d_una_altra_licitacio_o_retirada_no_entra(db, op_client):
    tender, ref, document = await public_tender_with_ref(db)
    _other, foreign_ref, _ = await public_tender_with_ref(db, title="Una altra")
    url = f"{base(tender.id)}/blocks"
    refused = await op_client.post(
        url, json={"code": "B1", "name": "x", "source_ref_ids": [str(foreign_ref.id)]}
    )
    assert (refused.status_code, _code(refused)) == (422, "source_ref_tender_mismatch")
    document = await db.get(type(document), document.id)
    from datetime import UTC, datetime

    document.removed_at = datetime.now(UTC)
    await db.commit()
    refused = await op_client.post(
        url, json={"code": "B1", "name": "x", "source_ref_ids": [str(ref.id)]}
    )
    assert (refused.status_code, _code(refused)) == (422, "source_ref_document_removed")
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderBlock)) == 0


async def test_una_fila_d_una_run_superada_no_s_anota_per_http(db, op_client):
    """M6 (3B-2 §4.3): en una licitacio publica nomes s'anota la run vigent; la
    lectura d'una fila superada continua oberta."""
    tender, ref, _ = await public_tender_with_ref(db)
    requirement = await extracted_requirement(db, tender)
    run = await db.get(RequirementExtractionRun, requirement.run_id)
    run.is_current = False  # una run mes nova n'ha pres el lloc
    await db.commit()
    cite = {"source_ref_ids": [str(ref.id)]}
    root = base(tender.id)

    for method, url, payload in (
        (
            "post",
            f"{root}/requirements/{requirement.id}/capability-needs",
            {"unmapped_capability": "Radioenllac", "comparator": "present",
             "need_type": "minimum", **cite},
        ),
        (
            "post",
            f"{root}/requirements/{requirement.id}/territories",
            {"territory": {"kind": "municipality", "label": "L'Escala"}, **cite},
        ),
    ):  # fmt: skip
        refused = await getattr(op_client, method)(url, json=payload)
        assert (refused.status_code, _code(refused)) == (409, "requirement_run_superseded"), (
            url,
            refused.text,
        )
    read = await op_client.get(f"{root}/requirements/{requirement.id}/territories")
    assert read.status_code == 200, read.text
    assert await db.scalar(sa.select(sa.func.count()).select_from(RequirementTerritory)) == 0
