"""Previews d'analisi: veure el volum, i confirmar-lo abans de fer despesa.

* `POST /tenders/{tid}/analysis-quotes` calcula el preview (cap proveidor) i el
  desa. Qualsevol membre de l'empresa.
* `POST /analysis-quotes/{qid}/confirm` el torna a calcular, el compara i crea la
  peticio d'analisi. Nomes els rols de `ANALYSIS_CONFIRM_ROLES`.

Si el preview ja no val (els plecs, el chunking o la versio del calcul han
canviat, o ha caducat), la confirmacio respon 409 amb el preview nou a
`error.details.quote`: l'usuari l'ha de tornar a veure. No es crea res.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CompanyCtx, SessionDep
from app.core.config import settings
from app.core.errors import ConflictError
from app.jobs import quotes as analysis_quotes
from app.jobs import requests as job_requests
from app.models.enums import JobOutcome
from app.schemas.jobs import (
    AnalysisConfirmationRead,
    AnalysisLimitsRead,
    AnalysisQuoteIn,
    AnalysisQuoteRead,
)

router = APIRouter(tags=["previews d'analisi"])


class _QuoteReplaced(ConflictError):
    """Resposta 409 que porta el preview nou perque la interficie l'ensenyi."""

    def __init__(self, exc: analysis_quotes.QuoteReplacedError) -> None:
        messages = {
            "preview_changed": "El volum de l'analisi ha canviat des del preview. Revisa'l.",
            "quote_expired": "El preview ha caducat. Revisa'l de nou.",
        }
        super().__init__(
            messages.get(exc.code, "Cal revisar el preview de nou."),
            details={
                "quote": AnalysisQuoteRead.model_validate(exc.quote).model_dump(mode="json")
            },
        )
        self.code = exc.code


@router.post(
    "/tenders/{tender_id}/analysis-quotes",
    response_model=AnalysisQuoteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Preview del volum d'una analisi (sense cridar cap proveidor)",
)
async def create_analysis_quote(
    tender_id: uuid.UUID,
    ctx: CompanyCtx,
    session: SessionDep,
    payload: AnalysisQuoteIn | None = None,
):
    options = payload or AnalysisQuoteIn()
    quote = await analysis_quotes.create_quote(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        tender_id=tender_id,
        force=options.force,
        document_ids=options.document_ids,
        profile=options.profile,
        resume_from_run_id=options.resume_from_run_id,
        accept_incomplete_package=options.accept_incomplete_package,
        acknowledge_quality=options.acknowledge_quality,
    )
    return AnalysisQuoteRead.model_validate(quote)


@router.get(
    "/analysis-quotes/{quote_id}",
    response_model=AnalysisQuoteRead,
    summary="Un preview de l'empresa",
)
async def read_analysis_quote(quote_id: uuid.UUID, ctx: CompanyCtx, session: SessionDep):
    quote = await analysis_quotes.get_quote(session, company_id=ctx.company_id, quote_id=quote_id)
    return AnalysisQuoteRead.model_validate(quote)


@router.post(
    "/analysis-quotes/{quote_id}/confirm",
    response_model=AnalysisConfirmationRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Confirma el preview i demana l'analisi",
)
async def confirm_analysis_quote(
    quote_id: uuid.UUID, ctx: CompanyCtx, session: SessionDep, response: Response
):
    try:
        confirmation = await analysis_quotes.confirm_quote(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            role=ctx.membership.role,
            quote_id=quote_id,
        )
    except analysis_quotes.QuoteReplacedError as exc:
        raise _QuoteReplaced(exc) from None

    submission = confirmation.submission
    request = submission.request
    view = await job_requests.load_view(session, request)
    if submission.already_requested or request.outcome == JobOutcome.REUSED_EXISTING:
        response.status_code = status.HTTP_200_OK
    return AnalysisConfirmationRead(
        **view,
        already_requested=submission.already_requested,
        quote_id=confirmation.quote.id,
    )


@router.get(
    "/analysis-limits",
    response_model=AnalysisLimitsRead,
    summary="Limits del pilot i permisos de l'usuari en aquesta empresa",
)
async def read_analysis_limits(ctx: CompanyCtx, session: SessionDep):
    role = ctx.membership.role
    return AnalysisLimitsRead(
        max_chunks_per_run=settings.ANALYSIS_MAX_CHUNKS_PER_RUN,
        max_active_per_company=settings.ANALYSIS_MAX_ACTIVE_PER_COMPANY,
        active_now=await analysis_quotes.active_analysis_count(session, ctx.company_id),
        can_confirm=analysis_quotes.can_confirm(role),
        can_process_documents=analysis_quotes.can_process_documents(role),
        confirm_roles=list(settings.ANALYSIS_CONFIRM_ROLES),
    )
