"""Pas 6 (abans de les dades reals): la fitxa d'assegurança atomica i les linies directes
de les referencies d'execucio (0053).

* ASSEGURANCES: polissa + conjunt COMPLET de limits en una sola transaccio. Si res
  falla, no en queda res (ni polissa nova, ni conjunt parcial); un canvi del conjunt
  d'una polissa verificada la torna a pendent; cap tenant toca la d'un altre.
* PROJECTES: `service_line_codes` del vocabulari, independents del cataleg de serveis.
  La fotografia i l'analisi (R2) llegeixen `company_project_service_lines`, mai
  `project_services` (legacy).
* MIGRACIO 0053: backfill des de l'estructura antiga, guarda del downgrade
  (`downgrade_0053_incompatible_rows`) i reconstruccio dels enllacos legacy sense
  inventar serveis.
"""

from __future__ import annotations

import importlib.util
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.knowledge import vocabulary
from app.models.credentials import CompanyInsurance, CompanyInsuranceLimit
from app.models.profile import CompanyService
from app.models.projects import CompanyProject, CompanyProjectServiceLine, project_services
from tests.test_company_knowledge_permissions_api import _role
from tests.test_company_knowledge_records_api import _link_evidence
from tests.test_company_qualification_api import (
    _analyze,
    _fits,
    _qualification_tender,
    _qualified_twin,
)
from tests.test_migrations import API_ROOT

pytestmark = pytest.mark.asyncio

COMPANY = "/api/v1/companies/{company}"


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.commit()


def _code(response) -> str:
    return response.json()["error"]["code"]


async def _limits(db, insurance_id) -> dict[str, Decimal]:
    rows = (
        await db.execute(
            sa.select(CompanyInsuranceLimit.kind, CompanyInsuranceLimit.amount).where(
                CompanyInsuranceLimit.insurance_id == uuid.UUID(str(insurance_id))
            )
        )
    ).all()
    return {str(kind): Decimal(amount) for kind, amount in rows}


async def _count(db, model, **where) -> int:
    stmt = sa.select(sa.func.count()).select_from(model)
    for column, value in where.items():
        stmt = stmt.where(getattr(model, column) == value)
    return (await db.execute(stmt)).scalar_one()


# --------------------------------------------------------------------------- #
# ASSEGURANCES                                                                  #
# --------------------------------------------------------------------------- #


async def test_a_policy_and_its_limits_are_created_in_one_operation(client_a, tenant_a, db):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    created = await client_a.post(
        f"{base}/insurances",
        json={
            "type": "civil_liability",
            "limits": [
                {"kind": "per_claim", "amount": "600000"},
                {"kind": "employer_per_victim", "amount": "150000"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert {limit["kind"] for limit in body["limits"]} == {"per_claim", "employer_per_victim"}
    assert all(limit["currency"] == "EUR" for limit in body["limits"])
    assert await _limits(db, body["id"]) == {
        "per_claim": Decimal("600000"),
        "employer_per_victim": Decimal("150000"),
    }
    listed = (await client_a.get(f"{base}/insurances")).json()
    assert {limit["kind"] for limit in listed[0]["limits"]} == {"per_claim", "employer_per_victim"}


@pytest.mark.parametrize(
    "limits",
    [
        # El segon limit no es valid: cap polissa ni cap limit.
        [
            {"kind": "per_claim", "amount": "600000"},
            {"kind": "employer_per_victim", "amount": "-1"},
        ],
        # Dos limits del mateix tipus: el conjunt no es valid.
        [{"kind": "per_claim", "amount": "1"}, {"kind": "per_claim", "amount": "2"}],
        # Massa digits per a la columna: es refusa abans de tocar la base.
        [{"kind": "per_claim", "amount": "1"}, {"kind": "total", "amount": "1e20"}],
    ],
)
async def test_an_invalid_limit_leaves_neither_policy_nor_limits(client_a, tenant_a, db, limits):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    refused = await client_a.post(
        f"{base}/insurances", json={"type": "civil_liability", "limits": limits}
    )
    assert refused.status_code == 422, refused.text
    assert await _count(db, CompanyInsurance, company_id=company.id) == 0
    assert await _count(db, CompanyInsuranceLimit, company_id=company.id) == 0


async def test_the_limit_set_is_replaced_exactly(client_a, tenant_a, db):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    policy = (
        await client_a.post(
            f"{base}/insurances",
            json={"type": "civil_liability", "limits": [
                {"kind": "total", "amount": "600000"},
                {"kind": "per_victim", "amount": "300000"},
            ]},
        )
    ).json()  # fmt: skip
    kept_id = next(limit["id"] for limit in policy["limits"] if limit["kind"] == "total")
    updated = await client_a.patch(
        f"{base}/insurances/{policy['id']}",
        json={"limits": [
            {"kind": "total", "amount": "700000"},
            {"kind": "employer_per_victim", "amount": "150000"},
        ]},
    )  # fmt: skip
    assert updated.status_code == 200, updated.text
    assert await _limits(db, policy["id"]) == {
        "total": Decimal("700000"),
        "employer_per_victim": Decimal("150000"),
    }
    # El limit que es mante es la mateixa fila (actualitzada), no una d'esborrada i nova.
    assert kept_id in {limit["id"] for limit in updated.json()["limits"]}
    # Sense `limits`, el conjunt no es toca; amb una llista buida, queda sense cap.
    await client_a.patch(f"{base}/insurances/{policy['id']}", json={"provider": "Exemple"})
    assert len(await _limits(db, policy["id"])) == 2
    await client_a.patch(f"{base}/insurances/{policy['id']}", json={"limits": []})
    assert await _limits(db, policy["id"]) == {}


async def test_a_failed_update_keeps_the_original_policy_and_limits(client_a, tenant_a, db):
    """La base refusa la fitxa (vigencia al reves) despres d'aplicar el conjunt nou: tot
    torna enrere, tambe els limits."""
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    policy = (
        await client_a.post(
            f"{base}/insurances",
            json={"type": "civil_liability", "starts_at": "2026-01-01", "expires_at": "2026-12-31",
                  "limits": [{"kind": "total", "amount": "600000"},
                             {"kind": "per_victim", "amount": "300000"}]},
        )
    ).json()  # fmt: skip
    failed = await client_a.patch(
        f"{base}/insurances/{policy['id']}",
        json={"expires_at": "2025-06-30", "limits": [
            {"kind": "total", "amount": "1"},
            {"kind": "employer_per_victim", "amount": "150000"},
        ]},
    )  # fmt: skip
    assert failed.status_code == 409, failed.text
    assert await _limits(db, policy["id"]) == {
        "total": Decimal("600000"),
        "per_victim": Decimal("300000"),
    }
    after = (await client_a.get(f"{base}/insurances/{policy['id']}")).json()
    assert after["expires_at"] == "2026-12-31"


async def test_changing_the_limits_of_a_verified_policy_unverifies_it(client_a, tenant_a):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    policy = (
        await client_a.post(
            f"{base}/insurances",
            json={"type": "civil_liability", "limits": [{"kind": "total", "amount": "600000"}]},
        )
    ).json()
    await _link_evidence(client_a, company, "company_insurance", policy["id"])
    verify = f"{base}/knowledge/records/insurance/{policy['id']}/verify"
    assert (await client_a.post(verify)).status_code == 200
    # El mateix conjunt no es cap canvi: continua verificada.
    same = await client_a.patch(
        f"{base}/insurances/{policy['id']}",
        json={"limits": [{"kind": "total", "amount": "600000.00"}]},
    )
    assert same.json()["verification_status"] == "verified"
    changed = await client_a.patch(
        f"{base}/insurances/{policy['id']}",
        json={"limits": [{"kind": "total", "amount": "650000"}]},
    )
    assert changed.json()["verification_status"] == "pending_verification"


async def test_another_tenant_cannot_touch_the_policy_or_its_limits(
    client_a, client_b, tenant_a, tenant_b, db
):
    _, company_b = tenant_b
    _, company_a = tenant_a
    base_b = COMPANY.format(company=company_b.id)
    policy = (
        await client_b.post(
            f"{base_b}/insurances",
            json={"type": "civil_liability", "limits": [{"kind": "total", "amount": "600000"}]},
        )
    ).json()
    # Per la ruta d'un altre tenant: no existeix.
    hijack = await client_a.patch(f"{base_b}/insurances/{policy['id']}", json={"limits": []})
    assert hijack.status_code in (403, 404)
    # Per la seva propia ruta amb l'id de l'altre: tampoc.
    base_a = COMPANY.format(company=company_a.id)
    sideways = await client_a.patch(f"{base_a}/insurances/{policy['id']}", json={"limits": []})
    assert sideways.status_code == 404
    assert await _limits(db, policy["id"]) == {"total": Decimal("600000")}


# --------------------------------------------------------------------------- #
# PROJECTES: linies directes                                                    #
# --------------------------------------------------------------------------- #


async def test_a_project_states_its_service_lines_without_any_company_service(
    client_a, tenant_a, db
):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    assert await _count(db, CompanyService, company_id=company.id) == 0
    created = await client_a.post(
        f"{base}/projects",
        json={"name": "Centraleta historica", "status": "completed",
              "service_line_codes": ["voice_pbx", "voice_pbx", "dedicated_network"]},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["service_line_codes"] == ["dedicated_network", "voice_pbx"]
    assert project["services"] == []

    unknown = await client_a.patch(
        f"{base}/projects/{project['id']}", json={"service_line_codes": ["no_such_line"]}
    )
    assert unknown.status_code == 422
    replaced = await client_a.patch(
        f"{base}/projects/{project['id']}", json={"service_line_codes": ["access_ftth"]}
    )
    assert replaced.json()["service_line_codes"] == ["access_ftth"]
    untouched = await client_a.patch(f"{base}/projects/{project['id']}", json={"client_name": "X"})
    assert untouched.json()["service_line_codes"] == ["access_ftth"]
    cleared = await client_a.patch(
        f"{base}/projects/{project['id']}", json={"service_line_codes": []}
    )
    assert cleared.json()["service_line_codes"] == []


async def test_changing_the_lines_of_a_verified_project_unverifies_it(client_a, tenant_a):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    project = (
        await client_a.post(
            f"{base}/projects", json={"name": "Referencia", "service_line_codes": ["voice_pbx"]}
        )
    ).json()
    await _link_evidence(client_a, company, "company_project", project["id"])
    verify = f"{base}/knowledge/records/project/{project['id']}/verify"
    assert (await client_a.post(verify)).status_code == 200
    same = await client_a.patch(
        f"{base}/projects/{project['id']}", json={"service_line_codes": ["voice_pbx"]}
    )
    assert same.json()["verification_status"] == "verified"
    moved = await client_a.patch(
        f"{base}/projects/{project['id']}", json={"service_line_codes": ["voice_trunk"]}
    )
    assert moved.json()["verification_status"] == "pending_verification"


async def test_the_snapshot_reads_direct_lines_and_ignores_legacy_services(client_a, tenant_a):
    _, company = tenant_a
    base = COMPANY.format(company=company.id)
    service = (
        await client_a.post(
            f"{base}/services", json={"name": "Veu", "service_line_code": "voice_pbx"}
        )
    ).json()
    legacy_only = (
        await client_a.post(
            f"{base}/projects", json={"name": "Legacy", "service_ids": [service["id"]]}
        )
    ).json()
    direct = (
        await client_a.post(
            f"{base}/projects", json={"name": "Directe", "service_line_codes": ["voice_pbx"]}
        )
    ).json()
    # Que el servei d'avui desaparegui no canvia el que acredita una referencia historica.
    assert (await client_a.delete(f"{base}/services/{service['id']}")).status_code == 204
    snapshot = (await client_a.get(f"{base}/knowledge/snapshot")).json()
    lines = {
        claim["record_id"]: claim["value"]["lines"]
        for entry in snapshot["execution_references"]
        for claim in entry["claims"]
    }
    assert lines[direct["id"]] == ["voice_pbx"]
    assert lines[legacy_only["id"]] == []


async def test_r2_counts_a_historic_line_the_company_no_longer_offers(db, tenant_a, client_a):
    """La centraleta de la referencia B acredita `voice_pbx` encara que l'empresa ja no
    tingui cap servei de centraleta: R2 continua igual."""
    _, company = tenant_a
    tender, req, _ = await _qualification_tender(db)
    tender_id = tender.id
    ids = await _qualified_twin(db, client_a, company)
    before = _fits(await _analyze(db, company, tender_id), req)["execution"]
    pbx_service = (
        await db.execute(
            sa.select(CompanyService).where(
                CompanyService.company_id == company.id,
                CompanyService.service_line_code == "voice_pbx",
            )
        )
    ).scalar_one()
    await db.delete(pbx_service)
    await db.commit()
    after = _fits(await _analyze(db, company, tender_id), req)["execution"]
    assert (after.fit_result, after.explanation_code) == (
        before.fit_result,
        before.explanation_code,
    )
    assert str(ids["pbx"]) in after.support_json["record_ids"]


# --------------------------------------------------------------------------- #
# MIGRACIO 0053                                                                 #
# --------------------------------------------------------------------------- #


def _migration():
    path = API_ROOT / "alembic" / "versions" / "0053_project_service_lines.py"
    spec = importlib.util.spec_from_file_location("migration_0053", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _service(db, company, name, line, *, active=True):
    service = CompanyService(
        company_id=company.id, name=name, service_line_code=line, active=active
    )
    db.add(service)
    await db.flush()
    return service


async def _legacy_lines(db) -> set[tuple[str, str]]:
    """Les linies tal com les llegia 0052: les dels serveis vinculats."""
    rows = await db.execute(
        sa.select(project_services.c.project_id, CompanyService.service_line_code)
        .join(CompanyService, CompanyService.id == project_services.c.service_id)
        .where(CompanyService.service_line_code.is_not(None))
    )
    return {(str(p), line) for p, line in rows}


async def _direct_lines(db) -> set[tuple[str, str]]:
    rows = await db.execute(
        sa.select(CompanyProjectServiceLine.project_id, CompanyProjectServiceLine.service_line_code)
    )
    return {(str(p), line) for p, line in rows}


async def test_0053_backfill_copies_exactly_what_0052_read(db, tenant_a):
    _, company = tenant_a
    voice = await _service(db, company, "Veu", "voice_pbx")
    voice_again = await _service(db, company, "Veu 2", "voice_pbx", active=False)
    net = await _service(db, company, "Xarxa", "dedicated_network")
    no_line = await _service(db, company, "Sense linia", None)
    a = CompanyProject(company_id=company.id, name="A")
    a.services = [voice, voice_again, net, no_line]
    b = CompanyProject(company_id=company.id, name="B")
    b.services = [no_line]
    db.add_all([a, b])
    await db.commit()
    migration = _migration()
    await db.execute(sa.text(migration.BACKFILL))
    await db.commit()
    assert (
        await _direct_lines(db)
        == await _legacy_lines(db)
        == {
            (str(a.id), "voice_pbx"),
            (str(a.id), "dedicated_network"),
        }
    )
    assert await _count(db, CompanyProjectServiceLine) == 2, "sense duplicats"


async def _legacy_rows(db) -> list[tuple[str, str, str]]:
    """Totes les files de `project_services`, tal com son (per veure que res no les toca)."""
    rows = await db.execute(
        sa.select(
            project_services.c.project_id,
            project_services.c.service_id,
            project_services.c.created_at,
        ).order_by(project_services.c.project_id, project_services.c.service_id)
    )
    return [(str(p), str(s), str(c)) for p, s, c in rows]


def _guard(db, migration):
    return db.run_sync(
        lambda s: {k: n for k, n in migration.incompatible_rows(s.connection()).items() if n}
    )


async def _project(db, company, name, *, services=(), lines=()):
    project = CompanyProject(company_id=company.id, name=name)
    project.services = list(services)
    project.replace_service_lines(lines)
    db.add(project)
    await db.commit()
    return project


async def test_0053_downgrade_passes_only_with_identical_sets_and_touches_nothing(db, tenant_a):
    """1. A == B a tots els projectes: passa. Diversos serveis de la mateixa linia donen
    el mateix conjunt. La guarda nomes llegeix i el downgrade no toca `project_services`."""
    _, company = tenant_a
    migration = _migration()
    voice = await _service(db, company, "Veu", "voice_pbx")
    voice_2 = await _service(db, company, "Veu 2", "voice_pbx", active=False)
    net = await _service(db, company, "Xarxa", "dedicated_network")
    no_line = await _service(db, company, "Sense linia", None)
    await _project(db, company, "A", services=[voice, voice_2, net, no_line],
                   lines=["voice_pbx", "dedicated_network"])  # fmt: skip
    await _project(db, company, "Buit", services=[no_line])
    before = await _legacy_rows(db)
    assert await _guard(db, migration) == {}
    await db.run_sync(lambda s: migration.refuse_unless_representable_in_0052(s.connection()))
    assert await _legacy_rows(db) == before
    assert await _legacy_lines(db) == await _direct_lines(db)
    assert not hasattr(migration, "RECONSTRUCT"), "el downgrade no reconstrueix cap enllac"


@pytest.mark.parametrize("case", ["direct_without_link", "compatible_but_unlinked", "extra_legacy"])
async def test_0053_downgrade_refuses_any_set_difference_without_touching_rows(db, tenant_a, case):
    """2. Linia directa sense enllac -> refusa. 4. Encara que hi hagi un servei compatible
    no vinculat -> refusa. 3. Enllac legacy de mes -> refusa. 5. El refus no toca res."""
    _, company = tenant_a
    migration = _migration()
    voice = await _service(db, company, "Veu", "voice_pbx")
    trunk = await _service(db, company, "Troncal", "voice_trunk")
    if case == "direct_without_link":
        await _project(db, company, "P", services=[voice], lines=["voice_pbx", "access_ftth"])
        expected = "cap enllac legacy existent"
    elif case == "compatible_but_unlinked":
        # Hi ha un servei `voice_trunk` a l'empresa, pero el projecte no hi esta vinculat.
        assert trunk.service_line_code == "voice_trunk"
        await _project(db, company, "P", services=[voice], lines=["voice_pbx", "voice_trunk"])
        expected = "cap enllac legacy existent"
    else:
        await _project(db, company, "P", services=[voice, trunk], lines=["voice_pbx"])
        expected = "enllacos legacy"
    before_links = await _legacy_rows(db)
    before_lines = await _direct_lines(db)
    found = await _guard(db, migration)
    assert list(found.values()) == [1] and expected in next(iter(found))
    with pytest.raises(migration.DowngradeBlockedError, match=migration.DOWNGRADE_BLOCKED):
        await db.run_sync(lambda s: migration.refuse_unless_representable_in_0052(s.connection()))
    assert await _legacy_rows(db) == before_links
    assert await _direct_lines(db) == before_lines
    assert migration.DOWNGRADE_BLOCKED == "downgrade_0053_incompatible_rows"


# --------------------------------------------------------------------------- #
# CPV DEL PROJECTE                                                              #
# --------------------------------------------------------------------------- #


async def _cpv_project(client, company) -> tuple[str, dict]:
    base = COMPANY.format(company=company.id)
    project = (await client.post(f"{base}/projects", json={"name": "Referencia CPV"})).json()
    return f"{base}/projects/{project['id']}", project


async def _cpv_audits(db, project_id) -> list[str]:
    from app.models.audit import AuditLog

    rows = await db.execute(
        sa.select(AuditLog.summary)
        .where(AuditLog.entity_id == uuid.UUID(str(project_id)))
        .order_by(AuditLog.created_at)
    )
    return [summary for (summary,) in rows if summary and summary.startswith("cpv")]


@pytest.mark.parametrize("role", ["viewer", "technical", "bid_manager", "owner"])
async def test_cpv_permissions_follow_the_knowledge_edit_policy(client_a, tenant_a, db, role):
    """Llegir: tothom. Afegir i treure: qui pot editar coneixement (treure un CPV
    modifica el projecte, no l'esborra)."""
    user, company = tenant_a
    url, _ = await _cpv_project(client_a, company)
    assert (await client_a.post(f"{url}/cpvs", json={"cpv_code": "64200000"})).status_code == 201
    await _role(db, user, company, role)
    assert (await client_a.get(f"{url}/cpvs")).status_code == 200
    added = await client_a.post(f"{url}/cpvs", json={"cpv_code": "32500000"})
    removed = await client_a.delete(f"{url}/cpvs/64200000")
    if role == "viewer":
        assert added.status_code == 403 and _code(added) == "permission_denied"
        assert removed.status_code == 403 and _code(removed) == "permission_denied"
        assert [c["cpv_code"] for c in (await client_a.get(f"{url}/cpvs")).json()] == ["64200000"]
    else:
        assert added.status_code == 201, added.text
        assert removed.status_code == 200, removed.text
        assert [c["cpv_code"] for c in removed.json()["cpvs"]] == ["32500000"]


async def test_a_cpv_change_unverifies_a_verified_project_and_a_duplicate_does_not(
    client_a, tenant_a, db
):
    _, company = tenant_a
    url, project = await _cpv_project(client_a, company)
    base = COMPANY.format(company=company.id)
    assert (await client_a.post(f"{url}/cpvs", json={"cpv_code": "64200000"})).status_code == 201
    verify = f"{base}/knowledge/records/project/{project['id']}/verify"

    async def verified() -> str:
        await _link_evidence(client_a, company, "company_project", project["id"])
        assert (await client_a.post(verify)).status_code == 200
        return (await client_a.get(url)).json()["verification_status"]

    assert await verified() == "verified"
    duplicate = await client_a.post(f"{url}/cpvs", json={"cpv_code": "64200000"})
    assert duplicate.status_code == 409
    assert (await client_a.get(url)).json()["verification_status"] == "verified"
    missing = await client_a.delete(f"{url}/cpvs/99999999")
    assert missing.status_code == 404
    assert (await client_a.get(url)).json()["verification_status"] == "verified"

    added = await client_a.post(f"{url}/cpvs", json={"cpv_code": "32500000"})
    assert added.json()["verification_status"] == "pending_verification"
    assert await verified() == "verified"
    removed = await client_a.delete(f"{url}/cpvs/32500000")
    assert removed.json()["verification_status"] == "pending_verification"
    # Un error no deixa rastre; cada canvi real, un sol registre d'auditoria.
    assert await _cpv_audits(db, project["id"]) == ["cpv afegit", "cpv afegit", "cpv retirat"]


async def test_another_tenant_cannot_read_or_change_the_cpvs(client_a, client_b, tenant_b, db):
    _, company_b = tenant_b
    url, project = await _cpv_project(client_b, company_b)
    assert (await client_b.post(f"{url}/cpvs", json={"cpv_code": "64200000"})).status_code == 201
    for response in (
        await client_a.get(f"{url}/cpvs"),
        await client_a.post(f"{url}/cpvs", json={"cpv_code": "32500000"}),
        await client_a.delete(f"{url}/cpvs/64200000"),
    ):
        assert response.status_code in (403, 404), response.text
    cpvs = (await client_b.get(f"{url}/cpvs")).json()
    assert [c["cpv_code"] for c in cpvs] == ["64200000"]
    assert await _cpv_audits(db, project["id"]) == ["cpv afegit"]


async def test_cpvs_do_not_feed_the_qualification_snapshot(client_a, tenant_a):
    """Els CPV son metadada de l'experiencia: no son linies i no entren a R2."""
    _, company = tenant_a
    url, project = await _cpv_project(client_a, company)
    base = COMPANY.format(company=company.id)
    before = (await client_a.get(f"{base}/knowledge/snapshot")).json()
    await client_a.post(f"{url}/cpvs", json={"cpv_code": "64200000"})
    after = (await client_a.get(f"{base}/knowledge/snapshot")).json()
    assert before["fingerprint"] == after["fingerprint"]
    lines = [c["value"]["lines"] for e in after["execution_references"] for c in e["claims"]]
    assert lines == [[]]
