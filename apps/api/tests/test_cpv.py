"""Tests de CPV: cataleg, CPV de l'empresa i CPV per projecte."""

from __future__ import annotations


async def test_alta_de_cpv_crea_el_codi_al_cataleg(client_a, tenant_a):
    """El cataleg oficial no esta importat: el codi es dona d'alta en usar-lo."""
    _, company = tenant_a
    response = await client_a.post(
        f"/api/v1/companies/{company.id}/cpv-preferences",
        json={
            "cpv_code": "45314320",
            "description": "Treballs d'instal-lacio de cablejat informatic",
            "priority": 10,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["cpv_code"] == "45314320"
    assert body["description"] == "Treballs d'instal-lacio de cablejat informatic"
    assert body["active"] is True
    assert body["excluded"] is False

    catalog = await client_a.get("/api/v1/cpv-codes", params={"q": "45314320"})
    assert [item["code"] for item in catalog.json()] == ["45314320"]


async def test_codi_cpv_invalid(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.post(
        f"/api/v1/companies/{company.id}/cpv-preferences", json={"cpv_code": "123"}
    )
    assert response.status_code == 422


async def test_cpv_duplicat_a_lempresa(client_a, tenant_a):
    _, company = tenant_a
    url = f"/api/v1/companies/{company.id}/cpv-preferences"
    assert (await client_a.post(url, json={"cpv_code": "32412110"})).status_code == 201
    duplicate = await client_a.post(url, json={"cpv_code": "32412110"})
    assert duplicate.status_code == 409


async def test_actiu_i_exclos_son_dimensions_diferents(client_a, tenant_a):
    _, company = tenant_a
    url = f"/api/v1/companies/{company.id}/cpv-preferences"
    created = await client_a.post(url, json={"cpv_code": "45000000", "description": "Construccio"})
    cpv_id = created.json()["id"]

    updated = await client_a.patch(f"{url}/{cpv_id}", json={"active": False})
    assert updated.json()["active"] is False
    assert updated.json()["excluded"] is False

    excluded = await client_a.patch(f"{url}/{cpv_id}", json={"excluded": True})
    assert excluded.json()["excluded"] is True

    assert (await client_a.delete(f"{url}/{cpv_id}")).status_code == 204


async def test_cpv_dun_projecte(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    project_id = (await client_a.post(f"{base}/projects", json={"name": "Obra FTTH"})).json()["id"]

    added = await client_a.post(
        f"{base}/projects/{project_id}/cpvs",
        json={"cpv_code": "45314320", "description": "Cablejat informatic"},
    )
    assert added.status_code == 201
    assert [item["cpv_code"] for item in added.json()["cpvs"]] == ["45314320"]
    assert added.json()["cpvs"][0]["description"] == "Cablejat informatic"

    duplicate = await client_a.post(
        f"{base}/projects/{project_id}/cpvs", json={"cpv_code": "45314320"}
    )
    assert duplicate.status_code == 409

    # El CPV tambe apareix en llegir el projecte.
    project = await client_a.get(f"{base}/projects/{project_id}")
    assert [item["cpv_code"] for item in project.json()["cpvs"]] == ["45314320"]

    removed = await client_a.delete(f"{base}/projects/{project_id}/cpvs/45314320")
    assert removed.status_code == 200
    assert removed.json()["cpvs"] == []

    assert (
        await client_a.delete(f"{base}/projects/{project_id}/cpvs/45314320")
    ).status_code == 404


async def test_els_cpv_son_per_empresa(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    await client_b.post(
        f"/api/v1/companies/{company_b.id}/cpv-preferences", json={"cpv_code": "35125300"}
    )

    propis = await client_a.get(f"/api/v1/companies/{company_a.id}/cpv-preferences")
    assert propis.json() == []

    aliens = await client_a.get(f"/api/v1/companies/{company_b.id}/cpv-preferences")
    assert aliens.status_code == 404


async def test_cerca_de_cpv_per_prefix_de_codi_o_paraula_de_la_descripcio(client_a, tenant_a):
    """El contracte del cercador de la web (addendum 3B-2, C3): el codi (o un
    prefix) o una paraula de la descripcio del cataleg; el filtre de la llista
    s'aplica despres pel codi triat."""
    _, company = tenant_a
    for code, description in (
        ("98765432", "Radioenllacos de prova per al cercador"),
        ("98760000", "Cablejat de prova per al cercador"),
    ):
        response = await client_a.post(
            f"/api/v1/companies/{company.id}/cpv-preferences",
            json={"cpv_code": code, "description": description, "priority": 5},
        )
        assert response.status_code == 201

    by_prefix = await client_a.get("/api/v1/cpv-codes", params={"q": "98765"})
    assert [item["code"] for item in by_prefix.json()] == ["98765432"]

    by_word = await client_a.get("/api/v1/cpv-codes", params={"q": "radioenlla"})
    assert [item["code"] for item in by_word.json()] == ["98765432"]

    both = await client_a.get("/api/v1/cpv-codes", params={"q": "per al cercador"})
    assert sorted(item["code"] for item in both.json()) == ["98760000", "98765432"]

    nothing = await client_a.get("/api/v1/cpv-codes", params={"q": "paraula que no hi es"})
    assert nothing.json() == []


async def test_no_es_pot_afegir_un_cpv_a_un_projecte_daltri(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    foreign_project = (
        await client_b.post(
            f"/api/v1/companies/{company_b.id}/projects", json={"name": "Projecte alie"}
        )
    ).json()["id"]

    response = await client_a.post(
        f"/api/v1/companies/{company_a.id}/projects/{foreign_project}/cpvs",
        json={"cpv_code": "45314320"},
    )
    assert response.status_code == 404
