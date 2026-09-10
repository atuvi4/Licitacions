"""Feed public de licitacions.

Aquests endpoints NO pengen de `/companies/{company_id}`: les licitacions son
dades publiques i globals, compartides per tots els tenants. Nomes cal sessio
iniciada, no pertinenca a cap empresa.

En aquesta fase no hi ha cap relacio empresa-licitacio: ni matching, ni
puntuacio, ni Go/No-Go. Nomes el que publica la font.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import noload, selectinload

from app.api.deps import CurrentUser, SessionDep
from app.api.v1.tender_filters import TenderFilters, tender_filters
from app.core.errors import NotFoundError
from app.models.cpv import CPVCode
from app.models.sources import IngestionRun, Source, SourceRecord
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
)
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Buyer, Tender, TenderLot
from app.schemas.tenders import (
    BuyerRead,
    BuyerSummary,
    IngestionRunRead,
    Page,
    TenderChangeEventRead,
    TenderCPVRead,
    TenderDetail,
    TenderDocumentFileSummary,
    TenderDocumentRead,
    TenderFacets,
    TenderHistoryRevisionRead,
    TenderListItem,
    TenderLotRead,
    TenderRevisionRead,
    TenderTraceability,
)

router = APIRouter(tags=["licitacions"])

SortOption = Literal["recent", "deadline", "amount", "published"]


def _cpv_reads(tender: Tender, descriptions: dict[str, str]) -> list[TenderCPVRead]:
    return [
        TenderCPVRead(
            cpv_code=item.cpv_code,
            is_main=item.is_main,
            description=descriptions.get(item.cpv_code),
        )
        for item in sorted(tender.cpvs, key=lambda item: (not item.is_main, item.cpv_code))
    ]


async def _cpv_descriptions(session, codes: set[str]) -> dict[str, str]:
    if not codes:
        return {}
    rows = await session.execute(
        sa.select(CPVCode.code, CPVCode.description).where(CPVCode.code.in_(codes))
    )
    return {code: description for code, description in rows}


@router.get("/tenders", response_model=Page[TenderListItem], summary="Feed de licitacions")
async def list_tenders(
    session: SessionDep,
    _: CurrentUser,
    criteria: Annotated[TenderFilters, Depends(tender_filters)],
    sort: SortOption = Query(default="recent"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    filters = criteria.criteria()

    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Tender).where(*filters)
        )
    ).scalar_one()

    order = {
        "recent": (Tender.first_seen_at.desc(), Tender.id.desc()),
        "published": (Tender.publication_date.desc().nullslast(), Tender.id.desc()),
        "deadline": (Tender.deadline_at.asc().nullslast(), Tender.id.desc()),
        "amount": (
            sa.func.coalesce(Tender.estimated_value, Tender.base_budget).desc().nullslast(),
            Tender.id.desc(),
        ),
    }[sort]

    rows = (
        await session.execute(
            sa.select(Tender)
            .where(*filters)
            # El feed no necessita els documents: evitem carregar-los.
            .options(
                selectinload(Tender.cpvs),
                selectinload(Tender.lots).noload(TenderLot.cpvs),
                noload(Tender.documents),
            )
            .order_by(*order)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().unique().all()

    codes = {item.cpv_code for tender in rows for item in tender.cpvs}
    descriptions = await _cpv_descriptions(session, codes)
    source_codes = {
        source_id: code
        for source_id, code in await session.execute(sa.select(Source.id, Source.code))
    }

    items = [
        TenderListItem(
            id=tender.id,
            external_id=tender.external_id,
            expediente_number=tender.expediente_number,
            title=tender.title,
            status=tender.status,
            procedure_type=tender.procedure_type,
            contract_type=tender.contract_type,
            estimated_value=tender.estimated_value,
            base_budget=tender.base_budget,
            currency=tender.currency,
            publication_date=tender.publication_date,
            deadline_at=tender.deadline_at,
            nuts_code=tender.nuts_code,
            place_of_execution=tender.place_of_execution,
            buyer=BuyerSummary.model_validate(tender.buyer) if tender.buyer else None,
            cpvs=_cpv_reads(tender, descriptions),
            lot_count=len(tender.lots),
            revision_count=tender.revision_count,
            source_code=source_codes.get(tender.source_id),
            is_modified=tender.revision_count > 1,
        )
        for tender in rows
    ]
    return Page[TenderListItem](items=items, total=total, limit=limit, offset=offset)


@router.get("/tenders/facets", response_model=TenderFacets, summary="Valors per als filtres")
async def tender_facets(session: SessionDep, _: CurrentUser):
    async def distinct(column) -> list[str]:
        rows = await session.execute(
            sa.select(column).where(column.is_not(None)).distinct().order_by(column)
        )
        return [str(getattr(value, "value", value)) for value in rows.scalars()]

    total = (await session.execute(sa.select(sa.func.count()).select_from(Tender))).scalar_one()
    return TenderFacets(
        statuses=await distinct(Tender.status),
        procedure_types=await distinct(Tender.procedure_type),
        contract_types=await distinct(Tender.contract_type),
        nuts_codes=await distinct(Tender.nuts_code),
        total_tenders=total,
    )


async def _load_tender(session, tender_id: uuid.UUID) -> Tender:
    tender = (
        await session.execute(
            sa.select(Tender)
            .where(Tender.id == tender_id)
            .options(
                selectinload(Tender.cpvs),
                selectinload(Tender.lots).selectinload(TenderLot.cpvs),
                selectinload(Tender.documents),
            )
        )
    ).scalars().unique().one_or_none()
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


async def _document_summaries(
    session, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, TenderDocumentFileSummary]:
    """Resum de l'estat documental de cada document, en una sola consulta.

    Nomes la versio vigent: l'historial de versions es demana explicitament a
    `/documents/{id}/files`.
    """
    if not document_ids:
        return {}

    rows = await session.execute(
        sa.select(TenderDocumentFile, TenderDocumentContent, TenderDocumentExtraction)
        .outerjoin(
            TenderDocumentContent, TenderDocumentContent.id == TenderDocumentFile.content_id
        )
        .outerjoin(
            TenderDocumentExtraction,
            sa.and_(
                TenderDocumentExtraction.content_id == TenderDocumentFile.content_id,
                TenderDocumentExtraction.is_current.is_(True),
            ),
        )
        .where(
            TenderDocumentFile.tender_document_id.in_(document_ids),
            TenderDocumentFile.is_current.is_(True),
        )
    )

    summaries: dict[uuid.UUID, TenderDocumentFileSummary] = {}
    for file, content, extraction in rows:
        summaries[file.tender_document_id] = TenderDocumentFileSummary(
            file_id=file.id,
            version_number=file.version_number,
            download_status=file.download_status,
            file_size=content.file_size if content else None,
            sha256=content.sha256 if content else None,
            extraction_status=extraction.status if extraction else None,
            quality=extraction.quality if extraction else None,
            page_count=extraction.page_count if extraction else None,
            text_char_count=extraction.text_char_count if extraction else None,
            ocr_candidate=bool(extraction.ocr_candidate) if extraction else False,
            extraction_id=extraction.id if extraction else None,
            processed_at=file.downloaded_at,
            error_code=file.error_code or (extraction.error_code if extraction else None),
        )
    return summaries


@router.get("/tenders/{tender_id}", response_model=TenderDetail, summary="Fitxa de licitacio")
async def get_tender(tender_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    tender = await _load_tender(session, tender_id)

    codes = {item.cpv_code for item in tender.cpvs}
    for lot in tender.lots:
        codes.update(item.cpv_code for item in lot.cpvs)
    descriptions = await _cpv_descriptions(session, codes)

    source = (
        await session.execute(sa.select(Source).where(Source.id == tender.source_id))
    ).scalar_one()
    record = None
    if tender.latest_source_record_id:
        record = (
            await session.execute(
                sa.select(SourceRecord).where(SourceRecord.id == tender.latest_source_record_id)
            )
        ).scalar_one_or_none()

    summaries = await _document_summaries(session, [doc.id for doc in tender.documents])

    return TenderDetail(
        id=tender.id,
        external_id=tender.external_id,
        expediente_number=tender.expediente_number,
        title=tender.title,
        description=tender.description,
        status=tender.status,
        source_status_code=tender.source_status_code,
        procedure_type=tender.procedure_type,
        contract_type=tender.contract_type,
        estimated_value=tender.estimated_value,
        base_budget=tender.base_budget,
        base_budget_without_tax=tender.base_budget_without_tax,
        currency=tender.currency,
        publication_date=tender.publication_date,
        deadline_at=tender.deadline_at,
        place_of_execution=tender.place_of_execution,
        nuts_code=tender.nuts_code,
        country_code=tender.country_code,
        source_url=tender.source_url,
        first_seen_at=tender.first_seen_at,
        last_seen_at=tender.last_seen_at,
        last_changed_at=tender.last_changed_at,
        revision_count=tender.revision_count,
        buyer=BuyerRead.model_validate(tender.buyer) if tender.buyer else None,
        cpvs=_cpv_reads(tender, descriptions),
        lots=[
            TenderLotRead(
                id=lot.id,
                external_lot_id=lot.external_lot_id,
                lot_number=lot.lot_number,
                title=lot.title,
                description=lot.description,
                estimated_value=lot.estimated_value,
                base_budget=lot.base_budget,
                base_budget_without_tax=lot.base_budget_without_tax,
                currency=lot.currency,
                place_of_execution=lot.place_of_execution,
                nuts_code=lot.nuts_code,
                deadline_at=lot.deadline_at,
                cpvs=[
                    TenderCPVRead(
                        cpv_code=item.cpv_code,
                        is_main=item.is_main,
                        description=descriptions.get(item.cpv_code),
                    )
                    for item in sorted(lot.cpvs, key=lambda i: (not i.is_main, i.cpv_code))
                ],
            )
            for lot in tender.lots
        ],
        documents=[
            TenderDocumentRead.model_validate(document).model_copy(
                update={"latest_file": summaries.get(document.id)}
            )
            for document in tender.documents
        ],
        traceability=TenderTraceability(
            source_code=source.code,
            source_name=source.name,
            external_id=tender.external_id,
            source_url=tender.source_url,
            latest_source_record_id=record.id if record else None,
            fetched_at=record.fetched_at if record else None,
            parser_version=record.parser_version if record else None,
            raw_hash=record.raw_hash if record else None,
        ),
    )


@router.get(
    "/tenders/{tender_id}/lots", response_model=list[TenderLotRead], summary="Lots de la licitacio"
)
async def list_tender_lots(tender_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    tender = await _load_tender(session, tender_id)
    codes = {item.cpv_code for lot in tender.lots for item in lot.cpvs}
    descriptions = await _cpv_descriptions(session, codes)
    return [
        TenderLotRead(
            id=lot.id,
            external_lot_id=lot.external_lot_id,
            lot_number=lot.lot_number,
            title=lot.title,
            description=lot.description,
            estimated_value=lot.estimated_value,
            base_budget=lot.base_budget,
            base_budget_without_tax=lot.base_budget_without_tax,
            currency=lot.currency,
            place_of_execution=lot.place_of_execution,
            nuts_code=lot.nuts_code,
            deadline_at=lot.deadline_at,
            cpvs=[
                TenderCPVRead(
                    cpv_code=item.cpv_code,
                    is_main=item.is_main,
                    description=descriptions.get(item.cpv_code),
                )
                for item in lot.cpvs
            ],
        )
        for lot in tender.lots
    ]


@router.get(
    "/tenders/{tender_id}/documents",
    response_model=list[TenderDocumentRead],
    summary="Documents publicats (nomes metadata i URL)",
)
async def list_tender_documents(tender_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    tender = await _load_tender(session, tender_id)
    return [TenderDocumentRead.model_validate(document) for document in tender.documents]


@router.get(
    "/tenders/{tender_id}/revisions",
    response_model=list[TenderRevisionRead],
    summary="Historial de versions rebudes",
)
async def list_tender_revisions(tender_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    await _load_tender(session, tender_id)
    rows = (
        await session.execute(
            sa.select(SourceRecord)
            .where(SourceRecord.tender_id == tender_id)
            .order_by(SourceRecord.revision_number.desc())
        )
    ).scalars().all()
    return [TenderRevisionRead.model_validate(record) for record in rows]


@router.get("/buyers", response_model=Page[BuyerSummary], summary="Organismes contractants")
async def list_buyers(
    session: SessionDep,
    _: CurrentUser,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    filters = []
    if q:
        filters.append(sa.func.lower(Buyer.name).like(f"%{q.lower()}%"))
    total = (
        await session.execute(sa.select(sa.func.count()).select_from(Buyer).where(*filters))
    ).scalar_one()
    rows = (
        await session.execute(
            sa.select(Buyer).where(*filters).order_by(Buyer.name).limit(limit).offset(offset)
        )
    ).scalars().all()
    return Page[BuyerSummary](
        items=[BuyerSummary.model_validate(buyer) for buyer in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/buyers/{buyer_id}", response_model=BuyerRead, summary="Organisme contractant")
async def get_buyer(buyer_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    buyer = (
        await session.execute(sa.select(Buyer).where(Buyer.id == buyer_id))
    ).scalar_one_or_none()
    if buyer is None:
        raise NotFoundError("Organisme no trobat.")
    return buyer


@router.get(
    "/ingestion/runs",
    response_model=Page[IngestionRunRead],
    summary="Historial d'execucions d'ingesta",
)
async def list_ingestion_runs(
    session: SessionDep,
    _: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total = (
        await session.execute(sa.select(sa.func.count()).select_from(IngestionRun))
    ).scalar_one()
    rows = (
        await session.execute(
            sa.select(IngestionRun)
            .order_by(IngestionRun.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return Page[IngestionRunRead](
        items=[IngestionRunRead.model_validate(run) for run in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/tenders/{tender_id}/history",
    response_model=list[TenderHistoryRevisionRead],
    summary="Historial d'estats normalitzats d'una licitacio",
)
async def tender_history(tender_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    """Com hem arribat a l'estat actual, de la mes recent a la mes antiga.

    Es public i global, com la licitacio: la historia d'un expedient no pertany
    a cap empresa. Els impactes sobre un workspace concret s'exposen al
    workspace, que si que es del tenant.
    """
    await _load_tender(session, tender_id)
    revisions = (
        await session.scalars(
            sa.select(TenderRevision)
            .options(selectinload(TenderRevision.events))
            .where(TenderRevision.tender_id == tender_id)
            .order_by(TenderRevision.revision_number.desc())
        )
    ).all()
    return list(revisions)


@router.get(
    "/tenders/{tender_id}/changes",
    response_model=list[TenderChangeEventRead],
    summary="Canvis publicats d'una licitacio",
)
async def tender_changes(
    tender_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    material_only: bool = True,
):
    """Els canvis, del mes recent al mes antic.

    Per defecte nomes els materials: un canvi de titol no invalida res i
    barrejar-lo amb una substitucio de plec faria que la llista deixes de
    llegir-se. `material_only=false` els ensenya tots.
    """
    await _load_tender(session, tender_id)
    query = (
        sa.select(TenderChangeEvent)
        .where(TenderChangeEvent.tender_id == tender_id)
        .order_by(TenderChangeEvent.detected_at.desc(), TenderChangeEvent.change_code)
    )
    if material_only:
        query = query.where(TenderChangeEvent.is_material.is_(True))
    return list((await session.scalars(query)).all())
