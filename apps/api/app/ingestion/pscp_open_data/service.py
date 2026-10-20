"""Servei de l'adaptador de la PSCP: univers, consulta, guardes, escriptura i informe.

Flux d'un run:

1. **Univers**: les licitacions amb `source_metadata->>'pscp_id_intern'` (opcionalment
   una sola, `--tender`, o les N primeres per `pscp_id_intern`, `--limit`), amb els seus
   lots vigents (`removed_at IS NULL`). Nomes lectura.
2. **Metadades** del dataset (`rowsUpdatedAt`) a l'inici.
3. **Consulta** per lots de 100 UUID: files amb `id_intern = <uuid>` o que comencen per
   `<uuid>_`, `$select` minim, `$order=id_intern,:id`, paginant. Cada resposta es desa
   redactada a `--out` (amb el SHA-256 de l'original).
4. **Metadades** al final: si `rowsUpdatedAt` ha canviat durant el run, el run **no
   escriu res** (les files llegides podrien barrejar dues versions del dataset); si
   falta a l'inici o al final, tampoc (la llicencia exigeix informar de la data).
5. **Guardes** (`guards.evaluate`) per licitacio.
6. **Escriptura** en una sola transaccio (en `--dry-run` es fa igual i es desfa):
   per a cada (licitacio, lot o expedient, `pbl_with_tax`, font, `source_row_id`):
   * cap observacio vigent -> se n'afegeix una;
   * la vigent te el mateix estat, import, fase i `validation` -> no s'escriu res;
   * si no, es TANCA la vigent (`valid_to = ara`) i se n'afegeix una de nova.
   Una lectura nova mai no deixa vigent una d'antiga que ja no avala:
   * la mateixa fila llegida ara sota una altra clau (lot retirat, lot que ja no
     s'identifica) tanca la vigent de la clau antiga (`reassigned`);
   * una `accepted` vigent d'una fila que no torna, en un objectiu que si te lectura
     nova, es tanca (`displaced` si la nova es `accepted`, `superseded_by_reading` si
     no).
   Una observacio d'un objectiu del qual la font ja no publica res **no es tanca**: es
   llista a l'informe (`disappeared`), segons la regla del propietari.
   Un `unchanged` no reescriu res: la data de la darrera reverificacio d'una
   observacio es la del darrer informe de run (`report.json`), no `retrieved_at`.
7. **Informe** a `--out`: `report.json`, `observations.csv`, `fetch_log.json` i el
   manifest de les respostes desades.

Evidencia de cada observacio: `evidence_url` es la consulta curta i exacta de la seva
fila (`id_intern = ...`) o de les files de lot de l'expedient (`starts_with(id_intern,
'<uuid>_')`), que es pot tornar a demanar; la peticio per lots d'on surt queda
referenciada per `evidence_sha256` (el seu cos original) i `raw_storage_key` (la resposta
redactada), i la seva URL completa, un sol cop, a `fetch_log.json` i `raw_manifest.json`.

S'escriu `amount_kind = pbl_with_tax` (expedient i lots) i, des de F-06e, tambe el PBL
sense IVA (`pbl_without_tax`) i el VEC (`estimated_value`) de cada fila de lot. Cada tipus
es una serie independent: la clau d'una observacio inclou `amount_kind`, i les regles de
tancament de dalt s'apliquen tipus per tipus (una observacio nova d'un tipus no tanca ni
reassigna mai cap observacio d'un altre). Res de nou a nivell d'expedient. Mai no es
toca cap altra taula.

Rejoc (`--replay <dir>`, F-06e): el client de rejoc (`replay.ReplayClient`) serveix les
respostes desades d'un run anterior, sense xarxa. Les observacions noves apunten a la
captura original (clau d'emmagatzematge, SHA-256 del cos original, `retrieved_at`,
`rowsUpdatedAt`) amb la marca `provenance.replay` (directori i SHA-256 del manifest), i
l'informe diu `replay: true` i 0 peticions de xarxa. No es torna a desar cap resposta.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.pscp_open_data import (
    DATASET_ID,
    LICENCE_NAME,
    LICENCE_URL,
    PORTAL,
    SOURCE_CODE,
    attribution,
)
from app.ingestion.pscp_open_data import guards as g
from app.ingestion.pscp_open_data import parser as p
from app.ingestion.pscp_open_data.client import (
    SodaClient,
    metadata_url,
    redact,
    row_evidence_url,
    rows_url,
)
from app.models.enums import AmountObservationStatus, TenderAmountKind
from app.models.tender_amounts import TenderAmountObservation
from app.models.tenders import Tender, TenderLot

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
PAGE_LIMIT = 5000
STORAGE_PREFIX = "pscp_open_data"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DATASET_CHANGED = 3
EXIT_DATASET_DATE_MISSING = 4
#: El run ha escrit, pero alguna licitacio no s'ha pogut avaluar (desada com a
#: `conflict` amb l'error; vegeu `evaluation_errors` a l'informe).
EXIT_EVALUATION_ERRORS = 5
#: Massa licitacions no avaluables: fallada sistematica, no s'escriu res.
EXIT_EVALUATION_ABORTED = 6

#: Llindar de fallada sistematica (I-3). Unes poques licitacions amb una forma de la
#: font inesperada es desen com a `conflict` i el run continua; mes de 20, o mes de l'1 %
#: de l'univers quan ja n'hi ha mes de 5, vol dir que el que falla es el codi (o la font
#: ha canviat de forma) i el run s'atura sense escriure res. Amb l'univers del pilot
#: (7.859) l'1 % son 78: mana el limit absolut de 20.
MAX_EVALUATION_ERRORS = 20
MAX_EVALUATION_ERROR_RATIO = 0.01
MIN_EVALUATION_ERRORS_FOR_RATIO = 5


def too_many_evaluation_errors(errors: int, tenders: int) -> bool:
    if errors > MAX_EVALUATION_ERRORS:
        return True
    return (
        errors > MIN_EVALUATION_ERRORS_FOR_RATIO and errors > MAX_EVALUATION_ERROR_RATIO * tenders
    )


class DatasetChangedDuringRun(Exception):
    """`rowsUpdatedAt` ha canviat entre l'inici i el final: no s'escriu res."""


@dataclass
class RunOptions:
    out_dir: Path
    #: Segur per defecte: nomes `dry_run=False` (el CLI, `--apply`) escriu.
    dry_run: bool = True
    limit: int | None = None
    tender: str | None = None
    batch_size: int = BATCH_SIZE
    page_limit: int = PAGE_LIMIT


@dataclass
class _Page:
    batch: int
    offset: int
    url: str
    sha256: str
    retrieved_at: datetime
    storage_key: str
    rows: int


def _json_safe(value: Any) -> Any:
    """El mateix document que tornara la base (JSON pla)."""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class PscpOpenDataService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: SodaClient,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self._sessions = session_factory
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ #
    # Univers                                                              #
    # ------------------------------------------------------------------ #

    async def load_universe(
        self, session: AsyncSession, *, tender: str | None, limit: int | None
    ) -> tuple[list[g.TenderSnapshot], dict[str, Any]]:
        pscp = Tender.source_metadata["pscp_id_intern"].as_string()
        organ = Tender.source_metadata["placsp_organ_id"].as_string()
        rows = (
            await session.execute(
                sa.select(
                    Tender.id,
                    pscp,
                    organ,
                    Tender.base_budget_without_tax,
                    Tender.estimated_value,
                ).where(pscp.is_not(None))
            )
        ).all()
        lots = (
            await session.execute(
                sa.select(
                    TenderLot.id,
                    TenderLot.tender_id,
                    TenderLot.lot_number,
                    TenderLot.base_budget_without_tax,
                    TenderLot.estimated_value,
                )
                .join(Tender, Tender.id == TenderLot.tender_id)
                .where(pscp.is_not(None), TenderLot.removed_at.is_(None))
            )
        ).all()
        lots_by_tender: dict[uuid.UUID, list[g.LotSnapshot]] = {}
        for lot_id, tender_id, number, net, vec in lots:
            lots_by_tender.setdefault(tender_id, []).append(
                g.LotSnapshot(lot_id, number, _decimal(net), _decimal(vec))
            )
        normalized = [
            (tid, str(code).strip().lower(), org, net, vec) for tid, code, org, net, vec in rows
        ]
        counts = Counter(code for _, code, *_ in normalized)
        invalid = sorted(str(tid) for tid, code, *_ in normalized if not p.UUID_RE.match(code))
        snapshots = [
            g.TenderSnapshot(
                id=tid,
                pscp_id=code,
                organ_id=None if org is None else str(org).strip(),
                net=_decimal(net),
                vec=_decimal(vec),
                lots=tuple(
                    sorted(
                        lots_by_tender.get(tid, []),
                        key=lambda lot: (lot.lot_number is None, lot.lot_number or 0),
                    )
                ),
                identity_unique=counts[code] == 1,
            )
            for tid, code, org, net, vec in normalized
            if p.UUID_RE.match(code)
        ]
        snapshots.sort(key=lambda snap: (snap.pscp_id, str(snap.id)))
        if tender:
            wanted = tender.strip().lower()
            snapshots = [s for s in snapshots if str(s.id) == wanted or s.pscp_id == wanted]
        if limit is not None:
            snapshots = snapshots[:limit]
        info = {
            "tenders_with_pscp_id": len(rows),
            "invalid_pscp_id": invalid,
            "shared_pscp_id": sorted(code for code, n in counts.items() if n > 1),
            "selected": len(snapshots),
            "lots_selected": sum(len(s.lots) for s in snapshots),
        }
        return snapshots, info

    # ------------------------------------------------------------------ #
    # Consulta                                                             #
    # ------------------------------------------------------------------ #

    def _store(self, out_dir: Path, stamp: str, name: str, body: bytes) -> tuple[str, dict]:
        """Desa la resposta redactada; retorna la clau i l'entrada del manifest."""
        key = f"{STORAGE_PREFIX}/{stamp}/{name}"
        target = out_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        redacted_fields = 0
        try:
            document = json.loads(body)
        except ValueError:
            document = None
        stored = body
        if document is not None:
            clean, redacted_fields = redact(document)
            if redacted_fields:
                stored = json.dumps(clean, ensure_ascii=False, indent=1).encode("utf-8")
        target.write_bytes(stored)
        return key, {
            "key": key,
            "sha256_original": hashlib.sha256(body).hexdigest(),
            "bytes_original": len(body),
            "redacted_fields": redacted_fields,
            "sha256_stored": hashlib.sha256(stored).hexdigest(),
        }

    async def _metadata(self, out_dir: Path, stamp: str, which: str, manifest: list) -> dict:
        fetched = await self._client.get(metadata_url(), purpose=f"metadata {which}")
        if fetched.origins is not None:
            # Rejoc: el fitxer original (ja verificat), no una copia nova.
            [origin] = fetched.origins
            key = origin["key"]
            manifest.append({**origin, "replay": True})
        else:
            key, entry = self._store(out_dir, stamp, f"metadata_{which}.json", fetched.body)
            manifest.append(
                {**entry, "url": fetched.url, "retrieved_at": fetched.retrieved_at.isoformat()}
            )
        document = json.loads(fetched.body)
        return {
            "rows_updated_at": p.rows_updated_at_iso(document.get("rowsUpdatedAt")),
            "rows_updated_at_epoch": document.get("rowsUpdatedAt"),
            "license": (document.get("license") or {}).get("name"),
            "license_id": document.get("licenseId"),
            "attribution": document.get("attribution"),
            "attribution_link": document.get("attributionLink"),
            "name": document.get("name"),
            "storage_key": key,
        }

    async def _fetch_rows(
        self,
        out_dir: Path,
        stamp: str,
        snapshots: list[g.TenderSnapshot],
        options: RunOptions,
        manifest: list,
    ) -> tuple[dict[str, list[dict]], dict[int, _Page], int]:
        """Les files de cada `pscp_id`, i de quina pagina surt cada fila."""
        uuids = sorted({s.pscp_id for s in snapshots})
        by_uuid: dict[str, list[dict]] = {uid: [] for uid in uuids}
        row_page: dict[int, _Page] = {}
        #: Rejoc: una sola `_Page` per fitxer original (clau d'emmagatzematge).
        original_pages: dict[str, _Page] = {}
        unexpected = 0
        for batch_index in range(0, len(uuids), options.batch_size):
            batch = uuids[batch_index : batch_index + options.batch_size]
            wanted = set(batch)
            number = batch_index // options.batch_size
            offset = 0
            while True:
                url = rows_url(
                    batch, select=p.SELECT_FIELDS, offset=offset, limit=options.page_limit
                )
                fetched = await self._client.get(
                    url, purpose=f"batch {number} ({len(batch)} uuids) offset {offset}"
                )
                rows = json.loads(fetched.body)
                if not isinstance(rows, list):
                    raise ValueError("La resposta de files no es una llista.")
                if fetched.origins is not None:
                    # Rejoc: cada fila apunta al fitxer original d'on surt (clau, SHA-256
                    # del cos original, `retrieved_at`); no es desa cap copia.
                    if len(fetched.origins) != len(rows):
                        raise ValueError("El rejoc no dona l'origen de cada fila.")
                    pages = []
                    for origin in fetched.origins:
                        page = original_pages.get(origin["key"])
                        if page is None:
                            page = _Page(
                                origin.get("batch", number),
                                origin.get("offset", offset),
                                origin["url"],
                                origin["sha256_original"],
                                datetime.fromisoformat(origin["retrieved_at"]),
                                origin["key"],
                                origin.get("rows", 0),
                            )
                            original_pages[origin["key"]] = page
                            manifest.append({**origin, "replay": True})
                        pages.append(page)
                else:
                    key, entry = self._store(
                        out_dir, stamp, f"batch_{number:03d}_off{offset}.json", fetched.body
                    )
                    page = _Page(
                        number,
                        offset,
                        fetched.url,
                        fetched.sha256,
                        fetched.retrieved_at,
                        key,
                        len(rows),
                    )
                    manifest.append(
                        {
                            **entry,
                            "url": fetched.url,
                            "retrieved_at": fetched.retrieved_at.isoformat(),
                            "rows": len(rows),
                        }
                    )
                    pages = [page] * len(rows)
                for row, page in zip(rows, pages, strict=True):
                    identifier = str(row.get("id_intern") or "")
                    uid = identifier[:36].lower()
                    if uid in wanted and (len(identifier) == 36 or identifier[36:37] == "_"):
                        # La fila es desa tal com es publica; el grup es l'UUID.
                        by_uuid[uid].append(row)
                        row_page[id(row)] = page
                    else:
                        unexpected += 1
                if len(rows) < options.page_limit:
                    break
                offset += options.page_limit
        return by_uuid, row_page, unexpected

    # ------------------------------------------------------------------ #
    # Procedencia                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _provenance(
        draft: g.Draft, metadata: dict, pages: list[_Page], replay: dict | None = None
    ) -> dict[str, Any]:
        url_json: dict[str, set[str]] = {}
        rows = []
        for row in draft.rows:
            for name in p.URL_JSON_FIELDS:
                link = p.link_url(row.get(name))
                if link:
                    url_json.setdefault(name, set()).add(link)
            rows.append(
                {
                    "id_intern": row.get("id_intern"),
                    ":id": row.get(":id"),
                    ":updated_at": row.get(":updated_at"),
                    "codi_organ": row.get("codi_organ"),
                    "fase_publicacio": row.get("fase_publicacio"),
                    "numero_lot": row.get("numero_lot"),
                    "enllac_publicacio": p.link_url(row.get("enllac_publicacio")),
                    "publication_id": p.publication_id(row),
                }
            )
        provenance: dict[str, Any] = {
            "dataset": DATASET_ID,
            "portal": PORTAL,
            "dataset_name": metadata.get("name"),
            "rows_updated_at": metadata.get("rows_updated_at"),
            "field": draft.field,
            "published_value": draft.published_text,
            "cross_check": draft.cross_check,
            "rows": rows,
            "url_json": {name: sorted(links) for name, links in sorted(url_json.items())},
            "licence": {
                "name": LICENCE_NAME,
                "url": LICENCE_URL,
                "attribution": attribution(metadata.get("rows_updated_at")),
            },
        }
        if len(pages) > 1:
            provenance["pages"] = [
                {"sha256": page.sha256, "storage_key": page.storage_key} for page in pages
            ]
        if replay is not None:
            # Rejoc: no es una lectura nova de la font, sino la captura original.
            # Identificador logic (cap cami de la maquina): els dos darrers components del
            # directori, el SHA-256 del manifest i la data del dataset de la captura;
            # `raw_storage_key` es relatiu a aquell directori.
            provenance["replay"] = {
                "capture": replay["capture"],
                "raw_manifest_sha256": replay["raw_manifest_sha256"],
                "rows_updated_at": metadata.get("rows_updated_at"),
            }
        return provenance

    # ------------------------------------------------------------------ #
    # Run                                                                  #
    # ------------------------------------------------------------------ #

    async def run(self, options: RunOptions) -> tuple[int, dict[str, Any]]:
        started = self._clock()
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        out_dir = options.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest: list[dict] = []
        replay = self._replay_info()
        report: dict[str, Any] = {
            "source_code": SOURCE_CODE,
            "dataset": DATASET_ID,
            "started_at": started.isoformat(),
            "dry_run": options.dry_run,
            "replay": replay is not None,
            "options": {
                "limit": options.limit,
                "tender": options.tender,
                "batch_size": options.batch_size,
                "page_limit": options.page_limit,
            },
        }
        if replay is not None:
            report["replay_source"] = dict(replay)
        exit_code = EXIT_OK
        try:
            async with self._sessions() as session:
                snapshots, universe = await self.load_universe(
                    session, tender=options.tender, limit=options.limit
                )
            report["universe"] = universe
            meta_start = await self._metadata(out_dir, stamp, "start", manifest)
            by_uuid, row_page, unexpected = await self._fetch_rows(
                out_dir, stamp, snapshots, options, manifest
            )
            meta_end = await self._metadata(out_dir, stamp, "end", manifest)
            report["dataset_metadata"] = {"start": meta_start, "end": meta_end}
            report["attribution"] = attribution(meta_start.get("rows_updated_at"))
            report["unexpected_rows"] = unexpected

            evaluations = []
            evaluation_errors = []
            for snap in snapshots:
                rows = by_uuid.get(snap.pscp_id, [])
                # (I-3) Una excepcio en avaluar una licitacio mai no s'empassa: es registra
                # (amb la traca), es llista a l'informe, la licitacio es desa com a
                # `conflict` amb l'error (i les seves `accepted` vigents es tanquen), i
                # el run acaba amb un codi propi. Massa errors: no s'escriu res.
                try:
                    evaluations.append(g.evaluate(snap, rows))
                except Exception as exc:  # noqa: BLE001 - es desa, es llista i canvia el codi
                    logger.exception(
                        "pscp_open_data.evaluate.failed", extra={"tender_id": str(snap.id)}
                    )
                    error = {"class": type(exc).__name__, "message": str(exc)[:200]}
                    evaluation_errors.append({"tender_id": str(snap.id), **error})
                    if rows:
                        evaluations.append(g.error_evaluation(snap, rows, error=error))
            report["evaluation"] = _summarize(evaluations)
            report["evaluation"]["evaluation_errors"] = evaluation_errors
            changed = meta_start.get("rows_updated_at_epoch") != meta_end.get(
                "rows_updated_at_epoch"
            )
            missing_date = not (
                meta_start.get("rows_updated_at") and meta_end.get("rows_updated_at")
            )
            if missing_date:
                # (m-2) La llicencia exigeix informar de la darrera actualitzacio: sense la
                # data, no s'escriu res.
                report["writes"] = {"skipped": "dataset_rows_updated_at_missing"}
                exit_code = EXIT_DATASET_DATE_MISSING
            elif changed:
                report["writes"] = {"skipped": "dataset_changed_during_run"}
                exit_code = EXIT_DATASET_CHANGED
            elif too_many_evaluation_errors(len(evaluation_errors), len(snapshots)):
                report["writes"] = {"skipped": "too_many_evaluation_errors"}
                exit_code = EXIT_EVALUATION_ABORTED
            elif replay is not None and (
                newer := await self._newer_than_capture(snapshots, meta_start)
            ):
                # (I-1) Una captura mes antiga que alguna observacio vigent de l'univers
                # faria tornar enrere dades mes noves: no s'escriu res (tampoc en dry-run).
                report["writes"] = {"skipped": "replay_capture_older_than_current"}
                report["replay_freshness"] = newer
                report["error"] = (
                    f"ReplayError: {newer['observations']} observacions vigents de la PSCP "
                    f"tenen una data del dataset ({newer['newest']}) posterior a la de la "
                    f"captura ({newer['capture_rows_updated_at']})."
                )
                exit_code = EXIT_FAILED
            else:
                report["writes"] = await self._write(
                    evaluations,
                    snapshots,
                    row_page,
                    meta_start,
                    dry_run=options.dry_run,
                    replay=replay,
                )
                if evaluation_errors:
                    exit_code = EXIT_EVALUATION_ERRORS
            _write_observations_csv(out_dir / "observations.csv", evaluations)
        except Exception as exc:
            logger.exception("pscp_open_data.run.failed")
            report["error"] = f"{type(exc).__name__}: {exc}"[:500]
            exit_code = EXIT_FAILED
        finally:
            # Peticions de XARXA (en rejoc, sempre 0).
            report["requests"] = self._client.requests_made
            if replay is not None:
                report["network_requests"] = self._client.requests_made
                report["replayed_requests"] = getattr(self._client, "replayed", None)
            report["finished_at"] = self._clock().isoformat()
            report["exit_code"] = exit_code
            (out_dir / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
            )
            (out_dir / "fetch_log.json").write_text(
                json.dumps(self._client.log, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            (out_dir / "raw_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        return exit_code, report

    async def _newer_than_capture(
        self, snapshots: list[g.TenderSnapshot], metadata: dict
    ) -> dict[str, Any] | None:
        """(I-1) Les observacions PSCP vigents de l'univers amb una data del dataset
        POSTERIOR a la de la captura (`rowsUpdatedAt`). Igual es admes."""
        capture = datetime.fromisoformat(metadata["rows_updated_at"])
        tender_ids = {snap.id for snap in snapshots}
        obs = TenderAmountObservation
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    sa.select(obs.tender_id, obs.source_dataset_updated_at).where(
                        obs.source_code == SOURCE_CODE,
                        obs.valid_to.is_(None),
                        obs.source_dataset_updated_at.is_not(None),
                    )
                )
            ).all()
        newer = []
        for tender_id, moment in rows:
            if tender_id not in tender_ids:
                continue
            if moment.tzinfo is None:  # SQLite torna les dates sense zona (son UTC).
                moment = moment.replace(tzinfo=UTC)
            if moment > capture:
                newer.append(moment)
        if not newer:
            return None
        return {
            "capture_rows_updated_at": capture.isoformat(),
            "newest": max(newer).isoformat(),
            "observations": len(newer),
        }

    def _replay_info(self) -> dict[str, Any] | None:
        """El directori i el SHA-256 del manifest si el client es de rejoc (F-06e)."""
        info = getattr(self._client, "replay_info", None)
        return dict(info) if isinstance(info, dict) else None

    async def _write(
        self,
        evaluations: list[g.TenderEvaluation],
        snapshots: list[g.TenderSnapshot],
        row_page: dict[int, _Page],
        metadata: dict,
        *,
        dry_run: bool,
        replay: dict | None = None,
    ) -> dict[str, Any]:
        tender_ids = {snap.id for snap in snapshots}
        drafts = [draft for ev in evaluations for draft in ev.all_drafts]
        dataset_updated = (
            datetime.fromisoformat(metadata["rows_updated_at"])
            if metadata.get("rows_updated_at")
            else None
        )
        # (I-3, n-1) Una licitacio que la font publica pero que no s'ha pogut llegir del
        # tot (error d'avaluacio, files no desables).
        unverified = {ev.tender_id for ev in evaluations if ev.unverified}
        async with self._sessions() as session:
            current_rows = (
                (
                    await session.execute(
                        sa.select(TenderAmountObservation).where(
                            TenderAmountObservation.source_code == SOURCE_CODE,
                            TenderAmountObservation.amount_kind.in_(WRITTEN_KINDS),
                            TenderAmountObservation.valid_to.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            # (F-06e) Cada tipus d'import es una serie independent, amb les mateixes
            # regles: una observacio d'un tipus mai no tanca ni reassigna la d'un altre.
            # Per al PBL amb IVA, el pla es exactament el d'abans de F-06e.
            plans: dict[TenderAmountKind, _KindPlan] = {}
            for kind in WRITTEN_KINDS:
                plans[kind] = _plan_kind(
                    [draft for draft in drafts if draft.amount_kind == kind],
                    {
                        (obs.tender_id, obs.tender_lot_id, obs.source_row_id): obs
                        for obs in current_rows
                        if obs.amount_kind == kind and obs.tender_id in tender_ids
                    },
                    unverified,
                )
            to_close: dict[uuid.UUID, tuple[TenderAmountObservation, str]] = {}
            to_insert: list[g.Draft] = []
            for plan in plans.values():
                to_close.update(plan.to_close)
                to_insert.extend(plan.to_insert)

            now = self._clock()
            closed_ids = list(to_close)
            for start in range(0, len(closed_ids), 500):
                await session.execute(
                    sa.update(TenderAmountObservation)
                    .where(TenderAmountObservation.id.in_(closed_ids[start : start + 500]))
                    .values(valid_to=now)
                )
            await session.flush()
            for draft in to_insert:
                pages: list[_Page] = []
                for row in draft.rows:
                    page = row_page[id(row)]
                    if page not in pages:
                        pages.append(page)
                first = pages[0]
                session.add(
                    TenderAmountObservation(
                        tender_id=draft.tender_id,
                        tender_lot_id=draft.tender_lot_id,
                        amount_kind=draft.amount_kind,
                        amount=draft.amount,
                        currency="EUR",
                        source_code=SOURCE_CODE,
                        source_entity_key=draft.source_entity_key,
                        source_row_id=draft.source_row_id,
                        source_phase=draft.source_phase,
                        source_dataset_updated_at=dataset_updated,
                        retrieved_at=first.retrieved_at,
                        evidence_url=_evidence_url(draft),
                        evidence_sha256=first.sha256,
                        raw_storage_key=first.storage_key,
                        validation_status=draft.status,
                        validation=draft.validation,
                        provenance=self._provenance(draft, metadata, pages, replay),
                        valid_from=now,
                    )
                )
            await session.flush()
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
        summary = _plan_summary(to_insert, to_close, sum(plan.unchanged for plan in plans.values()))
        disappeared = sorted(
            (item for plan in plans.values() for item in plan.disappeared),
            key=lambda item: (item["amount_kind"], item["tender_id"], item["source_row_id"]),
        )
        return {
            "dry_run": dry_run,
            "committed": not dry_run,
            **summary,
            "disappeared_not_closed": disappeared,
            "by_kind": {
                kind.value: {
                    **_plan_summary(plan.to_insert, plan.to_close, plan.unchanged),
                    "disappeared_not_closed": len(plan.disappeared),
                }
                for kind, plan in plans.items()
            },
        }


#: Els tipus d'import que escriu l'adaptador (F-06e: tambe el PBL sense IVA i el VEC de
#: lot). Cadascun es planifica a part (`_plan_kind`).
WRITTEN_KINDS: tuple[TenderAmountKind, ...] = (
    TenderAmountKind.PBL_WITH_TAX,
    TenderAmountKind.PBL_WITHOUT_TAX,
    TenderAmountKind.ESTIMATED_VALUE,
)


@dataclass
class _KindPlan:
    to_close: dict[uuid.UUID, tuple[TenderAmountObservation, str]]
    to_insert: list[g.Draft]
    unchanged: int
    disappeared: list[dict[str, Any]]


def _plan_kind(
    drafts: list[g.Draft],
    current: dict[tuple, TenderAmountObservation],
    unverified: set[uuid.UUID],
) -> _KindPlan:
    """Que s'insereix i que es tanca d'UN tipus d'import (les regles de sempre).

    `current`: les vigents d'aquest tipus de les licitacions de l'univers, per (licitacio,
    lot, `source_row_id`); `drafts`: les lectures d'ara d'aquest tipus.
    """
    accepted_at = {
        (obs.tender_id, obs.tender_lot_id): obs
        for obs in current.values()
        if obs.validation_status == AmountObservationStatus.ACCEPTED
    }
    to_close: dict[uuid.UUID, tuple[TenderAmountObservation, str]] = {}
    to_insert: list[g.Draft] = []
    unchanged = 0
    for draft in drafts:
        existing = current.get(draft.key)
        validation = _json_safe(draft.validation)
        draft.validation = validation
        if existing is not None and _same(existing, draft):
            unchanged += 1
            continue
        if existing is not None:
            to_close[existing.id] = (existing, "changed")
        to_insert.append(draft)
    produced = {draft.key for draft in drafts}
    # (I-1) La mateixa fila de la font llegida ara sota una altra clau (p. ex. el lot
    # s'ha retirat i la fila ja no te lot nostre): la vigent de la clau antiga es tanca.
    # Una fila que encara hi es mai no deixa una lectura antiga vigent.
    produced_rows = {(draft.tender_id, draft.source_row_id) for draft in drafts}
    for key, obs in current.items():
        if (
            key not in produced
            and (obs.tender_id, obs.source_row_id) in produced_rows
            and obs.id not in to_close
        ):
            to_close[obs.id] = (obs, "reassigned")
    # (I-1) Una `accepted` vigent la fila de la qual no torna en aquest run, en un
    # objectiu que si te lectura nova: si la nova es `accepted`, la desplaca (n'hi ha
    # d'haver una sola); si no, la lectura d'ara no l'avala i es tanca igualment. Un
    # objectiu sense cap lectura nova (la font ja no en publica res) es llista com a
    # `disappeared` i no es tanca (regla del propietari).
    # (I-3, n-1) Una licitacio que la font publica pero que no s'ha pogut llegir del tot
    # (error d'avaluacio, files no desables): cap `accepted` vigent que aquesta lectura
    # no torni a avalar no queda vigent (`unverifiable`).
    for key, obs in current.items():
        if (
            obs.tender_id in unverified
            and obs.validation_status == AmountObservationStatus.ACCEPTED
            and key not in produced
            and obs.id not in to_close
        ):
            to_close[obs.id] = (obs, "unverifiable")
    readings: dict[tuple, list[g.Draft]] = {}
    for draft in drafts:
        readings.setdefault((draft.tender_id, draft.tender_lot_id), []).append(draft)
    for target, holder in accepted_at.items():
        holder_key = (holder.tender_id, holder.tender_lot_id, holder.source_row_id)
        if holder.id in to_close or holder_key in produced or target not in readings:
            continue
        accepted_now = any(
            draft.status == AmountObservationStatus.ACCEPTED for draft in readings[target]
        )
        to_close[holder.id] = (
            holder,
            "displaced" if accepted_now else "superseded_by_reading",
        )
    disappeared = [
        {
            "tender_id": str(obs.tender_id),
            "tender_lot_id": None if obs.tender_lot_id is None else str(obs.tender_lot_id),
            "source_row_id": obs.source_row_id,
            "status": obs.validation_status.value,
            "amount_kind": obs.amount_kind.value,
        }
        for key, obs in current.items()
        if key not in produced and obs.id not in to_close
    ]
    return _KindPlan(to_close, to_insert, unchanged, disappeared)


def _plan_summary(
    to_insert: list[g.Draft],
    to_close: dict[uuid.UUID, tuple[TenderAmountObservation, str]],
    unchanged: int,
) -> dict[str, Any]:
    reasons = Counter(reason for _, reason in to_close.values())
    return {
        "inserted": len(to_insert),
        "closed": len(to_close),
        "closed_changed": reasons.get("changed", 0),
        "closed_displaced": reasons.get("displaced", 0),
        "closed_reassigned": reasons.get("reassigned", 0),
        "closed_superseded_by_reading": reasons.get("superseded_by_reading", 0),
        "closed_unverifiable": reasons.get("unverifiable", 0),
        "unchanged": unchanged,
        "inserted_by_status": dict(Counter(d.status.value for d in to_insert)),
    }


def _evidence_url(draft: g.Draft) -> str:
    """La consulta curta de la fila (o files) de l'observacio, tal com es publiquen.

    Una fila desable: `id_intern = ...`. Diverses files, o una que no es pot desar com a
    clau (lectura en error): el prefix de l'expedient (`<uuid>` o `<uuid>_`).
    """
    ids = {str(row.get("id_intern")) for row in draft.rows}
    if len(ids) == 1:
        identifier = ids.pop()
        if p.KEYABLE_ID.fullmatch(identifier):
            return row_evidence_url(identifier)
    return row_evidence_url(prefix=draft.source_row_id.removesuffix("*"))


def _same(existing: TenderAmountObservation, draft: g.Draft) -> bool:
    return (
        existing.validation_status == draft.status
        and existing.amount == draft.amount
        and existing.source_phase == draft.source_phase
        and existing.validation == draft.validation
    )


def _label(draft: g.Draft) -> str:
    """`expedient` / `lot` per al PBL amb IVA (com abans de F-06e); `lot.<tipus>` per als
    imports de lot nous (`lot.pbl_without_tax`, `lot.estimated_value`)."""
    if draft.amount_kind == TenderAmountKind.PBL_WITH_TAX:
        return draft.level
    return f"{draft.level}.{draft.amount_kind.value}"


def _summarize(evaluations: list[g.TenderEvaluation]) -> dict[str, Any]:
    by_level_status: Counter = Counter()
    sums: dict[str, Decimal] = {"expedient": Decimal("0"), "lot": Decimal("0")}
    no_value: Counter = Counter()
    conflicts = []
    superseded = []
    unrepresentable = []
    shapes = Counter(ev.shape for ev in evaluations)
    for ev in evaluations:
        for draft in ev.all_drafts:
            label = _label(draft)
            by_level_status[f"{label}:{draft.status.value}"] += 1
            if draft.status == AmountObservationStatus.ACCEPTED:
                sums[label] = sums.get(label, Decimal("0")) + draft.amount
            if draft.status == AmountObservationStatus.NO_VALUE:
                no_value[f"{label}:{draft.validation.get('no_value_reason')}"] += 1
            if draft.status == AmountObservationStatus.CONFLICT:
                conflicts.append(
                    {
                        "tender_id": str(ev.tender_id),
                        "pscp_id": ev.pscp_id,
                        "level": draft.level,
                        "amount_kind": draft.amount_kind.value,
                        "source_row_id": draft.source_row_id,
                        **_failed_fields(draft.validation),
                    }
                )
            if draft.validation.get("role") == "superseded_row":
                superseded.append(
                    {
                        "tender_id": str(ev.tender_id),
                        "pscp_id": ev.pscp_id,
                        "status": draft.status.value,
                        "phase": draft.source_phase,
                        "superseded_by": len(draft.validation["supersession"]["superseded_by"]),
                    }
                )
            if draft.validation.get("amount_problem"):
                unrepresentable.append(
                    {
                        "tender_id": str(ev.tender_id),
                        "level": draft.level,
                        "source_row_id": draft.source_row_id,
                        "field": draft.field,
                        "problem": draft.validation["amount_problem"],
                    }
                )
    conflict_tenders = {item["tender_id"] for item in conflicts if item["level"] == "expedient"}
    return {
        "tenders": len(evaluations),
        "shapes": dict(shapes),
        "not_found": sorted(str(ev.tender_id) for ev in evaluations if ev.shape == "not_found"),
        "observations": dict(sorted(by_level_status.items())),
        "accepted_sum": {level: str(total) for level, total in sums.items()},
        "no_value_reasons": dict(sorted(no_value.items())),
        "conflict_tenders": len(conflict_tenders),
        "conflicts": conflicts,
        "superseded_rows": superseded,
        "unrepresentable_amounts": unrepresentable,
        "old_rows": dict(
            sorted(
                Counter(
                    f"{d.validation['role']}:{d.status.value}"
                    for ev in evaluations
                    for d in ev.drafts
                    if d.validation.get("role")
                ).items()
            )
        ),
        "guard_failures": dict(
            sorted(Counter(name for ev in evaluations for name in ev.failed).items())
        ),
        "our_lots_without_row": sum(
            len(ev.notes.get("our_lots_without_row", [])) for ev in evaluations
        ),
    }


def _failed_fields(validation: dict[str, Any]) -> dict[str, list[str]]:
    """Que ha fallat, per a l'informe i el CSV.

    Les observacions de sempre: `failed` (expedient) o la unio d'`expedient_failed` i
    `lot.<lot_failed>` (lot), com abans. Els imports de lot nous (F-06e): `failed` es el
    que les ha bloquejat (`blocking_failed`) i `non_blocking_failed`, la resta.
    """
    if isinstance(validation.get("blocking_failed"), list):
        blocking = list(validation["blocking_failed"])
        everything = {f"expedient.{n}" for n in validation.get("expedient_failed", [])} | {
            f"lot.{n}" for n in validation.get("lot_failed", [])
        }
        return {"failed": blocking, "non_blocking_failed": sorted(everything - set(blocking))}
    failed = validation.get("failed")
    if failed is None:
        failed = sorted(
            set(validation.get("expedient_failed", []))
            | {f"lot.{n}" for n in validation.get("lot_failed", [])}
        )
    return {"failed": failed}


def _write_observations_csv(path: Path, evaluations: list[g.TenderEvaluation]) -> None:
    columns = [
        "level",
        "tender_id",
        "tender_lot_id",
        "pscp_id_intern",
        "source_entity_key",
        "source_row_id",
        "shape",
        "status",
        "amount",
        "published_value",
        "no_value_reason",
        "role",
        "failed",
        "phase",
        "amount_kind",
        "non_blocking_failed",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for ev in evaluations:
            if not ev.drafts:
                writer.writerow(
                    {
                        "level": "expedient",
                        "tender_id": ev.tender_id,
                        "pscp_id_intern": ev.pscp_id,
                        "shape": ev.shape,
                        "status": ev.shape,
                    }
                )
            for draft in ev.all_drafts:
                validation = draft.validation
                fields = _failed_fields(validation)
                failed = fields["failed"]
                writer.writerow(
                    {
                        "level": draft.level,
                        "tender_id": draft.tender_id,
                        "tender_lot_id": draft.tender_lot_id or "",
                        "pscp_id_intern": ev.pscp_id,
                        "source_entity_key": draft.source_entity_key,
                        "source_row_id": draft.source_row_id,
                        "shape": ev.shape,
                        "status": draft.status.value,
                        "amount": "" if draft.amount is None else str(draft.amount),
                        "published_value": draft.published_text or "",
                        "no_value_reason": validation.get("no_value_reason") or "",
                        "role": validation.get("role") or "",
                        "failed": "|".join(failed),
                        "phase": draft.source_phase or "",
                        "amount_kind": draft.amount_kind.value,
                        "non_blocking_failed": "|".join(fields.get("non_blocking_failed", [])),
                    }
                )
