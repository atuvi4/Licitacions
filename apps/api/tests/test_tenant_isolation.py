"""Aillament entre empreses (multi-tenant).

Aquests tests son els mes importants de la suite: qualsevol regressio aqui es
una fuga de dades entre clients.
"""

from __future__ import annotations


async def test_no_es_veu_lempresa_daltri(client_a, tenant_b):
    _, company_b = tenant_b
    response = await client_a.get(f"/api/v1/companies/{company_b.id}")
    # 404 i no 403: no confirmem l'existencia de recursos d'altres tenants.
    assert response.status_code == 404


async def test_no_es_pot_editar_lempresa_daltri(client_a, tenant_b):
    _, company_b = tenant_b
    response = await client_a.patch(
        f"/api/v1/companies/{company_b.id}", json={"legal_name": "Segrestada SL"}
    )
    assert response.status_code == 404


async def test_llistat_dempreses_nomes_mostra_les_propies(client_a, tenant_a, tenant_b):
    _, company_a = tenant_a
    response = await client_a.get("/api/v1/companies")
    ids = [company["id"] for company in response.json()]
    assert ids == [str(company_a.id)]


async def test_no_es_llisten_serveis_daltra_empresa(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    await client_b.post(f"/api/v1/companies/{company_b.id}/services", json={"name": "Secret"})

    propis = await client_a.get(f"/api/v1/companies/{company_a.id}/services")
    assert propis.json() == []

    aliens = await client_a.get(f"/api/v1/companies/{company_b.id}/services")
    assert aliens.status_code == 404


async def test_no_es_pot_llegir_un_recurs_daltri_per_id(client_a, client_b, tenant_a, tenant_b):
    """IDOR classic: URL de la propia empresa pero id de recurs d'una altra."""
    _, company_a = tenant_a
    _, company_b = tenant_b
    foreign_id = (
        await client_b.post(
            f"/api/v1/companies/{company_b.id}/certifications", json={"name": "ISO 27001"}
        )
    ).json()["id"]

    response = await client_a.get(
        f"/api/v1/companies/{company_a.id}/certifications/{foreign_id}"
    )
    assert response.status_code == 404

    deleted = await client_a.delete(
        f"/api/v1/companies/{company_a.id}/certifications/{foreign_id}"
    )
    assert deleted.status_code == 404


async def test_no_es_pot_enllacar_un_document_a_una_entitat_daltri(
    client_a, client_b, tenant_a, tenant_b
):
    _, company_a = tenant_a
    _, company_b = tenant_b

    foreign_project = (
        await client_b.post(
            f"/api/v1/companies/{company_b.id}/projects", json={"name": "Projecte alie"}
        )
    ).json()["id"]

    document_id = (
        await client_a.post(
            f"/api/v1/companies/{company_a.id}/documents",
            files={"file": ("evidencia.pdf", b"%PDF-1.4 demo", "application/pdf")},
        )
    ).json()["id"]

    response = await client_a.post(
        f"/api/v1/companies/{company_a.id}/documents/{document_id}/links",
        json={
            "entity_type": "company_project",
            "entity_id": foreign_project,
            "relationship_type": "certificate_of_good_execution",
        },
    )
    assert response.status_code == 404


async def test_no_es_pot_descarregar_un_document_daltri(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    foreign_document = (
        await client_b.post(
            f"/api/v1/companies/{company_b.id}/documents",
            files={"file": ("privat.pdf", b"%PDF-1.4 privat", "application/pdf")},
        )
    ).json()["id"]

    response = await client_a.get(
        f"/api/v1/companies/{company_a.id}/documents/{foreign_document}/download"
    )
    assert response.status_code == 404
