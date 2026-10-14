"""Lector XLSX `xlsx-v2` (QUALITY-01): rangs combinats, formules i procedencia.

Defecte de `xlsx-v1` (auditoria del primer run real complet de L'Escala, 2026-10-14): el lector
emetia totes les cel·les no buides, tambe les INTERIORS d'un rang combinat. Excel nomes mostra la
cel·la ancora (dalt a l'esquerra); a la plantilla oficial de L'Escala, A1 diu «— 32 punts» i les
cel·les ocultes B1:F1 conserven «— 35 punts» d'una versio anterior. El model en va treure dues
incerteses falses (32 vs 35, 28 vs 25).

El test amb el fitxer oficial nomes s'executa si `LESCALA_OFFICIAL_XLSX` apunta al .xlsx oficial
(no es versiona al repositori).
"""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
from pathlib import Path

import pytest

from app.classification import xlsx_merged
from app.models.enums import ExtractionStatus
from app.services import office_text
from tests.test_office_text import PKG_REL, R, S, _zip


def _book(sheets: list[tuple[str, str, str]], shared: list[str]) -> bytes:
    """`sheets` = [(nom, files XML, mergeCells XML)]: el `mergeCells` va despres de `sheetData`,
    com en un fitxer real."""
    workbook_sheets = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _, _) in enumerate(sheets, 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="worksheet" Target="worksheets/s{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    parts = {
        "xl/workbook.xml": f"<workbook {S} {R}><sheets>{workbook_sheets}</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PKG_REL}">{rels}</Relationships>',
        "xl/sharedStrings.xml": f"<sst {S}>"
        + "".join(f"<si><t>{s}</t></si>" for s in shared)
        + "</sst>",
    }
    for i, (_, rows, merges) in enumerate(sheets, 1):
        merge_xml = f"<mergeCells>{merges}</mergeCells>" if merges else ""
        parts[f"xl/worksheets/s{i}.xml"] = (
            f"<worksheet {S} {R}><sheetData>{rows}</sheetData>{merge_xml}</worksheet>"
        )
    return _zip(parts)


ESCALA_LIKE = (
    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
    '<c r="C1" t="s"><v>1</v></c>'
    '<c r="D1" t="s"><v>1</v></c><c r="E1" t="s"><v>1</v></c><c r="F1" t="s"><v>1</v></c></row>'
    '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>5</v></c></row>'
)
ESCALA_SHARED = [
    "Criteris automàtics no econòmics — 32 punts",
    "Criteris automàtics no econòmics — 35 punts",
    "C1",
]


def test_reprodueix_i_corregeix_el_defecte_32_vs_35():
    result = office_text.extract_xlsx(
        _book([("Automatics", ESCALA_LIKE, '<mergeCell ref="A1:F1"/>')], ESCALA_SHARED)
    )
    assert result.status == ExtractionStatus.EXTRACTED
    assert result.parser_version == "xlsx-v2"
    lines = result.pages[0].text.split("\n")
    assert lines == [
        "# Full: Automatics",
        "Cel·les combinades: A1:F1 (visible A1; 5 cel·les interiors amb contingut ocult no llegit)",
        "Fila 1 | A1 [A1:F1]: Criteris automàtics no econòmics — 32 punts",
        "Fila 2 | A2: C1 | B2: 5",
    ]
    assert "35 punts" not in result.pages[0].text


def test_una_formula_interior_d_un_rang_combinat_no_es_llegeix_i_la_de_l_ancora_si():
    rows = (
        '<row r="1"><c r="A1"><f>SUM(A3:A4)</f><v>7</v></c>'
        '<c r="B1"><f>A3*99</f><v>297</v></c></row>'
        '<row r="3"><c r="A3"><v>3</v></c></row><row r="4"><c r="A4"><v>4</v></c></row>'
    )
    text = (
        office_text.extract_xlsx(_book([("F", rows, '<mergeCell ref="A1:B2"/>')], [])).pages[0].text
    )
    assert "Fila 1 | A1 [A1:B2]: =SUM(A3:A4) → 7" in text
    assert "A3*99" not in text and "297" not in text
    assert (
        "Cel·les combinades: A1:B2 (visible A1; 1 cel·les interiors amb contingut ocult no llegit)"
        in text
    )


def test_rang_combinat_sense_contingut_ocult_no_diu_res_d_interiors():
    rows = '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
    text = (
        office_text.extract_xlsx(_book([("F", rows, '<mergeCell ref="A1:C1"/>')], ["Títol"]))
        .pages[0]
        .text
    )
    assert "Cel·les combinades: A1:C1 (visible A1)" in text
    assert "Fila 1 | A1 [A1:C1]: Títol" in text


def test_formules_valors_introduits_i_valors_desats_es_distingeixen():
    rows = (
        '<row r="1"><c r="A1"><v>10</v></c><c r="B1"><f>A1*2</f><v>20</v></c>'
        '<c r="C1"><f>B1+Inputs!C4</f></c><c r="D1" t="s"><v>0</v></c></row>'
    )
    text = office_text.extract_xlsx(_book([("F", rows, "")], ["=no és una fórmula"])).pages[0].text
    assert text.split("\n")[1] == (
        "Fila 1 | A1: 10 | B1: =A1*2 → 20 | C1: =B1+Inputs!C4 (sense valor desat) "
        "| D1: '=no és una fórmula"
    )


def test_formula_compartida_conserva_la_referencia_a_la_mestra():
    rows = (
        '<row r="1"><c r="A1"><f t="shared" ref="A1:A2" si="0">B1+1</f><v>3</v></c></row>'
        '<row r="2"><c r="A2"><f t="shared" si="0"/><v>4</v></c></row>'
    )
    lines = office_text.extract_xlsx(_book([("F", rows, "")], [])).pages[0].text.split("\n")
    assert lines[1:] == ["Fila 1 | A1: =B1+1 → 3", "Fila 2 | A2: =⟨formula compartida de A1⟩ → 4"]


def test_rang_combinat_mal_format_s_ignora_sense_perdre_cel_les():
    rows = '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>0</v></c></row>'
    text = (
        office_text.extract_xlsx(
            _book([("F", rows, '<mergeCell ref="B1"/><mergeCell ref="Z9:A1"/>')], ["x"])
        )
        .pages[0]
        .text
    )
    assert "Fila 1 | A1: x | B1: x" in text
    assert "Cel·les combinades" not in text


def test_un_rang_combinat_desmesurat_falla_sense_llancar():
    rows = '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
    result = office_text.extract_xlsx(
        _book([("F", rows, '<mergeCell ref="A1:XFD1048576"/>')], ["x"])
    )
    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "xlsx_unreadable"


def test_mateixos_bytes_mateix_text():
    data = _book([("Automatics", ESCALA_LIKE, '<mergeCell ref="A1:F1"/>')], ESCALA_SHARED)
    first, second = office_text.extract_xlsx(data), office_text.extract_xlsx(data)
    assert [p.text_hash for p in first.pages] == [p.text_hash for p in second.pages]


# --------------------------------------------------------------------------- #
# Fitxer oficial de L'Escala (opcional)                                         #
# --------------------------------------------------------------------------- #

OFFICIAL = os.environ.get("LESCALA_OFFICIAL_XLSX")
OFFICIAL_SHA = "996aa185dec3ff8e62d6b4b62f23c4d9e8a2adf843aefb30b506bd0056ef426c"


@pytest.mark.skipif(
    not OFFICIAL, reason="LESCALA_OFFICIAL_XLSX no definit (fitxer oficial fora del repositori)"
)
def test_plantilla_oficial_de_l_escala():
    data = Path(OFFICIAL).read_bytes()
    assert hashlib.sha256(data).hexdigest() == OFFICIAL_SHA, "no es el fitxer oficial verificat"
    result = office_text.extract_xlsx(data)
    assert result.status == ExtractionStatus.EXTRACTED and result.parser_version == "xlsx-v2"
    pages = {p.text.split("\n")[0]: p.text for p in result.pages}
    automatics, judici = pages["# Full: Automatics"], pages["# Full: Judici"]
    assert "A1 [A1:F1]: Criteris automàtics no econòmics — 32 punts" in automatics
    assert "A1 [A1:E1]: Criteris sotmesos a judici de valor — 28 punts" in judici
    every = "\n".join(p.text for p in result.pages)
    assert "35 punts" not in every and "— 25 punts" not in every
    # Cap formula d'una cel·la visible es perd: totes les <f> fora d'interiors de rangs combinats.
    with zipfile.ZipFile(Path(OFFICIAL)) as zf:
        sheet_xml = [
            zf.read(n).decode("utf-8") for n in zf.namelist() if n.startswith("xl/worksheets/sheet")
        ]
    visible_formulas = 0
    for xml in sheet_xml:
        interior = set()
        for ref in re.findall(r'<mergeCell ref="([A-Z]+\d+:[A-Z]+\d+)"', xml):
            interior |= set(_cells(ref)[1:])
        for ref in re.findall(r'<c r="([A-Z]+\d+)"[^>]*>(?:(?!</c>).)*<f', xml, re.S):
            visible_formulas += ref not in interior
    assert every.count("=") >= visible_formulas


def _cells(ref: str) -> list[str]:
    start, end = ref.split(":")
    (c1, r1), (c2, r2) = (re.match(r"([A-Z]+)(\d+)", x).groups() for x in (start, end))
    n1, n2 = xlsx_merged.column_number(c1), xlsx_merged.column_number(c2)
    return [
        f"{xlsx_merged.column_letters(c)}{r}"
        for r in range(int(r1), int(r2) + 1)
        for c in range(n1, n2 + 1)
    ]
