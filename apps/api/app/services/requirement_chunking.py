"""Que s'analitza, com es talla i com es presenta al model.

Tres responsabilitats deterministes i sense IA:

* **Seleccio.** Nomes documents amb text de veritat. Una extraccio pot ser
  `is_current` i tot i aixi no servir: un OLE2 o un ZIP donen
  `skipped_unsupported`, i un PDF escanejat dona 0 caracters. Enviar-los al
  model seria pagar tokens per no res i convidar-lo a inventar.
* **Chunking.** Per pagines senceres, mai partides, amb un solapament petit
  perque un requisit que travessa un salt de pagina no es perdi. Els limits
  viuen a `config`.
* **Fingerprint.** Identifica exactament quins textos s'han analitzat, per
  poder reutilitzar un Run enlloc de tornar a gastar tokens.

Res d'aixo depen del proveidor ni de la base de dades: son funcions pures sobre
estructures que el servei d'analisi els passa.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.core.config import settings
from app.models.enums import TenderDocumentType

#: Ordre de prioritat: primer el que defineix les regles del joc.
#: Un document fora d'aquesta llista no s'analitza automaticament, pero es pot
#: demanar a ma des de la interficie.
DEFAULT_DOCUMENT_TYPES: tuple[TenderDocumentType, ...] = (
    TenderDocumentType.ADMINISTRATIVE_TERMS,  # PCAP
    TenderDocumentType.TECHNICAL_TERMS,       # PPT
    TenderDocumentType.ADDITIONAL,            # annexos i correccions amb text
)

_PRIORITY = {document_type: index for index, document_type in enumerate(DEFAULT_DOCUMENT_TYPES)}


@dataclass(frozen=True, slots=True)
class PageRef:
    """Una pagina analitzable, amb tot el que cal per citar-la despres."""

    page_id: str
    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class AnalyzableDocument:
    """Un document amb text utilitzable, ja resolt a la revisio concreta."""

    tender_document_id: str
    document_name: str
    document_type: str
    extraction_id: str
    revision_number: int
    parser_version: str
    content_sha256: str
    pages: tuple[PageRef, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass(frozen=True, slots=True)
class SkippedDocument:
    """Un document que NO s'analitza, i per que. No s'inventa el seu contingut."""

    tender_document_id: str
    document_name: str
    document_type: str
    reason: str


@dataclass(slots=True)
class Chunk:
    """Un bloc de pagines d'un sol document."""

    index: int
    document: AnalyzableDocument
    pages: tuple[PageRef, ...]
    #: Pagines que venen del solapament amb el chunk anterior.
    overlap_page_numbers: frozenset[int] = field(default_factory=frozenset)


def document_sort_key(document: AnalyzableDocument) -> tuple[int, str]:
    """Ordre estable: primer PCAP, despres PPT, despres annexos."""
    try:
        priority = _PRIORITY[TenderDocumentType(document.document_type)]
    except (ValueError, KeyError):
        priority = len(_PRIORITY)
    return (priority, document.tender_document_id)


def compute_input_fingerprint(documents: list[AnalyzableDocument]) -> str:
    """SHA-256 determinista de les extraccions analitzades.

    Es fa servir per decidir si es pot reutilitzar un Run anterior. Inclou la
    revisio i el hash del contingut: si el document es reextreu, el fingerprint
    canvia i l'analisi s'ha de refer.

    S'ordena abans d'agregar perque l'ordre de la consulta no ha d'afectar el
    resultat.
    """
    parts = sorted(
        f"{d.extraction_id}|{d.revision_number}|{d.content_sha256}|{d.parser_version}"
        for d in documents
    )
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_chunks(
    documents: list[AnalyzableDocument],
    *,
    pages_per_chunk: int | None = None,
    overlap_pages: int | None = None,
) -> list[Chunk]:
    """Talla els documents en blocs de pagines senceres.

    Un chunk mai barreja documents: aixi el model no pot confondre de quin plec
    ve una cita. El solapament es de pagines completes al final del bloc
    anterior.
    """
    size = pages_per_chunk or settings.REQUIREMENTS_CHUNK_PAGES
    overlap = settings.REQUIREMENTS_CHUNK_OVERLAP_PAGES if overlap_pages is None else overlap_pages
    if size < 1:
        raise ValueError("Un chunk necessita com a minim una pagina.")
    # Un solapament igual o mes gran que el chunk no avancaria mai.
    overlap = max(0, min(overlap, size - 1))

    chunks: list[Chunk] = []
    index = 0
    for document in sorted(documents, key=document_sort_key):
        pages = document.pages
        start = 0
        previous_end = 0
        while start < len(pages):
            window = pages[start : start + size]
            overlapping = frozenset(
                page.page_number for page in window if start and page.page_number <= previous_end
            )
            chunks.append(
                Chunk(
                    index=index,
                    document=document,
                    pages=tuple(window),
                    overlap_page_numbers=overlapping,
                )
            )
            index += 1
            previous_end = window[-1].page_number
            if start + size >= len(pages):
                break
            start += size - overlap
    return chunks


def render_chunk(chunk: Chunk) -> str:
    """Format que veu el model.

    Els identificadors van als atributs perque el model els pugui copiar
    literalment a l'evidencia; el backend els revalidara igualment.
    """
    lines = [
        "<DOCUMENT",
        f'  document_id="{chunk.document.tender_document_id}"',
        f'  extraction_id="{chunk.document.extraction_id}"',
        f'  document_type="{chunk.document.document_type}"',
        f'  document_name="{_escape(chunk.document.document_name)}"',
        ">",
    ]
    for page in chunk.pages:
        lines.append(f'<PAGE number="{page.page_number}">')
        lines.append(page.text)
        lines.append("</PAGE>")
    lines.append("</DOCUMENT>")
    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace('"', "'").replace("<", "(").replace(">", ")")
