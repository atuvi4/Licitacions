"""Quines pagines ha arribat a llegir el model, i quines no.

Existeix per un error de mesura concret. El run del perfil v2 sobre el tender
422-2026 va completar **5 de 8 chunks** (`invalid_structured_output` a tres);
tres blocs del plec no es van analitzar mai. El benchmark ho va comptar com un
requisit que el model "no havia trobat", quan el model no havia vist la pagina.
La xifra deia una cosa sobre el model i en mesurava una altra sobre l'execucio.

El comptador que hi havia (`chunks_completed`/`chunks_total`) diu *quants* en
van caure, no *quins*, i el que necessita el benchmark es la pagina: sense
saber-la no es pot decidir si un requisit del gold era mesurable en aquell run.
`RequirementExtractionRun.pages_analyzed` tampoc no serveix: compta les pagines
que tenien text, no les que van arribar al model.

Per aixo cada run desa una fotografia dels seus chunks amb l'estat de cadascun.
S'hi desa i no s'hi enllaca, igual que `source_quality_issues`: si el document
es reextreu, els chunks d'avui deixarien de ser els d'aquell run.

**Cobertura desconeguda no es cobertura.** Els runs anteriors a aquesta
instrumentacio no tenen la fotografia, i llavors `covers()` torna `None`: ni
cobert ni descobert. Tractar-ho com a cobert tornaria a atribuir al model
pagines que ningu no sap si es van llegir.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.ai.provider import Usage
from app.services.requirement_chunking import Chunk

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

#: Versio de la fotografia. Si algun dia canvia la forma, els runs vells encara
#: es podran llegir (o descartar) sabent amb quina es van escriure.
COVERAGE_SCHEMA = "chunk-coverage-v1"


def build_coverage(
    chunks: Iterable[Chunk],
    *,
    failures: Mapping[int, str],
    pages_per_chunk: int,
    overlap_pages: int,
    usage: Mapping[int, Usage] | None = None,
    diagnostics: Mapping[int, list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """La fotografia dels chunks d'un run, amb l'estat de cadascun.

    `failures` va per index de chunk: el motiu pel qual aquell bloc no s'ha
    pogut fer servir. Un chunk que no hi surt es un chunk analitzat.

    `usage` va tambe per index: el que va costar cada crida, i per que el model
    va parar. Un chunk sense entrada no va arribar a tenir resposta (error de
    xarxa o HTTP), i els seus tokens queden `None`, no zero.

    `diagnostics` porta, per a un `schema_invalid`, on i de quin tipus eren els
    primers errors del contracte. Nomes estructura: cap text del plec.
    """
    usage = usage or {}
    diagnostics = diagnostics or {}
    return {
        "schema": COVERAGE_SCHEMA,
        "pages_per_chunk": pages_per_chunk,
        "overlap_pages": overlap_pages,
        "chunks": [
            {
                "index": chunk.index,
                "tender_document_id": chunk.document.tender_document_id,
                "document_name": chunk.document.document_name,
                "pages": [page.page_number for page in chunk.pages],
                "status": STATUS_FAILED if chunk.index in failures else STATUS_COMPLETED,
                "error": failures.get(chunk.index),
                "error_detail": diagnostics.get(chunk.index),
                "input_tokens": _usage_field(usage, chunk.index, "input_tokens"),
                "output_tokens": _usage_field(usage, chunk.index, "output_tokens"),
                "stop_reason": _usage_field(usage, chunk.index, "stop_reason"),
                "request_id": _usage_field(usage, chunk.index, "request_id"),
            }
            for chunk in chunks
        ],
    }


def _usage_field(usage: Mapping[int, Usage], index: int, name: str) -> Any:
    item = usage.get(index)
    return getattr(item, name, None) if item is not None else None


def analyzed_pages(coverage: Mapping[str, Any] | None) -> dict[str, set[int]] | None:
    """Pagines que van arribar al model, per nom de document.

    Torna `None` si el run no porta la fotografia: es desconegut, no buit.

    Una pagina del solapament viu en dos chunks. Compta com a analitzada si
    **algun** dels dos va anar be: el model la va llegir.
    """
    if not coverage:
        return None
    chunks = coverage.get("chunks")
    if not isinstance(chunks, list):
        return None
    pages: dict[str, set[int]] = {}
    for chunk in chunks:
        if chunk.get("status") != STATUS_COMPLETED:
            continue
        name = str(chunk.get("document_name") or "")
        pages.setdefault(name, set()).update(int(p) for p in chunk.get("pages") or [])
    # Un document del qual tots els chunks han fallat ha de sortir amb un
    # conjunt buit, no absent: "analitzat, zero pagines" i "no hi era" es
    # distingeixen.
    for chunk in chunks:
        pages.setdefault(str(chunk.get("document_name") or ""), set())
    return pages


def covers(coverage: Mapping[str, Any] | None, document_name: str, page: int) -> bool | None:
    """Va llegir el model aquesta pagina d'aquest document?

    `None` = no se sap (run sense fotografia, o document que no surt al pla de
    chunks d'aquell run).
    """
    pages = analyzed_pages(coverage)
    if pages is None:
        return None
    if document_name not in pages:
        return None
    return page in pages[document_name]


def coverage_summary(coverage: Mapping[str, Any] | None) -> dict[str, Any]:
    """Resum llegible per a l'informe: quants chunks i quantes pagines."""
    if not coverage or not isinstance(coverage.get("chunks"), list):
        return {"known": False}
    chunks = coverage["chunks"]
    analyzed = [c for c in chunks if c.get("status") == STATUS_COMPLETED]
    failed = [c for c in chunks if c.get("status") != STATUS_COMPLETED]
    pages = analyzed_pages(coverage) or {}
    all_pages = {
        (str(c.get("document_name")), int(p)) for c in chunks for p in (c.get("pages") or [])
    }
    covered = {(name, page) for name, nums in pages.items() for page in nums}
    return {
        "known": True,
        "chunks_total": len(chunks),
        "chunks_analyzed": len(analyzed),
        "chunks_failed": len(failed),
        "pages_total": len(all_pages),
        "pages_analyzed": len(covered),
        "pages_missing": sorted(
            f"{name} p.{page}" for name, page in sorted(all_pages - covered)
        ),
        "errors": sorted({str(c.get("error")) for c in failed if c.get("error")}),
        # Tokens pagats per chunks que no han aportat res. Nomes els runs amb
        # l'us per chunk ho saben; els anteriors ho perdien pel cami.
        "failed_input_tokens": sum(c.get("input_tokens") or 0 for c in failed),
        "failed_output_tokens": sum(c.get("output_tokens") or 0 for c in failed),
        "failed_stop_reasons": sorted(
            {str(c["stop_reason"]) for c in failed if c.get("stop_reason")}
        ),
    }
