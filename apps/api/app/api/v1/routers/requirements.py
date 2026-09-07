"""Analisi de plecs: llancar-la, llegir-la i revisar-la.

Endpoints publics globals, com el feed de licitacions i la capa documental: cal
sessio iniciada, no pertinenca a cap empresa. Els requisits deriven de documents
publics i no pertanyen a cap tenant.

L'endpoint nomes tradueix HTTP: tota la logica viu a
`app.services.requirement_analysis`, que no sap res de FastAPI. Quan calgui
moure l'analisi a un worker no s'haura de tocar.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Query
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import NotFoundError
from app.models.enums import AnalysisRunStatus
from app.models.requirements import (
    AwardCriterion,
    Requirement,
    RequirementExtractionRun,
)
from app.models.tender_documents import TenderDocumentExtraction
from app.models.tenders import Tender, TenderDocument
from app.schemas.requirements import (
    AnalysisRequest,
    AnalysisResult,
    AwardCriterionRead,
    EvidenceRead,
    ExtractionRunRead,
    RequirementAnalysisRead,
    RequirementRead,
    ReviewRequest,
)
from app.services.requirement_analysis import (
    analyze_tender_requirements,
    collect_analyzable_documents,
    compute_input_fingerprint,
    current_run_for,
)

router = APIRouter(tags=["analisi de plecs"])


async def _load_tender(session: SessionDep, tender_id: uuid.UUID) -> Tender:
    tender = await session.get(Tender, tender_id)
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


async def _decorate_evidence(session: SessionDep, items: list) -> dict[uuid.UUID, dict]:
    """Revisio i nom del document de cada cita, per pintar-la sense mes crides."""
    extraction_ids = {e.extraction_id for item in items for e in item.evidence}
    document_ids = {
        e.tender_document_id for item in items for e in item.evidence if e.tender_document_id
    }
    revisions: dict[uuid.UUID, int] = {}
    names: dict[uuid.UUID, str] = {}

    if extraction_ids:
        rows = (
            await session.execute(
                sa.select(
                    TenderDocumentExtraction.id, TenderDocumentExtraction.revision_number
                ).where(TenderDocumentExtraction.id.in_(extraction_ids))
            )
        ).all()
        revisions = dict(rows)
    if document_ids:
        rows = (
            await session.execute(
                sa.select(TenderDocument.id, TenderDocument.name).where(
                    TenderDocument.id.in_(document_ids)
                )
            )
        ).all()
        names = dict(rows)
    return {"revisions": revisions, "names": names}


def _evidence_reads(item, decoration: dict) -> list[EvidenceRead]:
    reads = []
    for evidence in item.evidence:
        read = EvidenceRead.model_validate(evidence)
        read.extraction_revision = decoration["revisions"].get(evidence.extraction_id)
        read.document_name = decoration["names"].get(evidence.tender_document_id)
        reads.append(read)
    return reads


@router.post(
    "/tenders/{tender_id}/analysis/requirements",
    response_model=AnalysisResult,
    summary="Analitza els plecs i extreu-ne els requisits",
)
async def run_analysis(
    tender_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    payload: AnalysisRequest | None = None,
):
    tender = await _load_tender(session, tender_id)
    options = payload or AnalysisRequest()
    outcome = await analyze_tender_requirements(
        session,
        tender,
        force=options.force,
        document_ids=options.document_ids or None,
    )
    return AnalysisResult(
        reused=outcome.reused, run=ExtractionRunRead.model_validate(outcome.run)
    )


@router.get(
    "/tenders/{tender_id}/analysis/requirements",
    response_model=RequirementAnalysisRead,
    summary="Requisits i criteris de l'analisi vigent",
)
async def read_analysis(tender_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    await _load_tender(session, tender_id)
    run = await current_run_for(session, tender_id)
    if run is None:
        return RequirementAnalysisRead()

    requirements = (
        await session.scalars(
            sa.select(Requirement)
            .options(selectinload(Requirement.evidence))
            .where(Requirement.run_id == run.id)
            .order_by(Requirement.category, Requirement.created_at)
        )
    ).all()
    criteria = (
        await session.scalars(
            sa.select(AwardCriterion)
            .options(selectinload(AwardCriterion.evidence))
            .where(AwardCriterion.run_id == run.id)
            .order_by(AwardCriterion.points.desc().nullslast(), AwardCriterion.name)
        )
    ).all()

    decoration = await _decorate_evidence(session, list(requirements) + list(criteria))

    requirement_reads = []
    for requirement in requirements:
        read = RequirementRead.model_validate(requirement)
        read.evidence = _evidence_reads(requirement, decoration)
        requirement_reads.append(read)

    criteria_reads = []
    for criterion in criteria:
        read = AwardCriterionRead.model_validate(criterion)
        read.evidence = _evidence_reads(criterion, decoration)
        criteria_reads.append(read)

    # Obsolet: els documents han canviat des que es va fer l'analisi.
    documents, _skipped = await collect_analyzable_documents(session, tender_id)
    is_stale = bool(documents) and compute_input_fingerprint(documents) != run.input_fingerprint

    return RequirementAnalysisRead(
        run=ExtractionRunRead.model_validate(run),
        requirements=requirement_reads,
        award_criteria=criteria_reads,
        is_stale=is_stale,
    )


@router.get(
    "/tenders/{tender_id}/analysis/runs",
    response_model=list[ExtractionRunRead],
    summary="Historial d'analisis",
)
async def list_runs(
    tender_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
):
    await _load_tender(session, tender_id)
    runs = (
        await session.scalars(
            sa.select(RequirementExtractionRun)
            .where(RequirementExtractionRun.tender_id == tender_id)
            .order_by(RequirementExtractionRun.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [ExtractionRunRead.model_validate(run) for run in runs]


@router.get(
    "/requirements/{requirement_id}",
    response_model=RequirementRead,
    summary="Detall d'un requisit",
)
async def get_requirement(requirement_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    requirement = await session.scalar(
        sa.select(Requirement)
        .options(selectinload(Requirement.evidence))
        .where(Requirement.id == requirement_id)
    )
    if requirement is None:
        raise NotFoundError("Requisit no trobat.")
    decoration = await _decorate_evidence(session, [requirement])
    read = RequirementRead.model_validate(requirement)
    read.evidence = _evidence_reads(requirement, decoration)
    return read


@router.post(
    "/requirements/{requirement_id}/review",
    response_model=RequirementRead,
    summary="Revisa un requisit",
)
async def review_requirement(
    requirement_id: uuid.UUID,
    payload: ReviewRequest,
    session: SessionDep,
    user: CurrentUser,
):
    """Registra la revisio humana SENSE tocar el que va dir el model.

    La correccio va a `manual_override_json`; els camps extrets es queden com
    estaven. Aixi es pot auditar sempre si l'extractor encertava.
    """
    requirement = await session.scalar(
        sa.select(Requirement)
        .options(selectinload(Requirement.evidence))
        .where(Requirement.id == requirement_id)
    )
    if requirement is None:
        raise NotFoundError("Requisit no trobat.")

    requirement.review_status = payload.review_status
    requirement.review_note = payload.note
    if payload.manual_override is not None:
        requirement.manual_override_json = payload.manual_override
    requirement.reviewed_by = user.id
    requirement.reviewed_at = sa.func.now()
    await session.commit()
    await session.refresh(requirement)

    decoration = await _decorate_evidence(session, [requirement])
    read = RequirementRead.model_validate(requirement)
    read.evidence = _evidence_reads(requirement, decoration)
    return read


@router.get(
    "/award-criteria/{criterion_id}",
    response_model=AwardCriterionRead,
    summary="Detall d'un criteri d'adjudicacio",
)
async def get_award_criterion(criterion_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    criterion = await session.scalar(
        sa.select(AwardCriterion)
        .options(selectinload(AwardCriterion.evidence))
        .where(AwardCriterion.id == criterion_id)
    )
    if criterion is None:
        raise NotFoundError("Criteri no trobat.")
    decoration = await _decorate_evidence(session, [criterion])
    read = AwardCriterionRead.model_validate(criterion)
    read.evidence = _evidence_reads(criterion, decoration)
    return read


@router.post(
    "/award-criteria/{criterion_id}/review",
    response_model=AwardCriterionRead,
    summary="Revisa un criteri d'adjudicacio",
)
async def review_award_criterion(
    criterion_id: uuid.UUID,
    payload: ReviewRequest,
    session: SessionDep,
    user: CurrentUser,
):
    criterion = await session.scalar(
        sa.select(AwardCriterion)
        .options(selectinload(AwardCriterion.evidence))
        .where(AwardCriterion.id == criterion_id)
    )
    if criterion is None:
        raise NotFoundError("Criteri no trobat.")

    criterion.review_status = payload.review_status
    criterion.review_note = payload.note
    if payload.manual_override is not None:
        criterion.manual_override_json = payload.manual_override
    criterion.reviewed_by = user.id
    criterion.reviewed_at = sa.func.now()
    await session.commit()
    await session.refresh(criterion)

    decoration = await _decorate_evidence(session, [criterion])
    read = AwardCriterionRead.model_validate(criterion)
    read.evidence = _evidence_reads(criterion, decoration)
    return read


__all__ = ["router", "AnalysisRunStatus"]
