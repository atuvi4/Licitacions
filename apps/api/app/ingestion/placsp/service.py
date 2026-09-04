"""Orquestracio de la ingesta de PLACSP.

Uneix client + parser + normalitzador + persistencia, i es qui garanteix les
tres regles d'idempotencia:

  A. mateix `external_id` i mateix hash  -> UNCHANGED (no es crea res)
  B. mateix `external_id`, hash diferent -> nova revisio (SourceRecord nou,
     mateix Tender, dades actualitzades)
  C. `external_id` nou                   -> Tender nou

Les tres estan protegides tambe per constraints de base de dades: no depenen
nomes d'aquest codi.
"""

from __future__ import annotations

import logging
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.placsp import parser as placsp_parser
from app.ingestion.placsp.client import PLACSP_LICITACIONES_FEED, FeedFetchError, PlacspClient
from app.ingestion.placsp.normalizer import NormalizedTender, normalize
from app.models.cpv import CPVCode
from app.models.enums import IngestionStatus, ProcessingStatus, SourceType, TenderStatus
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

logger = logging.getLogger("app.ingestion.placsp")

SOURCE_CODE = "PLACSP"
SOURCE_NAME = "Plataforma de Contratacion del Sector Publico"
RAW_PREFIX = "sources/placsp"


@dataclass(slots=True)
class RunCounters:
    fetched: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    failed: int = 0
    tenders_created: int = 0
    tenders_updated: int = 0
    errors: list[str] = field(default_factory=list)

    def as_log(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "new": self.new,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "failed": self.failed,
            "tenders_created": self.tenders_created,
            "tenders_updated": self.tenders_updated,
        }


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
    async def get_or_create_source(self) -> Source:
        source = (
            await self.session.execute(sa.select(Source).where(Source.code == SOURCE_CODE))
        ).scalar_one_or_none()
        if source is None:
            source = Source(
                code=SOURCE_CODE,
                name=SOURCE_NAME,
                source_type=SourceType.SYNDICATION_ATOM,
                base_url=PLACSP_LICITACIONES_FEED,
                enabled=True,
            )
            self.session.add(source)
            await self.session.flush()
        return source

    # ------------------------------------------------------------------ run
    async def run(
        self,
        *,
        limit: int | None = None,
        max_files: int = 1,
        fixture: Path | None = None,
        feed_url: str | None = None,
        dry_run: bool = False,
        resume: bool = False,
    ) -> IngestionRun:
        """Executa una ingesta i retorna el registre d'auditoria."""
        started = time.monotonic()
        source = await self.get_or_create_source()

        run = IngestionRun(
            source_id=source.id,
            status=IngestionStatus.RUNNING,
            checkpoint_before=source.checkpoint,
            parameters=(
                f"limit={limit} max_files={max_files} dry_run={dry_run} "
                f"fixture={fixture.name if fixture else None} resume={resume}"
            )[:500],
        )
        self.session.add(run)
        await self.session.flush()

        counters = RunCounters()
        checkpoint_after = source.checkpoint
        seen_urls: set[str] = set()

        try:
            if fixture is not None:
                documents = [(str(fixture), fixture.read_bytes())]
            else:
                documents = None  # es descarreguen dins del bucle

            if documents is not None:
                for url, content in documents:
                    checkpoint_after = await self._process_document(
                        content, url, source, run, counters, limit, dry_run
                    ) or checkpoint_after
            else:
                start_url = feed_url or (
                    source.checkpoint if (resume and source.checkpoint) else PLACSP_LICITACIONES_FEED
                )
                async with PlacspClient() as client:
                    url: str | None = start_url
                    for file_index in range(max_files):
                        if url is None or url in seen_urls:
                            break
                        seen_urls.add(url)
                        if file_index > 0:
                            await client.pause()
                        content = await client.fetch(url)
                        next_url = await self._process_document(
                            content, url, source, run, counters, limit, dry_run
                        )
                        checkpoint_after = url
                        if limit is not None and counters.fetched >= limit:
                            break
                        url = next_url

            if not dry_run:
                source.checkpoint = checkpoint_after
                source.checkpoint_updated_at = datetime.now(UTC)

            run.status = IngestionStatus.PARTIAL if counters.failed else IngestionStatus.SUCCESS

        except FeedFetchError as exc:
            run.status = IngestionStatus.FAILED
            counters.errors.append(str(exc))
            logger.error("placsp.run.failed", extra={"run_id": str(run.id), "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - qualsevol error deixa rastre auditable
            run.status = IngestionStatus.FAILED
            counters.errors.append(f"{type(exc).__name__}: {exc}")
            logger.exception("placsp.run.crashed", extra={"run_id": str(run.id)})

        run.finished_at = datetime.now(UTC)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.records_fetched = counters.fetched
        run.records_new = counters.new
        run.records_changed = counters.changed
        run.records_unchanged = counters.unchanged
        run.records_failed = counters.failed
        run.tenders_created = counters.tenders_created
        run.tenders_updated = counters.tenders_updated
        run.checkpoint_after = checkpoint_after
        run.error_summary = "\n".join(counters.errors[:50]) or None

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
    ) -> str | None:
        feed = placsp_parser.parse_feed(content)
        logger.info(
            "placsp.feed.parsed",
            extra={"url": url, "entries": len(feed.entries), "deleted": len(feed.deleted_entries)},
        )

        for element in feed.entries:
            if limit is not None and counters.fetched >= limit:
                break
            counters.fetched += 1
            try:
                await self._process_entry(element, url, source, run, counters, dry_run)
            except Exception as exc:  # noqa: BLE001 - una entrada dolenta no atura el feed
                counters.failed += 1
                message = f"{type(exc).__name__}: {exc}"
                counters.errors.append(message[:500])
                logger.warning("placsp.entry.failed", extra={"url": url, "error": message})
                await self.session.rollback()

        return feed.next_url

    async def _process_entry(
        self,
        element: ET.Element,
        feed_url: str,
        source: Source,
        run: IngestionRun,
        counters: RunCounters,
        dry_run: bool,
    ) -> None:
        raw_xml = ET.tostring(element, encoding="utf-8")
        raw_hash = compute_sha256(raw_xml)
        external_id = element.findtext(f"{placsp_parser.ATOM}id")
        if not external_id:
            raise placsp_parser.ParseError("Entrada sense <id>.")
        external_id = external_id.strip()

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
        if existing is not None:
            counters.unchanged += 1
            if not dry_run and existing.tender_id:
                await self.session.execute(
                    sa.update(Tender)
                    .where(Tender.id == existing.tender_id)
                    .values(last_seen_at=datetime.now(UTC))
                )
                await self.session.commit()
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
        is_first_revision = previous_revisions == 0

        if dry_run:
            placsp_parser.parse_entry(element)  # valida que es parsejable
            counters.new += 1 if is_first_revision else 0
            counters.changed += 0 if is_first_revision else 1
            return

        record_id = uuid.uuid4()
        storage_key = raw_storage_key(record_id)
        parsed = None
        parse_error: str | None = None
        try:
            parsed = placsp_parser.parse_entry(element)
        except Exception as exc:  # noqa: BLE001
            parse_error = f"{type(exc).__name__}: {exc}"

        # El raw es guarda SEMPRE, fins i tot si el parseig ha fallat: es
        # justament el cas en que voldrem tornar-hi amb un parser millor.
        await self.storage.upload(storage_key, raw_xml, "application/xml")

        record = SourceRecord(
            id=record_id,
            source_id=source.id,
            external_id=external_id,
            external_version_id=(parsed.updated_at.isoformat() if parsed and parsed.updated_at else None),
            revision_number=previous_revisions + 1,
            source_url=feed_url,
            fetched_at=datetime.now(UTC),
            source_updated_at=parsed.updated_at if parsed else None,
            source_published_at=(
                datetime.combine(parsed.publication_date, datetime.min.time(), tzinfo=UTC)
                if parsed and parsed.publication_date
                else None
            ),
            raw_hash=raw_hash,
            raw_storage_key=storage_key,
            raw_size=len(raw_xml),
            mime_type="application/xml",
            parser_version=placsp_parser.PARSER_VERSION,
            processing_status=ProcessingStatus.FAILED if parse_error else ProcessingStatus.PENDING,
            error_message=parse_error,
            ingestion_run_id=run.id,
        )
        self.session.add(record)
        await self.session.flush()

        if parse_error or parsed is None:
            counters.failed += 1
            counters.errors.append(f"{external_id}: {parse_error}")
            await self.session.commit()
            return

        tender, created = await self._apply(normalize(parsed), source, record)
        record.processing_status = ProcessingStatus.PROCESSED
        record.tender_id = tender.id

        if created:
            counters.tenders_created += 1
        else:
            counters.tenders_updated += 1
        if is_first_revision:
            counters.new += 1
        else:
            counters.changed += 1

        await self.session.commit()

    # ------------------------------------------------------- model canonic
    async def _apply(
        self, data: NormalizedTender, source: Source, record: SourceRecord
    ) -> tuple[Tender, bool]:
        buyer = await self._upsert_buyer(data, source)
        now = datetime.now(UTC)

        tender = (
            await self.session.execute(
                sa.select(Tender).where(
                    Tender.source_id == source.id, Tender.external_id == data.external_id
                )
            )
        ).scalar_one_or_none()
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
        # Una lapida del feed marca l'expedient com anul.lat.
        tender.status = TenderStatus.CANCELLED if data.is_deleted else data.status
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
        tender.last_changed_at = now
        tender.revision_count = record.revision_number
        await self.session.flush()
        tender.latest_source_record_id = record.id

        await self._sync_cpvs(tender, data)
        await self._sync_lots(tender, data)
        await self._sync_documents(tender, data, record)
        await self.session.flush()
        return tender, created

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
        if buyer is None:
            buyer = Buyer(source_id=source.id, external_identifier=info.external_identifier)
            self.session.add(buyer)
        for attribute in (
            "name", "tax_identifier", "registry_identifier", "buyer_type", "activity",
            "website", "buyer_profile_url", "address", "city", "postal_code",
            "country_code", "nuts_code", "hierarchy",
        ):
            value = getattr(info, attribute)
            if value is not None:
                setattr(buyer, attribute, value)
        await self.session.flush()
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
        await self.session.execute(sa.delete(TenderCPV).where(TenderCPV.tender_id == tender.id))
        for code, is_main in wanted.items():
            self.session.add(TenderCPV(tender_id=tender.id, cpv_code=code, is_main=is_main))

    async def _sync_lots(self, tender: Tender, data: NormalizedTender) -> None:
        existing = {
            lot.external_lot_id: lot
            for lot in (
                await self.session.execute(
                    sa.select(TenderLot).where(TenderLot.tender_id == tender.id)
                )
            ).scalars()
        }
        wanted_ids = {lot.external_lot_id for lot in data.lots}

        for external_id, lot in existing.items():
            if external_id not in wanted_ids:
                await self.session.delete(lot)

        for info in data.lots:
            lot = existing.get(info.external_lot_id)
            if lot is None:
                lot = TenderLot(tender_id=tender.id, external_lot_id=info.external_lot_id)
                self.session.add(lot)
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
            await self.session.execute(
                sa.delete(TenderLotCPV).where(TenderLotCPV.tender_lot_id == lot.id)
            )
            for code, is_main in codes.items():
                self.session.add(
                    TenderLotCPV(tender_lot_id=lot.id, cpv_code=code, is_main=is_main)
                )

    async def _sync_documents(
        self, tender: Tender, data: NormalizedTender, record: SourceRecord
    ) -> None:
        existing = {
            document.url: document
            for document in (
                await self.session.execute(
                    sa.select(TenderDocument).where(TenderDocument.tender_id == tender.id)
                )
            ).scalars()
        }
        for info in data.documents:
            document = existing.get(info.url)
            if document is None:
                document = TenderDocument(tender_id=tender.id, url=info.url)
                self.session.add(document)
            document.name = info.name
            document.document_type = info.document_type
            document.source_document_type_code = info.source_document_type_code
            document.published_at = info.published_at
            document.source_record_id = record.id
