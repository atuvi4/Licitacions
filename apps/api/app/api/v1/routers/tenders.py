"""Feed public de licitacions.

Aquests endpoints NO pengen de `/companies/{company_id}`. La majoria de
licitacions son publiques i globals, compartides per tots els tenants, i nomes
calia sessio iniciada. Des d'S3-S0 n'hi pot haver de privades d'una empresa, i
com que la ruta no diu de quina empresa es parla, el filtre es fa per USUARI:
veu el que es public i el que pertany a empreses de les quals es membre
(`visible_to_user`). No s'hi inventa cap empresa.

En aquesta fase no hi ha cap relacio empresa-licitacio: ni matching, ni
puntuacio, ni Go/No-Go. Nomes el que publica la font.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.orm import noload, selectinload

from app.analysis.fingerprint import coverage_gaps, processing_gaps
from app.api.deps import CompanyCtx, CurrentUser, SessionDep
from app.api.v1.tender_filters import (
    DEFAULT_SORT,
    CatalogueSort,
    TenderFilters,
    catalogue_order,
    list_item,
    load_row_context,
    tender_filters,
)
from app.core.config import settings
from app.core.errors import NotFoundError, PayloadTooLargeError
from app.matching.service import MatchingService
from app.models.audit import AuditAction
from app.models.cpv import CPVCode
from app.models.enums import TenderDocumentType
from app.models.sources import IngestionRun, Source, SourceRecord
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
)
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Buyer, Tender, TenderLot
from app.schemas.tender_structure import TenderBlockRead, TenderSiteRead
from app.schemas.tenders import (
    BuyerRead,
    BuyerSummary,
    CataloguePage,
    IngestionRunRead,
    ManualTenderCreate,
    ManualTenderCreateResult,
    ManualTenderUpdate,
    ManualTenderUpdateResult,
    Page,
    SourceFacet,
    TenderChangeEventRead,
    TenderCPVRead,
    TenderDetail,
    TenderDocumentFileSummary,
    TenderDocumentRead,
    TenderDocumentUploadResult,
    TenderFacets,
    TenderHistoryRevisionRead,
    TenderListItem,
    TenderLotRead,
    TenderPackageGapRead,
    TenderPackageRead,
    TenderRevisionRead,
    TenderTraceability,
    analysis_status_for,
)
from app.services import audit, manual_tender, tender_document_upload
from app.services.tender_analysis import load_structure_view
from app.services.tender_document_processing import is_text_extractable_mime
from app.tenders import amount_observations
from app.tenders.documents import is_active_document
from app.tenders.visibility import (
    buyer_visible_to_company,
    visible_to_company,
    visible_to_user,
)

router = APIRouter(tags=["licitacions"])


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


_UPLOAD_CHUNK = 1024 * 1024


async def _read_tender_upload(file: UploadFile) -> bytes:
    """Llegeix el fitxer amb tall dur de mida, sense confiar en Content-Length.

    El limit es el dels plecs (`DOCUMENT_MAX_SIZE_MB`) i no el de la biblioteca
    documental: son dues coses diferents amb dos contractes diferents.
    """
    limit = settings.document_max_size_bytes
    buffer = bytearray()
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise PayloadTooLargeError(
                f"El fitxer supera el maxim permes de {settings.DOCUMENT_MAX_SIZE_MB} MB."
            )
    return bytes(buffer)

@router.post(
    "/companies/{company_id}/tenders",
    response_model=ManualTenderCreateResult,
    status_code=status.HTTP_201_CREATED,
    summary="Obre una licitacio a ma",
)
async def create_manual_tender(
    payload: ManualTenderCreate, session: SessionDep, ctx: CompanyCtx, response: Response
):
    """Idempotent pel `client_token`: un reintent recupera la que ja hi havia.

    Quan la troba respon **200** en comptes de 201, perque no ha creat res. Si
    el mateix token torna amb una licitacio materialment diferent —altre titol,
    altre expedient, altre organisme— no se sobreescriu: es un `409`.

    Despres de crear-la se'n recalcula el pre-fit **nomes d'aquesta licitacio**,
    que es el que la fa apareixer a Oportunitats. Si en surt `insufficient_data`
    es correcte: desconegut no es «no».
    """
    manual_tender.assert_can_create(ctx.membership.role)
    buyer = payload.buyer_manual
    tender, created = await manual_tender.create_manual_tender(
        session,
        company_id=ctx.company_id,
        user=ctx.user,
        data=manual_tender.ManualTenderInput(
            client_token=payload.client_token,
            title=payload.title,
            expediente_number=payload.expediente_number,
            buyer_id=payload.buyer_id,
            buyer_manual=(
                manual_tender.ManualBuyerInput(
                    client_token=buyer.client_token,
                    name=buyer.name,
                    tax_identifier=buyer.tax_identifier,
                    nuts_code=buyer.nuts_code,
                )
                if buyer is not None
                else None
            ),
        ),
    )

    if created:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.CREATE,
            entity_type="tender",
            entity_id=tender.id,
            summary=manual_tender.audit_summary(
                licitacio=tender.id, expedient=tender.expediente_number
            ),
        )
        await MatchingService(session).recalculate(ctx.company_id, tender_ids=[tender.id])
        await session.commit()
    else:
        response.status_code = status.HTTP_200_OK

    return ManualTenderCreateResult(
        created=created, tender=await _tender_detail(session, tender.id, ctx.user.id)
    )


@router.patch(
    "/companies/{company_id}/tenders/{tender_id}",
    response_model=ManualTenderUpdateResult,
    summary="Corregeix una licitacio oberta a ma",
)
async def update_manual_tender(
    tender_id: uuid.UUID,
    payload: ManualTenderUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Nomes una licitacio manual i de la propia empresa.

    **Un paquet congelat o aprovat NO bloqueja l'edicio** (C-12). El que passa
    es el que ja passava quan l'organ publicava un canvi: es desa una revisio
    nova i `evaluate_staleness` dira `tender_changed`. L'aprovacio anterior
    queda intacta; el servidor no la converteix en un pany.

    S'aplica **nomes el que arriba**: ometre un camp no el toca i enviar-hi
    `null` l'esborra. Si els valors ja eren aquests no es crea cap revisio i
    `revised` es `False`; qualsevol escriptura real en crea una i fa pujar el
    testimoni, sigui el canvi material o informatiu.
    """
    manual_tender.assert_can_edit(ctx.membership.role)
    tender = await manual_tender.load_editable_tender(session, ctx.company_id, tender_id)

    sent = payload.model_fields_set
    fields = {
        name: getattr(payload, name)
        for name in manual_tender.EDITABLE_FIELDS
        if name in sent
    }
    buyer = payload.buyer_manual
    tender, revised = await manual_tender.update_manual_tender(
        session,
        tender=tender,
        company_id=ctx.company_id,
        expected_revision=payload.expected_revision,
        fields=fields,
        buyer_id=payload.buyer_id if "buyer_id" in sent else manual_tender.UNSET,
        buyer_manual=(
            manual_tender.ManualBuyerInput(
                client_token=buyer.client_token,
                name=buyer.name,
                tax_identifier=buyer.tax_identifier,
                nuts_code=buyer.nuts_code,
            )
            if buyer is not None
            else None
        ),
    )

    if revised:
        # L'organisme no es un camp del formulari pero si una correccio, i qui
        # llegeixi l'auditoria ha de poder saber que es va tocar.
        touched = sorted(fields)
        if "buyer_id" in sent or buyer is not None:
            touched.append("buyer")
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="tender",
            entity_id=tender.id,
            summary=manual_tender.audit_summary(
                licitacio=tender.id, camps=",".join(touched) or "-"
            ),
        )
        # Un canvi material pot moure el pre-fit. Nomes aquesta licitacio.
        await MatchingService(session).recalculate(ctx.company_id, tender_ids=[tender.id])

    # Es desa SEMPRE. Sense revisio no hi ha res a desar mes enlla del que ja
    # hi havia, pero tancar la sessio sense `commit` deixaria la peticio sense
    # efecte i no val la pena dependre de quin cami hi ha arribat.
    await session.commit()
    # La fitxa es torna a llegir i l'organisme es una relacio ja carregada:
    # sense expirar-la, una licitacio que acaba de rebre organisme el
    # continuaria mostrant buit.
    session.expire(tender)

    return ManualTenderUpdateResult(
        revised=revised,
        revision=await manual_tender.current_revision_number(session, tender_id),
        tender=await _tender_detail(session, tender_id, ctx.user.id),
    )


@router.post(
    "/companies/{company_id}/tenders/{tender_id}/documents",
    response_model=TenderDocumentUploadResult,
    status_code=status.HTTP_201_CREATED,
    summary="Puja un plec a una licitacio oberta a ma",
)
async def upload_tender_document(
    tender_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    file: UploadFile = File(...),
    document_type: TenderDocumentType = Form(default=TenderDocumentType.OTHER),
):
    """Guarda el fitxer i, si del format en sabem llegir el text, el llegeix.

    Les dues coses es responen per separat: `stored` diu que els bytes hi son i
    `file.analysis_status` diu que se'n sap del text. Un DOCX torna `201` amb
    `analysis_status="unsupported"`, que no es cap error de pujada.

    Nomes s'hi pugen documents a una licitacio **oberta a ma** i de la propia
    empresa. Els plecs d'una licitacio publicada els porta la font.
    """
    manual_tender.assert_can_edit(ctx.membership.role)
    tender = await manual_tender.load_editable_tender(session, ctx.company_id, tender_id)

    outcome = await tender_document_upload.upload_tender_document(
        session,
        tender=tender,
        user=ctx.user,
        filename=file.filename,
        declared_mime_type=file.content_type,
        data=await _read_tender_upload(file),
        document_type=document_type,
    )

    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="tender_document",
        entity_id=outcome.document.id,
        summary=manual_tender.audit_summary(
            licitacio=tender_id,
            document=outcome.document.name,
            bytes=outcome.content.file_size,
        ),
    )
    await session.commit()

    return TenderDocumentUploadResult(
        id=outcome.document.id,
        name=outcome.document.name,
        document_type=outcome.document.document_type,
        mime_type=outcome.content.mime_type,
        stored=True,
        file=file_summary(outcome.file, outcome.content, outcome.extraction),
    )


@router.post(
    "/companies/{company_id}/tenders/{tender_id}/documents/{document_id}/versions",
    response_model=TenderDocumentUploadResult,
    status_code=status.HTTP_201_CREATED,
    summary="Puja una versio nova d'un plec",
)
async def upload_tender_document_version(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    response: Response,
    file: UploadFile = File(...),
):
    """Substitueix el contingut vigent d'un document sense perdre l'anterior.

    Si els bytes son **els mateixos** que els de la versio vigent no es crea cap
    versio: es respon `200` i no ha passat res. Tornar a pujar el mateix fitxer
    passa —dues persones, dues finestres— i convertir-ho en "els plecs han
    canviat" gastaria el senyal que serveix per avisar quan canvien de debo.

    La versio anterior es conserva sencera, amb el seu contingut i la seva
    extraccio: es l'evidencia que una cita pot haver referenciat.
    """
    manual_tender.assert_can_edit(ctx.membership.role)
    tender = await manual_tender.load_editable_tender(session, ctx.company_id, tender_id)
    document = await tender_document_upload.load_document_of(session, tender, document_id)

    outcome = await tender_document_upload.upload_new_version(
        session,
        tender=tender,
        document=document,
        user=ctx.user,
        filename=file.filename,
        declared_mime_type=file.content_type,
        data=await _read_tender_upload(file),
    )

    if outcome.created:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="tender_document",
            entity_id=outcome.document.id,
            summary=manual_tender.audit_summary(
                licitacio=tender_id,
                document=outcome.document.name,
                versio=outcome.file.version_number,
            ),
        )
        await session.commit()
    else:
        response.status_code = status.HTTP_200_OK

    return TenderDocumentUploadResult(
        id=outcome.document.id,
        name=outcome.document.name,
        document_type=outcome.document.document_type,
        mime_type=outcome.content.mime_type,
        stored=True,
        file=file_summary(outcome.file, outcome.content, outcome.extraction),
    )


@router.get(
    "/tenders", response_model=CataloguePage[TenderListItem], summary="Feed de licitacions"
)
async def list_tenders(
    session: SessionDep,
    user: CurrentUser,
    criteria: Annotated[TenderFilters, Depends(tender_filters)],
    sort: CatalogueSort = Query(default=DEFAULT_SORT),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """El cataleg public: qualsevol licitacio visible, sense cap pre-fit.

    Mateix contracte de filtres, ordre i fila que `/companies/{c}/matches`
    (`app.api.v1.tender_filters`). Aqui no hi ha empresa: `sort=score` cau a
    l'ordre neutral.
    """
    visible = visible_to_user(user.id)
    filters = [visible, *criteria.criteria()]

    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Tender).where(*filters)
        )
    ).scalar_one()
    excluded_without_amount = None
    if criteria.filters_amount:
        excluded_without_amount = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(Tender)
                .where(visible, *criteria.without_amount_criteria())
            )
        ).scalar_one()

    order = catalogue_order(sort, criteria.q, dialect_name=session.bind.dialect.name)

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

    context = await load_row_context(session, list(rows))
    return CataloguePage[TenderListItem](
        items=[list_item(tender, context) for tender in rows],
        total=total,
        limit=limit,
        offset=offset,
        excluded_without_amount=excluded_without_amount,
    )


@router.get(
    "/companies/{company_id}/tenders/facets",
    response_model=TenderFacets,
    summary="Valors per als filtres d'Oportunitats",
)
async def tender_facets(session: SessionDep, ctx: CompanyCtx):
    """Els valors dels filtres d'Oportunitats, en el context d'UNA empresa.

    Penja de `/companies/{company_id}` i no del feed global perque el seu unic
    consumidor —la pantalla d'Oportunitats— ja es company-scoped: mira els
    matches d'una empresa concreta. Amb el filtre per usuari, algu que fos
    membre d'A i de B veuria a les facetes d'A valors que nomes existeixen a una
    licitacio privada de B, i el recompte "Totes" tampoc no quadraria amb la
    llista que te al davant.

    Una faceta es una agregacio, i una agregacio delata: si una licitacio d'una
    altra empresa aportes el seu NUTS o el seu estat a la llista de valors, o el
    seu +1 al recompte, filtrar la llista no serviria de res.
    """
    visible = visible_to_company(ctx.company_id)

    async def distinct(column) -> list[str]:
        rows = await session.execute(
            sa.select(column)
            .where(column.is_not(None), visible)
            .distinct()
            .order_by(column)
        )
        return [str(getattr(value, "value", value)) for value in rows.scalars()]

    total = (
        await session.execute(sa.select(sa.func.count()).select_from(Tender).where(visible))
    ).scalar_one()
    sources = (
        await session.execute(
            sa.select(Source.code, Source.name, sa.func.count(Tender.id))
            .join(Tender, Tender.source_id == Source.id)
            .where(visible)
            .group_by(Source.code, Source.name)
        )
    ).all()
    status_counts = (
        await session.execute(
            sa.select(Tender.status, sa.func.count()).where(visible).group_by(Tender.status)
        )
    ).all()
    return TenderFacets(
        statuses=await distinct(Tender.status),
        procedure_types=await distinct(Tender.procedure_type),
        contract_types=await distinct(Tender.contract_type),
        nuts_codes=await distinct(Tender.nuts_code),
        total_tenders=total,
        # Ordenades a Python: l'ordre de la col·lacio de la BD no ha de canviar la UI.
        sources=sorted(
            (SourceFacet(code=code, name=name, count=count) for code, name, count in sources),
            key=lambda facet: facet.code,
        ),
        status_counts={
            str(getattr(value, "value", value)): count for value, count in status_counts
        },
    )


async def _load_tender(session, tender_id: uuid.UUID, user_id: uuid.UUID) -> Tender:
    """La fitxa, si aquest usuari la pot veure.

    Una privada d'una altra empresa dona 404 i no 403: dir "no pots" ja seria
    dir que existeix, i el punt es que per a qui no la te no existeix.
    """
    tender = (
        await session.execute(
            sa.select(Tender)
            .where(Tender.id == tender_id, visible_to_user(user_id))
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

    return {
        file.tender_document_id: file_summary(file, content, extraction)
        for file, content, extraction in rows
    }


def file_summary(file, content, extraction) -> TenderDocumentFileSummary:
    """L'estat d'una versio, amb el veredicte sobre el text ja calculat.

    Es una sola funcio i no una per pantalla: si la fitxa i l'assistent
    d'importacio calculessin cadascun el seu, acabarien dient coses diferents
    del mateix fitxer.
    """
    return TenderDocumentFileSummary(
        file_id=file.id,
        version_number=file.version_number,
        download_status=file.download_status,
        origin=file.origin,
        file_size=content.file_size if content else None,
        sha256=content.sha256 if content else None,
        mime_type=content.mime_type if content else None,
        text_extraction_supported=(
            is_text_extractable_mime(content.mime_type) if content else False
        ),
        analysis_status=analysis_status_for(
            extraction.status if extraction else None,
            extraction.quality if extraction else None,
        ),
        extraction_status=extraction.status if extraction else None,
        quality=extraction.quality if extraction else None,
        page_count=extraction.page_count if extraction else None,
        text_char_count=extraction.text_char_count if extraction else None,
        ocr_candidate=bool(extraction.ocr_candidate) if extraction else False,
        extraction_id=extraction.id if extraction else None,
        processed_at=file.downloaded_at,
        error_code=file.error_code or (extraction.error_code if extraction else None),
    )


def _gap_key(detail: dict | None) -> str | None:
    """La clau d'un forat (`detail.key`): el rol, el codi citat o el document."""
    key = (detail or {}).get("key")
    return None if key is None else str(key)


async def _tender_detail(session, tender_id: uuid.UUID, user_id: uuid.UUID) -> TenderDetail:
    """La fitxa sencera, amb el filtre de visibilitat de `_load_tender`."""
    tender = await _load_tender(session, tender_id, user_id)

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

    # Nomes els plecs vigents: un document retirat de la font (`removed_at`) ja no
    # es part de la publicacio; l'historial de revisions en conserva el rastre. Els
    # retirats van a part (`removed_documents`) perque la fitxa ho pugui dir.
    documents = [doc for doc in tender.documents if is_active_document(doc)]
    removed = sorted(
        (doc for doc in tender.documents if not is_active_document(doc)),
        key=lambda doc: (doc.removed_at, doc.name),
    )
    summaries = await _document_summaries(session, [doc.id for doc in documents])
    # El paquet (estat, forats oberts) i l'estat de cada seccio surten de la mateixa
    # vista pura que llegeix el motor (`section_status_for`): cap segona regla aqui.
    # Vista global (sense empresa): el paquet es de la licitacio, no d'una empresa.
    view, _ = await load_structure_view(session, tender)
    # F-06a: el PBL amb IVA de la PSCP, nomes informatiu (una consulta, expedient i
    # lots vigents). La licitacio ja ha passat el filtre de visibilitat de dalt.
    budget_observations = await amount_observations.load(
        session, tender.id, {lot.id for lot in tender.lots if lot.removed_at is None}
    )

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
        source_publication_count=tender.revision_count,
        source_code=source.code,
        source_updated_at=tender.source_updated_at,
        source_removed_at=tender.source_removed_at,
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
            for document in documents
        ],
        removed_documents=[TenderDocumentRead.model_validate(document) for document in removed],
        package=TenderPackageRead(
            status=view.package_status,
            gaps=[
                TenderPackageGapRead(
                    code=gap.code, key=_gap_key(gap.detail), would_provide=list(gap.would_provide)
                )
                for gap in coverage_gaps(view.gaps)
            ],
            processing=[
                TenderPackageGapRead(
                    code=gap.code, key=_gap_key(gap.detail), would_provide=list(gap.would_provide)
                )
                for gap in processing_gaps(view.gaps)
            ],
            section_status=dict(view.section_status),
        ),
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
        duration_months=tender.duration_months,
        extension_months=tender.extension_months,
        regulated_harmonised=tender.regulated_harmonised,
        planned_start_date=tender.planned_start_date,
        blocks=[TenderBlockRead.model_validate(block) for block in tender.blocks],
        sites=[TenderSiteRead.model_validate(site) for site in tender.sites],
        budget_with_tax_observations=budget_observations,
    )


@router.get("/tenders/{tender_id}", response_model=TenderDetail, summary="Fitxa de licitacio")
async def get_tender(tender_id: uuid.UUID, session: SessionDep, user: CurrentUser):
    return await _tender_detail(session, tender_id, user.id)


@router.get(
    "/tenders/{tender_id}/lots", response_model=list[TenderLotRead], summary="Lots de la licitacio"
)
async def list_tender_lots(tender_id: uuid.UUID, session: SessionDep, user: CurrentUser):
    tender = await _load_tender(session, tender_id, user.id)
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
    "/companies/{company_id}/tenders/{tender_id}/documents",
    response_model=list[TenderDocumentRead],
    summary="Documents d'una licitacio (nomes metadata i URL)",
)
async def list_tender_documents(
    tender_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    include_removed: bool = Query(
        default=False, description="Inclou els documents retirats de la font (`removed_at`)."
    ),
):
    """Dins d'una empresa, com tota la lectura de plecs.

    La metadata d'un plec ja diu prou —nom, tipus, mida, estat— com perque
    llegir-la des d'una altra empresa sigui una fuita. Les licitacions
    publicades hi continuen sent: `visible_to_company` inclou les que no tenen
    propietari. Per defecte nomes els plecs vigents a la font.
    """
    tender = (
        await session.execute(
            sa.select(Tender)
            .where(Tender.id == tender_id, visible_to_company(ctx.company_id))
            .options(selectinload(Tender.documents))
        )
    ).scalars().unique().one_or_none()
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return [
        TenderDocumentRead.model_validate(document)
        for document in tender.documents
        if include_removed or is_active_document(document)
    ]


@router.get(
    "/tenders/{tender_id}/revisions",
    response_model=list[TenderRevisionRead],
    summary="Historial de versions rebudes",
)
async def list_tender_revisions(tender_id: uuid.UUID, session: SessionDep, user: CurrentUser):
    await _load_tender(session, tender_id, user.id)
    rows = (
        await session.execute(
            sa.select(SourceRecord)
            .where(SourceRecord.tender_id == tender_id)
            .order_by(SourceRecord.revision_number.desc())
        )
    ).scalars().all()
    return [TenderRevisionRead.model_validate(record) for record in rows]


@router.get(
    "/companies/{company_id}/buyers",
    response_model=Page[BuyerSummary],
    summary="Organismes contractants",
)
async def list_buyers(
    session: SessionDep,
    ctx: CompanyCtx,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """El cataleg que veu una empresa: els publics i els seus.

    Penja de `/companies/{company_id}` i no de l'arrel perque el seu consumidor
    es el selector del wizard d'importacio, que treballa en context d'UNA
    empresa. Amb un filtre per usuari, algu membre d'A i de B hi barrejaria els
    dos cataleges privats mentre dona d'alta una licitacio d'una de sola.
    """
    filters = [buyer_visible_to_company(ctx.company_id)]
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


@router.get(
    "/companies/{company_id}/buyers/{buyer_id}",
    response_model=BuyerRead,
    summary="Organisme contractant",
)
async def get_buyer(buyer_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Un organisme public o de la mateixa empresa. D'una altra: 404."""
    buyer = (
        await session.execute(
            sa.select(Buyer).where(
                Buyer.id == buyer_id, buyer_visible_to_company(ctx.company_id)
            )
        )
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
async def tender_history(tender_id: uuid.UUID, session: SessionDep, user: CurrentUser):
    """Com hem arribat a l'estat actual, de la mes recent a la mes antiga.

    Es public i global, com la licitacio: la historia d'un expedient no pertany
    a cap empresa. Els impactes sobre un workspace concret s'exposen al
    workspace, que si que es del tenant.
    """
    await _load_tender(session, tender_id, user.id)
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
    user: CurrentUser,
    material_only: bool = True,
):
    """Els canvis, del mes recent al mes antic.

    Per defecte nomes els materials: un canvi de titol no invalida res i
    barrejar-lo amb una substitucio de plec faria que la llista deixes de
    llegir-se. `material_only=false` els ensenya tots.
    """
    await _load_tender(session, tender_id, user.id)
    query = (
        sa.select(TenderChangeEvent)
        .where(TenderChangeEvent.tender_id == tender_id)
        .order_by(TenderChangeEvent.detected_at.desc(), TenderChangeEvent.change_code)
    )
    if material_only:
        query = query.where(TenderChangeEvent.is_material.is_(True))
    return list((await session.scalars(query)).all())
