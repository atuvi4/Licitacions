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

from collections.abc import Collection, Iterable, Mapping
from typing import Any

from app.ai.provider import Usage
from app.services.requirement_chunking import Chunk

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

#: Versio de la fotografia. Si algun dia canvia la forma, els runs vells encara
#: es podran llegir (o descartar) sabent amb quina es van escriure.
COVERAGE_SCHEMA = "chunk-coverage-v1"


#: Codi d'un chunk que l'operador no va seleccionar. Mateix valor que
#: `requirement_analysis.CHUNK_NOT_SELECTED` (aquell modul importa aquest, no a
#: l'inreves).
NOT_SELECTED = "not_selected"


def build_coverage(
    chunks: Iterable[Chunk],
    *,
    failures: Mapping[int, str],
    pages_per_chunk: int,
    overlap_pages: int,
    usage: Mapping[int, Usage] | None = None,
    diagnostics: Mapping[int, list[dict[str, str]]] | None = None,
    normalisations: Mapping[int, list[str]] | None = None,
    provider_errors: Mapping[int, dict[str, Any]] | None = None,
    rejections: Mapping[int, list[dict[str, Any]]] | None = None,
    reused: Mapping[int, Mapping[str, Any]] | None = None,
    reused_from_run_id: str | None = None,
    not_sent_code: str = "not_sent",
    not_selected_code: str | None = None,
    unsent_codes: Collection[str] = (),
) -> dict[str, Any]:
    """La fotografia dels chunks d'un run, amb l'estat de cadascun.

    `failures` va per index de chunk: el motiu pel qual aquell bloc no s'ha
    pogut fer servir. Un chunk que no hi surt es un chunk analitzat.

    `usage` va tambe per index: el que va costar cada crida, i per que el model
    va parar. Un chunk sense entrada no va arribar a tenir resposta (error de
    xarxa o HTTP), i els seus tokens queden `None`, no zero.

    `diagnostics` porta, per a un `schema_invalid`, on i de quin tipus eren els
    primers errors del contracte. Nomes estructura: cap text del plec.

    `normalisations` diu quines normalitzacions estrictes (`path:regla`) va
    necessitar la sortida abans de validar, i `provider_errors` el diagnostic
    sanititzat d'un error HTTP (estat, tipus, missatge retallat, request_id).

    `rejections` diu, per a un chunk que ha respost, quins elements o cites es
    van descartar i per que: `{item, index, stage, reason}`, mai el text.

    `origin` diu d'on surt el resultat de cada chunk: `sent` (s'ha enviat en
    aquest run), `reused` (copiat del run parcial `reused_from_run_id`, sense
    cap crida; els tokens queden `None` perque ja es van comptar alla) o
    `not_sent` (un error fatal va aturar el run abans, no estava seleccionat, o
    un dels `unsent_codes`: encara pendent en un run en curs o aturat per un cap
    de despesa).
    """
    not_sent = {code for code in (not_sent_code, not_selected_code, *unsent_codes) if code}
    usage = usage or {}
    diagnostics = diagnostics or {}
    normalisations = normalisations or {}
    provider_errors = provider_errors or {}
    rejections = rejections or {}
    reused = reused or {}
    return {
        "schema": COVERAGE_SCHEMA,
        "pages_per_chunk": pages_per_chunk,
        "overlap_pages": overlap_pages,
        "chunks": [
            _reused_entry(chunk, reused[chunk.index], reused_from_run_id)
            if chunk.index in reused
            else {
                "index": chunk.index,
                "tender_document_id": chunk.document.tender_document_id,
                "document_name": chunk.document.document_name,
                "pages": [page.page_number for page in chunk.pages],
                "status": STATUS_FAILED if chunk.index in failures else STATUS_COMPLETED,
                "error": failures.get(chunk.index),
                "error_detail": diagnostics.get(chunk.index),
                "normalised": normalisations.get(chunk.index),
                "provider_error": provider_errors.get(chunk.index),
                "rejections": rejections.get(chunk.index),
                "input_tokens": _usage_field(usage, chunk.index, "input_tokens"),
                "output_tokens": _usage_field(usage, chunk.index, "output_tokens"),
                "stop_reason": _usage_field(usage, chunk.index, "stop_reason"),
                "request_id": _usage_field(usage, chunk.index, "request_id"),
                "origin": "not_sent" if failures.get(chunk.index) in not_sent else "sent",
            }
            for chunk in chunks
        ],
    }


def _reused_entry(
    chunk: Chunk, source: Mapping[str, Any], reused_from_run_id: str | None
) -> dict[str, Any]:
    return {
        "index": chunk.index,
        "tender_document_id": chunk.document.tender_document_id,
        "document_name": chunk.document.document_name,
        "pages": [page.page_number for page in chunk.pages],
        "status": STATUS_COMPLETED,
        "error": None,
        "error_detail": None,
        "normalised": source.get("normalised"),
        "provider_error": None,
        # Els rebutjos del chunk original, per no perdre la tracabilitat.
        "rejections": source.get("rejections"),
        "input_tokens": None,
        "output_tokens": None,
        "stop_reason": source.get("stop_reason"),
        "request_id": None,
        "origin": "reused",
        "reused_from_run_id": reused_from_run_id,
        "reused_request_id": source.get("request_id"),
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
    # Un chunk no seleccionat (canari d'operador) no es un error: es compta a part.
    not_selected = [c for c in chunks if c.get("error") == NOT_SELECTED]
    failed = [
        c for c in chunks if c.get("status") != STATUS_COMPLETED and c.get("error") != NOT_SELECTED
    ]
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
        "chunks_not_selected": len(not_selected),
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
