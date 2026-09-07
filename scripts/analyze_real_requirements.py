"""Analitza els plecs ja processats d'unes quantes licitacions reals.

Deliberadament acotat: NO analitza centenars d'expedients. Amb 2-3 en tenim prou
per comprovar que la cadena funciona sobre PDF de veritat.

    python scripts/analyze_real_requirements.py --limit 2
    python scripts/analyze_real_requirements.py --limit 2 --provider anthropic

Amb `--provider fake` (per defecte) NO es mesura la qualitat del model: les
"respostes" es construeixen a partir de frases REALS de les pagines reals, i el
que es verifica es la canonada sencera —seleccio, chunking, validacio de cites,
offsets, deduplicacio i persistencia— sobre documents autentics.

Amb `--provider anthropic` cal `ANTHROPIC_API_KEY`. Sense clau, l'script ho diu
i no inventa cap resultat.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

import sqlalchemy as sa  # noqa: E402

from app.ai.provider import get_provider  # noqa: E402
from app.ai.providers import fake  # noqa: E402
from app.db.session import SessionFactory  # noqa: E402
from app.models.requirements import (  # noqa: E402
    AwardCriterion,
    Requirement,
    RequirementEvidence,
)
from app.models.tenders import Tender  # noqa: E402
from app.services.requirement_analysis import (  # noqa: E402
    analyze_tender_requirements,
    collect_analyzable_documents,
)
from app.services.requirement_chunking import build_chunks  # noqa: E402

HARD_LIMIT = 5

# Frases que a un plec real solen contenir una exigencia quantificada.
_MONEY = re.compile(r"[^.]*\b\d[\d.,]*\s*(?:euros|EUR|€)[^.]*\.", re.IGNORECASE)
_POINTS = re.compile(r"[^.]*\b\d+\s*puntos[^.]*\.", re.IGNORECASE)


async def tenders_with_text(session, limit: int) -> list[Tender]:
    """Licitacions amb pagines de text extret."""
    rows = (
        await session.execute(
            sa.text(
                """
                SELECT DISTINCT t.id
                FROM tenders t
                JOIN tender_documents td ON td.tender_id = t.id
                JOIN tender_document_files f
                  ON f.tender_document_id = td.id AND f.is_current
                JOIN tender_document_extractions e
                  ON e.content_id = f.content_id AND e.is_current
                     AND e.status = 'extracted' AND e.page_count > 0
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
    ).scalars().all()
    return [await session.get(Tender, row) for row in rows]


def seed_fake_from_real_pages(chunks) -> int:
    """Encua respostes fetes amb frases LITERALS de les pagines reals.

    Aixi la validacio de cites s'exerceix de veritat: si el localitzador tingues
    un bug, aquestes cites —que si que son al text— es rebutjarien.
    """
    seeded = 0
    for chunk in chunks:
        requirements, criteria = [], []
        for page in chunk.pages:
            money = _MONEY.search(page.text)
            if money and len(requirements) < 2:
                quote = " ".join(money.group(0).split())[:300]
                requirements.append(
                    {
                        "category": "economic_solvency",
                        "obligation": "unknown",
                        "title": quote[:120],
                        "confidence": "medium",
                        "evidence": [
                            {
                                "extraction_id": chunk.document.extraction_id,
                                "page_number": page.page_number,
                                "quote": quote,
                            }
                        ],
                    }
                )
            points = _POINTS.search(page.text)
            if points and len(criteria) < 2:
                quote = " ".join(points.group(0).split())[:300]
                criteria.append(
                    {
                        "name": quote[:120],
                        "evaluation_type": "unknown",
                        "confidence": "medium",
                        "evidence": [
                            {
                                "extraction_id": chunk.document.extraction_id,
                                "page_number": page.page_number,
                                "quote": quote,
                            }
                        ],
                    }
                )
        fake.queue_response({"requirements": requirements, "award_criteria": criteria})
        seeded += len(requirements) + len(criteria)
    return seeded


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=2, help="licitacions a analitzar (max 5)")
    parser.add_argument("--provider", default="fake", help="fake | anthropic")
    parser.add_argument("--force", action="store_true", help="ignora la cache")
    args = parser.parse_args()
    limit = min(args.limit, HARD_LIMIT)

    try:
        provider = get_provider(args.provider)
    except Exception as exc:
        print(f"No s'ha pogut construir el proveidor {args.provider!r}: {exc}")
        return 1
    if not provider.is_available():
        print(
            f"El proveidor {args.provider!r} no esta disponible (falta clau o SDK).\n"
            "La validacio amb model real queda pendent de credencial. No s'inventa cap resultat."
        )
        return 1

    print(f"Proveidor: {provider.name} · model: {provider.model}\n")

    async with SessionFactory() as session:
        tenders = await tenders_with_text(session, limit)
        if not tenders:
            print("Cap licitacio amb text extret. Processa'n els plecs primer.")
            return 1

        for tender in tenders:
            label = tender.expediente_number or str(tender.external_id)[:24]
            documents, skipped = await collect_analyzable_documents(session, tender.id)
            if not documents:
                print(f"{label}: cap document analitzable ({len(skipped)} descartats)")
                continue

            chunks = build_chunks(documents)
            if provider.name == "fake":
                fake.reset()
                seed_fake_from_real_pages(chunks)

            outcome = await analyze_tender_requirements(
                session, tender, force=args.force, provider=provider
            )
            run = outcome.run

            print("=" * 76)
            print(f"Expedient : {label}")
            print(f"Reutilitzat: {outcome.reused}")
            print(f"Estat     : {run.status} · vigent={run.is_current}")
            print(f"Documents : {run.documents_analyzed} · pagines {run.pages_analyzed}")
            print(f"Chunks    : {run.chunks_completed}/{run.chunks_total} (fallits {run.chunks_failed})")
            print(f"Requisits : {run.requirements_extracted} · rebutjats {run.requirements_rejected}")
            print(f"Criteris  : {run.award_criteria_extracted}")
            print(f"Cites rebutjades: {run.evidence_rejected}")
            print(f"Tokens    : in={run.input_tokens} out={run.output_tokens} · {run.duration_ms} ms")
            print(f"Versions  : {run.extractor_version} · {run.prompt_version} · {run.schema_version}")
            if skipped:
                for item in skipped:
                    print(f"  no analitzat: {item.document_name} -> {item.reason}")

            rows = (
                await session.execute(
                    sa.select(Requirement, RequirementEvidence)
                    .join(RequirementEvidence, RequirementEvidence.requirement_id == Requirement.id)
                    .where(Requirement.run_id == run.id)
                    .limit(4)
                )
            ).all()
            for requirement, evidence in rows:
                print(f"\n  [{requirement.category}] {requirement.title[:70]}")
                print(f"    evidencia: p.{evidence.page_number} chars {evidence.char_start}-{evidence.char_end}")
                print(f"    cita     : {evidence.quote[:90]!r}")

            criteria = (
                await session.scalars(
                    sa.select(AwardCriterion).where(AwardCriterion.run_id == run.id).limit(3)
                )
            ).all()
            for criterion in criteria:
                print(f"\n  [criteri] {criterion.name[:70]} · formula={criterion.formula}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
