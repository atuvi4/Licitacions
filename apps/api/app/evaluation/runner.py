"""Execucio del benchmark: gold -> proveidor -> comparacio -> resum.

El benchmark reutilitza la canonada real de 4B (`analyze_tender_requirements`),
no una copia. Si el dia de dema canvia el chunking o la validacio de cites, la
mesura ho reflecteix. Un harness que reimplementes l'extraccio mesuraria el
harness.

La cache es la de 4B: amb el mateix `input_fingerprint`, la mateixa versio de
prompt i el mateix model, no es torna a gastar cap token. `force=True` la salta.

No es toca cap dada d'empresa: 4B es tenant-agnostic i aixo tambe.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.prompts.requirements_v1 import EXTRACTOR_VERSION, PROMPT_VERSION
from app.ai.provider import ProviderError, StructuredLLMProvider, get_provider
from app.ai.schemas import SCHEMA_VERSION
from app.evaluation.dataset import GoldCase, GoldDataset
from app.evaluation.matching import (
    PredictedAward,
    PredictedRequirement,
    match_award_criteria,
    match_requirements,
)
from app.evaluation.metrics import (
    DetectionMetrics,
    EvaluationSummary,
    accumulate_case,
    update_evidence,
)
from app.models.requirements import (
    AwardCriterion,
    Requirement,
    RequirementExtractionRun,
)
from app.models.tender_documents import TenderDocumentPage
from app.models.tenders import Tender, TenderDocument
from app.services.evidence_validation import quote_is_present
from app.services.requirement_analysis import (
    analyze_tender_requirements,
    collect_analyzable_documents,
)


@dataclass(slots=True)
class CaseOutcome:
    case_id: str
    tender_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    reused: bool = False
    skipped_reason: str | None = None
    analysed_pages: set[int] = field(default_factory=set)


async def resolve_tender(session: AsyncSession, case: GoldCase) -> Tender | None:
    """Troba el Tender real del cas. Per expedient o per identificador extern."""
    if case.tender_external_id:
        tender = await session.scalar(
            sa.select(Tender).where(Tender.external_id == case.tender_external_id)
        )
        if tender is not None:
            return tender
    if case.expediente_number:
        return await session.scalar(
            sa.select(Tender).where(Tender.expediente_number == case.expediente_number)
        )
    return None


async def _page_text(session: AsyncSession, page_id: uuid.UUID) -> str:
    page = await session.get(TenderDocumentPage, page_id)
    return page.text if page else ""


async def load_predictions(
    session: AsyncSession, run: RequirementExtractionRun
) -> tuple[list[PredictedRequirement], list[PredictedAward]]:
    """Converteix el que ha desat 4B a la vista neutra del benchmark."""
    document_names: dict[uuid.UUID, str] = dict(
        (
            await session.execute(
                sa.select(TenderDocument.id, TenderDocument.name).where(
                    TenderDocument.tender_id == run.tender_id
                )
            )
        ).all()
    )

    requirements = (
        await session.scalars(
            sa.select(Requirement)
            .options(selectinload(Requirement.evidence))
            .where(Requirement.run_id == run.id)
        )
    ).all()

    predicted: list[PredictedRequirement] = []
    for requirement in requirements:
        evidence: list[tuple[str | None, int, str, bool]] = []
        for cite in requirement.evidence:
            text = await _page_text(session, cite.page_id)
            evidence.append(
                (
                    document_names.get(cite.tender_document_id),
                    cite.page_number,
                    cite.quote,
                    quote_is_present(text, cite.quote),
                )
            )
        predicted.append(
            PredictedRequirement(
                id=requirement.id,
                category=requirement.category,
                obligation=requirement.obligation,
                operator=requirement.operator,
                scope=requirement.scope,
                title=requirement.title,
                value=requirement.value_json,
                lot_labels=list(requirement.raw_lot_labels or []),
                evidence=tuple(evidence),
            )
        )

    criteria = (
        await session.scalars(
            sa.select(AwardCriterion)
            .options(selectinload(AwardCriterion.evidence))
            .where(AwardCriterion.run_id == run.id)
        )
    ).all()
    awards: list[PredictedAward] = []
    for criterion in criteria:
        evidence = []
        for cite in criterion.evidence:
            text = await _page_text(session, cite.page_id)
            evidence.append(
                (
                    document_names.get(cite.tender_document_id),
                    cite.page_number,
                    cite.quote,
                    quote_is_present(text, cite.quote),
                )
            )
        awards.append(
            PredictedAward(
                id=criterion.id,
                name=criterion.name,
                points=criterion.points,
                evaluation_type=criterion.evaluation_type,
                formula=criterion.formula,
                evidence=tuple(evidence),
            )
        )

    return predicted, awards


async def run_case(
    session: AsyncSession,
    case: GoldCase,
    provider: StructuredLLMProvider,
    *,
    force: bool = False,
) -> CaseOutcome:
    """Analitza un cas amb el proveidor real i deixa el run a la base de dades."""
    outcome = CaseOutcome(case_id=case.case_id)

    tender = await resolve_tender(session, case)
    if tender is None:
        outcome.skipped_reason = "tender_not_found"
        return outcome
    outcome.tender_id = tender.id

    documents, _skipped = await collect_analyzable_documents(session, tender.id)
    if not documents:
        outcome.skipped_reason = "document_not_processed"
        return outcome
    outcome.analysed_pages = {
        page.page_number for document in documents for page in document.pages
    }

    try:
        result = await analyze_tender_requirements(
            session, tender, force=force, provider=provider
        )
    except ProviderError as exc:
        outcome.skipped_reason = f"provider_error:{exc.code}"
        return outcome
    except Exception as exc:  # noqa: BLE001 - un cas no pot tombar el benchmark
        outcome.skipped_reason = f"error:{type(exc).__name__}"
        return outcome

    outcome.run_id = result.run.id
    outcome.reused = result.reused
    return outcome


async def evaluate_dataset(
    session: AsyncSession,
    dataset: GoldDataset,
    *,
    provider: StructuredLLMProvider | None = None,
    force: bool = False,
    limit: int | None = None,
    only_case: str | None = None,
) -> tuple[EvaluationSummary, list[dict[str, Any]]]:
    """Executa el benchmark sencer i retorna el resum i el detall per cas."""
    llm = provider or get_provider()
    started = time.monotonic()

    summary = EvaluationSummary(
        dataset_id=dataset.dataset_id,
        provider=llm.name,
        model=llm.model,
        prompt_version=PROMPT_VERSION,
        extractor_version=EXTRACTOR_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    details: list[dict[str, Any]] = []

    cases = dataset.cases
    if only_case:
        cases = [c for c in cases if c.case_id == only_case]
    if limit:
        cases = cases[:limit]

    for case in cases:
        result = await run_case(session, case, llm, force=force)
        if result.skipped_reason or result.run_id is None:
            summary.skipped_cases.append(
                {"case_id": case.case_id, "reason": result.skipped_reason or "no_run"}
            )
            # Els requisits del gold segueixen comptant com a perduts: si el
            # sistema no arriba a analitzar el plec, tampoc en treu els requisits.
            accumulate_case(
                summary,
                case_id=case.case_id,
                outcome=match_requirements(case.requirements, []),
                analysed_pages=result.analysed_pages,
            )
            continue

        run = await session.get(RequirementExtractionRun, result.run_id)
        predicted, awards = await load_predictions(session, run)

        matched = match_requirements(case.requirements, predicted)
        accumulate_case(
            summary,
            case_id=case.case_id,
            outcome=matched,
            analysed_pages=result.analysed_pages,
        )
        for pair in matched.pairs:
            update_evidence(
                summary.evidence,
                gold_page=pair.gold.page_number,
                gold_document=pair.gold.document_name,
                predicted=pair.predicted,
            )

        award_pairs, award_missed, award_spurious = match_award_criteria(
            case.award_criteria, awards
        )
        summary.award_criteria = DetectionMetrics(
            summary.award_criteria.true_positives + len(award_pairs),
            summary.award_criteria.false_positives + len(award_spurious),
            summary.award_criteria.false_negatives + len(award_missed),
        )

        summary.cost.tenders += 1
        if not result.reused:
            summary.cost.input_tokens += run.input_tokens or 0
            summary.cost.output_tokens += run.output_tokens or 0
            summary.cost.requests += run.chunks_completed
        details.append(
            {
                "case_id": case.case_id,
                "expediente_number": case.expediente_number,
                "run_id": str(run.id),
                "reused": result.reused,
                "gold_requirements": len(case.requirements),
                "predicted_requirements": len(predicted),
                "matched": len(matched.pairs),
                "missed": [
                    {"gold_id": g.gold_id, "category": str(g.category), "quote": g.quote[:160]}
                    for g in matched.missed
                ],
                "spurious": [
                    {"title": p.title[:160], "category": str(p.category)}
                    for p in matched.spurious
                ],
                "pairs": [
                    {
                        "gold_id": pair.gold.gold_id,
                        "score": round(pair.score, 3),
                        "gold_quote": pair.gold.quote[:160],
                        "predicted_title": pair.predicted.title[:160],
                        "gold_value": pair.gold.value,
                        "predicted_value": pair.predicted.value,
                        "gold_obligation": str(pair.gold.obligation),
                        "predicted_obligation": str(pair.predicted.obligation),
                        "evidence_valid": pair.predicted.has_valid_evidence,
                    }
                    for pair in matched.pairs
                ],
            }
        )

    summary.cost.duration_ms = int((time.monotonic() - started) * 1000)
    return summary, details
