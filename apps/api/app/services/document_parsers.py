"""Quin parser toca a cada tipus de document, i el de text pla.

La `parser_version` forma part de la identitat d'una extraccio: `pypdf-v1` i
`plaintext-v1` son dues maneres diferents de llegir, i una cita a una pagina ha
de poder dir amb quina es va llegir. Per aixo aqui no hi ha una constant global
de versio, sino una per parser i una funcio que diu quina toca.

El que NO hi ha, i es deliberat: cap OCR i cap model. Llegir un document es
determinista o no es fa. Un PDF escanejat surt d'aqui amb el text que te —cap—
i amb `ocr_candidate`, que es un indici perque algu ho decideixi despres, no una
promesa que ja s'hagi fet.
"""

from __future__ import annotations

import asyncio
import time

from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus
from app.services.pdf_text import (
    PARSER_VERSION as PDF_PARSER_VERSION,
)
from app.services.pdf_text import (
    ExtractedPage,
    ExtractionResult,
    _hash_text,
    extract_pdf_text_async,
    normalize_page_text,
    unsupported_result,
)

#: Un fitxer de text no te versions de parser possibles: llegir-lo es
#: descodificar-lo. La versio hi es igualment perque una extraccio sempre ha de
#: poder dir com es va fer.
PLAIN_TEXT_PARSER_VERSION = "plaintext-v1"

PLAIN_TEXT_MIME = "text/plain"

_ENCODINGS = ("utf-8", "cp1252", "latin-1")


def parser_version_for(mime_type: str) -> str:
    """La versio de parser vigent per a aquest tipus.

    Serveix per saber si una extraccio guardada ja esta al dia: comparar-la amb
    una versio global diria que un TXT llegit amb `plaintext-v1` esta caducat
    perque el parser de PDF ha canviat.
    """
    if mime_type == PLAIN_TEXT_MIME:
        return PLAIN_TEXT_PARSER_VERSION
    return PDF_PARSER_VERSION


def extract_plain_text(data: bytes) -> ExtractionResult:
    """Llegeix un fitxer de text. Mai llanca.

    **Una sola pagina.** Un fitxer de text no en te: la pagina 1 es una frontera
    NOSTRA, no del document, i es posa perque la fase de requisits cita pagines
    i necessita alguna cosa a que apuntar. Inventar-ne mes —tallant per salts de
    pagina o per llargada— faria que una cita digues "pagina 4" d'una cosa que
    no en te.

    La codificacio es prova en ordre i la ultima no falla mai: un plec mal
    codificat es mes util llegit amb algun caracter rar que rebutjat del tot.
    """
    started = time.monotonic()
    text = ""
    for encoding in _ENCODINGS:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    normalized = normalize_page_text(text)
    char_count = len(normalized)
    empty = char_count == 0
    page = ExtractedPage(
        page_number=1,
        text=normalized,
        char_count=char_count,
        text_hash=_hash_text(normalized),
        status=PageExtractionStatus.EMPTY if empty else PageExtractionStatus.EXTRACTED,
    )
    # NO es fa servir `evaluate_quality`: el llindar de caracters per pagina
    # existeix per detectar una pagina de PDF a la qual li falta la capa de
    # text, i un fitxer de text no en te cap que pugui faltar. Aplicar-l'hi
    # diria "poc text" d'un fitxer curt que s'ha llegit sencer.
    quality = ExtractionQuality.EMPTY if empty else ExtractionQuality.GOOD
    return ExtractionResult(
        parser_version=PLAIN_TEXT_PARSER_VERSION,
        status=ExtractionStatus.EXTRACTED,
        quality=quality,
        pages=(page,),
        page_count=1,
        extracted_page_count=0 if empty else 1,
        empty_page_count=1 if empty else 0,
        text_char_count=char_count,
        # Un TXT sense text no es cap escaneig: no hi ha res a reconeixer.
        ocr_candidate=False,
        parse_ms=int((time.monotonic() - started) * 1000),
        error_code=None,
        error_message=None,
    )


async def extract_text_async(mime_type: str, data: bytes) -> ExtractionResult:
    """Llegeix els bytes amb el parser que toca al seu tipus."""
    if mime_type == PLAIN_TEXT_MIME:
        return await asyncio.to_thread(extract_plain_text, data)
    if mime_type == "application/pdf":
        return await extract_pdf_text_async(data)
    return unsupported_result(mime_type)  # pragma: no cover - el gate es abans
