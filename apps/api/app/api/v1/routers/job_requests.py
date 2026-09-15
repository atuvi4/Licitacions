"""Peticions de feina llarga: processar plecs i analitzar-ne els requisits.

Cap endpoint d'aqui no fa la feina: creen o consulten una peticio i tornen de
seguida. La fa el worker (`python -m app.jobs worker`).

Pengen de `/companies/{company_id}`: la peticio es de l'empresa (auditoria i, mes
endavant, cost). El resultat no ho es: els requisits es llegeixen, com sempre, a
`GET /tenders/{tender_id}/analysis/requirements`.

Respostes de `POST`:

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
from app.jobs import requests as job_requests
from app.models.enums import JobOutcome, WorkJobType
from app.schemas.jobs import (
    AnalyzeRequirementsJobIn,
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
    response_model=JobSubmissionRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Demana l'analisi de requisits dels plecs",
)
async def request_requirement_analysis(
    tender_id: uuid.UUID,
    ctx: CompanyCtx,
    session: SessionDep,
    response: Response,
    payload: AnalyzeRequirementsJobIn | None = None,
):
    options = payload or AnalyzeRequirementsJobIn()
    submission = await job_requests.submit_analysis_request(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        tender_id=tender_id,
        force=options.force,
        document_ids=options.document_ids,
    )
    return await _submission_response(session, response, submission)


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
