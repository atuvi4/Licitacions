"""Tests dels endpoints de perfil d'empresa."""

from __future__ import annotations


async def test_actualitzar_dades_generals(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.patch(
        f"/api/v1/companies/{company.id}",
        json={"trade_name": "Nom Comercial", "tax_id": "b12345678", "city": "Girona"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trade_name"] == "Nom Comercial"
    assert body["tax_id"] == "B12345678"  # es normalitza a majuscules
    assert body["legal_name"] == "Empresa A"  # els camps no enviats no es toquen


async def test_nif_invalid_es_rebutja(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.patch(f"/api/v1/companies/{company.id}", json={"tax_id": "??"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_camp_desconegut_es_rebutja(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.patch(
        f"/api/v1/companies/{company.id}", json={"camp_inventat": "x"}
    )
    assert response.status_code == 422


async def test_cicle_complet_de_serveis(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}/services"

    assert (await client_a.get(base)).json() == []

    created = await client_a.post(
        base, json={"name": "Fibra optica", "category": "Infraestructura"}
    )
    assert created.status_code == 201
    service_id = created.json()["id"]
    assert created.json()["active"] is True

    listed = await client_a.get(base)
    assert len(listed.json()) == 1

    updated = await client_a.patch(f"{base}/{service_id}", json={"active": False})
    assert updated.status_code == 200
    assert updated.json()["active"] is False

    deleted = await client_a.delete(f"{base}/{service_id}")
    assert deleted.status_code == 204
    assert (await client_a.get(f"{base}/{service_id}")).status_code == 404


async def test_servei_duplicat_dona_conflicte(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}/services"
    assert (await client_a.post(base, json={"name": "CCTV"})).status_code == 201
    duplicate = await client_a.post(base, json={"name": "CCTV"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"


async def test_exercici_economic_unic_per_any(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}/financial-years"
    assert (await client_a.post(base, json={"year": 2024, "revenue": "1000000"})).status_code == 201
    duplicate = await client_a.post(base, json={"year": 2024, "revenue": "2000000"})
    assert duplicate.status_code == 409


async def test_consultar_les_preferencies_no_les_crea(client_a, tenant_a, engine, db):
    """Un GET no escriu: sense fila, es retornen els defaults i prou.

    Abans la primera lectura inseria la fila. Ara neix a la primera escriptura
    explicita, i nomes una vegada.
    """
    import sqlalchemy as sa

    from app.models.profile import CompanyContractPreferences
    from tests.dml import DmlRecorder

    _, company = tenant_a
    url = f"/api/v1/companies/{company.id}/contract-preferences"
    rows = sa.select(sa.func.count()).select_from(CompanyContractPreferences).where(
        CompanyContractPreferences.company_id == company.id
    )

    with DmlRecorder(engine) as dml:
        for _ in range(3):
            response = await client_a.get(url)
            assert response.status_code == 200
            body = response.json()
            assert body["currency"] == "EUR"
            # Sense identificador inventat: la fila no existeix.
            assert body["id"] is None
            assert body["created_at"] is None and body["updated_at"] is None
    assert dml.statements == [], "cap GET pot escriure"
    assert await db.scalar(rows) == 0

    saved = await client_a.put(url, json={"minimum_interesting_contract": "30000"})
    assert saved.status_code == 200
    assert saved.json()["minimum_interesting_contract"] == "30000.00"
    assert saved.json()["id"] is not None
    assert await db.scalar(rows) == 1

    again = await client_a.put(url, json={"notes": "Nomes serveis."})
    assert again.json()["id"] == saved.json()["id"], "no se'n crea una segona"
    assert await db.scalar(rows) == 1

    read = (await client_a.get(url)).json()
    assert read["id"] == saved.json()["id"]
    assert read["minimum_interesting_contract"] == "30000.00"
    assert read["notes"] == "Nomes serveis."


async def test_certificacio_calcula_estat_temporal(client_a, tenant_a):
    from datetime import date, timedelta

    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}/certifications"

    caducada = await client_a.post(
        base,
        json={
            "name": "ISO 9001",
            "expires_at": (date.today() - timedelta(days=5)).isoformat(),
        },
    )
    assert caducada.status_code == 201
    assert caducada.json()["temporal_status"] == "expired"
    # L'estat temporal no es persisteix: nomes hi ha estat de verificacio.
    assert caducada.json()["verification_status"] == "pending_verification"

    vigent = await client_a.post(
        base,
        json={"name": "ISO 14001", "expires_at": (date.today() + timedelta(days=400)).isoformat()},
    )
    assert vigent.json()["temporal_status"] == "valid"


async def test_certificacio_amb_dates_incoherents(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.post(
        f"/api/v1/companies/{company.id}/certifications",
        json={"name": "ISO", "issued_at": "2024-01-01", "expires_at": "2023-01-01"},
    )
    assert response.status_code == 422


async def test_projecte_amb_serveis_vinculats(client_a, tenant_a):
    _, company = tenant_a
    services_url = f"/api/v1/companies/{company.id}/services"
    projects_url = f"/api/v1/companies/{company.id}/projects"

    service_id = (await client_a.post(services_url, json={"name": "Fibra"})).json()["id"]

    created = await client_a.post(
        projects_url,
        json={
            "name": "Desplegament FTTH",
            "client_name": "Ajuntament X",
            "client_type": "public",
            "amount": "281430.00",
            "start_date": "2025-01-10",
            "end_date": "2025-09-30",
            "service_ids": [service_id],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert [service["id"] for service in body["services"]] == [service_id]

    filtered = await client_a.get(projects_url, params={"min_amount": "200000"})
    assert len(filtered.json()) == 1
    empty = await client_a.get(projects_url, params={"min_amount": "500000"})
    assert empty.json() == []


async def test_projecte_no_accepta_servei_daltra_empresa(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    foreign_service = (
        await client_b.post(
            f"/api/v1/companies/{company_b.id}/services", json={"name": "Servei extern"}
        )
    ).json()["id"]

    response = await client_a.post(
        f"/api/v1/companies/{company_a.id}/projects",
        json={"name": "Projecte", "service_ids": [foreign_service]},
    )
    assert response.status_code == 422
