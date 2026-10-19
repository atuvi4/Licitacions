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
   escriu res** (les files llegides podrien barrejar dues versions del dataset).
5. **Guardes** (`guards.evaluate`) per licitacio.
6. **Escriptura** en una sola transaccio (en `--dry-run` es fa igual i es desfa):
   per a cada (licitacio, lot o expedient, `pbl_with_tax`, font, `source_row_id`):
   * cap observacio vigent -> se n'afegeix una;
   * la vigent te el mateix estat, import, fase i `validation` -> no s'escriu res;
   * si no, es TANCA la vigent (`valid_to = ara`) i se n'afegeix una de nova.
   Una observacio vigent la fila de la qual ja no torna en aquest run **no es tanca**:
   es llista a l'informe (`disappeared`). Nomes es tanca si una altra fila passa a ser
   l'`accepted` del mateix objectiu (`displaced`), perque nomes n'hi pot haver una.
7. **Informe** a `--out`: `report.json`, `observations.csv`, `fetch_log.json` i el
   manifest de les respostes desades.

Evidencia de cada observacio: `evidence_url` es la consulta curta i exacta de la seva
fila (`id_intern = ...`) o de les files de lot de l'expedient (`starts_with(id_intern,
'<uuid>_')`), que es pot tornar a demanar; la peticio per lots d'on surt queda
referenciada per `evidence_sha256` (el seu cos original) i `raw_storage_key` (la resposta
redactada), i la seva URL completa, un sol cop, a `fetch_log.json` i `raw_manifest.json`.

Nomes s'escriu `amount_kind = pbl_with_tax`. El PBL sense IVA i el VEC publicats van a
`validation` i a `provenance` com a evidencia de contrast. Mai no es toca cap altra taula.
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


class DatasetChangedDuringRun(Exception):
    """`rowsUpdatedAt` ha canviat entre l'inici i el final: no s'escriu res."""


@dataclass
class RunOptions:
    out_dir: Path
    dry_run: bool = False
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
                for row in rows:
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
    def _provenance(draft: g.Draft, metadata: dict, pages: list[_Page]) -> dict[str, Any]:
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
        report: dict[str, Any] = {
            "source_code": SOURCE_CODE,
            "dataset": DATASET_ID,
            "started_at": started.isoformat(),
            "dry_run": options.dry_run,
            "options": {
                "limit": options.limit,
                "tender": options.tender,
                "batch_size": options.batch_size,
                "page_limit": options.page_limit,
            },
        }
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

            evaluations = [g.evaluate(snap, by_uuid.get(snap.pscp_id, [])) for snap in snapshots]
            report["evaluation"] = _summarize(evaluations)
            changed = meta_start.get("rows_updated_at_epoch") != meta_end.get(
                "rows_updated_at_epoch"
            )
            if changed:
                report["writes"] = {"skipped": "dataset_changed_during_run"}
                exit_code = EXIT_DATASET_CHANGED
            else:
                report["writes"] = await self._write(
                    evaluations, snapshots, row_page, meta_start, dry_run=options.dry_run
                )
            _write_observations_csv(out_dir / "observations.csv", evaluations)
        except Exception as exc:
            logger.exception("pscp_open_data.run.failed")
            report["error"] = f"{type(exc).__name__}: {exc}"[:500]
            exit_code = EXIT_FAILED
        finally:
            report["requests"] = self._client.requests_made
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

    async def _write(
        self,
        evaluations: list[g.TenderEvaluation],
        snapshots: list[g.TenderSnapshot],
        row_page: dict[int, _Page],
        metadata: dict,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        tender_ids = {snap.id for snap in snapshots}
        drafts = [draft for ev in evaluations for draft in ev.drafts]
        dataset_updated = (
            datetime.fromisoformat(metadata["rows_updated_at"])
            if metadata.get("rows_updated_at")
            else None
        )
        async with self._sessions() as session:
            current_rows = (
                (
                    await session.execute(
                        sa.select(TenderAmountObservation).where(
                            TenderAmountObservation.source_code == SOURCE_CODE,
                            TenderAmountObservation.amount_kind == TenderAmountKind.PBL_WITH_TAX,
                            TenderAmountObservation.valid_to.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            current = {
                (obs.tender_id, obs.tender_lot_id, obs.source_row_id): obs
                for obs in current_rows
                if obs.tender_id in tender_ids
            }
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
            # Una sola `accepted` vigent per objectiu: si una altra fila hi passa, la
            # que hi havia (d'una fila que ja no torna o que ha canviat) es tanca.
            for draft in to_insert:
                if draft.status != AmountObservationStatus.ACCEPTED:
                    continue
                holder = accepted_at.get((draft.tender_id, draft.tender_lot_id))
                if holder is not None and holder.id not in to_close:
                    to_close[holder.id] = (holder, "displaced")
            produced = {draft.key for draft in drafts}
            disappeared = sorted(
                (
                    {
                        "tender_id": str(obs.tender_id),
                        "tender_lot_id": None
                        if obs.tender_lot_id is None
                        else str(obs.tender_lot_id),
                        "source_row_id": obs.source_row_id,
                        "status": obs.validation_status.value,
                    }
                    for key, obs in current.items()
                    if key not in produced and obs.id not in to_close
                ),
                key=lambda item: (item["tender_id"], item["source_row_id"]),
            )

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
                        amount_kind=TenderAmountKind.PBL_WITH_TAX,
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
                        provenance=self._provenance(draft, metadata, pages),
                        valid_from=now,
                    )
                )
            await session.flush()
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
        reasons = Counter(reason for _, reason in to_close.values())
        return {
            "dry_run": dry_run,
            "committed": not dry_run,
            "inserted": len(to_insert),
            "closed": len(to_close),
            "closed_changed": reasons.get("changed", 0),
            "closed_displaced": reasons.get("displaced", 0),
            "unchanged": unchanged,
            "inserted_by_status": dict(Counter(d.status.value for d in to_insert)),
            "disappeared_not_closed": disappeared,
        }


def _evidence_url(draft: g.Draft) -> str:
    """La consulta curta de la fila (o files) de l'observacio, tal com es publiquen."""
    ids = {str(row.get("id_intern")) for row in draft.rows}
    if len(ids) == 1:
        return row_evidence_url(ids.pop())
    return row_evidence_url(prefix=draft.source_row_id.removesuffix("*"))


def _same(existing: TenderAmountObservation, draft: g.Draft) -> bool:
    return (
        existing.validation_status == draft.status
        and existing.amount == draft.amount
        and existing.source_phase == draft.source_phase
        and existing.validation == draft.validation
    )


def _summarize(evaluations: list[g.TenderEvaluation]) -> dict[str, Any]:
    by_level_status: Counter = Counter()
    sums: dict[str, Decimal] = {"expedient": Decimal("0"), "lot": Decimal("0")}
    no_value: Counter = Counter()
    conflicts = []
    superseded = []
    unrepresentable = []
    shapes = Counter(ev.shape for ev in evaluations)
    for ev in evaluations:
        for draft in ev.drafts:
            by_level_status[f"{draft.level}:{draft.status.value}"] += 1
            if draft.status == AmountObservationStatus.ACCEPTED:
                sums[draft.level] += draft.amount
            if draft.status == AmountObservationStatus.NO_VALUE:
                no_value[f"{draft.level}:{draft.validation.get('no_value_reason')}"] += 1
            if draft.status == AmountObservationStatus.CONFLICT:
                conflicts.append(
                    {
                        "tender_id": str(ev.tender_id),
                        "pscp_id": ev.pscp_id,
                        "level": draft.level,
                        "source_row_id": draft.source_row_id,
                        "failed": draft.validation.get("failed")
                        or sorted(
                            set(draft.validation.get("expedient_failed", []))
                            | {f"lot.{n}" for n in draft.validation.get("lot_failed", [])}
                        ),
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
            for draft in ev.drafts:
                validation = draft.validation
                failed = validation.get("failed")
                if failed is None:
                    failed = sorted(
                        set(validation.get("expedient_failed", []))
                        | {f"lot.{n}" for n in validation.get("lot_failed", [])}
                    )
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
                    }
                )
