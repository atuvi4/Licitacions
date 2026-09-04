"""Les migracions d'Alembic no poden divergir dels models.

La primera migracio esta escrita a ma (no hi havia base de dades on fer
`--autogenerate`), aixi que aquest test es la xarxa de seguretat: genera l'SQL
en mode offline —no cal cap PostgreSQL engegat— i el compara columna a columna
amb el que declaren els models.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.models import Base

API_ROOT = Path(__file__).resolve().parents[1]
# Mode offline: Alembic nomes compila SQL per a aquest dialecte, no s'hi connecta.
OFFLINE_URL = "postgresql+asyncpg://user:pass@localhost/db"

# Es compara el PRIMER TOKEN exacte, no un prefix: una columna anomenada
# `checkpoint` comenca per "CHECK" i es descartaria com si fos un constraint.
STRUCTURAL_TOKENS = {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}


@pytest.fixture(scope="module")
def migration_sql() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": OFFLINE_URL},
    )
    assert result.returncode == 0, f"alembic ha fallat:\n{result.stderr[-2000:]}"
    return result.stdout


def _columns_created_by_migrations(ddl: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S):
        name, body = match.group(1), match.group(2)
        columns = set()
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            if not line:
                continue
            # Les columnes que xoquen amb una paraula reservada de PostgreSQL
            # poden sortir entre cometes.
            first = line.split(" ")[0].strip('"')
            if first.upper() not in STRUCTURAL_TOKENS:
                columns.add(first)
        tables[name] = columns
    for name, column in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", ddl):
        tables.setdefault(name, set()).add(column)
    return tables


def test_les_migracions_creen_totes_les_taules_dels_models(migration_sql):
    from_migrations = _columns_created_by_migrations(migration_sql)
    from_models = {table.name for table in Base.metadata.sorted_tables}

    # `alembic_version` la crea el propi Alembic i no es del domini.
    assert from_models <= set(from_migrations)
    assert set(from_migrations) - from_models <= {"alembic_version"}


def test_cada_taula_te_les_mateixes_columnes_que_el_model(migration_sql):
    from_migrations = _columns_created_by_migrations(migration_sql)

    for table in Base.metadata.sorted_tables:
        expected = {column.name for column in table.columns}
        actual = from_migrations[table.name]
        assert expected == actual, (
            f"{table.name}: al model i no a la migracio {sorted(expected - actual)}; "
            f"a la migracio i no al model {sorted(actual - expected)}"
        )


def test_les_migracions_son_reversibles(migration_sql):
    """Cada revisio ha de tenir downgrade; si no, un desplegament no es pot desfer."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    revisions = list(script.walk_revisions())
    assert revisions, "no hi ha cap migracio"

    for revision in revisions:
        source = Path(revision.path).read_text(encoding="utf-8")
        body = source.split("def downgrade()")[1]
        assert "pass" not in body.split("\n")[1], f"{revision.revision} no te downgrade real"
