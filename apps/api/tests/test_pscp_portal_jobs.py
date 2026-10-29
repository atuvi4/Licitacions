"""La feina `sync_portal_documents`: peticio per l'API, cua i worker, contra el portal fals."""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.ingestion.pscp_portal import client as portal_client
from app.jobs.handlers import HANDLERS
from app.models.enums import MembershipRole, TenderDocumentOrigin, WorkJobType
from app.models.jobs import WorkJob
from app.models.tenders import TenderDocument
from tests.conftest import login
from tests.jobs_support import run_worker
from tests.pscp_portal_support import PINEDA, PINEDA_CURRENT, pineda, resolver
from tests.test_bid_workspace import add_member
from tests.test_pscp_portal_sync import pineda_with_feed, public_tender


@pytest.fixture(autouse=True)
def portal_on(monkeypatch):
    monkeypatch.setattr(settings, "PSCP_PORTAL_ENABLED", True)
    monkeypatch.setattr(settings, "PSCP_PORTAL_PAUSE_SECONDS", 0.0)

    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(portal_client, "_sleep", no_sleep)


def endpoint(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/job-requests/sync-portal-documents"


async def test_the_worker_knows_the_new_job_type():
    assert WorkJobType.SYNC_PORTAL_DOCUMENTS in HANDLERS


async def test_a_request_is_queued_once_and_the_worker_syncs_the_tender(
    db, session_factory, client_a, tenant_a
):
    _, company = tenant_a
    fake = pineda()
    tender = await pineda_with_feed(db, fake)

    first = await client_a.post(endpoint(company.id, tender.id))
    again = await client_a.post(endpoint(company.id, tender.id))

    assert first.status_code == 202, first.text
    assert first.json()["status"] == "accepted"
    assert first.json()["job_type"] == "sync_portal_documents"
    assert again.status_code == 200 and again.json()["already_requested"] is True
    jobs = (await db.scalars(sa.select(WorkJob).where(WorkJob.tender_id == tender.id))).all()
    assert [(job.job_type, job.dedupe_key) for job in jobs] == [
        (WorkJobType.SYNC_PORTAL_DOCUMENTS, f"portal:{tender.id}")
    ]

    async with fake.client() as http:
        await run_worker(session_factory, download_client=http, resolver=resolver)

    done = await client_a.get(f"/api/v1/companies/{company.id}/job-requests/{first.json()['id']}")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "succeeded" and done.json()["outcome"] == "executed"
    job = await db.scalar(
        sa.select(WorkJob).where(WorkJob.id == jobs[0].id).execution_options(populate_existing=True)
    )
    assert job.result["reconciliation"]["created"] == 11
    assert job.result["portal"]["answers"] == 5
    assert job.result["expedient_id"] == PINEDA
    portal = await db.scalar(
        sa.select(sa.func.count(TenderDocument.id)).where(
            TenderDocument.tender_id == tender.id,
            TenderDocument.origin == TenderDocumentOrigin.PORTAL,
        )
    )
    assert portal == 11

    # Bon us del portal: just despres, la mateixa licitacio no es torna a sincronitzar.
    too_soon = await client_a.post(endpoint(company.id, tender.id))
    assert too_soon.status_code == 429, too_soon.text
    assert too_soon.json()["error"]["code"] == "portal_sync_too_soon"
    assert too_soon.json()["error"]["details"]["retry_after_seconds"] > 0

    # Passada l'espera i sense canvis al portal, la feina seguent no fa res: `reused`.
    job.finished_at = job.finished_at - timedelta(seconds=settings.PSCP_PORTAL_MIN_INTERVAL_SECONDS)
    await db.commit()
    later = await client_a.post(endpoint(company.id, tender.id))
    assert later.status_code == 202, later.text
    async with fake.client() as http:
        await run_worker(session_factory, download_client=http, resolver=resolver)
    final = await client_a.get(f"/api/v1/companies/{company.id}/job-requests/{later.json()['id']}")
    assert final.json()["status"] == "succeeded" and final.json()["outcome"] == "reused"


async def test_a_portal_that_does_not_answer_fails_the_job_without_changes(
    db, session_factory, client_a, tenant_a
):
    _, company = tenant_a
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.status[f"detall-publicacio-expedient/{PINEDA}/{PINEDA_CURRENT}"] = [503]

    response = await client_a.post(endpoint(company.id, tender.id))
    async with fake.client() as http:
        await run_worker(session_factory, download_client=http, resolver=resolver)

    done = await client_a.get(
        f"/api/v1/companies/{company.id}/job-requests/{response.json()['id']}"
    )
    assert done.json()["status"] == "failed"
    assert done.json()["error_code"] == "portal_unavailable"
    origins = (
        await db.scalars(
            sa.select(TenderDocument.origin).where(TenderDocument.tender_id == tender.id)
        )
    ).all()
    assert sorted(origins) == [TenderDocumentOrigin.FEED, TenderDocumentOrigin.FEED]


async def test_the_switch_off_refuses_the_request(db, client_a, tenant_a, monkeypatch):
    monkeypatch.setattr(settings, "PSCP_PORTAL_ENABLED", False)
    _, company = tenant_a
    tender = await public_tender(db, PINEDA, PINEDA_CURRENT)

    response = await client_a.post(endpoint(company.id, tender.id))

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "portal_sync_disabled"
    assert await db.scalar(sa.select(sa.func.count(WorkJob.id))) == 0


async def test_a_tender_without_a_pscp_publication_is_refused(db, client_a, tenant_a):
    _, company = tenant_a
    tender = await public_tender(db, PINEDA, PINEDA_CURRENT)
    tender.source_url = "https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion"
    await db.commit()

    response = await client_a.post(endpoint(company.id, tender.id))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "portal_sync_not_available"
    assert response.json()["error"]["details"] == {"reason": "no_pscp_publication"}


async def test_another_company_private_tender_is_not_found(db, client_a, tenant_a, tenant_b):
    _, company = tenant_a
    _, other = tenant_b
    tender = await public_tender(db, PINEDA, PINEDA_CURRENT, owner_company_id=other.id)

    response = await client_a.post(endpoint(company.id, tender.id))

    assert response.status_code == 404, response.text


async def test_a_viewer_cannot_ask_for_the_sync(db, client_factory, tenant_a):
    _, company = tenant_a
    viewer = await add_member(db, company.id, role=MembershipRole.VIEWER)
    http = client_factory()
    await login(http, viewer.email)
    tender = await public_tender(db, PINEDA, PINEDA_CURRENT)

    response = await http.post(endpoint(company.id, tender.id))

    assert response.status_code == 403, response.text


async def _run_one(db, session_factory, client_a, company, tender, fake) -> tuple[dict, WorkJob]:
    response = await client_a.post(endpoint(company.id, tender.id))
    assert response.status_code == 202, response.text
    async with fake.client() as http:
        await run_worker(session_factory, download_client=http, resolver=resolver)
    done = await client_a.get(
        f"/api/v1/companies/{company.id}/job-requests/{response.json()['id']}"
    )
    job = await db.scalar(
        sa.select(WorkJob)
        .where(WorkJob.tender_id == tender.id)
        .order_by(WorkJob.created_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    return done.json(), job


async def test_a_detail_the_portal_does_not_find_fails_with_its_path_and_the_log(
    db, session_factory, client_a, tenant_a
):
    _, company = tenant_a
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.status["detall-avis/resposta/300908253"] = [404]

    view, job = await _run_one(db, session_factory, client_a, company, tender, fake)

    assert view["status"] == "failed" and view["error_code"] == "portal_not_found"
    assert "detall-avis/resposta/300908253" in job.error_message
    assert job.result["request_log"][-1]["status"] == 404


async def test_the_worker_refuses_when_the_switch_is_off_meanwhile(
    db, session_factory, client_a, tenant_a, monkeypatch
):
    _, company = tenant_a
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    response = await client_a.post(endpoint(company.id, tender.id))
    monkeypatch.setattr(settings, "PSCP_PORTAL_ENABLED", False)

    async with fake.client() as http:
        await run_worker(session_factory, download_client=http, resolver=resolver)

    done = await client_a.get(
        f"/api/v1/companies/{company.id}/job-requests/{response.json()['id']}"
    )
    assert done.json()["error_code"] == "portal_sync_disabled"
    assert fake.calls == []


async def test_a_reading_beyond_the_budget_fails_without_changes(
    db, session_factory, client_a, tenant_a, monkeypatch
):
    monkeypatch.setattr(settings, "PSCP_PORTAL_MAX_REQUESTS", 2)
    _, company = tenant_a
    fake = pineda()
    tender = await pineda_with_feed(db, fake)

    view, job = await _run_one(db, session_factory, client_a, company, tender, fake)

    assert view["error_code"] == "portal_request_budget"
    assert len(job.result["request_log"]) == 2


async def test_a_file_that_cannot_be_stored_makes_the_job_partial(
    db, session_factory, client_a, tenant_a
):
    _, company = tenant_a
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.replace_file(302674548, b"Rar!\x1a\x07\x00 dades", "application/x-rar")

    view, job = await _run_one(db, session_factory, client_a, company, tender, fake)

    assert view["status"] == "succeeded" and view["outcome"] == "partial"
    assert len(job.result["missing_content"]) == 1


async def test_a_failed_sync_can_be_asked_again_at_once(db, session_factory, client_a, tenant_a):
    """L'espera minima nomes compta les sincronitzacions acabades be: si el portal no ha
    respost, es pot tornar a demanar de seguida."""
    _, company = tenant_a
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.status[f"detall-publicacio-expedient/{PINEDA}/{PINEDA_CURRENT}"] = [503]
    view, _ = await _run_one(db, session_factory, client_a, company, tender, fake)
    assert view["error_code"] == "portal_unavailable"

    again = await client_a.post(endpoint(company.id, tender.id))

    assert again.status_code == 202, again.text
