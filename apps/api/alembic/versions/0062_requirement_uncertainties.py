"""Incerteses materials de l'extraccio de requisits (perfil `product-v2`).

Additiva i reversible. Una taula global nova, `requirement_uncertainties`: una fila per
clausula MATERIAL que el plec no permet resoldre amb seguretat (admissibilitat,
solvencia, obligacio contractual, puntuacio, dimensionament, cost, documentacio,
termini, responsabilitat). No es cap requisit, criteri, bloquejador, forat del paquet
ni resultat de compliment; no te `company_id` (es de l'extraccio global) ni vigencia
propia: penja d'un run de requisits (`run_id`, `ON DELETE CASCADE`) i es immutable un cop
el run s'ha construit (mentre es construeix, una deteccio amb millor procedencia de la
mateixa incertesa en reescriu la fila del mateix run; cap altre run no la toca mai).

Provenance amb la mateixa precisio que `requirement_evidence`: `extraction_id`
(`RESTRICT`), `tender_document_id` (`SET NULL`), `page_number` (>= 1) i, NOMES amb una
cita literal a la pagina, `page_id` (`RESTRICT`), `quote` i offsets. CHECK: la cita i la
`page_id` hi son si i nomes si `quote_status = 'literal'`; `diagnosis` (codis) obligatori
amb una cita rebutjada i nul amb una de literal; offsets coherents; nota no buida;
vocabularis tancats de `scope` i `quote_status`. Noms de FK explicits, identics als del
model. Unicitat `(run_id, dedup_key)`: el solapament entre chunks i
una represa no la dupliquen.

Cap canvi a cap taula existent, cap backfill.

Downgrade: a PostgreSQL refusa (RAISE abans de cap DROP) si hi ha alguna incertesa
desada: tornar enrere les esborraria. Sense files, treu la taula. Mai s'esborra res.

Revision ID: 0062_requirement_uncertainties
Revises: 0061_requirement_run_basis
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0062_requirement_uncertainties"
down_revision: str | None = "0061_requirement_run_basis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "requirement_uncertainties"

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_SCOPES = (
    "admissibility",
    "solvency",
    "contract_obligation",
    "scoring",
    "sizing",
    "cost",
    "submission_documentation",
    "deadline",
    "liability",
)
_QUOTE_STATUSES = ("literal", "not_provided", "rejected")

# Sense `:`, `%` ni cometes al missatge: `op.execute` el compila com a `text()`.
_REFUSAL = (
    "0062 downgrade refusat - hi ha incerteses de requisits desades i tornar enrere les esborraria"
)
assert "'" not in _REFUSAL  # el missatge va dins d'un literal SQL
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


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("source_chunk_index", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("tender_document_id", sa.Uuid(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("quote_status", sa.String(length=12), nullable=False),
        sa.Column("diagnosis", sa.String(length=80), nullable=True),
        sa.Column("dedup_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_TABLE}")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["requirement_extraction_runs.id"],
            name="fk_requirement_uncertainties_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            name="fk_requirement_uncertainties_tender_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["tender_document_extractions.id"],
            name="fk_requirement_uncertainties_extraction_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tender_document_id"],
            ["tender_documents.id"],
            name="fk_requirement_uncertainties_tender_document_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["tender_document_pages.id"],
            name="fk_requirement_uncertainties_page_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("run_id", "dedup_key", name="uq_requirement_uncertainties_run_key"),
        sa.CheckConstraint("page_number >= 1", name=op.f(f"ck_{_TABLE}_page_one_based")),
        sa.CheckConstraint(
            "(quote_status = 'literal') = (quote IS NOT NULL) "
            "AND (quote_status = 'literal') = (page_id IS NOT NULL) "
            "AND (quote_status = 'literal') = (char_start IS NOT NULL AND char_end IS NOT NULL)",
            name=op.f(f"ck_{_TABLE}_quote_if_literal"),
        ),
        sa.CheckConstraint(_in_list("scope", _SCOPES), name=op.f(f"ck_{_TABLE}_scope_valid")),
        sa.CheckConstraint(
            _in_list("quote_status", _QUOTE_STATUSES),
            name=op.f(f"ck_{_TABLE}_quote_status_valid"),
        ),
        sa.CheckConstraint(
            "(quote_status <> 'rejected' OR diagnosis IS NOT NULL) "
            "AND (quote_status <> 'literal' OR diagnosis IS NULL)",
            name=op.f(f"ck_{_TABLE}_diagnosis_consistent"),
        ),
        sa.CheckConstraint(
            "char_start IS NULL OR (char_start >= 0 AND char_end > char_start)",
            name=op.f(f"ck_{_TABLE}_offsets_valid"),
        ),
        sa.CheckConstraint("length(trim(note)) > 0", name=op.f(f"ck_{_TABLE}_note_not_blank")),
    )
    op.create_index(f"ix_{_TABLE}_tender_id", _TABLE, ["tender_id"])


def downgrade() -> None:
    if _is_postgresql():
        # Primer el refus: amb una sola incertesa desada no es toca res.
        op.execute(_REFUSE_DOWNGRADE)
    op.drop_index(f"ix_{_TABLE}_tender_id", table_name=_TABLE)
    op.drop_table(_TABLE)
