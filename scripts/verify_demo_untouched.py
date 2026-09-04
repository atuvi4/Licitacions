"""Comprova que una execucio d'smoke test no deixa cap rastre al tenant demo.

Fa una radiografia del tenant demo, executa l'smoke test complet, i torna a
radiografiar-lo. Si ha canviat una sola fila, un sol document o un sol objecte
de l'storage, falla i diu exactament que.

    python scripts/verify_demo_untouched.py --base-url http://localhost:8000/api/v1

L'smoke test ja fa aquesta comprovacio pel seu compte; aixo la fa des de fora,
amb un proces independent, perque no depengui de que el propi test la faci be.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.models.identity import Company  # noqa: E402
from app.models.tenders import Tender  # noqa: E402
from app.models.sources import SourceRecord  # noqa: E402
from app.tenant_ops import TenantSnapshot, list_ephemeral, session_scope, snapshot  # noqa: E402

DEMO_COMPANY_NAME = "Empresa Demo Telecom SL"


async def take_snapshot() -> tuple[TenantSnapshot, int, int]:
    async with session_scope() as session:
        company_id = (
            await session.execute(
                sa.select(Company.id).where(Company.legal_name == DEMO_COMPANY_NAME)
            )
        ).scalar_one_or_none()
        if company_id is None:
            raise SystemExit(f"No hi ha cap tenant demo ({DEMO_COMPANY_NAME}). Executa el seed.")
        data = await snapshot(session, company_id)
        tenders = (
            await session.execute(sa.select(sa.func.count()).select_from(Tender))
        ).scalar_one()
        records = (
            await session.execute(sa.select(sa.func.count()).select_from(SourceRecord))
        ).scalar_one()
        return data, tenders, records


async def pending_ephemeral() -> list[str]:
    async with session_scope() as session:
        return [f"{company_id} {name}" for company_id, name in await list_ephemeral(session)]


def render(data: TenantSnapshot, tenders: int, records: int) -> None:
    print(f"  empresa           : {data.legal_name}")
    print(f"  nom comercial     : {data.trade_name}")
    print(f"  files de tenant   : {data.total_rows}")
    for table, count in sorted(data.rows.items()):
        if count:
            print(f"      {table:32s} {count}")
    print(f"  documents         : {len(data.document_hashes)}")
    print(f"  objectes storage  : {len(data.storage_keys)}")
    print(f"  publiques (intactes esperades): {tenders} licitacions, {records} revisions")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000/api/v1")
    args = parser.parse_args()

    print("=" * 72)
    print("ABANS DE L'SMOKE TEST")
    print("=" * 72)
    before, tenders_before, records_before = asyncio.run(take_snapshot())
    render(before, tenders_before, records_before)

    stragglers = asyncio.run(pending_ephemeral())
    if stragglers:
        print(f"\n  ATENCIO: ja hi ha tenants efimers pendents: {stragglers}")

    print("\n" + "=" * 72)
    print("EXECUTANT L'SMOKE TEST")
    print("=" * 72)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke_test.py"), "--base-url", args.base_url],
        cwd=ROOT,
    )

    print("\n" + "=" * 72)
    print("DESPRES DE L'SMOKE TEST")
    print("=" * 72)
    after, tenders_after, records_after = asyncio.run(take_snapshot())
    render(after, tenders_after, records_after)

    print("\n" + "=" * 72)
    print("VEREDICTE")
    print("=" * 72)
    failures = 0

    problems = before.diff(after)
    if problems:
        failures += 1
        print("  [KO]  El tenant demo ha canviat:")
        for problem in problems:
            print(f"          {problem}")
    else:
        print(
            f"  [OK]  El tenant demo es identic: {before.total_rows} files, "
            f"{len(before.document_hashes)} documents, "
            f"{len(before.storage_keys)} objectes"
        )

    if (tenders_before, records_before) != (tenders_after, records_after):
        # No es un error per si sol: una ingesta PLACSP independent podria
        # haver-hi entrat pel mig. Es reporta perque es pugui descartar.
        print(
            f"  [--]  Les dades publiques han canviat: "
            f"{tenders_before}->{tenders_after} licitacions, "
            f"{records_before}->{records_after} revisions. "
            "Comprova si hi ha hagut una ingesta pel mig."
        )
    else:
        print("  [OK]  Les dades publiques PLACSP no s'han tocat")

    left = asyncio.run(pending_ephemeral())
    new = [item for item in left if item not in stragglers]
    if new:
        failures += 1
        print(f"  [KO]  L'smoke test ha deixat tenants efimers: {new}")
    else:
        print("  [OK]  No queda cap tenant efimer nou")

    if result.returncode != 0:
        failures += 1
        print(f"  [KO]  L'smoke test ha acabat amb codi {result.returncode}")
    else:
        print("  [OK]  L'smoke test ha passat")

    print()
    if failures:
        print(f"{failures} problema(es). Vegeu el detall a dalt.")
        return 1
    print("Tot correcte: l'smoke test no ha tocat res que no fos seu.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
