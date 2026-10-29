"""Client HTTP del portal de la PSCP: interruptor, host, reintents, pausa, limit i registre."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.ingestion.pscp_portal import PORTAL_API
from app.ingestion.pscp_portal import client as portal_client
from app.ingestion.pscp_portal.client import (
    PortalClient,
    PortalDisabledError,
    PortalFetchError,
    PortalNotFoundError,
    RequestBudgetExceeded,
)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "PSCP_PORTAL_ENABLED", True)


@pytest.fixture
def sleeps(monkeypatch):
    """Les esperes del client, sense esperar de debo."""
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(portal_client, "_sleep", fake_sleep)
    return waited


def transport(responses):
    """Respon per ordre; recorda cada peticio (URL i capcaleres)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = responses[min(len(seen), len(responses)) - 1]
        return item if isinstance(item, httpx.Response) else item(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_the_switch_off_opens_nothing(monkeypatch):
    monkeypatch.setattr(settings, "PSCP_PORTAL_ENABLED", False)
    http, seen = transport([httpx.Response(200, json={})])
    with pytest.raises(PortalDisabledError):
        async with PortalClient(client=http):
            pass
    assert seen == []


async def test_without_an_injected_transport_the_tests_never_touch_the_network():
    with pytest.raises(RuntimeError, match="Xarxa desactivada"):
        async with PortalClient():
            pass


async def test_a_get_returns_the_decoded_json_and_logs_it(sleeps):
    http, seen = transport([httpx.Response(200, json={"comptadorAvisos": 0})])
    async with PortalClient(client=http, pause_seconds=0) as client:
        data = await client.get_json(
            "detall-publicacio-expedient/tauler-avisos/x", purpose="tauler"
        )

    assert data == {"comptadorAvisos": 0}
    assert str(seen[0].url) == f"{PORTAL_API}/detall-publicacio-expedient/tauler-avisos/x"
    assert seen[0].method == "GET"
    entry = client.log[0]
    assert entry["path"] == "detall-publicacio-expedient/tauler-avisos/x"
    assert entry["status"] == 200 and entry["purpose"] == "tauler" and len(entry["sha256"]) == 64
    assert client.requests_made == 1


async def test_the_default_client_identifies_itself(monkeypatch):
    """Sense transport injectat, el client propi porta el User-Agent de la plataforma."""
    monkeypatch.delenv("TUVI_TESTS_NO_NETWORK", raising=False)
    created: list[dict] = []
    real = httpx.AsyncClient

    def spy(**kwargs):
        created.append(kwargs)
        return real(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))

    monkeypatch.setattr(portal_client.httpx, "AsyncClient", spy)
    async with PortalClient(pause_seconds=0) as client:
        assert await client.get_json("respostes/x") == []
    headers = created[0]["headers"]
    assert headers["User-Agent"].startswith("LicitacionsBot/")
    assert created[0]["follow_redirects"] is False


@pytest.mark.parametrize("path", ["../secret", "https://evil.example/x", "x/../../y"])
async def test_only_the_public_api_can_be_queried(path):
    http, seen = transport([httpx.Response(200, json={})])
    async with PortalClient(client=http, pause_seconds=0) as client:
        with pytest.raises(PortalFetchError):
            await client.get_json(path)
    assert seen == []


async def test_a_busy_portal_is_retried_with_its_retry_after(sleeps):
    http, seen = transport(
        [
            httpx.Response(503, headers={"Retry-After": "7"}),
            httpx.Response(429),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    async with PortalClient(client=http, pause_seconds=0, max_attempts=4) as client:
        assert await client.get_json("respostes/x") == {"ok": True}

    assert len(seen) == 3
    assert sleeps[:2] == [7.0, 8.0]  # Retry-After i, despres, l'espera creixent (2 * 2**2)
    assert [entry["status"] for entry in client.log] == [503, 429, 200]


async def test_a_network_error_is_retried_and_then_reported(sleeps):
    def broken(request):
        raise httpx.ConnectError("caigut", request=request)

    http, seen = transport([broken])
    async with PortalClient(client=http, pause_seconds=0, max_attempts=3) as client:
        with pytest.raises(PortalFetchError, match="ConnectError"):
            await client.get_json("respostes/x")
    assert len(seen) == 3
    assert len(sleeps) == 2


async def test_a_not_found_is_not_retried_and_says_what(sleeps):
    http, seen = transport([httpx.Response(404)])
    async with PortalClient(client=http, pause_seconds=0) as client:
        with pytest.raises(PortalNotFoundError) as raised:
            await client.get_json("detall-avis/resposta/1")
    assert raised.value.path == "detall-avis/resposta/1"
    assert len(seen) == 1 and sleeps == []
    assert client.log[0]["status"] == 404


async def test_an_oversized_answer_is_cut_while_it_streams(monkeypatch):
    monkeypatch.setattr(portal_client, "MAX_JSON_BYTES", 1024)
    declared, _ = transport([httpx.Response(200, content=b"[" + b"0," * 800 + b"0]")])
    async with PortalClient(client=declared, pause_seconds=0) as client:
        with pytest.raises(PortalFetchError, match="mida"):
            await client.get_json("respostes/x")

    async def chunks():
        for _ in range(10):
            yield b" " * 200

    streamed, _ = transport([httpx.Response(200, content=chunks())])
    async with PortalClient(client=streamed, pause_seconds=0) as client:
        with pytest.raises(PortalFetchError, match="mida"):
            await client.get_json("respostes/x")
    assert client.log[-1]["error"] == "too_large"


async def test_deeply_nested_json_is_refused_cleanly():
    nested = b"[" * 100_000 + b"]" * 100_000
    http, _ = transport([httpx.Response(200, content=nested)])
    async with PortalClient(client=http, pause_seconds=0) as client:
        with pytest.raises(PortalFetchError, match="JSON"):
            await client.get_json("respostes/x")


async def test_the_budget_grows_with_what_the_board_announces(sleeps):
    http, seen = transport([httpx.Response(200, json={})])
    async with PortalClient(client=http, pause_seconds=0, max_requests=1) as client:
        await client.get_json("a")
        client.allow(2)
        await client.get_json("b")
        await client.get_json("c")
        with pytest.raises(RequestBudgetExceeded):
            await client.get_json("d")
    assert len(seen) == 3


async def test_an_answer_that_is_not_json_is_refused():
    http, _ = transport([httpx.Response(200, content=b"<html>manteniment</html>")])
    async with PortalClient(client=http, pause_seconds=0) as client:
        with pytest.raises(PortalFetchError, match="JSON"):
            await client.get_json("respostes/x")


async def test_the_request_budget_stops_the_sync(sleeps):
    http, seen = transport([httpx.Response(200, json={})])
    async with PortalClient(client=http, pause_seconds=0, max_requests=2) as client:
        await client.get_json("a")
        await client.get_json("b")
        with pytest.raises(RequestBudgetExceeded):
            await client.get_json("c")
    assert len(seen) == 2


async def test_requests_and_downloads_share_one_cadence(sleeps):
    http, _ = transport([httpx.Response(200, json={})])
    async with PortalClient(client=http, pause_seconds=5) as client:
        await client.get_json("a")
        await client.get_json("b")
        await client.wait_turn()
        client.mark()
        await client.wait_turn()

    assert len(sleeps) == 3
    assert all(0 < seconds <= 5 for seconds in sleeps)
