"""Processa uns quants documents reals de PLACSP i n'informa.

Deliberadament acotat: NO baixa els 1.454 documents coneguts. Tria uns quants
tenders amb PDF i en processa un nombre limitat, perque el que volem comprovar
es que la cadena funciona amb PDF de veritat, no fer-nos una copia de PLACSP.

    python scripts/process_real_documents.py --limit 5
    python scripts/process_real_documents.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

import sqlalchemy as sa  # noqa: E402

from app.db.session import SessionFactory  # noqa: E402
from app.models.tender_documents import TenderDocumentExtraction  # noqa: E402
from app.models.tenders import Tender, TenderDocument  # noqa: E402
from app.services.tender_document_processing import process_tender_document  # noqa: E402

# Limit de seguretat absolut: encara que algu passi --limit 500, no.
HARD_LIMIT = 20


async def pick_documents(session, limit: int) -> list[tuple[Tender, TenderDocument]]:
    """Tria documents que semblin PDF, d'expedients diferents.

    Un document per expedient: volem varietat d'organs i de generadors de PDF,
    no cinc annexos del mateix plec.
    """
    rows = (
        await session.execute(
            sa.select(Tender, TenderDocument)
            .join(TenderDocument, TenderDocument.tender_id == Tender.id)
            .where(
                sa.or_(
                    TenderDocument.url.ilike("%.pdf"),
                    TenderDocument.mime_type == "application/pdf",
                )
            )
            .order_by(Tender.publication_date.desc().nullslast())
            .limit(limit * 10)
        )
    ).all()

    picked: list[tuple[Tender, TenderDocument]] = []
    seen: set = set()
    for tender, document in rows:
        if tender.id in seen:
            continue
        seen.add(tender.id)
        picked.append((tender, document))
        if len(picked) >= limit:
            break
    return picked


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5, help="documents a processar (max 20)")
    parser.add_argument("--dry-run", action="store_true", help="nomes llista, no descarrega")
    args = parser.parse_args()
    limit = min(args.limit, HARD_LIMIT)

    async with SessionFactory() as session:
        selection = await pick_documents(session, limit)
        if not selection:
            print("No s'ha trobat cap document PDF a la base de dades.")
            return 1

        print(f"{len(selection)} documents seleccionats.\n")
        if args.dry_run:
            for tender, document in selection:
                print(f"  {tender.expediente_number or tender.external_id} :: {document.name}")
            return 0

        header = (
            f"{'Expedient':<22} {'Document':<28} {'Bytes':>10} {'Pag':>5} "
            f"{'Chars':>9} {'Qualitat':<12} {'ms desc':>8} {'ms parse':>9}"
        )
        print(header)
        print("-" * len(header))

        failures = 0
        ocr_candidates = 0

        for tender, document in selection:
            outcome = await process_tender_document(session, document)
            file = outcome.file
            extraction = None
            if file.content_id:
                extraction = await session.scalar(
                    sa.select(TenderDocumentExtraction).where(
                        TenderDocumentExtraction.content_id == file.content_id,
                        TenderDocumentExtraction.is_current.is_(True),
                    )
                )

            if file.download_status.value != "downloaded":
                failures += 1
                print(
                    f"{(tender.expediente_number or tender.external_id)[:21]:<22} "
                    f"{document.name[:27]:<28} ERROR {file.error_code}"
                )
                continue

            if extraction and extraction.ocr_candidate:
                ocr_candidates += 1

            print(
                f"{(tender.expediente_number or tender.external_id)[:21]:<22} "
                f"{document.name[:27]:<28} "
                f"{file.content.file_size:>10} "
                f"{(extraction.page_count if extraction else 0):>5} "
                f"{(extraction.text_char_count if extraction else 0):>9} "
                f"{(extraction.quality.value if extraction else '-'):<12} "
                f"{(file.download_ms or 0):>8} "
                f"{(extraction.parse_ms if extraction else 0):>9}"
            )
            # El contingut dels plecs no es reprodueix: nomes metriques.
            print(f"{'':22} sha256: {file.content.sha256}")

        print(f"\nFallades: {failures} · Candidats a OCR: {ocr_candidates}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
