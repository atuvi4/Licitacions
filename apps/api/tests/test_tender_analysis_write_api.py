"""Escriptura de l'analisi (pas 7): POST /analysis/runs.

Sincron, determinista, sense LLM. Torna sempre l'analisi resultant i diu si s'ha
reutilitzat. Sense requisits efectius no analitza (409 estable). Permis: el de la
revisio de requisits del plec. Dades sintetiques.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.api.v1.routers import tender_analysis as router_module
from app.knowledge import vocabulary
from app.models.enums import RequirementCategory
from app.models.requirements import Requirement
from app.models.tender_analysis import TenderAnalysisRun
from app.models.tenders import Tender
from app.services import tender_analysis as analysis
from tests import test_tender_scope_models as m
from tests.test_company_knowledge_permissions_api import _role
from tests.test_tender_analysis_engine import _requirement, _synthetic_tender, _twin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


def _base(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis"


async def _runs(db, company_id, tender_id) -> int:
    return (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderAnalysisRun)
            .where(
                TenderAnalysisRun.company_id == company_id,
                TenderAnalysisRun.tender_id == tender_id,
            )
        )
    ).scalar_one()


async def test_first_run_is_created_and_an_identical_one_is_reused(db, tenant_a, client_a):
    _, company = tenant_a
    tender, reqs, _, _, _ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    url = f"{_base(company.id, tender.id)}/runs"
    first = await client_a.post(url)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["reused"] is False and body["is_current"] is True
    assert len(body["fit_assessments"]) == len(reqs)
    assert "score" not in body["summary"] and "recommendation" not in body
    again = await client_a.post(url)
    assert again.status_code == 200, again.text
    assert again.json()["reused"] is True and again.json()["id"] == body["id"]
    assert await _runs(db, company.id, tender.id) == 1
    current = await client_a.get(f"{_base(company.id, tender.id)}/current")
    assert current.json()["id"] == body["id"] and current.json()["stale"] is False


async def test_without_effective_requirements_it_refuses_with_a_stable_code(db, tenant_a, client_a):
    _, company = tenant_a
    tender = await m._public_tender(db, title="Plec sintetic sense requisits")
    await db.commit()
    refused = await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "analysis_prerequisites_missing"
    assert error["details"]["missing"] == ["requirements"]
    assert await _runs(db, company.id, tender.id) == 0


async def test_a_409_leaves_an_existing_stale_run_untouched(db, tenant_a, client_a):
    """Si els requisits deixen de ser efectius despres d'una analisi, el 409 no la toca.
    Els requisits del plec sintetic son manuals: arxivar-los els treu del conjunt efectiu."""
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    url = f"{_base(company.id, tender.id)}/runs"
    first = (await client_a.post(url)).json()
    await db.execute(
        sa.update(Requirement)
        .where(Requirement.tender_id == tender.id)
        .values(archived_at=sa.func.now())
    )
    await db.commit()
    refused = await client_a.post(url)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "analysis_prerequisites_missing"
    assert await _runs(db, company.id, tender.id) == 1
    current = await client_a.get(f"{_base(company.id, tender.id)}/current")
    assert current.status_code == 200 and current.json()["id"] == first["id"]
    assert current.json()["is_current"] is True


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("viewer", False),
        ("technical", True),
        ("bid_manager", True),
        ("admin", True),
        ("owner", True),
    ],
)
async def test_recalculating_follows_the_requirement_review_policy(
    db, tenant_a, client_a, role, allowed
):
    user, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    await _role(db, user, company, role)
    response = await client_a.post(f"{_base(company.id, tender.id)}/runs")
    if allowed:
        assert response.status_code == 201, response.text
    else:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"
        assert await _runs(db, company.id, tender.id) == 0
    # Llegir, tothom.
    current = await client_a.get(f"{_base(company.id, tender.id)}/current")
    assert current.status_code == (200 if allowed else 404)


async def test_another_company_cannot_analyse_through_this_route(
    db, tenant_a, tenant_b, client_a, client_b
):
    _, company_a = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await db.commit()
    # Ruta d'una altra empresa: qui no n'es membre rep 404 (`get_company_context`).
    hijack = await client_b.post(f"{_base(company_a.id, tender.id)}/runs")
    assert hijack.status_code == 404
    assert await _runs(db, company_a.id, tender.id) == 0
    # Una licitacio inexistent: 404, cap analisi.
    missing = await client_a.post(f"{_base(company_a.id, uuid.uuid4())}/runs")
    assert missing.status_code == 404


async def test_a_changed_input_produces_a_new_current_run(db, tenant_a, client_a):
    """Spec 6: una entrada material nova no reutilitza l'analisi anterior."""
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    await db.commit()
    company_id, tender_id = company.id, tender.id
    url = f"{_base(company_id, tender_id)}/runs"
    first = (await client_a.post(url)).json()
    await _requirement(
        db,
        await db.get(Tender, tender_id),
        "Requisit manual nou",
        RequirementCategory.TECHNICAL_REQUIREMENT,
    )
    await db.commit()
    second = await client_a.post(url)
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["reused"] is False and body["stale"] is False and body["is_current"] is True
    assert body["id"] != first["id"]
    assert await _runs(db, company_id, tender_id) == 2
    db.expire_all()
    old = await db.get(TenderAnalysisRun, uuid.UUID(first["id"]))
    assert old.is_current is False
    current = await client_a.get(f"{_base(company_id, tender_id)}/current")
    assert current.json()["id"] == body["id"]


async def test_a_member_cannot_analyse_another_companys_private_tender(
    db, tenant_a, tenant_b, client_a
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    tender, *_ = await _synthetic_tender(db)
    tender.owner_company_id = company_b.id  # licitacio manual privada de B
    await db.commit()
    response = await client_a.post(f"{_base(company_a.id, tender.id)}/runs")
    assert response.status_code == 404, response.text
    assert await _runs(db, company_a.id, tender.id) == 0
    assert await _runs(db, company_b.id, tender.id) == 0


async def test_a_lost_race_returns_the_other_run_instead_of_a_500(
    db, tenant_a, client_a, session_factory, monkeypatch
):
    """L'altra peticio desa la mateixa analisi abans que el nostre commit: el commit
    topa amb la unicitat i l'endpoint torna la de l'altra, reutilitzada."""
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    real = router_module._analyze_and_commit
    calls = {"n": 0}

    async def racing(session, company_id, tender_id):
        calls["n"] += 1
        if calls["n"] == 1:
            async with session_factory() as other:
                await analysis.analyze_tender(other, company_id=company_id, tender_id=tender_id)
                await other.commit()
            # El missatge real de SQLite per a la unicitat de les entrades.
            raise IntegrityError(
                "INSERT",
                {},
                Exception(
                    "UNIQUE constraint failed: tender_analysis_runs.company_id, "
                    "tender_analysis_runs.tender_id, tender_analysis_runs.inputs_hash"
                ),
            )
        return await real(session, company_id, tender_id)

    monkeypatch.setattr(router_module, "_analyze_and_commit", racing)
    response = await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert response.status_code == 200, response.text
    assert response.json()["reused"] is True
    assert await _runs(db, company.id, tender.id) == 1
    assert calls["n"] == 2


async def test_a_second_conflict_is_a_stable_409(db, tenant_a, client_a, monkeypatch):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)

    async def always_conflicts(session, company_id, tender_id):
        raise IntegrityError(
            "INSERT",
            {},
            Exception(
                "UNIQUE constraint failed: tender_analysis_runs.company_id, "
                "tender_analysis_runs.tender_id"
            ),
        )

    monkeypatch.setattr(router_module, "_analyze_and_commit", always_conflicts)
    response = await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "analysis_run_conflict"
    assert await _runs(db, company.id, tender.id) == 0


async def test_an_unrelated_integrity_error_is_not_masked_as_a_race(
    db, tenant_a, client_a, monkeypatch
):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    calls = {"n": 0}

    async def unrelated(session, company_id, tender_id):
        calls["n"] += 1
        raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))

    monkeypatch.setattr(router_module, "_analyze_and_commit", unrelated)
    with pytest.raises(IntegrityError):
        await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert calls["n"] == 1, "cap reintent per a un error que no es la cursa"


async def test_a_retry_that_hits_an_unrelated_integrity_error_propagates(
    db, tenant_a, client_a, monkeypatch
):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    calls = {"n": 0}

    async def race_then_unrelated(session, company_id, tender_id):
        calls["n"] += 1
        message = (
            "UNIQUE constraint failed: tender_analysis_runs.company_id, "
            "tender_analysis_runs.tender_id, tender_analysis_runs.inputs_hash"
            if calls["n"] == 1
            else "FOREIGN KEY constraint failed"
        )
        raise IntegrityError("INSERT", {}, Exception(message))

    monkeypatch.setattr(router_module, "_analyze_and_commit", race_then_unrelated)
    with pytest.raises(IntegrityError):
        await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert calls["n"] == 2


@pytest.mark.postgres
async def test_two_simultaneous_requests_get_the_same_run(db, tenant_a, client_factory):
    import asyncio

    from tests.conftest import login

    user, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company)
    clients = [client_factory(), client_factory()]
    for client in clients:
        await login(client, user.email)
    url = f"{_base(company.id, tender.id)}/runs"
    first, second = await asyncio.gather(*(client.post(url) for client in clients))
    assert {first.status_code, second.status_code} <= {200, 201}, (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    assert await _runs(db, company.id, tender.id) == 1
