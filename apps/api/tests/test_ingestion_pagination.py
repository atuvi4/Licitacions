"""Paginacio de la ingesta agregada: solapament, `partial` i backfill acotat.

Tot sense xarxa: un `MockTransport` serveix `page1.atom` (amb `rel=next`) i
`page2.atom` (3 dies mes antiga, sense `next`).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from app.ingestion.placsp import client as client_module
from app.ingestion.placsp import service as service_module
from app.ingestion.placsp.client import PlacspClient
from app.ingestion.placsp.service import PlacspIngestionService
from app.ingestion.sources import SOURCES
from app.models.enums import IngestionStatus
from app.models.sources import Source
from app.models.tenders import Tender, TenderDocument, TenderLot

FIXTURES = Path(__file__).parent / "fixtures" / "placsp" / "agregadas"
CODE = "placsp_agregadas"
PAGE1 = (FIXTURES / "page1.atom").read_bytes()
PAGE2 = (FIXTURES / "page2.atom").read_bytes()
PAGE2_URL = re.search(rb'href="([^"]+)" rel="next"', PAGE1).group(1).decode()
# L'`updated` mes recent de la pagina 1 (2026-10-05T18:01:42.188+02:00) en UTC.
MAX_UPDATED = datetime(2026, 10, 5, 16, 1, 42, 188000, tzinfo=UTC)
PAGE1_ITEMS = 3  # 2 entrades + 1 lapida


def _without_next(content: bytes) -> bytes:
    return re.sub(rb'<link href="[^"]+" rel="next"/>', b"", content)


class Feed:
    """Transport de proves: serveix pagines per URL i registra les peticions."""

    def __init__(
        self, *, page1: bytes = PAGE1, page2: bytes = PAGE2, page2_status: int = 200
    ) -> None:
        self.page1 = page1
        self.page2 = page2
        self.page2_status = page2_status
        self.urls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.urls.append(url)
        if url == PAGE2_URL:
            if self.page2_status != 200:
                return httpx.Response(self.page2_status)
            return httpx.Response(200, content=self.page2)
        return httpx.Response(200, content=self.page1)

    def reset(self) -> None:
        self.urls.clear()


def _install(monkeypatch, feed: Feed) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(client_module, "_sleep", no_sleep)
    transport = httpx.MockTransport(feed.handler)
    monkeypatch.setattr(
        service_module,
        "PlacspClient",
        lambda: PlacspClient(client=httpx.AsyncClient(transport=transport), pause_seconds=0),
    )


@pytest.fixture
def two_pages(monkeypatch) -> Feed:
    feed = Feed()
    _install(monkeypatch, feed)
    return feed


@pytest.fixture
def two_pages_second_fails(monkeypatch) -> Feed:
    feed = Feed(page2_status=500)
    _install(monkeypatch, feed)
    return feed


@pytest.fixture
def page_without_next(monkeypatch) -> Feed:
    feed = Feed(page1=_without_next(PAGE1))
    _install(monkeypatch, feed)
    return feed


async def get_source(db) -> Source:
    await db.rollback()
    return (await db.execute(sa.select(Source).where(Source.code == CODE))).scalar_one()


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def test_incremental_processa_totes_les_entrades_visitades_i_s_atura_pel_solapament(
    db, storage, two_pages
):
    svc = PlacspIngestionService(db, storage)
    r1 = await svc.run(source_code=CODE, mode="incremental")
    assert r1.status == IngestionStatus.SUCCESS and r1.pages_processed == 2
    assert r1.records_fetched == 5  # 2 + 1 lapida + 2
    assert as_utc((await get_source(db)).updated_watermark) == MAX_UPDATED

    two_pages.reset()
    r2 = await svc.run(source_code=CODE, mode="incremental")
    assert r2.status == IngestionStatus.SUCCESS
    assert r2.records_unchanged == PAGE1_ITEMS and r2.pages_processed == 1
    # La pagina 2 (< marca - 48 h) es llegeix per comprovar-ho, pero no es processa.
    assert as_utc((await get_source(db)).updated_watermark) == MAX_UPDATED


async def test_pagina_fallida_es_partial_i_no_deriva_absencies(db, storage, two_pages):
    svc = PlacspIngestionService(db, storage)
    r1 = await svc.run(source_code=CODE, mode="incremental")
    assert r1.status == IngestionStatus.SUCCESS
    before = as_utc((await get_source(db)).updated_watermark)
    checkpoint = (await get_source(db)).checkpoint
    tenders = (await db.execute(sa.select(sa.func.count()).select_from(Tender))).scalar_one()
    documents = (await db.execute(sa.select(TenderDocument))).scalars().all()
    assert documents

    # Segon run amb marca desada: pagina 1 sencera + pagina 2 que falla (500 x3).
    two_pages.page2_status = 500
    watermark = await get_source(db)
    watermark.updated_watermark = before - timedelta(days=5)  # forca a visitar la pagina 2
    await db.commit()
    r = await svc.run(source_code=CODE, mode="incremental")
    assert r.status == IngestionStatus.PARTIAL and r.pages_processed == 1
    src = await get_source(db)
    assert as_utc(src.updated_watermark) == before - timedelta(days=5)
    assert src.checkpoint == checkpoint
    assert r.error_summary and "500" not in r.error_summary and len(r.error_summary) <= 200
    assert (
        await db.execute(sa.select(sa.func.count()).select_from(Tender))
    ).scalar_one() == tenders
    for model in (TenderDocument, TenderLot):
        rows = (await db.execute(sa.select(model))).scalars().all()
        assert all(row.removed_at is None for row in rows)
    assert all(t.source_removed_at is None for t in (await db.execute(sa.select(Tender))).scalars())


async def test_pagina_no_parsejable_es_partial_i_no_compta(db, storage, monkeypatch):
    feed = Feed(page2=b"<no es xml")
    _install(monkeypatch, feed)
    r = await PlacspIngestionService(db, storage).run(source_code=CODE, mode="incremental")
    assert r.status == IngestionStatus.PARTIAL and r.pages_processed == 1
    assert (await get_source(db)).updated_watermark is None
    assert r.error_summary.startswith("pagina 2: ParseError") and len(r.error_summary) <= 200


async def test_el_limit_a_mitja_pagina_no_mou_el_checkpoint_i_es_partial(db, storage, two_pages):
    svc = PlacspIngestionService(db, storage)
    until = date(2020, 1, 1)
    r = await svc.run(source_code=CODE, mode="backfill", backfill_until=until, limit=1)
    base = SOURCES[CODE].feed_url
    assert r.status == IngestionStatus.PARTIAL and r.records_fetched == 1
    assert r.checkpoint_after == base and (await get_source(db)).checkpoint == base
    assert (await get_source(db)).updated_watermark is None
    two_pages.reset()
    r2 = await svc.run(
        source_code=CODE, mode="backfill", backfill_until=until, resume=True, max_pages=1
    )
    assert r2.checkpoint_before == base and two_pages.urls[0] == base


async def test_un_incremental_no_trepitja_el_checkpoint_del_backfill(db, storage, two_pages):
    svc = PlacspIngestionService(db, storage)
    until = date(2020, 1, 1)
    await svc.run(source_code=CODE, mode="backfill", max_pages=1, backfill_until=until)
    await svc.run(source_code=CODE, mode="incremental")
    assert (await get_source(db)).checkpoint == PAGE2_URL
    two_pages.reset()
    r = await svc.run(
        source_code=CODE, mode="backfill", max_pages=1, backfill_until=until, resume=True
    )
    assert r.checkpoint_before == PAGE2_URL and two_pages.urls == [PAGE2_URL]


async def test_les_lapides_recents_eviten_l_aturada_per_solapament(db, storage, monkeypatch):
    feed = Feed()
    _install(monkeypatch, feed)
    svc = PlacspIngestionService(db, storage)
    await svc.run(source_code=CODE, mode="incremental")
    tombstone = (
        b'<at:deleted-entry xmlns:at="http://purl.org/atompub/tombstones/1.0" '
        b'ref="https://example.test/desconeguda" when="2026-10-06T00:00:51.017+02:00">'
        b'<at:comment type="CERRADA"/></at:deleted-entry></feed>'
    )
    feed.page2 = PAGE2.replace(b"</feed>", tombstone)
    r = await svc.run(source_code=CODE, mode="incremental")
    assert r.pages_processed == 2
    assert feed.urls.count(PAGE2_URL) >= 2


async def test_topall_de_pagines_abans_del_solapament_es_partial_i_no_mou_la_marca(
    db, storage, two_pages
):
    """`--max-pages` es un topall de seguretat: no converteix un cataleg incomplet en complet."""
    svc = PlacspIngestionService(db, storage)
    src = await svc.get_or_create_source(CODE)
    old_mark = datetime(2026, 9, 1, tzinfo=UTC)  # solapament molt per sota de la pagina 2
    src.updated_watermark = old_mark
    await db.commit()
    r = await svc.run(source_code=CODE, mode="incremental", max_pages=1)
    assert r.status == IngestionStatus.PARTIAL and r.pages_processed == 1
    assert as_utc((await get_source(db)).updated_watermark) == old_mark


async def test_el_primer_run_sense_marca_la_fixa_amb_el_que_ha_processat(db, storage, two_pages):
    """Sense marca no hi ha solapament a assolir: el topall acaba `success` i fixa la marca."""
    svc = PlacspIngestionService(db, storage)
    r = await svc.run(source_code=CODE, mode="incremental", max_pages=1)
    assert r.status == IngestionStatus.SUCCESS and r.pages_processed == 1
    assert as_utc((await get_source(db)).updated_watermark) == MAX_UPDATED


async def test_next_incomplet_es_partial(db, storage, page_without_next):
    svc = PlacspIngestionService(db, storage)
    src = await svc.get_or_create_source(CODE)
    # Amb marca d'aigua: la cadena s'acaba abans d'arribar al solapament.
    src.updated_watermark = datetime(2026, 10, 1, tzinfo=UTC)
    await db.commit()
    r = await svc.run(source_code=CODE, mode="incremental")
    assert r.status == IngestionStatus.PARTIAL and r.pages_processed == 1
    assert as_utc((await get_source(db)).updated_watermark) == datetime(2026, 10, 1, tzinfo=UTC)


async def test_backfill_acotat_partial_i_resume(db, storage, two_pages):
    svc = PlacspIngestionService(db, storage)
    until = date(2020, 1, 1)
    r = await svc.run(source_code=CODE, mode="backfill", max_pages=1, backfill_until=until)
    assert r.status == IngestionStatus.PARTIAL and r.pages_processed == 1
    assert r.checkpoint_after == PAGE2_URL

    r2 = await svc.run(
        source_code=CODE, mode="backfill", max_pages=1, backfill_until=until, resume=True
    )
    assert r2.checkpoint_before == PAGE2_URL
    assert r2.pages_processed == 1 and r2.status == IngestionStatus.SUCCESS


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "pagina 2: FeedFetchError: feed no disponible",
            "pagina 2: FeedFetchError: feed no disponible",
        ),
        ("FeedFetchError: HTTP 500 a https://x", "FeedFetchError: feed no disponible"),
        (
            "https://contrataciondelestado.es/sindicacion/X/1: ParseError: Entrada sense <id>.",
            "ParseError: Entrada sense <id>.",
        ),
        ("ParseError: Lapida sense ref.", "ParseError: Lapida sense ref."),
        # La classe nomes compta a l'inici: un `ParseError` dins d'un missatge
        # (SQL, parametres) no fa que se'n conservi el text.
        (
            "IntegrityError: (x) INSERT ... VALUES ('ParseError: B12345678')",
            "IntegrityError: error inesperat",
        ),
        ("RuntimeError: ParseError: B12345678", "RuntimeError: error inesperat"),
        ("text sense classe ParseError: B12345678", "Error: error inesperat"),
    ],
)
def test_safe_line_nomes_llegeix_la_classe_a_l_inici(line, expected):
    assert service_module._safe_line(line) == expected
