"""Biblioteca documental i enllacos d'evidencia (DADA -> EVIDENCIA)."""

import uuid
from datetime import date

import sqlalchemy as sa
from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError

from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.crud import apply_changes, get_owned_or_404
from app.core.config import settings
from app.core.errors import (
    ConflictError,
    DuplicateDocumentError,
    NotFoundError,
    PayloadTooLargeError,
)
from app.models.documents import Document, DocumentLink
from app.models.enums import AuditAction, DocumentCategory, LinkedEntityType, RelationshipType
from app.schemas.documents import (
    DocumentLinkCreate,
    DocumentLinkRead,
    DocumentRead,
    DocumentUpdate,
    EvidenceRead,
)
from app.services import audit, knowledge_evidence
from app.services.document_retention import assert_document_is_deletable
from app.services.entities import assert_entity_in_company
from app.services.files import compute_sha256, extension_for, sanitize_filename, validate_upload
from app.services.storage import build_storage_key, get_storage

router = APIRouter(prefix="/documents", tags=["documents"])
evidence_router = APIRouter(prefix="/evidence", tags=["documents"])

_CHUNK = 1024 * 1024


async def _read_upload(file: UploadFile) -> bytes:
    """Llegeix el fitxer amb tall dur de mida, sense confiar en Content-Length."""
    limit = settings.max_upload_size_bytes
    buffer = bytearray()
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise PayloadTooLargeError(
                f"El fitxer supera el maxim permes de {settings.MAX_UPLOAD_SIZE_MB} MB."
            )
    return bytes(buffer)


@router.get("", response_model=list[DocumentRead], summary="Llista documents")
async def list_documents(
    session: SessionDep,
    ctx: CompanyCtx,
    q: str | None = Query(default=None, max_length=160),
    category: DocumentCategory | None = Query(default=None),
    sha256: str | None = Query(default=None, min_length=64, max_length=64),
):
    stmt = sa.select(Document).where(Document.company_id == ctx.company_id)
    if q:
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(
            sa.or_(
                sa.func.lower(Document.name).like(pattern),
                sa.func.lower(Document.original_filename).like(pattern),
            )
        )
    if category is not None:
        stmt = stmt.where(Document.category == category)
    if sha256:
        stmt = stmt.where(Document.sha256 == sha256.lower())
    rows = (await session.execute(stmt.order_by(Document.uploaded_at.desc()))).scalars().all()
    return list(rows)


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Puja un document",
)
async def upload_document(
    session: SessionDep,
    ctx: CompanyCtx,
    file: UploadFile = File(...),
    name: str | None = Form(default=None, max_length=255),
    category: DocumentCategory = Form(default=DocumentCategory.OTHER),
    description: str | None = Form(default=None),
    issued_at: date | None = Form(default=None),
    expires_at: date | None = Form(default=None),
    entity_type: LinkedEntityType | None = Form(default=None),
    entity_id: uuid.UUID | None = Form(default=None),
    relationship_type: RelationshipType = Form(default=RelationshipType.SUPPORTING_DOCUMENT),
    allow_duplicate: bool = Form(default=False),
):
    content = await _read_upload(file)
    mime_type = validate_upload(content, file.content_type)
    original_filename = sanitize_filename(file.filename)
    digest = compute_sha256(content)

    # Deduplicacio per contingut: el mateix fitxer no s'ha de guardar dues
    # vegades a l'storage ni aparèixer duplicat a la biblioteca. Es comprova
    # ABANS de pujar res. `allow_duplicate` permet forçar-ho conscientment.
    if not allow_duplicate:
        twin = (
            await session.execute(
                sa.select(Document).where(
                    Document.company_id == ctx.company_id, Document.sha256 == digest
                )
            )
        ).scalars().first()
        if twin is not None:
            raise DuplicateDocumentError(
                "Aquest fitxer ja existeix a la biblioteca documental.",
                details={
                    "existing_document_id": str(twin.id),
                    "existing_document_name": twin.name,
                    "sha256": digest,
                },
            )

    # Si es demana crear l'enllac d'evidencia alhora, es valida ABANS de pujar
    # el binari per no deixar fitxers orfes a l'storage.
    if entity_type is not None and entity_id is not None:
        await assert_entity_in_company(session, entity_type, entity_id, ctx.company_id)

    storage_key = build_storage_key(ctx.company_id, extension_for(mime_type, original_filename))
    await get_storage().upload(storage_key, content, mime_type)

    document = Document(
        company_id=ctx.company_id,
        name=(name or original_filename)[:255],
        original_filename=original_filename,
        storage_key=storage_key,
        mime_type=mime_type,
        file_size=len(content),
        sha256=digest,
        category=category,
        description=description,
        issued_at=issued_at,
        expires_at=expires_at,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    session.add(document)
    await session.flush()

    if entity_type is not None and entity_id is not None:
        session.add(
            DocumentLink(
                company_id=ctx.company_id,
                document_id=document.id,
                entity_type=entity_type,
                entity_id=entity_id,
                relationship_type=relationship_type,
                created_by=ctx.user_id,
            )
        )

    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="documents",
        entity_id=document.id,
        summary=f"Document pujat ({mime_type}, {len(content)} bytes)",
    )
    await session.commit()
    await session.refresh(document)
    return document


@router.get("/{document_id}", response_model=DocumentRead, summary="Metadata d'un document")
async def get_document(document_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    return await get_owned_or_404(session, Document, document_id, ctx.company_id)


@router.patch("/{document_id}", response_model=DocumentRead, summary="Edita metadata")
async def update_document(
    document_id: uuid.UUID, payload: DocumentUpdate, session: SessionDep, ctx: CompanyCtx
):
    document = await get_owned_or_404(session, Document, document_id, ctx.company_id)
    apply_changes(document, payload, ctx.user_id)
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="documents",
        entity_id=document.id,
    )
    await session.commit()
    await session.refresh(document)
    return document


@router.delete(
    "/{document_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Esborra un document"
)
async def delete_document(
    document_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    document = await get_owned_or_404(session, Document, document_id, ctx.company_id)
    # Un document que sosté un paquet de presentacio congelat no s'esborra: sense
    # els bytes, el SHA-256 del manifest deixaria de ser comprovable. La FK ja ho
    # impediria; aixo hi posa un error que es pot llegir.
    await assert_document_is_deletable(session, document.id)
    storage_key = document.storage_key
    # Si aquest document era l'unica evidencia d'un coneixement verificat, deixa de
    # ser-ho: es llegeixen els enllacos abans que caiguin.
    targets = [(link.entity_type, link.entity_id) for link in document.links]
    await session.delete(document)  # els enllacos cauen per FK ON DELETE CASCADE
    await session.flush()
    await knowledge_evidence.revoke_unsupported_verifications(session, ctx.company_id, targets)
    await audit.record(
        session,
        action=AuditAction.DELETE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="documents",
        entity_id=document_id,
    )
    await session.commit()
    # L'esborrat de l'storage es fa despres del commit: si falla, queda un
    # fitxer orfe (inofensiu) en comptes d'una fila sense fitxer.
    await get_storage().delete(storage_key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{document_id}/download", summary="Descarrega el fitxer")
async def download_document(document_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Excepcio INTENCIONADA a "un GET no escriu".

    Descarregar no modifica cap estat de negoci (ni el document, ni cap
    workspace, paquet o artefacte): nomes deixa constancia de l'acces a
    `audit_logs`. Saber qui s'ha endut un fitxer d'evidencies es una garantia de
    seguretat, no un canvi d'estat. `test_documents.py` exigeix que aquesta fila
    d'auditoria sigui l'unica escriptura.
    """
    document = await get_owned_or_404(session, Document, document_id, ctx.company_id)
    content = await get_storage().download(document.storage_key)
    await audit.record(
        session,
        action=AuditAction.DOWNLOAD,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="documents",
        entity_id=document.id,
    )
    await session.commit()

    def _iterator():
        yield content

    filename = sanitize_filename(document.original_filename)
    return StreamingResponse(
        _iterator(),
        media_type=document.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(document.file_size),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/{document_id}/links", response_model=list[DocumentLinkRead], summary="Enllacos del document"
)
async def list_links(document_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    document = await get_owned_or_404(session, Document, document_id, ctx.company_id)
    return list(document.links)


@router.post(
    "/{document_id}/links",
    response_model=DocumentLinkRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enllaca el document amb una entitat",
)
async def create_link(
    document_id: uuid.UUID, payload: DocumentLinkCreate, session: SessionDep, ctx: CompanyCtx
):
    document = await get_owned_or_404(session, Document, document_id, ctx.company_id)
    await assert_entity_in_company(session, payload.entity_type, payload.entity_id, ctx.company_id)

    link = DocumentLink(
        company_id=ctx.company_id,
        document_id=document.id,
        created_by=ctx.user_id,
        **payload.model_dump(),
    )
    session.add(link)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Aquest document ja esta enllacat amb la mateixa relacio.") from exc
    await session.refresh(link)
    return link


@router.delete(
    "/{document_id}/links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Elimina un enllac",
)
async def delete_link(
    document_id: uuid.UUID, link_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    link = (
        await session.execute(
            sa.select(DocumentLink).where(
                DocumentLink.id == link_id,
                DocumentLink.document_id == document_id,
                DocumentLink.company_id == ctx.company_id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise NotFoundError("Enllac no trobat.")
    target = (link.entity_type, link.entity_id)
    await session.delete(link)
    await session.flush()
    # Sense cap altra evidencia, el coneixement verificat deixa de ser-ho.
    await knowledge_evidence.revoke_unsupported_verifications(session, ctx.company_id, [target])
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@evidence_router.get(
    "/{entity_type}/{entity_id}",
    response_model=list[EvidenceRead],
    summary="Evidencies que justifiquen una dada",
)
async def list_evidence(
    entity_type: LinkedEntityType, entity_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    await assert_entity_in_company(session, entity_type, entity_id, ctx.company_id)
    rows = (
        await session.execute(
            sa.select(DocumentLink, Document)
            .join(Document, Document.id == DocumentLink.document_id)
            .where(
                DocumentLink.company_id == ctx.company_id,
                DocumentLink.entity_type == entity_type,
                DocumentLink.entity_id == entity_id,
            )
            .order_by(DocumentLink.created_at.desc())
        )
    ).all()
    return [
        EvidenceRead(
            link_id=link.id,
            relationship_type=link.relationship_type,
            notes=link.notes,
            document=DocumentRead.model_validate(document),
        )
        for link, document in rows
    ]
