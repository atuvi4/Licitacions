"""Completesa del paquet documental d'una licitacio publica (0058).

`tender_package_gaps` diu QUE FALTA al paquet: un rol obligatori sense document,
un document que els plecs citen i no tenim, un fitxer sense text o amb un format
que no sabem llegir. Es GLOBAL (cap `company_id`): el paquet es del plec, no de
cap empresa, i el que falta es el mateix per a totes.

Un forat no s'esborra mai: es RESOL (`resolved_at`, i el document que el cobreix
a `resolved_by_document_id`). Mentre no estigui resolt, `(tender, code, key)` es
unic: la mateixa troballa no es duplica entre classificacions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import PackageGapCode, PackageGapProducer
from app.models.review import JSONDocument, in_values

#: Clau de deduplicacio d'un forat dins del seu `detail` (nom o referencia citada).
_GAP_KEY = "coalesce(detail ->> 'key', '')"
_OPEN = "resolved_at IS NULL"


class TenderPackageGap(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tender_package_gaps"
    __table_args__ = (
        sa.Index("ix_tender_package_gaps_tender_id", "tender_id"),
        sa.Index(
            "uq_tender_package_gaps_open",
            "tender_id",
            "code",
            sa.text(_GAP_KEY),
            unique=True,
            postgresql_where=sa.text(_OPEN),
            sqlite_where=sa.text(_OPEN),
        ),
        sa.CheckConstraint(in_values("code", PackageGapCode), name="code_valid"),
        sa.CheckConstraint(in_values("producer", PackageGapProducer), name="producer_valid"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[PackageGapCode] = mapped_column(
        enum_column(PackageGapCode, "package_gap_code"), nullable=False
    )
    #: Nom o referencia citada; `detail.key` en fa la clau de deduplicacio.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONDocument)
    referenced_from_source_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_source_refs.id", ondelete="SET NULL")
    )
    #: Llista d'entitats/camps que aquest document aportaria.
    would_provide: Mapped[list[Any] | None] = mapped_column(JSONDocument)
    producer: Mapped[PackageGapProducer] = mapped_column(
        enum_column(PackageGapProducer, "package_gap_producer"), nullable=False
    )
    #: Passada que l'ha produit. Sense FK: la classificacio global arriba a 0059.
    producer_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    resolved_by_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_documents.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
