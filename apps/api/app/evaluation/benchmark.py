"""Benchmark d'extraccio: el mateix corpus mesurat amb perfils diferents.

    python -m app.evaluation.benchmark --dataset gold-v2.1 --profile V2 --plan-only
    python -m app.evaluation.benchmark --dataset gold-v2.1 --profile V2 --paid-run

Que aporta sobre `app.evaluation.requirements`, que segueix sent el benchmark
d'un sol perfil:

* **Tria de perfil.** La canonada ja sabia extreure amb v1 o v2; el benchmark no
  ho podia demanar. Sense aixo, comparar les dues versions voldria dir canviar
  `DEFAULT_PROFILE`, i llavors la mesura hauria canviat el producte.
* **Qualsevol versio de gold.** `gold-v2.1` s'escriu amb l'esquema v2 i el
  lector estricte de la v1 el rebutja. Aqui es carrega amb `load_gold()`, que
  torna el model v2 sencer: anotacions, criteris, estats i metadades de revisio.
  No es converteix a v1, perque convertir-lo seria perdre exactament allo que la
  v2 va venir a mesurar.
* **Base de dades propia.** El corpus viu en una base de dades, i el benchmark hi
  escriu els runs. Fer-ho contra la base de desenvolupament o contra la de tests
  seria contaminar-les: aqui es refusa per nom, abans de connectar.
* **Pla abans de pagar.** `--plan-only` diu quins casos, documents, pagines i
  chunks s'enviarien, amb una estimacio de volum si hi ha historic, sense
  construir cap proveidor. I una execucio amb proveidor real no comenca sense
  `--paid-run`: un run es paga per chunk, i no s'ha de poder llancar per accident.
* **Manifest per execucio.** Cada run deixa escrit contra que es va mesurar
  —commit, dataset, proveidor, model, perfil, versions i hora— al seu propi
  directori. Una execucio no pot trepitjar-ne una altra.

Aquest modul NO promou res: `DEFAULT_PROFILE` no es toca. Mesurar i decidir son
passos diferents, i el segon no el fa un script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.profiles import PROFILES, ExtractorProfile
from app.ai.provider import ProviderNotConfiguredError, StructuredLLMProvider, get_provider
from app.compliance.rules import RULES_VERSION
from app.core.config import settings
from app.evaluation.corpus import resolve_tender
from app.evaluation.dataset import GoldDataset, available_datasets
from app.evaluation.dataset_v2 import GoldDatasetV2, load_gold
from app.evaluation.plan import build_plan, render_plan
from app.evaluation.report import render_markdown, write_reports
from app.evaluation.runner import evaluate_dataset

#: On van els runs. Cadascun al seu subdirectori: res no se sobreescriu.
DEFAULT_OUTPUT = Path("var/evaluation/benchmarks")

#: La base de dades que el benchmark hauria de fer servir.
DEFAULT_BENCHMARK_DATABASE = "licitacions_benchmark"

#: Bases on el benchmark no pot escriure MAI. `licitacions` es desenvolupament i
#: pilot; `licitacions_test` es la dels tests automatics, i un benchmark que hi
#: deixes runs falsejaria l'aillament que els protegeix.
FORBIDDEN_DATABASES = frozenset({"licitacions", "licitacions_test", "postgres"})

#: Proveidors que no llegeixen cap plec. Serveixen per provar la canonada, no
#: per mesurar res.
SIMULATED_PROVIDERS = frozenset({"fake", "fixture"})

_DRIVER = "postgresql+asyncpg"

#: Codi de sortida quan falta la confirmacio explicita d'un run de pagament.
EXIT_PAID_RUN_NOT_CONFIRMED = 5


class BenchmarkConfigError(RuntimeError):
    """Falta alguna cosa per poder mesurar, o el que hi ha no serveix."""


# --------------------------------------------------------------------------- #
# Que mesurem i amb que                                                         #
# --------------------------------------------------------------------------- #


def resolve_profile(name: str) -> ExtractorProfile:
    """El perfil d'extraccio demanat, escrit com s'escrigui (`V2`, `v2`)."""
    profile = PROFILES.get((name or "").strip().lower())
    if profile is None:
        disponibles = ", ".join(sorted(PROFILES))
        raise BenchmarkConfigError(f"Perfil {name!r} desconegut. N'hi ha: {disponibles}.")
    return profile


def resolve_provider(name: str | None) -> StructuredLLMProvider:
    """El proveidor real, o un error que diu exactament que falta.

    Un proveidor simulat respon el que li han encuat o un plec inventat. Deixar
    que un benchmark l'utilitzi produiria xifres amb aparenca de mesura.
    """
    try:
        provider = get_provider(name)
    except ProviderNotConfiguredError as exc:
        raise BenchmarkConfigError(str(exc)) from None
    if provider.name in SIMULATED_PROVIDERS:
        raise BenchmarkConfigError(
            f"El proveidor {provider.name!r} no llegeix cap plec: no mesura res. "
            "Cal un proveidor real (LLM_PROVIDER=anthropic i la seva credencial "
            "a l'entorn, mai al repositori)."
        )
    if not provider.is_available():
        raise BenchmarkConfigError(
            f"El proveidor {provider.name!r} no esta disponible: falta la credencial."
        )
    return provider


def resolve_benchmark_database(raw: str | None) -> URL:
    """La base on s'executara el benchmark, comprovada abans de connectar.

    Es refusa per nom, i no per si de cas: el corpus i els runs s'escriuen, i
    aquesta comprovacio es l'unica cosa que separa una mesura d'un incident a la
    base de desenvolupament.
    """
    if not raw:
        raise BenchmarkConfigError(
            "Falta la base de dades del benchmark. Passa --database-url o defineix "
            f"BENCHMARK_DATABASE_URL (per exemple, una base {DEFAULT_BENCHMARK_DATABASE!r} "
            "amb el corpus ingerit)."
        )
    try:
        url = make_url(raw)
    except ArgumentError:
        raise BenchmarkConfigError("La DSN del benchmark no es valida.") from None
    if url.drivername != _DRIVER:
        raise BenchmarkConfigError(f"La DSN ha de fer servir {_DRIVER!r}, no {url.drivername!r}.")
    if url.query:
        raise BenchmarkConfigError(
            "La DSN no pot portar parametres: podrien canviar la base o el search_path."
        )
    if not url.host or "," in url.host:
        raise BenchmarkConfigError("La DSN ha de tenir exactament un host explicit.")
    if not url.database:
        raise BenchmarkConfigError("La DSN no diu a quina base connecta.")
    prohibides = FORBIDDEN_DATABASES | {settings.POSTGRES_DB}
    if url.database in prohibides:
        raise BenchmarkConfigError(
            f"El benchmark no pot escriure a {url.database!r}. Fes servir una base propia, "
            f"per exemple {DEFAULT_BENCHMARK_DATABASE!r}."
        )
    return url


async def resolve_corpus(
    session: AsyncSession, dataset: GoldDataset
) -> tuple[list[str], list[str]]:
    """Quins casos del gold tenen licitacio a la base, i quins no.

    Es comprova ABANS de cridar el proveidor: descobrir que no hi ha corpus
    despres d'haver gastat tokens seria car i evitable.
    """
    trobats: list[str] = []
    absents: list[str] = []
    for case in dataset.cases:
        tender = await resolve_tender(session, case)
        (trobats if tender is not None else absents).append(case.case_id)
    return trobats, absents


# --------------------------------------------------------------------------- #
# Manifest: contra que es va mesurar                                            #
# --------------------------------------------------------------------------- #


def git_commit() -> str:
    """El commit exacte del codi que mesura. Sense ell, un run no es repetible."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


def new_benchmark_id(dataset_id: str, profile: ExtractorProfile) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{dataset_id}-{profile.name}-{stamp}-{uuid.uuid4().hex[:6]}"


@dataclass(slots=True)
class RunManifest:
    """Tot el que cal saber per repetir una execucio i per comparar-la amb una altra."""

    benchmark_id: str
    started_at: str
    git_commit: str
    dataset_id: str
    dataset_version: int
    gold_schema_version: str
    provider: str
    model: str
    profile: str
    extractor_version: str
    prompt_version: str
    schema_version: str
    rules_version: str
    #: Nomes el nom de la base: una DSN amb credencials no ha d'acabar en un artefacte.
    database: str
    cases_requested: int
    cases_with_corpus: int
    cases_without_corpus: list[str] = field(default_factory=list)
    finished_at: str | None = None
    results: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_manifest(directory: Path, manifest: RunManifest) -> Path:
    """Desa el manifest. Si ja existeix, s'atura: un run no en sobreescriu un altre."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    if path.exists():
        raise BenchmarkConfigError(f"Ja existeix un manifest a {path}: no se sobreescriu.")
    path.write_text(
        json.dumps(manifest.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def update_manifest(directory: Path, manifest: RunManifest) -> Path:
    """Completa el manifest del run amb el resultat, un cop acabat."""
    path = directory / "manifest.json"
    path.write_text(
        json.dumps(manifest.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.benchmark",
        description="Mesura un gold dataset amb un perfil d'extraccio concret.",
    )
    parser.add_argument("--dataset", default="gold-v2.1", help="identificador del gold dataset")
    parser.add_argument(
        "--profile", default="v1", help=f"perfil d'extraccio: {', '.join(sorted(PROFILES))}"
    )
    parser.add_argument("--provider", default=None, help="proveidor (per defecte, ENV)")
    parser.add_argument(
        "--database-url",
        default=None,
        help="DSN de la base del benchmark (o BENCHMARK_DATABASE_URL). No es crea sola: "
        f"ha d'existir, amb el corpus ingerit (per exemple {DEFAULT_BENCHMARK_DATABASE!r}).",
    )
    parser.add_argument("--limit", type=int, default=None, help="nombre maxim de casos")
    parser.add_argument("--tender", default=None, help="avalua nomes aquest case_id")
    parser.add_argument("--force", action="store_true", help="ignora la cache de runs")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="directori de runs")
    parser.add_argument("--list", action="store_true", help="llista els datasets disponibles")
    parser.add_argument(
        "--include-draft",
        action="store_true",
        help="inclou anotacions en esborrany (exploracio; NO son metriques oficials)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--plan-only",
        action="store_true",
        help="mostra casos, documents, pagines i chunks que s'enviarien, i una estimacio "
        "de volum; NO construeix ni crida cap proveidor i no escriu res",
    )
    mode.add_argument(
        "--paid-run",
        action="store_true",
        help="confirmacio explicita: aquesta execucio crida el proveidor real i te cost",
    )
    return parser


def planned_provider(name: str | None) -> tuple[str, str]:
    """Nom i model del proveidor que es faria servir, SENSE construir-lo."""
    return (name or settings.LLM_PROVIDER or "fake").strip().lower(), settings.LLM_MODEL


async def _describe_cluster(session: AsyncSession) -> str | None:
    """Quin servidor respon: el port 5433 d'una maquina ha allotjat clusters diferents."""
    if session.bind.dialect.name != "postgresql":
        return None
    database = await session.scalar(sa.text("SELECT current_database()"))
    version = await session.scalar(sa.text("SHOW server_version"))
    parts = [f"base {database!r}", f"PostgreSQL {version}"]
    try:
        async with session.begin_nested():
            data_dir = await session.scalar(sa.text("SHOW data_directory"))
        parts.append(f"data_directory {data_dir}")
    except DBAPIError:
        # Cal pg_read_all_settings; sense aquest permis, la resta ja identifica prou.
        pass
    return " · ".join(parts)


async def plan_only(
    args: argparse.Namespace, dataset: GoldDataset, profile: ExtractorProfile, database: URL
) -> int:
    """Llegeix la base i diu que s'enviaria. No construeix ni crida cap proveidor."""
    provider_name, model = planned_provider(args.provider)
    engine = create_async_engine(database)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            if session.bind.dialect.name == "postgresql":
                await session.execute(sa.text("SET TRANSACTION READ ONLY"))
            cluster = await _describe_cluster(session)
            plan = await build_plan(
                session,
                dataset,
                profile=profile,
                provider_name=provider_name,
                model=model,
                only_case=args.tender,
                limit=args.limit,
                force=args.force,
                include_draft=args.include_draft,
            )
            await session.rollback()
    finally:
        await engine.dispose()

    print()
    if cluster:
        print(f"Servidor: {cluster}")
    print(render_plan(plan))
    if provider_name in SIMULATED_PROVIDERS:
        print(
            f"\nNota: el proveidor configurat es {provider_name!r}, que no mesura res: "
            "una execucio real el refusaria."
        )
    print(
        "\nNO s'ha construit ni cridat cap proveidor i no s'ha escrit res. "
        "Per executar-ho de debo cal --paid-run."
    )
    return 0 if any(case.has_corpus for case in plan.cases) else 4


def describe_dataset(dataset: GoldDataset) -> str:
    linies = [
        f"Dataset {dataset.dataset_id} v{dataset.version}: {len(dataset.cases)} casos, "
        f"{dataset.total_requirements} requisits ({dataset.total_critical} critics), "
        f"{dataset.total_award_criteria} criteris."
    ]
    coverage = dataset.coverage()
    linies.append(
        f"  Anotacions: verificades={coverage.get('verified', 0)} "
        f"esborrany={coverage.get('draft', 0)} "
        f"necessiten_revisio={coverage.get('needs_review', 0)} "
        f"rebutjades={coverage.get('rejected', 0)}"
    )
    if isinstance(dataset, GoldDatasetV2):
        estats = dataset.status_counts()
        linies.append(
            f"  Gold v2: compten per a metriques={estats.get('counts_for_metrics', 0)} "
            f"superades={estats.get('superseded', 0)}"
        )
    return "\n".join(linies)


async def run(args: argparse.Namespace) -> int:
    if args.list:
        for name in available_datasets():
            print(name)
        return 0

    try:
        dataset = load_gold(args.dataset)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Dataset no valid: {exc}")
        return 1
    print(describe_dataset(dataset))

    try:
        profile = resolve_profile(args.profile)
        database = resolve_benchmark_database(
            args.database_url or settings.BENCHMARK_DATABASE_URL or None
        )
    except BenchmarkConfigError as exc:
        print(f"\nNo es pot mesurar: {exc}")
        return 2

    if args.plan_only:
        return await plan_only(args, dataset, profile, database)

    try:
        provider = resolve_provider(args.provider)
    except BenchmarkConfigError as exc:
        print(f"\nNo es pot mesurar: {exc}")
        return 2
    if not args.paid_run:
        # Construir el proveidor no gasta res (el client es crea a la primera
        # crida); el que es paga ve despres. Aqui s'atura tota execucio que no
        # s'hagi confirmat expressament.
        print(
            f"\nAquesta execucio cridaria el proveidor real {provider.name!r} "
            f"(model {provider.model!r}) i TE COST.\n"
            "Revisa primer que s'enviaria amb --plan-only, i repeteix-la amb --paid-run "
            "si la vols pagar."
        )
        return EXIT_PAID_RUN_NOT_CONFIRMED

    coverage = dataset.coverage()
    if coverage.get("verified", 0) == 0 and not args.include_draft:
        print(
            "\nCap anotacio verificada: no hi ha gold truth i les metriques oficials\n"
            "no son calculables. Passa --include-draft per explorar (NO son metriques)."
        )
        return 3

    engine = create_async_engine(database)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            trobats, absents = await resolve_corpus(session, dataset)
            if not trobats:
                print(
                    f"\nCap dels {len(dataset.cases)} casos del gold te licitacio a "
                    f"{database.database!r}: no hi ha corpus per mesurar.\n"
                    "Cal ingerir els expedients del dataset en aquesta base abans "
                    "d'executar el benchmark."
                )
                return 4

            manifest = RunManifest(
                benchmark_id=new_benchmark_id(dataset.dataset_id, profile),
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                git_commit=git_commit(),
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.version,
                gold_schema_version=getattr(dataset, "gold_schema_version", "gold-schema-v1"),
                provider=provider.name,
                model=provider.model,
                profile=profile.name,
                extractor_version=profile.extractor_version,
                prompt_version=profile.prompt_version,
                schema_version=profile.schema_version,
                rules_version=RULES_VERSION,
                database=database.database or "",
                cases_requested=len(dataset.cases),
                cases_with_corpus=len(trobats),
                cases_without_corpus=absents,
            )
            directory = args.output / manifest.benchmark_id
            try:
                path = write_manifest(directory, manifest)
            except BenchmarkConfigError as exc:
                print(f"\n{exc}")
                return 2
            print(f"\nRun {manifest.benchmark_id}\n  manifest: {path}")

            summary, details = await evaluate_dataset(
                session,
                dataset,
                provider=provider,
                force=args.force,
                limit=args.limit,
                only_case=args.tender,
                include_draft=args.include_draft,
                profile=profile,
            )
    finally:
        await engine.dispose()

    print()
    print(render_markdown(summary, details))
    paths = write_reports(summary, details, directory)
    manifest.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    manifest.results = summary.as_dict()
    update_manifest(directory, manifest)
    print("Informes:")
    for kind, path in paths.items():
        print(f"  {kind:9} {path}")
    return 0


def main() -> int:
    # La consola de Windows es cp1252 i l'informe porta accents: sense aixo,
    # imprimir-lo peta just despres d'haver gastat els tokens.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
