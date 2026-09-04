"""Configuracio exposada al client i contracte de l'storage."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.storage import LocalFilesystemStorage, S3Storage, StorageService


class TestClientConfig:
    """El limit d'upload ha de tenir una unica font de veritat: el backend."""

    async def test_el_client_pot_llegir_els_limits(self, client_a):
        response = await client_a.get("/api/v1/config")
        assert response.status_code == 200
        body = response.json()
        assert body["max_upload_size_mb"] == settings.MAX_UPLOAD_SIZE_MB
        assert body["expiry_warning_days"] == settings.EXPIRY_WARNING_DAYS
        assert "application/pdf" in body["allowed_mime_types"]

    async def test_els_tipus_publicats_son_els_que_saplicaran(self, client_a):
        """Si divergissin, el frontend deixaria pujar coses que el backend rebutja."""
        published = set((await client_a.get("/api/v1/config")).json()["allowed_mime_types"])
        assert published == settings.allowed_mime_types

    async def test_la_configuracio_requereix_sessio(self, client):
        assert (await client.get("/api/v1/config")).status_code == 401


class TestStorageContract:
    """El domini nomes depen de StorageService, no de cap proveidor."""

    def test_les_implementacions_compleixen_la_interficie(self):
        for implementation in (LocalFilesystemStorage, S3Storage):
            assert issubclass(implementation, StorageService)
        assert not getattr(StorageService, "__abstractmethods__", set()) - {
            "upload",
            "download",
            "delete",
            "exists",
        }

    async def test_cicle_complet_upload_download_exists_delete(self, tmp_path):
        storage = LocalFilesystemStorage(tmp_path / "s")
        key = "companies/abc/2026/09/dades.pdf"

        assert await storage.exists(key) is False
        await storage.upload(key, b"contingut", "application/pdf")
        assert await storage.exists(key) is True
        assert await storage.download(key) == b"contingut"

        await storage.delete(key)
        assert await storage.exists(key) is False
        # Esborrar dues vegades no ha de petar.
        await storage.delete(key)

    async def test_no_es_pot_sortir_del_directori_arrel(self, tmp_path):
        from app.services.storage import StorageError

        storage = LocalFilesystemStorage(tmp_path / "s")
        with pytest.raises(StorageError):
            await storage.exists("../fora.pdf")
