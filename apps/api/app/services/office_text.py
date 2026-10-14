"""Lectura determinista de DOCX, XLSX i XML per a l'analisi de requisits.

Tres parsers amb versio propia (`docx-v1`, `xlsx-v2`, `xml-v1`), germans de
`pypdf-v1` i `plaintext-v1`: llegeixen els bytes desats, no els modifiquen mai
(l'original i el seu SHA es conserven tal com van arribar) i retornen pagines amb
text citable. Cap OCR, cap model, cap interpretacio: mateixos bytes, mateix text.

**Que es una pagina.** Nomes una frontera que el document ja porta:

* DOCX: els salts de pagina desats al fitxer (el salt explicit `w:br type=page` i
  el darrer que va renderitzar Word, `w:lastRenderedPageBreak`). Sense cap salt,
  una sola pagina. Mai se'n tallen per llargada.
* XLSX: un full, una pagina.
* XML: cada element compost de primer nivell (un fill de l'arrel amb fills), una
  pagina; els elements simples consecutius de primer nivell (camp i valor)
  comparteixen pagina. La primera porta la capcalera amb l'arrel i els espais de
  noms.

**Que se'n llegeix.**

* DOCX: el cos en ordre (paragrafs, titols amb el seu nivell, llistes, taules fila
  a fila, controls de contingut i el text de les equacions). No: capcaleres, peus,
  notes ni comentaris (v1).
* XLSX: noms dels fulls, cel·les no buides amb la seva referencia, formules tal com
  estan escrites i el valor desat quan n'hi ha; noms definits i taules amb el seu
  rang. Una formula compartida que no porta text diu de quina cel·la ve: no es
  tradueix (aixo seria interpretar-la). Tres formes, sempre distingibles: valor
  introduit (`A1: text`), formula amb el valor calculat que Excel va desar
  (`B2: =A2*2 → 20`) i formula sense valor desat (`B3: =SUM(..) (sense valor desat)`:
  no es calcula mai). Un valor introduit que comenca per «=» porta l'apostrof d'Excel.
  `xlsx-v2`: dels rangs combinats nomes es llegeix la cel·la ancora (la visible), que
  porta el rang (`A1 [A1:F1]: ...`); les interiors no es llegeixen (poden guardar valors
  residuals ocults) i nomes se'n diu quantes tenen contingut.
* XML: l'estructura (un element per linia, sagnat per profunditat), els atributs,
  el text i els espais de noms declarats.

Un DOCX o un XLSX es un ZIP d'un paquet que no es de fiar: limits de mida per part i
`defusedxml` (sense entitats ni DTD). Qualsevol fallada torna `FAILED` amb un codi
estable i un missatge sense contingut del document; mai llanca.
"""

from __future__ import annotations

import io
import posixpath
import re
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field

from defusedxml import ElementTree

from app.classification.ooxml_text import DOCX, XLSX
from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus
from app.services.pdf_text import ExtractedPage, ExtractionResult, _hash_text, normalize_page_text

DOCX_PARSER_VERSION = "docx-v1"
XLSX_PARSER_VERSION = "xlsx-v2"
XML_PARSER_VERSION = "xml-v1"

XML_MIME_TYPES = frozenset({"application/xml", "text/xml"})

PARSER_VERSIONS: dict[str, str] = {
    DOCX: DOCX_PARSER_VERSION,
    XLSX: XLSX_PARSER_VERSION,
    **{mime: XML_PARSER_VERSION for mime in XML_MIME_TYPES},
}

#: Cap part d'un document de licitacio no s'hi acosta; una bomba si.
_MAX_PART_BYTES = 50 * 1024 * 1024
_MAX_SHEETS = 200
_MAX_CELLS = 500_000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_HEADING_NAME = re.compile(r"^(heading|titol|título|títol|titulo|titre|überschrift)\s*(\d)$", re.I)
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


class _Unreadable(Exception):
    """El fitxer no es pot llegir (ZIP o XML malmes, part massa gran, entitats...)."""


# --------------------------------------------------------------------------- #
# Resultat                                                                      #
# --------------------------------------------------------------------------- #


def _result(version: str, raw_pages: list[str], started: float) -> ExtractionResult:
    pages: list[ExtractedPage] = []
    for raw in raw_pages:
        text = normalize_page_text(raw)
        pages.append(
            ExtractedPage(
                page_number=len(pages) + 1,
                text=text,
                char_count=len(text),
                text_hash=_hash_text(text),
                status=PageExtractionStatus.EXTRACTED if text else PageExtractionStatus.EMPTY,
            )
        )
    if not pages:
        pages.append(
            ExtractedPage(
                page_number=1,
                text="",
                char_count=0,
                text_hash=_hash_text(""),
                status=PageExtractionStatus.EMPTY,
            )
        )
    chars = sum(page.char_count for page in pages)
    empty = sum(1 for page in pages if page.char_count == 0)
    return ExtractionResult(
        parser_version=version,
        status=ExtractionStatus.EXTRACTED,
        # Un fitxer d'oficina o un XML no te cap capa de text que pugui faltar: el
        # llindar de caracters per pagina dels PDF no hi aplica.
        quality=ExtractionQuality.GOOD if chars else ExtractionQuality.EMPTY,
        pages=tuple(pages),
        page_count=len(pages),
        extracted_page_count=len(pages) - empty,
        empty_page_count=empty,
        text_char_count=chars,
        ocr_candidate=False,
        parse_ms=int((time.monotonic() - started) * 1000),
        error_code=None,
        error_message=None,
    )


def _failed(version: str, code: str, message: str, started: float) -> ExtractionResult:
    return ExtractionResult(
        parser_version=version,
        status=ExtractionStatus.FAILED,
        quality=ExtractionQuality.FAILED,
        pages=(),
        page_count=0,
        extracted_page_count=0,
        empty_page_count=0,
        text_char_count=0,
        ocr_candidate=False,
        parse_ms=int((time.monotonic() - started) * 1000),
        error_code=code,
        error_message=message,
    )


def _guarded(version: str, code: str, label: str, reader, data: bytes) -> ExtractionResult:
    started = time.monotonic()
    try:
        pages = reader(data)
    except Exception:  # noqa: BLE001 - ZIP, XML, defusedxml, mides: mai llanca
        return _failed(version, code, f"No s'ha pogut llegir el {label}.", started)
    return _result(version, pages, started)


# --------------------------------------------------------------------------- #
# ZIP                                                                           #
# --------------------------------------------------------------------------- #


def _part(zf: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_PART_BYTES:
        raise _Unreadable(name)
    return zf.read(info)


def _xml(zf: zipfile.ZipFile, name: str):
    raw = _part(zf, name)
    return None if raw is None else ElementTree.fromstring(raw)


def _relationships(zf: zipfile.ZipFile, part: str) -> dict[str, str]:
    """`rId` -> ruta de la part de desti, resolta respecte de la part d'origen."""
    folder, filename = posixpath.split(part)
    root = _xml(zf, posixpath.join(folder, "_rels", f"{filename}.rels"))
    if root is None:
        return {}
    targets: dict[str, str] = {}
    for rel in root.iter(f"{_PR}Relationship"):
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
        targets[rel.get("Id") or ""] = posixpath.normpath(path)
    return targets


# --------------------------------------------------------------------------- #
# DOCX                                                                          #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Pages:
    pages: list[list[str]] = field(default_factory=lambda: [[]])
    line: list[str] = field(default_factory=list)

    def _has_text(self) -> bool:
        return any(part.strip() for part in self.line) or any(
            text.strip() for text in self.pages[-1]
        )

    def text(self, value: str) -> None:
        self.line.append(value)

    def end_line(self, prefix: str = "") -> None:
        text = "".join(self.line).strip()
        self.line = []
        if text:
            self.pages[-1].append(f"{prefix}{text}")

    def page_break(self, prefix: str = "") -> None:
        # Un salt abans de cap text (o dos salts seguits) no crea cap pagina buida.
        if not self._has_text():
            return
        self.end_line(prefix)
        self.pages.append([])

    def add_line(self, text: str) -> None:
        if text.strip():
            self.pages[-1].append(text)

    def result(self) -> list[str]:
        self.end_line()
        return ["\n".join(lines) for lines in self.pages if lines]


def _heading_levels(zf: zipfile.ZipFile) -> dict[str, int]:
    """styleId -> nivell de titol (1 = el mes alt), pel nom o per `outlineLvl`."""
    root = _xml(zf, "word/styles.xml")
    if root is None:
        return {}
    levels: dict[str, int] = {}
    for style in root.iter(f"{_W}style"):
        style_id = style.get(f"{_W}styleId")
        if not style_id:
            continue
        outline = style.find(f"{_W}pPr/{_W}outlineLvl")
        if outline is not None and (outline.get(f"{_W}val") or "").isdigit():
            levels[style_id] = int(outline.get(f"{_W}val")) + 1
            continue
        name = style.find(f"{_W}name")
        match = (
            _HEADING_NAME.match((name.get(f"{_W}val") or "").strip()) if name is not None else None
        )
        if match:
            levels[style_id] = int(match.group(2))
    return levels


def _paragraph_prefix(paragraph, headings: dict[str, int]) -> str:
    ppr = paragraph.find(f"{_W}pPr")
    if ppr is None:
        return ""
    outline = ppr.find(f"{_W}outlineLvl")
    if outline is not None and (outline.get(f"{_W}val") or "").isdigit():
        return "#" * (int(outline.get(f"{_W}val")) + 1) + " "
    style = ppr.find(f"{_W}pStyle")
    level = headings.get(style.get(f"{_W}val") or "") if style is not None else None
    if level:
        return "#" * level + " "
    if ppr.find(f"{_W}numPr") is not None:
        return "- "
    return ""


def _paragraph_runs(paragraph, out: _Pages, prefix: str) -> None:
    for node in paragraph.iter():
        tag = node.tag
        if tag in (f"{_W}t", f"{_M}t"):
            out.text(node.text or "")
        elif tag == f"{_W}tab":
            out.text("\t")
        elif tag == f"{_W}br":
            if node.get(f"{_W}type") == "page":
                out.page_break(prefix)
            else:
                out.text("\n")
        elif tag == f"{_W}cr":
            out.text("\n")
        elif tag == f"{_W}lastRenderedPageBreak":
            out.page_break(prefix)


def _cell_text(cell, headings: dict[str, int]) -> str:
    """El text d'una cel·la (paragrafs i taules niades), en una sola linia."""
    inner = _Pages()
    _blocks(cell, inner, headings, page_breaks=False)
    lines = [line for page in inner.result() for line in page.split("\n")]
    return " / ".join(line.strip() for line in lines if line.strip())


def _blocks(parent, out: _Pages, headings: dict[str, int], *, page_breaks: bool = True) -> None:
    for child in parent:
        tag = child.tag
        if tag == f"{_W}p":
            prefix = _paragraph_prefix(child, headings)
            if page_breaks:
                _paragraph_runs(child, out, prefix)
            else:
                for node in child.iter():
                    if node.tag in (f"{_W}t", f"{_M}t"):
                        out.text(node.text or "")
                    elif node.tag == f"{_W}tab":
                        out.text("\t")
            out.end_line(prefix)
        elif tag == f"{_W}tbl":
            out.end_line()
            # Nomes les files d'aquesta taula: una taula niada surt dins de la seva cel·la.
            for row in child.findall(f"{_W}tr"):
                cells = [_cell_text(cell, headings) for cell in row.findall(f"{_W}tc")]
                if any(cells):
                    out.add_line("| " + " | ".join(cells) + " |")
        elif tag in (f"{_W}sdt", f"{_W}sdtContent", f"{_W}customXml", f"{_W}smartTag"):
            _blocks(child, out, headings, page_breaks=page_breaks)


def _read_docx(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        document = _xml(zf, "word/document.xml")
        if document is None:
            raise _Unreadable("word/document.xml")
        headings = _heading_levels(zf)
    body = document.find(f"{_W}body")
    out = _Pages()
    if body is not None:
        _blocks(body, out, headings)
    return out.result()


def extract_docx(data: bytes) -> ExtractionResult:
    return _guarded(DOCX_PARSER_VERSION, "docx_unreadable", "DOCX", _read_docx, data)


# --------------------------------------------------------------------------- #
# XLSX                                                                          #
# --------------------------------------------------------------------------- #


def _cell_value(cell, shared: list[str]) -> str | None:
    kind = cell.get("t")
    if kind == "inlineStr":
        node = cell.find(f"{_S}is")
        return "".join(t.text or "" for t in node.iter(f"{_S}t")) if node is not None else None
    value = cell.find(f"{_S}v")
    if value is None or value.text is None:
        return None
    raw = value.text
    if kind == "s":
        if raw.strip().isdigit() and int(raw) < len(shared):
            return shared[int(raw)]
        return None
    if kind == "b":
        return "TRUE" if raw.strip() == "1" else "FALSE"
    # n, e (error), str (resultat de formula), d (data ISO): el valor desat, tal qual.
    return raw


def _column_number(letters: str) -> int:
    number = 0
    for char in letters:
        number = number * 26 + (ord(char) - 64)
    return number


def _column_letters(number: int) -> str:
    letters = ""
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _merged_ranges(root) -> tuple[dict[str, str], dict[str, str]]:
    """`({rang: cel·la ancora}, {cel·la interior: rang})` dels rangs combinats del full.

    L'ancora es la cel·la de dalt a l'esquerra: l'unica que Excel mostra i fa servir. Un rang
    mal format s'ignora (no es pot saber quina cel·la mostra). Les interiors es comptabilitzen
    dins del limit de cel·les del llibre: un rang desmesurat no pot inflar la memoria.
    """
    anchors: dict[str, str] = {}
    interior: dict[str, str] = {}
    for merged in root.iter(f"{_S}mergeCell"):
        ref = (merged.get("ref") or "").strip()
        bounds = ref.split(":")
        if len(bounds) != 2:
            continue
        start, end = _CELL_REF.match(bounds[0]), _CELL_REF.match(bounds[1])
        if start is None or end is None:
            continue
        c1, r1 = _column_number(start.group(1)), int(start.group(2))
        c2, r2 = _column_number(end.group(1)), int(end.group(2))
        if c2 < c1 or r2 < r1:
            continue
        if len(interior) + (c2 - c1 + 1) * (r2 - r1 + 1) > _MAX_CELLS:
            raise _Unreadable("rang combinat massa gran")
        anchor = f"{_column_letters(c1)}{r1}"
        anchors[ref] = anchor
        for row in range(r1, r2 + 1):
            for column in range(c1, c2 + 1):
                cell = f"{_column_letters(column)}{row}"
                if cell != anchor:
                    interior[cell] = ref
    return anchors, interior


def _hidden_interior_counts(root, interior: dict[str, str]) -> dict[str, int]:
    """Quantes cel·les interiors de cada rang combinat porten valor o formula.

    No se'n llegeix el contingut: nomes es compten.
    """
    counts: dict[str, int] = {}
    if not interior:
        return counts
    for cell in root.iter(f"{_S}c"):
        merged = interior.get(cell.get("r") or "")
        if merged is None:
            continue
        has_value = any(
            node is not None and (node.text or "").strip()
            for node in (cell.find(f"{_S}v"), cell.find(f"{_S}f"))
        ) or cell.find(f"{_S}is") is not None
        if has_value:
            counts[merged] = counts.get(merged, 0) + 1
    return counts


def _read_xlsx(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        workbook = _xml(zf, "xl/workbook.xml")
        if workbook is None:
            raise _Unreadable("xl/workbook.xml")
        rels = _relationships(zf, "xl/workbook.xml")
        shared: list[str] = []
        strings = _xml(zf, "xl/sharedStrings.xml")
        if strings is not None:
            shared = [
                "".join(t.text or "" for t in item.iter(f"{_S}t"))
                for item in strings.iter(f"{_S}si")
            ]

        defined: list[tuple[str, str]] = [
            (node.get("name") or "", (node.text or "").strip())
            for node in workbook.iter(f"{_S}definedName")
            if not (node.get("name") or "").startswith("_xlnm.")
        ]
        sheets = list(workbook.iter(f"{_S}sheet"))[:_MAX_SHEETS]
        names = [sheet.get("name") or "" for sheet in sheets]

        pages: list[str] = []
        cells_read = 0
        for index, sheet in enumerate(sheets):
            name = names[index]
            path = rels.get(sheet.get(f"{_R}id") or "") or f"xl/worksheets/sheet{index + 1}.xml"
            hidden = sheet.get("state") in ("hidden", "veryHidden")
            lines = [f"# Full: {name}{' (ocult)' if hidden else ''}"]
            for defined_name, ref in defined:
                owner = ref.split("!", 1)[0].strip("'") if "!" in ref else None
                if owner == name or (owner not in names and index == 0):
                    lines.append(f"Nom definit «{defined_name}»: {ref}")
            root = _xml(zf, path)
            if root is None:
                pages.append("\n".join(lines))
                continue
            sheet_rels = _relationships(zf, path)
            for part in root.iter(f"{_S}tablePart"):
                table = _xml(zf, sheet_rels.get(part.get(f"{_R}id") or "", ""))
                if table is None:
                    continue
                columns = [c.get("name") or "" for c in table.iter(f"{_S}tableColumn")]
                label = table.get("displayName") or table.get("name") or ""
                suffix = f" (columnes: {', '.join(columns)})" if columns else ""
                lines.append(f"Taula «{label}»: {table.get('ref') or ''}{suffix}")
            # xlsx-v2: en un rang combinat Excel nomes mostra (i nomes fa servir) la cel·la
            # ancora. Les interiors poden conservar valors antics que ningu no veu (L'Escala:
            # «32 punts» visible a A1 i «35 punts» ocult a B1:F1): no es llegeixen com a dades.
            # Es diu quantes n'hi ha, mai que contenen.
            anchors, interior = _merged_ranges(root)
            hidden_counts = _hidden_interior_counts(root, interior)
            range_of_anchor = {anchor: merged_ref for merged_ref, anchor in anchors.items()}
            for merged_ref, anchor in anchors.items():
                count = hidden_counts.get(merged_ref, 0)
                note = f"; {count} cel·les interiors amb contingut ocult no llegit" if count else ""
                lines.append(f"Cel·les combinades: {merged_ref} (visible {anchor}{note})")

            masters: dict[str, str] = {}
            for row in root.iter(f"{_S}row"):
                parts: list[str] = []
                for cell in row.iter(f"{_S}c"):
                    cells_read += 1
                    if cells_read > _MAX_CELLS:
                        raise _Unreadable("massa cel·les")
                    ref = cell.get("r") or ""
                    if ref in interior:
                        continue
                    value = _cell_value(cell, shared)
                    formula = cell.find(f"{_S}f")
                    text: str | None
                    if formula is not None:
                        expression = (formula.text or "").strip()
                        if formula.get("t") == "shared" and formula.get("si") is not None:
                            if expression:
                                masters[formula.get("si")] = ref
                            else:
                                origin = masters.get(formula.get("si"), "?")
                                expression = f"⟨formula compartida de {origin}⟩"
                        stored = (
                            f" → {value}" if value not in (None, "") else " (sense valor desat)"
                        )
                        text = f"={expression}{stored}"
                    elif value is not None and value.lstrip().startswith("="):
                        # Un valor introduit que comenca per «=» no es cap formula: l'apostrof
                        # (convencio d'Excel) evita confondre'l amb una.
                        text = f"'{value}"
                    else:
                        text = value
                    if text is not None and text.strip():
                        # La cel·la ancora diu de quin rang combinat ve.
                        label = f"{ref} [{range_of_anchor[ref]}]" if ref in range_of_anchor else ref
                        parts.append(f"{label}: {text.strip()}")
                if parts:
                    lines.append(f"Fila {row.get('r') or '?'} | " + " | ".join(parts))
            pages.append("\n".join(lines))
    return pages


def extract_xlsx(data: bytes) -> ExtractionResult:
    return _guarded(XLSX_PARSER_VERSION, "xlsx_unreadable", "XLSX", _read_xlsx, data)


# --------------------------------------------------------------------------- #
# XML                                                                           #
# --------------------------------------------------------------------------- #


def _qualified(name: str, prefixes: dict[str, str]) -> str:
    if name.startswith("{"):
        uri, local = name[1:].split("}", 1)
        prefix = prefixes.get(uri)
        return f"{prefix}:{local}" if prefix else local
    return name


def _xml_lines(node, prefixes: dict[str, str], depth: int) -> Iterator[str]:
    attributes = "; ".join(
        f"{_qualified(key, prefixes)}={value}" for key, value in node.attrib.items()
    )
    text = (node.text or "").strip()
    line = "  " * depth + _qualified(node.tag, prefixes)
    if attributes:
        line += f" [{attributes}]"
    if text:
        line += f": {text}"
    yield line
    for child in node:
        if not isinstance(child.tag, str):  # comentaris i instruccions de processament
            continue
        yield from _xml_lines(child, prefixes, depth + 1)
        tail = (child.tail or "").strip()
        if tail:
            yield "  " * (depth + 1) + tail


def _read_xml(data: bytes) -> list[str]:
    declared: list[tuple[str, str]] = []
    for event, item in ElementTree.iterparse(io.BytesIO(data), events=("start-ns",)):
        if event == "start-ns" and item not in declared:
            declared.append(item)
    root = ElementTree.fromstring(data)
    # Si un mateix URI te diversos prefixos, mana el primer declarat.
    prefixes: dict[str, str] = {}
    for prefix, uri in declared:
        prefixes.setdefault(uri, prefix)

    header = [f"# XML: {_qualified(root.tag, prefixes)}"]
    if declared:
        header.append("Espais de noms:")
        header += [f"  {prefix or '(per defecte)'} = {uri}" for prefix, uri in declared]
    root_attributes = "; ".join(
        f"{_qualified(key, prefixes)}={value}" for key, value in root.attrib.items()
    )
    if root_attributes:
        header.append(f"Atributs: {root_attributes}")
    if (root.text or "").strip():
        header.append(root.text.strip())

    # Un element compost de primer nivell (amb fills) es una pagina; els elements
    # simples consecutius (un camp i el seu valor) comparteixen pagina. Totes dues son
    # fronteres del document: cap pagina es talla per llargada.
    pages: list[list[str]] = []
    simple: list[str] = []
    for child in root:
        if not isinstance(child.tag, str):
            continue
        lines = list(_xml_lines(child, prefixes, 0))
        tail = (child.tail or "").strip()
        if tail:
            lines.append(tail)
        if any(isinstance(grandchild.tag, str) for grandchild in child):
            if simple:
                pages.append(simple)
                simple = []
            pages.append(lines)
        else:
            simple += lines
    if simple:
        pages.append(simple)
    if not pages:
        return ["\n".join(header)]
    pages[0] = header + pages[0]
    return ["\n".join(lines) for lines in pages]


def extract_xml(data: bytes) -> ExtractionResult:
    return _guarded(XML_PARSER_VERSION, "xml_unreadable", "XML", _read_xml, data)


EXTRACTORS = {
    DOCX: extract_docx,
    XLSX: extract_xlsx,
    **{mime: extract_xml for mime in XML_MIME_TYPES},
}
