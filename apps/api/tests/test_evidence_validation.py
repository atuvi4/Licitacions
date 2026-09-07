"""Localitzacio de cites: la frontera entre "ho ha dit el model" i "ho diu el plec".

La regla es literal amb tolerancia tipografica. Cap similitud semantica: si el
fragment no hi es, es rebutja.
"""

from __future__ import annotations

import pytest

from app.services.evidence_validation import locate_quote, normalize_quote, quote_is_present

PAGE = (
    "8.1.2. Solvencia economica y financiera.\n"
    "El volumen anual de negocios del licitador deberá ser al menos de 450.000 euros\n"
    "referido al mejor ejercicio de los tres últimos disponibles.\n"
)


def test_locates_a_literal_quote_and_returns_offsets():
    match = locate_quote(PAGE, "El volumen anual de negocios del licitador")

    assert match is not None
    assert PAGE[match.char_start : match.char_end] == "El volumen anual de negocios del licitador"
    assert match.exact_text == "El volumen anual de negocios del licitador"


def test_tolerates_line_breaks_inside_the_quote():
    """El model cita en una linia el que al PDF esta partit en dues."""
    quote = "deberá ser al menos de 450.000 euros referido al mejor ejercicio"

    match = locate_quote(PAGE, quote)

    assert match is not None
    assert "450.000" in PAGE[match.char_start : match.char_end]


@pytest.mark.parametrize(
    "quote",
    [
        "El  volumen   anual  de negocios",           # espais de mes
        "\n El volumen anual de negocios \t",          # vora bruta
        "EL VOLUMEN ANUAL DE NEGOCIOS",                # majuscules
    ],
)
def test_tolerates_whitespace_and_case(quote: str):
    assert quote_is_present(PAGE, quote) is True


def test_tolerates_typographic_quotes_and_dashes():
    page = 'La clasificación “Grupo U” — subgrupo 1 — es exigible.'

    assert quote_is_present(page, 'La clasificación "Grupo U" - subgrupo 1') is True


def test_tolerates_unicode_composition():
    page = "Solvencia técnica del licitador"  # e + accent combinant

    assert quote_is_present(page, "Solvencia técnica del licitador") is True


def test_rejects_a_quote_that_is_not_there():
    assert locate_quote(PAGE, "El volumen anual de negocios de 900.000 euros") is None


def test_rejects_a_semantically_equivalent_paraphrase():
    """Aquesta es la garantia: parafrasejar NO es citar."""
    assert quote_is_present(PAGE, "La facturación mínima exigida es de 450.000 euros") is False


def test_rejects_a_quote_invented_from_pieces_of_the_page():
    assert quote_is_present(PAGE, "Solvencia economica de 450.000 euros anuales") is False


@pytest.mark.parametrize("quote", ["", "   ", "\n"])
def test_rejects_an_empty_quote(quote: str):
    assert locate_quote(PAGE, quote) is None


def test_rejects_any_quote_on_an_empty_page():
    assert locate_quote("", "qualsevol cosa") is None


def test_offsets_point_at_the_immutable_page_text():
    quote = "450.000 euros"
    match = locate_quote(PAGE, quote)

    assert match is not None
    assert PAGE[match.char_start : match.char_end] == "450.000 euros"
    assert match.char_end > match.char_start


def test_normalisation_is_stable():
    assert normalize_quote("  Dos   espais\ni  salt ") == "dos espais i salt"
