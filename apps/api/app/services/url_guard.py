"""Validacio d'URL externes abans de qualsevol connexio.

`TenderDocument.url` ve d'una font externa i viu a la base de dades. Res
d'aixo la fa segura: sense aquesta guarda, el backend seria un proxy que
qualsevol contingut ingerit pot dirigir cap a la xarxa interna, cap al
localhost o cap a l'endpoint de metadades del proveidor de cloud.

La validacio s'aplica ABANS DE CADA SALT, no nomes a la URL inicial: una
redireccio 302 cap a 169.254.169.254 es exactament l'atac que aixo evita.

Limitacio coneguda: entre la resolucio que fem aqui i la connexio real d'httpx
hi ha una finestra de DNS rebinding. Tancar-la del tot vol dir connectar-se a
la IP validada i sobreescriure la capcalera Host. Queda documentat com a deute
a `docs/documents.md`.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import anyio

from app.core.config import settings

#: Resol un host a una llista d'adreces IP en text. Injectable per als tests.
Resolver = Callable[[str, int], list[str]]

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_PORTS = {"http": 80, "https": 443}

# `is_private` de la stdlib ja cobreix loopback, link-local i ULA, pero es
# preferible ser explicit amb les xarxes que a la practica son l'objectiu:
# les metadades del cloud i el rang CGNAT dels operadors.
CLOUD_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
EXTRA_BLOCKED_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)


class BlockedURLError(Exception):
    """La URL no es apta per descarregar-la."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def is_blocked_address(raw: str) -> bool:
    """Diu si una adreca IP cau en un rang que no volem que el downloader toqui."""
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        # Si no la sabem interpretar, no la deixem passar.
        return True

    # Una IPv4 disfressada d'IPv6 s'ha de jutjar com el que es.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped

    if address in CLOUD_METADATA_ADDRESSES:
        return True
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in EXTRA_BLOCKED_NETWORKS)
    return False


def _literal_address(host: str) -> str | None:
    """Retorna el host si ja es una IP literal (amb o sense claudators)."""
    candidate = host.strip("[]")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


async def validate_url(url: str, *, resolver: Resolver | None = None) -> ValidatedURL:
    """Comprova esquema, credencials, port i totes les IP a les quals resol el host."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()

    if scheme not in ALLOWED_SCHEMES:
        raise BlockedURLError(f"Esquema no permes: {scheme or 'cap'}.", "invalid_scheme")
    if parts.username or parts.password:
        raise BlockedURLError("Les URL amb credencials no s'accepten.", "blocked_url")

    host = parts.hostname
    if not host:
        raise BlockedURLError("La URL no te host.", "blocked_url")

    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError:
        raise BlockedURLError("Port no valid.", "blocked_port") from None
    if port not in settings.document_allowed_ports:
        raise BlockedURLError(f"Port no permes: {port}.", "blocked_port")

    allow_private = settings.DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS

    literal = _literal_address(host)
    if literal is not None:
        # Una IP literal no s'ha de resoldre: ja sabem cap a on va.
        if not allow_private and is_blocked_address(literal):
            raise BlockedURLError(f"Adreca no permesa: {literal}.", "blocked_url")
        return ValidatedURL(url=url, host=host, port=port, addresses=(literal,))

    resolve = resolver or default_resolver
    try:
        addresses = await anyio.to_thread.run_sync(resolve, host, port)
    except Exception as exc:  # noqa: BLE001 - qualsevol fallada de DNS es el mateix cas
        raise BlockedURLError(f"No s'ha pogut resoldre {host}.", "dns_error") from exc

    if not addresses:
        raise BlockedURLError(f"{host} no resol a cap adreca.", "dns_error")

    if not allow_private:
        for address in addresses:
            if is_blocked_address(address):
                raise BlockedURLError(
                    f"{host} resol a una adreca no permesa.", "blocked_url"
                )

    return ValidatedURL(url=url, host=host, port=port, addresses=tuple(addresses))
