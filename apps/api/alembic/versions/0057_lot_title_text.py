"""Hotfix radar: titols sense limit i fallades conegudes de la ingesta.

1. Titols de la font sense limit (`tender_lots.title`, `tenders.title` -> Text).
   El primer sync real del pilot (2026-10-07, pagina 9 del feed 1044) va trobar
   una entrada amb dos titols de lot de 1.017 i 1.076 caracters:
   `tender_lots.title` VARCHAR(1000) els rebutjava (StringDataRightTruncation).
   `ProcurementProject/Name` de l'expedient es el mateix camp lliure de CODICE i a
   la mateixa pagina n'hi ha un de 1.999 caracters (el normalitzador el retallava
   en silenci a 1.000). Tots dos passen a Text, sense cap retall. Cap altra
   columna s'amplia (auditoria a l'informe del hotfix). Nomes a PostgreSQL: a
   SQLite VARCHAR(n) i TEXT son el mateix (sense limit).
2. Fallades conegudes (totes les bases):
   * `source_records.failed_schema_revision` (VARCHAR(64), nullable): el cap
     d'Alembic del codi quan el registre va fallar. Un error determinista nomes
     es reintenta si canvia aixo o el `parser_version`.
   * `ingestion_runs.records_failed_known` (INTEGER NOT NULL DEFAULT 0): les
     fallades ja conegudes que no s'han reintentat.

Downgrade: torna els titols a VARCHAR(1000) NOMES si cap no supera els 1.000
caracters. Si n'hi ha, refusa de manera explicita (RAISE abans de cap ALTER) en
comptes de truncar: tornar enrere no pot perdre dades de la font. Sense cap cast
(`USING ...::varchar(1000)` truncaria en silenci). Les dues columnes noves
s'esborren (informacio derivada de la ingesta, no de la font).

Revision ID: 0057_lot_title_text
Revises: 0056_radar_search
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0057_lot_title_text"
down_revision: str | None = "0056_radar_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (taula, nullable) de les dues columnes `title` que passen a Text.
_COLUMNS = (("tender_lots", True), ("tenders", False))
_LIMIT = 1000

# Sense `:`, `%` ni cometes al missatge: `op.execute` el compila com a `text()`.
_REFUSAL = (
    f"0057 downgrade refusat - hi ha titols de mes de {_LIMIT} caracters "
    f"(tender_lots.title o tenders.title) i VARCHAR({_LIMIT}) els truncaria"
)
_REFUSE_DOWNGRADE = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM tender_lots WHERE char_length(title) > {_LIMIT})
       OR EXISTS (SELECT 1 FROM tenders WHERE char_length(title) > {_LIMIT}) THEN
        RAISE EXCEPTION '{_REFUSAL}';
    END IF;
END
$$
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.add_column(
        "source_records",
        sa.Column("failed_schema_revision", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("records_failed_known", sa.Integer(), nullable=False, server_default="0"),
    )
    if not _is_postgresql():
        return
    for table, nullable in _COLUMNS:
        op.alter_column(
            table,
            "title",
            existing_type=sa.String(length=_LIMIT),
            type_=sa.Text(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    postgresql = _is_postgresql()
    if postgresql:
        # Primer el refus: si n'hi ha titols llargs no es toca res.
        op.execute(_REFUSE_DOWNGRADE)
    op.drop_column("ingestion_runs", "records_failed_known")
    op.drop_column("source_records", "failed_schema_revision")
    if not postgresql:
        return
    for table, nullable in _COLUMNS:
        op.alter_column(
            table,
            "title",
            existing_type=sa.Text(),
            type_=sa.String(length=_LIMIT),
            existing_nullable=nullable,
        )
