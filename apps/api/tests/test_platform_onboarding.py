"""Alta d'empresa i propietari per l'administracio de la plataforma.

El flux sencer, com el faria una persona: l'administrador dona d'alta l'empresa,
el propietari entra amb la temporal, no pot fer res fins que la canvia, i
despres treballa amb la seva empresa. I els limits: qui no administra no pot,
no hi ha duplicats, i la temporal no queda enlloc.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app import platform_admin
from app.core.security import verify_password
from app.models.audit import AuditLog
from app.models.enums import MembershipRole
from app.models.identity import Company, CompanyMembership, User
from app.services import platform_onboarding
from tests.conftest import create_user_with_company, login

pytestmark = pytest.mark.asyncio

URL = "/api/v1/platform/companies"
NEW_PASSWORD = "Indalec-2026-nova"


@pytest.fixture
async def admin(db) -> User:
    user, _ = await create_user_with_company(
        db, email=f"admin-{uuid.uuid4().hex[:8]}@plataforma-qa.com", company_name="Plataforma"
    )
    await platform_admin.set_platform_admin(db, user.email, value=True)
    return user


@pytest.fixture
async def admin_client(client_factory, admin):
    http_client = client_factory()
    await login(http_client, admin.email)
    return http_client


def _payload(**overrides) -> dict:
    return {
        "legal_name": "Indaleccius SL",
        "trade_name": "Indaleccius",
        "tax_id": "b-12 345 678",
        "owner_email": "Propietaria@Indaleccius-qa.com",
        "owner_full_name": "Propietària Indaleccius",
        **overrides,
    }


async def test_l_administracio_dona_d_alta_empresa_i_propietari(admin_client, admin, session):
    response = await admin_client.post(URL, json=_payload())

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["company"]["legal_name"] == "Indaleccius SL"
    assert body["company"]["tax_id"] == "B12345678"
    assert body["owner"]["email"] == "propietaria@indaleccius-qa.com"
    temporary = body["temporary_password"]
    assert len(temporary) >= 16
    assert any(c.isdigit() for c in temporary) and any(c.isalpha() for c in temporary)

    owner_email = "propietaria@indaleccius-qa.com"
    owner = await session.scalar(sa.select(User).where(User.email == owner_email))
    assert owner is not None and owner.is_active and owner.must_change_password
    assert not owner.is_superuser
    # Nomes el hash: la temporal no es desa en clar.
    assert owner.hashed_password != temporary
    assert verify_password(temporary, owner.hashed_password)
    membership = await session.scalar(
        sa.select(CompanyMembership).where(CompanyMembership.user_id == owner.id)
    )
    assert membership.role == MembershipRole.OWNER and membership.is_default
    assert str(membership.company_id) == body["company"]["id"]

    entries = (
        await session.scalars(
            sa.select(AuditLog).where(AuditLog.company_id == membership.company_id)
        )
    ).all()
    assert {(e.entity_type, e.user_id) for e in entries} == {
        ("companies", admin.id),
        ("users", admin.id),
    }
    assert all(temporary not in (e.summary or "") for e in entries)


async def test_el_propietari_ha_de_canviar_la_temporal_abans_de_fer_res(
    admin_client, client_factory
):
    created = (await admin_client.post(URL, json=_payload())).json()
    company_id = created["company"]["id"]
    owner = client_factory()
    await login(owner, "propietaria@indaleccius-qa.com", created["temporary_password"])

    me = await owner.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["must_change_password"] is True
    assert me.json()["memberships"][0]["role"] == "owner"

    # Amb la temporal, res mes: ni l'empresa, ni la configuracio.
    for path in ("/api/v1/companies", f"/api/v1/companies/{company_id}", "/api/v1/config"):
        blocked = await owner.get(path)
        assert blocked.status_code == 403, path
        assert blocked.json()["error"]["code"] == "password_change_required"

    same = await owner.post(
        "/api/v1/auth/password",
        json={"current_password": created["temporary_password"],
              "new_password": created["temporary_password"]},
    )
    assert same.status_code == 422

    changed = await owner.post(
        "/api/v1/auth/password",
        json={"current_password": created["temporary_password"], "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200, changed.text

    assert (await owner.get("/api/v1/auth/me")).json()["must_change_password"] is False
    companies = await owner.get("/api/v1/companies")
    assert companies.status_code == 200
    assert [c["legal_name"] for c in companies.json()] == ["Indaleccius SL"]

    # La temporal ja no val; la nova si.
    stale = await client_factory().post(
        "/api/v1/auth/login",
        json={"email": "propietaria@indaleccius-qa.com", "password": created["temporary_password"]},
    )
    assert stale.status_code == 401
    await login(client_factory(), "propietaria@indaleccius-qa.com", NEW_PASSWORD)


async def test_qui_no_administra_la_plataforma_no_pot_donar_d_alta(client_a, client, session):
    assert (await client.post(URL, json=_payload())).status_code == 401
    denied = await client_a.post(URL, json=_payload())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"
    named = sa.select(sa.func.count()).select_from(Company)
    assert await session.scalar(named.where(Company.legal_name == "Indaleccius SL")) == 0
    # El rol d'empresa (owner) no fa ningu administrador de la plataforma.
    assert (await client_a.get("/api/v1/auth/me")).json()["is_platform_admin"] is False


async def test_l_administrador_es_veu_com_a_tal(admin_client):
    me = (await admin_client.get("/api/v1/auth/me")).json()
    assert me["is_platform_admin"] is True and me["must_change_password"] is False


async def test_no_es_repeteix_ni_el_correu_ni_el_nif(admin_client, session, tenant_a):
    first = await admin_client.post(URL, json=_payload())
    assert first.status_code == 201
    companies_before = await session.scalar(sa.select(sa.func.count()).select_from(Company))
    users_before = await session.scalar(sa.select(sa.func.count()).select_from(User))

    same_email = await admin_client.post(
        URL, json=_payload(legal_name="Una altra SL", tax_id=None,
                           owner_email="PROPIETARIA@indaleccius-qa.com")
    )
    assert same_email.status_code == 409
    assert same_email.json()["error"]["code"] == "owner_email_in_use"

    existing_user = await admin_client.post(
        URL, json=_payload(legal_name="Una altra SL", tax_id=None, owner_email=tenant_a[0].email)
    )
    assert existing_user.status_code == 409

    same_tax = await admin_client.post(
        URL, json=_payload(legal_name="Una altra SL", tax_id="B12345678",
                           owner_email="altra@indaleccius-qa.com")
    )
    assert same_tax.status_code == 409
    assert same_tax.json()["error"]["code"] == "tax_id_in_use"

    # Cap fila a mitges.
    assert await session.scalar(sa.select(sa.func.count()).select_from(Company)) == companies_before
    assert await session.scalar(sa.select(sa.func.count()).select_from(User)) == users_before


async def test_les_dades_minimes_es_validen(admin_client):
    for broken in (
        _payload(legal_name=""),
        _payload(owner_email="no-es-un-correu"),
        _payload(owner_full_name=""),
        {**_payload(), "role": "admin"},
    ):
        assert (await admin_client.post(URL, json=broken)).status_code == 422


async def test_un_administrador_amb_la_temporal_pendent_tampoc_no_pot(
    admin_client, admin, db, client_factory
):
    admin.must_change_password = True
    await db.commit()
    blocked = await admin_client.post(URL, json=_payload())
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "password_change_required"


async def test_la_temporal_es_llegible_i_passa_la_regla_de_contrasenyes():
    seen = {platform_onboarding.temporary_password() for _ in range(50)}
    assert len(seen) == 50
    for value in seen:
        assert len(value) >= 16
        assert not set(value) & set("0O1lI")
        assert not value.isdigit() and not value.isalpha()


async def test_el_rol_de_plataforma_nomes_es_dona_per_linia_d_ordres(db, tenant_a, session):
    user, _ = tenant_a
    await platform_admin.set_platform_admin(db, user.email.upper(), value=True)
    assert [u.email for u in await platform_admin.list_platform_admins(db)] == [user.email]
    await platform_admin.set_platform_admin(db, user.email, value=False)
    assert await platform_admin.list_platform_admins(db) == []
    with pytest.raises(LookupError):
        await platform_admin.set_platform_admin(db, "ningu@enlloc-qa.com", value=True)
    summaries = (
        await session.scalars(sa.select(AuditLog.summary).where(AuditLog.entity_id == user.id))
    ).all()
    assert "Administracio de plataforma concedida" in summaries
    assert "Administracio de plataforma retirada" in summaries


async def test_el_cli_exigeix_repetir_el_correu(capsys):
    # Es comprova abans d'obrir cap connexio: no toca la base.
    assert platform_admin.main(["grant", "a@b-qa.com", "--confirm", "c@d-qa.com"]) == 2
    assert "no coincideix" in capsys.readouterr().err
