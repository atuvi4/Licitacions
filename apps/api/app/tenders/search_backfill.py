"""Recalcula `Tender.search_text` per a tot el cataleg (spec 3B-1 §7.2).

    python -m app.tenders.search_backfill [--batch-size 500]

Idempotent: nomes escriu les files on el valor calculat difereix del desat, de
manera que una segona passada no toca res. Es recorre per lots de 500 en ordre
d'id (keyset, sense `OFFSET`) i es fa `commit` a cada lot: un tall a mig cami
deixa fet el que s'ha fet i tornar-lo a llançar continua sense problemes.

Es la mateixa funcio que fan servir la ingesta i l'alta/edicio manual
(`build_search_text`): organisme de la fila `Buyer` i titols dels lots vigents.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenders import Buyer, Tender
from app.tenders.search import build_search_text, live_lot_titles

BATCH_SIZE = 500


@dataclass(slots=True)
class BackfillResult:
    scanned: int = 0
    updated: int = 0


async def backfill_search_text(
    session: AsyncSession, *, batch_size: int = BATCH_SIZE
) -> BackfillResult:
    result = BackfillResult()
    last_id: uuid.UUID | None = None
    while True:
        query = (
            sa.select(
                Tender.id,
                Tender.title,
                Tender.expediente_number,
                Tender.description,
                Tender.search_text,
                Buyer.name,
            )
            .outerjoin(Buyer, Buyer.id == Tender.buyer_id)
            .order_by(Tender.id)
            .limit(batch_size)
        )
        if last_id is not None:
            query = query.where(Tender.id > last_id)
        rows = (await session.execute(query)).all()
        if not rows:
            break

        lot_titles = await live_lot_titles(session, [row.id for row in rows])
        changes = []
        for row in rows:
            wanted = build_search_text(row, row.name, lot_titles.get(row.id, []))
            if wanted != row.search_text:
                changes.append({"id": row.id, "search_text": wanted})
        if changes:
            # UPDATE massiu per clau primaria (ORM 2.0): una sentencia per lot.
            await session.execute(sa.update(Tender), changes)
        await session.commit()

        result.scanned += len(rows)
        result.updated += len(changes)
        last_id = rows[-1].id
    return result


async def _main(batch_size: int) -> None:
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        result = await backfill_search_text(session, batch_size=batch_size)
    print(f"search_text: {result.scanned} licitacions revisades, {result.updated} actualitzades")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.tenders.search_backfill")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()
    asyncio.run(_main(args.batch_size))


if __name__ == "__main__":
    main()
