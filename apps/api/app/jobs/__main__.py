"""CLI de la cua de feina.

    python -m app.jobs worker [--once] [--types analyze_tender_requirements ...]
    python -m app.jobs maintenance        # recollir abandonats i reconciliar orfes
    python -m app.jobs list [--status queued] [--limit 20]

Aturada: el primer Ctrl+C deixa de reclamar feina nova i acaba el job en curs
(una analisi a mig fer ja s'ha pagat en part); el segon surt de cop, i llavors el
lease venç i el job queda per al reaper.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

import sqlalchemy as sa

from app.core.logging import configure_logging
from app.db.session import SessionFactory, engine
from app.jobs import queue
from app.jobs.worker import Worker
from app.models.enums import WorkJobStatus, WorkJobType
from app.models.jobs import WorkJob


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.jobs", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    worker = commands.add_parser("worker", help="executa feina de la cua")
    worker.add_argument("--once", action="store_true", help="un sol job (o cap) i surt")
    worker.add_argument(
        "--types", nargs="*", choices=[t.value for t in WorkJobType], default=None
    )
    worker.add_argument("--worker-id", default=None)

    commands.add_parser("maintenance", help="recull jobs abandonats i reconcilia orfes")

    listing = commands.add_parser("list", help="llista jobs")
    listing.add_argument("--status", choices=[s.value for s in WorkJobStatus], default=None)
    listing.add_argument("--limit", type=int, default=20)
    return parser


async def _worker(args: argparse.Namespace) -> int:
    worker = Worker(
        SessionFactory,
        worker_id=args.worker_id,
        job_types=[WorkJobType(t) for t in args.types] if args.types else None,
    )
    if args.once:
        job = await worker.run_once()
        print(f"job: {job.id if job else '(cap)'}")
        return 0

    stop = asyncio.Event()
    interrupts = {"count": 0}

    def _on_sigint(_signum, _frame) -> None:
        interrupts["count"] += 1
        if interrupts["count"] > 1:
            raise KeyboardInterrupt
        print("Aturant: s'acaba el job en curs (Ctrl+C un altre cop per sortir ja).")
        stop.set()

    signal.signal(signal.SIGINT, _on_sigint)
    await worker.run_forever(stop)
    return 0


async def _maintenance() -> int:
    async with SessionFactory() as session:
        report = await queue.run_maintenance(session)
    print(
        f"reencuats={len(report.requeued)} orfes={len(report.orphaned)} "
        f"fallats={len(report.failed)} reconciliats={report.reconciled}"
    )
    return 0


async def _list(args: argparse.Namespace) -> int:
    async with SessionFactory() as session:
        query = sa.select(WorkJob).order_by(WorkJob.created_at.desc()).limit(args.limit)
        if args.status:
            query = query.where(WorkJob.status == args.status)
        for job in (await session.scalars(query)).all():
            print(
                f"{job.id} {job.job_type:28} {job.status:10} tender={job.tender_id} "
                f"attempts={job.attempts}/{job.max_attempts} "
                f"progress={job.progress_current}/{job.progress_total} "
                f"error={job.error_code or '-'}"
            )
    return 0


async def _main(args: argparse.Namespace) -> int:
    try:
        if args.command == "worker":
            return await _worker(args)
        if args.command == "maintenance":
            return await _maintenance()
        return await _list(args)
    finally:
        await engine.dispose()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    configure_logging()
    return asyncio.run(_main(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
