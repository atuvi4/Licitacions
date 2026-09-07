"""CLI del benchmark d'extraccio de requisits.

    python -m app.evaluation.requirements --dataset gold-v1 --provider anthropic

Sense credencial no s'inventa cap resultat: l'ordre diu exactament que falta i
surt amb codi 2, de manera que un CI ho pot distingir d'un fracas de qualitat.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.ai.provider import ProviderNotConfiguredError, get_provider
from app.db.session import SessionFactory
from app.evaluation.dataset import available_datasets, load_dataset
from app.evaluation.report import render_markdown, write_reports
from app.evaluation.runner import evaluate_dataset

DEFAULT_OUTPUT = Path("var/evaluation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.requirements", description=__doc__
    )
    parser.add_argument("--dataset", default="gold-v1", help="identificador del gold dataset")
    parser.add_argument("--provider", default=None, help="fake | anthropic (per defecte, ENV)")
    parser.add_argument("--limit", type=int, default=None, help="nombre maxim de casos")
    parser.add_argument("--tender", default=None, help="avalua nomes aquest case_id")
    parser.add_argument(
        "--force", action="store_true", help="ignora la cache i torna a cridar el proveidor"
    )
    parser.add_argument(
        "--prompt-version", default=None, help="informatiu: comprova la versio esperada"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="directori d'informes")
    parser.add_argument("--list", action="store_true", help="llista els datasets disponibles")
    parser.add_argument(
        "--include-draft",
        action="store_true",
        help="inclou anotacions en esborrany (exploracio; NO son metriques oficials)",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    if args.list:
        for name in available_datasets():
            print(name)
        return 0

    try:
        dataset = load_dataset(args.dataset)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Dataset no valid: {exc}")
        return 1

    coverage = dataset.coverage()
    print(
        f"Dataset {dataset.dataset_id} v{dataset.version}: {len(dataset.cases)} casos, "
        f"{dataset.total_requirements} requisits ({dataset.total_critical} critics), "
        f"{dataset.total_award_criteria} criteris."
    )
    print(
        f"  Anotacions: verificades={coverage['verified']} esborrany={coverage['draft']} "
        f"necessiten_revisio={coverage['needs_review']} rebutjades={coverage['rejected']}"
    )
    if coverage["verified"] == 0 and not args.include_draft:
        # Sense cap anotacio verificada no hi ha gold truth. Retornar xifres
        # calculades sobre esborranys seria donar per mesurat el que no ho esta.
        print(
            "\nCap anotacio verificada: no hi ha gold truth i les metriques\n"
            "oficials no son calculables. Revisa el dataset amb:\n"
            f"  python -m app.evaluation.review --dataset {dataset.dataset_id}\n"
            "O passa --include-draft per explorar (NO son metriques oficials)."
        )
        return 3

    try:
        provider = get_provider(args.provider)
    except ProviderNotConfiguredError as exc:
        print(f"Proveidor no configurat: {exc}")
        return 2

    if not provider.is_available():
        print(
            f"\nEl proveidor {provider.name!r} no esta disponible.\n"
            "Falta ANTHROPIC_API_KEY per executar el benchmark real.\n"
            "L'arnes esta llest: dataset, evaluator, metriques, CLI i informes.\n"
            "No s'inventa cap resultat."
        )
        return 2

    if args.prompt_version:
        from app.ai.prompts.requirements_v1 import PROMPT_VERSION

        if args.prompt_version != PROMPT_VERSION:
            print(
                f"La versio de prompt activa es {PROMPT_VERSION!r}, no {args.prompt_version!r}. "
                "Canvia el prompt importat o treu el flag."
            )
            return 1

    async with SessionFactory() as session:
        summary, details = await evaluate_dataset(
            session,
            dataset,
            provider=provider,
            force=args.force,
            limit=args.limit,
            only_case=args.tender,
            include_draft=args.include_draft,
        )

    print()
    print(render_markdown(summary, details))
    paths = write_reports(summary, details, args.output)
    print("Informes:")
    for kind, path in paths.items():
        print(f"  {kind:9} {path}")
    return 0


def main() -> int:
    # La consola de Windows es cp1252 per defecte i l'informe porta accents i
    # simbols d'avis. Sense aixo, imprimir-lo peta amb UnicodeEncodeError just
    # despres d'haver gastat els tokens, que es el pitjor moment possible.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
