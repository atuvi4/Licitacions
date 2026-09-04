"""Pre-fit d'una empresa sobre les licitacions publiques.

A diferencia de `/tenders`, aquests endpoints SI que pengen de
`/companies/{company_id}`: un pre-fit es una dada de tenant i un usuari
d'Empresa A no pot veure els resultats d'Empresa B.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import selectinload

from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.tender_filters import TenderFilters, tender_filters
from app.core.errors import NotFoundError
from app.matching.profile import load_profile
from app.matching.rules import HIGH_FIT_THRESHOLD, RULES_VERSION
from app.matching.service import MatchingService, is_stale
from app.models.cpv import CPVCode
from app.models.enums import MatchStatus
from app.models.matching import CompanyTenderLotMatch, CompanyTenderMatch
from app.models.sources import Source
from app.models.tenders import Tender
from app.schemas.matching import (
    LotMatchRead,
    MatchSummary,
    RecalculationResult,
    TenderMatchListItem,
    TenderMatchRead,
)
from app.schemas.tenders import BuyerSummary, Page, TenderCPVRead, TenderListItem

router = APIRouter(prefix="/companies/{company_id}/matches", tags=["pre-fit"])

MatchSort = Literal["score", "deadline", "recent", "published", "amount"]


def _to_read(match: CompanyTenderMatch, stale: bool) -> TenderMatchRead:
    return TenderMatchRead(
        id=match.id,
        tender_id=match.tender_id,
        score=match.score,
        status=match.status,
        exclusion_kind=match.exclusion_kind,
        exclusion_reason_code=match.exclusion_reason_code,
        cpv_score=match.cpv_score,
        geographic_score=match.geographic_score,
        economic_score=match.economic_score,
        timing_score=match.timing_score,
        data_coverage=match.data_coverage,
        factors=match.factors,
        rules_version=match.rules_version,
        calculated_at=match.calculated_at,
        best_lot_id=match.best_lot_id,
        candidate_lot_count=match.candidate_lot_count,
        total_lot_count=match.total_lot_count,
        is_stale=stale,
        lot_matches=[
            LotMatchRead(
                id=lot_match.id,
                tender_lot_id=lot_match.tender_lot_id,
                lot_number=lot_match.lot.lot_number if lot_match.lot else None,
                lot_title=lot_match.lot.title if lot_match.lot else None,
                score=lot_match.score,
                status=lot_match.status,
                exclusion_kind=lot_match.exclusion_kind,
                exclusion_reason_code=lot_match.exclusion_reason_code,
                cpv_score=lot_match.cpv_score,
                geographic_score=lot_match.geographic_score,
                economic_score=lot_match.economic_score,
                timing_score=lot_match.timing_score,
                data_coverage=lot_match.data_coverage,
                factors=lot_match.factors,
                rules_version=lot_match.rules_version,
                calculated_at=lot_match.calculated_at,
            )
            for lot_match in sorted(
                match.lot_matches, key=lambda item: (item.score is None, -(item.score or 0))
            )
        ],
    )


@router.get("", response_model=Page[TenderMatchListItem], summary="Feed amb pre-fit")
async def list_matches(
    session: SessionDep,
    ctx: CompanyCtx,
    criteria: Annotated[TenderFilters, Depends(tender_filters)],
    match_status: list[MatchStatus] | None = Query(
        default=None, description="Estat del pre-fit (no de la licitacio)"
    ),
    min_score: int | None = Query(default=None, ge=0, le=100),
    sort: MatchSort = Query(default="score"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    # Els criteris sobre la licitacio son exactament els del cataleg public:
    # canviar de vista no ha de canviar que es cerca.
    filters = [CompanyTenderMatch.company_id == ctx.company_id, *criteria.criteria()]
    if match_status:
        filters.append(CompanyTenderMatch.status.in_(match_status))
    if min_score is not None:
        filters.append(CompanyTenderMatch.score >= min_score)

    total = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(CompanyTenderMatch)
            .join(Tender, Tender.id == CompanyTenderMatch.tender_id)
            .where(*filters)
        )
    ).scalar_one()

    # Les mateixes ordenacions que el cataleg public, mes la del pre-fit.
    order = {
        "score": (CompanyTenderMatch.score.desc().nullslast(), CompanyTenderMatch.id.desc()),
        "deadline": (Tender.deadline_at.asc().nullslast(), CompanyTenderMatch.id.desc()),
        "recent": (Tender.first_seen_at.desc(), CompanyTenderMatch.id.desc()),
        "published": (Tender.publication_date.desc().nullslast(), CompanyTenderMatch.id.desc()),
        "amount": (
            sa.func.coalesce(Tender.estimated_value, Tender.base_budget).desc().nullslast(),
            CompanyTenderMatch.id.desc(),
        ),
    }[sort]

    rows = (
        await session.execute(
            sa.select(CompanyTenderMatch)
            .join(Tender, Tender.id == CompanyTenderMatch.tender_id)
            .where(*filters)
            .options(
                selectinload(CompanyTenderMatch.lot_matches),
                selectinload(CompanyTenderMatch.tender).selectinload(Tender.cpvs),
                selectinload(CompanyTenderMatch.tender).selectinload(Tender.lots),
                selectinload(CompanyTenderMatch.tender).noload(Tender.documents),
            )
            .order_by(*order)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().unique().all()

    profile = await load_profile(session, ctx.company_id)
    codes = {item.cpv_code for row in rows for item in row.tender.cpvs}
    descriptions = dict(
        (
            await session.execute(
                sa.select(CPVCode.code, CPVCode.description).where(CPVCode.code.in_(codes))
            )
        ).all()
    ) if codes else {}
    source_codes = dict((await session.execute(sa.select(Source.id, Source.code))).all())

    items = [
        TenderMatchListItem(
            tender=TenderListItem(
                id=row.tender.id,
                external_id=row.tender.external_id,
                expediente_number=row.tender.expediente_number,
                title=row.tender.title,
                status=row.tender.status,
                procedure_type=row.tender.procedure_type,
                contract_type=row.tender.contract_type,
                estimated_value=row.tender.estimated_value,
                base_budget=row.tender.base_budget,
                currency=row.tender.currency,
                publication_date=row.tender.publication_date,
                deadline_at=row.tender.deadline_at,
                nuts_code=row.tender.nuts_code,
                place_of_execution=row.tender.place_of_execution,
                buyer=BuyerSummary.model_validate(row.tender.buyer) if row.tender.buyer else None,
                cpvs=[
                    TenderCPVRead(
                        cpv_code=item.cpv_code,
                        is_main=item.is_main,
                        description=descriptions.get(item.cpv_code),
                    )
                    for item in sorted(
                        row.tender.cpvs, key=lambda i: (not i.is_main, i.cpv_code)
                    )
                ],
                lot_count=len(row.tender.lots),
                revision_count=row.tender.revision_count,
                source_code=source_codes.get(row.tender.source_id),
                is_modified=row.tender.revision_count > 1,
            ),
            match=_to_read(
                row, is_stale(row, profile.profile_changed_at, row.tender.last_changed_at)
            ),
        )
        for row in rows
    ]
    return Page[TenderMatchListItem](items=items, total=total, limit=limit, offset=offset)


@router.get("/summary", response_model=MatchSummary, summary="Metriques del pre-fit")
async def match_summary(session: SessionDep, ctx: CompanyCtx):
    counts = dict(
        (
            await session.execute(
                sa.select(CompanyTenderMatch.status, sa.func.count())
                .where(CompanyTenderMatch.company_id == ctx.company_id)
                .group_by(CompanyTenderMatch.status)
            )
        ).all()
    )
    total = (
        await session.execute(sa.select(sa.func.count()).select_from(Tender))
    ).scalar_one()
    high_fit = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(CompanyTenderMatch)
            .where(
                CompanyTenderMatch.company_id == ctx.company_id,
                CompanyTenderMatch.score >= HIGH_FIT_THRESHOLD,
            )
        )
    ).scalar_one()
    last = (
        await session.execute(
            sa.select(sa.func.max(CompanyTenderMatch.calculated_at)).where(
                CompanyTenderMatch.company_id == ctx.company_id
            )
        )
    ).scalar_one_or_none()

    profile = await load_profile(session, ctx.company_id)
    stale = 0
    if profile.profile_changed_at is not None:
        stale = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(CompanyTenderMatch)
                .where(
                    CompanyTenderMatch.company_id == ctx.company_id,
                    sa.or_(
                        CompanyTenderMatch.company_profile_changed_at.is_(None),
                        CompanyTenderMatch.company_profile_changed_at < profile.profile_changed_at,
                        CompanyTenderMatch.rules_version != RULES_VERSION,
                    ),
                )
            )
        ).scalar_one()

    def count_of(status: MatchStatus) -> int:
        return int(counts.get(status, 0))

    return MatchSummary(
        total_tenders=total,
        actionable=count_of(MatchStatus.CANDIDATE) + count_of(MatchStatus.LOW_FIT),
        candidates=count_of(MatchStatus.CANDIDATE),
        high_fit=high_fit,
        low_fit=count_of(MatchStatus.LOW_FIT),
        excluded=count_of(MatchStatus.EXCLUDED),
        closed=count_of(MatchStatus.CLOSED),
        insufficient_data=count_of(MatchStatus.INSUFFICIENT_DATA),
        stale=stale,
        rules_version=RULES_VERSION,
        last_calculated_at=last,
    )


@router.get(
    "/by-tender/{tender_id}", response_model=TenderMatchRead, summary="Pre-fit d'una licitacio"
)
async def match_for_tender(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    match = (
        await session.execute(
            sa.select(CompanyTenderMatch)
            .where(
                CompanyTenderMatch.company_id == ctx.company_id,
                CompanyTenderMatch.tender_id == tender_id,
            )
            .options(
                selectinload(CompanyTenderMatch.lot_matches).joinedload(CompanyTenderLotMatch.lot),
                selectinload(CompanyTenderMatch.tender).noload(Tender.documents),
            )
        )
    ).scalars().unique().one_or_none()
    if match is None:
        raise NotFoundError("Aquesta licitacio encara no te pre-fit calculat.")

    profile = await load_profile(session, ctx.company_id)
    return _to_read(match, is_stale(match, profile.profile_changed_at, match.tender.last_changed_at))


@router.get("/{match_id}", response_model=TenderMatchRead, summary="Detall d'un pre-fit")
async def get_match(match_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    match = (
        await session.execute(
            sa.select(CompanyTenderMatch)
            .where(
                CompanyTenderMatch.id == match_id,
                # El filtre per empresa no es opcional: sense ell, coneixent un
                # UUID es podria llegir el pre-fit d'una altra empresa.
                CompanyTenderMatch.company_id == ctx.company_id,
            )
            .options(
                selectinload(CompanyTenderMatch.lot_matches).joinedload(CompanyTenderLotMatch.lot),
                selectinload(CompanyTenderMatch.tender).noload(Tender.documents),
            )
        )
    ).scalars().unique().one_or_none()
    if match is None:
        raise NotFoundError("Pre-fit no trobat.")

    profile = await load_profile(session, ctx.company_id)
    return _to_read(match, is_stale(match, profile.profile_changed_at, match.tender.last_changed_at))


@router.post(
    "/recalculate", response_model=RecalculationResult, summary="Recalcula el pre-fit"
)
async def recalculate(
    session: SessionDep,
    ctx: CompanyCtx,
    tender_id: uuid.UUID | None = Query(default=None, description="Nomes aquesta licitacio."),
):
    summary = await MatchingService(session).recalculate(
        ctx.company_id, tender_ids=[tender_id] if tender_id else None
    )
    return RecalculationResult(
        tenders_evaluated=summary.tenders_evaluated,
        lots_evaluated=summary.lots_evaluated,
        created=summary.created,
        updated=summary.updated,
        duration_ms=summary.duration_ms,
        rules_version=summary.rules_version,
    )
