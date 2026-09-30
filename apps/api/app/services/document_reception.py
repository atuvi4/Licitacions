"""Rebre una entrega de documents, vingui del canal que vingui.

Capa 2 del motor d'analisi. El canal (avui el ZIP; dema Drive, una plataforma o
una API) ja ha traduit el que ha rebut a una `Delivery`. Aqui es fa, igual per
a tots els canals:

1. **Desar el contenidor original**, si n'hi havia, al magatzem adrecat per
   SHA-256 on hi ha la resta de plecs.
2. **Validar cada fitxer** contra el contracte de formats dels plecs. Els que no
   hi caben queden omesos, amb motiu.
3. **Crear un `TenderDocument` normal per fitxer** amb `store_uploaded_document`,
   la mateixa porta que una pujada individual, i llegir-ne el text amb
   l'extraccio de sempre. La capa documental no sap per on va arribar.
4. **Demanar la classificacio** de la licitacio sencera (capa 3), que no sap res
   d'aquesta entrega mes enlla de qui l'ha provocada.

Una sola revisio de la licitacio per entrega: deu fitxers que arriben junts son
un sol canvi de l'expedient.

**Si falla a mig cami** (despres d'acceptar l'entrega), la recepcio queda
`failed` amb els documents que ja s'havien creat. No s'esborren: son plecs reals,
desats be, i esborrar-los amagaria que l'entrega va arribar.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.classification import ooxml_text
from app.core.config import settings
from app.core.errors import (
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from app.models.enums import (
    ClassificationTrigger,
    ReceptionItemStatus,
    ReceptionSkipReason,
    ReceptionStatus,
)
from app.models.identity import User
from app.models.reception import TenderDocumentReception, TenderDocumentReceptionItem
from app.models.tenders import Tender
from app.reception.archive import ArchiveLimits
from app.reception.delivery import Delivery, ReceivedFile
from app.services import document_classification
from app.services.document_download import extension_for
from app.services.files import compute_sha256
from app.services.tender_document_processing import (
    _ensure_current_extraction,
    _record_tender_revision,
    get_or_create_content,
)
from app.services.tender_document_upload import store_uploaded_document, validate_tender_upload

logger = logging.getLogger(__name__)

_MB = 1024 * 1024

#: Quan el canal no declara el tipus (dins d'un ZIP no n'hi ha), l'extensio fa de
#: declaracio. Els bytes hi manen igual que en una pujada.
_EXTENSION_MIME = {
    "pdf": "application/pdf",
    "docx": ooxml_text.DOCX,
    "xlsx": ooxml_text.XLSX,
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint",
    "txt": "text/plain",
    "xml": "application/xml",
}

#: Omesos que la persona ha de veure. Els de sistema (`__MACOSX`, `.DS_Store`) no:
#: son soroll de l'eina que va fer l'entrega.
ATTENTION_SKIP_REASONS = frozenset(set(ReceptionSkipReason) - {ReceptionSkipReason.SYSTEM_FILE})


def zip_limits() -> ArchiveLimits:
    return ArchiveLimits(
        max_members=settings.INTAKE_MAX_MEMBERS,
        max_member_bytes=settings.document_max_size_bytes,
        max_total_bytes=settings.INTAKE_MAX_TOTAL_UNCOMPRESSED_MB * _MB,
        max_ratio=settings.INTAKE_MAX_COMPRESSION_RATIO,
    )


def max_container_bytes() -> int:
    return settings.INTAKE_MAX_ARCHIVE_MB * _MB


def _mime_for(received: ReceivedFile) -> tuple[str | None, ReceptionSkipReason | None]:
    extension = received.filename.rsplit(".", 1)[-1].lower() if "." in received.filename else ""
    declared = received.declared_mime_type or _EXTENSION_MIME.get(extension)
    try:
        return validate_tender_upload(received.data or b"", declared), None
    except UnsupportedMediaTypeError:
        return None, ReceptionSkipReason.UNSUPPORTED_TYPE
    except PayloadTooLargeError:
        return None, ReceptionSkipReason.TOO_LARGE
    except ValidationError:
        # Amb un tipus declarat, els bytes el contradiuen. Sense, simplement no
        # es cap format que s'accepti com a plec (una imatge, per exemple).
        reason = (
            ReceptionSkipReason.TYPE_MISMATCH if declared else ReceptionSkipReason.UNSUPPORTED_TYPE
        )
        return None, reason


@dataclass(slots=True)
class ReceptionOutcome:
    #: Nomes identificadors: despres d'un `rollback` els objectes caduquen, i qui
    #: vulgui el detall el torna a llegir.
    reception_id: uuid.UUID
    classification_run_id: uuid.UUID | None
    classification_failed: bool = False


async def receive(
    session: AsyncSession,
    *,
    tender: Tender,
    company_id: uuid.UUID,
    user: User,
    delivery: Delivery,
) -> ReceptionOutcome:
    container_id = None
    if delivery.container is not None:
        mime = delivery.container_mime_type or "application/octet-stream"
        container, _created = await get_or_create_content(
            session,
            data=delivery.container,
            sha256=compute_sha256(delivery.container),
            mime_type=mime,
            detected_extension=extension_for(mime),
        )
        container_id = container.id

    reception = TenderDocumentReception(
        company_id=company_id,
        tender_id=tender.id,
        channel=delivery.channel,
        label=delivery.label[:255],
        reference=delivery.reference[:1000] if delivery.reference else None,
        container_content_id=container_id,
        status=ReceptionStatus.PROCESSING,
        item_count=len(delivery.files),
        accepted_count=0,
        skipped_count=0,
        received_by=user.id,
    )
    session.add(reception)
    await session.flush()

    items: list[TenderDocumentReceptionItem] = []
    pending: list[tuple[TenderDocumentReceptionItem, ReceivedFile]] = []
    for received in delivery.files:
        item = TenderDocumentReceptionItem(
            reception_id=reception.id,
            company_id=company_id,
            position=received.position,
            path=received.path[:1000],
            filename=received.filename[:255],
            size_bytes=received.size_bytes,
            packed_size=received.packed_size,
            source_reference=received.source_reference,
            status=received.status,
            skip_reason=received.skip_reason,
        )
        if received.status is ReceptionItemStatus.ACCEPTED:
            item.sha256 = compute_sha256(received.data or b"")
            mime, reason = _mime_for(received)
            if reason is not None:
                item.status = ReceptionItemStatus.SKIPPED
                item.skip_reason = reason
            else:
                item.detected_mime_type = mime
                pending.append((item, received))
        session.add(item)
        items.append(item)
    await session.commit()
    reception_id = reception.id

    try:
        for item, received in pending:
            document, _file, content = await store_uploaded_document(
                session,
                tender=tender,
                user=user,
                filename=received.filename,
                declared_mime_type=item.detected_mime_type,
                data=received.data or b"",
                mime_type=item.detected_mime_type or "",
            )
            item.tender_document_id = document.id
            await session.commit()
            await _ensure_current_extraction(session, content, force=False)

        reception.accepted_count = len(pending)
        reception.skipped_count = sum(1 for i in items if i.status is ReceptionItemStatus.SKIPPED)
        reception.status = ReceptionStatus.COMPLETED
        reception.completed_at = datetime.now(UTC)
        await session.commit()
        if pending:
            await _record_tender_revision(session, tender.id)
    except Exception:
        await session.rollback()
        failed = await session.get(TenderDocumentReception, reception_id)
        if failed is not None:
            failed.status = ReceptionStatus.FAILED
            failed.error_code = "processing_error"
            failed.completed_at = datetime.now(UTC)
            await session.commit()
        raise

    # La recepcio ja es completa i els documents hi son: si la classificacio
    # falla, aixo no la desfa. Es diu a la resposta i es pot tornar a demanar.
    try:
        run = await document_classification.classify_tender(
            session,
            tender=tender,
            company_id=company_id,
            trigger=ClassificationTrigger.RECEPTION,
            user=user,
            reception_id=reception.id,
        )
    except Exception:
        logger.exception("classificacio fallida despres de la recepcio %s", reception_id)
        await session.rollback()
        return ReceptionOutcome(
            reception_id=reception_id, classification_run_id=None, classification_failed=True
        )
    return ReceptionOutcome(
        reception_id=reception_id, classification_run_id=run.id if run else None
    )


# --------------------------------------------------------------------------- #
# Lectura                                                                      #
# --------------------------------------------------------------------------- #


async def list_receptions(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> list[TenderDocumentReception]:
    rows = await session.scalars(
        sa.select(TenderDocumentReception)
        .where(
            TenderDocumentReception.company_id == company_id,
            TenderDocumentReception.tender_id == tender_id,
        )
        .order_by(TenderDocumentReception.created_at.desc(), TenderDocumentReception.id)
    )
    return list(rows)


async def load_reception(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID, reception_id: uuid.UUID
) -> tuple[TenderDocumentReception, list[TenderDocumentReceptionItem]]:
    reception = await session.scalar(
        sa.select(TenderDocumentReception).where(
            TenderDocumentReception.id == reception_id,
            TenderDocumentReception.company_id == company_id,
            TenderDocumentReception.tender_id == tender_id,
        )
    )
    if reception is None:
        raise NotFoundError("Entrega no trobada.")
    items = await session.scalars(
        sa.select(TenderDocumentReceptionItem)
        .where(TenderDocumentReceptionItem.reception_id == reception.id)
        .order_by(TenderDocumentReceptionItem.position)
    )
    return reception, list(items)
