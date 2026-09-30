"""Paquets ZIP sintetics per provar la recepcio i la classificacio.

`lescala_twin()` reprodueix l'estructura i les incoherencies del paquet real de
l'Escala (Telecomunicacions 2027) amb textos minims generats aqui, de manera
que les asserts del gold corren a qualsevol maquina, tambe sense els fitxers
reals (que no son al repositori):

* els mateixos noms de fitxer i la mateixa carpeta "Resta Documentació";
* capcaleres amb el bloc de control i el `Codi DOC-0N` de cada document;
* el fitxer `04_Annex_Mestre_Inventaris…` porta el TEXT del DOC-03 en bytes
  diferents (com al real: mateixa mida, MD5 diferent);
* el PPT cita el DOC-04, que no hi es;
* un XLSX de formules i un DOCX de l'acord de la Junta de Govern.
"""

from __future__ import annotations

import io
import zipfile

from tests.fixtures.pdf_builder import build_pdf

_BODY = " ".join(["Text del document amb prou contingut per comparar-lo."] * 8)

PPT = (
    "Telecomunicacions 2027 | Plec de prescripcions tecniques\nPagina 1\n"
    "Plec de prescripcions tecniques particulars\nControl Valor\nCodi DOC-02\n"
    "Estat Versio tecnica completa per a licitacio; inventaris vinculats al DOC-04.\n" + _BODY
)
QUADRE = (
    "Telecomunicacions 2027 | Quadre de caracteristiques i clausules\nPagina 1\n"
    "Quadre de caracteristiques i clausules especifiques\n"
    "Contingut per integrar al plec de clausules administratives particulars municipal\n"
    "Control Valor\nCodi DOC-03\n" + _BODY
)
MODEL = (
    "Telecomunicacions 2027 | Model d'oferta\nPagina 1\n"
    "Model d'oferta economica i criteris automatics\nControl Valor\nCodi DOC-05\n" + _BODY
)
MEMORIA = (
    "Telecomunicacions 2027 | Memoria justificativa i estudi economic\nPagina 1\n"
    "Control Valor\nCodi DOC-01\n" + _BODY
)
PCAP = (
    "Ajuntament de l'Escala\nCLAUSULA PRIMERA. Objecte, qualificacio i CPV\n"
    "PLEC DE CLAUSULES ADMINISTRATIVES PARTICULARS QUE HAN DE REGIR LA\n"
    "d'acord amb el Plec de Prescripcions Tecniques Particulars.\n" + _BODY
)


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


def docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    return _zip(
        [
            ("[Content_Types].xml", b"<Types/>"),
            (
                "word/document.xml",
                (
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
                ).encode(),
            ),
        ]
    )


def xlsx(sheet: str, *cells: str) -> bytes:
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    row = "".join(f'<c t="inlineStr"><is><t>{c}</t></is></c>' for c in cells)
    return _zip(
        [
            ("[Content_Types].xml", b"<Types/>"),
            (
                "xl/workbook.xml",
                f'<workbook {ns}><sheets><sheet name="{sheet}"/></sheets></workbook>'.encode(),
            ),
            (
                "xl/worksheets/sheet1.xml",
                f"<worksheet {ns}><sheetData><row>{row}</row></sheetData></worksheet>".encode(),
            ),
        ]
    )


def lescala_twin() -> bytes:
    quadre = build_pdf([QUADRE, "Criteris d'adjudicacio"])
    # Mateix text, bytes diferents: el que distingeix una copia mal pujada d'un
    # fitxer repetit.
    annex_mal_pujat = quadre + b"\n% copia\n"
    return _zip(
        [
            ("05_Model_Oferta_Economica_i_Criteris_Automatics.pdf", build_pdf([MODEL])),
            ("04_Annex_Mestre_Inventaris_Ubicacions_FTTH_Telefonia.pdf", annex_mal_pujat),
            ("PPT (1).pdf", build_pdf([PPT, "Bloc 1 -- Connectivitat"])),
            ("03_Quadre_Caracteristiques_i_Clausules_Especifiques.pdf", quadre),
            (
                "06_Plantilla_Formules_Licitacio_Telecomunicacions (Definitiu).xlsx",
                xlsx(
                    "Economia",
                    "Baixa percentual i puntuacio economica per bloc",
                    "Preu de sortida",
                    "Oferta",
                ),
            ),
            ("PCAP (1).pdf", build_pdf([PCAP, "CLAUSULA SEGONA"])),
            ("Resta Documentació/Informe de necessitat.pdf", build_pdf([MEMORIA])),
            (
                "Resta Documentació/Acord JGL PRP2026-2261.docx",
                docx(
                    "ACORD",
                    "La Junta de govern local d'aquest Ajuntament en sessio celebrada "
                    "en data 16 de setembre de 2026 va adoptar el seguent acord:",
                ),
            ),
        ]
    )
