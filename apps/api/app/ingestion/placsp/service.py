"""Orquestracio de la ingesta de PLACSP.

Uneix client + parser + normalitzador + persistencia, i es qui garanteix les
tres regles d'idempotencia:

  A. mateix `external_id` i mateix hash  -> UNCHANGED (no es crea res)
  B. mateix `external_id`, hash diferent -> nova revisio (SourceRecord nou,
     mateix Tender, dades actualitzades)
  C. `external_id` nou                   -> Tender nou

Les tres estan protegides tambe per constraints de base de dades: no depenen
nomes d'aquest codi.

Garanties no destructives (radar 3B-1, spec 4.2-4.4). Sigui U l'`updated` de la
publicacio rebuda i U* el `Tender.source_updated_at` vigent; es comparen ABANS
de tocar res:

  * U < U*                -> superada: es desa el SourceRecord, no s'aplica res.
  * U = U*, hash diferent -> conflicte: es desen tots dos, no s'aplica res.
  * U > U* o U* nul       -> s'aplica (B/C).

Una lapida (`at:deleted-entry`) es presencia a la font, no un estat: marca
`source_removed_at` (si `when` >= U*) i mai toca `Tender.status`. Les absencies
de documents i lots nomes es dedueixen d'una entrada completa que s'aplica.

Aillament per unitat (hotfix radar, 2026-10-07). Cada entrada i cada lapida
s'aplica dins d'un SAVEPOINT (`session.begin_nested()`) i es confirma sola. Si
falla, nomes es desfa aquella unitat: la transaccio exterior continua viva, el
SourceRecord cru es desa igualment com a `failed` (amb un missatge segur de
`app.ingestion.errors`) i la seguent unitat continua en la mateixa sessio. Abans
es feia `session.rollback()` sencer, que EXPIRAVA la font i el run que el servei
te en memoria entre unitats: la seguent llegia `source.id`, SQLAlchemy intentava
recarregar-lo sincronament dins la sessio async (MissingGreenlet) i totes les
entrades i lapides posteriors fallaven.

Reintents (mateix contingut, mateix hash; el registre fallit es reutilitza, no
es duplica): una fallada transitoria (`network_source`, `unexpected`) es torna a
provar sempre; una de determinista (`db_data`, `integrity_constraint`,
`parse_validation`) nomes si ha canviat el `parser_version` o la revisio
d'esquema del codi (`app.db.revision.SCHEMA_REVISION`, desada a
`failed_schema_revision`). Si no, es una fallada coneguda: compta a
`records_failed_known`, surt al resum i NO fa el run `partial` ni frena la marca
d'aigua. Identitat dels comptadors: `fetched = new + changed + unchanged + failed
+ failed_known + superseded + conflicted + tombstones_applied` (excepcio: una
entrada que adopta una lapida orfena compta `new` i `tombstones_applied`).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.revision import SCHEMA_REVISION
from app.ingestion.errors import (
    PARSE_VALIDATION,
    TRANSIENT_CLASSES,
    SafeError,
    classify_exception,
    error_class_of_message,
    known_failures_line,
    safe_error_line,
    summary_line,
)
from app.ingestion.placsp import parser as placsp_parser
from app.ingestion.placsp.client import FeedFetchError, PlacspClient
from app.ingestion.placsp.normalizer import NormalizedTender, normalize
from app.ingestion.sources import SOURCES
from app.models.cpv import CPVCode
from app.models.enums import IngestionStatus, ProcessingStatus
from app.models.sources import IngestionRun, Source, SourceRecord
from app.models.tenders import (
    Buyer,
    Tender,
    TenderCPV,
    TenderDocument,
    TenderLot,
    TenderLotCPV,
)
from app.services.files import compute_sha256
from app.services.storage import StorageService, get_storage
from app.services.tender_lifecycle import record_revision
from app.tenders.search import (
    build_search_text,
    ordered_lot_titles,
    refresh_search_text_for_buyer,
)

logger = logging.getLogger("app.ingestion.placsp")

RAW_PREFIX = "sources/placsp"

#: `error_message` d'una publicacio que no s'aplica perque n'hi ha una de mes nova.
SUPERSEDED = "superseded"
#: `error_message` d'una lapida d'un expedient que encara no coneixem. Es queda
#: sense `tender_id` fins que l'entrada arriba (backfill cap enrere).
ORPHAN_TOMBSTONE = "tombstone: licitacio encara desconeguda"
#: `error_message` d'una lapida pendent que s'ha aplicat en crear la licitacio.
ADOPTED_TOMBSTONE = "tombstone: aplicada en crear la licitacio"

#: Veredictes de l'ordre entre la publicacio rebuda i la canonica.
APPLY, OLDER, SAME = "apply", "older", "same"

MODE_INCREMENTAL, MODE_BACKFILL = "incremental", "backfill"
#: Solapament de l'incremental: es relleguen les entrades fins a 48 h abans de la marca.
OVERLAP_HOURS = 48
#: Pagines com a maxim per run si no se'n demana cap (l'incremental s'atura abans).
DEFAULT_MAX_PAGES = 50


#: La classe d'error nomes es llegeix a l'inici de la linia (despres del prefix
#: de pagina o de l'`external_id` d'una entrada fallida): mai dins del missatge.
_CLASS_IN_LINE = re.compile(
    r"^(?:pagina \d+: )?(?:https?://\S+: )?([A-Za-z_][\w.]*(?:Error|Exception))(?=:|$)"
)
_PAGE_PREFIX = re.compile(r"^pagina \d+")
_REASONS = {
    "FeedFetchError": "feed no disponible",
    "ParseError": "contingut no valid",
}


def _safe_line(line: str) -> str:
    """Una linia d'error segura (<= 200 caracters).

    Les linies que produeix el servei ja venen classificades
    (`[pagina N: ]classe: Excepcio[ (SQLSTATE)][ taula.columna]`) i passen tal
    qual si compleixen la gramatica estricta. Qualsevol altre text (heretat o
    inesperat) es redueix a `[pagina N: ]Classe: motiu`, sense el missatge.
    """
    classified = safe_error_line(line)
    if classified is not None:
        return classified
    page = _PAGE_PREFIX.match(line)
    klass = _CLASS_IN_LINE.match(line)
    name = klass.group(1) if klass else "Error"
    reason = _REASONS.get(name, "error inesperat")
    if name == "ParseError" and klass:
        # Els missatges del parser son textos propis (sense SQL ni dades): es conserven curts.
        tail = line[klass.end() :].lstrip(": ").strip()
        if tail and "\n" not in tail:
            reason = tail[:120]
    text = f"{name}: {reason}"
    if page:
        text = f"{page.group(0)}: {text}"
    return text[:200]


def _tombstone_when(element: ET.Element) -> datetime | None:
    parsed = placsp_parser.parse_deleted_entry(element)
    return _utc(parsed.updated_at) if parsed is not None else None


def _entry_updated(element: ET.Element) -> datetime | None:
    """L'`updated` d'una entrada ATOM en UTC (sense valor si falta o no es llegeix)."""
    value = element.findtext(f"{placsp_parser.ATOM}updated")
    return _utc(placsp_parser._datetime(value))  # noqa: SLF001 - mateix paquet


def _utc(value: datetime | None) -> datetime | None:
    """L'instant en UTC.

    Els instants de la font s'escriuen sempre en UTC; un valor sense zona es
    llegeix com a UTC (SQLite no conserva l'offset).
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def publication_order(incoming: datetime | None, canonical: datetime | None) -> str:
    """Ordre de la publicacio rebuda (U) respecte de la canonica (U*).

    Sense U* (tenders d'abans de 3B-1 o nous) s'aplica i s'inicialitza. Sense U
    no es pot demostrar que sigui mes nova: no s'aplica (mai regressa).
    """
    canonical = _utc(canonical)
    if canonical is None:
        return APPLY
    incoming = _utc(incoming)
    if incoming is None or incoming < canonical:
        return OLDER
    if incoming == canonical:
        return SAME
    return APPLY


@dataclass(slots=True)
class RunCounters:
    fetched: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    failed: int = 0
    tenders_created: int = 0
    tenders_updated: int = 0
    superseded: int = 0
    conflicted: int = 0
    tombstones_applied: int = 0
    entries_restored: int = 0
    #: Fallades deterministes ja conegudes que no es reintenten (no fan `partial`).
    failed_known: int = 0
    #: Linies d'error ja segures (`SafeError.line`), en ordre d'aparicio.
    errors: list[str] = field(default_factory=list)
    #: Els errors classificats (per al resum per classe del run).
    failures: list[SafeError] = field(default_factory=list)
    #: Classe de cada fallada coneguda (per al resum «N fallades conegudes (...)»).
    known_classes: list[str] = field(default_factory=list)

    def add_failure(self, error: SafeError, prefix: str | None = None) -> None:
        self.failures.append(error)
        self.errors.append(error.line(prefix))

    def add_known_failure(self, error_class: str) -> None:
        self.failed_known += 1
        self.known_classes.append(error_class)

    def merge(self, other: RunCounters) -> None:
        """Suma els comptadors d'una unitat ja confirmada."""
        for name in self.as_log():
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.errors.extend(other.errors)
        self.failures.extend(other.failures)
        self.known_classes.extend(other.known_classes)

    def as_log(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "new": self.new,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "failed_known": self.failed_known,
            "tenders_created": self.tenders_created,
            "tenders_updated": self.tenders_updated,
            "superseded": self.superseded,
            "conflicted": self.conflicted,
            "tombstones_applied": self.tombstones_applied,
            "entries_restored": self.entries_restored,
        }


@dataclass(slots=True)
class UnitTrace:
    """El que una unitat ha fet fora de la base abans de fallar.

    El raw ja pujat a l'storage es reutilitza (mateixa clau i mateix id) per
    desar el SourceRecord fallit: cap pujada duplicada.
    """

    external_id: str | None = None
    record_id: uuid.UUID | None = None
    storage_key: str | None = None


#: Veredictes sobre un registre amb el mateix contingut que ja es a casa.
UNCHANGED, RETRY, KNOWN_FAILURE = "unchanged", "retry", "known_failure"


def _existing_verdict(record: SourceRecord) -> str:
    """Que es fa quan torna a arribar un contingut (mateix hash) que ja tenim.

    * No fallit -> UNCHANGED.
    * Fallit per una classe transitoria (`network_source`, `unexpected`) -> RETRY.
    * Fallit per una classe determinista (`db_data`, `integrity_constraint`,
      `parse_validation`, o un missatge antic sense classe): RETRY nomes si ha
      canviat el `parser_version` o la revisio d'esquema del codi des de la
      fallada; si no, KNOWN_FAILURE (es compta a part i no fa el run `partial`).
    """
    if record.processing_status != ProcessingStatus.FAILED:
        return UNCHANGED
    if error_class_of_message(record.error_message) in TRANSIENT_CLASSES:
        return RETRY
    if (
        record.parser_version != placsp_parser.PARSER_VERSION
        or record.failed_schema_revision != SCHEMA_REVISION
    ):
        return RETRY
    return KNOWN_FAILURE


def _known_class(record: SourceRecord) -> str:
    """La classe d'una fallada coneguda (els missatges antics son de parseig)."""
    return error_class_of_message(record.error_message) or PARSE_VALIDATION


def _unit_external_id(element: ET.Element, tombstone: bool) -> str | None:
    if tombstone:
        value = element.get("ref")
    else:
        value = element.findtext(f"{placsp_parser.ATOM}id")
    return (value or "").strip() or None


def _unit_updated(element: ET.Element, tombstone: bool) -> datetime | None:
    """L'`updated` (entrada) o el `when` (lapida) tal com el publica la font."""
    if tombstone:
        parsed = placsp_parser.parse_deleted_entry(element)
        return parsed.updated_at if parsed is not None else None
    return placsp_parser._datetime(element.findtext(f"{placsp_parser.ATOM}updated"))  # noqa: SLF001


@dataclass(frozen=True, slots=True)
class ApplyOutcome:
    tender: Tender
    created: bool
    #: La publicacio era posterior a la lapida: l'entrada torna a ser a la font.
    restored: bool = False
    #: En crear-la s'hi ha aplicat una lapida que havia arribat abans.
    tombstone_adopted: bool = False


def raw_storage_key(record_id: uuid.UUID, moment: datetime | None = None) -> str:
    """Espai de noms propi per a les dades publiques.

    Els documents corporatius viuen a `companies/...` i no s'han de barrejar
    mai amb aixo: son dades de tenant, amb un cicle de vida i una politica de
    privacitat completament diferents.
    """
    stamp = moment or datetime.now(UTC)
    return f"{RAW_PREFIX}/{stamp:%Y/%m}/{record_id}.xml"


class PlacspIngestionService:
    def __init__(self, session: AsyncSession, storage: StorageService | None = None) -> None:
        self.session = session
        self.storage = storage or get_storage()

    # ------------------------------------------------------------------ font
    async def get_or_create_source(self, code: str = "placsp") -> Source:
        definition = SOURCES[code]
        source = (
            await self.session.execute(sa.select(Source).where(Source.code == definition.db_code))
        ).scalar_one_or_none()
        if source is None:
            source = Source(
                code=definition.db_code,
                name=definition.name,
                source_type=definition.source_type,
                base_url=definition.feed_url,
                enabled=True,
            )
            self.session.add(source)
            await self.session.flush()
        elif source.base_url is None:
            source.base_url = definition.feed_url
            await self.session.flush()
        return source

    # ------------------------------------------------------------------ run
    async def run(
        self,
        *,
        limit: int | None = None,
        max_files: int | None = None,
        max_pages: int | None = None,
        mode: str = "incremental",
        backfill_until: date | None = None,
        fixture: Path | None = None,
        feed_url: str | None = None,
        dry_run: bool = False,
        resume: bool = False,
        source_code: str = "placsp",
    ) -> IngestionRun:
        """Executa una ingesta i retorna el registre d'auditoria.

        `mode="incremental"` recorre el feed des de la pagina 1 fins que una
        pagina nomes te entrades anteriors a `marca - OVERLAP_HOURS`;
        `mode="backfill"` fins a la data `backfill_until`. `max_files` es
        l'alies antic de `max_pages`.
        """
        if mode not in (MODE_INCREMENTAL, MODE_BACKFILL):
            raise ValueError(f"mode desconegut: {mode!r}")
        page_budget = max_pages or max_files or DEFAULT_MAX_PAGES
        started = time.monotonic()
        source = await self.get_or_create_source(source_code)

        # Llindar (UTC) per sota del qual una pagina ja no aporta res de nou.
        cutoff: datetime | None = None
        if mode == MODE_INCREMENTAL and source.updated_watermark is not None:
            cutoff = _utc(source.updated_watermark) - timedelta(hours=OVERLAP_HOURS)
        elif mode == MODE_BACKFILL and backfill_until is not None:
            cutoff = datetime.combine(backfill_until, datetime.min.time(), tzinfo=UTC)

        run = IngestionRun(
            source_id=source.id,
            status=IngestionStatus.RUNNING,
            checkpoint_before=source.checkpoint,
            parameters=(
                f"mode={mode} limit={limit} max_pages={page_budget} "
                f"backfill_until={backfill_until} dry_run={dry_run} "
                f"fixture={fixture.name if fixture else None} resume={resume}"
            )[:500],
        )
        self.session.add(run)
        # Commit immediat: el `rollback` d'una entrada fallida (vegeu
        # `_process_document`) no s'ha d'endur el run ni la font, o les entrades
        # seguents fallarien per clau forana i el run no quedaria auditable.
        await self.session.commit()

        counters = RunCounters()
        checkpoint_after = source.checkpoint
        seen_urls: set[str] = set()
        pages_checked = 0
        # La cadena de pagines no s'ha completat: no es pot garantir res de
        # les pagines que no s'han obtingut (cap absencia derivada, sense marca).
        incomplete = False
        # Un `limit` que talla el run fa que no tot l'`updated` vist estigui processat.
        cut_by_limit = False
        max_updated: datetime | None = None
        is_feed_walk = fixture is None

        try:
            if fixture is not None:
                checkpoint_after = (
                    await self._process_document(
                        fixture.read_bytes(), str(fixture), source, run, counters, limit, dry_run
                    )
                    or checkpoint_after
                )
                pages_checked = 1
            else:
                start_url = feed_url or (
                    source.checkpoint
                    if (resume and source.checkpoint)
                    else (source.base_url or SOURCES[source_code].feed_url)
                )
                async with PlacspClient() as client:
                    url: str | None = start_url
                    reached_end = False  # solapament/data assolits, o fi de la cadena
                    for page_index in range(page_budget):
                        if url is None or url in seen_urls:
                            reached_end = True
                            break
                        seen_urls.add(url)
                        if page_index > 0:
                            await client.pause()
                        try:
                            fetched = await client.fetch(url)
                            if fetched.not_modified:
                                # 304: res a processar. A la pagina 1 el feed no ha canviat
                                # (success); a una posterior no sabem el `next`: cadena
                                # incompleta.
                                incomplete = page_index > 0
                                reached_end = True
                                if page_index == 0:
                                    pages_checked = 1  # contracte: 304 a la pagina 1 = 1 pagina
                                break
                            feed = placsp_parser.parse_feed(fetched.body or b"")
                        except (FeedFetchError, placsp_parser.ParseError) as exc:
                            if page_index == 0:
                                raise
                            # Una pagina posterior fallida: tot el que s'ha desat de les
                            # anteriors es valid, pero el run queda `partial`.
                            incomplete = True
                            reached_end = True
                            error = classify_exception(exc)
                            counters.add_failure(error, prefix=f"pagina {page_index + 1}")
                            logger.error(
                                "placsp.page.failed",
                                extra={
                                    "url": url,
                                    "error_class": error.error_class,
                                    "error": error.line(),
                                },
                            )
                            break

                        updates = [_entry_updated(e) for e in feed.entries]
                        # Les lapides compten: una pagina nomes amb lapides recents no atura.
                        stamps = updates + [_tombstone_when(e) for e in feed.deleted_entries]
                        if (
                            cutoff is not None
                            and stamps
                            and all(u is not None and u < cutoff for u in stamps)
                        ):
                            # Tota la pagina es anterior al solapament: ja estava processada.
                            reached_end = True
                            break

                        pages_checked += 1

                        next_url = await self._process_document(
                            fetched.body or b"",
                            url,
                            source,
                            run,
                            counters,
                            limit,
                            dry_run,
                            feed=feed,
                        )
                        known = [u for u in updates if u is not None]
                        if known:
                            max_updated = max(known + ([max_updated] if max_updated else []))
                        # Backfill: el checkpoint es la pagina que toca llegir; incremental:
                        # l'ultima pagina completa.
                        checkpoint_after = next_url if (mode == MODE_BACKFILL and next_url) else url
                        if limit is not None and counters.fetched >= limit:
                            # La pagina pot haver quedat a mitges: el checkpoint no passa
                            # de l'ultima pagina completa (`--resume` la rellegeix).
                            cut_by_limit = True
                            reached_end = True
                            incomplete = mode == MODE_BACKFILL or cutoff is not None
                            if mode == MODE_BACKFILL:
                                checkpoint_after = url
                            break
                        if next_url is None:
                            # Fi de la cadena. En incremental amb marca, sense haver arribat
                            # al solapament, no es pot garantir que no falti res.
                            reached_end = True
                            incomplete = cutoff is not None and mode == MODE_INCREMENTAL
                            break
                        url = next_url
                    if not reached_end:
                        # Pressupost de pagines esgotat amb cadena pendent.
                        incomplete = mode == MODE_BACKFILL or cutoff is not None

            # Nomes el backfill escriu el checkpoint de represa: l'incremental sempre
            # comença a la pagina 1 i no ha de trepitjar-lo.
            if not dry_run and mode == MODE_BACKFILL:
                source.checkpoint = checkpoint_after
                source.checkpoint_updated_at = datetime.now(UTC)

            run.status = (
                IngestionStatus.PARTIAL
                if (counters.failed or incomplete)
                else IngestionStatus.SUCCESS
            )
            if (
                run.status == IngestionStatus.SUCCESS
                and is_feed_walk
                and not dry_run
                and not cut_by_limit
                and max_updated is not None
            ):
                current = _utc(source.updated_watermark)
                if current is None or max_updated > current:
                    source.updated_watermark = max_updated

        except FeedFetchError as exc:
            error = classify_exception(exc)
            await self._reset_session(source, run)
            run.status = IngestionStatus.FAILED
            counters.add_failure(error)
            logger.error(
                "placsp.run.failed",
                extra={
                    "run_id": str(run.id),
                    "error_class": error.error_class,
                    "error": error.line(),
                },
            )
        except Exception as exc:  # noqa: BLE001 - qualsevol error deixa rastre auditable
            error = classify_exception(exc)
            await self._reset_session(source, run)
            run.status = IngestionStatus.FAILED
            counters.add_failure(error)
            logger.error(
                "placsp.run.crashed",
                extra={
                    "run_id": str(run.id),
                    "error_class": error.error_class,
                    "error": error.line(),
                },
            )

        run.finished_at = datetime.now(UTC)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.pages_processed = pages_checked
        run.records_fetched = counters.fetched
        run.records_new = counters.new
        run.records_changed = counters.changed
        run.records_unchanged = counters.unchanged
        run.records_failed = counters.failed
        run.tenders_created = counters.tenders_created
        run.tenders_updated = counters.tenders_updated
        run.records_superseded = counters.superseded
        run.records_conflicted = counters.conflicted
        run.tombstones_applied = counters.tombstones_applied
        run.entries_restored = counters.entries_restored
        run.records_failed_known = counters.failed_known
        run.checkpoint_after = checkpoint_after
        # Primera linia: recompte per classe amb el primer error representatiu
        # de cadascuna; despres, si n'hi ha, «N fallades conegudes (...)»; i les
        # linies segures diferents (com a molt 50).
        summary = summary_line(counters.failures)
        known = known_failures_line(counters.known_classes)
        lines = [_safe_line(line) for line in dict.fromkeys(counters.errors)][:50]
        head = [line for line in (summary, known) if line]
        run.error_summary = "\n".join(head + lines) or None

        await self.session.commit()
        logger.info(
            "placsp.run.finished",
            extra={"run_id": str(run.id), "status": run.status.value, **counters.as_log()},
        )
        return run

    # ------------------------------------------------------------- documents
    async def _process_document(
        self,
        content: bytes,
        url: str,
        source: Source,
        run: IngestionRun,
        counters: RunCounters,
        limit: int | None,
        dry_run: bool,
        feed: placsp_parser.ParsedFeed | None = None,
    ) -> str | None:
        if feed is None:
            feed = placsp_parser.parse_feed(content)
        logger.info(
            "placsp.feed.parsed",
            extra={"url": url, "entries": len(feed.entries), "deleted": len(feed.deleted_entries)},
        )

        for element in feed.entries:
            if limit is not None and counters.fetched >= limit:
                break
            counters.fetched += 1
            await self._process_unit(element, url, source, run, counters, dry_run, tombstone=False)

        # Les lapides despres de les entrades: l'ordre per `updated`/`when` fa
        # que el resultat no depengui de la posicio, pero aixi una entrada i la
        # seva lapida a la mateixa pagina desen primer les dades.
        for element in feed.deleted_entries:
            if limit is not None and counters.fetched >= limit:
                break
            counters.fetched += 1
            await self._process_unit(element, url, source, run, counters, dry_run, tombstone=True)

        return feed.next_url

    # ------------------------------------------------------- aillament
    async def _process_unit(
        self,
        element: ET.Element,
        feed_url: str,
        source: Source,
        run: IngestionRun,
        counters: RunCounters,
        dry_run: bool,
        *,
        tombstone: bool,
    ) -> None:
        """Una entrada o una lapida, aillada: SAVEPOINT propi i commit propi.

        Els comptadors de la unitat nomes se sumen als del run un cop
        confirmada; si falla, nomes compta com a `failed` (comptadors exactes).
        """
        unit = RunCounters()
        trace = UnitTrace()
        process = self._process_tombstone if tombstone else self._process_entry
        committing = False
        try:
            async with self.session.begin_nested():
                await process(element, feed_url, source, run, unit, dry_run, trace=trace)
            committing = True
            await self.session.commit()
        except Exception as exc:  # noqa: BLE001 - una unitat dolenta no atura el feed
            error = classify_exception(exc)
            if committing:
                # El SAVEPOINT ja s'havia alliberat: ha fallat el COMMIT i la
                # transaccio sencera (nomes aquesta unitat) queda desfeta.
                await self._reset_session(source, run)
            else:
                # El context ja ha fet ROLLBACK TO SAVEPOINT: la transaccio
                # exterior continua viva. Nomes cal assegurar que la font i el run
                # (els unics objectes que viuen entre unitats) no han quedat expirats.
                try:
                    await self._ensure_loaded(source, run)
                except Exception:  # noqa: BLE001 - p. ex. el ROLLBACK TO no ha anat be
                    await self._reset_session(source, run)
            counters.failed += 1
            counters.add_failure(error)
            logger.warning(
                "placsp.tombstone.failed" if tombstone else "placsp.entry.failed",
                extra={
                    "url": feed_url,
                    "external_id": trace.external_id,
                    "error_class": error.error_class,
                    "error": error.line(),
                },
            )
            if not dry_run:
                await self._persist_failed_unit(
                    element, feed_url, source, run, trace, error, tombstone=tombstone
                )
            return
        counters.merge(unit)

    async def _ensure_loaded(self, *instances) -> None:
        """Recarrega (amb await) els objectes que un rollback ha deixat expirats.

        Amb la sessio async, llegir un atribut expirat fa IO implicita i peta amb
        MissingGreenlet: aixo es el que enverinava totes les unitats seguents.
        """
        for instance in instances:
            state = sa.inspect(instance)
            if state.expired or state.expired_attributes:
                await self.session.refresh(instance)

    async def _reset_session(self, source: Source, run: IngestionRun) -> None:
        """Rollback sencer i recarrega de la font i del run (mai llanca)."""
        try:
            await self.session.rollback()
            await self._ensure_loaded(source, run)
        except Exception:  # noqa: BLE001 - nomes es registra
            logger.exception("placsp.session.reset_failed")

    async def _persist_failed_unit(
        self,
        element: ET.Element,
        feed_url: str,
        source: Source,
        run: IngestionRun,
        trace: UnitTrace,
        error: SafeError,
        *,
        tombstone: bool,
    ) -> None:
        """Desa el SourceRecord cru d'una unitat fallida (`failed`, missatge segur).

        Si ja hi ha el registre d'aquest mateix contingut (un reintent que torna a
        fallar), s'actualitza: mai no es duplica. Sense identificador de l'entrada
        no hi ha registre possible (`external_id` es obligatori).
        """
        external_id = trace.external_id or _unit_external_id(element, tombstone)
        if not external_id:
            return
        raw_xml = ET.tostring(element, encoding="utf-8")
        raw_hash = compute_sha256(raw_xml)
        message = error.line()
        try:
            async with self.session.begin_nested():
                existing_id = await self.session.scalar(
                    sa.select(SourceRecord.id).where(
                        SourceRecord.source_id == source.id,
                        SourceRecord.external_id == external_id,
                        SourceRecord.raw_hash == raw_hash,
                    )
                )
                if existing_id is not None:
                    await self.session.execute(
                        sa.update(SourceRecord)
                        .where(SourceRecord.id == existing_id)
                        .values(
                            processing_status=ProcessingStatus.FAILED,
                            error_message=message,
                            ingestion_run_id=run.id,
                            # El reintent ha fallat amb AQUEST parser i AQUEST esquema.
                            parser_version=placsp_parser.PARSER_VERSION,
                            failed_schema_revision=SCHEMA_REVISION,
                        )
                        .execution_options(synchronize_session=False)
                    )
                else:
                    record_id = trace.record_id or uuid.uuid4()
                    storage_key = trace.storage_key
                    if storage_key is None:
                        storage_key = raw_storage_key(record_id)
                        await self.storage.upload(storage_key, raw_xml, "application/xml")
                    previous = (
                        await self.session.scalar(
                            sa.select(sa.func.count())
                            .select_from(SourceRecord)
                            .where(
                                SourceRecord.source_id == source.id,
                                SourceRecord.external_id == external_id,
                            )
                        )
                    ) or 0
                    updated = _unit_updated(element, tombstone)
                    self.session.add(
                        SourceRecord(
                            id=record_id,
                            source_id=source.id,
                            external_id=external_id,
                            external_version_id=updated.isoformat() if updated else None,
                            revision_number=previous + 1,
                            source_url=feed_url,
                            fetched_at=datetime.now(UTC),
                            source_updated_at=_utc(updated),
                            raw_hash=raw_hash,
                            raw_storage_key=storage_key,
                            raw_size=len(raw_xml),
                            mime_type="application/xml",
                            parser_version=placsp_parser.PARSER_VERSION,
                            processing_status=ProcessingStatus.FAILED,
                            error_message=message,
                            failed_schema_revision=SCHEMA_REVISION,
                            ingestion_run_id=run.id,
                        )
                    )
                    await self.session.flush()
            await self.session.commit()
        except Exception as exc:  # noqa: BLE001 - el registre fallit no pot aturar el feed
            await self._reset_session(source, run)
            logger.error(
                "placsp.unit.failed_record_not_saved",
                extra={
                    "url": feed_url,
                    "external_id": external_id,
                    "error_class": classify_exception(exc).error_class,
                },
            )

    # ------------------------------------------------------- unitats
    async def _process_entry(
        self,
        element: ET.Element,
        feed_url: str,
        source: Source,
        run: IngestionRun,
        counters: RunCounters,
        dry_run: bool,
        *,
        trace: UnitTrace | None = None,
    ) -> None:
        """Una entrada. No fa commit: la confirma (o la desfa) `_process_unit`."""
        trace = trace if trace is not None else UnitTrace()
        raw_xml = ET.tostring(element, encoding="utf-8")
        raw_hash = compute_sha256(raw_xml)
        external_id = element.findtext(f"{placsp_parser.ATOM}id")
        if not external_id:
            raise placsp_parser.ParseError("Entrada sense <id>.")
        external_id = external_id.strip()
        trace.external_id = external_id

        # --- CAS A: contingut identic al que ja teniem ---
        existing = (
            await self.session.execute(
                sa.select(SourceRecord).where(
                    SourceRecord.source_id == source.id,
                    SourceRecord.external_id == external_id,
                    SourceRecord.raw_hash == raw_hash,
                )
            )
        ).scalar_one_or_none()
        verdict = _existing_verdict(existing) if existing is not None else None
        if verdict == KNOWN_FAILURE:
            # Ja va fallar de manera determinista amb aquest parser i aquest esquema:
            # tornar-ho a provar donaria el mateix. Visible, pero no fa `partial`.
            counters.add_known_failure(_known_class(existing))
            return
        if verdict == UNCHANGED:
            counters.unchanged += 1
            if not dry_run and existing.tender_id:
                await self.session.execute(
                    sa.update(Tender)
                    .where(Tender.id == existing.tender_id)
                    .values(last_seen_at=datetime.now(UTC))
                )
            return

        previous_revisions = (
            await self.session.execute(
                sa.select(sa.func.count())
                .select_from(SourceRecord)
                .where(
                    SourceRecord.source_id == source.id,
                    SourceRecord.external_id == external_id,
                )
            )
        ).scalar_one() or 0
        if existing is not None:
            # Reintent d'una publicacio fallida: el seu propi registre no compta.
            previous_revisions -= 1
        is_first_revision = previous_revisions == 0

        if dry_run:
            placsp_parser.parse_entry(element)  # valida que es parsejable
            counters.new += 1 if is_first_revision else 0
            counters.changed += 0 if is_first_revision else 1
            return

        parsed = None
        data: NormalizedTender | None = None
        parse_error: SafeError | None = None
        try:
            parsed = placsp_parser.parse_entry(element)
            if element.find("{*}ContractFolderStatus") is None:
                # Sense la carrega CODICE l'entrada no es una publicacio completa:
                # aplicar-la deixaria a NULL dades que la font si que te i en
                # deduiria absencies de documents i lots que no ha publicat.
                raise placsp_parser.ParseError(
                    "Entrada sense ContractFolderStatus: publicacio incompleta."
                )
            data = normalize(parsed)
        except Exception as exc:  # noqa: BLE001
            # Qualsevol excepcio en llegir o normalitzar l'entrada es un error de
            # dades de la font (determinista amb el mateix parser): mai
            # `unexpected`, que es reintentaria a cada run. Nomes el text propi
            # del ParseError es conserva.
            parse_error = SafeError(
                PARSE_VALIDATION,
                type(exc).__name__,
                detail=(
                    classify_exception(exc).detail
                    if isinstance(exc, placsp_parser.ParseError)
                    else None
                ),
            )

        external_version_id = (
            parsed.updated_at.isoformat() if parsed and parsed.updated_at else None
        )
        source_updated_at = _utc(parsed.updated_at) if parsed else None
        source_published_at = (
            datetime.combine(parsed.publication_date, datetime.min.time(), tzinfo=UTC)
            if parsed and parsed.publication_date
            else None
        )
        status = ProcessingStatus.FAILED if parse_error else ProcessingStatus.PENDING
        message = parse_error.line() if parse_error else None
        failed_revision = SCHEMA_REVISION if parse_error else None

        if existing is not None:
            # El raw ja es a l'storage (mateix hash): es reutilitza el registre.
            record = existing
            record.external_version_id = external_version_id
            record.source_updated_at = source_updated_at
            record.source_published_at = source_published_at
            record.parser_version = placsp_parser.PARSER_VERSION
            record.processing_status = status
            record.error_message = message
            record.failed_schema_revision = failed_revision
            record.ingestion_run_id = run.id
        else:
            record_id = uuid.uuid4()
            storage_key = raw_storage_key(record_id)
            # El raw es guarda SEMPRE, fins i tot si el parseig ha fallat: es
            # justament el cas en que voldrem tornar-hi amb un parser millor.
            await self.storage.upload(storage_key, raw_xml, "application/xml")
            trace.record_id, trace.storage_key = record_id, storage_key

            record = SourceRecord(
                id=record_id,
                source_id=source.id,
                external_id=external_id,
                external_version_id=external_version_id,
                revision_number=previous_revisions + 1,
                source_url=feed_url,
                fetched_at=datetime.now(UTC),
                source_updated_at=source_updated_at,
                source_published_at=source_published_at,
                raw_hash=raw_hash,
                raw_storage_key=storage_key,
                raw_size=len(raw_xml),
                mime_type="application/xml",
                parser_version=placsp_parser.PARSER_VERSION,
                processing_status=status,
                error_message=message,
                failed_schema_revision=failed_revision,
                ingestion_run_id=run.id,
            )
            self.session.add(record)
        await self.session.flush()

        if parse_error or data is None:
            counters.failed += 1
            if parse_error:
                counters.add_failure(parse_error)
            return

        tender = await self._find_tender(source, data.external_id)

        # --- Ordre (spec 4.2): es decideix ABANS de tocar res del Tender ---
        if tender is not None:
            order = publication_order(data.source_updated_at, tender.source_updated_at)
            if order != APPLY:
                record.tender_id = tender.id
                record.processing_status = ProcessingStatus.PROCESSED
                if order == OLDER:
                    record.error_message = SUPERSEDED
                    counters.superseded += 1
                    logger.info(
                        "ingestion.superseded",
                        extra={"source": source.code, "external_id": external_id},
                    )
                else:
                    # Mateix `updated`, contingut diferent: no es tria cap de les
                    # dues com a "mes nova". Es conserven totes dues i ho resol
                    # una persona.
                    canonical_hash = await self._canonical_raw_hash(tender)
                    record.processing_status = ProcessingStatus.CONFLICT
                    record.error_message = (
                        f"same updated, raw_hash {canonical_hash} vs {raw_hash}"
                    )
                    counters.conflicted += 1
                    logger.warning(
                        "ingestion.conflict",
                        extra={
                            "source": source.code,
                            "external_id": external_id,
                            "canonical_raw_hash": canonical_hash,
                            "raw_hash": raw_hash,
                        },
                    )
                await self.session.flush()
                return

        outcome = await self._apply(data, source, record, tender)
        record.processing_status = ProcessingStatus.PROCESSED
        record.tender_id = outcome.tender.id

        if outcome.created:
            counters.tenders_created += 1
        else:
            counters.tenders_updated += 1
        if is_first_revision:
            counters.new += 1
        else:
            counters.changed += 1
        if outcome.restored:
            counters.entries_restored += 1
        if outcome.tombstone_adopted:
            counters.tombstones_applied += 1

        await self.session.flush()

    async def _process_tombstone(
        self,
        element: ET.Element,
        feed_url: str,
        source: Source,
        run: IngestionRun,
        counters: RunCounters,
        dry_run: bool,
        *,
        trace: UnitTrace | None = None,
    ) -> None:
        """Una lapida: l'entrada ja no es accessible a la font (spec 4.3).

        Es desa com a SourceRecord (provenance) i, si no es anterior a la
        publicacio canonica, marca `source_removed_at`. L'estat contractual no es
        toca: una lapida no diu si l'expedient s'ha anul.lat, nomes que ja no hi
        es. No fa commit: la confirma (o la desfa) `_process_unit`.
        """
        trace = trace if trace is not None else UnitTrace()
        external_id = (element.get("ref") or "").strip()
        if not external_id:
            raise placsp_parser.ParseError("Lapida sense ref.")
        trace.external_id = external_id
        raw_xml = ET.tostring(element, encoding="utf-8")
        raw_hash = compute_sha256(raw_xml)

        # --- CAS A: la mateixa lapida ja es a casa ---
        existing = await self.session.scalar(
            sa.select(SourceRecord).where(
                SourceRecord.source_id == source.id,
                SourceRecord.external_id == external_id,
                SourceRecord.raw_hash == raw_hash,
            )
        )
        verdict = _existing_verdict(existing) if existing is not None else None
        if verdict == KNOWN_FAILURE:
            counters.add_known_failure(_known_class(existing))
            return
        if verdict == UNCHANGED:
            counters.unchanged += 1
            return

        if dry_run:
            return

        previous_revisions = (
            await self.session.scalar(
                sa.select(sa.func.count())
                .select_from(SourceRecord)
                .where(
                    SourceRecord.source_id == source.id,
                    SourceRecord.external_id == external_id,
                )
            )
        ) or 0
        parsed = placsp_parser.parse_deleted_entry(element)
        when = _utc(parsed.updated_at) if parsed else None
        tender = await self._find_tender(source, external_id)
        external_version_id = (
            parsed.updated_at.isoformat() if parsed and parsed.updated_at else None
        )

        if existing is not None:
            # Reintent d'una lapida fallida: el raw ja es a l'storage.
            record = existing
            record.external_version_id = external_version_id
            record.source_updated_at = when
            record.parser_version = placsp_parser.PARSER_VERSION
            record.processing_status = ProcessingStatus.PENDING
            record.error_message = None
            record.failed_schema_revision = None
            record.ingestion_run_id = run.id
            record.tender_id = tender.id if tender is not None else None
        else:
            record_id = uuid.uuid4()
            storage_key = raw_storage_key(record_id)
            await self.storage.upload(storage_key, raw_xml, "application/xml")
            trace.record_id, trace.storage_key = record_id, storage_key
            record = SourceRecord(
                id=record_id,
                source_id=source.id,
                external_id=external_id,
                external_version_id=external_version_id,
                revision_number=previous_revisions + 1,
                source_url=feed_url,
                fetched_at=datetime.now(UTC),
                source_updated_at=when,
                raw_hash=raw_hash,
                raw_storage_key=storage_key,
                raw_size=len(raw_xml),
                mime_type="application/xml",
                parser_version=placsp_parser.PARSER_VERSION,
                processing_status=ProcessingStatus.PENDING,
                ingestion_run_id=run.id,
                tender_id=tender.id if tender is not None else None,
            )
            self.session.add(record)
        await self.session.flush()

        if when is None:
            error = SafeError(PARSE_VALIDATION, "ParseError", detail="lapida sense when valid")
            record.processing_status = ProcessingStatus.FAILED
            record.error_message = error.line()
            record.failed_schema_revision = SCHEMA_REVISION
            counters.failed += 1
            counters.add_failure(error)
            await self.session.flush()
            return

        record.processing_status = ProcessingStatus.PROCESSED
        if tender is None:
            # Encara no coneixem l'expedient (p. ex. backfill cap enrere: la
            # lapida, mes nova, arriba abans que l'entrada). Queda pendent i
            # s'aplica quan es creï la licitacio. Es un registre nou (compta a
            # `records_new`): aixi els comptadors sumen `records_fetched`.
            record.error_message = ORPHAN_TOMBSTONE
            counters.new += 1
            await self.session.flush()
            return

        if publication_order(when, tender.source_updated_at) == OLDER:
            # Una lapida antiga no amaga una publicacio mes nova.
            record.error_message = SUPERSEDED
            counters.superseded += 1
            await self.session.flush()
            return

        now = datetime.now(UTC)
        if tender.source_removed_at is None:
            tender.source_removed_at = when
        tender.source_updated_at = when
        await self.session.flush()
        outcome = await record_revision(
            self.session, tender, source_record_id=record.id, trigger="tombstone"
        )
        if not outcome.unchanged:
            tender.last_changed_at = now
        counters.tombstones_applied += 1
        logger.info(
            "ingestion.tombstone",
            extra={"source": source.code, "external_id": external_id, "when": when.isoformat()},
        )
        await self.session.flush()

    async def _find_tender(self, source: Source, external_id: str) -> Tender | None:
        return (
            await self.session.execute(
                sa.select(Tender).where(
                    Tender.source_id == source.id, Tender.external_id == external_id
                )
            )
        ).scalar_one_or_none()

    async def _canonical_raw_hash(self, tender: Tender) -> str | None:
        if tender.latest_source_record_id is None:
            return None
        return await self.session.scalar(
            sa.select(SourceRecord.raw_hash).where(
                SourceRecord.id == tender.latest_source_record_id
            )
        )

    async def _adopt_orphan_tombstones(
        self, tender: Tender, source: Source, incoming: datetime | None
    ) -> bool:
        """Lapides que van arribar abans que la licitacio existis.

        Es lliguen totes a la licitacio. Si la mes recent no es anterior a la
        publicacio que l'acaba de crear, la licitacio neix marcada com a retirada
        de la font. Retorna si n'ha aplicat alguna.
        """
        orphans = list(
            (
                await self.session.execute(
                    sa.select(SourceRecord).where(
                        SourceRecord.source_id == source.id,
                        SourceRecord.external_id == tender.external_id,
                        SourceRecord.tender_id.is_(None),
                        SourceRecord.error_message == ORPHAN_TOMBSTONE,
                    )
                )
            ).scalars()
        )
        if not orphans:
            return False
        latest = max(orphans, key=lambda r: _utc(r.source_updated_at))
        applied = publication_order(latest.source_updated_at, incoming) != OLDER
        for orphan in orphans:
            orphan.tender_id = tender.id
            orphan.error_message = SUPERSEDED
        if applied:
            when = _utc(latest.source_updated_at)
            latest.error_message = ADOPTED_TOMBSTONE
            tender.source_removed_at = when
            tender.source_updated_at = when
        return applied

    # ------------------------------------------------------- model canonic
    async def _apply(
        self,
        data: NormalizedTender,
        source: Source,
        record: SourceRecord,
        tender: Tender | None,
    ) -> ApplyOutcome:
        """Aplica una entrada completa que ja ha passat la guarda d'ordre.

        Nomes s'hi arriba amb U > U* (o U* nul) i una entrada parsejada sencera:
        per aixo aqui, i nomes aqui, es poden deduir absencies de documents i
        lots.
        """
        buyer = await self._upsert_buyer(data, source)
        now = datetime.now(UTC)
        created = tender is None

        if tender is None:
            tender = Tender(
                source_id=source.id,
                external_id=data.external_id,
                first_seen_at=now,
                revision_count=0,
            )
            self.session.add(tender)

        tender.expediente_number = data.expediente_number
        tender.title = data.title
        tender.description = data.description
        tender.buyer_id = buyer.id if buyer else None
        tender.procedure_type = data.procedure_type
        tender.contract_type = data.contract_type
        # Nomes una dada explicita de la font canvia l'estat (ANUL -> cancelled).
        # Una lapida no hi arriba mai: es presencia a la font (`source_removed_at`).
        tender.status = data.status
        tender.source_status_code = data.source_status_code
        tender.estimated_value = data.estimated_value
        tender.base_budget = data.base_budget
        tender.base_budget_without_tax = data.base_budget_without_tax
        tender.currency = data.currency
        tender.publication_date = data.publication_date
        tender.deadline_at = data.deadline_at
        tender.place_of_execution = data.place_of_execution
        tender.nuts_code = data.nuts_code
        tender.country_code = data.country_code
        tender.source_url = data.source_url
        tender.last_seen_at = now
        # «Publicada N cops»: publicacions aplicades. Els SourceRecord superats,
        # en conflicte, fallits o de lapides no son publicacions vigents.
        tender.revision_count = (tender.revision_count or 0) + 1
        tender.source_metadata = dict(data.source_metadata) or None
        incoming = _utc(data.source_updated_at)
        tender.source_updated_at = incoming

        # Republicacio posterior a la lapida: l'entrada torna a ser a la font.
        restored = False
        if (
            tender.source_removed_at is not None
            and incoming is not None
            and incoming > _utc(tender.source_removed_at)
        ):
            tender.source_removed_at = None
            restored = True

        await self.session.flush()
        tender.latest_source_record_id = record.id

        tombstone_adopted = False
        if created:
            tombstone_adopted = await self._adopt_orphan_tombstones(tender, source, incoming)

        await self._sync_cpvs(tender, data)
        await self._sync_lots(tender, data, now)
        await self._sync_documents(tender, data, record, now)
        # Cerca normalitzada (spec 3B-1 §7.2): l'organisme d'aquesta entrada i
        # els lots que publica (els absents queden retirats i ja no compten), en
        # l'ordre determinista que fa servir tambe el backfill.
        tender.search_text = build_search_text(
            tender, buyer.name if buyer else None, ordered_lot_titles(data.lots)
        )
        await self.session.flush()

        # La historia s'escriu aqui, amb l'estat ja sincronitzat. Si el
        # contingut normalitzat no ha canviat, aixo no crea res: reingerir el
        # mateix feed deixa el sistema exactament igual.
        outcome = await record_revision(self.session, tender, source_record_id=record.id)

        # `last_changed_at` nomes es mou si ha canviat alguna cosa de veritat.
        # Abans s'escrivia a cada reingestió, i com que el pre-fit el compara
        # per saber si s'ha de recalcular, TOT semblava obsolet cada vegada que
        # passava el feed. Un avis que salta sempre no informa de res.
        if not outcome.unchanged:
            tender.last_changed_at = now
        return ApplyOutcome(
            tender=tender,
            created=created,
            restored=restored,
            tombstone_adopted=tombstone_adopted,
        )

    async def _upsert_buyer(self, data: NormalizedTender, source: Source) -> Buyer | None:
        if data.buyer is None:
            return None
        info = data.buyer
        buyer = (
            await self.session.execute(
                sa.select(Buyer).where(
                    Buyer.source_id == source.id,
                    Buyer.external_identifier == info.external_identifier,
                )
            )
        ).scalar_one_or_none()
        renamed = False
        if buyer is None:
            buyer = Buyer(source_id=source.id, external_identifier=info.external_identifier)
            self.session.add(buyer)
        else:
            renamed = info.name is not None and info.name != buyer.name
        for attribute in (
            "name", "tax_identifier", "registry_identifier", "buyer_type", "activity",
            "website", "buyer_profile_url", "address", "city", "postal_code",
            "country_code", "nuts_code", "hierarchy",
        ):
            value = getattr(info, attribute)
            if value is not None:
                setattr(buyer, attribute, value)
        await self.session.flush()
        if renamed:
            # El nom de l'organisme es a `search_text` de TOTES les seves
            # licitacions, no nomes de la d'aquesta entrada: es propaga ara, a
            # la mateixa transaccio i en bloc (spec 3B-1 §7.2).
            await refresh_search_text_for_buyer(self.session, buyer.id)
        return buyer

    async def _ensure_cpv_codes(self, codes: list[str]) -> set[str]:
        """Reutilitza el cataleg CPV existent; no en crea un de paral.lel.

        PLACSP nomes dona el codi, no la descripcio, aixi que els codis nous
        entren amb descripcio provisional fins que s'importi el cataleg oficial.
        """
        if not codes:
            return set()
        unique = list(dict.fromkeys(codes))
        present = set(
            (
                await self.session.execute(sa.select(CPVCode.code).where(CPVCode.code.in_(unique)))
            ).scalars()
        )
        for code in unique:
            if code not in present:
                self.session.add(
                    CPVCode(code=code, description=f"CPV {code}", parent_code=code[:2])
                )
                present.add(code)
        await self.session.flush()
        return present

    async def _sync_cpvs(self, tender: Tender, data: NormalizedTender) -> None:
        wanted = {cpv.code: cpv.is_main for cpv in data.cpvs}
        await self._ensure_cpv_codes(list(wanted))
        existing = {
            row.cpv_code: row
            for row in (
                await self.session.execute(
                    sa.select(TenderCPV).where(TenderCPV.tender_id == tender.id)
                )
            ).scalars()
        }
        await self._diff_cpv_rows(
            existing,
            wanted,
            lambda code, is_main: TenderCPV(tender_id=tender.id, cpv_code=code, is_main=is_main),
        )

    async def _diff_cpv_rows(self, existing: dict, wanted: dict[str, bool], build) -> None:
        """CPV per diferencia: nomes s'afegeix o es treu el que canvia."""
        for code, row in existing.items():
            if code not in wanted:
                await self.session.delete(row)
            elif bool(row.is_main) != wanted[code]:
                row.is_main = wanted[code]
        for code, is_main in wanted.items():
            if code not in existing:
                self.session.add(build(code, is_main))

    async def _sync_lots(
        self, tender: Tender, data: NormalizedTender, now: datetime
    ) -> None:
        existing = {
            lot.external_lot_id: lot
            for lot in (
                await self.session.execute(
                    sa.select(TenderLot).where(TenderLot.tender_id == tender.id)
                )
            ).scalars()
        }
        wanted_ids = {lot.external_lot_id for lot in data.lots}

        # Un lot que ja no es al feed es MARCA, no s'esborra. Esborrar-lo se'n
        # duria per davant qualsevol candidatura que hi apunti —`tender_lot_id`
        # te ON DELETE CASCADE— i amb ella les seves decisions i els seus
        # paquets. La feina d'una empresa no pot desapareixer perque l'organ
        # hagi reorganitzat els lots: ha de quedar-hi, i avisar.
        for external_id, lot in existing.items():
            if external_id not in wanted_ids and lot.removed_at is None:
                lot.removed_at = now

        for info in data.lots:
            lot = existing.get(info.external_lot_id)
            if lot is None:
                lot = TenderLot(tender_id=tender.id, external_lot_id=info.external_lot_id)
                self.session.add(lot)
            # Un lot que torna deixa d'estar marcat: la font mana.
            lot.removed_at = None
            lot.lot_number = info.lot_number
            lot.title = info.title
            lot.estimated_value = info.estimated_value
            lot.base_budget = info.base_budget
            lot.base_budget_without_tax = info.base_budget_without_tax
            lot.currency = info.currency
            lot.place_of_execution = info.place_of_execution
            lot.nuts_code = info.nuts_code
            await self.session.flush()

            codes = {cpv.code: cpv.is_main for cpv in info.cpvs}
            await self._ensure_cpv_codes(list(codes))
            lot_id = lot.id
            existing_cpvs = {
                row.cpv_code: row
                for row in (
                    await self.session.execute(
                        sa.select(TenderLotCPV).where(TenderLotCPV.tender_lot_id == lot_id)
                    )
                ).scalars()
            }
            await self._diff_cpv_rows(
                existing_cpvs,
                codes,
                lambda code, is_main, lot_id=lot_id: TenderLotCPV(
                    tender_lot_id=lot_id, cpv_code=code, is_main=is_main
                ),
            )

    async def _sync_documents(
        self, tender: Tender, data: NormalizedTender, record: SourceRecord, now: datetime
    ) -> None:
        existing = {
            document.url: document
            for document in (
                await self.session.execute(
                    sa.select(TenderDocument).where(TenderDocument.tender_id == tender.id)
                )
            ).scalars()
        }
        wanted_urls = {info.url for info in data.documents}

        # La publicacio completa porta la llista sencera de documents: el que no
        # hi es s'ha retirat. Es MARCA, mai s'esborra: les versions baixades i el
        # que s'hi ha analitzat es conserven.
        for url, document in existing.items():
            if url not in wanted_urls and document.removed_at is None:
                document.removed_at = now

        for info in data.documents:
            document = existing.get(info.url)
            if document is None:
                document = TenderDocument(tender_id=tender.id, url=info.url)
                self.session.add(document)
            # Un document que torna deixa d'estar marcat: la font mana.
            document.removed_at = None
            document.name = info.name
            document.document_type = info.document_type
            document.source_document_type_code = info.source_document_type_code
            document.published_at = info.published_at
            document.source_record_id = record.id
