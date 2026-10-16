"""Imports d'un expedient segons una font externa acreditada (F-06a).

Additiva i reversible. Una taula global nova, `tender_amount_observations`: cada import
(PBL amb IVA, PBL sense IVA, VEC) que una font externa acreditada publica per a un
expedient o per a un lot, amb la procedencia completa (font, fila, fase, actualitzacio
del dataset, moment de la consulta, consulta exacta, SHA-256 del cos original i, si n'hi
ha, la resposta redactada desada). **No toca cap taula existent**: ni
`tenders.base_budget` ni cap altre import de la licitacio. Cap backfill.

* Vigencia: `valid_to` NULL = vigent. Una sola observacio `accepted` vigent per
  (expedient, lot o expedient, tipus d'import, font): index unic parcial amb
  `coalesce(tender_lot_id, nil)`, perque dos NULL no son "diferents" aqui.
* CHECK: els valors dels dos enums (VARCHAR + CHECK, cap tipus natiu: vegeu
  `enum_column`), `amount >= 0`, `valid_to >= valid_from` i el SHA-256 de 64
  caracters.
* Nomes d'afegir, a PostgreSQL: el mateix patro que `review_item_revisions` (0040).
  Un trigger refusa tot UPDATE que no sigui TANCAR una observacio vigent (`valid_to`
  de NULL a un valor, sense tocar res mes) i tot DELETE directe; un DELETE que arriba
  per una cascada (licitacio o lot) s'executa a `pg_trigger_depth() >= 2` i passa.

Downgrade: a PostgreSQL refusa (RAISE abans de cap DROP) si hi ha alguna observacio
desada: tornar enrere les esborraria, i la procedencia d'un import no es pot refer. Com
a 0062, sense files treu el trigger, la funcio i la taula (els enums son VARCHAR + CHECK
i se'n van amb ella). Qui de debo vulgui perdre les observacions ho ha de dir
explicitament: `alembic -x drop_amount_observations=yes downgrade ...`.

Revision ID: 0063_tender_amount_observations
Revises: 0062_requirement_uncertainties
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "0063_tender_amount_observations"
down_revision: str | None = "0062_requirement_uncertainties"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tender_amount_observations"

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_AMOUNT_KINDS = ("pbl_with_tax", "pbl_without_tax", "estimated_value")
_STATUSES = ("accepted", "conflict")
_NIL_LOT = "'00000000-0000-0000-0000-000000000000'"
_CURRENT_ACCEPTED = "valid_to IS NULL AND validation_status = 'accepted'"

# El mateix text que `app.models.tender_amounts`.
_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION tender_amount_observations_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() < 2 THEN
            RAISE EXCEPTION 'tender_amount_observations es nomes d''afegir (%)', TG_OP
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
       AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'tender_amount_observations nomes admet tancar valid_to (%)', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql
"""
_APPEND_ONLY_TRIGGER = """
CREATE TRIGGER tender_amount_observations_append_only
BEFORE UPDATE OR DELETE ON tender_amount_observations
FOR EACH ROW EXECUTE FUNCTION tender_amount_observations_append_only()
"""

#: Marca explicita (`alembic -x drop_amount_observations=yes downgrade ...`) per baixar
#: encara que hi hagi observacions: sense ella, el downgrade refusa.
DROP_ROWS_OPT_IN = "drop_amount_observations"

# Sense `:`, `%` ni cometes al missatge: `op.execute` el compila com a `text()`.
_REFUSAL = (
    "0063 downgrade refusat - hi ha observacions d imports desades i tornar enrere les "
    "esborraria. Per perdre-les de debo, alembic -x drop_amount_observations=yes"
)
assert "'" not in _REFUSAL and ":" not in _REFUSAL and "%" not in _REFUSAL
_REFUSE_DOWNGRADE = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
        RAISE EXCEPTION '{_REFUSAL}';
    END IF;
END
$$
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def drop_rows_opted_in() -> bool:
    """Si qui executa Alembic ha demanat explicitament perdre les observacions.

    Fora d'un entorn d'Alembic (la migracio cridada directament des d'un test) no hi ha
    cap `-x`: mai s'hi dona permis per defecte.
    """
    try:
        arguments = context.get_x_argument(as_dictionary=True)
    except NameError:  # cap EnvironmentContext establert
        return False
    return arguments.get(DROP_ROWS_OPT_IN) == "yes"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("tender_lot_id", sa.Uuid(), nullable=True),
        sa.Column("amount_kind", sa.String(length=15), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="EUR", nullable=False),
        sa.Column("source_code", sa.String(length=60), nullable=False),
        sa.Column("source_entity_key", sa.String(length=200), nullable=False),
        sa.Column("source_row_id", sa.String(length=200), nullable=False),
        sa.Column("source_phase", sa.String(length=80), nullable=True),
        sa.Column("source_dataset_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_url", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_storage_key", sa.String(length=512), nullable=True),
        sa.Column("validation_status", sa.String(length=8), nullable=False),
        sa.Column("validation", _json(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_TABLE}")),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            name="fk_tender_amount_observations_tender_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tender_lot_id"],
            ["tender_lots.id"],
            name="fk_tender_amount_observations_tender_lot_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _in_list("amount_kind", _AMOUNT_KINDS),
            name=op.f(f"ck_{_TABLE}_amount_kind_valid"),
        ),
        sa.CheckConstraint(
            _in_list("validation_status", _STATUSES),
            name=op.f(f"ck_{_TABLE}_validation_status_valid"),
        ),
        sa.CheckConstraint("amount >= 0", name=op.f(f"ck_{_TABLE}_amount_non_negative")),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name=op.f(f"ck_{_TABLE}_validity_ordered"),
        ),
        sa.CheckConstraint(
            "length(evidence_sha256) = 64", name=op.f(f"ck_{_TABLE}_evidence_sha256_length")
        ),
    )
    op.create_index(f"ix_{_TABLE}_tender_id", _TABLE, ["tender_id"])
    op.create_index(
        f"uq_{_TABLE}_current",
        _TABLE,
        [
            "tender_id",
            sa.text(f"coalesce(tender_lot_id, {_NIL_LOT})"),
            "amount_kind",
            "source_code",
        ],
        unique=True,
        postgresql_where=sa.text(_CURRENT_ACCEPTED),
        sqlite_where=sa.text(_CURRENT_ACCEPTED),
    )
    if _is_postgresql():
        op.execute(_APPEND_ONLY_FUNCTION)
        op.execute(_APPEND_ONLY_TRIGGER)


def downgrade() -> None:
    apply_downgrade(drop_rows=drop_rows_opted_in())


def apply_downgrade(*, drop_rows: bool) -> None:
    """El downgrade, amb la marca ja resolta (els tests la passen directament)."""
    if _is_postgresql():
        if not drop_rows:
            # Primer el refus: amb una sola observacio desada no es toca res.
            op.execute(_REFUSE_DOWNGRADE)
        op.execute(f"DROP TRIGGER IF EXISTS tender_amount_observations_append_only ON {_TABLE}")
        op.execute("DROP FUNCTION IF EXISTS tender_amount_observations_append_only()")
    op.drop_index(f"uq_{_TABLE}_current", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_tender_id", table_name=_TABLE)
    op.drop_table(_TABLE)
