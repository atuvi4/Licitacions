"""Crea l'esquema per al mode de desenvolupament local (SQLite).

Nomes per a desenvolupament sense Docker. A PostgreSQL l'esquema es gestiona
EXCLUSIVAMENT amb Alembic (`alembic upgrade head`): aquest script s'hi nega
expressament per no crear dos camins divergents de migracio.

    python -m app.dev_bootstrap
"""

from __future__ import annotations

import asyncio
import sys

from app.core.config import settings
from app.db.session import engine

# Importar `Base` des de `app.models` garanteix que tots els models estan
# registrats al metadata abans de crear l'esquema.
from app.models import Base


async def create_schema() -> None:
    url = settings.sqlalchemy_url
    if "postgresql" in url:
        print(
            "Aquest script nomes serveix per a SQLite en desenvolupament local.\n"
            "Amb PostgreSQL, fes servir: alembic upgrade head",
            file=sys.stderr,
        )
        raise SystemExit(1)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print(f"Esquema creat a {url}")


if __name__ == "__main__":
    asyncio.run(create_schema())
