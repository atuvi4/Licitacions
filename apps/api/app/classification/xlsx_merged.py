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
#: `A1`, `$A$1` o `a1` (Excel escriu `A1`; altres generadors no sempre).
_CELL_REF = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")


class MergedRangeTooLarge(ValueError):
    """Els rangs combinats superen el limit de cel·les: el llibre no es llegeix."""


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


def _corner(text: str) -> tuple[int, int] | None:
    match = _CELL_REF.match(text.strip())
    if match is None or int(match.group(2)) < 1:
        return None
    return column_number(match.group(1).upper()), int(match.group(2))


def merged_ranges(root, max_cells: int) -> tuple[dict[str, str], dict[str, str]]:
    """`({rang: cel·la ancora}, {cel·la interior: rang})` dels rangs combinats del full.

    El rang es normalitza (`$A$1:$F$1`, `a1:f1` o `F1:A1` son `A1:F1`). Un rang il·legible o
    d'una sola cel·la s'ignora: no amaga res. `max_cells` es el que queda del pressupost de
    cel·les interiors del LLIBRE (qui crida el va restant full a full): si se supera, llenca
    `MergedRangeTooLarge` abans de construir res mes, i un fitxer petit amb rangs enormes no
    pot inflar ni la memoria ni el temps.
    """
    anchors: dict[str, str] = {}
    interior: dict[str, str] = {}
    for merged in root.iter(f"{_S}mergeCell"):
        bounds = (merged.get("ref") or "").split(":")
        if len(bounds) != 2:
            continue
        first, second = _corner(bounds[0]), _corner(bounds[1])
        if first is None or second is None:
            continue
        c1, c2 = sorted((first[0], second[0]))
        r1, r2 = sorted((first[1], second[1]))
        if (c1, r1) == (c2, r2):
            continue
        anchor = f"{column_letters(c1)}{r1}"
        ref = f"{anchor}:{column_letters(c2)}{r2}"
        if ref in anchors:
            continue
        if len(interior) + (c2 - c1 + 1) * (r2 - r1 + 1) - 1 > max_cells:
            raise MergedRangeTooLarge("rang combinat massa gran")
        anchors[ref] = anchor
        for row in range(r1, r2 + 1):
            for column in range(c1, c2 + 1):
                cell = f"{column_letters(column)}{row}"
                if cell != anchor:
                    interior[cell] = ref
    return anchors, interior
