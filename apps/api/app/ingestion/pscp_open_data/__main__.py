"""CLI de l'adaptador de dades obertes de la PSCP (F-06a, fase 1).

    python -m app.ingestion.pscp_open_data --out <dir> --dry-run
    python -m app.ingestion.pscp_open_data --out <dir> --limit 50
    python -m app.ingestion.pscp_open_data --out <dir> --tender <uuid>
    python -m app.ingestion.pscp_open_data --out <dir>

`--dry-run` ho calcula tot (consulta, guardes, escriptura) i escriu l'informe, pero desfa
la transaccio: no queda res a la base. `--tender` accepta l'id de la licitacio o el seu
`pscp_id_intern`. `--out` es el directori de l'informe i, alhora, l'arrel on es desen les
respostes redactades (`raw_storage_key` hi es relatiu).

Codi de sortida: 0 be; 1 fallada (cap escriptura); 3 el dataset s'ha actualitzat durant
el run (`rowsUpdatedAt` diferent a l'inici i al final): cap escriptura.

Sense programador ni sync diari: cada execucio la llanca un operador.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import configure_logging
from app.ingestion.pscp_open_data.client import DEFAULT_MAX_REQUESTS, SodaClient
from app.ingestion.pscp_open_data.service import (
    BATCH_SIZE,
    PAGE_LIMIT,
    PscpOpenDataService,
    RunOptions,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.pscp_open_data",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", required=True, type=Path, help="Directori de l'informe.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Ho calcula tot pero no escriu res a la base."
    )
    parser.add_argument("--limit", type=int, default=None, help="Maxim de licitacions.")
    parser.add_argument("--tender", default=None, help="Una sola licitacio (id o pscp_id_intern).")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help=argparse.SUPPRESS)
    parser.add_argument("--page-limit", type=int, default=PAGE_LIMIT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--max-requests",
        type=int,
        default=DEFAULT_MAX_REQUESTS,
        help="Limit dur de peticions a la font en aquest run.",
    )
    return parser


async def amain(
    argv: list[str] | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    client: SodaClient | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.batch_size < 1 or args.batch_size > 100:
        raise SystemExit("--batch-size ha de ser entre 1 i 100")
    if session_factory is None:
        from app.db.session import SessionFactory

        session_factory = SessionFactory
    options = RunOptions(
        out_dir=args.out,
        dry_run=args.dry_run,
        limit=args.limit,
        tender=args.tender,
        batch_size=args.batch_size,
        page_limit=args.page_limit,
    )
    soda = client or SodaClient(max_requests=args.max_requests)
    async with soda:
        code, report = await PscpOpenDataService(session_factory, soda).run(options)
    _print(report)
    return code


def _print(report: dict) -> None:
    evaluation = report.get("evaluation") or {}
    writes = report.get("writes") or {}
    mode = "DRY-RUN" if report.get("dry_run") else "RUN"
    print(f"\nPSCP dades obertes {mode}  (codi {report.get('exit_code')})")
    print(f"  peticions       : {report.get('requests')}")
    print(f"  licitacions     : {evaluation.get('tenders')}")
    for key, value in (evaluation.get("observations") or {}).items():
        print(f"    {key:<22}: {value}")
    if writes:
        for key in ("inserted", "closed", "unchanged"):
            print(f"  {key:<16}: {writes.get(key)}")
        if writes.get("skipped"):
            print(f"  escriptura      : NO ({writes['skipped']})")
    if report.get("error"):
        print(f"  error           : {report['error']}")
    print(f"  {report.get('attribution', '')}")


def main() -> int:
    configure_logging()
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
