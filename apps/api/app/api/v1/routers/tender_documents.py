"""Processat i lectura de plecs.

Com el feed de licitacions, aquests endpoints NO pengen de
`/companies/{company_id}`: els plecs son publics i globals. Cal sessio
iniciada, no pertinenca a cap empresa.

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

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import EndpointMovedError, NotFoundError
from app.models.tender_documents import (
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import TenderDocument
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

router = APIRouter(tags=["plecs"])


async def _load_document(
    session: SessionDep, tender_id: uuid.UUID, document_id: uuid.UUID
) -> TenderDocument:
    document = await session.scalar(
        sa.select(TenderDocument).where(
            TenderDocument.id == document_id,
            # El tender de la ruta ha de coincidir: sense aixo, la URL
            # afirmaria una relacio que no s'ha comprovat.
            TenderDocument.tender_id == tender_id,
        )
    )
    if document is None:
        raise NotFoundError("Document no trobat.")
    return document


async def _load_file(session: SessionDep, file_id: uuid.UUID) -> TenderDocumentFile:
    file = await session.scalar(
        sa.select(TenderDocumentFile).where(TenderDocumentFile.id == file_id)
    )
    if file is None:
        raise NotFoundError("Fitxer no trobat.")
    return file


async def _load_extraction(
    session: SessionDep, extraction_id: uuid.UUID
) -> TenderDocumentExtraction:
    extraction = await session.scalar(
        sa.select(TenderDocumentExtraction).where(TenderDocumentExtraction.id == extraction_id)
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
    "/tenders/{tender_id}/documents/{document_id}/files",
    response_model=list[TenderDocumentFileRead],
    summary="Versions descarregades d'un document",
)
async def list_document_files(
    tender_id: uuid.UUID, document_id: uuid.UUID, session: SessionDep, _: CurrentUser
):
    await _load_document(session, tender_id, document_id)
    files = (
        await session.scalars(
            sa.select(TenderDocumentFile)
            .where(TenderDocumentFile.tender_document_id == document_id)
            .order_by(TenderDocumentFile.version_number.desc())
        )
    ).all()
    return [await _file_read(session, file) for file in files]


@router.get(
    "/tender-document-files/{file_id}",
    response_model=TenderDocumentFileRead,
    summary="Detall d'una versio",
)
async def get_file(file_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    return await _file_read(session, await _load_file(session, file_id))


@router.get(
    "/tender-document-files/{file_id}/extractions",
    response_model=list[TenderDocumentExtractionRead],
    summary="Extraccions fetes sobre aquests bytes",
)
async def list_file_extractions(file_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    file = await _load_file(session, file_id)
    if file.content_id is None:
        return []
    extractions = (
        await session.scalars(
            sa.select(TenderDocumentExtraction)
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
    "/tender-document-files/{file_id}/pages",
    response_model=Page[TenderDocumentPageRead],
    summary="Pagines de l'extraccio vigent",
)
async def list_file_pages(
    file_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    file = await _load_file(session, file_id)
    extraction = await current_extraction_for(session, file.content_id) if file.content_id else None
    if extraction is None:
        return Page(items=[], total=0, limit=limit, offset=offset)
    return await _paginated_pages(session, extraction.id, limit, offset)


@router.get(
    "/tender-document-extractions/{extraction_id}/pages",
    response_model=Page[TenderDocumentPageRead],
    summary="Pagines d'una extraccio concreta",
)
async def list_extraction_pages(
    extraction_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    extraction = await _load_extraction(session, extraction_id)
    return await _paginated_pages(session, extraction.id, limit, offset)


@router.get(
    "/tender-document-extractions/{extraction_id}/pages/{page_number}",
    response_model=TenderDocumentPageRead,
    summary="Una pagina concreta",
)
async def get_extraction_page(
    extraction_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    # ge=1 perque la numeracio es 1-based: la pagina 0 no existeix.
    page_number: int = Path(ge=1),
):
    await _load_extraction(session, extraction_id)
    page = await session.scalar(
        sa.select(TenderDocumentPage).where(
            TenderDocumentPage.extraction_id == extraction_id,
            TenderDocumentPage.page_number == page_number,
        )
    )
    if page is None:
        raise NotFoundError(f"La pagina {page_number} no existeix en aquesta extraccio.")
    return TenderDocumentPageRead.model_validate(page)


@router.get("/tender-document-files/{file_id}/original", summary="Descarrega l'original")
async def download_original(file_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    file = await _load_file(session, file_id)
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
