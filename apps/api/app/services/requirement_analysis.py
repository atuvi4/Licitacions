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
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.normalize_v2 import normalise_requirement
from app.ai.profiles import DEFAULT_PROFILE, ExtractorProfile
from app.ai.provider import ProviderError, StructuredLLMProvider, get_provider
from app.ai.schemas import (
    AwardCriterionOut,
    EvidenceOut,
    RequirementOut,
)
from app.core.config import settings
from app.core.errors import AnalysisInProgressError, NothingAnalyzableError
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


def validate_evidence(
    candidates: list[EvidenceOut], chunk: Chunk
) -> tuple[list[ValidatedEvidence], int]:
    """Conserva nomes les cites verificables. Retorna tambe quantes s'han caigut.

    Es comprova, en aquest ordre: que l'extraccio sigui la del chunk, que la
    pagina hi fos, i que el quote aparegui de veritat al text d'aquella pagina.
    """
    by_number = {page.page_number: page for page in chunk.pages}
    max_quote = settings.REQUIREMENTS_MAX_QUOTE_CHARS
    kept: list[ValidatedEvidence] = []
    rejected = 0

    for candidate in candidates:
        page = by_number.get(candidate.page_number)
        if page is None or candidate.extraction_id != chunk.document.extraction_id:
            # Cita una pagina o un document que no se li ha ensenyat.
            rejected += 1
            continue

        match = locate_quote(page.text, candidate.quote[:max_quote])
        if match is None:
            rejected += 1
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

    return kept, rejected


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
        )
        .values(
            status=AnalysisRunStatus.FAILED,
            error_code="stale",
            error_message="L'analisi es va interrompre i s'ha alliberat automaticament.",
        )
    )
    if result.rowcount:
        await session.commit()


async def _find_reusable_run(
    session: AsyncSession,
    tender_id: uuid.UUID,
    fingerprint: str,
    provider: StructuredLLMProvider,
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
) -> AnalysisOutcome:
    """Extreu els requisits d'una licitacio a partir dels plecs ja processats.

    Viu separada de l'endpoint HTTP a proposit: mes endavant la podra executar
    un worker sense tocar-ne ni una linia.

    El `profile` decideix amb quin prompt i quin contracte s'extreu, i les seves
    versions es desen al Run: dos runs de perfils diferents no es reaprofiten
    l'un a l'altre ni es poden comparar per accident.
    """
    started = time.monotonic()
    llm = provider or get_provider()

    await _reclaim_stale(session, tender.id)

    documents, skipped = await collect_analyzable_documents(
        session, tender.id, document_ids=document_ids
    )
    if not documents:
        raise NothingAnalyzableError(
            "Cap document d'aquesta licitacio te text extret per analitzar. "
            "Processa'n els plecs primer."
        )

    documents = _cap_pages(documents)
    fingerprint = compute_input_fingerprint(documents)

    if not force:
        reusable = await _find_reusable_run(session, tender.id, fingerprint, llm, profile)
        if reusable is not None:
            return AnalysisOutcome(run=reusable, reused=True)

    chunks = build_chunks(documents)
    run = await _claim_run(
        session, tender.id, fingerprint, llm, documents, chunks, skipped, profile
    )

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

    for chunk in chunks:
        try:
            result = await _run_chunk(llm, chunk, profile)
        except ProviderError as exc:
            tally.validation_errors += 1
            run.chunks_failed += 1
            last_error = f"{exc.code}: {exc}"
            logger.warning(
                "requirements.chunk.failed",
                extra={"run_id": str(run.id), "chunk": chunk.index, "error_code": exc.code},
            )
            continue

        parsed, usage = result
        if usage.input_tokens:
            input_tokens += usage.input_tokens
        if usage.output_tokens:
            output_tokens += usage.output_tokens
        if usage.request_id:
            request_ids.append(usage.request_id)

        if parsed is None:
            tally.validation_errors += 1
            run.chunks_failed += 1
            last_error = "invalid_structured_output"
            continue

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
        )
        run.chunks_completed += 1

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
        last_error=last_error,
    )
    return AnalysisOutcome(run=run, reused=False)


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
) -> RequirementExtractionRun:
    """Reserva l'analisi. L'index unic parcial impedeix el doble clic."""
    quality_status, quality_issues = await _source_quality_snapshot(session, documents)
    revision = await latest_revision(session, tender_id)
    run = RequirementExtractionRun(
        source_quality_status=quality_status,
        source_quality_issues=quality_issues or None,
        tender_id=tender_id,
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


async def _run_chunk(
    llm: StructuredLLMProvider, chunk: Chunk, profile: ExtractorProfile
) -> tuple[Any | None, Any]:
    """Crida el proveidor i valida la sortida contra l'esquema del perfil."""
    from app.ai.provider import Usage

    response = await llm.extract_structured(
        system=profile.system_prompt,
        user_content=profile.build_user_message(render_chunk(chunk)),
        json_schema=profile.json_schema(),
        max_output_tokens=settings.LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS,
    )
    try:
        parsed = profile.parse(response.data)
    except PydanticValidationError as exc:
        logger.warning(
            "requirements.chunk.schema_invalid",
            extra={"chunk": chunk.index, "errors": len(exc.errors())},
        )
        return None, response.usage or Usage()
    return parsed, response.usage


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
) -> None:
    for item in parsed.requirements:
        evidence, rejected = validate_evidence(item.evidence, chunk)
        tally.evidence_rejected += rejected
        if not evidence:
            # Sense cita verificable no es un requisit: nomes un candidat.
            tally.rejected += 1
            continue

        key = requirement_dedup_key(
            item, item.evidence, include_pages=not profile.structured_values
        )
        existing = seen_requirements.get(key)
        if existing is not None:
            if not profile.structured_values:
                # v1: solapament de chunks, el mateix requisit vist dues vegades.
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

    for item in parsed.award_criteria:
        evidence, rejected = validate_evidence(item.evidence, chunk)
        tally.evidence_rejected += rejected
        if not evidence:
            tally.rejected += 1
            continue

        key = award_dedup_key(item)
        if key in seen_awards:
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
) -> None:
    """Tanca el run i decideix si passa a vigent.

    Nomes hi passa si ha acabat be. Si no, el run anterior es queda: una analisi
    fallida no pot deixar sense requisits una licitacio que ja en tenia.
    """
    run.requirements_extracted = tally.requirements
    run.requirements_rejected = tally.rejected
    run.award_criteria_extracted = tally.award_criteria
    run.evidence_rejected = tally.evidence_rejected
    run.validation_errors = tally.validation_errors
    run.input_tokens = input_tokens or None
    run.output_tokens = output_tokens or None
    run.duration_ms = int((time.monotonic() - started) * 1000)
    run.provider_metadata = {"request_ids": request_ids[:50]} if request_ids else None
    run.completed_at = datetime.now(UTC)

    if run.chunks_completed == 0 and run.chunks_total:
        run.status = AnalysisRunStatus.FAILED
        run.error_code = "all_chunks_failed"
        run.error_message = last_error
    elif run.chunks_failed:
        run.status = AnalysisRunStatus.PARTIAL
        run.error_code = "some_chunks_failed"
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
