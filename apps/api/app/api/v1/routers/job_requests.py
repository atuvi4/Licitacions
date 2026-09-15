"""Peticions de feina llarga: processar plecs i analitzar-ne els requisits.

Cap endpoint d'aqui no fa la feina: creen o consulten una peticio i tornen de
seguida. La fa el worker (`python -m app.jobs worker`).

Pengen de `/companies/{company_id}`: la peticio es de l'empresa (auditoria i, mes
endavant, cost). El resultat no ho es: els requisits es llegeixen, com sempre, a
`GET /tenders/{tender_id}/analysis/requirements`.

L'analisi es demana des d'un preview confirmat (`analysis_quotes.py`). El `POST`
d'analisi directe respon 409 `analysis_confirmation_required` i no executa res.

Respostes de `POST` (i de la confirmacio d'un preview):

* 202 `accepted`: la peticio queda registrada. Tant si ha creat la feina com si
  s'ha vinculat a una que ja existia: la resposta no ho distingeix.
* 200 `accepted` + `already_requested`: la mateixa empresa ja ho havia demanat.
* 200 `succeeded` + `reused_existing`: ja hi havia resultat vigent i no cal fer res.
* 409 `operation_busy`: ara mateix no es pot, per una rao que no depen de cap tenant.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CompanyCtx, SessionDep
from app.core.errors import AnalysisConfirmationRequiredError, PermissionDeniedError
from app.jobs import quotes as analysis_quotes
from app.jobs import requests as job_requests
from app.models.enums import JobOutcome, WorkJobType
from app.schemas.jobs import (
    JobRequestRead,
    JobSubmissionRead,
    ProcessDocumentsJobIn,
)

router = APIRouter(tags=["feina asincrona"])


async def _submission_response(
    session: SessionDep, response: Response, submission: job_requests.Submission
) -> JobSubmissionRead:
    request = submission.request
    view = await job_requests.load_view(session, request)
    if submission.already_requested or request.outcome == JobOutcome.REUSED_EXISTING:
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_202_ACCEPTED
    return JobSubmissionRead(**view, already_requested=submission.already_requested)


@router.post(
    "/tenders/{tender_id}/job-requests/analyze-requirements",
    status_code=status.HTTP_409_CONFLICT,
    summary="(Cal confirmacio) L'analisi es demana des d'un preview confirmat",
)
async def request_requirement_analysis(tender_id: uuid.UUID, ctx: CompanyCtx):
    """No executa ni encua res. Accepta qualsevol cos: un client antic rep 409, no 422."""
    base = f"/companies/{ctx.company_id}"
    raise AnalysisConfirmationRequiredError(
        "Cal veure el volum de l'analisi i confirmar-lo abans de demanar-la.",
        details={
            "preview": f"{base}/tenders/{tender_id}/analysis-quotes",
            "confirm": f"{base}/analysis-quotes/{{quote_id}}/confirm",
        },
    )


@router.post(
    "/tenders/{tender_id}/job-requests/process-documents",
    response_model=JobSubmissionRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Demana el processat dels plecs",
)
async def request_document_processing(
    tender_id: uuid.UUID,
    ctx: CompanyCtx,
    session: SessionDep,
    response: Response,
    payload: ProcessDocumentsJobIn | None = None,
):
    if not analysis_quotes.can_process_documents(ctx.membership.role):
        raise PermissionDeniedError("El teu rol no pot demanar el processat dels plecs.")
    options = payload or ProcessDocumentsJobIn()
    submission = await job_requests.submit_process_request(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        tender_id=tender_id,
        document_ids=options.document_ids,
        force_download=options.force_download,
        force_reextract=options.force_reextract,
    )
    return await _submission_response(session, response, submission)


@router.get(
    "/job-requests/{request_id}",
    response_model=JobRequestRead,
    summary="Estat d'una peticio de l'empresa",
)
async def read_job_request(request_id: uuid.UUID, ctx: CompanyCtx, session: SessionDep):
    request = await job_requests.get_request(
        session, company_id=ctx.company_id, request_id=request_id
    )
    return JobRequestRead(**await job_requests.load_view(session, request))


@router.get(
    "/tenders/{tender_id}/job-requests",
    response_model=list[JobRequestRead],
    summary="Peticions de l'empresa per a una licitacio",
)
async def list_job_requests(
    tender_id: uuid.UUID,
    ctx: CompanyCtx,
    session: SessionDep,
    job_type: WorkJobType | None = None,
    active: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
):
    views = await job_requests.list_requests(
        session,
        company_id=ctx.company_id,
        tender_id=tender_id,
        job_type=job_type,
        active_only=active,
        limit=limit,
    )
    return [JobRequestRead(**view) for view in views]


@router.post(
    "/job-requests/{request_id}/cancel",
    response_model=JobRequestRead,
    summary="Cancel·la una peticio pendent de l'empresa",
)
async def cancel_job_request(request_id: uuid.UUID, ctx: CompanyCtx, session: SessionDep):
    request = await job_requests.cancel_request(
        session, company_id=ctx.company_id, request_id=request_id
    )
    return JobRequestRead(**await job_requests.load_view(session, request))
