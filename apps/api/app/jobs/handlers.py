"""El que fa cada tipus de feina. Nomes orquestra: la logica viu als serveis.

* `process_tender_documents` crida `process_tender_document` per a cada
  document, tal com el feia l'endpoint: descarrega, dedup, extraccio, QA i
  revisio de la licitacio hi son dins.
* `analyze_tender_requirements` crida `analyze_tender_requirements` amb les
  costures del worker: `work_job_id`, empremta confirmada, progres i guarda del
  lease. La cache, el fingerprint i el tancament del run son els del servei.

Un handler torna `JobCompletion`, o `None` si l'execucio s'ha interromput i no
s'ha de tancar (el worker ha perdut el lease, o el run ja l'ha reclamat la
reconciliacio). Cap handler no pren decisions humanes: no hi ha res de
workspaces, decisions ni aprovacions en aquest registre.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.profiles import PROFILES
from app.ai.provider import ProviderNotConfiguredError, StructuredLLMProvider
from app.core.config import settings
from app.core.errors import (
    AnalysisInProgressError,
    AnalysisInputChangedError,
    AnalysisInterruptedError,
    AnalysisRunReclaimedError,
    AnalysisTooLargeError,
    NoContentToExtractError,
    NothingAnalyzableError,
    ProcessingInProgressError,
)
from app.jobs.params import AnalyzeParams, ProcessParams
from app.jobs.queue import JobCompletion
from app.models.enums import AnalysisRunStatus, DownloadStatus, JobOutcome, WorkJobType
from app.models.tenders import Tender, TenderDocument
from app.services.requirement_analysis import analyze_tender_requirements
from app.services.requirement_chunking import DEFAULT_DOCUMENT_TYPES
from app.services.tender_document_processing import process_tender_document
from app.services.url_guard import Resolver

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """El que el handler necessita del job, copiat en reclamar-lo."""

    id: uuid.UUID
    job_type: WorkJobType
    tender_id: uuid.UUID
    parameters: dict[str, Any]
    input_fingerprint: str | None
    provider: str | None
    model: str | None


@dataclass(slots=True)
class JobContext:
    session_factory: async_sessionmaker[AsyncSession]
    job: JobSnapshot
    #: Fals si el worker ja no pot afirmar que el job es seu.
    should_continue: Callable[[], Awaitable[bool]]
    report_progress: Callable[[int, int], Awaitable[None]]
    provider_factory: Callable[[], StructuredLLMProvider]
    download_client: httpx.AsyncClient | None = None
    resolver: Resolver | None = None


Handler = Callable[[JobContext], Awaitable[JobCompletion | None]]


async def process_tender_documents(ctx: JobContext) -> JobCompletion | None:
    params = ProcessParams.model_validate(ctx.job.parameters)
    async with ctx.session_factory() as session:
        query = sa.select(TenderDocument).where(TenderDocument.tender_id == ctx.job.tender_id)
        if params.document_ids:
            query = query.where(TenderDocument.id.in_(params.document_ids))
        else:
            query = query.where(
                TenderDocument.document_type.in_([t.value for t in DEFAULT_DOCUMENT_TYPES])
            )
        # Nomes els ids: un rollback del servei (document ja en curs) expira els
        # objectes de la sessio, i cada document es torna a carregar quan toca.
        document_ids = list(
            (await session.scalars(query.with_only_columns(TenderDocument.id).order_by(
                TenderDocument.name
            ))).all()
        )
        if not document_ids:
            return JobCompletion.failed("no_documents", "No hi ha cap document per processar.")

        await ctx.report_progress(0, len(document_ids))
        items: list[dict[str, Any]] = []
        any_failed = False
        all_reused = True
        for position, document_id in enumerate(document_ids):
            if not await ctx.should_continue():
                return None
            document = await session.get(TenderDocument, document_id)
            if document is None:  # pragma: no cover - esborrat entremig
                continue
            try:
                outcome = await process_tender_document(
                    session,
                    document,
                    force_download=params.force_download,
                    force_reextract=params.force_reextract,
                    client=ctx.download_client,
                    resolver=ctx.resolver,
                )
            except (ProcessingInProgressError, NoContentToExtractError) as exc:
                await session.rollback()
                items.append(
                    {
                        "document_id": str(document_id),
                        "file_id": None,
                        "download_status": None,
                        "error_code": exc.code,
                        "reused": False,
                    }
                )
                any_failed = True
                all_reused = False
            else:
                file = outcome.file
                failed = file.download_status == DownloadStatus.FAILED
                items.append(
                    {
                        "document_id": str(document_id),
                        "file_id": str(file.id),
                        "download_status": str(file.download_status),
                        "error_code": file.error_code,
                        "reused": outcome.reused,
                    }
                )
                any_failed = any_failed or failed
                all_reused = all_reused and outcome.reused
            await ctx.report_progress(position + 1, len(document_ids))

    if any_failed:
        outcome_code = JobOutcome.PARTIAL
    elif all_reused:
        outcome_code = JobOutcome.REUSED
    else:
        outcome_code = JobOutcome.EXECUTED
    return JobCompletion.succeeded(outcome_code, result={"documents": items})


async def analyze_requirements(ctx: JobContext) -> JobCompletion | None:
    params = AnalyzeParams.model_validate(ctx.job.parameters)
    profile = PROFILES.get(params.profile)
    if profile is None:
        return JobCompletion.failed("unknown_profile")
    try:
        llm = ctx.provider_factory()
    except ProviderNotConfiguredError:
        return JobCompletion.failed("provider_not_configured")
    # La identitat confirmada inclou proveidor i model: si el worker en te uns
    # altres configurats, no s'executa una feina diferent de la demanada.
    if llm.name != ctx.job.provider or llm.model != ctx.job.model:
        return JobCompletion.failed(
            "provider_changed", "El worker no te configurat el proveidor i model demanats."
        )

    async with ctx.session_factory() as session:
        tender = await session.get(Tender, ctx.job.tender_id)
        if tender is None:
            return JobCompletion.failed("tender_not_found")
        try:
            outcome = await analyze_tender_requirements(
                session,
                tender,
                force=params.force,
                document_ids=params.document_ids or None,
                provider=llm,
                profile=profile,
                work_job_id=ctx.job.id,
                expected_fingerprint=ctx.job.input_fingerprint,
                on_progress=ctx.report_progress,
                should_continue=ctx.should_continue,
                chunk_limit=settings.ANALYSIS_MAX_CHUNKS_PER_RUN,
            )
        except AnalysisTooLargeError:
            return JobCompletion.failed(
                "analysis_too_large", "L'analisi supera el limit de chunks configurat."
            )
        except AnalysisInputChangedError:
            return JobCompletion.failed(
                "input_changed", "Els plecs han canviat des que es va demanar l'analisi."
            )
        except NothingAnalyzableError:
            return JobCompletion.failed("nothing_analyzable")
        except AnalysisInProgressError:
            return JobCompletion.failed("analysis_in_progress")
        except (AnalysisInterruptedError, AnalysisRunReclaimedError):
            return None

    run = outcome.run
    if outcome.reused:
        return JobCompletion.succeeded(JobOutcome.REUSED, result_run_id=run.id)
    if run.status == AnalysisRunStatus.COMPLETED:
        return JobCompletion.succeeded(JobOutcome.EXECUTED, result_run_id=run.id)
    if run.status == AnalysisRunStatus.PARTIAL:
        return JobCompletion.succeeded(JobOutcome.PARTIAL, result_run_id=run.id)
    return JobCompletion.failed(run.error_code or "run_failed", result_run_id=run.id)


#: Registre tancat. Afegir-hi un tipus es una decisio explicita.
HANDLERS: dict[WorkJobType, Handler] = {
    WorkJobType.PROCESS_TENDER_DOCUMENTS: process_tender_documents,
    WorkJobType.ANALYZE_TENDER_REQUIREMENTS: analyze_requirements,
}
