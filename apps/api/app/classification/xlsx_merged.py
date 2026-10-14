"""Rangs combinats d'un full XLSX: quina cel·la es veu i quines no.

En un rang combinat Excel nomes mostra (i nomes fa servir) la cel·la ancora, la de dalt
a l'esquerra. Les interiors poden conservar valors antics que ningu no veu (L'Escala:
«32 punts» visible a A1 i «35 punts» ocult a B1:F1). Qualsevol lector que en tregui text
les ha de saltar. Ho comparteixen el parser d'extraccio (`app.services.office_text`) i el
lector del classificador (`app.classification.ooxml_text`): una sola definicio de que es
interior.

Pur: rep l'arrel XML d'un full ja llegit (amb `defusedxml`) i no obre res.
"""

from __future__ import annotations

import re

_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


class MergedRangeTooLarge(ValueError):
    """Els rangs combinats del full superen el limit de cel·les: el full no es llegeix."""


def column_number(letters: str) -> int:
    number = 0
    for char in letters:
        number = number * 26 + (ord(char) - 64)
    return number


def column_letters(number: int) -> str:
    letters = ""
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def merged_ranges(root, max_cells: int) -> tuple[dict[str, str], dict[str, str]]:
    """`({rang: cel·la ancora}, {cel·la interior: rang})` dels rangs combinats del full.

    Un rang mal format s'ignora (no es pot saber quina cel·la mostra). Les interiors es
    compten contra `max_cells`: un rang desmesurat no pot inflar la memoria i llenca
    `MergedRangeTooLarge`.
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
        c1, r1 = column_number(start.group(1)), int(start.group(2))
        c2, r2 = column_number(end.group(1)), int(end.group(2))
        if c2 < c1 or r2 < r1:
            continue
        if len(interior) + (c2 - c1 + 1) * (r2 - r1 + 1) > max_cells:
            raise MergedRangeTooLarge("rang combinat massa gran")
        anchor = f"{column_letters(c1)}{r1}"
        anchors[ref] = anchor
        for row in range(r1, r2 + 1):
            for column in range(c1, c2 + 1):
                cell = f"{column_letters(column)}{row}"
                if cell != anchor:
                    interior[cell] = ref
    return anchors, interior
