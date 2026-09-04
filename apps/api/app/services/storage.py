"""Abstraccio d'object storage.

`StorageService` es l'unica cosa que coneix el domini. Cap altre modul del
backend importa boto3 ni sap que hi ha MinIO al darrere: canviar de proveidor
(AWS S3, Cloudflare R2, Scaleway, OVH, qualsevol S3-compatible) es nomes canviar
variables d'entorn.

Els binaris NO es guarden mai a PostgreSQL: a la base de dades hi ha la clau
(`storage_key`), el hash, el tipus MIME i la mida.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path

import anyio

from app.core.config import settings
from app.core.errors import AppError

# Nomes acceptem claus que hem generat nosaltres: sense "..", sense barres
# inicials, sense caracters estranys. Defensa contra path traversal al backend
# de fitxers local.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_\-.]{0,510}$")


class StorageError(AppError):
    code = "storage_error"
    status_code = 502


def build_storage_key(company_id: uuid.UUID, extension: str) -> str:
    """Genera una clau interna segura. Mai deriva del nom de fitxer de l'usuari."""
    ext = extension.lower().lstrip(".")
    ext = re.sub(r"[^a-z0-9]", "", ext)[:10]
    now = datetime.now(UTC)
    suffix = f".{ext}" if ext else ""
    return f"companies/{company_id}/{now:%Y/%m}/{uuid.uuid4().hex}{suffix}"


def validate_storage_key(key: str) -> str:
    if not _SAFE_KEY.match(key) or ".." in key:
        raise StorageError("Clau d'emmagatzematge no valida.")
    return key


class StorageService(ABC):
    """Contracte minim d'emmagatzematge d'objectes.

    Quatre operacions i cap detall de proveidor. Qualsevol implementacio nova
    (S3, R2, Azure Blob, sistema de fitxers) nomes ha de complir aixo.
    """

    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        """Desa l'objecte. Si la clau ja existeix, se sobreescriu."""

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """Retorna el contingut. Llanca StorageError si no existeix."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Esborra l'objecte. Esborrar una clau inexistent no es un error."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Diu si la clau hi es, sense descarregar el contingut."""


class LocalFilesystemStorage(StorageService):
    """Backend de desenvolupament i tests. No usar en produccio."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_storage_key(key)
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root)):
            raise StorageError("Cami de fitxer no permes.")
        return path

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await anyio.to_thread.run_sync(path.write_bytes, data)

    async def download(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise StorageError("El fitxer no existeix a l'emmagatzematge.")
        return await anyio.to_thread.run_sync(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await anyio.to_thread.run_sync(path.unlink)

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Storage(StorageService):
    """Backend compatible amb S3: MinIO, AWS S3, Cloudflare R2, Scaleway, OVH...

    Boto3 es sincron, aixi que cada crida va a un fil per no bloquejar el bucle
    d'esdeveniments.
    """

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = settings.STORAGE_BUCKET
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.STORAGE_ENDPOINT_URL,
            region_name=settings.STORAGE_REGION,
            aws_access_key_id=settings.STORAGE_ACCESS_KEY,
            aws_secret_access_key=settings.STORAGE_SECRET_KEY,
            config=Config(
                signature_version="s3v4",
                # MinIO i molts S3 europeus necessiten path-style; AWS accepta virtual-hosted.
                s3={"addressing_style": "path" if settings.STORAGE_FORCE_PATH_STYLE else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        validate_storage_key(key)

        def _put() -> None:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )

        try:
            await anyio.to_thread.run_sync(_put)
        except Exception as exc:  # noqa: BLE001 - es reenvia com a error de domini
            raise StorageError("No s'ha pogut desar el fitxer.") from exc

    async def download(self, key: str) -> bytes:
        validate_storage_key(key)

        def _get() -> bytes:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()

        try:
            return await anyio.to_thread.run_sync(_get)
        except Exception as exc:  # noqa: BLE001
            raise StorageError("No s'ha pogut recuperar el fitxer.") from exc

    async def delete(self, key: str) -> None:
        validate_storage_key(key)

        def _delete() -> None:
            self._client.delete_object(Bucket=self.bucket, Key=key)

        try:
            await anyio.to_thread.run_sync(_delete)
        except Exception as exc:  # noqa: BLE001
            raise StorageError("No s'ha pogut esborrar el fitxer.") from exc

    async def exists(self, key: str) -> bool:
        validate_storage_key(key)

        def _head() -> bool:
            from botocore.exceptions import ClientError

            try:
                self._client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    return False
                raise
            return True

        try:
            return await anyio.to_thread.run_sync(_head)
        except Exception as exc:  # noqa: BLE001
            raise StorageError("No s'ha pogut consultar el fitxer.") from exc


_backend: StorageService | None = None


def get_storage() -> StorageService:
    global _backend
    if _backend is None:
        if settings.STORAGE_BACKEND.lower() == "s3":
            _backend = S3Storage()
        else:
            _backend = LocalFilesystemStorage(settings.STORAGE_LOCAL_PATH)
    return _backend


def set_storage(backend: StorageService | None) -> None:
    """Punt d'injeccio per a tests."""
    global _backend
    _backend = backend
