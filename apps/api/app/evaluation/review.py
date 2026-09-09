"""CLI de revisio humana del gold.

Generar la pagina de revisio:

    python -m app.evaluation.review --dataset gold-v1

Aplicar les decisions descarregades i publicar la versio revisada:

    python -m app.evaluation.review --apply decisions.json --reviewer "Nom Cognom" \\
        --promote gold-v1-reviewed

La revisio NO sobreescriu `gold-v1`: en crea una versio nova. Un benchmark ha de
poder dir exactament amb quina versio del dataset es va mesurar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.db.session import SessionFactory
from app.evaluation.dataset import save_dataset

# Carrega el gold sigui de la versio d'esquema que sigui: la revisio ha de
# funcionar igual amb un dataset v1 i amb el candidat v2.
from app.evaluation.dataset_v2 import load_gold
from app.evaluation.refinements import RefinementConflict
from app.evaluation.review_page import (
    apply_decisions,
    page_texts,
    promote,
    render_review_page,
    write_review_page,
)

DEFAULT_OUTPUT = Path("var/evaluation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.review", description=__doc__
    )
    parser.add_argument("--dataset", default="gold-v1", help="dataset a revisar")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--apply", type=Path, default=None, help="decisions.json descarregat")
    parser.add_argument("--reviewer", default=None, help="qui signa la revisio")
    parser.add_argument(
        "--promote",
        default=None,
        help="identificador de la versio revisada (p. ex. gold-v1-reviewed)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="aplica les decisions sobre el mateix dataset en comptes de promoure'l",
    )
    return parser


async def generate(args: argparse.Namespace) -> int:
    dataset = load_gold(args.dataset)
    async with SessionFactory() as session:
        texts = await page_texts(session, dataset)

    missing = sum(
        1
        for case in dataset.cases
        for item in [*case.requirements, *case.award_criteria]
        if (case.case_id, item.document_name, item.page_number) not in texts
    )

    path = write_review_page(dataset, render_review_page(dataset, texts), args.output)
    coverage = dataset.coverage()
    print(f"Pagina de revisio: {path}")
    print(f"  {coverage['cases']} casos · estat actual: ", end="")
    print(
        ", ".join(
            f"{key}={coverage[key]}" for key in ("draft", "verified", "rejected", "needs_review")
        )
    )
    if missing:
        print(
            f"  AVIS: {missing} anotacio(ns) sense text de pagina disponible "
            "(document no processat en aquesta base de dades)."
        )
    print(
        "\nObre-la, revisa cada anotacio contra el text de la pagina "
        "i descarrega decisions.json."
    )
    return 0


def apply(args: argparse.Namespace) -> int:
    if not args.reviewer:
        print("Cal --reviewer: una revisio sense signatura no es una revisio.")
        return 1
    payload = json.loads(args.apply.read_text(encoding="utf-8"))
    dataset_id = payload.get("dataset_id", args.dataset)
    decisions = payload.get("decisions", payload)

    dataset = load_gold(dataset_id)
    try:
        dataset, applied = apply_decisions(dataset, decisions, reviewer=args.reviewer)
    except RefinementConflict as conflict:
        # No es desa res. Verificar un refinament sense dir que passa amb
        # l'anotacio que afina deixaria el gold comptant dues vegades la mateixa
        # exigencia, i aixo ha de bloquejar, no avisar.
        print("No s'ha aplicat cap decisio: hi ha refinaments sense resoldre.")
        for problem in conflict.problems:
            print(f"  - {problem}")
        return 1

    if applied["unknown_id"]:
        print(f"AVIS: {applied['unknown_id']} decision(s) amb un id que no existeix al dataset.")

    if args.in_place:
        path = save_dataset(dataset)
    else:
        target = args.promote or f"{dataset_id}-reviewed"
        path = save_dataset(promote(dataset, target))

    coverage = dataset.coverage()
    # El gold efectiu es el que compta al denominador, no el que porta l'etiqueta
    # `verified`: una anotacio superada segueix dient que va ser verificada i ja
    # no es compara amb res.
    effective = coverage.get("counts_for_metrics", coverage["verified"])
    print(f"Decisions aplicades per {args.reviewer}: ", end="")
    print(", ".join(f"{k}={v}" for k, v in applied.items() if v))
    print(f"Dataset desat a {path}")
    print(
        f"  gold efectiu: {effective} anotacio(ns) al denominador "
        f"de {sum(coverage[k] for k in ('draft','verified','rejected','needs_review'))}"
    )
    if coverage.get("superseded"):
        print(
            f"  {coverage['superseded']} anotacio(ns) substituida(es): es conserven "
            "senceres i queden fora de les metriques."
        )
    if effective == 0:
        print("  ATENCIO: cap anotacio verificada. Les metriques oficials no son calculables.")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args()
    if args.apply:
        return apply(args)
    return asyncio.run(generate(args))


if __name__ == "__main__":
    sys.exit(main())
