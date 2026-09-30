"""Recepcio de documents d'una licitacio (capa 2 del motor d'analisi).

Tot penja de `/companies/{company_id}/tenders/{tender_id}/receptions`. Cada canal
te la seva ruta d'entrada (`/zip`, avui; Drive, plataformes o API, dema); totes
acaben al mateix servei (`document_reception.receive`) i tornen el mateix:
l'entrega i com ha quedat classificada la licitacio.

Rebre documents es escriure sobre la licitacio i demana el mateix que pujar un
plec: una licitacio oberta a ma, de la propia empresa, i un rol que la pugui
editar. Llegir les entregues, nomes veure-la.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, UploadFile, status

from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.routers.document_classification import build_read
from app.core.config import settings
from app.core.errors import PayloadTooLargeError, ValidationError
from app.models.enums import AuditAction
from app.reception.archive import ArchiveRejected
from app.reception.zip_channel import delivery_from_zip
from app.schemas.reception import (
    ReceptionItemRead,
    ReceptionRead,
    ReceptionResult,
    ReceptionSummary,
)
from app.services import audit, document_classification, manual_tender
from app.services import document_reception as service
from app.services.review_view import load_visible_tender

router = APIRouter(prefix="/tenders/{tender_id}/receptions", tags=["recepcio de documents"])

_CHUNK = 1024 * 1024


class ArchiveRejectedError(ValidationError):
    """El paquet no s'accepta. `details.reason` diu per que, amb un codi estable."""

    code = "archive_rejected"


async def _read_container(file: UploadFile) -> bytes:
    """Tall dur de mida mentre es llegeix, sense confiar en `Content-Length`."""
    limit = service.max_container_bytes()
    buffer = bytearray()
    while chunk := await file.read(_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise PayloadTooLargeError(
                f"El paquet supera el maxim permes de {settings.INTAKE_MAX_ARCHIVE_MB} MB."
            )
    if not buffer:
        raise ValidationError("El fitxer es buit.")
    return bytes(buffer)


async def _reception_read(session, company_id, tender_id, reception_id) -> ReceptionRead:
    reception, items = await service.load_reception(session, company_id, tender_id, reception_id)
    return ReceptionRead(
        **ReceptionSummary.model_validate(reception).model_dump(),
        items=[
            ReceptionItemRead.model_validate(item).model_copy(
                update={"needs_attention": item.skip_reason in service.ATTENTION_SKIP_REASONS}
            )
            for item in items
        ],
    )


@router.post(
    "/zip",
    response_model=ReceptionResult,
    status_code=status.HTTP_201_CREATED,
    summary="Rep un paquet ZIP de la licitacio",
)
async def receive_zip(
    tender_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    file: UploadFile = File(...),
):
    """Obre el paquet, desa'n cada document i classifica la licitacio sencera.

    Un paquet hostil o malmes (camins que en surten, xifrat, bomba de
    compressio...) es rebutja sencer amb `422 archive_rejected` i no es desa res.
    Els fitxers que no es poden fer servir (buits, massa grans, de sistema) es
    llisten com a omesos, amb el motiu.
    """
    manual_tender.assert_can_edit(ctx.membership.role)
    # Si la classificacio falla, el servei fa `rollback` i els objectes de la
    # sessio (tambe els del context) caduquen: d'aqui en endavant, nomes
    # identificadors capturats abans.
    company_id, user_id = ctx.company_id, ctx.user.id
    tender = await manual_tender.load_editable_tender(session, company_id, tender_id)
    data = await _read_container(file)
    try:
        delivery = delivery_from_zip(data, file.filename, service.zip_limits())
    except ArchiveRejected as exc:
        raise ArchiveRejectedError(
            exc.message, details={"reason": exc.reason, "path": exc.path}
        ) from exc

    outcome = await service.receive(
        session, tender=tender, company_id=company_id, user=ctx.user, delivery=delivery
    )
    reception = await _reception_read(session, company_id, tender_id, outcome.reception_id)
    await audit.record(
        session,
        company_id=company_id,
        user_id=user_id,
        action=AuditAction.CREATE,
        entity_type="tender_document_reception",
        entity_id=outcome.reception_id,
        summary=manual_tender.audit_summary(
            licitacio=tender_id,
            canal=reception.channel,
            fitxers=reception.item_count,
            acceptats=reception.accepted_count,
            omesos=reception.skipped_count,
        ),
    )
    await session.commit()
    return ReceptionResult(
        reception=reception,
        classification=build_read(
            await document_classification.load_view(session, company_id, tender_id)
        ),
        classification_failed=outcome.classification_failed,
    )


@router.get("", response_model=list[ReceptionSummary], summary="Entregues de la licitacio")
async def list_receptions(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    await load_visible_tender(session, ctx.company_id, tender_id)
    return await service.list_receptions(session, ctx.company_id, tender_id)


@router.get(
    "/{reception_id}", response_model=ReceptionRead, summary="Una entrega i els seus fitxers"
)
async def get_reception(
    tender_id: uuid.UUID, reception_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    await load_visible_tender(session, ctx.company_id, tender_id)
    return await _reception_read(session, ctx.company_id, tender_id, reception_id)
