"""Tests unitaris de les regles de domini que no toquen la BD."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.security import hash_password, verify_password
from app.core.errors import PayloadTooLargeError, UnsupportedMediaTypeError
from app.models.enums import TemporalStatus
from app.schemas.common import compute_temporal_status
from app.services.files import compute_sha256, sanitize_filename, validate_upload
from app.services.storage import StorageError, build_storage_key, validate_storage_key

TODAY = date.today()


class TestTemporalStatus:
    def test_sense_data_es_desconegut(self):
        assert compute_temporal_status(None) is TemporalStatus.UNKNOWN

    def test_data_passada_es_caducat(self):
        assert compute_temporal_status(TODAY - timedelta(days=1)) is TemporalStatus.EXPIRED

    def test_data_propera_avisa(self):
        assert compute_temporal_status(TODAY + timedelta(days=10)) is TemporalStatus.EXPIRING_SOON

    def test_data_llunyana_es_valid(self):
        assert compute_temporal_status(TODAY + timedelta(days=400)) is TemporalStatus.VALID


class TestPasswords:
    def test_hash_i_verificacio(self):
        digest = hash_password("Una-Clau-Prou-Llarga1")
        assert digest != "Una-Clau-Prou-Llarga1"
        assert verify_password("Una-Clau-Prou-Llarga1", digest)
        assert not verify_password("una-clau-prou-llarga1", digest)

    def test_hash_no_es_deterministic(self):
        assert hash_password("mateixa-clau-1") != hash_password("mateixa-clau-1")


class TestFilenames:
    @pytest.mark.parametrize(
        ("entrada", "prohibit"),
        [
            ("../../etc/passwd", "/"),
            ("..\\..\\windows\\system32\\config", "\\"),
            ("informe final.pdf", ".."),
        ],
    )
    def test_no_conserva_camins(self, entrada, prohibit):
        assert prohibit not in sanitize_filename(entrada)

    def test_nom_buit_te_valor_per_defecte(self):
        assert sanitize_filename(None) == "document"

    def test_accents_es_normalitzen(self):
        assert sanitize_filename("memòria tècnica.pdf") == "memoria tecnica.pdf"


class TestUploads:
    def test_hash_estable(self):
        assert compute_sha256(b"abc") == compute_sha256(b"abc")
        assert compute_sha256(b"abc") != compute_sha256(b"abd")

    def test_mime_no_permes(self):
        with pytest.raises(UnsupportedMediaTypeError):
            validate_upload(b"MZ...", "application/x-msdownload")

    def test_mime_permes_es_normalitza(self):
        assert validate_upload(b"%PDF-1.4", "application/pdf; charset=binary") == "application/pdf"

    def test_fitxer_massa_gran(self, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "MAX_UPLOAD_SIZE_MB", 0)
        with pytest.raises(PayloadTooLargeError):
            validate_upload(b"x" * 10, "application/pdf")


class TestStorageKeys:
    def test_clau_generada_es_valida(self):
        import uuid

        key = build_storage_key(uuid.uuid4(), "PDF")
        assert key.endswith(".pdf")
        assert validate_storage_key(key) == key

    @pytest.mark.parametrize("clau", ["../secret", "/absolute/path", "a/../../b"])
    def test_claus_perilloses_es_rebutgen(self, clau):
        with pytest.raises(StorageError):
            validate_storage_key(clau)
