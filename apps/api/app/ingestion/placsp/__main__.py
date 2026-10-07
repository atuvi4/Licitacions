"""CLI d'ingesta de PLACSP.

    python -m app.ingestion.placsp --source placsp_agregadas --max-pages 2
    python -m app.ingestion.placsp --fixture tests/fixtures/placsp/feed_sample.atom
    python -m app.ingestion.placsp --dry-run --limit 10
    python -m app.ingestion.placsp --backfill-until 2026-09-01 --max-pages 20
    python -m app.ingestion.placsp --backfill-until 2026-09-01 --max-pages 20 --resume

Codi de sortida: 0 success, 2 partial, 1 failed (o font desactivada / sync ja en curs).

Contra el feed viu (sense `--fixture`) la ingesta passa per `run_source_sync`
(`app.ingestion.sync`): pren el lease de la font, escriu la salut i recalcula el
pre-fit, exactament com `python -m app.ingestion.sync`; mai corren dos syncs de
la mateixa font alhora. `--fixture` processa un fitxer local directament (proves
i desenvolupament), sense lease ni salut.

`--resume` nomes te sentit en backfill i requereix `--backfill-until` (que, si no
es diu `--mode`, ja vol dir backfill). `--feed-url` es un override de
desenvolupament: nomes amb `--dry-run`, que no escriu res del cataleg ni mou la
marca d'aigua ni el checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import configure_logging
from app.ingestion import sync as sync_module
from app.ingestion.placsp.service import PlacspIngestionService
from app.ingestion.sources import SOURCES
from app.models.sources import IngestionRun


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.placsp",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--limit", type=int, default=None, help="Maxim d'entrades a processar.")
    parser.add_argument(
        "--max-pages",
        dest="max_pages",
        type=int,
        default=None,
        help="Quantes pagines ATOM encadenades es recorren com a maxim (rel=next va cap enrere).",
    )
    parser.add_argument(
        "--max-files", dest="max_files", type=int, default=None, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--mode",
        choices=["incremental", "backfill"],
        default=None,
        help=(
            "incremental: fins al solapament de 48 h; backfill: fins a --backfill-until. "
            "Per defecte, backfill si es dona --backfill-until."
        ),
    )
    parser.add_argument(
        "--backfill-until",
        type=date.fromisoformat,
        default=None,
        help="Data (AAAA-MM-DD) fins a la qual el backfill recorre l'historic.",
    )
    parser.add_argument("--fixture", help="Processa un fitxer local en comptes de descarregar.")
    parser.add_argument(
        "--source",
        default="placsp",
        choices=sorted(SOURCES),
        help="Font del registre (per defecte placsp).",
    )
    parser.add_argument(
        "--feed-url", help="Desenvolupament: sobreescriu la URL del feed (nomes amb --dry-run)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Parseja i compta, pero no escriu res."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Backfill: continua des del checkpoint de la font (requereix --backfill-until).",
    )
    return parser


def _print_run(run: IngestionRun) -> int:
    print(f"\nIngesta {run.status.value.upper()}  (run_id={run.id})")
    print(f"  durada            : {run.duration_ms} ms")
    print(f"  pagines           : {run.pages_processed}")
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
    return sync_module.EXIT_CODES.get(run.status.value, 1)


async def amain(
    argv: list[str] | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    storage=None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    mode = args.mode or ("backfill" if args.backfill_until else "incremental")
    problem = sync_module.invalid_sync_options(
        mode=mode,
        backfill_until=args.backfill_until,
        resume=args.resume,
        dry_run=args.dry_run,
        feed_url=args.feed_url,
    )
    if problem:
        parser.error(problem)
    if session_factory is None:
        from app.db.session import SessionFactory

        session_factory = SessionFactory
    max_pages = args.max_pages or args.max_files

    if args.fixture:
        # Fitxer local: cami directe, sense lease ni salut de font.
        async with session_factory() as session:
            run = await PlacspIngestionService(session, storage).run(
                limit=args.limit,
                max_pages=max_pages,
                mode=mode,
                backfill_until=args.backfill_until,
                fixture=Path(args.fixture),
                dry_run=args.dry_run,
                source_code=args.source,
            )
        return _print_run(run)

    try:
        run = await sync_module.run_source_sync(
            session_factory,
            args.source,
            mode=mode,
            max_pages=max_pages,
            backfill_until=args.backfill_until,
            resume=args.resume,
            limit=args.limit,
            dry_run=args.dry_run,
            feed_url=args.feed_url,
            storage=storage,
        )
    except sync_module.SyncAlreadyRunning:
        print(f"Sync ja en curs per a {args.source}", file=sys.stderr)
        return 1
    except sync_module.SourceDisabled:
        print(f"Font desactivada: {args.source}", file=sys.stderr)
        return 1
    return _print_run(run)


def main() -> int:
    configure_logging()
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
