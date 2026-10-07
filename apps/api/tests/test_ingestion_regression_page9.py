"""Regressio del primer sync real del pilot (2026-10-07, run 357b640e).

Pagina 9 del feed 1044: l'entrada 193 (`.../14362883`) te dos lots amb titols de
1.017 i 1.076 caracters. Amb `tender_lots.title` VARCHAR(1000) el flush fallava
(StringDataRightTruncation) i, com que el `rollback` de la sessio expirava la
font i el run que el servei te en memoria, TOTES les entrades i lapides
seguents fallaven amb «Error: error inesperat» (MissingGreenlet).

Fixtures (`tests/fixtures/placsp/agregadas/README.md`):

* `entry_long_lot_titles.atom`: nomes l'entrada 193 retallada (sense adjudicataries).
* `page9_regression.atom`: A (entrada base d'`entry_lots`), B (la 193), C1-C3
  (les altres tres d'`entry_lots`) i una lapida bona de A.

Tecnica de l'«esquema antic» a PostgreSQL: el harness crea l'schema amb
`create_all` (estat del cap, amb 0057); el test torna `tender_lots.title` i
`tenders.title` a VARCHAR(1000) amb `ALTER TABLE` sobre l'schema aillat (el que
feia 0056) i simula el codi d'abans amb `SCHEMA_REVISION = "0056_radar_search"`;
despres els torna a TEXT i restaura la revisio. No s'usa el `downgrade()` sencer
de 0057 perque tambe esborra les columnes de fallades conegudes que el model
actual escriu (el round-trip real de 0057 es a `test_migrations.py`). A SQLite
VARCHAR no limita la longitud: els tests de longitud nomes demostren la fallada
a PostgreSQL; l'aillament es prova a totes dues amb una violacio controlada.

Reintents (fix round 1): les fallades transitories es reintenten sempre; les
deterministes nomes si canvia el `parser_version` o la revisio d'esquema; si no,
son «fallades conegudes» (`records_failed_known`), que no fan el run `partial`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.ingestion import sync as sync_module
from app.ingestion.placsp import parser as parser_module
from app.ingestion.placsp import service as service_module
from app.ingestion.placsp.service import PlacspIngestionService
from app.models.enums import IngestionStatus, ProcessingStatus
from app.models.sources import IngestionRun, Source, SourceRecord
from app.models.tender_lifecycle import TenderRevision
from app.models.tenders import Tender, TenderLot
from app.services.files import compute_sha256
from tests.test_ingestion_pagination import PAGE2_URL, Feed, _install, as_utc, get_source

AGREGADAS = Path(__file__).parent / "fixtures" / "placsp" / "agregadas"
LONG = AGREGADAS / "entry_long_lot_titles.atom"
PAGE9 = AGREGADAS / "page9_regression.atom"
CODE = "placsp_agregadas"

_PREFIX = "https://contrataciondelestado.es/sindicacion/PlataformasAgregadasSinMenores/"
A_ID = _PREFIX + "20529505"
B_ID = _PREFIX + "14362883"
C_IDS = (_PREFIX + "19835238", _PREFIX + "20463765", _PREFIX + "20626007")
GOOD_IDS = (A_ID, *C_IDS)

#: Res d'aixo pot apareixer als tres camps d'error (SQL, parametres, XML, titols).
FORBIDDEN = (
    "INSERT",
    "UPDATE ",
    "SELECT",
    "VALUES",
    "$1",
    "parameters",
    "Dializador",
    "Agujas",
    "<",
    "sqlalche.me",
    "character varying",
)


# --------------------------------------------------------------------------- #
# Ajudants                                                                      #
# --------------------------------------------------------------------------- #


def _source_names(path: Path, external_id: str) -> tuple[str, list[str]]:
    """El `ProcurementProject/Name` de l'expedient i dels lots, tal com els publica la font."""
    atom = "{http://www.w3.org/2005/Atom}"
    for entry in ET.parse(path).getroot().findall(f"{atom}entry"):
        if entry.findtext(f"{atom}id").strip() != external_id:
            continue
        status = entry.find("{*}ContractFolderStatus")
        tender_name = status.findtext("{*}ProcurementProject/{*}Name").strip()
        lots = [
            lot.findtext("{*}ProcurementProject/{*}Name").strip()
            for lot in status.findall("{*}ProcurementProjectLot")
        ]
        return tender_name, lots
    raise AssertionError(external_id)


def _b_segment(text: str) -> tuple[str, str, str]:
    start = text.index(f"<id>{B_ID}</id>")
    end = text.index("</entry>", start)
    return text[:start], text[start:end], text[end:]


def _duplicate_lot_variant(tmp_path: Path) -> Path:
    """B amb dos lots `ID_LOTE` 1: viola `uq_tender_lots_tender_external` (SQLite i PG).

    Es una violacio controlada que continua fallant amb el fix: serveix per
    provar l'aillament a totes dues bases.
    """
    head, segment, tail = _b_segment(PAGE9.read_text(encoding="utf-8"))
    broken, n = re.subn(
        r'(<cbc:ID schemeName="ID_LOTE">)2(</cbc:ID>)', r"\g<1>1\g<2>", segment, count=1
    )
    assert n == 1
    target = tmp_path / "page9_duplicate_lot.atom"
    target.write_text(head + broken + tail, encoding="utf-8")
    return target


async def _run(db, storage, path: Path) -> IngestionRun:
    return await PlacspIngestionService(db, storage).run(fixture=path, source_code=CODE)


def _balanced(run: IngestionRun) -> bool:
    return run.records_fetched == (
        run.records_new
        + run.records_changed
        + run.records_unchanged
        + run.records_failed
        + run.records_failed_known
        + run.records_superseded
        + run.records_conflicted
        + run.tombstones_applied
    )


async def _records(db, external_id: str) -> list[SourceRecord]:
    return list(
        (
            await db.execute(
                sa.select(SourceRecord)
                .where(SourceRecord.external_id == external_id)
                .order_by(SourceRecord.revision_number)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def _tender(db, external_id: str) -> Tender | None:
    return (
        await db.execute(
            sa.select(Tender)
            .where(Tender.external_id == external_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _lot_titles(db, external_id: str) -> list[str]:
    tender = await _tender(db, external_id)
    rows = (
        await db.execute(
            sa.select(TenderLot)
            .where(TenderLot.tender_id == tender.id)
            .order_by(TenderLot.lot_number)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return [row.title for row in rows]


async def _count(db, model) -> int:
    return (await db.execute(sa.select(sa.func.count()).select_from(model))).scalar_one()


async def _set_title_types(engine, sql_type: str) -> None:
    """Tipus de `tender_lots.title` i `tenders.title` a l'schema aillat de PG.

    `VARCHAR(1000)` es l'estat de 0056; `TEXT`, el de 0057. Sense cap `USING`.
    """
    assert sql_type in ("VARCHAR(1000)", "TEXT")
    async with engine.begin() as conn:
        for table in ("tender_lots", "tenders"):
            await conn.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN title TYPE {sql_type}"))
    # Com un desplegament real (migrar i reiniciar): fora les connexions del pool i
    # les seves sentencies preparades d'asyncpg, que amb el tipus del resultat
    # canviat petarien amb InvalidCachedStatementError (0A000).
    await engine.dispose()


async def _column_type(db, table: str, column: str) -> tuple[str, int | None]:
    row = (
        await db.execute(
            sa.text(
                "SELECT data_type, character_maximum_length FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        )
    ).one()
    return row[0], row[1]


# --------------------------------------------------------------------------- #
# 1. Titols de font > 1000                                                      #
# --------------------------------------------------------------------------- #


async def test_titols_de_lot_llargs_s_ingereixen_sencers_i_idempotents(db, storage):
    run = await _run(db, storage, LONG)
    assert run.status == IngestionStatus.SUCCESS, run.error_summary
    assert run.records_new == 1 and run.records_failed == 0

    _, expected = _source_names(LONG, B_ID)
    assert sorted(len(title) for title in expected)[-2:] == [1017, 1076]
    persisted = await _lot_titles(db, B_ID)
    # Unicode-identics: mateixa longitud i mateix text (accents, «&nbsp;», salts de linia).
    assert [len(title) for title in persisted] == [len(title) for title in expected]
    assert persisted == expected
    assert any("Hemodiálisis" in title for title in persisted)

    revisions = await _count(db, TenderRevision)
    again = await _run(db, storage, LONG)
    assert again.status == IngestionStatus.SUCCESS
    assert again.records_unchanged == 1 and again.records_new == 0 and again.records_changed == 0
    assert await _count(db, TenderRevision) == revisions
    assert await _lot_titles(db, B_ID) == expected


async def test_titol_de_licitacio_llarg_no_es_trunca(db, storage, tmp_path):
    """`ProcurementProject/Name` de l'expedient es el mateix camp lliure que el del lot.

    A la mateixa pagina 9 n'hi ha un de 1.999 caracters (entrada 434). Variant
    sintetica: el nom de l'expedient de B substituit pels dos titols reals de lot.
    """
    text = LONG.read_text(encoding="utf-8")
    raw_lots = re.findall(
        r"<cac:ProcurementProjectLot>.*?<cbc:Name>(.*?)</cbc:Name>", text, flags=re.DOTALL
    )
    long_name_raw = raw_lots[0] + " " + raw_lots[1]
    variant, n = re.subn(
        r"(<cac-place-ext:ContractFolderStatus>.*?<cac:ProcurementProject>\s*<cbc:Name>)"
        r"(.*?)(</cbc:Name>)",
        lambda m: m.group(1) + long_name_raw + m.group(3),
        text,
        count=1,
        flags=re.DOTALL,
    )
    assert n == 1
    path = tmp_path / "entry_long_tender_title.atom"
    path.write_text(variant, encoding="utf-8")
    expected, lots = _source_names(path, B_ID)
    assert len(expected) > 2000 and expected == f"{lots[0]} {lots[1]}"

    run = await _run(db, storage, path)
    assert run.status == IngestionStatus.SUCCESS, run.error_summary
    tender = await _tender(db, B_ID)
    assert len(tender.title) == len(expected)
    assert tender.title == expected


# --------------------------------------------------------------------------- #
# 2. Aillament per entrada i per lapida                                         #
# --------------------------------------------------------------------------- #


async def test_una_entrada_dolenta_no_enverina_les_seguents_ni_la_lapida(
    db, session_factory, storage, tmp_path
):
    """Patro real: una entrada dolenta seguida de 3 de bones i d'una lapida bona."""
    duplicate = _duplicate_lot_variant(tmp_path)
    run = await _run(db, storage, duplicate)

    assert run.status == IngestionStatus.PARTIAL
    assert run.records_fetched == 6
    assert run.records_new == 4 and run.tenders_created == 4
    assert run.records_failed == 1
    assert run.tombstones_applied == 1
    assert _balanced(run)

    # A i C s'han aplicat en la mateixa sessio despres de B.
    for external_id in GOOD_IDS:
        tender = await _tender(db, external_id)
        assert tender is not None and tender.latest_source_record_id is not None
    assert (await _tender(db, A_ID)).source_removed_at is not None  # la lapida
    assert await _tender(db, B_ID) is None  # res de B a mitges: ni licitacio ni lots

    # El SourceRecord cru de B es conserva, fallit, amb un missatge segur.
    [failed] = await _records(db, B_ID)
    assert failed.processing_status == ProcessingStatus.FAILED
    assert failed.ingestion_run_id == run.id and failed.tender_id is None
    assert failed.error_message.startswith("integrity_constraint: ")
    raw = await storage.download(failed.raw_storage_key)
    assert compute_sha256(raw) == failed.raw_hash and B_ID.encode() in raw
    assert run.error_summary.splitlines()[0].startswith("1 integrity_constraint (")
    await sync_module._write_health(  # noqa: SLF001 - el cami real de `last_sync_error`
        session_factory, run.source_id, started=datetime.now(UTC), run=run, error=None
    )
    async with session_factory() as other:
        last_sync_error = (await other.get(Source, run.source_id)).last_sync_error
    assert last_sync_error.startswith("1 integrity_constraint (")
    assert last_sync_error.endswith("; 1 entrades fallides") and len(last_sync_error) <= 200
    for field in (failed.error_message, run.error_summary, last_sync_error):
        for forbidden in FORBIDDEN:
            assert forbidden not in field, (forbidden, field)

    # Segon sync identic: res no es duplica; B (determinista, mateix parser i
    # mateix esquema) es una fallada coneguda: no es reintenta ni fa `partial`.
    revisions = await _count(db, TenderRevision)
    records = await _count(db, SourceRecord)
    again = await _run(db, storage, duplicate)
    assert again.status == IngestionStatus.SUCCESS, again.error_summary
    assert again.records_unchanged == 5 and again.records_failed == 0
    assert again.records_failed_known == 1
    assert again.records_new == 0 and again.records_changed == 0
    assert _balanced(again)
    assert again.error_summary == "1 fallades conegudes (integrity_constraint)"
    assert await _count(db, TenderRevision) == revisions
    assert await _count(db, SourceRecord) == records
    assert [r.processing_status for r in await _records(db, B_ID)] == [ProcessingStatus.FAILED]

    # Amb B corregida: s'aplica B, sense duplicar A ni C.
    fixed = await _run(db, storage, PAGE9)
    assert fixed.status == IngestionStatus.SUCCESS, fixed.error_summary
    assert fixed.tenders_created == 1 and fixed.records_unchanged == 5
    assert fixed.records_new + fixed.records_changed == 1
    assert _balanced(fixed)
    assert await _count(db, Tender) == 5
    for external_id in GOOD_IDS:
        # A te tambe el registre de la seva lapida.
        assert len(await _records(db, external_id)) == (2 if external_id == A_ID else 1)


async def test_una_lapida_dolenta_entre_dues_de_bones(db, storage, tmp_path, monkeypatch):
    """La lapida del mig peta a mitja transaccio (SQL real que falla): les altres s'apliquen."""
    first = await _run(db, storage, AGREGADAS / "entry_lots.atom")
    assert first.status == IngestionStatus.SUCCESS

    tombstone = re.search(
        r"<at:deleted-entry\b.*?</at:deleted-entry>",
        PAGE9.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    ).group(0)
    stones = [tombstone.replace(A_ID, external_id) for external_id in (A_ID, *C_IDS[1:])]
    head = PAGE9.read_text(encoding="utf-8").split("<entry>")[0]
    path = tmp_path / "three_tombstones.atom"
    path.write_text(head + "\n    ".join(stones) + "\n</feed>\n", encoding="utf-8")

    bad_id = C_IDS[1]
    original = service_module.record_revision

    async def poisoned(session, tender, **kwargs):
        if tender.external_id == bad_id:
            # Error real de la base a mitja unitat: a PG avorta la transaccio.
            await session.execute(sa.text("SELECT * FROM taula_que_no_existeix"))
        return await original(session, tender, **kwargs)

    monkeypatch.setattr(service_module, "record_revision", poisoned)
    run = await _run(db, storage, path)
    assert run.status == IngestionStatus.PARTIAL
    assert run.records_fetched == 3 and run.tombstones_applied == 2 and run.records_failed == 1
    assert _balanced(run)
    assert (await _tender(db, A_ID)).source_removed_at is not None
    assert (await _tender(db, C_IDS[2])).source_removed_at is not None
    assert (await _tender(db, bad_id)).source_removed_at is None  # rollback nomes d'aquesta
    records = await _records(db, bad_id)
    failed = [r for r in records if r.processing_status == ProcessingStatus.FAILED]
    assert len(failed) == 1 and failed[0].error_message.startswith("unexpected: ")
    assert run.error_summary.splitlines()[0].startswith("1 unexpected (")

    # `unexpected` es transitoria: es reintenta encara que res no hagi canviat
    # (aqui el defecte persisteix i torna a fallar; mai es «coneguda»).
    persists = await _run(db, storage, path)
    assert persists.status == IngestionStatus.PARTIAL
    assert persists.records_failed == 1 and persists.records_failed_known == 0
    assert persists.records_unchanged == 2
    assert _balanced(persists)

    # Corregit el defecte, la lapida fallida es reintenta; les bones queden igual.
    monkeypatch.setattr(service_module, "record_revision", original)
    retry = await _run(db, storage, path)
    assert retry.status == IngestionStatus.SUCCESS, retry.error_summary
    assert retry.records_unchanged == 2 and retry.tombstones_applied == 1
    assert _balanced(retry)
    assert (await _tender(db, bad_id)).source_removed_at is not None
    assert [r.processing_status for r in await _records(db, bad_id)][-1] == (
        ProcessingStatus.PROCESSED
    )


async def test_les_pagines_posteriors_tambe_s_apliquen(db, storage, tmp_path, monkeypatch):
    """Recorregut del feed: B a la pagina 1 no enverina la pagina 2."""
    page1 = _duplicate_lot_variant(tmp_path).read_text(encoding="utf-8")
    page1 = page1.replace(
        'rel="self"/>', f'rel="self"/>\n    <link href="{PAGE2_URL}" rel="next"/>', 1
    )
    feed = Feed(page1=page1.encode("utf-8"))
    _install(monkeypatch, feed)
    run = await PlacspIngestionService(db, storage).run(
        source_code=CODE, mode="backfill", backfill_until=date(2020, 1, 1)
    )
    assert run.pages_processed == 2
    assert run.records_failed == 1
    assert _balanced(run)
    assert await _tender(db, _PREFIX + "19824604") is not None  # entrada de la pagina 2


# --------------------------------------------------------------------------- #
# Esquema anterior a 0057 (PostgreSQL): falla per longitud, aillada i observable #
# --------------------------------------------------------------------------- #


@pytest.mark.postgres
async def test_esquema_antic_falla_per_longitud_aillat_i_observable(
    engine, session_factory, db, storage, monkeypatch
):
    # Esquema i codi d'abans de 0057: titols VARCHAR(1000) i revisio 0056.
    await _set_title_types(engine, "VARCHAR(1000)")
    assert await _column_type(db, "tender_lots", "title") == ("character varying", 1000)
    await db.rollback()
    monkeypatch.setattr(service_module, "SCHEMA_REVISION", "0056_radar_search")

    run = await _run(db, storage, PAGE9)
    assert run.status == IngestionStatus.PARTIAL
    assert run.records_fetched == 6 and run.records_new == 4 and run.records_failed == 1
    assert run.tombstones_applied == 1 and run.tenders_created == 4
    assert _balanced(run)
    for external_id in GOOD_IDS:
        assert await _tender(db, external_id) is not None
    assert await _tender(db, B_ID) is None

    [failed] = await _records(db, B_ID)
    assert failed.processing_status == ProcessingStatus.FAILED
    assert failed.error_message == (
        "db_data: StringDataRightTruncationError (22001) tender_lots.title"
    )
    raw = await storage.download(failed.raw_storage_key)
    assert compute_sha256(raw) == failed.raw_hash

    assert run.error_summary.splitlines()[0] == (
        "1 db_data (StringDataRightTruncationError 22001, tender_lots.title)"
    )
    source_id = run.source_id
    await sync_module._write_health(  # noqa: SLF001 - el cami real de `last_sync_error`
        session_factory, source_id, started=datetime.now(UTC), run=run, error=None
    )
    async with session_factory() as other:
        last_sync_error = (await other.get(Source, source_id)).last_sync_error
    assert "db_data" in last_sync_error and "StringDataRightTruncation" in last_sync_error
    assert "tender_lots.title" in last_sync_error and len(last_sync_error) <= 200
    for field in (failed.error_message, run.error_summary, last_sync_error):
        for forbidden in FORBIDDEN:
            assert forbidden not in field, (forbidden, field)

    assert failed.failed_schema_revision == "0056_radar_search"

    # Mateix codi i mateix esquema: `db_data` es determinista -> fallada coneguda.
    known = await _run(db, storage, PAGE9)
    assert known.status == IngestionStatus.SUCCESS, known.error_summary
    assert known.records_failed_known == 1 and known.records_failed == 0
    assert known.records_unchanged == 5 and _balanced(known)

    # Amb 0057 (esquema i codi nous), la mateixa publicacio es reintenta i s'aplica.
    await db.rollback()
    await _set_title_types(engine, "TEXT")
    assert await _column_type(db, "tender_lots", "title") == ("text", None)
    await db.rollback()
    monkeypatch.undo()

    fixed = await _run(db, storage, PAGE9)
    assert fixed.status == IngestionStatus.SUCCESS, fixed.error_summary
    assert fixed.records_new == 1 and fixed.records_unchanged == 5 and fixed.records_failed == 0
    assert _balanced(fixed)
    [record] = await _records(db, B_ID)
    assert record.id == failed.id
    assert record.processing_status == ProcessingStatus.PROCESSED
    assert record.error_message is None and record.ingestion_run_id == fixed.id
    assert record.failed_schema_revision is None
    _, expected = _source_names(PAGE9, B_ID)
    assert await _lot_titles(db, B_ID) == expected
    assert await _count(db, Tender) == 5

    revisions = await _count(db, TenderRevision)
    idempotent = await _run(db, storage, PAGE9)
    assert idempotent.status == IngestionStatus.SUCCESS
    assert idempotent.records_unchanged == 6 and idempotent.records_new == 0
    assert await _count(db, TenderRevision) == revisions


# --------------------------------------------------------------------------- #
# Fix round 1: fallades conegudes i reintents per classe                        #
# --------------------------------------------------------------------------- #


async def test_fallada_determinista_es_coneguda_i_no_frena_la_marca_d_aigua(
    db, session_factory, storage, tmp_path, monkeypatch
):
    """Recorregut incremental real (sense xarxa): B amb un lot duplicat a la font.

    Run 1: B `failed`, run `partial`, sense marca d'aigua. Run 2, mateix
    contingut: B es una fallada coneguda, el run es `success` i la marca avanca.
    Run 3 amb un parser nou: B es reintenta (i torna a fallar). La pagina 1 apunta
    a la 2 (2026-10-02): amb marca d'aigua, l'incremental hi arriba al solapament i
    la cadena es completa (sense aixo el run seria `partial` per cadena incompleta).
    """
    page1 = _duplicate_lot_variant(tmp_path).read_text(encoding="utf-8")
    page1 = page1.replace(
        'rel="self"/>', f'rel="self"/>\n    <link href="{PAGE2_URL}" rel="next"/>', 1
    )
    _install(monkeypatch, Feed(page1=page1.encode("utf-8")))

    first = await PlacspIngestionService(db, storage).run(source_code=CODE, mode="incremental")
    assert first.status == IngestionStatus.PARTIAL
    assert first.records_failed == 1 and first.records_failed_known == 0
    assert _balanced(first)
    assert (await get_source(db)).updated_watermark is None
    [failed] = await _records(db, B_ID)
    assert failed.error_message.startswith("integrity_constraint: ")
    assert failed.failed_schema_revision == service_module.SCHEMA_REVISION
    assert failed.parser_version == parser_module.PARSER_VERSION

    second = await PlacspIngestionService(db, storage).run(source_code=CODE, mode="incremental")
    assert second.status == IngestionStatus.SUCCESS, second.error_summary
    assert second.records_failed == 0 and second.records_failed_known == 1
    # A, C1-C3 i la lapida (pagina 1) + les 2 entrades de la pagina 2.
    assert second.records_unchanged == 7 and _balanced(second)
    watermark = as_utc((await get_source(db)).updated_watermark)
    assert watermark is not None  # la fallada coneguda no la frena
    assert second.error_summary == "1 fallades conegudes (integrity_constraint)"
    await sync_module._write_health(  # noqa: SLF001
        session_factory, second.source_id, started=datetime.now(UTC), run=second, error=None
    )
    async with session_factory() as other:
        health = await other.get(Source, second.source_id)
        assert health.last_sync_status == "success"
        assert health.last_sync_error == "1 fallades conegudes (integrity_constraint)"
    [still] = await _records(db, B_ID)
    assert still.processing_status == ProcessingStatus.FAILED and still.id == failed.id

    # Un parser nou pot llegir-la d'una altra manera: es torna a provar.
    monkeypatch.setattr(parser_module, "PARSER_VERSION", "placsp-v2-test-bump")
    third = await PlacspIngestionService(db, storage).run(source_code=CODE, mode="incremental")
    assert third.records_failed == 1 and third.records_failed_known == 0
    assert third.status == IngestionStatus.PARTIAL and _balanced(third)
    [retried] = await _records(db, B_ID)
    assert retried.id == failed.id and retried.parser_version == "placsp-v2-test-bump"
    assert as_utc((await get_source(db)).updated_watermark) == watermark

    # I amb aquest parser ja es coneguda.
    fourth = await PlacspIngestionService(db, storage).run(source_code=CODE, mode="incremental")
    assert fourth.status == IngestionStatus.SUCCESS, fourth.error_summary
    assert fourth.records_failed_known == 1 and fourth.records_failed == 0


async def test_una_excepcio_del_normalitzador_es_parse_validation_i_no_es_reintenta(
    db, storage, monkeypatch
):
    """S1: qualsevol excepcio en llegir o normalitzar una entrada es de dades de la font."""
    original = service_module.normalize

    def broken(parsed):
        if parsed.external_id == B_ID:
            raise ValueError(f"import impossible a {B_ID}")  # el missatge no es copia
        return original(parsed)

    monkeypatch.setattr(service_module, "normalize", broken)
    first = await _run(db, storage, PAGE9)
    assert first.status == IngestionStatus.PARTIAL
    assert first.records_failed == 1 and _balanced(first)
    [failed] = await _records(db, B_ID)
    assert failed.processing_status == ProcessingStatus.FAILED
    assert failed.error_message == "parse_validation: ValueError"
    assert first.error_summary.splitlines()[0] == "1 parse_validation (ValueError)"
    assert await _tender(db, B_ID) is None

    second = await _run(db, storage, PAGE9)
    assert second.status == IngestionStatus.SUCCESS, second.error_summary
    assert second.records_failed == 0 and second.records_failed_known == 1
    assert second.records_unchanged == 5 and _balanced(second)
    assert second.error_summary == "1 fallades conegudes (parse_validation)"
    [still] = await _records(db, B_ID)
    assert still.id == failed.id and still.processing_status == ProcessingStatus.FAILED
