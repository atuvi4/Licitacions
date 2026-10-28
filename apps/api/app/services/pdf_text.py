"""Extraccio de text de PDF, pagina a pagina.

Biblioteca: **pypdf**, llicencia BSD-3-Clause. El criteri decisiu es la
llicencia: PyMuPDF es AGPL i, en un servei accessible per xarxa, obligaria a
publicar el codi del producte sencer. pypdf a mes es Python pur, aixi que no
afegeix cap dependencia de sistema ni al Dockerfile ni al desenvolupament a
Windows.

Es guarda `parser_version` a cada extraccio: si algun dia canviem de biblioteca
o d'estrategia, es podra tornar a extreure el text sobre els mateixos bytes
sense destruir el que ja teniem.

El resultat NO es un string gegant. La separacio per pagina es el requisit:
sense ella, una cita futura del tipus "solvencia economica, PCAP pagina 27" no
es pot verificar.

**PDF xifrats.** Molts formularis oficials (el model del DEUC, per exemple) venen
xifrats amb AES pero sense contrasenya d'obertura: nomes restringeixen permisos.
pypdf hi prova la contrasenya buida, i amb `cryptography` (l'extra `crypto`) els
obre. Dues fallades que no s'han de confondre entre elles ni amb un PDF malmes:

* `encrypted_pdf`: el PDF demana una contrasenya de debo. Es del document.
* `pdf_crypto_unavailable`: falta la biblioteca de l'AES. Es de l'entorn, no del
  document, i per aixo no es definitiva: quan el proces ja la te, el seguent
  processat ho torna a provar (`should_retry`). Pot sortir en obrir el fitxer, en
  llegir l'arbre de pagines o en llegir una pagina; en tots tres casos tomba el
  document sencer.

pypdf llanca el mateix `DependencyError` quan li falta el programa `jbig2dec`: nomes
es llegeix com a falta de l'AES si el PDF es xifrat i l'AES de debo no hi es. Si no,
es una fallada com qualsevol altra (una pagina `FAILED`, o `corrupt_pdf`), com abans.

Obrir-los no canvia com es llegeix cap PDF sense xifrar: la versio continua sent
`pypdf-v1`.
"""

from __future__ import annotations

import functools
import hashlib
import io
import logging
import re
import time
import unicodedata
from dataclasses import dataclass

import anyio
from pypdf import PdfReader
from pypdf.errors import DependencyError, FileNotDecryptedError

from app.core.config import settings
from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus

logger = logging.getLogger(__name__)

#: Identifica com s'ha extret el text. Canviar de parser o d'estrategia obliga
#: a pujar aquesta versio: es el que fa possible re-extreure sense re-descarregar.
PARSER_VERSION = "pypdf-v1"

#: Falta la biblioteca criptografica que pypdf necessita per a l'AES.
CRYPTO_UNAVAILABLE = "pdf_crypto_unavailable"
_CRYPTO_UNAVAILABLE_MESSAGE = (
    "Falta la biblioteca criptografica (cryptography) per obrir aquest PDF xifrat."
)
#: El proveidor de pypdf quan no troba ni `cryptography` ni `pycryptodome`: fa RC4,
#: pero no AES.
_FALLBACK_PROVIDER = "local_crypt_fallback"
#: Com desava `corrupt_pdf` un PDF AES el codi d'abans de `pdf_crypto_unavailable`
#: (`f"{type(exc).__name__}: {exc}"` del constructor o de l'arbre de pagines).
_LEGACY_MISSING_AES = re.compile(r"^DependencyError: .*\bAES\b")

# Es conserven el salt de linia i el tabulador; la resta de caracters de control
# no aporten res i embruten el text.
_CONTROL_CODES = frozenset(set(range(32)) - {9, 10} | {127})
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    #: 1-based, com la pagina que veu l'usuari. Mai 0-based.
    page_number: int
    text: str
    char_count: int
    text_hash: str
    status: PageExtractionStatus


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    parser_version: str
    status: ExtractionStatus
    quality: ExtractionQuality
    pages: tuple[ExtractedPage, ...]
    page_count: int
    extracted_page_count: int
    empty_page_count: int
    text_char_count: int
    ocr_candidate: bool
    parse_ms: int
    error_code: str | None
    error_message: str | None


def normalize_page_text(raw: str) -> str:
    """Neteja minima i conservadora.

    NO es col·lapsen els espais interns: en un plec, l'alineacio de columnes es
    informacio util per a la fase seguent. Aixo no resumeix ni reinterpreta res.
    """
    text = unicodedata.normalize("NFC", raw or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if ord(char) not in _CONTROL_CODES)
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    return _BLANK_RUN.sub("\n\n", text).strip("\n")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluate_quality(
    page_count: int, empty_page_count: int, text_char_count: int
) -> tuple[ExtractionQuality, bool]:
    """Qualitat determinista i el senyal `ocr_candidate`.

    `ocr_candidate` es un INDICI, no una prova: un plec pot ser legitimament
    curt. La decisio de fer OCR es posterior, mes cara i separada.
    """
    if page_count <= 0:
        return ExtractionQuality.EMPTY, False
    if text_char_count == 0:
        return ExtractionQuality.EMPTY, True
    mean_chars = text_char_count / page_count
    empty_ratio = empty_page_count / page_count
    if mean_chars < settings.DOCUMENT_LOW_TEXT_CHARS_PER_PAGE or empty_ratio > 0.5:
        return ExtractionQuality.LOW_TEXT, True
    return ExtractionQuality.GOOD, False


def _failed(code: str, message: str, started: float) -> ExtractionResult:
    return ExtractionResult(
        parser_version=PARSER_VERSION,
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
        error_message=message[:500],
    )


def unsupported_result(mime_type: str) -> ExtractionResult:
    """Resultat per a un format del qual no n'extreiem text (XML, ZIP, DOCX...).

    No es un error: l'original s'ha guardat correctament, simplement no en
    llegim el text en aquesta iteracio.
    """
    return ExtractionResult(
        parser_version=PARSER_VERSION,
        status=ExtractionStatus.SKIPPED_UNSUPPORTED,
        quality=ExtractionQuality.NOT_APPLICABLE,
        pages=(),
        page_count=0,
        extracted_page_count=0,
        empty_page_count=0,
        text_char_count=0,
        ocr_candidate=False,
        parse_ms=0,
        error_code="unsupported_for_text_extraction",
        error_message=f"No s'extreu text de {mime_type} en aquesta versio.",
    )


def crypto_available() -> bool:
    """Si el pypdf d'aquest proces sap desxifrar AES.

    pypdf tria el proveidor en importar-se: si `cryptography` s'instal·la amb el
    proces en marxa, no compta fins que es reinicia. El registre de proveidors es
    intern de pypdf: si una versio futura el mou, es prova l'AES de debo.
    """
    try:
        from pypdf._crypt_providers import crypt_provider
    except ImportError:
        return _aes_round_trip()
    return crypt_provider[0] != _FALLBACK_PROVIDER


@functools.lru_cache(maxsize=1)
def _aes_round_trip() -> bool:
    """Xifra amb AES-128 un PDF d'una pagina en blanc i el torna a obrir (API publica)."""
    from pypdf import PdfWriter

    try:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.encrypt(user_password="", owner_password="tuvi", algorithm="AES-128")
        out = io.BytesIO()
        writer.write(out)
        return len(PdfReader(io.BytesIO(out.getvalue())).pages) == 1
    except Exception:  # noqa: BLE001 - DependencyError o qualsevol altra: sense AES
        return False


def _missing_aes(reader: PdfReader | None) -> bool:
    """Si un `DependencyError` es la falta de l'AES (i no la del `jbig2dec`).

    Sense `reader` (en obrir el fitxer) encara no se sap si es xifrat: mana l'entorn.
    """
    return (reader is None or reader.is_encrypted) and not crypto_available()


def _failure_message(exc: Exception) -> str:
    """El missatge d'una fallada per una excepcio.

    Un `DependencyError` amb l'AES a punt porta una marca: la forma sense marca la reintenta
    `should_retry` (es la que deixava el codi d'abans), i aquesta no s'ha de reintentar mai.
    """
    if isinstance(exc, DependencyError):
        return f"{type(exc).__name__} (amb AES): {exc}"
    return f"{type(exc).__name__}: {exc}"


def should_retry(error_code: str | None, error_message: str | None = None) -> bool:
    """Si una extraccio fallida s'ha de tornar a fer encara que la versio no hagi canviat.

    Nomes una fallada de l'entorn, i nomes quan l'entorn ja ho permet: sense la
    biblioteca, cada intent afegiria una revisio fallida mes i no llegiria res. Tambe
    la forma antiga: un PDF AES llegit abans que hi hagues `pdf_crypto_unavailable`
    va quedar `corrupt_pdf` amb el `DependencyError` de l'AES al missatge.
    """
    legacy = error_code == "corrupt_pdf" and bool(
        error_message and _LEGACY_MISSING_AES.match(error_message)
    )
    return (error_code == CRYPTO_UNAVAILABLE or legacy) and crypto_available()


def extract_pdf_text(data: bytes) -> ExtractionResult:
    """Extreu el text d'un PDF. Sincrona i CPU-bound: crida-la amb `extract_pdf_text_async`.

    Mai llanca: qualsevol fallada torna un `ExtractionResult` amb `error_code`.
    """
    started = time.monotonic()

    try:
        # Si el PDF es xifrat, el constructor ja hi prova la contrasenya buida.
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:  # noqa: BLE001 - qualsevol PDF trencat acaba aqui
        if isinstance(exc, DependencyError) and _missing_aes(None):
            return _failed(CRYPTO_UNAVAILABLE, _CRYPTO_UNAVAILABLE_MESSAGE, started)
        return _failed("corrupt_pdf", _failure_message(exc), started)

    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DependencyError) and _missing_aes(reader):
                return _failed(CRYPTO_UNAVAILABLE, _CRYPTO_UNAVAILABLE_MESSAGE, started)
            return _failed("encrypted_pdf", _failure_message(exc), started)
        if not opened:
            return _failed("encrypted_pdf", "El PDF esta protegit amb contrasenya.", started)

    try:
        # Amb un PDF xifrat, l'arbre de pagines ja es llegeix desxifrant (AES inclos).
        page_count = len(reader.pages)
    except FileNotDecryptedError:
        return _failed("encrypted_pdf", "El PDF esta protegit amb contrasenya.", started)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, DependencyError) and _missing_aes(reader):
            return _failed(CRYPTO_UNAVAILABLE, _CRYPTO_UNAVAILABLE_MESSAGE, started)
        return _failed("corrupt_pdf", _failure_message(exc), started)

    if page_count == 0:
        return _failed("corrupt_pdf", "El PDF no declara cap pagina.", started)
    if page_count > settings.DOCUMENT_MAX_PAGES:
        return _failed(
            "too_many_pages",
            f"{page_count} pagines superen el limit de {settings.DOCUMENT_MAX_PAGES}.",
            started,
        )

    pages: list[ExtractedPage] = []
    empty = 0
    extracted = 0
    total_chars = 0

    for index in range(page_count):
        number = index + 1
        try:
            raw = reader.pages[index].extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - una pagina trencada no tomba el document
            if isinstance(exc, DependencyError) and _missing_aes(reader):
                # No es una pagina trencada: cap pagina xifrada no es podria llegir. Una
                # extraccio amb totes les pagines buides diria "escanejat", i no ho es.
                return _failed(CRYPTO_UNAVAILABLE, _CRYPTO_UNAVAILABLE_MESSAGE, started)
            logger.warning("document.extract.page_failed", extra={"page": number})
            pages.append(ExtractedPage(number, "", 0, _hash_text(""), PageExtractionStatus.FAILED))
            continue

        text = normalize_page_text(raw)
        if text:
            extracted += 1
            total_chars += len(text)
            status = PageExtractionStatus.EXTRACTED
        else:
            empty += 1
            status = PageExtractionStatus.EMPTY
        pages.append(ExtractedPage(number, text, len(text), _hash_text(text), status))

    quality, ocr_candidate = evaluate_quality(page_count, empty, total_chars)

    return ExtractionResult(
        parser_version=PARSER_VERSION,
        status=ExtractionStatus.EXTRACTED,
        quality=quality,
        pages=tuple(pages),
        page_count=page_count,
        extracted_page_count=extracted,
        empty_page_count=empty,
        text_char_count=total_chars,
        ocr_candidate=ocr_candidate,
        parse_ms=int((time.monotonic() - started) * 1000),
        error_code=None,
        error_message=None,
    )


async def extract_pdf_text_async(data: bytes) -> ExtractionResult:
    """Executa el parser en un fil: es sincron i CPU-bound, no ha de bloquejar el loop."""
    return await anyio.to_thread.run_sync(extract_pdf_text, data)
