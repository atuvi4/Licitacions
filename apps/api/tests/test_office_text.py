"""Lectura determinista de DOCX, XLSX i XML per a l'analisi (`docx-v1`, `xlsx-v2`, `xml-v1`).

Els fitxers es construeixen aqui mateix (OOXML minim): cap document real a git.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.models.enums import ExtractionQuality, ExtractionStatus
from app.services import office_text
from app.services.document_parsers import extract_text_async, parser_version_for

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, body in parts.items():
            zf.writestr(name, body)
    return buffer.getvalue()


def _docx(body: str, styles: str | None = None) -> bytes:
    parts = {"word/document.xml": f"<w:document {W}><w:body>{body}</w:body></w:document>"}
    if styles is not None:
        parts["word/styles.xml"] = f"<w:styles {W}>{styles}</w:styles>"
    return _zip(parts)


def _p(text: str, style: str | None = None, extra: str = "") -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r>{extra}</w:p>"


HEADING_STYLES = (
    '<w:style w:styleId="Ttol1"><w:name w:val="heading 1"/></w:style>'
    '<w:style w:styleId="Subtitol"><w:name w:val="Subtitol"/>'
    '<w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>'
)


# --------------------------------------------------------------------------- #
# DOCX                                                                          #
# --------------------------------------------------------------------------- #


def test_docx_paragrafs_titols_i_taules_en_ordre():
    body = (
        _p("Acord de la Junta", style="Ttol1")
        + _p("Antecedents", style="Subtitol")
        + _p("Primer. S'aprova l'expedient.")
        + "<w:tbl>"
        + "<w:tr><w:tc>"
        + _p("Concepte")
        + "</w:tc><w:tc>"
        + _p("Import")
        + "</w:tc></w:tr>"
        + "<w:tr><w:tc>"
        + _p("PBL")
        + "</w:tc><w:tc>"
        + _p("204.232,75")
        + "</w:tc></w:tr>"
        + "</w:tbl>"
        + _p("Segon. Publicar-ho.")
    )
    result = office_text.extract_docx(_docx(body, HEADING_STYLES))

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.parser_version == "docx-v1"
    assert result.quality == ExtractionQuality.GOOD
    assert result.page_count == 1
    assert result.pages[0].text.split("\n") == [
        "# Acord de la Junta",
        "## Antecedents",
        "Primer. S'aprova l'expedient.",
        "| Concepte | Import |",
        "| PBL | 204.232,75 |",
        "Segon. Publicar-ho.",
    ]


def test_docx_les_pagines_son_els_salts_desats_al_fitxer():
    hard = '<w:r><w:br w:type="page"/></w:r>'
    rendered = "<w:r><w:lastRenderedPageBreak/></w:r>"
    body = _p("U", extra=hard) + _p("Dos") + f"<w:p>{rendered}<w:r><w:t>Tres</w:t></w:r></w:p>"
    result = office_text.extract_docx(_docx(body))

    assert [page.text for page in result.pages] == ["U", "Dos", "Tres"]
    assert [page.page_number for page in result.pages] == [1, 2, 3]


def test_docx_un_salt_al_principi_no_crea_cap_pagina_buida():
    body = '<w:p><w:r><w:br w:type="page"/><w:lastRenderedPageBreak/><w:t>Sol</w:t></w:r></w:p>'
    result = office_text.extract_docx(_docx(body))
    assert [page.text for page in result.pages] == ["Sol"]


def test_docx_sense_text_es_buit_pero_extret():
    result = office_text.extract_docx(_docx("<w:p/>"))
    assert result.status == ExtractionStatus.EXTRACTED
    assert result.quality == ExtractionQuality.EMPTY
    assert result.text_char_count == 0
    assert result.ocr_candidate is False


def test_docx_malmes_falla_sense_llancar():
    result = office_text.extract_docx(b"no es un zip")
    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "docx_unreadable"
    assert result.parser_version == "docx-v1"


def test_docx_amb_entitats_es_refusa():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "xxxxxxxx">]>'
        f"<w:document {W}><w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>"
    )
    result = office_text.extract_docx(_zip({"word/document.xml": bomb}))
    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "docx_unreadable"


# --------------------------------------------------------------------------- #
# XLSX                                                                          #
# --------------------------------------------------------------------------- #


def _xlsx(
    sheets: list[tuple[str, str]], *, shared: list[str], defined: str = "", tables=None
) -> bytes:
    workbook_sheets = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _) in enumerate(sheets, start=1)
    )
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="worksheet" Target="worksheets/full{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    parts = {
        "xl/workbook.xml": (
            f"<workbook {S} {R}><sheets>{workbook_sheets}</sheets>"
            f"{f'<definedNames>{defined}</definedNames>' if defined else ''}</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PKG_REL}">{rels}</Relationships>',
        "xl/sharedStrings.xml": f"<sst {S}>"
        + "".join(f"<si><t>{s}</t></si>" for s in shared)
        + "</sst>",
    }
    for i, (_, data) in enumerate(sheets, start=1):
        table_parts = ""
        if tables and i in tables:
            parts[f"xl/worksheets/_rels/full{i}.xml.rels"] = (
                f'<Relationships xmlns="{PKG_REL}">'
                f'<Relationship Id="rT1" Type="table" Target="../tables/taula{i}.xml"/>'
                "</Relationships>"
            )
            parts[f"xl/tables/taula{i}.xml"] = tables[i]
            table_parts = '<tableParts count="1"><tablePart r:id="rT1"/></tableParts>'
        parts[f"xl/worksheets/full{i}.xml"] = (
            f"<worksheet {S} {R}><sheetData>{data}</sheetData>{table_parts}</worksheet>"
        )
    return _zip(parts)


def test_xlsx_fulls_celles_formules_i_valors_desats():
    sheet1 = (
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2"><v>10</v></c><c r="B2"><f>A2*2</f><v>20</v></c></row>'
        '<row r="3"><c r="A3" t="b"><v>1</v></c><c r="B3"><f>SUM(B2:B2)</f></c>'
        '<c r="C3" t="inlineStr"><is><t>nota</t></is></c></row>'
    )
    sheet2 = '<row r="1"><c r="A1" t="str"><f>Dades!A1&amp;"x"</f><v>Concepte x</v></c></row>'
    data = _xlsx(
        [("Dades", sheet1), ("Resum", sheet2)],
        shared=["Concepte", "Import"],
        defined='<definedName name="Baixa">Dades!$B$2</definedName>',
    )
    result = office_text.extract_xlsx(data)

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.parser_version == "xlsx-v2"
    assert result.page_count == 2
    assert result.pages[0].text.split("\n") == [
        "# Full: Dades",
        "Nom definit «Baixa»: Dades!$B$2",
        "Fila 1 | A1: Concepte | B1: Import",
        "Fila 2 | A2: 10 | B2: =A2*2 → 20",
        "Fila 3 | A3: TRUE | B3: =SUM(B2:B2) (sense valor desat) | C3: nota",
    ]
    assert result.pages[1].text.split("\n") == [
        "# Full: Resum",
        'Fila 1 | A1: =Dades!A1&"x" → Concepte x',
    ]


def test_xlsx_formula_compartida_no_s_interpreta():
    sheet = (
        '<row r="1"><c r="A1"><f t="shared" ref="A1:A2" si="0">B1+1</f><v>3</v></c></row>'
        '<row r="2"><c r="A2"><f t="shared" si="0"/><v>4</v></c></row>'
    )
    result = office_text.extract_xlsx(_xlsx([("F", sheet)], shared=[]))
    lines = result.pages[0].text.split("\n")
    assert lines[1] == "Fila 1 | A1: =B1+1 → 3"
    assert lines[2] == "Fila 2 | A2: =⟨formula compartida de A1⟩ → 4"


def test_xlsx_taules_amb_nom_i_rang():
    table = (
        f'<table {S} id="1" name="T1" displayName="Preus" ref="A1:B2">'
        '<tableColumns count="2"><tableColumn id="1" name="Concepte"/>'
        '<tableColumn id="2" name="Preu"/></tableColumns></table>'
    )
    sheet = '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
    result = office_text.extract_xlsx(_xlsx([("P", sheet)], shared=["Concepte"], tables={1: table}))
    assert "Taula «Preus»: A1:B2 (columnes: Concepte, Preu)" in result.pages[0].text.split("\n")


def test_xlsx_malmes_falla_sense_llancar():
    result = office_text.extract_xlsx(_zip({"xl/workbook.xml": "<workbook"}))
    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "xlsx_unreadable"


# --------------------------------------------------------------------------- #
# XML                                                                           #
# --------------------------------------------------------------------------- #

ESPD_LIKE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<QualificationApplicationRequest xmlns="urn:espd:request" '
    'xmlns:cbc="urn:cbc" xmlns:cac="urn:cac">'
    '<cbc:ID schemeAgencyID="EU-COM-GROW">2026/3964</cbc:ID>'
    "<cac:ContractingParty><cac:Party><cbc:Name>Ajuntament</cbc:Name></cac:Party></cac:ContractingParty>"
    "<cac:TenderingCriterion><cbc:Name>Volum de negoci</cbc:Name>"
    "<cbc:Description>Volum anual minim</cbc:Description></cac:TenderingCriterion>"
    "</QualificationApplicationRequest>"
)


def test_xml_estructura_espais_de_noms_atributs_i_text():
    result = office_text.extract_xml(ESPD_LIKE.encode())

    assert result.status == ExtractionStatus.EXTRACTED
    assert result.parser_version == "xml-v1"
    # Un element compost de primer nivell, una pagina; els camps simples consecutius,
    # junts. Fronteres del document, no inventades.
    assert result.page_count == 3
    assert result.pages[0].text.split("\n") == [
        "# XML: QualificationApplicationRequest",
        "Espais de noms:",
        "  (per defecte) = urn:espd:request",
        "  cbc = urn:cbc",
        "  cac = urn:cac",
        "cbc:ID [schemeAgencyID=EU-COM-GROW]: 2026/3964",
    ]
    assert result.pages[1].text.split("\n") == [
        "cac:ContractingParty",
        "  cac:Party",
        "    cbc:Name: Ajuntament",
    ]
    assert result.pages[2].text.split("\n") == [
        "cac:TenderingCriterion",
        "  cbc:Name: Volum de negoci",
        "  cbc:Description: Volum anual minim",
    ]


def test_xml_els_camps_simples_consecutius_comparteixen_pagina():
    xml = (
        '<r xmlns:cbc="urn:cbc" xmlns:cac="urn:cac">'
        "<cbc:UBLVersionID>2.2</cbc:UBLVersionID><cbc:ID>X</cbc:ID>"
        "<cac:Party><cbc:Name>A</cbc:Name></cac:Party>"
        "<cbc:Note>N1</cbc:Note><cbc:Note>N2</cbc:Note>"
        "</r>"
    )
    result = office_text.extract_xml(xml.encode())
    assert [page.text.split("\n")[-2:] for page in result.pages] == [
        ["cbc:UBLVersionID: 2.2", "cbc:ID: X"],
        ["cac:Party", "  cbc:Name: A"],
        ["cbc:Note: N1", "cbc:Note: N2"],
    ]


def test_xml_amb_entitats_es_refusa():
    bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "xx">]><r>&a;</r>'
    result = office_text.extract_xml(bomb.encode())
    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "xml_unreadable"


def test_xml_malmes_falla_sense_llancar():
    result = office_text.extract_xml(b"<a><b></a>")
    assert result.status == ExtractionStatus.FAILED
    assert result.error_code == "xml_unreadable"


# --------------------------------------------------------------------------- #
# Encaminament                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mime", "version"),
    [
        (office_text.DOCX, "docx-v1"),
        (office_text.XLSX, "xlsx-v2"),
        ("application/xml", "xml-v1"),
        ("text/xml", "xml-v1"),
        ("application/pdf", "pypdf-v1"),
        ("text/plain", "plaintext-v1"),
    ],
)
def test_cada_tipus_te_la_seva_versio_de_parser(mime, version):
    assert parser_version_for(mime) == version


async def test_extract_text_async_encamina_per_tipus():
    result = await extract_text_async("application/xml", ESPD_LIKE.encode())
    assert result.parser_version == "xml-v1"
    assert result.status == ExtractionStatus.EXTRACTED


def test_mateixos_bytes_mateix_text():
    first = office_text.extract_xml(ESPD_LIKE.encode())
    second = office_text.extract_xml(ESPD_LIKE.encode())
    assert [p.text_hash for p in first.pages] == [p.text_hash for p in second.pages]
