"""CLI de l'adaptador de dades obertes de la PSCP (F-06a, fase 1).

    python -m app.ingestion.pscp_open_data --out <dir>                  (dry-run)
    python -m app.ingestion.pscp_open_data --out <dir> --limit 50
    python -m app.ingestion.pscp_open_data --out <dir> --tender <uuid>
    python -m app.ingestion.pscp_open_data --out <dir> --apply          (escriu)
    python -m app.ingestion.pscp_open_data --replay <captura> --out <dir> [--apply]

**Segur per defecte**: sense `--apply` es un dry-run (`--dry-run` n'es un alias). El
dry-run ho calcula tot (consulta, guardes i la mateixa escriptura) i escriu l'informe,
pero desfa la transaccio: no queda res a la base. ATENCIO: no es de "nomes lectura" a
nivell de base: fa els INSERT/UPDATE dins d'una transaccio que acaba amb ROLLBACK, i per
tant necessita la migracio 0063 aplicada (la taula ha d'existir). Nomes `--apply`
escriu de debo.

`--tender` accepta l'id de la licitacio o el seu `pscp_id_intern`. `--out` es el
directori de l'informe i, alhora, l'arrel on es desen les respostes redactades
(`raw_storage_key` hi es relatiu).

Codi de sortida: 0 be; 1 fallada (cap escriptura); 3 el dataset s'ha actualitzat durant
el run (`rowsUpdatedAt` diferent a l'inici i al final); 4 el dataset no informa de
`rowsUpdatedAt` (la llicencia exigeix la data); 5 completat, pero alguna licitacio no
s'ha pogut avaluar (desada com a `conflict` amb l'error; vegeu `evaluation_errors`); 6
massa licitacions no avaluables (fallada sistematica). En 1, 3, 4 i 6 no s'escriu res.

`--replay <captura>` (F-06e): cap peticio de xarxa. Rejuga el directori d'informe d'un
run anterior (`raw_manifest.json` i les respostes desades): abans de res verifica el
SHA-256 de cada fitxer contra el manifest (qualsevol discrepancia -> codi 1 sense
escriure res), i una licitacio de l'univers que el run original no va demanar tambe fa
fallar el run (codi 1). Es combina amb `--apply` / dry-run igual que el mode normal. Les
observacions noves apunten a la captura original, amb la marca `replay`.

Sense programador ni sync diari: cada execucio la llanca un operador.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import configure_logging
from app.ingestion.pscp_open_data import DATASET_ID, SOURCE_CODE
from app.ingestion.pscp_open_data.client import DEFAULT_MAX_REQUESTS, SodaClient
from app.ingestion.pscp_open_data.replay import ReplayClient, ReplayError
from app.ingestion.pscp_open_data.service import (
    BATCH_SIZE,
    EXIT_FAILED,
    PAGE_LIMIT,
    PscpOpenDataService,
    RunOptions,
)


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("ha de ser un enter >= 1")
    return number


def _batch_size(value: str) -> int:
    number = _positive(value)
    if number > 100:
        raise argparse.ArgumentTypeError("ha de ser entre 1 i 100")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion.pscp_open_data",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", required=True, type=Path, help="Directori de l'informe.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Escriu a la base. Sense aquesta opcio, el run es un dry-run.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Alias del comportament per defecte: ho calcula tot i no escriu res.",
    )
    parser.add_argument("--limit", type=_positive, default=None, help="Maxim de licitacions.")
    parser.add_argument("--tender", default=None, help="Una sola licitacio (id o pscp_id_intern).")
    parser.add_argument(
        "--batch-size", type=_batch_size, default=BATCH_SIZE, help=argparse.SUPPRESS
    )
    parser.add_argument("--page-limit", type=_positive, default=PAGE_LIMIT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--max-requests",
        type=_positive,
        default=DEFAULT_MAX_REQUESTS,
        help="Limit dur de peticions a la font en aquest run.",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=None,
        metavar="CAPTURA",
        help=(
            "Rejuga el directori d'informe d'un run anterior, sense cap peticio de xarxa "
            "(verifica el SHA-256 de cada fitxer contra raw_manifest.json)."
        ),
    )
    return parser


async def amain(
    argv: list[str] | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    client: SodaClient | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if session_factory is None:
        from app.db.session import SessionFactory

        session_factory = SessionFactory
    options = RunOptions(
        out_dir=args.out,
        dry_run=not args.apply,
        limit=args.limit,
        tender=args.tender,
        batch_size=args.batch_size,
        page_limit=args.page_limit,
    )
    if args.replay is not None:
        if client is not None:
            raise ValueError("--replay no admet un client injectat: no fa cap peticio.")
        # Exclusiu amb la xarxa: el client real no s'instancia mai.
        soda = ReplayClient(args.replay)
    else:
        soda = client or SodaClient(max_requests=args.max_requests)
    try:
        async with soda:
            code, report = await PscpOpenDataService(session_factory, soda).run(options)
    except ReplayError as exc:
        # La captura no es la del manifest: no s'ha llegit ni escrit res a la base.
        report = _replay_failure(args, options, exc)
        code = EXIT_FAILED
    _print(report)
    return code


def _replay_failure(args: argparse.Namespace, options: RunOptions, exc: Exception) -> dict:
    report = {
        "source_code": SOURCE_CODE,
        "dataset": DATASET_ID,
        "dry_run": options.dry_run,
        "replay": True,
        "replay_source": {"dir": str(args.replay)},
        "error": f"{type(exc).__name__}: {exc}"[:500],
        "requests": 0,
        "network_requests": 0,
        "exit_code": EXIT_FAILED,
    }
    options.out_dir.mkdir(parents=True, exist_ok=True)
    (options.out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return report


def _print(report: dict) -> None:
    evaluation = report.get("evaluation") or {}
    writes = report.get("writes") or {}
    mode = "DRY-RUN" if report.get("dry_run") else "RUN"
    if report.get("replay"):
        mode += " (REJOC, sense xarxa)"
    print(f"\nPSCP dades obertes {mode}  (codi {report.get('exit_code')})")
    print(f"  peticions       : {report.get('requests')}")
    if report.get("replay"):
        source = report.get("replay_source") or {}
        print(f"  captura         : {source.get('dir')}")
        print(f"  manifest sha256 : {source.get('raw_manifest_sha256')}")
    print(f"  licitacions     : {evaluation.get('tenders')}")
    for key, value in (evaluation.get("observations") or {}).items():
        print(f"    {key:<22}: {value}")
    errors = evaluation.get("evaluation_errors") or []
    print(f"  errors d'avaluacio: {len(errors)}")
    if writes:
        for key in ("inserted", "closed", "unchanged"):
            if key in writes:
                print(f"  {key:<16}: {writes.get(key)}")
        for key, value in writes.items():
            if key.startswith("closed_") and value:
                print(f"    {key:<26}: {value}")
        for kind, counts in (writes.get("by_kind") or {}).items():
            print(
                f"    {kind:<16}: inserides {counts.get('inserted')}, tancades "
                f"{counts.get('closed')}, sense canvis {counts.get('unchanged')}"
            )
        if "disappeared_not_closed" in writes:
            print(f"  desaparegudes (no tancades): {len(writes['disappeared_not_closed'])}")
        if writes.get("skipped"):
            print(f"  escriptura      : NO ({writes['skipped']})")
    if report.get("error"):
        print(f"  error           : {report['error']}")
    print(f"  {report.get('attribution', '')}")


def main() -> int:
    configure_logging()
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
