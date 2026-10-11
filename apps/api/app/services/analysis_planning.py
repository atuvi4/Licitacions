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
enviar (`estimate`, el que s'ensenyava fins ara, que es manté igual).

**Prerequisits (3B-2 §6.1) i cost (§6.7).** El preview tambe diu si l'analisi es
pot demanar i, si no, per que (un sol `blocking_reason`, en l'ordre de
`BLOCKING_PRIORITY`; la resta a `prerequisites.failed[]`):

1. proveidor real, model pinat i preu (`llm_budget.provider_failures`);
2. pressupost: cost estimat <= cap de run i gastat + estimat <= cap total;
3. paquet: cap plec per defecte sense text, cap parell amb el mateix contingut, la
   classificacio global vigent (tender public) sense troballes HIGH de paquet i cap
   forat obert; si no, nomes amb `accept_incomplete_package`;
4. qualitat del text: cap extraccio `unknown`/`poor`; `warning` demana
   `acknowledge_quality`;
5. elegibilitat canonica (tender public): seleccio per defecte, perfil de producte
   i sense `force`.

Del 2 al 5 nomes BLOQUEGEN quan la quota faria despesa real (proveidor real i algun
chunk a enviar): una simulacio no gasta i mai es vigent, i reaprofitar un run
canonic no envia res. En aquests casos queden com a avis (`advisory`), visibles.

El cost estimat en dolars (`estimated_cost_usd`) son tokens x `LLM_MODEL_PRICES`
del model exacte. Els tokens surten de l'historic si n'hi ha; si no, dels
caracters del que s'enviaria (`CHARS_PER_TOKEN`) i del sostre de sortida per chunk:
una cota, no una mesura. Sense historic no es cap motiu de bloqueig.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.profiles import ExtractorProfile
from app.core.config import settings
from app.models.enums import (
    AnalysisRunStatus,
    ClassificationFindingCode,
    FindingSeverity,
    RequirementRunBasis,
)
from app.models.package import TenderPackageGap
from app.models.requirements import RequirementExtractionRun
from app.models.tender_documents import TenderDocumentExtraction
from app.models.tenders import Tender
from app.services import llm_budget
from app.services.extraction_qa import QaStatus, combine_status
from app.services.global_document_classification import (
    compute_global_fingerprint,
    current_global_run,
    run_findings,
)
from app.services.requirement_analysis import (
    SELECTION_NOT_CANONICAL,
    AnalysisInput,
    ProviderIdentity,
    analysis_identity,
    analysis_input,
    find_reusable_run,
    plan_resume,
    run_is_superseded,
)
from app.services.requirement_chunking import Chunk, build_chunks

#: Versio del calcul del preview. Es desa a cada quote: si canvia com es compten
#: els chunks o com s'estima, un preview antic ja no es pot confirmar sense
#: tornar-lo a ensenyar. v2: prerequisits de §6.1 i cost en dolars (Task 8).
PLANNING_VERSION = "analysis-planning-v2"

#: Versio de la forma de `prerequisites`. v2 (revisio 3B-2, F10): `package.status`
#: es `unknown` (no `incomplete`) quan nomes falta la classificacio vigent. Entra a
#: la identitat del preview: una quota desada amb v1 no es confirma, es re-ensenya.
PREREQUISITES_VERSION = "quote-prerequisites-v2"

PACKAGE_INCOMPLETE = "package_incomplete"
QUALITY_UNKNOWN = "quality_unknown"
QUALITY_WARNING = "quality_warning"
ANALYSIS_TOO_LARGE = "analysis_too_large"
NOTHING_ANALYZABLE = "nothing_analyzable"

#: Un sol `blocking_reason`: el primer d'aquest ordre que falla (rulings de Task 8).
#: `analysis_too_large` (el limit de chunks del pilot, anterior a 3B-2) va al final.
BLOCKING_PRIORITY: tuple[str, ...] = (
    llm_budget.PROVIDER_NOT_REAL,
    llm_budget.MODEL_NOT_PINNED,
    llm_budget.PRICE_UNKNOWN,
    llm_budget.BUDGET_EXHAUSTED,
    llm_budget.BUDGET_EXCEEDED,
    PACKAGE_INCOMPLETE,
    QUALITY_UNKNOWN,
    QUALITY_WARNING,
    SELECTION_NOT_CANONICAL,
    ANALYSIS_TOO_LARGE,
)

#: Troballes de la classificacio global que fan el paquet incomplet (§6.1.3).
PACKAGE_FINDING_CODES = frozenset(
    {
        ClassificationFindingCode.REQUIRED_ROLE_MISSING,
        ClassificationFindingCode.REFERENCED_DOCUMENT_MISSING,
        ClassificationFindingCode.DUPLICATE_BYTES,
        ClassificationFindingCode.DUPLICATE_TEXT,
    }
)

#: Avis (no bloqueig, carry-forward de Task 7): l'ultim run real del mateix corpus va
#: acabar sense res (`completed_empty`) i esta pendent de revisio.
WARNING_PREVIOUS_EMPTY = "previous_run_completed_empty"

#: L'historic i l'estimacio viuen a `llm_budget` (una sola regla per a la quota i per a
#: la porta de l'analisi). Es re-exporten aqui pel pla del benchmark.
CHARS_PER_TOKEN = llm_budget.CHARS_PER_TOKEN
ESTIMATE_FROM_HISTORY = llm_budget.ESTIMATE_FROM_HISTORY
ESTIMATE_FROM_CHARACTERS = llm_budget.ESTIMATE_FROM_CHARACTERS
UsageHistory = llm_budget.UsageHistory
usage_history = llm_budget.usage_history
_request_count = llm_budget._request_count


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
    #: 3B-2 §6.2: sha256 del JSON canonic de l'analisi. NUL si no hi ha res a analitzar.
    analysis_identity: str | None = None
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
    #: Prerequisits de §6.1 (vegeu el docstring del modul i `_prerequisites`).
    prerequisites: dict[str, Any] = field(default_factory=dict)
    #: Dolars (§6.7). `None` sense preu del model (simulacio o `price_unknown`).
    estimated_cost_usd: Decimal | None = None
    accept_incomplete_package: bool = False
    acknowledge_quality: bool = False

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
            self.analysis_identity,
            prerequisites_identity(self.prerequisites),
        )


def resume_identity(run_id: uuid.UUID | None, chunks_to_send: int) -> tuple | None:
    """La part de la identitat que fixa una represa: de quin run i quants chunks."""
    return (str(run_id), chunks_to_send) if run_id is not None else None


def prerequisites_identity(prerequisites: Mapping[str, Any] | None) -> tuple | None:
    """La part dels prerequisits que fixa el preview (§6.1: «part de la identitat»).

    Hi entren la base del proveidor, el que bloqueja (`failed`) i les acceptacions:
    si entre el preview i la confirmacio apareix (o desapareix) un incompliment, cal
    tornar-lo a ensenyar. No hi entren els imports (el gastat es mou amb cada chunk
    de qualsevol run) ni els avisos.
    """
    if not prerequisites:
        return None
    return (
        prerequisites.get("version"),
        prerequisites.get("provider_basis"),
        tuple(prerequisites.get("failed") or ()),
        bool(prerequisites.get("accept_incomplete_package")),
        bool(prerequisites.get("acknowledge_quality")),
    )


def _by_priority(codes: list[str]) -> list[str]:
    order = {code: index for index, code in enumerate(BLOCKING_PRIORITY)}
    unique = list(dict.fromkeys(codes))
    return sorted(unique, key=lambda code: order.get(code, len(order)))


# --------------------------------------------------------------------------- #
# Prerequisits (§6.1)                                                           #
# --------------------------------------------------------------------------- #


async def _package_issues(
    session: AsyncSession,
    tender_id: uuid.UUID,
    current: AnalysisInput,
    *,
    public: bool,
    explicit: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """§6.1.2-3: el que fa el paquet incomplet, i si hi ha passada global vigent."""
    issues: list[dict[str, Any]] = [
        {
            "code": "selected_document_skipped" if explicit else "default_document_skipped",
            "tender_document_id": item.tender_document_id,
            "reason": item.reason,
        }
        for item in current.skipped
    ]
    by_sha: dict[str, list[str]] = {}
    for document in current.documents:
        by_sha.setdefault(document.content_sha256, []).append(document.tender_document_id)
    issues += [
        {"code": "duplicate_content", "tender_document_ids": sorted(ids)}
        for ids in by_sha.values()
        if len(ids) > 1
    ]

    has_run = False
    if public:
        run = await current_global_run(session, tender_id)
        fingerprint = await compute_global_fingerprint(session, tender_id)
        if run is None or run.input_fingerprint != fingerprint:
            issues.append({"code": "classification_not_current"})
        else:
            has_run = True
            selected = {document.tender_document_id for document in current.documents}
            for finding in await run_findings(session, run):
                touches = (
                    finding.tender_document_id is None
                    or str(finding.tender_document_id) in selected
                    or str(finding.related_document_id) in selected
                )
                if (
                    finding.severity == FindingSeverity.HIGH
                    and finding.code in PACKAGE_FINDING_CODES
                    and touches
                ):
                    issues.append(
                        {
                            "code": str(finding.code),
                            "finding_id": str(finding.id),
                            "tender_document_id": (
                                str(finding.tender_document_id)
                                if finding.tender_document_id
                                else None
                            ),
                        }
                    )

    gaps = await session.scalars(
        sa.select(TenderPackageGap)
        .where(TenderPackageGap.tender_id == tender_id, TenderPackageGap.resolved_at.is_(None))
        .order_by(TenderPackageGap.created_at, TenderPackageGap.id)
    )
    issues += [
        {"code": "package_gap", "gap_code": str(gap.code), "gap_id": str(gap.id)} for gap in gaps
    ]
    return issues, has_run


#: Motius que nomes diuen que el paquet NO S'HA COMPROVAT, no que hi falti res.
UNVERIFIED_PACKAGE_CODES = frozenset({"classification_not_current"})


def _package_status(issues: list[dict[str, Any]], *, has_run: bool, public: bool) -> str:
    """L'estat del paquet a la quota (revisio 3B-2, F10): unknown != incomplet.

    Sense classificacio vigent i cap altre motiu, el paquet es `unknown`: no se
    sap si hi falta res. El bloqueig (`package_incomplete` sense acceptacio,
    §6.1.3) no canvia: nomes el que se'n diu.
    """
    if any(issue["code"] not in UNVERIFIED_PACKAGE_CODES for issue in issues):
        return "incomplete"
    if issues:
        return "unknown"
    return "complete" if has_run or not public else "unknown"


async def _quality(
    session: AsyncSession, current: AnalysisInput
) -> tuple[QaStatus, list[dict[str, Any]]]:
    """§6.1.4: la QA de cada extraccio seleccionada i la del conjunt."""
    ids = [uuid.UUID(document.extraction_id) for document in current.documents]
    rows = dict(
        (
            await session.execute(
                sa.select(TenderDocumentExtraction.id, TenderDocumentExtraction.qa_status).where(
                    TenderDocumentExtraction.id.in_(ids)
                )
            )
        ).all()
    )
    statuses = [rows.get(extraction_id) for extraction_id in ids]
    documents = [
        {"tender_document_id": document.tender_document_id, "qa_status": status or "unknown"}
        for document, status in zip(current.documents, statuses, strict=True)
    ]
    return combine_status(statuses), documents


async def _previous_run_was_empty(
    session: AsyncSession, tender_id: uuid.UUID, fingerprint: str
) -> bool:
    latest = await session.scalar(
        sa.select(RequirementExtractionRun.status)
        .where(
            RequirementExtractionRun.tender_id == tender_id,
            RequirementExtractionRun.input_fingerprint == fingerprint,
            RequirementExtractionRun.run_basis == RequirementRunBasis.REAL,
        )
        .order_by(
            RequirementExtractionRun.started_at.desc(),
            RequirementExtractionRun.created_at.desc(),
        )
        .limit(1)
    )
    return latest == AnalysisRunStatus.COMPLETED_EMPTY


async def _prerequisites(
    session: AsyncSession,
    preview: AnalysisPreview,
    *,
    current: AnalysisInput,
    to_send: list[Chunk],
    profile: ExtractorProfile,
    provider: ProviderIdentity,
    public: bool,
    explicit_selection: bool,
    prior_run_ids: list[uuid.UUID],
) -> list[str]:
    """Calcula i desa a `preview` els prerequisits; torna els incompliments que bloquegen.

    El pressupost que hi queda es nomes l'estimacio PROPIA i booleans
    (`within_run_cap`, `within_total_cap`, `tripped`): cap import global, que es de
    l'operador (`GET /platform/llm-budget`).
    """
    basis = llm_budget.provider_basis(provider)
    provider_failures = llm_budget.provider_failures(provider)
    spending = basis is RequirementRunBasis.REAL and preview.chunks_to_send > 0

    # Cost (§6.7): la mateixa estimacio que la porta de l'analisi.
    guess = await llm_budget.estimate_tokens(
        session, chunks=to_send, profile=profile, provider=provider
    )
    price = llm_budget.model_price(provider.model)
    if price is not None:
        preview.estimated_cost_usd = llm_budget.chunk_cost(
            provider.model, input_tokens=guess.input_tokens, output_tokens=guess.output_tokens
        )
    budget: dict[str, Any] = {
        "estimated_cost_usd": (
            float(preview.estimated_cost_usd) if preview.estimated_cost_usd is not None else None
        ),
        "estimated_input_tokens": guess.input_tokens,
        "estimated_output_tokens": guess.output_tokens,
        "basis": guess.basis,
    }
    other: list[str] = []
    if spending and price is not None:
        quote_budget = await llm_budget.quote_budget(
            session, estimated_cost=preview.estimated_cost_usd, prior_run_ids=prior_run_ids
        )
        budget.update(quote_budget.snapshot)
        other += quote_budget.failures

    issues, has_run = await _package_issues(
        session, preview.tender_id, current, public=public, explicit=explicit_selection
    )
    if issues and not preview.accept_incomplete_package:
        other.append(PACKAGE_INCOMPLETE)

    quality, quality_documents = await _quality(session, current)
    if quality in (QaStatus.UNKNOWN, QaStatus.POOR):
        other.append(QUALITY_UNKNOWN)
    elif quality == QaStatus.WARNING and not preview.acknowledge_quality:
        other.append(QUALITY_WARNING)

    canonical = not (
        preview.force or explicit_selection or profile.name != settings.REQUIREMENTS_PRODUCT_PROFILE
    )
    if public and not canonical:
        other.append(SELECTION_NOT_CANONICAL)

    failed = _by_priority(provider_failures + (other if spending else []))
    advisory = [] if spending else _by_priority(other)
    warnings = (
        [WARNING_PREVIOUS_EMPTY]
        if await _previous_run_was_empty(session, preview.tender_id, current.fingerprint)
        else []
    )
    preview.prerequisites = {
        "version": PREREQUISITES_VERSION,
        "provider_basis": basis.value,
        "simulation_allowed": llm_budget.simulation_allowed(),
        #: Cert si la quota faria despesa real: llavors el paquet, la qualitat, el
        #: pressupost i l'elegibilitat canonica bloquegen.
        "enforced": spending,
        "public_tender": public,
        "canonical": canonical,
        "accept_incomplete_package": preview.accept_incomplete_package,
        "acknowledge_quality": preview.acknowledge_quality,
        "model_pinned": llm_budget.MODEL_NOT_PINNED not in provider_failures,
        "price_known": price is not None,
        "package": {
            "status": _package_status(issues, has_run=has_run, public=public),
            "issues": issues,
            "accepted": bool(issues) and preview.accept_incomplete_package,
        },
        "quality": {
            "status": quality.value,
            "documents": quality_documents,
            "acknowledged": quality == QaStatus.WARNING and preview.acknowledge_quality,
        },
        "budget": budget,
        "failed": failed,
        "advisory": advisory,
        "warnings": warnings,
    }
    return failed


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
    accept_incomplete_package: bool = False,
    acknowledge_quality: bool = False,
) -> AnalysisPreview:
    """Calcula el preview d'una analisi. Nomes lectures; cap crida al proveidor.

    Amb `resume_from_run_id`, nomes compta els chunks fallits d'aquell run
    parcial (`plan_resume`); si no es la mateixa analisi, `ResumeNotAllowedError`.

    `accept_incomplete_package` i `acknowledge_quality` son les acceptacions de qui
    confirma (§6.1.3-4). No son inputs del run: nomes decideixen si es pot demanar.
    """
    ids = sorted({str(item) for item in document_ids or []})
    limit = settings.ANALYSIS_MAX_CHUNKS_PER_RUN if max_chunks is None else max_chunks
    current = await analysis_input(
        session, tender_id, document_ids=[uuid.UUID(item) for item in ids] or None
    )
    owner = await session.scalar(sa.select(Tender.owner_company_id).where(Tender.id == tender_id))
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
        accept_incomplete_package=accept_incomplete_package,
        acknowledge_quality=acknowledge_quality,
    )
    if not current.documents:
        preview.allowed = False
        preview.blocking_reason = NOTHING_ANALYZABLE
        preview.prerequisites = {
            "version": PREREQUISITES_VERSION,
            "provider_basis": llm_budget.provider_basis(provider).value,
            "failed": [NOTHING_ANALYZABLE],
        }
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
    preview.analysis_identity = analysis_identity(
        current.documents, profile=profile, provider=provider
    )
    chunks = build_chunks(current.documents)
    preview.chunks = len(chunks)
    to_send = chunks
    prior_run_ids: list[uuid.UUID] = []

    if resume_from_run_id is not None:
        source = await session.get(RequirementExtractionRun, resume_from_run_id)
        plan = plan_resume(
            source,
            tender_id=tender_id,
            fingerprint=current.fingerprint,
            chunks=chunks,
            provider=provider,
            profile=profile,
            superseded=source is not None and await run_is_superseded(session, source),
        )
        preview.resume_from_run_id = plan.source.id
        preview.reusable_run_id = plan.source.id
        preview.chunks_to_send = len(plan.send)
        to_send = [chunk for chunk in chunks if chunk.index in plan.send]
        # El cap de run es de tota la cadena de represa (fix round 1, M1).
        prior_run_ids = await llm_budget.resume_chain(session, plan.source)
    else:
        if not force:
            reusable = await find_reusable_run(
                session, tender_id, current.fingerprint, provider, profile
            )
            preview.reusable_run_id = reusable.id if reusable is not None else None
        preview.chunks_to_send = 0 if preview.reusable_run_id else preview.chunks
        if preview.reusable_run_id:
            to_send = []

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

    failed = await _prerequisites(
        session,
        preview,
        current=current,
        to_send=to_send,
        profile=profile,
        provider=provider,
        public=owner is None,
        explicit_selection=bool(ids),
        prior_run_ids=prior_run_ids,
    )
    # Un resultat reaprofitable no costa res: el limit nomes compta el que
    # arribaria al proveidor.
    if preview.chunks_to_send > limit:
        failed = _by_priority([*failed, ANALYSIS_TOO_LARGE])
        preview.prerequisites["failed"] = failed
    preview.allowed = not failed
    preview.blocking_reason = failed[0] if failed else None
    return preview
