"""Validacio i normalitzacio de fitxers pujats."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import PurePosixPath

from app.core.config import settings
from app.core.errors import PayloadTooLargeError, UnsupportedMediaTypeError, ValidationError

# Extensio canonica per tipus MIME acceptat. La font de veritat es el MIME
# declarat, no l'extensio del fitxer que envia el navegador.
MIME_EXTENSIONS: dict[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/tiff": "tif",
    "image/webp": "webp",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/zip": "zip",
}

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._\- ]+")


def sanitize_filename(filename: str | None) -> str:
    """Neteja el nom original per guardar-lo com a metadata (mai com a ruta)."""
    if not filename:
        return "document"
    # Descarta qualsevol component de directori que arribi del client.
    base = PurePosixPath(filename.replace("\\", "/")).name
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    base = _UNSAFE_CHARS.sub("_", base).strip(" .")
    return base[:255] or "document"


def compute_sha256(data: bytes) -> str:
    """Hash de contingut. Servira per detectar duplicats i cachejar processament futur."""
    return hashlib.sha256(data).hexdigest()


def validate_upload(content: bytes, mime_type: str | None) -> str:
    """Comprova mida i tipus. Retorna el MIME normalitzat."""
    if not content:
        raise ValidationError("El fitxer es buit.")
    if len(content) > settings.max_upload_size_bytes:
        raise PayloadTooLargeError(
            f"El fitxer supera el maxim permes de {settings.MAX_UPLOAD_SIZE_MB} MB."
        )
    normalized = (mime_type or "").split(";")[0].strip().lower()
    if normalized not in settings.allowed_mime_types:
        raise UnsupportedMediaTypeError(
            "Tipus de fitxer no permes.",
            details={"mime_type": normalized, "allowed": sorted(settings.allowed_mime_types)},
        )
    return normalized


def extension_for(mime_type: str, original_filename: str) -> str:
    if mime_type in MIME_EXTENSIONS:
        return MIME_EXTENSIONS[mime_type]
    suffix = PurePosixPath(original_filename).suffix.lstrip(".")
    return re.sub(r"[^A-Za-z0-9]", "", suffix).lower()[:10]
