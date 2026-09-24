"""Lectura de plecs, sempre dins d'una empresa.

Aquests endpoints **pengen de `/companies/{company_id}`** i no ho feien. El
motiu es que un plec ja no es necessariament public: des que hi ha licitacions
d'una empresa, "qui ets" no n'hi ha prou per decidir que pots llegir, perque qui
es membre de dues empreses treballa sempre DINS d'una. En context B no ha de
poder llegir un plec d'A nomes perque tambe es d'A.

El filtre es `visible_to_company(company_id)`, que inclou les licitacions sense
propietari: un plec publicat es llegeix des de qualsevol empresa, exactament com
abans. El que s'ha tancat es la porta user-global, que era per on es llegia el
d'una altra.

El processat ja no es fa aqui: el demana
`/companies/{company_id}/tenders/{tender_id}/job-requests/process-documents` i
l'executa el worker. Una descarrega fallida no fa fallar la feina: queda escrita
a la fila del document amb `download_status: failed` i el seu `error_code`.
"""

from __future__ import annotations

import io
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Path, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import selectinload

from app.api.deps import CompanyCtx, CurrentUser, SessionDep
from app.core.errors import EndpointMovedError, NotFoundError
from app.models.tender_documents import (
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender, TenderDocument
from app.schemas.tender_documents import (
    TenderDocumentExtractionRead,
    TenderDocumentFileRead,
    TenderDocumentPageRead,
)
from app.schemas.tenders import Page
from app.services.storage import get_storage
from app.services.tender_document_processing import (
    current_extraction_for,
)
from app.tenders.visibility import visible_to_company

router = APIRouter(tags=["plecs"])


def _visible(company_id: uuid.UUID):
    """El filtre que converteix "existeix" en "el pots veure DES D'AQUI".

    Totes les consultes d'aquest modul hi passen. L'empresa es la del context de
    la ruta, que `CompanyCtx` ja ha comprovat que es de l'usuari: no la unio de
    totes les seves pertinences. La diferencia es justament el forat que hi
    havia, perque qui es de dues empreses llegia des d'una el que era de
    l'altra.
    """
    return TenderDocument.tender_id.in_(
        sa.select(Tender.id).where(visible_to_company(company_id))
    )


async def _load_document(
    session: SessionDep, tender_id: uuid.UUID, document_id: uuid.UUID, company_id: uuid.UUID
) -> TenderDocument:
    document = await session.scalar(
        sa.select(TenderDocument).where(
            TenderDocument.id == document_id,
            # El tender de la ruta ha de coincidir: sense aixo, la URL
            # afirmaria una relacio que no s'ha comprovat.
            TenderDocument.tender_id == tender_id,
            _visible(company_id),
        )
    )
    if document is None:
        raise NotFoundError("Document no trobat.")
    return document


async def _load_file(
    session: SessionDep, file_id: uuid.UUID, company_id: uuid.UUID
) -> TenderDocumentFile:
    file = await session.scalar(
        sa.select(TenderDocumentFile)
        .join(TenderDocument, TenderDocument.id == TenderDocumentFile.tender_document_id)
        .where(TenderDocumentFile.id == file_id, _visible(company_id))
    )
    if file is None:
        raise NotFoundError("Fitxer no trobat.")
    return file


async def _load_extraction(
    session: SessionDep, extraction_id: uuid.UUID, company_id: uuid.UUID
) -> TenderDocumentExtraction:
    """Una extraccio es dels BYTES, i els mateixos bytes poden penjar de mes
    d'una licitacio.

    Per tant el dret NO pot dependre de qui la va crear: si dues empreses pugen
    el mateix plec hi ha un sol contingut i una sola extraccio, i la segona no
    pot quedar-se sense poder llegir el text del SEU document. La pregunta es
    si aquest contingut el referencia algun document visible **en aquesta
    empresa**; si nomes el referencia l'altra, es un 404.
    """
    extraction = await session.scalar(
        sa.select(TenderDocumentExtraction)
        .options(selectinload(TenderDocumentExtraction.qa_issues))
        .where(
            TenderDocumentExtraction.id == extraction_id,
            sa.exists(
                sa.select(sa.literal(1))
                .select_from(TenderDocumentFile)
                .join(
                    TenderDocument,
                    TenderDocument.id == TenderDocumentFile.tender_document_id,
                )
                .where(
                    TenderDocumentFile.content_id == TenderDocumentExtraction.content_id,
                    _visible(company_id),
                )
            ),
        )
    )
    if extraction is None:
        raise NotFoundError("Extraccio no trobada.")
    return extraction


async def _file_read(session: SessionDep, file: TenderDocumentFile) -> TenderDocumentFileRead:
    """Munta la resposta afegint-hi l'extraccio vigent del contingut."""
    read = TenderDocumentFileRead.model_validate(file)
    if file.content_id is not None:
        extraction = await current_extraction_for(session, file.content_id)
        if extraction is not None:
            read.extraction = TenderDocumentExtractionRead.model_validate(extraction)
    return read


@router.post(
    "/tenders/{tender_id}/documents/{document_id}/process",
    summary="(Retirat) El processat ja no s'executa dins de la peticio",
    status_code=410,
)
async def process_document(tender_id: uuid.UUID, document_id: uuid.UUID, _: CurrentUser):
    """Baixar i extreure un plec pot durar massa per a una peticio HTTP."""
    raise EndpointMovedError(
        "El processat s'executa en segon pla. Demana'l a "
        "/companies/{company_id}/tenders/{tender_id}/job-requests/process-documents.",
        details={"tender_id": str(tender_id), "document_id": str(document_id)},
    )


@router.get(
    "/companies/{company_id}/tenders/{tender_id}/documents/{document_id}/files",
    response_model=list[TenderDocumentFileRead],
    summary="Versions descarregades d'un document",
)
async def list_document_files(
    tender_id: uuid.UUID, document_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    await _load_document(session, tender_id, document_id, ctx.company_id)
    files = (
        await session.scalars(
            sa.select(TenderDocumentFile)
            .where(TenderDocumentFile.tender_document_id == document_id)
            .order_by(TenderDocumentFile.version_number.desc())
        )
    ).all()
    return [await _file_read(session, file) for file in files]


@router.get(
    "/companies/{company_id}/tender-document-files/{file_id}",
    response_model=TenderDocumentFileRead,
    summary="Detall d'una versio",
)
async def get_file(file_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    return await _file_read(session, await _load_file(session, file_id, ctx.company_id))


@router.get(
    "/companies/{company_id}/tender-document-files/{file_id}/extractions",
    response_model=list[TenderDocumentExtractionRead],
    summary="Extraccions fetes sobre aquests bytes",
)
async def list_file_extractions(file_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    file = await _load_file(session, file_id, ctx.company_id)
    if file.content_id is None:
        return []
    extractions = (
        await session.scalars(
            sa.select(TenderDocumentExtraction)
            # Els avisos de qualitat viatgen amb l'extraccio i la resposta els
            # inclou: sense carregar-los aqui, llegir-los ja fora de la sessio
            # peta amb `MissingGreenlet`.
            .options(selectinload(TenderDocumentExtraction.qa_issues))
            .where(TenderDocumentExtraction.content_id == file.content_id)
            .order_by(TenderDocumentExtraction.created_at.desc())
        )
    ).all()
    return [TenderDocumentExtractionRead.model_validate(item) for item in extractions]


async def _paginated_pages(
    session: SessionDep, extraction_id: uuid.UUID, limit: int, offset: int
) -> Page[TenderDocumentPageRead]:
    total = await session.scalar(
        sa.select(sa.func.count())
        .select_from(TenderDocumentPage)
        .where(TenderDocumentPage.extraction_id == extraction_id)
    )
    rows = (
        await session.scalars(
            sa.select(TenderDocumentPage)
            .where(TenderDocumentPage.extraction_id == extraction_id)
            .order_by(TenderDocumentPage.page_number)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(
        items=[TenderDocumentPageRead.model_validate(row) for row in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/companies/{company_id}/tender-document-files/{file_id}/pages",
    response_model=Page[TenderDocumentPageRead],
    summary="Pagines de l'extraccio vigent",
)
async def list_file_pages(
    file_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    file = await _load_file(session, file_id, ctx.company_id)
    extraction = await current_extraction_for(session, file.content_id) if file.content_id else None
    if extraction is None:
        return Page(items=[], total=0, limit=limit, offset=offset)
    return await _paginated_pages(session, extraction.id, limit, offset)


@router.get(
    "/companies/{company_id}/tender-document-extractions/{extraction_id}/pages",
    response_model=Page[TenderDocumentPageRead],
    summary="Pagines d'una extraccio concreta",
)
async def list_extraction_pages(
    extraction_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    extraction = await _load_extraction(session, extraction_id, ctx.company_id)
    return await _paginated_pages(session, extraction.id, limit, offset)


@router.get(
    "/companies/{company_id}/tender-document-extractions/{extraction_id}/pages/{page_number}",
    response_model=TenderDocumentPageRead,
    summary="Una pagina concreta",
)
async def get_extraction_page(
    extraction_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    # ge=1 perque la numeracio es 1-based: la pagina 0 no existeix.
    page_number: int = Path(ge=1),
):
    await _load_extraction(session, extraction_id, ctx.company_id)
    page = await session.scalar(
        sa.select(TenderDocumentPage).where(
            TenderDocumentPage.extraction_id == extraction_id,
            TenderDocumentPage.page_number == page_number,
        )
    )
    if page is None:
        raise NotFoundError(f"La pagina {page_number} no existeix en aquesta extraccio.")
    return TenderDocumentPageRead.model_validate(page)


@router.get(
    "/companies/{company_id}/tender-document-files/{file_id}/original",
    summary="Descarrega l'original",
)
async def download_original(file_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    file = await _load_file(session, file_id, ctx.company_id)
    if file.content is None:
        raise NotFoundError("Aquesta versio no te cap fitxer descarregat.")

    data = await get_storage().download(file.content.storage_key)
    filename = (
        file.detected_filename
        or file.original_filename
        or f"{file.content.sha256[:16]}.{file.content.detected_extension or 'bin'}"
    )
    # Inline nomes per a PDF: es el que el navegador sap mostrar sense baixar-lo.
    disposition = "inline" if file.content.mime_type == "application/pdf" else "attachment"

    return StreamingResponse(
        io.BytesIO(data),
        media_type=file.content.mime_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Content-Length": str(file.content.file_size),
        },
    )
