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
from decimal import Decimal
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
from app.ai.profiles import (
    DEFAULT_PROFILE,
    EXPERIMENTAL_PROFILES,
    TRANSPORT_METADATA_KEY,
    ExtractorProfile,
    auto_tool_envelope_check,
    requires_auto_tool,
    transport_identity,
    transport_json_schema,
)
from app.ai.provider import (
    IllegalEnvelopeError,
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
    COMPLETED_EMPTY_MIN_PAGES,
    AnalysisRunStatus,
    AwardCriterionType,
    EvidenceRole,
    ExtractionStatus,
    LlmBudgetScope,
    RequirementCategory,
    RequirementModality,
    RequirementRunBasis,
    RequirementScope,
    ReviewStatus,
    UncertaintyQuoteStatus,
)
from app.models.jobs import ACTIVE_WORK_JOB_STATUSES, WorkJob
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
    RequirementUncertainty,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
    TenderExtractionIssue,
)
from app.models.tenders import Tender, TenderDocument, TenderLot
from app.services import llm_budget
from app.services.analysis_coverage import NOT_SELECTED, build_coverage
from app.services.document_roles import effective_document_roles
from app.services.evidence_validation import (
    diagnose_missing_quote,
    locate_quote,
    normalize_quote,
)
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
from app.services.structure_annotations import carry_over_annotations
from app.services.tender_lifecycle import latest_revision
from app.services.title_keys import normalized_title
from app.tenders.documents import ANALYZABLE_DOCUMENT, removed_document_ids

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
            # Un plec retirat de la font no es input de cap analisi nova, ni si
            # algu el demana explicitament (les runs que el van llegir es queden).
            # Tampoc un alies ni una aportacio de l'operador sense verificar
            # (3B-2 §2.3): la seva arribada no mou l'empremta.
            .where(TenderDocument.tender_id == tender_id, ANALYZABLE_DOCUMENT)
            .order_by(TenderDocument.name)
        )
    ).all()

    analyzable: list[AnalyzableDocument] = []
    skipped: list[SkippedDocument] = []
    wanted = set(document_ids or [])
    default_types = {t.value for t in DEFAULT_DOCUMENT_TYPES}
    # El tipus que llegeix l'extraccio es l'EFECTIU de la lectura compartida
    # (3B-2 §3.2, `company_id=None`): revisio de l'operador > classificacio global
    # amb confianca alta > tipus del feed. Cap override d'empresa hi entra (opcio
    # A): l'extraccio es global. El `document_type` del feed no es toca mai.
    effective = await effective_document_roles(session, tender_id, None)

    for document, file, content in rows:
        role = effective.get(document.id)
        document_type = str(role.document_type if role else document.document_type)
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


#: Versio del contracte de `analysis_identity`. Si canvia que hi entra, canvia aixo.
ANALYSIS_IDENTITY_VERSION = "analysis-identity-v1"


def analysis_identity(
    documents: list[AnalyzableDocument],
    *,
    profile: ExtractorProfile,
    provider: ProviderIdentity,
) -> str:
    """Identitat d'una analisi (3B-2 §6.2): sha256 del JSON canonic. Pura.

    Hi entren els documents del pla (ja amb el sostre de pagines) amb el tipus
    EFECTIU, el contingut, l'extraccio, el parser i les pagines del pla; el
    chunking; les versions del perfil; i proveidor i model. Res del tenant: dues
    empreses que demanen el mateix plec amb el mateix motor tenen la mateixa
    identitat. Es desa a la quota i al run, i fa el `dedupe_key` dels tenders
    publics.
    """
    payload = {
        "fp_version": ANALYSIS_IDENTITY_VERSION,
        "documents": sorted(
            [
                document.tender_document_id,
                document.document_type,
                document.content_sha256,
                document.extraction_id,
                document.parser_version,
                document.page_count,
            ]
            for document in documents
        ),
        "chunking": {
            "pages_per_chunk": settings.REQUIREMENTS_CHUNK_PAGES,
            "overlap": settings.REQUIREMENTS_CHUNK_OVERLAP_PAGES,
            "max_pages": settings.REQUIREMENTS_MAX_PAGES_PER_RUN,
        },
        "versions": {
            "extractor_version": profile.extractor_version,
            "prompt_version": profile.prompt_version,
            "schema_version": profile.schema_version,
        },
        "provider": provider.name,
        "model": provider.model,
    }
    # Nomes els models de tool estricta (Sonnet 5.5): el transport (instruccio, esquema estricte,
    # `thinking`) no es part de les versions del perfil. La resta no canvia d'identitat.
    transport = transport_identity(profile, provider)
    if transport is not None:
        payload["transport"] = transport
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def run_includes_removed_document(
    session: AsyncSession, run: RequirementExtractionRun
) -> bool:
    """Cert si una run d'extraccio va llegir o cita un document ja retirat de la font.

    Una run aixi no pot ser la base de cap analisi nova: els seus requisits i
    criteris parlen, en part, d'un plec que ja no es de la publicacio vigent. Es
    mira el que la run va llegir (`chunk_coverage`, nul a les runs antigues) i el
    que cita (evidencies de requisits i criteris).
    """
    removed = await removed_document_ids(session, run.tender_id)
    if not removed:
        return False
    read = {
        str(chunk.get("tender_document_id"))
        for chunk in (run.chunk_coverage or {}).get("chunks") or []
        if chunk.get("tender_document_id")
    }
    if read & {str(document_id) for document_id in removed}:
        return True
    cited_requirement = sa.exists().where(
        RequirementEvidence.requirement_id == Requirement.id,
        Requirement.run_id == run.id,
        RequirementEvidence.tender_document_id.in_(removed),
    )
    cited_criterion = sa.exists().where(
        AwardCriterionEvidence.award_criterion_id == AwardCriterion.id,
        AwardCriterion.run_id == run.id,
        AwardCriterionEvidence.tender_document_id.in_(removed),
    )
    return bool(await session.scalar(sa.select(sa.or_(cited_requirement, cited_criterion))))


#: Clau de `provider_metadata` on es desa la seleccio explicita d'un run.
SELECTION_METADATA_KEY = "document_ids"


async def run_document_selection(
    session: AsyncSession, run: RequirementExtractionRun
) -> list[uuid.UUID] | None:
    """La seleccio EXPLICITA de documents d'un run, o `None` si es mira la canonica.

    La «seleccio explicita» nomes existeix en una licitacio MANUAL (els plecs son
    del propietari, que tria que s'analitza). En una PUBLICA es `None` sempre: un
    run que es va promoure amb una seleccio igual al corpus canonic ha de caducar
    si despres hi entra un plec nou (fix round 2, N1); la seva obsolescencia es
    mira amb l'empremta canonica.

    Es desa a `provider_metadata` en tancar el run; per als runs anteriors, es la
    dels parametres de la feina que el va executar (`work_jobs.parameters`). Un
    run llancat fora de la cua sense cap de les dues es tracta com a canonic.
    """
    owner = await session.scalar(
        sa.select(Tender.owner_company_id).where(Tender.id == run.tender_id)
    )
    if owner is None:
        return None
    raw = (run.provider_metadata or {}).get(SELECTION_METADATA_KEY)
    if raw is None and run.work_job_id is not None:
        parameters = await session.scalar(
            sa.select(WorkJob.parameters).where(WorkJob.id == run.work_job_id)
        )
        raw = (parameters or {}).get("document_ids")
    if not raw:
        return None
    return [uuid.UUID(str(item)) for item in raw]


async def run_is_outdated(session: AsyncSession, run: RequirementExtractionRun) -> bool:
    """Cert si la run ja no parla dels plecs d'ara (3B-2 §6.2).

    Dues causes: l'entrada d'ara no es la que va llegir, o va llegir o cita un
    document ja retirat de la font. L'entrada es la de la SEVA seleccio: la
    canonica per a un run canonic (una re-extraccio amb bytes nous, un document que
    entra o surt); per a un run amb seleccio explicita, nomes els seus documents (un
    canvi en un plec que no va triar no el fa caducar). Si ara no hi ha res
    analitzable, l'empremta no diu res (no es fa caducar per un document que no
    sabem llegir); la retirada, si.

    Una run `gold` no surt de cap corpus (la seva empremta es la del dataset):
    nomes hi compta la retirada.
    """
    if run.run_basis != RequirementRunBasis.GOLD:
        selection = await run_document_selection(session, run)
        current = await analysis_input(session, run.tender_id, document_ids=selection)
        if current.documents and current.fingerprint != run.input_fingerprint:
            return True
    return await run_includes_removed_document(session, run)


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


# El titol entra a les claus per `title_keys.normalized_title`: la MATEIXA funcio
# que fan servir els requisits manuals i l'eina d'acceptacio (cap copia inline).


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
        normalized_title(item.title),
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
        normalized_title(item.name),
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


def evidence_rejection_details(
    candidates: list[EvidenceOut], chunk: Chunk
) -> list[dict[str, Any]]:
    """Per a cada cita rebutjada, el motiu, la pagina citada i un diagnostic SENSE text.

    Mateix ordre i mateixes regles que `validate_evidence_detailed`: nomes explica el
    rebuig (`diagnose_missing_quote`), no accepta res que aquella no accepti.
    """
    by_number = {page.page_number: page for page in chunk.pages}
    texts = {page.page_number: page.text for page in chunk.pages}
    max_quote = settings.REQUIREMENTS_MAX_QUOTE_CHARS
    details: list[dict[str, Any]] = []
    for candidate in candidates:
        quote = candidate.quote[:max_quote]
        if candidate.extraction_id != chunk.document.extraction_id:
            details.append({"reason": EVIDENCE_EXTRACTION_MISMATCH,
                            "cited_page": candidate.page_number})
            continue
        page = by_number.get(candidate.page_number)
        if page is None:
            details.append({"reason": EVIDENCE_PAGE_NOT_IN_CHUNK,
                            "cited_page": candidate.page_number,
                            "diagnosis": diagnose_missing_quote(
                                texts, candidate.page_number, quote)})
            continue
        if locate_quote(page.text, quote) is None:
            details.append({"reason": EVIDENCE_QUOTE_NOT_FOUND,
                            "cited_page": candidate.page_number,
                            "diagnosis": diagnose_missing_quote(
                                texts, candidate.page_number, quote)})
    return details


def chunk_notes_of(parsed: Any) -> dict[str, list[int]] | None:
    """Les pagines de les incerteses i notes que el model ha retornat per al chunk.

    Nomes pagines (sense text). Abans es descartaven: una clausula que el model veu
    indeterminada ha de quedar com a rastre, no desapareixer.
    """
    uncertainty = sorted({n.page_number for n in getattr(parsed, "uncertainties", []) or []
                          if n.page_number is not None})
    notes = sorted({n.page_number for n in getattr(parsed, "document_notes", []) or []
                    if n.page_number is not None})
    uncertain_without_page = sum(1 for n in getattr(parsed, "uncertainties", []) or []
                                 if n.page_number is None)
    if not (uncertainty or notes or uncertain_without_page):
        return None
    out: dict[str, Any] = {"uncertainty_pages": uncertainty, "document_note_pages": notes}
    if uncertain_without_page:
        out["uncertainties_without_page"] = uncertain_without_page
    return out


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
    """Un run acabat amb la mateixa entrada i la mateixa configuracio.

    Amb un proveidor real (3B-2 §6.3), nomes el canonic vigent: un run real que no
    ho es (parcial, `completed_empty`, `input_changed`) no es reaprofita mai com si
    fos la lectura del plec. Una simulacio no pot ser mai vigent: es reaprofita el
    darrer run acabat de la licitacio si es exactament aquest, que es el que
    abans feia `is_current` per a ella.
    """
    same_analysis = sa.and_(
        RequirementExtractionRun.tender_id == tender_id,
        RequirementExtractionRun.input_fingerprint == fingerprint,
        RequirementExtractionRun.extractor_version == profile.extractor_version,
        RequirementExtractionRun.prompt_version == profile.prompt_version,
        RequirementExtractionRun.schema_version == profile.schema_version,
        RequirementExtractionRun.provider == provider.name,
        RequirementExtractionRun.model == provider.model,
    )
    if RequirementRunBasis.for_provider(provider.name) is RequirementRunBasis.REAL:
        return await session.scalar(
            sa.select(RequirementExtractionRun).where(
                same_analysis,
                RequirementExtractionRun.status == AnalysisRunStatus.COMPLETED,
                RequirementExtractionRun.is_current.is_(True),
            )
        )
    latest = await latest_simulation_run(session, tender_id)
    if latest is None:
        return None
    return await session.scalar(
        sa.select(RequirementExtractionRun).where(
            same_analysis, RequirementExtractionRun.id == latest.id
        )
    )


#: Estats d'un run que n'han acabat de llegir el plec (encara que no sigui sencer).
FINISHED_RUN_STATUSES = (
    AnalysisRunStatus.COMPLETED,
    AnalysisRunStatus.COMPLETED_EMPTY,
    AnalysisRunStatus.PARTIAL,
)


def experimental_profile_of(run: RequirementExtractionRun) -> ExtractorProfile | None:
    """El perfil experimental (`EXPERIMENTAL_PROFILES`) de la familia del run, si ho es.

    Per `extractor_version`, que identifica la familia (la canonada): una revisio del prompt
    o de l'esquema (product-v2 -> product-v2.1) continua sent experimental. Una run d'una
    revisio anterior mai pot passar a vigent ni barrejar-se amb l'historial d'un altre perfil.
    La identitat d'analisi (reutilitzacio, represa) continua sent les tres versions."""
    for profile in EXPERIMENTAL_PROFILES.values():
        if profile.extractor_version == run.extractor_version:
            return profile
    return None


async def run_is_superseded(session: AsyncSession, run: RequirementExtractionRun) -> bool:
    """Cert si hi ha un run acabat MES NOU de la mateixa licitacio.

    Substitueix l'antiga condicio «el parcial ha de ser el vigent» de la represa:
    un parcial ja no es mai vigent (3B-2 §6.3), pero reprendre'n un de superat
    continuaria barrejant dues fotografies.

    Nomes compten els runs `real` o `gold`: una simulacio posterior (fake/fixture)
    no llegeix el plec i no impedeix reprendre un parcial real. Per a un run
    simulat, compta qualsevol run acabat mes nou (com abans).
    """
    newer = [
        RequirementExtractionRun.tender_id == run.tender_id,
        RequirementExtractionRun.id != run.id,
        RequirementExtractionRun.status.in_(FINISHED_RUN_STATUSES),
        RequirementExtractionRun.started_at > run.started_at,
    ]
    # Historial separat dels perfils experimentals (product-v2): un parcial seu nomes el
    # supera un run del MATEIX perfil, i un run seu no supera el parcial de cap altre.
    if experimental_profile_of(run) is not None:
        newer += [
            RequirementExtractionRun.extractor_version == run.extractor_version,
            RequirementExtractionRun.prompt_version == run.prompt_version,
            RequirementExtractionRun.schema_version == run.schema_version,
        ]
    else:
        newer += [
            RequirementExtractionRun.extractor_version != profile.extractor_version
            for profile in EXPERIMENTAL_PROFILES.values()
        ]
    if run.run_basis in TRUSTED_RUN_BASES:
        newer.append(RequirementExtractionRun.run_basis.in_(TRUSTED_RUN_BASES))
    return bool(await session.scalar(sa.select(sa.exists().where(*newer))))


#: Les bases que poden ser vigents (i les uniques que una empresa pot adoptar).
TRUSTED_RUN_BASES = (RequirementRunBasis.REAL, RequirementRunBasis.GOLD)


async def latest_simulation_run(
    session: AsyncSession, tender_id: uuid.UUID
) -> RequirementExtractionRun | None:
    """El darrer run acabat (COMPLETED o PARTIAL) no real de la licitacio.

    Una simulacio mai no es vigent (§6.3): aixo es el que en fa les vegades per a
    qui treballa nomes amb simulacions (la cache de `find_reusable_run` i la demo
    local amb `LLM_PROVIDER=fixture`).
    """
    return await session.scalar(
        sa.select(RequirementExtractionRun)
        .where(
            RequirementExtractionRun.tender_id == tender_id,
            RequirementExtractionRun.status.in_(
                [AnalysisRunStatus.COMPLETED, AnalysisRunStatus.PARTIAL]
            ),
            RequirementExtractionRun.run_basis != RequirementRunBasis.REAL,
        )
        .order_by(
            RequirementExtractionRun.started_at.desc(),
            RequirementExtractionRun.created_at.desc(),
        )
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

    3B-2 §6.6-§6.7:

    * **Commit per chunk.** Cada chunk respost (tambe un de caigut que el proveidor
      va arribar a respondre) es desa en un commit propi mentre el run es RUNNING i
      no vigent: requisits i evidencies, `chunk_coverage` (els pendents com a
      `pending`), tokens, cost i, per a un run `real`, la linia del llibre de
      despesa. Un worker que mor a mig run no perd la feina pagada, i
      `plan_resume` (des de `worker_lost`/`internal_error`) envia nomes el que falta.
    * **Porta unica.** Abans de reservar el run i de cap crida,
      `llm_budget.provider_gate`: simulacio nomes si es permet, model pinat, preu,
      pressupost no esgotat i estimacio dins del cap (`AnalysisNotAllowedError`). La
      passen el worker, la sonda `--paid-run` i el benchmark, que criden aquesta funcio.
    * **Caps.** Abans de cada chunk d'un run `real`, `llm_budget.assert_within_budget`
      (gastat total + estimacio del chunk seguent <= cap total; gastat de la cadena de represa
      + estimacio <= cap de l'scope:
      `canary` per a `only_chunks` de com a molt 2 chunks, `run` per a la resta). Si se
      supera, no s'envia res mes: el run es tanca `PARTIAL` (o `FAILED` si encara no
      n'havia completat cap) amb `budget_exceeded` i l'scope excedit.
    * **`force` mai canonic** en una licitacio publica (§6.4): el run es desa,
      pero no passa a vigent (`forced_not_canonical`).
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
        source = await session.get(RequirementExtractionRun, resume_from_run_id)
        plan = plan_resume(
            source,
            tender_id=tender.id,
            fingerprint=fingerprint,
            chunks=chunks,
            provider=llm,
            profile=profile,
            superseded=source is not None and await run_is_superseded(session, source),
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
    # La porta unica (3B-2 §6.3 capa 1, §6.7), ABANS de reservar el run i de cap
    # crida: per al worker, la sonda `--paid-run` i el benchmark per igual.
    run_scope = llm_budget.budget_scope_for(only_chunks)
    prior_run_ids = await llm_budget.resume_chain(session, plan.source) if plan else []
    await llm_budget.provider_gate(
        session,
        llm,
        chunks=[
            chunk
            for chunk in chunks
            if (only_chunks is None or chunk.index in only_chunks)
            and (plan is None or chunk.index in plan.send)
        ],
        profile=profile,
        run_scope=run_scope,
        prior_run_ids=prior_run_ids,
    )
    # El transport (models `auto`) es fixa UN cop i neix amb el run: un run perdut abans de
    # tancar-se (worker_lost, internal_error) tambe el porta, i la represa el pot comparar.
    transport = transport_identity(profile, llm)
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
        identity=analysis_identity(documents, profile=profile, provider=llm),
        transport=transport,
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
    # Les incerteses materials del run (product-v2): dedup i millor procedencia.
    uncertainty_ledger = UncertaintyLedger(session)
    last_error: str | None = None
    # Per index de chunk, el motiu pel qual no s'ha pogut fer servir. El
    # recompte ja hi era; el que faltava era saber QUINS, que es l'unic que
    # permet dir despres si una pagina del gold va arribar al model.
    chunk_failures: dict[int, str] = {}
    # Per index de chunk, diagnosi estructural d'un `schema_invalid` (path i
    # tipus dels primers errors). Sense aixo, "l'esquema no valida" no diu que.
    chunk_diagnostics: dict[int, list[dict[str, Any]]] = {}
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
    # Per index de chunk, les pagines de les incerteses i notes del model (sense text).
    chunk_notes: dict[int, dict[str, Any]] = {}

    if plan is not None:
        await _copy_reused_results(
            session,
            run=run,
            source=plan.source,
            tally=tally,
            seen_requirements=seen_requirements,
            seen_awards=seen_awards,
            uncertainty_ledger=uncertainty_ledger,
        )
        run.chunks_completed += len(plan.reused)

    def snapshot(pending: frozenset[int] = frozenset()) -> dict[str, Any]:
        # La fotografia dels chunks. Durant el run, els que encara no s'han tractat
        # hi surten `pending` (no enviats): una represa des d'un worker mort els enviara.
        return build_coverage(
            chunks,
            failures={**{index: CHUNK_PENDING for index in pending}, **chunk_failures},
            usage=chunk_usage,
            diagnostics=chunk_diagnostics,
            normalisations=chunk_normalisations,
            provider_errors=chunk_provider_errors,
            rejections=chunk_rejections,
            notes=chunk_notes,
            reused=plan.reused if plan is not None else None,
            reused_from_run_id=str(plan.source.id) if plan is not None else None,
            not_sent_code=CHUNK_NOT_SENT,
            not_selected_code=CHUNK_NOT_SELECTED,
            unsent_codes=UNSENT_CHUNK_CODES,
            pages_per_chunk=settings.REQUIREMENTS_CHUNK_PAGES,
            overlap_pages=settings.REQUIREMENTS_CHUNK_OVERLAP_PAGES,
        )

    real_spend = run.run_basis == RequirementRunBasis.REAL
    if only_chunks is not None:
        # Abans del primer commit: la fotografia d'un run a mig fer no pot donar per
        # llegit un chunk no seleccionat que el bucle encara no ha tocat.
        for chunk in chunks:
            if chunk.index not in only_chunks and not (
                plan is not None and chunk.index in plan.reused
            ):
                chunk_failures[chunk.index] = CHUNK_NOT_SELECTED
    to_visit = [
        chunk.index
        for chunk in chunks
        if not (plan is not None and chunk.index in plan.reused)
        and chunk.index not in chunk_failures
    ]
    #: L'scope del cap que ha aturat el run (`None` si no s'ha aturat per pressupost).
    exceeded_scope: LlmBudgetScope | None = None

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
        if real_spend:
            # §6.7: abans de cada chunk, el que ja s'ha gastat (la cadena de represa
            # d'aquest run i el total) ha de cabre als caps. Si no, res mes.
            try:
                budget_run_ids = [run.id, *prior_run_ids]
                await llm_budget.assert_within_budget(
                    session,
                    run_ids=budget_run_ids,
                    run_scope=run_scope,
                    next_chunk_usd=await llm_budget.next_chunk_estimate(
                        session,
                        run_ids=budget_run_ids,
                        chunk=chunk,
                        profile=profile,
                        model=run.model,
                    ),
                )
            except llm_budget.BudgetExceededError as exc:
                stopped = [index for index in to_visit if index not in chunk_usage]
                stopped = [index for index in stopped if index not in chunk_failures]
                for index in stopped:
                    chunk_failures[index] = llm_budget.BUDGET_EXCEEDED
                    run.chunks_failed += 1
                last_error = f"{llm_budget.BUDGET_EXCEEDED}: {exc}"
                exceeded_scope = exc.scope
                logger.warning(
                    "requirements.run.budget_exceeded",
                    extra={"run_id": str(run.id), "before_chunk": chunk.index, **exc.details},
                )
                break
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
                # Nomes compten els que s'HAURIEN enviat: un chunk no seleccionat
                # (canari, ja marcat `not_selected`) o reaprofitat d'una represa no
                # s'havia d'enviar mai i no es cap fallada.
                remaining = [
                    pending
                    for pending in chunks[position + 1 :]
                    if pending.index not in chunk_failures
                    and not (plan is not None and pending.index in plan.reused)
                ]
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
            if exc.usage is not None:
                # El proveidor ha respost (i es paga), tambe abans d'un error fatal: es
                # desa com un chunk fet i la seva linia queda al llibre.
                await _commit_chunk(
                    session,
                    run=run,
                    chunk_index=chunk.index,
                    usage=exc.usage,
                    tally=tally,
                    chunk_usage=chunk_usage,
                    coverage=snapshot(_pending(to_visit, chunk_usage, chunk_failures)),
                    real_spend=real_spend,
                    scope=run_scope,
                )
            if exc.fatal:
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
            uncertainty_ledger=uncertainty_ledger,
        )
        if rejections:
            chunk_rejections[chunk.index] = rejections[:MAX_CHUNK_REJECTIONS]
        notes = chunk_notes_of(parsed)
        if notes:
            chunk_notes[chunk.index] = notes
        run.chunks_completed += 1
        await _commit_chunk(
            session,
            run=run,
            chunk_index=chunk.index,
            usage=usage,
            tally=tally,
            chunk_usage=chunk_usage,
            coverage=snapshot(_pending(to_visit, chunk_usage, chunk_failures)),
            real_spend=real_spend,
            scope=run_scope,
        )

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
    run.chunk_coverage = snapshot()

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
        document_ids=document_ids,
        force=force,
        exceeded_scope=exceeded_scope,
        transport=transport,
    )
    return AnalysisOutcome(run=run, reused=False)


def _pending(
    to_visit: list[int], usage: Mapping[int, Usage], failures: Mapping[int, str]
) -> frozenset[int]:
    """Els chunks que el run ha d'enviar i que encara no han tingut resposta ni motiu."""
    return frozenset(index for index in to_visit if index not in usage and index not in failures)


async def _commit_chunk(
    session: AsyncSession,
    *,
    run: RequirementExtractionRun,
    chunk_index: int,
    usage: Usage,
    tally: _ChunkTally,
    chunk_usage: Mapping[int, Usage],
    coverage: dict[str, Any],
    real_spend: bool,
    scope: LlmBudgetScope,
) -> None:
    """Commit propi d'un chunk respost (3B-2 §6.6): el que ha pagat no es perd.

    Hi van les files del chunk (ja a la sessio), la fotografia de la cobertura, els
    comptadors i els tokens acumulats, el cost acumulat del run i, si el run es
    `real`, la linia del llibre de despesa (§6.7). El run continua RUNNING i no
    vigent: nomes `_finalise_run` el tanca i el pot promoure.
    """
    run.chunk_coverage = coverage
    run.requirements_extracted = tally.requirements
    run.requirements_rejected = tally.rejected
    run.award_criteria_extracted = tally.award_criteria
    run.evidence_rejected = tally.evidence_rejected
    run.validation_errors = tally.validation_errors
    run.input_tokens = sum(item.input_tokens or 0 for item in chunk_usage.values()) or None
    run.output_tokens = sum(item.output_tokens or 0 for item in chunk_usage.values()) or None
    cost = llm_budget.chunk_cost(
        run.model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
    )
    if real_spend:
        llm_budget.record_chunk_spend(
            session,
            run=run,
            chunk_index=chunk_index,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            scope=scope,
        )
    if cost is not None:
        run.estimated_cost = float(llm_budget.usd(Decimal(str(run.estimated_cost or 0)) + cost))
    await session.commit()


# --------------------------------------------------------------------------- #
# Represa d'un run parcial                                                      #
# --------------------------------------------------------------------------- #


#: Fallades d'execucio (no del model) despres de les quals el treball pagat dels
#: chunks completats es pot reprendre (3B-2 §6.6).
RESUMABLE_FAILURE_CODES = frozenset({"worker_lost", "internal_error"})


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
    superseded: bool = False,
) -> ResumePlan:
    """Nomes es repren si es EXACTAMENT la mateixa analisi. Pura: no escriu res.

    Mateixa licitacio; mateix `input_fingerprint` (que ja inclou documents,
    revisio d'extraccio, contingut i parser); mateixes versions d'extractor,
    prompt i esquema; mateix proveidor i model; i el mateix pla de chunks, un a
    un.

    Es repren un run parcial, o un de fallit per `worker_lost`/`internal_error`
    amb algun chunk completat (3B-2 §6.6). Un parcial ja no es mai vigent
    (§6.3): el que no pot passar es que n'hi hagi un de MES NOU (`superseded`,
    `run_is_superseded`), perque reprendre l'antic barrejaria dues fotografies.
    """

    def refuse(reason: str, message: str) -> ResumeNotAllowedError:
        return ResumeNotAllowedError(message, details={"reason": reason})

    if source is None or source.tender_id != tender_id:
        raise refuse("run_not_found", "No hi ha cap run d'aquesta licitacio per reprendre.")
    resumable_failure = (
        source.status == AnalysisRunStatus.FAILED
        and source.error_code in RESUMABLE_FAILURE_CODES
        and (source.chunks_completed or 0) > 0
    )
    if source.status != AnalysisRunStatus.PARTIAL and not resumable_failure:
        raise refuse("run_not_partial", "Nomes es pot reprendre un run parcial.")
    if superseded:
        raise refuse("run_not_current", "Hi ha un run mes nou d'aquesta licitacio.")
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
    metadata = source.provider_metadata if isinstance(source.provider_metadata, dict) else {}
    if metadata.get(TRANSPORT_METADATA_KEY) != transport_identity(profile, provider):
        # Mai es barregen chunks de transports diferents (estricte v1/v2, tool no estricta).
        raise refuse("transport_mismatch", "El run es va fer amb un altre transport de la tool.")

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
    uncertainty_ledger: UncertaintyLedger | None = None,
) -> None:
    """Copia al run nou els requisits i criteris del run d'origen, amb les cites, i
    les incerteses materials (product-v2, 0062): mateixa provenance, sense duplicar.

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

    # Incerteses: totes venen de chunks bons, com els requisits. No sumen a cap
    # comptador de requisits ni criteris (no ho son).
    uncertainties = (
        await session.scalars(
            sa.select(RequirementUncertainty)
            .where(RequirementUncertainty.run_id == source.id)
            .order_by(RequirementUncertainty.source_chunk_index, RequirementUncertainty.created_at)
        )
    ).all()
    # Passen pel mateix registre que les deteccions noves: els chunks que ara s'envien
    # en tenen en compte la qualitat, i el resultat es el d'un run sencer.
    ledger = uncertainty_ledger if uncertainty_ledger is not None else UncertaintyLedger(session)
    for original in uncertainties:
        ledger.offer(_copy_columns(original, RequirementUncertainty, run_id=run.id))


#: Codi d'un chunk que no s'ha enviat perque un error fatal ha aturat el run.
CHUNK_NOT_SENT = "not_sent"
#: Codi d'un chunk que l'operador no va seleccionar (`only_chunks`). No es cap
#: error del model: simplement no es va enviar, i una represa l'enviara.
CHUNK_NOT_SELECTED = NOT_SELECTED
#: Codi d'un chunk encara no tractat a la fotografia d'un run en curs (commit per
#: chunk, 3B-2 §6.6). Si el worker mor, es queda aixi i una represa l'enviara.
CHUNK_PENDING = "pending"
#: Codis de chunks que no han arribat al proveidor (`origin = not_sent`).
UNSENT_CHUNK_CODES = frozenset({CHUNK_PENDING, llm_budget.BUDGET_EXCEEDED})
#: Codi d'un run real complet que no es promou perque era `force` en una licitacio
#: publica (3B-2 §6.4): `force` nomes el fa l'operador, i mai es canonic.
FORCED_NOT_CANONICAL = "forced_not_canonical"


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
    identity: str | None = None,
    transport: dict[str, Any] | None = None,
) -> RequirementExtractionRun:
    """Reserva l'analisi. L'index unic parcial impedeix el doble clic.

    `work_job_id` s'escriu a la mateixa transaccio que crea el run: no hi ha cap
    moment en que el run existeixi sense saber de quin job ve. La base del run
    (`run_basis`) i la identitat d'analisi tambe hi neixen.
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
        run_basis=RequirementRunBasis.for_provider(llm.name),
        analysis_identity=identity,
        provider_metadata={TRANSPORT_METADATA_KEY: transport} if transport is not None else None,
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
) -> list[dict[str, Any]]:
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
    diagnostics: list[dict[str, Any]] = []
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


#: Quantes claus d'arrel es descriuen com a molt en un `core_missing`.
MAX_ROOT_SHAPE_FIELDS = 20


def root_shape_diagnostic(
    data: Any, diagnostics: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Que hi havia a l'arrel quan hi falta un camp obligatori. Nomes la forma.

    Si l'error diu que falta un camp d'arrel (`missing` a un path d'un sol
    segment) i la resposta es un objecte, es descriu cada clau d'arrel present:
    el nom si es un identificador, si no `<text,N chars>`, i la categoria de
    tipus del valor. Mai el valor, ni res de dins. Sense aixo, "requirements
    missing" no diu si el model ho va posar sota una altra clau o no va dir res.
    """
    if not isinstance(data, Mapping):
        return None
    if not any(
        d.get("type") == "missing" and "." not in str(d.get("path", "")) for d in diagnostics
    ):
        return None
    fields = [
        {"field": describe_root_key(key), "type": json_type(value)}
        for key, value in list(data.items())[:MAX_ROOT_SHAPE_FIELDS]
    ]
    entry: dict[str, Any] = {"stage": "root", "reason": "core_missing", "present_fields": fields}
    if len(data) > MAX_ROOT_SHAPE_FIELDS:
        entry["present_fields_total"] = len(data)
    # Un embolcall d'una sola clau que no s'ha pogut desembolcallar: la forma
    # del primer nivell de dins (noms segurs i tipus), per saber-ne el per que.
    if len(data) == 1:
        [inner] = data.values()
        if isinstance(inner, Mapping):
            entry["inner_fields"] = [
                {"field": describe_root_key(key), "type": json_type(value)}
                for key, value in list(inner.items())[:MAX_ROOT_SHAPE_FIELDS]
            ]
    return entry


def describe_root_key(key: Any) -> str:
    """El nom d'una clau d'arrel nomes si no pot ser text del plec."""
    text = str(key)
    return text if _IDENTIFIER.fullmatch(text) else f"<text,{len(text)} chars>"


def json_type(value: Any) -> str:
    """La categoria JSON d'un valor, sense mirar-ne el contingut."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return "other"


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

    Abans de Pydantic passa per `normalise_output`, que nomes desfa els
    patrons coneguts: llista serialitzada com a text, `extraction_id` del
    context repetit a l'arrel i (v3.2) un sobre embolcallat sota una sola clau
    quan el de dins porta tots els camps obligatoris. Les regles aplicades
    s'afegeixen a `normalisations`, tambe quan despres l'esquema falla.
    """
    # El canonic del perfil (tool forcada o `auto` sense `strict`, on nomes guia el model) o
    # l'esquema de transport estricte. El parser del perfil valida la resposta igual, i
    # l'envoltori es valida contra AQUEST esquema (el que s'envia).
    sent_schema = transport_json_schema(profile, llm)
    response = await llm.extract_structured(
        system=profile.system_prompt,
        user_content=profile.build_user_message(render_chunk(chunk)),
        json_schema=sent_schema,
        max_output_tokens=settings.LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS,
    )
    usage = response.usage or Usage()
    # El proveidor d'Anthropic ja ho refusa; aqui es cobreix qualsevol altre.
    # Un payload tallat que valida continua sent un chunk incomplet.
    if usage.stop_reason == "max_tokens":
        raise OutputTruncatedError(
            "La resposta ha topat amb el limit de sortida.", usage=usage
        )
    auto = requires_auto_tool(llm)
    absent_rules: list[str] = []
    if auto:
        # Sense tool forcada ni gramatica estricta, ningu no garanteix la forma de la resposta:
        # obligatoris de l'esquema enviat, almenys una llista (cap `{}` acceptat com a chunk
        # fet), cap clau inventada i nomes llistes. Si no, FATAL. Una llista OPCIONAL absent
        # s'accepta (el parser hi posa el seu valor per defecte) i queda registrada a la
        # cobertura: absent no vol dir buida. Els elements, un a un, com sempre.
        envelope = auto_tool_envelope_check(response.data, profile, schema=sent_schema)
        if envelope.problem is not None:
            error = IllegalEnvelopeError(
                f"L'envoltori de la tool no es el canonic ({envelope.problem}).", usage=usage
            )
            error.fatal = True
            raise error
        absent_rules = envelope.rules
    # Cap normalitzacio indulgent per als models `auto` (ni a l'envoltori ni dins dels
    # elements): cap JSON dins d'un text es descodifica; el parser decideix sobre el que ha
    # arribat tal com ha arribat.
    data, applied = (
        (response.data, absent_rules)
        if auto
        else normalise_output(
            response.data,
            profile.output_model,
            context_extraction_ids={str(chunk.document.extraction_id)},
        )
    )
    if normalisations is not None:
        normalisations.extend(applied)
    try:
        parsed = profile.parse(data)
    except PydanticValidationError as exc:
        diagnostics = schema_diagnostics(exc, known_schema_fields(profile.json_schema()))
        root_shape = root_shape_diagnostic(data, diagnostics)
        if root_shape is not None:
            diagnostics.append(root_shape)
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


def uncertainty_identity(
    tender_document_id: str,
    page_number: int,
    scope: str,
    *,
    note: str,
    literal_quote: str | None,
) -> tuple[str, str]:
    """`(grup, clau)` d'una incertesa dins d'un run. Sense cap comparacio semantica.

    * **Grup** = la mateixa incertesa real: document, pagina declarada, ambit i nota
      normalitzada (`normalize_quote`: espais, cometes, guions i majuscules). La mateixa
      nota en una altra pagina o en un altre document es un altre grup: mai es fusionen.
    * **Clau** (`dedup_key`, unica per run) = grup + la cita literal normalitzada si n'hi
      ha; si no, grup + `note`. Dins d'un grup, cada cita literal diferent es una
      ocurrencia diferent; sense cita literal, el grup es una sola fila.
    """
    base = [tender_document_id, page_number, scope, normalize_quote(note)]

    def digest(parts: list[Any]) -> str:
        payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    group = digest(base)
    if literal_quote:
        return group, digest([*base, "literal", normalize_quote(literal_quote)])
    return group, digest([*base, "note"])


def uncertainty_provenance_verified(diagnosis: str | None) -> bool:
    """Cert si l'extraccio i la pagina declarades son les del chunk (es dedueix del codi
    de `diagnosis`, que es l'unic lloc on queda escrit)."""
    reason = (diagnosis or "").split(":", 1)[0]
    return reason not in (EVIDENCE_EXTRACTION_MISMATCH, EVIDENCE_PAGE_NOT_IN_CHUNK)


def uncertainty_rank(quote_status: str, diagnosis: str | None) -> int:
    """Qualitat de la procedencia: `literal > rejected > not_provided`, i dins de cada
    estat no literal, la procedencia verificada per davant de la que no ho es.

    4 literal (sempre verificada) · 3 rejected verificada · 2 not_provided verificada ·
    1 rejected no verificada · 0 not_provided no verificada.
    """
    if quote_status == UncertaintyQuoteStatus.LITERAL:
        return 4
    rejected = quote_status == UncertaintyQuoteStatus.REJECTED
    if uncertainty_provenance_verified(diagnosis):
        return 3 if rejected else 2
    return 1 if rejected else 0


def _uncertainty_order(row: RequirementUncertainty) -> tuple[Any, ...]:
    """Ordre total de qualitat (el menor guanya): rang, i en empat el chunk mes baix del
    pla i, a la fi, el contingut. Nomes camps desats: la tria no depen de l'ordre
    d'arribada dels chunks ni de si una fila ve d'una represa."""
    return (
        -uncertainty_rank(str(row.quote_status), row.diagnosis),
        row.source_chunk_index,
        row.diagnosis or "",
        row.note,
        -1 if row.char_start is None else row.char_start,
        row.quote or "",
    )


#: Camps que una millora de procedencia reescriu en una fila del MATEIX run en curs.
_UNCERTAINTY_SUPERSEDABLE = (
    "source_chunk_index",
    "note",
    "extraction_id",
    "tender_document_id",
    "page_number",
    "page_id",
    "quote",
    "char_start",
    "char_end",
    "quote_status",
    "diagnosis",
    "dedup_key",
)

UNCERTAINTY_ADDED = "added"
UNCERTAINTY_DUPLICATE = "duplicate"
UNCERTAINTY_SUPERSEDED = "superseded"
#: Motiu de cobertura quan una deteccio millor substitueix la d'un altre chunk.
UNCERTAINTY_REPLACED_LOWER = "replaced_lower_provenance"


@dataclass(slots=True)
class _UncertaintySlot:
    row: RequirementUncertainty
    key: str
    literal: bool
    order: tuple[Any, ...]


class UncertaintyLedger:
    """Les incerteses d'UN run en construccio, amb la regla de millor procedencia.

    Per a cada grup (`uncertainty_identity`), l'estat final es una funcio del conjunt de
    deteccions, no de l'ordre en que arriben:

    * si alguna deteccio del grup te cita literal: una fila per cita literal diferent (la
      millor de cada una); les deteccions sense cita literal del grup s'hi absorbeixen;
    * si no n'hi ha cap: una sola fila, la millor (`_uncertainty_order`).

    Una deteccio millor que arriba despres REESCRIU la fila del mateix run (mateix `id`):
    es l'unica escriptura sobre una fila desada, i nomes mentre el run es construeix.
    Les files d'un altre run (l'origen d'una represa) no es toquen mai: es copien.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._groups: dict[str, dict[str, _UncertaintySlot]] = {}

    def offer(self, candidate: RequirementUncertainty) -> tuple[str, int | None]:
        """Ofereix una deteccio (transitoria). Torna `(resultat, chunk substituit)`."""
        group, key = uncertainty_identity(
            str(candidate.tender_document_id),
            candidate.page_number,
            str(candidate.scope),
            note=candidate.note,
            literal_quote=candidate.quote,
        )
        candidate.dedup_key = key
        literal = candidate.quote_status == UncertaintyQuoteStatus.LITERAL
        order = _uncertainty_order(candidate)
        slots = self._groups.setdefault(group, {})
        if literal:
            weaker = next((slot for slot in slots.values() if not slot.literal), None)
            if weaker is not None:
                # Cap literal al grup fins ara: la literal substitueix la fila sense cita.
                del slots[weaker.key]
                return self._supersede(slots, weaker, candidate, key, literal, order)
            existing = slots.get(key)
        else:
            if any(slot.literal for slot in slots.values()):
                return UNCERTAINTY_DUPLICATE, None
            existing = next(iter(slots.values()), None)
        if existing is None:
            self._session.add(candidate)
            slots[key] = _UncertaintySlot(candidate, key, literal, order)
            return UNCERTAINTY_ADDED, None
        if order < existing.order:
            del slots[existing.key]
            return self._supersede(slots, existing, candidate, key, literal, order)
        return UNCERTAINTY_DUPLICATE, None

    @staticmethod
    def _supersede(
        slots: dict[str, _UncertaintySlot],
        old: _UncertaintySlot,
        candidate: RequirementUncertainty,
        key: str,
        literal: bool,
        order: tuple[Any, ...],
    ) -> tuple[str, int | None]:
        replaced_chunk = old.order[1]  # sense llegir la fila (pot estar expirada)
        for field in _UNCERTAINTY_SUPERSEDABLE:
            setattr(old.row, field, getattr(candidate, field))
        slots[key] = _UncertaintySlot(old.row, key, literal, order)
        return UNCERTAINTY_SUPERSEDED, replaced_chunk


#: Etapa del rebuig d'una procedencia declarada (sense cita) que no es pot verificar.
UNCERTAINTY_STAGE_PROVENANCE = "provenance"


def _persist_uncertainties(
    *,
    session: AsyncSession,
    run: RequirementExtractionRun,
    tender: Tender,
    chunk: Chunk,
    parsed: Any,
    ledger: UncertaintyLedger,
    record: Callable[[dict[str, Any]], None],
) -> None:
    """Desa les incerteses materials del chunk (product-v2, 0062).

    La incertesa es conserva sempre que l'esquema la valida. Sempre amb l'extraccio i
    el document REALS del chunk (mai els que declara el model) i la pagina declarada:

    * La procedencia es verifica SEMPRE, amb cita o sense: si l'extraccio declarada no
      es la del chunk o la pagina no hi es, `diagnosis` ho diu i hi ha un rebuig a la
      cobertura. Mai es desa una procedencia dubtosa en silenci.
    * La cita, NOMES si es literal a la pagina declarada del chunk: llavors porta
      `page_id`, el text exacte de la pagina i els offsets. Si no ho es, no es desa
      (una parafrasi no es evidencia): `quote_status = rejected` amb el diagnostic de
      codis, i a la cobertura el mateix rebuig que una cita de requisit.

    Les incerteses que l'esquema ha descartat (`parse_chunk_product_v2`) arriben com a
    rebutjos de codis. La deduplicacio i la tria de la millor procedencia son del
    `UncertaintyLedger` del run. Mai incrementa cap comptador de requisits o criteris.
    """
    for entry in getattr(parsed, "uncertainty_rejections", ()) or ():
        record(dict(entry))
    items = list(getattr(parsed, "uncertainties", []) or [])
    positions = list(getattr(parsed, "uncertainty_indexes", ()) or range(len(items)))
    by_number = {page.page_number: page for page in chunk.pages}
    texts = {page.page_number: page.text for page in chunk.pages}
    max_quote = settings.REQUIREMENTS_MAX_QUOTE_CHARS
    document_id = chunk.document.tender_document_id
    for item, index in zip(items, positions, strict=True):
        status = UncertaintyQuoteStatus.NOT_PROVIDED
        page_id = quote = char_start = char_end = diagnosis = None
        page = by_number.get(item.page_number)
        if item.extraction_id != chunk.document.extraction_id:
            provenance: str | None = EVIDENCE_EXTRACTION_MISMATCH
        elif page is None:
            provenance = EVIDENCE_PAGE_NOT_IN_CHUNK
        else:
            provenance = None
        raw_quote = (item.quote or "").strip()
        if raw_quote:
            candidate = raw_quote[:max_quote]
            match = None
            if provenance == EVIDENCE_EXTRACTION_MISMATCH:
                reason, detail = provenance, None
            elif provenance == EVIDENCE_PAGE_NOT_IN_CHUNK:
                reason = provenance
                detail = diagnose_missing_quote(texts, item.page_number, candidate)
            else:
                match = locate_quote(page.text, candidate)
                reason = EVIDENCE_QUOTE_NOT_FOUND
                detail = (
                    None if match else diagnose_missing_quote(texts, item.page_number, candidate)
                )
            if match is not None:
                status = UncertaintyQuoteStatus.LITERAL
                page_id = uuid.UUID(page.page_id)
                quote, char_start, char_end = match.exact_text, match.char_start, match.char_end
            else:
                status = UncertaintyQuoteStatus.REJECTED
                diagnosis = f"{reason}:{detail}" if detail else reason
                entry = {"item": "uncertainty", "index": index, "stage": "evidence",
                         "reason": reason, "cited_page": item.page_number}
                if detail:
                    entry["diagnosis"] = detail
                record(entry)
        elif provenance is not None:
            diagnosis = provenance
            record({"item": "uncertainty", "index": index,
                    "stage": UNCERTAINTY_STAGE_PROVENANCE, "reason": provenance,
                    "cited_page": item.page_number})

        outcome, replaced_chunk = ledger.offer(
            RequirementUncertainty(
                run_id=run.id,
                tender_id=tender.id,
                source_chunk_index=chunk.index,
                scope=item.scope,
                note=item.note.strip(),
                extraction_id=uuid.UUID(chunk.document.extraction_id),
                tender_document_id=uuid.UUID(document_id),
                page_number=item.page_number,
                page_id=page_id,
                quote=quote,
                char_start=char_start,
                char_end=char_end,
                quote_status=status,
                diagnosis=diagnosis[:80] if diagnosis else None,
            )
        )
        if outcome == UNCERTAINTY_DUPLICATE:
            record({"item": "uncertainty", "index": index, "stage": "dedup",
                    "reason": ITEM_DUPLICATE_KEY})
        elif outcome == UNCERTAINTY_SUPERSEDED:
            record({"item": "uncertainty", "index": index, "stage": "dedup",
                    "reason": UNCERTAINTY_REPLACED_LOWER, "replaced_chunk": replaced_chunk})


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
    uncertainty_ledger: UncertaintyLedger | None = None,
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

    # product-v2.1: requisits i criteris descartats SOLS per l'esquema (`parse_chunk_product_v2`).
    # Compten com a rebutjats, i les posicions que es reporten son les ORIGINALS de la resposta.
    # V1 i v2 no en tenen: cap canvi.
    schema_rejections = list(getattr(parsed, "schema_rejections", ()) or ())
    for entry in schema_rejections:
        record(dict(entry))
    tally.rejected += len(schema_rejections)
    requirement_positions = list(
        getattr(parsed, "requirement_indexes", ()) or range(len(parsed.requirements))
    )
    award_positions = list(
        getattr(parsed, "award_indexes", ()) or range(len(parsed.award_criteria))
    )

    for item, index in zip(parsed.requirements, requirement_positions, strict=True):
        evidence, reasons = validate_evidence_detailed(item.evidence, chunk)
        tally.evidence_rejected += len(reasons)
        for detail in evidence_rejection_details(item.evidence, chunk) if reasons else []:
            record({"item": "requirement", "index": index, "stage": "evidence", **detail})
        if not evidence:
            # Sense cita verificable no es un requisit: nomes un candidat.
            tally.rejected += 1
            record({"item": "requirement", "index": index, "stage": "item",
                    "reason": ITEM_NO_VALID_EVIDENCE,
                    "obligation": str(item.obligation), "category": str(item.category)})
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

    for item, index in zip(parsed.award_criteria, award_positions, strict=True):
        evidence, reasons = validate_evidence_detailed(item.evidence, chunk)
        tally.evidence_rejected += len(reasons)
        for detail in evidence_rejection_details(item.evidence, chunk) if reasons else []:
            record({"item": "award_criterion", "index": index, "stage": "evidence", **detail})
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

    # Les incerteses, DESPRES dels requisits i criteris: els seus rebutjos no poden fer
    # fora de la cobertura (limit per chunk) els diagnostics dels requisits.
    if profile.material_uncertainties:
        _persist_uncertainties(
            session=session,
            run=run,
            tender=tender,
            chunk=chunk,
            parsed=parsed,
            ledger=(
                uncertainty_ledger
                if uncertainty_ledger is not None
                else UncertaintyLedger(session)
            ),
            record=record,
        )


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
        needs_review = (
            lot_needs_review
            # v3.1: rescatat sense algun claim invalid. Ha perdut informacio, i
            # una persona ho ha de mirar encara que la resta estigui sostinguda.
            or index in parsed.salvaged_requirements
            or support.status in (
                RequirementSupportStatus.NEEDS_REVIEW,
                RequirementSupportStatus.NOT_SUPPORTED,
            )
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

        # La regla de taula (v3.2) mira la capcalera de la MATEIXA pagina validada.
        page_text = {page.page_id: page.text for page in chunk.pages}
        decision = classify_award(
            item,
            {eid: v.quote for eid, v in validated.items()},
            {eid: page_text.get(str(v.page_id), "") for eid, v in validated.items()},
        )
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


async def carry_over_in_savepoint(
    session: AsyncSession, old_run_id: uuid.UUID, new_run_id: uuid.UUID
) -> None:
    """Les anotacions d'estructura de la run antiga passen a la nova per `dedup_key`
    (0060, 3B-2 §4.3). En un punt de restauracio: si fallen, es registra i el que
    les ha provocat (el tancament d'una run ja pagada, l'adopcio en una licitacio
    manual) continua. L'unic lloc on es fa aquest pas."""
    try:
        async with session.begin_nested():
            await carry_over_annotations(session, old_run_id, new_run_id)
    except Exception:
        logger.exception(
            "structure_annotations.carry_over_failed",
            extra={"old_run_id": str(old_run_id), "new_run_id": str(new_run_id)},
        )


#: Codi d'un run real acabat que no es promou perque el corpus ha canviat.
INPUT_CHANGED = "input_changed"
#: Codi d'un run real acabat que no es promou perque la seva seleccio de documents
#: no es la canonica (3B-2 §6.4).
SELECTION_NOT_CANONICAL = "selection_not_canonical"
#: El run es d'un perfil experimental (product-v2): es conserva, pero mai es la base
#: vigent.
EXPERIMENTAL_PROFILE_NOT_CURRENT = "experimental_profile_not_current"


async def _may_become_current(
    session: AsyncSession,
    run: RequirementExtractionRun,
    *,
    document_ids: list[uuid.UUID] | None,
    force: bool = False,
) -> bool:
    """Les tres condicions de §6.3. Si falla la del corpus, ho deixa escrit al run.

    L'entrada actual es la de la seleccio del run: la canonica si no en tenia, o la
    seva (`document_ids`). Si ha canviat mentre s'analitzava, `input_changed`. Una
    seleccio explicita nomes es la base vigent en una licitacio MANUAL (els plecs
    son del propietari, que tria que s'analitza); en una de publica, si no coincideix
    amb la canonica, `selection_not_canonical` (§6.4). Igual amb `force`: en una
    licitacio publica nomes el fa l'operador, i aquell run mai es canonic
    (`forced_not_canonical`); en una manual, com sempre.
    """
    if run.status != AnalysisRunStatus.COMPLETED or run.run_basis != RequirementRunBasis.REAL:
        return False
    if experimental_profile_of(run) is not None:
        # Un perfil experimental no es mai la base vigent de ningu, en cap licitacio:
        # el seu run nomes s'ha fet perque el codi el va passar (canari).
        run.error_code = EXPERIMENTAL_PROFILE_NOT_CURRENT
        run.error_message = (
            "Run d'un perfil experimental: es conserva, pero mai no es la base vigent."
        )
        logger.warning(
            "requirements.run.not_promoted",
            extra={"run_id": str(run.id), "reason": run.error_code},
        )
        return False
    own = await analysis_input(session, run.tender_id, document_ids=document_ids or None)
    owner = await session.scalar(
        sa.select(Tender.owner_company_id).where(Tender.id == run.tender_id)
    )
    if own.fingerprint != run.input_fingerprint:
        run.error_code = INPUT_CHANGED
        run.error_message = (
            "Els plecs han canviat mentre s'analitzaven: el resultat es conserva, "
            "pero no es la base vigent."
        )
    elif force and owner is None:
        run.error_code = FORCED_NOT_CANONICAL
        run.error_message = (
            "Run forcat en una licitacio publica: es conserva, pero mai no es la base vigent."
        )
    elif not document_ids:
        return True
    else:
        if owner is not None:
            return True
        canonical = await analysis_input(session, run.tender_id)
        if canonical.fingerprint == run.input_fingerprint:
            return True
        run.error_code = SELECTION_NOT_CANONICAL
        run.error_message = "La seleccio de documents no es la canonica: no es la base vigent."
    logger.warning(
        "requirements.run.not_promoted",
        extra={"run_id": str(run.id), "reason": run.error_code},
    )
    return False


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
    document_ids: list[uuid.UUID] | None = None,
    force: bool = False,
    exceeded_scope: LlmBudgetScope | None = None,
    transport: dict[str, Any] | None = None,
) -> None:
    """Tanca el run i decideix si passa a vigent.

    Nomes hi passa (3B-2 §6.3, MUST) un run `real` + `COMPLETED` + amb l'entrada
    canonica actual. Si no, el run anterior es queda: una analisi fallida,
    parcial o simulada no pot deixar sense requisits (ni amb requisits falsos) una
    licitacio que ja en tenia.

    * Un run no `real` (fake/fixture) mai no es vigent.
    * Un PARTIAL mai no es vigent (nomes el pot llegir una empresa que l'adopti).
    * Un run real el corpus del qual ja no es l'actual en tancar-lo no es promou:
      `error_code=input_changed`, status COMPLETED (el treball pagat es conserva).
    * Un run real COMPLETED sense cap requisit ni criteri sobre >= 5 pagines
      analitzades queda `completed_empty` amb `needs_review`, i no es promou.
    * Un run aturat per un cap de despesa (`exceeded_scope`, §6.7) queda `PARTIAL`
      (o `FAILED` sense cap chunk completat) amb `error_code=budget_exceeded` i
      l'scope excedit a `provider_metadata.budget_exceeded_scope`: nomes una
      autoritzacio posterior d'aquell scope o de `pilot_total` el reobre.
    * Un run `force` d'una licitacio publica no es promou (`forced_not_canonical`).

    Les anotacions d'estructura del run antic (§4.3) nomes passen al nou quan
    aquest esdeve vigent.

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
    if transport is not None:
        # El transport dels models `auto` (estricte v1/v2 o de tool): la represa el compara.
        metadata[TRANSPORT_METADATA_KEY] = transport
    if resumed_from is not None:
        metadata[llm_budget.RESUMED_FROM_KEY] = str(resumed_from.source.id)
        metadata["reused_chunks"] = sorted(resumed_from.reused)
    if exceeded_scope is not None:
        metadata[llm_budget.EXCEEDED_SCOPE_KEY] = exceeded_scope.value
    if document_ids:
        # La seleccio explicita del run: la seva obsolescencia es mira nomes sobre
        # aquests documents (`run_document_selection`).
        metadata[SELECTION_METADATA_KEY] = sorted(str(item) for item in document_ids)
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
    elif (
        run.run_basis == RequirementRunBasis.REAL
        and not tally.requirements
        and not tally.award_criteria
        and run.pages_analyzed >= COMPLETED_EMPTY_MIN_PAGES
    ):
        # Cap requisit ni criteri en un plec de moltes pagines: o el plec no en
        # diu res o l'extraccio ha fallat en silenci. Ho decideix una persona.
        run.status = AnalysisRunStatus.COMPLETED_EMPTY
        run.needs_review = True
    else:
        run.status = AnalysisRunStatus.COMPLETED

    if exceeded_scope is not None:
        # No s'ha llegit tot perque s'ha superat un cap: no es cap error del model.
        # Una represa (amb una autoritzacio nova) enviara el que falta.
        run.status = (
            AnalysisRunStatus.PARTIAL if run.chunks_completed else AnalysisRunStatus.FAILED
        )
        run.error_code = llm_budget.BUDGET_EXCEEDED
        run.error_message = last_error

    if await _may_become_current(session, run, document_ids=document_ids, force=force):
        previous = await current_run_for(session, run.tender_id)
        if previous is not None and previous.id != run.id:
            previous.is_current = False
            await session.flush()
            await carry_over_in_savepoint(session, previous.id, run.id)
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
