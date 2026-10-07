"""Client HTTP de la sindicacio de PLACSP.

Responsabilitat unica: **obtenir bytes**. No parseja, no interpreta i no toca
la base de dades.

Bon us de la font (es un servei public, no una API comercial):

* User-Agent identificable amb contacte,
* timeouts sempre,
* reintents limitats amb espera creixent, mai bucles infinits,
* una pausa entre fitxers per no fer crawling agressiu,
* limit dur de mida per no menjar-nos un fitxer inesperadament enorme.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.core.config import settings

# Es reexporta perque la ingesta i la descarrega de plecs s'han de presentar
# igual davant de PLACSP.
from app.core.http import user_agent  # noqa: F401  (reexportacio intencionada)

logger = logging.getLogger(__name__)

# Indireccio perque els tests parchegin nomes la son del client.
_sleep = asyncio.sleep

# Feed oficial de licitacions dels perfils de contractant allotjats a PLACSP.
# Documentat a "Formato de sindicacion y reutilizacion de datos sobre las
# publicaciones en la Plataforma de Contratacion del Sector Publico" (v1.10).
PLACSP_LICITACIONES_FEED = (
    "https://contrataciondelestado.es/sindicacion/sindicacion_643/"
    "licitacionesPerfilesContratanteCompleto3.atom"
)

MAX_FEED_BYTES = 80 * 1024 * 1024
DEFAULT_TIMEOUT = 60.0
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0
# Cortesia entre fitxers consecutius de l'historic.
DELAY_BETWEEN_FILES = 1.0

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


MAX_RETRY_AFTER_SECONDS = 60.0

#: Amb valor "1", el client refusa crear la connexio real (nomes client injectat).
NO_NETWORK_ENV = "TUVI_TESTS_NO_NETWORK"


class FeedFetchError(Exception):
    """No s'ha pogut obtenir el fitxer despres dels reintents."""


# Nom public d'aquesta mateixa excepcio ("feed no disponible").
FeedUnavailable = FeedFetchError


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: bytes | None
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


def _retry_after_seconds(value: str | None) -> float | None:
    """Interpreta `Retry-After` (segons enters o data HTTP), acotat a 60 s."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(int(value))
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        seconds = (moment - datetime.now(UTC)).total_seconds()
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


class PlacspClient:
    """Descarrega fitxers ATOM de la sindicacio."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
        pause_seconds: float | None = None,
    ):
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._pause_seconds = (
            settings.INGESTION_PAGE_PAUSE_SECONDS if pause_seconds is None else pause_seconds
        )

    async def __aenter__(self) -> PlacspClient:
        if self._client is None:
            # Xarxa de seguretat dels tests: `tests/conftest.py` posa la variable i
            # cap test pot arribar a la font real sense injectar un client/transport.
            if os.environ.get(NO_NETWORK_ENV) == "1":
                raise RuntimeError("Xarxa desactivada als tests: injecta un transport")
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": user_agent(),
                    "Accept": "application/atom+xml, application/xml;q=0.9",
                },
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        """Descarrega un fitxer amb reintents i GET condicional.

        Un 304 es retorna com `not_modified=True` sense reintentar. Si el
        servidor envia `Retry-After` s'espera aquest temps (acotat a 60 s) en
        comptes de l'espera creixent. Llanca FeedFetchError si no ho aconsegueix.
        """
        if self._client is None:  # pragma: no cover - us fora del context manager
            raise RuntimeError("PlacspClient s'ha d'utilitzar com a context manager.")

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        last_error: str | None = None
        retry_after: float | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            retry_after = None
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "placsp.fetch.error", extra={"url": url, "attempt": attempt, "error": last_error}
                )
            else:
                if response.status_code == 304:
                    return FetchResult(
                        status=304,
                        body=None,
                        etag=response.headers.get("ETag") or etag,
                        last_modified=response.headers.get("Last-Modified") or last_modified,
                        not_modified=True,
                    )
                if response.status_code == 200:
                    content = response.content
                    if len(content) > MAX_FEED_BYTES:
                        raise FeedFetchError(
                            f"El fitxer {url} ocupa {len(content)} bytes i supera el limit."
                        )
                    logger.info(
                        "placsp.fetch.ok", extra={"url": url, "bytes": len(content)}
                    )
                    return FetchResult(
                        status=200,
                        body=content,
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                    )
                last_error = f"HTTP {response.status_code}"
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                if response.status_code not in RETRYABLE_STATUS:
                    # 404, 403... reintentar no arreglara res.
                    raise FeedFetchError(f"{url} ha respost {last_error}.")
                logger.warning(
                    "placsp.fetch.retryable",
                    extra={"url": url, "attempt": attempt, "status": response.status_code},
                )

            if attempt < MAX_ATTEMPTS:
                await _sleep(
                    retry_after if retry_after is not None else BACKOFF_SECONDS * attempt
                )

        raise FeedFetchError(f"No s'ha pogut descarregar {url}: {last_error}")

    async def pause(self) -> None:
        """Espera entre fitxers consecutius."""
        await _sleep(self._pause_seconds)
