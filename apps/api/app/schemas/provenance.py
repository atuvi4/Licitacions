"""Com es llegeix una cita (S3-S4).

La regla dura es la mateixa que a la resta de la capa documental: `storage_key`
no apareix enlloc. I n'hi ha una de propia: el read-model ha de deixar entendre
i reconstruir la cita **sense consultar que es vigent ara**, perque justament el
que la fa util es que parla d'aleshores i no d'ara.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import SourceRefKind


class TenderSourceRefRead(BaseModel):
    """Una cita, resolta.

    `document_id` i `document_name` poden ser `None` —el document logic es
    nomes ajuda de navegacio i pot desapareixer— i la cita segueix sent valida:
    el que la sosté son `file_id`/`extraction_id` i el `content_sha256`.
    """

    id: uuid.UUID
    ref_kind: SourceRefKind
    tender_id: uuid.UUID

    #: Nomes per navegar. Si es `None`, la cita continua sent reconstruible.
    document_id: uuid.UUID | None = None
    document_name: str | None = None

    file_id: uuid.UUID | None = None
    version_number: int | None = None
    #: Els bytes exactes. Es el que fa que la cita no depengui de res mes.
    content_sha256: str

    extraction_id: uuid.UUID | None = None
    page_id: uuid.UUID | None = None
    page_number: int | None = None

    location_label: str | None = None
    quote: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    created_at: datetime


def read_of(resolved) -> TenderSourceRefRead:
    """Projecta una cita resolta. Cap camp intern hi arriba."""
    ref = resolved.ref
    return TenderSourceRefRead(
        id=ref.id,
        ref_kind=ref.ref_kind,
        tender_id=ref.tender_id,
        document_id=resolved.document_id,
        document_name=resolved.document_name,
        file_id=resolved.file_id,
        version_number=resolved.version_number,
        content_sha256=resolved.content_sha256,
        extraction_id=resolved.extraction_id,
        page_id=resolved.page_id,
        page_number=resolved.page_number,
        location_label=ref.location_label,
        quote=ref.quote,
        char_start=ref.char_start,
        char_end=ref.char_end,
        created_at=ref.created_at,
    )
