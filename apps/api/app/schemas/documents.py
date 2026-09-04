"""Esquemes de documents i enllacos d'evidencia."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field

from app.models.enums import DocumentCategory, LinkedEntityType, RelationshipType
from app.schemas.common import APIModel, AuditFields, ExpirableMixin, ORMModel


class DocumentMetadataBase(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: DocumentCategory | None = None
    description: str | None = None
    issued_at: date | None = None
    expires_at: date | None = None


class DocumentUpdate(DocumentMetadataBase):
    """Edicio de metadata. El binari, el hash i la mida son immutables."""


class DocumentLinkCreate(APIModel):
    entity_type: LinkedEntityType
    entity_id: uuid.UUID
    relationship_type: RelationshipType = RelationshipType.SUPPORTING_DOCUMENT
    notes: str | None = None


class DocumentLinkRead(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    entity_type: LinkedEntityType
    entity_id: uuid.UUID
    relationship_type: RelationshipType
    notes: str | None = None
    created_at: datetime


class DocumentRead(AuditFields, ExpirableMixin):
    company_id: uuid.UUID
    name: str
    original_filename: str
    mime_type: str
    file_size: int
    sha256: str
    category: DocumentCategory
    description: str | None = None
    issued_at: date | None = None
    uploaded_at: datetime
    links: list[DocumentLinkRead] = []


class EvidenceRead(ORMModel):
    """Vista "des de l'entitat": quina evidencia justifica aquesta dada."""

    link_id: uuid.UUID
    relationship_type: RelationshipType
    notes: str | None = None
    document: DocumentRead
