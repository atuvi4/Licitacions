"""Radar 3B-1: index trigram de la cerca de licitacions (nomes PostgreSQL).

Crea l'extensio `pg_trgm` (si cal) i un index GIN sobre `tenders.search_text` per a la
cerca `ILIKE '%terme%'` del cataleg. A SQLite no fa res (la cerca hi funciona sense
index; es nomes l'entorn de proves).

Prerequisit operacional: crear l'extensio demana un rol amb permis (superusuari o
extensio de confianca). Si el rol no pot, la migracio falla amb un missatge clar; cal
executar `CREATE EXTENSION pg_trgm;` com a superusuari i tornar a migrar.

Downgrade: esborra NOMES l'index. L'extensio mai s'esborra (es de la base de dades, no
d'aquesta migracio, i altres objectes en poden dependre).

Revision ID: 0056_radar_search
Revises: 0055_radar_ingestion
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from alembic import op

revision: str = "0056_radar_search"
down_revision: str | None = "0055_radar_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_tenders_search_text_trgm"
_PREREQUISIT = "Prerequisit: CREATE EXTENSION pg_trgm; com a superusuari i tornar a migrar"
_INSUFFICIENT_PRIVILEGE = "42501"


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _ensure_pg_trgm() -> None:
    context = op.get_context()
    if context.as_sql:
        # Mode offline: no es pot consultar res; l'sentencia es idempotent.
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        return
    bind = op.get_bind()
    present = bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))
    if present.scalar() is not None:
        return
    try:
        # SAVEPOINT: un error de permisos no ha de deixar la transaccio avortada.
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except DBAPIError as exc:
        # Nomes la falta de permisos (42501 insufficient_privilege) es el
        # prerequisit documentat; qualsevol altre error es propaga tal qual.
        if _sqlstate(exc) == _INSUFFICIENT_PRIVILEGE:
            raise RuntimeError(_PREREQUISIT) from exc
        raise


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def upgrade() -> None:
    if not _is_postgresql():
        return
    _ensure_pg_trgm()
    op.create_index(
        _INDEX,
        "tenders",
        ["search_text"],
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )


def downgrade() -> None:
    if not _is_postgresql():
        return
    op.drop_index(_INDEX, table_name="tenders")
