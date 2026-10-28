"""Generador de .doc (Word 97-2003) minims per als tests, sense Word ni cap biblioteca.

Escriu a ma, com `pdf_builder`, els bytes d'un contenidor OLE2/CFB (versio 3, sectors de
512 bytes) amb els fluxos `WordDocument`, `1Table` (o `0Table`) i, si cal, `Data`: una FIB de
Word 97, el text en peces de 8 bits (cp1252) o UTF-16 i la taula de peces (CLX). Si hi ha taules,
les propietats de paragraf que en diuen les files (PlcBtePapx i FKP amb `sprmPFInTable` i
`sprmPFTtp`; a les niades, `sprmPItap`, `sprmPFInnerTableCell` i `sprmPFInnerTtp`; amb
`huge_rows`, les de final de fila al flux `Data`, per `sprmPHugePapx`). Si hi ha text suprimit, les
propietats de caracter que ho diuen (PlcBteChpx i FKP amb `sprmCFRMarkDel`). Tots els fluxos fan
com a minim 4096 bytes: van a sectors normals, sense mini-stream, que es el cas mes senzill
d'escriure i `olefile` el llegeix igual.

Les posicions (CP) i els desplacaments (FC) es compten en unitats de codi, com fa Word: un
caracter de fora del BMP en una peca UTF-16 n'ocupa dues.

Convencions del text d'entrada:

* `\\x07` tanca una cel·la; `ROW` (`\\x06`) tanca una fila (s'hi escriu `\\x07`);
* `CELL_BREAK` (`\\x04`) es un paragraf dins d'una cel·la (s'hi escriu `\\r`);
* `INNER_CELL` (`\\x10`) i `INNER_ROW` (`\\x11`) tanquen una cel·la i una fila d'una taula niada
  dins d'una cel·la de la de fora (s'hi escriu `\\r`);
* la resta de caracters (`\\r`, `\\x0c`, camps 0x13/0x14/0x15...) s'escriuen tal qual.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

ROW = "\x06"
CELL_BREAK = "\x04"
INNER_CELL = "\x10"
INNER_ROW = "\x11"

_SECTOR = 512
_MIN_STREAM = 4096
_END_OF_CHAIN = 0xFFFFFFFE
_FREE = 0xFFFFFFFF
_FAT_SECTOR = 0xFFFFFFFD
_NO_STREAM = 0xFFFFFFFF
_TEXT_OFFSET = 0x400
_FKP = 512

#: On son, a la FIB que escriu `_fib`, `ccpText` i el parell (fc, lcb) numero `index` de
#: `FibRgFcLcb97` (12 = propietats de caracter, 13 = de paragraf, 33 = taula de peces).
FIB_CCP_TEXT = 0x4C


def fib_pair(index: int) -> int:
    return 0x9A + index * 8


_ISTD = struct.pack("<H", 0)


def _sprm1(sprm: int, value: int) -> bytes:
    return struct.pack("<HB", sprm, value)


def _sprm4(sprm: int, value: int) -> bytes:
    return struct.pack("<HI", sprm, value)


#: Un `sprmTDefTable` (operand variable, amb dues cel·les), com els de debo.
_DEF_TABLE = struct.pack("<HH", 0xD608, 5) + b"\x00" * 4
#: Propietats de paragraf de prova (sense l'istd): dins de taula (amb un sprm de 2 bytes al
#: davant), final de fila, i cel·la i final de fila d'una taula niada (profunditat 2).
_IN_TABLE = struct.pack("<HH", 0x840F, 0) + _sprm1(0x2416, 1)
_ROW_END = _DEF_TABLE + _sprm1(0x2416, 1) + _sprm1(0x2417, 1)
_INNER_CELL = _sprm1(0x2416, 1) + _sprm4(0x6649, 2) + _sprm1(0x244B, 1)
_INNER_ROW_END = _sprm1(0x2416, 1) + _sprm4(0x6649, 2) + _sprm1(0x244C, 1) + _DEF_TABLE
#: On es desa al flux `Data` la grpprl dels finals de fila, amb `huge_rows`.
_HUGE_OFFSET = 0x40
_SPRM_HUGE_PAPX = 0x6646
_SPRM_RMARK_DEL = 0x0800

_PARAGRAPH_ENDS = "\r\x07" + ROW + CELL_BREAK + INNER_CELL + INNER_ROW


@dataclass(frozen=True)
class Piece:
    """Un tros del text. `deleted`: suprimit amb control de canvis, sencer (`True`) o nomes
    aquests trams `(inici, final)` de caracters del tros."""

    text: str
    compressed: bool = True
    deleted: bool | tuple[tuple[int, int], ...] = False


def row(*cells: str) -> str:
    """Una fila de taula: cada cel·la acaba amb la seva marca i la fila amb la de fila."""
    return "".join(cell + "\x07" for cell in cells) + ROW


def inner_row(*cells: str) -> str:
    """Una fila d'una taula niada (es posa dins d'una cel·la de `row`)."""
    return "".join(cell + INNER_CELL for cell in cells) + INNER_ROW


def _pad(data: bytes, size: int) -> bytes:
    return data + b"\x00" * (size - len(data)) if len(data) < size else data


def _papx_entry(grpprl: bytes) -> bytes:
    """Un PapxInFkp: amb `cb` si la llargada es senar, i amb `cb = 0` + `cb'` si es parell."""
    if len(grpprl) % 2:
        return bytes([(len(grpprl) + 1) // 2]) + grpprl
    return bytes([0, len(grpprl) // 2]) + grpprl


def _fkp(runs: list[tuple[int, int, bytes | None]]) -> bytes:
    """Una FKP de paragrafs: (fc inicial, fc final, propietats o res) per a cada paragraf."""
    page = bytearray(_FKP)
    count = len(runs)
    bounds = [runs[0][0], *(end for _start, end, _props in runs)]
    struct.pack_into(f"<{count + 1}I", page, 0, *bounds)
    offsets: dict[bytes, int] = {}
    cursor = _FKP - 1
    for _start, _end, props in runs:
        if props is not None and props not in offsets:
            entry = _papx_entry(props)
            cursor -= len(entry)
            cursor -= cursor % 2
            page[cursor : cursor + len(entry)] = entry
            offsets[props] = cursor
    assert 4 * (count + 1) + 13 * count <= cursor, "massa paragrafs per a una FKP de prova"
    for index, (_start, _end, props) in enumerate(runs):
        page[4 * (count + 1) + 13 * index] = 0 if props is None else offsets[props] // 2
    page[_FKP - 1] = count
    return bytes(page)


def _chpx_fkp(runs: list[tuple[int, int, bool]]) -> bytes:
    """Una FKP de caracters: (fc inicial, fc final, suprimit) per a cada tram."""
    page = bytearray(_FKP)
    count = len(runs)
    bounds = [runs[0][0], *(end for _start, end, _deleted in runs)]
    struct.pack_into(f"<{count + 1}I", page, 0, *bounds)
    entry = bytes([3]) + _sprm1(_SPRM_RMARK_DEL, 1)
    cursor = _FKP - 1 - len(entry)
    cursor -= cursor % 2
    page[cursor : cursor + len(entry)] = entry
    assert 4 * (count + 1) + count <= cursor, "massa trams per a una FKP de prova"
    for index, (_start, _end, deleted) in enumerate(runs):
        page[4 * (count + 1) + index] = cursor // 2 if deleted else 0
    page[_FKP - 1] = count
    return bytes(page)


def _plc_bte(chunks: list[list[tuple]], first_page: int) -> bytes:
    fcs = [chunk[0][0] for chunk in chunks] + [chunks[-1][-1][1]]
    pns = [first_page + index for index in range(len(chunks))]
    return struct.pack(f"<{len(fcs)}I", *fcs) + struct.pack(f"<{len(pns)}I", *pns)


def _fib(*, n_fib: int, flags: int, ccp_text: int, clx: tuple[int, int],
         papx: tuple[int, int], size: int, chpx: tuple[int, int] = (0, 0)) -> bytes:
    base = struct.pack(
        "<HHHHHHHIBBHHII", 0xA5EC, n_fib, 0, 0x0403, 0, flags, 0x00BF, 0, 0, 0, 0, 0, 0, 0
    )
    longs = [0] * 22
    longs[0] = size  # cbMac
    longs[3] = ccp_text
    pairs = [0] * (93 * 2)
    pairs[12 * 2 : 12 * 2 + 2] = chpx  # fcPlcfBteChpx, lcbPlcfBteChpx
    pairs[13 * 2 : 13 * 2 + 2] = papx  # fcPlcfBtePapx, lcbPlcfBtePapx
    pairs[33 * 2 : 33 * 2 + 2] = clx  # fcClx, lcbClx
    return (
        base
        + struct.pack("<H", 14) + b"\x00" * 28
        + struct.pack("<H", 22) + struct.pack("<22I", *longs)
        + struct.pack("<H", 93) + struct.pack("<186I", *pairs)
        + struct.pack("<H", 0)
    )


def _units(char: str, compressed: bool) -> int:
    """Quantes unitats de codi (CP) ocupa un caracter."""
    return 1 if compressed or ord(char) <= 0xFFFF else 2


def _deleted_spans(piece: Piece) -> list[tuple[int, int]]:
    if piece.deleted is True:
        return [(0, len(piece.text))]
    return list(piece.deleted or ())


def word_streams(
    pieces: Sequence[Piece] | str,
    *,
    ccp_text: int | None = None,
    n_fib: int = 0x00C1,
    encrypted: bool = False,
    obfuscated: bool = False,
    table_stream: str = "1Table",
    scramble: bool = False,
    prc: bool = False,
    paragraph_properties: bool = True,
    huge_rows: bool = False,
) -> dict[str, bytes]:
    """Els fluxos d'un .doc: `WordDocument`, el de taula i, amb `huge_rows`, `Data`."""
    if isinstance(pieces, str):
        pieces = [Piece(pieces)]
    stored = [
        piece.text.replace(ROW, "\x07").translate(
            {ord(CELL_BREAK): "\r", ord(INNER_CELL): "\r", ord(INNER_ROW): "\r"}
        )
        for piece in pieces
    ]
    encoded = [
        text.encode("cp1252") if piece.compressed else text.encode("utf-16-le")
        for text, piece in zip(stored, pieces, strict=True)
    ]
    word = bytearray(_TEXT_OFFSET)
    offsets: dict[int, int] = {}
    for index in reversed(range(len(stored))) if scramble else range(len(stored)):
        offsets[index] = len(word)
        word += encoded[index]
        word += b"\x00" * (len(word) % 2)
    text_end = len(word)

    # On acaba cada paragraf (el desplacament al flux despres de la marca), amb les seves
    # propietats, i quins trams del flux son suprimits.
    row_end = _ISTD + (_sprm4(_SPRM_HUGE_PAPX, _HUGE_OFFSET) if huge_rows else _ROW_END)
    props_by_end = {
        ROW: row_end,
        "\x07": _ISTD + _IN_TABLE,
        CELL_BREAK: _ISTD + _IN_TABLE,
        INNER_CELL: _ISTD + _INNER_CELL,
        INNER_ROW: _ISTD + _INNER_ROW_END,
    }
    ends: list[tuple[int, bytes | None]] = []
    deleted: list[tuple[int, int]] = []
    for index, piece in enumerate(pieces):
        width = 1 if piece.compressed else 2
        unit = 0
        positions = [0]
        for char in piece.text:
            unit += _units(char, piece.compressed)
            positions.append(unit)
            if char in _PARAGRAPH_ENDS:
                ends.append((offsets[index] + width * unit, props_by_end.get(char)))
        for start, end in _deleted_spans(piece):
            deleted.append(
                (offsets[index] + width * positions[start], offsets[index] + width * positions[end])
            )
    ends.sort()

    papx = chpx = (0, 0)
    table_tail = b""
    if (paragraph_properties and ends) or deleted:
        word += b"\x00" * (-len(word) % _FKP)
    clx_prefix = b"\x01" + struct.pack("<h", 3) + b"\x01\x02\x03" if prc else b""
    cps = [0]
    for piece in pieces:
        cps.append(cps[-1] + sum(_units(char, piece.compressed) for char in piece.text))
    pcds = b"".join(
        struct.pack(
            "<HIH",
            0,
            (offsets[index] * 2) | 0x40000000 if piece.compressed else offsets[index],
            0,
        )
        for index, piece in enumerate(pieces)
    )
    plc_pcd = struct.pack(f"<{len(cps)}I", *cps) + pcds
    clx = clx_prefix + b"\x02" + struct.pack("<I", len(plc_pcd)) + plc_pcd

    if paragraph_properties and ends:
        first = _TEXT_OFFSET
        runs = []
        for end, props in ends:
            runs.append((first, end, props))
            first = end
        chunks = [runs[i : i + 20] for i in range(0, len(runs), 20)]
        first_page = len(word) // _FKP
        word += b"".join(_fkp(chunk) for chunk in chunks)
        plc = _plc_bte(chunks, first_page)
        papx = (len(clx) + len(table_tail), len(plc))
        table_tail += plc

    if deleted:
        bounds = sorted({_TEXT_OFFSET, text_end, *(edge for span in deleted for edge in span)})
        runs = [
            (start, end, any(a <= start and end <= b for a, b in deleted))
            for start, end in zip(bounds, bounds[1:], strict=False)
            if start < end
        ]
        chunks = [runs[i : i + 50] for i in range(0, len(runs), 50)]
        first_page = len(word) // _FKP
        word += b"".join(_chpx_fkp(chunk) for chunk in chunks)
        plc = _plc_bte(chunks, first_page)
        chpx = (len(clx) + len(table_tail), len(plc))
        table_tail += plc

    flags = (0x0200 if table_stream == "1Table" else 0) | (0x0100 if encrypted else 0)
    flags |= 0x8000 if obfuscated else 0
    word = _pad(bytes(word), _MIN_STREAM)
    fib = _fib(
        n_fib=n_fib,
        flags=flags,
        ccp_text=cps[-1] if ccp_text is None else ccp_text,
        clx=(0, len(clx)),
        chpx=chpx,
        papx=papx,
        size=len(word),
    )
    word = fib + word[len(fib) :]
    streams = {"WordDocument": word, table_stream: _pad(clx + table_tail, _MIN_STREAM)}
    if huge_rows:
        grpprl = _ROW_END
        data = bytearray(_HUGE_OFFSET) + struct.pack("<h", len(grpprl)) + grpprl
        streams["Data"] = _pad(bytes(data), _MIN_STREAM)
    return streams


def with_fib(word: bytes, *, ccp_text: int | None = None,
             pairs: dict[int, tuple[int, int]] | None = None) -> bytes:
    """El flux `WordDocument` amb `ccpText` o alguns parells (fc, lcb) de la FIB canviats."""
    patched = bytearray(word)
    if ccp_text is not None:
        struct.pack_into("<i", patched, FIB_CCP_TEXT, ccp_text)
    for index, pair in (pairs or {}).items():
        struct.pack_into("<II", patched, fib_pair(index), *pair)
    return bytes(patched)


def _entry(name: str, kind: int, *, right: int = _NO_STREAM, child: int = _NO_STREAM,
           start: int = _END_OF_CHAIN, size: int = 0) -> bytes:
    encoded = (name + "\x00").encode("utf-16-le")
    return (
        encoded.ljust(64, b"\x00")
        + struct.pack("<HBBIII", len(encoded), kind, 1, _NO_STREAM, right, child)
        + b"\x00" * 16  # CLSID
        + struct.pack("<I", 0)  # bits d'estat
        + b"\x00" * 16  # dates
        + struct.pack("<IQ", start, size)
    )


def build_cfb(streams: dict[str, bytes]) -> bytes:
    """Un contenidor OLE2 v3 amb aquests fluxos (de >= 4096 bytes) penjant de l'arrel."""
    names = sorted(streams, key=lambda name: (len(name), name.upper()))
    sectors: list[bytes] = []
    chains: dict[str, tuple[int, int]] = {}
    for name in names:
        data = streams[name]
        assert len(data) >= _MIN_STREAM, "el generador no escriu mini-stream"
        count = -(-len(data) // _SECTOR)
        chains[name] = (len(sectors), count)
        padded = data + b"\x00" * (count * _SECTOR - len(data))
        sectors += [padded[i : i + _SECTOR] for i in range(0, len(padded), _SECTOR)]
    entries = [
        _entry("Root Entry", 5, child=1),
        *(
            _entry(name, 2, right=index + 2 if index + 1 < len(names) else _NO_STREAM,
                   start=chains[name][0], size=len(streams[name]))
            for index, name in enumerate(names)
        ),
    ]
    directory = b"".join(entries)
    directory += b"\x00" * (-len(directory) % _SECTOR)
    directory_first = len(sectors)
    directory_count = len(directory) // _SECTOR
    sectors += [directory[i : i + _SECTOR] for i in range(0, len(directory), _SECTOR)]
    fat_count = 1
    while fat_count * (_SECTOR // 4) < len(sectors) + fat_count:
        fat_count += 1
    fat_first = len(sectors)
    fat = [_FREE] * (fat_count * (_SECTOR // 4))
    for first, count in [*chains.values(), (directory_first, directory_count)]:
        for sector in range(first, first + count):
            fat[sector] = sector + 1 if sector + 1 < first + count else _END_OF_CHAIN
    for sector in range(fat_first, fat_first + fat_count):
        fat[sector] = _FAT_SECTOR
    fat_bytes = struct.pack(f"<{len(fat)}I", *fat)
    sectors += [fat_bytes[i : i + _SECTOR] for i in range(0, len(fat_bytes), _SECTOR)]
    assert fat_count <= 109
    difat = [fat_first + i for i in range(fat_count)] + [_FREE] * (109 - fat_count)
    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\x00" * 16
        + struct.pack("<HHHHH", 0x003E, 0x0003, 0xFFFE, 0x0009, 0x0006)
        + b"\x00" * 6
        + struct.pack(
            "<IIIIIIIII", 0, fat_count, directory_first, 0, 0x1000, _END_OF_CHAIN, 0,
            _END_OF_CHAIN, 0,
        )
        + struct.pack("<109I", *difat)
    )
    assert len(header) == _SECTOR
    return header + b"".join(sectors)


def build_doc(pieces: Sequence[Piece] | str, **options) -> bytes:
    """Un .doc sencer. Les opcions son les de `word_streams`."""
    return build_cfb(word_streams(pieces, **options))
