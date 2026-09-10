"""On poden correr els tests contra PostgreSQL, i on no.

El 10/09/2026 la suite es va executar contra PostgreSQL i va deixar 2.443 files
de prova a la base de desenvolupament, a mes de modificar-ne 6 de reals. Hi van
coincidir dues errades, i aquest modul existeix per fer impossibles totes dues:

1. **Es va apuntar a la base equivocada.** Res no comprovava a quina base es
   connectava la suite. Ara, abans de qualsevol escriptura, la DSN s'ha de
   referir exactament a la base de test configurada, no pot coincidir amb la de
   desenvolupament, i un cop connectats `current_database()` ho ha de confirmar.
2. **El schema "aillat" no ho era.** El `search_path` incloia `public`; com que
   les taules ja hi existien, `create_all` no en va crear cap al schema temporal
   i tots els tests van escriure a `public`. Ara el `search_path` es NOMES el
   schema temporal, i abans de retornar el motor es comprova que cada taula del
   model es resol dins d'aquest schema.

Si qualsevol comprovacio falla, s'atura abans d'escriure res.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

#: La base de proves PostgreSQL de l'entorn local. Es pot canviar amb
#: `TEST_DATABASE_NAME`, pero mai per un nom de la llista prohibida.
DEFAULT_TEST_DATABASE = "licitacions_test"

#: Mai, en cap configuracio. `licitacions` es la base de desenvolupament i pilot.
FORBIDDEN_DATABASES = frozenset({"licitacions", "postgres", "template0", "template1"})

SCHEMA_PREFIX = "pytest_"
_SCHEMA_RE = re.compile(r"^pytest_[0-9a-f]{12}$")
_DRIVER = "postgresql+asyncpg"


class UnsafeTestDatabaseError(RuntimeError):
    """La base configurada per als tests no es, demostrablement, la de test."""


def _describe(url: URL) -> str:
    """La DSN sense contrasenya, per als missatges d'error."""
    return url.render_as_string(hide_password=True)


def validate_test_database_url(
    test_url: str | None,
    *,
    expected: str,
    developer_urls: Iterable[str | None] = (),
    forbidden: Iterable[str | None] = (),
) -> URL:
    """Comprovacio estatica, sense connectar: la DSN apunta a la base de test.

    Tot el que no sigui inequivoc es rebutja. En particular:

    * parametres a la DSN (`?options=`, `?host=`, `?dbname=`...): poden canviar
      la base o el `search_path` sense que el nom ho reflecteixi;
    * sense host explicit o amb diversos hosts: la base real depen de l'entorn;
    * una base diferent de l'esperada, encara que acabi en `_test`;
    * qualsevol base prohibida o que coincideixi amb la de desenvolupament.
    """
    denied = {name for name in (*FORBIDDEN_DATABASES, *forbidden) if name}
    if expected in denied:
        raise UnsafeTestDatabaseError(
            f"La base de test configurada ({expected!r}) es una base prohibida."
        )
    if not test_url:
        raise UnsafeTestDatabaseError(
            "S'ha demanat PostgreSQL (--postgres) pero TEST_DATABASE_URL no esta definida."
        )
    try:
        url = make_url(test_url)
    except ArgumentError:
        raise UnsafeTestDatabaseError("TEST_DATABASE_URL no es una DSN valida.") from None

    if url.drivername != _DRIVER:
        raise UnsafeTestDatabaseError(
            f"TEST_DATABASE_URL ha de fer servir {_DRIVER!r}, no {url.drivername!r}."
        )
    if url.query:
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_URL no pot portar parametres "
            f"({', '.join(sorted(url.query))}): podrien canviar la base o el search_path."
        )
    if not url.host or "," in url.host:
        raise UnsafeTestDatabaseError(
            "TEST_DATABASE_URL ha de tenir exactament un host explicit."
        )
    if not url.database:
        raise UnsafeTestDatabaseError("TEST_DATABASE_URL no diu a quina base connecta.")
    if url.database in denied:
        raise UnsafeTestDatabaseError(
            f"Els tests no es poden executar mai contra {url.database!r} ({_describe(url)})."
        )
    if url.database != expected:
        raise UnsafeTestDatabaseError(
            f"TEST_DATABASE_URL apunta a {url.database!r}; nomes s'accepta {expected!r}."
        )

    for raw in developer_urls:
        if not raw:
            continue
        try:
            developer = make_url(raw)
        except ArgumentError:
            continue
        same_dsn = developer.render_as_string(hide_password=False) == url.render_as_string(
            hide_password=False
        )
        same_target = (
            (developer.host, developer.port or 5432, developer.database)
            == (url.host, url.port or 5432, url.database)
        )
        if same_dsn or same_target or developer.database == url.database:
            raise UnsafeTestDatabaseError(
                "TEST_DATABASE_URL apunta a la mateixa base que DATABASE_URL."
            )
    return url


async def assert_connected_to_test_database(conn: AsyncConnection, expected: str) -> None:
    """Comprovacio en viu: el servidor confirma que som a la base de test.

    La DSN pot dir una cosa i el servidor una altra (un alias en un pooler, un
    servei, una variable d'entorn). Mana el que diu el servidor.
    """
    actual = (await conn.exec_driver_sql("select current_database()")).scalar()
    if actual != expected:
        raise UnsafeTestDatabaseError(
            f"Connectats a {actual!r} en comptes de {expected!r}: no s'escriu res."
        )


async def verify_test_database(url: URL, expected: str) -> None:
    """Obre una connexio de NOMES LECTURA i confirma la base. No escriu mai."""
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    try:
        async with engine.connect() as conn:
            await assert_connected_to_test_database(conn, expected)
    finally:
        await engine.dispose()


async def assert_isolated(conn: AsyncConnection, schema: str, tables: Iterable[str]) -> None:
    """El schema temporal es l'unic lloc on es poden resoldre les taules del model."""
    search_path = (await conn.exec_driver_sql("show search_path")).scalar()
    current = (await conn.exec_driver_sql("select current_schema()")).scalar()
    if search_path.replace('"', "") != schema or current != schema:
        raise UnsafeTestDatabaseError(
            f"El schema no esta aillat: search_path={search_path!r}, current_schema={current!r}."
        )
    names = sorted(tables)
    if not names:
        return
    rows = (
        await conn.execute(
            sa.text(
                "select t, (select n.nspname from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where c.oid = to_regclass(quote_ident(t))) "
                "from unnest(cast(:names as text[])) as t"
            ),
            {"names": names},
        )
    ).all()
    outside = {table: nsp for table, nsp in rows if nsp != schema}
    if outside:
        raise UnsafeTestDatabaseError(
            f"Taules que no es resolen dins {schema!r}: {outside}. No s'hi escriu."
        )


@asynccontextmanager
async def isolated_schema_engine(
    url: URL, *, expected: str, metadata: sa.MetaData
) -> AsyncIterator[AsyncEngine]:
    """Un motor que nomes veu un schema propi, creat i esborrat al voltant de l'us.

    Ordre, i no es negociable: confirmar la base, crear el schema, connectar amb
    `search_path` = NOMES aquest schema, comprovar-ho, crear-hi les taules,
    comprovar que s'hi han creat. Si res d'aixo falla, no es retorna cap motor.
    En acabar s'esborra nomes aquest schema, i nomes si el nom es dels nostres.
    """
    schema = f"{SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}"
    # Un sol motor per a tot: crear el schema, treballar-hi i esborrar-lo. Un
    # `search_path` pot anomenar un schema que encara no existeix, i PostgreSQL
    # simplement l'ignora fins que hi es. Amb un motor d'administracio a part,
    # cada test obria el doble de connexions i una suite sencera podia esgotar
    # els ports efimers de la maquina.
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    created = False
    try:
        async with engine.begin() as conn:
            await assert_connected_to_test_database(conn, expected)
            await conn.exec_driver_sql(f'create schema "{schema}"')
            created = True
            await assert_isolated(conn, schema, ())
            await conn.run_sync(metadata.create_all)
            await assert_isolated(conn, schema, metadata.tables.keys())
        yield engine
    finally:
        try:
            if created and _SCHEMA_RE.match(schema):
                async with engine.begin() as conn:
                    await assert_connected_to_test_database(conn, expected)
                    await conn.exec_driver_sql(f'drop schema if exists "{schema}" cascade')
        finally:
            await engine.dispose()
