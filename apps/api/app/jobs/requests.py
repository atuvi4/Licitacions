"""Peticions dels tenants: crear-les, vincular-les a una execucio i cancel·lar-les.

Cap funcio d'aqui no executa la feina llarga: com a molt calcula l'empremta dels
plecs (lectura) i escriu dues files. Es el que fa una peticio HTTP.

**Deduplicacio global, resposta tenant-safe.** Dues empreses que demanen la
mateixa analisi comparteixen un `WorkJob`, pero cadascuna te la seva
`JobRequest`, i totes dues reben la mateixa resposta (`accepted`). Res del que
torna aquest modul cap a l'API no diu si la feina l'havia creada una altra
empresa: ni l'id de l'execucio, ni `linked_existing`, ni el progres acumulat
abans de vincular-s'hi.

**Flux concurrent (A i B alhora, PostgreSQL READ COMMITTED).** A insereix el
`WorkJob` amb `ON CONFLICT DO NOTHING`; l'insert de B xoca amb la fila encara no
confirmada d'A i **espera**. Quan A confirma, B no insereix res, llegeix el job
actiu per la seva clau, el bloqueja en mode compartit (`FOR SHARE`, perque un
tancament simultani no el deixi amb una peticio penjada) i hi vincula la seva
peticio. Si el job ha acabat entremig, B torna a comencar i, normalment, troba
el resultat per reaprofitar.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.profiles import DEFAULT_PROFILE, ExtractorProfile
from app.ai.provider import StructuredLLMProvider, get_provider
from app.core.config import settings
from app.core.errors import (
    AnalysisInputChangedError,
    JobRequestNotPendingError,
    NotFoundError,
    NothingAnalyzableError,
    OperationBusyError,
)
from app.jobs.params import (
    AnalyzeParams,
    ProcessParams,
    analysis_dedupe_key,
    process_dedupe_key,
)
from app.jobs.queue import is_postgres, utcnow
from app.models.enums import (
    AnalysisRunStatus,
    JobOutcome,
    JobRequestStatus,
    WorkJobStatus,
    WorkJobType,
)
from app.models.jobs import ACTIVE_WORK_JOB_STATUSES, JobRequest, WorkJob
from app.models.requirements import RequirementExtractionRun
from app.models.tenders import Tender, TenderDocument
from app.services.requirement_analysis import analysis_input, find_reusable_run

logger = logging.getLogger(__name__)

_MAX_LINK_ATTEMPTS = 3

#: S'executa just abans de confirmar la transaccio de la peticio, amb la peticio
#: ja escrita. Serveix per fer dins la mateixa transaccio el que ha de ser atomic
#: amb ella (marcar el preview com a confirmat, comprovar el limit d'actives).
BeforeCommit = Callable[[AsyncSession, JobRequest], Awaitable[None]]
_ACTIVE_SQL = sa.text("status IN ('queued', 'running', 'orphaned')")


@dataclass(frozen=True, slots=True)
class Submission:
    request: JobRequest
    #: La mateixa empresa ja tenia aquesta peticio pendent.
    already_requested: bool = False


async def _load_tender(session: AsyncSession, tender_id: uuid.UUID) -> Tender:
    tender = await session.get(Tender, tender_id)
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


async def _insert_or_get_job(
    session: AsyncSession, values: dict[str, Any]
) -> tuple[uuid.UUID | None, bool]:
    """Crea l'execucio activa per a la clau, o torna la que ja hi ha.

    `(None, False)` vol dir que no n'hi ha cap d'activa i tampoc no s'ha pogut
    crear: qui crida ho torna a intentar.
    """
    insert = postgresql.insert if is_postgres(session) else sqlite.insert
    statement = (
        insert(WorkJob)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[WorkJob.dedupe_key], index_where=_ACTIVE_SQL)
        .returning(WorkJob.id)
    )
    created = await session.scalar(statement)
    if created is not None:
        return created, True
    existing = await session.scalar(
        sa.select(WorkJob.id).where(
            WorkJob.dedupe_key == values["dedupe_key"],
            WorkJob.status.in_(ACTIVE_WORK_JOB_STATUSES),
        )
    )
    return existing, False


async def _active_job(session: AsyncSession, dedupe_key: str) -> uuid.UUID | None:
    return await session.scalar(
        sa.select(WorkJob.id).where(
            WorkJob.dedupe_key == dedupe_key,
            WorkJob.status.in_(ACTIVE_WORK_JOB_STATUSES),
        )
    )


async def _link(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    created: bool,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
    tender_id: uuid.UUID,
    job_type: WorkJobType,
    parameters: dict[str, Any],
    analysis_quote_id: uuid.UUID | None = None,
) -> Submission | None:
    """Vincula la peticio a l'execucio. `None` si l'execucio ja no es activa."""
    query = sa.select(WorkJob.status, WorkJob.progress_current).where(WorkJob.id == job_id)
    if is_postgres(session):
        # Compartit: un tancament (UPDATE) espera que aquesta peticio existeixi,
        # i per tant la propaga. Sense aixo podria quedar penjada en `pending`.
        query = query.with_for_update(read=True)
    row = (await session.execute(query)).one_or_none()
    if row is None or row.status not in ACTIVE_WORK_JOB_STATUSES:
        return None

    existing = await session.scalar(
        sa.select(JobRequest).where(
            JobRequest.company_id == company_id,
            JobRequest.work_job_id == job_id,
            JobRequest.status == JobRequestStatus.PENDING,
        )
    )
    if existing is not None:
        return Submission(existing, already_requested=True)

    request = JobRequest(
        company_id=company_id,
        user_id=user_id,
        tender_id=tender_id,
        job_type=job_type,
        parameters=parameters,
        work_job_id=job_id,
        status=JobRequestStatus.PENDING,
        requested_at=utcnow(),
        reused_existing=False,
        linked_existing=not created,
        progress_baseline=0 if created else (row.progress_current or 0),
        analysis_quote_id=analysis_quote_id,
    )
    session.add(request)
    try:
        await session.flush()
    except IntegrityError:
        # Doble clic simultani de la mateixa empresa: la volta seguent trobara
        # la peticio pendent de l'altre i la tornara com a `already_requested`.
        await session.rollback()
        return None
    return Submission(request)


async def _commit_or_retry(session: AsyncSession) -> bool:
    try:
        await session.commit()
    except IntegrityError:
        # Doble clic simultani de la mateixa empresa: la seguent volta trobara
        # la peticio de l'altre i la tornara com a `already_requested`.
        await session.rollback()
        return False
    return True


async def submit_analysis_request(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
    tender_id: uuid.UUID,
    force: bool = False,
    document_ids: list[uuid.UUID] | None = None,
    profile: ExtractorProfile = DEFAULT_PROFILE,
    provider_factory: Callable[[], StructuredLLMProvider] = get_provider,
    expected_fingerprint: str | None = None,
    analysis_quote_id: uuid.UUID | None = None,
    before_commit: BeforeCommit | None = None,
) -> Submission:
    """Demana l'analisi d'una licitacio. No crida el proveidor: nomes el construeix
    per saber-ne el nom i el model, que formen part de la identitat de la feina.

    Des dels previews (7B), la demana `app/jobs/quotes.py` amb:

    * `expected_fingerprint`: el del preview confirmat. Si els plecs han canviat
      entre la comprovacio i la insercio, `AnalysisInputChangedError`.
    * `analysis_quote_id`: queda desat a la peticio.
    * `before_commit`: el que ha de passar a la mateixa transaccio.
    """
    await _load_tender(session, tender_id)
    ids = sorted(set(document_ids or []), key=str)

    # Un run llancat fora de la cua (script d'operador) ocupa la licitacio. No
    # depen de cap tenant, i per aixo es pot dir obertament.
    operator_run = await session.scalar(
        sa.select(RequirementExtractionRun.id).where(
            RequirementExtractionRun.tender_id == tender_id,
            RequirementExtractionRun.status.in_(
                [AnalysisRunStatus.PENDING, AnalysisRunStatus.RUNNING]
            ),
            RequirementExtractionRun.work_job_id.is_(None),
        )
    )
    if operator_run is not None:
        raise OperationBusyError(
            "Aquesta licitacio s'esta analitzant fora de la cua. Torna-ho a provar d'aqui a una "
            "estona."
        )

    llm = provider_factory()
    params = AnalyzeParams(profile=profile.name, force=force, document_ids=ids)
    parameters = params.model_dump(mode="json")

    for _ in range(_MAX_LINK_ATTEMPTS):
        current = await analysis_input(session, tender_id, document_ids=ids or None)
        if not current.documents:
            raise NothingAnalyzableError(
                "Cap document d'aquesta licitacio te text extret per analitzar. "
                "Processa'n els plecs primer."
            )
        if expected_fingerprint is not None and current.fingerprint != expected_fingerprint:
            raise AnalysisInputChangedError(
                "Els plecs han canviat des del preview. Cal tornar-lo a veure."
            )

        if not force:
            reusable = await find_reusable_run(
                session, tender_id, current.fingerprint, llm, profile
            )
            if reusable is not None:
                now = utcnow()
                request = JobRequest(
                    company_id=company_id,
                    user_id=user_id,
                    tender_id=tender_id,
                    job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
                    parameters=parameters,
                    work_job_id=None,
                    status=JobRequestStatus.SUCCEEDED,
                    outcome=JobOutcome.REUSED_EXISTING,
                    reused_existing=True,
                    linked_existing=False,
                    progress_baseline=0,
                    result_run_id=reusable.id,
                    requested_at=now,
                    finished_at=now,
                    analysis_quote_id=analysis_quote_id,
                )
                session.add(request)
                await session.flush()
                if before_commit is not None:
                    await before_commit(session, request)
                await session.commit()
                return Submission(request)

        identity = {
            "tender_id": tender_id,
            "fingerprint": current.fingerprint,
            "profile": profile.name,
            "provider": llm.name,
            "model": llm.model,
            "document_ids": ids,
        }
        force_key = analysis_dedupe_key(**identity, force=True)
        job_id: uuid.UUID | None = None
        created = False
        if not force:
            # Una peticio normal pot aprofitar una execucio `force` compatible.
            job_id = await _active_job(session, force_key)
        if job_id is None:
            job_id, created = await _insert_or_get_job(
                session,
                {
                    "id": uuid.uuid4(),
                    "job_type": WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
                    "status": WorkJobStatus.QUEUED,
                    "tender_id": tender_id,
                    "parameters": parameters,
                    "dedupe_key": force_key
                    if force
                    else analysis_dedupe_key(**identity, force=False),
                    "input_fingerprint": current.fingerprint,
                    "provider": llm.name,
                    "model": llm.model,
                    "attempts": 0,
                    "max_attempts": 1,
                },
            )
        if job_id is None:
            await session.rollback()
            continue

        submission = await _link(
            session,
            job_id=job_id,
            created=created,
            company_id=company_id,
            user_id=user_id,
            tender_id=tender_id,
            job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
            parameters=parameters,
            analysis_quote_id=analysis_quote_id,
        )
        if submission is None:
            await session.rollback()
            continue
        if before_commit is not None:
            await before_commit(session, submission.request)
        if not await _commit_or_retry(session):
            continue
        return submission

    raise OperationBusyError("No s'ha pogut registrar la peticio ara mateix. Torna-ho a provar.")


async def submit_process_request(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
    tender_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None = None,
    force_download: bool = False,
    force_reextract: bool = False,
) -> Submission:
    await _load_tender(session, tender_id)
    ids = sorted(set(document_ids or []), key=str)
    if ids:
        found = set(
            (
                await session.scalars(
                    sa.select(TenderDocument.id).where(
                        TenderDocument.tender_id == tender_id, TenderDocument.id.in_(ids)
                    )
                )
            ).all()
        )
        if found != set(ids):
            raise NotFoundError("Document no trobat.")

    params = ProcessParams(
        document_ids=ids, force_download=force_download, force_reextract=force_reextract
    )
    parameters = params.model_dump(mode="json")
    dedupe_key = process_dedupe_key(tender_id=tender_id, params=params)

    for _ in range(_MAX_LINK_ATTEMPTS):
        job_id, created = await _insert_or_get_job(
            session,
            {
                "id": uuid.uuid4(),
                "job_type": WorkJobType.PROCESS_TENDER_DOCUMENTS,
                "status": WorkJobStatus.QUEUED,
                "tender_id": tender_id,
                "parameters": parameters,
                "dedupe_key": dedupe_key,
                "attempts": 0,
                "max_attempts": settings.JOB_DOCUMENT_MAX_ATTEMPTS,
            },
        )
        if job_id is None:
            await session.rollback()
            continue
        submission = await _link(
            session,
            job_id=job_id,
            created=created,
            company_id=company_id,
            user_id=user_id,
            tender_id=tender_id,
            job_type=WorkJobType.PROCESS_TENDER_DOCUMENTS,
            parameters=parameters,
        )
        if submission is None:
            await session.rollback()
            continue
        if not await _commit_or_retry(session):
            continue
        return submission

    raise OperationBusyError("No s'ha pogut registrar la peticio ara mateix. Torna-ho a provar.")


async def get_request(
    session: AsyncSession, *, company_id: uuid.UUID, request_id: uuid.UUID
) -> JobRequest:
    """La peticio, nomes si es de l'empresa. Si no, 404 (no confirmem que existeixi)."""
    request = await session.scalar(
        sa.select(JobRequest).where(
            JobRequest.id == request_id, JobRequest.company_id == company_id
        )
    )
    if request is None:
        raise NotFoundError("Peticio no trobada.")
    return request


async def cancel_request(
    session: AsyncSession, *, company_id: uuid.UUID, request_id: uuid.UUID
) -> JobRequest:
    """Cancel·la la peticio. L'execucio nomes si encara no ha comencat i ja no
    la necessita ningu mes. La resposta es la mateixa en tots dos casos."""
    request = await get_request(session, company_id=company_id, request_id=request_id)
    if request.status != JobRequestStatus.PENDING:
        raise JobRequestNotPendingError("Aquesta peticio ja no esta pendent.")

    job = None
    if request.work_job_id is not None:
        query = sa.select(WorkJob).where(WorkJob.id == request.work_job_id)
        if is_postgres(session):
            query = query.with_for_update()
        job = await session.scalar(query)

    now = utcnow()
    request.status = JobRequestStatus.CANCELLED
    request.finished_at = now
    await session.flush()

    if job is not None and job.status == WorkJobStatus.QUEUED:
        remaining = await session.scalar(
            sa.select(sa.func.count(JobRequest.id)).where(
                JobRequest.work_job_id == job.id,
                JobRequest.status == JobRequestStatus.PENDING,
            )
        )
        if not remaining:
            job.status = WorkJobStatus.CANCELLED
            job.finished_at = now
    await session.commit()
    await session.refresh(request)
    return request


# --------------------------------------------------------------------------- #
# Vista del tenant                                                              #
# --------------------------------------------------------------------------- #

#: Com veu el tenant l'estat de la seva peticio. `queued`, `running` i `orphaned`
#: no es distingeixen: dir "ja s'esta executant" a qui acaba de demanar-ho
#: revelaria que algu altre ho havia demanat abans.
_TENANT_STATUS = {
    JobRequestStatus.PENDING: "accepted",
    JobRequestStatus.SUCCEEDED: "succeeded",
    JobRequestStatus.FAILED: "failed",
    JobRequestStatus.CANCELLED: "cancelled",
}


def tenant_view(request: JobRequest, job: WorkJob | None) -> dict[str, Any]:
    """El que pot veure l'empresa que ha fet la peticio, i res mes."""
    progress = None
    if (
        request.status == JobRequestStatus.PENDING
        and job is not None
        and job.status == WorkJobStatus.RUNNING
        and job.progress_total
    ):
        baseline = request.progress_baseline or 0
        done = max(0, (job.progress_current or 0) - baseline)
        total = max(0, job.progress_total - baseline)
        # Nomes quan hi ha avanc des de la peticio: un 3/8 inicial delataria
        # feina comencada abans.
        if done > 0 and total > 0:
            progress = {"done": min(done, total), "total": total}
    return {
        "id": request.id,
        "job_type": request.job_type,
        "tender_id": request.tender_id,
        "status": _TENANT_STATUS[request.status],
        "outcome": request.outcome,
        "reused_existing": request.reused_existing,
        "result_run_id": request.result_run_id,
        "error_code": request.error_code,
        "requested_at": request.requested_at,
        "finished_at": request.finished_at,
        "progress": progress,
    }


async def load_view(session: AsyncSession, request: JobRequest) -> dict[str, Any]:
    job = await session.get(WorkJob, request.work_job_id) if request.work_job_id else None
    return tenant_view(request, job)


async def list_requests(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    job_type: WorkJobType | None = None,
    active_only: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    query = (
        sa.select(JobRequest, WorkJob)
        .outerjoin(WorkJob, WorkJob.id == JobRequest.work_job_id)
        .where(JobRequest.company_id == company_id, JobRequest.tender_id == tender_id)
        .order_by(JobRequest.created_at.desc())
        .limit(limit)
    )
    if job_type is not None:
        query = query.where(JobRequest.job_type == job_type)
    if active_only:
        query = query.where(JobRequest.status == JobRequestStatus.PENDING)
    rows = (await session.execute(query)).all()
    return [tenant_view(request, job) for request, job in rows]
