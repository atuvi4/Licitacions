"""Semantic gate de 0052: la guarda del downgrade i la matriu de permisos.

* **Downgrade.** La guarda decideix per representabilitat (valors explicits), mai per
  una comparacio de dates: una fila d'abans de 0052 creada a prop de mitjanit UTC no
  es «incompatible» en cap zona horaria de sessio, i una dada material de 0052 ho es
  sempre. La guarda nomes llegeix.
* **Permisos.** Les rutes de qualificacio fan servir la politica del coneixement
  (`app/knowledge/access.py` i, per verificar, la de `/knowledge/records/.../verify`):
  qui nomes consulta llegeix i res mes.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.knowledge import vocabulary
from app.models.credentials import CompanyInsurance, CompanyRegistration
from app.models.enums import EquipmentType, RegistrationType
from app.models.people import CompanyPerson
from app.models.profile import CompanyFinancialYear
from app.models.projects import CompanyProject, CompanyProjectAnnualAmount
from app.models.resources import CompanyEquipment
from tests.test_company_knowledge_permissions_api import _role, _upload
from tests.test_migrations import API_ROOT

pytestmark = pytest.mark.asyncio

COMPANY = "/api/v1/companies/{company}"
QUALIFIED = (
    "company_financial_years",
    "company_projects",
    "company_insurances",
    "company_registrations",
    "company_people",
)


def _migration():
    path = API_ROOT / "alembic" / "versions" / "0052_company_qualification.py"
    spec = importlib.util.spec_from_file_location("migration_0052", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _incompatible(db) -> dict[str, int]:
    migration = _migration()
    found = await db.run_sync(lambda s: migration.incompatible_rows(s.connection()))
    return {meaning: n for meaning, n in found.items() if n}


async def _table_digest(db) -> str:
    rows = {}
    for table in (*QUALIFIED, "company_project_annual_amounts", "company_equipment"):
        result = await db.execute(sa.text(f"SELECT * FROM {table} ORDER BY id"))
        rows[table] = [[str(v) for v in row] for row in result]
    return json.dumps(rows, sort_keys=True)


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.commit()


# --------------------------------------------------------------------------- #
# Downgrade                                                                     #
# --------------------------------------------------------------------------- #


async def test_the_downgrade_guard_decides_by_representability_in_any_time_zone(db, tenant_a):
    _, company = tenant_a
    # Files «d'abans de 0052» (valors per defecte, cap `as_of`) creades a tocar de
    # mitjanit UTC, una a cada costat.
    edges = (
        datetime(2026, 1, 1, 23, 59, 30, tzinfo=UTC),
        datetime(2026, 1, 2, 0, 0, 30, tzinfo=UTC),
    )
    rows = []
    for n, created in enumerate(edges):
        rows += [
            CompanyFinancialYear(company_id=company.id, year=2020 + n, revenue=Decimal("1")),
            CompanyProject(company_id=company.id, name=f"Projecte {n}"),
            CompanyInsurance(company_id=company.id),
            CompanyRegistration(company_id=company.id, name=f"Registre {n}"),
            CompanyPerson(company_id=company.id, first_name=f"Persona {n}"),
        ]
        for row in rows[-5:]:
            row.created_at = created
            row.updated_at = created
    db.add_all(rows)
    await db.commit()
    assert all(row.as_of is None for row in rows), "cap backfill: `as_of` explicit"

    zones = ("UTC",)
    if db.bind.dialect.name == "postgresql":
        zones = ("UTC", "Pacific/Kiritimati", "Etc/GMT+12")  # +14 i -12
    before = await _table_digest(db)
    for zone in zones:
        if db.bind.dialect.name == "postgresql":
            await db.execute(sa.text(f"SET TIME ZONE '{zone}'"))
        assert await _incompatible(db) == {}, zone
    assert await _table_digest(db) == before, "la guarda nomes llegeix"

    # Qualsevol semantica de 0052 la torna incompatible, en totes les zones.
    year = rows[0]
    year.as_of = date(2026, 1, 1)
    project = rows[1]
    db.add(
        CompanyProjectAnnualAmount(
            company_id=company.id, project_id=project.id, year=2025, amount=Decimal("10")
        )
    )
    db.add(CompanyEquipment(company_id=company.id, equipment_type=EquipmentType.OTDR))
    registration = rows[3]
    registration.type = RegistrationType.SECTOR_OPERATOR_REGISTRY
    await db.commit()
    before = await _table_digest(db)
    for zone in zones:
        if db.bind.dialect.name == "postgresql":
            await db.execute(sa.text(f"SET TIME ZONE '{zone}'"))
        found = await _incompatible(db)
        assert found["imports executats per any"] == 1, zone
        assert found["equips o instruments declarats"] == 1, zone
        assert any("company_financial_years" in m for m in found), zone
        assert any("sector_operator_registry" in m for m in found), zone
    assert await _table_digest(db) == before, "la guarda nomes llegeix"
    if db.bind.dialect.name == "postgresql":
        await db.execute(sa.text("SET TIME ZONE 'UTC'"))
    migration = _migration()
    with pytest.raises(migration.DowngradeBlockedError) as refused:
        await db.run_sync(lambda s: migration.refuse_unless_representable_in_0051(s.connection()))
    assert migration.DOWNGRADE_BLOCKED in str(refused.value)
    assert await _table_digest(db) == before, "refusar no muta cap fila"


# --------------------------------------------------------------------------- #
# Permisos                                                                      #
# --------------------------------------------------------------------------- #

#: (ruta, payload d'alta, payload de modificacio, tipus de coneixement, tipus d'entitat)
RESOURCES = (
    ("financial-years", {"year": 2024, "revenue": "1"}, {"revenue": "2"},
     "financial_year", "company_financial_year"),
    ("people", {"first_name": "Persona"}, {"role": "Tecnica"}, "person", "company_person"),
    ("registrations", {"name": "Registre"}, {"issuer": "Organisme"},
     "registration", "company_registration"),
    ("insurances", {"type": "civil_liability"}, {"provider": "Asseguradora"},
     "insurance", "company_insurance"),
    ("projects", {"name": "Referencia"}, {"amount": "10"}, "project", "company_project"),
    ("knowledge/records/equipment", {"equipment_type": "otdr"}, {"model": "M1"},
     "equipment", "company_equipment"),
)  # fmt: skip


async def _create_all(client, base) -> dict[str, str]:
    ids = {}
    for path, create, _, kind, _ in RESOURCES:
        response = await client.post(f"{base}/{path}", json=create)
        assert response.status_code == 201, (path, response.text)
        ids[kind] = response.json()["id"]
    return ids


async def test_a_viewer_reads_qualification_but_changes_nothing(client_a, tenant_a, db):
    user, company = tenant_a
    base = COMPANY.format(company=company.id)
    ids = await _create_all(client_a, base)
    owner_doc = await _upload(
        client_a, company, entity_type="company_financial_year", entity_id=ids["financial_year"]
    )
    assert owner_doc.status_code == 201
    document = owner_doc.json()
    await _role(db, user, company, "viewer")

    for path, create, update, kind, entity_type in RESOURCES:
        listing = await client_a.get(
            f"{base}/knowledge/records" if path.startswith("knowledge") else f"{base}/{path}"
        )
        assert listing.status_code == 200, path
        assert (await client_a.post(f"{base}/{path}", json=create)).status_code == 403, path
        patched = await client_a.patch(f"{base}/{path}/{ids[kind]}", json=update)
        assert patched.status_code == 403, path
        deleted = await client_a.delete(f"{base}/{path}/{ids[kind]}")
        assert deleted.status_code == 403, path
        verified = await client_a.post(f"{base}/knowledge/records/{kind}/{ids[kind]}/verify")
        assert verified.status_code in (403, 404), (path, verified.status_code)
        uploaded = await _upload(client_a, company, entity_type=entity_type, entity_id=ids[kind])
        assert uploaded.status_code == 403, path
    limit = await client_a.post(
        f"{base}/insurances/{ids['insurance']}/limits", json={"kind": "per_claim", "amount": "1"}
    )
    assert limit.status_code == 403
    unlinked = await client_a.delete(
        f"{base}/documents/{document['id']}/links/{document['links'][0]['id']}"
    )
    assert unlinked.status_code == 403
    assert (await client_a.delete(f"{base}/documents/{document['id']}")).status_code == 403


async def test_a_writer_declares_and_edits_like_any_knowledge_but_does_not_delete(
    client_a, tenant_a, db
):
    user, company = tenant_a
    base = COMPANY.format(company=company.id)
    await _role(db, user, company, "technical")
    ids = await _create_all(client_a, base)
    for path, _, update, kind, entity_type in RESOURCES:
        patched = await client_a.patch(f"{base}/{path}/{ids[kind]}", json=update)
        assert patched.status_code == 200, (path, patched.text)
        bypass = await client_a.patch(
            f"{base}/{path}/{ids[kind]}", json={"verification_status": "verified"}
        )
        assert bypass.status_code == 422, (path, "PATCH no pot verificar")
        uploaded = await _upload(client_a, company, entity_type=entity_type, entity_id=ids[kind])
        assert uploaded.status_code == 201, path
        # Eliminar fisicament i verificar per `/verify`: propietari o administracio.
        assert (await client_a.delete(f"{base}/{path}/{ids[kind]}")).status_code == 403, path
        verified = await client_a.post(f"{base}/knowledge/records/{kind}/{ids[kind]}/verify")
        assert verified.status_code in (403, 404), path

    await _role(db, user, company, "owner")
    for path, _, _, kind, _ in RESOURCES:
        # Tampoc el propietari verifica per PATCH: el contracte es `/verify`.
        bypass = await client_a.patch(
            f"{base}/{path}/{ids[kind]}", json={"verification_status": "verified"}
        )
        assert bypass.status_code == 422, path
        verified = await client_a.post(f"{base}/knowledge/records/{kind}/{ids[kind]}/verify")
        assert verified.status_code == 200, (path, verified.text)
        assert (await client_a.delete(f"{base}/{path}/{ids[kind]}")).status_code == 204, path
    assert uuid.UUID(ids["project"])
