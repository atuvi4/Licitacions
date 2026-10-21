"""Pre-fit d'una empresa sobre les licitacions publiques.

A diferencia de `/tenders`, aquests endpoints SI que pengen de
`/companies/{company_id}`: un pre-fit es una dada de tenant i un usuari
d'Empresa A no pot veure els resultats d'Empresa B.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import noload, selectinload

from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.tender_filters import (
    DEFAULT_SORT,
    CatalogueSort,
    TenderFilters,
    catalogue_order,
    list_item,
    load_row_context,
    tender_filters,
)
from app.core.errors import NotFoundError
from app.matching.profile import load_profile
from app.matching.rules import HIGH_FIT_THRESHOLD, RULES_VERSION
from app.matching.service import MatchingService, is_stale
from app.models.cpv import CompanyCPVPreference
from app.models.enums import MatchStatus
from app.models.matching import CompanyTenderLotMatch, CompanyTenderMatch
from app.models.profile import CompanyContractPreferences, CompanyTerritory
from app.models.tenders import Tender
from app.models.workspace import BidWorkspace
from app.schemas.matching import (
    LotMatchRead,
    MatchSummary,
    PrefitProfileCounts,
    RecalculationResult,
    TenderMatchListItem,
    TenderMatchRead,
)
from app.schemas.tenders import CataloguePage
from app.services.bid_workspace import CLOSED_WORKSPACE_STATUSES
from app.tenders.visibility import visible_to_company

router = APIRouter(prefix="/companies/{company_id}/matches", tags=["pre-fit"])


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


def _open_workspace_exists(company_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """La licitacio ja te una candidatura OBERTA d'aquesta empresa (no tancada)."""
    return (
        sa.select(BidWorkspace.id)
        .where(
            BidWorkspace.tender_id == Tender.id,
            BidWorkspace.company_id == company_id,
            BidWorkspace.status.not_in(CLOSED_WORKSPACE_STATUSES),
        )
        .exists()
    )


@dataclass(frozen=True, slots=True)
class _FeedQuery:
    """La definicio del feed amb pre-fit: un sol constructor per a la llista i el resum.

    Els criteris sobre la licitacio son exactament els del cataleg public;
    `view_filters` son els de la vista (estat i puntuacio del pre-fit, i
    l'exclusio de les que ja tenen candidatura). Comptar i llistar parteixen
    d'aqui perque la definicio no pugui divergir.
    """

    company_id: uuid.UUID
    criteria: TenderFilters
    view_filters: tuple[sa.ColumnElement[bool], ...]
    status_view: bool

    def where(self, *, without_amount: bool = False) -> list:
        tender_criteria = (
            self.criteria.without_amount_criteria() if without_amount else self.criteria.criteria()
        )
        return [visible_to_company(self.company_id), *tender_criteria, *self.view_filters]

    def scoped(self, select: sa.Select) -> sa.Select:
        same_company = sa.and_(
            CompanyTenderMatch.tender_id == Tender.id,
            CompanyTenderMatch.company_id == self.company_id,
        )
        select = select.select_from(Tender)
        if self.status_view:
            return select.join(CompanyTenderMatch, same_company)
        return select.outerjoin(CompanyTenderMatch, same_company)


def _feed_query(
    company_id: uuid.UUID,
    criteria: TenderFilters,
    match_status: list[MatchStatus] | None,
    min_score: int | None,
    *,
    exclude_open_workspaces: bool = False,
) -> _FeedQuery:
    # **«Totes» son les licitacions visibles, no els pre-fits.** Sense filtre de
    # pre-fit, l'univers es el mateix que compta `summary.total_tenders`: cada
    # licitacio visible per a l'empresa, amb el seu pre-fit AL COSTAT si n'hi ha
    # (`match` nul si encara no s'ha calculat). Abans la llista partia dels
    # pre-fits: una empresa sense cap pre-fit calculat veia «Totes 9» i una llista
    # buida. Les vistes per estat o per puntuacio si que parteixen del pre-fit.
    view_filters: list[sa.ColumnElement[bool]] = []
    if match_status:
        view_filters.append(CompanyTenderMatch.status.in_(match_status))
    if min_score is not None:
        view_filters.append(CompanyTenderMatch.score >= min_score)
    if exclude_open_workspaces:
        view_filters.append(sa.not_(_open_workspace_exists(company_id)))
    return _FeedQuery(
        company_id=company_id,
        criteria=criteria,
        view_filters=tuple(view_filters),
        status_view=bool(match_status) or min_score is not None,
    )


#: El filtre de «Candidates del Radar» d'Avui: candidates amb termini obert.
AVUI_CANDIDATE_STATUSES = [MatchStatus.CANDIDATE]


@router.get(
    "", response_model=CataloguePage[TenderMatchListItem], summary="Feed amb pre-fit"
)
async def list_matches(
    session: SessionDep,
    ctx: CompanyCtx,
    criteria: Annotated[TenderFilters, Depends(tender_filters)],
    match_status: list[MatchStatus] | None = Query(
        default=None, description="Estat del pre-fit (no de la licitacio)"
    ),
    min_score: int | None = Query(default=None, ge=0, le=100),
    exclude_open_workspaces: bool = Query(
        default=False,
        description="Sense les licitacions que ja tenen una candidatura oberta de l'empresa",
    ),
    sort: CatalogueSort = Query(default=DEFAULT_SORT),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    # Els criteris sobre la licitacio son exactament els del cataleg public:
    # canviar de vista no ha de canviar que es cerca.
    feed = _feed_query(
        ctx.company_id,
        criteria,
        match_status,
        min_score,
        exclude_open_workspaces=exclude_open_workspaces,
    )
    scoped = feed.scoped
    filters = feed.where()

    total = (
        await session.execute(scoped(sa.select(sa.func.count())).where(*filters))
    ).scalar_one()
    # Nomes amb un filtre d'import fixat: quantes, amb la resta de filtres (i la
    # mateixa vista de pre-fit), no es mostren nomes per no tenir import.
    excluded_without_amount = None
    if criteria.filters_amount:
        without_amount = feed.where(without_amount=True)
        excluded_without_amount = (
            await session.execute(scoped(sa.select(sa.func.count())).where(*without_amount))
        ).scalar_one()

    # Les mateixes ordenacions que el cataleg public, mes la del pre-fit, sempre
    # amb el desempat neutral. Sense pre-fit, la puntuacio va al final (una
    # empresa te com a molt un pre-fit per licitacio).
    order = catalogue_order(
        sort,
        criteria.q,
        dialect_name=session.bind.dialect.name,
        score=CompanyTenderMatch.score,
    )

    rows = (
        await session.execute(
            scoped(sa.select(Tender, CompanyTenderMatch))
            .where(*filters)
            .options(
                selectinload(CompanyTenderMatch.lot_matches),
                selectinload(Tender.cpvs),
                selectinload(Tender.lots),
                noload(Tender.documents),
            )
            .order_by(*order)
            .limit(limit)
            .offset(offset)
        )
    ).unique().all()

    profile = await load_profile(session, ctx.company_id)
    context = await load_row_context(session, [tender for tender, _match in rows])

    items = [
        TenderMatchListItem(
            tender=list_item(tender, context),
            match=(
                _to_read(match, is_stale(match, profile.profile_changed_at, tender.last_changed_at))
                if match is not None
                else None
            ),
        )
        for tender, match in rows
    ]
    return CataloguePage[TenderMatchListItem](
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        excluded_without_amount=excluded_without_amount,
    )


@router.get("/summary", response_model=MatchSummary, summary="Metriques del pre-fit")
async def match_summary(session: SessionDep, ctx: CompanyCtx):
    # Recomptes per estat nomes sobre licitacions VISIBLES: el mateix univers
    # que `total_tenders`, perque `evaluated_count` i els estats es puguin
    # comparar entre si (p. ex. «totes les avaluades tenen dades insuficients»).
    counts = dict(
        (
            await session.execute(
                sa.select(CompanyTenderMatch.status, sa.func.count())
                .join(Tender, Tender.id == CompanyTenderMatch.tender_id)
                .where(
                    CompanyTenderMatch.company_id == ctx.company_id,
                    visible_to_company(ctx.company_id),
                )
                .group_by(CompanyTenderMatch.status)
            )
        ).all()
    )
    # Quantes licitacions visibles tenen pre-fit calculat. Amb 0, el Radar
    # encara no s'ha calculat: la UI no pot dir «cap coincidencia» ni culpar
    # el perfil (revisio 3B-2, F1/F3). Mai es dedueix del perfil.
    evaluated = sum(int(value) for value in counts.values())
    # L'univers del mode "Totes". Sense el filtre comptaria tambe les
    # licitacions privades d'altres empreses, i el comptador delataria el que
    # la llista amaga.
    total = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(Tender)
            .where(visible_to_company(ctx.company_id))
        )
    ).scalar_one()
    high_fit = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(CompanyTenderMatch)
            .join(Tender, Tender.id == CompanyTenderMatch.tender_id)
            .where(
                CompanyTenderMatch.company_id == ctx.company_id,
                visible_to_company(ctx.company_id),
                CompanyTenderMatch.score >= HIGH_FIT_THRESHOLD,
                # v3: una puntuacio alta ja no vol dir candidata (minim de CPV).
                CompanyTenderMatch.status == MatchStatus.CANDIDATE,
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
                .join(Tender, Tender.id == CompanyTenderMatch.tender_id)
                .where(
                    CompanyTenderMatch.company_id == ctx.company_id,
                    # El mateix univers que la resta del resum: nomes visibles.
                    visible_to_company(ctx.company_id),
                    sa.or_(
                        CompanyTenderMatch.company_profile_changed_at.is_(None),
                        CompanyTenderMatch.company_profile_changed_at < profile.profile_changed_at,
                        CompanyTenderMatch.rules_version != RULES_VERSION,
                    ),
                )
            )
        ).scalar_one()

    # Una sola sentencia per a la resta (P2-09). Els tres recomptes del perfil son
    # la definicio de `build_summary` (`dashboard._count`): files de cada taula de
    # l'empresa, sense cap altre filtre. Les candidates amb candidatura surten del
    # MATEIX constructor que la llista d'Avui (`match_status=candidate`, `only_open`).
    def company_rows(model) -> sa.ScalarSelect:
        return (
            sa.select(sa.func.count())
            .select_from(model)
            .where(model.company_id == ctx.company_id)
            .scalar_subquery()
        )

    avui = _feed_query(
        ctx.company_id, TenderFilters(only_open=True), AVUI_CANDIDATE_STATUSES, None
    )
    with_workspace = (
        avui.scoped(sa.select(sa.func.count()))
        .where(*avui.where(), _open_workspace_exists(ctx.company_id))
        .scalar_subquery()
    )
    extra = (
        await session.execute(
            sa.select(
                company_rows(CompanyCPVPreference).label("cpv_preferences"),
                company_rows(CompanyTerritory).label("territories"),
                company_rows(CompanyContractPreferences).label("contract_preferences"),
                with_workspace.label("open_candidates_with_workspace"),
            )
        )
    ).one()

    def count_of(status: MatchStatus) -> int:
        return int(counts.get(status, 0))

    return MatchSummary(
        total_tenders=total,
        evaluated_count=evaluated,
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
        profile_counts=PrefitProfileCounts(
            cpv_preferences=int(extra.cpv_preferences or 0),
            territories=int(extra.territories or 0),
            contract_preferences=int(extra.contract_preferences or 0),
        ),
        open_candidates_with_workspace=int(extra.open_candidates_with_workspace or 0),
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
