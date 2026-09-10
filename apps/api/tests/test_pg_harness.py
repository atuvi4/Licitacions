"""El harness PostgreSQL no pot escriure mai fora de la base de test.

Regressio de l'incident del 10/09/2026: la suite es va executar contra la base
de desenvolupament i, amb `public` al search_path, hi va escriure 2.443 files i
en va modificar 6 de reals. Veure `pg_harness.py`.

Tres nivells:

* **Sense base de dades** (sempre): la validacio estatica de la DSN i l'ordre
  de les comprovacions, amb connexions falses que registren cada sentencia.
* **Subprocessos** (sempre): una execucio real de pytest amb `--postgres` que
  ha de fallar abans de recollir cap test. Apunten a un port tancat: encara que
  el guard fallés, no hi hauria cap base a qui escriure.
* **PostgreSQL de veritat** (`--postgres`): el schema temporal esta aillat.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.db.base import Base
from tests import pg_harness
from tests.pg_harness import (
    UnsafeTestDatabaseError,
    assert_connected_to_test_database,
    isolated_schema_engine,
    validate_test_database_url,
)

TEST_DB = "licitacions_test"
GOOD = f"postgresql+asyncpg://u:p@127.0.0.1:5433/{TEST_DB}"
DEVELOPER = "postgresql+asyncpg://u:p@127.0.0.1:5433/licitacions"
API_ROOT = Path(__file__).resolve().parents[1]


def validate(url, *, developer=DEVELOPER, expected=TEST_DB):
    return validate_test_database_url(
        url, expected=expected, developer_urls=[developer], forbidden=["licitacions"]
    )


# --------------------------------------------------------------------------- #
# Validacio estatica: sense connectar                                           #
# --------------------------------------------------------------------------- #


def test_licitacions_test_is_accepted():
    assert validate(GOOD).database == TEST_DB


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@127.0.0.1:5433/licitacions",
        "postgresql+asyncpg://u:p@127.0.0.1:5433/postgres",
        "postgresql+asyncpg://u:p@127.0.0.1:5433/template1",
    ],
    ids=["licitacions", "postgres", "template1"],
)
def test_forbidden_databases_are_refused(url):
    with pytest.raises(UnsafeTestDatabaseError):
        validate(url)


def test_the_development_database_is_refused_even_with_other_credentials():
    """No n'hi ha prou de canviar l'usuari: la base es la mateixa."""
    with pytest.raises(UnsafeTestDatabaseError):
        validate("postgresql+asyncpg://other:x@db.local:6000/licitacions")


def test_test_url_equal_to_database_url_is_refused():
    with pytest.raises(UnsafeTestDatabaseError, match="mateixa base"):
        validate(GOOD, developer=GOOD)


def test_a_name_ending_in_test_is_not_enough():
    """Es compara amb la base configurada, no amb el sufix."""
    with pytest.raises(UnsafeTestDatabaseError, match="nomes s'accepta"):
        validate("postgresql+asyncpg://u:p@127.0.0.1:5433/altra_test")


@pytest.mark.parametrize(
    "url",
    [
        f"{GOOD}?options=-csearch_path%3Dpublic",
        f"{GOOD}?host=10.0.0.9",
        f"{GOOD}?dbname=licitacions",
        f"postgresql+asyncpg://u:p@/{TEST_DB}",
        f"postgresql+asyncpg://u:p@h1,h2:5433/{TEST_DB}",
        "postgresql+asyncpg://u:p@127.0.0.1:5433/",
        f"postgresql://u:p@127.0.0.1:5433/{TEST_DB}",
        f"sqlite+aiosqlite:///{TEST_DB}",
        "no es una dsn",
        "",
        None,
    ],
    ids=[
        "options", "host-param", "dbname-param", "sense-host", "dos-hosts",
        "sense-base", "sense-asyncpg", "sqlite", "text", "buida", "absent",
    ],
)
def test_ambiguous_dsns_are_refused(url):
    with pytest.raises(UnsafeTestDatabaseError):
        validate(url)


def test_a_forbidden_name_cannot_be_configured_as_the_test_database():
    with pytest.raises(UnsafeTestDatabaseError, match="prohibida"):
        validate(DEVELOPER.replace("u:p", "x:y"), expected="licitacions")


# --------------------------------------------------------------------------- #
# Comprovacio en viu: amb connexions falses que registren cada sentencia        #
# --------------------------------------------------------------------------- #


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _FakeConnection:
    """Una connexio que diu ser a `actual` i apunta totes les sentencies."""

    def __init__(self, actual, log):
        self.actual, self.log = actual, log

    async def exec_driver_sql(self, sql):
        self.log.append(sql)
        return _Result(self.actual if "current_database" in sql else None)

    async def execute(self, *args, **kwargs):
        self.log.append(str(args[0]))
        return _Result(None)

    async def run_sync(self, fn):
        self.log.append("create_all")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, actual, log):
        self.actual, self.log = actual, log

    def connect(self):
        return _FakeConnection(self.actual, self.log)

    begin = connect

    async def dispose(self):
        pass


async def test_the_live_check_trusts_the_server_not_the_dsn():
    log: list[str] = []
    with pytest.raises(UnsafeTestDatabaseError, match="licitacions"):
        await assert_connected_to_test_database(_FakeConnection("licitacions", log), TEST_DB)
    assert log == ["select current_database()"]


async def test_no_schema_is_created_if_the_server_is_not_the_test_database(monkeypatch):
    """La DSN diu licitacions_test pero el servidor respon licitacions: no s'escriu res."""
    log: list[str] = []
    monkeypatch.setattr(
        pg_harness, "create_async_engine", lambda *a, **k: _FakeEngine("licitacions", log)
    )

    with pytest.raises(UnsafeTestDatabaseError):
        async with isolated_schema_engine(
            validate(GOOD), expected=TEST_DB, metadata=Base.metadata
        ):
            pytest.fail("no s'hauria d'haver obtingut cap motor")

    assert log == ["select current_database()"], "cap sentencia despres de la comprovacio"
    assert not any("create" in s.lower() or "drop" in s.lower() for s in log)


# --------------------------------------------------------------------------- #
# Subprocessos: una execucio real de pytest --postgres                          #
# --------------------------------------------------------------------------- #


def _pytest_postgres(test_url: str | None) -> subprocess.CompletedProcess:
    skip = {"TEST_DATABASE_URL", "TEST_DATABASE_NAME"}
    env = {k: v for k, v in os.environ.items() if k not in skip}
    # Port tancat a proposit: encara que el guard fallés, no hi hauria cap base.
    env["DATABASE_URL"] = "postgresql+asyncpg://dev:dev@127.0.0.1:1/licitacions"
    if test_url is not None:
        env["TEST_DATABASE_URL"] = test_url
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--postgres", "-q", "-p", "no:cacheprovider",
         "tests/test_pg_harness.py::test_licitacions_test_is_accepted"],
        cwd=API_ROOT, env=env, capture_output=True, text=True, timeout=180,
    )


def test_pytest_postgres_refuses_licitacions_before_connecting():
    result = _pytest_postgres("postgresql+asyncpg://u:p@127.0.0.1:1/licitacions")
    output = result.stdout + result.stderr
    assert result.returncode == 4, output  # UsageError: no s'ha recollit cap test
    assert "PostgreSQL de test refusat" in output
    # Si hagues intentat connectar, l'error seria de connexio, no de refus.
    assert "No es pot connectar" not in output


def test_a_broken_dsn_fails_for_real_and_never_falls_back_to_sqlite():
    result = _pytest_postgres(f"postgresql+asyncpg://u:p@127.0.0.1:1/{TEST_DB}")
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "No es pot connectar a la base de test PostgreSQL" in output
    assert "passed" not in output


def test_postgres_without_a_test_url_is_an_error():
    result = _pytest_postgres(None)
    output = result.stdout + result.stderr
    assert result.returncode == 4, output
    assert "TEST_DATABASE_URL no esta definida" in output


# --------------------------------------------------------------------------- #
# PostgreSQL de veritat (nomes amb --postgres, contra licitacions_test)         #
# --------------------------------------------------------------------------- #


@pytest.mark.postgres
async def test_the_engine_is_postgresql_in_an_isolated_schema(engine):
    """Cap fallback a SQLite, i les taules nomes es poden resoldre al schema propi."""
    assert engine.dialect.name == "postgresql"
    async with engine.connect() as conn:
        database = (await conn.exec_driver_sql("select current_database()")).scalar()
        schema = (await conn.exec_driver_sql("select current_schema()")).scalar()
        search_path = (await conn.exec_driver_sql("show search_path")).scalar()
        await pg_harness.assert_isolated(conn, schema, Base.metadata.tables.keys())
    assert database == TEST_DB
    assert schema.startswith(pg_harness.SCHEMA_PREFIX)
    assert search_path.replace('"', "") == schema
    assert "public" not in search_path


@pytest.mark.postgres
async def test_the_temporary_schema_disappears_and_public_is_not_touched(request):
    from tests.conftest import PG_TEST_URL

    url, expected = request.config.stash[PG_TEST_URL]
    marker = "99999999"
    schemas = sa.text("select count(*) from pg_namespace where nspname = :s")
    in_public = sa.text("select count(*) from public.cpv_codes where code = :c")

    async with isolated_schema_engine(url, expected=expected, metadata=Base.metadata) as eng:
        async with eng.connect() as conn:
            schema = (await conn.exec_driver_sql("select current_schema()")).scalar()
            assert (await conn.execute(schemas, {"s": schema})).scalar() == 1
            public_before = (await conn.execute(in_public, {"c": marker})).scalar()
            # Una escriptura sense qualificar ha d'anar al schema temporal, mai a public.
            await conn.execute(
                sa.text(
                    "insert into cpv_codes (code, description, is_active) values (:c, 'x', true)"
                ),
                {"c": marker},
            )
            await conn.commit()
            unqualified = sa.text("select count(*) from cpv_codes where code = :c")
            assert (await conn.execute(unqualified, {"c": marker})).scalar() == 1
            assert (await conn.execute(in_public, {"c": marker})).scalar() == public_before

    # Un altre schema temporal, nomes per poder mirar des de fora.
    async with isolated_schema_engine(url, expected=expected, metadata=sa.MetaData()) as eng:
        async with eng.connect() as conn:
            assert (await conn.execute(schemas, {"s": schema})).scalar() == 0, "s'ha esborrat"
            assert (await conn.execute(in_public, {"c": marker})).scalar() == public_before
