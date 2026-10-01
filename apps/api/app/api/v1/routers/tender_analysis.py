"""Lectura de l'analisi d'un plec per a una empresa (0051).

Nomes lectura: la run i els seus resultats son dades derivades i immutables.
Cap endpoint d'edicio, cap tauler: la run vigent i una run concreta, i prou.
L'execucio es la via interna `app.services.tender_analysis.analyze_tender`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.analysis.registry import question_text
from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.routers.tender_structure import _visible_tender
from app.core.errors import NotFoundError
from app.models.tender_analysis import TenderAnalysisRun
from app.schemas.tender_analysis import AnalysisRunRead, QuestionRead
from app.services import tender_analysis

router = APIRouter(
    prefix="/companies/{company_id}/tenders/{tender_id}/analysis", tags=["tender-analysis"]
)


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
    read.questions = [
        QuestionRead.model_validate(q).model_copy(
            update={"text": question_text(q.question_code, q.params_json)}
        )
        for q in run.questions
    ]
    return read


@router.get("/current", response_model=AnalysisRunRead, summary="L'analisi vigent")
async def read_current_analysis(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    await _visible_tender(session, ctx.company_id, tender_id)
    run = await tender_analysis.current_run(session, ctx.company_id, tender_id)
    if run is None:
        raise NotFoundError("Cap analisi vigent.")
    stale = await tender_analysis.is_stale(session, run)
    return await run_read(session, run, stale=stale)


@router.get("/runs/{run_id}", response_model=AnalysisRunRead, summary="Una analisi concreta")
async def read_analysis_run(
    tender_id: uuid.UUID, run_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    await _visible_tender(session, ctx.company_id, tender_id)
    run = await tender_analysis.load_run(session, ctx.company_id, tender_id, run_id)
    return await run_read(session, run)
