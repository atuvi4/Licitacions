"""El pla d'un benchmark: que s'enviaria al proveidor, sense enviar-hi res.

Existeix per pressupost. Un run del benchmark es paga per chunk, i fins ara
l'unica manera de saber quants chunks tenia una mostra era executar-la. El pla
refa exactament la seleccio de la canonada (`analysis_input`, `build_chunks`)
i s'atura aqui: **no construeix cap proveidor ni
en crida cap**.

L'estimacio de volum surt de runs ja fets a la mateixa base (tokens registrats
per peticio, del mateix prompt i model). Es una ESTIMACIO i es diu aixi a tot
arreu: el cost real depen del text de cada chunk i del que el model respongui.
Sense historic no s'estima res, i no s'inventa cap ratio. El cost en diners nomes
surt si `LLM_MODEL_PRICES` te preu per al model; el codi no en porta cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.profiles import ExtractorProfile
from app.evaluation.corpus import resolve_tender
from app.evaluation.dataset import GoldDataset
from app.evaluation.pricing import CURRENCY, configured_prices, price_for
from app.models.requirements import RequirementExtractionRun
from app.services.requirement_analysis import analysis_input, find_reusable_run
from app.services.requirement_chunking import build_chunks


@dataclass(slots=True)
class DocumentPlan:
    name: str
    document_type: str
    pages: int
    chars: int


@dataclass(slots=True)
class CasePlan:
    case_id: str
    tender_id: str | None = None
    documents: list[DocumentPlan] = field(default_factory=list)
    #: Documents que la canonada deixaria fora, amb el motiu.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    pages_total: int = 0
    pages_in_plan: int = 0
    chars_in_plan: int = 0
    chunks: int = 0
    gold_items: int = 0
    #: Gold que compta i que cita una pagina que el pla no enviaria mai.
    gold_outside_plan: list[str] = field(default_factory=list)
    #: Run existent que la canonada reaprofitaria sense gastar tokens.
    reusable_run_id: str | None = None

    @property
    def has_corpus(self) -> bool:
        return self.tender_id is not None

    @property
    def chunks_to_send(self) -> int:
        """Chunks que arribarien al proveidor: zero si no hi ha corpus o es reaprofita."""
        if not self.has_corpus or self.reusable_run_id:
            return 0
        return self.chunks


@dataclass(slots=True)
class UsageHistory:
    """Tokens per peticio observats en runs anteriors del mateix prompt i model."""

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


@dataclass(slots=True)
class BenchmarkPlan:
    provider: str
    model: str
    profile: str
    prompt_version: str
    cases: list[CasePlan] = field(default_factory=list)
    history: UsageHistory = field(default_factory=UsageHistory)

    @property
    def chunks_to_send(self) -> int:
        return sum(case.chunks_to_send for case in self.cases)

    def estimate(self) -> dict[str, Any]:
        """Volum aproximat. `None` a tot arreu si no hi ha historic en que basar-lo."""
        history = self.history
        chunks = self.chunks_to_send
        if history.input_per_request is None or history.output_per_request is None:
            return {
                "basis": None,
                "chunks": chunks,
                "input_tokens": None,
                "output_tokens": None,
                "cost": None,
                "currency": None,
                "cost_unavailable_reason": "sense historic de tokens per a aquest prompt i model",
            }
        input_tokens = round(history.input_per_request * chunks)
        output_tokens = round(history.output_per_request * chunks)
        price = price_for(self.model, configured_prices())
        return {
            "basis": (
                f"{history.runs} run(s), {history.requests} peticio(ns) de "
                f"{self.prompt_version} amb {self.model}"
            ),
            "chunks": chunks,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": round(price.cost(input_tokens, output_tokens), 2) if price else None,
            "currency": CURRENCY if price else None,
            "cost_unavailable_reason": (
                None if price else f"no hi ha preu configurat per a {self.model!r} "
                "(LLM_MODEL_PRICES)"
            ),
        }


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
    session: AsyncSession, *, prompt_version: str, model: str
) -> UsageHistory:
    runs = (
        await session.scalars(
            sa.select(RequirementExtractionRun).where(
                RequirementExtractionRun.prompt_version == prompt_version,
                RequirementExtractionRun.model == model,
                RequirementExtractionRun.input_tokens.is_not(None),
            )
        )
    ).all()
    history = UsageHistory()
    for run in runs:
        requests = _request_count(run)
        if not requests:
            continue
        history.runs += 1
        history.requests += requests
        history.input_tokens += run.input_tokens or 0
        history.output_tokens += run.output_tokens or 0
    return history


async def build_plan(
    session: AsyncSession,
    dataset: GoldDataset,
    *,
    profile: ExtractorProfile,
    provider_name: str,
    model: str,
    only_case: str | None = None,
    limit: int | None = None,
    force: bool = False,
    include_draft: bool = False,
) -> BenchmarkPlan:
    """Que faria el benchmark amb aquests arguments, llegint nomes la base.

    Reprodueix la seleccio de casos de `evaluate_dataset` (`only_case`, `limit`)
    i la de documents i chunks de la canonada. El proveidor es nomes un nom i un
    model: serveixen per saber si hi ha un run reaprofitable, no per construir-ne cap.
    """
    plan = BenchmarkPlan(
        provider=provider_name,
        model=model,
        profile=profile.name,
        prompt_version=profile.prompt_version,
    )
    identity = SimpleNamespace(name=provider_name, model=model)

    cases = dataset.cases
    if only_case:
        cases = [case for case in cases if case.case_id == only_case]
    if limit:
        cases = cases[:limit]

    for case in cases:
        case_plan = CasePlan(case_id=case.case_id)
        plan.cases.append(case_plan)
        if include_draft:
            gold = list(case.requirements) + list(case.award_criteria)
        else:
            gold = list(case.verified_requirements) + list(case.verified_award_criteria)
        case_plan.gold_items = len(gold)

        tender = await resolve_tender(session, case)
        if tender is None:
            continue
        case_plan.tender_id = str(tender.id)

        current = await analysis_input(session, tender.id)
        case_plan.skipped = [(doc.document_name, doc.reason) for doc in current.skipped]
        case_plan.pages_total = current.pages_total
        if not current.documents:
            continue
        capped = current.documents
        case_plan.documents = [
            DocumentPlan(
                name=doc.document_name,
                document_type=doc.document_type,
                pages=doc.page_count,
                chars=sum(len(page.text or "") for page in doc.pages),
            )
            for doc in capped
        ]
        case_plan.pages_in_plan = sum(doc.pages for doc in case_plan.documents)
        case_plan.chars_in_plan = sum(doc.chars for doc in case_plan.documents)
        case_plan.chunks = len(build_chunks(capped))

        in_plan = {(doc.document_name, page.page_number) for doc in capped for page in doc.pages}
        case_plan.gold_outside_plan = [
            f"{item.gold_id} ({item.document_name} p.{item.page_number})"
            for item in gold
            if (item.document_name, item.page_number) not in in_plan
        ]

        if not force:
            reusable = await find_reusable_run(
                session, tender.id, current.fingerprint, identity, profile
            )
            case_plan.reusable_run_id = str(reusable.id) if reusable is not None else None

    plan.history = await usage_history(
        session, prompt_version=profile.prompt_version, model=model
    )
    return plan


def render_plan(plan: BenchmarkPlan) -> str:
    lines = [
        f"PLA (no s'ha cridat cap proveidor) — perfil {plan.profile} "
        f"({plan.prompt_version}) · {plan.provider} · model {plan.model}",
        "",
    ]
    for case in plan.cases:
        if not case.has_corpus:
            lines.append(f"  {case.case_id}: SENSE CORPUS a la base (no s'enviaria res)")
            continue
        status = (
            f"reaprofitaria el run {case.reusable_run_id[:8]} (0 chunks)"
            if case.reusable_run_id
            else f"{case.chunks} chunks a enviar"
        )
        lines.append(
            f"  {case.case_id}: {len(case.documents)} document(s), "
            f"{case.pages_in_plan}/{case.pages_total} pagines, {case.chars_in_plan} caracters · "
            f"{status} · gold que compta: {case.gold_items}"
        )
        for doc in case.documents:
            lines.append(
                f"      {doc.name[:55]:55} {doc.document_type:22} {doc.pages:>4} pag "
                f"{doc.chars:>9} car"
            )
        for name, reason in case.skipped:
            lines.append(f"      (fora) {name[:55]:55} {reason}")
        if case.gold_outside_plan:
            outside = ", ".join(case.gold_outside_plan)
            lines.append(f"      ATENCIO: gold fora del pla, NO mesurable: {outside}")

    with_corpus = [case for case in plan.cases if case.has_corpus]
    lines += [
        "",
        f"Casos: {len(plan.cases)} ({len(with_corpus)} amb corpus) · "
        f"chunks a enviar: {plan.chunks_to_send}",
    ]
    estimate = plan.estimate()
    if estimate["basis"] is None:
        lines.append(f"ESTIMACIO de tokens: no disponible ({estimate['cost_unavailable_reason']}).")
    else:
        lines.append(
            f"ESTIMACIO (no es una mesura) a partir de {estimate['basis']}: "
            f"~{estimate['input_tokens']} tokens d'entrada i ~{estimate['output_tokens']} de "
            "sortida. El real depen del text de cada chunk i dels reintents."
        )
        if estimate["cost"] is not None:
            lines.append(
                f"ESTIMACIO de cost: ~{estimate['cost']} {estimate['currency']} "
                "(preu de LLM_MODEL_PRICES)."
            )
        else:
            lines.append(f"Cost: no s'estima ({estimate['cost_unavailable_reason']}).")
    return "\n".join(lines)
