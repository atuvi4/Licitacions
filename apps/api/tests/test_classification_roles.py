"""Rols de document i coherencia del conjunt (capa de classificacio).

Funcio pura: aquests tests no toquen cap base de dades. El cas real que
l'originen es l'expedient de l'Escala (`test_classification_gold_lescala.py`); aqui
cada regla es prova sola, amb textos minims.
"""

from __future__ import annotations

import hashlib
import io
import zipfile

from app.classification import ooxml_text
from app.classification.roles import (
    DocumentFacts,
    classify_documents,
    content_role,
    declared_code,
    filename_role,
)
from app.models.enums import (
    ClassificationFindingCode,
    DocumentRole,
    FindingSeverity,
    RoleConfidence,
    TenderDocumentType,
)

PDF = "application/pdf"
FILLER = " ".join(["Clausula amb text suficient per comparar documents."] * 10)

PCAP_PAGE = (
    "Ajuntament de Mostra\nPLEC DE CLAUSULES ADMINISTRATIVES PARTICULARS\n"
    "d'acord amb el Plec de Prescripcions Tecniques Particulars. " + FILLER
)
PPT_PAGE = (
    "Contracte 2027 | Plec de prescripcions tecniques\nControl Valor\nCodi DOC-02\n"
    "Estat: inventaris vinculats al DOC-04. " + FILLER
)
QUADRE_PAGE = (
    "Contracte 2027 | Quadre de caracteristiques i clausules\nControl Valor\nCodi DOC-03\n"
    "Contingut per integrar al plec de clausules administratives particulars. " + FILLER
)


def facts(position: int, filename: str, *pages: str, mime: str = PDF, sha: str | None = None):
    body = "\x00".join(pages) or filename
    return DocumentFacts(
        key=f"m{position}",
        position=position,
        path=filename,
        filename=filename,
        mime_type=mime,
        sha256=sha or hashlib.sha256(f"{position}{body}".encode()).hexdigest(),
        pages=tuple(pages) if pages else None,
        text_source="extraction" if pages else None,
    )


def codes(result, key=None):
    return [(f.code, f.severity) for f in result.findings if key is None or f.key == key]


# --------------------------------------------------------------------------- #
# Senyals                                                                      #
# --------------------------------------------------------------------------- #


def test_the_first_title_wins_even_if_the_page_cites_another_document():
    role, position, confidence, title = content_role(PCAP_PAGE)
    assert role is DocumentRole.PCAP
    assert confidence is RoleConfidence.HIGH
    assert title == "PLEC DE CLAUSULES ADMINISTRATIVES PARTICULARS"

    role, *_ = content_role(QUADRE_PAGE)
    assert role is DocumentRole.CHARACTERISTICS_TABLE


def test_a_title_far_from_the_top_is_only_medium():
    page = "Portada. " + "x " * 400 + "Plec de prescripcions tecniques"
    role, position, confidence, _ = content_role(page)
    assert role is DocumentRole.PPT
    assert position > 600
    assert confidence is RoleConfidence.MEDIUM


def test_spanish_titles_are_recognised():
    assert content_role("PLIEGO DE PRESCRIPCIONES TÉCNICAS")[0] is DocumentRole.PPT
    assert content_role("Cuadro de características del contrato")[0] is (
        DocumentRole.CHARACTERISTICS_TABLE
    )
    assert content_role("Anuncio de licitación")[0] is DocumentRole.NOTICE


def test_an_approval_resolution_needs_the_body_that_adopts_it():
    acord = "ACORD\nLa Junta de govern local en sessio de 16 de setembre va adoptar l'acord"
    assert content_role(acord)[0] is DocumentRole.APPROVAL_RESOLUTION
    # "d'acord amb" no es cap acord.
    assert content_role("D'acord amb la Junta de govern local.") is None


def test_filenames():
    assert filename_role("PCAP (1).pdf") is DocumentRole.PCAP
    assert filename_role("PPT (1).pdf") is DocumentRole.PPT
    assert filename_role("03_Quadre_Caracteristiques_i_Clausules_Especifiques.pdf") is (
        DocumentRole.CHARACTERISTICS_TABLE
    )
    assert filename_role("04_Annex_Mestre_Inventaris_Ubicacions_FTTH.pdf") is (
        DocumentRole.INVENTORY_ANNEX
    )
    assert filename_role("05_Model_Oferta_Economica_i_Criteris_Automatics.pdf") is (
        DocumentRole.OFFER_MODEL
    )
    assert filename_role("Informe de necessitat.pdf") is DocumentRole.JUSTIFICATION_REPORT
    assert filename_role("Acord JGL PRP2026-2261.docx") is DocumentRole.APPROVAL_RESOLUTION
    assert filename_role("DEUC.pdf") is DocumentRole.ADMINISTRATIVE_FORM
    assert filename_role("06_Plantilla_Formules_Licitacio.xlsx") is None


def test_declared_code_is_read_from_the_control_block_only():
    assert declared_code(PPT_PAGE) == "DOC-02"
    assert declared_code("Estat: inventaris vinculats al DOC-04") is None
    assert declared_code("Codi: DOC-7") == "DOC-07"


def test_a_spreadsheet_is_recognised_by_its_labels():
    text = "# Economia\nBaixa percentual i puntuacio economica per bloc\nPreu de sortida | Oferta"
    role, _, confidence, _ = content_role(text, spreadsheet=True)
    assert (role, confidence) == (DocumentRole.OFFER_MODEL, RoleConfidence.MEDIUM)
    # El mateix text en un PDF no val: un PDF te titol.
    assert content_role(text) is None


# --------------------------------------------------------------------------- #
# Decisio per document                                                         #
# --------------------------------------------------------------------------- #


def test_content_and_filename_agreeing_give_a_confident_role_that_reaches_analysis():
    result = classify_documents([facts(0, "PCAP.pdf", PCAP_PAGE), facts(1, "PPT.pdf", PPT_PAGE)])
    pcap = result.decisions["m0"]
    assert (pcap.role, pcap.confidence) == (DocumentRole.PCAP, RoleConfidence.HIGH)
    assert pcap.document_type is TenderDocumentType.ADMINISTRATIVE_TERMS
    assert result.decisions["m1"].document_type is TenderDocumentType.TECHNICAL_TERMS


def test_the_filename_alone_is_never_enough_to_reach_analysis():
    result = classify_documents([facts(0, "PPT.pdf", "Portada sense titol. " + FILLER)])
    ppt = result.decisions["m0"]
    assert (ppt.role, ppt.confidence) == (DocumentRole.PPT, RoleConfidence.MEDIUM)
    assert ppt.document_type is TenderDocumentType.OTHER


def test_a_scanned_pdf_keeps_the_filename_role_with_low_confidence_and_says_so():
    result = classify_documents([facts(0, "PPT.pdf", "", ""), facts(1, "PCAP.pdf", PCAP_PAGE)])
    ppt = result.decisions["m0"]
    assert (ppt.role, ppt.confidence, ppt.text_available) == (
        DocumentRole.PPT,
        RoleConfidence.LOW,
        False,
    )
    assert (ClassificationFindingCode.NO_TEXT, FindingSeverity.MEDIUM) in codes(result, "m0")


def test_a_file_that_says_nothing_is_other_and_undetermined():
    result = classify_documents([facts(0, "document.pdf", "Text qualsevol. " + FILLER)])
    assert result.decisions["m0"].role is DocumentRole.OTHER
    assert (ClassificationFindingCode.ROLE_UNDETERMINED, FindingSeverity.LOW) in codes(result, "m0")


def test_a_pcap_that_starts_with_its_characteristics_table_is_not_a_mismatch():
    result = classify_documents([facts(0, "PCAP.pdf", QUADRE_PAGE), facts(1, "PPT.pdf", PPT_PAGE)])
    assert result.decisions["m0"].role is DocumentRole.CHARACTERISTICS_TABLE
    assert ClassificationFindingCode.TITLE_CONTENT_MISMATCH not in [c for c, _ in codes(result)]


def test_formats_the_analysis_cannot_read_are_flagged_low():
    result = classify_documents([facts(0, "Acord.docx", "Acord de la Junta", mime=ooxml_text.DOCX)])
    assert (ClassificationFindingCode.FORMAT_NOT_ANALYZABLE, FindingSeverity.LOW) in codes(
        result, "m0"
    )


# --------------------------------------------------------------------------- #
# El conjunt de documents                                                     #
# --------------------------------------------------------------------------- #


def test_a_file_whose_title_lies_about_its_content():
    """El cas de l'Escala: un "annex d'inventaris" que porta el quadre."""
    result = classify_documents(
        [
            facts(0, "04_Annex_Mestre_Inventaris.pdf", QUADRE_PAGE),
            facts(1, "PPT.pdf", PPT_PAGE),
            facts(2, "PCAP.pdf", PCAP_PAGE),
        ]
    )
    annex = result.decisions["m0"]
    assert annex.role is DocumentRole.CHARACTERISTICS_TABLE  # mana el contingut
    assert annex.filename_role is DocumentRole.INVENTORY_ANNEX
    mismatch = next(
        f for f in result.findings if f.code is ClassificationFindingCode.TITLE_CONTENT_MISMATCH
    )
    assert mismatch.severity is FindingSeverity.HIGH
    assert mismatch.detail["title_line"].startswith("Contracte 2027 | Quadre")


def test_two_different_files_with_the_same_text_keep_the_one_that_says_what_it_is():
    result = classify_documents(
        [
            facts(0, "04_Annex_Mestre_Inventaris.pdf", QUADRE_PAGE, "pagina 2"),
            facts(1, "03_Quadre_Caracteristiques.pdf", QUADRE_PAGE, "pagina  2"),
            facts(2, "PPT.pdf", PPT_PAGE),
            facts(3, "PCAP.pdf", PCAP_PAGE),
        ]
    )
    copy = next(f for f in result.findings if f.code is ClassificationFindingCode.DUPLICATE_TEXT)
    assert (copy.key, copy.related_key, copy.severity) == ("m0", "m1", FindingSeverity.HIGH)
    assert result.decisions["m0"].duplicate_of == "m1"
    # La copia no entra a l'analisi (duplicaria requisits); l'original si.
    assert result.decisions["m0"].document_type is TenderDocumentType.OTHER
    assert result.decisions["m1"].document_type is TenderDocumentType.ADMINISTRATIVE_TERMS
    # El mateix codi en dues copies no es un segon problema.
    assert ClassificationFindingCode.DUPLICATE_INTERNAL_CODE not in [c for c, _ in codes(result)]


def test_the_same_bytes_twice():
    result = classify_documents(
        [
            facts(0, "PCAP.pdf", PCAP_PAGE, sha="a" * 64),
            facts(1, "PCAP (1).pdf", PCAP_PAGE, sha="a" * 64),
            facts(2, "PPT.pdf", PPT_PAGE),
        ]
    )
    assert codes(result, "m1") == [
        (ClassificationFindingCode.DUPLICATE_BYTES, FindingSeverity.MEDIUM)
    ]
    assert result.decisions["m1"].document_type is TenderDocumentType.OTHER
    assert result.decisions["m0"].document_type is TenderDocumentType.ADMINISTRATIVE_TERMS


def test_a_referenced_internal_code_that_no_file_declares():
    result = classify_documents([facts(0, "PPT.pdf", PPT_PAGE), facts(1, "PCAP.pdf", PCAP_PAGE)])
    missing = next(
        f
        for f in result.findings
        if f.code is ClassificationFindingCode.REFERENCED_DOCUMENT_MISSING
    )
    assert missing.severity is FindingSeverity.HIGH
    assert missing.detail["code"] == "DOC-04"
    assert missing.detail["declared_codes"] == ["DOC-02"]
    reference = missing.detail["references"][0]
    assert (reference["document"], reference["page"]) == ("m0", 1)
    assert "DOC-04" in reference["excerpt"]


def test_without_any_declared_code_there_is_no_missing_reference_check():
    page = "Plec de prescripcions tecniques. Vegeu el DOC-04. " + FILLER
    result = classify_documents([facts(0, "PPT.pdf", page), facts(1, "PCAP.pdf", PCAP_PAGE)])
    assert ClassificationFindingCode.REFERENCED_DOCUMENT_MISSING not in [
        c for c, _ in codes(result)
    ]


def test_the_same_code_on_two_different_texts():
    other = QUADRE_PAGE.replace("clausules administratives", "clausules d'una altra versio")
    result = classify_documents(
        [
            facts(0, "Quadre v1.pdf", QUADRE_PAGE),
            facts(1, "Quadre v2.pdf", other),
            facts(2, "PPT.pdf", PPT_PAGE.replace("DOC-04", "DOC-03")),
            facts(3, "PCAP.pdf", PCAP_PAGE),
        ]
    )
    dup = next(
        f for f in result.findings if f.code is ClassificationFindingCode.DUPLICATE_INTERNAL_CODE
    )
    assert (dup.key, dup.related_key, dup.detail) == ("m1", "m0", {"code": "DOC-03"})


def test_a_package_without_pcap_or_ppt_says_so():
    result = classify_documents([facts(0, "Informe de necessitat.pdf", "Memoria justificativa")])
    missing = [
        f.detail["role"]
        for f in result.findings
        if f.code is ClassificationFindingCode.REQUIRED_ROLE_MISSING
    ]
    assert missing == [DocumentRole.PCAP, DocumentRole.PPT]


def test_findings_come_most_severe_first():
    result = classify_documents(
        [facts(0, "document.pdf", "Res. " + FILLER), facts(1, "PPT.pdf", PPT_PAGE)]
    )
    order = [f.severity for f in result.findings]
    assert order == sorted(order, key=list(FindingSeverity).index)


# --------------------------------------------------------------------------- #
# Lectura d'OOXML per classificar                                              #
# --------------------------------------------------------------------------- #


def _ooxml(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        for name, xml in parts.items():
            zf.writestr(name, xml)
    return buffer.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def test_docx_paragraphs_are_read_in_order():
    data = _ooxml(
        {
            "word/document.xml": (
                f"<w:document {W}><w:body>"
                "<w:p><w:r><w:t>ACORD</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>La Junta de govern </w:t></w:r><w:r><w:t>local</w:t></w:r></w:p>"
                "</w:body></w:document>"
            )
        }
    )
    assert ooxml_text.read_text(data, ooxml_text.DOCX) == "ACORD\nLa Junta de govern local"


def test_xlsx_sheet_names_and_cell_texts_are_read():
    data = _ooxml(
        {
            "xl/workbook.xml": (
                f'<workbook {S}><sheets><sheet name="Economia"/></sheets></workbook>'
            ),
            "xl/sharedStrings.xml": (f"<sst {S}><si><t>Baixa</t></si><si><t>Punts</t></si></sst>"),
            "xl/worksheets/sheet1.xml": (
                f'<worksheet {S}><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c>'
                '<c><v>8</v></c><c t="inlineStr"><is><t>Preu</t></is></c></row>'
                "</sheetData></worksheet>"
            ),
        }
    )
    assert ooxml_text.read_text(data, ooxml_text.XLSX) == "# Economia\nBaixa | Punts | Preu"


def test_hostile_or_broken_ooxml_gives_no_text_instead_of_failing():
    bomb = _ooxml(
        {
            "word/document.xml": (
                '<!DOCTYPE w [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
                f"<w:document {W}><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p>"
                "</w:body></w:document>"
            )
        }
    )
    assert ooxml_text.read_text(bomb, ooxml_text.DOCX) is None
    assert ooxml_text.read_text(b"PK\x03\x04 trencat", ooxml_text.DOCX) is None
    assert ooxml_text.read_text(b"%PDF-1", PDF) is None
