"""Treu la plantilla del feed de `Tender.description` per a tot el cataleg (3B-2 §7.1.2).

    python -m app.tenders.description_backfill [--batch-size 500]

La ingesta ja no desa com a descripcio el `summary` fix del feed agregat («Id
licitación…; Órgano de Contratación…; Importe…; Estado…»): `description` queda nul
i el text cru va a `source_metadata.feed_summary`. Aquest CLI fa el mateix amb les
licitacions que ja hi eren. Mateix patro que `search_backfill`:

* Idempotent: nomes toca les files on `description` encara es la plantilla, de
  manera que una segona passada no canvia res ni deixa cap revisio.
* Per lots en ordre d'id (keyset, sense `OFFSET`) i `commit` a cada lot: un tall a
  mig cami deixa fet el que s'ha fet i tornar-lo a llancar continua.
* La deteccio es l'unica que hi ha (`is_feed_summary_template`).
* Les licitacions obertes a ma no es toquen: la seva descripcio l'ha escrit algu.

Cada licitacio corregida deixa una revisio amb trigger `normalization` (origen
`internal` a l'historial): la descripcio canvia a la fotografia, i si no es
registres ara, la propera ingesta la presentaria com un canvi publicat per la font.
Executeu-lo ABANS de la primera sincronitzacio amb el normalitzador nou.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SourceType
from app.models.sources import Source
from app.models.tender_lifecycle import TenderRevision
from app.models.tenders import Tender
from app.services.tender_lifecycle import NORMALIZATION_TRIGGER, record_revision
from app.tenders.search import is_feed_summary_template

BATCH_SIZE = 500


@dataclass(slots=True)
class DescriptionBackfillResult:
    scanned: int = 0
    updated: int = 0


async def backfill_descriptions(
    session: AsyncSession, *, batch_size: int = BATCH_SIZE
) -> DescriptionBackfillResult:
    result = DescriptionBackfillResult()
    last_id: uuid.UUID | None = None
    while True:
        query = (
            sa.select(Tender.id, Tender.description)
            .join(Source, Source.id == Tender.source_id)
            .where(Source.source_type != SourceType.MANUAL)
            .order_by(Tender.id)
            .limit(batch_size)
        )
        if last_id is not None:
            query = query.where(Tender.id > last_id)
        rows = (await session.execute(query)).all()
        if not rows:
            break

        templated = [row.id for row in rows if is_feed_summary_template(row.description)]
        for tender_id in templated:
            tender = await session.get(Tender, tender_id)
            if tender is None or not is_feed_summary_template(tender.description):
                continue
            metadata = dict(tender.source_metadata or {})
            metadata["feed_summary"] = tender.description
            tender.source_metadata = metadata
            tender.description = None
            await session.flush()
            has_history = await session.scalar(
                sa.select(sa.exists().where(TenderRevision.tender_id == tender.id))
            )
            if has_history:
                # Sense historia, la primera revisio la fara la propera ingesta.
                await record_revision(session, tender, trigger=NORMALIZATION_TRIGGER)
            result.updated += 1
        await session.commit()

        result.scanned += len(rows)
        last_id = rows[-1].id
    return result


async def _main(batch_size: int) -> None:
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        result = await backfill_descriptions(session, batch_size=batch_size)
    print(f"description: {result.scanned} licitacions revisades, {result.updated} corregides")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.tenders.description_backfill")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()
    asyncio.run(_main(args.batch_size))


if __name__ == "__main__":
    main()
