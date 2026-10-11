"""Dedup logic per contingut: alies entre documents d'una licitacio (3B-2 §2.1).

Dos documents vigents d'una mateixa licitacio amb els mateixos bytes vigents
(`sha256` del fitxer `is_current`) son, per a l'analisi, un sol document. Un
queda **canonic** i la resta en son **alies** (`duplicate_of_document_id`), que
el predicat `ANALYZABLE_DOCUMENT` no selecciona mai. Res s'esborra: l'alies
continua als llistats i conserva els seus fitxers.

Qui es el canonic d'un grup (mateix sha), per ordre:

1. un document que es pot analitzar (feed, pujada propia o aportacio verificada)
   abans que un que no (pendent o rebutjat): si el canonic no es pogues llegir,
   el contingut quedaria fora de l'analisi encara que un alies verificat el tingues;
2. l'origen: feed > operator_url > operator_upload (`owner_upload` compta com el
   feed: nomes existeix en licitacions manuals, on no hi ha feed);
3. el tipus: un plec o annex (`administrative_terms`, `technical_terms`,
   `additional`) abans que un anunci, una acta o `other`. Si el feed publica els
   mateixos bytes com a PCAP i com a OTHER, la seleccio per tipus de l'analisi
   ha de trobar el PCAP, no un alies seu;
4. el mes antic (`created_at`), i l'id com a desempat estable.

El grup sempre es recalcula sencer amb aquest ordre, tant quan arriben bytes
nous (`assign_duplicate_alias`) com quan el canonic es retira
(`promote_alias_if_canonical_retired`): mai depen de l'ordre d'arribada.

Mai es toca `tender_document_contents` (compartit entre tenants per sha256).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    DocumentContributionStatus,
    TenderDocumentOrigin,
    TenderDocumentType,
)
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tenders import Tender, TenderDocument
from app.services.tender_lifecycle import DOCUMENT_INTAKE_TRIGGER, record_revision
from app.tenders.documents import ACTIVE_DOCUMENT

#: Prioritat d'origen (menor = mana). `owner_upload` mai conviu amb el feed.
ORIGIN_PRIORITY: dict[TenderDocumentOrigin, int] = {
    TenderDocumentOrigin.FEED: 0,
    TenderDocumentOrigin.OWNER_UPLOAD: 0,
    TenderDocumentOrigin.OPERATOR_URL: 1,
    TenderDocumentOrigin.OPERATOR_UPLOAD: 2,
}


#: Tipus que l'analisi llegeix com a plec (preferits com a canonic).
PREFERRED_DOCUMENT_TYPES: frozenset[TenderDocumentType] = frozenset(
    {
        TenderDocumentType.ADMINISTRATIVE_TERMS,
        TenderDocumentType.TECHNICAL_TERMS,
        TenderDocumentType.ADDITIONAL,
    }
)


def _can_be_analyzed(document: TenderDocument) -> bool:
    """La part d'`ANALYZABLE_DOCUMENT` que depen de l'origen i la verificacio."""
    return (
        document.origin in (TenderDocumentOrigin.FEED, TenderDocumentOrigin.OWNER_UPLOAD)
        or document.contribution_status == DocumentContributionStatus.VERIFIED
    )


def canonical_order(document: TenderDocument) -> tuple:
    """Clau d'ordenacio: el primer d'un grup es el canonic."""
    return (
        0 if _can_be_analyzed(document) else 1,
        ORIGIN_PRIORITY.get(TenderDocumentOrigin(document.origin), len(ORIGIN_PRIORITY)),
        0 if document.document_type in PREFERRED_DOCUMENT_TYPES else 1,
        document.created_at,
        str(document.id),
    )


async def current_sha(session: AsyncSession, document_id: uuid.UUID) -> str | None:
    """El sha256 del fitxer vigent del document (o `None` si no te bytes)."""
    return await session.scalar(
        sa.select(TenderDocumentContent.sha256)
        .join(TenderDocumentFile, TenderDocumentFile.content_id == TenderDocumentContent.id)
        .where(
            TenderDocumentFile.tender_document_id == document_id,
            TenderDocumentFile.is_current.is_(True),
        )
        .limit(1)
    )


async def _active_with_sha(
    session: AsyncSession, tender_id: uuid.UUID, shas: Iterable[str]
) -> dict[str, list[TenderDocument]]:
    wanted = sorted(set(shas))
    if not wanted:
        return {}
    rows = (
        await session.execute(
            sa.select(TenderDocument, TenderDocumentContent.sha256)
            .join(
                TenderDocumentFile,
                sa.and_(
                    TenderDocumentFile.tender_document_id == TenderDocument.id,
                    TenderDocumentFile.is_current.is_(True),
                ),
            )
            .join(TenderDocumentContent, TenderDocumentContent.id == TenderDocumentFile.content_id)
            .where(
                TenderDocument.tender_id == tender_id,
                ACTIVE_DOCUMENT,
                TenderDocumentContent.sha256.in_(wanted),
            )
        )
    ).all()
    groups: dict[str, list[TenderDocument]] = defaultdict(list)
    for document, sha in rows:
        groups[sha].append(document)
    return groups


async def _regroup(
    session: AsyncSession, tender_id: uuid.UUID, shas: Iterable[str]
) -> dict[str, TenderDocument]:
    """Recalcula canonic i alies dels grups d'aquests sha. Torna el canonic de cada grup."""
    canonicals: dict[str, TenderDocument] = {}
    for sha, members in (await _active_with_sha(session, tender_id, shas)).items():
        members.sort(key=canonical_order)
        canonical = members[0]
        canonicals[sha] = canonical
        if canonical.duplicate_of_document_id is not None:
            canonical.duplicate_of_document_id = None
        for alias in members[1:]:
            if alias.duplicate_of_document_id != canonical.id:
                alias.duplicate_of_document_id = canonical.id
    await session.flush()
    return canonicals


async def _aliases_of(session: AsyncSession, document: TenderDocument) -> list[TenderDocument]:
    return list(
        (
            await session.scalars(
                sa.select(TenderDocument).where(
                    TenderDocument.tender_id == document.tender_id,
                    TenderDocument.duplicate_of_document_id == document.id,
                    ACTIVE_DOCUMENT,
                )
            )
        ).all()
    )


async def _shas_of_aliases(session: AsyncSession, aliases: list[TenderDocument]) -> set[str]:
    """Els sha dels alies; un alies sense bytes vigents deixa de ser-ho (no te res duplicat)."""
    shas: set[str] = set()
    for alias in aliases:
        sha = await current_sha(session, alias.id)
        if sha is None:
            alias.duplicate_of_document_id = None
        else:
            shas.add(sha)
    return shas


async def assign_duplicate_alias(
    session: AsyncSession, tender_id: uuid.UUID, document: TenderDocument
) -> TenderDocument | None:
    """Recalcula l'alies del document despres que el seu fitxer vigent canvii.

    Es crida quan `get_or_create_content` ha fet vigent un fitxer nou (o quan un
    document torna a ser vigent). Regrupa el sha nou del document i, si abans era
    el canonic d'uns altres i els seus bytes han canviat, tambe el d'aquells
    alies. Torna el canonic del grup del document (ell mateix si no es alies), o
    `None` si el document no es vigent o no te bytes. No fa commit.
    """
    shas: set[str] = set()
    sha = await current_sha(session, document.id) if document.removed_at is None else None
    if sha is not None:
        shas.add(sha)
    shas |= await _shas_of_aliases(session, await _aliases_of(session, document))
    canonicals = await _regroup(session, tender_id, shas)
    return canonicals.get(sha) if sha is not None else None


async def promote_alias_if_canonical_retired(
    session: AsyncSession, document: TenderDocument, *, record: bool = True
) -> TenderDocument | None:
    """Si un canonic ja no es vigent, promou el millor dels seus alies.

    `document` ha d'estar retirat (`removed_at`, del feed o de l'operador). Els
    seus alies vigents es regrupen sense ell: el primer per `canonical_order`
    (analitzable, origen, mes antic) passa a canonic i la resta n'apunta a ell.
    Amb `record` deixa una revisio `document_intake` (la promocio canvia quin
    document es llegeix). Torna el document promogut, o `None` si no n'hi havia
    cap. No fa commit.
    """
    if document.removed_at is None:
        return None
    aliases = await _aliases_of(session, document)
    if not aliases:
        return None
    shas = await _shas_of_aliases(session, aliases)
    canonicals = await _regroup(session, document.tender_id, shas)
    alias_ids = {alias.id for alias in aliases}
    promoted = sorted((c for c in canonicals.values() if c.id in alias_ids), key=canonical_order)
    if record:
        tender = await session.get(Tender, document.tender_id)
        if tender is not None:
            await record_revision(session, tender, trigger=DOCUMENT_INTAKE_TRIGGER)
    return promoted[0] if promoted else None


__all__ = [
    "ORIGIN_PRIORITY",
    "PREFERRED_DOCUMENT_TYPES",
    "assign_duplicate_alias",
    "canonical_order",
    "current_sha",
    "promote_alias_if_canonical_retired",
]
