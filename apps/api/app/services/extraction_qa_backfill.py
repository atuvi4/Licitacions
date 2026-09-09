"""Avalua la QA de les extraccions que encara no l'han passada.

    python -m app.services.extraction_qa_backfill            # nomes les pendents
    python -m app.services.extraction_qa_backfill --force    # recalcula-les totes
    python -m app.services.extraction_qa_backfill --dry-run  # sense escriure res

Les extraccions noves calculen la seva QA en el moment de parsejar. Aquesta
ordre es per a les que ja existien abans que la QA existis: sense ella
quedarien per sempre amb `qa_status = NULL`, i un `NULL` no es "cap problema",
es "ningu ho ha mirat". La diferencia importa perque un run que llegeix una font
sense avaluar queda `unknown`, i d'un `unknown` no se'n pot concloure que el
model s'hagi equivocat.

Per que NO va dins d'una migracio: Alembic ha de moure esquema, no processar
text. Un `upgrade` que recorregues milers de pagines seria lent, no es podria
repetir sense re-executar la migracio i no es podria observar mentre corre. A
mes, aixo es idempotent i re-executable per disseny, cosa que una migracio no
es.

Es idempotent en els dos sentits: per defecte no toca el que ja te QA, i amb
`--force` esborra els issues d'aquella extraccio abans de tornar-los a escriure,
de manera que executar-la dues vegades no en duplica cap.

Aixo NO fa OCR ni crida cap model. Nomes llegeix el text que ja hi ha.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tender_documents import (
    TenderDocumentExtraction,
    TenderDocumentPage,
    TenderExtractionIssue,
)
from app.services.extraction_qa import PageInput, QaStatus, analyse


@dataclass
class BackfillReport:
    processed: int = 0
    #: Ja tenien QA i no s'han tocat.
    skipped: int = 0
    #: Sense cap pagina: no hi ha res a avaluar.
    without_pages: int = 0
    by_status: dict[str, int] = field(default_factory=dict)

    def count(self, status: QaStatus) -> None:
        self.by_status[status.value] = self.by_status.get(status.value, 0) + 1

    def as_text(self) -> str:
        counts = " · ".join(
            f"{status}={self.by_status.get(status, 0)}"
            for status in ("good", "warning", "poor")
        )
        return (
            f"processades={self.processed} · omeses={self.skipped} · "
            f"sense pagines={self.without_pages}\n  {counts}"
        )


async def backfill(
    session: AsyncSession, *, force: bool = False, dry_run: bool = False
) -> BackfillReport:
    """Calcula la QA de cada extraccio pendent. Per revisio, no per document."""
    report = BackfillReport()

    query = sa.select(TenderDocumentExtraction).order_by(TenderDocumentExtraction.created_at)
    if not force:
        query = query.where(TenderDocumentExtraction.qa_status.is_(None))
    extractions = (await session.scalars(query)).all()

    for extraction in extractions:
        if extraction.qa_status is not None and not force:
            report.skipped += 1
            continue

        pages = (
            await session.scalars(
                sa.select(TenderDocumentPage)
                .where(TenderDocumentPage.extraction_id == extraction.id)
                .order_by(TenderDocumentPage.page_number)
            )
        ).all()
        if not pages:
            # Un OLE2 o un ZIP no tenen pagines. No es un problema de qualitat:
            # simplement no hi ha text sobre que dir res, i deixar-ho a `NULL`
            # es exactament el que volem que digui.
            report.without_pages += 1
            continue

        qa = analyse(
            [
                PageInput(
                    page_number=page.page_number,
                    text=page.text,
                    failed=str(page.status) == "failed",
                )
                for page in pages
            ],
            extraction_quality=extraction.quality,
        )
        report.processed += 1
        report.count(qa.status)
        if dry_run:
            continue

        # Amb `--force` es reescriu: esborrar abans es el que fa que tornar-ho
        # a executar no dupliqui cap issue.
        await session.execute(
            sa.delete(TenderExtractionIssue).where(
                TenderExtractionIssue.extraction_id == extraction.id
            )
        )
        extraction.qa_status = qa.status.value
        extraction.qa_version = qa.qa_version
        for issue in qa.issues:
            session.add(
                TenderExtractionIssue(
                    extraction_id=extraction.id,
                    page_number=issue.page_number,
                    code=issue.code.value,
                    detail=issue.detail or None,
                )
            )

    if not dry_run:
        await session.commit()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.extraction_qa_backfill", description=__doc__
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="recalcula tambe les extraccions que ja tenen QA",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="informa del que faria sense escriure res"
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        report = await backfill(session, force=args.force, dry_run=args.dry_run)
    print(("[dry-run] " if args.dry_run else "") + report.as_text())
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
