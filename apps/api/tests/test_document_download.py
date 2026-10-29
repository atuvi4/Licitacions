"""Descarrega de documents: nomes bytes, mai base de dades.

Tota la suite corre sense Internet. Els bytes venen d'un `MockTransport` i el
DNS d'un resolutor injectat. L'unic test que aixeca un socket de veritat es el
del tall per mida, perque un mock no demostra que el streaming s'aturi.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from app.core.config import settings
from app.services.document_download import (
    CHECKSUM_MISMATCH,
    IMAGE_MIME_TYPES,
    OLE2_MAGIC,
    DownloadError,
    download_document,
    refine_ole2,
    resolve_mime_type,
    sniff_mime_type,
)

PUBLIC_IP = "93.184.216.34"
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """Els reintents reals esperen segons. Als tests no cal esperar res."""
    monkeypatch.setattr(settings, "DOCUMENT_DOWNLOAD_BACKOFF_SECONDS", 0.0)


def resolver_returning(*addresses: str):
    def _resolve(host: str, port: int) -> list[str]:
        return list(addresses or (PUBLIC_IP,))

    return _resolve


def client_for(handler) -> httpx.AsyncClient:
    """Client amb transport fals. `follow_redirects` s'ha de quedar desactivat."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def test_downloads_a_valid_pdf_and_hashes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
        )

    assert result.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert result.file_size == len(PDF_BYTES)
    assert result.mime_type == "application/pdf"
    assert result.detected_extension == "pdf"
    assert result.original_filename == "pcap.pdf"
    assert result.http_status == 200
    assert result.redirect_count == 0


async def test_reports_404_without_retrying():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/no.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "not_found"
    assert caught.value.http_status == 404
    assert len(calls) == 1, "un 404 no s'ha de reintentar"


async def test_retries_a_500_and_then_succeeds():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) < 3:
            return httpx.Response(500)
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
        )

    assert len(calls) == 3
    assert result.http_status == 200


async def test_gives_up_after_the_configured_attempts():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503)

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
            )

    assert len(calls) == settings.DOCUMENT_DOWNLOAD_MAX_ATTEMPTS
    assert caught.value.code == "http_error"


async def test_reports_timeouts():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("massa lent", request=request)

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "timeout"


async def test_refuses_a_declared_content_length_over_the_limit():
    too_big = settings.document_max_size_bytes + 1

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=PDF_BYTES,
            headers={"content-type": "application/pdf", "content-length": str(too_big)},
        )

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/gros.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "too_large"


async def test_refuses_a_body_over_the_limit_without_content_length(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENT_MAX_SIZE_MB", 1)
    payload = b"%PDF-1.4\n" + b"x" * (2 * 1024 * 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/gros.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "too_large"


async def test_rejects_html_pretending_to_be_a_pdf():
    html = b"<!DOCTYPE html><html><body>Sessio caducada</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "invalid_content"


async def test_rejects_a_mime_type_outside_the_allow_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\x89PNG\r\n\x1a\n", headers={"content-type": "image/png"}
        )

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/imatge.png", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "invalid_content"


async def test_follows_a_valid_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://example.com/final.pdf"})
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/redirect", client=client, resolver=resolver_returning()
        )

    assert result.redirect_count == 1
    assert result.final_url == "https://example.com/final.pdf"
    assert result.mime_type == "application/pdf"


async def test_blocks_a_redirect_towards_a_private_address():
    """L'atac clau: la URL inicial es publica i la redireccio apunta a metadades."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "blocked_url"


async def test_stops_after_too_many_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/una-altra"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/inici", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "too_many_redirects"


async def test_reads_the_filename_from_content_disposition():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=PDF_BYTES,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="PCAP signat.pdf"',
            },
        )

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/descarrega?id=42", client=client, resolver=resolver_returning()
        )

    assert result.detected_filename == "PCAP signat.pdf"


async def test_accepts_xml_because_placsp_publishes_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b'<?xml version="1.0"?><root/>', headers={"content-type": "text/xml"}
        )

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/dades.xml", client=client, resolver=resolver_returning()
        )

    assert result.mime_type == "application/xml"
    assert result.detected_extension == "xml"


async def test_keeps_the_declared_ooxml_type_over_the_generic_zip_sniff():
    declared = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"PK\x03\x04" + b"\x00" * 64, headers={"content-type": declared}
        )

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/annex.docx", client=client, resolver=resolver_returning()
        )

    assert result.mime_type == declared
    assert result.detected_extension == "docx"


async def test_rejects_an_empty_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/buit.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "invalid_content"


async def test_accepts_an_ole2_document_declared_with_a_wrong_mime_type():
    """Cas real de PLACSP: declara un DOCX malformat i serveix un .doc antic.

    El servidor menteix dues vegades (tipus equivocat i cadena sense el guio de
    `openxmlformats-officedocument`). Els magic bytes manen: es un OLE2, i com
    que el format es admes, l'original s'ha de conservar.
    """
    declared = "application/vnd.openxmlformatsofficedocument.wordprocessingml.document"
    body = OLE2_MAGIC + b"\x00" * 512

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={
                "content-type": declared,
                "content-disposition": 'inline;filename=ANEXO I.doc',
            },
        )

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/servlet/GetDocumentByIdServlet?id=42",
            client=client,
            resolver=resolver_returning(),
        )

    assert result.mime_type == "application/x-ole-storage"
    assert result.declared_mime_type == declared
    assert result.detected_extension == "ole"
    assert result.detected_filename == "ANEXO I.doc"
    assert result.sha256 == hashlib.sha256(body).hexdigest()


async def test_a_declared_legacy_office_type_refines_the_generic_ole2_sniff():
    """Si el servidor si que encerta el tipus, la declaracio afina el sniffing."""
    body = OLE2_MAGIC + b"\x00" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/msword"})

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/annex", client=client, resolver=resolver_returning()
        )

    assert result.mime_type == "application/msword"
    assert result.detected_extension == "doc"


async def test_an_unrefined_ole2_with_a_word_stream_is_a_doc():
    """El mateix cas real amb un .doc de debo: el contenidor porta `WordDocument` i, com que
    cap declaracio no l'ha afinat, s'hi llegeix el tipus (Bloc 2): es un .doc."""
    from tests.fixtures.doc_builder import build_doc

    declared = "application/vnd.openxmlformatsofficedocument.wordprocessingml.document"
    body = build_doc("ANNEX I\r")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": declared})

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/servlet/GetDocumentByIdServlet?id=42",
            client=client,
            resolver=resolver_returning(),
        )

    assert result.mime_type == "application/msword"
    assert result.detected_extension == "doc"
    assert result.declared_mime_type == declared


async def test_an_unrefined_ole2_without_a_word_stream_stays_generic():
    """Un full de calcul OLE2 (`Workbook`) no es cap .doc: es queda generic."""
    from tests.fixtures.doc_builder import build_cfb

    body = build_cfb({"Workbook": b"\x09\x08" + b"\x00" * 4094})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body, headers={"content-type": "application/octet-stream"}
        )

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/annex", client=client, resolver=resolver_returning()
        )

    assert result.mime_type == "application/x-ole-storage"


def test_refine_ole2_only_refines_the_generic_type():
    from tests.fixtures.doc_builder import build_doc

    word = build_doc("Hola\r")
    assert refine_ole2("application/x-ole-storage", word) == "application/msword"
    # Una declaracio concreta mana (ja no es el generic): no es toca.
    assert refine_ole2("application/vnd.ms-excel", word) == "application/vnd.ms-excel"
    assert refine_ole2("application/pdf", word) == "application/pdf"
    # Un contenidor que no es pot llegir es queda generic.
    assert refine_ole2("application/x-ole-storage", OLE2_MAGIC + b"\x00" * 64) == (
        "application/x-ole-storage"
    )


async def test_an_ole2_body_on_a_pdf_url_is_still_invalid_content():
    """La guarda del PDF no s'ha relaxat: una URL .pdf que retorna OLE2 menteix."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=OLE2_MAGIC + b"\x00" * 64, headers={"content-type": "application/msword"}
        )

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as caught:
            await download_document(
                "https://example.com/pcap.pdf", client=client, resolver=resolver_returning()
            )

    assert caught.value.code == "invalid_content"


def test_sniffing_recognises_ole2():
    assert sniff_mime_type(OLE2_MAGIC + b"rest") == "application/x-ole-storage"
    # Un prefix parcial no es una firma valida.
    assert sniff_mime_type(b"\xd0\xcf\x11") is None


def test_sniffing_recognises_the_formats_that_matter():
    assert sniff_mime_type(b"%PDF-1.7\n...") == "application/pdf"
    assert sniff_mime_type(b"PK\x03\x04rest") == "application/zip"
    assert sniff_mime_type(b"  <!DOCTYPE html><html>") == "text/html"
    assert sniff_mime_type(b'<?xml version="1.0"?>') == "application/xml"
    assert sniff_mime_type(b"text pla sense magic") is None


def test_resolve_mime_type_refuses_a_pdf_url_serving_something_else():
    with pytest.raises(DownloadError) as caught:
        resolve_mime_type("text/html", "text/html", "https://example.com/pcap.pdf")
    assert caught.value.code == "invalid_content"


async def test_aborts_a_real_stream_that_exceeds_the_limit(monkeypatch):
    """Un mock no demostra que el streaming s'aturi de veritat; un socket si.

    El servidor intenta enviar 8 MB amb un limit d'1 MB. Si el tall funciona, el
    client tanca la connexio i el servidor rep un BrokenPipe.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - firma imposada per la stdlib
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(b"%PDF-1.4\n")
            try:
                for _ in range(128):
                    self.wfile.write(b"x" * 65536)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # el client ha tallat: exactament el que volem provar

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    monkeypatch.setattr(settings, "DOCUMENT_MAX_SIZE_MB", 1)
    monkeypatch.setattr(settings, "DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS", True)
    monkeypatch.setattr(settings, "DOCUMENT_DOWNLOAD_ALLOWED_PORTS", f"80,443,{port}")

    try:
        with pytest.raises(DownloadError) as caught:
            await download_document(f"http://127.0.0.1:{port}/gros.pdf")
        assert caught.value.code == "too_large"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_sniffing_recognises_images():
    assert sniff_mime_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert sniff_mime_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert sniff_mime_type(b"GIF89arest") == "image/gif"
    assert sniff_mime_type(b"II*\x00rest") == "image/tiff"
    assert sniff_mime_type(b"RIFF\x00\x00\x00\x00WEBPrest") == "image/webp"


async def test_the_expected_md5_is_checked_before_the_type_and_the_size():
    """Amb `expected_md5`, uns bytes que no son els publicats son `checksum_mismatch`, encara
    que siguin buits o d'un tipus no admes: no es una propietat del fitxer publicat."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as raised:
            await download_document(
                "https://example.com/doc",
                client=client,
                resolver=resolver_returning(),
                expected_md5=hashlib.md5(PDF_BYTES).hexdigest(),
            )

    assert raised.value.code == CHECKSUM_MISMATCH


async def test_the_published_bytes_with_the_right_md5_are_returned():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "application/pdf"})

    async with client_for(handler) as client:
        result = await download_document(
            "https://example.com/doc",
            client=client,
            resolver=resolver_returning(),
            expected_md5=hashlib.md5(PDF_BYTES).hexdigest().lower(),
        )

    assert result.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()


async def test_images_are_only_admitted_when_asked_for():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=png, headers={"content-type": "image/png"})

    async with client_for(handler) as client:
        with pytest.raises(DownloadError) as raised:
            await download_document(
                "https://example.com/grafic", client=client, resolver=resolver_returning()
            )
        result = await download_document(
            "https://example.com/grafic",
            client=client,
            resolver=resolver_returning(),
            extra_mime_types=IMAGE_MIME_TYPES,
        )

    assert raised.value.code == "invalid_content"
    assert result.mime_type == "image/png" and result.detected_extension == "png"
