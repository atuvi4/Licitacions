"""Client HTTP de l'API publica del portal de la PSCP.

Responsabilitat unica: **obtenir JSON** del portal, nomes amb GET i nomes de `PORTAL_API`.
No interpreta res ni toca la base. Les descarregues de fitxers no passen per aqui (les fa
`document_download`, amb la guarda SSRF), pero hi esperen torn (`wait_turn` i `mark`): el
portal veu una sola cadencia.

Bon us d'un servei public:

* User-Agent identificable amb contacte (`core.http.user_agent`);
* peticions sequencials, amb una pausa minima (`PSCP_PORTAL_PAUSE_SECONDS`) entre l'una i
  l'altra, tambe entre una consulta i una descarrega;
* reintents limitats davant de 429 i 5xx (o d'un error de xarxa), amb espera creixent o el
  `Retry-After` del servidor (acotat a 60 s);
* un limit dur de peticions JSON per sincronitzacio (`PSCP_PORTAL_MAX_REQUESTS`, que el
  servei amplia amb el que el tauler diu que hi ha per llegir);
* cada resposta es llegeix en streaming, amb un tall de mida i un temps total maxim;
* registre de cada peticio (cami, hora UTC, estat, bytes, SHA-256 del cos).

Interruptor: amb `PSCP_PORTAL_ENABLED` apagat, el client no s'obre (`PortalDisabledError`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings
from app.core.http import user_agent
from app.ingestion.placsp.client import NO_NETWORK_ENV
from app.ingestion.pscp_portal import PORTAL_API

logger = logging.getLogger(__name__)

# Indireccio perque els tests parchegin nomes la son del client.
_sleep = asyncio.sleep

ALLOWED_PREFIX = PORTAL_API + "/"
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 30.0
MAX_RETRY_AFTER_SECONDS = 60.0
#: Una resposta JSON del portal fa pocs kB; mes d'aixo no es una resposta normal.
MAX_JSON_BYTES = 8 * 1024 * 1024


def _retry_after_seconds(value: str | None) -> float | None:
    """`Retry-After` en segons enters, acotat a 60 s (una data HTTP no s'interpreta)."""
    if not value:
        return None
    try:
        seconds = float(int(value.strip()))
    except ValueError:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


class PortalFetchError(Exception):
    """El portal no ha respost be despres dels reintents (o la peticio no es admesa)."""


class PortalDisabledError(PortalFetchError):
    """L'interruptor `PSCP_PORTAL_ENABLED` es apagat: cap peticio al portal."""


class RequestBudgetExceeded(PortalFetchError):
    """S'ha arribat al limit de peticions de la sincronitzacio."""


class PortalNotFoundError(PortalFetchError):
    """El portal ha respost 404 per a un cami que la mateixa lectura li ha donat."""

    def __init__(self, path: str) -> None:
        super().__init__(f"El portal no troba `{path}`.")
        #: El cami (public: identificadors de l'expedient i de la publicacio).
        self.path = path


class _ResponseTooLarge(Exception):
    pass


class PortalClient:
    """GET sequencials a l'API del portal, amb pausa, reintents, limit i registre."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        pause_seconds: float | None = None,
        max_attempts: int | None = None,
        max_requests: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._pause = settings.PSCP_PORTAL_PAUSE_SECONDS if pause_seconds is None else pause_seconds
        self._max_attempts = max_attempts or settings.PSCP_PORTAL_MAX_ATTEMPTS
        self._max_requests = max_requests or settings.PSCP_PORTAL_MAX_REQUESTS
        self._timeout = timeout or settings.PSCP_PORTAL_TIMEOUT_SECONDS
        self._last: float | None = None
        self.requests_made = 0
        self.log: list[dict[str, Any]] = []

    async def __aenter__(self) -> PortalClient:
        if not settings.PSCP_PORTAL_ENABLED:
            raise PortalDisabledError("El connector del portal de la PSCP es apagat.")
        if self._client is None:
            if os.environ.get(NO_NETWORK_ENV) == "1":
                raise RuntimeError("Xarxa desactivada als tests: injecta un transport")
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                headers={"User-Agent": user_agent(), "Accept": "application/json"},
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def http_client(self) -> httpx.AsyncClient | None:
        """El client injectat (tests): les descarregues l'han de fer servir tambe."""
        return None if self._owns_client else self._client

    def allow(self, extra: int) -> None:
        """Amplia el limit de peticions amb les que el tauler diu que caldran (comptadors)."""
        self._max_requests += max(0, extra)

    async def wait_turn(self) -> None:
        """Espera la pausa minima des de l'ultima peticio (JSON o descarrega)."""
        if self._last is not None:
            remaining = self._pause - (time.monotonic() - self._last)
            if remaining > 0:
                await _sleep(remaining)

    def mark(self) -> None:
        """Una peticio que no passa per aqui (una descarrega) acaba d'acabar."""
        self._last = time.monotonic()

    async def _read(self, url: str) -> tuple[int, bytes, str | None]:
        """Estat, cos (tallat a `MAX_JSON_BYTES`) i `Retry-After`, en un temps total maxim."""
        async with asyncio.timeout(self._timeout * 2):
            async with self._client.stream("GET", url) as response:
                declared = response.headers.get("content-length", "")
                if declared.isdecimal() and int(declared) > MAX_JSON_BYTES:
                    raise _ResponseTooLarge
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_JSON_BYTES:
                        raise _ResponseTooLarge
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks), response.headers.get("Retry-After")

    async def get_json(self, path: str, *, purpose: str = "") -> Any:
        """Un GET amb reintents a `PORTAL_API/<path>`. Llanca `PortalFetchError` si no pot."""
        if self._client is None:  # pragma: no cover - us fora del context manager
            raise RuntimeError("PortalClient s'ha d'utilitzar com a context manager.")
        url = f"{PORTAL_API}/{path.lstrip('/')}"
        if not url.startswith(ALLOWED_PREFIX) or ".." in path or "://" in path:
            raise PortalFetchError("Nomes es pot consultar l'API publica del portal.")
        last_error = None
        for attempt in range(1, self._max_attempts + 1):
            if self.requests_made >= self._max_requests:
                raise RequestBudgetExceeded(
                    f"Limit de {self._max_requests} peticions de la sincronitzacio assolit."
                )
            await self.wait_turn()
            started = datetime.now(UTC)
            self.requests_made += 1
            entry: dict[str, Any] = {
                "n": self.requests_made,
                "method": "GET",
                "purpose": purpose,
                # El cami i prou: la base es sempre la mateixa i no porta cap secret.
                "path": path,
                "fetched_at_utc": started.isoformat(timespec="seconds"),
                "attempt": attempt,
            }
            retry_after = None
            try:
                status, body, retry_after_header = await self._read(url)
            except _ResponseTooLarge:
                self._last = time.monotonic()
                entry.update(status=None, error="too_large")
                self.log.append(entry)
                raise PortalFetchError("La resposta del portal supera el limit de mida.") from None
            except (httpx.HTTPError, TimeoutError) as exc:
                last_error = type(exc).__name__
                entry.update(status=None, error=last_error)
            else:
                entry.update(
                    status=status,
                    bytes=len(body),
                    sha256=hashlib.sha256(body).hexdigest() if body else None,
                )
                if status == 200:
                    self._last = time.monotonic()
                    self.log.append(entry)
                    try:
                        return json.loads(body)
                    except (ValueError, RecursionError):
                        raise PortalFetchError("El portal no ha respost amb JSON.") from None
                last_error = f"HTTP {status}"
                retry_after = _retry_after_seconds(retry_after_header)
                if status not in RETRYABLE_STATUS:
                    self._last = time.monotonic()
                    self.log.append(entry)
                    if status == 404:
                        raise PortalNotFoundError(path)
                    raise PortalFetchError(f"El portal ha respost {last_error}.")
            self._last = time.monotonic()
            self.log.append(entry)
            logger.warning(
                "pscp_portal.fetch.retryable", extra={"attempt": attempt, "error": last_error}
            )
            if attempt < self._max_attempts:
                await _sleep(
                    retry_after
                    if retry_after is not None
                    else min(MAX_BACKOFF_SECONDS, BACKOFF_SECONDS * 2**attempt)
                )
        raise PortalFetchError(f"No s'ha pogut obtenir la resposta del portal: {last_error}")
