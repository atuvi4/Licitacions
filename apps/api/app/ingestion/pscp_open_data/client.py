"""Client HTTP de les dades obertes de la PSCP (SODA, dataset `ybgg-dgi6`).

Responsabilitat unica: **obtenir bytes** de la font, nomes amb GET i nomes de
`analisi.transparenciacatalunya.cat`. No interpreta res ni toca la base.

Bon us d'un servei public:

* peticions sequencials, amb una pausa minima (1 s per defecte) entre l'una i l'altra;
* reintents limitats davant de 429 i 5xx (o d'un error de xarxa), amb espera creixent
  o el `Retry-After` del servidor (acotat a 60 s);
* un limit dur de peticions per run (per defecte 300): si s'hi arriba, s'atura;
* registre de cada peticio (URL exacta, hora UTC, estat, bytes, SHA-256 del cos).

Les respostes es desen redactades (`redact`): els NIF i les dades de contacte
d'adjudicataris i organs no surten mai del run; el SHA-256 de l'original es conserva.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.ingestion.placsp.client import NO_NETWORK_ENV
from app.ingestion.pscp_open_data import METADATA_URL, PORTAL, RESOURCE_URL

logger = logging.getLogger(__name__)

# Indireccio perque els tests parchegin nomes la son del client.
_sleep = asyncio.sleep

ALLOWED_PREFIX = PORTAL + "/"
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_TIMEOUT = 180.0
DEFAULT_PAUSE_SECONDS = 1.0
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_MAX_REQUESTS = 300
BACKOFF_SECONDS = 5.0
MAX_BODY_BYTES = 64 * 1024 * 1024
USER_AGENT = "Tuvi-F06a-pscp-open-data/1.0 (read-only; sequential)"


MAX_RETRY_AFTER_SECONDS = 60.0


def _retry_after_seconds(value: str | None) -> float | None:
    """`Retry-After` en segons enters, acotat a 60 s (una data HTTP no s'interpreta)."""
    if not value:
        return None
    try:
        seconds = float(int(value.strip()))
    except ValueError:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


class SodaFetchError(Exception):
    """La font no ha respost be despres dels reintents (o la peticio no es admesa)."""


class RequestBudgetExceeded(SodaFetchError):
    """S'ha arribat al limit de peticions del run."""


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    body: bytes
    sha256: str
    retrieved_at: datetime
    attempts: int


def rows_url(
    uuids: list[str], *, select: tuple[str, ...], offset: int = 0, limit: int = 5000
) -> str:
    """La consulta d'un lot d'UUID: `id_intern IN (...)` o `starts_with(id_intern, <uuid>_)`.

    Ordre estable (`$order=id_intern,:id`) perque la paginacio no perdi ni repeteixi
    files. Els UUID ja s'han validat (`parser.UUID_RE`): cap cometa hi pot arribar.
    """
    quoted = ",".join(f"'{uid}'" for uid in uuids)
    prefixes = " OR ".join(f"starts_with(id_intern,'{uid}_')" for uid in uuids)
    where = f"id_intern IN ({quoted}) OR {prefixes}"
    params = {
        "$select": ",".join(select),
        "$where": where,
        "$order": "id_intern,:id",
        "$limit": str(limit),
        "$offset": str(offset),
    }
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="(),'*:_")
    return f"{RESOURCE_URL}?{query}"


def metadata_url() -> str:
    return METADATA_URL


class SodaClient:
    """GET sequencials a la font, amb pausa, reintents, limit i registre."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        pause_seconds: float = DEFAULT_PAUSE_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._client = client
        self._owns_client = client is None
        self._pause = pause_seconds
        self._max_attempts = max_attempts
        self._max_requests = max_requests
        self._timeout = timeout
        self._last: float | None = None
        self.requests_made = 0
        self.log: list[dict[str, Any]] = []

    async def __aenter__(self) -> SodaClient:
        if self._client is None:
            if os.environ.get(NO_NETWORK_ENV) == "1":
                raise RuntimeError("Xarxa desactivada als tests: injecta un transport")
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _wait_turn(self) -> None:
        if self._last is not None:
            remaining = self._pause - (time.monotonic() - self._last)
            if remaining > 0:
                await _sleep(remaining)

    async def get(self, url: str, *, purpose: str = "") -> Fetched:
        """Un GET amb reintents. Llanca `SodaFetchError` si no ho aconsegueix."""
        if self._client is None:  # pragma: no cover - us fora del context manager
            raise RuntimeError("SodaClient s'ha d'utilitzar com a context manager.")
        if not url.startswith(ALLOWED_PREFIX):
            raise SodaFetchError("Nomes es poden consultar les dades obertes de la Generalitat.")
        last_error = None
        for attempt in range(1, self._max_attempts + 1):
            if self.requests_made >= self._max_requests:
                raise RequestBudgetExceeded(
                    f"Limit de {self._max_requests} peticions del run assolit."
                )
            await self._wait_turn()
            started = datetime.now(UTC)
            self.requests_made += 1
            entry: dict[str, Any] = {
                "n": self.requests_made,
                "method": "GET",
                "purpose": purpose,
                "url": url,
                "fetched_at_utc": started.isoformat(timespec="seconds"),
                "attempt": attempt,
            }
            retry_after = None
            try:
                response = await self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}"
                entry.update(status=None, error=last_error)
            else:
                body = response.content
                entry.update(
                    status=response.status_code,
                    bytes=len(body),
                    sha256=hashlib.sha256(body).hexdigest() if body else None,
                    sent_url=str(response.request.url),
                )
                if response.status_code == 200:
                    self._last = time.monotonic()
                    self.log.append(entry)
                    if len(body) > MAX_BODY_BYTES:
                        raise SodaFetchError("La resposta supera el limit de mida.")
                    return Fetched(
                        url=str(response.request.url),
                        status=200,
                        body=body,
                        sha256=hashlib.sha256(body).hexdigest(),
                        retrieved_at=started,
                        attempts=attempt,
                    )
                last_error = f"HTTP {response.status_code}"
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                if response.status_code not in RETRYABLE_STATUS:
                    self._last = time.monotonic()
                    self.log.append(entry)
                    raise SodaFetchError(f"La font ha respost {last_error}.")
            self._last = time.monotonic()
            self.log.append(entry)
            logger.warning(
                "pscp_open_data.fetch.retryable", extra={"attempt": attempt, "error": last_error}
            )
            if attempt < self._max_attempts:
                await _sleep(
                    retry_after
                    if retry_after is not None
                    else min(60.0, BACKOFF_SECONDS * 2**attempt)
                )
        raise SodaFetchError(f"No s'ha pogut obtenir la resposta: {last_error}")


# --------------------------------------------------------------------------- #
# Redaccio de les respostes desades                                            #
# --------------------------------------------------------------------------- #

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_NIF_LIKE = re.compile(r"(?i)[A-HJ-NP-SUVW]\d{7}[0-9A-J]|\d{8}[A-Z]|[XYZ]\d{7}[A-Z]")
_SENSITIVE_KEYS = (
    "nif",
    "cif",
    "dni",
    "identificacio",
    "numerodocument",
    "numdocument",
    "email",
    "correu",
    "telefon",
    "mail",
    "fax",
    "adjudicatari",
)
REDACTED = "[REDACTAT]"


def redact(value: Any) -> tuple[Any, int]:
    """Copia redactada d'un document JSON i quants valors s'hi han tapat.

    Es tapa tot valor escalar d'una clau sensible (NIF, identificacio, contacte,
    adjudicatari) i tot text que sigui exactament un NIF/NIE o que porti un correu.
    """
    count = 0

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, dict):
            # Metadades: els valors mes frequents (`cachedContents`) d'una columna
            # sensible (adjudicatari, identificacio...) es tapen sencers.
            column = str(node.get("fieldName", "")).lower()
            sensitive_column = any(token in column for token in _SENSITIVE_KEYS)
            out = {}
            for key, item in node.items():
                if sensitive_column and key == "cachedContents":
                    out[key] = REDACTED
                    count += 1
                    continue
                lowered = str(key).lower()
                if (
                    any(token in lowered for token in _SENSITIVE_KEYS)
                    and isinstance(item, (str, int, float))
                    and not isinstance(item, bool)
                    and item != ""
                ):
                    out[key] = REDACTED
                    count += 1
                else:
                    out[key] = walk(item)
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str):
            if _NIF_LIKE.fullmatch(node.strip()):
                count += 1
                return REDACTED
            if _EMAIL.search(node):
                count += 1
                return _EMAIL.sub("[EMAIL REDACTAT]", node)
        return node

    return walk(value), count
