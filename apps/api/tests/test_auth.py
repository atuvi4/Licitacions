"""Tests d'autenticacio i de sessio."""

from __future__ import annotations

from tests.conftest import TEST_PASSWORD


async def test_login_correcte_retorna_usuari_i_cookie(client, tenant_a):
    user, company = tenant_a
    response = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == user.email
    assert body["memberships"][0]["company"]["id"] == str(company.id)
    assert "licitacions_session" in response.cookies


async def test_login_amb_clau_incorrecta(client, tenant_a):
    user, _ = tenant_a
    response = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "clau-incorrecta"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


async def test_login_email_inexistent_dona_el_mateix_error(client, tenant_a):
    response = await client.post(
        "/api/v1/auth/login", json={"email": "ningu@no-existeix.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 401
    # Mateix missatge: no s'ha de poder deduir si l'email existeix.
    assert response.json()["error"]["message"] == "Credencials no valides."


async def test_me_sense_sessio_es_401(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_me_amb_sessio(client_a, tenant_a):
    user, _ = tenant_a
    response = await client_a.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == user.email


async def test_logout_invalida_la_cookie(client_a):
    assert (await client_a.get("/api/v1/auth/me")).status_code == 200
    assert (await client_a.post("/api/v1/auth/logout")).status_code == 200
    assert (await client_a.get("/api/v1/auth/me")).status_code == 401


async def test_endpoints_de_negoci_requereixen_sessio(client, tenant_a):
    _, company = tenant_a
    response = await client.get(f"/api/v1/companies/{company.id}/services")
    assert response.status_code == 401


async def test_canvi_de_contrasenya(client_a, tenant_a):
    user, _ = tenant_a
    response = await client_a.post(
        "/api/v1/auth/password",
        json={"current_password": TEST_PASSWORD, "new_password": "NovaClauSegura9!"},
    )
    assert response.status_code == 200

    logged_out = await client_a.post("/api/v1/auth/logout")
    assert logged_out.status_code == 200

    ko = await client_a.post(
        "/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
    )
    assert ko.status_code == 401

    ok = await client_a.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "NovaClauSegura9!"}
    )
    assert ok.status_code == 200


async def test_contrasenya_nova_massa_debil(client_a):
    response = await client_a.post(
        "/api/v1/auth/password",
        json={"current_password": TEST_PASSWORD, "new_password": "12345678901"},
    )
    assert response.status_code == 422
