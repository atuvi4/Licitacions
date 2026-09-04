"""CLI del pre-fit.

    python -m app.matching --company <uuid>
    python -m app.matching --all-companies
    python -m app.matching --company <uuid> --tender <uuid>

No hi ha planificador: el calcul s'engega a ma o despres d'una ingesta.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

import sqlalchemy as sa

from app.core.logging import configure_logging
from app.db.session import SessionFactory
from app.matching.service import MatchingService
from app.models.enums import MatchStatus
from app.models.identity import Company
from app.models.matching import CompanyTenderMatch


async def _run(args: argparse.Namespace) -> int:
    async with SessionFactory() as session:
        if args.all_companies:
            company_ids = list(
                (await session.execute(sa.select(Company.id))).scalars().all()
            )
        elif args.company:
            company_ids = [uuid.UUID(args.company)]
        else:
            print("Cal indicar --company <uuid> o --all-companies", file=sys.stderr)
            return 2

        service = MatchingService(session)
        for company_id in company_ids:
            summary = await service.recalculate(
                company_id,
                tender_ids=[uuid.UUID(args.tender)] if args.tender else None,
            )
            print(f"\nEmpresa {company_id}")
            print(f"  regles            : {summary.rules_version}")
            print(f"  durada            : {summary.duration_ms} ms")
            print(f"  licitacions       : {summary.tenders_evaluated}")
            print(f"  lots              : {summary.lots_evaluated}")
            print(f"  matches creats    : {summary.created}")
            print(f"  matches actualitzats: {summary.updated}")

            rows = await session.execute(
                sa.select(CompanyTenderMatch.status, sa.func.count())
                .where(CompanyTenderMatch.company_id == company_id)
                .group_by(CompanyTenderMatch.status)
            )
            distribution = {status: count for status, count in rows}
            print("  distribucio:")
            for status in MatchStatus:
                count = distribution.get(status, 0)
                if count:
                    print(f"    {status.value:20s} {count:4d}")

            high = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(CompanyTenderMatch)
                    .where(
                        CompanyTenderMatch.company_id == company_id,
                        CompanyTenderMatch.score >= 80,
                    )
                )
            ).scalar_one()
            print(f"    {'pre-fit >= 80':20s} {high:4d}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.matching", description=__doc__)
    parser.add_argument("--company", help="UUID de l'empresa.")
    parser.add_argument("--all-companies", action="store_true", help="Totes les empreses.")
    parser.add_argument("--tender", help="Limita el calcul a una licitacio concreta.")
    args = parser.parse_args()

    configure_logging()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
