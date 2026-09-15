"""Control de cost de l'analisi: preview, confirmacio, limits i permisos.

Tot amb el proveidor fals. El que es protegeix:

* ningu no pot demanar una analisi sense haver-ne vist el volum (preview desat);
* el preview no crida mai el proveidor;
* si el que es va veure ja no es el que hi ha, no es crea res i cal tornar-ho a veure;
* els limits del pilot i els rols venen de configuracio;
* res d'aixo revela activitat d'altres empreses.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.ai.providers import fake
from app.core.config import Settings, settings
from app.core.errors import CompanyActiveLimitError
from app.jobs import quotes as analysis_quotes
from app.models.analysis_quotes import AnalysisQuote
from app.models.enums import (
    AnalysisQuoteStatus,
    AnalysisRunStatus,
    JobRequestStatus,
    MembershipRole,
    TenderDocumentType,
    WorkJobStatus,
)
from app.models.identity import CompanyMembership
from app.models.jobs import JobRequest, WorkJob
from app.models.requirements import RequirementExtractionRun
from app.models.tenders import Tender, TenderDocument
from app.services import analysis_planning
from app.services.requirement_analysis import analysis_input
from tests import test_jobs as job_tests
from tests.jobs_support import (
    confirm_new_quote,
    confirm_quote,
    create_quote,
    request_analysis,
    run_worker,
)

# Fixtures compartides amb els tests de la cua.
plec = job_tests.plec
_clean_provider = job_tests._clean_provider
_count = job_tests._count
_run = job_tests._run
_submit = job_tests._submit


def _quotes_url(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis-quotes"


async def _set_role(session, tenant, role: MembershipRole) -> None:
    user, company = tenant
    await session.execute(
        sa.update(CompanyMembership)
        .where(CompanyMembership.user_id == user.id, CompanyMembership.company_id == company.id)
        .values(role=role)
    )
    await session.commit()


async def _another_plec(session, tender_id) -> uuid.UUID:
    """Una segona licitacio, amb el seu propi plec analitzable."""
    first = await session.get(Tender, tender_id)
    tender = Tender(
        source_id=first.source_id, external_id=f"exp-{uuid.uuid4().hex[:8]}", title="Una altra"
    )
    session.add(tender)
    await session.flush()
    document = TenderDocument(
        tender_id=tender.id,
        name="PCAP-2.pdf",
        document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
        url="https://example.com/pcap-2.pdf",
    )
    session.add(document)
    await session.commit()
    await job_tests._add_extracted_pages(session, document, pages=2, sha="d" * 64)
    return tender.id


# --------------------------------------------------------------------------- #
# Preview                                                                        #
# --------------------------------------------------------------------------- #


async def test_the_preview_shows_the_volume_without_calling_the_provider(
    client_a, tenant_a, plec, session
):
    _, company = tenant_a
    tender_id = plec["document"].tender_id

    response = await client_a.post(_quotes_url(company.id, tender_id))

    assert response.status_code == 201, response.text
    quote = response.json()
    assert quote["status"] == "open"
    assert quote["planning_version"] == analysis_planning.PLANNING_VERSION
    assert (quote["profile"], quote["provider"], quote["model"]) == (
        "v1", "fake", "fake-model-v1"
    )
    assert quote["input_fingerprint"] == (await analysis_input(session, tender_id)).fingerprint
    assert [d["name"] for d in quote["documents"]] == ["PCAP.pdf"]
    assert (quote["pages_in_plan"], quote["pages_total"], quote["pages_truncated"]) == (2, 2, False)
    assert (quote["chunks"], quote["chunks_to_send"]) == (1, 1)
    assert quote["estimated_input_tokens"] is None, "sense historic no s'estima res"
    assert quote["allowed"] is True and quote["max_chunks"] == settings.ANALYSIS_MAX_CHUNKS_PER_RUN
    assert fake.SENT_PAYLOADS == []
    assert await _count(session, WorkJob) == 0
    assert await _count(session, JobRequest) == 0


async def test_the_preview_does_not_even_build_a_calling_provider(tenant_a, plec, session):
    class Exploding(fake.FakeProvider):
        async def extract_structured(self, **kwargs):
            raise AssertionError("el preview no pot cridar el proveidor")

    quote = await analysis_quotes.create_quote(
        session,
        company_id=tenant_a[1].id,
        user_id=tenant_a[0].id,
        tender_id=plec["document"].tender_id,
        provider_factory=Exploding,
    )
    assert quote.chunks == 1


async def test_the_preview_makes_the_page_cap_visible(client_a, tenant_a, plec, monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENTS_MAX_PAGES_PER_RUN", 1)

    quote = await create_quote(client_a, tenant_a[1].id, plec["document"].tender_id)

    assert (quote["pages_in_plan"], quote["pages_total"]) == (1, 2)
    assert quote["pages_truncated"] is True


async def test_the_estimate_uses_only_the_same_provider_and_model(
    client_a, tenant_a, plec, session
):
    tender_id = plec["document"].tender_id
    same = _run(tender_id, status=AnalysisRunStatus.COMPLETED)
    same.input_tokens, same.output_tokens = 1000, 400
    same.provider_metadata = {"request_ids": ["r1"]}
    other = _run(tender_id, status=AnalysisRunStatus.COMPLETED, fingerprint="9" * 64)
    other.model = "un-altre-model"
    other.input_tokens, other.output_tokens = 999_999, 999_999
    other.provider_metadata = {"request_ids": ["r2"]}
    session.add_all([same, other])
    await session.commit()

    quote = await create_quote(client_a, tenant_a[1].id, tender_id)

    assert (quote["estimated_input_tokens"], quote["estimated_output_tokens"]) == (1000, 400)
    assert quote["estimate_basis"] == {"runs": 1, "requests": 1}


async def test_a_reusable_result_costs_nothing_and_is_always_allowed(
    client_a, tenant_a, plec, session_factory, monkeypatch
):
    tender_id = plec["document"].tender_id
    await request_analysis(client_a, tenant_a[1].id, tender_id, session_factory)
    monkeypatch.setattr(settings, "ANALYSIS_MAX_CHUNKS_PER_RUN", 0)

    quote = await create_quote(client_a, tenant_a[1].id, tender_id)

    assert quote["reusable_run_id"] is not None
    assert quote["chunks_to_send"] == 0
    assert quote["allowed"] is True


async def test_the_preview_does_not_reveal_another_companys_activity(
    client_a, client_b, tenant_a, tenant_b, plec
):
    tender_id = plec["document"].tender_id
    before = await create_quote(client_b, tenant_b[1].id, tender_id)
    await confirm_new_quote(client_a, tenant_a[1].id, tender_id)  # A ja l'esta analitzant

    after = await create_quote(client_b, tenant_b[1].id, tender_id)

    ignored = {"id", "expires_at", "status", "confirmed_at", "superseded_by_id"}
    assert {k: v for k, v in after.items() if k not in ignored} == {
        k: v for k, v in before.items() if k not in ignored
    }
    assert str(tenant_a[1].id) not in str(after)


async def test_a_company_cannot_read_or_confirm_another_companys_quote(
    client_a, client_b, tenant_a, tenant_b, plec, session
):
    quote = await create_quote(client_a, tenant_a[1].id, plec["document"].tender_id)

    read = await client_b.get(f"/api/v1/companies/{tenant_b[1].id}/analysis-quotes/{quote['id']}")
    confirm = await confirm_quote(client_b, tenant_b[1].id, quote["id"])

    assert read.status_code == 404
    assert confirm.status_code == 404
    assert await _count(session, JobRequest) == 0


# --------------------------------------------------------------------------- #
# Confirmacio                                                                    #
# --------------------------------------------------------------------------- #


async def test_the_old_analysis_post_asks_for_confirmation_and_runs_nothing(
    client_a, tenant_a, plec, session
):
    tender_id = plec["document"].tender_id
    response = await client_a.post(
        f"/api/v1/companies/{tenant_a[1].id}/tenders/{tender_id}/job-requests/analyze-requirements",
        json={"force": True, "document_ids": []},
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "analysis_confirmation_required"
    assert error["details"]["preview"].endswith(f"/tenders/{tender_id}/analysis-quotes")
    assert "/analysis-quotes/{quote_id}/confirm" in error["details"]["confirm"]
    assert await _count(session, WorkJob) == 0
    assert await _count(session, JobRequest) == 0
    assert fake.SENT_PAYLOADS == []


async def test_confirming_creates_the_request_and_links_the_quote(
    client_a, tenant_a, plec, session
):
    quote = await create_quote(client_a, tenant_a[1].id, plec["document"].tender_id)

    response = await confirm_quote(client_a, tenant_a[1].id, quote["id"])

    assert response.status_code == 202, response.text
    assert response.json()["quote_id"] == quote["id"]
    request = await session.scalar(sa.select(JobRequest))
    stored = await session.get(AnalysisQuote, uuid.UUID(quote["id"]), populate_existing=True)
    job = await session.get(WorkJob, request.work_job_id)
    assert request.analysis_quote_id == stored.id
    assert stored.status == AnalysisQuoteStatus.CONFIRMED and stored.confirmed_at is not None
    assert job.input_fingerprint == stored.input_fingerprint
    assert fake.SENT_PAYLOADS == []


async def _assert_replaced(response, session, old_quote_id, code, old_status):
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == code
    new = error["details"]["quote"]
    assert new["id"] != old_quote_id and new["status"] == "open"
    old = await session.get(AnalysisQuote, uuid.UUID(old_quote_id), populate_existing=True)
    assert old.status == old_status
    assert str(old.superseded_by_id) == new["id"]
    assert await _count(session, JobRequest) == 0
    assert await _count(session, WorkJob) == 0
    assert fake.SENT_PAYLOADS == []
    return new


async def test_changed_documents_between_preview_and_confirmation_create_nothing(
    client_a, tenant_a, plec, session
):
    quote = await create_quote(client_a, tenant_a[1].id, plec["document"].tender_id)
    plec["content"].sha256 = "e" * 64
    await session.commit()

    response = await confirm_quote(client_a, tenant_a[1].id, quote["id"])

    new = await _assert_replaced(
        response, session, quote["id"], "preview_changed", AnalysisQuoteStatus.SUPERSEDED
    )
    assert new["input_fingerprint"] != quote["input_fingerprint"]
    # El preview nou si que es pot confirmar.
    assert (await confirm_quote(client_a, tenant_a[1].id, new["id"])).status_code == 202


async def test_changed_chunking_is_a_changed_preview(
    client_a, tenant_a, plec, session, monkeypatch
):
    quote = await create_quote(client_a, tenant_a[1].id, plec["document"].tender_id)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 2)

    response = await confirm_quote(client_a, tenant_a[1].id, quote["id"])

    new = await _assert_replaced(
        response, session, quote["id"], "preview_changed", AnalysisQuoteStatus.SUPERSEDED
    )
    assert new["input_fingerprint"] == quote["input_fingerprint"]
    assert new["pages_per_chunk"] == 2


async def test_a_new_planning_version_is_a_changed_preview(
    client_a, tenant_a, plec, session, monkeypatch
):
    quote = await create_quote(client_a, tenant_a[1].id, plec["document"].tender_id)
    monkeypatch.setattr(analysis_planning, "PLANNING_VERSION", "analysis-planning-v2")

    response = await confirm_quote(client_a, tenant_a[1].id, quote["id"])

    new = await _assert_replaced(
        response, session, quote["id"], "preview_changed", AnalysisQuoteStatus.SUPERSEDED
    )
    assert new["planning_version"] == "analysis-planning-v2"


async def test_an_expired_quote_is_shown_again(client_a, tenant_a, plec, session):
    quote = await create_quote(client_a, tenant_a[1].id, plec["document"].tender_id)
    await session.execute(
        sa.update(AnalysisQuote)
        .where(AnalysisQuote.id == uuid.UUID(quote["id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()

    response = await confirm_quote(client_a, tenant_a[1].id, quote["id"])

    await _assert_replaced(
        response, session, quote["id"], "quote_expired", AnalysisQuoteStatus.EXPIRED
    )


async def test_confirming_an_existing_result_reuses_it(
    client_a, tenant_a, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    await request_analysis(client_a, tenant_a[1].id, tender_id, session_factory)
    calls = len(fake.SENT_PAYLOADS)
    quote = await create_quote(client_a, tenant_a[1].id, tender_id)

    response = await confirm_quote(client_a, tenant_a[1].id, quote["id"])

    assert response.status_code == 200
    assert response.json()["outcome"] == "reused_existing"
    stored = await session.get(AnalysisQuote, uuid.UUID(quote["id"]), populate_existing=True)
    assert stored.status == AnalysisQuoteStatus.CONFIRMED
    request = await session.scalar(
        sa.select(JobRequest).where(JobRequest.analysis_quote_id == stored.id)
    )
    assert request.reused_existing is True
    assert len(fake.SENT_PAYLOADS) == calls


# --------------------------------------------------------------------------- #
# Limits                                                                         #
# --------------------------------------------------------------------------- #


async def test_an_analysis_above_the_chunk_limit_is_blocked(
    client_a, tenant_a, plec, session, monkeypatch
):
    monkeypatch.setattr(settings, "ANALYSIS_MAX_CHUNKS_PER_RUN", 0)
    quote = await create_quote(client_a, tenant_a[1].id, plec["document"].tender_id)

    assert quote["allowed"] is False
    assert quote["blocking_reason"] == "analysis_too_large"

    response = await confirm_quote(client_a, tenant_a[1].id, quote["id"])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "analysis_too_large"
    assert await _count(session, WorkJob) == 0


async def test_the_active_limit_is_per_company(
    client_a, client_b, tenant_a, tenant_b, plec, session, session_factory, monkeypatch
):
    monkeypatch.setattr(settings, "ANALYSIS_MAX_ACTIVE_PER_COMPANY", 1)
    first_tender = plec["document"].tender_id
    second_tender = await _another_plec(session, first_tender)

    assert (await confirm_new_quote(client_a, tenant_a[1].id, first_tender)).status_code == 202
    blocked_quote = await create_quote(client_a, tenant_a[1].id, second_tender)
    blocked = await confirm_quote(client_a, tenant_a[1].id, blocked_quote["id"])

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "company_active_limit"
    stored = await session.get(
        AnalysisQuote, uuid.UUID(blocked_quote["id"]), populate_existing=True
    )
    assert stored.status == AnalysisQuoteStatus.OPEN, "no s'ha confirmat res"
    assert await _count(session, JobRequest) == 1

    # Una altra empresa no queda afectada pel limit d'A.
    assert (await confirm_new_quote(client_b, tenant_b[1].id, second_tender)).status_code == 202

    # Quan acaba la d'A, pot tornar a demanar.
    await run_worker(session_factory)
    assert (await confirm_quote(client_a, tenant_a[1].id, blocked_quote["id"])).status_code in (
        200,
        202,
    )


async def test_the_worker_refuses_a_job_above_a_lowered_limit(
    tenant_a, plec, session, session_factory, monkeypatch
):
    submission = await _submit(session, tenant_a[1], tenant_a[0], plec["document"].tender_id)
    monkeypatch.setattr(settings, "ANALYSIS_MAX_CHUNKS_PER_RUN", 0)

    await run_worker(session_factory)

    job = await session.get(WorkJob, submission.request.work_job_id, populate_existing=True)
    request = await session.get(JobRequest, submission.request.id, populate_existing=True)
    assert job.status == WorkJobStatus.FAILED and job.error_code == "analysis_too_large"
    assert request.status == JobRequestStatus.FAILED
    assert await _count(session, RequirementExtractionRun) == 0
    assert fake.SENT_PAYLOADS == []


# --------------------------------------------------------------------------- #
# Permisos                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", [MembershipRole.VIEWER, MembershipRole.TECHNICAL])
async def test_roles_outside_the_configuration_cannot_confirm(
    client_a, tenant_a, plec, session, role
):
    await _set_role(session, tenant_a, role)
    quote = await create_quote(client_a, tenant_a[1].id, plec["document"].tender_id)

    response = await confirm_quote(client_a, tenant_a[1].id, quote["id"])

    assert response.status_code == 403
    assert await _count(session, JobRequest) == 0


async def test_a_bid_manager_can_confirm(client_a, tenant_a, plec, session):
    await _set_role(session, tenant_a, MembershipRole.BID_MANAGER)
    response = await confirm_new_quote(client_a, tenant_a[1].id, plec["document"].tender_id)
    assert response.status_code == 202


async def test_technical_can_be_allowed_by_configuration(
    client_a, tenant_a, plec, session, monkeypatch
):
    monkeypatch.setattr(settings, "ANALYSIS_CONFIRM_ROLES", ["owner", "technical"])
    await _set_role(session, tenant_a, MembershipRole.TECHNICAL)

    response = await confirm_new_quote(client_a, tenant_a[1].id, plec["document"].tender_id)

    assert response.status_code == 202


def test_unknown_roles_are_rejected_by_the_configuration():
    with pytest.raises(ValidationError):
        Settings(ANALYSIS_CONFIRM_ROLES=["cap_de_colla"])


async def test_a_viewer_cannot_request_document_processing(
    client_a, tenant_a, tender_document, session
):
    await _set_role(session, tenant_a, MembershipRole.VIEWER)
    response = await client_a.post(
        f"/api/v1/companies/{tenant_a[1].id}/tenders/{tender_document.tender_id}"
        "/job-requests/process-documents",
        json={"document_ids": [str(tender_document.id)]},
    )
    assert response.status_code == 403


async def test_the_limits_endpoint_reports_limits_and_permissions(
    client_a, tenant_a, plec, session, monkeypatch
):
    monkeypatch.setattr(settings, "ANALYSIS_MAX_CHUNKS_PER_RUN", 25)
    await confirm_new_quote(client_a, tenant_a[1].id, plec["document"].tender_id)

    owner = (await client_a.get(f"/api/v1/companies/{tenant_a[1].id}/analysis-limits")).json()
    await _set_role(session, tenant_a, MembershipRole.VIEWER)
    viewer = (await client_a.get(f"/api/v1/companies/{tenant_a[1].id}/analysis-limits")).json()

    assert owner["max_chunks_per_run"] == 25
    assert owner["max_active_per_company"] == settings.ANALYSIS_MAX_ACTIVE_PER_COMPANY
    assert owner["active_now"] == 1
    assert owner["can_confirm"] is True and owner["can_process_documents"] is True
    assert viewer["can_confirm"] is False and viewer["can_process_documents"] is False


# --------------------------------------------------------------------------- #
# PostgreSQL: concurrencia                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.postgres
async def test_pg_simultaneous_confirmations_respect_the_active_limit(
    tenant_a, plec, session, session_factory, monkeypatch
):
    monkeypatch.setattr(settings, "ANALYSIS_MAX_ACTIVE_PER_COMPANY", 1)
    user, company = tenant_a
    tenders = [plec["document"].tender_id, await _another_plec(session, plec["document"].tender_id)]
    quote_ids = []
    for tender_id in tenders:
        quote = await analysis_quotes.create_quote(
            session,
            company_id=company.id,
            user_id=user.id,
            tender_id=tender_id,
            provider_factory=fake.FakeProvider,
        )
        quote_ids.append(quote.id)

    async def confirm(quote_id):
        async with session_factory() as s:
            try:
                await analysis_quotes.confirm_quote(
                    s,
                    company_id=company.id,
                    user_id=user.id,
                    role=MembershipRole.OWNER,
                    quote_id=quote_id,
                    provider_factory=fake.FakeProvider,
                )
                return "ok"
            except CompanyActiveLimitError:
                return "limit"

    results = sorted(await asyncio.gather(*(confirm(q) for q in quote_ids)))

    assert results == ["limit", "ok"]
    pending = await session.scalar(
        sa.select(sa.func.count())
        .select_from(JobRequest)
        .where(JobRequest.company_id == company.id, JobRequest.status == JobRequestStatus.PENDING)
    )
    assert pending == 1
