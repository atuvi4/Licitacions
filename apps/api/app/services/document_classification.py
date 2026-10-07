"""Classificar els documents d'una licitacio, vinguin del canal que vinguin.

Capa 3 del motor d'analisi. Llegeix els `TenderDocument` de la licitacio que
tenen contingut desat, en construeix els fets (nom, tipus, bytes, text) i
demana la classificacio pura (`app/classification/roles.py`). **No coneix la
recepcio**: l'unic que en fa servir es el cami del fitxer dins de l'entrega, si
n'hi ha, per ensenyar-lo i per classificar pel nom amb accents i carpeta.

**Sempre el conjunt sencer.** Una entrega nova no es classifica sola: es torna a
classificar tota la licitacio, perque una copia o un document que falta nomes es
veuen mirant-ho tot. Si l'empremta dels documents no ha canviat, es reaprofita
la passada vigent.

**Qui toca el `document_type`.** Es l'unica cosa d'aquesta capa que llegeix
l'analisi (capa 4). Nomes s'hi escriu en licitacions que l'empresa pot editar
(obertes a ma i seves), i nomes en documents que la classificacio **gestiona**:
els que son `other`, els que tenen el tipus que li va donar la passada anterior,
i els que tenen un rol fixat per una persona. Un tipus que algu va triar en pujar
un plec sol no el trepitja cap reclassificacio.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.classification import ooxml_text
from app.classification.roles import (
    CLASSIFIER_VERSION,
    ROLE_TO_DOCUMENT_TYPE,
    DocumentFacts,
    classify_documents,
)
from app.core.errors import NotFoundError
from app.models.classification import (
    TenderDocumentClassificationFinding,
    TenderDocumentClassificationRun,
    TenderDocumentRoleDecision,
    TenderDocumentRoleSuggestion,
)
from app.models.enums import (
    ClassificationTrigger,
    DocumentRole,
    ExtractionStatus,
    FindingSeverity,
    TenderDocumentType,
)
from app.models.identity import User
from app.models.reception import TenderDocumentReception, TenderDocumentReceptionItem
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender, TenderDocument
from app.services.storage import get_storage
from app.tenders.documents import ACTIVE_DOCUMENT, removed_document_ids


@dataclass(slots=True)
class _Input:
    document: TenderDocument
    content: TenderDocumentContent
    extraction: TenderDocumentExtraction | None
    path: str
    filename: str


async def _inputs(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> list[_Input]:
    """Els documents VIGENTS amb contingut desat, en l'ordre en que van arribar.

    Un plec retirat de la font no es classifica ni apareix entre els rols
    vigents; les runs de classificacio anteriors el conserven tal com era.
    """
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
            .where(TenderDocument.tender_id == tender_id, ACTIVE_DOCUMENT)
        )
    ).all()
    if not rows:
        return []

    ids = [document.id for document, _ in rows]
    # El cami dins de l'entrega, si el document va arribar per una. Es de
    # l'empresa (la recepcio es seva): una altra empresa veu el nom del document.
    received = (
        await session.execute(
            sa.select(TenderDocumentReceptionItem, TenderDocumentReception.created_at)
            .join(
                TenderDocumentReception,
                TenderDocumentReception.id == TenderDocumentReceptionItem.reception_id,
            )
            .where(
                TenderDocumentReceptionItem.tender_document_id.in_(ids),
                TenderDocumentReceptionItem.company_id == company_id,
            )
        )
    ).all()
    items = {item.tender_document_id: (item, received_at) for item, received_at in received}

    extractions = {
        e.content_id: e
        for e in await session.scalars(
            sa.select(TenderDocumentExtraction).where(
                TenderDocumentExtraction.content_id.in_([c.id for _, c in rows]),
                TenderDocumentExtraction.is_current.is_(True),
            )
        )
    }

    def order(row):
        document, _ = row
        item = items.get(document.id)
        arrived = item[1] if item else document.created_at
        return (arrived, item[0].position if item else 0, document.created_at, str(document.id))

    inputs = []
    for document, content in sorted(rows, key=order):
        item = items.get(document.id)
        inputs.append(
            _Input(
                document=document,
                content=content,
                extraction=extractions.get(content.id),
                path=item[0].path if item else document.name,
                filename=item[0].filename if item else document.name,
            )
        )
    return inputs


def _fingerprint(inputs: list[_Input]) -> str:
    lines = sorted(
        "|".join(
            (
                str(i.document.id),
                i.content.sha256,
                i.path,
                str(i.extraction.id) if i.extraction else "-",
            )
        )
        for i in inputs
    )
    return hashlib.sha256("\n".join([CLASSIFIER_VERSION, *lines]).encode()).hexdigest()


async def _pages(session: AsyncSession, extraction_id: uuid.UUID) -> tuple[str, ...]:
    rows = await session.scalars(
        sa.select(TenderDocumentPage.text)
        .where(TenderDocumentPage.extraction_id == extraction_id)
        .order_by(TenderDocumentPage.page_number)
    )
    return tuple(rows)


async def _facts(session: AsyncSession, position: int, item: _Input) -> DocumentFacts:
    pages: tuple[str, ...] | None = None
    source = None
    if item.extraction is not None and item.extraction.status == ExtractionStatus.EXTRACTED:
        pages, source = await _pages(session, item.extraction.id), "extraction"
    elif item.content.mime_type in (ooxml_text.DOCX, ooxml_text.XLSX):
        text = ooxml_text.read_text(
            await get_storage().download(item.content.storage_key), item.content.mime_type
        )
        if text:
            pages, source = (text,), "ooxml"
    return DocumentFacts(
        key=str(item.document.id),
        position=position,
        path=item.path,
        filename=item.filename,
        mime_type=item.content.mime_type,
        sha256=item.content.sha256,
        pages=pages,
        text_source=source,
    )


async def current_run(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> TenderDocumentClassificationRun | None:
    return await session.scalar(
        sa.select(TenderDocumentClassificationRun).where(
            TenderDocumentClassificationRun.company_id == company_id,
            TenderDocumentClassificationRun.tender_id == tender_id,
            TenderDocumentClassificationRun.is_current.is_(True),
        )
    )


async def run_includes_removed_document(
    session: AsyncSession, run: TenderDocumentClassificationRun
) -> bool:
    """Cert si la run de classificacio va classificar un document ja retirat de la font."""
    removed = await removed_document_ids(session, run.tender_id)
    if not removed:
        return False
    return bool(
        await session.scalar(
            sa.select(
                sa.exists().where(
                    TenderDocumentRoleSuggestion.run_id == run.id,
                    TenderDocumentRoleSuggestion.tender_document_id.in_(removed),
                )
            )
        )
    )


async def _decisions(
    session: AsyncSession, company_id: uuid.UUID, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, TenderDocumentRoleDecision]:
    if not document_ids:
        return {}
    rows = await session.scalars(
        sa.select(TenderDocumentRoleDecision).where(
            TenderDocumentRoleDecision.company_id == company_id,
            TenderDocumentRoleDecision.tender_document_id.in_(document_ids),
        )
    )
    return {d.tender_document_id: d for d in rows}


async def _suggestions(
    session: AsyncSession, run: TenderDocumentClassificationRun | None
) -> dict[uuid.UUID, TenderDocumentRoleSuggestion]:
    if run is None:
        return {}
    rows = await session.scalars(
        sa.select(TenderDocumentRoleSuggestion).where(TenderDocumentRoleSuggestion.run_id == run.id)
    )
    return {s.tender_document_id: s for s in rows}


def _owns(tender: Tender, company_id: uuid.UUID) -> bool:
    return tender.owner_company_id == company_id


async def classify_tender(
    session: AsyncSession,
    *,
    tender: Tender,
    company_id: uuid.UUID,
    trigger: ClassificationTrigger,
    user: User | None = None,
    reception_id: uuid.UUID | None = None,
) -> TenderDocumentClassificationRun | None:
    """Classifica tots els documents de la licitacio. `None` si no n'hi ha cap."""
    inputs = await _inputs(session, company_id, tender.id)
    if not inputs:
        # Si ja no queda cap plec vigent perque s'han retirat de la font, la
        # classificacio que en parlava deixa de ser vigent: no pot quedar com a
        # base de cap analisi nova. (Es conserva sencera com a historia.)
        previous = await current_run(session, company_id, tender.id)
        if previous is not None and await run_includes_removed_document(session, previous):
            previous.is_current = False
            await session.commit()
        return None
    fingerprint = _fingerprint(inputs)
    previous = await current_run(session, company_id, tender.id)
    if previous is not None and previous.input_fingerprint == fingerprint:
        return previous

    facts = [await _facts(session, position, item) for position, item in enumerate(inputs)]
    result = classify_documents(facts)
    previous_types = {
        document_id: suggestion.document_type
        for document_id, suggestion in (await _suggestions(session, previous)).items()
    }

    if previous is not None:
        previous.is_current = False
        await session.flush()
    run = TenderDocumentClassificationRun(
        company_id=company_id,
        tender_id=tender.id,
        classifier_version=result.version,
        input_fingerprint=fingerprint,
        trigger=trigger,
        trigger_reception_id=reception_id,
        document_count=len(inputs),
        is_current=True,
        created_by=user.id if user else None,
    )
    session.add(run)
    await session.flush()

    for fact in facts:
        decision = result.decisions[fact.key]
        session.add(
            TenderDocumentRoleSuggestion(
                run_id=run.id,
                company_id=company_id,
                tender_document_id=uuid.UUID(fact.key),
                role=decision.role,
                confidence=decision.confidence,
                document_type=decision.document_type,
                signals={**decision.signals(), "text_source": fact.text_source},
            )
        )
    for finding in result.findings:
        session.add(
            TenderDocumentClassificationFinding(
                run_id=run.id,
                company_id=company_id,
                code=finding.code,
                severity=finding.severity,
                tender_document_id=uuid.UUID(finding.key) if finding.key else None,
                related_document_id=uuid.UUID(finding.related_key) if finding.related_key else None,
                detail=finding.detail,
            )
        )

    if _owns(tender, company_id):
        decisions = await _decisions(session, company_id, [i.document.id for i in inputs])
        for item in inputs:
            document = item.document
            decided = decisions.get(document.id)
            if decided is not None:
                document.document_type = ROLE_TO_DOCUMENT_TYPE[decided.role]
                continue
            managed = document.document_type == TenderDocumentType.OTHER or (
                document.document_type == previous_types.get(document.id)
            )
            if managed:
                document.document_type = result.decisions[str(document.id)].document_type
    await session.commit()
    return run


# --------------------------------------------------------------------------- #
# Lectura i decisio humana                                                     #
# --------------------------------------------------------------------------- #


_REVIEW_SEVERITIES = frozenset({FindingSeverity.HIGH, FindingSeverity.MEDIUM})


@dataclass(slots=True)
class ClassifiedDocument:
    document: TenderDocument
    path: str
    mime_type: str
    sha256: str
    suggestion: TenderDocumentRoleSuggestion | None
    decision: TenderDocumentRoleDecision | None
    needs_review: bool

    @property
    def role(self) -> DocumentRole | None:
        if self.decision is not None:
            return self.decision.role
        return self.suggestion.role if self.suggestion else None


@dataclass(slots=True)
class ClassificationView:
    run: TenderDocumentClassificationRun | None
    documents: list[ClassifiedDocument]
    findings: list[TenderDocumentClassificationFinding]


async def load_view(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> ClassificationView:
    run = await current_run(session, company_id, tender_id)
    inputs = await _inputs(session, company_id, tender_id)
    suggestions = await _suggestions(session, run)
    decisions = await _decisions(session, company_id, [i.document.id for i in inputs])
    findings: list[TenderDocumentClassificationFinding] = []
    if run is not None:
        findings = list(
            await session.scalars(
                sa.select(TenderDocumentClassificationFinding).where(
                    TenderDocumentClassificationFinding.run_id == run.id
                )
            )
        )
    order = {severity: index for index, severity in enumerate(FindingSeverity)}
    position = {item.document.id: index for index, item in enumerate(inputs)}
    findings.sort(key=lambda f: (order[f.severity], position.get(f.tender_document_id, -1)))
    flagged = {f.tender_document_id for f in findings if f.severity in _REVIEW_SEVERITIES}

    documents = []
    for item in inputs:
        suggestion = suggestions.get(item.document.id)
        decision = decisions.get(item.document.id)
        documents.append(
            ClassifiedDocument(
                document=item.document,
                path=item.path,
                mime_type=item.content.mime_type,
                sha256=item.content.sha256,
                suggestion=suggestion,
                decision=decision,
                needs_review=decision is None
                and (
                    suggestion is None
                    or suggestion.confidence != "high"
                    or item.document.id in flagged
                ),
            )
        )
    return ClassificationView(run=run, documents=documents, findings=findings)


async def decide_role(
    session: AsyncSession,
    *,
    tender: Tender,
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    role: DocumentRole,
    user: User,
) -> TenderDocumentRoleDecision:
    """Una persona fixa el rol d'un document. El que va proposar el classificador es queda.

    Fixar-lo el fa definitiu per a l'analisi: el document passa al tipus del rol,
    encara que el classificador l'hagues deixat fora (per exemple, perque semblava
    una copia). Qui ho fixa en respon. La decisio sobreviu a les reclassificacions.
    """
    document = await session.scalar(
        sa.select(TenderDocument).where(
            TenderDocument.id == document_id, TenderDocument.tender_id == tender.id
        )
    )
    if document is None:
        raise NotFoundError("Document no trobat.")
    decision = await session.scalar(
        sa.select(TenderDocumentRoleDecision).where(
            TenderDocumentRoleDecision.company_id == company_id,
            TenderDocumentRoleDecision.tender_document_id == document_id,
        )
    )
    if decision is None:
        decision = TenderDocumentRoleDecision(company_id=company_id, tender_document_id=document_id)
        session.add(decision)
    decision.role = role
    decision.decided_by = user.id
    decision.decided_at = datetime.now(UTC)
    if _owns(tender, company_id):
        document.document_type = ROLE_TO_DOCUMENT_TYPE[role]
    await session.flush()
    return decision
