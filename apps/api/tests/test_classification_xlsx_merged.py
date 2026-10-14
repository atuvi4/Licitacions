"""El lector XLSX del classificador nomes llegeix les cel·les que Excel mostra.

Defecte (QUALITY-01, 2026-10-14): `ooxml_text._xlsx_text` llegia totes les cel·les amb
text, tambe les interiors d'un rang combinat. A la plantilla oficial de L'Escala hi
arribaven «— 35 punts» (x5) i «— 25 punts» (x4), ocults sota els «— 32 punts» i
«— 28 punts» visibles. Correccio: el mateix criteri que `xlsx-v2`, compartit a
`app.classification.xlsx_merged`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from app.classification import ooxml_text
from app.classification.roles import DocumentFacts, classify_documents
from app.models.enums import DocumentRole, RoleConfidence
from app.services import office_text

S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _xlsx(sheet: str, shared: list[str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(
            "xl/workbook.xml",
            f'<workbook {S}><sheets><sheet name="Automatics"/></sheets></workbook>',
        )
        if shared is not None:
            items = "".join(f"<si><t>{s}</t></si>" for s in shared)
            zf.writestr("xl/sharedStrings.xml", f"<sst {S}>{items}</sst>")
        zf.writestr("xl/worksheets/sheet1.xml", f"<worksheet {S}>{sheet}</worksheet>")
    return buffer.getvalue()


#: A1:F1 combinat: A1 visible («32 punts»), B1 i F1 interiors amb el valor antic.
HEADER = (
    '<sheetData><row r="1">'
    '<c r="A1" t="s"><v>0</v></c>'
    '<c r="B1" t="s"><v>1</v></c>'
    '<c r="F1" t="inlineStr"><is><t>Antic — 35 punts</t></is></c>'
    "</row>"
    '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>'
    "</sheetData>"
)
SHARED = ["Criteris automàtics — 32 punts", "Criteris automàtics — 35 punts", "Codi", "Punts"]


def test_an_interior_cell_of_a_merged_range_never_reaches_the_classifier():
    data = _xlsx(HEADER + '<mergeCells count="1"><mergeCell ref="A1:F1"/></mergeCells>', SHARED)
    text = ooxml_text.read_text(data, ooxml_text.XLSX)
    assert text == "# Automatics\nCriteris automàtics — 32 punts\nCodi | Punts"
    assert "35 punts" not in text


def test_without_merged_ranges_every_text_cell_is_still_read():
    """Sense rangs combinats, res no canvia: les cel·les B1 i F1 son visibles."""
    text = ooxml_text.read_text(_xlsx(HEADER, SHARED), ooxml_text.XLSX)
    assert text.splitlines()[1] == (
        "Criteris automàtics — 32 punts | Criteris automàtics — 35 punts | Antic — 35 punts"
    )


def test_a_malformed_range_is_ignored_and_a_vertical_one_hides_its_lower_cells():
    sheet = (
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Bloc</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Visible</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>Ocult</t></is></c></row></sheetData>'
        '<mergeCells count="3"><mergeCell ref="A1:A2"/><mergeCell ref="B1"/>'
        '<mergeCell ref="C3:"/></mergeCells>'
    )
    assert ooxml_text.read_text(_xlsx(sheet), ooxml_text.XLSX) == "# Automatics\nBloc | Visible"


def test_an_oversized_merged_range_gives_no_text_instead_of_eating_memory():
    sheet = (
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Model</t></is></c></row>'
        '</sheetData><mergeCells count="1"><mergeCell ref="A1:XFD1048576"/></mergeCells>'
    )
    assert ooxml_text.read_text(_xlsx(sheet), ooxml_text.XLSX) is None


def test_both_readers_share_one_definition_of_an_interior_cell():
    """El classificador i `xlsx-v2` amaguen exactament les mateixes cel·les."""
    data = _xlsx(HEADER + '<mergeCells count="1"><mergeCell ref="A1:F1"/></mergeCells>', SHARED)
    extracted = office_text.extract_xlsx(data)
    page = extracted.pages[0].text
    assert "A1 [A1:F1]: Criteris automàtics — 32 punts" in page
    assert "35 punts" not in page
    assert "35 punts" not in ooxml_text.read_text(data, ooxml_text.XLSX)


def test_refs_with_dollars_lowercase_or_swapped_corners_hide_the_same_cells_in_both_readers():
    merges = '<mergeCells count="1"><mergeCell ref="$f$1:A1"/></mergeCells>'
    data = _xlsx(HEADER + merges, SHARED)
    assert "35 punts" not in ooxml_text.read_text(data, ooxml_text.XLSX)
    page = office_text.extract_xlsx(data).pages[0].text
    assert "35 punts" not in page and "A1 [A1:F1]: Criteris automàtics — 32 punts" in page


def test_the_classifier_merge_budget_is_per_workbook():
    def book(sheets: int) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            names = "".join(f'<sheet name="F{i}"/>' for i in range(1, sheets + 1))
            zf.writestr("xl/workbook.xml", f"<workbook {S}><sheets>{names}</sheets></workbook>")
            for i in range(1, sheets + 1):
                zf.writestr(
                    f"xl/worksheets/sheet{i}.xml",
                    f'<worksheet {S}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>T</t>'
                    '</is></c></row></sheetData><mergeCells count="1">'
                    '<mergeCell ref="A1:A60000"/></mergeCells></worksheet>',
                )
        return buffer.getvalue()

    assert ooxml_text.read_text(book(1), ooxml_text.XLSX) == "# F1\nT"
    assert ooxml_text.read_text(book(2), ooxml_text.XLSX) is None


# --------------------------------------------------------------------------- #
# Fitxer oficial de L'Escala (opcional)                                         #
# --------------------------------------------------------------------------- #

OFFICIAL = os.environ.get("LESCALA_OFFICIAL_XLSX")
OFFICIAL_SHA = "996aa185dec3ff8e62d6b4b62f23c4d9e8a2adf843aefb30b506bd0056ef426c"
GOLD = Path(__file__).resolve().parents[1] / "datasets/classification/lescala-2026.gold.json"


def _gold_entry() -> dict:
    documents = json.loads(GOLD.read_text(encoding="utf-8"))["documents"]
    return next(d for d in documents if d["sha256"] == OFFICIAL_SHA)


def _classify(filename: str, pages: tuple[str, ...], source: str):
    facts = DocumentFacts(
        key="xlsx",
        position=0,
        path=filename,
        filename=filename,
        mime_type=ooxml_text.XLSX,
        sha256=OFFICIAL_SHA,
        pages=pages,
        text_source=source,
    )
    return classify_documents([facts]).decisions["xlsx"]


@pytest.mark.skipif(
    not OFFICIAL, reason="LESCALA_OFFICIAL_XLSX no definit (fitxer oficial fora del repositori)"
)
def test_the_official_lescala_template_is_read_and_classified_as_the_gold_says():
    data = Path(OFFICIAL).read_bytes()
    assert hashlib.sha256(data).hexdigest() == OFFICIAL_SHA, "no es el fitxer oficial verificat"
    text = ooxml_text.read_text(data, ooxml_text.XLSX)
    assert text is not None
    assert "Criteris automàtics no econòmics — 32 punts" in text
    assert "Criteris sotmesos a judici de valor — 28 punts" in text
    assert "35 punts" not in text and "— 25 punts" not in text

    # El cami `ooxml` (XLSX sense extraccio) es el que llegeix aquest lector: el rol ha de ser
    # el del gold. (Amb una extraccio `xlsx-v1` o `xlsx-v2` el classificador nomes mira el
    # primer full, «Inputs», i en surt `other` amb totes dues versions: comportament anterior
    # a QUALITY-01, documentat a l'informe i fora d'aquest abast.)
    gold = _gold_entry()
    decision = _classify(gold["path"], (text,), "ooxml")
    assert decision.role == DocumentRole(gold["role"]) == DocumentRole.OFFER_MODEL
    assert decision.confidence == RoleConfidence(gold["confidence"])
    assert decision.content_role == DocumentRole(gold["content_role"])
    assert decision.filename_role is None and gold["filename_role"] is None
