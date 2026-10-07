"""El downloader no pot convertir-se en una SSRF primitive.

Cap d'aquests tests toca la xarxa: el resolutor de DNS s'injecta.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.url_guard import BlockedURLError, is_blocked_address, validate_url

PUBLIC_IP = "93.184.216.34"

_STANDALONE = """
import asyncio, sys
assert "anyio.to_thread" not in sys.modules
from app.services.url_guard import validate_url
resolver = lambda h, p: ["93.184.216.34"]
result = asyncio.run(validate_url("https://contractaciopublica.cat/plec.pdf", resolver=resolver))
print(result.addresses[0])
"""


def test_validate_url_works_in_a_process_that_never_imported_anyio_to_thread():
    """Un script sol (sense API ni worker) no depen d'una importacio transitiva."""
    api_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(api_root), "LLM_PROVIDER": "fake"}
    completed = subprocess.run(
        [sys.executable, "-c", _STANDALONE],
        cwd=api_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip() == PUBLIC_IP


def resolver_returning(*addresses: str):
    """Resolutor fals: ignora el host i retorna sempre aquestes adreces."""

    def _resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return _resolve


async def test_accepts_a_public_https_url():
    result = await validate_url(
        "https://contrataciondelestado.es/plec.pdf", resolver=resolver_returning(PUBLIC_IP)
    )

    assert result.host == "contrataciondelestado.es"
    assert result.port == 443
    assert result.addresses == (PUBLIC_IP,)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/plec.pdf",
        "gopher://example.com/",
        "data:application/pdf;base64,AAAA",
    ],
)
async def test_rejects_non_http_schemes(url: str):
    with pytest.raises(BlockedURLError) as caught:
        await validate_url(url, resolver=resolver_returning(PUBLIC_IP))
    assert caught.value.code == "invalid_scheme"


async def test_rejects_embedded_credentials():
    with pytest.raises(BlockedURLError) as caught:
        await validate_url(
            "https://user:pass@example.com/plec.pdf", resolver=resolver_returning(PUBLIC_IP)
        )
    assert caught.value.code == "blocked_url"


async def test_rejects_unexpected_ports():
    with pytest.raises(BlockedURLError) as caught:
        await validate_url(
            "http://example.com:8080/plec.pdf", resolver=resolver_returning(PUBLIC_IP)
        )
    assert caught.value.code == "blocked_port"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",          # loopback
        "10.0.0.5",           # privada
        "172.16.3.9",         # privada
        "192.168.1.10",       # privada
        "169.254.169.254",    # metadata de cloud
        "100.64.0.1",         # CGNAT
        "0.0.0.0",            # unspecified
        "224.0.0.1",          # multicast
        "::1",                # loopback IPv6
        "fd00::1",            # ULA IPv6
        "fe80::1",            # link-local IPv6
        "::ffff:127.0.0.1",   # IPv4 mapejada dins d'IPv6
    ],
)
async def test_rejects_internal_addresses(address: str):
    assert is_blocked_address(address) is True

    with pytest.raises(BlockedURLError) as caught:
        await validate_url("https://intern.example.com/x.pdf", resolver=resolver_returning(address))
    assert caught.value.code == "blocked_url"


async def test_rejects_when_any_resolved_address_is_internal():
    """Un host que resol a una IP publica i una de privada NO es acceptable."""
    with pytest.raises(BlockedURLError) as caught:
        await validate_url(
            "https://mixt.example.com/x.pdf", resolver=resolver_returning(PUBLIC_IP, "127.0.0.1")
        )
    assert caught.value.code == "blocked_url"


async def test_rejects_ip_literals_that_are_internal_without_resolving():
    def explode(host: str, port: int) -> list[str]:
        raise AssertionError("una IP literal no s'ha de resoldre per DNS")

    with pytest.raises(BlockedURLError) as caught:
        await validate_url("http://127.0.0.1/plec.pdf", resolver=explode)
    assert caught.value.code == "blocked_url"


async def test_reports_dns_failures_distinctly():
    def fail(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    with pytest.raises(BlockedURLError) as caught:
        await validate_url("https://no-existeix.example/x.pdf", resolver=fail)
    assert caught.value.code == "dns_error"


async def test_rejects_urls_without_a_host():
    with pytest.raises(BlockedURLError) as caught:
        await validate_url("https:///plec.pdf", resolver=resolver_returning(PUBLIC_IP))
    assert caught.value.code == "blocked_url"


async def test_private_addresses_are_allowed_when_the_test_flag_is_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS", True)

    result = await validate_url(
        "http://127.0.0.1/plec.pdf", resolver=resolver_returning("127.0.0.1")
    )
    assert result.host == "127.0.0.1"
