"""Previews d'analisi: ensenyar el volum, i nomes despres deixar-lo demanar.

Flux:

1. `create_quote` calcula el preview (`analysis_planning.build_preview`, sense
   cap proveidor) i el desa com a `AnalysisQuote` de l'empresa. Qualsevol membre.
2. `confirm_quote`, en una transaccio:
   * rol dins `ANALYSIS_CONFIRM_ROLES` (configuracio), si no 403;
   * el quote ha d'estar obert i sense caducar;
   * es **torna a calcular** el preview i es compara amb el desat (versio del
     calcul, fingerprint, chunking, pagines, chunks, perfil, proveidor, model).
     Si res ha canviat no es crea res: es desa un preview nou i l'usuari l'ha de
     tornar a confirmar;
   * limit de chunks i limit d'analisis pendents de l'empresa;
   * es crea la `JobRequest` de 7A amb l'empremta confirmada, i el quote queda
     `confirmed` a la mateixa transaccio.

Tenant-safe: el preview no diu mai si una altra empresa esta analitzant el mateix
plec, i el limit d'actives nomes compta peticions de la mateixa empresa.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.profiles import DEFAULT_PROFILE, PROFILES, ExtractorProfile
from app.ai.provider import StructuredLLMProvider, get_provider
from app.core.config import settings
from app.core.errors import (
    AnalysisInputChangedError,
    AnalysisTooLargeError,
    CompanyActiveLimitError,
    NotFoundError,
    NothingAnalyzableError,
    PermissionDeniedError,
    QuoteNotOpenError,
)
from app.jobs import requests as job_requests
from app.jobs.queue import is_postgres, utcnow
from app.models.analysis_quotes import AnalysisQuote
from app.models.enums import AnalysisQuoteStatus, JobRequestStatus, WorkJobType
from app.models.identity import Company
from app.models.jobs import JobRequest
from app.models.tenders import Tender
from app.services.analysis_planning import AnalysisPreview, build_preview


class QuoteReplacedError(Exception):
    """El quote no es pot confirmar tal com es; n'hi ha un de nou per ensenyar."""

    code = "preview_changed"

    def __init__(self, quote: AnalysisQuote) -> None:
        super().__init__(self.code)
        self.quote = quote


class PreviewChangedError(QuoteReplacedError):
    code = "preview_changed"


class QuoteExpiredError(QuoteReplacedError):
    code = "quote_expired"


@dataclass(frozen=True, slots=True)
class Confirmation:
    quote: AnalysisQuote
    submission: job_requests.Submission


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def can_confirm(role: str | None) -> bool:
    return role is not None and str(role) in settings.ANALYSIS_CONFIRM_ROLES


def can_process_documents(role: str | None) -> bool:
    return role is not None and str(role) in settings.DOCUMENT_PROCESS_ROLES


def _quote_from_preview(
    preview: AnalysisPreview,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
    now: datetime,
) -> AnalysisQuote:
    estimate = preview.estimate
    return AnalysisQuote(
        company_id=company_id,
        user_id=user_id,
        tender_id=preview.tender_id,
        planning_version=preview.planning_version,
        profile=preview.profile,
        provider=preview.provider,
        model=preview.model,
        force=preview.force,
        document_ids=preview.document_ids,
        input_fingerprint=preview.input_fingerprint,
        pages_per_chunk=preview.pages_per_chunk,
        overlap_pages=preview.overlap_pages,
        documents=preview.documents,
        skipped_documents=preview.skipped_documents,
        documents_count=len(preview.documents),
        pages_total=preview.pages_total,
        pages_in_plan=preview.pages_in_plan,
        chunks=preview.chunks,
        chunks_to_send=preview.chunks_to_send,
        reusable_run_id=preview.reusable_run_id,
        estimated_input_tokens=estimate.input_tokens if estimate else None,
        estimated_output_tokens=estimate.output_tokens if estimate else None,
        estimate_basis=estimate.basis() if estimate else None,
        max_chunks=preview.max_chunks,
        allowed=preview.allowed,
        blocking_reason=preview.blocking_reason,
        status=AnalysisQuoteStatus.OPEN,
        expires_at=now + timedelta(seconds=settings.ANALYSIS_QUOTE_TTL_SECONDS),
    )


def _quote_identity(quote: AnalysisQuote) -> tuple:
    return (
        quote.planning_version,
        quote.input_fingerprint,
        quote.pages_per_chunk,
        quote.overlap_pages,
        quote.pages_in_plan,
        quote.chunks,
        quote.profile,
        quote.provider,
        quote.model,
    )


async def _preview(
    session: AsyncSession,
    *,
    tender_id: uuid.UUID,
    force: bool,
    document_ids: list[uuid.UUID] | list[str],
    profile: ExtractorProfile,
    provider_factory: Callable[[], StructuredLLMProvider],
) -> AnalysisPreview:
    if await session.get(Tender, tender_id) is None:
        raise NotFoundError("Licitacio no trobada.")
    return await build_preview(
        session,
        tender_id,
        profile=profile,
        provider=provider_factory(),
        force=force,
        document_ids=[uuid.UUID(str(item)) for item in document_ids],
    )


async def create_quote(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
    tender_id: uuid.UUID,
    force: bool = False,
    document_ids: list[uuid.UUID] | None = None,
    profile: ExtractorProfile = DEFAULT_PROFILE,
    provider_factory: Callable[[], StructuredLLMProvider] = get_provider,
) -> AnalysisQuote:
    """Calcula i desa el preview. Nomes lectures del plec; cap proveidor."""
    preview = await _preview(
        session,
        tender_id=tender_id,
        force=force,
        document_ids=document_ids or [],
        profile=profile,
        provider_factory=provider_factory,
    )
    if preview.is_empty:
        raise NothingAnalyzableError(
            "Cap document d'aquesta licitacio te text extret per analitzar. "
            "Processa'n els plecs primer."
        )
    quote = _quote_from_preview(preview, company_id=company_id, user_id=user_id, now=utcnow())
    session.add(quote)
    await session.commit()
    await session.refresh(quote)
    return quote


async def get_quote(
    session: AsyncSession, *, company_id: uuid.UUID, quote_id: uuid.UUID
) -> AnalysisQuote:
    quote = await session.scalar(
        sa.select(AnalysisQuote).where(
            AnalysisQuote.id == quote_id, AnalysisQuote.company_id == company_id
        )
    )
    if quote is None:
        raise NotFoundError("Preview no trobat.")
    return quote


async def active_analysis_count(session: AsyncSession, company_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            sa.select(sa.func.count(JobRequest.id)).where(
                JobRequest.company_id == company_id,
                JobRequest.job_type == WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
                JobRequest.status == JobRequestStatus.PENDING,
            )
        )
        or 0
    )


async def _replace(
    session: AsyncSession,
    old: AnalysisQuote,
    preview: AnalysisPreview,
    *,
    status: AnalysisQuoteStatus,
    user_id: uuid.UUID | None,
) -> AnalysisQuote:
    new = _quote_from_preview(preview, company_id=old.company_id, user_id=user_id, now=utcnow())
    session.add(new)
    await session.flush()
    old.status = status
    old.superseded_by_id = new.id
    await session.commit()
    await session.refresh(new)
    return new


async def confirm_quote(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
    role: str | None,
    quote_id: uuid.UUID,
    provider_factory: Callable[[], StructuredLLMProvider] = get_provider,
) -> Confirmation:
    """Confirma un preview i crea la peticio d'analisi. Veure el docstring del modul."""
    if not can_confirm(role):
        raise PermissionDeniedError(
            "El teu rol no pot llancar analisis: fan despesa. Demana-ho a qui en tingui permis."
        )

    query = sa.select(AnalysisQuote).where(
        AnalysisQuote.id == quote_id, AnalysisQuote.company_id == company_id
    )
    if is_postgres(session):
        query = query.with_for_update()
    quote = await session.scalar(query)
    if quote is None:
        raise NotFoundError("Preview no trobat.")
    if quote.status != AnalysisQuoteStatus.OPEN:
        raise QuoteNotOpenError("Aquest preview ja no es pot confirmar.")

    profile = PROFILES.get(quote.profile, DEFAULT_PROFILE)
    preview = await _preview(
        session,
        tender_id=quote.tender_id,
        force=quote.force,
        document_ids=quote.document_ids,
        profile=profile,
        provider_factory=provider_factory,
    )

    if _aware(quote.expires_at) <= utcnow():
        if preview.is_empty:
            quote.status = AnalysisQuoteStatus.EXPIRED
            await session.commit()
            raise NothingAnalyzableError("Ja no hi ha cap document analitzable.")
        raise QuoteExpiredError(
            await _replace(
                session, quote, preview, status=AnalysisQuoteStatus.EXPIRED, user_id=user_id
            )
        )

    if preview.is_empty:
        raise NothingAnalyzableError("Ja no hi ha cap document analitzable.")
    if preview.identity() != _quote_identity(quote):
        raise PreviewChangedError(
            await _replace(
                session, quote, preview, status=AnalysisQuoteStatus.SUPERSEDED, user_id=user_id
            )
        )
    if not preview.allowed:
        raise AnalysisTooLargeError(
            f"L'analisi te {preview.chunks_to_send} chunks i el maxim es {preview.max_chunks}."
        )

    confirmed_quote_id = quote.id

    async def before_commit(tx: AsyncSession, request: JobRequest) -> None:
        # Bloqueig de la fila de l'empresa: dues confirmacions simultanies de la
        # mateixa empresa es fan una rere l'altra, i el recompte inclou la
        # peticio que s'acaba d'escriure.
        #
        # `FOR NO KEY UPDATE` i no `FOR UPDATE`: inserir la peticio ja ha pres un
        # `FOR KEY SHARE` sobre aquesta fila (la FK de `job_requests.company_id`).
        # Amb `FOR UPDATE`, dues confirmacions amb el KEY SHARE pres s'esperaven
        # l'una a l'altra (deadlock); `NO KEY UPDATE` les serialitza igual entre
        # elles i no xoca amb els bloquejos de les claus foranes.
        lock = sa.select(Company.id).where(Company.id == company_id)
        if is_postgres(tx):
            lock = lock.with_for_update(key_share=True)  # FOR NO KEY UPDATE
        await tx.execute(lock)
        if await active_analysis_count(tx, company_id) > settings.ANALYSIS_MAX_ACTIVE_PER_COMPANY:
            raise CompanyActiveLimitError(
                "L'empresa ja te el maxim d'analisis en marxa. Espera que n'acabi alguna."
            )
        await tx.execute(
            sa.update(AnalysisQuote)
            .where(AnalysisQuote.id == confirmed_quote_id)
            .values(status=AnalysisQuoteStatus.CONFIRMED, confirmed_at=utcnow())
            .execution_options(synchronize_session=False)
        )

    try:
        submission = await job_requests.submit_analysis_request(
            session,
            company_id=company_id,
            user_id=user_id,
            tender_id=quote.tender_id,
            force=quote.force,
            document_ids=[uuid.UUID(str(item)) for item in quote.document_ids],
            profile=profile,
            provider_factory=provider_factory,
            expected_fingerprint=quote.input_fingerprint,
            analysis_quote_id=confirmed_quote_id,
            before_commit=before_commit,
        )
    except AnalysisInputChangedError:
        await session.rollback()
        fresh = await session.get(AnalysisQuote, confirmed_quote_id, populate_existing=True)
        preview = await _preview(
            session,
            tender_id=fresh.tender_id,
            force=fresh.force,
            document_ids=fresh.document_ids,
            profile=profile,
            provider_factory=provider_factory,
        )
        raise PreviewChangedError(
            await _replace(
                session, fresh, preview, status=AnalysisQuoteStatus.SUPERSEDED, user_id=user_id
            )
        ) from None
    except Exception:
        await session.rollback()
        raise

    confirmed = await session.get(AnalysisQuote, confirmed_quote_id, populate_existing=True)
    return Confirmation(quote=confirmed, submission=submission)
