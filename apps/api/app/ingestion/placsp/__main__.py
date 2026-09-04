"""CLI d'ingesta de PLACSP.

    python -m app.ingestion.placsp --limit 50
    python -m app.ingestion.placsp --fixture tests/fixtures/placsp/feed_sample.atom
    python -m app.ingestion.placsp --dry-run --limit 10
    python -m app.ingestion.placsp --max-files 3 --resume

No hi ha planificador ni cua: la ingesta s'engega a ma. Els workers arribaran
quan aquest cami estigui provat.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.logging import configure_logging
from app.db.session import SessionFactory
from app.ingestion.placsp.service import PlacspIngestionService


async def _run(args: argparse.Namespace) -> int:
    async with SessionFactory() as session:
        service = PlacspIngestionService(session)
        run = await service.run(
            limit=args.limit,
            max_files=args.max_files,
            fixture=Path(args.fixture) if args.fixture else None,
            feed_url=args.feed_url,
            dry_run=args.dry_run,
            resume=args.resume,
        )

        print(f"\nIngesta {run.status.value.upper()}  (run_id={run.id})")
        print(f"  durada            : {run.duration_ms} ms")
        print(f"  entrades llegides : {run.records_fetched}")
        print(f"    noves           : {run.records_new}")
        print(f"    modificades     : {run.records_changed}")
        print(f"    sense canvis    : {run.records_unchanged}")
        print(f"    fallides        : {run.records_failed}")
        print(f"  licitacions creades    : {run.tenders_created}")
        print(f"  licitacions actualitzades: {run.tenders_updated}")
        if run.checkpoint_after:
            print(f"  checkpoint        : {run.checkpoint_after}")
        if run.error_summary:
            print("\n  Errors:")
            for line in run.error_summary.splitlines()[:10]:
                print(f"    - {line}")
        return 0 if run.status.value in {"success", "partial"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ingestion.placsp", description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Maxim d'entrades a processar.")
    parser.add_argument(
        "--max-files",
        type=int,
        default=1,
        help="Quants fitxers ATOM encadenats es recorren (rel=next va cap enrere en el temps).",
    )
    parser.add_argument("--fixture", help="Processa un fitxer local en comptes de descarregar.")
    parser.add_argument("--feed-url", help="URL alternativa del feed.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Parseja i compta, pero no escriu res."
    )
    parser.add_argument(
        "--resume", action="store_true", help="Continua des del checkpoint de la font."
    )
    args = parser.parse_args()

    configure_logging()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
