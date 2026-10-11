"""Primera validacio de l'Extraction Contract v3 amb un model real, sobre UNA licitacio.

    # Credencials NOMES a la sessio: mai a la linia d'ordres ni a un .env.
    $env:DATABASE_URL = "postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_v3_probe_x"
    $env:PGPASSWORD   = "..."        # asyncpg el llegeix sol

    python scripts/v3_tender_probe.py --expediente 2024-3137 --expect-buyer Palamos \\
        --locate A=technical_terms:31 --plan-only
    python scripts/v3_tender_probe.py ... --chunks 2,5,15 --paid-run        # canari
    python scripts/v3_tender_probe.py ... --resume-from-run <RUN_ID> --plan-only
    python scripts/v3_tender_probe.py ... --resume-from-run <RUN_ID> --paid-run
    # Represa selectiva: nomes aquests chunks pendents; la resta segueixen pendents.
    python scripts/v3_tender_probe.py ... --resume-from-run <RUN_ID> --resume-chunks 15 --plan-only

Per que existeix i no es el benchmark: el gold encara no sap puntuar valors v3
(`value_json` son claims) i Palamos no hi es. Aquest script mesura una cosa mes
petita i honesta: que la v3 funciona de cap a cap amb un model real sobre un plec
on sabem que hi ha (una taula GPON/FTTH, un "es valorara", un model d'oferta, una
clausula de criteris). **No mesura la qualitat de l'extraccio.**

Garanties, en l'ordre en que s'apliquen:

1. **Base d'usar i llencar.** Nomes `licitacions_v3_probe_*`, un sol host
   explicit, mai el port 5432 (3CX), cap parametre a la DSN. Les bases canoniques
   es refusen per nom abans de connectar.
2. **Esquema al dia, sense migrar.** La base ha d'estar al `head` d'Alembic. Si
   no hi es, s'atura i diu com migrar la COPIA; no migra res tot sol.
3. **Una sola licitacio.** Per expedient exacte (i opcionalment per id i per
   nom de l'organisme). Zero o mes d'una: s'atura.
4. **Entrada cega.** Nomes els plecs que la canonada ja analitza (PCAP, PPT,
   annexos). Si el pla inclogues una acta d'adjudicacio, s'atura. El resultat
   real, preus oferts o respostes esperades no es llegeixen enlloc.
5. **Dos modes explicits.** `--plan-only` no construeix cap proveidor i obre una
   transaccio READ ONLY. `--paid-run` refusa els proveidors simulats i no fa
   reintents. No hi ha cap mode per defecte.
6. **Perfil explicit.** `--profile v1|v2|v3`; per defecte, el de producte
   (`REQUIREMENTS_PRODUCT_PROFILE`, 3B-2 §6.7). La v3 hi entra per la porta
   interna (`INTERNAL_PROFILES`): no s'afegeix a `PROFILES` i cap job no la pot
   demanar pel nom. El pla diu documents, pagines, chunks, omesos i QA.

La sortida son codis i xifres: cap resposta del model, cap text del plec, cap
credencial.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai import profiles
from app.core.config import settings
from app.models.enums import TenderDocumentType
from app.models.tender_documents import TenderDocumentExtraction
from app.models.tenders import Tender
from app.services.analysis_planning import usage_history
from app.services.extraction_qa import combine_status
from app.services.requirement_analysis import analysis_input
from app.services.requirement_chunking import AnalyzableDocument, Chunk, build_chunks

API_ROOT = Path(__file__).resolve().parents[1]
PROBE_PREFIX = "licitacions_v3_probe_"
_PROBE_NAME = re.compile(r"^licitacions_v3_probe_[a-z0-9_]+$")
#: Mai, en cap cas, encara que el nom comences pel prefix.
CANONICAL_DATABASES = frozenset(
    {"licitacions", "licitacions_review_s3", "licitacions_benchmark", "licitacions_test",
     "licitacions_recovery_check", "postgres", "template0", "template1"}
)
#: El port del PostgreSQL de 3CX en aquesta maquina. No es toca mai.
FORBIDDEN_PORTS = frozenset({5432})
_DRIVER = "postgresql+asyncpg"
#: Documents que no poden entrar mai a una extraccio cega.
OUTCOME_DOCUMENT_TYPES = frozenset({TenderDocumentType.AWARD.value})

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_UNSAFE_INPUT = 3
#: La porta unica (proveidor, model pinat, preu, pressupost) ha refusat el run pagat.
EXIT_NOT_ALLOWED = 4


class ProbeError(RuntimeError):
    """Falta alguna cosa, o el que hi ha no es segur. Sempre abans de cap crida."""


#: Perfils que la sonda pot executar: els de producte (`PROFILES`) i la v3 interna.
PROFILE_CHOICES = ("v1", "v2", "v3")


def resolve_profile(name: str | None) -> profiles.ExtractorProfile:
    """El perfil demanat; sense nom, el de producte (`REQUIREMENTS_PRODUCT_PROFILE`).

    La v3 continua fora de `PROFILES` (cap job no la pot demanar pel nom): aqui
    entra per `INTERNAL_PROFILES`, la porta interna de la sonda.
    """
    chosen = (name or settings.REQUIREMENTS_PRODUCT_PROFILE).strip().lower()
    profile = profiles.PROFILES.get(chosen) or profiles.INTERNAL_PROFILES.get(chosen)
    if profile is None:
        raise ProbeError(
            f"Perfil desconegut: {chosen!r} (admesos: {', '.join(PROFILE_CHOICES)})."
        )
    return profile


# --------------------------------------------------------------------------- #
# Base de dades                                                                #
# --------------------------------------------------------------------------- #


def validate_probe_database(raw: str | None) -> URL:
    """La DSN, comprovada ABANS de connectar. Tot el que no es inequivoc es refusa."""
    if not raw:
        raise ProbeError("Falta --database-url (una base licitacions_v3_probe_*).")
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError):
        # `make_url` llanca ValueError amb diversos hosts ("a:5433,b:5433").
        raise ProbeError("La DSN no es valida (un sol host explicit).") from None
    if url.drivername != _DRIVER:
        raise ProbeError(f"La DSN ha de fer servir {_DRIVER!r}.")
    if url.query:
        raise ProbeError("La DSN no pot portar parametres: podrien canviar la base.")
    if not url.host or "," in url.host:
        raise ProbeError("La DSN ha de tenir exactament un host explicit.")
    if url.port is None:
        raise ProbeError("La DSN ha de dir el port explicitament (el de treball es 5433).")
    if url.port in FORBIDDEN_PORTS:
        raise ProbeError(f"El port {url.port} no es del projecte (3CX). No es toca.")
    name = url.database or ""
    if name in CANONICAL_DATABASES or name == settings.POSTGRES_DB:
        raise ProbeError(f"{name!r} es una base canonica. El probe nomes treballa sobre copies.")
    if not _PROBE_NAME.match(name):
        raise ProbeError(
            f"{name!r} no es una base de probe. Ha de comencar per {PROBE_PREFIX!r} "
            "(una copia d'usar i llencar)."
        )
    return url


#: Variables de credencial que el probe llegeix. Nomes de l'entorn del proces.
DB_ENV_VARS = ("DATABASE_URL", "PGPASSWORD")


def resolve_database_dsn(
    cli_value: str | None,
    *,
    environ: dict[str, str] | None = None,
    persistent_scopes=None,
) -> str:
    """D'on surt la DSN, sense que la contrasenya passi mai per la linia d'ordres.

    * `--database-url` nomes s'accepta SENSE contrasenya (quedaria a l'historial).
      La contrasenya ve de `PGPASSWORD`, que asyncpg ja llegeix de l'entorn.
    * Sense `--database-url`, `DATABASE_URL` de l'entorn del proces (pot portar
      contrasenya: l'entorn de la sessio no queda a l'historial).
    * `DATABASE_URL` i `PGPASSWORD` es refusen si son a l'entorn persistent de
      Windows (User o Machine): nomes han de viure a la sessio.
    """
    environ = os.environ if environ is None else environ
    persistent_scopes = persistent_scopes or _persistent_windows_scopes
    for name in DB_ENV_VARS:
        scopes = persistent_scopes(name)
        if scopes:
            raise ProbeError(
                f"{name} es a l'entorn persistent de Windows ({', '.join(scopes)}). "
                "Treu-la'n i defineix-la nomes per a la sessio."
            )
    if cli_value:
        try:
            parsed = make_url(cli_value)
        except (ArgumentError, ValueError):
            raise ProbeError("La DSN no es valida.") from None
        if parsed.password:
            raise ProbeError(
                "--database-url no pot portar contrasenya (quedaria a l'historial). "
                "Posa-la a PGPASSWORD o la DSN sencera a DATABASE_URL, nomes a la sessio."
            )
        return cli_value
    value = environ.get("DATABASE_URL")
    if not value:
        raise ProbeError(
            "Falta la base: DATABASE_URL a l'entorn de la sessio, o --database-url sense "
            "contrasenya."
        )
    return value


def alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    head = script.get_current_head()
    if head is None:
        raise ProbeError("No s'ha pogut determinar el head d'Alembic.")
    return head


async def check_database(session: AsyncSession, url: URL) -> str:
    """La base es la demanada i esta al head. Si no, s'atura: no migra res."""
    current = await session.scalar(sa.text("SELECT current_database()"))
    if current != url.database:
        raise ProbeError(f"Connectat a {current!r}, no a {url.database!r}.")
    head = alembic_head()
    try:
        async with session.begin_nested():
            found = await session.scalar(sa.text("SELECT version_num FROM alembic_version"))
    except Exception:  # noqa: BLE001 - qualsevol fallada es "no se sap": s'atura
        found = None
    if found != head:
        raise ProbeError(
            f"La base {url.database!r} es a {found!r} i el codi a {head!r}. Migra NOMES la "
            f"copia: `DATABASE_URL=<dsn de la copia> alembic upgrade head`. El probe no migra."
        )
    return head


# --------------------------------------------------------------------------- #
# Credencial                                                                   #
# --------------------------------------------------------------------------- #

KEY_NAME = "ANTHROPIC_API_KEY"


def _persistent_windows_scopes(name: str) -> list[str]:
    """Scopes persistents de Windows (User, Machine) on hi ha la variable. Nomes presencia."""
    if sys.platform != "win32":
        return []
    import winreg

    found = []
    locations = (
        ("User", winreg.HKEY_CURRENT_USER, "Environment"),
        ("Machine", winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    )
    for scope, hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if value:
            found.append(scope)
    return found


def _dotenv_has(name: str, paths: tuple[Path, ...]) -> bool:
    for env in paths:
        if env.is_file():
            for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition("=")
                if key.strip() == name and value.strip():
                    return True
    return False


def require_session_scoped_key(
    *,
    environ: dict[str, str] | None = None,
    persistent_scopes=_persistent_windows_scopes,
    dotenv_paths: tuple[Path, ...] = (API_ROOT / ".env", API_ROOT.parent.parent / ".env"),
) -> None:
    """La clau ha de viure NOMES a l'entorn d'aquest proces.

    Es refusa si no hi es, si es a un `.env` (quedaria escrita al disc del
    repositori) o si es a l'entorn persistent de Windows (User o Machine). Mai
    no se'n llegeix ni s'imprimeix el valor: nomes si hi es.
    """
    environ = os.environ if environ is None else environ
    if not environ.get(KEY_NAME):
        raise ProbeError(
            f"{KEY_NAME} no es a l'entorn d'aquest proces. Defineix-la nomes per a la sessio."
        )
    if _dotenv_has(KEY_NAME, dotenv_paths):
        raise ProbeError(f"{KEY_NAME} es a un fitxer .env. Treu-la'n: nomes a la sessio.")
    scopes = persistent_scopes(KEY_NAME)
    if scopes:
        raise ProbeError(
            f"{KEY_NAME} es a l'entorn persistent de Windows ({', '.join(scopes)}). "
            "Treu-la'n i defineix-la nomes per a la sessio."
        )


# --------------------------------------------------------------------------- #
# Licitacio i chunks                                                           #
# --------------------------------------------------------------------------- #


def _fold(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


async def resolve_single_tender(
    session: AsyncSession,
    *,
    expediente: str,
    tender_id: uuid.UUID | None = None,
    expect_buyer: str | None = None,
) -> Tender:
    """Exactament una licitacio, per expedient EXACTE. Zero o mes d'una: s'atura."""
    # `Tender.buyer` ja es `lazy="joined"`: el nom de l'organisme arriba amb la fila.
    query = sa.select(Tender).where(Tender.expediente_number == expediente.strip())
    if tender_id is not None:
        query = query.where(Tender.id == tender_id)
    matches = (await session.scalars(query)).all()
    if len(matches) != 1:
        raise ProbeError(
            f"L'expedient {expediente!r} resol a {len(matches)} licitacions; n'ha de ser una. "
            "Precisa-la amb --tender-id."
        )
    tender = matches[0]
    if expect_buyer is not None:
        buyer = tender.buyer.name if tender.buyer is not None else None
        if _fold(expect_buyer) not in _fold(buyer):
            raise ProbeError("L'organisme de la licitacio no es l'esperat (--expect-buyer).")
    return tender


def parse_chunk_selection(raw: str | None, chunks: list[Chunk]) -> list[int]:
    """`None` = tots. Si no, indexs separats per comes, tots existents."""
    available = [chunk.index for chunk in chunks]
    if raw is None:
        return available
    try:
        wanted = sorted({int(part) for part in raw.split(",") if part.strip()})
    except ValueError:
        raise ProbeError(f"--chunks ha de ser una llista d'enters, no {raw!r}.") from None
    if not wanted:
        raise ProbeError("--chunks buit.")
    unknown = [index for index in wanted if index not in available]
    if unknown:
        raise ProbeError(f"Chunks inexistents: {unknown} (n'hi ha 0-{len(available) - 1}).")
    return wanted


def parse_locators(raw: list[str] | None) -> dict[str, tuple[str, int]]:
    """`ETIQUETA=tipus_document:pagina`, p. ex. `A=technical_terms:31`."""
    locators: dict[str, tuple[str, int]] = {}
    for item in raw or []:
        match = re.fullmatch(r"([A-Za-z0-9_-]+)=([a-z_]+):(\d+)", item.strip())
        if not match or match.group(2) not in {t.value for t in TenderDocumentType}:
            raise ProbeError(f"--locate {item!r}: format ETIQUETA=tipus_document:pagina.")
        locators[match.group(1)] = (match.group(2), int(match.group(3)))
    return locators


def locate(chunks: list[Chunk], locators: dict[str, tuple[str, int]]) -> dict[str, list[int]]:
    """Per a cada etiqueta, els chunks que contenen aquella pagina (tambe per solapament)."""
    found: dict[str, list[int]] = {}
    for label, (document_type, page) in locators.items():
        found[label] = [
            chunk.index
            for chunk in chunks
            if str(chunk.document.document_type) == document_type
            and any(ref.page_number == page for ref in chunk.pages)
        ]
    return found


def minimal_cover(located: dict[str, list[int]]) -> list[int]:
    """El conjunt de chunks mes petit que cobreix totes les etiquetes (cerca exacta).

    Son poques etiquetes i pocs candidats: es proven totes les mides, de menor a
    major, i es torna la primera combinacio que ho cobreix tot.
    """
    from itertools import combinations

    if any(not indexes for indexes in located.values()):
        missing = [label for label, indexes in located.items() if not indexes]
        raise ProbeError(f"Cap chunk conte les pagines de {missing}.")
    candidates = sorted({index for indexes in located.values() for index in indexes})
    for size in range(1, len(candidates) + 1):
        for combo in combinations(candidates, size):
            if all(set(indexes) & set(combo) for indexes in located.values()):
                return list(combo)
    return candidates


# --------------------------------------------------------------------------- #
# Represa                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ResumePreview:
    source_run_id: str
    source_status: str
    reused: list[int]
    send: list[int]
    #: Represa selectiva: els que la represa podria enviar i ara NO s'envien.
    #: Queden pendents (`not_selected`), no fallits.
    pending: list[int] = field(default_factory=list)

    @property
    def provider_calls(self) -> int:
        return len(self.send)


async def preview_resume(
    session: AsyncSession,
    tender: Tender,
    run_id: uuid.UUID,
    *,
    provider_name: str,
    model: str,
    only_raw: str | None = None,
    profile: profiles.ExtractorProfile,
) -> ResumePreview:
    """Que faria `--resume-from-run`, amb el MATEIX `plan_resume` de la canonada.

    Nomes llegeix. Totes les comprovacions de la represa (mateixa licitacio,
    entrada, perfil, proveidor i model, pla de chunks; run parcial, o fallit per
    `worker_lost`/`internal_error`, i no superat per cap run mes nou) son les de
    `plan_resume` i `run_is_superseded`: aqui no n'hi ha cap de propia. Un parcial
    ja no es mai vigent (3B-2 §6.3). Si refusa, s'atura.

    `profile` no te cap valor per defecte: un sol perfil, el que la CLI resol
    (`resolve_profile`: `--profile` o el de producte).
    """
    from types import SimpleNamespace

    from app.core.errors import ResumeNotAllowedError
    from app.models.requirements import RequirementExtractionRun
    from app.services.requirement_analysis import (
        plan_resume,
        restrict_resume,
        run_is_superseded,
    )

    current = await analysis_input(session, tender.id)
    chunks = build_chunks(current.documents)
    source = await session.get(RequirementExtractionRun, run_id)
    try:
        plan = plan_resume(
            source,
            tender_id=tender.id,
            fingerprint=current.fingerprint,
            chunks=chunks,
            provider=SimpleNamespace(name=provider_name, model=model),
            profile=profile,
            superseded=source is not None and await run_is_superseded(session, source),
        )
    except ResumeNotAllowedError as exc:
        reason = (exc.details or {}).get("reason", "resume_refused")
        raise ProbeError(f"La represa es refusa ({reason}): {exc}") from None
    send = plan.send
    if only_raw is not None:
        # La mateixa regla que aplicara la canonada al run pagat.
        try:
            wanted = frozenset(parse_chunk_selection(only_raw, chunks))
            send = restrict_resume(plan, wanted)
        except (ProbeError, ValueError) as exc:
            raise ProbeError(f"--resume-chunks: {exc}") from None
    return ResumePreview(
        source_run_id=str(run_id),
        source_status=str(source.status),
        reused=sorted(plan.reused),
        send=sorted(send),
        pending=sorted(plan.send - send),
    )


def render_resume(preview: ResumePreview) -> str:
    lines = [
        f"REPRESA de {preview.source_run_id} ({preview.source_status}):",
        f"  reaprofitats ({len(preview.reused)}): {preview.reused}",
        f"  a enviar ({len(preview.send)}): {preview.send}",
    ]
    if preview.pending:
        lines.append(
            f"  pendents, no s'envien ara ({len(preview.pending)}): {preview.pending}"
        )
    lines.append(
        f"  crides al proveidor previstes: {preview.provider_calls} (sense reintents)"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Pla                                                                          #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ChunkPlan:
    index: int
    document_type: str
    document_name: str
    pages: str
    chars: int


@dataclass(slots=True)
class ProbePlan:
    database: str
    alembic_revision: str
    tender_id: str
    external_id: str
    expediente: str
    buyer: str | None
    profile: str
    extractor_version: str
    prompt_version: str
    schema_version: str
    provider: str
    model: str
    output_token_cap: int
    timeout_seconds: float = 0.0
    documents: list[dict[str, Any]] = field(default_factory=list)
    pages_total: int = 0
    chunks_total: int = 0
    selected_chunks: list[int] = field(default_factory=list)
    selected: list[ChunkPlan] = field(default_factory=list)
    located: dict[str, list[int]] = field(default_factory=dict)
    minimal_canary: list[int] | None = None
    max_provider_calls: int = 0
    #: L'historic de tokens del mateix prompt i model: la BASE de l'estimacio.
    history: Any = None
    provider_calls_made: int = 0
    token_estimate: dict[str, Any] = field(default_factory=dict)
    #: Documents que NO entren al pla, i per que (`analysis_input().skipped`).
    skipped: list[dict[str, Any]] = field(default_factory=list)
    #: QA agregada de les extraccions llegides; `unknown` = ningu ho ha mirat, NO bona.
    qa_status: str = "unknown"
    #: Nom del document -> `qa_status` de la seva extraccio vigent (`None` = sense QA).
    qa_by_document: dict[str, str | None] = field(default_factory=dict)


async def _qa_by_document(
    session: AsyncSession, documents: list[AnalyzableDocument]
) -> dict[str, str | None]:
    """El `qa_status` de l'extraccio vigent de cada plec del pla. Nomes llegeix."""
    ids = [uuid.UUID(document.extraction_id) for document in documents]
    statuses: dict[str, str | None] = {}
    if ids:
        rows = await session.execute(
            sa.select(TenderDocumentExtraction.id, TenderDocumentExtraction.qa_status).where(
                TenderDocumentExtraction.id.in_(ids)
            )
        )
        statuses = {str(extraction_id): status for extraction_id, status in rows.all()}
    return {document.document_name: statuses.get(document.extraction_id) for document in documents}


async def build_probe_plan(
    session: AsyncSession,
    tender: Tender,
    *,
    chunks_raw: str | None,
    locators: dict[str, tuple[str, int]],
    provider_name: str,
    model: str,
    database: str = "",
    alembic_revision: str = "",
    profile: profiles.ExtractorProfile,
) -> tuple[ProbePlan, list[Chunk]]:
    """Que s'enviaria. Nomes llegeix: cap proveidor, cap run, cap escriptura.

    `profile` no te cap valor per defecte: un sol perfil, el que la CLI resol
    (`resolve_profile`: `--profile` o el de producte).
    """
    current = await analysis_input(session, tender.id)
    if not current.documents:
        raise ProbeError("La licitacio no te cap plec analitzable.")
    outcome_docs = [
        doc.document_name for doc in current.documents
        if str(doc.document_type) in OUTCOME_DOCUMENT_TYPES
    ]
    if outcome_docs:
        raise ProbeError(
            f"El pla inclouria documents d'adjudicacio ({len(outcome_docs)}): l'entrada no "
            "seria cega. S'atura."
        )
    chunks = build_chunks(current.documents)
    selected = parse_chunk_selection(chunks_raw, chunks)
    by_index = {chunk.index: chunk for chunk in chunks}
    located = locate(chunks, locators)
    qa_by_document = await _qa_by_document(session, current.documents)

    history = await usage_history(session, prompt_version=profile.prompt_version, model=model)

    plan = ProbePlan(
        database=database,
        alembic_revision=alembic_revision,
        tender_id=str(tender.id),
        external_id=tender.external_id,
        expediente=tender.expediente_number or "",
        buyer=tender.buyer.name if tender.buyer is not None else None,
        profile=profile.name,
        extractor_version=profile.extractor_version,
        prompt_version=profile.prompt_version,
        schema_version=profile.schema_version,
        provider=provider_name,
        model=model,
        output_token_cap=settings.LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS,
        timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        documents=[
            {"type": str(doc.document_type), "name": doc.document_name, "pages": doc.page_count}
            for doc in current.documents
        ],
        pages_total=sum(doc.page_count for doc in current.documents),
        chunks_total=len(chunks),
        selected_chunks=selected,
        selected=[
            ChunkPlan(
                index=index,
                document_type=str(by_index[index].document.document_type),
                document_name=by_index[index].document.document_name,
                pages=_page_range(by_index[index]),
                chars=sum(len(ref.text or "") for ref in by_index[index].pages),
            )
            for index in selected
        ],
        located=located,
        minimal_canary=minimal_cover(located) if located else None,
        # Sense reintents (`PAID_RUN_MAX_RETRIES = 0`): una crida per chunk.
        max_provider_calls=len(selected),
        token_estimate=estimate_tokens(history, len(selected), profile.prompt_version, model),
        history=history,
        skipped=[{"name": s.document_name, "reason": s.reason} for s in current.skipped],
        qa_status=combine_status(list(qa_by_document.values())).value,
        qa_by_document=qa_by_document,
    )
    return plan, chunks


def estimate_tokens(history: Any, calls: int, prompt_version: str, model: str) -> dict[str, Any]:
    """Tokens previstos per a `calls` crides, amb la mitjana per peticio de l'historic.

    `calls` son les crides que ES FARAN (despres de la represa i de la seleccio),
    i `history_requests` son les peticions PASSADES que fan de mostra. Son dues
    xifres diferents i es diuen per separat: confondre-les era el defecte del
    canari (s'estimava per als 22 chunks encara que se n'enviessin 1 o 19).
    """
    if history is None or history.input_per_request is None or history.output_per_request is None:
        return {
            "available": False,
            "calls": calls,
            "reason": f"sense historic de {prompt_version} amb {model}",
        }
    return {
        "available": True,
        "calls": calls,
        "history_requests": history.requests,
        "prompt_version": prompt_version,
        "input_tokens": round(history.input_per_request * calls),
        "output_tokens": round(history.output_per_request * calls),
    }


def _page_range(chunk: Chunk) -> str:
    numbers = [ref.page_number for ref in chunk.pages]
    return f"p{min(numbers)}-{max(numbers)}" if numbers else "-"


def render_plan(plan: ProbePlan) -> str:
    lines = [
        f"PLA {plan.profile} (no s'ha construit ni cridat cap proveidor) · base "
        f"{plan.database!r} a {plan.alembic_revision}",
        f"Licitacio {plan.tender_id} · expedient {plan.expediente!r} · {plan.buyer!r}",
        f"  external_id: {plan.external_id}",
        f"Perfil {plan.profile}: {plan.extractor_version} / {plan.prompt_version} / "
        f"{plan.schema_version}",
        f"Proveidor previst: {plan.provider} · model {plan.model} · limit de sortida "
        f"{plan.output_token_cap} tokens · temps maxim per crida {plan.timeout_seconds:g} s",
        f"Documents: {len(plan.documents)} · pagines: {plan.pages_total} · chunks: "
        f"{plan.chunks_total} · omesos: {len(plan.skipped)}",
    ]
    lines += [
        f"    {d['type']:<22} {d['pages']:>4} pag  {d['name']}  "
        f"(QA {plan.qa_by_document.get(d['name']) or 'unknown'})"
        for d in plan.documents
    ]
    lines += [f"    omes: {s['name']} ({s['reason']})" for s in plan.skipped]
    lines.append(
        f"QA: {plan.qa_status} (agregat de les extraccions llegides; unknown = ningu ho ha "
        "mirat, no vol dir bona)"
    )
    if plan.located:
        lines.append("Pagines localitzades (chunks que les contenen):")
        lines += [f"    {label}: {indexes}" for label, indexes in plan.located.items()]
        lines.append(f"Canari minim que les cobreix: {plan.minimal_canary}")
    lines.append(f"Chunks seleccionats ({len(plan.selected_chunks)}): {plan.selected_chunks}")
    lines += [
        f"    #{c.index:<3} {c.document_type:<22} {c.pages:<9} {c.chars:>7} car"
        for c in plan.selected
    ]
    lines.append(
        f"Crides maximes al proveidor: {plan.max_provider_calls} (sense reintents) · "
        f"crides fetes: {plan.provider_calls_made}"
    )
    e = plan.token_estimate
    if e.get("available"):
        lines.append(
            f"ESTIMACIO per a {e['calls']} crida(es) prevista(es): ~{e['input_tokens']} tokens "
            f"d'entrada, ~{e['output_tokens']} de sortida "
            f"(base: {e['history_requests']} peticio(ns) historica(ues) de {e['prompt_version']})."
        )
    else:
        lines.append(
            f"Estimacio de tokens per a {e.get('calls', 0)} crida(es): no disponible "
            f"({e['reason']})."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Resultat d'un run pagat                                                      #
# --------------------------------------------------------------------------- #


NOT_SELECTED = "not_selected"


def canary_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Qualitat del que S'HA ENVIAT. Els no seleccionats no son errors del model."""
    attempted = [r for r in rows if r["error_code"] != NOT_SELECTED]
    return {
        "chunks_total": len(rows),
        "attempted": len(attempted),
        "succeeded": sum(1 for r in attempted if r["status"] == "completed"),
        "actual_failures": sum(1 for r in attempted if r["status"] != "completed"),
        "not_selected": len(rows) - len(attempted),
        "schema_invalid": sum(1 for r in attempted if r["schema_invalid"]),
        "output_truncated": sum(1 for r in attempted if r["output_truncated"]),
        "items_rejected_schema": sum(r.get("items_rejected_schema", 0) for r in attempted),
        "claims_rejected_schema": sum(r.get("claims_rejected_schema", 0) for r in attempted),
        "items_salvaged": sum(r.get("items_salvaged", 0) for r in attempted),
        "root_fields_rejected_schema": sum(
            r.get("root_fields_rejected_schema", 0) for r in attempted
        ),
        "auxiliary_rejected_schema": sum(
            r.get("auxiliary_rejected_schema", 0) for r in attempted
        ),
    }


def chunk_report(run: Any) -> list[dict[str, Any]]:
    """Per chunk: estat, codi, truncament, tokens i rebutjos per motiu. Nomes codis."""
    rows = []
    for entry in (run.chunk_coverage or {}).get("chunks", []):
        rejections: dict[str, int] = {}
        items_rejected = claims_rejected = root_rejected = auxiliary_rejected = 0
        salvaged: set[Any] = set()
        for rejection in entry.get("rejections") or []:
            reason = str(rejection.get("reason"))
            rejections[reason] = rejections.get(reason, 0) + 1
            if reason == "schema_invalid" and rejection.get("stage") == "root":
                root_rejected += 1
            elif reason == "schema_invalid" and rejection.get("stage") == "auxiliary":
                auxiliary_rejected += 1
            elif reason == "schema_invalid" and rejection.get("stage") == "schema":
                items_rejected += 1
            elif reason == "schema_invalid" and rejection.get("stage") == "claim":
                claims_rejected += 1
                salvaged.add((rejection.get("item"), rejection.get("index")))
        rows.append({
            "index": entry.get("index"),
            "status": entry.get("status"),
            "error_code": entry.get("error"),
            "origin": entry.get("origin"),
            "schema_invalid": entry.get("error") == "schema_invalid",
            "output_truncated": entry.get("error") == "output_truncated"
            or entry.get("stop_reason") == "max_tokens",
            "stop_reason": entry.get("stop_reason"),
            "input_tokens": entry.get("input_tokens"),
            "output_tokens": entry.get("output_tokens"),
            "normalised": entry.get("normalised"),
            "rejections": rejections,
            "items_rejected_schema": items_rejected,
            "claims_rejected_schema": claims_rejected,
            "items_salvaged": len(salvaged),
            "root_fields_rejected_schema": root_rejected,
            "auxiliary_rejected_schema": auxiliary_rejected,
        })
    return rows


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/v3_tender_probe.py",
        description="Extraction Contract v3 sobre una sola licitacio, en una base "
        "d'usar i llencar.",
    )
    parser.add_argument("--database-url", default=None,
                        help="DSN SENSE contrasenya d'una copia licitacions_v3_probe_* al "
                        "port 5433 (per defecte, DATABASE_URL de la sessio; la contrasenya, "
                        "a PGPASSWORD)")
    parser.add_argument("--expediente", required=True, help="numero d'expedient EXACTE")
    parser.add_argument("--tender-id", type=uuid.UUID, default=None,
                        help="id de la licitacio, si l'expedient no es unic")
    parser.add_argument("--expect-buyer", default=None,
                        help="text que ha de contenir el nom de l'organisme (comprovacio)")
    parser.add_argument("--chunks", default=None,
                        help="indexs separats per comes; per defecte, tots")
    parser.add_argument("--resume-from-run", type=uuid.UUID, default=None,
                        help="repren aquest run parcial: reaprofita els chunks bons i envia "
                        "nomes la resta (no es combina amb --chunks)")
    parser.add_argument("--resume-chunks", default=None,
                        help="nomes amb --resume-from-run: envia NOMES aquests chunks "
                        "pendents; la resta segueixen pendents. Mai un chunk ja completat")
    parser.add_argument("--locate", action="append", default=[],
                        help="ETIQUETA=tipus_document:pagina per trobar-ne el chunk (repetible)")
    parser.add_argument("--provider", default=None, help="proveidor (per defecte, ENV)")
    parser.add_argument("--profile", choices=PROFILE_CHOICES, default=None,
                        help="perfil d'extraccio (per defecte, REQUIREMENTS_PRODUCT_PROFILE; "
                        "la v3 es la interna de la sonda)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true",
                      help="nomes el pla: cap proveidor, cap crida, cap escriptura")
    mode.add_argument("--paid-run", action="store_true",
                      help="confirmacio explicita: crida el proveidor real i TE COST")
    return parser


async def run(args: argparse.Namespace) -> int:
    try:
        if args.resume_from_run is not None and args.chunks is not None:
            raise ProbeError("--resume-from-run i --chunks no es combinen: la represa ja "
                             "decideix que s'envia (per triar, --resume-chunks).")
        if args.resume_chunks is not None and args.resume_from_run is None:
            raise ProbeError("--resume-chunks nomes serveix amb --resume-from-run.")
        url = validate_probe_database(resolve_database_dsn(args.database_url))
        locators = parse_locators(args.locate)
    except ProbeError as exc:
        print(f"No es pot executar: {exc}")
        return EXIT_CONFIG

    provider = None
    if args.paid_run:
        # El proveidor es resol ABANS de connectar: un proveidor simulat s'atura
        # aqui, sense tocar cap base.
        from app.evaluation.benchmark import (
            PAID_RUN_MAX_RETRIES,
            BenchmarkConfigError,
            resolve_provider,
        )

        try:
            provider = resolve_provider(args.provider, max_retries=PAID_RUN_MAX_RETRIES)
            require_session_scoped_key()
        except (BenchmarkConfigError, ProbeError) as exc:
            print(f"No es pot executar: {exc}")
            return EXIT_CONFIG
    if provider is not None:
        provider_name, model = provider.name, provider.model
    else:
        provider_name = (args.provider or settings.LLM_PROVIDER or "fake").strip().lower()
        model = settings.LLM_MODEL

    engine = create_async_engine(url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        return await _run_with_session(
            args, url, factory, provider, provider_name, model, locators
        )
    except Exception as exc:  # noqa: BLE001 - l'error surt, pero sense credencials
        print(f"Error ({type(exc).__name__}): {redact(str(exc), url)}")
        return 1
    finally:
        await engine.dispose()


def redact(text: str, url: URL) -> str:
    """Treu la contrasenya i la DSN sencera de qualsevol text que s'hagi d'imprimir."""
    if url.password:
        text = text.replace(str(url.password), "***")
    return text.replace(url.render_as_string(hide_password=False), url.render_as_string())


async def _run_with_session(
    args: argparse.Namespace,
    url: URL,
    factory: async_sessionmaker,
    provider: Any,
    provider_name: str,
    model: str,
    locators: dict[str, tuple[str, int]],
) -> int:
    async with factory() as session:
        if args.plan_only and session.bind.dialect.name == "postgresql":
            await session.execute(sa.text("SET TRANSACTION READ ONLY"))
        try:
            profile = resolve_profile(getattr(args, "profile", None))
            revision = await check_database(session, url)
            tender = await resolve_single_tender(
                session, expediente=args.expediente, tender_id=args.tender_id,
                expect_buyer=args.expect_buyer,
            )
            plan, _ = await build_probe_plan(
                session, tender, chunks_raw=args.chunks, locators=locators,
                provider_name=provider_name, model=model,
                database=url.database, alembic_revision=revision, profile=profile,
            )
            resume = None
            if args.resume_from_run is not None:
                resume = await preview_resume(
                    session, tender, args.resume_from_run,
                    provider_name=provider_name, model=model, only_raw=args.resume_chunks,
                    profile=profile,
                )
                plan.selected_chunks = resume.send
                plan.selected = [c for c in plan.selected if c.index in set(resume.send)]
                plan.max_provider_calls = resume.provider_calls
                plan.token_estimate = estimate_tokens(
                    plan.history, resume.provider_calls, plan.prompt_version, model
                )
        except ProbeError as exc:
            await session.rollback()
            print(f"No es pot executar: {exc}")
            return EXIT_UNSAFE_INPUT

        print(render_plan(plan))
        if resume is not None:
            print(render_resume(resume))
        if args.plan_only:
            await session.rollback()
            print("\nNO s'ha construit ni cridat cap proveidor i no s'ha escrit res.")
            return EXIT_OK

        from app.core.errors import AnalysisNotAllowedError
        from app.services.requirement_analysis import analyze_tender_requirements

        # La canonada aplica la porta unica (3B-2 §6.7, `llm_budget.provider_gate`)
        # abans de reservar el run i de cap crida: model pinat, preu, pressupost no
        # esgotat i estimacio dins del cap (`canary` amb --chunks de com a molt 2).
        try:
            if resume is not None:
                # La represa de la canonada, sense cap mecanisme propi: torna a fer
                # `plan_resume` i les seves comprovacions abans de cap crida.
                outcome = await analyze_tender_requirements(
                    session, tender, provider=provider, profile=profile,
                    resume_from_run_id=args.resume_from_run,
                    # Represa selectiva: la canonada torna a aplicar `restrict_resume`.
                    only_chunks=(
                        frozenset(resume.send) if args.resume_chunks is not None else None
                    ),
                )
            else:
                selected = frozenset(plan.selected_chunks)
                outcome = await analyze_tender_requirements(
                    session, tender, provider=provider, profile=profile, force=True,
                    only_chunks=None if len(selected) == plan.chunks_total else selected,
                )
        except AnalysisNotAllowedError as exc:
            await session.rollback()
            print(f"No es pot executar: {exc.code} - {exc.message}")
            print("NO s'ha cridat cap proveidor i no s'ha reservat cap run.")
            return EXIT_NOT_ALLOWED
        result = outcome.run
        rows = chunk_report(result)
        summary = {
            "run_id": str(result.id),
            "status": str(result.status),
            "error_code": result.error_code,
            "canary": canary_summary(rows),
            "requirements_extracted": result.requirements_extracted,
            "requirements_rejected": result.requirements_rejected,
            "award_criteria_extracted": result.award_criteria_extracted,
            "evidence_rejected": result.evidence_rejected,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "chunks": rows,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        # L'id, sol a la seva linia, per copiar-lo a --resume-from-run.
        print(f"\nRUN_ID={result.id}")
        if str(result.status) == "partial":
            # Les dues passes, cadascuna amb el seu mode explicit: primer el pla
            # (sense cost) i nomes despres el run pagat.
            print("Per continuar aquesta analisi:")
            print(f"  1) previsualitzar (sense cost): --resume-from-run {result.id} --plan-only")
            print(f"  2) executar (TE COST):          --resume-from-run {result.id} --paid-run")
        return EXIT_OK


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())

