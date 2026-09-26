"""Orquestracio de l'analisi de requisits d'una licitacio.

L'unic modul d'aquesta capa que toca la base de dades i el proveidor. La
seleccio, el chunking, la validacio de cites i el prompt viuen en moduls purs;
aqui nomes es coordinen.

Ordre de garanties, de mes forta a mes feble:

1. **Res sense evidencia.** Un candidat que no es pot citar literalment no
   arriba mai a ser un requisit; queda comptat com a rebutjat.
2. **Res inventat.** Un `extraction_id` o una pagina que no eren al context
   s'ignoren. El model no pot fer parlar un document que no ha vist.
3. **Res sobreescrit.** Un run completat no es toca. Repetir l'analisi crea un
   run nou, i nomes passa a vigent si acaba be.
4. **Res del tenant.** Al proveidor nomes hi va text public del plec. Cap dada
   d'empresa entra en aquest modul: ni tan sols te com demanar-la.

Aquesta iteracio respon "que exigeix el plec". NO respon "l'empresa ho
compleix": aixo necessita el perfil corporatiu i es una altra iteracio.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Protocol

import sqlalchemy as sa
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.contract_v3 import (
    LEGACY_REASONS,
    VALUE_SCHEMA_V3,
    AwardClass,
    MechanismCheck,
    ParsedChunkV3,
    RejectionItem,
    RejectionLog,
    RejectionReason,
    RejectionStage,
    RequirementSupportStatus,
    adjudication_basis_json,
    classify_award,
    counts_as_award_criterion,
    evidence_support_json,
    record_requirement_support,
    rejection,
    remap_evidence_ids,
    requirement_support,
    requirement_value_json,
)
from app.ai.normalize_v2 import alternative_group_key, derive_obligation, normalise_requirement
from app.ai.output_normalization import normalise_output
from app.ai.profiles import DEFAULT_PROFILE, ExtractorProfile
from app.ai.provider import (
    OutputTruncatedError,
    ProviderError,
    SchemaInvalidError,
    StructuredLLMProvider,
    Usage,
    get_provider,
)
from app.ai.schemas import (
    AwardCriterionOut,
    EvidenceOut,
    RequirementOut,
)
from app.core.config import settings
from app.core.errors import (
    AnalysisInProgressError,
    AnalysisInputChangedError,
    AnalysisInterruptedError,
    AnalysisRunReclaimedError,
    AnalysisTooLargeError,
    NothingAnalyzableError,
    ResumeNotAllowedError,
)
from app.models.enums import (
    AnalysisRunStatus,
    AwardCriterionType,
    EvidenceRole,
    ExtractionStatus,
    RequirementCategory,
    RequirementModality,
    RequirementScope,
    ReviewStatus,
)
from app.models.jobs import ACTIVE_WORK_JOB_STATUSES, WorkJob
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
    TenderExtractionIssue,
)
from app.models.tenders import Tender, TenderDocument, TenderLot
from app.services.analysis_coverage import NOT_SELECTED, build_coverage
from app.services.evidence_validation import locate_quote
from app.services.extraction_qa import QaStatus, combine_status
from app.services.requirement_chunking import (
    DEFAULT_DOCUMENT_TYPES,
    AnalyzableDocument,
    Chunk,
    PageRef,
    SkippedDocument,
    build_chunks,
    compute_input_fingerprint,
    render_chunk,
)
from app.services.tender_lifecycle import latest_revision

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    run: RequirementExtractionRun
    #: Cert si s'ha reutilitzat un run anterior sense gastar tokens.
    reused: bool


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    """Que s'analitzaria ara mateix: els documents, ja amb el sostre de pagines,
    i la seva empremta.

    Es l'unica definicio de "l'entrada d'una analisi". La fan servir l'analisi,
    la cua de feina (per confirmar abans de pagar que res no ha canviat) i el pla
    del benchmark.
    """

    documents: list[AnalyzableDocument]
    skipped: list[SkippedDocument]
    fingerprint: str
    #: Pagines amb text abans d'aplicar `REQUIREMENTS_MAX_PAGES_PER_RUN`.
    pages_total: int


class ProviderIdentity(Protocol):
    """El que cal d'un proveidor per decidir si un run es reaprofitable."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...


#: Crida de progres: (chunks tractats, chunks totals).
ProgressCallback = Callable[[int, int], Awaitable[None]]
#: Es consulta abans de cada chunk. Fals = no s'ha d'iniciar cap crida mes.
ContinueCallback = Callable[[], Awaitable[bool]]


@dataclass(slots=True)
class _ChunkTally:
    """Comptadors d'un chunk. Serveixen per a les metriques del Run."""

    requirements: int = 0
    rejected: int = 0
    award_criteria: int = 0
    evidence_rejected: int = 0
    validation_errors: int = 0


# --------------------------------------------------------------------------- #
# Seleccio dels documents analitzables                                          #
# --------------------------------------------------------------------------- #


async def collect_analyzable_documents(
    session: AsyncSession,
    tender_id: uuid.UUID,
    *,
    document_ids: list[uuid.UUID] | None = None,
) -> tuple[list[AnalyzableDocument], list[SkippedDocument]]:
    """Documents amb text real, i els que queden fora amb el seu motiu.

    `is_current` no es suficient: una extraccio vigent pot ser
    `skipped_unsupported` (un OLE2 o un ZIP) o no tenir cap caracter (un PDF
    escanejat). Aquests documents queden registrats com a no analitzables; no
    se n'inventa el contingut.
    """
    rows = (
        await session.execute(
            sa.select(TenderDocument, TenderDocumentFile, TenderDocumentContent)
            .join(
                TenderDocumentFile,
                sa.and_(
                    TenderDocumentFile.tender_document_id == TenderDocument.id,
                    TenderDocumentFile.is_current.is_(True),
                ),
                isouter=True,
            )
            .join(
                TenderDocumentContent,
                TenderDocumentContent.id == TenderDocumentFile.content_id,
                isouter=True,
            )
            .where(TenderDocument.tender_id == tender_id)
            .order_by(TenderDocument.name)
        )
    ).all()

    analyzable: list[AnalyzableDocument] = []
    skipped: list[SkippedDocument] = []
    wanted = set(document_ids or [])
    default_types = {t.value for t in DEFAULT_DOCUMENT_TYPES}

    for document, file, content in rows:
        document_type = str(document.document_type)
        if wanted:
            if document.id not in wanted:
                continue
        elif document_type not in default_types:
            # Seleccio automatica conservadora; la UI en pot demanar d'altres.
            continue

        def skip(reason: str, *, doc=document, doc_type=document_type) -> None:
            # Els valors es lliguen per defecte: una clausura que capturi la
            # variable del bucle registraria el document equivocat.
            skipped.append(
                SkippedDocument(
                    tender_document_id=str(doc.id),
                    document_name=doc.name,
                    document_type=doc_type,
                    reason=reason,
                )
            )

        if file is None or content is None:
            skip("document_not_processed")
            continue

        extraction = await session.scalar(
            sa.select(TenderDocumentExtraction).where(
                TenderDocumentExtraction.content_id == content.id,
                TenderDocumentExtraction.is_current.is_(True),
            )
        )
        if extraction is None:
            skip("document_not_processed")
            continue
        if extraction.status != ExtractionStatus.EXTRACTED:
            # OLE2, ZIP, PDF corrupte... no es un error nostre, pero no hi ha text.
            skip("document_not_analyzable")
            continue

        pages = (
            await session.scalars(
                sa.select(TenderDocumentPage)
                .where(
                    TenderDocumentPage.extraction_id == extraction.id,
                    TenderDocumentPage.char_count > 0,
                )
                .order_by(TenderDocumentPage.page_number)
            )
        ).all()
        if not pages:
            skip("document_without_text")
            continue

        analyzable.append(
            AnalyzableDocument(
                tender_document_id=str(document.id),
                document_name=document.name,
                document_type=document_type,
                extraction_id=str(extraction.id),
                revision_number=extraction.revision_number,
                parser_version=extraction.parser_version,
                content_sha256=content.sha256,
                pages=tuple(
                    PageRef(page_id=str(p.id), page_number=p.page_number, text=p.text)
                    for p in pages
                ),
            )
        )

    return analyzable, skipped


async def analysis_input(
    session: AsyncSession,
    tender_id: uuid.UUID,
    *,
    document_ids: list[uuid.UUID] | None = None,
) -> AnalysisInput:
    """Els documents que s'analitzarien i la seva empremta, calculats ara.

    No llanca si no hi ha res analitzable: `documents` torna buit i qui crida
    decideix. L'analisi ho converteix en `NothingAnalyzableError`; el pla del
    benchmark ho mostra.
    """
    documents, skipped = await collect_analyzable_documents(
        session, tender_id, document_ids=document_ids
    )
    pages_total = sum(document.page_count for document in documents)
    capped = _cap_pages(documents)
    return AnalysisInput(
        documents=capped,
        skipped=skipped,
        fingerprint=compute_input_fingerprint(capped),
        pages_total=pages_total,
    )


def _cap_pages(documents: list[AnalyzableDocument]) -> list[AnalyzableDocument]:
    """Sostre dur de pagines per execucio, per no cremar tokens sense adonar-se'n."""
    budget = settings.REQUIREMENTS_MAX_PAGES_PER_RUN
    kept: list[AnalyzableDocument] = []
    for document in documents:
        if budget <= 0:
            break
        if document.page_count <= budget:
            kept.append(document)
            budget -= document.page_count
            continue
        kept.append(
            AnalyzableDocument(
                tender_document_id=document.tender_document_id,
                document_name=document.document_name,
                document_type=document.document_type,
                extraction_id=document.extraction_id,
                revision_number=document.revision_number,
                parser_version=document.parser_version,
                content_sha256=document.content_sha256,
                pages=document.pages[:budget],
            )
        )
        budget = 0
    return kept


# --------------------------------------------------------------------------- #
# Deduplicacio determinista                                                     #
# --------------------------------------------------------------------------- #


def _canonical_value(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def requirement_dedup_key(
    item: RequirementOut, evidence: list[EvidenceOut], *, include_pages: bool = True
) -> str:
    """Clau canonica d'un requisit.

    Deliberadament CONSERVADORA: inclou categoria, operador, abast i valor
    estructurat. Dos requisits que difereixin en qualsevol d'aquests camps es
    conserven tots dos. Val mes un duplicat visible que fusionar dues exigencies
    diferents, i per aixo no s'hi fa servir cap similitud semantica. Aixo es el
    que fa que una cobertura total i un sublimit per victima —valors diferents—
    no es fusionin mai.

    `include_pages` es la diferencia entre les dues versions, i te un motiu:

    * v1 hi posava les pagines, aixi que el mateix requisit citat a dues pagines
      es desava dues vegades. Es conserva perque els runs v1 no canviin.
    * v2 les treu, de manera que les cites de pagines diferents s'acumulen al
      MATEIX requisit. Es el que permet que el requisit general, l'import i la
      forma d'acreditar-lo visquin junts sense duplicar el blocador.
    """
    parts = [
        str(item.category),
        str(item.operator or ""),
        str(item.scope),
        ",".join(sorted(label.strip().casefold() for label in item.lot_labels)),
        _canonical_value(item.value),
        # El titol normalitzat desempata quan no hi ha valor estructurat.
        " ".join(item.title.split()).casefold(),
        # Camps v2: una alternativa i una exigencia amb el mateix text NO son el
        # mateix requisit, i fusionar-les perdria justament el matis.
        str(getattr(item, "modality", "")),
        str(getattr(item, "alternative_group", "") or ""),
        "eq" if getattr(item, "equivalence_allowed", False) else "",
    ]
    if include_pages:
        parts.append(
            "|".join(sorted({f"{e.extraction_id}:{e.page_number}" for e in evidence}))
        )
    return hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()[:64]


def award_dedup_key(item: AwardCriterionOut) -> str:
    parts = [
        " ".join(item.name.split()).casefold(),
        str(item.points if item.points is not None else ""),
        str(item.weight_percent if item.weight_percent is not None else ""),
        str(item.evaluation_type),
        ",".join(sorted(label.strip().casefold() for label in item.lot_labels)),
    ]
    return hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()[:64]


def award_dedup_key_v3(item: Any) -> str:
    """La clau v1/v2, amb el `kind` declarat. **Nomes per a la v3.**

    `award_dedup_key` no mira que es el candidat: un criteri i un text
    informatiu amb el mateix nom, sense punts, es fusionarien, i guanyaria el
    que arribes primer. Afegir-hi el `kind` els separa. v1/v2 no tenen `kind` i
    continuen amb la clau de sempre; cap run antic canvia de clau.

    Es el `kind` DECLARAT i no la classe final: el mateix criteri vist en dos
    chunks que se solapen ha de continuar sent un sol criteri encara que una de
    les cites en provi el mecanisme i l'altra no.
    """
    parts = [award_dedup_key(item), f"kind={item.kind}"]
    return hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()[:64]


# --------------------------------------------------------------------------- #
# Validacio de l'evidencia                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ValidatedEvidence:
    extraction_id: uuid.UUID
    page_id: uuid.UUID
    tender_document_id: uuid.UUID
    page_number: int
    quote: str
    char_start: int
    char_end: int
    section: str | None
    role: Any


#: Motius de rebuig d'una cita. Nomes codis: mai el text de la cita.
EVIDENCE_EXTRACTION_MISMATCH = "extraction_mismatch"
EVIDENCE_PAGE_NOT_IN_CHUNK = "page_not_in_chunk"
EVIDENCE_QUOTE_NOT_FOUND = "quote_not_found"


def validate_evidence(
    candidates: list[EvidenceOut], chunk: Chunk
) -> tuple[list[ValidatedEvidence], int]:
    """Conserva nomes les cites verificables. Retorna tambe quantes s'han caigut."""
    kept, reasons = validate_evidence_detailed(candidates, chunk)
    return kept, len(reasons)


def validate_evidence_detailed(
    candidates: list[EvidenceOut], chunk: Chunk
) -> tuple[list[ValidatedEvidence], list[str]]:
    """Com `validate_evidence`, pero amb el motiu de cada cita rebutjada.

    Es comprova, en aquest ordre: que l'extraccio sigui la del chunk, que la
    pagina hi fos, i que el quote aparegui de veritat al text d'aquella pagina.
    """
    by_number = {page.page_number: page for page in chunk.pages}
    max_quote = settings.REQUIREMENTS_MAX_QUOTE_CHARS
    kept: list[ValidatedEvidence] = []
    reasons: list[str] = []

    for candidate in candidates:
        if candidate.extraction_id != chunk.document.extraction_id:
            # Cita un document que no se li ha ensenyat.
            reasons.append(EVIDENCE_EXTRACTION_MISMATCH)
            continue
        page = by_number.get(candidate.page_number)
        if page is None:
            # Cita una pagina que no era al fragment.
            reasons.append(EVIDENCE_PAGE_NOT_IN_CHUNK)
            continue

        match = locate_quote(page.text, candidate.quote[:max_quote])
        if match is None:
            reasons.append(EVIDENCE_QUOTE_NOT_FOUND)
            continue

        kept.append(
            ValidatedEvidence(
                extraction_id=uuid.UUID(chunk.document.extraction_id),
                page_id=uuid.UUID(page.page_id),
                tender_document_id=uuid.UUID(chunk.document.tender_document_id),
                page_number=page.page_number,
                quote=match.exact_text,
                char_start=match.char_start,
                char_end=match.char_end,
                section=candidate.section,
                role=candidate.evidence_role,
            )
        )

    return kept, reasons


# --------------------------------------------------------------------------- #
# Resolucio de lots                                                             #
# --------------------------------------------------------------------------- #


def resolve_lot(
    labels: list[str], lots: list[TenderLot]
) -> tuple[uuid.UUID | None, bool]:
    """Relaciona etiquetes de lot amb lots reals. Nomes si es inequivoc.

    Retorna `(lot_id, needs_review)`. Si l'etiqueta no casa amb exactament un
    lot, no s'inventa cap identificador: es marca per revisar.
    """
    if not labels or not lots:
        return None, bool(labels)

    matched: set[uuid.UUID] = set()
    for label in labels:
        digits = "".join(char for char in label if char.isdigit())
        if not digits:
            return None, True
        number = digits.lstrip("0") or "0"
        candidates = [
            lot
            for lot in lots
            if (lot.lot_number or "").strip().lstrip("0") == number
            or (lot.external_lot_id or "").strip().lstrip("0") == number
        ]
        if len(candidates) != 1:
            return None, True
        matched.add(candidates[0].id)

    if len(matched) == 1:
        return next(iter(matched)), False
    # Diversos lots: no hi ha un unic lot_id honest per guardar.
    return None, False


# --------------------------------------------------------------------------- #
# Concurrencia i cache                                                          #
# --------------------------------------------------------------------------- #


async def current_run_for(
    session: AsyncSession, tender_id: uuid.UUID
) -> RequirementExtractionRun | None:
    return await session.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.tender_id == tender_id,
            RequirementExtractionRun.is_current.is_(True),
        )
    )


async def _reclaim_stale(session: AsyncSession, tender_id: uuid.UUID) -> None:
    """Allibera una analisi penjada perque el proces va morir."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.REQUIREMENTS_ANALYSIS_STALE_SECONDS)
    result = await session.execute(
        sa.update(RequirementExtractionRun)
        .where(
            RequirementExtractionRun.tender_id == tender_id,
            RequirementExtractionRun.status.in_(
                [AnalysisRunStatus.PENDING, AnalysisRunStatus.RUNNING]
            ),
            RequirementExtractionRun.started_at < cutoff,
            # Un run amb un job viu (o orfe, pendent de reconciliar) no es reclama
            # per antiguitat: una analisi llarga amb heartbeat es legitima, i la
            # decisio sobre un orfe la pren el reconciliador.
            sa.or_(
                RequirementExtractionRun.work_job_id.is_(None),
                ~sa.exists().where(
                    WorkJob.id == RequirementExtractionRun.work_job_id,
                    WorkJob.status.in_(ACTIVE_WORK_JOB_STATUSES),
                ),
            ),
        )
        .values(
            status=AnalysisRunStatus.FAILED,
            error_code="stale",
            error_message="L'analisi es va interrompre i s'ha alliberat automaticament.",
        )
    )
    if result.rowcount:
        await session.commit()


async def find_reusable_run(
    session: AsyncSession,
    tender_id: uuid.UUID,
    fingerprint: str,
    provider: ProviderIdentity,
    profile: ExtractorProfile,
) -> RequirementExtractionRun | None:
    """Un run completat amb la mateixa entrada i la mateixa configuracio."""
    return await session.scalar(
        sa.select(RequirementExtractionRun)
        .where(
            RequirementExtractionRun.tender_id == tender_id,
            RequirementExtractionRun.input_fingerprint == fingerprint,
            RequirementExtractionRun.extractor_version == profile.extractor_version,
            RequirementExtractionRun.prompt_version == profile.prompt_version,
            RequirementExtractionRun.schema_version == profile.schema_version,
            RequirementExtractionRun.provider == provider.name,
            RequirementExtractionRun.model == provider.model,
            RequirementExtractionRun.status.in_(
                [AnalysisRunStatus.COMPLETED, AnalysisRunStatus.PARTIAL]
            ),
            RequirementExtractionRun.is_current.is_(True),
        )
        .order_by(RequirementExtractionRun.created_at.desc())
        .limit(1)
    )


# --------------------------------------------------------------------------- #
# Punt d'entrada                                                                #
# --------------------------------------------------------------------------- #


async def analyze_tender_requirements(
    session: AsyncSession,
    tender: Tender,
    *,
    force: bool = False,
    document_ids: list[uuid.UUID] | None = None,
    provider: StructuredLLMProvider | None = None,
    profile: ExtractorProfile = DEFAULT_PROFILE,
    work_job_id: uuid.UUID | None = None,
    expected_fingerprint: str | None = None,
    on_progress: ProgressCallback | None = None,
    should_continue: ContinueCallback | None = None,
    chunk_limit: int | None = None,
    resume_from_run_id: uuid.UUID | None = None,
    only_chunks: frozenset[int] | None = None,
) -> AnalysisOutcome:
    """Extreu els requisits d'una licitacio a partir dels plecs ja processats.

    Viu separada de l'endpoint HTTP: l'executa el worker de la cua (`app/jobs/`)
    i tambe els scripts d'operador.

    El `profile` decideix amb quin prompt i quin contracte s'extreu, i les seves
    versions es desen al Run: dos runs de perfils diferents no es reaprofiten
    l'un a l'altre ni es poden comparar per accident.

    Costures per al worker, totes opcionals i cap amb dades de tenant:

    * `work_job_id` queda desat al run a la mateixa transaccio que el crea.
    * `expected_fingerprint`: l'entrada confirmada en demanar l'analisi. Si els
      plecs han canviat, s'atura amb `AnalysisInputChangedError` **abans** de
      cap crida al proveidor: no s'analitza en silenci una altra versio.
    * `on_progress(fets, total)` en reservar el run i a cada chunk.
    * `chunk_limit`: si l'analisi ENVIARIA mes chunks, s'atura amb
      `AnalysisTooLargeError` abans de reservar el run i de cap crida. Cobreix un
      limit abaixat mentre el job era a la cua. En una represa compten nomes els
      chunks que s'envien, no els reaprofitats.
    * `should_continue()` abans de cada chunk. Si torna fals no s'inicia cap
      crida mes i el run **no** es tanca: queda en curs perque el reconcilii qui
      en te l'autoritat (`AnalysisInterruptedError`).
    * `resume_from_run_id`: represa d'un run parcial (`plan_resume`). Es copien
      els resultats dels seus chunks bons i nomes s'envien els fallits. Si no es
      exactament la mateixa analisi, `ResumeNotAllowedError` abans de cap crida.
      El run d'origen no es toca, tret de deixar de ser vigent com qualsevol run
      que en substitueix un altre.
    * `only_chunks`: nomes s'envien aquests indexs (canari d'operador). La resta
      queden fallits amb `not_selected`: el run es parcial i ho diu, i una represa
      posterior envia nomes els que falten. `None` (per defecte) = tots. Un index
      que no existeix es refusa abans de reservar el run i de cap crida.
      Amb `resume_from_run_id`, es una represa selectiva (`restrict_resume`): han
      de ser chunks que la represa enviaria; un chunk ja completat al run d'origen
      es refusa, perque no es torna a pagar mai.
    """
    started = time.monotonic()
    llm = provider or get_provider()

    await _reclaim_stale(session, tender.id)

    current_input = await analysis_input(session, tender.id, document_ids=document_ids)
    if not current_input.documents:
        raise NothingAnalyzableError(
            "Cap document d'aquesta licitacio te text extret per analitzar. "
            "Processa'n els plecs primer."
        )
    if expected_fingerprint is not None and current_input.fingerprint != expected_fingerprint:
        raise AnalysisInputChangedError(
            "Els plecs han canviat des que es va demanar l'analisi. Cal demanar-la de nou."
        )

    documents = current_input.documents
    skipped = current_input.skipped
    fingerprint = current_input.fingerprint

    if not force and resume_from_run_id is None:
        reusable = await find_reusable_run(session, tender.id, fingerprint, llm, profile)
        if reusable is not None:
            return AnalysisOutcome(run=reusable, reused=True)

    chunks = build_chunks(documents)
    plan: ResumePlan | None = None
    if resume_from_run_id is not None:
        plan = plan_resume(
            await session.get(RequirementExtractionRun, resume_from_run_id),
            tender_id=tender.id,
            fingerprint=fingerprint,
            chunks=chunks,
            provider=llm,
            profile=profile,
        )
    if only_chunks is not None:
        if plan is not None:
            # Represa selectiva: nomes un subconjunt del que la represa enviaria.
            # Les comprovacions de `plan_resume` ja s'han fet; aixo nomes restringeix.
            only_chunks = restrict_resume(plan, only_chunks)
        else:
            unknown = sorted(only_chunks - {chunk.index for chunk in chunks})
            if unknown or not only_chunks:
                raise ValueError(f"Chunks inexistents o seleccio buida: {unknown}.")
    to_send = (
        len(only_chunks) if only_chunks is not None
        else len(plan.send) if plan is not None
        else len(chunks)
    )
    if chunk_limit is not None and to_send > chunk_limit:
        raise AnalysisTooLargeError(
            f"L'analisi enviaria {to_send} chunks i el maxim es {chunk_limit}."
        )
    run = await _claim_run(
        session,
        tender.id,
        fingerprint,
        llm,
        documents,
        chunks,
        skipped,
        profile,
        work_job_id=work_job_id,
    )
    if on_progress is not None:
        await on_progress(0, len(chunks))

    lots = list(
        (await session.scalars(sa.select(TenderLot).where(TenderLot.tender_id == tender.id))).all()
    )

    tally = _ChunkTally()
    input_tokens = output_tokens = 0
    request_ids: list[str] = []
    # Mapa i no conjunt: a la v2, retrobar una clau vol dir acumular-hi les
    # cites noves, no descartar-les.
    seen_requirements: dict[str, Requirement] = {}
    seen_awards: set[str] = set()
    last_error: str | None = None
    # Per index de chunk, el motiu pel qual no s'ha pogut fer servir. El
    # recompte ja hi era; el que faltava era saber QUINS, que es l'unic que
    # permet dir despres si una pagina del gold va arribar al model.
    chunk_failures: dict[int, str] = {}
    # Per index de chunk, diagnosi estructural d'un `schema_invalid` (path i
    # tipus dels primers errors). Sense aixo, "l'esquema no valida" no diu que.
    chunk_diagnostics: dict[int, list[dict[str, str]]] = {}
    # Per index de chunk, el que va costar. Inclou els chunks caiguts si el
    # proveidor va arribar a respondre: aquells tokens tambe es paguen.
    chunk_usage: dict[int, Usage] = {}
    # Per index de chunk, les normalitzacions estrictes aplicades abans de
    # Pydantic (`path:regla`, sense valors).
    chunk_normalisations: dict[int, list[str]] = {}
    # Per index de chunk, el diagnostic sanititzat d'un error HTTP.
    chunk_provider_errors: dict[int, dict[str, Any]] = {}
    # Per index de chunk, que s'ha descartat de la resposta i per que (codis).
    chunk_rejections: dict[int, list[dict[str, Any]]] = {}

    if plan is not None:
        await _copy_reused_results(
            session,
            run=run,
            source=plan.source,
            tally=tally,
            seen_requirements=seen_requirements,
            seen_awards=seen_awards,
        )
        run.chunks_completed += len(plan.reused)

    for position, chunk in enumerate(chunks):
        if plan is not None and chunk.index in plan.reused:
            # Ja es va llegir be al run d'origen: no es torna a enviar.
            continue
        if only_chunks is not None and chunk.index not in only_chunks:
            # No seleccionat: no s'envia, i el run ho diu en comptes de fer
            # veure que l'ha llegit. Queda al mapa de la cobertura (una represa
            # l'enviara), pero NO a `chunks_failed`: no es cap error del model.
            chunk_failures[chunk.index] = CHUNK_NOT_SELECTED
            continue
        # Abans de cada chunk, i no despres: una crida que ja es en vol pot
        # acabar, pero cap de nova no comenca si el worker ja no es propietari
        # de la feina. El run queda en curs, sense tancar-lo.
        if should_continue is not None and not await should_continue():
            logger.warning(
                "requirements.run.interrupted",
                extra={"run_id": str(run.id), "before_chunk": chunk.index},
            )
            raise AnalysisInterruptedError(
                "L'execucio ha perdut el lease: no s'inicia cap chunk mes."
            )
        if position and on_progress is not None:
            await on_progress(position, len(chunks))
        applied: list[str] = []
        try:
            result = await _run_chunk(llm, chunk, profile, applied)
        except ProviderError as exc:
            tally.validation_errors += 1
            run.chunks_failed += 1
            chunk_failures[chunk.index] = exc.code
            last_error = f"{exc.code}: {exc}"
            if exc.usage is not None:
                chunk_usage[chunk.index] = exc.usage
            if isinstance(exc, SchemaInvalidError) and exc.diagnostics:
                chunk_diagnostics[chunk.index] = exc.diagnostics
            if applied:
                chunk_normalisations[chunk.index] = applied
            if exc.details:
                chunk_provider_errors[chunk.index] = exc.details
            logger.warning(
                "requirements.chunk.failed",
                extra={
                    "run_id": str(run.id),
                    "chunk": chunk.index,
                    "error_code": exc.code,
                    "stop_reason": exc.usage.stop_reason if exc.usage else None,
                },
            )
            if exc.fatal:
                # El mateix error es repetiria a cada chunk: no se n'envia cap mes.
                remaining = chunks[position + 1 :]
                for skipped_chunk in remaining:
                    chunk_failures[skipped_chunk.index] = CHUNK_NOT_SENT
                    run.chunks_failed += 1
                logger.warning(
                    "requirements.run.fail_fast",
                    extra={
                        "run_id": str(run.id),
                        "chunk": chunk.index,
                        "not_sent": len(remaining),
                        "status": (exc.details or {}).get("status"),
                    },
                )
                break
            continue

        parsed, usage = result
        chunk_usage[chunk.index] = usage
        if applied:
            chunk_normalisations[chunk.index] = applied

        rejections: list[dict[str, Any]] = []
        _persist_chunk(
            session=session,
            run=run,
            tender=tender,
            chunk=chunk,
            parsed=parsed,
            lots=lots,
            tally=tally,
            seen_requirements=seen_requirements,
            seen_awards=seen_awards,
            profile=profile,
            rejections=rejections,
        )
        if rejections:
            chunk_rejections[chunk.index] = rejections[:MAX_CHUNK_REJECTIONS]
        run.chunks_completed += 1

    if on_progress is not None:
        await on_progress(len(chunks), len(chunks))

    for usage in chunk_usage.values():
        input_tokens += usage.input_tokens or 0
        output_tokens += usage.output_tokens or 0
        if usage.request_id:
            request_ids.append(usage.request_id)

    # La fotografia es desa sempre, tambe quan no ha fallat res: "tots els
    # chunks analitzats" es una afirmacio que despres es voldra poder comprovar,
    # no assumir per l'absencia d'errors.
    run.chunk_coverage = build_coverage(
        chunks,
        failures=chunk_failures,
        usage=chunk_usage,
        diagnostics=chunk_diagnostics,
        normalisations=chunk_normalisations,
        provider_errors=chunk_provider_errors,
        rejections=chunk_rejections,
        reused=plan.reused if plan is not None else None,
        reused_from_run_id=str(plan.source.id) if plan is not None else None,
        not_sent_code=CHUNK_NOT_SENT,
        not_selected_code=CHUNK_NOT_SELECTED,
        pages_per_chunk=settings.REQUIREMENTS_CHUNK_PAGES,
        overlap_pages=settings.REQUIREMENTS_CHUNK_OVERLAP_PAGES,
    )

    conflicts = await check_deadline_consistency(session, run, tender)

    await _finalise_run(
        session=session,
        run=run,
        conflicts=conflicts,
        tally=tally,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        request_ids=request_ids,
        started=started,
        last_error=failure_summary(chunk_failures, len(chunks), last_error),
        resumed_from=plan,
    )
    return AnalysisOutcome(run=run, reused=False)


# --------------------------------------------------------------------------- #
# Represa d'un run parcial                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """Que es reaprofita d'un run parcial i que s'ha d'enviar."""

    source: RequirementExtractionRun
    #: Per index: l'entrada de cobertura del chunk bo al run d'origen.
    reused: dict[int, dict[str, Any]]
    #: Indexs dels chunks que van fallar i que ara s'envien.
    send: frozenset[int]


def plan_resume(
    source: RequirementExtractionRun | None,
    *,
    tender_id: uuid.UUID,
    fingerprint: str,
    chunks: list[Chunk],
    provider: ProviderIdentity,
    profile: ExtractorProfile,
) -> ResumePlan:
    """Nomes es repren si es EXACTAMENT la mateixa analisi. Pura: no escriu res.

    Mateixa licitacio; mateix `input_fingerprint` (que ja inclou documents,
    revisio d'extraccio, contingut i parser); mateixes versions d'extractor,
    prompt i esquema; mateix proveidor i model; i el mateix pla de chunks, un a
    un. El run ha de ser el parcial vigent: si n'hi ha un de mes nou, reprendre
    l'antic barrejaria dues fotografies.
    """

    def refuse(reason: str, message: str) -> ResumeNotAllowedError:
        return ResumeNotAllowedError(message, details={"reason": reason})

    if source is None or source.tender_id != tender_id:
        raise refuse("run_not_found", "No hi ha cap run d'aquesta licitacio per reprendre.")
    if source.status != AnalysisRunStatus.PARTIAL:
        raise refuse("run_not_partial", "Nomes es pot reprendre un run parcial.")
    if not source.is_current:
        raise refuse("run_not_current", "Aquest run ja no es el vigent.")
    if source.input_fingerprint != fingerprint:
        raise refuse("input_changed", "Els plecs han canviat des d'aquest run.")
    if (source.extractor_version, source.prompt_version, source.schema_version) != (
        profile.extractor_version,
        profile.prompt_version,
        profile.schema_version,
    ):
        raise refuse("profile_mismatch", "El run es va fer amb un altre perfil d'extraccio.")
    if (source.provider, source.model) != (provider.name, provider.model):
        raise refuse("provider_mismatch", "El run es va fer amb un altre proveidor o model.")

    coverage = source.chunk_coverage if isinstance(source.chunk_coverage, dict) else {}
    entries = coverage.get("chunks")
    if (
        not isinstance(entries, list)
        or coverage.get("pages_per_chunk") != settings.REQUIREMENTS_CHUNK_PAGES
        or coverage.get("overlap_pages") != settings.REQUIREMENTS_CHUNK_OVERLAP_PAGES
        or len(entries) != len(chunks)
        or source.chunks_total != len(chunks)
    ):
        raise refuse("chunk_plan_mismatch", "El pla de chunks no es el mateix.")

    reused: dict[int, dict[str, Any]] = {}
    send: set[int] = set()
    by_index = {entry.get("index"): entry for entry in entries if isinstance(entry, dict)}
    for chunk in chunks:
        entry = by_index.get(chunk.index)
        if (
            entry is None
            or str(entry.get("tender_document_id")) != str(chunk.document.tender_document_id)
            or entry.get("pages") != [page.page_number for page in chunk.pages]
        ):
            raise refuse("chunk_plan_mismatch", "El pla de chunks no es el mateix.")
        if entry.get("status") == "completed":
            reused[chunk.index] = entry
        else:
            send.add(chunk.index)
    if not send:
        raise refuse("nothing_to_resume", "Aquest run no te cap chunk fallit.")
    return ResumePlan(source=source, reused=reused, send=frozenset(send))


def restrict_resume(plan: ResumePlan, only: frozenset[int]) -> frozenset[int]:
    """Quins chunks de la represa s'envien ara. Pura; mateixa regla per al pla i el run.

    Nomes pot triar entre els que la represa ja enviaria (`plan.send`: fallits o
    no seleccionats). Un chunk completat al run d'origen es refusa: no es torna a
    pagar mai. Un index fora del pla, o una seleccio buida, tambe.
    """
    if not only:
        raise ValueError("Seleccio de represa buida.")
    already = sorted(only & set(plan.reused))
    if already:
        raise ValueError(
            f"Chunks ja completats al run d'origen: {already}. No es tornen a enviar."
        )
    unknown = sorted(only - plan.send)
    if unknown:
        raise ValueError(f"Chunks fora del pla de represa: {unknown}.")
    return frozenset(only)


#: Camps que NO es copien d'un resultat reaprofitat: identitat, run, dates, el
#: vincle amb el pare (el posa la relacio) i la revisio humana (un run nou
#: comenca sense revisar, com un run complet).
_NOT_COPIED = frozenset(
    {
        "id",
        "run_id",
        "created_at",
        "updated_at",
        "review_note",
        "reviewed_by",
        "reviewed_at",
        "manual_override_json",
        "requirement_id",
        "award_criterion_id",
    }
)


def _copy_columns(source: Any, model: type, **overrides: Any) -> Any:
    values = {
        column.key: getattr(source, column.key)
        for column in model.__table__.columns
        if column.key not in _NOT_COPIED
    }
    values.update(overrides)
    return model(**values)


def _machine_review_status(source: Any) -> ReviewStatus:
    """L'estat que va posar la maquina. Si una persona l'ha revisat, no se sap: pendent."""
    return source.review_status if source.reviewed_at is None else ReviewStatus.UNREVIEWED


async def _copy_reused_results(
    session: AsyncSession,
    *,
    run: RequirementExtractionRun,
    source: RequirementExtractionRun,
    tally: _ChunkTally,
    seen_requirements: dict[str, Requirement],
    seen_awards: set[str],
) -> None:
    """Copia al run nou els requisits i criteris del run d'origen, amb les cites.

    Tots venen de chunks bons (un chunk fallit no persisteix res). Es copien i
    no es mouen: el run d'origen es queda sencer. Les claus de deduplicacio
    entren a `seen_*`, i els chunks que ara s'envien es deduplicaran contra
    aquests exactament igual que en un run complet.
    """
    requirements = (
        await session.scalars(
            sa.select(Requirement)
            .options(selectinload(Requirement.evidence))
            .where(Requirement.run_id == source.id, Requirement.archived_at.is_(None))
            .order_by(Requirement.source_chunk_index, Requirement.created_at)
        )
    ).all()
    for original in requirements:
        copy = _copy_columns(
            original, Requirement, run_id=run.id, review_status=_machine_review_status(original)
        )
        for evidence in original.evidence:
            copy.evidence.append(_copy_columns(evidence, RequirementEvidence))
        session.add(copy)
        seen_requirements[copy.dedup_key] = copy
        tally.requirements += 1

    criteria = (
        await session.scalars(
            sa.select(AwardCriterion)
            .options(selectinload(AwardCriterion.evidence))
            .where(AwardCriterion.run_id == source.id)
            .order_by(AwardCriterion.source_chunk_index, AwardCriterion.created_at)
        )
    ).all()
    for original in criteria:
        copy = _copy_columns(
            original,
            AwardCriterion,
            run_id=run.id,
            review_status=_machine_review_status(original),
        )
        new_ids: dict[str, str] = {}
        for evidence in original.evidence:
            copied = _copy_columns(evidence, AwardCriterionEvidence, id=uuid.uuid4())
            new_ids[str(evidence.id)] = str(copied.id)
            copy.evidence.append(copied)
        # v3: la base d'adjudicacio cita files d'evidencia per id; les copiades
        # en tenen un de nou. A v1/v2 la columna es NULL i aixo no fa res.
        copy.adjudication_basis_json = remap_evidence_ids(original.adjudication_basis_json, new_ids)
        session.add(copy)
        seen_awards.add(copy.dedup_key)
        # Es copien tots (res no es descarta), pero el comptador del run diu
        # criteris: un candidat v3 no ho es. A v1/v2 sempre suma, com abans.
        if counts_as_award_criterion(copy.adjudication_basis_json):
            tally.award_criteria += 1


#: Codi d'un chunk que no s'ha enviat perque un error fatal ha aturat el run.
CHUNK_NOT_SENT = "not_sent"
#: Codi d'un chunk que l'operador no va seleccionar (`only_chunks`). No es cap
#: error del model: simplement no es va enviar, i una represa l'enviara.
CHUNK_NOT_SELECTED = NOT_SELECTED


def failure_summary(
    failures: Mapping[int, str], total: int, last_error: str | None
) -> str | None:
    """Que ha fallat, comptat per motiu, i l'ultim error.

    Un run parcial amb 9 chunks tallats i 4 de mal formats no es pot resumir
    amb l'ultim error: diria nomes `schema_invalid` i amagaria la causa principal.
    """
    if not failures:
        return last_error
    # Un chunk no seleccionat (canari) no ha fallat: no es va enviar. Es diu a
    # part, i mai no compta com a fallit.
    omitted = sum(1 for code in failures.values() if code == CHUNK_NOT_SELECTED)
    actual = {index: code for index, code in failures.items() if code != CHUNK_NOT_SELECTED}
    parts: list[str] = []
    if omitted:
        parts.append(
            f"{total - omitted} de {total} chunks enviats; {omitted} no seleccionats "
            "(pendents, no son errors)."
        )
    if actual:
        counts: dict[str, int] = {}
        for code in actual.values():
            counts[code] = counts.get(code, 0) + 1
        by_reason = ", ".join(
            f"{code}: {count}"
            for code, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        of_total = total - omitted if omitted else total
        parts.append(f"{len(actual)} de {of_total} chunks fallits ({by_reason}).")
    summary = " ".join(parts)
    return f"{summary} Darrer error: {last_error}" if last_error else summary


async def _source_quality_snapshot(
    session: AsyncSession, documents: list[AnalyzableDocument]
) -> tuple[str, list[dict[str, Any]]]:
    """Amb quina qualitat de text s'ha fet aquest run, i que s'hi va detectar.

    Es una FOTOGRAFIA i no un enllaç: si el dia de dema es torna a extreure el
    document amb un parser millor, la seva qualitat canviara, pero aquest run es
    va fer amb el text d'ara i ha de poder dir-ho.

    L'estat agregat es tan important com la llista. Si alguna de les extraccions
    llegides no ha passat mai per la QA, el run queda `unknown`: no vol dir que
    hi hagi problemes, vol dir que **ningu ho ha mirat**, i d'aixo no se'n pot
    concloure que el text fos complet.

    A la llista nomes hi entren les pagines que de veritat s'analitzen. Avisar
    per una pagina que ni tan sols s'ha ensenyat al model faria que el senyal es
    llegis com a soroll i deixes de mirar-se.
    """
    if not documents:
        return QaStatus.UNKNOWN.value, []
    wanted_pages = {
        (document.extraction_id, page.page_number)
        for document in documents
        for page in document.pages
    }
    names = {document.extraction_id: document.document_name for document in documents}

    extraction_ids = [uuid.UUID(document.extraction_id) for document in documents]
    statuses = list(
        (
            await session.scalars(
                sa.select(TenderDocumentExtraction.qa_status).where(
                    TenderDocumentExtraction.id.in_(extraction_ids)
                )
            )
        ).all()
    )
    # Una extraccio que no apareix a la consulta tampoc te QA.
    statuses += [None] * (len(extraction_ids) - len(statuses))
    status = combine_status(statuses).value

    rows = (
        await session.scalars(
            sa.select(TenderExtractionIssue).where(
                TenderExtractionIssue.extraction_id.in_(extraction_ids)
            )
        )
    ).all()

    snapshot: list[dict[str, Any]] = []
    for issue in rows:
        extraction_id = str(issue.extraction_id)
        if issue.page_number is not None and (extraction_id, issue.page_number) not in wanted_pages:
            continue
        snapshot.append(
            {
                "extraction_id": extraction_id,
                "document_name": names.get(extraction_id),
                "page_number": issue.page_number,
                "code": issue.code,
            }
        )
    return status, sorted(
        snapshot, key=lambda entry: (entry["document_name"] or "", entry["page_number"] or 0)
    )


async def _claim_run(
    session: AsyncSession,
    tender_id: uuid.UUID,
    fingerprint: str,
    llm: StructuredLLMProvider,
    documents: list[AnalyzableDocument],
    chunks: list[Chunk],
    skipped: list[SkippedDocument],
    profile: ExtractorProfile,
    *,
    work_job_id: uuid.UUID | None = None,
) -> RequirementExtractionRun:
    """Reserva l'analisi. L'index unic parcial impedeix el doble clic.

    `work_job_id` s'escriu a la mateixa transaccio que crea el run: no hi ha cap
    moment en que el run existeixi sense saber de quin job ve.
    """
    quality_status, quality_issues = await _source_quality_snapshot(session, documents)
    revision = await latest_revision(session, tender_id)
    run = RequirementExtractionRun(
        source_quality_status=quality_status,
        source_quality_issues=quality_issues or None,
        tender_id=tender_id,
        work_job_id=work_job_id,
        # Sobre quin estat de la licitacio s'ha llegit. Serveix per no haver de
        # reextreure els plecs quan el que s'ha mogut es un import.
        tender_revision_id=revision.id if revision else None,
        status=AnalysisRunStatus.RUNNING,
        input_fingerprint=fingerprint,
        extractor_version=profile.extractor_version,
        prompt_version=profile.prompt_version,
        schema_version=profile.schema_version,
        provider=llm.name,
        model=llm.model,
        started_at=datetime.now(UTC),
        documents_analyzed=len(documents),
        pages_analyzed=sum(d.page_count for d in documents),
        chunks_total=len(chunks),
        is_current=False,
        skipped_documents=[
            {
                "tender_document_id": s.tender_document_id,
                "document_name": s.document_name,
                "document_type": s.document_type,
                "reason": s.reason,
            }
            for s in skipped
        ],
    )
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AnalysisInProgressError(
            "Aquesta licitacio ja s'esta analitzant. Torna-ho a provar d'aqui a un moment."
        ) from None
    await session.refresh(run)
    return run


#: Quants errors de Pydantic es desen com a diagnosi d'un `schema_invalid`.
MAX_SCHEMA_DIAGNOSTICS = 5
#: Segment que substitueix qualsevol clau que no sigui un camp de l'esquema.
UNKNOWN_PATH_SEGMENT = "<key>"


def known_schema_fields(json_schema: Mapping[str, Any]) -> frozenset[str]:
    """Tots els noms de camp que declara l'esquema, a qualsevol nivell."""
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                names.update(str(name) for name in properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(json_schema)
    return frozenset(names)


#: Errors de tipus en que la FORMA del que ha arribat diu quina normalitzacio
#: caldria (una llista que arriba com a text, com a objecte, buida...).
SHAPE_ERROR_TYPES = frozenset(
    {"list_type", "dict_type", "model_type", "model_attributes_type", "string_type"}
)
#: Una clau inventada nomes es desa si te forma d'identificador curt en
#: minuscules (`items`, `extraction_id`): aixo no pot portar una frase del plec.
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,39}")


def schema_diagnostics(
    exc: PydanticValidationError, known_fields: frozenset[str]
) -> list[dict[str, str]]:
    """On i de quin tipus son els primers errors. Ni valors ni missatges.

    Del path nomes es conserven els noms de camp que declara l'esquema i els
    indexs de llista. Qualsevol altre segment —una clau que el model s'ha
    inventat (`extra_forbidden`), l'etiqueta d'una branca d'union— es
    `<key>`: una clau dinamica pot portar text del plec, i retallar-la no ho evita.

    `shape` (nomes en errors de tipus i en claus inventades) descriu la FORMA
    del que va arribar, mai el contingut: `null`, `string(not_json,starts:[)`,
    `object(keys:items)`, `key:foo`... Sense aixo, "list_type" no diu si la
    llista va arribar com a text, com a objecte o buida, i no es pot decidir
    quina normalitzacio estricta caldria.
    """
    diagnostics: list[dict[str, str]] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=True):
        loc = tuple(error.get("loc", ()))
        segments = [
            str(part)
            if isinstance(part, int) or part in known_fields
            else UNKNOWN_PATH_SEGMENT
            for part in loc
        ]
        kind = str(error.get("type", ""))
        item = {"path": ".".join(segments) or "$", "type": kind}
        if kind in SHAPE_ERROR_TYPES:
            item["shape"] = describe_shape(error.get("input"))
        elif kind == "extra_forbidden" and loc and isinstance(loc[-1], str):
            item["shape"] = describe_unknown_key(loc[-1])
        diagnostics.append(item)
        if len(diagnostics) == MAX_SCHEMA_DIAGNOSTICS:
            break
    return diagnostics


def describe_unknown_key(key: str) -> str:
    """El nom d'una clau inventada nomes si no pot ser text del plec."""
    return f"key:{key}" if _IDENTIFIER.fullmatch(key) else f"key:<text,{len(key)} chars>"


def describe_shape(value: Any) -> str:
    """La forma d'un valor, sense cap part del seu contingut."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return f"array({len(value)})"
    if isinstance(value, dict):
        return f"object(keys:{_safe_keys(value)})"
    if isinstance(value, str):
        stripped = value.lstrip()
        first = stripped[:1]
        if not first:
            starts = "empty"
        elif first in "[{\"":
            starts = first
        else:
            starts = "text"
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            return f"string(not_json,starts:{starts},chars:{len(value)})"
        if isinstance(decoded, dict):
            # Les claus de l'objecte decodificat, amb la mateixa regla: diuen si
            # el model ha embolcallat la llista (`{"requirements": [...]}`) o ha
            # enviat una altra estructura, sense copiar cap valor.
            return f"string(json:object(keys:{_safe_keys(decoded)}),chars:{len(value)})"
        kind = {list: "array", str: "string"}.get(type(decoded), "scalar")
        if decoded is None:
            kind = "null"
        return f"string(json:{kind},chars:{len(value)})"
    return type(value).__name__


def _safe_keys(value: dict) -> str:
    """Noms de clau amb forma d'identificador; la resta, `<key>`. Com a molt 5."""
    keys = sorted(
        key if isinstance(key, str) and _IDENTIFIER.fullmatch(key) else UNKNOWN_PATH_SEGMENT
        for key in value
    )
    return ",".join(keys[:5]) + (",..." if len(keys) > 5 else "")


async def _run_chunk(
    llm: StructuredLLMProvider,
    chunk: Chunk,
    profile: ExtractorProfile,
    normalisations: list[str] | None = None,
) -> tuple[Any, Usage]:
    """Crida el proveidor i valida la sortida contra l'esquema del perfil.

    Tota sortida inservible surt com a `ProviderError` amb el seu codi i l'us
    que va costar: `output_truncated`, `no_tool_call` (des del proveidor) o
    `schema_invalid` (aqui).

    Abans de Pydantic passa per `normalise_output`, que nomes desfa els dos
    patrons coneguts (llista serialitzada com a text, `extraction_id` del
    context repetit a l'arrel). Les regles aplicades s'afegeixen a
    `normalisations`, tambe quan despres l'esquema falla.
    """
    response = await llm.extract_structured(
        system=profile.system_prompt,
        user_content=profile.build_user_message(render_chunk(chunk)),
        json_schema=profile.json_schema(),
        max_output_tokens=settings.LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS,
    )
    usage = response.usage or Usage()
    # El proveidor d'Anthropic ja ho refusa; aqui es cobreix qualsevol altre.
    # Un payload tallat que valida continua sent un chunk incomplet.
    if usage.stop_reason == "max_tokens":
        raise OutputTruncatedError(
            "La resposta ha topat amb el limit de sortida.", usage=usage
        )
    data, applied = normalise_output(
        response.data,
        profile.output_model,
        context_extraction_ids={str(chunk.document.extraction_id)},
    )
    if normalisations is not None:
        normalisations.extend(applied)
    try:
        parsed = profile.parse(data)
    except PydanticValidationError as exc:
        diagnostics = schema_diagnostics(exc, known_schema_fields(profile.json_schema()))
        logger.warning(
            "requirements.chunk.schema_invalid",
            extra={"chunk": chunk.index, "errors": len(exc.errors()), "first": diagnostics},
        )
        raise SchemaInvalidError(
            f"La sortida no compleix el contracte del perfil ({len(exc.errors())} errors).",
            usage=usage,
            diagnostics=diagnostics,
        ) from None
    return parsed, usage


def _append_new_evidence(requirement: Requirement, validated: list[ValidatedEvidence]) -> None:
    """Afegeix nomes les cites que el requisit encara no te.

    Un requisit pot estar descrit en diverses pagines —l'exigencia a una,
    l'import a una altra, la forma d'acreditar-lo a una tercera— i totes tres
    cites el sostenen. El que NO ha de passar es que la mateixa cita hi entri
    dues vegades perque dos chunks se solapen.
    """
    known = {(e.extraction_id, e.page_number, e.quote) for e in requirement.evidence}
    for item in validated:
        if (item.extraction_id, item.page_number, item.quote) in known:
            continue
        requirement.evidence.append(
            RequirementEvidence(
                extraction_id=item.extraction_id,
                page_id=item.page_id,
                tender_document_id=item.tender_document_id,
                page_number=item.page_number,
                quote=item.quote,
                char_start=item.char_start,
                char_end=item.char_end,
                section=item.section,
                # Una cita que arriba despres reforca el requisit; la primaria
                # segueix sent la que el va crear.
                evidence_role=EvidenceRole.SUPPORTING,
            )
        )


#: Motius de rebuig d'un element sencer.
ITEM_NO_VALID_EVIDENCE = "no_valid_evidence"
ITEM_DUPLICATE_KEY = "duplicate_key"
#: Quantes entrades de rebuig es desen per chunk, com a molt.
MAX_CHUNK_REJECTIONS = 50


def _persist_chunk(
    *,
    session: AsyncSession,
    run: RequirementExtractionRun,
    tender: Tender,
    chunk: Chunk,
    parsed: Any,
    lots: list[TenderLot],
    tally: _ChunkTally,
    seen_requirements: dict[str, Requirement],
    seen_awards: set[str],
    profile: ExtractorProfile,
    rejections: list[dict[str, Any]] | None = None,
) -> None:
    """Desa el que ha tornat un chunk. `rejections` rep que s'ha descartat i per que.

    Cada entrada es `{item, index, stage, reason}`: el tipus d'element, la seva
    posicio a la resposta, l'etapa i un codi. Cap text del plec ni de la cita.
    """
    if profile.claims_contract:
        # Extraction Contract v3: branca propia. Tot el que ve a continuacio es
        # v1/v2 i no es toca.
        _persist_chunk_v3(
            session=session,
            run=run,
            tender=tender,
            chunk=chunk,
            parsed=parsed,
            lots=lots,
            tally=tally,
            seen_requirements=seen_requirements,
            seen_awards=seen_awards,
            rejections=rejections,
        )
        return

    record = rejections.append if rejections is not None else (lambda _entry: None)

    for index, item in enumerate(parsed.requirements):
        evidence, reasons = validate_evidence_detailed(item.evidence, chunk)
        tally.evidence_rejected += len(reasons)
        for reason in reasons:
            record({"item": "requirement", "index": index, "stage": "evidence", "reason": reason})
        if not evidence:
            # Sense cita verificable no es un requisit: nomes un candidat.
            tally.rejected += 1
            record({"item": "requirement", "index": index, "stage": "item",
                    "reason": ITEM_NO_VALID_EVIDENCE})
            continue

        key = requirement_dedup_key(
            item, item.evidence, include_pages=not profile.structured_values
        )
        existing = seen_requirements.get(key)
        if existing is not None:
            if not profile.structured_values:
                # v1: solapament de chunks, el mateix requisit vist dues vegades.
                record({"item": "requirement", "index": index, "stage": "dedup",
                        "reason": ITEM_DUPLICATE_KEY})
                continue
            # v2: el mateix requisit citat en una altra pagina. Les cites
            # s'acumulen en comptes de duplicar el requisit —i el seu blocador—.
            _append_new_evidence(existing, evidence)
            continue

        lot_id, needs_review = resolve_lot(item.lot_labels, lots)
        scope = item.scope
        if lot_id is not None and scope == RequirementScope.UNKNOWN:
            scope = RequirementScope.LOT

        normalised = (
            normalise_requirement(item, run_id=run.id, chunk_index=chunk.index)
            if profile.structured_values
            else None
        )

        requirement = Requirement(
            run_id=run.id,
            tender_id=tender.id,
            tender_lot_id=lot_id,
            category=item.category,
            obligation=normalised.obligation if normalised else item.obligation,
            operator=item.operator,
            scope=scope,
            title=item.title[:500],
            description=item.description,
            value_json=normalised.value_json if normalised else item.value,
            value_schema=normalised.value_schema if normalised else None,
            modality=normalised.modality if normalised else RequirementModality.UNKNOWN,
            alternative_group=normalised.alternative_group if normalised else None,
            equivalence_allowed=bool(normalised.equivalence_allowed) if normalised else False,
            condition_note=normalised.condition_note if normalised else None,
            raw_lot_labels=item.lot_labels or None,
            confidence=item.confidence,
            review_status=ReviewStatus.NEEDS_REVIEW if needs_review else ReviewStatus.UNREVIEWED,
            dedup_key=key,
            source_chunk_index=chunk.index,
        )
        session.add(requirement)
        seen_requirements[key] = requirement
        # Les evidencies pengen per relacio: el cascade les desa amb el commit.
        for validated in evidence:
            requirement.evidence.append(
                RequirementEvidence(
                    extraction_id=validated.extraction_id,
                    page_id=validated.page_id,
                    tender_document_id=validated.tender_document_id,
                    page_number=validated.page_number,
                    quote=validated.quote,
                    char_start=validated.char_start,
                    char_end=validated.char_end,
                    section=validated.section,
                    evidence_role=validated.role,
                )
            )
        tally.requirements += 1

    for index, item in enumerate(parsed.award_criteria):
        evidence, reasons = validate_evidence_detailed(item.evidence, chunk)
        tally.evidence_rejected += len(reasons)
        for reason in reasons:
            record({"item": "award_criterion", "index": index, "stage": "evidence",
                    "reason": reason})
        if not evidence:
            tally.rejected += 1
            record({"item": "award_criterion", "index": index, "stage": "item",
                    "reason": ITEM_NO_VALID_EVIDENCE})
            continue

        key = award_dedup_key(item)
        if key in seen_awards:
            record({"item": "award_criterion", "index": index, "stage": "dedup",
                    "reason": ITEM_DUPLICATE_KEY})
            continue
        seen_awards.add(key)

        lot_id, needs_review = resolve_lot(item.lot_labels, lots)
        criterion = AwardCriterion(
            run_id=run.id,
            tender_id=tender.id,
            tender_lot_id=lot_id,
            category=item.category,
            name=item.name[:500],
            description=item.description,
            points=item.points,
            weight_percent=item.weight_percent,
            formula=item.formula,
            # v2: la frase sencera, el significat dels simbols i els trams. Es
            # conserven tal com venen; aqui no s'executa cap formula.
            formula_text=getattr(item, "formula_text", None),
            formula_variables=[
                v.model_dump() for v in getattr(item, "formula_variables", []) or []
            ]
            or None,
            tranches=[t.model_dump() for t in getattr(item, "tranches", []) or []] or None,
            criterion_type=getattr(item, "criterion_type", AwardCriterionType.UNKNOWN),
            evaluation_type=item.evaluation_type,
            confidence=item.confidence,
            review_status=ReviewStatus.NEEDS_REVIEW if needs_review else ReviewStatus.UNREVIEWED,
            dedup_key=key,
            source_chunk_index=chunk.index,
        )
        session.add(criterion)
        for validated in evidence:
            criterion.evidence.append(
                AwardCriterionEvidence(
                    extraction_id=validated.extraction_id,
                    page_id=validated.page_id,
                    tender_document_id=validated.tender_document_id,
                    page_number=validated.page_number,
                    quote=validated.quote,
                    char_start=validated.char_start,
                    char_end=validated.char_end,
                    section=validated.section,
                    evidence_role=validated.role,
                )
            )
        tally.award_criteria += 1


# --------------------------------------------------------------------------- #
# Extraction Contract v3                                                       #
# --------------------------------------------------------------------------- #


def _validate_evidence_v3(
    evidence: list[Any],
    chunk: Chunk,
    *,
    log: RejectionLog,
    item: RejectionItem,
    index: int,
    tally: _ChunkTally,
) -> dict[str, ValidatedEvidence]:
    """Valida cada cita per separat i la torna indexada pel seu id local.

    Mateixa validacio que v1/v2 (`validate_evidence_detailed`), cita a cita,
    perque la v3 ha de saber QUINA cita ha passat: el suport es declara per id.
    """
    kept: dict[str, ValidatedEvidence] = {}
    for candidate in evidence:
        validated, reasons = validate_evidence_detailed([candidate], chunk)
        tally.evidence_rejected += len(reasons)
        for reason in reasons:
            log.add(rejection(
                item, index, RejectionStage.EVIDENCE,
                LEGACY_REASONS.get(reason) or RejectionReason(reason),
            ))
        if validated:
            kept[candidate.id] = validated[0]
    return kept


def _dedup_view_v3(item: Any) -> Any:
    """El requisit v3 vist amb els camps que llegeix `requirement_dedup_key`.

    **La deduplicacio no canvia**: la clau es la de sempre, amb les pagines (la
    semantica conservadora de la v1), i el "valor" son els claims. Com que els
    claims porten ancora i certesa, dos requisits amb el mateix numero pero de
    coses diferents no es fusionen mai.
    """
    return SimpleNamespace(
        category=item.category,
        operator=item.operator,
        scope=item.scope,
        lot_labels=item.lot_labels,
        value=requirement_value_json(item),
        title=item.title,
        modality=item.modality,
        alternative_group=item.alternative_group,
        equivalence_allowed=item.equivalence_allowed,
    )


def _persist_chunk_v3(
    *,
    session: AsyncSession,
    run: RequirementExtractionRun,
    tender: Tender,
    chunk: Chunk,
    parsed: ParsedChunkV3,
    lots: list[TenderLot],
    tally: _ChunkTally,
    seen_requirements: dict[str, Requirement],
    seen_awards: set[str],
    rejections: list[dict[str, Any]] | None,
) -> None:
    """Desa un chunk v3: suport per claim i base d'adjudicacio, amb rebutjos per codi.

    parse_chunk_v3 -> validacio de cada cita -> requirement_support ->
    claim_support_json -> classify_award -> adjudication_basis_json.

    Res no es descarta pel suport: un claim no sostingut o un criteri sense
    mecanisme provat es desen igualment, marcats `needs_review`, i el motiu
    queda als rebutjos del chunk. Nomes es descarta el que la v1 ja descartava:
    element invalid, sense cap cita valida, o duplicat.
    """
    log = RejectionLog()
    for entry in parsed.schema_rejections.entries:
        log.add(entry)
    tally.rejected += parsed.schema_rejected
    output = parsed.output

    for index, item in zip(parsed.requirement_indexes, output.requirements, strict=True):
        validated = _validate_evidence_v3(
            item.evidence, chunk, log=log, item=RejectionItem.REQUIREMENT, index=index, tally=tally
        )
        if not validated:
            tally.rejected += 1
            log.add(rejection(RejectionItem.REQUIREMENT, index, RejectionStage.ITEM,
                              RejectionReason.NO_VALID_EVIDENCE))
            continue

        key = requirement_dedup_key(_dedup_view_v3(item), item.evidence, include_pages=True)
        if key in seen_requirements:
            log.add(rejection(RejectionItem.REQUIREMENT, index, RejectionStage.DEDUP,
                              RejectionReason.DUPLICATE_KEY))
            continue

        support = requirement_support(item, {eid: v.quote for eid, v in validated.items()})
        record_requirement_support(log, index, item, support)

        lot_id, lot_needs_review = resolve_lot(item.lot_labels, lots)
        scope = item.scope
        if lot_id is not None and scope == RequirementScope.UNKNOWN:
            scope = RequirementScope.LOT
        needs_review = lot_needs_review or support.status in (
            RequirementSupportStatus.NEEDS_REVIEW,
            RequirementSupportStatus.NOT_SUPPORTED,
        )

        requirement = Requirement(
            run_id=run.id,
            tender_id=tender.id,
            tender_lot_id=lot_id,
            category=item.category,
            obligation=derive_obligation(item.obligation, item.modality),
            operator=item.operator,
            scope=scope,
            title=item.title[:500],
            description=item.description,
            value_json=requirement_value_json(item),
            value_schema=VALUE_SCHEMA_V3,
            modality=item.modality,
            alternative_group=alternative_group_key(
                item.alternative_group, run_id=run.id, chunk_index=chunk.index
            ),
            equivalence_allowed=item.equivalence_allowed,
            condition_note=item.condition_note or None,
            raw_lot_labels=item.lot_labels or None,
            confidence=item.confidence,
            review_status=ReviewStatus.NEEDS_REVIEW if needs_review else ReviewStatus.UNREVIEWED,
            dedup_key=key,
            source_chunk_index=chunk.index,
        )
        session.add(requirement)
        seen_requirements[key] = requirement
        for evidence_id, cite in validated.items():
            requirement.evidence.append(
                RequirementEvidence(
                    extraction_id=cite.extraction_id,
                    page_id=cite.page_id,
                    tender_document_id=cite.tender_document_id,
                    page_number=cite.page_number,
                    quote=cite.quote,
                    char_start=cite.char_start,
                    char_end=cite.char_end,
                    section=cite.section,
                    evidence_role=cite.role,
                    claim_support_json=evidence_support_json(evidence_id, support),
                )
            )
        tally.requirements += 1

    for index, item in zip(parsed.award_indexes, output.award_candidates, strict=True):
        validated = _validate_evidence_v3(
            item.evidence, chunk, log=log, item=RejectionItem.AWARD_CANDIDATE, index=index,
            tally=tally,
        )
        if not validated:
            tally.rejected += 1
            log.add(rejection(RejectionItem.AWARD_CANDIDATE, index, RejectionStage.ITEM,
                              RejectionReason.NO_VALID_EVIDENCE))
            continue

        key = award_dedup_key_v3(item)
        if key in seen_awards:
            log.add(rejection(RejectionItem.AWARD_CANDIDATE, index, RejectionStage.DEDUP,
                              RejectionReason.DUPLICATE_KEY))
            continue
        seen_awards.add(key)

        decision = classify_award(item, {eid: v.quote for eid, v in validated.items()})
        for check, _ids in decision.mechanisms.values():
            if check != MechanismCheck.VERIFIED:
                log.add(rejection(RejectionItem.AWARD_CANDIDATE, index, RejectionStage.CLAIM,
                                  RejectionReason.CLAIM_NOT_SUPPORTED, detail=check.value))
        for refs in decision.unknown_refs.values():
            for _ref in refs:
                log.add(rejection(RejectionItem.AWARD_CANDIDATE, index, RejectionStage.CLAIM,
                                  RejectionReason.CLAIM_NOT_SUPPORTED, detail="unknown_ref"))

        lot_id, lot_needs_review = resolve_lot(item.lot_labels, lots)
        # L'id de cada cita es fixa aqui perque la base d'adjudicacio el cita.
        row_ids = {evidence_id: uuid.uuid4() for evidence_id in validated}
        criterion = AwardCriterion(
            run_id=run.id,
            tender_id=tender.id,
            tender_lot_id=lot_id,
            category=item.category,
            name=item.name[:500],
            description=item.description,
            points=item.points,
            weight_percent=item.weight_percent,
            formula=item.formula,
            criterion_type=item.criterion_type,
            evaluation_type=item.evaluation_type,
            confidence=item.confidence,
            review_status=(
                ReviewStatus.NEEDS_REVIEW
                if lot_needs_review or decision.needs_review
                else ReviewStatus.UNREVIEWED
            ),
            adjudication_basis_json=adjudication_basis_json(item, decision, row_ids),
            dedup_key=key,
            source_chunk_index=chunk.index,
        )
        session.add(criterion)
        for evidence_id, cite in validated.items():
            criterion.evidence.append(
                AwardCriterionEvidence(
                    id=row_ids[evidence_id],
                    extraction_id=cite.extraction_id,
                    page_id=cite.page_id,
                    tender_document_id=cite.tender_document_id,
                    page_number=cite.page_number,
                    quote=cite.quote,
                    char_start=cite.char_start,
                    char_end=cite.char_end,
                    section=cite.section,
                    evidence_role=cite.role,
                )
            )
        # La fila es desa sempre; el comptador del run nomes compta criteris.
        if decision.award_class == AwardClass.FORMAL_AWARD_CRITERION:
            tally.award_criteria += 1

    if rejections is not None:
        rejections.extend(log.entries)


#: Marca de conflicte entre el que diu el plec i el que diu la metadada de PLACSP.
DEADLINE_CONFLICT_GROUP = "deadline_vs_tender_metadata"


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


async def check_deadline_consistency(
    session: AsyncSession, run: RequirementExtractionRun, tender: Tender
) -> int:
    """Compara els terminis extrets amb `Tender.deadline_at`. Determinista.

    Si divergeixen NO es substitueix la metadada: es marca el requisit com a
    conflicte perque algu ho miri. Decidir quina de les dues dates mana es una
    interpretacio, i aquesta capa no interpreta.
    """
    if tender.deadline_at is None:
        return 0

    expected = tender.deadline_at.date()
    conflicts = 0
    candidates = (
        await session.scalars(
            sa.select(Requirement).where(
                Requirement.run_id == run.id,
                Requirement.category == RequirementCategory.DEADLINE,
            )
        )
    ).all()

    for requirement in candidates:
        value = requirement.value_json or {}
        extracted = _parse_iso_date(value.get("date"))
        # Nomes es compara el termini de presentacio, no una visita o una garantia.
        if extracted is None or value.get("kind") not in (None, "submission", "deadline"):
            continue
        if extracted != expected:
            requirement.has_conflict = True
            requirement.conflict_group = DEADLINE_CONFLICT_GROUP
            requirement.review_status = ReviewStatus.NEEDS_REVIEW
            conflicts += 1

    if conflicts:
        logger.info(
            "requirements.deadline.conflict",
            extra={"run_id": str(run.id), "conflicts": conflicts, "metadata_date": str(expected)},
        )
    return conflicts


async def _finalise_run(
    *,
    session: AsyncSession,
    run: RequirementExtractionRun,
    tally: _ChunkTally,
    conflicts: int,
    input_tokens: int,
    output_tokens: int,
    request_ids: list[str],
    started: float,
    last_error: str | None,
    resumed_from: ResumePlan | None = None,
) -> None:
    """Tanca el run i decideix si passa a vigent.

    Nomes hi passa si ha acabat be. Si no, el run anterior es queda: una analisi
    fallida no pot deixar sense requisits una licitacio que ja en tenia.

    Primer es bloqueja la fila i es comprova que el run segueix en curs. Si
    mentrestant el reconciliador l'ha donat per perdut (`worker_lost`), aquest
    tancament arriba tard: es desfa tot i el run no passa a vigent. El bloqueig
    serialitza la comprovacio amb l'UPDATE condicional del reconciliador.
    """
    locked_status = await session.scalar(
        sa.select(RequirementExtractionRun.status)
        .where(RequirementExtractionRun.id == run.id)
        .with_for_update()
    )
    if locked_status != AnalysisRunStatus.RUNNING:
        await session.rollback()
        raise AnalysisRunReclaimedError(
            "Aquest run ja no estava en curs quan s'ha intentat tancar."
        )
    run.requirements_extracted = tally.requirements
    run.requirements_rejected = tally.rejected
    run.award_criteria_extracted = tally.award_criteria
    run.evidence_rejected = tally.evidence_rejected
    run.validation_errors = tally.validation_errors
    run.input_tokens = input_tokens or None
    run.output_tokens = output_tokens or None
    run.duration_ms = int((time.monotonic() - started) * 1000)
    metadata: dict[str, Any] = {"request_ids": request_ids[:50]} if request_ids else {}
    if resumed_from is not None:
        metadata["resumed_from_run_id"] = str(resumed_from.source.id)
        metadata["reused_chunks"] = sorted(resumed_from.reused)
    run.provider_metadata = metadata or None
    run.completed_at = datetime.now(UTC)

    # Chunks que ni s'han completat ni han fallat: no seleccionats (canari).
    # En un run normal sempre es zero, i res del que segueix no canvia.
    omitted = run.chunks_total - run.chunks_completed - run.chunks_failed
    if run.chunks_completed == 0 and run.chunks_total:
        run.status = AnalysisRunStatus.FAILED
        run.error_code = "all_selected_chunks_failed" if omitted else "all_chunks_failed"
        run.error_message = last_error
    elif run.chunks_failed:
        run.status = AnalysisRunStatus.PARTIAL
        run.error_code = "some_chunks_failed"
        run.error_message = last_error
    elif omitted:
        # Parcial de debo (no s'ha llegit tot) pero sense cap error: el que falta
        # es el que l'operador no va seleccionar, i una represa ho enviara.
        run.status = AnalysisRunStatus.PARTIAL
        run.error_code = "chunks_not_selected"
        run.error_message = last_error
    else:
        run.status = AnalysisRunStatus.COMPLETED

    if run.status in (AnalysisRunStatus.COMPLETED, AnalysisRunStatus.PARTIAL):
        previous = await current_run_for(session, run.tender_id)
        if previous is not None and previous.id != run.id:
            previous.is_current = False
            await session.flush()
        run.is_current = True

    await session.commit()
    await session.refresh(run)

    logger.info(
        "requirements.run.done",
        extra={
            "run_id": str(run.id),
            "tender_id": str(run.tender_id),
            "status": str(run.status),
            "chunks": f"{run.chunks_completed}/{run.chunks_total}",
            "requirements": run.requirements_extracted,
            "rejected": run.requirements_rejected,
            "award_criteria": run.award_criteria_extracted,
            "evidence_rejected": run.evidence_rejected,
            "conflicts": conflicts,
            # Mai el text dels plecs ni cap clau d'API.
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "duration_ms": run.duration_ms,
        },
    )
