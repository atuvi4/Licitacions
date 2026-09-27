"""Capa de revisio canonica (S-R1): el que una EMPRESA accepta d'una licitacio.

Tres idees sostenen el modul.

**L'extraccio no es toca mai.** `requirements`, `requirement_evidence`,
`award_criteria` i la resta son el que va retornar el model, i ho han de poder
continuar dient d'aqui a dos anys. Un element de revisio APUNTA a aquestes files
(`review_item_sources`); no les copia ni les sobreescriu. El que el revisor
accepta viu a `review_items.accepted_json`, i com s'hi ha arribat, a
`review_item_revisions`.

**La revisio es per empresa.** Els plecs i la seva extraccio son globals: si
cent empreses miren la mateixa licitacio, s'analitza una vegada. Pero que un
requisit estigui verificat o descartat es un judici d'una empresa concreta. Per
aixo totes les taules porten `company_id`, i les fonts, l'historial i les
relacions referencien l'element amb `(item_id, company_id)`: la base de dades
no deixa penjar res d'una empresa d'un element d'una altra.

**L'historial nomes creix.** `review_item_revisions` no te cap cami
d'actualitzacio ni d'esborrat a l'aplicacio, i a PostgreSQL un trigger ho
rebutja (tret de l'esborrat en cascada quan desapareix l'empresa o la
licitacio). Els camps de revisio LEGACY de `requirements` (`review_status`,
`manual_override_json`, `review_note`...) no formen part d'aquesta capa.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    ReviewAction,
    ReviewAwardRole,
    ReviewItemKind,
    ReviewOriginHint,
    ReviewRelationType,
    ReviewSourceRole,
    ReviewVerification,
)

#: JSON a tot arreu i JSONB a PostgreSQL: s'hi podra consultar per clau.
JSONDocument = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def in_values(column: str, enum_cls: type) -> str:
    """`col IN (...)` amb els valors de l'enum: el CHECK el genera el mateix enum."""
    return "{} IN ({})".format(column, ", ".join(f"'{member.value}'" for member in enum_cls))


#: Una font es exactament UNA de les tres columnes.
EXACTLY_ONE_SOURCE = (
    "(CASE WHEN requirement_id IS NOT NULL THEN 1 ELSE 0 END"
    " + CASE WHEN award_criterion_id IS NOT NULL THEN 1 ELSE 0 END"
    " + CASE WHEN source_ref_id IS NOT NULL THEN 1 ELSE 0 END) = 1"
)
_ACTIVE_PRIMARY = "link_role = 'primary' AND unlinked_at IS NULL"


class ReviewItem(UUIDPrimaryKeyMixin, Base):
    """Un requisit o criteri tal com el veu i l'accepta una empresa."""

    __tablename__ = "review_items"
    __table_args__ = (
        sa.CheckConstraint(in_values("kind", ReviewItemKind), name="kind_valid"),
        sa.CheckConstraint(
            in_values("verification_status", ReviewVerification), name="verification_valid"
        ),
        sa.CheckConstraint(
            "award_role IS NULL OR " + in_values("award_role", ReviewAwardRole),
            name="award_role_valid",
        ),
        sa.CheckConstraint(in_values("origin_hint", ReviewOriginHint), name="origin_hint_valid"),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        # Les taules filles referencien `(id, company_id[, tender_id])`: aixi la
        # base garanteix que res d'una empresa penja d'un element d'una altra.
        sa.UniqueConstraint("id", "company_id", name="uq_review_items_id_company"),
        sa.UniqueConstraint(
            "id", "company_id", "tender_id", name="uq_review_items_id_company_tender"
        ),
        sa.Index("ix_review_items_scope", "company_id", "tender_id", "kind", "archived_at"),
        sa.Index("ix_review_items_verification", "company_id", "tender_id", "verification_status"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[ReviewItemKind] = mapped_column(
        enum_column(ReviewItemKind, "review_item_kind"), nullable=False
    )
    verification_status: Mapped[ReviewVerification] = mapped_column(
        enum_column(ReviewVerification, "review_verification"), nullable=False
    )
    award_role: Mapped[ReviewAwardRole | None] = mapped_column(
        enum_column(ReviewAwardRole, "review_award_role")
    )
    accepted_json: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)
    origin_hint: Mapped[ReviewOriginHint] = mapped_column(
        enum_column(ReviewOriginHint, "review_origin_hint"), nullable=False
    )
    #: Numero de la darrera revisio. Serveix de control de concurrencia optimista.
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class ReviewItemSource(UUIDPrimaryKeyMixin, Base):
    """Quina fila immutable sosté un element canonic, i com.

    **Esborrat de les fonts: `NO ACTION`, no `RESTRICT`.** Tots dos impedeixen
    esborrar una run o un requisit mentre un historial de revisio hi apunta. La
    diferencia es QUAN es comprova: `NO ACTION` ho fa al final de la sentencia, i
    per aixo esborrar una licitacio o una empresa —que arrossega en cascada
    tant l'extraccio com la revisio en la mateixa sentencia— continua
    funcionant. `RESTRICT` es comprova a l'instant i podria fallar segons l'ordre
    de les cascades. L'aplicacio no esborra runs ni requisits enlloc.
    """

    __tablename__ = "review_item_sources"
    __table_args__ = (
        sa.CheckConstraint(EXACTLY_ONE_SOURCE, name="exactly_one_source"),
        sa.CheckConstraint(in_values("link_role", ReviewSourceRole), name="link_role_valid"),
        # Una cita (`tender_source_refs`) nomes pot ser evidencia addicional:
        # l'element sempre neix d'un requisit o d'un criteri.
        sa.CheckConstraint(
            "source_ref_id IS NULL OR link_role = 'extra_evidence'",
            name="source_ref_is_extra_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "company_id"],
            ["review_items.id", "review_items.company_id"],
            ondelete="CASCADE",
        ),
        sa.Index(
            "uq_review_item_sources_active_primary",
            "item_id",
            unique=True,
            postgresql_where=sa.text(_ACTIVE_PRIMARY),
            sqlite_where=sa.text(_ACTIVE_PRIMARY),
        ),
        sa.Index(
            "uq_review_item_sources_company_requirement",
            "company_id",
            "requirement_id",
            unique=True,
            postgresql_where=sa.text(f"requirement_id IS NOT NULL AND {_ACTIVE_PRIMARY}"),
            sqlite_where=sa.text(f"requirement_id IS NOT NULL AND {_ACTIVE_PRIMARY}"),
        ),
        sa.Index(
            "uq_review_item_sources_company_criterion",
            "company_id",
            "award_criterion_id",
            unique=True,
            postgresql_where=sa.text(f"award_criterion_id IS NOT NULL AND {_ACTIVE_PRIMARY}"),
            sqlite_where=sa.text(f"award_criterion_id IS NOT NULL AND {_ACTIVE_PRIMARY}"),
        ),
        sa.Index("ix_review_item_sources_item", "item_id"),
        sa.Index("ix_review_item_sources_requirement", "requirement_id"),
        sa.Index("ix_review_item_sources_criterion", "award_criterion_id"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="NO ACTION")
    )
    award_criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("award_criteria.id", ondelete="NO ACTION")
    )
    source_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_source_refs.id", ondelete="NO ACTION")
    )
    link_role: Mapped[ReviewSourceRole] = mapped_column(
        enum_column(ReviewSourceRole, "review_source_role"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    unlinked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class ReviewItemRevision(UUIDPrimaryKeyMixin, Base):
    """Una entrada de l'historial de revisio. Nomes s'hi afegeix."""

    __tablename__ = "review_item_revisions"
    __table_args__ = (
        sa.CheckConstraint(in_values("action", ReviewAction), name="action_valid"),
        sa.CheckConstraint(
            in_values("verification_status_after", ReviewVerification),
            name="verification_after_valid",
        ),
        sa.CheckConstraint("revision_no >= 1", name="revision_no_positive"),
        sa.UniqueConstraint("item_id", "revision_no", name="uq_review_item_revisions_item_no"),
        sa.ForeignKeyConstraint(
            ["item_id", "company_id"],
            ["review_items.id", "review_items.company_id"],
            ondelete="CASCADE",
        ),
        sa.Index("ix_review_item_revisions_item", "item_id", "revision_no"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    action: Mapped[ReviewAction] = mapped_column(
        enum_column(ReviewAction, "review_action"), nullable=False
    )
    verification_status_after: Mapped[ReviewVerification] = mapped_column(
        enum_column(ReviewVerification, "review_verification_after"), nullable=False
    )
    accepted_json_after: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)
    note: Mapped[str | None] = mapped_column(sa.Text)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONDocument)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class ReviewItemRelation(UUIDPrimaryKeyMixin, Base):
    """Relacio dirigida entre dos elements canonics de la mateixa empresa i licitacio."""

    __tablename__ = "review_item_relations"
    __table_args__ = (
        sa.CheckConstraint(
            in_values("relation_type", ReviewRelationType), name="relation_type_valid"
        ),
        sa.CheckConstraint("from_item_id <> to_item_id", name="not_self"),
        # `(item, empresa, licitacio)`: una relacio no pot creuar empreses ni licitacions.
        sa.ForeignKeyConstraint(
            ["from_item_id", "company_id", "tender_id"],
            ["review_items.id", "review_items.company_id", "review_items.tender_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_item_id", "company_id", "tender_id"],
            ["review_items.id", "review_items.company_id", "review_items.tender_id"],
            ondelete="CASCADE",
        ),
        sa.Index(
            "uq_review_item_relations_active",
            "from_item_id",
            "to_item_id",
            "relation_type",
            unique=True,
            postgresql_where=sa.text("archived_at IS NULL"),
            sqlite_where=sa.text("archived_at IS NULL"),
        ),
        sa.Index("ix_review_item_relations_from", "from_item_id"),
        sa.Index("ix_review_item_relations_to", "to_item_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    from_item_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    to_item_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    relation_type: Mapped[ReviewRelationType] = mapped_column(
        enum_column(ReviewRelationType, "review_relation_type"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    archived_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )


# --------------------------------------------------------------------------- #
# Historial nomes d'afegir, a PostgreSQL                                        #
# --------------------------------------------------------------------------- #

#: Rebutja UPDATE sempre i DELETE directe. Un DELETE que arriba per una cascada
#: (desapareix l'element, l'empresa o la licitacio) passa: aquell s'executa dins
#: del trigger d'integritat referencial, a `pg_trigger_depth() >= 2`.
APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION review_item_revisions_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' OR pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'review_item_revisions es nomes d''afegir (%)', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql
"""
APPEND_ONLY_TRIGGER = """
CREATE TRIGGER review_item_revisions_append_only
BEFORE UPDATE OR DELETE ON review_item_revisions
FOR EACH ROW EXECUTE FUNCTION review_item_revisions_append_only()
"""
DROP_APPEND_ONLY_TRIGGER = (
    "DROP TRIGGER IF EXISTS review_item_revisions_append_only ON review_item_revisions"
)
DROP_APPEND_ONLY_FUNCTION = "DROP FUNCTION IF EXISTS review_item_revisions_append_only()"

# La migracio 0040 crea el mateix; aixo cobreix les bases creades des dels
# models (`metadata.create_all`, el gate PostgreSQL dels tests).
_revisions = ReviewItemRevision.__table__
sa.event.listen(
    _revisions,
    "after_create",
    # `DDL` aplica format `%`: el `%` del missatge de RAISE s'ha d'escapar.
    sa.DDL(APPEND_ONLY_FUNCTION.replace("%", "%%")).execute_if(dialect="postgresql"),
)
sa.event.listen(
    _revisions,
    "after_create",
    sa.DDL(APPEND_ONLY_TRIGGER).execute_if(dialect="postgresql"),
)
