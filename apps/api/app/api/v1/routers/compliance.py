"""Compliment empresa <-> requisits.

A diferencia dels plecs i dels requisits, aixo SI que penja de
`/companies/{company_id}`: la resposta a "ho acredito?" es d'una empresa
concreta i queda sotmesa a l'aillament multi-tenant. La dependencia
`get_company_context` comprova la pertinenca a cada crida.

L'endpoint nomes tradueix HTTP; el motor viu a `app.compliance` i
l'orquestracio a `app.services.compliance_analysis`.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Query
from sqlalchemy.orm import selectinload

from app.api.deps import CompanyCtx, SessionDep
from app.compliance import consolidation
from app.core.errors import NotFoundError
from app.models.compliance import CompanyComplianceRun, CompanyRequirementAssessment
from app.models.identity import Company, User
from app.models.requirements import Requirement, RequirementEvidence
from app.models.tender_documents import TenderDocumentExtraction
from app.models.tenders import Tender, TenderDocument
from app.schemas.compliance import (
    AssessmentGroupRead,
    AssessmentRead,
    AssessmentReviewRequest,
    CitationRead,
    ComplianceRead,
    ComplianceRequest,
    ComplianceResult,
    ComplianceRunRead,
    ConsolidationRead,
    RequirementSourceRead,
)
from app.services.compliance_analysis import (
    analyze_company_compliance,
    current_run_for,
    is_run_stale,
    review_assessment,
)
from app.tenders.visibility import visible_to_company

router = APIRouter(prefix="/tenders/{tender_id}/compliance", tags=["compliment"])
#: Els assessments s'adrecen sols, pero igualment validen la pertinenca.
assessments_router = APIRouter(prefix="/assessments", tags=["compliment"])


async def _load_tender(
    session: SessionDep, tender_id: uuid.UUID, company_id: uuid.UUID
) -> Tender:
    """La licitacio, NOMES si aquesta empresa la pot veure.

    Sense el filtre, respondre `200` sobre una licitacio privada d'una altra
    empresa ja en confirma l'existencia, encara que el compliment que es calcula
    sigui el de qui pregunta.
    """
    tender = await session.scalar(
        sa.select(Tender).where(Tender.id == tender_id, visible_to_company(company_id))
    )
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


async def _requirement_sources(
    session: SessionDep, requirement_ids: list[uuid.UUID]
) -> dict[uuid.UUID, RequirementSourceRead]:
    """La cita del plec de cada requisit, per poder-hi saltar."""
    if not requirement_ids:
        return {}

    requirements = (
        await session.scalars(
            sa.select(Requirement)
            .options(selectinload(Requirement.evidence))
            .where(Requirement.id.in_(requirement_ids))
        )
    ).all()

    extraction_ids = {
        evidence.extraction_id for r in requirements for evidence in r.evidence
    }
    document_ids = {
        evidence.tender_document_id
        for r in requirements
        for evidence in r.evidence
        if evidence.tender_document_id
    }
    revisions: dict[uuid.UUID, int] = {}
    names: dict[uuid.UUID, str] = {}
    if extraction_ids:
        revisions = dict(
            (
                await session.execute(
                    sa.select(
                        TenderDocumentExtraction.id, TenderDocumentExtraction.revision_number
                    ).where(TenderDocumentExtraction.id.in_(extraction_ids))
                )
            ).all()
        )
    if document_ids:
        names = dict(
            (
                await session.execute(
                    sa.select(TenderDocument.id, TenderDocument.name).where(
                        TenderDocument.id.in_(document_ids)
                    )
                )
            ).all()
        )

    sources: dict[uuid.UUID, RequirementSourceRead] = {}
    for requirement in requirements:
        primary: RequirementEvidence | None = next(iter(requirement.evidence), None)
        citations = [
            CitationRead(
                evidence_id=evidence.id,
                tender_document_id=evidence.tender_document_id,
                extraction_id=evidence.extraction_id,
                extraction_revision=revisions.get(evidence.extraction_id),
                page_number=evidence.page_number,
                quote=evidence.quote,
                document_name=names.get(evidence.tender_document_id),
            )
            for evidence in requirement.evidence
        ]
        sources[requirement.id] = RequirementSourceRead(
            requirement_id=requirement.id,
            title=requirement.title,
            category=requirement.category,
            review_status=requirement.review_status,
            tender_document_id=primary.tender_document_id if primary else None,
            extraction_id=primary.extraction_id if primary else None,
            extraction_revision=revisions.get(primary.extraction_id) if primary else None,
            page_number=primary.page_number if primary else None,
            quote=primary.quote if primary else None,
            document_name=(
                names.get(primary.tender_document_id) if primary else None
            ),
            citations=citations,
        )
    return sources


def _consolidation_read(assessment: CompanyRequirementAssessment) -> ConsolidationRead | None:
    data = (assessment.explanation_params or {}).get(consolidation.PARAMS_KEY)
    if not data:
        return None
    return ConsolidationRead(
        canonical_requirement_id=data["canonical_requirement_id"],
        is_canonical=bool(data.get("is_canonical")),
        member_requirement_ids=data.get("member_requirement_ids") or [],
        divergent=bool(data.get("divergent")),
    )


def _assessment_read(
    assessment: CompanyRequirementAssessment,
    sources: dict,
    reviewers: dict | None = None,
) -> AssessmentRead:
    # `effective_result` es una propietat del model: `from_attributes` la llegeix.
    read = AssessmentRead.model_validate(assessment)
    read.requirement = sources.get(assessment.requirement_id)
    read.consolidation = _consolidation_read(assessment)
    normalization = (assessment.explanation_params or {}).get("normalization") or {}
    if normalization:
        read.compared_value = normalization.get("value")
        read.compared_provenance = list(normalization.get("provenance") or [])
        read.normalization_status = normalization.get("status")
        read.normalization_reason = normalization.get("reason")
    if assessment.reviewed_by:
        read.reviewed_by_name = (reviewers or {}).get(assessment.reviewed_by)
    return read


def _groups(assessments) -> list[AssessmentGroupRead]:
    """Una entrada per exigencia amb duplicats, resolta com el recompte del run.

    La resolucio es la de `consolidation.resolve` (el resultat mes prudent i el
    bloqueig mes fort), i el canonic es el que va triar el calcul: aqui no es
    torna a decidir res.
    """
    members: dict[str, list[CompanyRequirementAssessment]] = {}
    for assessment in assessments:
        if (assessment.explanation_params or {}).get(consolidation.PARAMS_KEY):
            marker = consolidation.group_marker(
                assessment.requirement_id, assessment.explanation_params
            )
            members.setdefault(marker, []).append(assessment)
    groups = []
    for marker, items in members.items():
        canonical = next(
            (a for a in items if str(a.requirement_id) == marker), items[0]
        )
        ordered = [canonical, *(a for a in items if a is not canonical)]
        result, blocker = consolidation.resolve(
            (a.effective_result, a.blocker_status) for a in ordered
        )
        groups.append(
            AssessmentGroupRead(
                canonical_assessment_id=canonical.id,
                canonical_requirement_id=canonical.requirement_id,
                member_assessment_ids=[a.id for a in ordered],
                member_requirement_ids=[a.requirement_id for a in ordered],
                effective_result=result,
                blocker_status=blocker,
                divergent=len({a.effective_result for a in ordered}) > 1,
            )
        )
    return groups


async def _reviewer_names(session, assessments) -> dict:
    """Qui va revisar cada assessment. Un veredicte huma porta nom."""
    ids = {a.reviewed_by for a in assessments if a.reviewed_by}
    if not ids:
        return {}
    rows = (
        await session.execute(
            sa.select(User.id, User.full_name, User.email).where(User.id.in_(ids))
        )
    ).all()
    return {row[0]: row[1] or row[2] for row in rows}


@router.post("", response_model=ComplianceResult, summary="Calcula el compliment")
async def run_compliance(
    tender_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    payload: ComplianceRequest | None = None,
):
    tender = await _load_tender(session, tender_id, ctx.company_id)
    company = await session.get(Company, ctx.company_id)
    options = payload or ComplianceRequest()

    outcome = await analyze_company_compliance(
        session,
        company,
        tender,
        force=options.force,
        tender_lot_id=options.tender_lot_id,
    )
    return ComplianceResult(
        reused=outcome.reused, run=ComplianceRunRead.model_validate(outcome.run)
    )


@router.get("", response_model=ComplianceRead, summary="Compliment vigent")
async def read_compliance(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    await _load_tender(session, tender_id, ctx.company_id)
    run = await current_run_for(session, ctx.company_id, tender_id)
    if run is None:
        return ComplianceRead()

    assessments = (
        await session.scalars(
            sa.select(CompanyRequirementAssessment)
            .options(selectinload(CompanyRequirementAssessment.evidence))
            .where(CompanyRequirementAssessment.assessment_run_id == run.id)
            .order_by(
                CompanyRequirementAssessment.obligation,
                CompanyRequirementAssessment.blocker_status.desc(),
            )
        )
    ).all()

    sources = await _requirement_sources(session, [a.requirement_id for a in assessments])
    reviewers = await _reviewer_names(session, assessments)
    company = await session.get(Company, ctx.company_id)
    stale = await is_run_stale(session, run, company)

    groups = _groups(assessments)
    reads = [_assessment_read(a, sources, reviewers) for a in assessments]
    # `divergent` es del grup i es calcula ara: el desat al calcul pot haver quedat
    # desfasat per una revisio humana posterior.
    divergent = {
        member: group.divergent for group in groups for member in group.member_assessment_ids
    }
    for read in reads:
        if read.consolidation is not None:
            read.consolidation.divergent = divergent.get(read.id, False)
    return ComplianceRead(
        run=ComplianceRunRead.model_validate(run),
        assessments=reads,
        groups=groups,
        is_stale=stale,
    )


@router.get("/runs", response_model=list[ComplianceRunRead], summary="Historial de calculs")
async def list_runs(
    tender_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    limit: int = Query(default=20, ge=1, le=100),
):
    await _load_tender(session, tender_id, ctx.company_id)
    runs = (
        await session.scalars(
            sa.select(CompanyComplianceRun)
            .where(
                CompanyComplianceRun.company_id == ctx.company_id,
                CompanyComplianceRun.tender_id == tender_id,
            )
            .order_by(CompanyComplianceRun.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [ComplianceRunRead.model_validate(run) for run in runs]


async def _load_assessment(
    session: SessionDep, ctx: CompanyCtx, assessment_id: uuid.UUID
) -> CompanyRequirementAssessment:
    """Sempre filtrat per empresa: un tenant no pot llegir els d'un altre."""
    assessment = await session.scalar(
        sa.select(CompanyRequirementAssessment)
        .options(selectinload(CompanyRequirementAssessment.evidence))
        .where(
            CompanyRequirementAssessment.id == assessment_id,
            CompanyRequirementAssessment.company_id == ctx.company_id,
        )
    )
    if assessment is None:
        raise NotFoundError("Avaluacio no trobada.")
    return assessment


@assessments_router.get(
    "/{assessment_id}", response_model=AssessmentRead, summary="Detall d'una avaluacio"
)
async def get_assessment(assessment_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    assessment = await _load_assessment(session, ctx, assessment_id)
    sources = await _requirement_sources(session, [assessment.requirement_id])
    reviewers = await _reviewer_names(session, [assessment])
    return _assessment_read(assessment, sources, reviewers)


@assessments_router.post(
    "/{assessment_id}/review", response_model=AssessmentRead, summary="Revisa una avaluacio"
)
async def review(
    assessment_id: uuid.UUID,
    payload: AssessmentReviewRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Registra el criteri huma sense esborrar el del motor."""
    assessment = await _load_assessment(session, ctx, assessment_id)
    updated = await review_assessment(
        session,
        assessment,
        reviewed_result=payload.reviewed_result,
        review_status=payload.review_status,
        note=payload.note,
        user_id=ctx.user.id,
    )
    sources = await _requirement_sources(session, [updated.requirement_id])
    reviewers = await _reviewer_names(session, [updated])
    return _assessment_read(updated, sources, reviewers)
