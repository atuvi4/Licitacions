"""Garanties no destructives de la ingesta (spec 3B-1, seccions 4.2-4.4).

Ordre (`updated` anterior mai regressa), conflictes observables (mateix
`updated`, hash diferent), presencia a la font (lapides sense tocar l'estat
contractual) i absencies de documents i lots nomes des d'una publicacio
completa posterior. Fixtures: `tests/fixtures/placsp/agregadas/README.md`.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from app.ingestion.errors import safe_error_line
from app.ingestion.placsp.service import (
    APPLY,
    OLDER,
    SAME,
    PlacspIngestionService,
    publication_order,
)
from app.models.enums import (
    DocumentContributionStatus,
    DownloadStatus,
    IngestionStatus,
    ProcessingStatus,
    TenderDocumentOrigin,
    TenderDocumentType,
)
from app.models.sources import IngestionRun, SourceRecord
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Tender, TenderCPV, TenderDocument, TenderLot
from app.services.tender_lifecycle import record_revision
from app.tenders.documents import analyzable_documents

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
    # Hotfix radar: el cru de l'entrada fallida tambe es conserva (`failed`, missatge segur).
    assert len(records) == 4 and {r.ingestion_run_id for r in records} == {run.id}
    failed = [r for r in records if r.processing_status == ProcessingStatus.FAILED]
    assert len(failed) == 1 and failed[0].error_message == "unexpected: RuntimeError"
    assert failed[0].tender_id is None
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


# --------------------------------------------------------------------------- #
# Source ownership (3B-2 §2.1, §2.4)                                            #
# --------------------------------------------------------------------------- #

#: Les dues URL de l'entrada base d'`entry_lots.atom`.
PCAP_URL = (
    "https://contractaciopublica.cat/portal-api/descarrega-document/302650531/"
    "A0640B65450E110D5E7E2159F9A4D132"
)
PPT_URL = (
    "https://contractaciopublica.cat/portal-api/descarrega-document/302650532/"
    "18BA628A594015644C2C1BA930CFC55D"
)
OPERATOR_URL = "https://contractaciopublica.cat/perfil/annex-oficial.pdf"
VERIFIED_AT = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


async def _source(db, code: str):
    return await PlacspIngestionService(db).get_or_create_source(code)


async def _by_url(db, url: str) -> TenderDocument:
    tender = await one_tender(db)
    return (
        await db.execute(
            sa.select(TenderDocument)
            .where(TenderDocument.tender_id == tender.id, TenderDocument.url == url)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _operator_document(db, url: str = OPERATOR_URL) -> TenderDocument:
    """Document oficial suplementari ja verificat (Task 3 encara no te servei)."""
    tender = await one_tender(db)
    document = TenderDocument(
        tender_id=tender.id,
        name="Annex oficial del perfil.pdf",
        document_type=TenderDocumentType.ADDITIONAL,
        url=url,
        origin=TenderDocumentOrigin.OPERATOR_URL,
        governing_source_id=None,
        contribution_status=DocumentContributionStatus.VERIFIED,
        verified_at=VERIFIED_AT,
        contribution_source_url="https://contractaciopublica.cat/perfil",
    )
    db.add(document)
    await db.commit()
    return document


async def test_els_documents_del_feed_neixen_governats_per_la_font_sense_el_listener(
    db, run_fixture
):
    """La ingesta posa `origin`/`governing_source_id` explicitament (no el listener)."""
    from app.models.tenders import _default_origin

    sa.event.remove(TenderDocument, "before_insert", _default_origin)
    try:
        await run_fixture("entry_lots.atom")
    finally:
        sa.event.listen(TenderDocument, "before_insert", _default_origin)

    source = await _source(db, SOURCE_CODE)
    documents = await docs(db)
    assert len(documents) == 2
    for document in documents:
        assert document.origin == TenderDocumentOrigin.FEED
        assert document.governing_source_id == source.id
        assert document.contribution_status == DocumentContributionStatus.NOT_APPLICABLE


async def test_un_sync_complet_posterior_no_retira_un_document_d_operador_verificat(
    db, run_fixture
):
    """Prova obligatoria del §2.4: el document oficial suplementari sobreviu intacte."""
    await run_fixture("entry_lots.atom")
    operator = await _operator_document(db)

    await run_fixture("entry_lots_doc_removed.atom")  # sense el PPT ni el document d'operador

    kept = await _by_url(db, OPERATOR_URL)
    assert kept.removed_at is None
    assert kept.name == "Annex oficial del perfil.pdf"
    assert kept.document_type == TenderDocumentType.ADDITIONAL
    assert kept.origin == TenderDocumentOrigin.OPERATOR_URL
    assert kept.governing_source_id is None
    assert kept.contribution_status == DocumentContributionStatus.VERIFIED
    assert kept.verified_at is not None
    analyzable = await analyzable_documents(db, kept.tender_id)
    assert operator.id in {d.id for d in analyzable}

    # El document del feed que falta si que queda retirat.
    assert (await _by_url(db, PPT_URL)).removed_at is not None
    assert (await _by_url(db, PCAP_URL)).removed_at is None


async def test_un_sync_no_toca_els_documents_governats_per_una_altra_font(db, run_fixture):
    await run_fixture("entry_lots.atom")
    other = await _source(db, "placsp")  # la 643 historica
    tender = await one_tender(db)
    foreign = TenderDocument(
        tender_id=tender.id,
        name="Document de la 643.pdf",
        document_type=TenderDocumentType.OTHER,
        url="https://contrataciondelestado.es/doc/643.pdf",
        origin=TenderDocumentOrigin.FEED,
        governing_source_id=other.id,
    )
    db.add(foreign)
    await db.commit()

    await run_fixture("entry_lots_doc_removed.atom")

    untouched = await _by_url(db, "https://contrataciondelestado.es/doc/643.pdf")
    assert untouched.removed_at is None
    assert untouched.governing_source_id == other.id
    assert untouched.name == "Document de la 643.pdf"


async def test_la_restauracio_no_sobreescriu_un_document_no_governat(db, run_fixture):
    await run_fixture("entry_lots.atom")
    await run_fixture("entry_lots_doc_removed.atom")
    other = await _source(db, "placsp")
    ppt = await _by_url(db, PPT_URL)
    assert ppt.removed_at is not None
    removed_at = ppt.removed_at
    # Ara el governa una altra font (p. ex. la 643): aquesta ja no el pot tocar.
    ppt.governing_source_id = other.id
    ppt.name = "Nom de l'altra font"
    ppt.document_type = TenderDocumentType.OTHER
    await db.commit()

    await run_fixture("entry_lots_republished.atom")  # el PPT torna a ser al feed

    after = await _by_url(db, PPT_URL)
    assert after.removed_at == removed_at
    assert after.name == "Nom de l'altra font"
    assert after.document_type == TenderDocumentType.OTHER
    assert after.governing_source_id == other.id


async def test_una_url_del_feed_que_xoca_amb_un_document_d_operador_no_l_adopta(
    db, run_fixture, caplog
):
    """UNIQUE(tender_id, url): el feed no s'apropia el document; es deixa i s'avisa."""
    await run_fixture("entry_lots.atom")
    await run_fixture("entry_lots_doc_removed.atom")
    ppt = await _by_url(db, PPT_URL)
    # L'operador l'havia incorporat per la seva banda amb la mateixa URL.
    ppt.origin = TenderDocumentOrigin.OPERATOR_URL
    ppt.governing_source_id = None
    ppt.contribution_status = DocumentContributionStatus.VERIFIED
    ppt.verified_at = VERIFIED_AT
    ppt.removed_at = None
    ppt.name = "Memoria aportada per l'operador.pdf"
    await db.commit()
    n_docs = await count(db, TenderDocument)

    with caplog.at_level("WARNING", logger="app.ingestion.placsp"):
        await run_fixture("entry_lots_republished.atom")

    after = await _by_url(db, PPT_URL)
    assert after.origin == TenderDocumentOrigin.OPERATOR_URL
    assert after.governing_source_id is None
    assert after.name == "Memoria aportada per l'operador.pdf"
    assert after.contribution_status == DocumentContributionStatus.VERIFIED
    assert await count(db, TenderDocument) == n_docs

    lines = [
        getattr(record, "error", None)
        for record in caplog.records
        if record.getMessage() == "placsp.document.not_governed"
    ]
    assert lines == ["integrity_constraint: DocumentNotGoverned uq_tender_documents_tender_url"]
    assert safe_error_line(lines[0]) == lines[0]


async def _same_bytes(db, *documents: TenderDocument) -> None:
    """Els documents passen a tenir el mateix contingut vigent (sense xarxa)."""
    content = TenderDocumentContent(
        sha256="a" * 64,
        storage_key="tests/alies.pdf",
        file_size=10,
        mime_type="application/pdf",
        is_text_extractable=True,
    )
    db.add(content)
    await db.flush()
    for document in documents:
        db.add(
            TenderDocumentFile(
                tender_document_id=document.id,
                content_id=content.id,
                is_current=True,
                download_status=DownloadStatus.DOWNLOADED,
            )
        )
    await db.commit()


async def test_si_el_feed_retira_el_canonic_l_alies_d_operador_es_promou(db, run_fixture):
    await run_fixture("entry_lots.atom")
    ppt = await _by_url(db, PPT_URL)
    operator = await _operator_document(db)
    await _same_bytes(db, ppt, operator)
    operator.duplicate_of_document_id = ppt.id
    # Com fa el processat real: la revisio `document_processing` en coneix el sha.
    await record_revision(db, await one_tender(db), trigger="document_processing")
    await db.commit()

    await run_fixture("entry_lots_doc_removed.atom")

    assert (await _by_url(db, PPT_URL)).removed_at is not None
    promoted = await _by_url(db, OPERATOR_URL)
    assert promoted.removed_at is None
    assert promoted.duplicate_of_document_id is None
    tender = await one_tender(db)
    triggers = (
        await db.scalars(
            sa.select(TenderRevision.trigger)
            .where(TenderRevision.tender_id == tender.id)
            .order_by(TenderRevision.revision_number)
        )
    ).all()
    assert triggers[-2:] == ["ingestion", "document_intake"]
    intake = (
        await db.scalars(
            sa.select(TenderRevision)
            .where(TenderRevision.tender_id == tender.id)
            .order_by(TenderRevision.revision_number.desc())
            .limit(1)
        )
    ).one()
    intake_codes = (
        await db.scalars(
            sa.select(TenderChangeEvent.change_code).where(
                TenderChangeEvent.to_revision_id == intake.id
            )
        )
    ).all()
    # La promocio s'explica (informatiu); el PPT retirat no es `DOCUMENT_REMOVED`
    # perque els seus bytes continuen al tender sota el document d'operador.
    assert list(intake_codes) == ["DOCUMENT_ALIAS_CHANGED"]
    assert "DOCUMENT_REMOVED" not in await codes(db, tender.id)

    # El feed torna a publicar el PPT: torna a ser el canonic i l'operador, alies.
    await run_fixture("entry_lots_republished.atom")

    assert (await _by_url(db, PPT_URL)).duplicate_of_document_id is None
    assert (await _by_url(db, OPERATOR_URL)).duplicate_of_document_id == ppt.id


# --------------------------------------------------------------------------- #
# Un rol revisat sobreviu a la reingesta (3B-2 §3.2)                           #
# --------------------------------------------------------------------------- #


@pytest.fixture
def republished_variant(tmp_path):
    """`entry_lots_republished` tornada a publicar mes tard (`updated` = `day`).

    Amb `retype`, el feed publica el PPT com a document addicional
    (`TechnicalDocumentReference` -> `AdditionalDocumentReference`); sense, la
    publicacio es nova pero identica (una reingesta completa que no canvia res).
    """

    def _variant(day: str, *, retype: bool, ppt_url: str | None = None) -> Path:
        text = (AGREGADAS / "entry_lots_republished.atom").read_text(encoding="utf-8")
        text, bumped = re.subn(
            r"<updated>2026-10-07T20:00:34\.510\+02:00</updated>",
            f"<updated>{day}T20:00:34.510+02:00</updated>",
            text,
            count=1,
        )
        assert bumped == 1
        if ppt_url is not None:
            # El feed publica els mateixos bytes del PPT en una URL nova.
            assert text.count(PPT_URL) == 1
            text = text.replace(PPT_URL, ppt_url)
        if retype:
            text, opened = re.subn(
                r"<cac:TechnicalDocumentReference>",
                "<cac:AdditionalDocumentReference>",
                text,
                count=1,
            )
            text, closed = re.subn(
                r"</cac:TechnicalDocumentReference>",
                "</cac:AdditionalDocumentReference>",
                text,
                count=1,
            )
            assert (opened, closed) == (1, 1)
        kind = ("retyped" if retype else "same") + ("_moved" if ppt_url else "")
        target = tmp_path / f"entry_lots_{day}_{kind}.atom"
        target.write_text(text, encoding="utf-8")
        return target

    return _variant


async def test_un_rol_revisat_sobreviu_a_la_reingesta_i_el_canvi_de_tipus_es_registra(
    db, run_fixture, republished_variant
):
    """Prova obligatoria: la revisio continua manant, intacta, i el canvi es diu."""
    from app.models.enums import DocumentRole, RoleSource
    from app.models.global_classification import DocumentRoleReview
    from app.services.document_roles import effective_document_roles, record_role_review
    from tests.test_platform_onboarding import create_operator

    await run_fixture("entry_lots.atom")
    await run_fixture("entry_lots_republished.atom")
    ppt = await _by_url(db, PPT_URL)
    assert ppt.document_type == TenderDocumentType.TECHNICAL_TERMS
    await _same_bytes(db, ppt)
    tender = await one_tender(db)
    await record_revision(db, tender, trigger="document_processing")
    await db.commit()

    operator = await create_operator(db)
    review = await record_role_review(
        db,
        document_id=ppt.id,
        content_sha256="a" * 64,
        role=DocumentRole.CHARACTERISTICS_TABLE,
        actor=operator,
        reason="La memoria valorada es el quadre de caracteristiques.",
        based_on_run_id=None,
    )
    await db.commit()
    review_id = review.id

    # Una reingesta completa (publicacio nova) que no canvia res no registra res.
    # Unica excepcio, NOMES a SQLite: el termini es desa sense zona i en tornar-lo
    # a llegir es pren com a UTC, de manera que pot sortir un `DEADLINE_CHANGED`
    # (artefacte del dialecte; a PostgreSQL `timestamptz` conserva l'instant i no
    # es crea cap revisio: `test_una_reingesta_identica_no_crea_cap_revisio_a_postgres`).
    revisions = await count(db, TenderRevision)
    before = Counter(await codes(db, tender.id))
    same = await run_fixture(republished_variant("2026-10-08", retype=False))
    assert same.records_failed == 0
    added = Counter(await codes(db, tender.id)) - before
    assert set(added) <= {"DEADLINE_CHANGED"}, added
    if db.bind.dialect.name == "postgresql":
        assert not added
        assert await count(db, TenderRevision) == revisions
    assert (await _by_url(db, PPT_URL)).document_type == TenderDocumentType.TECHNICAL_TERMS

    # Reingesta completa que canvia el tipus de font del document revisat.
    retyped_fixture = republished_variant("2026-10-09", retype=True)
    await run_fixture(retyped_fixture)

    retyped = await _by_url(db, PPT_URL)
    assert retyped.document_type == TenderDocumentType.ADDITIONAL  # el feed governa el tipus
    rows = (
        (
            await db.execute(
                sa.select(DocumentRoleReview)
                .where(DocumentRoleReview.tender_document_id == ppt.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert [(r.id, r.role, r.content_sha256, r.reason) for r in rows] == [
        (
            review_id,
            DocumentRole.CHARACTERISTICS_TABLE,
            "a" * 64,
            "La memoria valorada es el quadre de caracteristiques.",
        )
    ]
    roles = await effective_document_roles(db, tender.id, None)
    assert (roles[ppt.id].source, roles[ppt.id].role) == (
        RoleSource.OPERATOR,
        DocumentRole.CHARACTERISTICS_TABLE,
    )
    assert roles[ppt.id].document_type == TenderDocumentType.ADMINISTRATIVE_TERMS

    event = (
        await db.execute(
            sa.select(TenderChangeEvent, TenderRevision.trigger)
            .join(TenderRevision, TenderRevision.id == TenderChangeEvent.to_revision_id)
            .where(
                TenderChangeEvent.tender_id == tender.id,
                TenderChangeEvent.change_code == "SOURCE_TYPE_CHANGED_AFTER_REVIEW",
            )
        )
    ).all()
    assert len(event) == 1
    change, trigger = event[0]
    assert trigger == "ingestion"
    assert (change.old_value, change.new_value) == ("technical_terms", "additional")
    assert change.is_material is False
    assert change.impacts == ["informational"]

    # Tornar a passar la mateixa publicacio no torna a registrar res.
    await run_fixture(retyped_fixture)
    assert (await codes(db, tender.id)).count("SOURCE_TYPE_CHANGED_AFTER_REVIEW") == 1


async def _reviewed_ppt(db, run_fixture):
    """Tender base republicat, PPT amb bytes (`a`*64) i revisat `characteristics_table`."""
    from app.models.enums import DocumentRole
    from app.services.document_roles import record_role_review
    from tests.test_platform_onboarding import create_operator

    await run_fixture("entry_lots.atom")
    await run_fixture("entry_lots_republished.atom")
    ppt = await _by_url(db, PPT_URL)
    await _same_bytes(db, ppt)
    tender = await one_tender(db)
    await record_revision(db, tender, trigger="document_processing")
    await db.commit()
    operator = await create_operator(db)
    await record_role_review(
        db,
        document_id=ppt.id,
        content_sha256="a" * 64,
        role=DocumentRole.CHARACTERISTICS_TABLE,
        actor=operator,
        reason="Es el quadre de caracteristiques.",
        based_on_run_id=None,
    )
    await db.commit()
    return tender, ppt


@pytest.mark.postgres
async def test_una_reingesta_identica_no_crea_cap_revisio_a_postgres(
    db, run_fixture, republished_variant
):
    """M1: a PostgreSQL una publicacio nova identica no crea cap revisio, ni un event."""
    tender, _ppt = await _reviewed_ppt(db, run_fixture)
    revisions = await count(db, TenderRevision)
    before = Counter(await codes(db, tender.id))

    same = await run_fixture(republished_variant("2026-10-08", retype=False))

    assert same.records_failed == 0
    assert await count(db, TenderRevision) == revisions
    assert Counter(await codes(db, tender.id)) == before


async def test_una_revisio_segueix_el_contingut_quan_el_feed_canvia_l_url(
    db, run_fixture, republished_variant
):
    """M4: la revisio s'ancora al contingut DINS del tender, no al document.

    El feed publica els mateixos bytes en una URL nova: el document vell queda
    retirat i el nou, quan se'n tenen els bytes, continua revisat (resolutor i
    fotografia).
    """
    from app.models.enums import DocumentRole, RoleSource
    from app.services.document_roles import effective_document_roles
    from app.services.tender_lifecycle import build_snapshot

    tender, ppt = await _reviewed_ppt(db, run_fixture)
    new_url = PPT_URL.replace("302650532", "302650999")

    await run_fixture(republished_variant("2026-10-09", retype=False, ppt_url=new_url))

    assert (await _by_url(db, PPT_URL)).removed_at is not None
    moved = await _by_url(db, new_url)
    assert moved.id != ppt.id
    content = await db.scalar(
        sa.select(TenderDocumentContent).where(TenderDocumentContent.sha256 == "a" * 64)
    )
    db.add(
        TenderDocumentFile(
            tender_document_id=moved.id,
            content_id=content.id,
            is_current=True,
            download_status=DownloadStatus.DOWNLOADED,
        )
    )
    await db.commit()

    roles = await effective_document_roles(db, tender.id, None)
    assert ppt.id not in roles
    assert (roles[moved.id].source, roles[moved.id].role) == (
        RoleSource.OPERATOR,
        DocumentRole.CHARACTERISTICS_TABLE,
    )
    snapshot = await build_snapshot(db, await one_tender(db))
    entry = next(d for d in snapshot["documents"] if d["url"] == new_url)
    assert entry["reviewed_role"] == "characteristics_table"
