"""Prou text d'un DOCX o d'un XLSX per saber que son. Res mes.

**Aixo no es un parser d'extraccio.** No crea pagines ni evidencia citable i el
seu text no arriba mai a l'analisi de requisits: nomes serveix perque el
classificador de rols pugui llegir el titol d'un DOCX i els rotuls d'un full de
calcul. Llegir un XLSX per analitzar-lo (fulls, cel·les i formules com a
evidencia) es una altra peca, amb la seva versio de parser.

Un DOCX o un XLSX tambe es un ZIP, i ve del mateix paquet que no es de fiar:
s'hi apliquen limits de mida i l'XML es llegeix amb `defusedxml` (sense
entitats externes ni expansions).
"""

from __future__ import annotations

import io
import zipfile

from defusedxml import ElementTree

from app.classification.xlsx_merged import merged_ranges

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Cap part XML d'un document de licitacio no s'hi acosta; una bomba si.
_MAX_PART_BYTES = 20 * 1024 * 1024
_MAX_SHEETS = 50
#: Cel·les interiors de rangs combinats a tot el llibre. El classificador corre dins de la
#: peticio: un fitxer petit amb rangs desmesurats no l'ha de poder alentir (el fitxer queda
#: sense text, com un de malmes).
_MAX_MERGED_CELLS = 100_000
#: El classificador nomes mira el principi. No cal mes.
_MAX_CHARS = 200_000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _read_part(zf: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_PART_BYTES:
        return None
    return zf.read(info)


def _docx_text(zf: zipfile.ZipFile) -> str:
    raw = _read_part(zf, "word/document.xml")
    if raw is None:
        return ""
    root = ElementTree.fromstring(raw)
    lines = []
    for paragraph in root.iter(f"{_W}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_W}t"))
        if text.strip():
            lines.append(text.strip())
    return "\n".join(lines)


def _xlsx_text(zf: zipfile.ZipFile) -> str:
    """Noms de full i textos de les cel·les visibles, full per full i en ordre.

    D'un rang combinat nomes es llegeix l'ancora, la cel·la que Excel mostra. Les interiors
    poden conservar textos antics que ningu no veu (L'Escala: «— 35 punts» ocult sota el
    «— 32 punts» visible); no arriben al classificador. El mateix criteri que `xlsx-v2`
    (`app.classification.xlsx_merged`).
    """
    shared: list[str] = []
    raw = _read_part(zf, "xl/sharedStrings.xml")
    if raw is not None:
        for item in ElementTree.fromstring(raw).iter(f"{_S}si"):
            shared.append("".join(t.text or "" for t in item.iter(f"{_S}t")))

    sheet_names: list[str] = []
    raw = _read_part(zf, "xl/workbook.xml")
    if raw is not None:
        sheet_names = [s.get("name", "") for s in ElementTree.fromstring(raw).iter(f"{_S}sheet")]

    # Els fulls es llegeixen com `sheetN.xml` en l'ordre del llibre. Per saber
    # que es un fitxer n'hi ha prou; per citar-ne una cel·la no ho seria.
    lines: list[str] = []
    merged_budget = _MAX_MERGED_CELLS
    for index, name in enumerate(sheet_names[:_MAX_SHEETS], start=1):
        lines.append(f"# {name}")
        raw = _read_part(zf, f"xl/worksheets/sheet{index}.xml")
        if raw is None:
            continue
        root = ElementTree.fromstring(raw)
        _, interior = merged_ranges(root, merged_budget)
        merged_budget -= len(interior)
        for row in root.iter(f"{_S}row"):
            cells = []
            for cell in row.iter(f"{_S}c"):
                if (cell.get("r") or "") in interior:
                    continue
                kind = cell.get("t")
                if kind == "s":
                    value = cell.find(f"{_S}v")
                    if value is not None and value.text and value.text.isdigit():
                        position = int(value.text)
                        if position < len(shared):
                            cells.append(shared[position])
                elif kind == "inlineStr":
                    cells.append("".join(t.text or "" for t in cell.iter(f"{_S}t")))
            if cells:
                lines.append(" | ".join(c.strip() for c in cells if c.strip()))
    return "\n".join(line for line in lines if line.strip())


def read_text(data: bytes, mime_type: str) -> str | None:
    """El text per classificar, o `None` si el format no s'hi presta o esta malmes."""
    if mime_type not in (DOCX, XLSX):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            text = _docx_text(zf) if mime_type == DOCX else _xlsx_text(zf)
    except (zipfile.BadZipFile, ElementTree.ParseError, ValueError, OSError):
        return None
    except Exception:  # defusedxml llenca les seves excepcions (DTD, entitats...)
        return None
    return text[:_MAX_CHARS]
