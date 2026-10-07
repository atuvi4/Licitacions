"""Garanties no destructives de la ingesta (spec 3B-1, seccions 4.2-4.4).

Ordre (`updated` anterior mai regressa), conflictes observables (mateix
`updated`, hash diferent), presencia a la font (lapides sense tocar l'estat
contractual) i absencies de documents i lots nomes des d'una publicacio
completa posterior. Fixtures: `tests/fixtures/placsp/agregadas/README.md`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from app.ingestion.placsp.service import (
    APPLY,
    OLDER,
    SAME,
    PlacspIngestionService,
    publication_order,
)
from app.models.enums import IngestionStatus, ProcessingStatus
from app.models.sources import IngestionRun, SourceRecord
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Tender, TenderCPV, TenderDocument, TenderLot

AGREGADAS = Path(__file__).parent / "fixtures" / "placsp" / "agregadas"
#: L'entrada base d'`entry_lots.atom` (2 lots, 2 documents); les variants i les
#: lapides hi apunten.
BASE_ID = "https://contrataciondelestado.es/sindicacion/PlataformasAgregadasSinMenores/20529505"
SOURCE_CODE = "placsp_agregadas"


# --------------------------------------------------------------------------- #
# Ajudants                                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_fixture(db, storage):
    async def _run(name: str | Path) -> IngestionRun:
        path = name if isinstance(name, Path) else AGREGADAS / name
        service = PlacspIngestionService(db, storage)
        return await service.run(fixture=path, source_code=SOURCE_CODE)

    return _run


@pytest.fixture
def corrupt_fixture(tmp_path):
    """Copia d'una fixture amb l'entrada malmesa: sense la carrega CODICE.

    L'entrada conserva `id` i `updated` (posterior a l'estat canonic), pero ja no
    porta `ContractFolderStatus`: no es una publicacio completa i no pot
    deduir-ne cap absencia.
    """

    def _corrupt(name: str) -> Path:
        text = (AGREGADAS / name).read_text(encoding="utf-8")
        broken, replaced = re.subn(
            r"<cac-place-ext:ContractFolderStatus\b.*?</cac-place-ext:ContractFolderStatus>",
            "",
            text,
            flags=re.DOTALL,
        )
        assert replaced == 1, "la fixture hauria de tenir una sola carrega CODICE"
        target = tmp_path / f"corrupt_{name}"
        target.write_text(broken, encoding="utf-8")
        return target

    return _corrupt


async def one_tender(db) -> Tender:
    return (
        await db.execute(
            sa.select(Tender)
            .where(Tender.external_id == BASE_ID)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def codes(db, tender_id) -> list[str]:
    return list(
        (
            await db.execute(
                sa.select(TenderChangeEvent.change_code).where(
                    TenderChangeEvent.tender_id == tender_id
                )
            )
        ).scalars()
    )


async def docs(db) -> list[TenderDocument]:
    tender = await one_tender(db)
    return list(
        (
            await db.execute(
                sa.select(TenderDocument)
                .where(TenderDocument.tender_id == tender.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def lots(db) -> list[TenderLot]:
    tender = await one_tender(db)
    return list(
        (
            await db.execute(
                sa.select(TenderLot)
                .where(TenderLot.tender_id == tender.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def all_records(db) -> list[SourceRecord]:
    return list(
        (
            await db.execute(
                sa.select(SourceRecord)
                .where(SourceRecord.external_id == BASE_ID)
                .order_by(SourceRecord.revision_number)
            )
        ).scalars()
    )


async def count(db, model) -> int:
    """Files del tender base (les altres entrades d'`entry_lots` no compten)."""
    tender = await one_tender(db)
    column = {
        SourceRecord: SourceRecord.external_id == BASE_ID,
        TenderRevision: TenderRevision.tender_id == tender.id,
        TenderDocument: TenderDocument.tender_id == tender.id,
        TenderLot: TenderLot.tender_id == tender.id,
    }[model]
    return (
        await db.execute(sa.select(sa.func.count()).select_from(model).where(column))
    ).scalar_one()


# --------------------------------------------------------------------------- #
# Ordre i conflictes (4.2)                                                      #
# --------------------------------------------------------------------------- #


async def test_publicacio_antiga_no_regressa(db, run_fixture):
    await run_fixture("entry_lots.atom")
    deadline_nou = (await one_tender(db)).deadline_at

    run = await run_fixture("entry_lots_older.atom")

    tender = await one_tender(db)
    assert tender.deadline_at == deadline_nou
    assert run.records_superseded == 1
    assert run.tenders_updated == 0
    assert await count(db, SourceRecord) == 2
    assert await count(db, TenderRevision) == 1
    superseded = (await all_records(db))[-1]
    assert superseded.processing_status == ProcessingStatus.PROCESSED
    assert superseded.error_message == "superseded"


async def test_mateix_updated_hash_diferent_es_conflicte_no_destructiu(db, run_fixture):
    await run_fixture("entry_lots.atom")
    titol_original = (await one_tender(db)).title

    run = await run_fixture("entry_lots_same_updated_other_hash.atom")

    tender = await one_tender(db)
    assert tender.title == titol_original
    assert run.records_conflicted == 1
    records = await all_records(db)
    assert len(records) == 2
    assert {r.processing_status for r in records} == {
        ProcessingStatus.PROCESSED,
        ProcessingStatus.CONFLICT,
    }
    conflict = records[-1]
    assert conflict.error_message == (
        f"same updated, raw_hash {records[0].raw_hash} vs {conflict.raw_hash}"
    )
    assert await count(db, TenderRevision) == 1


# --------------------------------------------------------------------------- #
# Presencia a la font (4.3)                                                     #
# --------------------------------------------------------------------------- #


async def test_lapida_marca_presencia_sense_canviar_estat(db, run_fixture):
    await run_fixture("entry_lots.atom")
    status = (await one_tender(db)).status

    run = await run_fixture("tombstone_anulada.atom")

    tender = await one_tender(db)
    assert tender.source_removed_at is not None
    assert tender.status == status
    assert run.tombstones_applied == 1
    tender_codes = await codes(db, tender.id)
    assert "SOURCE_ENTRY_REMOVED" in tender_codes
    assert "TENDER_CANCELLED" not in tender_codes
    tombstone = (await all_records(db))[-1]
    assert tombstone.external_version_id is not None
    assert tombstone.tender_id == tender.id
    assert (await run_fixture("tombstone_anulada.atom")).records_unchanged == 1


async def test_lapida_antiga_no_oculta_publicacio_nova(db, run_fixture):
    await run_fixture("entry_lots.atom")

    run = await run_fixture("tombstone_old.atom")

    assert (await one_tender(db)).source_removed_at is None
    assert run.records_superseded == 1
    assert run.tombstones_applied == 0


async def test_republicacio_restaura(db, run_fixture):
    await run_fixture("entry_lots.atom")
    await run_fixture("tombstone_cerrada.atom")

    run = await run_fixture("entry_lots_republished.atom")

    tender = await one_tender(db)
    assert tender.source_removed_at is None
    assert run.entries_restored == 1
    assert "SOURCE_ENTRY_RESTORED" in await codes(db, tender.id)


async def test_lapida_abans_que_lentrada_no_es_perd(db, run_fixture):
    """Backfill cap enrere: la lapida (mes nova) arriba abans que l'entrada.

    La licitacio neix ja marcada. Com que es la seva primera revisio, no hi ha
    cap fotografia anterior amb que comparar: la marca queda a la fotografia de
    la revisio 1 i no surt cap `SOURCE_ENTRY_REMOVED` (no hi ha cap transicio).
    """
    orphan_run = await run_fixture("tombstone_cerrada.atom")
    assert orphan_run.tombstones_applied == 0  # encara no hi ha licitacio

    created = await run_fixture("entry_lots.atom")

    tender = await one_tender(db)
    assert created.tombstones_applied == 1
    assert tender.source_removed_at is not None
    assert tender.title  # les dades de l'entrada s'han desat igualment
    records = await all_records(db)
    assert all(r.tender_id == tender.id for r in records)
    assert records[0].error_message == "tombstone: aplicada en crear la licitacio"
    revisions = list(
        (
            await db.execute(sa.select(TenderRevision).where(TenderRevision.tender_id == tender.id))
        ).scalars()
    )
    assert len(revisions) == 1
    assert revisions[0].normalized_snapshot.get("source_removed") is True
    assert "SOURCE_ENTRY_REMOVED" not in await codes(db, tender.id)

    run = await run_fixture("entry_lots_republished.atom")
    assert (await one_tender(db)).source_removed_at is None
    assert run.entries_restored == 1


async def test_lapida_orfena_antiga_no_marca_la_licitacio_que_es_crea(db, run_fixture):
    """Una lapida antiga arribada abans que l'entrada no oculta la publicacio nova."""
    await run_fixture("tombstone_old.atom")  # when anterior a l'`updated` de l'entrada

    run = await run_fixture("entry_lots.atom")

    tender = await one_tender(db)
    assert tender.source_removed_at is None
    assert run.tombstones_applied == 0
    records = await all_records(db)
    assert len(records) == 2
    assert all(r.tender_id == tender.id for r in records)
    assert records[0].error_message == "superseded"
    assert {r.processing_status for r in records} == {ProcessingStatus.PROCESSED}
    assert "SOURCE_ENTRY_REMOVED" not in await codes(db, tender.id)


async def test_entrada_sense_updated_amb_ustar_no_saplica(db, run_fixture, tmp_path):
    """Sense `atom:updated` no es pot demostrar que sigui mes nova: mai regressa."""
    await run_fixture("entry_lots.atom")
    before = await one_tender(db)
    title, deadline, ustar = before.title, before.deadline_at, before.source_updated_at

    text = (AGREGADAS / "entry_lots_republished.atom").read_text(encoding="utf-8")
    entry_updated = "<updated>2026-10-07T20:00:34.510+02:00</updated>"
    assert text.count(entry_updated) == 1
    variant = tmp_path / "entry_lots_sense_updated.atom"
    variant.write_text(text.replace(entry_updated, ""), encoding="utf-8")

    run = await run_fixture(variant)

    assert run.records_superseded == 1
    assert (run.records_failed, run.records_changed, run.tenders_updated) == (0, 0, 0)
    tender = await one_tender(db)
    assert (tender.title, tender.deadline_at, tender.source_updated_at) == (
        title,
        deadline,
        ustar,
    )
    records = await all_records(db)
    assert len(records) == 2
    kept = records[-1]
    assert kept.source_updated_at is None
    assert kept.tender_id == tender.id
    assert kept.processing_status == ProcessingStatus.PROCESSED
    assert kept.error_message == "superseded"
    assert await count(db, TenderRevision) == 1


_U_STAR = datetime(2026, 10, 5, 18, 0, 34, tzinfo=UTC)


@pytest.mark.parametrize(
    ("incoming", "canonical", "expected"),
    [
        pytest.param(_U_STAR - timedelta(days=1), _U_STAR, OLDER, id="U<U*"),
        # Mateix instant: el servei nomes hi arriba amb un hash diferent (el
        # mateix hash ja ha sortit per la regla A) -> conflicte.
        pytest.param(_U_STAR, _U_STAR, SAME, id="U==U*"),
        pytest.param(
            _U_STAR.astimezone(ZoneInfo("Europe/Madrid")), _U_STAR, SAME, id="U==U* altre offset"
        ),
        pytest.param(_U_STAR.replace(tzinfo=None), _U_STAR, SAME, id="naive (UTC) == aware"),
        pytest.param(_U_STAR + timedelta(seconds=1), _U_STAR, APPLY, id="U>U*"),
        pytest.param(_U_STAR, None, APPLY, id="U* NULL"),
        pytest.param(None, None, APPLY, id="U i U* NULL"),
        pytest.param(None, _U_STAR, OLDER, id="sense U amb U*"),
    ],
)
def test_ordre_de_publicacio(incoming, canonical, expected):
    assert publication_order(incoming, canonical) == expected


# --------------------------------------------------------------------------- #
# Absencies (4.4)                                                               #
# --------------------------------------------------------------------------- #


async def test_document_absent_en_publicacio_posterior_queda_retirat_i_torna(db, run_fixture):
    await run_fixture("entry_lots.atom")
    n_docs = await count(db, TenderDocument)

    await run_fixture("entry_lots_doc_removed.atom")

    assert sum(d.removed_at is not None for d in await docs(db)) == 1
    assert "DOCUMENT_REMOVED" in await codes(db, (await one_tender(db)).id)

    await run_fixture("entry_lots_republished.atom")

    assert all(d.removed_at is None for d in await docs(db))
    assert (await codes(db, (await one_tender(db)).id)).count("DOCUMENT_ADDED") == 1
    assert await count(db, TenderDocument) == n_docs  # cap delete fisic


async def test_lot_absent_queda_retirat_i_torna(db, run_fixture):
    await run_fixture("entry_lots.atom")

    await run_fixture("entry_lots_lot_removed.atom")

    assert sum(lot.removed_at is not None for lot in await lots(db)) == 1
    assert "LOT_REMOVED" in await codes(db, (await one_tender(db)).id)

    await run_fixture("entry_lots_republished.atom")

    assert all(lot.removed_at is None for lot in await lots(db))
    assert "LOT_RESTORED" in await codes(db, (await one_tender(db)).id)


async def test_entrada_malmesa_no_toca_documents(db, run_fixture, corrupt_fixture):
    await run_fixture("entry_lots.atom")
    before = await one_tender(db)
    title, deadline = before.title, before.deadline_at

    run = await run_fixture(corrupt_fixture("entry_lots_doc_removed.atom"))

    assert run.records_failed == 1
    assert all(d.removed_at is None for d in await docs(db))
    assert all(lot.removed_at is None for lot in await lots(db))
    tender = await one_tender(db)
    assert (tender.title, tender.deadline_at) == (title, deadline)
    assert await count(db, TenderRevision) == 1


async def test_cpv_per_diferencia_nomes_toca_els_codis_que_canvien(db, run_fixture, tmp_path):
    """Republicacio (+3 dies) que treu un CPV i n'afegeix un altre.

    El CPV que no canvia conserva la seva fila (cap DELETE sobre ell) i el seu
    `is_main`; nomes surten events dels dos codis que han canviat.
    """
    await run_fixture("entry_lots.atom")
    tender = await one_tender(db)
    before = {
        row.cpv_code: row.is_main
        for row in (
            await db.execute(sa.select(TenderCPV).where(TenderCPV.tender_id == tender.id))
        ).scalars()
    }
    assert before == {"45233222": True, "45233140": False}

    text = (AGREGADAS / "entry_lots.atom").read_text(encoding="utf-8")
    assert text.count(">45233140<") == 1 and ">45233120<" not in text
    text = text.replace(">45233140<", ">45233120<")
    text = text.replace(
        "<updated>2026-10-05T20:00:34.510+02:00</updated>",
        "<updated>2026-10-08T20:00:34.510+02:00</updated>",
        1,
    )
    variant = tmp_path / "entry_lots_cpv_changed.atom"
    variant.write_text(text, encoding="utf-8")

    deletes: list[tuple[str, str]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("DELETE FROM TENDER_CPVS"):
            deletes.append((statement, repr(parameters)))

    engine = db.bind.sync_engine
    sa.event.listen(engine, "before_cursor_execute", capture)
    try:
        run = await run_fixture(variant)
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture)

    assert run.tenders_updated == 1 and run.records_unchanged == 3
    after = {
        row.cpv_code: row.is_main
        for row in (
            await db.execute(
                sa.select(TenderCPV)
                .where(TenderCPV.tender_id == tender.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    assert after == {"45233222": True, "45233120": False}

    # Cap esborrat massiu: nomes la fila del codi retirat.
    assert deletes, "el CPV retirat s'havia d'esborrar"
    assert all("cpv_code" in statement for statement, _ in deletes)
    assert all("45233222" not in params for _, params in deletes)
    assert any("45233140" in params for _, params in deletes)

    events = (
        await db.execute(
            sa.select(TenderChangeEvent.change_code, TenderChangeEvent.subject).where(
                TenderChangeEvent.tender_id == tender.id,
                TenderChangeEvent.change_code.like("%CPV%"),
            )
        )
    ).all()
    assert sorted(events) == [("CPV_ADDED", "45233120"), ("CPV_REMOVED", "45233140")]


# --------------------------------------------------------------------------- #
# Run observable i comptadors (onada final)                                     #
# --------------------------------------------------------------------------- #


async def test_una_primera_entrada_que_peta_no_esborra_el_run(db, storage, monkeypatch):
    """El rollback d'una entrada no s'emporta el run: les seguents s'ingereixen igualment."""
    original = PlacspIngestionService._process_entry
    calls = 0

    async def flaky(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(PlacspIngestionService, "_process_entry", flaky)
    run = await PlacspIngestionService(db, storage).run(
        fixture=AGREGADAS / "entry_lots.atom", source_code=SOURCE_CODE
    )
    assert run.status == IngestionStatus.PARTIAL
    assert run.records_fetched == 4 and run.records_failed == 1
    assert run.records_new == 3 and run.tenders_created == 3

    runs = (await db.execute(sa.select(IngestionRun))).scalars().all()
    assert [r.id for r in runs] == [run.id]
    records = (await db.execute(sa.select(SourceRecord))).scalars().all()
    assert len(records) == 3 and {r.ingestion_run_id for r in records} == {run.id}
    assert (await db.execute(sa.select(sa.func.count()).select_from(Tender))).scalar_one() == 3


def _classified(run: IngestionRun) -> int:
    return (
        run.records_new
        + run.records_changed
        + run.records_unchanged
        + run.records_failed
        + run.records_superseded
        + run.records_conflicted
    )


async def test_una_lapida_orfena_compta_com_a_registre_nou(run_fixture):
    """`page1.atom`: 2 entrades + 1 lapida d'un expedient desconegut (es crea un SourceRecord)."""
    first = await run_fixture("page1.atom")
    assert first.records_fetched == 3
    assert first.records_new == 3 and first.tenders_created == 2
    assert _classified(first) == first.records_fetched

    again = await run_fixture("page1.atom")
    assert again.records_unchanged == 3 and again.records_new == 0
    assert _classified(again) == again.records_fetched


async def test_revision_count_compta_nomes_publicacions_aplicades(db, run_fixture, corrupt_fixture):
    """«Publicada N cops»: ni superades, ni conflictes, ni lapides, ni fallides."""
    await run_fixture("entry_lots.atom")
    assert (await one_tender(db)).revision_count == 1

    await run_fixture("entry_lots_older.atom")  # superada
    await run_fixture("entry_lots_same_updated_other_hash.atom")  # conflicte
    await run_fixture("tombstone_cerrada.atom")  # lapida
    failed = await run_fixture(corrupt_fixture("entry_lots_republished.atom"))  # fallida
    assert failed.records_failed == 1
    assert (await one_tender(db)).revision_count == 1
    assert len(await all_records(db)) == 5

    await run_fixture("entry_lots_republished.atom")  # aplicada
    tender = await one_tender(db)
    assert tender.revision_count == 2
    assert len(await all_records(db)) == 6
