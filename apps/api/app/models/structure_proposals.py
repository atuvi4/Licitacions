"""Provenance de l'estructura del plec i propostes immutables (0060, 3B-2 §4.1-§4.2).

Dues peces:

* **`StructureProvenanceMixin`**: les columnes de provenance de les set taules
  d'estructura i abast (`tender_blocks`, `tender_sites`, `tender_scope_items`,
  `requirement_capability_needs`, `tender_contract_facts`,
  `requirement_territories`, `requirement_award_links`). `origin` diu d'on surt
  el valor VIGENT (l'ultim escriptor); `created_by` qui va crear la fila i
  `updated_by` qui la va tocar per ultim cop; `authority` amb quina autoritat;
  `producer_run_id` la passada d'un productor (sense FK: pot ser una run
  d'extraccio, la del gold...); `proposal_id` la proposta promoguda.
  Cap d'aquestes columnes entra a l'empremta d'estructura: no son «el que el
  plec demana», sino d'on ho sabem.
* **`TenderStructureProposal`** (`tender_structure_proposals`, GLOBAL, sense
  `company_id`): una afirmacio sobre l'estructura del plec que encara no s'ha
  acceptat. Porta la forma exacta del servei intern (`payload_json`) i almenys
  una cita (`source_ref_ids`, CHECK a la base). Es immutable: nomes canvia
  l'estat, una sola vegada (`proposed` -> `promoted` | `rejected`). A
  PostgreSQL un trigger ho imposa; a SQLite (nomes tests) ho fa el model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, validates

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    StructureAuthority,
    StructureEntityKind,
    StructureOrigin,
    StructureProposalProducer,
    StructureProposalStatus,
)
from app.models.review import JSONDocument, in_values

PROPOSALS_TABLE = "tender_structure_proposals"

#: Les set taules que porten provenance (0060).
PROVENANCE_TABLES = (
    "tender_blocks",
    "tender_sites",
    "tender_scope_items",
    "requirement_capability_needs",
    "tender_contract_facts",
    "requirement_territories",
    "requirement_award_links",
)


def provenance_checks() -> tuple[sa.CheckConstraint, sa.CheckConstraint]:
    """Els dos CHECK d'enum de la provenance (es posen a `__table_args__`)."""
    return (
        sa.CheckConstraint(in_values("origin", StructureOrigin), name="origin_valid"),
        sa.CheckConstraint(
            "authority IS NULL OR " + in_values("authority", StructureAuthority),
            name="authority_valid",
        ),
    )


class StructureProvenanceMixin:
    """Columnes de provenance. Les FK porten un nom curt explicit (la convencio
    amb la taula referida passaria dels 63 caracters de PostgreSQL)."""

    @declared_attr
    @classmethod
    def origin(cls) -> Mapped[StructureOrigin]:
        return mapped_column(enum_column(StructureOrigin, "structure_origin"), nullable=False)

    @declared_attr
    @classmethod
    def authority(cls) -> Mapped[StructureAuthority | None]:
        return mapped_column(enum_column(StructureAuthority, "structure_authority"))

    @declared_attr
    @classmethod
    def producer_run_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(sa.Uuid(as_uuid=True))

    @declared_attr
    @classmethod
    def proposal_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                f"{PROPOSALS_TABLE}.id",
                ondelete="SET NULL",
                name=f"fk_{cls.__tablename__}_proposal_id",
            ),
        )

    @declared_attr
    @classmethod
    def created_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name=f"fk_{cls.__tablename__}_created_by"
            ),
        )

    @declared_attr
    @classmethod
    def updated_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name=f"fk_{cls.__tablename__}_updated_by"
            ),
        )


class EmptyProposalCitationsError(ValueError):
    """Una proposta sense cap cita no existeix (tambe a SQLite)."""


#: Les columnes que no canvien mai despres d'inserir (el contingut de la proposta).
IMMUTABLE_COLUMNS = (
    "tender_id",
    "producer",
    "producer_run_id",
    "entity_kind",
    "natural_key",
    "payload_json",
    "source_ref_ids",
    "created_at",
)


class TenderStructureProposal(UUIDPrimaryKeyMixin, Base):
    """Una afirmacio sobre l'estructura del plec, amb cita, encara no acceptada."""

    __tablename__ = PROPOSALS_TABLE
    __table_args__ = (
        sa.CheckConstraint(in_values("producer", StructureProposalProducer), name="producer_valid"),
        sa.CheckConstraint(in_values("entity_kind", StructureEntityKind), name="entity_kind_valid"),
        sa.CheckConstraint(in_values("status", StructureProposalStatus), name="status_valid"),
        sa.CheckConstraint("length(trim(natural_key)) > 0", name="natural_key_not_empty"),
        # Cap proposta sense cita. El mateix nom a les dues bases, amb la funcio de cadascuna.
        sa.CheckConstraint(
            "CASE WHEN jsonb_typeof(source_ref_ids) = 'array' "
            "THEN jsonb_array_length(source_ref_ids) > 0 ELSE false END",
            name="source_refs_not_empty",
        ).ddl_if(dialect="postgresql"),
        sa.CheckConstraint(
            "json_array_length(source_ref_ids) > 0", name="source_refs_not_empty"
        ).ddl_if(dialect="sqlite"),
        sa.CheckConstraint(
            "(status = 'promoted') = (promoted_at IS NOT NULL)", name="promoted_has_date"
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (rejected_at IS NOT NULL)", name="rejected_has_date"
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR length(trim(coalesce(rejected_reason, ''))) > 0",
            name="rejected_has_reason",
        ),
        sa.Index("ix_tender_structure_proposals_tender", "tender_id", "status"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    producer: Mapped[StructureProposalProducer] = mapped_column(
        enum_column(StructureProposalProducer, "structure_proposal_producer"), nullable=False
    )
    #: La passada del productor (sense FK, com a la provenance de les entitats).
    producer_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    entity_kind: Mapped[StructureEntityKind] = mapped_column(
        enum_column(StructureEntityKind, "structure_entity_kind"), nullable=False
    )
    #: Codi de bloc, identitat de seu, `dedup_key` del requisit... (vegeu el servei).
    natural_key: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    #: La forma exacta de l'entrada del servei intern per a `entity_kind`.
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)
    #: Identificadors de `tender_source_refs` (cadenes). Mai buit.
    source_ref_ids: Mapped[list[str]] = mapped_column(JSONDocument, nullable=False)
    status: Mapped[StructureProposalStatus] = mapped_column(
        enum_column(StructureProposalStatus, "structure_proposal_status"),
        nullable=False,
        default=StructureProposalStatus.PROPOSED,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_structure_proposals_created_by"),
    )
    promoted_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_structure_proposals_promoted_by"),
    )
    promoted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_structure_proposals_rejected_by"),
    )
    rejected_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(sa.String(500))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    @validates("source_ref_ids")
    def _validate_source_ref_ids(self, _key: str, value: Any) -> list[str]:
        if not isinstance(value, list) or not value:
            raise EmptyProposalCitationsError("Una proposta necessita almenys una cita.")
        return [str(item) for item in value]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderStructureProposal {self.entity_kind} {self.natural_key} {self.status}>"


class ProposalImmutableError(RuntimeError):
    """Es vol canviar el contingut d'una proposta, o decidir-ne una de ja decidida."""


def _rewritten(state, name: str) -> bool:
    """Cert si la columna passa a un valor NO nul diferent (un `SET NULL` passa)."""
    history = state.attrs[name].history
    return bool(history.has_changes() and history.added and history.added[0] is not None)


@sa.event.listens_for(TenderStructureProposal, "before_update")
def _refuse_content_changes(_mapper, _connection, target: TenderStructureProposal) -> None:
    """El mateix que el trigger de PostgreSQL, per a les bases sense trigger."""
    state = sa.inspect(target)
    for name in IMMUTABLE_COLUMNS:
        if state.attrs[name].history.has_changes():
            raise ProposalImmutableError(f"La proposta es immutable ({name}).")
    if _rewritten(state, "created_by"):
        raise ProposalImmutableError("La proposta es immutable (created_by).")
    status = state.attrs.status.history
    previous = status.deleted[0] if status.has_changes() and status.deleted else target.status
    if previous is not None and str(previous) != StructureProposalStatus.PROPOSED.value:
        if status.has_changes():
            raise ProposalImmutableError("La proposta ja estava decidida.")
        for name in ("promoted_at", "rejected_at", "rejected_reason"):
            if state.attrs[name].history.has_changes():
                raise ProposalImmutableError(f"La proposta ja estava decidida ({name}).")
        for name in ("promoted_by", "rejected_by"):
            if _rewritten(state, name):
                raise ProposalImmutableError(f"La proposta ja estava decidida ({name}).")


@sa.event.listens_for(TenderStructureProposal, "before_delete")
def _refuse_direct_delete(_mapper, _connection, _target: TenderStructureProposal) -> None:
    """Cap DELETE directe des de l'ORM (paritat amb el trigger). Una cascada de la
    licitacio la fa la base (`ON DELETE CASCADE`), no l'ORM, i continua passant."""
    raise ProposalImmutableError("La proposta es immutable (DELETE).")


# --------------------------------------------------------------------------- #
# Immutable a PostgreSQL                                                         #
# --------------------------------------------------------------------------- #

#: Rebutja: un DELETE directe (una cascada de la licitacio passa); canviar el
#: contingut o qui la va crear; tornar a decidir una proposta decidida o tocar-ne
#: la decisio (tambe qui la va decidir). Un `SET NULL` dels actors (usuari
#: esborrat) passa.
PROPOSALS_IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION tender_structure_proposals_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() < 2 THEN
            RAISE EXCEPTION 'tender_structure_proposals es immutable (%)', TG_OP
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.tender_id IS DISTINCT FROM OLD.tender_id
        OR NEW.producer IS DISTINCT FROM OLD.producer
        OR NEW.producer_run_id IS DISTINCT FROM OLD.producer_run_id
        OR NEW.entity_kind IS DISTINCT FROM OLD.entity_kind
        OR NEW.natural_key IS DISTINCT FROM OLD.natural_key
        OR NEW.payload_json IS DISTINCT FROM OLD.payload_json
        OR NEW.source_ref_ids IS DISTINCT FROM OLD.source_ref_ids
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR (NEW.created_by IS NOT NULL AND NEW.created_by IS DISTINCT FROM OLD.created_by)
        OR (OLD.status <> 'proposed' AND (
            NEW.status IS DISTINCT FROM OLD.status
            OR NEW.promoted_at IS DISTINCT FROM OLD.promoted_at
            OR NEW.rejected_at IS DISTINCT FROM OLD.rejected_at
            OR NEW.rejected_reason IS DISTINCT FROM OLD.rejected_reason
            OR (NEW.promoted_by IS NOT NULL AND NEW.promoted_by IS DISTINCT FROM OLD.promoted_by)
            OR (NEW.rejected_by IS NOT NULL AND NEW.rejected_by IS DISTINCT FROM OLD.rejected_by)
        ))
    THEN
        RAISE EXCEPTION 'tender_structure_proposals es immutable (%)', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
PROPOSALS_IMMUTABLE_TRIGGER = """
CREATE TRIGGER tender_structure_proposals_immutable
BEFORE UPDATE OR DELETE ON tender_structure_proposals
FOR EACH ROW EXECUTE FUNCTION tender_structure_proposals_immutable()
"""

# La migracio 0060 crea el mateix; aixo cobreix les bases creades des dels models.
_proposals = TenderStructureProposal.__table__
sa.event.listen(
    _proposals,
    "after_create",
    # `DDL` aplica format `%`: el `%` del missatge de RAISE s'ha d'escapar.
    sa.DDL(PROPOSALS_IMMUTABLE_FUNCTION.replace("%", "%%")).execute_if(dialect="postgresql"),
)
sa.event.listen(
    _proposals,
    "after_create",
    sa.DDL(PROPOSALS_IMMUTABLE_TRIGGER).execute_if(dialect="postgresql"),
)
