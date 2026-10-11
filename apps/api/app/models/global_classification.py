"""Classificacio GLOBAL dels documents d'una licitacio i revisio de rols (0059).

Fase 3B-2 (spec §3.2). Tres capes que no es barregen mai i que nomes combina,
en llegir, el resolutor de rols efectius (`app/services/document_roles.py`):

1. *Tipus de font*: `TenderDocument.document_type`. L'escriu nomes el connector
   que governa el document (o la propietaria en una licitacio manual).
2. *Classificacio automatica*: `global_document_classification_runs` i les seves
   filles. Immutable: una passada nova es una fila nova, i mai es toca la vella.
   Sense `company_id`: nomes llegeix fets globals (nom del document, sha del
   contingut, extraccio), cap ruta de recepcio de cap empresa.
3. *Rol global revisat*: `document_role_reviews`. Nomes d'afegir (trigger a
   PostgreSQL), amb actor, motiu, versio del fitxer i passada de base. Nomes
   l'operador de plataforma l'escriu.

Les taules tenant de `classification.py` es mantenen per a les licitacions
manuals propies; `tender_document_role_decisions` es l'override d'una empresa.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    ClassificationFindingCode,
    DocumentRole,
    FindingSeverity,
    GlobalClassificationTrigger,
    RoleConfidence,
    RoleReviewAuthority,
    TenderDocumentType,
)
from app.models.review import JSONDocument, in_values
from app.models.tenders import TenderDocument


class GlobalDocumentClassificationRun(UUIDPrimaryKeyMixin, Base):
    """Una passada del classificador sobre els documents ANALITZABLES d'un tender.

    Immutable; nomes una es vigent per tender. Mateixa empremta = mateix
    resultat: no se'n crea cap de nova (unic `(tender, empremta)`).
    """

    __tablename__ = "global_document_classification_runs"
    __table_args__ = (
        sa.CheckConstraint(in_values("trigger", GlobalClassificationTrigger), name="trigger_valid"),
        sa.CheckConstraint("document_count >= 0", name="count_not_negative"),
        sa.UniqueConstraint(
            "tender_id",
            "input_fingerprint",
            name="uq_global_document_classification_runs_fingerprint",
        ),
        sa.Index(
            "uq_global_document_classification_runs_current",
            "tender_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
            sqlite_where=sa.text("is_current = 1"),
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    classifier_version: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: sha256(versio + `document_id|sha256|nom|extraccio` ordenats). Nomes fets globals.
    input_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    trigger: Mapped[GlobalClassificationTrigger] = mapped_column(
        enum_column(GlobalClassificationTrigger, "global_classification_trigger"),
        nullable=False,
    )
    document_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class GlobalDocumentRoleSuggestion(UUIDPrimaryKeyMixin, Base):
    """El rol que va proposar la passada per a un document, ancorat al seu contingut."""

    __tablename__ = "global_document_role_suggestions"
    __table_args__ = (
        sa.CheckConstraint(in_values("role", DocumentRole), name="role_valid"),
        sa.CheckConstraint(in_values("confidence", RoleConfidence), name="confidence_valid"),
        sa.CheckConstraint(
            in_values("document_type", TenderDocumentType), name="document_type_valid"
        ),
        sa.UniqueConstraint(
            "run_id", "tender_document_id", name="uq_global_document_role_suggestions_document"
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("global_document_classification_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tender_document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: La versio del fitxer que es va classificar.
    content_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_document_extractions.id", ondelete="SET NULL"),
    )
    role: Mapped[DocumentRole] = mapped_column(
        enum_column(DocumentRole, "document_role"), nullable=False
    )
    confidence: Mapped[RoleConfidence] = mapped_column(
        enum_column(RoleConfidence, "role_confidence"), nullable=False
    )
    #: Derivat: el tipus del rol nomes amb confianca alta i si no es una copia.
    document_type: Mapped[TenderDocumentType] = mapped_column(
        enum_column(TenderDocumentType, "tender_document_type"), nullable=False
    )
    signals: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)


class GlobalDocumentClassificationFinding(UUIDPrimaryKeyMixin, Base):
    """El que no quadra del conjunt de documents en aquella passada."""

    __tablename__ = "global_document_classification_findings"
    __table_args__ = (
        sa.CheckConstraint(in_values("code", ClassificationFindingCode), name="code_valid"),
        sa.CheckConstraint(in_values("severity", FindingSeverity), name="severity_valid"),
        sa.Index("ix_global_document_classification_findings_run", "run_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("global_document_classification_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[ClassificationFindingCode] = mapped_column(
        enum_column(ClassificationFindingCode, "classification_finding_code"), nullable=False
    )
    severity: Mapped[FindingSeverity] = mapped_column(
        enum_column(FindingSeverity, "finding_severity"), nullable=False
    )
    tender_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_documents.id", ondelete="CASCADE")
    )
    related_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_documents.id", ondelete="CASCADE")
    )
    detail: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)


class DocumentRoleReview(UUIDPrimaryKeyMixin, Base):
    """Rol global revisat per l'operador. Nomes d'afegir: una correccio es una fila nova.

    Ancorat al CONTINGUT dins de la licitacio (`content_sha256`): un fitxer nou
    deixa la revisio sense efecte, i en canvi els mateixos bytes publicats per la
    font en una URL nova (un altre `TenderDocument` del mateix tender) continuen
    revisats. `tender_document_id` diu sobre quin document es va fer.
    `supersedes_id` apunta a la revisio anterior del mateix contingut al tender.

    Les claus cap a passada, suggeriment, cita i revisio anterior son `NO ACTION`
    (no `SET NULL`): el trigger prohibeix qualsevol UPDATE, tambe el d'una FK.
    """

    __tablename__ = "document_role_reviews"
    __table_args__ = (
        sa.CheckConstraint(in_values("role", DocumentRole), name="role_valid"),
        sa.CheckConstraint(in_values("authority", RoleReviewAuthority), name="authority_valid"),
        sa.CheckConstraint("length(trim(reason)) > 0", name="reason_not_empty"),
        sa.Index("ix_document_role_reviews_document", "tender_document_id", "created_at"),
    )

    tender_document_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    role: Mapped[DocumentRole] = mapped_column(
        enum_column(DocumentRole, "document_role"), nullable=False
    )
    authority: Mapped[RoleReviewAuthority] = mapped_column(
        enum_column(RoleReviewAuthority, "role_review_authority"), nullable=False
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    based_on_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("global_document_classification_runs.id", ondelete="NO ACTION"),
    )
    based_on_suggestion_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("global_document_role_suggestions.id", ondelete="NO ACTION"),
    )
    source_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_source_refs.id", ondelete="NO ACTION")
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("document_role_reviews.id", ondelete="NO ACTION")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def review_heads(reviews: Iterable[DocumentRoleReview]) -> dict[str, DocumentRoleReview]:
    """La revisio vigent de cada contingut (`content_sha256`) de la licitacio.

    `reviews` han de ser les revisions dels documents d'UNA licitacio: la clau es
    el contingut, no el document (vegeu `DocumentRoleReview`).

    No n'hi ha prou amb `created_at`: a PostgreSQL `now()` es l'hora de la
    transaccio, i dues revisions d'una mateixa transaccio la comparteixen. La
    cadena `supersedes_id` es la que mana; l'hora nomes desempata (no hauria de
    caldre mai: cada revisio nova substitueix la vigent).
    """
    items = list(reviews)
    superseded = {r.supersedes_id for r in items if r.supersedes_id is not None}
    heads: dict[str, DocumentRoleReview] = {}
    for review in sorted(items, key=lambda r: (r.created_at, str(r.id))):
        if review.id not in superseded:
            heads[review.content_sha256] = review
    return heads


def tender_reviews_query(tender_id: uuid.UUID) -> sa.Select[tuple[DocumentRoleReview]]:
    """Totes les revisions de rol dels documents d'una licitacio (retirats inclosos)."""
    return (
        sa.select(DocumentRoleReview)
        .join(TenderDocument, TenderDocument.id == DocumentRoleReview.tender_document_id)
        .where(TenderDocument.tender_id == tender_id)
    )


# --------------------------------------------------------------------------- #
# Nomes d'afegir, a PostgreSQL (mateix patro que `review_item_revisions`)       #
# --------------------------------------------------------------------------- #

#: Rebutja UPDATE sempre i DELETE directe. Un DELETE per cascada (desapareix el
#: document o la licitacio) passa: s'executa a `pg_trigger_depth() >= 2`.
ROLE_REVIEWS_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION document_role_reviews_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' OR pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'document_role_reviews es nomes d''afegir (%)', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql
"""
ROLE_REVIEWS_APPEND_ONLY_TRIGGER = """
CREATE TRIGGER document_role_reviews_append_only
BEFORE UPDATE OR DELETE ON document_role_reviews
FOR EACH ROW EXECUTE FUNCTION document_role_reviews_append_only()
"""

# La migracio 0059 crea el mateix; aixo cobreix les bases creades des dels models.
_reviews = DocumentRoleReview.__table__
sa.event.listen(
    _reviews,
    "after_create",
    # `DDL` aplica format `%`: el `%` del missatge de RAISE s'ha d'escapar.
    sa.DDL(ROLE_REVIEWS_APPEND_ONLY_FUNCTION.replace("%", "%%")).execute_if(dialect="postgresql"),
)
sa.event.listen(
    _reviews,
    "after_create",
    sa.DDL(ROLE_REVIEWS_APPEND_ONLY_TRIGGER).execute_if(dialect="postgresql"),
)
