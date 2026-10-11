"""La cua sobre PostgreSQL: reclamar, lease, tancar, recollir i reconciliar.

Tres regles governen el modul:

1. **Reclamar es atomic.** `SELECT ... FOR UPDATE SKIP LOCKED` i un `UPDATE`
   condicional a la mateixa transaccio: dos workers no poden quedar-se el mateix
   job. L'index `uq_work_jobs_busy_tender` es la garantia final que mai no hi
   haura dos jobs actius sobre la mateixa licitacio.
2. **Tota escriptura d'un worker porta el seu `lease_token`** (fencing). Si el
   job ha canviat de mans, l'UPDATE no toca cap fila i el worker ho sap: no pot
   sobreescriure el que ha decidit un altre.
3. **Una analisi mai no es reexecuta sola.** Un lease vençut deixa el job
   `orphaned`, i la reconciliacio el tanca a partir de l'estat REAL del run
   (`RequirementExtractionRun.work_job_id`), no a cegues.

A SQLite (nomes tests) no hi ha `SKIP LOCKED`; la sentencia es la mateixa sense
bloqueig, i les garanties de concurrencia es proven contra PostgreSQL.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.ai.profiles import PROFILES
from app.core.config import settings
from app.models.enums import (
    AnalysisRunStatus,
    JobOutcome,
    JobRequestStatus,
    WorkJobStatus,
    WorkJobType,
)
from app.models.jobs import BUSY_WORK_JOB_STATUSES, JobRequest, WorkJob
from app.models.requirements import RequirementExtractionRun

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE = 500

_REQUEST_STATUS_FOR = {
    WorkJobStatus.SUCCEEDED: JobRequestStatus.SUCCEEDED,
    WorkJobStatus.FAILED: JobRequestStatus.FAILED,
    WorkJobStatus.CANCELLED: JobRequestStatus.CANCELLED,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite torna datetimes sense zona; PostgreSQL, amb. Es comparen en UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def is_postgres(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "postgresql"


@dataclass(frozen=True, slots=True)
class JobCompletion:
    """Com acaba una execucio. `None` en lloc d'aixo vol dir "interrompuda"."""

    status: WorkJobStatus
    outcome: JobOutcome | None = None
    error_code: str | None = None
    error_message: str | None = None
    result: dict[str, Any] | None = None
    #: Resultat per a les peticions: el run executat o el reaprofitat.
    result_run_id: uuid.UUID | None = None

    @classmethod
    def succeeded(
        cls,
        outcome: JobOutcome,
        *,
        result: dict[str, Any] | None = None,
        result_run_id: uuid.UUID | None = None,
    ) -> JobCompletion:
        return cls(
            WorkJobStatus.SUCCEEDED, outcome=outcome, result=result, result_run_id=result_run_id
        )

    @classmethod
    def failed(
        cls,
        error_code: str,
        error_message: str | None = None,
        *,
        result_run_id: uuid.UUID | None = None,
        result: dict[str, Any] | None = None,
    ) -> JobCompletion:
        return cls(
            WorkJobStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
            result_run_id=result_run_id,
            result=result,
        )


# --------------------------------------------------------------------------- #
# Reclamar i lease                                                              #
# --------------------------------------------------------------------------- #


async def claim_next(
    session: AsyncSession,
    worker_id: str,
    *,
    job_types: list[WorkJobType] | None = None,
    lease_seconds: int | None = None,
    now: datetime | None = None,
) -> WorkJob | None:
    """Reclama el job encuat mes antic que es pugui executar ara. Atomic."""
    now = now or utcnow()
    lease = timedelta(seconds=lease_seconds or settings.JOB_LEASE_SECONDS)
    busy = aliased(WorkJob)
    skipped: set[uuid.UUID] = set()

    for _ in range(5):
        query = (
            sa.select(WorkJob.id)
            .where(
                WorkJob.status == WorkJobStatus.QUEUED,
                sa.or_(WorkJob.run_after.is_(None), WorkJob.run_after <= now),
                ~sa.exists().where(
                    busy.tender_id == WorkJob.tender_id,
                    busy.status.in_(BUSY_WORK_JOB_STATUSES),
                ),
            )
            .order_by(WorkJob.created_at, WorkJob.id)
            .limit(1)
        )
        if job_types:
            query = query.where(WorkJob.job_type.in_(job_types))
        if skipped:
            query = query.where(WorkJob.id.not_in(skipped))
        if is_postgres(session):
            query = query.with_for_update(skip_locked=True, of=WorkJob)

        job_id = await session.scalar(query)
        if job_id is None:
            await session.rollback()
            return None

        try:
            claimed = await session.execute(
                sa.update(WorkJob)
                .where(WorkJob.id == job_id, WorkJob.status == WorkJobStatus.QUEUED)
                .values(
                    status=WorkJobStatus.RUNNING,
                    worker_id=worker_id[:120],
                    lease_token=uuid.uuid4(),
                    attempts=WorkJob.attempts + 1,
                    started_at=now,
                    heartbeat_at=now,
                    lease_expires_at=now + lease,
                    run_after=None,
                )
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                await session.rollback()
                skipped.add(job_id)
                continue
            await session.commit()
        except IntegrityError:
            # Un altre worker s'ha quedat un job de la mateixa licitacio.
            await session.rollback()
            skipped.add(job_id)
            continue

        job = await session.get(WorkJob, job_id, populate_existing=True)
        logger.info(
            "jobs.claimed",
            extra={"job_id": str(job_id), "job_type": str(job.job_type), "worker": worker_id},
        )
        return job
    return None


async def renew_lease(
    session: AsyncSession,
    job_id: uuid.UUID,
    token: uuid.UUID,
    *,
    lease_seconds: int | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """Renova el lease si el worker encara n'es el propietari.

    Torna la nova expiracio, o `None` si la base de dades diu que el job ja no
    es seu (token diferent o estat canviat): **perdua definitiva**. Un error de
    connexio es propaga: no se sap, i qui crida ho ha de tractar com a transitori.
    """
    now = now or utcnow()
    expires = now + timedelta(seconds=lease_seconds or settings.JOB_LEASE_SECONDS)
    renewed = await session.execute(
        sa.update(WorkJob)
        .where(
            WorkJob.id == job_id,
            WorkJob.lease_token == token,
            WorkJob.status == WorkJobStatus.RUNNING,
        )
        .values(heartbeat_at=now, lease_expires_at=expires)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return expires if renewed.rowcount == 1 else None


async def update_progress(
    session: AsyncSession,
    job_id: uuid.UUID,
    token: uuid.UUID,
    current: int,
    total: int,
    *,
    now: datetime | None = None,
) -> bool:
    """Desa el progres. Fals si el worker ja no es el propietari."""
    updated = await session.execute(
        sa.update(WorkJob)
        .where(
            WorkJob.id == job_id,
            WorkJob.lease_token == token,
            WorkJob.status == WorkJobStatus.RUNNING,
        )
        .values(progress_current=current, progress_total=total, heartbeat_at=now or utcnow())
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return updated.rowcount == 1


async def _propagate(
    session: AsyncSession,
    work_job_id: uuid.UUID,
    status: WorkJobStatus,
    *,
    outcome: JobOutcome | None,
    error_code: str | None,
    result_run_id: uuid.UUID | None,
    now: datetime,
) -> None:
    """Totes les peticions pendents d'una execucio acaben amb ella."""
    await session.execute(
        sa.update(JobRequest)
        .where(
            JobRequest.work_job_id == work_job_id,
            JobRequest.status == JobRequestStatus.PENDING,
        )
        .values(
            status=_REQUEST_STATUS_FOR[status],
            outcome=outcome,
            error_code=error_code,
            result_run_id=result_run_id,
            finished_at=now,
        )
        .execution_options(synchronize_session=False)
    )


async def complete(
    session: AsyncSession,
    job_id: uuid.UUID,
    token: uuid.UUID,
    completion: JobCompletion,
    *,
    now: datetime | None = None,
) -> bool:
    """Tanca el job i les seves peticions en una sola transaccio. Amb fencing."""
    now = now or utcnow()
    closed = await session.execute(
        sa.update(WorkJob)
        .where(
            WorkJob.id == job_id,
            WorkJob.lease_token == token,
            WorkJob.status == WorkJobStatus.RUNNING,
        )
        .values(
            status=completion.status,
            outcome=completion.outcome,
            error_code=completion.error_code,
            error_message=(completion.error_message or None)
            and completion.error_message[:_MAX_ERROR_MESSAGE],
            result=completion.result,
            finished_at=now,
            lease_token=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if closed.rowcount != 1:
        await session.rollback()
        return False
    await _propagate(
        session,
        job_id,
        completion.status,
        outcome=completion.outcome,
        error_code=completion.error_code,
        result_run_id=completion.result_run_id,
        now=now,
    )
    await session.commit()
    return True


async def fail_owned_run(
    session: AsyncSession, job_id: uuid.UUID, token: uuid.UUID, error_code: str
) -> bool:
    """Marca com a fallat el run d'un job que el worker encara posseeix.

    Nomes per a una excepcio inesperada del mateix worker: si el lease ja no es
    seu, no toca res, i la decisio es del reconciliador.
    """
    query = sa.select(WorkJob.id).where(
        WorkJob.id == job_id,
        WorkJob.lease_token == token,
        WorkJob.status == WorkJobStatus.RUNNING,
    )
    if is_postgres(session):
        query = query.with_for_update()
    if await session.scalar(query) is None:
        await session.rollback()
        return False
    await session.execute(
        sa.update(RequirementExtractionRun)
        .where(
            RequirementExtractionRun.work_job_id == job_id,
            RequirementExtractionRun.status.in_(
                [AnalysisRunStatus.PENDING, AnalysisRunStatus.RUNNING]
            ),
        )
        .values(
            status=AnalysisRunStatus.FAILED,
            error_code=error_code,
            error_message="L'execucio s'ha interromput per un error intern del worker.",
            completed_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return True


# --------------------------------------------------------------------------- #
# Jobs abandonats                                                               #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class MaintenanceReport:
    requeued: list[uuid.UUID] = field(default_factory=list)
    orphaned: list[uuid.UUID] = field(default_factory=list)
    failed: list[uuid.UUID] = field(default_factory=list)
    reconciled: dict[uuid.UUID, str] = field(default_factory=dict)


def _finish(job: WorkJob, completion: JobCompletion, now: datetime) -> None:
    job.status = completion.status
    job.outcome = completion.outcome
    job.error_code = completion.error_code
    job.error_message = (completion.error_message or None) and completion.error_message[
        :_MAX_ERROR_MESSAGE
    ]
    if completion.result is not None:
        job.result = completion.result
    job.finished_at = now
    job.lease_token = None


async def reap_expired(
    session: AsyncSession, *, now: datetime | None = None
) -> MaintenanceReport:
    """Recull els jobs `running` amb el lease vençut.

    * Documents (idempotents i sense cost de proveidor): tornen a la cua, amb
      `run_after` perque el servei hagi alliberat el document que s'estava
      baixant, fins a `max_attempts`.
    * Analisi: passa a `orphaned`. **No es reexecuta ni es toca el run**: ho
      decidira la reconciliacio segons el que hagi passat de veritat.
    """
    now = now or utcnow()
    report = MaintenanceReport()
    query = sa.select(WorkJob).where(
        WorkJob.status == WorkJobStatus.RUNNING,
        WorkJob.lease_expires_at < now,
    )
    if is_postgres(session):
        query = query.with_for_update(skip_locked=True)
    jobs = (await session.scalars(query)).all()

    for job in jobs:
        if job.job_type == WorkJobType.ANALYZE_TENDER_REQUIREMENTS:
            job.status = WorkJobStatus.ORPHANED
            job.lease_token = None
            report.orphaned.append(job.id)
        elif job.attempts < job.max_attempts:
            job.status = WorkJobStatus.QUEUED
            job.lease_token = None
            job.worker_id = None
            job.lease_expires_at = None
            job.run_after = now + timedelta(seconds=settings.DOCUMENT_PROCESSING_STALE_SECONDS)
            report.requeued.append(job.id)
        else:
            completion = JobCompletion.failed(
                "lease_expired", "El worker ha deixat de respondre massa vegades."
            )
            _finish(job, completion, now)
            await session.flush()
            await _propagate(
                session,
                job.id,
                completion.status,
                outcome=None,
                error_code=completion.error_code,
                result_run_id=None,
                now=now,
            )
            report.failed.append(job.id)
    await session.commit()
    if jobs:
        logger.warning(
            "jobs.reaped",
            extra={
                "requeued": len(report.requeued),
                "orphaned": len(report.orphaned),
                "failed": len(report.failed),
            },
        )
    return report


async def _reconcile_one(
    session: AsyncSession, job: WorkJob, *, now: datetime, grace: timedelta
) -> str:
    """Decideix que ha passat amb un job d'analisi orfe, a partir del seu run."""
    # Import diferit: el servei d'analisi importa aquest paquet de models.
    from app.services.requirement_analysis import find_reusable_run

    last_alive = _as_aware(job.lease_expires_at or job.heartbeat_at or job.updated_at)
    grace_passed = last_alive is None or now - last_alive >= grace

    for _ in range(2):
        run = await session.scalar(
            sa.select(RequirementExtractionRun)
            .where(RequirementExtractionRun.work_job_id == job.id)
            .execution_options(populate_existing=True)
        )

        if run is not None and run.status in (
            AnalysisRunStatus.COMPLETED,
            AnalysisRunStatus.COMPLETED_EMPTY,
        ):
            completion = JobCompletion.succeeded(JobOutcome.EXECUTED, result_run_id=run.id)
        elif run is not None and run.status == AnalysisRunStatus.PARTIAL:
            completion = JobCompletion.succeeded(JobOutcome.PARTIAL, result_run_id=run.id)
        elif run is not None and run.status == AnalysisRunStatus.FAILED:
            completion = JobCompletion.failed(run.error_code or "run_failed", result_run_id=run.id)
        elif run is not None:
            # En curs. El worker pot seguir viu i acabar-lo: s'espera.
            if not grace_passed:
                return "waiting"
            lost = await session.execute(
                sa.update(RequirementExtractionRun)
                .where(
                    RequirementExtractionRun.id == run.id,
                    RequirementExtractionRun.status.in_(
                        [AnalysisRunStatus.PENDING, AnalysisRunStatus.RUNNING]
                    ),
                )
                .values(
                    status=AnalysisRunStatus.FAILED,
                    error_code="worker_lost",
                    error_message="El worker va deixar de respondre i no el va tancar.",
                    completed_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if lost.rowcount != 1:
                # Algu l'ha tancat just ara: es torna a llegir.
                continue
            completion = JobCompletion.failed("worker_lost", result_run_id=run.id)
        else:
            if not grace_passed:
                return "waiting"
            params = job.parameters or {}
            profile = PROFILES.get(str(params.get("profile", "")))
            reusable = None
            if profile is not None and job.input_fingerprint and job.provider and job.model:
                reusable = await find_reusable_run(
                    session,
                    job.tender_id,
                    job.input_fingerprint,
                    SimpleNamespace(name=job.provider, model=job.model),
                    profile,
                )
            if reusable is not None:
                completion = JobCompletion.succeeded(JobOutcome.REUSED, result_run_id=reusable.id)
            else:
                completion = JobCompletion.failed("worker_lost_before_start")

        _finish(job, completion, now)
        await session.flush()
        await _propagate(
            session,
            job.id,
            completion.status,
            outcome=completion.outcome,
            error_code=completion.error_code,
            result_run_id=completion.result_run_id,
            now=now,
        )
        return f"{completion.status}:{completion.outcome or completion.error_code}"
    return "waiting"


async def reconcile_orphaned(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    grace_seconds: int | None = None,
    job_id: uuid.UUID | None = None,
) -> MaintenanceReport:
    """Tanca els jobs d'analisi orfes segons l'estat real del seu run.

    | run                   | dins la gracia | passada la gracia                     |
    | --------------------- | -------------- | ------------------------------------- |
    | completed / partial   | succeeded      | succeeded                             |
    | failed                | failed         | failed                                |
    | running               | espera         | run `worker_lost` (condicional) + job |
    | cap                   | espera         | reaprofitat si n'hi ha, si no failed  |
    """
    now = now or utcnow()
    grace = timedelta(
        seconds=settings.JOB_ORPHAN_GRACE_SECONDS if grace_seconds is None else grace_seconds
    )
    report = MaintenanceReport()
    query = sa.select(WorkJob).where(WorkJob.status == WorkJobStatus.ORPHANED)
    if job_id is not None:
        query = query.where(WorkJob.id == job_id)
    if is_postgres(session):
        query = query.with_for_update(skip_locked=True)
    for job in (await session.scalars(query)).all():
        report.reconciled[job.id] = await _reconcile_one(session, job, now=now, grace=grace)
    await session.commit()
    return report


async def run_maintenance(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    grace_seconds: int | None = None,
) -> MaintenanceReport:
    now = now or utcnow()
    reaped = await reap_expired(session, now=now)
    reconciled = await reconcile_orphaned(session, now=now, grace_seconds=grace_seconds)
    reaped.reconciled = reconciled.reconciled
    return reaped
