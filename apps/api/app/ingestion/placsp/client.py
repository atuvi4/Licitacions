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

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

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


class FeedFetchError(Exception):
    """No s'ha pogut obtenir el fitxer despres dels reintents."""


def user_agent() -> str:
    contact = getattr(settings, "INGESTION_CONTACT_EMAIL", "") or "sense-contacte"
    return f"LicitacionsBot/0.1 (+ingesta de dades obertes; contacte: {contact})"


class PlacspClient:
    """Descarrega fitxers ATOM de la sindicacio."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, client: httpx.AsyncClient | None = None):
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> PlacspClient:
        if self._client is None:
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

    async def fetch(self, url: str) -> bytes:
        """Descarrega un fitxer amb reintents. Llanca FeedFetchError si no ho aconsegueix."""
        if self._client is None:  # pragma: no cover - us fora del context manager
            raise RuntimeError("PlacspClient s'ha d'utilitzar com a context manager.")

        last_error: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "placsp.fetch.error", extra={"url": url, "attempt": attempt, "error": last_error}
                )
            else:
                if response.status_code == 200:
                    content = response.content
                    if len(content) > MAX_FEED_BYTES:
                        raise FeedFetchError(
                            f"El fitxer {url} ocupa {len(content)} bytes i supera el limit."
                        )
                    logger.info(
                        "placsp.fetch.ok", extra={"url": url, "bytes": len(content)}
                    )
                    return content
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUS:
                    # 404, 403... reintentar no arreglara res.
                    raise FeedFetchError(f"{url} ha respost {last_error}.")
                logger.warning(
                    "placsp.fetch.retryable",
                    extra={"url": url, "attempt": attempt, "status": response.status_code},
                )

            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(BACKOFF_SECONDS * attempt)

        raise FeedFetchError(f"No s'ha pogut descarregar {url}: {last_error}")

    async def pause(self) -> None:
        """Espera entre fitxers consecutius."""
        await asyncio.sleep(DELAY_BETWEEN_FILES)
