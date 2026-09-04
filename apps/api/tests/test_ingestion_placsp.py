"""Tests de la ingesta de PLACSP: parser, idempotencia, revisions i errors.

Els fixtures son entrades reals del feed oficial (vegeu
`tests/fixtures/placsp/README.md`): si el parser es trenca amb un canvi de
format, aquests tests ho han de veure.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.ingestion.placsp import parser
from app.ingestion.placsp.normalizer import normalize
from app.ingestion.placsp.service import PlacspIngestionService
from app.models.enums import ContractType, IngestionStatus, ProcedureType, TenderStatus
from app.models.sources import SourceRecord
from app.models.tenders import Buyer, Tender, TenderCPV, TenderDocument, TenderLot

FIXTURES = Path(__file__).parent / "fixtures" / "placsp"


def load(name: str) -> parser.ParsedFeed:
    return parser.parse_feed((FIXTURES / name).read_bytes())


def entry_by_expedient(feed: parser.ParsedFeed, expedient: str) -> parser.ParsedEntry:
    for element in feed.entries:
        parsed = parser.parse_entry(element)
        if parsed.contract_folder_id == expedient:
            return parsed
    raise AssertionError(f"No hi ha cap entrada amb expedient {expedient}")


# ---------------------------------------------------------------------------
# Parser i normalitzador
# ---------------------------------------------------------------------------


class TestParser:
    def test_llegeix_lestructura_del_feed(self):
        feed = load("feed_sample.atom")
        assert len(feed.entries) == 3
        assert feed.self_url is not None

    def test_expedient_sense_lots(self):
        parsed = entry_by_expedient(load("feed_sample.atom"), "AMARCO/2026/MUSEO/607BAS")
        tender = normalize(parsed)

        assert tender.external_id.endswith("/20232857")
        assert tender.title.startswith("Energ")
        assert tender.lots == []
        assert tender.status is TenderStatus.RESOLVED
        assert tender.source_status_code == "RES"
        assert tender.contract_type is ContractType.GOODS
        assert tender.procedure_type is ProcedureType.OPEN
        assert tender.estimated_value == Decimal("115148.31")
        assert tender.base_budget == Decimal("139329.46")
        assert tender.currency == "EUR"
        assert tender.nuts_code == "ES523"
        assert [cpv.code for cpv in tender.cpvs] == ["09310000"]
        assert tender.cpvs[0].is_main is True

    def test_expedient_amb_lots(self):
        parsed = entry_by_expedient(load("feed_sample.atom"), "26-002735")
        tender = normalize(parsed)

        assert len(tender.lots) == 3
        assert [lot.lot_number for lot in tender.lots] == [1, 2, 3]
        assert all(lot.base_budget is not None for lot in tender.lots)
        assert all(lot.cpvs for lot in tender.lots)

    def test_multiples_cpv_amb_un_de_principal(self):
        parsed = entry_by_expedient(load("feed_sample.atom"), "2026-044-O")
        tender = normalize(parsed)

        assert len(tender.cpvs) == 22
        assert sum(1 for cpv in tender.cpvs if cpv.is_main) == 1
        assert tender.cpvs[0].is_main is True
        assert len(tender.lots) == 7

    def test_organisme_identificat_per_id_de_plataforma(self):
        parsed = entry_by_expedient(load("feed_sample.atom"), "26-002735")
        tender = normalize(parsed)

        assert tender.buyer is not None
        # El NIF no serveix com a identitat: un mateix NIF te diversos organs.
        assert tender.buyer.external_identifier != tender.buyer.tax_identifier
        assert tender.buyer.name
        assert tender.buyer.hierarchy

    def test_termini_amb_zona_horaria(self):
        parsed = entry_by_expedient(load("feed_sample.atom"), "26-002735")
        tender = normalize(parsed)

        assert tender.deadline_at is not None
        assert tender.deadline_at.tzinfo is not None
        assert tender.deadline_at.utcoffset() is not None

    def test_data_de_publicacio_anterior_al_termini(self):
        """La publicacio ha de sortir de l'anunci de convocatoria, no de l'adjudicacio."""
        for expedient in ("26-002735", "2026-044-O"):
            tender = normalize(entry_by_expedient(load("feed_sample.atom"), expedient))
            assert tender.publication_date is not None
            assert tender.deadline_at is not None
            assert tender.publication_date <= tender.deadline_at.date()

    def test_documents_amb_metadata_pero_sense_descarrega(self):
        tender = normalize(entry_by_expedient(load("feed_sample.atom"), "26-002735"))
        assert tender.documents
        for document in tender.documents:
            assert document.url.startswith("http")
            assert document.name  # mai buit, encara que la font no doni FileName

    def test_camps_opcionals_absents(self):
        tender = normalize(entry_by_expedient(load("feed_minimal.atom"), "MINIM/2026"))

        assert tender.estimated_value is None
        assert tender.deadline_at is None
        assert tender.nuts_code is None
        assert tender.cpvs == []
        assert tender.buyer is None
        assert tender.contract_type is ContractType.UNKNOWN

    def test_dades_brutes_no_inventen_valors(self):
        tender = normalize(entry_by_expedient(load("feed_malformed.atom"), "TRENCAT/2026"))

        # Import no numeric, data inexistent i codis desconeguts -> null/unknown.
        assert tender.base_budget is None
        assert tender.deadline_at is None
        assert tender.status is TenderStatus.UNKNOWN
        assert tender.procedure_type is ProcedureType.UNKNOWN
        assert tender.contract_type is ContractType.UNKNOWN

    def test_entrada_sense_id_es_rebutjada(self):
        feed = load("feed_malformed.atom")
        errors = 0
        for element in feed.entries:
            try:
                parser.parse_entry(element)
            except parser.ParseError:
                errors += 1
        assert errors == 1

    def test_lapida_datom(self):
        feed = load("feed_with_tombstone.atom")
        assert len(feed.deleted_entries) == 1
        deleted = parser.parse_deleted_entry(feed.deleted_entries[0])
        assert deleted is not None
        assert deleted.deleted is True

    def test_el_parser_no_resol_entitats_externes(self):
        """Proteccio contra XXE: una entitat externa no ha de llegir cap fitxer."""
        payload = b"""<?xml version="1.0"?>
        <!DOCTYPE feed [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
        <feed xmlns="http://www.w3.org/2005/Atom"><id>&xxe;</id></feed>"""
        with pytest.raises(parser.ParseError):
            parser.parse_feed(payload)


# ---------------------------------------------------------------------------
# Ingesta completa
# ---------------------------------------------------------------------------


@pytest.fixture
async def service(db, storage) -> PlacspIngestionService:
    return PlacspIngestionService(db, storage)


async def count(session, model) -> int:
    return (await session.execute(sa.select(sa.func.count()).select_from(model))).scalar_one()


class TestIngestion:
    async def test_primera_ingesta_crea_el_model_canonic(self, service, db, storage):
        run = await service.run(fixture=FIXTURES / "feed_sample.atom")

        assert run.status is IngestionStatus.SUCCESS
        assert run.records_fetched == 3
        assert run.records_new == 3
        assert run.records_unchanged == 0
        assert run.tenders_created == 3

        assert await count(db, Tender) == 3
        assert await count(db, Buyer) == 3
        assert await count(db, TenderLot) == 10  # 0 + 3 + 7
        assert await count(db, SourceRecord) == 3
        assert await count(db, TenderCPV) > 0
        assert await count(db, TenderDocument) > 0

    async def test_el_xml_original_es_guarda_a_lstorage(self, service, db, storage):
        await service.run(fixture=FIXTURES / "feed_sample.atom")

        records = (await db.execute(sa.select(SourceRecord))).scalars().all()
        for record in records:
            # Espai de noms propi: mai sota companies/.
            assert record.raw_storage_key.startswith("sources/placsp/")
            assert await storage.exists(record.raw_storage_key)
            raw = await storage.download(record.raw_storage_key)
            assert len(raw) == record.raw_size
            from app.services.files import compute_sha256

            assert compute_sha256(raw) == record.raw_hash

    async def test_la_mateixa_ingesta_dues_vegades_no_duplica_res(self, service, db):
        await service.run(fixture=FIXTURES / "feed_sample.atom")
        tenders_after_first = await count(db, Tender)
        lots_after_first = await count(db, TenderLot)

        second = await service.run(fixture=FIXTURES / "feed_sample.atom")

        assert second.records_unchanged == 3
        assert second.records_new == 0
        assert second.records_changed == 0
        assert second.tenders_created == 0
        assert await count(db, Tender) == tenders_after_first
        assert await count(db, TenderLot) == lots_after_first
        assert await count(db, SourceRecord) == 3  # cap revisio nova

    async def test_un_canvi_crea_revisio_pero_no_una_licitacio_nova(self, service, db):
        await service.run(fixture=FIXTURES / "feed_sample.atom")
        second = await service.run(fixture=FIXTURES / "feed_sample_modified.atom")

        assert second.records_changed == 1
        assert second.records_unchanged == 2
        assert second.tenders_created == 0
        assert second.tenders_updated == 1

        assert await count(db, Tender) == 3
        assert await count(db, SourceRecord) == 4  # 3 + 1 revisio

        tender = (
            await db.execute(
                sa.select(Tender).where(Tender.expediente_number == "AMARCO/2026/MUSEO/607BAS")
            )
        ).scalar_one()
        assert tender.revision_count == 2
        # La dada actual reflecteix la versio nova...
        assert tender.deadline_at is not None
        assert tender.deadline_at.year == 2027
        # ...i l'historial conserva totes dues versions.
        revisions = (
            await db.execute(
                sa.select(SourceRecord)
                .where(SourceRecord.tender_id == tender.id)
                .order_by(SourceRecord.revision_number)
            )
        ).scalars().all()
        assert [record.revision_number for record in revisions] == [1, 2]
        assert revisions[0].raw_hash != revisions[1].raw_hash

    async def test_una_entrada_dolenta_no_atura_les_bones(self, service, db):
        run = await service.run(fixture=FIXTURES / "feed_malformed.atom")

        assert run.status is IngestionStatus.PARTIAL
        assert run.records_failed == 1
        assert run.records_fetched == 3
        # Les dues entrades processables s'han desat igualment.
        assert await count(db, Tender) == 2
        assert "Entrada sense" in (run.error_summary or "")

    async def test_dry_run_no_escriu_res(self, service, db):
        run = await service.run(fixture=FIXTURES / "feed_sample.atom", dry_run=True)

        assert run.records_fetched == 3
        assert await count(db, Tender) == 0
        assert await count(db, SourceRecord) == 0

    async def test_el_limit_es_respecta(self, service, db):
        run = await service.run(fixture=FIXTURES / "feed_sample.atom", limit=1)

        assert run.records_fetched == 1
        assert await count(db, Tender) == 1

    async def test_les_licitacions_no_pertanyen_a_cap_empresa(self, service, db):
        """Regla d'arquitectura: les dades publiques no es dupliquen per tenant."""
        await service.run(fixture=FIXTURES / "feed_sample.atom")

        for model in (Tender, TenderLot, Buyer, SourceRecord, TenderDocument):
            assert not hasattr(model, "company_id"), f"{model.__name__} no pot tenir company_id"

    async def test_lexecucio_queda_auditada(self, service, db):
        run = await service.run(fixture=FIXTURES / "feed_sample.atom")

        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.duration_ms is not None and run.duration_ms >= 0
        assert run.parameters and "fixture=" in run.parameters
