"""El worker: un proces independent que buida la cua de `work_jobs`.

    python -m app.jobs worker            # bucle
    python -m app.jobs worker --once     # un sol job (o res) i surt

Cada volta: recollir abandonats i reconciliar orfes, reclamar un job, executar-lo
amb la guarda de lease, i tancar-lo amb fencing.

**Propietat del lease.** Nomes es pot afirmar amb la base de dades:

* renovacio acceptada        -> es seu fins a la nova expiracio;
* renovacio rebutjada        -> **perdua definitiva** (un altre l'ha reclamat o el
  reaper l'ha deixat orfe): no s'inicia res mes;
* error de connexio          -> **transitori**: es continua nomes mentre el lease
  que SABEM renovat no hagi vençut. Passat aquest moment no es pot verificar, i no
  s'inventa: s'atura igual, sense tancar res.

En cap dels dos casos d'aturada no es tanca el job ni el run. El reaper el
deixara orfe i la reconciliacio decidira. No hi ha reexecucio automatica.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.provider import StructuredLLMProvider, get_provider
from app.core.config import settings
from app.jobs import queue
from app.jobs.handlers import HANDLERS, JobContext, JobSnapshot
from app.jobs.queue import JobCompletion, utcnow
from app.models.enums import WorkJobType
from app.models.jobs import WorkJob
from app.services.url_guard import Resolver

logger = logging.getLogger(__name__)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class LeaseGuard:
    """Sap si el worker encara pot afirmar que el job es seu."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        job_id: uuid.UUID,
        token: uuid.UUID,
        expires_at: datetime,
        lease_seconds: int,
        heartbeat_seconds: float,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self.job_id = job_id
        self.token = token
        self._valid_until = expires_at
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._clock = clock
        #: La base de dades ha dit que el job ja no es seu.
        self.lost = False
        self._task: asyncio.Task | None = None

    async def verify(self) -> bool:
        """Renova el lease i diu si es pot continuar. Es crida abans de cada unitat."""
        if self.lost:
            return False
        try:
            async with self._session_factory() as session:
                expires = await queue.renew_lease(
                    session, self.job_id, self.token, lease_seconds=self._lease_seconds
                )
        except Exception:  # noqa: BLE001 - qualsevol error de connexio es transitori
            still_valid = self._clock() < self._valid_until
            logger.warning(
                "jobs.lease.unverifiable",
                extra={"job_id": str(self.job_id), "still_valid": still_valid},
            )
            return still_valid
        if expires is None:
            self.lost = True
            logger.warning("jobs.lease.lost", extra={"job_id": str(self.job_id)})
            return False
        self._valid_until = expires
        return True

    def mark_lost(self) -> None:
        self.lost = True

    async def _beat(self) -> None:
        while not self.lost:
            await asyncio.sleep(self._heartbeat_seconds)
            await self.verify()

    async def __aenter__(self) -> LeaseGuard:
        self._task = asyncio.create_task(self._beat())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


class Worker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        worker_id: str | None = None,
        job_types: list[WorkJobType] | None = None,
        provider_factory: Callable[[], StructuredLLMProvider] = get_provider,
        download_client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        lease_seconds: int | None = None,
        heartbeat_seconds: float | None = None,
        poll_seconds: float | None = None,
        orphan_grace_seconds: int | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id or default_worker_id()
        self.job_types = job_types
        self.provider_factory = provider_factory
        self.download_client = download_client
        self.resolver = resolver
        self.lease_seconds = lease_seconds or settings.JOB_LEASE_SECONDS
        self.heartbeat_seconds = heartbeat_seconds or settings.JOB_HEARTBEAT_SECONDS
        self.poll_seconds = poll_seconds or settings.JOB_POLL_SECONDS
        self.orphan_grace_seconds = orphan_grace_seconds

    async def maintenance(self) -> queue.MaintenanceReport:
        async with self.session_factory() as session:
            return await queue.run_maintenance(session, grace_seconds=self.orphan_grace_seconds)

    async def run_once(self) -> WorkJob | None:
        """Manteniment, i un job si n'hi ha cap. Torna el job reclamat."""
        await self.maintenance()
        async with self.session_factory() as session:
            job = await queue.claim_next(
                session,
                self.worker_id,
                job_types=self.job_types,
                lease_seconds=self.lease_seconds,
            )
        if job is None:
            return None
        await self.execute(job)
        return job

    async def execute(self, job: WorkJob) -> None:
        snapshot = JobSnapshot(
            id=job.id,
            job_type=job.job_type,
            tender_id=job.tender_id,
            parameters=dict(job.parameters or {}),
            input_fingerprint=job.input_fingerprint,
            provider=job.provider,
            model=job.model,
        )
        token = job.lease_token
        expires = queue._as_aware(job.lease_expires_at) or utcnow() + timedelta(
            seconds=self.lease_seconds
        )
        guard = LeaseGuard(
            self.session_factory,
            job_id=job.id,
            token=token,
            expires_at=expires,
            lease_seconds=self.lease_seconds,
            heartbeat_seconds=self.heartbeat_seconds,
        )

        async def report_progress(current: int, total: int) -> None:
            try:
                async with self.session_factory() as session:
                    owned = await queue.update_progress(session, job.id, token, current, total)
            except Exception:  # noqa: BLE001 - el progres no ha d'aturar la feina
                logger.warning("jobs.progress.unsaved", extra={"job_id": str(job.id)})
                return
            if not owned:
                guard.mark_lost()

        ctx = JobContext(
            session_factory=self.session_factory,
            job=snapshot,
            should_continue=guard.verify,
            report_progress=report_progress,
            provider_factory=self.provider_factory,
            download_client=self.download_client,
            resolver=self.resolver,
        )
        handler = HANDLERS[job.job_type]

        completion: JobCompletion | None
        async with guard:
            try:
                completion = await handler(ctx)
            except Exception as exc:  # noqa: BLE001 - qualsevol fallada ha de quedar escrita
                logger.exception("jobs.handler.crashed", extra={"job_id": str(job.id)})
                completion = JobCompletion.failed("internal_error", type(exc).__name__)
                if job.job_type == WorkJobType.ANALYZE_TENDER_REQUIREMENTS and not guard.lost:
                    async with self.session_factory() as session:
                        await queue.fail_owned_run(session, job.id, token, "internal_error")

        if completion is None:
            logger.warning("jobs.interrupted", extra={"job_id": str(job.id)})
            return

        async with self.session_factory() as session:
            closed = await queue.complete(session, job.id, token, completion)
        if closed:
            logger.info(
                "jobs.finished",
                extra={
                    "job_id": str(job.id),
                    "status": str(completion.status),
                    "outcome": str(completion.outcome),
                    "error_code": completion.error_code,
                },
            )
            return

        # El lease s'ha perdut mentre s'acabava. Una analisi s'ha pogut tancar
        # igualment al servei: es reconcilia amb el que hi ha de veritat.
        logger.warning("jobs.completion.lost_lease", extra={"job_id": str(job.id)})
        if job.job_type == WorkJobType.ANALYZE_TENDER_REQUIREMENTS:
            async with self.session_factory() as session:
                await queue.reconcile_orphaned(
                    session, job_id=job.id, grace_seconds=self.orphan_grace_seconds
                )

    async def run_forever(self, stop: asyncio.Event) -> None:
        logger.info("jobs.worker.started", extra={"worker": self.worker_id})
        while not stop.is_set():
            job = await self.run_once()
            if job is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
        logger.info("jobs.worker.stopped", extra={"worker": self.worker_id})
