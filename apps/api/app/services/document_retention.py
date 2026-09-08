"""Retencio dels documents que sostenen un paquet de presentacio.

Un paquet aprovat ha de ser **reproduible**, no nomes auditable. El manifest diu
quin SHA-256 tenia cada fitxer; si els bytes desapareixen, aquella xifra deixa de
poder-se comprovar i el paquet passa de ser una prova a ser una afirmacio.

Per aixo un document referenciat per una revisio congelada no s'esborra. La
base de dades ja ho impedeix —la clau forana es `RESTRICT`— i aixo hi afegeix el
que una restriccio no pot donar: un error llegible, i saber QUINES revisions el
retenen.

Sobre l'overwrite dels bytes: la clau d'storage es unica i es genera amb un UUID
a cada pujada, i cap endpoint no permet substituir el binari d'un document
existent (`DocumentUpdate` nomes edita metadata). La proteccio contra reescriure
la mateixa clau no cal implementar-la: no hi ha cap cami que hi arribi, i hi ha
un test que ho fixa.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.submission import SubmissionPackage, SubmissionPackageItem


class DocumentRetainedError(ConflictError):
    """El document sosté un paquet de presentacio i no es pot esborrar."""

    code = "document_retained_by_package"


async def retaining_revisions(
    session: AsyncSession, document_id: uuid.UUID
) -> list[int]:
    """Quines revisions de paquet depenen d'aquest document.

    Hi entren TOTES les revisions congelades, tambe les superades i les
    rebutjades: son historial, i l'historial ha de seguir sent comprovable.
    """
    rows = (
        await session.execute(
            sa.select(SubmissionPackage.revision_number)
            .join(SubmissionPackageItem, SubmissionPackageItem.package_id == SubmissionPackage.id)
            .where(SubmissionPackageItem.document_id == document_id)
            .order_by(SubmissionPackage.revision_number)
            .distinct()
        )
    ).all()
    return [row[0] for row in rows]


async def assert_document_is_deletable(
    session: AsyncSession, document_id: uuid.UUID
) -> None:
    """Rebutja l'esborrat si algun paquet el reté, i diu quins."""
    revisions = await retaining_revisions(session, document_id)
    if not revisions:
        return
    raise DocumentRetainedError(
        "Aquest document forma part d'un paquet de presentacio congelat i no es "
        "pot esborrar: sense els bytes, el manifest no es podria comprovar.",
        details={"package_revisions": revisions},
    )
