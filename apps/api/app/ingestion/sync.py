"""Sincronitzacio d'una font: lease, run observable, salut de font i prefit.

    python -m app.ingestion.sync --source placsp_agregadas
    python -m app.ingestion.sync --backfill-until 2026-09-01 --max-pages 200
    python -m app.ingestion.sync --backfill-until 2026-09-01 --max-pages 200 --resume
    python -m app.ingestion.sync --every 900
    python -m app.ingestion.sync --status [--source placsp_agregadas]

Codi de sortida: 0 success, 2 partial, 1 failed (o font desactivada / ja en curs).
`--resume` nomes te sentit en backfill: requereix `--backfill-until`. `--status`
nomes llegeix la salut de les fonts (sense lease ni sync).

Un sync pren un lease per font (una sola fila `sources`, UPDATE condicional
amb comprovacio de `rowcount`: val igual a SQLite i PostgreSQL). El servei
d'ingesta (`PlacspIngestionService.run`) no exposa cap hook per pagina, de
manera que el lease es renova amb un batec en segon pla (`HEARTBEAT_SECONDS`)
mentre dura el run, i s'allibera sempre al `finally`. Despres del run s'escriu
la salut de la font (`last_sync_*`) i es recalcula el pre-fit de les empreses
amb perfil nomes per a les licitacions que ha tocat el run.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import re
import signal
import socket
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import configure_logging
from app.ingestion.errors import (
    KNOWN_FAILURES_RE,
    SUMMARY_RE,
    classify_exception,
    safe_error_line,
)
from app.ingestion.placsp.service import PlacspIngestionService
from app.ingestion.sources import SOURCES
from app.matching.service import MatchingService
from app.models.cpv import CompanyCPVPreference
from app.models.enums import IngestionStatus
from app.models.profile import CompanyContractPreferences, CompanyTerritory
from app.models.sources import IngestionRun, Source, SourceRecord

logger = logging.getLogger(__name__)

LEASE_MINUTES = 15
#: Interval del batec que renova el lease mentre corre el run.
HEARTBEAT_SECONDS = 300.0
PREFIT_CHUNK = 500

EXIT_CODES = {"success": 0, "partial": 2, "failed": 1}


class SyncAlreadyRunning(RuntimeError):
    """Un altre procés te el lease vigent d'aquesta font."""


class SourceDisabled(RuntimeError):
    """La font esta desactivada: no se sincronitza."""


def make_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """L'instant en UTC; un valor sense zona (SQLite) es llegeix com a UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# --------------------------------------------------------------------------- #
# Lease                                                                        #
# --------------------------------------------------------------------------- #


async def _acquire_lease(
    session_factory: async_sessionmaker[AsyncSession],
    source_id: uuid.UUID,
    owner: str,
    now: datetime,
) -> bool:
    async with session_factory() as session:
        result = await session.execute(
            sa.update(Source)
            .where(
                Source.id == source_id,
                sa.or_(Source.sync_lease_until.is_(None), Source.sync_lease_until < now),
            )
            .values(
                sync_lease_owner=owner,
                sync_lease_until=now + timedelta(minutes=LEASE_MINUTES),
            )
        )
        await session.commit()
        return (result.rowcount or 0) == 1


async def _renew_lease(
    session_factory: async_sessionmaker[AsyncSession], source_id: uuid.UUID, owner: str
) -> None:
    async with session_factory() as session:
        await session.execute(
            sa.update(Source)
            .where(Source.id == source_id, Source.sync_lease_owner == owner)
            .values(sync_lease_until=_now() + timedelta(minutes=LEASE_MINUTES))
        )
        await session.commit()


async def _release_lease(
    session_factory: async_sessionmaker[AsyncSession], source_id: uuid.UUID, owner: str
) -> None:
    async with session_factory() as session:
        await session.execute(
            sa.update(Source)
            .where(Source.id == source_id, Source.sync_lease_owner == owner)
            .values(sync_lease_owner=None, sync_lease_until=None)
        )
        await session.commit()


async def _heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    source_id: uuid.UUID,
    owner: str,
    interval: float,
    stop: asyncio.Event,
) -> None:
    """Renova el lease cada `interval` fins que `stop` es marca.

    Mai es cancel.la des de fora: si s'interromp a mig UPDATE, SQLAlchemy
    invalida la connexio (a SQLite `:memory:` amb StaticPool, la base sencera).
    S'atura sortint del bucle entre dues renovacions.
    """
    while not stop.is_set():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            await _renew_lease(session_factory, source_id, owner)
        except Exception:  # noqa: BLE001 - un batec fallit no ha d'aturar el sync
            logger.exception("sync.lease.renew_failed", extra={"source_id": str(source_id)})


# --------------------------------------------------------------------------- #
# Salut i prefit                                                               #
# --------------------------------------------------------------------------- #


_PAGE_RE = re.compile(r"^pagina (\d+)")
_CLASS_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception)):")


def _safe_summary(run: IngestionRun) -> str | None:
    """Resum curt (<= 200) i sense dades crues (SQL, parametres, XML) d'un run.

    Primer el recompte per classe que escriu el servei (`1 db_data
    (StringDataRightTruncationError 22001, tender_lots.title) + ...`), nomes si
    compleix la gramatica estricta; despres les pagines fallides i el nombre
    d'entrades fallides; i, si n'hi ha, `N fallades conegudes (classe, ...)`
    (fallades deterministes ja conegudes que no s'han reintentat i que no fan el
    run `partial`). Les linies d'un run antic sense classificar es redueixen com
    abans a `Classe: error inesperat`.
    """
    parts: list[str] = []
    for line in (run.error_summary or "").splitlines():
        page = _PAGE_RE.match(line)
        klass = _CLASS_RE.match(line)
        if SUMMARY_RE.match(line) or KNOWN_FAILURES_RE.match(line):
            parts.append(line)
        elif page:
            parts.append(f"pagina {page.group(1)} fallida")
        elif safe_error_line(line) is not None:
            continue  # ja compta al recompte per classe
        elif klass:
            parts.append(f"{klass.group(1)}: error inesperat")
        else:
            parts.append("error de feed")
    parts = list(dict.fromkeys(parts))
    if run.records_failed:
        parts.append(f"{run.records_failed} entrades fallides")
    if not parts and run.status == IngestionStatus.PARTIAL:
        parts.append("cadena next incompleta")
    return "; ".join(parts)[:200] or None


async def _write_health(
    session_factory: async_sessionmaker[AsyncSession],
    source_id: uuid.UUID,
    *,
    started: datetime,
    run: IngestionRun | None,
    error: str | None,
) -> None:
    async with session_factory() as session:
        source = await session.get(Source, source_id)
        if source is None:
            return
        source.last_sync_started_at = started
        source.last_sync_finished_at = _now()
        if run is None:
            source.last_sync_status = IngestionStatus.FAILED.value
            source.last_sync_error = error
        else:
            source.last_sync_status = run.status.value
            source.last_sync_pages = run.pages_processed or 0
            source.last_sync_entries = run.records_fetched or 0
            source.last_sync_tombstones = run.tombstones_applied or 0
            source.last_sync_error = _safe_summary(run)
            # No hi ha cap hook per llegir l'`updated` de la pagina 1: s'usa el
            # maxim `updated` dels registres d'aquest run i, si no n'hi ha
            # (feed sense canvis), la marca d'aigua.
            feed_updated = (
                await session.execute(
                    sa.select(sa.func.max(SourceRecord.source_updated_at)).where(
                        SourceRecord.ingestion_run_id == run.id
                    )
                )
            ).scalar_one_or_none()
            feed_updated = _as_utc(feed_updated or source.updated_watermark)
            current = _as_utc(source.last_feed_updated_at)
            # Nomes avanca: un run `partial` amb dades mes antigues no la fa retrocedir.
            if feed_updated is not None and (current is None or feed_updated > current):
                source.last_feed_updated_at = feed_updated
        await session.commit()


async def _prefit(session_factory: async_sessionmaker[AsyncSession], run: IngestionRun) -> int:
    """Recalcula el pre-fit de cada empresa amb perfil per als tenders del run."""
    async with session_factory() as session:
        tender_ids = list(
            (
                await session.execute(
                    sa.select(SourceRecord.tender_id)
                    .where(
                        SourceRecord.ingestion_run_id == run.id,
                        SourceRecord.tender_id.is_not(None),
                    )
                    .distinct()
                )
            ).scalars()
        )
        if not tender_ids:
            # `recalculate(tender_ids=[])` vol dir «tots»: aqui vol dir «cap».
            return 0
        company_ids: set[uuid.UUID] = set()
        for column in (
            CompanyCPVPreference.company_id,
            CompanyTerritory.company_id,
            CompanyContractPreferences.company_id,
        ):
            company_ids.update((await session.execute(sa.select(column).distinct())).scalars())
    done = 0
    for company_id in sorted(company_ids, key=str):
        try:
            async with session_factory() as session:
                for start in range(0, len(tender_ids), PREFIT_CHUNK):
                    await MatchingService(session).recalculate(
                        company_id, tender_ids=tender_ids[start : start + PREFIT_CHUNK]
                    )
            done += 1
        except Exception:  # noqa: BLE001 - el prefit no ha de tombar el sync
            logger.exception("sync.prefit.failed", extra={"company_id": str(company_id)})
    return done


# --------------------------------------------------------------------------- #
# Sync                                                                         #
# --------------------------------------------------------------------------- #


async def run_source_sync(
    session_factory: async_sessionmaker[AsyncSession],
    source_code: str,
    *,
    mode: str = "incremental",
    max_pages: int | None = None,
    backfill_until: date | None = None,
    resume: bool = False,
    owner: str | None = None,
    now: datetime | None = None,
    storage=None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    limit: int | None = None,
    dry_run: bool = False,
    feed_url: str | None = None,
) -> IngestionRun:
    """Sincronitza una font amb lease. Llanca `SourceDisabled`/`SyncAlreadyRunning`.

    `limit` talla el run per entrades (mai mou la marca d'aigua). `dry_run`
    parseja i compta sense escriure el cataleg: pren el lease, pero no escriu la
    salut de la font ni recalcula el pre-fit. `feed_url` es un override de
    desenvolupament (`python -m app.ingestion.placsp --feed-url`): nomes amb
    `dry_run`, perque mai s'ingereixi un altre feed dins d'una font registrada.
    """
    if source_code not in SOURCES:
        raise ValueError(f"font desconeguda: {source_code!r}")
    problem = invalid_sync_options(
        mode=mode, backfill_until=backfill_until, resume=resume, dry_run=dry_run, feed_url=feed_url
    )
    if problem:
        raise ValueError(problem)
    owner = owner or make_owner()
    moment = now or _now()

    async with session_factory() as session:
        source = await PlacspIngestionService(session, storage).get_or_create_source(source_code)
        await session.commit()
        source_id, enabled = source.id, source.enabled
    if not enabled:
        raise SourceDisabled(source_code)
    if not await _acquire_lease(session_factory, source_id, owner, moment):
        raise SyncAlreadyRunning(source_code)

    stop_beat = asyncio.Event()
    beat = asyncio.create_task(
        _heartbeat(session_factory, source_id, owner, heartbeat_seconds, stop_beat)
    )
    started = _now()
    run: IngestionRun | None = None
    error: str | None = None
    try:
        await _renew_lease(session_factory, source_id, owner)
        async with session_factory() as session:
            run = await PlacspIngestionService(session, storage).run(
                source_code=source_code,
                mode=mode,
                max_pages=max_pages,
                backfill_until=backfill_until,
                resume=resume,
                limit=limit,
                dry_run=dry_run,
                feed_url=feed_url,
            )
        await _renew_lease(session_factory, source_id, owner)
        if not dry_run:
            try:
                await _prefit(session_factory, run)
            except Exception:  # noqa: BLE001
                logger.exception("sync.prefit.crashed", extra={"source": source_code})
        return run
    except Exception as exc:
        error = classify_exception(exc).line()
        raise
    finally:
        # El batec acaba la renovacio en curs i surt; mai `beat.cancel()`, que
        # podria tallar un UPDATE a mig cami (vegeu `_heartbeat`).
        stop_beat.set()
        await beat
        try:
            if not dry_run:
                await _write_health(
                    session_factory, source_id, started=started, run=run, error=error
                )
        finally:
            await _release_lease(session_factory, source_id, owner)


def invalid_sync_options(
    *,
    mode: str,
    backfill_until: date | None,
    resume: bool,
    dry_run: bool = False,
    feed_url: str | None = None,
) -> str | None:
    """El motiu pel qual una combinacio d'opcions no es valida (o `None`).

    L'incremental no escriu mai el checkpoint: `--resume` en incremental
    partiria del checkpoint d'un backfill antic i acabaria `success` sense
    haver llegit res nou. Per aixo nomes es permet amb `--backfill-until`.
    """
    if resume and backfill_until is None:
        return "--resume requereix --backfill-until"
    if resume and mode != "backfill":
        return "--resume nomes es permet en mode backfill"
    if feed_url is not None and not dry_run:
        return "--feed-url nomes es permet amb --dry-run"
    return None


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


DEFAULT_SOURCE = "placsp_agregadas"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.sync",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        default=None,
        choices=sorted(SOURCES),
        help=f"Font a sincronitzar (per defecte {DEFAULT_SOURCE}); amb --status, filtra.",
    )
    parser.add_argument("--max-pages", dest="max_pages", type=int, default=None)
    parser.add_argument("--backfill-until", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--resume", action="store_true", help="Backfill: repren des del checkpoint de la font."
    )
    parser.add_argument(
        "--every", type=float, default=None, help="Repeteix el sync cada N segons fins a SIGINT."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Nomes imprimeix la salut de les fonts (sense lease ni sync).",
    )
    return parser


# --------------------------------------------------------------------------- #
# Estat (`--status`)                                                           #
# --------------------------------------------------------------------------- #

#: Columnes de salut de spec §6.1, en l'ordre en que s'imprimeixen.
_STATUS_FIELDS = (
    "last_sync_status",
    "last_sync_started_at",
    "last_sync_finished_at",
    "last_sync_pages",
    "last_sync_entries",
    "last_sync_tombstones",
    "last_sync_error",
    "last_feed_updated_at",
    "updated_watermark",
    "sync_lease_owner",
    "sync_lease_until",
)


def _status_value(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    return str(value)


def format_source_health(source: Source, now: datetime | None = None) -> str:
    """La salut d'una font com a linies `clau: valor` (sense DSN ni secrets)."""
    moment = now or _now()
    until = _as_utc(source.sync_lease_until)
    lines = [f"{source.code}  ({source.name})"]
    rows = [("activa", "si" if source.enabled else "no")]
    rows += [(name, _status_value(getattr(source, name))) for name in _STATUS_FIELDS]
    rows.append(("lease", "actiu" if until is not None and until > moment else "lliure"))
    width = max(len(name) for name, _ in rows)
    lines += [f"  {name.ljust(width)} : {value}" for name, value in rows]
    return "\n".join(lines)


async def print_status(
    session_factory: async_sessionmaker[AsyncSession], source_code: str | None = None
) -> int:
    """Imprimeix la salut de les fonts registrades a la base de dades. Nomes lectura."""
    query = sa.select(Source).order_by(Source.code)
    if source_code is not None:
        query = query.where(Source.code == SOURCES[source_code].db_code)
    async with session_factory() as session:
        sources = list((await session.execute(query)).scalars())
    if not sources:
        print(f"Font {source_code} encara no registrada (cap sync)" if source_code else "Cap font")
        return 0
    print("\n\n".join(format_source_health(source) for source in sources))
    return 0


async def _cycle(session_factory, args: argparse.Namespace, **kwargs) -> int:
    mode = "backfill" if args.backfill_until else "incremental"
    try:
        run = await run_source_sync(
            session_factory,
            args.source,
            mode=mode,
            max_pages=args.max_pages,
            backfill_until=args.backfill_until,
            resume=args.resume,
            **kwargs,
        )
    except SyncAlreadyRunning:
        print(f"Sync ja en curs per a {args.source}", file=sys.stderr)
        return 1
    except SourceDisabled:
        print(f"Font desactivada: {args.source}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001
        logger.exception("sync.cycle.failed")
        return 1
    print(
        f"Sync {run.status.value.upper()} (run_id={run.id}) pagines={run.pages_processed} "
        f"entrades={run.records_fetched} lapides={run.tombstones_applied} "
        f"fallides={run.records_failed} conegudes={run.records_failed_known}"
    )
    if run.records_failed_known:
        # Nomes presentacio: les fallades conegudes no canvien l'estat del run ni la
        # marca d'aigua (success l'avanca; partial/failed no). Cap detall del run.
        watermark = "advanced" if run.status == IngestionStatus.SUCCESS else "held"
        print(
            f"WARNING known deterministic failures: {run.records_failed_known} - "
            f"watermark {watermark}; affected records require retry after "
            "parser/schema change or explicit backfill"
        )
    return EXIT_CODES.get(run.status.value, 1)


async def amain(
    argv: list[str] | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    stop_event: asyncio.Event | None = None,
    **kwargs,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.status:
        args.source = args.source or DEFAULT_SOURCE
        problem = invalid_sync_options(
            mode="backfill" if args.backfill_until else "incremental",
            backfill_until=args.backfill_until,
            resume=args.resume,
        )
        if problem:
            parser.error(problem)
    if session_factory is None:
        from app.db.session import SessionFactory

        session_factory = SessionFactory
    if args.status:
        return await print_status(session_factory, args.source)
    code = await _cycle(session_factory, args, **kwargs)
    if args.every is None:
        return code
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=args.every)
        if stop.is_set():
            break
        code = await _cycle(session_factory, args, **kwargs)
    return code


def main() -> int:
    configure_logging()

    async def _runner() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(sig, stop.set)
        return await amain(stop_event=stop)

    return asyncio.run(_runner())


if __name__ == "__main__":
    sys.exit(main())
