"""Comprovacio que una cita existeix de veritat.

Aquesta es la peca que separa "el model ho ha dit" de "el plec ho diu". Un LLM
pot retornar una pagina plausible i un fragment versemblant que no apareix
enlloc; sense aquesta verificacio, tot el sistema seria una al·lucinacio ben
formatada.

Que es tolera i que no:

* SI: diferencies d'espais, salts de linia, tabuladors i normalitzacio Unicode.
  Son artefactes de com el PDF codifica el text, no discrepancies de contingut.
  Tambe les cometes tipografiques, que els parsers barregen amb les rectes.
* NO: similitud semantica, sinonims, parafrasi o "s'hi assembla prou". Si el
  quote no es al text, l'evidencia es rebutja. Millor perdre un requisit que
  publicar-ne un que ningu pot verificar.

Els offsets els calcula aquest modul sobre el text immutable de la pagina, no
els inventa el model.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

# Cometes i guions que els extractors de PDF barregen alegrement.
_EQUIVALENTS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-", " ": " ",
}
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class QuoteMatch:
    """Posicio del quote dins del text ORIGINAL de la pagina."""

    char_start: int
    char_end: int
    #: El text tal com apareix realment a la pagina, no com l'ha escrit el model.
    exact_text: str


def _canonical_char(char: str) -> str:
    return _EQUIVALENTS.get(char, char)


def _fold(text: str) -> tuple[str, list[int]]:
    """Normalitza i retorna el mapa de posicions cap al text original.

    El mapa es el que permet donar offsets sobre el text immutable: es
    normalitza per comparar, pero es reporta sobre l'original.
    """
    folded: list[str] = []
    offsets: list[int] = []
    pending_space = False

    for index, raw in enumerate(unicodedata.normalize("NFC", text)):
        char = _canonical_char(raw)
        if char.isspace():
            pending_space = True
            continue
        if pending_space and folded:
            folded.append(" ")
            offsets.append(index)
        pending_space = False
        folded.append(char.casefold())
        offsets.append(index)

    return "".join(folded), offsets


def normalize_quote(text: str) -> str:
    """Forma canonica d'un fragment, per comparar-lo."""
    normalized = unicodedata.normalize("NFC", text or "")
    normalized = "".join(_canonical_char(char) for char in normalized)
    return _WHITESPACE.sub(" ", normalized).strip().casefold()


def locate_quote(page_text: str, quote: str) -> QuoteMatch | None:
    """Busca el quote dins de la pagina. `None` si no hi es.

    Nomes coincidencia literal un cop normalitzats els espais i la puntuacio
    equivalent. Cap heuristica de similitud.
    """
    if not quote or not quote.strip() or not page_text:
        return None

    folded_page, offsets = _fold(page_text)
    folded_quote = normalize_quote(quote)
    if not folded_quote:
        return None

    position = folded_page.find(folded_quote)
    if position == -1:
        return None

    start = offsets[position]
    # `offsets` apunta al primer caracter de cada unitat; el final ha d'incloure
    # l'ultim caracter del quote.
    last_index = position + len(folded_quote) - 1
    end = offsets[last_index] + 1
    return QuoteMatch(char_start=start, char_end=end, exact_text=page_text[start:end])


def quote_is_present(page_text: str, quote: str) -> bool:
    return locate_quote(page_text, quote) is not None


# --------------------------------------------------------------------------- #
# Diagnostic d'una cita rebutjada (nomes codis, mai text)                        #
# --------------------------------------------------------------------------- #

#: Caracters que nomes separen lletres d'una mateixa paraula (la ela geminada).
_JOINERS = {"·": "", "•": ""}


def _fold_accents(text: str) -> str:
    """`normalize_quote` sense diacritics: nomes per DIAGNOSTICAR, mai per acceptar."""
    decomposed = unicodedata.normalize("NFD", normalize_quote(text))
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return "".join(_JOINERS.get(char, char) for char in stripped)


def diagnose_missing_quote(pages: Mapping[int, str], cited_page: int, quote: str) -> str:
    """Per que una cita no es literal a la pagina citada, en un codi sense text.

    * `on_other_page:N`: es literal a una altra pagina del chunk (pagina equivocada).
    * `accent_variant`: a la pagina citada hi es si s'ignoren accents i diacritics.
    * `accent_variant_on_other_page:N`: el mateix, pero a una altra pagina.
    * `absent`: no hi es enlloc del chunk (parafrasi o text inventat).

    No canvia res del que s'accepta: una cita que no es literal es rebutja igualment.
    """
    others = [number for number in sorted(pages) if number != cited_page]
    for number in others:
        if quote_is_present(pages[number], quote):
            return f"on_other_page:{number}"
    folded = _fold_accents(quote)
    if not folded:
        return "absent"
    if cited_page in pages and folded in _fold_accents(pages[cited_page]):
        return "accent_variant"
    for number in others:
        if folded in _fold_accents(pages[number]):
            return f"accent_variant_on_other_page:{number}"
    return "absent"
