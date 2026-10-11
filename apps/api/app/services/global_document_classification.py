"""Classificacio GLOBAL dels documents d'una licitacio publica (3B-2 §3.2, §2.5).

La mateixa classificacio pura de sempre (`app/classification/roles.py`), pero
alimentada NOMES amb fets globals: el nom del document (com a ruta i nom de
fitxer), el sha del contingut vigent i el text de l'extraccio vigent. Cap ruta de
recepcio de cap empresa, cap parametre de tenant. Per aixo l'empremta es la
mateixa per a tothom i el resultat es pot compartir.

* **Qui la produeix**: el worker despres de processar documents
  (`document_processing`), la incorporacio de l'operador (`document_intake`) i
  l'operador a peticio (`operator_request`). Determinista i sense cost.
* **Immutable i idempotent**: mateixa empremta, mateixa passada (cap fila nova);
  una passada antiga amb la mateixa empremta torna a ser la vigent.
* **Nomes analitzables**: vigents, no alies, del feed o verificats
  (`ANALYZABLE_DOCUMENT`), i amb contingut desat.
* **Nomes licitacions publiques**: una manual es d'una empresa i es classifica a
  les taules tenant (`document_classification`).
* **Mai escriu `document_type`**: es del feed. El que llegeix l'analisi es el rol
  efectiu del resolutor (`app/services/document_roles.py`).

Les troballes de conjunt (`required_role_missing`, `referenced_document_missing`,
`no_text`, `format_not_analyzable`) son els forats del paquet
(`tender_package_gaps`, productor `classification`): es creen quan apareixen i
es RESOLEN (mai s'esborren) quan una passada posterior ja no els veu.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.classification import ooxml_text
from app.classification.roles import (
    CLASSIFIER_VERSION,
    Classification,
    DocumentFacts,
    classify_documents,
)
from app.models.enums import (
    ClassificationFindingCode,
    ExtractionStatus,
    GlobalClassificationTrigger,
    PackageGapCode,
    PackageGapProducer,
    SourceType,
)
from app.models.global_classification import (
    DocumentRoleReview,
    GlobalDocumentClassificationFinding,
    GlobalDocumentClassificationRun,
    GlobalDocumentRoleSuggestion,
)
from app.models.package import TenderPackageGap
from app.models.sources import Source
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender, TenderDocument
from app.services.storage import get_storage
from app.tenders.documents import ANALYZABLE_DOCUMENT

logger = logging.getLogger(__name__)

#: Troballes de la classificacio que son un forat del paquet.
GAP_CODES: dict[ClassificationFindingCode, PackageGapCode] = {
    ClassificationFindingCode.REQUIRED_ROLE_MISSING: PackageGapCode.REQUIRED_ROLE_MISSING,
    ClassificationFindingCode.REFERENCED_DOCUMENT_MISSING: (
        PackageGapCode.REFERENCED_DOCUMENT_MISSING
    ),
    ClassificationFindingCode.NO_TEXT: PackageGapCode.NO_TEXT,
    ClassificationFindingCode.FORMAT_NOT_ANALYZABLE: PackageGapCode.FORMAT_NOT_ANALYZABLE,
}

#: Que aportaria un rol obligatori que falta. Generic: el PCAP i el PPT son els
#: mateixos a qualsevol licitacio.
ROLE_WOULD_PROVIDE: dict[str, list[str]] = {
    "pcap": ["requirements", "award_criteria"],
    "ppt": ["requirements", "scope_items"],
}


def would_provide(code: PackageGapCode, key: str) -> list[str]:
    """Que aportaria el document que falta. `[]` vol dir impacte desconegut.

    Un document citat que falta (`referenced_document_missing`) dona SEMPRE `[]`:
    els codis DOC-NN els posa cada organ de contractacio a la seva primera pagina
    i no signifiquen el mateix d'un expedient a un altre (el DOC-03 de l'Escala
    es el quadre de caracteristiques; en un altre, qualsevol cosa). El codi, les
    cites i els fragments es queden a `detail`; el paquet consta com a incomplet
    sense dir quina seccio. Quan l'operador declara un forat
    (`declared_by_operator`) hi pot posar el `would_provide` que sap cert.
    """
    if code == PackageGapCode.REFERENCED_DOCUMENT_MISSING:
        return []
    if code == PackageGapCode.REQUIRED_ROLE_MISSING:
        return list(ROLE_WOULD_PROVIDE.get(key, []))
    if code == PackageGapCode.NO_TEXT:
        # Un plec sense text no aporta cap requisit a l'extraccio.
        return ["requirements"]
    return []


# --------------------------------------------------------------------------- #
# Entrada: nomes fets globals                                                  #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Input:
    document: TenderDocument
    content: TenderDocumentContent
    extraction: TenderDocumentExtraction | None


async def is_public_tender(session: AsyncSession, tender: Tender) -> bool:
    """D'una font i sense propietari: la mateixa regla que l'operador (`load_public_tender`)."""
    if tender.owner_company_id is not None:
        return False
    source_type = await session.scalar(
        sa.select(Source.source_type).where(Source.id == tender.source_id)
    )
    return source_type != SourceType.MANUAL


async def _inputs(session: AsyncSession, tender_id: uuid.UUID) -> list[_Input]:
    """Els documents analitzables amb contingut desat, en ordre d'arribada."""
    rows = (
        await session.execute(
            sa.select(TenderDocument, TenderDocumentContent)
            .join(
                TenderDocumentFile,
                sa.and_(
                    TenderDocumentFile.tender_document_id == TenderDocument.id,
                    TenderDocumentFile.is_current.is_(True),
                ),
            )
            .join(TenderDocumentContent, TenderDocumentContent.id == TenderDocumentFile.content_id)
            .where(TenderDocument.tender_id == tender_id, ANALYZABLE_DOCUMENT)
            .order_by(TenderDocument.created_at, TenderDocument.id)
        )
    ).all()
    if not rows:
        return []
    extractions = {
        e.content_id: e
        for e in await session.scalars(
            sa.select(TenderDocumentExtraction).where(
                TenderDocumentExtraction.content_id.in_([c.id for _, c in rows]),
                TenderDocumentExtraction.is_current.is_(True),
            )
        )
    }
    return [_Input(document, content, extractions.get(content.id)) for document, content in rows]


def global_fingerprint(
    items: Iterable[tuple[uuid.UUID, str, str, uuid.UUID | None]],
) -> str:
    """sha256(versio + `document_id|sha256|nom|extraccio` ordenats). Res mes."""
    lines = sorted(
        "|".join((str(document_id), sha256, name, str(extraction_id) if extraction_id else "-"))
        for document_id, sha256, name, extraction_id in items
    )
    return hashlib.sha256("\n".join([CLASSIFIER_VERSION, *lines]).encode()).hexdigest()


def _fingerprint(inputs: list[_Input]) -> str:
    return global_fingerprint(
        (
            i.document.id,
            i.content.sha256,
            i.document.name,
            i.extraction.id if i.extraction else None,
        )
        for i in inputs
    )


async def compute_global_fingerprint(session: AsyncSession, tender_id: uuid.UUID) -> str | None:
    """L'empremta que tindria ara la classificacio global. `None` sense documents."""
    inputs = await _inputs(session, tender_id)
    return _fingerprint(inputs) if inputs else None


async def _facts(session: AsyncSession, position: int, item: _Input) -> DocumentFacts:
    pages: tuple[str, ...] | None = None
    source = None
    if item.extraction is not None and item.extraction.status == ExtractionStatus.EXTRACTED:
        rows = await session.scalars(
            sa.select(TenderDocumentPage.text)
            .where(TenderDocumentPage.extraction_id == item.extraction.id)
            .order_by(TenderDocumentPage.page_number)
        )
        pages, source = tuple(rows), "extraction"
    elif item.content.mime_type in (ooxml_text.DOCX, ooxml_text.XLSX):
        text = ooxml_text.read_text(
            await get_storage().download(item.content.storage_key), item.content.mime_type
        )
        if text:
            pages, source = (text,), "ooxml"
    return DocumentFacts(
        key=str(item.document.id),
        position=position,
        # El nom del document es el fet global: el mateix per a totes les empreses.
        path=item.document.name,
        filename=item.document.name,
        mime_type=item.content.mime_type,
        sha256=item.content.sha256,
        pages=pages,
        text_source=source,
    )


# --------------------------------------------------------------------------- #
# Passades                                                                     #
# --------------------------------------------------------------------------- #


async def current_global_run(
    session: AsyncSession, tender_id: uuid.UUID
) -> GlobalDocumentClassificationRun | None:
    return await session.scalar(
        sa.select(GlobalDocumentClassificationRun).where(
            GlobalDocumentClassificationRun.tender_id == tender_id,
            GlobalDocumentClassificationRun.is_current.is_(True),
        )
    )


async def run_suggestions(
    session: AsyncSession, run: GlobalDocumentClassificationRun | None
) -> dict[uuid.UUID, GlobalDocumentRoleSuggestion]:
    if run is None:
        return {}
    rows = await session.scalars(
        sa.select(GlobalDocumentRoleSuggestion).where(GlobalDocumentRoleSuggestion.run_id == run.id)
    )
    return {s.tender_document_id: s for s in rows}


async def run_findings(
    session: AsyncSession, run: GlobalDocumentClassificationRun | None
) -> list[GlobalDocumentClassificationFinding]:
    if run is None:
        return []
    return list(
        (
            await session.scalars(
                sa.select(GlobalDocumentClassificationFinding).where(
                    GlobalDocumentClassificationFinding.run_id == run.id
                )
            )
        ).all()
    )


async def classify_tender_globally(
    session: AsyncSession,
    tender_id: uuid.UUID,
    trigger: GlobalClassificationTrigger,
) -> GlobalDocumentClassificationRun | None:
    """Classifica els documents analitzables d'un tender public i fa commit.

    `None` si la licitacio no es publica o no te cap document analitzable amb
    contingut (llavors la passada vigent, si n'hi havia, deixa de ser-ho: ja no
    descriu cap document que es pugui llegir).
    """
    tender = await session.get(Tender, tender_id)
    if tender is None or not await is_public_tender(session, tender):
        return None
    inputs = await _inputs(session, tender_id)
    previous = await current_global_run(session, tender_id)
    if not inputs:
        if previous is not None:
            previous.is_current = False
            await session.commit()
        return None

    fingerprint = _fingerprint(inputs)
    if previous is not None and previous.input_fingerprint == fingerprint:
        return previous
    existing = await session.scalar(
        sa.select(GlobalDocumentClassificationRun).where(
            GlobalDocumentClassificationRun.tender_id == tender_id,
            GlobalDocumentClassificationRun.input_fingerprint == fingerprint,
        )
    )
    if existing is not None:
        # Els mateixos fets que una passada anterior: torna a ser la vigent.
        if previous is not None:
            previous.is_current = False
            await session.flush()
        existing.is_current = True
        await session.flush()
        await _sync_gaps(session, tender_id, existing)
        await session.commit()
        return existing

    facts = [await _facts(session, position, item) for position, item in enumerate(inputs)]
    result = classify_documents(facts)
    try:
        return await _store_run(
            session, tender_id, trigger, fingerprint, previous, inputs, facts, result
        )
    except IntegrityError:
        # Cursa (worker i operador alhora): una altra sessio ja ha desat una
        # passada (mateixa empremta, o una altra vigent) entre la comprovacio i
        # el flush. No es una fallada: es torna la que hi ha. El rollback nomes
        # desfa aquesta passada (qui crida no porta escriptures pendents: totes
        # les vies fan commit abans de classificar).
        await session.rollback()
        winner = await session.scalar(
            sa.select(GlobalDocumentClassificationRun).where(
                GlobalDocumentClassificationRun.tender_id == tender_id,
                GlobalDocumentClassificationRun.input_fingerprint == fingerprint,
            )
        )
        winner = winner or await current_global_run(session, tender_id)
        if winner is None:  # pragma: no cover - cap altra restriccio pot fallar aqui
            raise
        logger.info(
            "global_classification.race",
            extra={"tender_id": str(tender_id), "run_id": str(winner.id)},
        )
        return winner


async def _store_run(
    session: AsyncSession,
    tender_id: uuid.UUID,
    trigger: GlobalClassificationTrigger,
    fingerprint: str,
    previous: GlobalDocumentClassificationRun | None,
    inputs: list[_Input],
    facts: list[DocumentFacts],
    result: Classification,
) -> GlobalDocumentClassificationRun:
    """Desa la passada nova (immutable), la fa vigent, sincronitza els forats i fa commit."""
    if previous is not None:
        previous.is_current = False
        await session.flush()
    run = GlobalDocumentClassificationRun(
        tender_id=tender_id,
        classifier_version=result.version,
        input_fingerprint=fingerprint,
        trigger=trigger,
        document_count=len(inputs),
        is_current=True,
    )
    session.add(run)
    await session.flush()

    for item, fact in zip(inputs, facts, strict=True):
        decision = result.decisions[fact.key]
        session.add(
            GlobalDocumentRoleSuggestion(
                run_id=run.id,
                tender_document_id=item.document.id,
                content_sha256=item.content.sha256,
                extraction_id=item.extraction.id if item.extraction else None,
                role=decision.role,
                confidence=decision.confidence,
                document_type=decision.document_type,
                signals={**decision.signals(), "text_source": fact.text_source},
            )
        )
    for finding in result.findings:
        session.add(
            GlobalDocumentClassificationFinding(
                run_id=run.id,
                code=finding.code,
                severity=finding.severity,
                tender_document_id=uuid.UUID(finding.key) if finding.key else None,
                related_document_id=uuid.UUID(finding.related_key) if finding.related_key else None,
                detail=finding.detail,
            )
        )
    await session.flush()
    await _sync_gaps(session, tender_id, run)
    await session.commit()
    return run


async def classify_safely(
    session: AsyncSession, tender_id: uuid.UUID, trigger: GlobalClassificationTrigger
) -> GlobalDocumentClassificationRun | None:
    """Per als ganxos (worker, incorporacio): una fallada aqui no tomba la feina feta.

    La classificacio es derivada i es pot refer (l'operador la pot demanar); el
    que no es pot perdre es el processat o la verificacio que ja s'han desat.
    """
    try:
        return await classify_tender_globally(session, tender_id, trigger)
    except Exception:
        logger.exception("global_classification.failed", extra={"tender_id": str(tender_id)})
        await session.rollback()
        return None


# --------------------------------------------------------------------------- #
# Forats del paquet                                                            #
# --------------------------------------------------------------------------- #


def _gap_entry(
    finding: GlobalDocumentClassificationFinding,
) -> tuple[PackageGapCode, str, dict[str, Any]] | None:
    code = GAP_CODES.get(ClassificationFindingCode(finding.code))
    if code is None:
        return None
    detail = dict(finding.detail or {})
    if code == PackageGapCode.REQUIRED_ROLE_MISSING:
        key = str(detail.get("role"))
    elif code == PackageGapCode.REFERENCED_DOCUMENT_MISSING:
        key = str(detail.get("code"))
    else:
        key = str(finding.tender_document_id)
        detail["document_id"] = key
    return code, key, {**detail, "key": key}


def _resolving_document(
    gap: TenderPackageGap, suggestions: dict[uuid.UUID, GlobalDocumentRoleSuggestion]
) -> uuid.UUID | None:
    """El document de la passada nova que cobreix el forat, si n'hi ha un."""
    key = (gap.detail or {}).get("key")
    for document_id, suggestion in suggestions.items():
        signals = suggestion.signals or {}
        if signals.get("duplicate_of"):
            continue
        if gap.code == PackageGapCode.REFERENCED_DOCUMENT_MISSING:
            if signals.get("declared_code") == key:
                return document_id
        elif gap.code == PackageGapCode.REQUIRED_ROLE_MISSING:
            if str(suggestion.role) == key:
                return document_id
        elif str(document_id) == key:
            return document_id
    return None


async def _sync_gaps(
    session: AsyncSession, tender_id: uuid.UUID, run: GlobalDocumentClassificationRun
) -> None:
    """Els forats oberts de la classificacio = les troballes de la passada vigent."""
    desired: dict[tuple[str, str], dict[str, Any]] = {}
    for finding in await run_findings(session, run):
        entry = _gap_entry(finding)
        if entry is not None:
            code, key, detail = entry
            desired[(str(code), key)] = detail

    open_gaps = list(
        (
            await session.scalars(
                sa.select(TenderPackageGap).where(
                    TenderPackageGap.tender_id == tender_id,
                    TenderPackageGap.resolved_at.is_(None),
                )
            )
        ).all()
    )
    seen = {(str(g.code), str((g.detail or {}).get("key", ""))) for g in open_gaps}
    for (code, key), detail in desired.items():
        if (code, key) in seen:
            continue  # ja obert (d'aquesta o d'una altra passada, o de l'operador)
        session.add(
            TenderPackageGap(
                tender_id=tender_id,
                code=PackageGapCode(code),
                detail=detail,
                would_provide=would_provide(PackageGapCode(code), key),
                producer=PackageGapProducer.CLASSIFICATION,
                producer_run_id=run.id,
            )
        )

    suggestions = await run_suggestions(session, run)
    now = datetime.now(UTC)
    for gap in open_gaps:
        if gap.producer != PackageGapProducer.CLASSIFICATION:
            continue  # els declara i els resol l'operador
        if (str(gap.code), str((gap.detail or {}).get("key", ""))) in desired:
            continue
        gap.resolved_at = now
        gap.resolved_by_document_id = _resolving_document(gap, suggestions)
    await session.flush()


# --------------------------------------------------------------------------- #
# Lectura (operador)                                                           #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class GlobalClassificationView:
    run: GlobalDocumentClassificationRun | None
    suggestions: list[GlobalDocumentRoleSuggestion]
    findings: list[GlobalDocumentClassificationFinding]
    reviews: list[DocumentRoleReview]
    gaps: list[TenderPackageGap]


async def load_global_view(session: AsyncSession, tender_id: uuid.UUID) -> GlobalClassificationView:
    run = await current_global_run(session, tender_id)
    suggestions = sorted(
        (await run_suggestions(session, run)).values(), key=lambda s: str(s.tender_document_id)
    )
    reviews = list(
        (
            await session.scalars(
                sa.select(DocumentRoleReview)
                .join(TenderDocument, TenderDocument.id == DocumentRoleReview.tender_document_id)
                .where(TenderDocument.tender_id == tender_id)
                .order_by(DocumentRoleReview.created_at, DocumentRoleReview.id)
            )
        ).all()
    )
    gaps = list(
        (
            await session.scalars(
                sa.select(TenderPackageGap)
                .where(TenderPackageGap.tender_id == tender_id)
                .order_by(TenderPackageGap.created_at, TenderPackageGap.id)
            )
        ).all()
    )
    return GlobalClassificationView(
        run=run,
        suggestions=suggestions,
        findings=await run_findings(session, run),
        reviews=reviews,
        gaps=gaps,
    )


__all__ = [
    "GAP_CODES",
    "GlobalClassificationView",
    "classify_safely",
    "classify_tender_globally",
    "compute_global_fingerprint",
    "current_global_run",
    "global_fingerprint",
    "is_public_tender",
    "load_global_view",
    "run_findings",
    "run_suggestions",
    "would_provide",
]
