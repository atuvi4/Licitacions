"""Declaracio directa de coneixement actual (fase 2B del motor).

Una empresa nova, sense historic, ha de poder dir que es i que pot fer des de
Tuvi. Que es protegeix aqui:

* **tot el que es declara a ma neix `declared` i `pending_verification`**: cap
  payload no pot portar verificacio ni origen, i les files no en surten
  verificades fins que hi ha evidencia enllacada i algu ho confirma;
* **el vocabulari mana**: linies, capacitats i valors fora del vocabulari no
  entren;
* **les filles pengen del pare de la mateixa empresa** (capacitats d'una
  solucio, papers d'un tercer), i esborrar el pare neteja la seva evidencia;
* **aillament**: una altra empresa no llegeix, modifica ni esborra res;
* **la fotografia es coherent** despres de cada accio.

Totes les dades son sintetiques.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.knowledge import vocabulary
from app.models.documents import DocumentLink
from app.models.enums import KnowledgeOrigin, ObservationSubject
from app.models.knowledge import CompanyOperatingFact, PartnerRoleRow, SolutionCapability
from app.services import company_knowledge

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/companies/{company}/knowledge"


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.commit()


async def _snapshot(client, company) -> dict:
    response = await client.get(BASE.format(company=company.id) + "/snapshot")
    assert response.status_code == 200, response.text
    return response.json()


def _entry(snapshot: dict, section: str, key: str) -> dict | None:
    return next((e for e in snapshot[section] if e["key"] == key), None)


def _pending(snapshot: dict) -> list[tuple[str, str, str]]:
    return [(p["kind"], p["subject"], p["key"]) for p in snapshot["pending_validation"]]


async def _declare(client, company, kind: str, payload: dict, expected: int = 201) -> dict:
    response = await client.post(f"{BASE.format(company=company.id)}/records/{kind}", json=payload)
    assert response.status_code == expected, (kind, response.text)
    return response.json()


async def _link_evidence(client, company, entity_type: str, entity_id) -> dict:
    from tests.fixtures.pdf_builder import build_pdf

    response = await client.post(
        f"/api/v1/companies/{company.id}/documents",
        files={
            "file": (
                f"evidencia-{uuid.uuid4().hex[:6]}.pdf",
                build_pdf([f"Evidencia {uuid.uuid4()}"]),
                "application/pdf",
            )
        },
        data={
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "relationship_type": "supporting_document",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Vocabulari                                                                   #
# --------------------------------------------------------------------------- #


async def test_vocabulary_is_readable_and_says_the_same_as_the_code(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.get(f"{BASE.format(company=company.id)}/vocabulary")
    assert response.status_code == 200
    body = response.json()
    assert [s["code"] for s in body["service_lines"]] == [s.code for s in vocabulary.SERVICE_LINES]
    assert {c["code"] for c in body["capabilities"]} == set(vocabulary.CAPABILITIES_BY_CODE)
    poe = next(c for c in body["capabilities"] if c["code"] == "switch.poe_standard")
    assert (poe["value_type"], poe["comparator"], poe["ordinal_scale"]) == (
        "ordinal",
        "gte",
        ["af", "at", "bt"],
    )
    ens = next(s for s in body["standards"] if s["code"] == "ENS")
    assert ens["levels"] == ["basic", "medium", "high"]


async def test_vocabulary_is_read_only():
    from app.main import app

    paths = app.openapi()["paths"]
    vocabulary_paths = [p for p in paths if p.endswith("/knowledge/vocabulary")]
    assert vocabulary_paths and all(set(paths[p]) == {"get"} for p in vocabulary_paths)


# --------------------------------------------------------------------------- #
# Declaracio directa                                                           #
# --------------------------------------------------------------------------- #


async def test_everything_declared_by_hand_is_declared_and_pending(client_a, tenant_a, db):
    _, company = tenant_a
    fact = await _declare(
        client_a,
        company,
        "operating_fact",
        {"key": "support_24x7", "value": True, "detail": "Guardia propia", "as_of": "2026-09-01"},
    )
    solution = await _declare(
        client_a,
        company,
        "solution",
        {"name": "Centraleta Demo", "service_line_code": "voice_pbx", "vendor": "Fabricant Demo"},
    )
    capability = await _declare(
        client_a,
        company,
        "solution_capability",
        {
            "solution_id": solution["id"],
            "capability_code": "pbx.concurrent_calls",
            "status": "supported",
            "value_number": "60",
        },
    )
    product = await _declare(
        client_a,
        company,
        "product",
        {"manufacturer": "Fabricant Demo", "model": "T1", "service_line_code": "voice_pbx"},
    )
    link = await _declare(
        client_a,
        company,
        "solution_product",
        {"solution_id": solution["id"], "product_id": product["id"]},
    )
    partner = await _declare(
        client_a,
        company,
        "partner",
        {
            "name": "Soci Demo",
            "roles": [{"role": "subcontractor", "service_line_code": "civil_works"}],
        },
    )
    role = await _declare(
        client_a,
        company,
        "partner_role",
        {"partner_id": partner["id"], "role": "supplier", "territory": "Girona"},
    )

    for body in (fact, solution, capability, product, link, partner, role):
        assert body["knowledge_origin"] == "declared", body
        assert body["lifecycle"] == "current", body
        # L'estat efectiu no surt de la fila: el diu la fotografia.
        assert "verification_status" not in body, body
    assert fact["as_of"] == "2026-09-01"

    stored = await db.scalar(sa.select(CompanyOperatingFact))
    assert (stored.knowledge_origin, stored.verification_status) == (
        KnowledgeOrigin.DECLARED,
        "pending_verification",
    )
    roles = list(await db.scalars(sa.select(PartnerRoleRow).order_by(PartnerRoleRow.role)))
    assert [(r.role, r.territory) for r in roles] == [
        ("subcontractor", None),
        ("supplier", "Girona"),
    ]

    snapshot = await _snapshot(client_a, company)
    for section, key in (
        ("facts", "support_24x7"),
        ("solutions", "Centraleta Demo"),
        ("capabilities", "pbx.concurrent_calls"),
        ("products", "Fabricant Demo T1"),
        ("partners", "Soci Demo"),
    ):
        entry = _entry(snapshot, section, key)
        assert entry is not None, key
        assert (entry["source"], entry["accreditation_pending"]) == ("current_declared", True)
        assert entry["claims"][0]["has_evidence"] is False
    value = _entry(snapshot, "capabilities", "pbx.concurrent_calls")["claims"][0]["value"]
    assert (value["solution"], value["status"], value["level"]) == (
        "Centraleta Demo",
        "supported",
        None,
    )
    assert Decimal(value["number"]) == 60
    assert _entry(snapshot, "partners", "Soci Demo")["claims"][0]["value"]["roles"] == [
        {"role": "subcontractor", "line": "civil_works", "territory": None},
        {"role": "supplier", "line": None, "territory": "Girona"},
    ]
    kinds = {(p["kind"], p["subject"]) for p in snapshot["pending_validation"]}
    assert kinds == {
        ("accreditation_pending", subject)
        for subject in (
            "operating_fact",
            "solution",
            "solution_capability",
            "product",
            "solution_product",
            "partner",
            "partner_role",
        )
    }
    detail = next(p["detail"] for p in snapshot["pending_validation"] if p["subject"] == "partner")
    assert detail == "Declarat sense evidencia verificada"

    records = (await client_a.get(f"{BASE.format(company=company.id)}/records")).json()
    assert {k: len(v) for k, v in records.items()} == {
        "operating_facts": 1,
        "solutions": 1,
        "solution_capabilities": 1,
        "products": 1,
        "solution_products": 1,
        "partners": 1,
        "partner_roles": 2,
    }


async def test_a_declaration_cannot_carry_verification_or_origin(client_a, tenant_a):
    _, company = tenant_a
    for extra in (
        {"verification_status": "verified"},
        {"knowledge_origin": "promoted_observation"},
    ):
        body = await _declare(
            client_a,
            company,
            "operating_fact",
            {"key": "support_24x7", "value": True, **extra},
            422,
        )
        assert body["error"]["code"] == "validation_error"


async def test_capability_values_follow_the_vocabulary(client_a, tenant_a):
    _, company = tenant_a
    solution = await _declare(
        client_a,
        company,
        "solution",
        {"name": "Switching Demo", "service_line_code": "network_switching"},
    )
    base = {"solution_id": solution["id"], "status": "supported"}
    for broken in (
        {"capability_code": "pbx.ivr_multilevel", "value_number": "1"},  # booleana amb valor
        {"capability_code": "switch.poe_standard", "value_level": "zz"},  # fora de l'escala
        {"capability_code": "switch.poe_standard", "value_number": "3"},  # ordinal amb nombre
        {"capability_code": "pbx.concurrent_calls", "value_level": "at"},  # numerica amb nivell
        {"capability_code": "capacitat.inventada"},
    ):
        await _declare(client_a, company, "solution_capability", {**base, **broken}, 422)
    poe = await _declare(
        client_a,
        company,
        "solution_capability",
        {**base, "capability_code": "switch.poe_standard", "value_level": "at"},
    )
    assert poe["value_level"] == "at"

    # Tampoc en modificar-la.
    url = f"{BASE.format(company=company.id)}/records/solution_capability/{poe['id']}"
    assert (await client_a.patch(url, json={"value_level": "ultra"})).status_code == 422
    ok = await client_a.patch(url, json={"value_level": "bt", "status": "supported_with_limits"})
    assert ok.status_code == 200, ok.text
    assert (ok.json()["value_level"], ok.json()["status"]) == ("bt", "supported_with_limits")


async def test_children_need_their_parent_in_the_same_company(
    client_a, client_b, tenant_a, tenant_b
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    solution = await _declare(
        client_a, company_a, "solution", {"name": "Centraleta A", "service_line_code": "voice_pbx"}
    )
    partner = await _declare(client_a, company_a, "partner", {"name": "Soci A"})
    product = await _declare(client_a, company_a, "product", {"manufacturer": "F", "model": "M"})

    for kind, payload in (
        (
            "solution_capability",
            {"solution_id": solution["id"], "capability_code": "pbx.queues", "status": "supported"},
        ),
        ("partner_role", {"partner_id": partner["id"], "role": "supplier"}),
        ("solution_product", {"solution_id": solution["id"], "product_id": product["id"]}),
    ):
        body = await _declare(client_b, company_b, kind, payload, 404)
        assert body["error"]["code"] == "not_found"
    assert (await _snapshot(client_b, company_b))["capabilities"] == []


async def test_declaring_the_same_thing_twice_conflicts(client_a, tenant_a):
    _, company = tenant_a
    fact = {"key": "datacenter_tier3", "value": True}
    await _declare(client_a, company, "operating_fact", fact)
    body = await _declare(client_a, company, "operating_fact", {**fact, "value": False}, 409)
    assert body["error"]["code"] == "knowledge_already_declared"

    solution = await _declare(
        client_a, company, "solution", {"name": "Centraleta Demo", "service_line_code": "voice_pbx"}
    )
    await _declare(
        client_a,
        company,
        "solution",
        {"name": "Centraleta Demo", "service_line_code": "voice_pbx"},
        409,
    )
    capability = {
        "solution_id": solution["id"],
        "capability_code": "pbx.queues",
        "status": "supported",
    }
    await _declare(client_a, company, "solution_capability", capability)
    body = await _declare(client_a, company, "solution_capability", capability, 409)
    assert body["error"]["code"] == "knowledge_already_declared"


async def test_updating_keeps_origin_and_never_verifies(client_a, tenant_a, db):
    user, company = tenant_a
    observation = await company_knowledge.record_observation(
        db,
        company_id=company.id,
        subject=ObservationSubject.OPERATING_FACT,
        subject_key="own_number_range_nrn",
        statement="Tenien rang NRN propi",
        proposal={"key": "own_number_range_nrn", "value": True},
    )
    await db.commit()
    promoted = await client_a.post(
        f"{BASE.format(company=company.id)}/observations/{observation.id}/promote", json={}
    )
    fact_id = promoted.json()["promoted_entity_id"]

    url = f"{BASE.format(company=company.id)}/records/operating_fact/{fact_id}"
    response = await client_a.patch(
        url, json={"value": False, "detail": "Ja no", "valid_until": "2027-01-01"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["knowledge_origin"], body["value"], body["valid_until"]) == (
        "promoted_observation",
        False,
        "2027-01-01",
    )
    assert (await client_a.patch(url, json={"valid_until": "2000-01-01"})).status_code == 422
    assert (await client_a.patch(url, json={"verification_status": "verified"})).status_code == 422

    fact = await db.get(CompanyOperatingFact, uuid.UUID(fact_id))
    await db.refresh(fact)
    assert fact.verification_status == "pending_verification"
    entry = _entry(await _snapshot(client_a, company), "facts", "own_number_range_nrn")
    assert (entry["source"], entry["claims"][0]["value"]) == ("current_declared", False)

    # Retirar-la la treu de la fotografia sense esborrar-la.
    assert (await client_a.patch(url, json={"lifecycle": "retired"})).status_code == 200
    assert _entry(await _snapshot(client_a, company), "facts", "own_number_range_nrn") is None
    assert await db.get(CompanyOperatingFact, uuid.UUID(fact_id)) is not None


async def test_deleting_a_parent_cleans_the_evidence_of_its_children(client_a, tenant_a, db):
    _, company = tenant_a
    solution = await _declare(
        client_a, company, "solution", {"name": "Centraleta Demo", "service_line_code": "voice_pbx"}
    )
    capability = await _declare(
        client_a,
        company,
        "solution_capability",
        {"solution_id": solution["id"], "capability_code": "pbx.queues", "status": "supported"},
    )
    await _link_evidence(client_a, company, "solution_capability", capability["id"])
    await _link_evidence(client_a, company, "company_solution", solution["id"])
    assert await db.scalar(sa.select(sa.func.count()).select_from(DocumentLink)) == 2

    response = await client_a.delete(
        f"{BASE.format(company=company.id)}/records/solution/{solution['id']}"
    )
    assert response.status_code == 204
    assert await db.scalar(sa.select(sa.func.count()).select_from(SolutionCapability)) == 0
    assert await db.scalar(sa.select(sa.func.count()).select_from(DocumentLink)) == 0
    snapshot = await _snapshot(client_a, company)
    assert (snapshot["solutions"], snapshot["capabilities"], snapshot["pending_validation"]) == (
        [],
        [],
        [],
    )
    assert (
        await client_a.delete(
            f"{BASE.format(company=company.id)}/records/solution/{solution['id']}"
        )
    ).status_code == 404
    assert (
        await client_a.delete(
            f"{BASE.format(company=company.id)}/records/inventat/{solution['id']}"
        )
    ).status_code == 404


async def test_another_company_cannot_read_change_or_delete_records(
    client_a, client_b, tenant_a, tenant_b
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    fact = await _declare(
        client_a, company_a, "operating_fact", {"key": "support_24x7", "value": True}
    )

    listed = (await client_b.get(f"{BASE.format(company=company_b.id)}/records")).json()
    assert all(rows == [] for rows in listed.values())
    assert (await client_b.get(f"{BASE.format(company=company_a.id)}/records")).status_code == 404
    url_b = f"{BASE.format(company=company_b.id)}/records/operating_fact/{fact['id']}"
    assert (await client_b.patch(url_b, json={"value": False})).status_code == 404
    assert (await client_b.delete(url_b)).status_code == 404
    url_a = f"{BASE.format(company=company_a.id)}/records/operating_fact/{fact['id']}"
    assert (await client_b.patch(url_a, json={"value": False})).status_code == 404

    entry = _entry(await _snapshot(client_a, company_a), "facts", "support_24x7")
    assert entry["claims"][0]["value"] is True


async def test_declare_then_evidence_then_verify(client_a, tenant_a):
    _, company = tenant_a
    fact = await _declare(
        client_a, company, "operating_fact", {"key": "datacenter_tier3", "value": True}
    )
    verify = f"{BASE.format(company=company.id)}/records/operating_fact/{fact['id']}/verify"

    refused = await client_a.post(verify)
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "evidence_required")

    await _link_evidence(client_a, company, "company_operating_fact", fact["id"])
    snapshot = await _snapshot(client_a, company)
    claim = _entry(snapshot, "facts", "datacenter_tier3")["claims"][0]
    # Amb evidencia pero sense verificar: continua declarada, i la fotografia ho diu.
    assert (claim["source"], claim["has_evidence"]) == ("current_declared", True)
    pending = next(p for p in snapshot["pending_validation"] if p["subject"] == "operating_fact")
    assert pending["detail"] == "Amb evidencia enllacada, pendent de verificar"

    assert (await client_a.post(verify)).status_code == 200
    snapshot = await _snapshot(client_a, company)
    claim = _entry(snapshot, "facts", "datacenter_tier3")["claims"][0]
    assert (claim["source"], claim["has_evidence"]) == ("current_verified", True)
    assert _pending(snapshot) == []


async def test_a_company_without_history_starts_empty_but_can_declare(client_a, tenant_a):
    _, company = tenant_a
    snapshot = await _snapshot(client_a, company)
    assert all(
        snapshot[section] == []
        for section in ("certifications", "facts", "services", "solutions", "partners")
    )
    assert snapshot["pending_validation"] == []
    before = snapshot["fingerprint"]

    await _declare(client_a, company, "partner", {"name": "Operador Demo", "roles": []})
    after = await _snapshot(client_a, company)
    assert after["fingerprint"] != before
    assert _pending(after) == [("accreditation_pending", "partner", "Operador Demo")]


# --------------------------------------------------------------------------- #
# Dades anteriors a la regla "verificat nomes amb evidencia"                   #
# --------------------------------------------------------------------------- #


async def test_a_legacy_verified_row_is_shown_as_declared_and_stays_editable(
    client_a, tenant_a, db
):
    """Una fila antiga marcada `verified` sense cap document: la fotografia la
    compta com a declarada, i editar-ne l'abast no queda bloquejat."""
    from app.models.credentials import CompanyCertification

    _, company = tenant_a
    legacy = CompanyCertification(
        company_id=company.id,
        name="ENS antic",
        standard_code="ENS",
        level="medium",
        verification_status="verified",
    )
    db.add(legacy)
    await db.commit()

    snapshot = await _snapshot(client_a, company)
    claim = _entry(snapshot, "certifications", "ENS")["claims"][0]
    assert (claim["source"], claim["accreditation_pending"], claim["has_evidence"]) == (
        "current_declared",
        True,
        False,
    )
    pending = next(
        p for p in snapshot["pending_validation"] if p["kind"] == "accreditation_pending"
    )
    assert pending["detail"] == "Marcat verificat pero sense cap evidencia enllacada"

    url = f"/api/v1/companies/{company.id}/certifications/{legacy.id}"
    edited = await client_a.patch(url, json={"scope_service_lines": ["voice_pbx"]})
    assert edited.status_code == 200, edited.text
    # La fila queda dient el mateix que la fotografia.
    assert edited.json()["verification_status"] == "pending_verification"
    assert edited.json()["scope_service_lines"] == ["voice_pbx"]

    # Demanar-ho explicitament continua exigint evidencia.
    refused = await client_a.patch(url, json={"verification_status": "verified"})
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "evidence_required")
    await _link_evidence(client_a, company, "company_certification", legacy.id)
    verified = await client_a.patch(url, json={"verification_status": "verified"})
    assert verified.status_code == 200, verified.text
    claim = _entry(await _snapshot(client_a, company), "certifications", "ENS")["claims"][0]
    assert (claim["source"], claim["scope_status"]) == ("current_verified", "structured")
