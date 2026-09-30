"""Consola de l'Administracio Tuvi: estat del client, llistat, fitxa i seguiment.

Els limits que es protegeixen:

* Nomes l'Administracio Tuvi hi entra (un propietari o un tecnic, 403).
* Un client nou comenca en pilot de 60 dies, amb qui en va fer l'alta.
* L'estat es registra i s'audita; `paused` encara no bloqueja ningu.
* La vista de seguiment son resums: cap document ni cap ruta d'empresa.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.enums import (
    AuditAction,
    CompanyPlatformStatus,
    MembershipRole,
    SourceType,
    WorkspaceStatus,
)
from app.models.identity import Company, CompanyMembership, User
from app.models.sources import Source
from app.models.tenders import Buyer, Tender
from app.models.workspace import BidWorkspace
from app.services import audit
from tests.conftest import TEST_PASSWORD, login
from tests.test_platform_onboarding import create_operator

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/platform/companies"
TODAY = datetime.now(UTC).date()


@pytest.fixture
async def operator(db) -> User:
    return await create_operator(db)


@pytest.fixture
async def op_client(client_factory, operator):
    http_client = client_factory()
    await login(http_client, operator.email)
    return http_client


def _payload(n: int = 1, **overrides) -> dict:
    return {
        "legal_name": f"Client {n} SL",
        "trade_name": f"Client {n}",
        "tax_id": f"B1000000{n}",
        "owner_email": f"owner{n}@client{n}-qa.com",
        "owner_full_name": f"Propietari {n}",
        **overrides,
    }


async def _onboard(op_client, n: int = 1, **overrides) -> dict:
    response = await op_client.post(BASE, json=_payload(n, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


async def _tender(db, title: str, buyer_name: str | None = None) -> Tender:
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    buyer = None
    if buyer_name:
        buyer = Buyer(
            name=buyer_name, source_id=source.id, external_identifier=f"b-{uuid.uuid4().hex[:6]}"
        )
        db.add(buyer)
        await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="1/2026",
        title=title,
        buyer_id=buyer.id if buyer else None,
    )
    db.add(tender)
    await db.commit()
    return tender


# --------------------------------------------------------------------------- #
# Alta: estat inicial                                                          #
# --------------------------------------------------------------------------- #


async def test_un_client_nou_comenca_en_pilot_de_60_dies_amb_qui_en_va_fer_l_alta(
    op_client, operator, session
):
    created = await _onboard(op_client)
    assert created["company"]["platform_status"] == "pilot"
    assert created["company"]["pilot_ends_at"] == (TODAY + timedelta(days=60)).isoformat()

    company = await session.get(Company, uuid.UUID(created["company"]["id"]))
    assert company.pilot_started_at == TODAY
    assert company.onboarded_by == operator.id
    assert company.status_changed_at is not None
    summary = await session.scalar(
        sa.select(AuditLog.summary).where(
            AuditLog.entity_id == company.id, AuditLog.entity_type == "companies"
        )
    )
    assert summary == f"Empresa donada d'alta en pilot fins al {company.pilot_ends_at.isoformat()}"


async def test_alta_activa_o_amb_fi_de_pilot_propia(op_client, session):
    active = await _onboard(op_client, 1, initial_status="active")
    assert active["company"]["platform_status"] == "active"
    assert active["company"]["pilot_ends_at"] is None

    end = (TODAY + timedelta(days=30)).isoformat()
    pilot = await _onboard(op_client, 2, pilot_ends_at=end)
    assert pilot["company"]["pilot_ends_at"] == end


async def test_l_estat_inicial_es_valida(op_client, session):
    before = await session.scalar(sa.select(sa.func.count()).select_from(Company))
    cases = (
        _payload(1, initial_status="paused"),
        _payload(
            2, initial_status="active", pilot_ends_at=(TODAY + timedelta(days=10)).isoformat()
        ),
        _payload(3, pilot_ends_at=(TODAY - timedelta(days=1)).isoformat()),
    )
    for payload in cases:
        assert (await op_client.post(BASE, json=payload)).status_code == 422, payload
    assert await session.scalar(sa.select(sa.func.count()).select_from(Company)) == before


# --------------------------------------------------------------------------- #
# Llistat                                                                      #
# --------------------------------------------------------------------------- #


async def test_el_llistat_porta_propietari_recomptes_i_pestanyes(op_client, db, tenant_a):
    first = await _onboard(op_client, 1)
    await _onboard(op_client, 2, initial_status="active")
    company_id = uuid.UUID(first["company"]["id"])
    # Dos usuaris i una candidatura al client 1.
    extra = User(
        email="tecnic@client1-qa.com", hashed_password=hash_password(TEST_PASSWORD), is_active=True
    )
    db.add(extra)
    await db.flush()
    db.add(
        CompanyMembership(user_id=extra.id, company_id=company_id, role=MembershipRole.TECHNICAL)
    )
    tender = await _tender(db, "Manteniment de xarxa")
    db.add(BidWorkspace(company_id=company_id, tender_id=tender.id))
    await db.commit()

    body = (await op_client.get(BASE)).json()
    # tenant_a (fixture) ja existia: entra com a activa.
    assert body["counts"] == {"all": 3, "pilot": 1, "active": 2, "paused": 0}
    row = next(item for item in body["items"] if item["id"] == str(company_id))
    assert row["owner"]["email"] == "owner1@client1-qa.com"
    assert row["owner"]["full_name"] == "Propietari 1"
    assert row["users_count"] == 2
    assert row["tenders_count"] == 1
    assert row["platform_status"] == "pilot"
    # L'alta ja es activitat: l'operador la va registrar.
    assert row["last_activity_at"] is not None
    other = next(item for item in body["items"] if item["id"] == str(tenant_a[1].id))
    assert other["platform_status"] == "active" and other["tenders_count"] == 0


async def test_filtres_per_estat_i_cerca(op_client):
    await _onboard(op_client, 1)
    await _onboard(op_client, 2, initial_status="active")

    pilots = (await op_client.get(BASE, params={"status": "pilot"})).json()
    assert [item["legal_name"] for item in pilots["items"]] == ["Client 1 SL"]
    # Les pestanyes no depenen del filtre.
    assert pilots["counts"]["all"] == 2

    by_owner = (await op_client.get(BASE, params={"q": "OWNER2@"})).json()["items"]
    assert [item["legal_name"] for item in by_owner] == ["Client 2 SL"]
    by_tax = (await op_client.get(BASE, params={"q": "b10000001"})).json()["items"]
    assert [item["legal_name"] for item in by_tax] == ["Client 1 SL"]
    assert (await op_client.get(BASE, params={"status": "archived"})).status_code == 422


async def test_nomes_l_administracio_tuvi_hi_entra(client, client_a, op_client):
    created = await _onboard(op_client)
    detail = f"{BASE}/{created['company']['id']}"
    for method, path, body in (
        ("get", BASE, None),
        ("get", detail, None),
        ("patch", f"{detail}/status", {"status": "active"}),
    ):
        assert (
            await getattr(client, method)(path, **({"json": body} if body else {}))
        ).status_code == 401
        denied = await getattr(client_a, method)(path, **({"json": body} if body else {}))
        assert denied.status_code == 403, (method, path)


# --------------------------------------------------------------------------- #
# Fitxa i seguiment                                                            #
# --------------------------------------------------------------------------- #


async def test_la_fitxa_separa_el_que_gestiona_tuvi_del_seguiment_del_client(
    op_client, operator, db
):
    created = await _onboard(op_client)
    company_id = uuid.UUID(created["company"]["id"])
    for title, status in (
        ("Estudi de mobilitat", WorkspaceStatus.PREPARING_BID),
        ("Pla de manteniment", WorkspaceStatus.SUBMITTED),
        ("Enllumenat", WorkspaceStatus.ANALYSIS),
        ("Descartada", WorkspaceStatus.NO_GO_APPROVED),
    ):
        tender = await _tender(db, title, buyer_name="Ajuntament de Figueres")
        db.add(BidWorkspace(company_id=company_id, tender_id=tender.id, status=status))
    await db.commit()

    body = (await op_client.get(f"{BASE}/{company_id}")).json()
    # Gestionat per Tuvi.
    assert body["tax_id"] == "B10000001"
    assert body["onboarded_by"]["email"] == operator.email
    assert body["status"]["platform_status"] == "pilot"
    assert body["status"]["pilot_started_at"] == TODAY.isoformat()
    assert body["owner"]["email"] == "owner1@client1-qa.com"
    assert body["owner"]["last_login_at"] is None
    # Espai del client: resums de nomes lectura.
    space = body["client_space"]
    assert set(space) == {"tenders", "recent_tenders", "recent_activity", "users"}
    assert space["tenders"] == {"total": 4, "active": 3, "preparing": 1, "submitted": 1}
    assert len(space["recent_tenders"]) == 4
    assert set(space["recent_tenders"][0]) == {"title", "buyer_name", "status", "deadline_at"}
    assert {t["buyer_name"] for t in space["recent_tenders"]} == {"Ajuntament de Figueres"}
    assert space["users"] == {"total": 1, "active_last_7_days": 0}
    alta = space["recent_activity"][-1]
    assert alta["by_platform"] is True and alta["actor"]["email"] == operator.email


async def test_l_activitat_del_client_i_els_usuaris_actius(op_client, db):
    created = await _onboard(op_client)
    company_id = uuid.UUID(created["company"]["id"])
    owner = await db.get(User, uuid.UUID(created["owner"]["id"]))
    owner.last_login_at = datetime.now(UTC) - timedelta(days=2)
    await audit.record(
        db,
        action=AuditAction.CREATE,
        company_id=company_id,
        user_id=owner.id,
        entity_type="bid_workspace",
        summary="Workspace de candidatura obert",
    )
    await db.commit()

    body = (await op_client.get(f"{BASE}/{company_id}")).json()
    latest = body["client_space"]["recent_activity"][0]
    assert latest["summary"] == "Workspace de candidatura obert"
    assert latest["by_platform"] is False
    assert latest["actor"]["email"] == owner.email
    assert body["client_space"]["users"] == {"total": 1, "active_last_7_days": 1}


async def test_una_empresa_que_no_existeix_es_404(op_client):
    assert (await op_client.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404
    missing = await op_client.patch(f"{BASE}/{uuid.uuid4()}/status", json={"status": "active"})
    assert missing.status_code == 404


# --------------------------------------------------------------------------- #
# Estat del client                                                             #
# --------------------------------------------------------------------------- #


async def _summaries(session, company_id) -> list[str]:
    rows = await session.scalars(
        sa.select(AuditLog.summary)
        .where(AuditLog.entity_id == company_id, AuditLog.action == AuditAction.UPDATE)
        .order_by(AuditLog.created_at)
    )
    return list(rows)


async def test_convertir_pausar_i_tornar_a_pilot_queda_auditat(op_client, session):
    created = await _onboard(op_client)
    company_id = created["company"]["id"]
    url = f"{BASE}/{company_id}/status"

    active = (await op_client.patch(url, json={"status": "active"})).json()
    assert active["status"]["platform_status"] == "active"
    paused = (await op_client.patch(url, json={"status": "paused"})).json()
    assert paused["status"]["platform_status"] == "paused"
    again = (await op_client.patch(url, json={"status": "pilot"})).json()
    assert again["status"]["pilot_started_at"] == TODAY.isoformat()
    assert again["status"]["pilot_ends_at"] == (TODAY + timedelta(days=60)).isoformat()

    new_end = (TODAY + timedelta(days=60)).isoformat()
    assert await _summaries(session, uuid.UUID(company_id)) == [
        "Estat del client: pilot -> activa",
        "Estat del client: activa -> pausada",
        f"Estat del client: pausada -> pilot (fi del pilot {new_end})",
    ]


async def test_allargar_el_pilot(op_client, session):
    created = await _onboard(op_client)
    company_id = created["company"]["id"]
    url = f"{BASE}/{company_id}/status"

    # Sense data nova no s'allarga res.
    assert (await op_client.patch(url, json={"status": "pilot"})).status_code == 422
    new_end = (TODAY + timedelta(days=90)).isoformat()
    extended = (
        await op_client.patch(url, json={"status": "pilot", "pilot_ends_at": new_end})
    ).json()
    assert extended["status"]["pilot_ends_at"] == new_end
    # L'inici no es mou.
    assert extended["status"]["pilot_started_at"] == TODAY.isoformat()
    assert len(await _summaries(session, uuid.UUID(company_id))) == 1


async def test_els_canvis_d_estat_es_validen_i_la_repeticio_no_fa_res(op_client, session):
    created = await _onboard(op_client, initial_status="active")
    company_id = created["company"]["id"]
    url = f"{BASE}/{company_id}/status"
    tomorrow = (TODAY + timedelta(days=1)).isoformat()

    assert (
        await op_client.patch(url, json={"status": "paused", "pilot_ends_at": tomorrow})
    ).status_code == 422
    past = (TODAY - timedelta(days=1)).isoformat()
    assert (
        await op_client.patch(url, json={"status": "pilot", "pilot_ends_at": past})
    ).status_code == 422
    assert (await op_client.patch(url, json={"status": "archived"})).status_code == 422
    same = await op_client.patch(url, json={"status": "active"})
    assert same.status_code == 200
    assert await _summaries(session, uuid.UUID(company_id)) == []


async def test_pausar_encara_no_bloqueja_el_client(op_client, client_factory):
    """Decisio de fase: l'estat es registra; el bloqueig d'acces vindra despres."""
    created = await _onboard(op_client)
    await op_client.patch(f"{BASE}/{created['company']['id']}/status", json={"status": "paused"})
    owner = client_factory()
    await login(owner, "owner1@client1-qa.com", created["temporary_password"])
    await owner.post(
        "/api/v1/auth/password",
        json={
            "current_password": created["temporary_password"],
            "new_password": "Client-2026-nova",
        },
    )
    assert (await owner.get(f"/api/v1/companies/{created['company']['id']}")).status_code == 200


# --------------------------------------------------------------------------- #
# La base imposa les regles                                                    #
# --------------------------------------------------------------------------- #


async def test_la_base_no_admet_un_pilot_sense_fi_ni_un_estat_inventat(db):
    db.add(Company(legal_name="Sense fi", platform_status=CompanyPlatformStatus.PILOT))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()

    db.add(
        Company(
            legal_name="Dates girades",
            platform_status=CompanyPlatformStatus.PILOT,
            pilot_started_at=date(2026, 10, 1),
            pilot_ends_at=date(2026, 9, 1),
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()

    # SQL cru: el CHECK el posa la base, no el model.
    with pytest.raises(IntegrityError):
        await db.execute(
            sa.text(
                "INSERT INTO companies (id, legal_name, platform_status, created_at, updated_at) "
                "VALUES (:id, 'Inventat', 'closed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": uuid.uuid4().hex if db.bind.dialect.name == "sqlite" else uuid.uuid4()},
        )
        await db.commit()
