"""Proveidor real, model pinat, preus i caps de despesa d'LLM (3B-2 §6.1.1, §6.7).

L'UNIC lloc on viuen aquestes regles. La quota (`analysis_planning.build_preview`)
i l'analisi (`requirement_analysis.analyze_tender_requirements`) en pregunten aqui,
i qualsevol cami que arribi al proveidor (worker, sonda `--paid-run`, benchmark)
passa per l'analisi: cap d'ells no les reescriu.

* **Porta** (`provider_gate`), ABANS de reservar el run i de cap crida: una simulacio
  (`fake`/`fixture`) nomes amb `ENVIRONMENT=test` o `ALLOW_SIMULATED_ANALYSIS=true`
  (`provider_not_real`); un proveidor real exigeix el model pinat a un ID exacte de
  `LLM_ALLOWED_MODELS` (`model_not_pinned`), un preu per a aquest model exacte a
  `LLM_MODEL_PRICES` (`price_unknown`), un pressupost no esgotat i que hi capiga
  l'estimacio (`budget_exhausted`), i que la cadena de represa mes l'estimacio
  capiga al cap del seu scope (`budget_exceeded`).
* **Cost** (`chunk_cost`): `Usage` x preus, en `Decimal` arrodonit a 6 decimals
  (`ROUND_HALF_UP`), el mateix arrodoniment per al llibre, el run i la quota.
* **Estimacio** (`estimate_tokens`): l'historic de tokens per peticio del mateix
  prompt, proveidor i model; sense, una cota per caracters (`CHARS_PER_TOKEN`) i el
  sostre de sortida per chunk.
* **Scopes** (`budget_scope_for`): un canari (`only_chunks` de com a molt
  `CANARY_MAX_CHUNKS`) es `canary`; la resta, `run`. Cada linia del llibre en porta
  l'scope. El cap d'un run es compta sobre TOTA la cadena de represa (el run i els
  runs dels quals es repren), no fila a fila.
* **Caps** (`cap_usd`, `spent_usd`): el cap vigent d'un scope es el de l'ultima
  autoritzacio del scope; sense cap, el de configuracio.
* **Esgotat** (`budget_tripped`): un run aturat per `budget_exceeded` desa quin scope
  va excedir (`provider_metadata.budget_exceeded_scope`). Fins que l'operador no
  registra una autoritzacio POSTERIOR d'aquell scope o de `pilot_total`, cap analisi
  nova que hagi de gastar no es pot demanar ni executar (`budget_exhausted`).

Global: res d'aqui no rep ni escriu cap empresa. L'autoritzacio es de l'operador de
plataforma, amb auditoria `company_id=None`. La quota d'una empresa nomes en rep
booleans (`QuoteBudget.snapshot`): cap import global.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.profiles import ExtractorProfile
from app.core.config import settings
from app.core.errors import AnalysisNotAllowedError, AppError
from app.models.enums import (
    AnalysisRunStatus,
    AuditAction,
    LlmBudgetScope,
    RequirementRunBasis,
)
from app.models.llm_budget import LlmBudgetAuthorization, LlmSpendLedger
from app.models.requirements import RequirementExtractionRun
from app.services import audit
from app.services.requirement_chunking import Chunk, render_chunk

#: Codis de fallada (els mateixos a la quota, al handler, a la sonda i al run).
PROVIDER_NOT_REAL = "provider_not_real"
MODEL_NOT_PINNED = "model_not_pinned"
PRICE_UNKNOWN = "price_unknown"
BUDGET_EXHAUSTED = "budget_exhausted"
BUDGET_EXCEEDED = "budget_exceeded"

#: Clau de `provider_metadata` d'un run aturat amb l'scope que va excedir.
EXCEEDED_SCOPE_KEY = "budget_exceeded_scope"
#: Clau de `provider_metadata` d'un run represa: el run del qual es repren.
RESUMED_FROM_KEY = "resumed_from_run_id"

#: Entitat de l'auditoria d'una autoritzacio.
AUDIT_ENTITY = "llm_budget_authorization"

USD_QUANTUM = Decimal("0.000001")
TOKENS_PER_MILLION = Decimal(1_000_000)

#: Un canari es una prova de com a molt aquests chunks (`--chunks` de la sonda).
CANARY_MAX_CHUNKS = 2

#: Caracters per token per a la cota sense historic (3B-2 §6.7: 3,3 car./token).
CHARS_PER_TOKEN = Decimal("3.3")
ESTIMATE_FROM_HISTORY = "history"
ESTIMATE_FROM_CHARACTERS = "characters"

_CONFIG_CAPS = {
    LlmBudgetScope.CANARY: "LLM_BUDGET_CANARY_USD",
    LlmBudgetScope.RUN: "LLM_BUDGET_RUN_USD",
    LlmBudgetScope.PILOT_TOTAL: "LLM_BUDGET_PILOT_TOTAL_USD",
}


class ProviderIdentity(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...


class BudgetExceededError(AppError):
    """Un cap de despesa ja s'ha superat: no s'envia res mes. `scope` diu quin."""

    status_code = 409
    code = BUDGET_EXCEEDED

    def __init__(
        self, message: str, *, scope: LlmBudgetScope, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, details=details)
        self.scope = scope


def usd(value: Any) -> Decimal:
    """Un import en dolars, amb el mateix arrodoniment a tot arreu."""
    return Decimal(str(value)).quantize(USD_QUANTUM, rounding=ROUND_HALF_UP)


def _refuse(code: str, message: str, **details: Any) -> AnalysisNotAllowedError:
    return AnalysisNotAllowedError(message, blocking_reason=code, details=details or None)


# --------------------------------------------------------------------------- #
# Proveidor, model i preu                                                       #
# --------------------------------------------------------------------------- #


def simulation_allowed() -> bool:
    """Una simulacio es pot demanar a les proves o amb la bandera explicita."""
    return settings.ENVIRONMENT.strip().lower() == "test" or settings.ALLOW_SIMULATED_ANALYSIS


def provider_basis(provider: ProviderIdentity) -> RequirementRunBasis:
    return RequirementRunBasis.for_provider(provider.name)


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Dolars per milio de tokens."""

    input_per_million: Decimal
    output_per_million: Decimal


def model_price(model: str, prices: Mapping[str, Any] | None = None) -> ModelPrice | None:
    """El preu del model EXACTE (§13.5: pinat a un ID exacte). Cap prefix.

    Un preu mal format (no numeric o negatiu) es tracta com absent: no es calcula
    cap cost amb un preu que no es pot llegir.
    """
    raw = (settings.LLM_MODEL_PRICES if prices is None else prices).get(model)
    if not isinstance(raw, Mapping):
        return None
    try:
        price = ModelPrice(
            input_per_million=Decimal(str(raw["input"])),
            output_per_million=Decimal(str(raw["output"])),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    if price.input_per_million < 0 or price.output_per_million < 0:
        return None
    return price


def chunk_cost(
    model: str, *, input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    """`Usage` x preus. `None` si el model no te preu."""
    price = model_price(model)
    if price is None:
        return None
    total = (
        Decimal(input_tokens or 0) * price.input_per_million
        + Decimal(output_tokens or 0) * price.output_per_million
    ) / TOKENS_PER_MILLION
    return usd(total)


def provider_failures(provider: ProviderIdentity) -> list[str]:
    """Que falla del proveidor per poder fer una analisi, en ordre de prioritat.

    Simulacio: nomes `provider_not_real` (si no es permet). Real: `model_not_pinned` i
    `price_unknown`, tots dos si escau. Cap altra base (`unconfigured`, `gold`) no es
    un proveidor per demanar res.
    """
    basis = provider_basis(provider)
    if basis is RequirementRunBasis.SIMULATION:
        return [] if simulation_allowed() else [PROVIDER_NOT_REAL]
    if basis is not RequirementRunBasis.REAL:
        return [PROVIDER_NOT_REAL]
    failures: list[str] = []
    if provider.model not in settings.LLM_ALLOWED_MODELS:
        failures.append(MODEL_NOT_PINNED)
    if model_price(provider.model) is None:
        failures.append(PRICE_UNKNOWN)
    return failures


# --------------------------------------------------------------------------- #
# Estimacio                                                                     #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class UsageHistory:
    """Tokens per peticio observats en runs anteriors."""

    runs: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def input_per_request(self) -> float | None:
        return self.input_tokens / self.requests if self.requests else None

    @property
    def output_per_request(self) -> float | None:
        return self.output_tokens / self.requests if self.requests else None


def _request_count(run: RequirementExtractionRun) -> int:
    """Peticions que va respondre el proveidor en aquell run, amb la millor font que hi hagi."""
    coverage = run.chunk_coverage or {}
    chunks = coverage.get("chunks") if isinstance(coverage, dict) else None
    if isinstance(chunks, list):
        return sum(1 for chunk in chunks if chunk.get("input_tokens") is not None)
    metadata = run.provider_metadata or {}
    request_ids = metadata.get("request_ids") if isinstance(metadata, dict) else None
    if isinstance(request_ids, list) and request_ids:
        return len(request_ids)
    return run.chunks_completed or 0


async def usage_history(
    session: AsyncSession,
    *,
    prompt_version: str,
    model: str,
    provider: str | None = None,
) -> UsageHistory:
    """Historic de tokens del mateix prompt i model (i proveidor, si es dona).

    Filtrar pel proveidor evita que els runs del proveidor fals de
    desenvolupament, amb tokens simbolics, contaminin l'estimacio real.
    """
    query = sa.select(RequirementExtractionRun).where(
        RequirementExtractionRun.prompt_version == prompt_version,
        RequirementExtractionRun.model == model,
        RequirementExtractionRun.input_tokens.is_not(None),
    )
    if provider is not None:
        query = query.where(RequirementExtractionRun.provider == provider)
    history = UsageHistory()
    for run in (await session.scalars(query)).all():
        requests = _request_count(run)
        if not requests:
            continue
        history.runs += 1
        history.requests += requests
        history.input_tokens += run.input_tokens or 0
        history.output_tokens += run.output_tokens or 0
    return history


@dataclass(frozen=True, slots=True)
class TokenGuess:
    input_tokens: int
    output_tokens: int
    #: `history` o `characters`.
    basis: str


def character_tokens(chunks: Iterable[Chunk], profile: ExtractorProfile) -> tuple[int, int]:
    """Cota sense historic: caracters del que s'enviaria / 3,3 i el sostre de sortida."""
    chunks = list(chunks)
    input_tokens = 0
    for chunk in chunks:
        chars = len(profile.system_prompt) + len(profile.build_user_message(render_chunk(chunk)))
        input_tokens += math.ceil(Decimal(chars) / CHARS_PER_TOKEN)
    return input_tokens, settings.LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS * len(chunks)


async def estimate_tokens(
    session: AsyncSession,
    *,
    chunks: list[Chunk],
    profile: ExtractorProfile,
    provider: ProviderIdentity,
) -> TokenGuess:
    """Tokens que costaria enviar `chunks`: historic si n'hi ha; si no, la cota."""
    history = await usage_history(
        session, prompt_version=profile.prompt_version, model=provider.model, provider=provider.name
    )
    if history.input_per_request is not None and history.output_per_request is not None:
        return TokenGuess(
            input_tokens=round(history.input_per_request * len(chunks)),
            output_tokens=round(history.output_per_request * len(chunks)),
            basis=ESTIMATE_FROM_HISTORY,
        )
    input_tokens, output_tokens = character_tokens(chunks, profile)
    return TokenGuess(input_tokens, output_tokens, ESTIMATE_FROM_CHARACTERS)


# --------------------------------------------------------------------------- #
# Scopes, caps i gastat                                                         #
# --------------------------------------------------------------------------- #


def budget_scope_for(only_chunks: frozenset[int] | None) -> LlmBudgetScope:
    """`canary` per a una prova de com a molt `CANARY_MAX_CHUNKS` chunks; si no, `run`."""
    if only_chunks is not None and len(only_chunks) <= CANARY_MAX_CHUNKS:
        return LlmBudgetScope.CANARY
    return LlmBudgetScope.RUN


@dataclass(frozen=True, slots=True)
class CapInfo:
    scope: LlmBudgetScope
    cap_usd: Decimal
    #: `config` o `authorization`.
    source: str
    authorization_id: uuid.UUID | None = None
    authorized_at: datetime | None = None


async def cap_usd(session: AsyncSession, scope: LlmBudgetScope) -> CapInfo:
    """El cap vigent: l'ultima autoritzacio del scope o, si no n'hi ha, la configuracio."""
    latest = await session.scalar(
        sa.select(LlmBudgetAuthorization)
        .where(LlmBudgetAuthorization.scope == scope)
        .order_by(LlmBudgetAuthorization.created_at.desc(), LlmBudgetAuthorization.id.desc())
        .limit(1)
    )
    if latest is not None:
        return CapInfo(
            scope=scope,
            cap_usd=usd(latest.cap_usd),
            source="authorization",
            authorization_id=latest.id,
            authorized_at=latest.created_at,
        )
    return CapInfo(
        scope=scope, cap_usd=usd(getattr(settings, _CONFIG_CAPS[scope])), source="config"
    )


async def spent_usd(
    session: AsyncSession,
    scope: LlmBudgetScope = LlmBudgetScope.PILOT_TOTAL,
    *,
    run_ids: Iterable[uuid.UUID] | None = None,
) -> Decimal:
    """Gastat segons el llibre. `pilot_total`: tot. `run`/`canary`: el d'aquests runs
    (una cadena de represa sencera: el cap es de l'analisi, no de cada fila)."""
    query = sa.select(sa.func.coalesce(sa.func.sum(LlmSpendLedger.cost_usd), 0))
    if scope is not LlmBudgetScope.PILOT_TOTAL:
        ids = list(run_ids or [])
        if not ids:
            return usd(0)
        query = query.where(LlmSpendLedger.run_id.in_(ids))
    return usd(await session.scalar(query) or 0)


async def resume_chain(
    session: AsyncSession, source: RequirementExtractionRun | None
) -> list[uuid.UUID]:
    """El run d'origen d'una represa i, enrere, els runs dels quals aquell es reprenia."""
    chain: list[uuid.UUID] = []
    current = source
    while current is not None and current.id not in chain:
        chain.append(current.id)
        previous = (current.provider_metadata or {}).get(RESUMED_FROM_KEY)
        if not previous:
            break
        current = await session.get(RequirementExtractionRun, uuid.UUID(str(previous)))
    return chain


async def max_run_spent_usd(session: AsyncSession, scope: LlmBudgetScope) -> Decimal:
    """El que mes ha gastat un sol run amb linies d'aquest scope."""
    per_run = (
        sa.select(sa.func.sum(LlmSpendLedger.cost_usd).label("spent"))
        .where(LlmSpendLedger.run_id.is_not(None), LlmSpendLedger.budget_scope == scope)
        .group_by(LlmSpendLedger.run_id)
        .subquery()
    )
    return usd(await session.scalar(sa.select(sa.func.max(per_run.c.spent))) or 0)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _stopped_scope(run_metadata: Mapping[str, Any] | None) -> LlmBudgetScope:
    """L'scope que va excedir un run aturat. Un run sense la dada es tracta com a total."""
    raw = (run_metadata or {}).get(EXCEEDED_SCOPE_KEY)
    try:
        return LlmBudgetScope(raw)
    except ValueError:
        return LlmBudgetScope.PILOT_TOTAL


async def budget_tripped(session: AsyncSession) -> bool:
    """Cert si algun run aturat per pressupost no ha estat reobert.

    Reobre una aturada nomes una autoritzacio POSTERIOR del mateix scope o de
    `pilot_total`: una autoritzacio de canari no reobre un cap de run excedit.
    """
    stops = (
        await session.execute(
            sa.select(
                RequirementExtractionRun.completed_at, RequirementExtractionRun.provider_metadata
            ).where(
                RequirementExtractionRun.error_code == BUDGET_EXCEEDED,
                RequirementExtractionRun.status.in_(
                    [AnalysisRunStatus.PARTIAL, AnalysisRunStatus.FAILED]
                ),
            )
        )
    ).all()
    if not stops:
        return False
    latest = {
        scope: _aware(created)
        for scope, created in (
            await session.execute(
                sa.select(
                    LlmBudgetAuthorization.scope, sa.func.max(LlmBudgetAuthorization.created_at)
                ).group_by(LlmBudgetAuthorization.scope)
            )
        ).all()
    }
    for completed_at, metadata in stops:
        stopped = _aware(completed_at)
        scope = _stopped_scope(metadata)
        reopeners = {scope, LlmBudgetScope.PILOT_TOTAL}
        if not any(
            latest.get(candidate) is not None and (stopped is None or latest[candidate] > stopped)
            for candidate in reopeners
        ):
            return True
    return False


@dataclass(frozen=True, slots=True)
class QuoteBudget:
    """El pressupost tal com el veu una quota d'empresa: nomes booleans."""

    failures: list[str]
    snapshot: dict[str, Any]


async def quote_budget(
    session: AsyncSession,
    *,
    estimated_cost: Decimal | None,
    prior_run_ids: Iterable[uuid.UUID] = (),
) -> QuoteBudget:
    """§6.7, abans de confirmar: estimat (+ cadena de represa) <= cap de run i gastat +
    estimat <= cap total; un pressupost esgotat tambe bloqueja.

    La quota es d'una empresa: no s'hi desa ni s'hi ensenya cap import global (gastat
    de la plataforma, caps), nomes si hi cap (`within_*`) i si esta esgotat.
    """
    estimate = usd(estimated_cost or 0)
    run_cap = await cap_usd(session, LlmBudgetScope.RUN)
    total_cap = await cap_usd(session, LlmBudgetScope.PILOT_TOTAL)
    total = await spent_usd(session, LlmBudgetScope.PILOT_TOTAL)
    chain = await spent_usd(session, LlmBudgetScope.RUN, run_ids=prior_run_ids)
    tripped = await budget_tripped(session)
    within_total = total + estimate <= total_cap.cap_usd
    within_run = chain + estimate <= run_cap.cap_usd
    failures: list[str] = []
    if tripped or not within_total:
        failures.append(BUDGET_EXHAUSTED)
    if not within_run:
        failures.append(BUDGET_EXCEEDED)
    return QuoteBudget(
        failures=failures,
        snapshot={
            "within_run_cap": within_run,
            "within_total_cap": within_total,
            "tripped": tripped,
        },
    )


async def next_chunk_estimate(
    session: AsyncSession,
    *,
    run_ids: Iterable[uuid.UUID],
    chunk: Chunk,
    profile: ExtractorProfile,
    model: str,
) -> Decimal:
    """Cost estimat del chunk SEGUENT, conservador: el mes gran entre la cota per
    caracters d'aquest chunk (amb el sostre de sortida) i el chunk mes car que la cadena
    ja ha pagat al llibre. Sense preu, 0 (la porta ja ho hauria refusat)."""
    input_tokens, output_tokens = character_tokens([chunk], profile)
    by_characters = chunk_cost(model, input_tokens=input_tokens, output_tokens=output_tokens)
    ids = list(run_ids)
    observed = 0
    if ids:
        observed = (
            await session.scalar(
                sa.select(sa.func.max(LlmSpendLedger.cost_usd)).where(
                    LlmSpendLedger.run_id.in_(ids)
                )
            )
            or 0
        )
    return max(by_characters or usd(0), usd(observed))


async def assert_within_budget(
    session: AsyncSession,
    *,
    run_ids: Iterable[uuid.UUID],
    run_scope: LlmBudgetScope,
    next_chunk_usd: Decimal = Decimal(0),
) -> None:
    """§6.7, abans de cada chunk: gastat total + estimacio del chunk seguent <= cap
    total i gastat de la cadena del run + estimacio <= cap del seu scope (`canary` o
    `run`), de manera que cap run no supera mai el cap. Si no, `BudgetExceededError`
    amb l'scope excedit, abans d'enviar el chunk."""
    ids = list(run_ids)
    total_cap = await cap_usd(session, LlmBudgetScope.PILOT_TOTAL)
    total = await spent_usd(session, LlmBudgetScope.PILOT_TOTAL)
    run_cap = await cap_usd(session, run_scope)
    run_spent = await spent_usd(session, run_scope, run_ids=ids)
    details = {
        "total_spent_usd": float(total),
        "total_cap_usd": float(total_cap.cap_usd),
        "run_spent_usd": float(run_spent),
        "run_cap_usd": float(run_cap.cap_usd),
        "next_chunk_estimate_usd": float(next_chunk_usd),
    }
    if total + next_chunk_usd > total_cap.cap_usd:
        raise BudgetExceededError(
            "El proxim chunk superaria el cap total de despesa d'LLM: no s'envia.",
            scope=LlmBudgetScope.PILOT_TOTAL,
            details=details,
        )
    if run_spent + next_chunk_usd > run_cap.cap_usd:
        raise BudgetExceededError(
            f"El proxim chunk superaria el cap de despesa de {run_scope.value}: no s'envia.",
            scope=run_scope,
            details=details,
        )


async def provider_gate(
    session: AsyncSession,
    provider: ProviderIdentity,
    *,
    chunks: list[Chunk],
    profile: ExtractorProfile,
    run_scope: LlmBudgetScope,
    prior_run_ids: Iterable[uuid.UUID] = (),
) -> None:
    """La porta unica (§6.3 capa 1, §6.7), ABANS de reservar el run i de cap crida.

    Llanca `AnalysisNotAllowedError` amb el primer codi que falla. La fa servir
    l'analisi, i per ella el worker, la sonda i el benchmark.
    """
    failures = provider_failures(provider)
    if failures:
        raise _refuse(
            failures[0],
            "El proveidor o el model no estan autoritzats per a aquesta analisi.",
            failed=failures,
        )
    if provider_basis(provider) is not RequirementRunBasis.REAL:
        return
    guess = await estimate_tokens(session, chunks=chunks, profile=profile, provider=provider)
    estimate = chunk_cost(
        provider.model, input_tokens=guess.input_tokens, output_tokens=guess.output_tokens
    ) or usd(0)
    total_cap = await cap_usd(session, LlmBudgetScope.PILOT_TOTAL)
    total = await spent_usd(session, LlmBudgetScope.PILOT_TOTAL)
    if await budget_tripped(session) or total + estimate > total_cap.cap_usd:
        raise _refuse(
            BUDGET_EXHAUSTED,
            "El pressupost d'LLM esta esgotat fins a una autoritzacio nova.",
        )
    scope_cap = await cap_usd(session, run_scope)
    chain = await spent_usd(session, run_scope, run_ids=prior_run_ids)
    if chain + estimate > scope_cap.cap_usd:
        raise _refuse(
            BUDGET_EXCEEDED,
            f"L'estimacio no cap al cap de despesa de {run_scope.value}.",
            scope=run_scope.value,
            basis=guess.basis,
        )


# --------------------------------------------------------------------------- #
# Escriptures                                                                   #
# --------------------------------------------------------------------------- #


def record_chunk_spend(
    session: AsyncSession,
    *,
    run: RequirementExtractionRun,
    chunk_index: int,
    input_tokens: int | None,
    output_tokens: int | None,
    scope: LlmBudgetScope,
) -> Decimal:
    """Afegeix la linia del llibre d'un chunk respost (sense commit). Torna el cost.

    Un run real nomes arriba aqui si la porta n'ha trobat el preu: sense, es un error
    de programacio, no una despesa que es pugui deixar sense import.
    """
    cost = chunk_cost(run.model, input_tokens=input_tokens, output_tokens=output_tokens)
    if cost is None:
        raise RuntimeError(
            f"Sense preu per a {run.model!r}: la porta no ho havia de deixar passar."
        )
    session.add(
        LlmSpendLedger(
            run_id=run.id,
            chunk_index=chunk_index,
            provider=run.provider,
            model=run.model,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            cost_usd=cost,
            budget_scope=scope,
        )
    )
    return cost


async def authorize(
    session: AsyncSession,
    *,
    scope: LlmBudgetScope,
    cap_usd: Decimal,
    user_id: uuid.UUID | None,
    note: str,
) -> LlmBudgetAuthorization:
    """Registra un cap nou per a `scope` i en deixa auditoria global. Fa commit."""
    authorization = LlmBudgetAuthorization(
        scope=scope, cap_usd=usd(cap_usd), authorized_by=user_id, note=note.strip()
    )
    session.add(authorization)
    await session.flush()
    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=None,
        user_id=user_id,
        entity_type=AUDIT_ENTITY,
        entity_id=authorization.id,
        summary=f"scope={scope.value} cap_usd={authorization.cap_usd}",
    )
    await session.commit()
    await session.refresh(authorization)
    return authorization


async def budget_status(session: AsyncSession) -> dict[str, Any]:
    """L'estat per scope que ensenya `GET /platform/llm-budget` (nomes l'operador)."""
    total = await spent_usd(session, LlmBudgetScope.PILOT_TOTAL)
    scopes = []
    exhausted = await budget_tripped(session)
    for scope in LlmBudgetScope:
        cap = await cap_usd(session, scope)
        per_run = scope is not LlmBudgetScope.PILOT_TOTAL
        spent = await max_run_spent_usd(session, scope) if per_run else total
        if not per_run and spent > cap.cap_usd:
            exhausted = True
        scopes.append(
            {
                "scope": scope.value,
                "per_run": per_run,
                "cap_usd": float(cap.cap_usd),
                "cap_source": cap.source,
                "authorization_id": cap.authorization_id,
                "authorized_at": cap.authorized_at,
                "spent_usd": float(spent),
            }
        )
    return {"scopes": scopes, "total_spent_usd": float(total), "exhausted": exhausted}
