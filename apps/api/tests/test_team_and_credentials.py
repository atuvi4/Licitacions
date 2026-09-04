"""Tests d'equip huma, titulacions, registres i assegurances."""

from __future__ import annotations

from datetime import date, timedelta

PDF = b"%PDF-1.4 evidencia"


async def test_cicle_de_persona_i_titulacions(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"

    person = await client_a.post(
        f"{base}/people",
        json={
            "first_name": "Marta",
            "last_name": "Exemple",
            "role": "Cap de projectes",
            "years_experience": 12,
            "availability_status": "available",
        },
    )
    assert person.status_code == 201
    person_id = person.json()["id"]

    qualification = await client_a.post(
        f"{base}/people/{person_id}/qualifications",
        json={
            "name": "Enginyeria de Telecomunicacions",
            "type": "degree",
            "issuer": "Universitat Demo",
            "issued_at": "2010-06-30",
        },
    )
    assert qualification.status_code == 201
    assert qualification.json()["temporal_status"] == "unknown"
    qualification_id = qualification.json()["id"]

    listed = await client_a.get(f"{base}/people/{person_id}/qualifications")
    assert len(listed.json()) == 1

    # Evidencia documental de la titulacio.
    document = await client_a.post(
        f"{base}/documents",
        files={"file": ("titol.pdf", PDF, "application/pdf")},
        data={
            "category": "cv",
            "entity_type": "person_qualification",
            "entity_id": qualification_id,
            "relationship_type": "diploma",
        },
    )
    assert document.status_code == 201

    evidence = await client_a.get(f"{base}/evidence/person_qualification/{qualification_id}")
    assert len(evidence.json()) == 1

    updated = await client_a.patch(
        f"{base}/people/{person_id}/qualifications/{qualification_id}",
        json={"credential_number": "12345"},
    )
    assert updated.json()["credential_number"] == "12345"


async def test_esborrar_una_persona_neteja_les_evidencies_de_les_titulacions(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    person_id = (await client_a.post(f"{base}/people", json={"first_name": "Joan"})).json()["id"]
    qualification_id = (
        await client_a.post(
            f"{base}/people/{person_id}/qualifications", json={"name": "Curs PRL"}
        )
    ).json()["id"]
    document_id = (
        await client_a.post(
            f"{base}/documents",
            files={"file": ("prl.pdf", PDF, "application/pdf")},
            data={"entity_type": "person_qualification", "entity_id": qualification_id},
        )
    ).json()["id"]

    assert (await client_a.delete(f"{base}/people/{person_id}")).status_code == 204

    # El document sobreviu; l'enllac orfe no.
    document = await client_a.get(f"{base}/documents/{document_id}")
    assert document.status_code == 200
    assert document.json()["links"] == []


async def test_registre_amb_evidencia(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"

    registration = await client_a.post(
        f"{base}/registrations",
        json={
            "name": "ROLECE - Inscripcio",
            "type": "rolece",
            "registration_number": "ROLECE-0001",
            "issuer": "Ministeri",
            "issued_at": "2023-01-15",
        },
    )
    assert registration.status_code == 201
    assert registration.json()["type"] == "rolece"
    registration_id = registration.json()["id"]

    await client_a.post(
        f"{base}/documents",
        files={"file": ("rolece.pdf", PDF, "application/pdf")},
        data={
            "category": "registration",
            "entity_type": "company_registration",
            "entity_id": registration_id,
            "relationship_type": "accreditation",
        },
    )
    evidence = await client_a.get(f"{base}/evidence/company_registration/{registration_id}")
    assert evidence.json()[0]["relationship_type"] == "accreditation"


async def test_assegurança_calcula_vigencia(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"

    response = await client_a.post(
        f"{base}/insurances",
        json={
            "type": "civil_liability",
            "provider": "Asseguradora Demo",
            "policy_number": "RC-1",
            "coverage_amount": "1000000",
            "starts_at": (date.today() - timedelta(days=30)).isoformat(),
            "expires_at": (date.today() + timedelta(days=20)).isoformat(),
        },
    )
    assert response.status_code == 201
    assert response.json()["temporal_status"] == "expiring_soon"
    assert response.json()["coverage_amount"] == "1000000.00"


async def test_assegurança_amb_dates_incoherents(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.post(
        f"/api/v1/companies/{company.id}/insurances",
        json={"type": "civil_liability", "starts_at": "2026-01-01", "expires_at": "2025-01-01"},
    )
    assert response.status_code == 422


async def test_aillament_de_persones_i_titulacions(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    foreign_person = (
        await client_b.post(f"/api/v1/companies/{company_b.id}/people", json={"first_name": "Alie"})
    ).json()["id"]

    # Persona d'una altra empresa, pero demanada sota la propia URL de tenant.
    response = await client_a.get(
        f"/api/v1/companies/{company_a.id}/people/{foreign_person}/qualifications"
    )
    assert response.status_code == 404

    creation = await client_a.post(
        f"/api/v1/companies/{company_a.id}/people/{foreign_person}/qualifications",
        json={"name": "Titulacio injectada"},
    )
    assert creation.status_code == 404
