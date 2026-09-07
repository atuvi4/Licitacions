"""Descarrega de documents publics de licitacio.

Responsabilitat unica: **obtenir bytes validats**. No parseja, no toca la base
de dades i no puja res a l'storage.

Tres decisions que val la pena explicitar:

* **Streaming amb tall dur.** El SHA-256 es calcula chunk a chunk i la
  descarrega s'avorta en el moment que se supera el limit. Un plec inesperadament
  enorme no arriba mai a estar sencer a la RAM.
* **Redireccions manuals.** `follow_redirects` es queda desactivat perque cada
  salt s'ha de tornar a validar contra la guarda SSRF. Deixar que httpx les
  segueixi sol equivaldria a validar nomes la primera URL.
* **Els magic bytes manen.** L'ordre de confianca es magic bytes > Content-Type
  > extensio de la URL. Una URL que acaba en `.pdf` i retorna HTML es un error,
  no un PDF estrany.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import httpx

from app.core.config import settings
from app.core.http import user_agent
from app.services.files import MIME_EXTENSIONS, sanitize_filename
from app.services.url_guard import BlockedURLError, Resolver, validate_url

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
CHUNK_SIZE = 64 * 1024

# Els tipus OOXML son fitxers ZIP: el sniffing no els sap distingir. Quan el
# servidor en declara un i el contingut es un ZIP, la declaracio no contradiu el
# sniffing, l'afina.
ZIP_REFINEMENTS = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)

# Signatura OLE2 (Compound File Binary): la comparteixen .doc, .xls i .ppt
# anteriors a 2007. Els magic bytes NO permeten distingir-los entre ells, aixi
# que el tipus generic es l'unica resposta honesta quan el servidor no l'afina.
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
OLE2_GENERIC = "application/x-ole-storage"

# Mateix patro que ZIP_REFINEMENTS: si el servidor declara un tipus concret de
# la familia OLE2, la declaracio no contradiu el sniffing, l'afina.
OLE2_REFINEMENTS = frozenset(
    {
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    }
)

EXTRA_EXTENSIONS = {
    "application/xml": "xml",
    "text/xml": "xml",
    "text/html": "html",
    OLE2_GENERIC: "ole",
}

_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", re.IGNORECASE)


class DownloadError(Exception):
    """La descarrega no ha donat un document utilitzable."""

    def __init__(self, message: str, code: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class DownloadResult:
    content: bytes
    sha256: str
    file_size: int
    #: Tipus real, decidit pels magic bytes.
    mime_type: str
    #: El que deia la capcalera, nomes com a metadada de diagnostic.
    declared_mime_type: str | None
    detected_extension: str
    original_filename: str
    detected_filename: str | None
    final_url: str
    http_status: int
    redirect_count: int
    download_ms: int


def sniff_mime_type(head: bytes) -> str | None:
    """Determina el tipus pels primers bytes. `None` si no hi ha cap firma coneguda."""
    # La norma PDF tolera fins a 1024 bytes de brossa abans de la capcalera.
    if b"%PDF-" in head[:1024]:
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    if head.startswith(OLE2_MAGIC):
        return OLE2_GENERIC
    stripped = head.lstrip()[:512].lower()
    if stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html"):
        return "text/html"
    if stripped.startswith(b"<?xml"):
        return "application/xml"
    return None


def resolve_mime_type(sniffed: str | None, declared: str | None, url: str) -> str:
    """Decideix el tipus real. Llanca `DownloadError` si el contingut menteix."""
    path = urlsplit(url).path.lower()
    announces_pdf = declared == "application/pdf" or path.endswith(".pdf")
    if announces_pdf and sniffed != "application/pdf":
        raise DownloadError(
            "La URL anuncia un PDF pero el contingut descarregat no ho es.",
            "invalid_content",
        )
    if sniffed == "application/zip" and declared in ZIP_REFINEMENTS:
        return declared
    if sniffed == OLE2_GENERIC and declared in OLE2_REFINEMENTS:
        return declared
    if sniffed:
        return sniffed
    if declared:
        return declared
    raise DownloadError("No s'ha pogut determinar el tipus del document.", "invalid_content")


def extension_for(mime_type: str) -> str:
    return MIME_EXTENSIONS.get(mime_type) or EXTRA_EXTENSIONS.get(mime_type, "bin")


def filename_from_url(url: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    return sanitize_filename(name or "document")


def filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    match = _FILENAME_RE.search(header)
    return sanitize_filename(unquote(match.group(1))) if match else None


@dataclass(slots=True)
class _Attempt:
    status: int
    redirect_to: str | None
    body: bytes | None
    sha256: str | None
    headers: httpx.Headers


async def _attempt(client: httpx.AsyncClient, url: str, max_bytes: int) -> _Attempt:
    async with client.stream("GET", url) as response:
        if response.status_code in REDIRECT_STATUS:
            location = response.headers.get("location")
            if not location:
                raise DownloadError(
                    "Redireccio sense capcalera Location.",
                    "http_error",
                    http_status=response.status_code,
                )
            return _Attempt(
                response.status_code, str(response.url.join(location)), None, None, response.headers
            )

        if response.status_code != 200:
            return _Attempt(response.status_code, None, None, None, response.headers)

        declared_length = response.headers.get("content-length")
        if declared_length and declared_length.isdigit() and int(declared_length) > max_bytes:
            raise DownloadError(
                f"El document supera el limit de {settings.DOCUMENT_MAX_SIZE_MB} MB.", "too_large"
            )

        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes(chunk_size=CHUNK_SIZE):
            total += len(chunk)
            if total > max_bytes:
                # Sortir del context manager tanca la connexio: la descarrega
                # s'atura de veritat, no es llegeix la resta del cos.
                raise DownloadError(
                    f"El document supera el limit de {settings.DOCUMENT_MAX_SIZE_MB} MB.",
                    "too_large",
                )
            digest.update(chunk)
            chunks.append(chunk)

        return _Attempt(200, None, b"".join(chunks), digest.hexdigest(), response.headers)


async def _attempt_with_retries(
    client: httpx.AsyncClient, url: str, max_bytes: int, deadline: float
) -> _Attempt:
    attempts = settings.DOCUMENT_DOWNLOAD_MAX_ATTEMPTS
    last_code = "network_error"
    last_message = "No s'ha pogut connectar amb l'origen."

    for attempt in range(1, attempts + 1):
        try:
            result = await _attempt(client, url, max_bytes)
        except httpx.TimeoutException as exc:
            last_code, last_message = "timeout", f"Temps d'espera esgotat: {exc}"
        except httpx.HTTPError as exc:
            last_code, last_message = "network_error", f"{type(exc).__name__}: {exc}"
        else:
            if result.status in RETRYABLE_STATUS and attempt < attempts:
                last_code, last_message = "http_error", f"HTTP {result.status}"
            else:
                return result

        if attempt >= attempts or time.monotonic() >= deadline:
            break
        await asyncio.sleep(settings.DOCUMENT_DOWNLOAD_BACKOFF_SECONDS * attempt)

    raise DownloadError(last_message, last_code)


async def download_document(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver | None = None,
) -> DownloadResult:
    """Descarrega i valida un document. Llanca `DownloadError` amb un codi estable."""
    started = time.monotonic()
    deadline = started + settings.DOCUMENT_DOWNLOAD_TOTAL_TIMEOUT_SECONDS
    max_bytes = settings.document_max_size_bytes

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS),
            follow_redirects=False,
            headers={"User-Agent": user_agent(), "Accept": "*/*"},
        )

    try:
        current = url
        redirects = 0

        while True:
            if time.monotonic() >= deadline:
                raise DownloadError("Temps total de descarrega esgotat.", "timeout")

            try:
                await validate_url(current, resolver=resolver)
            except BlockedURLError as exc:
                raise DownloadError(str(exc), exc.code) from exc

            attempt = await _attempt_with_retries(client, current, max_bytes, deadline)
            if attempt.redirect_to is None:
                break

            redirects += 1
            if redirects > settings.DOCUMENT_MAX_REDIRECTS:
                raise DownloadError(
                    f"Mes de {settings.DOCUMENT_MAX_REDIRECTS} redireccions.",
                    "too_many_redirects",
                )
            current = attempt.redirect_to

        if attempt.status == 404:
            raise DownloadError("El document no existeix a l'origen.", "not_found", http_status=404)
        if attempt.status != 200:
            raise DownloadError(
                f"L'origen ha respost HTTP {attempt.status}.",
                "http_error",
                http_status=attempt.status,
            )

        body = attempt.body or b""
        if not body:
            raise DownloadError("El document descarregat es buit.", "invalid_content")

        declared = (attempt.headers.get("content-type") or "").split(";")[0].strip().lower() or None
        mime_type = resolve_mime_type(sniff_mime_type(body[:2048]), declared, current)
        if mime_type not in settings.document_allowed_mime_types:
            raise DownloadError(f"Tipus de document no admes: {mime_type}.", "invalid_content")

        download_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "document.download.ok",
            extra={
                # Nomes el host: la query string d'un plec pot dur identificadors.
                "url_host": urlsplit(current).hostname,
                "http_status": attempt.status,
                "bytes": len(body),
                "sha256": attempt.sha256,
                "redirect_count": redirects,
                "download_ms": download_ms,
            },
        )

        return DownloadResult(
            content=body,
            sha256=attempt.sha256 or hashlib.sha256(body).hexdigest(),
            file_size=len(body),
            mime_type=mime_type,
            declared_mime_type=declared,
            detected_extension=extension_for(mime_type),
            original_filename=filename_from_url(url),
            detected_filename=filename_from_disposition(attempt.headers.get("content-disposition")),
            final_url=current,
            http_status=attempt.status,
            redirect_count=redirects,
            download_ms=download_ms,
        )
    finally:
        if owns_client:
            await client.aclose()
