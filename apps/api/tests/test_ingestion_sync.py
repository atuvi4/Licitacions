"""Sincronitzacio de fonts: lease, salut, prefit post-sync, CLI i salut a l'API.

Sense xarxa: `MockTransport` serveix `page1.atom`/`page2.atom`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa

from app.ingestion import sync as sync_module
from app.ingestion.placsp import client as client_module
from app.ingestion.placsp import service as service_module
from app.ingestion.placsp.client import PlacspClient
from app.ingestion.sync import (
    SourceDisabled,
    SyncAlreadyRunning,
    amain,
    run_source_sync,
)
from app.matching.service import MatchingService
from app.models.enums import IngestionStatus
from app.models.matching import CompanyTenderMatch
from app.models.sources import IngestionRun, Source, SourceRecord
from app.models.tenders import Tender
from tests.conftest import login
from tests.test_ingestion_pagination import Feed
from tests.test_matching_api import configure_company
from tests.test_platform_onboarding import create_operator

CODE = "placsp_agregadas"


class SlowFeed(Feed):
    """Transport asincron que triga: permet que un segon sync arribi a mig run."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    async def ahandler(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(self.delay)
        return self.handler(request)


def _install(monkeypatch, handler) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(client_module, "_sleep", no_sleep)
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        service_module,
        "PlacspClient",
        lambda: PlacspClient(client=httpx.AsyncClient(transport=transport), pause_seconds=0),
    )


@pytest.fixture
def fixture_transport(monkeypatch) -> Feed:
    feed = Feed()
    _install(monkeypatch, feed.handler)
    return feed


@pytest.fixture
def two_pages_second_fails(monkeypatch) -> Feed:
    feed = Feed(page2_status=500)
    _install(monkeypatch, feed.handler)
    return feed


async def get_source(db) -> Source:
    await db.rollback()
    return (
        await db.execute(
            sa.select(Source).where(Source.code == CODE).execution_options(populate_existing=True)
        )
    ).scalar_one()


async def ensure_source(db) -> Source:
    source = await service_module.PlacspIngestionService(db).get_or_create_source(CODE)
    await db.commit()
    return source


@pytest.mark.postgres
async def test_dos_syncs_de_la_mateixa_font_no_corren_alhora(
    session_factory, db, storage, monkeypatch
):
    slow = SlowFeed(0.4)
    _install(monkeypatch, slow.ahandler)
    first = asyncio.create_task(run_source_sync(session_factory, CODE, storage=storage))
    await asyncio.sleep(0.1)
    with pytest.raises(SyncAlreadyRunning):
        await run_source_sync(session_factory, CODE, storage=storage)
    run = await first
    assert run.status == IngestionStatus.SUCCESS
    assert (await get_source(db)).sync_lease_until is None


async def test_lease_vigent_bloqueja_i_caducat_es_pot_prendre(
    session_factory, db, storage, fixture_transport
):
    source = await ensure_source(db)
    source.sync_lease_owner = "altre:1:abc"
    source.sync_lease_until = datetime.now(UTC) + timedelta(minutes=10)
    await db.commit()
    with pytest.raises(SyncAlreadyRunning):
        await run_source_sync(session_factory, CODE, storage=storage)
    assert (await db.execute(sa.select(sa.func.count()).select_from(IngestionRun))).scalar() == 0

    source = await get_source(db)
    source.sync_lease_until = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()
    run = await run_source_sync(session_factory, CODE, storage=storage)
    assert run.status == IngestionStatus.SUCCESS
    src = await get_source(db)
    assert src.sync_lease_owner is None and src.sync_lease_until is None


async def test_salut_de_font_despres_de_success_i_partial(
    session_factory, db, storage, monkeypatch
):
    ok = Feed()
    _install(monkeypatch, ok.handler)
    await run_source_sync(session_factory, CODE, storage=storage)
    src = await get_source(db)
    assert src.last_sync_status == "success"
    assert src.last_sync_pages == 2 and src.last_sync_entries > 0
    assert src.sync_lease_until is None and src.last_sync_error is None
    assert src.last_sync_started_at is not None and src.last_sync_finished_at is not None
    assert src.last_feed_updated_at is not None

    broken = Feed(page2_status=500)
    _install(monkeypatch, broken.handler)
    await run_source_sync(session_factory, CODE, mode="backfill", storage=storage)
    src = await get_source(db)
    assert src.last_sync_status == "partial"
    assert src.last_sync_error and "pagina" in src.last_sync_error.lower()
    assert src.sync_lease_until is None


async def test_font_desactivada_no_sincronitza(session_factory, db, storage, fixture_transport):
    source = await ensure_source(db)
    source.enabled = False
    await db.commit()
    with pytest.raises(SourceDisabled):
        await run_source_sync(session_factory, CODE, storage=storage)
    assert (await db.execute(sa.select(sa.func.count()).select_from(IngestionRun))).scalar() == 0
    assert fixture_transport.urls == []
    assert (await get_source(db)).sync_lease_until is None


async def test_prefit_per_als_tenders_canviats(
    session_factory, db, storage, tenant_a, fixture_transport
):
    _, company = tenant_a
    await configure_company(db, company.id)
    await db.commit()
    run = await run_source_sync(session_factory, CODE, storage=storage)
    assert run.tenders_created > 0
    count = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(CompanyTenderMatch)
            .where(CompanyTenderMatch.company_id == company.id)
        )
    ).scalar()
    assert count == run.tenders_created


async def test_cli_codis_de_sortida(monkeypatch, session_factory):
    calls: list[str] = []

    def fake(status: IngestionStatus):
        async def _fake(factory, code, **kwargs):
            calls.append(code)
            return SimpleNamespace(
                status=status,
                id="r",
                pages_processed=1,
                records_fetched=2,
                tombstones_applied=0,
                records_failed=0,
            )

        return _fake

    for status, expected in (
        (IngestionStatus.SUCCESS, 0),
        (IngestionStatus.PARTIAL, 2),
        (IngestionStatus.FAILED, 1),
    ):
        monkeypatch.setattr(sync_module, "run_source_sync", fake(status))
        assert await amain(["--source", CODE], session_factory=session_factory) == expected

    async def busy(*a, **k):
        raise SyncAlreadyRunning(CODE)

    monkeypatch.setattr(sync_module, "run_source_sync", busy)
    assert await amain([], session_factory=session_factory) == 1

    # --every: bucle que s'atura amb l'esdeveniment.
    stop = asyncio.Event()
    cycles: list[int] = []

    async def counting(factory, code, **kwargs):
        cycles.append(1)
        if len(cycles) == 2:
            stop.set()
        return SimpleNamespace(
            status=IngestionStatus.SUCCESS,
            id="r",
            pages_processed=1,
            records_fetched=0,
            tombstones_applied=0,
            records_failed=0,
        )

    monkeypatch.setattr(sync_module, "run_source_sync", counting)
    code = await asyncio.wait_for(
        amain(["--every", "0.01"], session_factory=session_factory, stop_event=stop), timeout=5
    )
    assert code == 0 and len(cycles) == 2


async def test_get_platform_sources_mostra_salut(client_factory, session_factory, db, client_a):
    source = await ensure_source(db)
    source.last_sync_status = "partial"
    source.last_sync_pages = 2
    source.last_sync_error = "pagina 2: boom"
    source.sync_lease_until = datetime.now(UTC) + timedelta(minutes=5)
    await db.commit()

    assert (await client_a.get("/api/v1/platform/sources")).status_code == 403

    operator = await create_operator(db)
    http_client = client_factory()
    await login(http_client, operator.email)
    response = await http_client.get("/api/v1/platform/sources")
    assert response.status_code == 200, response.text
    rows = {row["code"]: row for row in response.json()}
    row = rows[CODE]
    assert row["last_sync_status"] == "partial" and row["last_sync_pages"] == 2
    assert row["last_sync_error"] == "pagina 2: boom"
    assert row["lease_active"] is True and row["enabled"] is True


async def test_excepcio_dins_run_allibera_lease_i_no_filtra_detall(
    session_factory, db, storage, monkeypatch
):
    secret = "SELECT * FROM x WHERE nif = 'B12345678' [parameters: ('B12345678',)]"

    async def boom(self, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(service_module.PlacspIngestionService, "run", boom)
    with pytest.raises(RuntimeError):
        await run_source_sync(session_factory, CODE, storage=storage)
    src = await get_source(db)
    assert src.sync_lease_until is None and src.sync_lease_owner is None
    assert src.last_sync_status == "failed"
    assert src.last_sync_error.startswith("RuntimeError")
    assert "SELECT" not in src.last_sync_error and "B12345678" not in src.last_sync_error
    assert len(src.last_sync_error) <= 200


def _empty_run() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=IngestionStatus.SUCCESS,
        pages_processed=0,
        records_fetched=0,
        tombstones_applied=0,
        records_failed=0,
        error_summary=None,
    )


async def _lease_until(session_factory) -> datetime:
    async with session_factory() as other:
        until = (
            await other.execute(sa.select(Source.sync_lease_until).where(Source.code == CODE))
        ).scalar_one()
    return until if until.tzinfo else until.replace(tzinfo=UTC)


async def test_heartbeat_renova_el_lease(session_factory, db, storage, monkeypatch):
    """El batec renova el lease mentre corre el run (s'espera el batec, no un temps)."""
    seen: list[datetime] = []
    in_run = False
    beat_done = asyncio.Event()
    original_renew = sync_module._renew_lease

    async def spy_renew(factory, source_id, owner):
        await original_renew(factory, source_id, owner)
        if in_run:
            beat_done.set()

    async def slow_run(self, **kwargs):
        nonlocal in_run
        seen.append(await _lease_until(session_factory))
        in_run = True
        await asyncio.wait_for(beat_done.wait(), timeout=10)
        in_run = False
        seen.append(await _lease_until(session_factory))
        return _empty_run()

    monkeypatch.setattr(sync_module, "_renew_lease", spy_renew)
    monkeypatch.setattr(service_module.PlacspIngestionService, "run", slow_run)
    await run_source_sync(session_factory, CODE, storage=storage, heartbeat_seconds=0.2)
    assert seen[1] > seen[0]
    assert (await get_source(db)).sync_lease_until is None


async def test_el_batec_no_es_cancella_a_mig_query(session_factory, db, storage, monkeypatch):
    """`run_source_sync` espera que el batec acabi la renovacio abans d'alliberar el lease."""
    events: list[str] = []
    in_run = False
    beat_started = asyncio.Event()
    original_renew = sync_module._renew_lease
    original_release = sync_module._release_lease

    async def slow_renew(factory, source_id, owner):
        if not in_run:
            await original_renew(factory, source_id, owner)
            return
        events.append("renew_start")
        beat_started.set()
        await asyncio.sleep(0.3)  # un query del batec encara en curs quan el run acaba
        await original_renew(factory, source_id, owner)
        events.append("renew_end")

    async def spy_release(factory, source_id, owner):
        events.append("release")
        await original_release(factory, source_id, owner)

    async def short_run(self, **kwargs):
        nonlocal in_run
        in_run = True
        await asyncio.wait_for(beat_started.wait(), timeout=10)
        in_run = False
        return _empty_run()

    monkeypatch.setattr(sync_module, "_renew_lease", slow_renew)
    monkeypatch.setattr(sync_module, "_release_lease", spy_release)
    monkeypatch.setattr(service_module.PlacspIngestionService, "run", short_run)
    await run_source_sync(session_factory, CODE, storage=storage, heartbeat_seconds=0.2)
    assert events == ["renew_start", "renew_end", "release"]
    src = await get_source(db)
    assert src.sync_lease_until is None and src.sync_lease_owner is None


async def test_prefit_nomes_per_als_tenders_del_run(
    session_factory, db, storage, tenant_a, monkeypatch
):
    _, company_a = tenant_a
    await configure_company(db, company_a.id)
    source = await ensure_source(db)
    tender_a = Tender(source_id=source.id, external_id="A", title="A")
    tender_b = Tender(source_id=source.id, external_id="B", title="B")
    db.add_all([tender_a, tender_b])
    await db.commit()
    ids = (tender_a.id, tender_b.id)
    company_id = company_a.id

    async def fake_run(self, **kwargs):
        run = IngestionRun(source_id=source.id, status=IngestionStatus.SUCCESS)
        self.session.add(run)
        await self.session.flush()
        self.session.add(
            SourceRecord(
                source_id=source.id,
                external_id="A",
                raw_hash="h" * 64,
                raw_storage_key="k",
                parser_version="placsp-v2",
                ingestion_run_id=run.id,
                tender_id=ids[0],
            )
        )
        await self.session.commit()
        return run

    calls: list = []
    original = MatchingService.recalculate

    async def spy(self, cid, *, tender_ids=None, now=None):
        calls.append((cid, tender_ids))
        return await original(self, cid, tender_ids=tender_ids, now=now)

    monkeypatch.setattr(service_module.PlacspIngestionService, "run", fake_run)
    monkeypatch.setattr(MatchingService, "recalculate", spy)
    await run_source_sync(session_factory, CODE, storage=storage)
    assert calls == [(company_id, [ids[0]])]
    matched = set((await db.execute(sa.select(CompanyTenderMatch.tender_id))).scalars().all())
    assert matched == {ids[0]}


async def test_un_error_de_prefit_deixa_el_sync_success(
    session_factory, db, storage, tenant_a, fixture_transport, monkeypatch
):
    _, company = tenant_a
    await configure_company(db, company.id)
    await db.commit()

    async def broken(self, *args, **kwargs):
        raise RuntimeError("prefit trencat")

    monkeypatch.setattr(MatchingService, "recalculate", broken)
    run = await run_source_sync(session_factory, CODE, storage=storage)
    assert run.status == IngestionStatus.SUCCESS and run.tenders_created > 0
    src = await get_source(db)
    assert src.last_sync_status == "success" and src.last_sync_error is None
    assert src.sync_lease_until is None


# --------------------------------------------------------------------------- #
# Onada final: salut que no retrocedeix, --resume, CLI antiga i --status         #
# --------------------------------------------------------------------------- #


async def test_last_feed_updated_at_nomes_avanca(
    session_factory, db, storage, two_pages_second_fails
):
    """Un run `partial` amb dades mes antigues no fa retrocedir l'`updated` del feed."""
    later = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    source = await ensure_source(db)
    source.last_feed_updated_at = later
    await db.commit()

    run = await run_source_sync(session_factory, CODE, mode="backfill", storage=storage)
    assert run.status == IngestionStatus.PARTIAL and run.records_new > 0
    src = await get_source(db)
    seen = src.last_feed_updated_at
    assert (seen if seen.tzinfo else seen.replace(tzinfo=UTC)) == later
    assert src.last_sync_status == "partial"


async def test_resume_sense_backfill_until_es_rebutja(
    session_factory, db, storage, fixture_transport
):
    with pytest.raises(ValueError, match="--resume requereix --backfill-until"):
        await run_source_sync(session_factory, CODE, resume=True, storage=storage)
    assert (await db.execute(sa.select(sa.func.count()).select_from(IngestionRun))).scalar() == 0

    with pytest.raises(SystemExit) as exc:
        await amain(["--resume"], session_factory=session_factory)
    assert exc.value.code == 2

    from app.ingestion.placsp import __main__ as placsp_cli

    for argv in (["--resume"], ["--resume", "--fixture", "x.atom"]):
        with pytest.raises(SystemExit) as exc:
            await placsp_cli.amain(argv, session_factory=session_factory)
        assert exc.value.code == 2


async def test_cli_antiga_contra_el_feed_viu_passa_pel_sync_amb_lease(
    session_factory, monkeypatch, capsys
):
    from app.ingestion.placsp import __main__ as placsp_cli

    calls: list[tuple[str, dict]] = []

    async def spy(factory, code, **kwargs):
        calls.append((code, kwargs))
        return SimpleNamespace(
            status=IngestionStatus.PARTIAL,
            id="r",
            duration_ms=1,
            pages_processed=2,
            records_fetched=3,
            records_new=1,
            records_changed=0,
            records_unchanged=2,
            records_failed=0,
            tenders_created=1,
            tenders_updated=0,
            checkpoint_after=None,
            error_summary=None,
        )

    monkeypatch.setattr(sync_module, "run_source_sync", spy)
    code = await placsp_cli.amain(
        ["--source", CODE, "--max-pages", "2", "--limit", "5"], session_factory=session_factory
    )
    assert code == 2
    assert [c for c, _ in calls] == [CODE]
    kwargs = calls[0][1]
    assert kwargs["mode"] == "incremental" and kwargs["max_pages"] == 2
    assert kwargs["limit"] == 5 and kwargs["resume"] is False and kwargs["dry_run"] is False
    assert kwargs["feed_url"] is None

    # Backfill amb represa: tambe pel sync.
    calls.clear()
    await placsp_cli.amain(
        ["--source", CODE, "--backfill-until", "2026-09-01", "--resume"],
        session_factory=session_factory,
    )
    assert calls[0][1]["mode"] == "backfill" and calls[0][1]["resume"] is True

    async def busy(*a, **k):
        raise SyncAlreadyRunning(CODE)

    monkeypatch.setattr(sync_module, "run_source_sync", busy)
    assert await placsp_cli.amain(["--source", CODE], session_factory=session_factory) == 1
    assert "ja en curs" in capsys.readouterr().err


async def test_cli_antiga_feed_url_nomes_en_dry_run(
    session_factory, db, storage, fixture_transport
):
    from app.ingestion.placsp import __main__ as placsp_cli

    with pytest.raises(SystemExit) as exc:
        await placsp_cli.amain(
            ["--source", CODE, "--feed-url", "https://example.com/feed.atom"],
            session_factory=session_factory,
        )
    assert exc.value.code == 2
    with pytest.raises(ValueError, match="--feed-url"):
        await run_source_sync(
            session_factory, CODE, feed_url="https://example.com/feed.atom", storage=storage
        )


async def test_cli_antiga_amb_fixture_no_pren_lease(session_factory, db, storage, monkeypatch):
    from app.ingestion.placsp import __main__ as placsp_cli

    async def never(*a, **k):
        raise AssertionError("una fixture no passa pel sync")

    monkeypatch.setattr(sync_module, "run_source_sync", never)
    fixture = Path(__file__).parent / "fixtures" / "placsp" / "agregadas" / "page2.atom"
    code = await placsp_cli.amain(
        ["--source", CODE, "--fixture", str(fixture)],
        session_factory=session_factory,
        storage=storage,
    )
    assert code == 0
    src = await get_source(db)
    assert src.last_sync_status is None and src.sync_lease_until is None


async def test_status_imprimeix_la_salut_de_cada_font(session_factory, db, capsys):
    source = await ensure_source(db)
    source.last_sync_status = "partial"
    source.last_sync_started_at = datetime(2026, 10, 6, 13, 0, tzinfo=UTC)
    source.last_sync_finished_at = datetime(2026, 10, 6, 13, 1, tzinfo=UTC)
    source.last_sync_pages = 2
    source.last_sync_entries = 802
    source.last_sync_tombstones = 1
    source.last_sync_error = "pagina 2 fallida"
    source.last_feed_updated_at = datetime(2026, 10, 5, 23, 41, 9, tzinfo=UTC)
    source.updated_watermark = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
    source.sync_lease_owner = "host:123:abcd1234"
    source.sync_lease_until = datetime.now(UTC) + timedelta(minutes=5)
    await db.commit()

    assert await amain(["--status", "--source", CODE], session_factory=session_factory) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith(CODE)
    expected = {
        "activa": "si",
        "last_sync_status": "partial",
        "last_sync_started_at": "2026-10-06T13:00:00+00:00",
        "last_sync_finished_at": "2026-10-06T13:01:00+00:00",
        "last_sync_pages": "2",
        "last_sync_entries": "802",
        "last_sync_tombstones": "1",
        "last_sync_error": "pagina 2 fallida",
        "last_feed_updated_at": "2026-10-05T23:41:09+00:00",
        "updated_watermark": "2026-10-05T22:00:00+00:00",
        "sync_lease_owner": "host:123:abcd1234",
    }
    values = {
        key.strip(): value.strip()
        for key, _, value in (line.partition(":") for line in lines[1:] if ":" in line)
    }
    for key, value in expected.items():
        assert values[key] == value, key
    assert values["lease"] == "actiu"
    assert "sync_lease_until" in values
    for forbidden in ("postgresql", "sqlite", "@", "password"):
        assert forbidden not in out.lower()

    # Sense --source: totes les fonts registrades a la base de dades.
    assert await amain(["--status"], session_factory=session_factory) == 0
    assert CODE in capsys.readouterr().out
