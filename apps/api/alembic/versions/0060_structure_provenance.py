"""Provenance de l'estructura del plec i propostes immutables.

Fase 3B-2 (spec §4.1-§4.2). Additiva i reversible.

Set taules d'estructura i abast (`tender_blocks`, `tender_sites`,
`tender_scope_items`, `requirement_capability_needs`, `tender_contract_facts`,
`requirement_territories`, `requirement_award_links`) guanyen:

* `origin` (NOT NULL): d'on surt el valor vigent (`extracted`, `operator`,
  `promoted`, `owner_manual`, `gold`). Backfill: `owner_manual`, i `gold` per a
  les files d'una licitacio de font `gold:*` (el carregador d'avaluacio). Fins
  ara nomes hi escrivien els routers del propietari i el gold.
* `producer_run_id` (UUID, sense FK), `proposal_id` (FK a la proposta, SET NULL),
  `created_by`/`updated_by` (FK `users`, SET NULL) i `authority` (nul·lable:
  `platform_operator`, `tender_owner`, `promoted_from_company`, `producer`). El
  backfill no inventa ni autor ni autoritat: queden a NULL.

Els set `*_source_ref_links` de l'estructura i dels requisits guanyen
`producer_run_id` (UUID, sense FK): un enllac nou de la via interna sense actor
porta la passada del productor (mai NULL alhora que `created_by`; ho imposa el
servei: `created_by` es SET NULL i un CHECK impediria esborrar un usuari).

Taula nova global `tender_structure_proposals` (sense `company_id`): una
afirmacio amb cita sobre l'estructura, en la forma del servei intern. CHECK
`source_refs_not_empty` (PostgreSQL `jsonb_array_length`, SQLite
`json_array_length`). Immutable a PostgreSQL per trigger: nomes canvia l'estat,
una sola vegada (`proposed` -> `promoted` | `rejected`); `created_by` no es
reescriu mai, i `promoted_by`/`rejected_by` tampoc un cop decidida (un `SET NULL`
per usuari esborrat passa); un DELETE directe es refusa (una cascada de la
licitacio passa).

Tots els enums porten CHECK. SQLite (nomes tests, que creen l'esquema amb els
models): la taula es crea sencera; les columnes s'afegeixen sense NOT NULL, FK
ni CHECK (cal recrear la taula) i el trigger no existeix.

Downgrade: refusa de manera explicita (RAISE abans de cap DROP) si hi ha alguna
proposta `promoted` o alguna fila amb `origin` `operator` o `promoted`: tornar
enrere en perdria la provenance de l'operador (les files quedarien com si les
hagues escrit el propietari). Sense aixo, treu el trigger ABANS de la taula, les
columnes i la taula.

Revision ID: 0060_structure_provenance
Revises: 0059_global_classification
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0060_structure_provenance"
down_revision: str | None = "0059_global_classification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROPOSALS = "tender_structure_proposals"

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_ORIGINS = ("extracted", "operator", "promoted", "owner_manual", "gold")
_AUTHORITIES = ("platform_operator", "tender_owner", "promoted_from_company", "producer")
_PRODUCERS = ("operator", "extraction")
_ENTITY_KINDS = (
    "block",
    "site",
    "scope_item",
    "capability_need",
    "award_link",
    "contract_fact",
    "territory",
    "requirement_timing",
    "criterion_citation",
)
_STATUSES = ("proposed", "promoted", "rejected")

#: Les taules amb `tender_id` propi i les que hi arriben pel requisit.
_WITH_TENDER = (
    "tender_blocks",
    "tender_sites",
    "tender_scope_items",
    "requirement_capability_needs",
    "tender_contract_facts",
)
_VIA_REQUIREMENT = ("requirement_territories", "requirement_award_links")
_ENTITIES = _WITH_TENDER + _VIA_REQUIREMENT
_LINKS = (
    "tender_block_source_ref_links",
    "tender_site_source_ref_links",
    "award_criterion_source_ref_links",
    "tender_scope_item_source_ref_links",
    "requirement_capability_need_source_ref_links",
    "tender_contract_fact_source_ref_links",
    "requirement_source_ref_links",
)

_GOLD_TENDERS = (
    "SELECT tenders.id FROM tenders JOIN sources ON sources.id = tenders.source_id "
    "WHERE sources.code LIKE 'gold:%'"
)

# El mateix text que `app.models.structure_proposals`.
_IMMUTABLE_FUNCTION = """
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
_IMMUTABLE_TRIGGER = """
CREATE TRIGGER tender_structure_proposals_immutable
BEFORE UPDATE OR DELETE ON tender_structure_proposals
FOR EACH ROW EXECUTE FUNCTION tender_structure_proposals_immutable()
"""
_DROP_TRIGGER = (
    "DROP TRIGGER IF EXISTS tender_structure_proposals_immutable ON tender_structure_proposals"
)
_DROP_FUNCTION = "DROP FUNCTION IF EXISTS tender_structure_proposals_immutable()"

# Sense `:`, `%` ni cometes al missatge: `op.execute` el compila com a `text()`.
_REFUSAL = (
    "0060 downgrade refusat - hi ha propostes d estructura promogudes o files "
    "escrites per l operador i tornar enrere en perdria la provenance"
)
_OPERATOR_ROWS = " OR ".join(
    f"EXISTS (SELECT 1 FROM {table} WHERE origin IN ('operator', 'promoted'))"
    for table in _ENTITIES
)
_REFUSE_DOWNGRADE = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM tender_structure_proposals WHERE status = 'promoted')
        OR {_OPERATOR_ROWS}
    THEN
        RAISE EXCEPTION '{_REFUSAL}';
    END IF;
END
$$
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def _tz() -> sa.types.TypeEngine:
    return sa.DateTime(timezone=True)


def _uuid_fk(table: str, column: str, target: str) -> sa.Column:
    """Columna UUID nul·lable amb FK SET NULL (amb nom curt explicit) nomes a PostgreSQL."""
    if not _is_postgresql():
        return sa.Column(column, sa.Uuid(), nullable=True)
    return sa.Column(
        column,
        sa.Uuid(),
        sa.ForeignKey(target, name=f"fk_{table}_{column}", ondelete="SET NULL"),
        nullable=True,
    )


def _source_refs_check() -> sa.CheckConstraint:
    if _is_postgresql():
        condition = (
            "CASE WHEN jsonb_typeof(source_ref_ids) = 'array' "
            "THEN jsonb_array_length(source_ref_ids) > 0 ELSE false END"
        )
    else:
        condition = "json_array_length(source_ref_ids) > 0"
    return sa.CheckConstraint(condition, name=op.f(f"ck_{_PROPOSALS}_source_refs_not_empty"))


def upgrade() -> None:
    postgresql_ = _is_postgresql()
    op.create_table(
        _PROPOSALS,
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("producer", sa.String(length=10), nullable=False),
        sa.Column("producer_run_id", sa.Uuid(), nullable=True),
        sa.Column("entity_kind", sa.String(length=18), nullable=False),
        sa.Column("natural_key", sa.String(length=300), nullable=False),
        sa.Column("payload_json", _json(), nullable=False),
        sa.Column("source_ref_ids", _json(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("promoted_by", sa.Uuid(), nullable=True),
        sa.Column("promoted_at", _tz(), nullable=True),
        sa.Column("rejected_by", sa.Uuid(), nullable=True),
        sa.Column("rejected_at", _tz(), nullable=True),
        sa.Column("rejected_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", _tz(), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_PROPOSALS}")),
        sa.CheckConstraint(
            _in_list("producer", _PRODUCERS), name=op.f(f"ck_{_PROPOSALS}_producer_valid")
        ),
        sa.CheckConstraint(
            _in_list("entity_kind", _ENTITY_KINDS),
            name=op.f(f"ck_{_PROPOSALS}_entity_kind_valid"),
        ),
        sa.CheckConstraint(
            _in_list("status", _STATUSES), name=op.f(f"ck_{_PROPOSALS}_status_valid")
        ),
        sa.CheckConstraint(
            "length(trim(natural_key)) > 0", name=op.f(f"ck_{_PROPOSALS}_natural_key_not_empty")
        ),
        _source_refs_check(),
        sa.CheckConstraint(
            "(status = 'promoted') = (promoted_at IS NOT NULL)",
            name=op.f(f"ck_{_PROPOSALS}_promoted_has_date"),
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (rejected_at IS NOT NULL)",
            name=op.f(f"ck_{_PROPOSALS}_rejected_has_date"),
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR length(trim(coalesce(rejected_reason, ''))) > 0",
            name=op.f(f"ck_{_PROPOSALS}_rejected_has_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            name=op.f(f"fk_{_PROPOSALS}_tender_id_tenders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_structure_proposals_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["promoted_by"], ["users.id"], name="fk_structure_proposals_promoted_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rejected_by"], ["users.id"], name="fk_structure_proposals_rejected_by",
            ondelete="SET NULL",
        ),
    )  # fmt: skip
    op.create_index("ix_tender_structure_proposals_tender", _PROPOSALS, ["tender_id", "status"])
    if postgresql_:
        op.execute(_IMMUTABLE_FUNCTION)
        op.execute(_IMMUTABLE_TRIGGER)

    for table in _ENTITIES:
        op.add_column(table, sa.Column("origin", sa.String(length=12), nullable=True))
        op.add_column(table, sa.Column("authority", sa.String(length=21), nullable=True))
        op.add_column(table, sa.Column("producer_run_id", sa.Uuid(), nullable=True))
        op.add_column(table, _uuid_fk(table, "proposal_id", f"{_PROPOSALS}.id"))
        op.add_column(table, _uuid_fk(table, "created_by", "users.id"))
        op.add_column(table, _uuid_fk(table, "updated_by", "users.id"))

    # Backfill ABANS del NOT NULL i dels CHECK: fins ara nomes escrivien el
    # propietari (via del tenant) i el carregador del gold.
    for table in _ENTITIES:
        op.execute(sa.text(f"UPDATE {table} SET origin = 'owner_manual'"))
    for table in _WITH_TENDER:
        op.execute(
            sa.text(f"UPDATE {table} SET origin = 'gold' WHERE tender_id IN ({_GOLD_TENDERS})")
        )
    for table in _VIA_REQUIREMENT:
        op.execute(
            sa.text(
                f"UPDATE {table} SET origin = 'gold' WHERE requirement_id IN ("
                f"SELECT requirements.id FROM requirements WHERE requirements.tender_id IN "
                f"({_GOLD_TENDERS}))"
            )
        )
    if postgresql_:
        for table in _ENTITIES:
            op.alter_column(table, "origin", nullable=False)
            op.create_check_constraint(
                op.f(f"ck_{table}_origin_valid"), table, _in_list("origin", _ORIGINS)
            )
            op.create_check_constraint(
                op.f(f"ck_{table}_authority_valid"),
                table,
                "authority IS NULL OR " + _in_list("authority", _AUTHORITIES),
            )

    for table in _LINKS:
        op.add_column(table, sa.Column("producer_run_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    postgresql_ = _is_postgresql()
    if postgresql_:
        # Primer el refus: amb provenance de l'operador no es toca res.
        op.execute(_REFUSE_DOWNGRADE)
        # El trigger abans de la taula (i la funcio despres del trigger).
        op.execute(_DROP_TRIGGER)
        op.execute(_DROP_FUNCTION)
    for table in reversed(_LINKS):
        op.drop_column(table, "producer_run_id")
    for table in reversed(_ENTITIES):
        if postgresql_:
            op.drop_constraint(op.f(f"ck_{table}_authority_valid"), table, type_="check")
            op.drop_constraint(op.f(f"ck_{table}_origin_valid"), table, type_="check")
        for column in (
            "updated_by",
            "created_by",
            "proposal_id",
            "producer_run_id",
            "authority",
            "origin",
        ):
            op.drop_column(table, column)
    op.drop_index("ix_tender_structure_proposals_tender", table_name=_PROPOSALS)
    op.drop_table(_PROPOSALS)
