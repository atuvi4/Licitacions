"""Feina asincrona: la cua, el worker, les peticions dels tenants i els invariants.

Tot amb el proveidor fals. El que es protegeix, per ordre d'importancia:

* cap peticio HTTP no executa la feina llarga;
* una analisi confirmada no s'executa sobre uns plecs diferents (`input_changed`);
* perdre el lease talla les crides pagades seguents i no tanca el run;
* un job orfe es reconcilia amb l'estat real del run, i mai no es reexecuta sol;
* dues empreses comparteixen una execucio sense saber-ho l'una de l'altra.

Els tests marcats `postgres` proven la concurrencia real (`SKIP LOCKED`, bloquejos
de fila) i nomes corren amb `--postgres`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.ai.profiles import V1
from app.ai.providers import fake
from app.core.config import settings
from app.core.errors import (
    AnalysisRunReclaimedError,
    NotFoundError,
    OperationBusyError,
)
from app.jobs import queue
from app.jobs import requests as job_requests
from app.jobs.params import analysis_dedupe_key
from app.jobs.worker import LeaseGuard, Worker
from app.models.enums import (
    AnalysisRunStatus,
    ExtractionQuality,
    ExtractionStatus,
    JobOutcome,
    JobRequestStatus,
    PageExtractionStatus,
    TenderDocumentType,
    WorkJobStatus,
    WorkJobType,
)
from app.models.jobs import JobRequest, WorkJob
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.submission import SubmissionPackageApproval
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender
from app.models.workspace import WorkspaceDecision
from app.services.requirement_analysis import analysis_input, analyze_tender_requirements
from tests.jobs_support import confirm_new_quote, confirm_quote, create_quote, run_worker

QUOTE = "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros"


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


def _page_text(number: int) -> str:
    return (
        f"Pagina {number}. Clausula de solvencia.\n"
        f"{QUOTE} en el lote {number}.\n"
    )


async def _add_extracted_pages(session, tender_document, pages: int, sha: str = "a" * 64):
    tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS
    content = TenderDocumentContent(
        sha256=sha,
        storage_key=f"tenders/documents/aa/aa/{sha}.pdf",
        file_size=2048,
        mime_type="application/pdf",
        detected_extension="pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()
    texts = [_page_text(n) for n in range(1, pages + 1)]
    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="pypdf-v1",
        revision_number=1,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=pages,
        extracted_page_count=pages,
        empty_page_count=0,
        text_char_count=sum(len(t) for t in texts),
        ocr_candidate=False,
        is_current=True,
    )
    session.add(extraction)
    await session.flush()
    for number, text in enumerate(texts, start=1):
        session.add(
            TenderDocumentPage(
                extraction_id=extraction.id,
                page_number=number,
                text=text,
                char_count=len(text),
                text_hash="h" * 64,
                status=PageExtractionStatus.EXTRACTED,
            )
        )
    session.add(
        TenderDocumentFile(
            tender_document_id=tender_document.id,
            content_id=content.id,
            source_url=tender_document.url,
            version_number=1,
            is_current=True,
            download_status="downloaded",
        )
    )
    await session.commit()
    return {"document": tender_document, "extraction": extraction, "content": content}


@pytest.fixture
async def plec(session, tender_document):
    return await _add_extracted_pages(session, tender_document, pages=2)


def _analyze_url(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/job-requests/analyze-requirements"


async def _request(client, company_id, tender_id):
    """La interficie: preview i confirmacio."""
    return await confirm_new_quote(client, company_id, tender_id)


async def _count(session, model) -> int:
    return await session.scalar(sa.select(sa.func.count()).select_from(model))


async def _submit(session, company, user, tender_id, **options):
    return await job_requests.submit_analysis_request(
        session,
        company_id=company.id,
        user_id=user.id,
        tender_id=tender_id,
        provider_factory=fake.FakeProvider,
        **options,
    )


def _no_execution(*_args, **_kwargs):
    raise AssertionError("una peticio HTTP no pot executar la feina llarga")


# --------------------------------------------------------------------------- #
# Peticions: crear, deduplicar, reaprofitar                                      #
# --------------------------------------------------------------------------- #


async def test_a_request_only_enqueues(client_a, tenant_a, plec, session, monkeypatch):
    from app.jobs import handlers
    from app.services import requirement_analysis

    monkeypatch.setattr(requirement_analysis, "analyze_tender_requirements", _no_execution)
    monkeypatch.setattr(handlers, "analyze_tender_requirements", _no_execution)
    _, company = tenant_a
    tender_id = plec["document"].tender_id

    response = await _request(client_a, company.id, tender_id)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["already_requested"] is False
    job = await session.scalar(sa.select(WorkJob))
    assert job.status == WorkJobStatus.QUEUED
    assert job.input_fingerprint == (await analysis_input(session, tender_id)).fingerprint
    assert (job.provider, job.model) == ("fake", "fake-model-v1")
    request = await session.scalar(sa.select(JobRequest))
    assert request.status == JobRequestStatus.PENDING and request.work_job_id == job.id
    assert fake.SENT_PAYLOADS == []


async def test_a_double_click_returns_the_same_request(client_a, tenant_a, plec, session):
    _, company = tenant_a
    tender_id = plec["document"].tender_id

    quote = await create_quote(client_a, company.id, tender_id)
    first = await confirm_quote(client_a, company.id, quote["id"])
    again = await confirm_quote(client_a, company.id, quote["id"])
    second = await _request(client_a, company.id, tender_id)

    assert first.status_code == 202
    assert again.status_code == 409 and again.json()["error"]["code"] == "quote_not_open"
    assert second.status_code == 200
    assert second.json()["already_requested"] is True
    assert second.json()["id"] == first.json()["id"]
    assert await _count(session, WorkJob) == 1
    assert await _count(session, JobRequest) == 1


async def test_an_existing_result_is_reused_without_enqueuing(
    client_a, tenant_a, plec, session, session_factory
):
    _, company = tenant_a
    tender_id = plec["document"].tender_id
    await _request(client_a, company.id, tender_id)
    await run_worker(session_factory)
    calls = len(fake.SENT_PAYLOADS)

    response = await _request(client_a, company.id, tender_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["outcome"] == "reused_existing"
    assert body["reused_existing"] is True
    assert body["result_run_id"]
    assert await _count(session, WorkJob) == 1, "no s'ha encuat res"
    assert len(fake.SENT_PAYLOADS) == calls


async def test_two_companies_share_one_execution_and_cannot_tell(
    client_a, client_b, tenant_a, tenant_b, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    a = await _request(client_a, tenant_a[1].id, tender_id)
    b = await _request(client_b, tenant_b[1].id, tender_id)

    assert a.status_code == b.status_code == 202
    assert a.json().keys() == b.json().keys()
    for response in (a, b):
        body = response.json()
        assert body["status"] == "accepted" and body["already_requested"] is False
        assert body["progress"] is None
        # A la cua i sense worker: ni l'una ni l'altra la veuen començada.
        assert body["started"] is False
        assert "work_job" not in response.text and "linked" not in response.text
    assert await _count(session, WorkJob) == 1
    requests = (await session.scalars(sa.select(JobRequest))).all()
    assert len(requests) == 2 and len({r.work_job_id for r in requests}) == 1

    await run_worker(session_factory)

    url_a = f"/api/v1/companies/{tenant_a[1].id}/job-requests/{a.json()['id']}"
    url_b = f"/api/v1/companies/{tenant_b[1].id}/job-requests/{b.json()['id']}"
    final_a = await client_a.get(url_a)
    final_b = await client_b.get(url_b)
    assert final_a.json()["status"] == final_b.json()["status"] == "succeeded"
    assert final_a.json()["result_run_id"] == final_b.json()["result_run_id"]
    assert len(fake.SENT_PAYLOADS) == 1, "una sola execucio (1 chunk), no dues"


async def test_a_company_cannot_read_or_cancel_another_companys_request(
    client_a, client_b, tenant_a, tenant_b, plec
):
    tender_id = plec["document"].tender_id
    a = await _request(client_a, tenant_a[1].id, tender_id)
    request_id = a.json()["id"]

    read = await client_b.get(f"/api/v1/companies/{tenant_b[1].id}/job-requests/{request_id}")
    cancel = await client_b.post(
        f"/api/v1/companies/{tenant_b[1].id}/job-requests/{request_id}/cancel"
    )
    listing = await client_b.get(
        f"/api/v1/companies/{tenant_b[1].id}/tenders/{tender_id}/job-requests"
    )

    assert read.status_code == 404
    assert cancel.status_code == 404
    assert listing.json() == []


async def test_there_is_no_global_active_jobs_endpoint(client_a, plec):
    response = await client_a.get(f"/api/v1/tenders/{plec['document'].tender_id}/jobs/active")
    assert response.status_code == 404


async def test_the_progress_seen_by_a_tenant_starts_at_its_request():
    job = WorkJob(status=WorkJobStatus.RUNNING, progress_current=5, progress_total=8)
    now = datetime.now(UTC)
    late = JobRequest(
        job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
        tender_id=uuid.uuid4(),
        status=JobRequestStatus.PENDING,
        progress_baseline=3,
        requested_at=now,
        reused_existing=False,
    )
    joined_now = JobRequest(
        job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
        tender_id=uuid.uuid4(),
        status=JobRequestStatus.PENDING,
        progress_baseline=5,
        requested_at=now,
        reused_existing=False,
    )

    assert job_requests.tenant_view(late, job)["progress"] == {"done": 2, "total": 5}
    assert job_requests.tenant_view(joined_now, job)["progress"] is None
    assert job_requests.tenant_view(late, job)["status"] == "accepted"


def _pending(
    requested_at: datetime, status: JobRequestStatus = JobRequestStatus.PENDING
) -> JobRequest:
    return JobRequest(
        job_type=WorkJobType.PROCESS_TENDER_DOCUMENTS,
        tender_id=uuid.uuid4(),
        status=status,
        progress_baseline=0,
        requested_at=requested_at,
        reused_existing=False,
    )


async def test_a_tenant_sees_queued_apart_from_running_without_learning_who_asked_first():
    asked = datetime.now(UTC)
    request = _pending(asked)

    # A la cua (cap worker l'ha agafada) o sense execució: encara no ha començat.
    queued = WorkJob(status=WorkJobStatus.QUEUED)
    assert job_requests.tenant_view(request, queued)["started"] is False
    assert job_requests.tenant_view(request, None)["started"] is False

    # Començada després de la petició: s'executa per a aquesta petició.
    running = WorkJob(status=WorkJobStatus.RUNNING, started_at=asked + timedelta(seconds=5))
    assert job_requests.tenant_view(request, running)["started"] is True
    orphaned = WorkJob(status=WorkJobStatus.ORPHANED, started_at=asked + timedelta(seconds=5))
    assert job_requests.tenant_view(request, orphaned)["started"] is True

    # Començada ABANS de la petició (la d'una altra empresa): no es revela fins
    # que avança des d'aquesta, com el progrés; i mai se n'exposa l'hora.
    earlier = WorkJob(status=WorkJobStatus.RUNNING, started_at=asked - timedelta(minutes=3),
                      progress_current=3, progress_total=8)
    joined = _pending(asked)
    joined.progress_baseline = 3
    view = job_requests.tenant_view(joined, earlier)
    assert view["started"] is False and view["progress"] is None
    assert "started_at" not in view
    earlier.progress_current = 5
    assert job_requests.tenant_view(joined, earlier)["started"] is True

    # SQLite torna dates sense zona: la comparació no peta.
    naive_start = (asked + timedelta(seconds=1)).replace(tzinfo=None)
    naive = WorkJob(status=WorkJobStatus.RUNNING, started_at=naive_start)
    assert job_requests.tenant_view(request, naive)["started"] is True

    # Acabada: el camp ja no diu res; l'estat final és el que compta.
    for status in (JobRequestStatus.SUCCEEDED, JobRequestStatus.FAILED, JobRequestStatus.CANCELLED):
        assert job_requests.tenant_view(_pending(asked, status), running)["started"] is None


async def test_cancelling_detaches_the_request_and_only_cancels_unneeded_queued_work(
    tenant_a, tenant_b, plec, session
):
    tender_id = plec["document"].tender_id
    a = await _submit(session, tenant_a[1], tenant_a[0], tender_id)
    b = await _submit(session, tenant_b[1], tenant_b[0], tender_id)
    job_id = a.request.work_job_id

    await job_requests.cancel_request(
        session, company_id=tenant_a[1].id, request_id=a.request.id
    )
    job = await session.get(WorkJob, job_id, populate_existing=True)
    assert job.status == WorkJobStatus.QUEUED, "B encara la necessita"

    await job_requests.cancel_request(
        session, company_id=tenant_b[1].id, request_id=b.request.id
    )
    job = await session.get(WorkJob, job_id, populate_existing=True)
    assert job.status == WorkJobStatus.CANCELLED


async def test_a_normal_request_joins_an_active_force_execution(tenant_a, tenant_b, plec, session):
    tender_id = plec["document"].tender_id
    forced = await _submit(session, tenant_a[1], tenant_a[0], tender_id, force=True)
    normal = await _submit(session, tenant_b[1], tenant_b[0], tender_id)

    assert normal.request.work_job_id == forced.request.work_job_id
    assert await _count(session, WorkJob) == 1


async def test_a_force_request_never_joins_a_normal_execution(
    tenant_a, tenant_b, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    normal = await _submit(session, tenant_a[1], tenant_a[0], tender_id)
    forced = await _submit(session, tenant_b[1], tenant_b[0], tender_id, force=True)

    assert forced.request.work_job_id != normal.request.work_job_id
    assert await _count(session, WorkJob) == 2

    executed = await run_worker(session_factory)

    assert len(executed) == 2, "en serie, un rere l'altre"
    forced_job = await session.get(WorkJob, forced.request.work_job_id, populate_existing=True)
    normal_job = await session.get(WorkJob, normal.request.work_job_id, populate_existing=True)
    # La force analitza sempre de nou; la normal pot reaprofitar-la si passa despres.
    assert forced_job.outcome == JobOutcome.EXECUTED
    assert normal_job.outcome in (JobOutcome.EXECUTED, JobOutcome.REUSED)
    expected_calls = 1 if normal_job.outcome == JobOutcome.REUSED else 2
    assert len(fake.SENT_PAYLOADS) == expected_calls


async def test_a_queued_normal_job_reuses_a_result_that_appeared_meanwhile(
    tenant_a, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    submission = await _submit(session, tenant_a[1], tenant_a[0], tender_id)
    tender = await session.get(Tender, tender_id)
    # Algu (un script d'operador) l'analitza abans que el worker arribi al job.
    done = await analyze_tender_requirements(session, tender, provider=fake.FakeProvider())
    calls = len(fake.SENT_PAYLOADS)

    await run_worker(session_factory)

    request = await session.get(JobRequest, submission.request.id, populate_existing=True)
    assert request.status == JobRequestStatus.SUCCEEDED
    assert request.outcome == JobOutcome.REUSED
    assert request.result_run_id == done.run.id
    assert len(fake.SENT_PAYLOADS) == calls


async def test_an_operator_run_in_flight_makes_the_request_busy(tenant_a, plec, session):
    tender_id = plec["document"].tender_id
    session.add(_run(tender_id, status=AnalysisRunStatus.RUNNING))
    await session.commit()

    with pytest.raises(OperationBusyError):
        await _submit(session, tenant_a[1], tenant_a[0], tender_id)


async def test_processing_an_unknown_document_is_404(tenant_a, plec, session):
    with pytest.raises(NotFoundError):
        await job_requests.submit_process_request(
            session,
            company_id=tenant_a[1].id,
            user_id=tenant_a[0].id,
            tender_id=plec["document"].tender_id,
            document_ids=[uuid.uuid4()],
        )


# --------------------------------------------------------------------------- #
# Worker: execucio, identitat i input immutable                                  #
# --------------------------------------------------------------------------- #


async def test_the_run_records_its_job_and_the_request_gets_the_result(
    tenant_a, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    submission = await _submit(session, tenant_a[1], tenant_a[0], tender_id)

    await run_worker(session_factory)

    job = await session.get(WorkJob, submission.request.work_job_id, populate_existing=True)
    run = await session.scalar(
        sa.select(RequirementExtractionRun).where(RequirementExtractionRun.work_job_id == job.id)
    )
    request = await session.get(JobRequest, submission.request.id, populate_existing=True)
    assert job.status == WorkJobStatus.SUCCEEDED and job.outcome == JobOutcome.EXECUTED
    assert (job.progress_current, job.progress_total) == (1, 1)
    assert run is not None and run.status == AnalysisRunStatus.COMPLETED
    assert request.result_run_id == run.id
    assert not hasattr(WorkJob, "result_run_id"), "l'enllaç canonic es run.work_job_id"


async def test_changed_documents_stop_the_job_before_any_provider_call(
    tenant_a, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    submission = await _submit(session, tenant_a[1], tenant_a[0], tender_id)
    # Els plecs canvien despres de confirmar la peticio.
    plec["content"].sha256 = "b" * 64
    await session.commit()

    await run_worker(session_factory)

    job = await session.get(WorkJob, submission.request.work_job_id, populate_existing=True)
    request = await session.get(JobRequest, submission.request.id, populate_existing=True)
    assert job.status == WorkJobStatus.FAILED and job.error_code == "input_changed"
    assert request.status == JobRequestStatus.FAILED and request.error_code == "input_changed"
    assert fake.SENT_PAYLOADS == []
    assert await _count(session, RequirementExtractionRun) == 0


async def test_a_worker_with_another_model_does_not_run_the_job(
    tenant_a, plec, session, session_factory
):
    submission = await _submit(session, tenant_a[1], tenant_a[0], plec["document"].tender_id)

    await run_worker(session_factory, provider_factory=lambda: fake.FakeProvider(model="altre"))

    job = await session.get(WorkJob, submission.request.work_job_id, populate_existing=True)
    assert job.error_code == "provider_changed"
    assert fake.SENT_PAYLOADS == []


async def test_nothing_in_the_prompt_identifies_the_tenant(
    tenant_a, plec, session, session_factory
):
    user, company = tenant_a
    submission = await _submit(session, company, user, plec["document"].tender_id)

    await run_worker(session_factory)

    sent = json.dumps(fake.SENT_PAYLOADS, default=str)
    for secret in (str(company.id), company.legal_name, str(user.id), user.email,
                   str(submission.request.id)):
        assert secret not in sent


async def test_jobs_never_take_human_decisions(tenant_a, plec, session, session_factory):
    await _submit(session, tenant_a[1], tenant_a[0], plec["document"].tender_id)

    await run_worker(session_factory)

    assert await _count(session, WorkspaceDecision) == 0
    assert await _count(session, SubmissionPackageApproval) == 0


async def test_a_queued_job_survives_a_new_worker_process(tenant_a, plec, session, session_factory):
    """La cua es la base de dades: un worker nou (API reiniciada) troba la feina."""
    submission = await _submit(session, tenant_a[1], tenant_a[0], plec["document"].tender_id)
    await session.close()

    fresh = Worker(session_factory, provider_factory=fake.FakeProvider, heartbeat_seconds=3600)
    job = await fresh.run_once()

    assert job is not None and job.id == submission.request.work_job_id


async def test_an_unexpected_crash_fails_the_job_and_its_own_run(
    tenant_a, plec, session, session_factory
):
    class Exploding(fake.FakeProvider):
        async def extract_structured(self, **kwargs):
            raise RuntimeError("bug")

    submission = await _submit(session, tenant_a[1], tenant_a[0], plec["document"].tender_id)

    await run_worker(session_factory, provider_factory=Exploding)

    job = await session.get(WorkJob, submission.request.work_job_id, populate_existing=True)
    run = await session.scalar(
        sa.select(RequirementExtractionRun).where(RequirementExtractionRun.work_job_id == job.id)
    )
    assert job.status == WorkJobStatus.FAILED and job.error_code == "internal_error"
    assert run.status == AnalysisRunStatus.FAILED and run.error_code == "internal_error"


# --------------------------------------------------------------------------- #
# Lease: perdre'l talla les crides pagades                                        #
# --------------------------------------------------------------------------- #


async def test_losing_the_lease_after_chunk_2_of_8_stops_paid_calls(
    tenant_a, tender_document, session, session_factory, monkeypatch
):
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    await _add_extracted_pages(session, tender_document, pages=8)
    submission = await _submit(session, tenant_a[1], tenant_a[0], tender_document.tender_id)
    job_id = submission.request.work_job_id

    class StolenAfterTwo(fake.FakeProvider):
        calls = 0

        async def extract_structured(self, **kwargs):
            result = await super().extract_structured(**kwargs)
            type(self).calls += 1
            if type(self).calls == 2:
                # El reaper (o un altre worker) li pren el job mentre la segona
                # crida era en vol.
                async with session_factory() as other:
                    await other.execute(
                        sa.update(WorkJob)
                        .where(WorkJob.id == job_id)
                        .values(
                            status=WorkJobStatus.ORPHANED,
                            lease_token=None,
                            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
                        )
                    )
                    await other.commit()
            return result

    await run_worker(session_factory, provider_factory=StolenAfterTwo)

    assert StolenAfterTwo.calls == 2, "cap chunk nou despres de perdre el lease"
    assert len(fake.SENT_PAYLOADS) == 2, "nomes 2 payloads han arribat al proveidor"
    job = await session.get(WorkJob, job_id, populate_existing=True)
    run = await session.scalar(
        sa.select(RequirementExtractionRun)
        .where(RequirementExtractionRun.work_job_id == job_id)
        .execution_options(populate_existing=True)
    )
    request = await session.get(JobRequest, submission.request.id, populate_existing=True)
    assert run.chunks_total == 8
    assert job.status == WorkJobStatus.ORPHANED
    assert run.status == AnalysisRunStatus.RUNNING, "no es tanca com si fos complet"
    assert request.status == JobRequestStatus.PENDING
    assert await _count(session, Requirement) == 0

    # I no es reexecuta: la reconciliacio el tanca a partir del run real.
    async with session_factory() as maintenance:
        await queue.reconcile_orphaned(maintenance, grace_seconds=0)
    job = await session.get(WorkJob, job_id, populate_existing=True)
    run = await session.get(RequirementExtractionRun, run.id, populate_existing=True)
    request = await session.get(JobRequest, submission.request.id, populate_existing=True)
    assert job.status == WorkJobStatus.FAILED and job.error_code == "worker_lost"
    assert run.status == AnalysisRunStatus.FAILED and run.error_code == "worker_lost"
    assert request.status == JobRequestStatus.FAILED
    assert StolenAfterTwo.calls == 2
    assert len(fake.SENT_PAYLOADS) == 2, "la reconciliacio no reexecuta res"


class _BrokenFactory:
    """Una base de dades que no respon: el lease no es pot verificar."""

    def __call__(self):
        return self

    async def __aenter__(self):
        raise ConnectionError("sense connexio")

    async def __aexit__(self, *exc):
        return False


async def test_a_transient_heartbeat_error_is_not_a_lost_lease():
    now = datetime.now(UTC)
    clock = {"now": now}
    guard = LeaseGuard(
        _BrokenFactory(),
        job_id=uuid.uuid4(),
        token=uuid.uuid4(),
        expires_at=now + timedelta(seconds=60),
        lease_seconds=60,
        heartbeat_seconds=3600,
        clock=lambda: clock["now"],
    )

    assert await guard.verify() is True, "encara dins del lease que sabem renovat"
    assert guard.lost is False

    clock["now"] = now + timedelta(seconds=61)
    assert await guard.verify() is False, "passat el lease no es pot afirmar la propietat"
    assert guard.lost is False, "no s'inventa una perdua definitiva"


async def test_a_definitive_loss_is_what_the_database_says(
    tenant_a, plec, session, session_factory
):
    await _submit(session, tenant_a[1], tenant_a[0], plec["document"].tender_id)
    async with session_factory() as claiming:
        job = await queue.claim_next(claiming, "w1")
    guard = LeaseGuard(
        session_factory,
        job_id=job.id,
        token=uuid.uuid4(),  # un token que no es el seu
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
        lease_seconds=60,
        heartbeat_seconds=3600,
    )

    assert await guard.verify() is False
    assert guard.lost is True


# --------------------------------------------------------------------------- #
# Fencing, reaper i reconciliacio                                                #
# --------------------------------------------------------------------------- #


def _run(tender_id, *, status, work_job_id=None, fingerprint="f" * 64, is_current=False,
         error_code=None, started_at=None) -> RequirementExtractionRun:
    return RequirementExtractionRun(
        tender_id=tender_id,
        work_job_id=work_job_id,
        status=status,
        input_fingerprint=fingerprint,
        extractor_version=V1.extractor_version,
        prompt_version=V1.prompt_version,
        schema_version=V1.schema_version,
        provider="fake",
        model="fake-model-v1",
        started_at=started_at or datetime.now(UTC),
        is_current=is_current,
        error_code=error_code,
    )


async def _orphan(session, tender_id, *, expired_seconds_ago=10, fingerprint="f" * 64):
    job = WorkJob(
        job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
        status=WorkJobStatus.ORPHANED,
        tender_id=tender_id,
        parameters={"profile": "v1", "force": False, "document_ids": []},
        dedupe_key=f"test:{uuid.uuid4()}",
        input_fingerprint=fingerprint,
        provider="fake",
        model="fake-model-v1",
        attempts=1,
        max_attempts=1,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=expired_seconds_ago),
    )
    session.add(job)
    await session.flush()
    session.add(
        JobRequest(
            company_id=(await session.scalar(sa.select(JobRequest.company_id).limit(1)))
            or (await _any_company(session)),
            tender_id=tender_id,
            job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
            parameters={},
            work_job_id=job.id,
            status=JobRequestStatus.PENDING,
            requested_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return job


async def _any_company(session):
    from app.models.identity import Company

    return await session.scalar(sa.select(Company.id).limit(1))


async def _reconciled(session, session_factory, job, grace=0):
    async with session_factory() as maintenance:
        report = await queue.reconcile_orphaned(maintenance, grace_seconds=grace)
    job = await session.get(WorkJob, job.id, populate_existing=True)
    request = await session.scalar(
        sa.select(JobRequest)
        .where(JobRequest.work_job_id == job.id)
        .execution_options(populate_existing=True)
    )
    return report, job, request


@pytest.mark.parametrize(
    ("run_status", "job_status", "outcome", "error_code"),
    [
        (AnalysisRunStatus.COMPLETED, WorkJobStatus.SUCCEEDED, JobOutcome.EXECUTED, None),
        (AnalysisRunStatus.PARTIAL, WorkJobStatus.SUCCEEDED, JobOutcome.PARTIAL, None),
        (AnalysisRunStatus.FAILED, WorkJobStatus.FAILED, None, "all_chunks_failed"),
    ],
    ids=["completed", "partial", "failed"],
)
async def test_an_orphan_adopts_the_real_state_of_its_run(
    tenant_a, plec, session, session_factory, run_status, job_status, outcome, error_code
):
    tender_id = plec["document"].tender_id
    job = await _orphan(session, tender_id, expired_seconds_ago=1)
    session.add(
        _run(tender_id, status=run_status, work_job_id=job.id,
             error_code="all_chunks_failed" if run_status == AnalysisRunStatus.FAILED else None)
    )
    await session.commit()

    # Dins de la gracia i tot: el run ja ha acabat, no cal esperar.
    _, job, request = await _reconciled(session, session_factory, job, grace=3600)

    run = await session.scalar(
        sa.select(RequirementExtractionRun).where(RequirementExtractionRun.work_job_id == job.id)
    )
    assert job.status == job_status
    assert job.outcome == outcome
    assert job.error_code == error_code
    assert request.result_run_id == run.id
    assert request.status.value == job_status.value


async def test_an_orphan_with_a_running_run_waits_and_then_is_lost(
    tenant_a, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    job = await _orphan(session, tender_id, expired_seconds_ago=5)
    session.add(_run(tender_id, status=AnalysisRunStatus.RUNNING, work_job_id=job.id))
    await session.commit()

    report, job, _ = await _reconciled(session, session_factory, job, grace=3600)
    assert report.reconciled[job.id] == "waiting"
    assert job.status == WorkJobStatus.ORPHANED

    _, job, request = await _reconciled(session, session_factory, job, grace=0)
    run = await session.scalar(
        sa.select(RequirementExtractionRun)
        .where(RequirementExtractionRun.work_job_id == job.id)
        .execution_options(populate_existing=True)
    )
    assert run.status == AnalysisRunStatus.FAILED and run.error_code == "worker_lost"
    assert job.status == WorkJobStatus.FAILED and job.error_code == "worker_lost"
    assert request.status == JobRequestStatus.FAILED


async def test_an_orphan_without_run_is_reused_or_failed_after_the_grace(
    tenant_a, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    lost = await _orphan(session, tender_id, fingerprint="1" * 64)
    report, lost, _ = await _reconciled(session, session_factory, lost, grace=3600)
    assert report.reconciled[lost.id] == "waiting"
    _, lost, _ = await _reconciled(session, session_factory, lost, grace=0)
    assert lost.error_code == "worker_lost_before_start"

    reusable = _run(tender_id, status=AnalysisRunStatus.COMPLETED, fingerprint="2" * 64,
                    is_current=True)
    session.add(reusable)
    await session.commit()
    found = await _orphan(session, tender_id, fingerprint="2" * 64)
    _, found, request = await _reconciled(session, session_factory, found, grace=0)
    assert found.status == WorkJobStatus.SUCCEEDED and found.outcome == JobOutcome.REUSED
    assert request.result_run_id == reusable.id


async def test_a_zombie_cannot_finalise_a_run_declared_lost(
    tenant_a, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    tender = await session.get(Tender, tender_id)
    previous = await analyze_tender_requirements(session, tender, provider=fake.FakeProvider())
    plec["content"].sha256 = "c" * 64
    await session.commit()

    async def reconciler_declares_it_lost() -> bool:
        async with session_factory() as other:
            await other.execute(
                sa.update(RequirementExtractionRun)
                .where(RequirementExtractionRun.status == AnalysisRunStatus.RUNNING)
                .values(status=AnalysisRunStatus.FAILED, error_code="worker_lost")
            )
            await other.commit()
        return True

    async with session_factory() as zombie:
        tender = await zombie.get(Tender, tender_id)
        with pytest.raises(AnalysisRunReclaimedError):
            await analyze_tender_requirements(
                zombie,
                tender,
                provider=fake.FakeProvider(),
                should_continue=reconciler_declares_it_lost,
            )

    current = await session.scalar(
        sa.select(RequirementExtractionRun)
        .where(RequirementExtractionRun.is_current.is_(True))
        .execution_options(populate_existing=True)
    )
    assert current.id == previous.run.id, "l'analisi vigent anterior no es toca"


async def test_fencing_rejects_writes_from_a_worker_that_lost_the_job(
    tenant_a, plec, session, session_factory
):
    await _submit(session, tenant_a[1], tenant_a[0], plec["document"].tender_id)
    async with session_factory() as claiming:
        job = await queue.claim_next(claiming, "w1")
    stale = uuid.uuid4()

    async with session_factory() as s:
        assert await queue.renew_lease(s, job.id, stale) is None
        assert await queue.update_progress(s, job.id, stale, 1, 2) is False
        assert await queue.complete(s, job.id, stale, queue.JobCompletion.failed("x")) is False
        assert await queue.complete(
            s, job.id, job.lease_token, queue.JobCompletion.succeeded(JobOutcome.EXECUTED)
        ) is True
        # Terminal: ja no es pot tancar de nou.
        assert await queue.complete(
            s, job.id, job.lease_token, queue.JobCompletion.failed("x")
        ) is False

    job = await session.get(WorkJob, job.id, populate_existing=True)
    assert job.status == WorkJobStatus.SUCCEEDED and job.error_code is None


async def test_the_reaper_requeues_documents_and_orphans_analyses(
    tenant_a, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    past = datetime.now(UTC) - timedelta(seconds=5)
    process_retry = WorkJob(
        job_type=WorkJobType.PROCESS_TENDER_DOCUMENTS, status=WorkJobStatus.RUNNING,
        tender_id=tender_id, parameters={}, dedupe_key="p1", attempts=1, max_attempts=3,
        lease_token=uuid.uuid4(), lease_expires_at=past,
    )
    session.add(process_retry)
    await session.commit()

    async with session_factory() as s:
        report = await queue.reap_expired(s)
    process_retry = await session.get(WorkJob, process_retry.id, populate_existing=True)
    assert report.requeued == [process_retry.id]
    assert process_retry.status == WorkJobStatus.QUEUED and process_retry.run_after is not None

    process_retry.status = WorkJobStatus.RUNNING
    process_retry.attempts = 3
    process_retry.lease_expires_at = past
    await session.commit()
    async with session_factory() as s:
        await queue.reap_expired(s)
    process_retry = await session.get(WorkJob, process_retry.id, populate_existing=True)
    assert process_retry.status == WorkJobStatus.FAILED
    assert process_retry.error_code == "lease_expired"

    analysis = WorkJob(
        job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS, status=WorkJobStatus.RUNNING,
        tender_id=tender_id, parameters={"profile": "v1"}, dedupe_key="a1", attempts=1,
        max_attempts=1, lease_token=uuid.uuid4(), lease_expires_at=past,
    )
    session.add(analysis)
    await session.flush()
    session.add(_run(tender_id, status=AnalysisRunStatus.RUNNING, work_job_id=analysis.id))
    await session.commit()
    async with session_factory() as s:
        report = await queue.reap_expired(s)
    analysis = await session.get(WorkJob, analysis.id, populate_existing=True)
    run = await session.scalar(
        sa.select(RequirementExtractionRun)
        .where(RequirementExtractionRun.work_job_id == analysis.id)
        .execution_options(populate_existing=True)
    )
    assert report.orphaned == [analysis.id]
    assert analysis.status == WorkJobStatus.ORPHANED
    assert run.status == AnalysisRunStatus.RUNNING, "el reaper no toca el run"


async def test_a_long_run_with_a_live_job_is_not_reclaimed_as_stale(
    tenant_a, plec, session, session_factory
):
    from app.services.requirement_analysis import _reclaim_stale

    tender_id = plec["document"].tender_id
    stale = settings.REQUIREMENTS_ANALYSIS_STALE_SECONDS * 4
    long_ago = datetime.now(UTC) - timedelta(seconds=stale)
    live = WorkJob(
        job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS, status=WorkJobStatus.RUNNING,
        tender_id=tender_id, parameters={"profile": "v1"}, dedupe_key="live", attempts=1,
        max_attempts=1, lease_token=uuid.uuid4(),
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    session.add(live)
    await session.flush()
    run = _run(tender_id, status=AnalysisRunStatus.RUNNING, work_job_id=live.id,
               started_at=long_ago)
    session.add(run)
    await session.commit()

    await _reclaim_stale(session, tender_id)
    run = await session.get(RequirementExtractionRun, run.id, populate_existing=True)
    assert run.status == AnalysisRunStatus.RUNNING

    live.status = WorkJobStatus.FAILED
    await session.commit()
    await _reclaim_stale(session, tender_id)
    run = await session.get(RequirementExtractionRun, run.id, populate_existing=True)
    assert run.status == AnalysisRunStatus.FAILED and run.error_code == "stale"


async def test_the_dedupe_key_changes_with_every_identity_element():
    base = dict(
        tender_id=uuid.uuid4(), fingerprint="f" * 64, profile="v1", provider="fake",
        model="m", document_ids=[], force=False,
    )
    key = analysis_dedupe_key(**base)
    for field, value in (
        ("tender_id", uuid.uuid4()), ("fingerprint", "e" * 64), ("profile", "v2"),
        ("provider", "anthropic"), ("model", "m2"), ("document_ids", [uuid.uuid4()]),
        ("force", True),
    ):
        assert analysis_dedupe_key(**{**base, field: value}) != key, field


# --------------------------------------------------------------------------- #
# Invariants verificats explicitament                                            #
# --------------------------------------------------------------------------- #


def test_work_job_has_no_result_run_id_in_the_model_nor_in_the_migration():
    """L'unic enllaç WorkJob <-> run es `requirement_extraction_runs.work_job_id`."""
    from pathlib import Path

    assert "result_run_id" not in WorkJob.__table__.c
    assert "work_job_id" in RequirementExtractionRun.__table__.c
    migration = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0022_work_jobs.py"
    ).read_text(encoding="utf-8")
    work_jobs_table = migration.split("'work_jobs',", 1)[1].split("op.create_index", 1)[0]
    assert "result_run_id" not in work_jobs_table


async def test_a_work_job_cannot_have_two_runs(tenant_a, plec, session):
    from sqlalchemy.exc import IntegrityError

    tender_id = plec["document"].tender_id
    job = _queued(tender_id, WorkJobType.ANALYZE_TENDER_REQUIREMENTS)
    session.add(job)
    await session.flush()
    session.add(_run(tender_id, status=AnalysisRunStatus.FAILED, work_job_id=job.id))
    await session.commit()

    session.add(_run(tender_id, status=AnalysisRunStatus.COMPLETED, work_job_id=job.id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_linked_company_sees_nothing_of_the_other_one(
    client_a, client_b, tenant_a, tenant_b, plec, session
):
    """Ni identificadors, ni dades, ni el progres anterior a la seva peticio."""
    tender_id = plec["document"].tender_id
    user_a, company_a = tenant_a
    user_b, company_b = tenant_b

    first = await _request(client_a, company_a.id, tender_id)
    job = await session.scalar(sa.select(WorkJob))
    # La feina ja va per 3 de 8 quan B la demana.
    job.status = WorkJobStatus.RUNNING
    job.progress_current, job.progress_total = 3, 8
    await session.commit()

    second = await _request(client_b, company_b.id, tender_id)
    b_url = f"/api/v1/companies/{company_b.id}/job-requests/{second.json()['id']}"
    a_url = f"/api/v1/companies/{company_a.id}/job-requests/{first.json()['id']}"
    b_view = await client_b.get(b_url)
    b_list = await client_b.get(
        f"/api/v1/companies/{company_b.id}/tenders/{tender_id}/job-requests"
    )

    assert second.status_code == 202 and second.json()["status"] == "accepted"
    assert b_view.json()["progress"] is None, "el 3/8 anterior no es veu"
    for response in (second, b_view, b_list):
        text = response.text
        for secret in (
            str(company_a.id), company_a.legal_name, str(user_a.id), user_a.email,
            first.json()["id"], str(job.id),
        ):
            assert secret not in text
        assert "work_job" not in text and "linked" not in text
    assert [item["id"] for item in b_list.json()] == [second.json()["id"]]

    # Quan la feina avanca, cadascu veu el seu tram: A des del principi, B des del 3.
    job.progress_current = 5
    await session.commit()
    assert (await client_a.get(a_url)).json()["progress"] == {"done": 5, "total": 8}
    assert (await client_b.get(b_url)).json()["progress"] == {"done": 2, "total": 5}


# --------------------------------------------------------------------------- #
# PostgreSQL: concurrencia real                                                  #
# --------------------------------------------------------------------------- #


async def _tenders(session, count: int) -> list[uuid.UUID]:
    from app.models.sources import Source

    source = Source(code=f"jobs-{uuid.uuid4().hex[:8]}", name="Font de prova")
    session.add(source)
    await session.flush()
    tenders = [
        Tender(source_id=source.id, external_id=f"t-{uuid.uuid4().hex[:8]}", title="Licitacio")
        for _ in range(count)
    ]
    session.add_all(tenders)
    await session.commit()
    return [tender.id for tender in tenders]


def _queued(tender_id, job_type=WorkJobType.PROCESS_TENDER_DOCUMENTS, key=None) -> WorkJob:
    return WorkJob(
        job_type=job_type, status=WorkJobStatus.QUEUED, tender_id=tender_id, parameters={},
        dedupe_key=key or f"k:{uuid.uuid4()}", attempts=0, max_attempts=3,
    )


@pytest.mark.postgres
async def test_pg_concurrent_workers_never_claim_the_same_job(session, session_factory):
    tender_ids = await _tenders(session, 8)
    session.add_all([_queued(tender_id) for tender_id in tender_ids])
    await session.commit()

    async def drain(name: str) -> list[uuid.UUID]:
        claimed = []
        while True:
            async with session_factory() as s:
                job = await queue.claim_next(s, name)
            if job is None:
                return claimed
            claimed.append(job.id)

    results = await asyncio.gather(*(drain(f"w{i}") for i in range(4)))
    everything = [job_id for claimed in results for job_id in claimed]

    assert len(everything) == 8
    assert len(set(everything)) == 8, "cap job reclamat dues vegades"


@pytest.mark.postgres
async def test_pg_two_jobs_of_the_same_tender_never_run_together(session, session_factory):
    [tender_id] = await _tenders(session, 1)
    session.add_all(
        [
            _queued(tender_id, WorkJobType.PROCESS_TENDER_DOCUMENTS),
            _queued(tender_id, WorkJobType.ANALYZE_TENDER_REQUIREMENTS),
        ]
    )
    await session.commit()

    async def claim(name):
        async with session_factory() as s:
            return await queue.claim_next(s, name)

    claimed = [job for job in await asyncio.gather(claim("a"), claim("b")) if job is not None]

    assert len(claimed) == 1
    running = await session.scalar(
        sa.select(sa.func.count())
        .select_from(WorkJob)
        .where(WorkJob.status == WorkJobStatus.RUNNING)
    )
    assert running == 1


@pytest.mark.postgres
async def test_pg_simultaneous_requests_from_two_companies_share_one_job(
    tenant_a, tenant_b, plec, session, session_factory
):
    tender_id = plec["document"].tender_id

    async def submit(tenant):
        async with session_factory() as s:
            return await _submit(s, tenant[1], tenant[0], tender_id)

    first, second = await asyncio.gather(submit(tenant_a), submit(tenant_b))

    assert first.request.work_job_id == second.request.work_job_id
    assert await _count(session, WorkJob) == 1
    assert await _count(session, JobRequest) == 2


@pytest.mark.postgres
async def test_pg_a_zombie_finalising_and_the_reconciler_serialise(
    tenant_a, plec, session, session_factory
):
    tender_id = plec["document"].tender_id
    job = await _orphan(session, tender_id, expired_seconds_ago=5)
    run = _run(tender_id, status=AnalysisRunStatus.RUNNING, work_job_id=job.id)
    session.add(run)
    await session.commit()

    async with session_factory() as zombie:
        # El zombi bloqueja el run com fa `_finalise_run`...
        await zombie.execute(
            sa.select(RequirementExtractionRun.status)
            .where(RequirementExtractionRun.id == run.id)
            .with_for_update()
        )
        reconciling = asyncio.create_task(_reconciled(session, session_factory, job, grace=0))
        await asyncio.sleep(0.5)
        assert not reconciling.done(), "el reconciliador espera el bloqueig"
        # ...i el tanca be abans que el reconciliador el pugui donar per perdut.
        await zombie.execute(
            sa.update(RequirementExtractionRun)
            .where(RequirementExtractionRun.id == run.id)
            .values(status=AnalysisRunStatus.COMPLETED)
        )
        await zombie.commit()

    _, job, request = await reconciling
    assert job.status == WorkJobStatus.SUCCEEDED and job.outcome == JobOutcome.EXECUTED
    assert request.result_run_id == run.id
