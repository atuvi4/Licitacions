"""Client HTTP de PLACSP: condicionals, Retry-After i reintents."""

from __future__ import annotations

import httpx
import pytest

from app.ingestion.placsp import client as client_module
from app.ingestion.placsp.client import FeedUnavailable, PlacspClient

URL = "https://example.test/feed.atom"


@pytest.fixture
def transport_factory():
    def make(responses):
        calls: list[httpx.Request] = []
        queue = list(responses)

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            status, headers, body = queue.pop(0)
            return httpx.Response(status, headers=headers, content=body)

        return httpx.MockTransport(handler), calls

    return make


@pytest.fixture
def sleep_spy(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    return slept


def make_client(transport, **kwargs) -> PlacspClient:
    return PlacspClient(client=httpx.AsyncClient(transport=transport), pause_seconds=0, **kwargs)


async def test_sense_transport_injectat_els_tests_no_surten_a_la_xarxa(monkeypatch):
    """`tests/conftest.py` posa `TUVI_TESTS_NO_NETWORK=1`: el cami real es refusa."""
    import os
    import socket

    assert os.environ.get("TUVI_TESTS_NO_NETWORK") == "1"
    touched: list[str] = []

    def forbidden(*args, **kwargs):
        touched.append("socket")
        raise AssertionError("cap socket als tests")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)

    with pytest.raises(RuntimeError, match="Xarxa desactivada als tests: injecta un transport"):
        async with PlacspClient(pause_seconds=0) as client:
            await client.fetch(URL)
    assert touched == []

    # Sense la variable, el client per defecte es construeix (no es crida `fetch`).
    monkeypatch.delenv("TUVI_TESTS_NO_NETWORK")
    async with PlacspClient(pause_seconds=0) as client:
        assert client._client is not None  # noqa: SLF001
    assert touched == []


async def test_304_no_torna_cos(transport_factory):
    transport, calls = transport_factory([(304, {"ETag": '"abc"'}, b"")])
    result = await make_client(transport).fetch(URL, etag='"abc"')
    assert result.not_modified and result.body is None
    assert calls[0].headers["If-None-Match"] == '"abc"'


async def test_last_modified_s_envia_com_if_modified_since(transport_factory):
    transport, calls = transport_factory([(304, {}, b"")])
    stamp = "Tue, 06 Oct 2026 10:00:00 GMT"
    result = await make_client(transport).fetch(URL, last_modified=stamp)
    assert result.not_modified
    assert calls[0].headers["If-Modified-Since"] == stamp


async def test_200_retorna_cos_i_validadors(transport_factory):
    transport, calls = transport_factory(
        [(200, {"ETag": '"v2"', "Last-Modified": "Tue, 06 Oct 2026 10:00:00 GMT"}, b"<feed/>")]
    )
    result = await make_client(transport).fetch(URL)
    assert result.status == 200 and result.body == b"<feed/>" and not result.not_modified
    assert result.etag == '"v2"' and result.last_modified == "Tue, 06 Oct 2026 10:00:00 GMT"
    assert "If-None-Match" not in calls[0].headers


async def test_retry_after_es_respecta_i_despres_200(transport_factory, sleep_spy):
    transport, _ = transport_factory([(503, {"Retry-After": "2"}, b""), (200, {}, b"<feed/>")])
    result = await make_client(transport).fetch(URL)
    assert result.status == 200 and 2.0 in sleep_spy


async def test_retry_after_gran_es_acota_a_60(transport_factory, sleep_spy):
    transport, _ = transport_factory([(429, {"Retry-After": "3600"}, b""), (200, {}, b"x")])
    await make_client(transport).fetch(URL)
    assert sleep_spy == [60.0]


async def test_retry_after_com_data_http(transport_factory, sleep_spy):
    transport, _ = transport_factory(
        [(503, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, b""), (200, {}, b"x")]
    )
    await make_client(transport).fetch(URL)
    # Data passada: no espera mes que el minim, mai negatiu.
    assert len(sleep_spy) == 1 and 0.0 <= sleep_spy[0] <= 60.0


async def test_tres_fallades_aixequen_error(transport_factory, sleep_spy):
    transport, calls = transport_factory([(500, {}, b"")] * 3)
    with pytest.raises(FeedUnavailable):
        await make_client(transport).fetch(URL)
    assert len(calls) == 3


async def test_304_no_es_reintenta(transport_factory, sleep_spy):
    transport, calls = transport_factory([(304, {}, b"")])
    await make_client(transport).fetch(URL, etag='"a"')
    assert len(calls) == 1 and sleep_spy == []
