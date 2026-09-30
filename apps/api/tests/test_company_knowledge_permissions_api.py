"""Qui pot modificar el coneixement de l'empresa (fase 2B del motor).

La politica es a `app/knowledge/access.py`:

* llegir, qualsevol membre;
* declarar, editar i retirar: propietari, administracio, responsable de
  licitacions i tecnic. **Qui nomes consulta (`viewer`), no**;
* eliminar fisicament: propietari i administracio.

I no hi ha cap porta del darrere: les rutes que ja existien (certificacions,
serveis i els enllacos d'evidencia) apliquen la mateixa politica.

Totes les dades son sintetiques.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.knowledge import vocabulary
from app.models.credentials import CompanyCertification
from app.models.enums import MembershipRole
from app.models.identity import CompanyMembership
from app.models.knowledge import CompanyOperatingFact
from app.models.profile import CompanyService

pytestmark = pytest.mark.asyncio

COMPANY = "/api/v1/companies/{company}"
FACT = {"key": "support_24x7", "value": True}


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.commit()


async def _role(db, user, company, role: str) -> None:
    await db.execute(
        sa.update(CompanyMembership)
        .where(CompanyMembership.user_id == user.id, CompanyMembership.company_id == company.id)
        .values(role=MembershipRole(role))
    )
    await db.commit()


def _denied(response) -> bool:
    return response.status_code == 403 and response.json()["error"]["code"] == "permission_denied"


async def _fingerprint(client, company) -> str:
    response = await client.get(f"{COMPANY.format(company=company.id)}/knowledge/snapshot")
    assert response.status_code == 200, response.text
    return response.json()["fingerprint"]


async def _upload(client, company, **link) -> object:
    from tests.fixtures.pdf_builder import build_pdf

    return await client.post(
        f"{COMPANY.format(company=company.id)}/documents",
        files={
            "file": (
                f"doc-{uuid.uuid4().hex[:6]}.pdf",
                build_pdf([f"Document {uuid.uuid4()}"]),
                "application/pdf",
            )
        },
        data={key: str(value) for key, value in link.items()},
    )


# --------------------------------------------------------------------------- #
# Coneixement declarat                                                         #
# --------------------------------------------------------------------------- #


async def test_a_viewer_reads_everything_but_writes_nothing(client_a, tenant_a, db):
    user, company = tenant_a
    base = f"{COMPANY.format(company=company.id)}/knowledge"
    fact = (await client_a.post(f"{base}/records/operating_fact", json=FACT)).json()
    before = await _fingerprint(client_a, company)

    await _role(db, user, company, "viewer")
    for path in ("/snapshot", "/records", "/vocabulary", "/observations"):
        assert (await client_a.get(base + path)).status_code == 200, path

    url = f"{base}/records/operating_fact/{fact['id']}"
    declare = await client_a.post(
        f"{base}/records/operating_fact", json={"key": "datacenter_tier3", "value": True}
    )
    assert _denied(declare), declare.text
    assert _denied(await client_a.patch(url, json={"value": False}))
    assert _denied(await client_a.patch(url, json={"lifecycle": "retired"}))  # retirar
    assert _denied(await client_a.delete(url))  # eliminar
    assert _denied(
        await client_a.post(
            f"{base}/records/solution", json={"name": "X", "service_line_code": "voice_pbx"}
        )
    )

    # Res no ha canviat.
    assert await _fingerprint(client_a, company) == before
    assert await db.scalar(sa.select(sa.func.count()).select_from(CompanyOperatingFact)) == 1


@pytest.mark.parametrize("role", ["technical", "bid_manager"])
async def test_technical_and_bid_manager_declare_edit_and_retire_but_do_not_delete(
    role, client_a, tenant_a, db
):
    user, company = tenant_a
    await _role(db, user, company, role)
    base = f"{COMPANY.format(company=company.id)}/knowledge/records"

    declared = await client_a.post(f"{base}/operating_fact", json=FACT)
    assert declared.status_code == 201, declared.text
    url = f"{base}/operating_fact/{declared.json()['id']}"
    edited = await client_a.patch(url, json={"value": False, "detail": "Ja no"})
    assert edited.status_code == 200, edited.text
    retired = await client_a.patch(url, json={"lifecycle": "retired"})
    assert (retired.status_code, retired.json()["lifecycle"]) == (200, "retired")

    # Retirar es pot; eliminar fisicament, no.
    assert _denied(await client_a.delete(url))
    assert await db.scalar(sa.select(sa.func.count()).select_from(CompanyOperatingFact)) == 1


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_only_owner_and_admin_delete_for_real(role, client_a, tenant_a, db):
    user, company = tenant_a
    await _role(db, user, company, role)
    base = f"{COMPANY.format(company=company.id)}/knowledge/records"
    fact = (await client_a.post(f"{base}/operating_fact", json=FACT)).json()

    assert (await client_a.delete(f"{base}/operating_fact/{fact['id']}")).status_code == 204
    assert await db.scalar(sa.select(sa.func.count()).select_from(CompanyOperatingFact)) == 0


# --------------------------------------------------------------------------- #
# Les rutes que ja existien no son una porta del darrere                       #
# --------------------------------------------------------------------------- #


async def test_certifications_and_services_are_not_a_bypass_for_a_viewer(client_a, tenant_a, db):
    user, company = tenant_a
    base = COMPANY.format(company=company.id)
    resources = (
        ("certifications", {"name": "ENS", "standard_code": "ENS", "level": "medium"},
         {"standard_code": "ENS", "level": "high"}, CompanyCertification),
        ("services", {"name": "Centraleta", "service_line_code": "voice_pbx"},
         {"delivery_mode": "own"}, CompanyService),
    )  # fmt: skip
    created = {}
    for name, payload, _, _ in resources:
        response = await client_a.post(f"{base}/{name}", json=payload)
        assert response.status_code == 201, response.text
        created[name] = response.json()["id"]

    await _role(db, user, company, "viewer")
    for name, payload, change, model in resources:
        url = f"{base}/{name}/{created[name]}"
        assert (await client_a.get(f"{base}/{name}")).status_code == 200
        assert _denied(await client_a.post(f"{base}/{name}", json={**payload, "name": "Altra"}))
        assert _denied(await client_a.patch(url, json=change)), name
        assert _denied(await client_a.delete(url)), name
        assert await db.scalar(sa.select(sa.func.count()).select_from(model)) == 1

    # El tecnic hi pot treballar, pero no esborrar.
    await _role(db, user, company, "technical")
    for name, payload, change, _ in resources:
        url = f"{base}/{name}/{created[name]}"
        assert (await client_a.patch(url, json=change)).status_code == 200, name
        added = await client_a.post(f"{base}/{name}", json={**payload, "name": "Una altra"})
        assert added.status_code == 201, added.text
        assert _denied(await client_a.delete(url)), name

    await _role(db, user, company, "admin")
    for name, _, _, model in resources:
        assert (await client_a.delete(f"{base}/{name}/{created[name]}")).status_code == 204
        assert await db.scalar(sa.select(sa.func.count()).select_from(model)) == 1


async def test_a_viewer_cannot_change_the_evidence_of_knowledge(client_a, tenant_a, db):
    """Treure l'ultima evidencia desverifica una dada: tampoc es una porta per a
    qui nomes consulta."""
    user, company = tenant_a
    base = COMPANY.format(company=company.id)
    cert = (
        await client_a.post(
            f"{base}/certifications", json={"name": "ENS", "standard_code": "ENS", "level": "high"}
        )
    ).json()
    target = {"entity_type": "company_certification", "entity_id": cert["id"]}
    document = (await _upload(client_a, company, **target)).json()
    verify = f"{base}/knowledge/records/certification/{cert['id']}/verify"
    assert (await client_a.post(verify)).status_code == 200
    link = (await client_a.get(f"{base}/documents/{document['id']}/links")).json()[0]
    loose = (await _upload(client_a, company)).json()

    await _role(db, user, company, "viewer")
    # Llegir l'evidencia, si.
    evidence = await client_a.get(f"{base}/evidence/company_certification/{cert['id']}")
    assert evidence.status_code == 200 and len(evidence.json()) == 1
    # Posar-ne o treure'n, no.
    assert _denied(await _upload(client_a, company, **target))
    assert _denied(await client_a.post(f"{base}/documents/{loose['id']}/links", json=target))
    assert _denied(await client_a.delete(f"{base}/documents/{document['id']}/links/{link['id']}"))
    assert _denied(await client_a.delete(f"{base}/documents/{document['id']}"))
    snapshot = (await client_a.get(f"{base}/knowledge/snapshot")).json()
    assert snapshot["certifications"][0]["source"] == "current_verified"

    # El tecnic si que aporta i retira evidencia.
    await _role(db, user, company, "technical")
    linked = await client_a.post(f"{base}/documents/{loose['id']}/links", json=target)
    assert linked.status_code == 201, linked.text
    removed = await client_a.delete(f"{base}/documents/{loose['id']}/links/{linked.json()['id']}")
    assert removed.status_code == 204
