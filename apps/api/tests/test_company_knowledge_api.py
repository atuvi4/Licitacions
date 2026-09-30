"""Coneixement de l'empresa amb base de dades i API (fase 2 del motor).

Que es protegeix aqui:

* **aillament**: la fotografia d'una empresa nomes porta les seves files, i
  ningu no pot promoure, descartar ni verificar res d'una altra;
* **l'historic suggereix, una persona decideix**: una observacio nomes es fa
  coneixement actual si algu la promou, i en queda la traça en tots dos sentits;
* **declarar no es verificar**: el que es declara o es promou neix pendent
  d'evidencia; nomes qui pot verificar ho passa a `current_verified`;
* **els vocabularis no son de ningu**: esborrar una empresa no els toca.

Totes les dades son sintetiques. Cap cost, marge ni tarifa real.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.credentials import CompanyCertification
from app.models.enums import KnowledgeOrigin, MembershipRole, ObservationSubject
from app.models.identity import CompanyMembership
from app.models.knowledge import (
    CapabilityDefinitionRow,
    CompanyOperatingFact,
    CompanyPartner,
    CompanySolution,
    KnowledgeObservation,
    PartnerRoleRow,
    ServiceLineRow,
)
from app.services import company_knowledge

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/companies/{company}"

SYNTHETIC = {
    "current": {
        "certifications": [
            {
                "standard_code": "ENS",
                "level": "medium",
                "name": "ENS categoria mitjana",
                "as_of": "2026-09-29",
            }
        ]
    },
    "historic_bids": [
        {
            "reference": "2025.06.01 Ajuntament de Mostra. Telefonia",
            "buyer_name": "Ajuntament de Mostra",
            "bid_date": "2025-06-01",
            "service_line_codes": ["voice_pbx"],
            "base_budget": "40000.00",
            "decision": "not_submitted",
            "decision_reason": "Demanaven ENS i no en teniem",
        }
    ],
    "observations": [
        {
            "subject": "certification",
            "subject_key": "ENS",
            "statement": "Es va descartar perque no tenien ENS",
            "proposal": {"standard_code": "ENS", "held": False},
            "historic_bid_reference": "2025.06.01 Ajuntament de Mostra. Telefonia",
        },
        {
            "subject": "operating_fact",
            "subject_key": "support_24x7",
            "statement": "Declaraven suport 24x7 amb resposta en menys de 2 h",
            "proposal": {"key": "support_24x7", "value": True},
        },
        {
            "subject": "solution",
            "subject_key": "Centraleta Demo",
            "statement": "Van oferir la Centraleta Demo allotjada",
            "proposal": {
                "name": "Centraleta Demo",
                "service_line_code": "voice_pbx",
                "vendor": "Fabricant Demo",
            },
        },
        {
            "subject": "partner",
            "subject_key": "Soci Demo",
            "statement": "El Soci Demo feia l'obra civil",
            "proposal": {
                "name": "Soci Demo",
                "roles": [
                    {
                        "role": "subcontractor",
                        "service_line_code": "civil_works",
                        "territory": "Girona",
                    }
                ],
            },
        },
    ],
}


@pytest.fixture(autouse=True)
async def vocabulary(db):
    from app.knowledge.vocabulary import seed_vocabulary

    await seed_vocabulary(db)
    await db.commit()


async def _import(db, company, document=SYNTHETIC) -> dict:
    counts = await company_knowledge.import_knowledge(db, company.id, document)
    await db.commit()
    return counts


async def _snapshot(client, company) -> dict:
    response = await client.get(BASE.format(company=company.id) + "/knowledge/snapshot")
    assert response.status_code == 200, response.text
    return response.json()


def _entry(snapshot: dict, section: str, key: str) -> dict | None:
    return next((e for e in snapshot[section] if e["key"] == key), None)


async def _observation(db, company, subject: str) -> KnowledgeObservation:
    return await db.scalar(
        sa.select(KnowledgeObservation).where(
            KnowledgeObservation.company_id == company.id,
            KnowledgeObservation.subject == subject,
        )
    )


async def _set_role(db, user, company, role: str) -> None:
    await db.execute(
        sa.update(CompanyMembership)
        .where(CompanyMembership.user_id == user.id, CompanyMembership.company_id == company.id)
        .values(role=MembershipRole(role))
    )
    await db.commit()


async def _link_evidence(client, company, entity_type: str, entity_id) -> dict:
    """Puja un document sintetic i l'enllaca com a evidencia de l'entitat."""
    from tests.fixtures.pdf_builder import build_pdf

    response = await client.post(
        BASE.format(company=company.id) + "/documents",
        files={
            "file": (
                f"evidencia-{uuid.uuid4().hex[:6]}.pdf",
                build_pdf([f"Evidencia {uuid.uuid4()}"]),
                "application/pdf",
            )
        },
        data={
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "relationship_type": "certificate",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Importacio i fotografia                                                      #
# --------------------------------------------------------------------------- #


async def test_import_keeps_history_as_candidates_only(client_a, tenant_a, db):
    _, company = tenant_a
    counts = await _import(db, company)
    assert counts == {
        "certifications": 1,
        "operating_facts": 0,
        "historic_bids": 1,
        "observations": 4,
    }

    snapshot = await _snapshot(client_a, company)
    ens = _entry(snapshot, "certifications", "ENS")
    assert (ens["source"], ens["accreditation_pending"], ens["claims"][0]["value"]) == (
        "current_declared",
        True,
        "medium",
    )
    # Res de l'historic no s'ha convertit en coneixement actual.
    assert _entry(snapshot, "facts", "support_24x7")["source"] == "historic"
    assert _entry(snapshot, "solutions", "Centraleta Demo")["claims"] == []
    assert _entry(snapshot, "partners", "Soci Demo")["claims"] == []
    assert await db.scalar(sa.select(sa.func.count()).select_from(CompanySolution)) == 0

    pending = [(p["kind"], p["subject"], p["key"]) for p in snapshot["pending_validation"]]
    assert pending == [
        ("accreditation_pending", "certification", "ENS"),
        ("scope_unknown", "certification", "ENS"),
        ("historic_candidate", "certification", "ENS"),
        ("historic_candidate", "operating_fact", "support_24x7"),
        ("historic_candidate", "partner", "Soci Demo"),
        ("historic_candidate", "solution", "Centraleta Demo"),
    ]
    ens_candidate = next(
        p
        for p in snapshot["pending_validation"]
        if p["kind"] == "historic_candidate" and p["key"] == "ENS"
    )
    assert ens_candidate["superseded_by_current"] is True


async def test_confidential_economics_are_refused(db, tenant_a):
    _, company = tenant_a
    from app.core.errors import ValidationError

    document = {
        "historic_bids": [
            {"reference": "x", "decision": "submitted", "offer_amount": "1000", "margin_pct": "20"}
        ]
    }
    with pytest.raises(ValidationError) as exc:
        await company_knowledge.import_knowledge(db, company.id, document)
    assert exc.value.details["fields"] == ["margin_pct", "offer_amount"]


async def test_a_malformed_proposal_does_not_get_in(db, tenant_a):
    _, company = tenant_a
    document = {
        "observations": [
            {
                "subject": "solution",
                "subject_key": "X",
                "statement": "?",
                "proposal": {"name": "X", "service_line_code": "linia_inventada"},
            }
        ]
    }
    with pytest.raises(company_knowledge.InvalidProposalError):
        await company_knowledge.import_knowledge(db, company.id, document)


# --------------------------------------------------------------------------- #
# Aillament                                                                    #
# --------------------------------------------------------------------------- #


async def test_one_company_never_sees_another_company_knowledge(
    client_a, client_b, tenant_a, tenant_b, db
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    await _import(db, company_a)

    snapshot_b = await _snapshot(client_b, company_b)
    for section in (
        "certifications",
        "facts",
        "services",
        "solutions",
        "capabilities",
        "products",
        "partners",
        "pending_validation",
    ):
        assert snapshot_b[section] == [], section
    assert snapshot_b["company_id"] == str(company_b.id)

    observation = await _observation(db, company_a, "operating_fact")
    base_b = BASE.format(company=company_b.id) + "/knowledge"
    assert (
        await client_b.get(f"{BASE.format(company=company_a.id)}/knowledge/snapshot")
    ).status_code == 404
    assert (
        await client_b.post(f"{base_b}/observations/{observation.id}/promote", json={})
    ).status_code == 404
    assert (
        await client_b.post(
            f"{base_b}/observations/{observation.id}/dismiss", json={"note": "no es nostra"}
        )
    ).status_code == 404
    certification = await db.scalar(sa.select(CompanyCertification))
    assert (
        await client_b.post(f"{base_b}/records/certification/{certification.id}/verify")
    ).status_code == 404
    assert (await client_b.get(f"{base_b}/observations")).json() == []


# --------------------------------------------------------------------------- #
# Promoure, descartar, verificar                                               #
# --------------------------------------------------------------------------- #


async def test_promoting_creates_traceable_current_knowledge(client_a, tenant_a, db):
    user, company = tenant_a
    await _import(db, company)
    before = (await _snapshot(client_a, company))["fingerprint"]
    observation = await _observation(db, company, "operating_fact")

    response = await client_a.post(
        f"{BASE.format(company=company.id)}/knowledge/observations/{observation.id}/promote",
        json={"note": "Confirmat amb direccio tecnica"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["promoted_entity_type"]) == ("promoted", "operating_fact")
    fact = await db.get(CompanyOperatingFact, uuid.UUID(body["promoted_entity_id"]))
    assert (fact.key, fact.value, fact.knowledge_origin, fact.verification_status) == (
        "support_24x7",
        True,
        KnowledgeOrigin.PROMOTED_OBSERVATION,
        "pending_verification",
    )
    assert fact.company_id == company.id

    snapshot = await _snapshot(client_a, company)
    entry = _entry(snapshot, "facts", "support_24x7")
    assert (entry["source"], entry["accreditation_pending"], entry["hints"]) == (
        "current_declared",
        True,
        [],
    )
    assert snapshot["fingerprint"] != before
    audit = await db.scalar(
        sa.select(AuditLog).where(AuditLog.entity_type == "knowledge_observation")
    )
    assert audit.user_id == user.id and "promoguda" in audit.summary


async def test_promoting_a_partner_brings_its_roles(client_a, tenant_a, db):
    _, company = tenant_a
    await _import(db, company)
    observation = await _observation(db, company, "partner")
    response = await client_a.post(
        f"{BASE.format(company=company.id)}/knowledge/observations/{observation.id}/promote",
        json={},
    )
    assert response.status_code == 200
    roles = list(await db.scalars(sa.select(PartnerRoleRow)))
    assert [(r.role, r.service_line_code, r.territory) for r in roles] == [
        ("subcontractor", "civil_works", "Girona")
    ]
    partner = _entry(await _snapshot(client_a, company), "partners", "Soci Demo")
    assert partner["claims"][0]["value"]["roles"] == [
        {"role": "subcontractor", "line": "civil_works", "territory": "Girona"}
    ]


async def test_a_negative_certification_cannot_be_promoted_only_dismissed(client_a, tenant_a, db):
    _, company = tenant_a
    await _import(db, company)
    observation = await _observation(db, company, "certification")
    base = f"{BASE.format(company=company.id)}/knowledge/observations/{observation.id}"

    refused = await client_a.post(f"{base}/promote", json={})
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "observation_not_promotable"

    dismissed = await client_a.post(f"{base}/dismiss", json={"note": "Ara tenim ENS Mitja"})
    assert dismissed.status_code == 200
    assert (dismissed.json()["status"], dismissed.json()["review_note"]) == (
        "dismissed",
        "Ara tenim ENS Mitja",
    )
    again = await client_a.post(f"{base}/promote", json={})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "observation_not_candidate"
    snapshot = await _snapshot(client_a, company)
    assert _entry(snapshot, "certifications", "ENS")["hints"] == []


async def test_history_never_overwrites_what_is_declared(client_a, tenant_a, db):
    _, company = tenant_a
    await _import(db, company)
    db.add(
        CompanyOperatingFact(
            company_id=company.id, key="support_24x7", value=False, detail="Nomes horari laboral"
        )
    )
    await db.commit()
    observation = await _observation(db, company, "operating_fact")

    response = await client_a.post(
        f"{BASE.format(company=company.id)}/knowledge/observations/{observation.id}/promote",
        json={},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "knowledge_already_declared"
    entry = _entry(await _snapshot(client_a, company), "facts", "support_24x7")
    assert (entry["source"], entry["claims"][0]["value"]) == ("current_declared", False)


async def test_verifying_needs_linked_evidence(client_a, tenant_a, db):
    """Una revisio humana sense evidencia no verifica res: continua declarat."""
    _, company = tenant_a
    await _import(db, company)
    certification = await db.scalar(sa.select(CompanyCertification))
    verify = (
        f"{BASE.format(company=company.id)}/knowledge/records/certification/"
        f"{certification.id}/verify"
    )

    refused = await client_a.post(verify)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "evidence_required"
    ens = _entry(await _snapshot(client_a, company), "certifications", "ENS")
    assert (ens["source"], ens["accreditation_pending"]) == ("current_declared", True)

    await _link_evidence(client_a, company, "company_certification", certification.id)
    assert (await client_a.post(verify)).status_code == 200
    ens = _entry(await _snapshot(client_a, company), "certifications", "ENS")
    assert (ens["source"], ens["accreditation_pending"]) == ("current_verified", False)


@pytest.mark.parametrize(
    ("role", "promote", "verify"),
    [("bid_manager", 200, 404), ("technical", 404, 404), ("viewer", 404, 404)],
)
async def test_who_can_decide_and_who_can_verify(client_a, tenant_a, db, role, promote, verify):
    user, company = tenant_a
    await _import(db, company)
    await _set_role(db, user, company, role)
    observation = await _observation(db, company, "solution")
    certification = await db.scalar(sa.select(CompanyCertification))
    base = f"{BASE.format(company=company.id)}/knowledge"

    assert (await client_a.get(f"{base}/snapshot")).status_code == 200
    assert (
        await client_a.post(f"{base}/observations/{observation.id}/promote", json={})
    ).status_code == promote
    assert (
        await client_a.post(f"{base}/records/certification/{certification.id}/verify")
    ).status_code == verify


# --------------------------------------------------------------------------- #
# Els CRUD existents porten el vocabulari                                      #
# --------------------------------------------------------------------------- #


async def test_certifications_are_declared_with_standard_and_level(client_a, tenant_a):
    _, company = tenant_a
    url = BASE.format(company=company.id) + "/certifications"

    created = await client_a.post(
        url, json={"name": "ENS", "standard_code": "ENS", "level": "medium"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["standard_code"], body["level"], body["knowledge_origin"], body["lifecycle"]) == (
        "ENS",
        "medium",
        "declared",
        "current",
    )
    for broken in (
        {"name": "ENS", "standard_code": "ENS", "level": "altissim"},
        {"name": "ISO", "standard_code": "ISO_9001", "level": "high"},
        {"name": "X", "level": "medium"},
        {"name": "X", "standard_code": "ISO_45001"},
    ):
        assert (await client_a.post(url, json=broken)).status_code == 422, broken

    # Pel mateix CRUD, verificar demana evidencia enllacada.
    patch = await client_a.patch(f"{url}/{body['id']}", json={"verification_status": "verified"})
    assert patch.status_code == 422
    assert patch.json()["error"]["code"] == "evidence_required"
    await _link_evidence(client_a, company, "company_certification", body["id"])
    patch = await client_a.patch(f"{url}/{body['id']}", json={"verification_status": "verified"})
    assert patch.status_code == 200, patch.text
    ens = _entry(await _snapshot(client_a, company), "certifications", "ENS")
    assert ens["source"] == "current_verified"


async def test_services_are_declared_with_a_service_line(client_a, tenant_a):
    _, company = tenant_a
    url = BASE.format(company=company.id) + "/services"
    created = await client_a.post(
        url,
        json={"name": "Centraleta cloud", "service_line_code": "voice_pbx", "delivery_mode": "own"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["knowledge_origin"] == "declared"
    assert (
        await client_a.post(url, json={"name": "X", "service_line_code": "inventada"})
    ).status_code == 422
    services = (await _snapshot(client_a, company))["services"]
    assert [(s["key"], s["source"]) for s in services] == [("voice_pbx", "current_declared")]

    # Inactiu = retirat: deixa de comptar.
    await client_a.patch(f"{url}/{created.json()['id']}", json={"active": False})
    assert (await _snapshot(client_a, company))["services"] == []


async def test_a_capability_needs_its_solution_in_the_same_company(db, tenant_a, tenant_b):
    """La clau composta impedeix penjar una capacitat d'una solucio d'una altra empresa."""
    from sqlalchemy.exc import IntegrityError

    from app.models.knowledge import SolutionCapability

    _, company_a = tenant_a
    _, company_b = tenant_b
    solution = CompanySolution(company_id=company_a.id, name="PBX", service_line_code="voice_pbx")
    db.add(solution)
    await db.commit()
    db.add(
        SolutionCapability(
            company_id=company_b.id,
            solution_id=solution.id,
            capability_code="pbx.queues",
            status="supported",
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# --------------------------------------------------------------------------- #
# Esborrar una empresa                                                         #
# --------------------------------------------------------------------------- #


async def test_deleting_a_company_keeps_the_global_vocabularies(db, tenant_a):
    from app.models.identity import Company

    _, company = tenant_a
    await _import(db, company)
    db.add(CompanyPartner(company_id=company.id, name="Proveidor Demo", as_of=date(2026, 9, 1)))
    await db.commit()
    lines = await db.scalar(sa.select(sa.func.count()).select_from(ServiceLineRow))
    capabilities = await db.scalar(sa.select(sa.func.count()).select_from(CapabilityDefinitionRow))

    await db.execute(sa.delete(Company).where(Company.id == company.id))
    await db.commit()

    assert await db.scalar(sa.select(sa.func.count()).select_from(ServiceLineRow)) == lines
    assert (
        await db.scalar(sa.select(sa.func.count()).select_from(CapabilityDefinitionRow))
        == capabilities
    )
    for model in (KnowledgeObservation, CompanyPartner, CompanyCertification):
        assert await db.scalar(sa.select(sa.func.count()).select_from(model)) == 0


async def test_fingerprint_is_stable_across_reads_and_ignores_history(client_a, tenant_a, db):
    _, company = tenant_a
    await _import(db, company)
    first = (await _snapshot(client_a, company))["fingerprint"]
    assert (await _snapshot(client_a, company))["fingerprint"] == first

    await company_knowledge.record_observation(
        db,
        company_id=company.id,
        subject=ObservationSubject.CERTIFICATION,
        subject_key="ISO_27001",
        statement="Hi havia una ISO 27001 en tramit?",
        proposal={"standard_code": "ISO_27001"},
    )
    await db.commit()
    snapshot = await _snapshot(client_a, company)
    assert snapshot["fingerprint"] == first
    assert _entry(snapshot, "certifications", "ISO_27001")["source"] == "historic"


# --------------------------------------------------------------------------- #
# Verificat nomes amb evidencia                                                #
# --------------------------------------------------------------------------- #


async def test_nothing_is_born_verified(client_a, tenant_a, db):
    _, company = tenant_a
    base = BASE.format(company=company.id)
    for url, payload in (
        (
            f"{base}/certifications",
            {
                "name": "ENS",
                "standard_code": "ENS",
                "level": "high",
                "verification_status": "verified",
            },
        ),
        (
            f"{base}/services",
            {"name": "PBX", "service_line_code": "voice_pbx", "verification_status": "verified"},
        ),
    ):
        response = await client_a.post(url, json=payload)
        assert response.status_code == 422, url
        assert response.json()["error"]["code"] == "evidence_required"
    assert await db.scalar(sa.select(sa.func.count()).select_from(CompanyCertification)) == 0

    document = {
        "current": {
            "certifications": [
                {
                    "standard_code": "ENS",
                    "level": "medium",
                    "name": "ENS",
                    "verification_status": "verified",
                }
            ]
        }
    }
    with pytest.raises(company_knowledge.knowledge_evidence.EvidenceRequiredError):
        await company_knowledge.import_knowledge(db, company.id, document)


async def test_removing_the_last_evidence_unverifies(client_a, tenant_a, db):
    _, company = tenant_a
    await _import(db, company)
    certification = await db.scalar(sa.select(CompanyCertification))
    base = BASE.format(company=company.id)
    first = await _link_evidence(client_a, company, "company_certification", certification.id)
    second = await _link_evidence(client_a, company, "company_certification", certification.id)
    verify = f"{base}/knowledge/records/certification/{certification.id}/verify"
    assert (await client_a.post(verify)).status_code == 200

    # Treure un dels dos enllacos: encara n'hi ha un altre, continua verificada.
    links = (await client_a.get(f"{base}/documents/{first['id']}/links")).json()
    unlinked = await client_a.delete(f"{base}/documents/{first['id']}/links/{links[0]['id']}")
    assert unlinked.status_code == 204
    ens = _entry(await _snapshot(client_a, company), "certifications", "ENS")
    assert ens["source"] == "current_verified"

    # Esborrar l'ultim document que la sostenia: torna a declarada.
    assert (await client_a.delete(f"{base}/documents/{second['id']}")).status_code == 204
    await db.refresh(certification)
    assert certification.verification_status == "pending_verification"
    ens = _entry(await _snapshot(client_a, company), "certifications", "ENS")
    assert ens["source"] == "current_declared"


async def test_new_knowledge_can_carry_evidence_and_be_verified(client_a, tenant_a, db):
    _, company = tenant_a
    await _import(db, company)
    observation = await _observation(db, company, "operating_fact")
    base = BASE.format(company=company.id)
    promoted = await client_a.post(
        f"{base}/knowledge/observations/{observation.id}/promote", json={}
    )
    fact_id = promoted.json()["promoted_entity_id"]

    verify = f"{base}/knowledge/records/operating_fact/{fact_id}/verify"
    assert (await client_a.post(verify)).json()["error"]["code"] == "evidence_required"
    await _link_evidence(client_a, company, "company_operating_fact", fact_id)
    assert (await client_a.post(verify)).status_code == 200
    fact = _entry(await _snapshot(client_a, company), "facts", "support_24x7")
    assert fact["source"] == "current_verified"


async def test_evidence_cannot_be_linked_to_another_company_knowledge(
    client_b, tenant_a, tenant_b, db
):
    from tests.fixtures.pdf_builder import build_pdf

    _, company_a = tenant_a
    _, company_b = tenant_b
    await _import(db, company_a)
    certification = await db.scalar(sa.select(CompanyCertification))

    response = await client_b.post(
        BASE.format(company=company_b.id) + "/documents",
        files={"file": ("x.pdf", build_pdf(["x"]), "application/pdf")},
        data={"entity_type": "company_certification", "entity_id": str(certification.id)},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Abast estructurat                                                            #
# --------------------------------------------------------------------------- #


async def test_scope_is_structured_and_the_text_is_only_a_note(client_a, tenant_a):
    _, company = tenant_a
    url = BASE.format(company=company.id) + "/certifications"
    noted = await client_a.post(
        url,
        json={
            "name": "ENS mitja",
            "standard_code": "ENS",
            "level": "medium",
            "scope": "Cobreix la centraleta (voice_pbx) i la xarxa dedicada",
        },
    )
    assert noted.status_code == 201
    assert noted.json()["scope_service_lines"] is None

    snapshot = await _snapshot(client_a, company)
    claim = _entry(snapshot, "certifications", "ENS")["claims"][0]
    # El text esmenta linies, pero no se n'infereix res.
    assert (claim["scope_service_lines"], claim["scope_status"]) == (None, "scope_unknown")
    assert ("scope_unknown", "certification", "ENS") in [
        (p["kind"], p["subject"], p["key"]) for p in snapshot["pending_validation"]
    ]

    structured = await client_a.patch(
        f"{url}/{noted.json()['id']}",
        json={"scope_service_lines": ["voice_trunk", "voice_pbx", "voice_pbx"]},
    )
    assert structured.status_code == 200, structured.text
    assert structured.json()["scope_service_lines"] == ["voice_pbx", "voice_trunk"]
    claim = _entry(await _snapshot(client_a, company), "certifications", "ENS")["claims"][0]
    assert (claim["scope_service_lines"], claim["scope_status"]) == (
        ["voice_pbx", "voice_trunk"],
        "structured",
    )
    for broken in (["inventada"], []):
        response = await client_a.patch(
            f"{url}/{noted.json()['id']}", json={"scope_service_lines": broken}
        )
        assert response.status_code == 422, broken


async def test_a_promoted_certification_keeps_its_structured_scope(client_a, tenant_a, db):
    _, company = tenant_a
    observation = await company_knowledge.record_observation(
        db,
        company_id=company.id,
        subject=ObservationSubject.CERTIFICATION,
        subject_key="ISO_27001",
        statement="ISO 27001 per a la centraleta",
        proposal={"standard_code": "ISO_27001", "scope_service_lines": ["voice_pbx"]},
    )
    await db.commit()
    response = await client_a.post(
        f"{BASE.format(company=company.id)}/knowledge/observations/{observation.id}/promote",
        json={},
    )
    assert response.status_code == 200, response.text
    entry = _entry(await _snapshot(client_a, company), "certifications", "ISO_27001")
    claim = entry["claims"][0]
    assert (claim["scope_service_lines"], claim["scope_status"], claim["source"]) == (
        ["voice_pbx"],
        "structured",
        "current_declared",
    )
