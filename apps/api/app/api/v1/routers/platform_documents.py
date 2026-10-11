"""Documents publics oficials d'una licitacio: via d'escriptura de l'operador (3B-2 §2.6).

Nomes l'Administracio Tuvi (`PlatformAdmin`: superusuari sense empresa) incorpora,
verifica, rebutja, retira, restaura i cedeix al feed documents d'una licitacio
PUBLICA. Cap empresa escriu a `tender_documents` d'un tender public (els seus
endpoints continuen refusant-ho amb `tender_not_manual`) i en aquesta fase no hi
ha cap workflow de propostes. La logica viu a `services/public_document_intake`.

`by-url` descarrega i extreu el text **dins de la peticio** (sincron). S'accepta
perque es una accio puntual de l'operador, no massiva, amb la guarda SSRF i
l'allowlist a cada salt, tall de mida i sostre de temps total
(`DOCUMENT_DOWNLOAD_*`), i sense cap transaccio oberta durant la descarrega. Si
un proxy invers talla abans, el reintent dona `document_url_exists`. Es pot moure
al worker mes endavant sense canviar el contracte del servei.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import PlatformAdmin, SessionDep
from app.api.v1.routers.tenders import file_summary
from app.core.config import settings
from app.core.errors import PayloadTooLargeError
from app.models.enums import TenderDocumentType
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
)
from app.models.tenders import TenderDocument
from app.schemas.tenders import (
    PlatformDocumentByUrlRequest,
    PlatformDocumentHandOverRequest,
    PlatformDocumentRejectRequest,
    PlatformDocumentRetireRequest,
    PlatformDocumentVerifyRequest,
    PlatformTenderDocumentRead,
    TenderDocumentFileSummary,
)
from app.services import public_document_intake as intake

router = APIRouter(prefix="/platform/tenders/{tender_id}/documents", tags=["platform"])

_UPLOAD_CHUNK = 1024 * 1024


async def _summaries(
    session: SessionDep, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, TenderDocumentFileSummary]:
    """La versio vigent de cada document, amb el mateix resum que la fitxa."""
    if not document_ids:
        return {}
    rows = await session.execute(
        sa.select(TenderDocumentFile, TenderDocumentContent, TenderDocumentExtraction)
        .outerjoin(TenderDocumentContent, TenderDocumentContent.id == TenderDocumentFile.content_id)
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


async def _reads(
    session: SessionDep, documents: list[TenderDocument]
) -> list[PlatformTenderDocumentRead]:
    summaries = await _summaries(session, [document.id for document in documents])
    return [
        PlatformTenderDocumentRead.model_validate(document).model_copy(
            update={"latest_file": summaries.get(document.id)}
        )
        for document in documents
    ]


async def _read(session: SessionDep, document: TenderDocument) -> PlatformTenderDocumentRead:
    return (await _reads(session, [document]))[0]


async def _read_upload(file: UploadFile) -> bytes:
    """Llegeix el fitxer amb tall dur de mida (el limit dels plecs)."""
    limit = settings.document_max_size_bytes
    buffer = bytearray()
    while chunk := await file.read(_UPLOAD_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise PayloadTooLargeError(
                f"El fitxer supera el maxim permes de {settings.DOCUMENT_MAX_SIZE_MB} MB."
            )
    return bytes(buffer)


@router.get(
    "",
    response_model=list[PlatformTenderDocumentRead],
    summary="Tots els documents d'una licitacio publica, retirats inclosos, amb provenance",
)
async def list_documents(
    tender_id: uuid.UUID, _: PlatformAdmin, session: SessionDep
) -> list[PlatformTenderDocumentRead]:
    return await _reads(session, await intake.list_documents(session, tender_id))


@router.post(
    "/by-url",
    response_model=PlatformTenderDocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Incorpora un document oficial per URL (pendent de verificacio)",
)
async def add_document_by_url(
    tender_id: uuid.UUID,
    payload: PlatformDocumentByUrlRequest,
    admin: PlatformAdmin,
    session: SessionDep,
) -> PlatformTenderDocumentRead:
    document = await intake.add_document_by_url(
        session,
        tender_id,
        payload.url,
        payload.source_page_url,
        admin,
        name=payload.name,
        document_type=payload.document_type,
    )
    return await _read(session, document)


@router.post(
    "/upload",
    response_model=PlatformTenderDocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Puja un document oficial no descarregable (pendent de verificacio)",
)
async def upload_document(
    tender_id: uuid.UUID,
    admin: PlatformAdmin,
    session: SessionDep,
    file: UploadFile = File(...),
    contribution_source_url: str | None = Form(default=None, max_length=1000),
    name: str | None = Form(default=None, max_length=500),
    document_type: TenderDocumentType = Form(default=TenderDocumentType.OTHER),
) -> PlatformTenderDocumentRead:
    document = await intake.upload_public_document(
        session,
        tender_id,
        actor=admin,
        filename=file.filename,
        declared_mime_type=file.content_type,
        data=await _read_upload(file),
        contribution_source_url=contribution_source_url,
        name=name,
        document_type=document_type,
    )
    return await _read(session, document)


@router.post(
    "/{document_id}/verify",
    response_model=PlatformTenderDocumentRead,
    summary="Verifica que es el document publicat oficialment (passa a analitzable)",
)
async def verify_document(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: PlatformDocumentVerifyRequest,
    admin: PlatformAdmin,
    session: SessionDep,
) -> PlatformTenderDocumentRead:
    document = await intake.verify_document(session, tender_id, document_id, admin, payload.note)
    return await _read(session, document)


@router.post(
    "/{document_id}/reject",
    response_model=PlatformTenderDocumentRead,
    summary="Rebutja una aportacio (no es el document publicat)",
)
async def reject_document(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: PlatformDocumentRejectRequest,
    admin: PlatformAdmin,
    session: SessionDep,
) -> PlatformTenderDocumentRead:
    document = await intake.reject_document(session, tender_id, document_id, admin, payload.reason)
    return await _read(session, document)


@router.post(
    "/{document_id}/retire",
    response_model=PlatformTenderDocumentRead,
    summary="Retira un document de l'operador (amb motiu; mai s'esborra)",
)
async def retire_document(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: PlatformDocumentRetireRequest,
    admin: PlatformAdmin,
    session: SessionDep,
) -> PlatformTenderDocumentRead:
    document = await intake.retire_document(session, tender_id, document_id, admin, payload.reason)
    return await _read(session, document)


@router.post(
    "/{document_id}/restore",
    response_model=PlatformTenderDocumentRead,
    summary="Restaura un document de l'operador retirat",
)
async def restore_document(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    admin: PlatformAdmin,
    session: SessionDep,
) -> PlatformTenderDocumentRead:
    document = await intake.restore_document(session, tender_id, document_id, admin)
    return await _read(session, document)


@router.post(
    "/{document_id}/hand-over-to-feed",
    response_model=PlatformTenderDocumentRead,
    summary="Cedeix al feed un document per URL que la font ja publica",
)
async def hand_over_to_feed(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: PlatformDocumentHandOverRequest,
    admin: PlatformAdmin,
    session: SessionDep,
) -> PlatformTenderDocumentRead:
    document = await intake.hand_over_to_feed(
        session,
        tender_id,
        document_id,
        admin,
        reason=payload.reason,
    )
    return await _read(session, document)
