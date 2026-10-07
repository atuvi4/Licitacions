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

from app.models.tenders import TenderDocument

#: Condicio `WHERE` dels documents vigents.
ACTIVE_DOCUMENT: sa.ColumnElement[bool] = TenderDocument.removed_at.is_(None)


def active_documents(tender_id: uuid.UUID) -> sa.Select[tuple[TenderDocument]]:
    """`SELECT` dels documents vigents d'una licitacio (sense ordre)."""
    return sa.select(TenderDocument).where(TenderDocument.tender_id == tender_id, ACTIVE_DOCUMENT)


def is_active_document(document: TenderDocument) -> bool:
    """La mateixa regla per a un document ja carregat."""
    return document.removed_at is None


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
    "active_documents",
    "is_active_document",
    "removed_document_ids",
]
