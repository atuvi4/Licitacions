"""`doc-v2`: el text dels .doc de Word 97-2003, sense Word ni cap programa extern.

Els .doc es generen al test (`tests/fixtures/doc_builder.py`): contenidor OLE2, FIB, taula de
peces i propietats de paragraf i de caracter escrits a ma. Fixen el que costa mes de recuperar si
es trenca: que el text surti sencer, en l'ordre del document i sense el que s'ha suprimit, que
les files de taula no es parteixin per una cel·la buida ni canviïn de lloc, que un fitxer petit
no pugui demanar memoria ni temps desmesurats, i que un fitxer dolent torni una fallada neta en
lloc de llancar.
"""

from __future__ import annotations

import random
import struct
import time

import pytest

from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus
from app.services import doc_text
from app.services.doc_text import DOC, DOC_PARSER_VERSION, extract_doc
from tests.fixtures.doc_builder import (
    CELL_BREAK,
    FIB_CCP_TEXT,
    Piece,
    build_cfb,
    build_doc,
    fib_pair,
    inner_row,
    row,
    with_fib,
    word_streams,
)

ACCENTS = "Adjudicació de l'expedient: anàlisi, sol·licitud, façana, Espanya, € i “cometes”"


def _text(result) -> str:
    return "\n\f".join(page.text for page in result.pages)


def _lines(result) -> list[str]:
    return result.pages[0].text.split("\n")


# --------------------------------------------------------------------------- #
# Text                                                                          #
# --------------------------------------------------------------------------- #


def test_reads_an_8_bit_piece_with_catalan_characters():
    result = extract_doc(build_doc(f"{ACCENTS}\r"))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.parser_version == DOC_PARSER_VERSION == "doc-v2"
    assert result.error_code is None
    assert result.pages[0].text == ACCENTS
    assert result.quality == ExtractionQuality.GOOD


def test_reads_a_utf16_piece():
    text = "Ŀ i ŀ (ela geminada), Ω, 中文 i € en UTF-16"
    result = extract_doc(build_doc([Piece(text + "\r", compressed=False)]))

    assert result.pages[0].text == text


def test_joins_pieces_in_document_order_even_if_stored_out_of_order():
    """Un .doc desat rapid te els trossos desordenats al flux: mana la taula de peces."""
    pieces = [
        Piece("Primer tros. ", compressed=True),
        Piece("Segon tros en UTF-16 amb ŀ. ", compressed=False),
        Piece("Tercer tros.\r", compressed=True),
    ]
    in_order = extract_doc(build_doc(pieces))
    scrambled = extract_doc(build_doc(pieces, scramble=True))

    expected = "Primer tros. Segon tros en UTF-16 amb ŀ. Tercer tros."
    assert in_order.pages[0].text == expected
    assert scrambled.pages[0].text == expected


def test_only_the_main_text_is_read():
    """Notes, capcaleres i comentaris venen despres de `ccpText`: no es llegeixen (v1)."""
    main = "Text principal del plec.\r"
    data = build_doc([Piece(main), Piece("Nota al peu.\rCapcalera.\r")], ccp_text=len(main))

    assert _text(extract_doc(data)) == "Text principal del plec."


def test_skips_property_modifiers_before_the_piece_table():
    assert extract_doc(build_doc("Hola\r", prc=True)).pages[0].text == "Hola"


def test_reads_the_0table_stream_when_the_fib_says_so():
    result = extract_doc(build_doc("Taula 0\r", table_stream="0Table"))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.pages[0].text == "Taula 0"


def test_paragraphs_are_lines_and_empty_ones_disappear():
    result = extract_doc(build_doc("Primer paragraf.\r\r\rSegon\x0bamb salt de linia.\r"))

    assert result.pages[0].text == "Primer paragraf.\nSegon\namb salt de linia."


def test_special_characters():
    result = extract_doc(build_doc("Guio\x1eque no parteix, par\x1ftir i tab\tulador.\r"))

    assert result.pages[0].text == "Guio-que no parteix, partir i tab\tulador."


def test_a_column_break_separates_the_words_it_is_between():
    """El salt de columna (0x0E) es un salt de linia, com a `docx-v1`: no enganxa paraules."""
    result = extract_doc(build_doc("Final de la columna u\x0eInici de la dos.\r"))

    assert result.pages[0].text == "Final de la columna u\nInici de la dos."


def test_a_character_outside_the_bmp_does_not_break_the_tables():
    """Un parell suplent UTF-16 son dues unitats de codi: les marques de despres continuen
    al seu lloc (abans, les files es trencaven i canviaven d'ordre)."""
    text = "Preu 𝟏𝟐 (fora del BMP)\r" + row("A", "B") + row("C", "D") + "Fi 𝟑\r"
    result = extract_doc(build_doc([Piece(text, compressed=False)]))

    assert _lines(result) == ["Preu 𝟏𝟐 (fora del BMP)", "| A | B |", "| C | D |", "Fi 𝟑"]
    assert result.text_char_count == sum(len(line) for line in _lines(result)) + 3


# --------------------------------------------------------------------------- #
# Control de canvis                                                             #
# --------------------------------------------------------------------------- #


def test_text_deleted_with_track_changes_is_not_read():
    """El text suprimit continua al fitxer marcat amb `sprmCFRMarkDel`: no es vigent."""
    amounts = "30 dies.\rImport: 100.000120.000 €.\r"
    old = amounts.index("100.000")
    pieces = [
        Piece("Termini de "),
        Piece("15 ", deleted=True),
        Piece(amounts, deleted=((old, old + len("100.000")),)),
    ]

    assert _lines(extract_doc(build_doc(pieces))) == [
        "Termini de 30 dies.",
        "Import: 120.000 €.",
    ]


def test_deleted_text_in_utf16_and_a_deleted_paragraph_mark():
    pieces = [
        Piece("Abans 𝟏 suprimit despres.\r", compressed=False, deleted=((8, 17),)),
        Piece("Primer.\r", deleted=((7, 8),)),
        Piece("Segon.\r"),
    ]

    assert _lines(extract_doc(build_doc(pieces, scramble=True))) == [
        "Abans 𝟏 despres.",
        "Primer.Segon.",
    ]


# --------------------------------------------------------------------------- #
# Pagines                                                                       #
# --------------------------------------------------------------------------- #


def test_page_breaks_are_the_page_boundaries():
    result = extract_doc(build_doc("Pagina u.\r\x0cPagina dos.\r\x0c\x0cPagina tres.\r"))

    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert [page.text for page in result.pages] == ["Pagina u.", "Pagina dos.", "Pagina tres."]
    assert result.page_count == 3


def test_a_short_document_without_page_breaks_is_a_single_page():
    result = extract_doc(build_doc("U.\rDos.\rTres.\r" * 50))

    assert result.page_count == 1


def _paragraphs(count: int, width: int = 60) -> list[str]:
    return [f"Clausula {index:04d} " + "x" * (width - 15) for index in range(count)]


def test_a_long_document_without_page_breaks_gets_virtual_pages():
    """`doc-v2`: sense cap salt, pagines d'uns 3.000 caracters entre paragrafs. No es perd ni
    es reordena res, i cap paragraf no queda partit entre dues pagines."""
    paragraphs = _paragraphs(200)
    result = extract_doc(build_doc("".join(f"{p}\r" for p in paragraphs)))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.page_count == len(result.pages) > 1
    assert [page.page_number for page in result.pages] == list(range(1, result.page_count + 1))
    assert all(page.char_count <= doc_text.VIRTUAL_PAGE_CHARS for page in result.pages)
    lines = [line for page in result.pages for line in page.text.split("\n")]
    assert lines == paragraphs
    # Plenes: la seguent linia no hi cabia.
    for page, following in zip(result.pages, result.pages[1:], strict=False):
        first = following.text.split("\n")[0]
        assert page.char_count + 1 + len(first) > doc_text.VIRTUAL_PAGE_CHARS


def test_a_paragraph_longer_than_a_virtual_page_is_a_page_on_its_own():
    long = "Paragraf molt llarg " + "y" * 5000
    text = "Abans.\r" + long + "\r" + "Despres.\r"
    result = extract_doc(build_doc(text))

    assert [page.text for page in result.pages] == ["Abans.", long, "Despres."]


def test_table_rows_are_never_cut_by_virtual_pages():
    text = "".join(row(f"Lot {index}", "z" * 70, "12,50 €") for index in range(120))
    result = extract_doc(build_doc(text))

    assert result.page_count > 1
    lines = [line for page in result.pages for line in page.text.split("\n")]
    assert lines == [f"| Lot {index} | {'z' * 70} | 12,50 € |" for index in range(120)]


def test_explicit_page_breaks_are_the_pages_even_if_they_are_long():
    """Amb salts explicits manen les pagines del document: no se'n fan de virtuals."""
    first, second = _paragraphs(80), _paragraphs(70)
    text = "".join(f"{p}\r" for p in first) + "\x0c" + "".join(f"{p}\r" for p in second)
    result = extract_doc(build_doc(text))

    assert [page.text.split("\n") for page in result.pages] == [first, second]
    assert result.pages[0].char_count > doc_text.VIRTUAL_PAGE_CHARS


def test_an_empty_document_is_one_empty_page():
    result = extract_doc(build_doc("\r", ccp_text=0))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.quality == ExtractionQuality.EMPTY
    assert result.page_count == 1
    assert result.pages[0].status == PageExtractionStatus.EMPTY
    assert result.ocr_candidate is False


# --------------------------------------------------------------------------- #
# Camps                                                                         #
# --------------------------------------------------------------------------- #


def test_a_field_shows_its_result_not_its_instruction():
    text = 'Data: \x13 DATE \\@ "d/M/yyyy" \x1424/10/2026\x15 fi.\r'

    assert extract_doc(build_doc(text)).pages[0].text == "Data: 24/10/2026 fi."


def test_nested_fields_and_fields_without_result():
    nested = "A\x13 IF \x13 MERGEFIELD x \x14valor\x15 = 1 \x14\x13 REF y \x14si\x15\x15B"
    no_result = "C\x13 TOC \\o \x15D"

    assert extract_doc(build_doc(nested + "\r" + no_result + "\r")).pages[0].text == "AsiB\nCD"


def test_a_field_that_never_ends_hides_nothing():
    """Un inici de camp sense final no s'empassa la resta del document (abans, si); un final o
    un separador de cap camp es treuen i prou."""
    text = (
        "Final\x15 i separador\x14 solts.\r"
        "Abans \x13camp sense final. Tot el que segueix.\rSegon paragraf.\r"
    )

    assert _lines(extract_doc(build_doc(text))) == [
        "Final i separador solts.",
        "Abans camp sense final. Tot el que segueix.",
        "Segon paragraf.",
    ]


# --------------------------------------------------------------------------- #
# Taules                                                                        #
# --------------------------------------------------------------------------- #


def test_table_rows_keep_their_empty_cells():
    """El cas dels formularis: les cel·les per emplenar son buides i no parteixen la fila."""
    text = (
        "Model d'oferta\r"
        + row("Lot", "Exercici", "Import ofertat")
        + row("CCSPT", "2027", "")
        + row("", "2028", "")
        + "Signatura\r"
    )
    result = extract_doc(build_doc(text))

    assert _lines(result) == [
        "Model d'oferta",
        "| Lot | Exercici | Import ofertat |",
        "| CCSPT | 2027 |  |",
        "|  | 2028 |  |",
        "Signatura",
    ]


def test_paragraphs_inside_a_cell_are_joined():
    text = row(f"Primer{CELL_BREAK}segon", f"u{CELL_BREAK}dos{CELL_BREAK}tres") + "Fi\r"

    assert _lines(extract_doc(build_doc(text))) == [
        "| Primer / segon | u / dos / tres |",
        "Fi",
    ]


def test_without_paragraph_properties_a_row_ends_at_two_marks():
    """La regla de reserva: deterministica, pero una cel·la buida hi parteix la fila."""
    text = row("A", "B") + row("C", "") + "Fi\r"
    result = extract_doc(build_doc(text, paragraph_properties=False))

    assert _lines(result) == ["| A | B |", "| C |", "Fi"]


def test_tables_across_utf16_pieces():
    pieces = [
        Piece(row("Concepte", "Preu"), compressed=False),
        Piece(row("Manteniment", "12,50 €")),
        Piece("Total\r", compressed=False),
    ]

    assert _lines(extract_doc(build_doc(pieces, scramble=True))) == [
        "| Concepte | Preu |",
        "| Manteniment | 12,50 € |",
        "Total",
    ]


def test_a_row_whose_end_is_not_recognised_stays_before_the_next_paragraph():
    """Si una marca de fila no porta `sprmPFTtp`, la fila es tanca al paragraf seguent: no
    surt despres de la signatura (abans, la fila de preus saltava al final)."""
    text = (
        "Model d'oferta\r"
        + row("Lot", "Import")
        + "Lot 1\x07" + "1.000\x07" + "\x07"  # final de fila sense sprmPFTtp
        + "Signatura del licitador\r"
        + "Data i lloc\r"
    )

    assert _lines(extract_doc(build_doc(text))) == [
        "Model d'oferta",
        "| Lot | Import |",
        "| Lot 1 | 1.000 |  |",
        "Signatura del licitador",
        "Data i lloc",
    ]


def test_row_properties_stored_in_the_data_stream_are_read():
    """Una taula ampla no cap a la FKP: Word hi deixa `sprmPHugePapx` i les propietats van al
    flux `Data`. Sense el flux, regla de reserva (i cap excepcio)."""
    text = "Abans\r" + row("A", "", "C") + row("D", "E", "") + "Fi\r"

    assert _lines(extract_doc(build_doc(text, huge_rows=True))) == [
        "Abans",
        "| A |  | C |",
        "| D | E |  |",
        "Fi",
    ]
    streams = word_streams(text, huge_rows=True)
    del streams["Data"]
    without_data = extract_doc(build_cfb(streams))
    assert without_data.status == ExtractionStatus.EXTRACTED
    assert _lines(without_data) == ["Abans", "| A |", "| C |", "| D | E |", "Fi"]


def test_a_nested_table_keeps_its_rows_and_cells():
    """Com `docx-v1`: la taula niada surt dins de la seva cel·la, fila a fila."""
    text = (
        "Abans\r"
        + row(inner_row("x1", "x2") + inner_row("y1", "y2"), "Segona")
        + "Despres\r"
    )

    assert _lines(extract_doc(build_doc(text))) == [
        "Abans",
        "| | x1 | x2 | / | y1 | y2 | | Segona |",
        "Despres",
    ]


# --------------------------------------------------------------------------- #
# Fallades netes i formats que no son .doc                                      #
# --------------------------------------------------------------------------- #


def _assert_failed(result, code: str) -> None:
    assert result.status == ExtractionStatus.FAILED
    assert result.quality == ExtractionQuality.FAILED
    assert result.error_code == code
    assert result.parser_version == DOC_PARSER_VERSION
    assert result.pages == ()
    assert result.error_message


def _assert_not_word(result) -> None:
    assert result.status == ExtractionStatus.SKIPPED_UNSUPPORTED
    assert result.quality == ExtractionQuality.NOT_APPLICABLE
    assert result.error_code == "unsupported_for_text_extraction"
    assert result.parser_version == DOC_PARSER_VERSION
    assert result.pages == ()


@pytest.mark.parametrize("flags", [{"encrypted": True}, {"encrypted": True, "obfuscated": True}])
def test_an_encrypted_document_fails_as_doc_encrypted(flags):
    _assert_failed(extract_doc(build_doc("Secret\r", **flags)), "doc_encrypted")


def test_the_obfuscation_bit_alone_is_not_encryption():
    """[MS-DOC]: `fObfuscated` s'ignora si `fEncrypted` no hi es."""
    result = extract_doc(build_doc("Public\r", obfuscated=True))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.pages[0].text == "Public"


@pytest.mark.parametrize("n_fib", [0x0065, 0x0068, 0x002D])
def test_word_6_and_95_fail_as_unsupported_version(n_fib):
    _assert_failed(extract_doc(build_doc("Antic\r", n_fib=n_fib)), "doc_unsupported_version")


def test_the_word_6_identifier_is_an_unsupported_version():
    streams = word_streams("Antic\r")
    word = bytearray(streams["WordDocument"])
    word[0:2] = (0xA5DC).to_bytes(2, "little")
    data = build_cfb({**streams, "WordDocument": bytes(word)})

    _assert_failed(extract_doc(data), "doc_unsupported_version")


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"aixo no es cap .doc",
        b"{\\rtf1\\ansi\\deff0 Plec de clausules}",
        b"<html><body><p>Plec de clausules</p></body></html>",
    ],
    ids=["buit", "text", "rtf", "html"],
)
def test_bytes_that_are_not_ole2_are_a_format_that_is_not_read(data):
    """Molts «.doc» dels portals son RTF o HTML: no son un Word malmes."""
    _assert_not_word(extract_doc(data))


def test_an_ole2_file_without_a_word_document_is_a_format_that_is_not_read():
    streams = word_streams("Hola\r")
    _assert_not_word(extract_doc(build_cfb({"Workbook": streams["WordDocument"]})))


def test_a_missing_table_stream_is_unreadable():
    streams = word_streams("Hola\r")
    _assert_failed(
        extract_doc(build_cfb({"WordDocument": streams["WordDocument"]})), "doc_unreadable"
    )


def test_a_wrong_word_ident_is_unreadable():
    streams = word_streams("Hola\r")
    word = bytearray(streams["WordDocument"])
    word[0:2] = b"\x00\x00"
    damaged = build_cfb({**streams, "WordDocument": bytes(word)})
    _assert_failed(extract_doc(damaged), "doc_unreadable")


def test_a_piece_outside_the_stream_is_unreadable():
    streams = word_streams("Hola\r")
    table = bytearray(streams["1Table"])
    # fc de la primera peca (despres de clxt, lcb i els dos cp): fora del flux.
    table[5 + 8 + 2 : 5 + 8 + 6] = ((0x3FFFFFF0 * 2) | 0x40000000).to_bytes(4, "little")
    _assert_failed(extract_doc(build_cfb({**streams, "1Table": bytes(table)})), "doc_unreadable")


def test_main_text_longer_than_the_pieces_is_unreadable():
    _assert_failed(extract_doc(build_doc("Hola\r", ccp_text=500)), "doc_unreadable")


def test_never_raises_on_damaged_files():
    """200 variants amb bytes canviats (llavor fixa): sempre un resultat, mai una excepcio."""
    original = build_doc("Paragraf.\r" + row("A", "", "C") + "Fi\r\x0cDos\r")
    rng = random.Random(20261028)
    statuses = set()
    for _ in range(200):
        damaged = bytearray(original)
        for _ in range(rng.randint(1, 40)):
            damaged[rng.randrange(len(damaged))] = rng.randrange(256)
        result = extract_doc(bytes(damaged))
        assert result.status in (
            ExtractionStatus.EXTRACTED,
            ExtractionStatus.FAILED,
            ExtractionStatus.SKIPPED_UNSUPPORTED,
        )
        assert result.parser_version == DOC_PARSER_VERSION
        statuses.add(result.status)
    assert ExtractionStatus.EXTRACTED in statuses


_FUZZ_BASE = [
    Piece("Primer paragraf. \x13 DATE \x1424/10/2026\x15\r" + row("A", "", f"x{CELL_BREAK}y")),
    Piece("Segona peca en UTF-16 amb ŀ i 𝟏.\r\x0c" + row(inner_row("p", "q"), "D"),
          compressed=False),
    Piece("Text suprimit. ", deleted=True),
    Piece("Fi.\r"),
]
_FUZZ_FIB_FIELDS = [
    (2, "<H"), (0x0A, "<H"), (0x20, "<H"), (0x3E, "<H"), (FIB_CCP_TEXT, "<i"), (0x98, "<H"),
    *((fib_pair(index) + half, "<I") for index in (12, 13, 33) for half in (0, 4)),
]
_FUZZ_VALUES = [
    0, 1, 2, 3, 4, 7, 8, 12, 13, 255, 256, 511, 512, 0x7FFF, 0x8000, 0xFFFF, 0x10000,
    0x3FFFFFFF, 0x40000000, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF,
]


def test_never_raises_when_the_streams_are_damaged():
    """El contenidor sempre es valid i el que es muta son les estructures que llegeix `doc-v2`
    (FIB, CLX, PlcBte, FKP de paragraf i de caracter, flux `Data`): 300 variants (llavor fixa),
    sempre un resultat, i cap de lenta."""
    base = word_streams(_FUZZ_BASE, scramble=True, prc=True, huge_rows=True)
    rng = random.Random(20261029)
    worst = 0.0
    for _ in range(300):
        streams = {name: bytearray(data) for name, data in base.items()}
        word = streams["WordDocument"]
        kind = rng.choice(["fib", "fib", "table", "word", "fkp", "data"])
        if kind == "fib":
            offset, fmt = rng.choice(_FUZZ_FIB_FIELDS)
            bits = 8 * struct.calcsize(fmt)
            value = rng.choice(_FUZZ_VALUES) if rng.random() < 0.7 else rng.getrandbits(bits)
            value %= 2**bits
            if fmt == "<i" and value >= 2**31:
                value -= 2**32
            struct.pack_into(fmt, word, offset, value)
        elif kind == "table":
            for _ in range(rng.randint(1, 8)):
                streams["1Table"][rng.randrange(300)] = rng.randrange(256)
        elif kind == "word":
            for _ in range(rng.randint(1, 8)):
                word[rng.randrange(0x400, 0x600)] = rng.randrange(256)
        elif kind == "fkp":
            # Les FKP comencen a la primera frontera de 512 despres del text.
            for _ in range(rng.randint(1, 6)):
                word[rng.randrange(0x600, 0xA00)] = rng.choice(
                    [0, 1, 2, 0x7F, 0xFF, rng.randrange(256)]
                )
        else:
            for _ in range(rng.randint(1, 4)):
                streams["Data"][rng.randrange(0x40, 0x60)] = rng.randrange(256)
        data = build_cfb({name: bytes(value) for name, value in streams.items()})
        started = time.monotonic()
        result = extract_doc(data)
        worst = max(worst, time.monotonic() - started)
        assert result.status in (ExtractionStatus.EXTRACTED, ExtractionStatus.FAILED)
        assert result.parser_version == DOC_PARSER_VERSION
    assert worst < 2, f"la variant mes lenta ha trigat {worst:.2f} s"


def test_a_truncated_file_fails_cleanly():
    data = build_doc("Hola\r")

    # Ni tan sols la signatura OLE2 sencera: no es cap .doc.
    _assert_not_word(extract_doc(data[:7]))
    for size in (512, 1024, len(data) // 2):
        _assert_failed(extract_doc(data[:size]), "doc_unreadable")


# --------------------------------------------------------------------------- #
# Limits: un fitxer petit no pot demanar memoria ni temps desmesurats           #
# --------------------------------------------------------------------------- #


def test_overlapping_pieces_cannot_make_a_small_file_demand_gigabytes():
    """600 peces sobre els mateixos 100.000 bytes declaren 60 milions de caracters (abans, mes
    d'1 GB de memoria per un fitxer de 110 KB): no hi caben al flux, i es refusa d'entrada."""
    length = 100_000
    streams = word_streams(("Clausula " * (length // 9 + 1))[: length - 1] + "\r")
    table = streams["1Table"]
    cps = [index * length for index in range(601)]
    pcd = struct.pack("<HIH", 0, (0x400 * 2) | 0x40000000, 0)
    plc = struct.pack("<601I", *cps) + pcd * 600
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    word = with_fib(streams["WordDocument"], ccp_text=600 * length,
                    pairs={33: (len(table), len(clx))})
    data = build_cfb({"WordDocument": word, "1Table": table + clx})

    started = time.monotonic()
    result = extract_doc(data)

    assert time.monotonic() - started < 1
    _assert_failed(result, "doc_unreadable")


def test_more_text_than_the_limit_is_doc_too_large(monkeypatch):
    monkeypatch.setattr(doc_text, "_MAX_TEXT_CHARS", 50)

    _assert_failed(extract_doc(build_doc("x" * 60 + "\r")), "doc_too_large")
    assert extract_doc(build_doc("x" * 40 + "\r")).status == ExtractionStatus.EXTRACTED


def test_too_many_pieces_is_doc_too_large(monkeypatch):
    monkeypatch.setattr(doc_text, "_MAX_PIECES", 2)

    _assert_failed(extract_doc(build_doc([Piece("a"), Piece("b"), Piece("c\r")])), "doc_too_large")


def test_more_pages_than_the_limit_is_too_many_pages(monkeypatch):
    """El mateix limit i el mateix codi que els PDF (`DOCUMENT_MAX_PAGES`)."""
    from app.core.config import settings

    _assert_failed(extract_doc(build_doc("p\x0c" * 5000)), "too_many_pages")

    monkeypatch.setattr(settings, "DOCUMENT_MAX_PAGES", 3)
    _assert_failed(extract_doc(build_doc("u\r\x0cdos\r\x0ctres\r\x0cquatre\r")), "too_many_pages")
    assert extract_doc(build_doc("u\r\x0cdos\r\x0ctres\r")).page_count == 3


def _with_paragraph_table(streams: dict[str, bytes], entries: int, *, room: int) -> bytes:
    """El .doc amb una PlcBtePapx de `entries` entrades que apunten totes a la primera FKP, i
    el flux WordDocument allargat fins a `room` bytes."""
    word = streams["WordDocument"]
    fc, lcb = struct.unpack_from("<II", word, fib_pair(13))
    table = streams["1Table"]
    first, last, pn = struct.unpack_from("<III", table, fc)
    plc = struct.pack(f"<{entries + 1}I", *([first] * entries), last) + struct.pack(
        f"<{entries}I", *([pn] * entries)
    )
    word = with_fib(word, pairs={13: (len(table), len(plc))})
    return build_cfb({"WordDocument": word.ljust(room, b"\x00"), "1Table": table + plc})


_SMALL_TABLE = row("A", "B") + row("C", "") + "Fi\r"


def test_a_paragraph_page_listed_many_times_is_read_once(monkeypatch):
    """Cada FKP i cada grpprl d'una FKP es llegeixen un sol cop (abans, una vegada per entrada:
    812 KB feien 24 s de CPU)."""
    calls: list[int] = []
    original = doc_text._Properties.paragraph

    def counting(self, grpprl, **kwargs):
        calls.append(len(grpprl))
        return original(self, grpprl, **kwargs)

    monkeypatch.setattr(doc_text._Properties, "paragraph", counting)
    data = _with_paragraph_table(word_streams(_SMALL_TABLE), 100, room=128 * 512)

    assert _lines(extract_doc(data)) == ["| A | B |", "| C |  |", "Fi"]
    assert len(calls) == 2  # les propietats de cel·la i les de final de fila


def test_a_paragraph_table_with_more_entries_than_pages_falls_back_at_once():
    """El cas de la revisio: 100.000 entrades en un flux on caben poques FKP. No es pot llegir:
    regla de reserva, i de seguida."""
    data = _with_paragraph_table(word_streams(_SMALL_TABLE), 100_000, room=0)

    started = time.monotonic()
    result = extract_doc(data)

    assert time.monotonic() - started < 1
    assert _lines(result) == ["| A | B |", "| C |", "Fi"]


def test_when_the_property_budget_runs_out_tables_use_the_reserve_rule(monkeypatch):
    monkeypatch.setattr(doc_text, "_MAX_PROPERTY_WORK", 3)

    assert _lines(extract_doc(build_doc(_SMALL_TABLE))) == ["| A | B |", "| C |", "Fi"]


def test_too_many_deleted_ranges_are_not_discarded(monkeypatch):
    """Cada tram suprimit parteix una peca: amb massa trams (un fitxer fet a mida), no se'n
    descarta cap, com si les propietats de caracter no es poguessin llegir."""
    pieces = [
        Piece("Si "), Piece("no ", deleted=True), Piece("i "), Piece("tampoc\r", deleted=True)
    ]
    assert _lines(extract_doc(build_doc(pieces))) == ["Si i"]

    monkeypatch.setattr(doc_text, "_MAX_DELETED_RANGES", 1)
    assert _lines(extract_doc(build_doc(pieces))) == ["Si no i tampoc"]


# --------------------------------------------------------------------------- #
# Rendiment                                                                     #
# --------------------------------------------------------------------------- #


def _large_document(*, properties: bool) -> bytes:
    paragraph = "Clausula de solvencia economica i tecnica del contracte. " * 20
    pieces = [
        Piece(
            f"{paragraph}{index}\r" + row(f"Lot {index}", "", "12,50 €")
            + f"Camp \x13 PAGE \x14{index}\x15.\r",
            compressed=index % 3 != 0,
        )
        for index in range(1000)
    ]
    return build_doc(pieces, scramble=True, paragraph_properties=properties)


@pytest.mark.parametrize("properties", [False, True], ids=["reserva", "propietats"])
def test_a_large_fragmented_document_is_read_in_linear_time(properties):
    """1.000 peces, ~1,1 milions de caracters, files de taula i camps: sense res quadratic,
    tant amb la regla de reserva com llegint les propietats de paragraf (5.000 paragrafs)."""
    data = _large_document(properties=properties)

    started = time.monotonic()
    result = extract_doc(data)
    elapsed = time.monotonic() - started

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.text_char_count > 1_100_000
    # Sense salts: pagines virtuals (`doc-v2`), i el darrer lot a la darrera.
    assert result.page_count > 300
    expected = "| Lot 999 |  | 12,50 € |" if properties else "| Lot 999 |"
    assert expected in result.pages[-1].text
    assert elapsed < 5, f"{elapsed:.2f} s per a {result.text_char_count} caracters"


# --------------------------------------------------------------------------- #
# Integracio amb el dispatcher                                                  #
# --------------------------------------------------------------------------- #


def test_the_dispatcher_reads_msword_with_doc_v1():
    import asyncio

    from app.services.document_parsers import extract_text_async, parser_version_for

    assert parser_version_for(DOC) == "doc-v2"
    result = asyncio.run(extract_text_async(DOC, build_doc("Hola\r")))
    assert result.parser_version == "doc-v2"
    assert result.pages[0].text == "Hola"
    assert doc_text.EXTRACTORS == {DOC: extract_doc}


def test_msword_is_now_text_extractable_but_generic_ole2_is_not():
    from app.core.config import settings

    assert DOC in settings.document_text_extraction_mime_types
    assert "application/x-ole-storage" not in settings.document_text_extraction_mime_types
