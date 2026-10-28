"""Lectura determinista dels .doc de Word 97-2003 (`doc-v1`) per a l'analisi de requisits.

Germa de `docx-v1` (`office_text`): llegeix els bytes desats, no els modifica mai (l'original
i el seu SHA es conserven tal com van arribar) i torna pagines amb text citable. Cap OCR, cap
model i cap programa extern: el contenidor OLE2 l'obre `olefile` (BSD-2, Python pur) i el text
el llegeix aquest modul amb les estructures publicades del format ([MS-DOC]):

* la FIB, la capcalera del flux `WordDocument`: versio, si el document es xifrat, quants
  caracters te el text principal (`ccpText`) i on son les taules seguents;
* la taula de peces (CLX, al flux `0Table` o `1Table`): on son els caracters de cada tros del
  text i si son de 8 bits (cp1252) o UTF-16. Un .doc desat rapid te el text a trossos
  desordenats, i la taula de peces el torna a posar en ordre;
* les propietats de caracter (PlcBteChpx i les seves FKP), nomes per saber quin text s'ha
  suprimit amb control de canvis (`sprmCFRMarkDel`);
* les propietats de paragraf (PlcBtePapx i les seves FKP, i el flux `Data` quan no hi caben:
  `sprmPHugePapx`), nomes per saber quins paragrafs son d'una taula i de quina profunditat
  (`sprmPFInTable`, `sprmPItap`), quina marca tanca una fila de la taula de fora (`sprmPFTtp`) i
  quina tanca una cel·la o una fila d'una taula niada (`sprmPFInnerTableCell`,
  `sprmPFInnerTtp`).

**Que se'n llegeix (v1).** El text principal, en l'ordre del document i amb els canvis
acceptats: el text suprimit amb control de canvis no hi surt, l'inserit si. No: capcaleres,
peus, notes, comentaris ni quadres de text (com `docx-v1`), ni estils (sense titols ni llistes).
Dels camps (`DATE`, `HYPERLINK`, `FORMTEXT`...) nomes el resultat que es veu, mai la instruccio;
un inici de camp sense el seu final no amaga res.

Limitacions conegudes de v1, que no fan fallar el document:

* un simbol d'una font de simbols (`sprmCSymbol`: les caselles de Wingdings, per exemple) surt
  com el caracter que li guarda el lloc, no com el simbol;
* les propietats que un desat rapid de Word 97/2000 deixa al `Prm` de cada peca no es llegeixen:
  si hi hagues les de taula, les files sortirien amb la regla de reserva;
* un .doc no desa les pagines que veu Word: sense salts, tot el document es una pagina (i, per a
  l'analisi, un sol tros).

**Que es una pagina.** Un salt de pagina o de seccio desat al text (0x0C). Sense cap salt, una
sola pagina. Mai se'n tallen per llargada.

**Taules.** Cada cel·la de la taula de fora acaba amb una marca 0x07, i cada fila amb una altra
que les propietats de paragraf diuen que es de fila. La fila surt com a `docx-v1`,
`| cel·la | cel·la |`, amb els paragrafs d'una cel·la units per ` / `, i una taula niada surt
dins de la seva cel·la, fila a fila (`| | x1 | x2 | / | y1 | y2 | | Segona |`). Un paragraf de
fora de cap taula tanca la fila que hi hagues oberta: si una marca de fila no es reconeix, la
fila es queda on era. Si les propietats de paragraf no es poden llegir, una marca de fila es la
que segueix immediatament una altra marca: aixi les cel·les buides parteixen la fila, i els
paragrafs de la primera cel·la surten com a linies soltes abans de la fila. Mateixos bytes,
mateix text.

**Fallades.** Mai llanca. Torna `FAILED` amb un codi estable i un missatge sense contingut del
document: `doc_encrypted` (protegit amb contrasenya), `doc_unsupported_version` (Word 6/95 o
anterior: una FIB d'una altra forma), `doc_unreadable` (l'estructura no quadra), `doc_too_large`
(mes text del que es llegeix d'un sol document) i `too_many_pages` (mes pagines que
`DOCUMENT_MAX_PAGES`, com els PDF). Uns bytes que no son cap contenidor OLE2, o un OLE2 sense flux
`WordDocument` (un RTF, un HTML o un full de calcul desats com a «.doc»), no son un .doc malmes:
tornen `SKIPPED_UNSUPPORTED`, com qualsevol format que no es llegeix. Un contenidor OLE2 generic
(`application/x-ole-storage`) no passa per aqui: el tipus es decideix en rebre'l.
"""

from __future__ import annotations

import bisect
import dataclasses
import io
import re
import struct
import time
from dataclasses import dataclass, field

import olefile

from app.core.config import settings
from app.services.document_download import OLE2_MAGIC
from app.services.office_text import _failed, _Pages, _result
from app.services.pdf_text import ExtractionResult, unsupported_result

DOC = "application/msword"
DOC_PARSER_VERSION = "doc-v1"

PARSER_VERSIONS: dict[str, str] = {DOC: DOC_PARSER_VERSION}

ENCRYPTED = "doc_encrypted"
UNSUPPORTED_VERSION = "doc_unsupported_version"
UNREADABLE = "doc_unreadable"
TOO_LARGE = "doc_too_large"
#: El mateix codi que els PDF: el limit tambe es el mateix (`DOCUMENT_MAX_PAGES`).
TOO_MANY_PAGES = "too_many_pages"

#: `wIdent` de la FIB de Word 97 i posteriors (i de Word 6 i 95 en alguns fitxers).
_WORD_IDENT = 0xA5EC
#: `wIdent` de Word 6.0 i 95.
_WORD_6_IDENT = 0xA5DC
#: Word 97 (`nFib` 0x00C1) i posteriors. Word 6 i 95 (0x0065-0x0068) tenen una altra FIB.
_MIN_NFIB = 0x00C0
#: Bits de `FibBase` (desplacament 0x0A). `fObfuscated` nomes compta amb `fEncrypted`.
_F_ENCRYPTED = 0x0100
_F_WHICH_TABLE = 0x0200
#: Posicio de `ccpText` a `FibRgLw97` i dels parells (fc, lcb) que es fan servir de
#: `FibRgFcLcb97`: les propietats de caracter i de paragraf, i la taula de peces.
_CCP_TEXT = 3
_BTE_CHPX = 12
_BTE_PAPX = 13
_CLX = 33
_FKP_SIZE = 512

#: Cap .doc d'un plec no s'hi acosta; un fitxer fet per fer mal, si. Els fluxos, el text que en
#: surt (un plec de 2.000 pagines en te uns 6 milions), les peces i la feina de llegir
#: propietats tenen sostre: un fitxer petit no pot demanar ni memoria ni temps desmesurats.
_MAX_STREAM_BYTES = 64 * 1024 * 1024
_MAX_TEXT_CHARS = 10_000_000
_MAX_PIECES = 200_000
#: Feina de cada lectura de propietats (la de caracter i la de paragraf, cadascuna la seva):
#: trams i sprm llegits. Exhaurida, aquelles propietats no compten: no es descarta cap text
#: suprimit, o les taules es llegeixen amb la regla de reserva.
_MAX_PROPERTY_WORK = 1_000_000
#: Trams de text suprimit que es descarten com a molt (cadascun parteix una peca). Mes, i no
#: se'n descarta cap, com si les propietats de caracter no es poguessin llegir.
_MAX_DELETED_RANGES = 50_000
#: Profunditat de taules niades que es representa (Word en permet 63).
_MAX_DEPTH = 16

#: Sprm de paragraf que es llegeixen: operand d'un byte (0 o 1)...
_SPRM_IN_TABLE = 0x2416
_SPRM_TTP = 0x2417
_SPRM_INNER_CELL = 0x244B
_SPRM_INNER_TTP = 0x244C
#: ...i de quatre: la profunditat de la taula i on son les propietats al flux `Data`.
_SPRM_ITAP = 0x6649
_SPRM_HUGE_PAPX = 0x6646
#: Text suprimit amb control de canvis (sprm de caracter, operand de commutacio).
_SPRM_RMARK_DEL = 0x0800
_TOGGLE_ON = frozenset({0x01, 0x81})
#: Sprm d'operand variable amb una regla de mida propia.
_SPRM_DEF_TABLE = 0xD608
_SPRM_DEF_TABLE_10 = 0xD606
_SPRM_CHG_TABS = 0xC615
#: Mida de l'operand segons `spra` (els 3 bits alts); 6 es variable.
_OPERAND_SIZE = {0: 1, 1: 1, 2: 2, 3: 4, 4: 2, 5: 2, 7: 3}
#: `cbGrpprl` maxim d'un PrcData del flux `Data`.
_MAX_HUGE_GRPPRL = 0x3FA2

_CELL_MARK = "\x07"
_PAGE_BREAK = "\x0c"
_FIELD_BEGIN = "\x13"
_FIELD_SEPARATOR = "\x14"
_FIELD_END = "\x15"
_TOKENS = re.compile("[\r\x07\x0c\x13\x14\x15]")
_FIELD_CHARS = re.compile("[\x13\x14\x15]")
#: Salt de linia manual (0x0B), salt de columna (0x0E) i guionet que no parteix (0x1E); el
#: guionet opcional (0x1F) no es veu. La resta de caracters de control els treu
#: `normalize_page_text`.
_SPECIAL = str.maketrans({"\x0b": "\n", "\x0e": "\n", "\x1e": "-", "\x1f": None})


def _cp1252_table() -> dict[int, str]:
    """Els caracters de 8 bits d'una peca: cp1252, tret dels cinc bytes que no hi son.

    [MS-DOC] dona la mateixa taula que cp1252 per a 0x80-0x9F. Els cinc bytes sense
    caracter (0x81, 0x8D, 0x8F, 0x90, 0x9D) es queden com a Latin-1: millor un caracter rar
    que un document rebutjat.
    """
    table: dict[int, str] = {}
    for byte in range(0x80, 0xA0):
        try:
            table[byte] = bytes([byte]).decode("cp1252")
        except UnicodeDecodeError:
            continue
    return table


_CP1252 = _cp1252_table()


class _DocError(Exception):
    """Una fallada amb el seu codi estable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _unreadable(message: str) -> _DocError:
    return _DocError(UNREADABLE, message)


class _NotWordBinary(Exception):
    """Els bytes no son un document de Word binari (un RTF o un HTML desat com a .doc)."""


class _OutOfBudget(Exception):
    """S'ha acabat la feina permesa per llegir propietats."""


class _Budget:
    def __init__(self, units: int) -> None:
        self.left = units

    def spend(self, units: int = 1) -> None:
        self.left -= units
        if self.left < 0:
            raise _OutOfBudget


@dataclass(frozen=True, slots=True)
class _Fib:
    table: str
    ccp_text: int
    clx: tuple[int, int]
    chpx: tuple[int, int]
    papx: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _Piece:
    start: int
    end: int
    #: Desplacament al flux `WordDocument` del primer caracter, en bytes.
    offset: int
    compressed: bool


@dataclass(frozen=True, slots=True)
class _Segment:
    """Un tros del text principal que es seguit al flux.

    Els index del text principal son unitats de codi (els CP de [MS-DOC]): una peca UTF-16 es
    descodifica sense ajuntar els parells suplents (`_units`), i aixi l'index d'un caracter diu
    on es al flux. Els parells s'ajunten al final.
    """

    start: int
    offset: int
    compressed: bool

    def offset_after(self, index: int) -> int:
        """El desplacament al flux just despres del caracter `index` del text principal."""
        return self.offset + (1 if self.compressed else 2) * (index - self.start + 1)


@dataclass(frozen=True, slots=True)
class _Paragraph:
    """Les propietats de taula d'un paragraf (nomes dels que en tenen)."""

    in_table: bool
    row_end: bool
    inner_cell: bool
    inner_row_end: bool
    depth: int


class _DataStream:
    """El flux `Data`, que nomes es llegeix si alguna propietat hi es."""

    def __init__(self, ole: olefile.OleFileIO) -> None:
        self._ole = ole
        self._data: bytes | None = None

    def read(self) -> bytes:
        if self._data is None:
            self._data = _stream(self._ole, "Data") if self._ole.exists("Data") else b""
        return self._data


# --------------------------------------------------------------------------- #
# FIB i peces                                                                   #
# --------------------------------------------------------------------------- #


def _stream(ole: olefile.OleFileIO, name: str) -> bytes:
    if not ole.exists(name):
        raise _unreadable(f"Al .doc li falta el flux {name}.")
    if ole.get_size(name) > _MAX_STREAM_BYTES:
        raise _unreadable("Un flux del .doc supera la mida maxima.")
    return ole.openstream(name).read()


def _fib(word: bytes) -> _Fib:
    """El que es fa servir de la FIB.

    Els desplacaments es calculen amb els comptadors de la FIB (`csw`, `cslw`,
    `cbRgFcLcb`), no fixos: una FIB de Word 2003 o 2007 es mes llarga que la de 97.
    """
    ident = struct.unpack_from("<H", word, 0)[0] if len(word) >= 2 else None
    if ident == _WORD_6_IDENT:
        raise _DocError(
            UNSUPPORTED_VERSION, "Document de Word 6/95 o anterior (v1 llegeix Word 97-2003)."
        )
    if len(word) < 0x22 or ident != _WORD_IDENT:
        raise _unreadable("El flux WordDocument no te la capcalera d'un document de Word.")
    if struct.unpack_from("<H", word, 2)[0] < _MIN_NFIB:
        raise _DocError(
            UNSUPPORTED_VERSION, "Document de Word 6/95 o anterior (v1 llegeix Word 97-2003)."
        )
    flags = struct.unpack_from("<H", word, 0x0A)[0]
    # [MS-DOC]: `fObfuscated` (XOR) nomes vol dir res si `fEncrypted` hi es.
    if flags & _F_ENCRYPTED:
        raise _DocError(ENCRYPTED, "El .doc esta protegit amb contrasenya.")
    offset = 0x20
    csw = struct.unpack_from("<H", word, offset)[0]
    offset += 2 + csw * 2
    cslw = struct.unpack_from("<H", word, offset)[0]
    offset += 2
    if cslw <= _CCP_TEXT:
        raise _unreadable("La FIB no porta la llargada del text.")
    ccp_text = struct.unpack_from("<i", word, offset + _CCP_TEXT * 4)[0]
    offset += cslw * 4
    pairs = struct.unpack_from("<H", word, offset)[0]
    offset += 2
    if pairs <= _CLX:
        raise _unreadable("La FIB no porta la taula de peces.")
    clx = struct.unpack_from("<II", word, offset + _CLX * 8)
    chpx = struct.unpack_from("<II", word, offset + _BTE_CHPX * 8)
    papx = struct.unpack_from("<II", word, offset + _BTE_PAPX * 8)
    if ccp_text < 0 or clx[1] == 0:
        raise _unreadable("La FIB no quadra.")
    # Cada caracter ocupa almenys un byte del flux: un text mes llarg vol dir peces que es
    # trepitgen, i es la manera de fer que uns quants KB demanin GB de text.
    if ccp_text > len(word):
        raise _unreadable("El text declarat no hi cap, al flux WordDocument.")
    if ccp_text > _MAX_TEXT_CHARS:
        raise _DocError(TOO_LARGE, f"El .doc te mes de {_MAX_TEXT_CHARS} caracters de text.")
    return _Fib("1Table" if flags & _F_WHICH_TABLE else "0Table", ccp_text, clx, chpx, papx)


def _pieces(table: bytes, fc_clx: int, lcb_clx: int) -> list[_Piece]:
    """La taula de peces, en l'ordre del text."""
    if fc_clx + lcb_clx > len(table):
        raise _unreadable("La taula de peces surt del flux de taula.")
    clx = table[fc_clx : fc_clx + lcb_clx]
    index = 0
    # Els Prc del principi nomes porten modificadors de propietats: cap text.
    while index < len(clx) and clx[index] == 0x01:
        size = struct.unpack_from("<h", clx, index + 1)[0]
        if size < 0:
            raise _unreadable("La taula de peces no quadra.")
        index += 3 + size
    if index + 5 > len(clx) or clx[index] != 0x02:
        raise _unreadable("La taula de peces no quadra.")
    size = struct.unpack_from("<I", clx, index + 1)[0]
    plc = clx[index + 5 : index + 5 + size]
    if len(plc) != size or size < 4 or (size - 4) % 12:
        raise _unreadable("La taula de peces no quadra.")
    count = (size - 4) // 12
    if count > _MAX_PIECES:
        raise _DocError(TOO_LARGE, "El .doc te massa trossos de text.")
    cps = struct.unpack_from(f"<{count + 1}I", plc, 0)
    if any(cps[i] > cps[i + 1] for i in range(count)):
        raise _unreadable("La taula de peces no esta en ordre.")
    base = (count + 1) * 4
    pieces = []
    for position in range(count):
        fc = struct.unpack_from("<I", plc, base + position * 8 + 2)[0]
        compressed = bool(fc & 0x40000000)
        fc &= 0x3FFFFFFF
        pieces.append(
            _Piece(cps[position], cps[position + 1], fc // 2 if compressed else fc, compressed)
        )
    return pieces


# --------------------------------------------------------------------------- #
# Propietats                                                                    #
# --------------------------------------------------------------------------- #


def _operand_size(sprm: int, data: bytes, position: int) -> int:
    """Quants bytes ocupa l'operand d'un sprm que comenca a `position`."""
    spra = sprm >> 13
    if spra != 6:
        return _OPERAND_SIZE[spra]
    if sprm in (_SPRM_DEF_TABLE, _SPRM_DEF_TABLE_10):
        # Dos bytes de mida, que compten la resta de l'operand mes u.
        return 2 + struct.unpack_from("<H", data, position)[0] - 1
    if sprm == _SPRM_CHG_TABS and data[position] == 255:
        # Tabuladors que s'esborren (posicio i tolerancia) i que s'afegeixen (posicio i tipus).
        deleted = data[position + 1]
        added = data[position + 2 + 4 * deleted]
        return 3 + 4 * deleted + 3 * added
    return 1 + data[position]


def _sprms(grpprl: bytes, budget: _Budget):
    """(sprm, posicio de l'operand) de cada sprm d'una grpprl. Llanca si en surt."""
    position = 0
    while position + 2 <= len(grpprl):
        budget.spend()
        sprm = struct.unpack_from("<H", grpprl, position)[0]
        position += 2
        if position >= len(grpprl):
            raise _unreadable("Un sprm surt de la seva grpprl.")
        yield sprm, position
        position += _operand_size(sprm, grpprl, position)
    if position > len(grpprl):
        raise _unreadable("Un sprm surt de la seva grpprl.")


@dataclass(slots=True)
class _Properties:
    """El que cal per llegir les propietats d'un .doc, amb la feina comptada."""

    word: bytes
    table: bytes
    data: _DataStream | None
    budget: _Budget = field(default_factory=lambda: _Budget(_MAX_PROPERTY_WORK))
    huge: dict[int, _Paragraph | None] = field(default_factory=dict)

    def pages(self, fc: int, lcb: int):
        """Cada FKP diferent d'una PlcBte (una sola vegada encara que s'hi repeteixi)."""
        if lcb < 12 or (lcb - 4) % 8 or fc + lcb > len(self.table):
            raise _unreadable("La PlcBte no quadra.")
        count = (lcb - 4) // 8
        # Una entrada per FKP, i cada FKP ocupa 512 bytes del flux: no n'hi caben mes.
        if count > len(self.word) // _FKP_SIZE:
            raise _unreadable("La PlcBte no quadra.")
        seen: set[int] = set()
        for position in range(count):
            pn = struct.unpack_from("<I", self.table, fc + (count + 1) * 4 + position * 4)[0]
            pn &= 0x3FFFFF
            if pn in seen:
                continue
            seen.add(pn)
            page = self.word[pn * _FKP_SIZE : (pn + 1) * _FKP_SIZE]
            if len(page) != _FKP_SIZE:
                raise _unreadable("Una FKP surt del flux WordDocument.")
            yield page

    def paragraph(self, grpprl: bytes, *, huge: bool = False) -> _Paragraph | None:
        in_table = row_end = inner_cell = inner_row_end = False
        depth = 0
        for sprm, position in _sprms(grpprl, self.budget):
            if sprm == _SPRM_IN_TABLE:
                in_table = grpprl[position] == 1
            elif sprm == _SPRM_TTP:
                row_end = grpprl[position] == 1
            elif sprm == _SPRM_INNER_CELL:
                inner_cell = grpprl[position] == 1
            elif sprm == _SPRM_INNER_TTP:
                inner_row_end = grpprl[position] == 1
            elif sprm == _SPRM_ITAP:
                depth = struct.unpack_from("<i", grpprl, position)[0]
            elif sprm == _SPRM_HUGE_PAPX and not huge:
                # La grpprl no cabia a la FKP: es al flux `Data` (i aleshores es l'unica).
                stored = self._huge(struct.unpack_from("<I", grpprl, position)[0])
                if stored is not None:
                    in_table |= stored.in_table
                    row_end |= stored.row_end
                    inner_cell |= stored.inner_cell
                    inner_row_end |= stored.inner_row_end
                    depth = max(depth, stored.depth)
        if not (in_table or row_end or inner_cell or inner_row_end):
            return None
        if inner_cell or inner_row_end:
            depth = max(depth, 2)
        return _Paragraph(
            in_table, row_end, inner_cell, inner_row_end, max(1, min(depth, _MAX_DEPTH))
        )

    def _huge(self, offset: int) -> _Paragraph | None:
        if offset not in self.huge:
            data = self.data.read() if self.data is not None else b""
            if offset + 2 > len(data):
                raise _unreadable("Unes propietats de paragraf surten del flux Data.")
            size = struct.unpack_from("<h", data, offset)[0]
            if size < 0 or size > _MAX_HUGE_GRPPRL or offset + 2 + size > len(data):
                raise _unreadable("Unes propietats de paragraf del flux Data no quadren.")
            self.huge[offset] = self.paragraph(data[offset + 2 : offset + 2 + size], huge=True)
        return self.huge[offset]


def _paragraphs(properties: _Properties, fc: int, lcb: int) -> dict[int, _Paragraph] | None:
    """Els paragrafs de taula, pel desplacament al flux on s'acaben (despres de la marca).

    `None` si el document no ho diu o no es pot llegir: les taules es llegeixen amb la regla
    de reserva.
    """
    if not lcb:
        return None
    found: dict[int, _Paragraph] = {}
    try:
        for page in properties.pages(fc, lcb):
            runs = page[_FKP_SIZE - 1]
            if not runs or 4 * (runs + 1) + 13 * runs > _FKP_SIZE - 1:
                return None
            bounds = struct.unpack_from(f"<{runs + 1}I", page, 0)
            # Molts paragrafs d'una FKP comparteixen propietats: cadascuna es llegeix un cop.
            cache: dict[int, _Paragraph | None] = {}
            for run in range(runs):
                properties.budget.spend()
                offset = page[4 * (runs + 1) + 13 * run] * 2
                if not offset:
                    continue  # propietats per defecte: fora de cap taula
                if offset not in cache:
                    size = page[offset]
                    if size:
                        grpprl = page[offset + 1 : offset + 2 * size]
                    else:
                        grpprl = page[offset + 2 : offset + 2 + 2 * page[offset + 1]]
                    # Els dos primers bytes son l'estil (istd): els sprm venen despres.
                    cache[offset] = properties.paragraph(grpprl[2:])
                if cache[offset] is not None:
                    found[bounds[run + 1]] = cache[offset]
    except (_DocError, _OutOfBudget, struct.error, IndexError, KeyError):
        return None
    return found


def _deleted(properties: _Properties, fc: int, lcb: int) -> list[tuple[int, int]]:
    """Els trossos del flux (en bytes, ordenats i sense encavalcar) de text suprimit.

    Si les propietats de caracter no es poden llegir, cap: tot el text es llegeix.
    """
    if not lcb:
        return []
    ranges: list[tuple[int, int]] = []
    try:
        for page in properties.pages(fc, lcb):
            runs = page[_FKP_SIZE - 1]
            if not runs or 4 * (runs + 1) + runs > _FKP_SIZE - 1:
                return []
            bounds = struct.unpack_from(f"<{runs + 1}I", page, 0)
            cache: dict[int, bool] = {}
            for run in range(runs):
                properties.budget.spend()
                offset = page[4 * (runs + 1) + run] * 2
                if not offset:
                    continue
                if offset not in cache:
                    grpprl = page[offset + 1 : offset + 1 + page[offset]]
                    deleted = False
                    for sprm, position in _sprms(grpprl, properties.budget):
                        if sprm == _SPRM_RMARK_DEL:
                            deleted = grpprl[position] in _TOGGLE_ON
                    cache[offset] = deleted
                if cache[offset] and bounds[run] < bounds[run + 1]:
                    ranges.append((bounds[run], bounds[run + 1]))
                    if len(ranges) > _MAX_DELETED_RANGES:
                        return []
    except (_DocError, _OutOfBudget, struct.error, IndexError, KeyError):
        return []
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# --------------------------------------------------------------------------- #
# Text principal                                                                #
# --------------------------------------------------------------------------- #


def _kept(piece: _Piece, count: int, deleted: list[tuple[int, int]], starts: list[int]):
    """Els trams [i, j) de caracters de la peca que no s'han suprimit."""
    width = 1 if piece.compressed else 2
    first, last = piece.offset, piece.offset + width * count
    cursor = 0
    k = max(bisect.bisect_right(starts, first) - 1, 0)
    while k < len(deleted) and deleted[k][0] < last:
        start, end = deleted[k]
        k += 1
        if end <= first:
            continue
        # Els caracters que comencen dins del tros suprimit.
        cut_from = max(0, -(-(start - first) // width))
        cut_to = min(count, -(-(end - first) // width))
        if cut_from > cursor:
            yield cursor, cut_from
        cursor = max(cursor, cut_to)
    if cursor < count:
        yield cursor, count


def _units(raw: bytes) -> str:
    """Un tros UTF-16 amb una unitat de codi per caracter: els parells suplents no s'ajunten."""
    text = raw.decode("utf-16-le", errors="surrogatepass")
    if len(text) * 2 == len(raw):
        return text
    # El descodificador UTF-16 ajunta els parells (amb `surrogatepass` tambe); l'UTF-32 no: cada
    # unitat, amb dos bytes de zeros, hi es un caracter.
    wide = bytearray(len(raw) * 2)
    wide[0::4] = raw[0::2]
    wide[1::4] = raw[1::2]
    return wide.decode("utf-32-le", errors="surrogatepass")


def _main_text(
    word: bytes, pieces: list[_Piece], ccp_text: int, deleted: list[tuple[int, int]]
) -> tuple[str, list[_Segment]]:
    """Els primers `ccpText` caracters (el text principal) sense el suprimit, i on es cada tros."""
    if pieces and pieces[-1].end < ccp_text:
        raise _unreadable("El text principal surt de la taula de peces.")
    starts = [start for start, _end in deleted]
    parts: list[str] = []
    segments: list[_Segment] = []
    length = 0
    for piece in pieces:
        if piece.start >= ccp_text:
            break
        count = min(piece.end, ccp_text) - piece.start
        if count <= 0:
            continue
        width = 1 if piece.compressed else 2
        if piece.offset + width * count > len(word):
            raise _unreadable("Una peca de text surt del flux WordDocument.")
        for begin, end in _kept(piece, count, deleted, starts):
            raw = word[piece.offset + width * begin : piece.offset + width * end]
            text = raw.decode("latin-1").translate(_CP1252) if piece.compressed else _units(raw)
            segments.append(_Segment(length, piece.offset + width * begin, piece.compressed))
            parts.append(text)
            length += len(text)
    return "".join(parts), segments


def _matched_fields(text: str) -> frozenset[int]:
    """On son els caracters de camp que fan de camp.

    Un inici amb el seu final, i el primer separador de cada un. Un inici sense final (o un
    final o un separador de cap camp) no amaga res: es treu i prou.
    """
    keep: set[int] = set()
    open_fields: list[list[int | None]] = []
    for match in _FIELD_CHARS.finditer(text):
        index = match.start()
        char = text[index]
        if char == _FIELD_BEGIN:
            open_fields.append([index, None])
        elif char == _FIELD_SEPARATOR:
            if open_fields and open_fields[-1][1] is None:
                open_fields[-1][1] = index
        elif open_fields:
            begin, separator = open_fields.pop()
            keep.add(begin)
            keep.add(index)
            if separator is not None:
                keep.add(separator)
    return frozenset(keep)


# --------------------------------------------------------------------------- #
# Paragrafs, taules i pagines                                                   #
# --------------------------------------------------------------------------- #


def _cell(lines: list[str]) -> str:
    return " / ".join(
        part.strip() for line in lines for part in line.split("\n") if part.strip()
    )


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


@dataclass(slots=True)
class _Table:
    """Una taula oberta: la cel·la i la fila que es llegeixen i, si es niada, les files ja
    llegides, que seran linies de la cel·la que la conte."""

    lines: list[str] = field(default_factory=list)
    cells: list[str] = field(default_factory=list)
    rows: list[str] = field(default_factory=list)

    def end_cell(self, text: str) -> None:
        self.lines.append(text)
        self.cells.append(_cell(self.lines))
        self.lines = []

    def end_row(self) -> str | None:
        if any(line.strip() for line in self.lines):
            self.end_cell("")  # una cel·la sense la seva marca de final
        row = _row(self.cells) if any(self.cells) else None
        self.lines, self.cells = [], []
        return row


class _Layout:
    """Paragrafs, files de taula (tambe niades) i pagines, amb el `_Pages` de `docx-v1`."""

    def __init__(self) -> None:
        self.out = _Pages()
        self.buffer: list[str] = []
        self.tables: list[_Table] = []

    def text(self, value: str) -> bool:
        value = value.translate(_SPECIAL)
        if value:
            self.buffer.append(value)
        return bool(value)

    def row_open(self) -> bool:
        return bool(self.tables and self.tables[0].cells)

    def _take(self) -> str:
        text = "".join(self.buffer)
        self.buffer = []
        return text

    def _enter(self, depth: int) -> _Table:
        while len(self.tables) > depth:
            self._close()
        while len(self.tables) < depth:
            self.tables.append(_Table())
        return self.tables[-1]

    def _close(self) -> None:
        """Tanca la taula mes interior: les seves files van a la cel·la que la conte."""
        table = self.tables.pop()
        row = table.end_row()
        rows = table.rows + ([row] if row else [])
        if self.tables:
            self.tables[-1].lines.extend(rows)
        else:
            for line in rows:
                self.out.add_line(line)

    def paragraph(self) -> None:
        # Un paragraf de fora de cap taula vol dir que la taula s'ha acabat, encara que la
        # seva darrera marca de fila no s'hagi reconegut: la fila surt abans que ell.
        while self.tables:
            self._close()
        self.out.text(self._take())
        self.out.end_line()

    def cell_paragraph(self, depth: int) -> None:
        self._enter(depth).lines.append(self._take())

    def cell_end(self, depth: int) -> None:
        self._enter(depth).end_cell(self._take())

    def row_end(self, depth: int) -> None:
        table = self._enter(depth)
        rest = self._take()
        if rest.strip():
            table.end_cell(rest)
        row = table.end_row()
        if row is None:
            return
        if depth == 1:
            self.out.add_line(row)
        else:
            table.rows.append(row)

    def page_break(self) -> None:
        self.paragraph()
        self.out.page_break()

    def result(self) -> list[str]:
        self.paragraph()
        return self.out.result()


def _is_row_end(paragraph: _Paragraph | None) -> bool:
    return paragraph is not None and paragraph.row_end


def _render(
    text: str, segments: list[_Segment], paragraphs: dict[int, _Paragraph] | None
) -> list[str]:
    """El text principal, en pagines."""
    starts = [segment.start for segment in segments]

    def table_paragraph(index: int) -> _Paragraph | None:
        """Les propietats de taula del paragraf que acaba a la marca `index`."""
        if not paragraphs:
            return None
        segment = segments[bisect.bisect_right(starts, index) - 1]
        return paragraphs.get(segment.offset_after(index))

    # Si les propietats no marquen cap fila pero hi ha cel·les, no se'n poden refiar: les
    # taules es llegeixen amb la regla de reserva.
    rows_marked = paragraphs is not None and (
        _CELL_MARK not in text
        or any(_is_row_end(table_paragraph(m.start())) for m in re.finditer(_CELL_MARK, text))
    )
    fields = _matched_fields(text) if _FIELD_BEGIN in text else frozenset()
    layout = _Layout()
    # Per a cada camp obert: si ja som al resultat. `hidden` = camps encara a la instruccio.
    open_fields: list[bool] = []
    hidden = 0
    # Regla de reserva: si l'ultim que s'ha llegit es una marca 0x07 (cap text des d'aleshores).
    after_cell_mark = False
    position = 0
    for match in _TOKENS.finditer(text):
        index = match.start()
        if not hidden and index > position and layout.text(text[position:index]):
            after_cell_mark = False
        position = index + 1
        char = text[index]
        if char in (_FIELD_BEGIN, _FIELD_SEPARATOR, _FIELD_END):
            if index not in fields:
                continue
            if char == _FIELD_BEGIN:
                open_fields.append(False)
                hidden += 1
            elif char == _FIELD_SEPARATOR:
                if open_fields and not open_fields[-1]:
                    open_fields[-1] = True
                    hidden -= 1
            elif open_fields and not open_fields.pop():
                hidden -= 1
            continue
        if hidden:
            continue
        if char == _PAGE_BREAK:
            layout.page_break()
        elif char == _CELL_MARK:
            # Una marca de fila la diuen les propietats; a la reserva, la que en segueix una altra.
            row_end = _is_row_end(table_paragraph(index)) if rows_marked else after_cell_mark
            if row_end:
                layout.row_end(1)
            else:
                layout.cell_end(1)
        elif not rows_marked:
            # Regla de reserva: un paragraf despres de la primera cel·la es d'una cel·la.
            if layout.row_open():
                layout.cell_paragraph(1)
            else:
                layout.paragraph()
        else:
            # Una taula niada no porta marques 0x07: les seves cel·les i files acaben amb un
            # final de paragraf que les propietats diuen de quina mena es.
            found = table_paragraph(index)
            if found is None:
                layout.paragraph()
            elif found.inner_row_end:
                layout.row_end(found.depth)
            elif found.inner_cell:
                layout.cell_end(found.depth)
            else:
                layout.cell_paragraph(found.depth)
        after_cell_mark = char == _CELL_MARK
    if not hidden and position < len(text):
        layout.text(text[position:])
    return layout.result()


# --------------------------------------------------------------------------- #
# Entrada                                                                       #
# --------------------------------------------------------------------------- #


def _read_doc(data: bytes) -> list[str]:
    if not data.startswith(OLE2_MAGIC):
        raise _NotWordBinary
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - olefile llanca de tot amb un fitxer malmes
        raise _unreadable("El contenidor OLE2 del .doc no es pot llegir.") from exc
    try:
        if not ole.exists("WordDocument"):
            raise _NotWordBinary
        word = _stream(ole, "WordDocument")
        fib = _fib(word)
        table = _stream(ole, fib.table)
        pieces = _pieces(table, *fib.clx)
        # Cada lectura de propietats te la seva feina comptada: l'una no deixa l'altra a zero.
        deleted = _deleted(_Properties(word, table, None), *fib.chpx)
        text, segments = _main_text(word, pieces, fib.ccp_text, deleted)
        # Sense cap marca 0x07 no hi ha cap taula (tampoc de niada: van dins d'una cel·la).
        paragraphs = (
            _paragraphs(_Properties(word, table, _DataStream(ole)), *fib.papx)
            if _CELL_MARK in text
            else None
        )
        pages = _render(text, segments, paragraphs)
    finally:
        ole.close()
    if len(pages) > settings.DOCUMENT_MAX_PAGES:
        raise _DocError(
            TOO_MANY_PAGES,
            f"{len(pages)} pagines superen el limit de {settings.DOCUMENT_MAX_PAGES}.",
        )
    if any(not segment.compressed for segment in segments):
        # Ara si: els parells suplents tornen a ser un sol caracter (un de solt, U+FFFD).
        pages = [
            page.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le", errors="replace")
            for page in pages
        ]
    return pages


def _not_word_binary() -> ExtractionResult:
    return dataclasses.replace(
        unsupported_result(DOC),
        parser_version=DOC_PARSER_VERSION,
        error_message=(
            "No es un document de Word binari (pot ser un RTF o un HTML desat com a .doc): "
            "no se n'extreu el text en aquesta versio."
        ),
    )


def extract_doc(data: bytes) -> ExtractionResult:
    """Extreu el text d'un .doc. Sincrona i CPU-bound: es crida en un fil. Mai llanca."""
    started = time.monotonic()
    try:
        pages = _read_doc(data)
    except _NotWordBinary:
        return _not_word_binary()
    except _DocError as exc:
        return _failed(DOC_PARSER_VERSION, exc.code, exc.message, started)
    except Exception:  # noqa: BLE001 - qualsevol estructura que no quadri
        return _failed(DOC_PARSER_VERSION, UNREADABLE, "No s'ha pogut llegir el .doc.", started)
    return _result(DOC_PARSER_VERSION, pages, started)


EXTRACTORS = {DOC: extract_doc}
