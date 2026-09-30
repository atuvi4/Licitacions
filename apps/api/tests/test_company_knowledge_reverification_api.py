"""Una verificacio val per al que es va verificar (fase 2B del motor).

    verificada + evidencia  --edicio material-->  pendent de verificar + evidencia

El document continua enllacat: la dada queda "declarada, pendent de verificar",
no "sense evidencia". Cal tornar-la a verificar amb `/verify`.

* val per a totes les dades actuals que es poden editar: certificacions, serveis,
  fets, solucions, capacitats, productes, tercers i els seus papers;
* un canvi editorial (`notes`) o desar el mateix valor no invalida res;
* una fila antiga `verified` sense evidencia continua sent editable i es
  normalitza a pendent.

Totes les dades son sintetiques.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.knowledge import vocabulary
from app.models.audit import AuditLog
from app.models.credentials import CompanyCertification
from app.models.documents import DocumentLink
from app.services.knowledge_evidence import KNOWLEDGE_KINDS

pytestmark = pytest.mark.asyncio

COMPANY = "/api/v1/companies/{company}"


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.commit()


async def _snapshot(client, company) -> dict:
    response = await client.get(f"{COMPANY.format(company=company.id)}/knowledge/snapshot")
    assert response.status_code == 200, response.text
    return response.json()


def _entry(snapshot: dict, section: str, key: str) -> dict:
    return next(e for e in snapshot[section] if e["key"] == key)


async def _declare(client, company, kind: str, payload: dict) -> dict:
    response = await client.post(
        f"{COMPANY.format(company=company.id)}/knowledge/records/{kind}", json=payload
    )
    assert response.status_code == 201, (kind, response.text)
    return response.json()


async def _evidence(client, company, kind: str, record_id) -> None:
    from tests.fixtures.pdf_builder import build_pdf

    response = await client.post(
        f"{COMPANY.format(company=company.id)}/documents",
        files={
            "file": (
                f"evidencia-{uuid.uuid4().hex[:6]}.pdf",
                build_pdf([f"Evidencia {uuid.uuid4()}"]),
                "application/pdf",
            )
        },
        data={"entity_type": str(KNOWLEDGE_KINDS[kind][0]), "entity_id": str(record_id)},
    )
    assert response.status_code == 201, response.text


async def _verify(client, company, kind: str, record_id) -> None:
    response = await client.post(
        f"{COMPANY.format(company=company.id)}/knowledge/records/{kind}/{record_id}/verify"
    )
    assert response.status_code == 200, response.text


async def _status(db, kind: str, record_id) -> str:
    model = KNOWLEDGE_KINDS[kind][1]
    return str(
        await db.scalar(
            sa.select(model.verification_status).where(model.id == uuid.UUID(record_id))
        )
    )


async def _links(db, kind: str, record_id) -> int:
    return await db.scalar(
        sa.select(sa.func.count())
        .select_from(DocumentLink)
        .where(
            DocumentLink.entity_type == KNOWLEDGE_KINDS[kind][0],
            DocumentLink.entity_id == uuid.UUID(record_id),
        )
    )


# --------------------------------------------------------------------------- #
# El cas complet                                                               #
# --------------------------------------------------------------------------- #


async def test_editing_a_verified_fact_sends_it_back_to_pending_and_keeps_the_evidence(
    client_a, tenant_a, db
):
    _, company = tenant_a
    fact = await _declare(
        client_a, company, "operating_fact", {"key": "support_24x7", "value": True}
    )
    await _evidence(client_a, company, "operating_fact", fact["id"])
    await _verify(client_a, company, "operating_fact", fact["id"])
    entry = _entry(await _snapshot(client_a, company), "facts", "support_24x7")
    assert (entry["source"], entry["accreditation_pending"]) == ("current_verified", False)

    url = f"{COMPANY.format(company=company.id)}/knowledge/records/operating_fact/{fact['id']}"
    edited = await client_a.patch(url, json={"value": False})
    assert edited.status_code == 200, edited.text

    # Torna a declarada, amb l'acreditacio pendent...
    snapshot = await _snapshot(client_a, company)
    entry = _entry(snapshot, "facts", "support_24x7")
    assert (entry["source"], entry["accreditation_pending"]) == ("current_declared", True)
    claim = entry["claims"][0]
    assert (claim["value"], claim["has_evidence"]) == (False, True)
    # ...pero l'evidencia continua enllacada: es "pendent de verificar", no "sense evidencia".
    assert await _links(db, "operating_fact", fact["id"]) == 1
    pending = next(p for p in snapshot["pending_validation"] if p["subject"] == "operating_fact")
    assert pending["detail"] == "Amb evidencia enllacada, pendent de verificar"
    assert await _status(db, "operating_fact", fact["id"]) == "pending_verification"
    summaries = list(
        await db.scalars(
            sa.select(AuditLog.summary).where(AuditLog.entity_type == "knowledge_operating_fact")
        )
    )
    assert sorted(summaries) == [
        "declarada",
        "modificada; torna a pendent de verificar",
        "verificada",
    ]

    # Tornar-la a verificar es un acte nou, i torna a comptar com a verificada.
    await _verify(client_a, company, "operating_fact", fact["id"])
    entry = _entry(await _snapshot(client_a, company), "facts", "support_24x7")
    assert (entry["source"], entry["accreditation_pending"]) == ("current_verified", False)
    assert entry["claims"][0]["has_evidence"] is True


# --------------------------------------------------------------------------- #
# Totes les dades editables                                                    #
# --------------------------------------------------------------------------- #


async def test_every_editable_record_loses_its_verification_on_a_material_edit(
    client_a, tenant_a, db
):
    _, company = tenant_a
    solution = await _declare(
        client_a, company, "solution", {"name": "Centraleta Demo", "service_line_code": "voice_pbx"}
    )
    product = await _declare(client_a, company, "product", {"manufacturer": "F", "model": "M1"})
    partner = await _declare(client_a, company, "partner", {"name": "Soci Demo"})
    records = {
        "operating_fact": await _declare(
            client_a, company, "operating_fact", {"key": "datacenter_tier3", "value": True}
        ),
        "solution": solution,
        "solution_capability": await _declare(
            client_a,
            company,
            "solution_capability",
            {
                "solution_id": solution["id"],
                "capability_code": "pbx.concurrent_calls",
                "status": "supported",
                "value_number": "60",
            },
        ),
        "product": product,
        "solution_product": await _declare(
            client_a,
            company,
            "solution_product",
            {"solution_id": solution["id"], "product_id": product["id"]},
        ),
        "partner": partner,
        "partner_role": await _declare(
            client_a, company, "partner_role", {"partner_id": partner["id"], "role": "supplier"}
        ),
    }
    edits = {
        "operating_fact": {"detail": "Ara en un altre centre"},
        "solution": {"edition": "Enterprise"},
        "solution_capability": {"value_number": "30"},
        "product": {"model": "M2"},
        "solution_product": {"valid_until": "2030-01-01"},
        "partner": {"name": "Soci Demo SL"},
        "partner_role": {"territory": "Girona"},
    }
    for kind, record in records.items():
        await _evidence(client_a, company, kind, record["id"])
        await _verify(client_a, company, kind, record["id"])
        assert await _status(db, kind, record["id"]) == "verified", kind

    base = f"{COMPANY.format(company=company.id)}/knowledge/records"
    for kind, record in records.items():
        response = await client_a.patch(f"{base}/{kind}/{record['id']}", json=edits[kind])
        assert response.status_code == 200, (kind, response.text)
        assert await _status(db, kind, record["id"]) == "pending_verification", kind
        assert await _links(db, kind, record["id"]) == 1, kind

    snapshot = await _snapshot(client_a, company)
    for section, key in (
        ("facts", "datacenter_tier3"),
        ("solutions", "Centraleta Demo"),
        ("capabilities", "pbx.concurrent_calls"),
        ("products", "F M2"),
        ("partners", "Soci Demo SL"),
    ):
        entry = _entry(snapshot, section, key)
        assert (entry["source"], entry["accreditation_pending"]) == ("current_declared", True), key
        assert entry["claims"][0]["has_evidence"] is True, key

    # I es poden tornar a verificar totes.
    for kind, record in records.items():
        await _verify(client_a, company, kind, record["id"])
        assert await _status(db, kind, record["id"]) == "verified", kind


async def test_certifications_and_services_follow_the_same_rule(client_a, tenant_a, db):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    cert = (
        await client_a.post(
            f"{base}/certifications",
            json={"name": "ENS", "standard_code": "ENS", "level": "medium"},
        )
    ).json()
    service = (
        await client_a.post(
            f"{base}/services", json={"name": "Centraleta", "service_line_code": "voice_pbx"}
        )
    ).json()
    for kind, record in (("certification", cert), ("service", service)):
        await _evidence(client_a, company, kind, record["id"])
        await _verify(client_a, company, kind, record["id"])

    # Certificacio: canviar-ne el nivell invalida la verificacio.
    cert_url = f"{base}/certifications/{cert['id']}"
    edited = await client_a.patch(cert_url, json={"standard_code": "ENS", "level": "high"})
    assert (edited.status_code, edited.json()["verification_status"]) == (
        200,
        "pending_verification",
    )
    entry = _entry(await _snapshot(client_a, company), "certifications", "ENS")
    assert (entry["source"], entry["accreditation_pending"]) == ("current_declared", True)
    assert entry["claims"][0]["has_evidence"] is True
    assert await _links(db, "certification", cert["id"]) == 1

    # Tambe l'abast i la vigencia, cada vegada.
    for change in ({"scope_service_lines": ["voice_pbx"]}, {"expires_at": "2030-01-01"}):
        await _verify(client_a, company, "certification", cert["id"])
        edited = await client_a.patch(cert_url, json=change)
        assert edited.json()["verification_status"] == "pending_verification", change

    # Editar i demanar `verified` alhora no salta el pas: la via es `/verify`.
    await _verify(client_a, company, "certification", cert["id"])
    both = await client_a.patch(
        cert_url,
        json={"standard_code": "ENS", "level": "medium", "verification_status": "verified"},
    )
    assert (both.status_code, both.json()["verification_status"]) == (200, "pending_verification")

    # Servei.
    edited = await client_a.patch(f"{base}/services/{service['id']}", json={"delivery_mode": "own"})
    assert (edited.status_code, edited.json()["verification_status"]) == (
        200,
        "pending_verification",
    )
    entry = _entry(await _snapshot(client_a, company), "services", "voice_pbx")
    assert (entry["source"], entry["claims"][0]["has_evidence"]) == ("current_declared", True)
    await _verify(client_a, company, "service", service["id"])
    entry = _entry(await _snapshot(client_a, company), "services", "voice_pbx")
    assert entry["source"] == "current_verified"


# --------------------------------------------------------------------------- #
# El que NO invalida                                                           #
# --------------------------------------------------------------------------- #


async def test_an_editorial_change_or_the_same_value_keeps_the_verification(client_a, tenant_a, db):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    cert = (
        await client_a.post(
            f"{base}/certifications",
            json={
                "name": "ENS",
                "standard_code": "ENS",
                "level": "medium",
                "expires_at": "2030-01-01",
            },
        )
    ).json()
    await _evidence(client_a, company, "certification", cert["id"])
    await _verify(client_a, company, "certification", cert["id"])
    fact = await _declare(
        client_a,
        company,
        "operating_fact",
        {"key": "support_24x7", "value": True, "detail": "Propi"},
    )
    await _evidence(client_a, company, "operating_fact", fact["id"])
    await _verify(client_a, company, "operating_fact", fact["id"])

    cert_url = f"{base}/certifications/{cert['id']}"
    # Una nota interna no canvia el que la certificacio afirma.
    noted = await client_a.patch(cert_url, json={"notes": "Renovacio prevista al maig"})
    assert noted.json()["verification_status"] == "verified"
    # Desar el mateix valor no es un canvi (un formulari envia tots els camps).
    same = await client_a.patch(
        cert_url,
        json={"name": "ENS", "standard_code": "ENS", "level": "medium", "expires_at": "2030-01-01"},
    )
    assert same.json()["verification_status"] == "verified"
    fact_url = f"{base}/knowledge/records/operating_fact/{fact['id']}"
    same = await client_a.patch(
        fact_url,
        json={"value": True, "detail": "Propi", "lifecycle": "current", "valid_until": None},
    )
    assert same.status_code == 200, same.text
    assert await _status(db, "operating_fact", fact["id"]) == "verified"

    snapshot = await _snapshot(client_a, company)
    assert _entry(snapshot, "certifications", "ENS")["source"] == "current_verified"
    assert _entry(snapshot, "facts", "support_24x7")["source"] == "current_verified"
    assert snapshot["pending_validation"] == [
        p for p in snapshot["pending_validation"] if p["kind"] == "scope_unknown"
    ]


# --------------------------------------------------------------------------- #
# Dades anteriors a la regla                                                   #
# --------------------------------------------------------------------------- #


async def test_a_legacy_verified_row_without_evidence_stays_editable_and_ends_pending(
    client_a, tenant_a, db
):
    _, company = tenant_a
    rows = [
        CompanyCertification(
            company_id=company.id, name=name, standard_code=code, verification_status="verified"
        )
        for name, code in (("ISO 9001 antiga", "ISO_9001"), ("ISO 14001 antiga", "ISO_14001"))
    ]
    db.add_all(rows)
    await db.commit()
    base = f"{COMPANY.format(company=company.id)}/certifications"

    # Tant si el canvi es material com si nomes es una nota: no es bloqueja, i es
    # normalitza al que la fotografia ja en deia.
    for row, change in zip(rows, ({"expires_at": "2031-01-01"}, {"notes": "revisar"}), strict=True):
        response = await client_a.patch(f"{base}/{row.id}", json=change)
        assert response.status_code == 200, response.text
        assert response.json()["verification_status"] == "pending_verification"

    snapshot = await _snapshot(client_a, company)
    for key in ("ISO_9001", "ISO_14001"):
        entry = _entry(snapshot, "certifications", key)
        assert (entry["source"], entry["claims"][0]["has_evidence"]) == ("current_declared", False)
    # I la via per verificar-les es la de sempre: primer evidencia, despres /verify.
    refused = await client_a.post(
        f"{COMPANY.format(company=company.id)}/knowledge/records/certification/{rows[0].id}/verify"
    )
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "evidence_required")
