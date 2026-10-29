"""El que fa cada tipus de feina. Nomes orquestra: la logica viu als serveis.

* `process_tender_documents` crida `process_tender_document` per a cada
  document, tal com el feia l'endpoint: descarrega, dedup, extraccio, QA i
  revisio de la licitacio hi son dins.
* `analyze_tender_requirements` crida `analyze_tender_requirements` amb les
  costures del worker: `work_job_id`, empremta confirmada, progres i guarda del
  lease. La cache, el fingerprint i el tancament del run son els del servei.
* `sync_portal_documents` crida `pscp_portal.service.sync_tender`: llegeix sencer el
  que el portal de la PSCP publica per a la licitacio, ho reconcilia, en baixa els
  fitxers amb l'MD5 comprovat i deixa la classificacio global al dia.

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.profiles import PROFILES
from app.ai.provider import ProviderNotConfiguredError, StructuredLLMProvider
from app.core.config import settings
from app.core.errors import (
    AnalysisInProgressError,
    AnalysisInputChangedError,
    AnalysisInterruptedError,
    AnalysisNotAllowedError,
    AnalysisRunReclaimedError,
    AnalysisTooLargeError,
    NoContentToExtractError,
    NothingAnalyzableError,
    ProcessingInProgressError,
    ResumeNotAllowedError,
)
from app.ingestion.pscp_portal.client import (
    PortalClient,
    PortalDisabledError,
    PortalFetchError,
    PortalNotFoundError,
    RequestBudgetExceeded,
)
from app.ingestion.pscp_portal.parser import PortalParseError
from app.ingestion.pscp_portal.service import (
    PortalSyncConflictError,
    PortalSyncNotAvailableError,
    sync_tender,
)
from app.jobs.params import AnalyzeParams, PortalSyncParams, ProcessParams
from app.jobs.queue import JobCompletion
from app.models.enums import (
    AnalysisRunStatus,
    DownloadStatus,
    GlobalClassificationTrigger,
    JobOutcome,
    WorkJobType,
)
from app.models.tenders import Tender, TenderDocument
from app.services.global_document_classification import classify_safely
from app.services.requirement_analysis import analyze_tender_requirements
from app.services.tender_document_processing import process_tender_document
from app.services.url_guard import Resolver
from app.tenders.documents import active_documents

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
        # Un plec retirat de la font (`removed_at`) ja no es de la publicacio
        # vigent: no es torna a baixar ni extreure, ni tan sols si es demana.
        query = active_documents(ctx.job.tender_id)
        if params.document_ids:
            query = query.where(TenderDocument.id.in_(params.document_ids))
        else:
            # Per defecte: vigents i no alies (un alies te els mateixos bytes que
            # el seu canonic), de TOTS els tipus (3B-2 §3.2): un plec que el feed
            # publica mal tipat s'ha de baixar i llegir perque la classificacio
            # global el pugui reconeixer; el que llegeix l'extraccio ja ho decideix
            # el resolutor. Un document de l'operador pendent de verificar SI que
            # es processa: s'ha de poder llegir abans de verificar-lo. El que
            # alimenta classificacio, analisi i cites es `ANALYZABLE_DOCUMENT`.
            query = query.where(TenderDocument.duplicate_of_document_id.is_(None))
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

    # Classificacio global (3B-2 §3.2): determinista i sense cost, en una sessio
    # neta. Idempotent per empremta; una fallada no tomba el processat ja desat.
    async with ctx.session_factory() as session:
        await classify_safely(
            session, ctx.job.tender_id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
        )

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
                resume_from_run_id=params.resume_from_run_id,
            )
        except AnalysisNotAllowedError as exc:
            # 3B-2 §6.3 capa 1 i §6.7: la porta unica de l'analisi
            # (`llm_budget.provider_gate`) ha refusat ABANS de cap crida (simulacio
            # fora de test, model no pinat, sense preu, pressupost esgotat o massa car).
            return JobCompletion.failed(exc.code, exc.message)
        except ResumeNotAllowedError:
            # Entre la confirmacio i l'execucio el run ha deixat de ser
            # reprenible. No s'ha enviat res.
            return JobCompletion.failed(
                "resume_not_allowed", "El run parcial ja no es pot reprendre."
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
    if run.status in (AnalysisRunStatus.COMPLETED, AnalysisRunStatus.COMPLETED_EMPTY):
        # `completed_empty` (3B-2 §6.3): la feina s'ha fet i s'ha pagat; el run porta
        # `needs_review` i no es vigent, pero no es una fallada del job.
        return JobCompletion.succeeded(JobOutcome.EXECUTED, result_run_id=run.id)
    if run.status == AnalysisRunStatus.PARTIAL:
        return JobCompletion.succeeded(JobOutcome.PARTIAL, result_run_id=run.id)
    return JobCompletion.failed(run.error_code or "run_failed", result_run_id=run.id)


async def sync_portal_documents(ctx: JobContext) -> JobCompletion | None:
    PortalSyncParams.model_validate(ctx.job.parameters)
    portal = PortalClient(client=ctx.download_client)

    def failed(code: str, message: str) -> JobCompletion:
        # La traça de les consultes fetes (camins publics, estats, mides) ajuda a saber que
        # ha fallat; no porta cap dada de cap empresa ni arriba mai a l'API dels tenants.
        return JobCompletion.failed(code, message, result={"request_log": list(portal.log)})

    try:
        async with portal:
            result = await sync_tender(
                ctx.session_factory,
                ctx.job.tender_id,
                portal=portal,
                resolver=ctx.resolver,
                should_continue=ctx.should_continue,
                report_progress=ctx.report_progress,
            )
    except PortalDisabledError:
        return JobCompletion.failed(
            "portal_sync_disabled", "El connector del portal de la PSCP es apagat."
        )
    except PortalSyncNotAvailableError as exc:
        return failed("portal_sync_not_available", exc.reason)
    except RequestBudgetExceeded:
        return failed(
            "portal_request_budget", "La lectura del portal supera el limit de peticions."
        )
    except PortalNotFoundError as exc:
        # El portal respon, pero no troba una publicacio o un detall que ell mateix llista.
        return failed("portal_not_found", f"El portal no troba {exc.path}.")
    except PortalFetchError:
        # Res desat: la lectura es fa sencera abans de tocar cap document.
        return failed("portal_unavailable", "El portal de la PSCP no ha respost.")
    except PortalParseError as exc:
        # Els missatges del parser son textos propis, sense dades de la resposta.
        return failed("portal_incomplete", str(exc))
    except PortalSyncConflictError:
        return failed(
            "portal_sync_conflict",
            "Els documents de la licitacio han canviat mentre es sincronitzaven.",
        )
    if result is None:
        return None
    if result.partial:
        # Algun fitxer del portal no te bytes (fallat ara, abans o encara no reintentat).
        outcome = JobOutcome.PARTIAL
    elif result.changed:
        outcome = JobOutcome.EXECUTED
    else:
        outcome = JobOutcome.REUSED
    return JobCompletion.succeeded(outcome, result=result.as_dict())


#: Registre tancat. Afegir-hi un tipus es una decisio explicita.
HANDLERS: dict[WorkJobType, Handler] = {
    WorkJobType.PROCESS_TENDER_DOCUMENTS: process_tender_documents,
    WorkJobType.ANALYZE_TENDER_REQUIREMENTS: analyze_requirements,
    WorkJobType.SYNC_PORTAL_DOCUMENTS: sync_portal_documents,
}
