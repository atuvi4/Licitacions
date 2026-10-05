"""Analisi d'un plec per a una empresa (0051): lectura i execucio.

La run i els seus resultats son dades derivades i immutables: cap endpoint d'edicio.
Lectura: la run vigent i una run concreta. Escriptura: `POST /runs`, sincron i
determinista (sense LLM), que analitza o reutilitza i deixa l'analisi vigent.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy.exc import IntegrityError

from app.analysis.registry import question_text
from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.routers.tender_structure import _visible_tender
from app.core.errors import (
    AnalysisPrerequisitesMissingError,
    AnalysisRunConflictError,
    NotFoundError,
)
from app.models.tender_analysis import TenderAnalysisRun
from app.schemas.tender_analysis import AnalysisRunRead, AnalysisRunWriteRead, QuestionRead
from app.services import manual_requirement, review_actions, tender_analysis

router = APIRouter(
    prefix="/companies/{company_id}/tenders/{tender_id}/analysis", tags=["tender-analysis"]
)

#: Codis tancats de `details.missing`. A la v1 nomes n'hi ha un.
MISSING_REQUIREMENTS = "requirements"


async def run_read(
    session, run: TenderAnalysisRun, *, stale: bool | None = None
) -> AnalysisRunRead:
    await session.refresh(
        run,
        attribute_names=[
            "fit_assessments",
            "need_assessments",
            "criterion_assessments",
            "delivery_candidates",
            "findings",
            "questions",
        ],
    )
    read = AnalysisRunRead.model_validate(run)
    read.summary = tender_analysis.summary_of(run)
    read.stale = stale
    # El copy es el de la versio del run, no el de la vigent (spec 3.11).
    read.questions = [
        QuestionRead.model_validate(q).model_copy(
            update={"text": question_text(q.question_code, q.params_json, run.rules_version)}
        )
        for q in run.questions
    ]
    return read


@router.get("/current", response_model=AnalysisRunRead, summary="L'analisi vigent")
async def read_current_analysis(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    await _visible_tender(session, ctx.company_id, tender_id)
    status_ = await tender_analysis.current_analysis(session, ctx.company_id, tender_id)
    if status_.run is None:
        raise NotFoundError("Cap analisi vigent.")
    return await run_read(session, status_.run, stale=status_.stale)


@router.get("/runs/{run_id}", response_model=AnalysisRunRead, summary="Una analisi concreta")
async def read_analysis_run(
    tender_id: uuid.UUID, run_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    await _visible_tender(session, ctx.company_id, tender_id)
    run = await tender_analysis.load_run(session, ctx.company_id, tender_id, run_id)
    return await run_read(session, run)


async def _analyze_and_commit(session, company_id: uuid.UUID, tender_id: uuid.UUID):
    """Una execucio del motor i el commit. Es una funcio a part per poder-la provar
    en una cursa."""
    outcome = await tender_analysis.analyze_tender(
        session, company_id=company_id, tender_id=tender_id
    )
    await session.commit()
    return outcome


#: Les unicitats d'una analisi: nomes un conflicte d'aquestes es una cursa entre peticions.
_RUN_RACE_MARKERS = (
    "uq_tender_analysis_runs_inputs",
    "uq_tender_analysis_runs_current",
    # SQLite no anomena la restriccio: en dona les columnes. Aquest prefix cobreix els dos
    # indexs de la taula (el de les entrades afegeix `inputs_hash`; el vigent s'hi acaba).
    "tender_analysis_runs.company_id, tender_analysis_runs.tender_id",
)


def _is_run_race(exc: IntegrityError) -> bool:
    """Cert nomes per a la unicitat esperada de les analisis. Res mes."""
    # asyncpg posa el nom de la restriccio a la causa de l'excepcio de SQLAlchemy.
    cause = getattr(getattr(exc, "orig", None), "__cause__", None)
    constraint = getattr(cause, "constraint_name", None)
    text = f"{constraint or ''} {exc.orig}"
    return any(marker in text for marker in _RUN_RACE_MARKERS)


@router.post(
    "/runs",
    response_model=AnalysisRunWriteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Analitza (o reutilitza) i deixa vigent",
)
async def run_analysis(
    tender_id: uuid.UUID, response: Response, session: SessionDep, ctx: CompanyCtx
):
    """Analitza el plec per a l'empresa i deixa l'analisi vigent.

    `reused` vol dir que no s'ha escrit cap analisi nova: ja n'hi havia una amb les
    mateixes entrades (pot ser diferent de la que el client tenia en pantalla).
    """
    # El mateix permis que revisar els requisits del plec: cap llista nova.
    review_actions.assert_can_review(ctx.membership.role)
    # Abans de cap rollback: despres, `ctx.company` queda caducat i no es pot rellegir.
    company_id = ctx.company_id
    await _visible_tender(session, company_id, tender_id)
    effective = await manual_requirement.effective_requirements(session, tender_id)
    if not list(effective.all):
        # El motor no refusa mai: sense requisits faria una analisi buida i enganyosa.
        raise AnalysisPrerequisitesMissingError(
            "No hi ha requisits per analitzar.",
            details={"missing": [MISSING_REQUIREMENTS]},
        )
    try:
        outcome = await _analyze_and_commit(session, company_id, tender_id)
    except IntegrityError as exc:
        if not _is_run_race(exc):
            raise  # un altre error d'integritat no s'emmascara
        # Una altra peticio ha desat la mateixa analisi (o una de vigent) alhora. Un
        # sol reintent: el motor la trobara per `inputs_hash` i la reutilitzara.
        await session.rollback()
        try:
            outcome = await _analyze_and_commit(session, company_id, tender_id)
        except IntegrityError as again:
            await session.rollback()
            if not _is_run_race(again):
                raise
            raise AnalysisRunConflictError(
                "L'analisi ha canviat alhora des d'una altra peticio: torneu-ho a provar."
            ) from again
    if outcome.reused:
        response.status_code = status.HTTP_200_OK
    read = await run_read(session, outcome.run, stale=False)
    return AnalysisRunWriteRead(**read.model_dump(), reused=outcome.reused)
