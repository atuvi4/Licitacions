"""Fins on arriba la revisio HUMANA dels plecs (S3-S3).

Aquest modul respon a dues preguntes i, deliberadament, **no** a una tercera.

Respon:

* hi ha plecs que ningu no ha confirmat haver mirat?
* el conjunt de plecs ha canviat des de l'ultima vegada que algu ho va dir?

No respon, i no ho pot respondre:

* han canviat els requisits?

Perque un document que acaba d'arribar no ha canviat cap requisit: nomes diu que
hi ha material per llegir. Qui respon aquella pregunta es l'extraccio, comparant
el corpus analitzat amb el d'ara, i te el seu propi finding.

**L'empremta es DOCUMENTAL.** No hi entra ni l'estat ni la qualitat de
l'extraccio, ni cap sortida de cap model. Un XLSX del qual no sabem llegir el
text hi entra igual que un PDF: una persona l'ha de revisar encara que Tuvi no en
pugui treure res, i que el nostre parser sapiga llegir-lo o no es una limitacio
nostra, no un fet del document. Per aixo tampoc no depen de si l'extraccio ha
fallat: un plec que no hem sabut obrir segueix sent un plec que hi es.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.enums import MembershipRole
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tenders import TenderDocument
from app.models.workspace import BidWorkspace
from app.tenders.documents import ANALYZABLE_DOCUMENT

#: Qui pot afirmar que ha revisat els plecs. Es una constatacio sobre la
#: candidatura, no una edicio tecnica: els mateixos que decideixen.
REVIEW_ROLES = frozenset(
    {MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.BID_MANAGER}
)


@dataclass(frozen=True, slots=True)
class DocumentSet:
    """El conjunt de plecs vigent d'una licitacio, amb la seva empremta."""

    fingerprint: str
    document_count: int

    @property
    def is_empty(self) -> bool:
        return self.document_count == 0


@dataclass(frozen=True, slots=True)
class DocumentReview:
    """Si algu ha revisat els plecs, i si allo que va revisar segueix sent aixo.

    Es deriva en llegir i no es persisteix mai, igual que la revisio de l'abast
    tecnic. Guardar "caducada" voldria dir tenir dues versions del mateix fet.
    """

    reviewed: bool
    stale: bool
    reviewed_at: datetime | None
    #: Quants plecs te la licitacio ARA. Zero canvia el sentit de tot.
    document_count: int
    #: L'empremta d'ara. Es la que es desaria si algu marques la revisio.
    current_fingerprint: str


async def fingerprint_of(session: AsyncSession, tender_id: uuid.UUID) -> DocumentSet:
    """SHA-256 canonic del conjunt documental vigent.

    Per cada document logic hi entra el que identifica QUE hi ha ara: el
    document, la versio vigent i el hash del seu contingut. Amb el document sol
    no n'hi hauria prou —substituir un plec sota el mateix id seria invisible— i
    amb el contingut sol tampoc: dos documents diferents amb els mateixos bytes
    son dues coses a revisar.

    Un document sense cap fitxer vigent hi entra igualment, amb el seu forat
    marcat: existeix, i que encara no s'hagi pogut baixar es informacio, no
    absencia. El dia que el fitxer arribi, l'empremta canviara, que es
    exactament el que ha de passar.

    S'ordena abans d'agregar perque l'ordre d'una consulta no ha de canviar la
    identitat del conjunt.

    Un plec retirat de la font (`removed_at`) ja no es del conjunt vigent: la
    seva retirada canvia l'empremta i la revisio humana torna a caducar.
    Tampoc un alies ni una aportacio de l'operador sense verificar (3B-2 §2.3):
    nomes entra el que es analitzable (`ANALYZABLE_DOCUMENT`).
    """
    rows = (
        await session.execute(
            sa.select(
                TenderDocument.id,
                TenderDocumentFile.id,
                TenderDocumentFile.version_number,
                TenderDocumentContent.sha256,
            )
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
            .where(TenderDocument.tender_id == tender_id, ANALYZABLE_DOCUMENT)
        )
    ).all()

    parts = sorted(
        f"{document_id}|{file_id or '-'}|{version or 0}|{sha256 or '-'}"
        for document_id, file_id, version, sha256 in rows
    )
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\n")
    return DocumentSet(fingerprint=digest.hexdigest(), document_count=len(parts))


async def review_state(session: AsyncSession, workspace: BidWorkspace) -> DocumentReview:
    """L'estat de la revisio documental d'aquesta candidatura."""
    current = await fingerprint_of(session, workspace.tender_id)
    reviewed = workspace.documents_reviewed_fingerprint is not None
    return DocumentReview(
        reviewed=reviewed,
        stale=reviewed and workspace.documents_reviewed_fingerprint != current.fingerprint,
        reviewed_at=workspace.documents_reviewed_at,
        document_count=current.document_count,
        current_fingerprint=current.fingerprint,
    )


def assert_can_review(role: MembershipRole) -> None:
    if role not in REVIEW_ROLES:
        raise NotFoundError("No es poden marcar els plecs com a revisats amb aquest rol.")


async def mark_reviewed(
    session: AsyncSession, workspace: BidWorkspace
) -> tuple[DocumentReview, bool]:
    """Desa que algu ha revisat el conjunt de plecs que hi ha ARA.

    Retorna l'estat resultant i si ha canviat alguna cosa. Es una declaracio
    humana i prou: no extreu requisits, no valida cap solucio, no aprova cap
    paquet i sobretot **no afirma que no hi hagi canvis**. Nomes diu fins on ha
    arribat la lectura d'una persona.

    Repetir-ho sense cap canvi documental no reescriu la data: la revisio
    vigent segueix sent la mateixa, i moure-li la data faria semblar que algu
    s'ho ha tornat a mirar quan no calia.
    """
    current = await fingerprint_of(session, workspace.tender_id)
    if workspace.documents_reviewed_fingerprint == current.fingerprint:
        return await review_state(session, workspace), False

    workspace.documents_reviewed_fingerprint = current.fingerprint
    workspace.documents_reviewed_at = datetime.now(UTC)
    await session.flush()
    return await review_state(session, workspace), True
