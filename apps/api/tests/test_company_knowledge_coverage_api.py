"""Cobertura territorial estructurada (0047) a l'API del coneixement.

El que es protegeix:

* **opcional i compatible**: una alta o una modificacio sense `coverage` es
  comporta com abans (`unknown`, cap area), i la lectura sempre en porta;
* **anada i tornada** per a fets, serveis i papers de partner: el que s'envia
  es el que es llegeix, en ordre canonic, i la fotografia ho conserva tal qual;
* **un canvi material de cobertura torna la dada verificada a pendent** i
  conserva l'evidencia; el mateix payload no es cap canvi i no desverifica;
* **els mateixos permisos** que la resta del coneixement (qui nomes consulta no
  hi escriu);
* **esborrar la dada neteja les seves arees**, tambe les dels papers d'un partner;
* **conservar no es comparar**: la fotografia no diu res d'un municipi absent.

Totes les dades son sintetiques.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.knowledge import vocabulary
from app.models.enums import MembershipRole
from app.models.identity import CompanyMembership
from app.models.knowledge import KnowledgeCoverageArea
from app.services.knowledge_evidence import KNOWLEDGE_KINDS

pytestmark = pytest.mark.asyncio

COMPANY = "/api/v1/companies/{company}"

ABC = [
    {"kind": "municipality", "label": "Municipi A"},
    {"kind": "municipality", "label": "Municipi B"},
    {"kind": "municipality", "label": "Municipi C"},
]
COMPLETE_ABC = {"completeness": "complete", "areas": ABC}
NEUTRAL = {"completeness": "unknown", "areas": []}


def _read(coverage: dict) -> dict:
    """El que l'API retorna per a aquest payload: camps complets, ordre canonic."""
    areas = sorted(
        (
            {
                "kind": a["kind"],
                "label": " ".join(a["label"].split()),
                "code": a.get("code"),
                "code_system": a.get("code_system"),
            }
            for a in coverage["areas"]
        ),
        key=lambda a: (a["kind"], a["code_system"] or "", a["code"] or "", a["label"]),
    )
    return {"completeness": coverage["completeness"], "areas": areas}


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.commit()


async def _snapshot(client, company) -> dict:
    response = await client.get(f"{COMPANY.format(company=company.id)}/knowledge/snapshot")
    assert response.status_code == 200, response.text
    return response.json()


def _entry(snapshot: dict, section: str, key: str) -> dict:
    return next(e for e in snapshot[section] if e["key"] == key)


async def _declare(client, company, kind: str, payload: dict) -> dict:
    response = await client.post(
        f"{COMPANY.format(company=company.id)}/knowledge/records/{kind}", json=payload
    )
    assert response.status_code == 201, (kind, response.text)
    return response.json()


async def _evidence(client, company, kind: str, record_id) -> None:
    from tests.fixtures.pdf_builder import build_pdf

    response = await client.post(
        f"{COMPANY.format(company=company.id)}/documents",
        files={
            "file": (
                f"evidencia-{uuid.uuid4().hex[:6]}.pdf",
                build_pdf([f"Evidencia {uuid.uuid4()}"]),
                "application/pdf",
            )
        },
        data={"entity_type": str(KNOWLEDGE_KINDS[kind][0]), "entity_id": str(record_id)},
    )
    assert response.status_code == 201, response.text


async def _verify(client, company, kind: str, record_id) -> None:
    response = await client.post(
        f"{COMPANY.format(company=company.id)}/knowledge/records/{kind}/{record_id}/verify"
    )
    assert response.status_code == 200, response.text


async def _status(db, kind: str, record_id) -> str:
    model = KNOWLEDGE_KINDS[kind][1]
    return str(
        await db.scalar(
            sa.select(model.verification_status).where(model.id == uuid.UUID(record_id))
        )
    )


async def _areas(db, record_id) -> list[tuple[str, str]]:
    rows = await db.execute(
        sa.select(KnowledgeCoverageArea.kind, KnowledgeCoverageArea.label)
        .where(KnowledgeCoverageArea.entity_id == uuid.UUID(record_id))
        .order_by(KnowledgeCoverageArea.kind, KnowledgeCoverageArea.label)
    )
    return [(str(kind), label) for kind, label in rows]


async def _role(db, user, company, role: str) -> None:
    await db.execute(
        sa.update(CompanyMembership)
        .where(CompanyMembership.user_id == user.id, CompanyMembership.company_id == company.id)
        .values(role=MembershipRole(role))
    )
    await db.commit()


def _urls(company) -> dict[str, str]:
    base = COMPANY.format(company=company.id)
    return {
        "operating_fact": f"{base}/knowledge/records/operating_fact",
        "partner_role": f"{base}/knowledge/records/partner_role",
        "service": f"{base}/services",
    }


async def _create_all(client, company) -> dict[str, dict]:
    """Un fet, un servei i un paper de partner, cadascun amb cobertura completa A/B/C."""
    urls = _urls(company)
    partner = await _declare(client, company, "partner", {"name": "Operador Demo", "roles": []})
    payloads = {
        "operating_fact": {
            "key": "own_ftth_infrastructure",
            "value": True,
            "coverage": COMPLETE_ABC,
        },
        "service": {"name": "FTTH", "service_line_code": "access_ftth", "coverage": COMPLETE_ABC},
        "partner_role": {
            "partner_id": partner["id"],
            "role": "wholesale_operator",
            "service_line_code": "access_ftth",
            "coverage": COMPLETE_ABC,
        },
    }
    created = {}
    for kind, payload in payloads.items():
        response = await client.post(urls[kind], json=payload)
        assert response.status_code == 201, (kind, response.text)
        created[kind] = response.json()
    return created


# --------------------------------------------------------------------------- #
# Compatibilitat: sense `coverage`, com abans                                  #
# --------------------------------------------------------------------------- #


async def test_without_coverage_everything_behaves_as_before_and_reads_unknown(
    client_a, tenant_a, db
):
    _, company = tenant_a
    urls = _urls(company)
    partner = await _declare(client_a, company, "partner", {"name": "Operador Demo", "roles": []})
    fact = (
        await client_a.post(urls["operating_fact"], json={"key": "support_24x7", "value": True})
    ).json()
    service = (
        await client_a.post(
            urls["service"], json={"name": "Centraleta", "service_line_code": "voice_pbx"}
        )
    ).json()
    role = (
        await client_a.post(
            urls["partner_role"],
            json={
                "partner_id": partner["id"],
                "role": "wholesale_operator",
                "service_line_code": "voice_pbx",
            },
        )
    ).json()
    for record in (fact, service, role):
        assert record["coverage"] == NEUTRAL
    assert await db.scalar(sa.select(sa.func.count()).select_from(KnowledgeCoverageArea)) == 0

    # Una modificacio que no parla de cobertura no la toca.
    edited = await client_a.patch(f"{urls['operating_fact']}/{fact['id']}", json={"detail": "24x7"})
    assert (edited.status_code, edited.json()["coverage"]) == (200, NEUTRAL)
    edited = await client_a.patch(
        f"{urls['service']}/{service['id']}", json={"delivery_mode": "own"}
    )
    assert (edited.status_code, edited.json()["coverage"]) == (200, NEUTRAL)

    # La fotografia tampoc no en diu res: `coverage` nul, cap clau al paper.
    snapshot = await _snapshot(client_a, company)
    assert _entry(snapshot, "facts", "support_24x7")["claims"][0]["coverage"] is None
    assert _entry(snapshot, "services", "voice_pbx")["claims"][0]["coverage"] is None
    assert (
        "coverage"
        not in _entry(snapshot, "partners", "Operador Demo")["claims"][0]["value"]["roles"][0]
    )

    # I la llista de dades (per editar-les) tambe la porta.
    records = (await client_a.get(f"{COMPANY.format(company=company.id)}/knowledge/records")).json()
    assert records["operating_facts"][0]["coverage"] == NEUTRAL
    assert records["partner_roles"][0]["coverage"] == NEUTRAL


# --------------------------------------------------------------------------- #
# Anada i tornada                                                              #
# --------------------------------------------------------------------------- #


async def test_coverage_round_trips_for_facts_services_and_partner_roles(client_a, tenant_a, db):
    _, company = tenant_a
    urls = _urls(company)
    created = await _create_all(client_a, company)
    for kind, record in created.items():
        assert record["coverage"] == _read(COMPLETE_ABC), kind
        read = await client_a.get(f"{urls[kind]}/{record['id']}")
        if read.status_code == 200:  # els serveis tenen GET individual; el coneixement, la llista
            assert read.json()["coverage"] == _read(COMPLETE_ABC), kind
        assert await _areas(db, record["id"]) == [
            ("municipality", "Municipi A"),
            ("municipality", "Municipi B"),
            ("municipality", "Municipi C"),
        ]

    # La fotografia la conserva tal qual, sense deduir-ne res.
    snapshot = await _snapshot(client_a, company)
    fact_claim = _entry(snapshot, "facts", "own_ftth_infrastructure")["claims"][0]
    assert fact_claim["coverage"] == _read(COMPLETE_ABC)
    assert _entry(snapshot, "services", "access_ftth")["claims"][0]["coverage"] == _read(
        COMPLETE_ABC
    )
    role_value = _entry(snapshot, "partners", "Operador Demo")["claims"][0]["value"]["roles"][0]
    assert role_value["coverage"] == _read(COMPLETE_ABC)
    assert "Municipi D" not in snapshot and "covers" not in fact_claim

    # Substituir-la: menys arees, una amb codi, completesa parcial. El que es
    # desa es exactament el que s'envia, normalitzat i ordenat.
    partial = {
        "completeness": "partial",
        "areas": [
            {
                "kind": "comarca",
                "label": "  Alt   Emporda ",
                "code": "02",
                "code_system": "IDESCAT",
            },
            {"kind": "municipality", "label": "Municipi A"},
        ],
    }
    for kind, record in created.items():
        edited = await client_a.patch(f"{urls[kind]}/{record['id']}", json={"coverage": partial})
        assert (edited.status_code, edited.json()["coverage"]) == (200, _read(partial)), kind
        assert await _areas(db, record["id"]) == [
            ("comarca", "Alt Emporda"),
            ("municipality", "Municipi A"),
        ], kind
    # Tres dades amb dues arees cadascuna: cap area orfena de les d'abans.
    assert await db.scalar(sa.select(sa.func.count()).select_from(KnowledgeCoverageArea)) == 6

    # Tornar a l'estat neutre tambe es pot: les arees desapareixen.
    for kind, record in created.items():
        edited = await client_a.patch(f"{urls[kind]}/{record['id']}", json={"coverage": NEUTRAL})
        assert (edited.status_code, edited.json()["coverage"]) == (200, NEUTRAL), kind
    assert await db.scalar(sa.select(sa.func.count()).select_from(KnowledgeCoverageArea)) == 0


async def test_the_fingerprint_moves_only_when_the_coverage_says_something(client_a, tenant_a):
    _, company = tenant_a
    urls = _urls(company)
    fact = (
        await client_a.post(
            urls["operating_fact"], json={"key": "own_ftth_infrastructure", "value": True}
        )
    ).json()
    base = (await _snapshot(client_a, company))["fingerprint"]
    url = f"{urls['operating_fact']}/{fact['id']}"

    # Dir expressament `unknown` sense arees no afirma res: la mateixa empremta.
    assert (await client_a.patch(url, json={"coverage": NEUTRAL})).status_code == 200
    assert (await _snapshot(client_a, company))["fingerprint"] == base

    seen = {base}
    for coverage in (
        COMPLETE_ABC,
        {"completeness": "partial", "areas": ABC},
        {"completeness": "partial", "areas": []},
        {"completeness": "complete", "areas": []},
    ):
        assert (await client_a.patch(url, json={"coverage": coverage})).status_code == 200
        fingerprint = (await _snapshot(client_a, company))["fingerprint"]
        assert fingerprint not in seen, coverage
        seen.add(fingerprint)


# --------------------------------------------------------------------------- #
# Verificacio                                                                  #
# --------------------------------------------------------------------------- #


async def test_a_material_coverage_change_sends_a_verified_record_back_to_pending(
    client_a, tenant_a, db
):
    _, company = tenant_a
    urls = _urls(company)
    created = await _create_all(client_a, company)
    for kind, record in created.items():
        await _evidence(client_a, company, kind, record["id"])
        await _verify(client_a, company, kind, record["id"])
        assert await _status(db, kind, record["id"]) == "verified", kind

    # El mateix payload (en un altre ordre i amb espais de mes) no es cap canvi.
    same = {
        "completeness": "complete",
        "areas": [
            {"kind": "municipality", "label": "Municipi C", "code": None, "code_system": None},
            {"kind": "municipality", "label": " Municipi  A"},
            {"kind": "municipality", "label": "Municipi B"},
        ],
    }
    for kind, record in created.items():
        edited = await client_a.patch(f"{urls[kind]}/{record['id']}", json={"coverage": same})
        assert edited.status_code == 200, (kind, edited.text)
        assert await _status(db, kind, record["id"]) == "verified", kind
    for section, key in (("facts", "own_ftth_infrastructure"), ("services", "access_ftth")):
        assert (
            _entry(await _snapshot(client_a, company), section, key)["source"] == "current_verified"
        )

    # Treure un municipi si que ho es: torna a pendent, amb l'evidencia encara enllacada.
    fewer = {"completeness": "complete", "areas": ABC[:2]}
    for kind, record in created.items():
        edited = await client_a.patch(f"{urls[kind]}/{record['id']}", json={"coverage": fewer})
        assert edited.status_code == 200, (kind, edited.text)
        assert await _status(db, kind, record["id"]) == "pending_verification", kind
        assert await _areas(db, record["id"]) == [
            ("municipality", "Municipi A"),
            ("municipality", "Municipi B"),
        ], kind
    snapshot = await _snapshot(client_a, company)
    for section, key in (("facts", "own_ftth_infrastructure"), ("services", "access_ftth")):
        entry = _entry(snapshot, section, key)
        assert (entry["source"], entry["claims"][0]["has_evidence"]) == ("current_declared", True)

    # Tambe canviar nomes la completesa, un cop tornada a verificar.
    for kind, record in created.items():
        await _verify(client_a, company, kind, record["id"])
        only_completeness = {"completeness": "partial", "areas": ABC[:2]}
        edited = await client_a.patch(
            f"{urls[kind]}/{record['id']}", json={"coverage": only_completeness}
        )
        assert edited.status_code == 200, (kind, edited.text)
        assert await _status(db, kind, record["id"]) == "pending_verification", kind


async def test_a_service_cannot_skip_verification_by_sending_verified_with_its_coverage(
    client_a, tenant_a, db
):
    """Com amb la resta de camps: editar i demanar `verified` alhora no salta `/verify`."""
    _, company = tenant_a
    urls = _urls(company)
    service = (
        await client_a.post(
            urls["service"], json={"name": "FTTH", "service_line_code": "access_ftth"}
        )
    ).json()
    url = f"{urls['service']}/{service['id']}"
    # Sense evidencia: refusat, i la cobertura no s'ha desat.
    refused = await client_a.patch(
        url, json={"coverage": COMPLETE_ABC, "verification_status": "verified"}
    )
    assert refused.status_code == 422, refused.text
    assert await _areas(db, service["id"]) == []
    assert await _status(db, "service", service["id"]) == "pending_verification"

    # Amb evidencia i ja verificat: demanar `verified` amb una cobertura nova
    # passa per la regla d'evidencia (la te) i es queda verificat, com la resta
    # de camps; sense demanar-ho, torna a pendent.
    await _evidence(client_a, company, "service", service["id"])
    await _verify(client_a, company, "service", service["id"])
    edited = await client_a.patch(url, json={"coverage": COMPLETE_ABC})
    assert (edited.status_code, edited.json()["verification_status"]) == (
        200,
        "pending_verification",
    )


# --------------------------------------------------------------------------- #
# Permisos                                                                     #
# --------------------------------------------------------------------------- #


async def test_a_viewer_cannot_touch_coverage_and_a_technical_can(client_a, tenant_a, db):
    user, company = tenant_a
    urls = _urls(company)
    created = await _create_all(client_a, company)
    partial = {"completeness": "partial", "areas": ABC[:1]}

    await _role(db, user, company, "viewer")
    for kind, record in created.items():
        denied = await client_a.patch(f"{urls[kind]}/{record['id']}", json={"coverage": partial})
        assert denied.status_code == 403, (kind, denied.text)
        assert denied.json()["error"]["code"] == "permission_denied"
        assert await _areas(db, record["id"]) == [
            ("municipality", "Municipi A"),
            ("municipality", "Municipi B"),
            ("municipality", "Municipi C"),
        ], kind
    # Llegir, si: la cobertura es part del coneixement visible.
    snapshot = await _snapshot(client_a, company)
    assert _entry(snapshot, "facts", "own_ftth_infrastructure")["claims"][0]["coverage"] is not None

    await _role(db, user, company, "technical")
    for kind, record in created.items():
        edited = await client_a.patch(f"{urls[kind]}/{record['id']}", json={"coverage": partial})
        assert (edited.status_code, edited.json()["coverage"]) == (200, _read(partial)), kind


async def test_another_company_cannot_read_or_change_coverage(
    client_a, client_b, tenant_a, tenant_b
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    created = await _create_all(client_a, company_a)
    urls_a = _urls(company_a)
    for kind, record in created.items():
        response = await client_b.patch(
            f"{urls_a[kind]}/{record['id']}",
            json={"coverage": {"completeness": "partial", "areas": []}},
        )
        assert response.status_code in (403, 404), (kind, response.text)
    snapshot_b = await _snapshot(client_b, company_b)
    assert snapshot_b["facts"] == [] and snapshot_b["services"] == []


# --------------------------------------------------------------------------- #
# Les arees son un conjunt                                                     #
# --------------------------------------------------------------------------- #


async def test_a_duplicated_area_makes_one_row_and_is_not_a_change(client_a, tenant_a, db):
    _, company = tenant_a
    urls = _urls(company)
    banyoles = {"kind": "municipality", "label": "Banyoles"}
    twice = {"completeness": "complete", "areas": [banyoles, {**banyoles, "label": " Banyoles  "}]}
    once = {"completeness": "complete", "areas": [banyoles]}

    # A l'alta: una sola fila i una sola area a la resposta, per als tres tipus.
    partner = await _declare(client_a, company, "partner", {"name": "Operador Demo", "roles": []})
    created = {}
    for kind, payload in (
        ("operating_fact", {"key": "own_ftth_infrastructure", "value": True, "coverage": twice}),
        ("service", {"name": "FTTH", "service_line_code": "access_ftth", "coverage": twice}),
        (
            "partner_role",
            {"partner_id": partner["id"], "role": "wholesale_operator", "coverage": twice},
        ),
    ):
        response = await client_a.post(urls[kind], json=payload)
        assert response.status_code == 201, (kind, response.text)
        created[kind] = response.json()
        assert created[kind]["coverage"] == _read(once), kind
        assert await _areas(db, created[kind]["id"]) == [("municipality", "Banyoles")], kind
    assert await db.scalar(sa.select(sa.func.count()).select_from(KnowledgeCoverageArea)) == 3

    # Un duplicat del que ja hi ha no es cap canvi: mateixa empremta i segueix verificat.
    for kind, record in created.items():
        await _evidence(client_a, company, kind, record["id"])
        await _verify(client_a, company, kind, record["id"])
    fingerprint = (await _snapshot(client_a, company))["fingerprint"]
    for kind, record in created.items():
        edited = await client_a.patch(f"{urls[kind]}/{record['id']}", json={"coverage": twice})
        assert (edited.status_code, edited.json()["coverage"]) == (200, _read(once)), kind
        assert await _status(db, kind, record["id"]) == "verified", kind
        assert await _areas(db, record["id"]) == [("municipality", "Banyoles")], kind
    assert (await _snapshot(client_a, company))["fingerprint"] == fingerprint

    # Afegir-ne una de diferent conserva Banyoles (la mateixa fila) i afegeix l'altra.
    fact = created["operating_fact"]
    row_id = await db.scalar(
        sa.select(KnowledgeCoverageArea.id).where(
            KnowledgeCoverageArea.entity_id == uuid.UUID(fact["id"])
        )
    )
    more = {
        "completeness": "complete",
        "areas": [banyoles, banyoles, {**banyoles, "kind": "comarca"}],
    }
    edited = await client_a.patch(f"{urls['operating_fact']}/{fact['id']}", json={"coverage": more})
    assert edited.status_code == 200, edited.text
    assert await _areas(db, fact["id"]) == [("comarca", "Banyoles"), ("municipality", "Banyoles")]
    assert (
        await db.scalar(
            sa.select(sa.func.count())
            .select_from(KnowledgeCoverageArea)
            .where(KnowledgeCoverageArea.id == row_id)
        )
        == 1
    )
    assert (await _snapshot(client_a, company))["fingerprint"] != fingerprint


async def test_the_database_itself_refuses_the_same_area_twice(db, tenant_a):
    """L'index unic: ni un cami que no passi per l'API pot repetir una area."""
    from sqlalchemy.exc import IntegrityError

    from app.models.enums import AreaKind, CoverageEntityType

    _, company = tenant_a
    entity_id = uuid.uuid4()

    def area(code: str | None = None, code_system: str | None = None) -> KnowledgeCoverageArea:
        return KnowledgeCoverageArea(
            company_id=company.id,
            entity_type=CoverageEntityType.OPERATING_FACT,
            entity_id=entity_id,
            kind=AreaKind.MUNICIPALITY,
            label="Banyoles",
            code=code,
            code_system=code_system,
        )

    db.add_all([area(), area(code="17015", code_system="INE")])
    await db.flush()
    db.add(area())  # mateixa identitat, amb els codis a NULL
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# --------------------------------------------------------------------------- #
# Validacio i neteja                                                           #
# --------------------------------------------------------------------------- #


async def test_coverage_payloads_are_validated(client_a, tenant_a):
    _, company = tenant_a
    urls = _urls(company)
    bad = (
        {"completeness": "total", "areas": []},
        {"completeness": "complete", "areas": [{"kind": "planet", "label": "Terra"}]},
        {"completeness": "complete", "areas": [{"kind": "municipality", "label": ""}]},
        {"completeness": "complete", "areas": [{"kind": "municipality", "label": "x" * 201}]},
        {"areas": []},
    )
    for coverage in bad:
        response = await client_a.post(
            urls["operating_fact"],
            json={"key": "support_24x7", "value": True, "coverage": coverage},
        )
        assert response.status_code == 422, coverage
        response = await client_a.post(
            urls["service"],
            json={"name": "FTTH", "service_line_code": "access_ftth", "coverage": coverage},
        )
        assert response.status_code == 422, coverage
    # Cap fila a mitges: ni la dada ni les arees.
    records = (await client_a.get(f"{COMPANY.format(company=company.id)}/knowledge/records")).json()
    assert records["operating_facts"] == []
    assert (await client_a.get(urls["service"])).json() == []


async def test_deleting_a_record_or_a_partner_cleans_its_coverage(client_a, tenant_a, db):
    _, company = tenant_a
    urls = _urls(company)
    created = await _create_all(client_a, company)
    assert await db.scalar(sa.select(sa.func.count()).select_from(KnowledgeCoverageArea)) == 9

    for kind in ("operating_fact", "service"):
        response = await client_a.delete(f"{urls[kind]}/{created[kind]['id']}")
        assert response.status_code == 204, (kind, response.text)
        assert await _areas(db, created[kind]["id"]) == [], kind

    # Esborrar el partner s'emporta els seus papers, i les arees dels papers.
    partner_id = created["partner_role"]["partner_id"]
    response = await client_a.delete(
        f"{COMPANY.format(company=company.id)}/knowledge/records/partner/{partner_id}"
    )
    assert response.status_code == 204, response.text
    assert await _areas(db, created["partner_role"]["id"]) == []
    assert await db.scalar(sa.select(sa.func.count()).select_from(KnowledgeCoverageArea)) == 0
