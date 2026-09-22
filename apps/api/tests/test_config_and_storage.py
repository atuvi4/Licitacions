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


def test_build_tender_document_key_is_content_addressed():
    from app.services.storage import build_tender_document_key

    sha = "a" * 64
    key = build_tender_document_key(sha, "pdf")

    assert key == f"tenders/documents/aa/aa/{sha}.pdf"
    # Ha de quedar fora de l'espai de noms de les empreses i de la ingesta.
    assert not key.startswith("companies/")
    assert not key.startswith("sources/")


def test_build_tender_document_key_validates_the_hash():
    from app.services.storage import StorageError, build_tender_document_key

    with pytest.raises(StorageError):
        build_tender_document_key("no-es-un-hash", "pdf")


def test_build_tender_document_key_sanitises_the_extension():
    from app.services.storage import build_tender_document_key

    sha = "b" * 64
    assert build_tender_document_key(sha, "../evil").endswith(f"{sha}.evil")
    assert build_tender_document_key(sha, "") == f"tenders/documents/bb/bb/{sha}"


def test_generated_tender_document_key_passes_the_key_validator():
    from app.services.storage import build_tender_document_key, validate_storage_key

    key = build_tender_document_key("c" * 64, "pdf")
    assert validate_storage_key(key) == key


def test_document_settings_expose_derived_limits():
    from app.core.config import settings

    assert settings.document_max_size_bytes == settings.DOCUMENT_MAX_SIZE_MB * 1024 * 1024
    assert "application/pdf" in settings.document_allowed_mime_types
    # El text pla hi entra des d'S3-S2: llegir-lo es descodificar-lo, sense cap
    # dependencia nova. De la resta de formats admesos se'n guarda l'original i
    # no se'n llegeix el text.
    assert settings.document_text_extraction_mime_types == {"application/pdf", "text/plain"}
    assert settings.document_text_extraction_mime_types <= settings.document_allowed_mime_types
    assert settings.document_allowed_ports == {80, 443}


def test_private_host_downloads_are_refused_in_production():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS"):
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY="x" * 48,
            DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS=True,
        )


def test_user_agent_identifies_the_bot_and_the_contact():
    from app.core.http import user_agent

    agent = user_agent()
    assert agent.startswith("LicitacionsBot/")
    assert "contacte" in agent


# --------------------------------------------------------------------------- #
# Un proveidor que no llegeix el plec no es una analisi                         #
# --------------------------------------------------------------------------- #


def test_la_base_dunalisi_es_dedueix_del_proveidor():
    """El nom del proveidor ja es a cada run: no cal cap columna nova."""
    from app.models.enums import AnalysisBasis

    assert AnalysisBasis.for_provider("anthropic") == AnalysisBasis.REAL
    assert AnalysisBasis.for_provider("fixture") == AnalysisBasis.SIMULATION
    assert AnalysisBasis.for_provider("fake") == AnalysisBasis.PROVIDER_NOT_CONFIGURED
    assert AnalysisBasis.for_provider(None) == AnalysisBasis.PROVIDER_NOT_CONFIGURED


def test_nomes_una_analisi_real_soste_una_afirmacio_sobre_el_plec():
    from app.models.enums import AnalysisBasis

    assert AnalysisBasis.REAL.is_trustworthy is True
    assert AnalysisBasis.SIMULATION.is_trustworthy is False
    assert AnalysisBasis.PROVIDER_NOT_CONFIGURED.is_trustworthy is False


@pytest.mark.parametrize("provider", ["fake", "fixture", "FIXTURE"])
def test_produccio_no_arrenca_amb_un_proveidor_que_no_llegeix_res(provider):
    """A produccio, servir requisits inventats seria pitjor que no servir res."""
    from app.core.config import Settings

    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY="x" * 40,
            LLM_PROVIDER=provider,
        )


def test_produccio_arrenca_amb_un_proveidor_real():
    from app.core.config import Settings

    settings = Settings(
        ENVIRONMENT="production", SECRET_KEY="x" * 40, LLM_PROVIDER="anthropic"
    )

    assert settings.LLM_PROVIDER == "anthropic"


def test_en_desenvolupament_la_simulacio_es_perfectament_valida():
    """Es el que permet recorrer el flux sencer sense xarxa ni API."""
    from app.core.config import Settings

    settings = Settings(ENVIRONMENT="local", LLM_PROVIDER="fixture")

    assert settings.LLM_PROVIDER == "fixture"
