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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.contract_v3 import counts_as_award_criterion
from app.ai.profiles import DEFAULT_PROFILE, ExtractorProfile
from app.ai.provider import ProviderError, StructuredLLMProvider, get_provider
from app.core.errors import AnalysisNotAllowedError

# `resolve_tender` viu a `corpus.py`, compartit amb la pagina de revisio: el
# gold s'ha de verificar contra EXACTAMENT el document que s'analitzara.
from app.evaluation.corpus import resolve_tender
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
from app.evaluation.pricing import configured_prices, price_for
from app.models.requirements import (
    AwardCriterion,
    Requirement,
    RequirementExtractionRun,
)
from app.models.tender_documents import TenderDocumentPage
from app.models.tenders import TenderDocument
from app.services.analysis_coverage import analyzed_pages, coverage_summary, covers
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


#: Un element del gold amb cita: es tot el que necessita el filtre de cobertura.
class _Citable(Protocol):
    gold_id: str
    document_name: str
    page_number: int


def split_by_coverage(
    items: Sequence[_Citable],
    coverage: Mapping[str, Any] | None,
    *,
    kind: str = "requirement",
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Separa el gold en el que aquell run podia trobar i el que no.

    Si la pagina que cita el gold no va arribar al model —el seu chunk va
    fallar—, comptar-ho com un requisit perdut seria atribuir al perfil un
    error de l'execucio. Aquests elements queden **fora de les metriques** i es
    llisten a part com a no mesurables.

    Cobertura desconeguda (runs anteriors a la instrumentacio) NO exclou res:
    no saber si una pagina es va llegir no es el mateix que saber que no. En
    aquest cas l'informe avisa que la cobertura no esta verificada.
    """
    measurable: list[Any] = []
    unmeasurable: list[dict[str, Any]] = []
    for item in items:
        if covers(coverage, item.document_name, item.page_number) is False:
            unmeasurable.append(
                {
                    "gold_id": item.gold_id,
                    "kind": kind,
                    "is_critical": bool(getattr(item, "is_critical", False)),
                    "category": str(getattr(item, "category", "") or ""),
                    "document_name": item.document_name,
                    "page_number": item.page_number,
                    "reason": "page_not_analysed",
                }
            )
        else:
            measurable.append(item)
    return measurable, unmeasurable


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
        # v3: un candidat, una caracteristica desitjable o un text informatiu
        # no es cap prediccio de criteri. Comptar-los seria mesurar falsos
        # positius que el producte no ensenya com a criteris.
        if not counts_as_award_criterion(criterion.adjudication_basis_json):
            continue
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
    profile: ExtractorProfile = DEFAULT_PROFILE,
) -> CaseOutcome:
    """Analitza un cas amb el proveidor real i deixa el run a la base de dades.

    El `profile` decideix amb quin prompt i quin contracte s'extreu. Es un
    parametre i no una constant perque el benchmark ha de poder mesurar dues
    versions de l'extractor sobre el mateix corpus; el que fa servir qui no en
    demana cap segueix sent `DEFAULT_PROFILE`.
    """
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
            session, tender, force=force, provider=provider, profile=profile
        )
    except ProviderError as exc:
        outcome.skipped_reason = f"provider_error:{exc.code}"
        return outcome
    except AnalysisNotAllowedError as exc:
        # La porta unica de l'analisi (3B-2 §6.7): model no pinat, sense preu o
        # pressupost esgotat. No s'ha cridat el proveidor ni reservat cap run.
        outcome.skipped_reason = f"not_allowed:{exc.code}"
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
    include_draft: bool = False,
    profile: ExtractorProfile = DEFAULT_PROFILE,
) -> tuple[EvaluationSummary, list[dict[str, Any]]]:
    """Executa el benchmark i retorna el resum i el detall per cas.

    Per defecte nomes compten les anotacions VERIFICADES. Un esborrany pot ser
    correcte, pero ningu no l'ha comprovat contra la pagina original, i posar-lo
    al denominador convertiria una intuicio en una xifra oficial.

    `include_draft=True` existeix per a exploracio, i el resum ho deixa marcat.

    El `profile` queda desat al resum amb les seves versions: una xifra de
    benchmark sense dir amb quin extractor es va obtenir no es pot comparar amb
    cap altra.
    """
    llm = provider or get_provider()
    started = time.monotonic()

    summary = EvaluationSummary(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        provider=llm.name,
        model=llm.model,
        profile=profile.name,
        prompt_version=profile.prompt_version,
        extractor_version=profile.extractor_version,
        schema_version=profile.schema_version,
        coverage=dataset.coverage(),
        include_draft=include_draft,
    )
    # El preu es resol un sol cop, al principi: si a mig benchmark canviessin els
    # preus configurats, el cost de l'execucio deixaria de ser una xifra sola.
    summary.cost.model = llm.model
    summary.cost.price = price_for(llm.model, configured_prices())
    details: list[dict[str, Any]] = []

    cases = dataset.cases
    if only_case:
        cases = [c for c in cases if c.case_id == only_case]
    if limit:
        cases = cases[:limit]

    for case in cases:
        result = await run_case(session, case, llm, force=force, profile=profile)
        if result.skipped_reason or result.run_id is None:
            summary.skipped_cases.append(
                {"case_id": case.case_id, "reason": result.skipped_reason or "no_run"}
            )
            # Els requisits del gold segueixen comptant com a perduts: si el
            # sistema no arriba a analitzar el plec, tampoc en treu els requisits.
            accumulate_case(
                summary,
                case_id=case.case_id,
                outcome=match_requirements(
                    case.requirements if include_draft else case.verified_requirements, []
                ),
                analysed_pages=result.analysed_pages,
            )
            continue

        run = await session.get(RequirementExtractionRun, result.run_id)
        predicted, awards = await load_predictions(session, run)

        gold_requirements = (
            case.requirements if include_draft else case.verified_requirements
        )
        gold_awards = (
            case.award_criteria if include_draft else case.verified_award_criteria
        )

        # Cobertura real d'aquest run: quines pagines van arribar al model. El
        # gold que cita una pagina que no s'hi va enviar no es mesurable aqui.
        coverage = run.chunk_coverage
        gold_requirements, unmeasurable_reqs = split_by_coverage(
            gold_requirements, coverage, kind="requirement"
        )
        gold_awards, unmeasurable_awards = split_by_coverage(
            gold_awards, coverage, kind="award_criterion"
        )
        for item in unmeasurable_reqs + unmeasurable_awards:
            summary.unmeasurable.append({"case_id": case.case_id, **item})

        # `analysed_pages` alimenta la hipotesi de per que s'ha perdut un
        # requisit. Amb cobertura coneguda es fan servir les pagines que el
        # model va llegir de veritat, no les que tenien text: aquesta confusio
        # es la que feia dir "model_miss" d'una pagina mai analitzada.
        covered = analyzed_pages(coverage)
        case_pages = (
            {page for pages in covered.values() for page in pages}
            if covered is not None
            else result.analysed_pages
        )

        matched = match_requirements(gold_requirements, predicted)
        accumulate_case(
            summary,
            case_id=case.case_id,
            outcome=matched,
            analysed_pages=case_pages,
        )
        for pair in matched.pairs:
            update_evidence(
                summary.evidence,
                gold_page=pair.gold.page_number,
                gold_document=pair.gold.document_name,
                predicted=pair.predicted,
            )

        award_pairs, award_missed, award_spurious = match_award_criteria(
            gold_awards, awards
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
                # L'estat del run al costat de les seves xifres: un `partial`
                # mesura nomes una part del plec, i aixo s'ha de poder veure
                # sense anar a la base de dades.
                "run_status": str(run.status),
                "run_error_code": run.error_code,
                "coverage": coverage_summary(coverage),
                "unmeasurable": unmeasurable_reqs + unmeasurable_awards,
                "gold_requirements": len(gold_requirements),
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
