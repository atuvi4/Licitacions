"""Generador de PDF minims per als tests.

Escriu sintaxi PDF crua a ma. Es fa aixi expressament per NO afegir cap
dependencia (reportlab, fpdf) nomes per generar fixtures: la unica biblioteca
de PDF del projecte ha de ser el parser.

Els PDF que en surten son valids: capcalera, objectes numerats, taula xref amb
els desplacaments reals, trailer i %%EOF. `pypdf` els obre sense queixar-se.
"""

from __future__ import annotations

from collections.abc import Sequence


def _escape(text: str) -> bytes:
    """Codifica a WinAnsi (cp1252) i escapa els caracters especials del PDF.

    cp1252 cobreix accents catalans i castellans: a e i o u amb accent, dieresi,
    c trencada i enya.
    """
    encoded = text.encode("cp1252", errors="replace")
    out = bytearray()
    for byte in encoded:
        char = bytes([byte])
        if char in (b"(", b")", b"\\"):
            out += b"\\"
        out += char
    return bytes(out)


def _content_stream(text: str) -> bytes:
    body = bytearray(b"BT\n/F1 12 Tf\n14 TL\n72 720 Td\n")
    for index, line in enumerate(text.split("\n")):
        if index:
            body += b"T*\n"
        body += b"(" + _escape(line) + b") Tj\n"
    body += b"ET\n"
    return bytes(body)


def build_pdf(pages: Sequence[str]) -> bytes:
    """Genera un PDF amb una pagina per element.

    Una cadena buida genera una pagina SENSE stream de continguts: es la manera
    honesta de simular una pagina escanejada, de la qual no es pot extreure text.
    """
    if not pages:
        raise ValueError("Un PDF necessita com a minim una pagina.")

    objects: dict[int, bytes] = {}
    page_ids: list[int] = []
    next_id = 4  # 1=Catalog, 2=Pages, 3=Font

    for text in pages:
        page_id = next_id
        next_id += 1
        contents = b""
        if text:
            stream_id = next_id
            next_id += 1
            data = _content_stream(text)
            objects[stream_id] = (
                b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"endstream"
            )
            contents = b" /Contents " + str(stream_id).encode() + b" 0 R"
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            b" /Resources << /Font << /F1 3 0 R >> >>" + contents + b" >>"
        )
        page_ids.append(page_id)

    kids = b" ".join(str(pid).encode() + b" 0 R" for pid in page_ids)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"
    )
    objects[3] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += str(number).encode() + b" 0 obj\n" + objects[number] + b"\nendobj\n"

    xref_offset = len(out)
    size = max(objects) + 1
    out += b"xref\n0 " + str(size).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(size).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


def build_corrupt_pdf() -> bytes:
    """Capcalera valida i cos truncat: passa el sniffing pero no el parser."""
    return build_pdf(["Contingut que no arriba a acabar"])[:150]
