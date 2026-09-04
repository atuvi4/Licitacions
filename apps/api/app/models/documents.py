"""Evidencia corporativa: documents i el seu vincle amb les dades que justifiquen.

DECISIO D'ARQUITECTURA (enllac polimorfic)
------------------------------------------
`document_links` relaciona un document amb qualsevol entitat mitjancant
(`entity_type`, `entity_id`) en comptes de tenir una taula pont per tipus.

Avantatges:
  * Afegir un tipus nou (Tender, Requirement, Lot...) no requereix taules noves.
  * Una unica API i una unica UI per gestionar evidencies.
  * Consultes uniformes del tipus "totes les evidencies d'aquesta empresa".

Inconvenients (assumits i mitigats):
  * No hi ha FK cap a l'entitat -> possibles enllacos orfes.
      Mitigacio: `entity_type` es un enum tancat validat; el servei comprova
      que l'entitat existeix i pertany a la mateixa empresa abans d'inserir;
      en esborrar una entitat s'esborren els seus enllacos explicitament.
  * No es pot fer JOIN declaratiu automatic.
      Mitigacio: `company_id` denormalitzat al link (amb FK real), que es el
      filtre que fem servir el 90% de les vegades.

Es una decisio reversible: qualsevol tipus concret es pot promoure mes
endavant a taula pont dedicada sense trencar la resta.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    ActorMixin,
    Base,
    CompanyOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)
from app.models.enums import DocumentCategory, LinkedEntityType, RelationshipType


class Document(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base):
    """Metadada d'un fitxer. El binari viu a l'object storage, mai a PostgreSQL.

    El nom del fitxer original NO es metadata de confianca: `name`, `category`,
    `issued_at` i `expires_at` son camps propis, editables i independents.
    """

    __tablename__ = "documents"
    __table_args__ = (
        sa.UniqueConstraint("storage_key", name="uq_documents_storage_key"),
        sa.Index("ix_documents_company_sha256", "company_id", "sha256"),
        sa.CheckConstraint("file_size >= 0", name="document_size_positive"),
    )

    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    file_size: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    category: Mapped[DocumentCategory] = mapped_column(
        enum_column(DocumentCategory, "document_category"),
        nullable=False,
        default=DocumentCategory.OTHER,
    )
    description: Mapped[str | None] = mapped_column(sa.Text)
    issued_at: Mapped[date | None] = mapped_column(sa.Date)
    expires_at: Mapped[date | None] = mapped_column(sa.Date)
    uploaded_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    links: Mapped[list[DocumentLink]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )


class DocumentLink(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """DADA -> EVIDENCIA. Vincle entre un document i l'entitat que justifica."""

    __tablename__ = "document_links"
    __table_args__ = (
        sa.UniqueConstraint(
            "document_id",
            "entity_type",
            "entity_id",
            "relationship_type",
            name="uq_document_links_unique_link",
        ),
        sa.Index("ix_document_links_entity", "entity_type", "entity_id"),
        sa.Index("ix_document_links_company_entity", "company_id", "entity_type", "entity_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[LinkedEntityType] = mapped_column(
        enum_column(LinkedEntityType, "linked_entity_type"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    relationship_type: Mapped[RelationshipType] = mapped_column(
        enum_column(RelationshipType, "relationship_type"),
        nullable=False,
        default=RelationshipType.SUPPORTING_DOCUMENT,
    )
    notes: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )

    document: Mapped[Document] = relationship(back_populates="links")
