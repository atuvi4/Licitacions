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


def _offline_sql(*args: str) -> str:
    """SQL d'un tram concret de migracions, sense connectar-se enlloc."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args, "--sql"],
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": OFFLINE_URL},
    )
    assert result.returncode == 0, f"alembic ha fallat:\n{result.stderr[-2000:]}"
    return result.stdout


def test_la_solucio_tecnica_es_pot_aplicar_i_desfer():
    """0023 -> 0024 -> 0023: el que una revisio crea, el seu downgrade ho treu.

    No n'hi ha prou que existeixi un `downgrade()`: ha de desfer exactament el
    que s'ha aplicat. Una revisio que crea una taula i en deixa els indexs
    enrere fa que tornar enrere i reaplicar falli, i aixo nomes es veu quan ja
    ha passat en un desplegament.
    """
    table = "workspace_technical_solutions"
    up = _offline_sql("upgrade", "0023_analysis_quotes:0024_technical_solution")
    down = _offline_sql("downgrade", "0024_technical_solution:0023_analysis_quotes")

    assert f"CREATE TABLE {table}" in up
    assert "uq_workspace_technical_solutions_requirement" in up
    assert "WHERE requirement_id IS NOT NULL" in up

    created_indexes = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    dropped_indexes = set(re.findall(r"DROP INDEX (\w+)", down))
    assert created_indexes
    assert created_indexes == dropped_indexes
    assert f"DROP TABLE {table}" in down


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


def _varchar_widths(ddl: str) -> dict[tuple[str, str], int]:
    """Amplada final de cada VARCHAR segons les migracions, en ordre d'aplicacio.

    Cal llegir-ho en ordre: una revisio posterior pot eixamplar una columna, i el
    que val es l'ultima amplada.
    """
    widths: dict[tuple[str, str], int] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S):
        table, body = match.group(1), match.group(2)
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            parts = line.split()
            if len(parts) < 2:
                continue
            column = parts[0].strip('"')
            if column.upper() in STRUCTURAL_TOKENS:
                continue
            size = re.match(r"VARCHAR\((\d+)\)", parts[1], re.I)
            if size:
                widths[(table, column)] = int(size.group(1))
    for table, column, size in re.findall(
        r"ALTER TABLE (\w+) ADD COLUMN (\w+) VARCHAR\((\d+)\)", ddl
    ):
        widths[(table, column)] = int(size)
    for table, column, size in re.findall(
        r"ALTER TABLE (\w+) ALTER COLUMN (\w+) TYPE VARCHAR\((\d+)\)", ddl
    ):
        widths[(table, column)] = int(size)
    return widths


def test_cap_varchar_no_es_mes_estret_que_el_del_model(migration_sql):
    """Una columna mes estreta que el model rebutjaria valors valids.

    Va passar de veritat: afegir `approved_for_submission` (23 caracters) a un
    enum la columna del qual s'havia creat com a VARCHAR(22). El test de columnes
    no ho veia perque el NOM de la columna no havia canviat, i l'error nomes
    hauria sortit contra un PostgreSQL real, en intentar desar el valor.
    """
    import sqlalchemy as sa

    widths = _varchar_widths(migration_sql)
    narrow: list[str] = []

    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            expected = getattr(column.type, "length", None)
            if not isinstance(column.type, sa.String) or not expected:
                continue
            actual = widths.get((table.name, column.name))
            if actual is not None and actual < expected:
                narrow.append(f"{table.name}.{column.name}: model {expected}, migracio {actual}")

    assert not narrow, "columnes mes estretes que el model: " + "; ".join(narrow)


#: Un FK sense clausula `ON DELETE` es comporta com `NO ACTION` a PostgreSQL.
_DEFAULT_ON_DELETE = "NO ACTION"
_FOREIGN_KEY = re.compile(
    r"CONSTRAINT (?P<name>\w+) FOREIGN\s*KEY\s*\((?P<columns>[^)]*)\)\s*"
    r"REFERENCES (?P<referred>\w+)\s*\([^)]*\)(?P<rest>[^,\n]*)",
    re.I,
)


def _on_delete(clause: str) -> str:
    action = re.search(
        r"ON DELETE (CASCADE|SET NULL|SET DEFAULT|RESTRICT|NO ACTION)", clause, re.I
    )
    return action.group(1).upper() if action else _DEFAULT_ON_DELETE


def _foreign_keys(ddl: str) -> dict[tuple[str, tuple[str, ...]], tuple[str, str]]:
    """Estat final de cada FK segons les migracions: `(taula, columnes) -> (nom, ON DELETE)`.

    Cal llegir-ho en ordre i no com un recompte: una revisio posterior pot
    esborrar una FK i tornar-la a crear amb un comportament diferent, i el que
    val es l'ultima.
    """
    state: dict[tuple[str, tuple[str, ...]], tuple[str, str]] = {}

    def remember(table: str, match: re.Match[str]) -> None:
        columns = tuple(c.strip().strip('"') for c in match.group("columns").split(","))
        state[(table, columns)] = (match.group("name"), _on_delete(match.group("rest")))

    for created in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S):
        for match in _FOREIGN_KEY.finditer(created.group(2)):
            remember(created.group(1), match)

    for table, operation, tail in re.findall(
        r"ALTER TABLE (\w+) (ADD|DROP) CONSTRAINT (.*?);", ddl, re.S
    ):
        if operation.upper() == "ADD":
            match = _FOREIGN_KEY.search(f"CONSTRAINT {tail}")
            if match:
                remember(table, match)
            continue
        name = tail.split()[0]
        for key, value in list(state.items()):
            if key[0] == table and value[0] == name:
                del state[key]

    return state


def test_cada_fk_esborra_igual_que_al_model(migration_sql):
    """El `ON DELETE` de les migracions ha de ser el que declara el model.

    Va passar de veritat: la 0013 va crear `company_insurance_limits.created_by`
    i `updated_by` sense `ON DELETE`, quan `ActorMixin` les declara `SET NULL`.
    A PostgreSQL aixo vol dir `NO ACTION`, i esborrar un usuari fallava amb
    violacio de clau forana en comptes de deixar la fila sense autor.

    La suite corre sobre SQLite creant les taules des dels MODELS, on la
    clausula hi era sempre; l'esquema real es construeix des de les MIGRACIONS.
    Mentre l'unic que es comparaven eren noms de columna, aquesta diferencia no
    la podia veure ningu fins a tenir un PostgreSQL al davant.
    """
    from_migrations = _foreign_keys(migration_sql)
    divergent: list[str] = []

    for table in Base.metadata.sorted_tables:
        for constraint in table.foreign_key_constraints:
            columns = tuple(column.name for column in constraint.columns)
            expected = (constraint.ondelete or _DEFAULT_ON_DELETE).upper()
            found = from_migrations.get((table.name, columns))
            if found is None:
                divergent.append(f"{table.name}({', '.join(columns)}): no hi es a la migracio")
            elif found[1] != expected:
                divergent.append(
                    f"{table.name}({', '.join(columns)}) [{found[0]}]: "
                    f"model ON DELETE {expected}, migracio ON DELETE {found[1]}"
                )

    assert not divergent, "FK que no esborren com el model: " + "; ".join(divergent)
