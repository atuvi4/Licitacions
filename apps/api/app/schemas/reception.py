"""Contracte extern de la recepcio de documents (capa 2)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import (
    ReceptionChannel,
    ReceptionItemStatus,
    ReceptionSkipReason,
    ReceptionStatus,
)
from app.schemas.classification import ClassificationRead
from app.schemas.common import ORMModel


class ReceptionSummary(ORMModel):
    id: uuid.UUID
    tender_id: uuid.UUID
    channel: ReceptionChannel
    label: str
    reference: str | None
    status: ReceptionStatus
    error_code: str | None
    item_count: int
    accepted_count: int
    skipped_count: int
    created_at: datetime
    completed_at: datetime | None


class ReceptionItemRead(ORMModel):
    id: uuid.UUID
    position: int
    path: str
    filename: str
    size_bytes: int
    sha256: str | None
    detected_mime_type: str | None
    source_reference: str | None
    status: ReceptionItemStatus
    skip_reason: ReceptionSkipReason | None
    tender_document_id: uuid.UUID | None
    #: Omes per un motiu que la persona ha de veure (no un fitxer de sistema).
    needs_attention: bool = False


class ReceptionRead(ReceptionSummary):
    items: list[ReceptionItemRead]


class ReceptionResult(BaseModel):
    """El que torna rebre una entrega: l'entrega i com ha quedat la licitacio."""

    reception: ReceptionRead
    classification: ClassificationRead
    #: La recepcio ha anat be pero la classificacio no. Es pot tornar a demanar.
    classification_failed: bool = False
