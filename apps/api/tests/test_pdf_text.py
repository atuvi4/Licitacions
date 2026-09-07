"""Extraccio de text: pagines, no un string gegant.

La cita futura "PCAP, pagina 27" nomes val si la pagina 27 desada es de veritat
la 27 del document. Aquests tests ho fixen.
"""

from __future__ import annotations

import hashlib

import pytest

from app.core.config import settings
from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus
from app.services.pdf_text import (
    PARSER_VERSION,
    extract_pdf_text,
    normalize_page_text,
    unsupported_result,
)
from tests.fixtures.pdf_builder import build_corrupt_pdf, build_pdf

LOREM = (
    "Plec de clausules administratives particulars per a la contractacio del "
    "servei de manteniment integral dels equipaments municipals. "
) * 3


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


def test_normalisation_composes_unicode():
    # "e" + accent combinant ha de quedar com un sol punt de codi.
    assert normalize_page_text("é") == "é"
