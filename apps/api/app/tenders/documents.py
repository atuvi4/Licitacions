"""Quins documents d'una licitacio son VIGENTS. Una regla, escrita una sola vegada.

    vigent  <=>  removed_at IS NULL

Un document retirat de la font (`removed_at`, spec 3B-1 §4.4) no s'esborra mai:
les seves versions, extraccions i pagines, i les runs i cites que el van
llegir, continuen exactament igual i auditables. Pero ja no forma part de la
publicacio vigent, i per tant **cap input nou** el pot llegir: ni el processat,
ni l'extraccio de requisits, ni la classificacio, ni l'estructura de l'analisi,
ni cites noves. Si torna en una publicacio completa posterior, `removed_at`
torna a NULL i hi torna a ser elegible.

Les lectures d'historia (`/history`, runs antigues, cites existents,
`include_removed=true`) NO fan servir aquest selector.

Existeix perque la condicio no acabi copiada a ma a cada carregador: un
aillament es perd per la consulta que algu oblida, no per la que s'escriu
malament.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import DocumentContributionStatus, TenderDocumentOrigin
from app.models.tenders import TenderDocument

#: Condicio `WHERE` dels documents vigents.
ACTIVE_DOCUMENT: sa.ColumnElement[bool] = TenderDocument.removed_at.is_(None)


#: Documents que poden entrar a l'analisi (3B-2 §2.3): vigents, no alies, i del
#: feed, d'una pujada propia o aportats per l'operador i VERIFICATS. Un
#: `pending_verification` o `rejected` es veu als llistats pero no es llegeix mai.
ANALYZABLE_DOCUMENT: sa.ColumnElement[bool] = sa.and_(
    ACTIVE_DOCUMENT,
    TenderDocument.duplicate_of_document_id.is_(None),
    sa.or_(
        TenderDocument.origin.in_([TenderDocumentOrigin.FEED, TenderDocumentOrigin.OWNER_UPLOAD]),
        TenderDocument.contribution_status == DocumentContributionStatus.VERIFIED,
    ),
)


def active_documents(tender_id: uuid.UUID) -> sa.Select[tuple[TenderDocument]]:
    """`SELECT` dels documents vigents d'una licitacio (sense ordre)."""
    return sa.select(TenderDocument).where(TenderDocument.tender_id == tender_id, ACTIVE_DOCUMENT)


def analyzable_documents_query(tender_id: uuid.UUID) -> sa.Select[tuple[TenderDocument]]:
    """`SELECT` dels documents analitzables d'una licitacio (sense ordre)."""
    return sa.select(TenderDocument).where(
        TenderDocument.tender_id == tender_id, ANALYZABLE_DOCUMENT
    )


async def analyzable_documents(session: AsyncSession, tender_id: uuid.UUID) -> list[TenderDocument]:
    """Els documents analitzables d'una licitacio, en ordre estable."""
    query = analyzable_documents_query(tender_id).order_by(
        TenderDocument.created_at, TenderDocument.id
    )
    return list((await session.scalars(query)).all())


def is_active_document(document: TenderDocument) -> bool:
    """La mateixa regla per a un document ja carregat."""
    return document.removed_at is None


def is_analyzable_document(document: TenderDocument) -> bool:
    """`ANALYZABLE_DOCUMENT` per a un document ja carregat."""
    return (
        is_active_document(document)
        and document.duplicate_of_document_id is None
        and (
            document.origin in (TenderDocumentOrigin.FEED, TenderDocumentOrigin.OWNER_UPLOAD)
            or document.contribution_status == DocumentContributionStatus.VERIFIED
        )
    )


async def removed_document_ids(session: AsyncSession, tender_id: uuid.UUID) -> set[uuid.UUID]:
    """Els documents retirats d'una licitacio (pocs: com a molt els seus plecs)."""
    return set(
        (
            await session.scalars(
                sa.select(TenderDocument.id).where(
                    TenderDocument.tender_id == tender_id, ~ACTIVE_DOCUMENT
                )
            )
        ).all()
    )


__all__ = [
    "ACTIVE_DOCUMENT",
    "ANALYZABLE_DOCUMENT",
    "active_documents",
    "analyzable_documents",
    "analyzable_documents_query",
    "is_active_document",
    "is_analyzable_document",
    "removed_document_ids",
]
