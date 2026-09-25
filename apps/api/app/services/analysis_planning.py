"""Planning d'una analisi: que s'enviaria al proveidor, calculat sense enviar-hi res.

Es la versio de producte del que feia el pla del benchmark. El benchmark el fa
servir (`app/evaluation/plan.py`), i no al reves: la canonada del producte no
pot dependre del codi d'avaluacio.

No construeix cap proveidor ni en crida cap. Del proveidor nomes en rep el nom i
el model, que formen part de la identitat de l'analisi i serveixen per saber si
ja hi ha un resultat reaprofitable i per triar l'historic de l'estimacio.

Independent del tenant: no rep ni empresa ni usuari. Qui ho demana i quan ho
guarda la capa de previews (`app/jobs/quotes.py`).

**L'estimacio de tokens es una ESTIMACIO.** Surt de runs anteriors del mateix
proveidor, model i prompt, a la mateixa base: tokens per peticio x chunks a
enviar. Sense aquest historic no s'estima res, i no s'inventa cap ratio. Cap
import en diners.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.profiles import ExtractorProfile
from app.core.config import settings
from app.models.requirements import RequirementExtractionRun
from app.services.requirement_analysis import (
    ProviderIdentity,
    analysis_input,
    find_reusable_run,
    plan_resume,
)
from app.services.requirement_chunking import build_chunks

#: Versio del calcul del preview. Es desa a cada quote: si canvia com es compten
#: els chunks o com s'estima, un preview antic ja no es pot confirmar sense
#: tornar-lo a ensenyar.
PLANNING_VERSION = "analysis-planning-v1"


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
class TokenEstimate:
    input_tokens: int
    output_tokens: int
    runs: int
    requests: int

    def basis(self) -> dict[str, Any]:
        return {"runs": self.runs, "requests": self.requests}


@dataclass(slots=True)
class AnalysisPreview:
    planning_version: str
    tender_id: uuid.UUID
    profile: str
    provider: str
    model: str
    force: bool
    document_ids: list[str]
    input_fingerprint: str
    pages_per_chunk: int
    overlap_pages: int
    documents: list[dict[str, Any]] = field(default_factory=list)
    skipped_documents: list[dict[str, Any]] = field(default_factory=list)
    pages_total: int = 0
    pages_in_plan: int = 0
    chunks: int = 0
    chunks_to_send: int = 0
    reusable_run_id: uuid.UUID | None = None
    #: Represa: el run parcial del qual es reaprofiten els chunks bons. Es desa
    #: al quote com `reusable_run_id` amb `chunks_to_send > 0` (veure
    #: `is_resume_quote`): una reutilitzacio completa sempre en te 0.
    resume_from_run_id: uuid.UUID | None = None
    estimate: TokenEstimate | None = None
    max_chunks: int = 0
    allowed: bool = True
    blocking_reason: str | None = None

    @property
    def pages_truncated(self) -> bool:
        return self.pages_in_plan < self.pages_total

    @property
    def is_empty(self) -> bool:
        return not self.documents

    def identity(self) -> tuple:
        """El que, si canvia, obliga a tornar a ensenyar el preview."""
        return (
            self.planning_version,
            self.input_fingerprint,
            self.pages_per_chunk,
            self.overlap_pages,
            self.pages_in_plan,
            self.chunks,
            self.profile,
            self.provider,
            self.model,
            resume_identity(self.resume_from_run_id, self.chunks_to_send),
        )


def resume_identity(run_id: uuid.UUID | None, chunks_to_send: int) -> tuple | None:
    """La part de la identitat que fixa una represa: de quin run i quants chunks."""
    return (str(run_id), chunks_to_send) if run_id is not None else None


async def build_preview(
    session: AsyncSession,
    tender_id: uuid.UUID,
    *,
    profile: ExtractorProfile,
    provider: ProviderIdentity,
    force: bool = False,
    document_ids: list[uuid.UUID] | None = None,
    max_chunks: int | None = None,
    resume_from_run_id: uuid.UUID | None = None,
) -> AnalysisPreview:
    """Calcula el preview d'una analisi. Nomes lectures; cap crida al proveidor.

    Amb `resume_from_run_id`, nomes compta els chunks fallits d'aquell run
    parcial (`plan_resume`); si no es la mateixa analisi, `ResumeNotAllowedError`.
    """
    ids = sorted({str(item) for item in document_ids or []})
    limit = settings.ANALYSIS_MAX_CHUNKS_PER_RUN if max_chunks is None else max_chunks
    current = await analysis_input(
        session, tender_id, document_ids=[uuid.UUID(item) for item in ids] or None
    )
    preview = AnalysisPreview(
        planning_version=PLANNING_VERSION,
        tender_id=tender_id,
        profile=profile.name,
        provider=provider.name,
        model=provider.model,
        force=force,
        document_ids=ids,
        input_fingerprint=current.fingerprint,
        pages_per_chunk=settings.REQUIREMENTS_CHUNK_PAGES,
        overlap_pages=settings.REQUIREMENTS_CHUNK_OVERLAP_PAGES,
        pages_total=current.pages_total,
        max_chunks=limit,
        skipped_documents=[
            {
                "tender_document_id": item.tender_document_id,
                "name": item.document_name,
                "document_type": item.document_type,
                "reason": item.reason,
            }
            for item in current.skipped
        ],
    )
    if not current.documents:
        preview.allowed = False
        preview.blocking_reason = "nothing_analyzable"
        return preview

    preview.documents = [
        {
            "tender_document_id": document.tender_document_id,
            "name": document.document_name,
            "document_type": document.document_type,
            "pages": document.page_count,
            "chars": sum(len(page.text or "") for page in document.pages),
        }
        for document in current.documents
    ]
    preview.pages_in_plan = sum(document.page_count for document in current.documents)
    chunks = build_chunks(current.documents)
    preview.chunks = len(chunks)

    if resume_from_run_id is not None:
        plan = plan_resume(
            await session.get(RequirementExtractionRun, resume_from_run_id),
            tender_id=tender_id,
            fingerprint=current.fingerprint,
            chunks=chunks,
            provider=provider,
            profile=profile,
        )
        preview.resume_from_run_id = plan.source.id
        preview.reusable_run_id = plan.source.id
        preview.chunks_to_send = len(plan.send)
    else:
        if not force:
            reusable = await find_reusable_run(
                session, tender_id, current.fingerprint, provider, profile
            )
            preview.reusable_run_id = reusable.id if reusable is not None else None
        preview.chunks_to_send = 0 if preview.reusable_run_id else preview.chunks

    history = await usage_history(
        session,
        prompt_version=profile.prompt_version,
        model=provider.model,
        provider=provider.name,
    )
    if history.input_per_request is not None and history.output_per_request is not None:
        preview.estimate = TokenEstimate(
            input_tokens=round(history.input_per_request * preview.chunks_to_send),
            output_tokens=round(history.output_per_request * preview.chunks_to_send),
            runs=history.runs,
            requests=history.requests,
        )

    # Un resultat reaprofitable no costa res: el limit nomes compta el que
    # arribaria al proveidor.
    if preview.chunks_to_send > limit:
        preview.allowed = False
        preview.blocking_reason = "analysis_too_large"
    return preview
