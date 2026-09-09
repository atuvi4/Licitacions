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


# --------------------------------------------------------------------------- #
# Limits d'una polissa                                                          #
# --------------------------------------------------------------------------- #


async def _policy(client, company_id, **overrides) -> str:
    response = await client.post(
        f"/api/v1/companies/{company_id}/insurances",
        json={"type": "civil_liability", "coverage_amount": "1000000", **overrides},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_els_limits_d_una_polissa_es_creen_i_es_llegeixen(client_a, tenant_a):
    """Cobertura total, per sinistre, per victima i agregat anual, per separat.

    Es tota la rao de ser de la taula: amb un sol import, un plec que exigeix un
    sublimit no es podia respondre mai.
    """
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    policy = await _policy(client_a, company.id)

    for kind, amount in (
        ("total", "1000000"),
        ("per_claim", "1000000"),
        ("per_victim", "300000"),
        ("annual_aggregate", "2000000"),
    ):
        response = await client_a.post(
            f"{base}/insurances/{policy}/limits",
            json={"kind": kind, "amount": amount, "currency": "EUR"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["kind"] == kind

    listed = await client_a.get(f"{base}/insurances/{policy}/limits")
    assert listed.status_code == 200
    assert {row["kind"]: row["amount"] for row in listed.json()} == {
        "total": "1000000.00",
        "per_claim": "1000000.00",
        "per_victim": "300000.00",
        "annual_aggregate": "2000000.00",
    }


async def test_un_limit_es_pot_corregir_i_esborrar(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    policy = await _policy(client_a, company.id)
    limit = (
        await client_a.post(
            f"{base}/insurances/{policy}/limits",
            json={"kind": "per_victim", "amount": "300000"},
        )
    ).json()["id"]

    patched = await client_a.patch(
        f"{base}/insurances/{policy}/limits/{limit}", json={"amount": "450000"}
    )
    assert patched.status_code == 200
    assert patched.json()["amount"] == "450000.00"

    deleted = await client_a.delete(f"{base}/insurances/{policy}/limits/{limit}")
    assert deleted.status_code == 204
    assert (await client_a.get(f"{base}/insurances/{policy}/limits")).json() == []


async def test_una_polissa_no_pot_tenir_dos_limits_del_mateix_tipus(client_a, tenant_a):
    """Dos "per victima" diferents serien dues respostes a la mateixa pregunta."""
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    policy = await _policy(client_a, company.id)
    payload = {"kind": "per_victim", "amount": "300000"}

    first = await client_a.post(f"{base}/insurances/{policy}/limits", json=payload)
    assert first.status_code == 201
    repeated = await client_a.post(f"{base}/insurances/{policy}/limits", json=payload)

    assert repeated.status_code == 409


async def test_no_es_poden_escriure_limits_a_la_polissa_d_un_altre_tenant(
    client_a, client_b, tenant_a, tenant_b
):
    """L'identificador de la polissa va a la URL: ha de comprovar-se el propietari.

    Sense la comprovacio del pare, filtrar el limit per `company_id` no serveix
    de res: el limit encara no existeix quan es decideix on escriure'l.
    """
    _, company_a = tenant_a
    _, company_b = tenant_b
    foreign = await _policy(client_b, company_b.id)

    # La polissa de B, demanada sota la URL de tenant de A.
    created = await client_a.post(
        f"/api/v1/companies/{company_a.id}/insurances/{foreign}/limits",
        json={"kind": "per_victim", "amount": "300000"},
    )
    listed = await client_a.get(
        f"/api/v1/companies/{company_a.id}/insurances/{foreign}/limits"
    )

    assert created.status_code == 404, "404 i no 403: A no ha de saber que existeix"
    assert listed.status_code == 404
    # I la polissa de B segueix sense limits.
    assert (
        await client_b.get(f"/api/v1/companies/{company_b.id}/insurances/{foreign}/limits")
    ).json() == []
