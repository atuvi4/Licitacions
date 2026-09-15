"""Reconstrueix quins chunks va analitzar un run ANTERIOR a `chunk_coverage`.

    python scripts/reconstruct_run_coverage.py --run-id bc2abade-18ac-4f6b-b5a7-b626e8634669

Per al run v2 del smoke 422-2026, que va completar 5 de 8 chunks sense deixar
dit quins. Sense saber-ho no es pot dir si `eco-1` (PCAP p.9) va ser un error
del model o una pagina que el model no va veure mai.

Com ho dedueix, i fins on arriba:

1. **Refa el pla de chunks** amb la mateixa canonada (`analysis_input`,
   `build_chunks`) i comprova que es el del run: mateix
   `input_fingerprint` i mateix `chunks_total`. Si no coincideix, s'atura: un pla
   diferent no diu res d'aquell run.
2. **Un chunk que ha deixat rastre es un chunk completat.** El rastre es
   `source_chunk_index` d'un requisit o criteri, o una cita en una pagina que
   nomes viu en aquell chunk. Un chunk fallit no persisteix res.
3. **Un chunk sense rastre NO es necessariament fallit**: podia haver-se
   completat sense trobar res nou. Per aixo la inferencia nomes es **exacta**
   si el nombre de chunks amb rastre coincideix amb `chunks_completed`. Si no,
   es diu, i els chunks sense rastre queden com a dubtosos.

Aquest script NO crida cap model, NO construeix cap proveidor i NO escriu res:
la transaccio es `READ ONLY`. El resultat no es desa a `chunk_coverage` a
proposit: una deduccio no es una observacio, i aquella columna nomes ha de
contenir el que el run va veure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.evaluation.benchmark import BenchmarkConfigError, resolve_benchmark_database
from app.evaluation.corpus import resolve_tender
from app.evaluation.dataset_v2 import load_gold
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.tenders import Tender
from app.services.requirement_analysis import analysis_input
from app.services.requirement_chunking import build_chunks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reconstruct_run_coverage")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset", default="gold-v2.1")
    parser.add_argument("--case", default=None, help="case_id del gold (defecte: el del tender)")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--json", dest="json_out", default=None, help="desa el resultat en JSON")
    return parser


async def _trace(
    session: AsyncSession, run_id: uuid.UUID
) -> tuple[set[int], set[tuple[str, int]]]:
    """Indexs de chunk declarats i (document, pagina) citats pel que va persistir el run."""
    indices: set[int] = set()
    for model in (Requirement, AwardCriterion):
        rows = await session.scalars(
            sa.select(model.source_chunk_index).where(
                model.run_id == run_id, model.source_chunk_index.is_not(None)
            )
        )
        indices.update(int(i) for i in rows)

    cited: set[tuple[str, int]] = set()
    for evidence, parent, fk in (
        (RequirementEvidence, Requirement, RequirementEvidence.requirement_id),
        (AwardCriterionEvidence, AwardCriterion, AwardCriterionEvidence.award_criterion_id),
    ):
        rows = await session.execute(
            sa.select(evidence.tender_document_id, evidence.page_number)
            .join(parent, parent.id == fk)
            .where(parent.run_id == run_id)
        )
        cited.update((str(doc), int(page)) for doc, page in rows if doc is not None)
    return indices, cited


async def main() -> int:
    args = build_parser().parse_args()
    try:
        database = resolve_benchmark_database(
            args.database_url or settings.BENCHMARK_DATABASE_URL or None
        )
    except BenchmarkConfigError as exc:
        print(f"KO  DSN del benchmark: {exc}")
        return 1

    engine = create_async_engine(database)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(sa.text("SET TRANSACTION READ ONLY"))
            run = await session.get(RequirementExtractionRun, uuid.UUID(args.run_id))
            if run is None:
                print(f"KO  El run {args.run_id} no existeix a {database.database!r}.")
                return 1
            tender = await session.get(Tender, run.tender_id)
            print(
                f"Run {run.id} · perfil {run.prompt_version} · estat {run.status} · "
                f"chunks {run.chunks_completed}/{run.chunks_total} (fallits {run.chunks_failed}) "
                f"· error {run.error_code!r}"
            )
            if run.chunk_coverage:
                print("NOTA: aquest run ja porta chunk_coverage; no cal reconstruir res.")

            current = await analysis_input(session, run.tender_id)
            documents = current.documents
            fingerprint = current.fingerprint
            chunks = build_chunks(documents)
            if fingerprint != run.input_fingerprint or len(chunks) != run.chunks_total:
                same = "igual" if fingerprint == run.input_fingerprint else "DIFERENT"
                print(
                    f"KO  El pla de chunks d'avui no es el del run (fingerprint {same}, "
                    f"{len(chunks)} chunks vs {run.chunks_total}). No es pot reconstruir."
                )
                return 1

            indices, cited = await _trace(session, run.id)

            page_owners: dict[tuple[str, int], list[int]] = {}
            for chunk in chunks:
                for page in chunk.pages:
                    key = (chunk.document.tender_document_id, page.page_number)
                    page_owners.setdefault(key, []).append(chunk.index)

            rows: list[dict[str, Any]] = []
            for chunk in chunks:
                doc_id = chunk.document.tender_document_id
                exclusive_cites = sorted(
                    page.page_number
                    for page in chunk.pages
                    if (doc_id, page.page_number) in cited
                    and page_owners[(doc_id, page.page_number)] == [chunk.index]
                )
                rows.append(
                    {
                        "index": chunk.index,
                        "document_name": chunk.document.document_name,
                        "pages": [p.page_number for p in chunk.pages],
                        "declared_by_items": chunk.index in indices,
                        "exclusive_cited_pages": exclusive_cites,
                        "traced": chunk.index in indices or bool(exclusive_cites),
                    }
                )

            traced = sum(1 for r in rows if r["traced"])
            exact = traced == run.chunks_completed
            for r in rows:
                if r["traced"]:
                    r["inferred_status"] = "completed"
                elif exact:
                    r["inferred_status"] = "failed"
                else:
                    r["inferred_status"] = "unknown"

            print(
                f"\nChunks amb rastre: {traced} · chunks_completed al run: {run.chunks_completed} "
                f"→ inferencia {'EXACTA' if exact else 'NO EXACTA (queden chunks dubtosos)'}\n"
            )
            for r in rows:
                pages = f"p.{r['pages'][0]}-{r['pages'][-1]}"
                print(
                    f"  #{r['index']} {r['document_name'][:45]:45} {pages:9} "
                    f"{r['inferred_status']:9} "
                    f"(source_chunk_index={'si' if r['declared_by_items'] else 'no'}, "
                    f"cites exclusives={r['exclusive_cited_pages'] or '—'})"
                )

            gold_rows: list[dict[str, Any]] = []
            dataset = load_gold(args.dataset)
            case = None
            if args.case:
                case = next((c for c in dataset.cases if c.case_id == args.case), None)
            else:
                for candidate in dataset.cases:
                    resolved = await resolve_tender(session, candidate)
                    if resolved is not None and resolved.id == run.tender_id:
                        case = candidate
                        break
            if case is not None:
                print(f"\nGold del cas {case.case_id}:")
                by_name = {c.document.document_name: c.document.tender_document_id for c in chunks}
                items = [("requirement", g) for g in case.requirements] + [
                    ("award_criterion", g) for g in case.award_criteria
                ]
                for kind, gold in items:
                    doc_id = by_name.get(gold.document_name)
                    owners = page_owners.get((doc_id, gold.page_number), []) if doc_id else []
                    statuses = [rows[i]["inferred_status"] for i in owners]
                    if not owners:
                        verdict = "pagina fora del pla de chunks"
                    elif "completed" in statuses:
                        verdict = "MESURABLE (algun chunk que la conte es va completar)"
                    elif all(s == "failed" for s in statuses):
                        verdict = "NO MESURABLE (tots els chunks que la contenen van fallar)"
                    else:
                        verdict = "DUBTOS (cap chunk amb rastre la conte)"
                    gold_rows.append(
                        {
                            "gold_id": gold.gold_id,
                            "kind": kind,
                            "document_name": gold.document_name,
                            "page_number": gold.page_number,
                            "chunks": owners,
                            "chunk_statuses": statuses,
                            "verdict": verdict,
                        }
                    )
                    print(
                        f"  {gold.gold_id:10} {gold.document_name[:40]:40} p.{gold.page_number:<4} "
                        f"chunks {owners} {statuses} → {verdict}"
                    )
            else:
                print("\n(No s'ha trobat el cas del gold per a aquest tender.)")

            if args.json_out:
                with open(args.json_out, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "run_id": str(run.id),
                            "tender": tender.expediente_number if tender else None,
                            "chunks_total": run.chunks_total,
                            "chunks_completed": run.chunks_completed,
                            "inference_exact": exact,
                            "chunks": rows,
                            "gold": gold_rows,
                        },
                        handle,
                        ensure_ascii=False,
                        indent=2,
                    )
                print(f"\nDesat a {args.json_out}")
            await session.rollback()
    finally:
        await engine.dispose()

    print("\nNO s'ha cridat cap model ni s'ha escrit res a la base de dades.")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
