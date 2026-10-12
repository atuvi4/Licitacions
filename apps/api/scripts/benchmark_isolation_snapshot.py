"""Foto de les bases que el benchmark NO ha de tocar, per comparar abans/despres.

    python scripts/benchmark_isolation_snapshot.py > abans.txt
    ... executa el benchmark (o la suite de tests) ...
    python scripts/benchmark_isolation_snapshot.py > despres.txt
    diff abans.txt despres.txt

    # Altres bases: per exemple, que la suite no ha tocat ni dev ni el benchmark
    python scripts/benchmark_isolation_snapshot.py licitacions licitacions_benchmark

Per que existeix: `benchmark.py` refusa per nom `licitacions` i `licitacions_test`,
pero aixo es una promesa del codi. Aixo la comprova amb dades: mida de la base,
comptadors d'escriptura de les taules d'usuari (`pg_stat_user_tables`, que no
compta el cataleg) i files de les taules que un benchmark ompliria si s'hagues
equivocat de base.

Nomes fa SELECTs. Reutilitza la credencial de `BENCHMARK_DATABASE_URL` canviant
el nom de la base: no cal cap secret nou.
"""

from __future__ import annotations

import asyncio
import sys

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

#: Les bases que han de quedar intactes si no se n'indiquen d'altres.
WATCHED = ("licitacions", "licitacions_test")

#: Taules que un benchmark mal dirigit ompliria.
TABLES = (
    "requirement_extraction_runs",
    "requirements",
    "requirement_evidence",
    "requirement_uncertainties",
    "award_criteria",
    "tenders",
    "tender_documents",
    "tender_document_pages",
)


async def snapshot(dsn: str, database: str) -> None:
    url = make_url(dsn).set(database=database)
    engine = create_async_engine(url)
    print(f"--- {database} ---")
    try:
        async with engine.connect() as conn:
            size = await conn.scalar(sa.text("SELECT pg_database_size(current_database())"))
            print(f"{'size_bytes':<28}{size}")
            row = (
                await conn.execute(
                    sa.text(
                        "SELECT coalesce(sum(n_tup_ins), 0) AS ins, "
                        "coalesce(sum(n_tup_upd), 0) AS upd, coalesce(sum(n_tup_del), 0) AS dels "
                        "FROM pg_stat_user_tables"
                    )
                )
            ).first()
            if row is not None:
                print(f"{'user_tup_inserted':<28}{row.ins}")
                print(f"{'user_tup_updated':<28}{row.upd}")
                print(f"{'user_tup_deleted':<28}{row.dels}")
            for table in TABLES:
                exists = await conn.scalar(sa.text("SELECT to_regclass(:t)"), {"t": table})
                if exists is None:
                    print(f"{table:<28}(no existeix)")
                    continue
                count = await conn.scalar(sa.text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
                print(f"{table:<28}{count}")
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}")
    finally:
        await engine.dispose()


async def main(databases: list[str]) -> int:
    dsn = settings.BENCHMARK_DATABASE_URL
    if not dsn:
        print("Falta BENCHMARK_DATABASE_URL.")
        return 1
    for database in databases or WATCHED:
        await snapshot(dsn, database)
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main(sys.argv[1:])))
