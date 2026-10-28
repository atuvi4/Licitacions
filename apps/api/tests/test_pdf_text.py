"""Extraccio de text: pagines, no un string gegant.

La cita futura "PCAP, pagina 27" nomes val si la pagina 27 desada es de veritat
la 27 del document. Aquests tests ho fixen.
"""

from __future__ import annotations

import hashlib
import io

import pytest
from pypdf import PdfReader
from pypdf.constants import UserAccessPermissions
from pypdf.errors import DependencyError

from app.core.config import settings
from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus
from app.services import pdf_text
from app.services.pdf_text import (
    PARSER_VERSION,
    crypto_available,
    extract_pdf_text,
    normalize_page_text,
    should_retry,
    unsupported_result,
)
from tests.fixtures.pdf_builder import (
    build_corrupt_pdf,
    build_pdf,
    encrypt_pdf,
    use_pypdf_without_aes,
)

LOREM = (
    "Plec de clausules administratives particulars per a la contractacio del "
    "servei de manteniment integral dels equipaments municipals. "
) * 3

TWO_PAGES = build_pdf(["Primera " + LOREM, "Segona " + LOREM])
#: Xifrats abans de cap test: fer-ho amb AES tambe demana la biblioteca.
ENCRYPTED = {
    algorithm: encrypt_pdf(TWO_PAGES, algorithm=algorithm)
    for algorithm in ("AES-128", "AES-256", "RC4-128")
}


def test_extracts_text_from_a_single_page_pdf():
    result = extract_pdf_text(build_pdf([LOREM]))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.parser_version == PARSER_VERSION
    assert result.page_count == 1
    assert len(result.pages) == 1
    assert "clausules administratives" in result.pages[0].text


def test_page_numbers_are_one_based_and_in_order():
    result = extract_pdf_text(
        build_pdf(["Primera " + LOREM, "Segona " + LOREM, "Tercera " + LOREM])
    )

    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert result.pages[0].text.startswith("Primera")
    assert result.pages[2].text.startswith("Tercera")


def test_keeps_catalan_and_spanish_accents():
    result = extract_pdf_text(
        build_pdf(
            [
                "Adjudicacio provisional: numero d'expedient, accio, "
                "informacio, Espanya, forca, alcada " + LOREM
            ]
        )
    )

    text = result.pages[0].text
    for word in ("Adjudicacio", "expedient", "Espanya"):
        assert word in text


def test_marks_a_page_without_a_content_stream_as_empty():
    result = extract_pdf_text(build_pdf([LOREM, "", LOREM]))

    assert result.pages[1].status == PageExtractionStatus.EMPTY
    assert result.pages[1].text == ""
    assert result.pages[1].char_count == 0
    assert result.empty_page_count == 1
    assert result.extracted_page_count == 2


def test_hashes_the_normalised_text_of_each_page():
    result = extract_pdf_text(build_pdf([LOREM]))
    page = result.pages[0]

    assert page.text_hash == hashlib.sha256(page.text.encode("utf-8")).hexdigest()
    assert page.char_count == len(page.text)


def test_quality_is_good_for_a_normal_document():
    result = extract_pdf_text(build_pdf([LOREM, LOREM, LOREM]))

    assert result.quality == ExtractionQuality.GOOD
    assert result.ocr_candidate is False


def test_a_document_without_any_text_is_empty_and_an_ocr_candidate():
    """El cas del plec escanejat: 5 pagines, cap caracter."""
    result = extract_pdf_text(build_pdf(["", "", "", "", ""]))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.page_count == 5
    assert result.text_char_count == 0
    assert result.quality == ExtractionQuality.EMPTY
    assert result.ocr_candidate is True


def test_a_document_with_almost_no_text_is_low_text():
    result = extract_pdf_text(build_pdf(["Annex I", "Annex II", "Annex III"]))

    assert result.quality == ExtractionQuality.LOW_TEXT
    assert result.ocr_candidate is True


def test_more_than_half_the_pages_empty_is_low_text():
    result = extract_pdf_text(build_pdf([LOREM, "", "", ""]))

    assert result.quality == ExtractionQuality.LOW_TEXT
    assert result.ocr_candidate is True


def test_a_corrupt_pdf_fails_without_raising():
    result = extract_pdf_text(build_corrupt_pdf())

    assert result.status == ExtractionStatus.FAILED
    assert result.quality == ExtractionQuality.FAILED
    assert result.error_code == "corrupt_pdf"
    assert result.pages == ()


def test_bytes_that_are_not_a_pdf_at_all_fail():
    result = extract_pdf_text(b"aixo no es cap PDF")

    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "corrupt_pdf"


def test_refuses_a_document_with_too_many_pages(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_MAX_PAGES", 2)

    result = extract_pdf_text(build_pdf([LOREM, LOREM, LOREM]))

    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "too_many_pages"


def test_handles_a_large_document_within_the_limit():
    """40 pagines: prou per detectar una implementacio quadratica."""
    result = extract_pdf_text(build_pdf([LOREM] * 40))

    assert result.page_count == 40
    assert result.pages[-1].page_number == 40
    assert result.quality == ExtractionQuality.GOOD


def test_unsupported_result_describes_a_non_pdf():
    result = unsupported_result("application/zip")

    assert result.status == ExtractionStatus.SKIPPED_UNSUPPORTED
    assert result.quality == ExtractionQuality.NOT_APPLICABLE
    assert result.error_code == "unsupported_for_text_extraction"
    assert result.ocr_candidate is False
    assert result.pages == ()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("linia   amb   espais", "linia   amb   espais"),  # no es col·lapsen espais interns
        ("final amb espais   \nsegona", "final amb espais\nsegona"),
        ("windows\r\nnewline", "windows\nnewline"),
        ("mac\rnewline", "mac\nnewline"),
        ("massa\n\n\n\n\nbuides", "massa\n\nbuides"),
        ("amb\x00nuls\x07dins", "ambnulsdins"),
        ("\n\n  vora  \n\n", "  vora"),
        ("tabulador\tconservat", "tabulador\tconservat"),
    ],
)
def test_normalisation_is_conservative(raw: str, expected: str):
    assert normalize_page_text(raw) == expected


# --------------------------------------------------------------------------- #
# PDF xifrats: el model oficial del DEUC es AES-128 sense contrasenya d'obertura #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256", "RC4-128"])
def test_an_encrypted_pdf_without_an_opening_password_reads_like_the_plain_one(algorithm):
    data = ENCRYPTED[algorithm]
    assert PdfReader(io.BytesIO(data)).is_encrypted

    plain = extract_pdf_text(TWO_PAGES)
    result = extract_pdf_text(data)

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.error_code is None
    assert result.parser_version == PARSER_VERSION == "pypdf-v1"
    assert [(p.page_number, p.text, p.text_hash) for p in result.pages] == [
        (p.page_number, p.text, p.text_hash) for p in plain.pages
    ]


def test_the_deuc_case_aes_128_with_restricted_permissions_is_read():
    """Com els DEUC del pilot: V4/R4, sense contrasenya d'obertura i sense permis de copiar."""
    data = encrypt_pdf(
        build_pdf([LOREM]), algorithm="AES-128", permissions=UserAccessPermissions.PRINT
    )
    encrypt = PdfReader(io.BytesIO(data)).trailer["/Encrypt"].get_object()
    assert (encrypt["/V"], encrypt["/R"]) == (4, 4)

    result = extract_pdf_text(data)

    assert result.status == ExtractionStatus.EXTRACTED
    assert "clausules administratives" in result.pages[0].text


@pytest.mark.parametrize("algorithm", ["AES-128", "RC4-128"])
def test_a_pdf_with_a_real_opening_password_is_encrypted_pdf(algorithm):
    data = encrypt_pdf(build_pdf([LOREM]), algorithm=algorithm, user_password="secret")

    result = extract_pdf_text(data)

    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "encrypted_pdf"
    assert result.pages == ()


def test_without_the_aes_library_aes_128_is_crypto_unavailable_not_corrupt(monkeypatch):
    """Sense `cryptography`, l'R4 s'obre (RC4 i MD5) pero cap pagina no es pot desxifrar: el
    document sencer falla com a `pdf_crypto_unavailable`, no com a `corrupt_pdf` ni com a
    pagines buides que farien pensar en un escaneig."""
    use_pypdf_without_aes(monkeypatch)

    result = extract_pdf_text(ENCRYPTED["AES-128"])

    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "pdf_crypto_unavailable"
    assert result.pages == ()
    assert result.ocr_candidate is False


def test_without_the_aes_library_aes_256_fails_on_opening(monkeypatch):
    use_pypdf_without_aes(monkeypatch)

    result = extract_pdf_text(ENCRYPTED["AES-256"])

    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "pdf_crypto_unavailable"


def test_without_the_aes_library_rc4_is_still_read(monkeypatch):
    use_pypdf_without_aes(monkeypatch)

    result = extract_pdf_text(ENCRYPTED["RC4-128"])

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.pages[0].text.startswith("Primera")


class _ReaderWithoutPageTree:
    """Un PDF xifrat que s'obre pero l'arbre de pagines del qual demana una dependencia."""

    is_encrypted = True

    def __init__(self, *_args, **_kwargs):
        pass

    def decrypt(self, _password):
        return 1

    @property
    def pages(self):
        raise DependencyError("cryptography>=3.1 is required for AES algorithm")


def test_a_missing_library_while_reading_the_page_tree_is_crypto_unavailable(monkeypatch):
    """El cas dels DEUC del pilot: l'arbre de pagines es dins de fluxos xifrats amb AES."""
    use_pypdf_without_aes(monkeypatch)
    monkeypatch.setattr(pdf_text, "PdfReader", _ReaderWithoutPageTree)

    result = extract_pdf_text(b"%PDF-1.6 simulat")

    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "pdf_crypto_unavailable"


def test_a_dependency_error_with_the_aes_at_hand_is_not_crypto_unavailable(monkeypatch):
    """Amb l'AES a punt, un `DependencyError` no pot ser la falta de l'AES: es una fallada del
    document, com abans, i no es reintenta (no s'acumulen revisions fallides)."""
    monkeypatch.setattr(pdf_text, "PdfReader", _ReaderWithoutPageTree)

    result = extract_pdf_text(b"%PDF-1.6 simulat")

    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "corrupt_pdf"
    # No te la forma que deixava el codi d'abans: no entra mai en un bucle de reintents.
    assert result.error_message.startswith("DependencyError (amb AES): ")
    assert should_retry(result.error_code, result.error_message) is False


@pytest.mark.parametrize("algorithm", [None, "AES-128", "AES-256"])
def test_a_jbig2_page_without_jbig2dec_fails_alone(monkeypatch, algorithm):
    """pypdf llanca el mateix `DependencyError` quan li falta el programa `jbig2dec` (un flux de
    continguts `/JBIG2Decode`, malformat). No falta cap AES: nomes falla aquella pagina, com
    abans, xifrat o no (abans d'aquest canvi, tombava el document com `pdf_crypto_unavailable`
    i es reintentava a cada processat)."""
    import pypdf.filters

    monkeypatch.setattr(pypdf.filters, "JBIG2DEC_BINARY", None)
    data = build_pdf([LOREM, LOREM, LOREM], jbig2_pages=frozenset({2}))
    if algorithm:
        data = encrypt_pdf(data, algorithm=algorithm)

    result = extract_pdf_text(data)

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.error_code is None
    assert [page.status for page in result.pages] == [
        PageExtractionStatus.EXTRACTED,
        PageExtractionStatus.EXTRACTED,
        PageExtractionStatus.FAILED,
    ]


LEGACY_MISSING_AES = "DependencyError: cryptography>=3.1 is required for AES algorithm"


def test_should_retry_only_an_environment_failure_once_the_environment_allows_it(monkeypatch):
    assert crypto_available() is True
    assert should_retry("pdf_crypto_unavailable") is True
    # La forma que deixava el codi d'abans en un PDF AES (els DEUC del pilot).
    assert should_retry("corrupt_pdf", LEGACY_MISSING_AES) is True
    for code, message in (
        ("corrupt_pdf", None),
        ("corrupt_pdf", "PdfReadError: EOF marker not found"),
        ("corrupt_pdf", "DependencyError: jbig2dec binary is not available."),
        ("encrypted_pdf", LEGACY_MISSING_AES),
        ("too_many_pages", None),
        (None, None),
    ):
        assert should_retry(code, message) is False, (code, message)

    use_pypdf_without_aes(monkeypatch)

    assert crypto_available() is False
    assert should_retry("pdf_crypto_unavailable") is False
    assert should_retry("corrupt_pdf", LEGACY_MISSING_AES) is False


def test_without_the_provider_registry_the_aes_is_tried_for_real(monkeypatch):
    """Si un pypdf futur mou el seu registre intern de proveidors, l'AES no es dona per bo: es
    xifra i s'obre un PDF de debo (abans, es donava per disponible i es reintentava sempre)."""
    import sys

    pdf_text._aes_round_trip.cache_clear()
    try:
        with monkeypatch.context() as without_registry:
            without_registry.setitem(sys.modules, "pypdf._crypt_providers", None)
            assert crypto_available() is True

        pdf_text._aes_round_trip.cache_clear()
        with monkeypatch.context() as without_aes:
            use_pypdf_without_aes(without_aes)
            without_aes.setitem(sys.modules, "pypdf._crypt_providers", None)
            assert crypto_available() is False
            assert should_retry("pdf_crypto_unavailable") is False
    finally:
        pdf_text._aes_round_trip.cache_clear()


def test_normalisation_composes_unicode():
    # "e" + accent combinant ha de quedar com un sol punt de codi.
    assert normalize_page_text("é") == "é"
