"""Crear i resoldre cites immutables (S3-S4).

**Un sol lloc valida.** Els consumidors que vindran —requisits manuals,
dimensions, dubtes— no han de repetir res d'aixo: passen per aqui o no creen
cap cita. Si cadascun validés pel seu compte, n'hi hauria prou que un s'ho
deixés perque entressin cites que apunten a una altra licitacio.

Tres coses que el client **no** decideix:

**El SHA-256.** El deriva el servidor del contingut real de la versio o de
l'extraccio referenciada. Acceptar-lo del client voldria dir que la prova de
quins bytes eren la diria justament qui te interes a dir-ne una altra.

**La coherencia de la cadena.** Que existeixin una pagina i una extraccio no
vol dir que la pagina sigui d'aquella extraccio, ni que l'extraccio sigui d'un
document d'aquesta licitacio. Una FK sola no demostra que les peces formin la
mateixa cita: aixo es comprova una per una.

**El text citat.** Si hi ha `char_start`/`char_end`, el `quote` es DERIVA del
text real d'aquella pagina. Deixar-lo escriure lliurement crearia proves que
diuen coses que el document no diu, que es exactament el contrari del que una
cita serveix.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models.enums import SourceRefKind
from app.models.identity import User
from app.models.provenance import TenderSourceRef
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender, TenderDocument
from app.tenders.documents import is_active_document


class SourceRefChainError(ValidationError):
    """Les peces existeixen pero no formen la mateixa cita."""

    code = "source_ref_chain_invalid"


class SourceRefSpanError(ValidationError):
    """El tros citat no cau dins del text d'aquella pagina."""

    code = "source_ref_span_invalid"


class SourceRefDocumentRemovedError(ValidationError):
    """El document ja no es vigent a la font (`removed_at`): no admet cites noves.

    Les cites que ja hi apuntaven es conserven tal com eren (historia); el que es
    refusa es crear-ne o enllacar-ne de noves, que serien inputs nous.
    """

    code = "source_ref_document_removed"


def _assert_active(document: TenderDocument) -> None:
    if not is_active_document(document):
        raise SourceRefDocumentRemovedError(
            "Aquest document ja no es vigent a la font: no s'hi poden afegir cites noves."
        )


async def assert_ref_citable(session: AsyncSession, ref: TenderSourceRef) -> None:
    """Refusa enllacar a una entitat nova una cita d'un document retirat."""
    if ref.tender_document_id is None:
        return
    document = await session.get(TenderDocument, ref.tender_document_id)
    if document is not None:
        _assert_active(document)


@dataclass(frozen=True, slots=True)
class ResolvedRef:
    """Una cita, resolta sense mirar que es vigent ARA.

    Es el que fa que serveixi: tot el que cal per entendre-la i reconstruir-la
    surt de la propia fila i de les files immutables que referencia.
    """

    ref: TenderSourceRef
    document_id: uuid.UUID | None
    document_name: str | None
    file_id: uuid.UUID | None
    version_number: int | None
    content_sha256: str
    extraction_id: uuid.UUID | None
    page_id: uuid.UUID | None
    page_number: int | None


async def _file_of_this_tender(
    session: AsyncSession, tender_id: uuid.UUID, file_id: uuid.UUID
) -> tuple[TenderDocumentFile, TenderDocument, TenderDocumentContent]:
    """El fitxer, el seu document i el seu contingut, si son d'aquesta licitacio.

    La comprovacio es la cadena sencera i no nomes el fitxer: coneixer un UUID
    no vol dir que sigui d'aqui, i els mateixos bytes poden penjar de dues
    licitacions diferents.
    """
    row = (
        await session.execute(
            sa.select(TenderDocumentFile, TenderDocument, TenderDocumentContent)
            .join(TenderDocument, TenderDocument.id == TenderDocumentFile.tender_document_id)
            .join(
                TenderDocumentContent,
                TenderDocumentContent.id == TenderDocumentFile.content_id,
            )
            .where(TenderDocumentFile.id == file_id, TenderDocument.tender_id == tender_id)
        )
    ).first()
    if row is None:
        raise SourceRefChainError(
            "Aquest fitxer no es d'aquesta licitacio, o encara no te contingut."
        )
    return row


async def _page_of_this_tender(
    session: AsyncSession,
    tender_id: uuid.UUID,
    extraction_id: uuid.UUID,
    page_id: uuid.UUID,
) -> tuple[TenderDocumentPage, TenderDocumentExtraction, TenderDocument, TenderDocumentContent]:
    """La pagina, la seva extraccio i el document d'on surt.

    Tres condicions alhora: la pagina ha de ser d'aquella extraccio, l'extraccio
    d'aquell contingut, i el contingut ha d'estar referenciat per algun document
    d'aquesta licitacio. Els mateixos bytes poden ser de dues empreses; el que
    fa valida la cita es que AQUESTA licitacio els tingui.
    """
    row = (
        await session.execute(
            sa.select(
                TenderDocumentPage,
                TenderDocumentExtraction,
                TenderDocument,
                TenderDocumentContent,
            )
            .join(
                TenderDocumentExtraction,
                TenderDocumentExtraction.id == TenderDocumentPage.extraction_id,
            )
            .join(
                TenderDocumentContent,
                TenderDocumentContent.id == TenderDocumentExtraction.content_id,
            )
            .join(
                TenderDocumentFile,
                TenderDocumentFile.content_id == TenderDocumentExtraction.content_id,
            )
            .join(TenderDocument, TenderDocument.id == TenderDocumentFile.tender_document_id)
            .where(
                TenderDocumentPage.id == page_id,
                TenderDocumentPage.extraction_id == extraction_id,
                TenderDocument.tender_id == tender_id,
            )
            # Si els mateixos bytes pengen d'un document vigent i d'un de retirat,
            # la cita nova s'ancora al vigent.
            .order_by(TenderDocument.removed_at.is_not(None))
            .limit(1)
        )
    ).first()
    if row is None:
        raise SourceRefChainError(
            "Aquesta pagina no es d'aquesta extraccio, o l'extraccio no es d'aquesta licitacio."
        )
    return row


def _quote_for(page: TenderDocumentPage, start: int | None, end: int | None) -> str | None:
    """El text citat, derivat del real quan hi ha posicions.

    Si el tros no cau dins de la pagina, es un error i no es retalla en silenci:
    una cita escapçada seguiria semblant una prova.
    """
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise SourceRefSpanError("Cal l'inici i el final, o cap dels dos.")
    if start < 0 or end <= start:
        raise SourceRefSpanError("El tros citat ha de comencar abans d'acabar.")
    text = page.text or ""
    if end > len(text):
        raise SourceRefSpanError(
            "El tros citat surt del text d'aquesta pagina.",
            details={"page_chars": len(text), "char_end": end},
        )
    return text[start:end]


async def _assert_tender(session: AsyncSession, tender_id: uuid.UUID) -> Tender:
    tender = await session.get(Tender, tender_id)
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


async def create_document_file_ref(
    session: AsyncSession,
    *,
    tender_id: uuid.UUID,
    file_id: uuid.UUID,
    user: User | None = None,
    location_label: str | None = None,
    quote: str | None = None,
) -> TenderSourceRef:
    """Cita una VERSIO concreta d'un document, sense pagines.

    Es el cas d'un XLSX, un DOCX o qualsevol format que es guarda i del qual no
    en llegim el text. Que no en sapiguem treure pagines no vol dir que no es
    pugui citar: `location_label` diu on mirar —"full Calculs, fila 42"— i qui
    ho llegeixi hi anira. Convertir un full de calcul en pagines seria
    inventar-se una precisio que no tenim.
    """
    await _assert_tender(session, tender_id)
    file, document, content = await _file_of_this_tender(session, tender_id, file_id)
    _assert_active(document)

    ref = TenderSourceRef(
        tender_id=tender_id,
        ref_kind=SourceRefKind.DOCUMENT_FILE,
        tender_document_file_id=file.id,
        tender_document_id=document.id,
        content_sha256=content.sha256,
        location_label=location_label,
        quote=quote,
        created_by=user.id if user else None,
    )
    session.add(ref)
    await session.flush()
    return ref


async def create_extracted_page_ref(
    session: AsyncSession,
    *,
    tender_id: uuid.UUID,
    extraction_id: uuid.UUID,
    page_id: uuid.UUID,
    user: User | None = None,
    location_label: str | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
) -> TenderSourceRef:
    """Cita una PAGINA concreta d'una lectura concreta.

    La cita mes forta que sabem fer: diu de quins bytes, amb quin parser i de
    quina pagina surt el text. El `quote` no s'accepta del client —es deriva del
    text real quan hi ha posicions—, perque una cita que digui una cosa que el
    document no diu es pitjor que no tenir-ne cap.
    """
    await _assert_tender(session, tender_id)
    page, extraction, document, content = await _page_of_this_tender(
        session, tender_id, extraction_id, page_id
    )
    _assert_active(document)
    quote = _quote_for(page, char_start, char_end)

    ref = TenderSourceRef(
        tender_id=tender_id,
        ref_kind=SourceRefKind.EXTRACTED_PAGE,
        extraction_id=extraction.id,
        page_id=page.id,
        page_number=page.page_number,
        tender_document_id=document.id,
        content_sha256=content.sha256,
        location_label=location_label,
        quote=quote,
        char_start=char_start,
        char_end=char_end,
        created_by=user.id if user else None,
    )
    session.add(ref)
    await session.flush()
    return ref


async def resolve_source_ref(session: AsyncSession, ref: TenderSourceRef) -> ResolvedRef:
    """Torna a muntar la cita **tal com era**, no tal com son les coses ara.

    No mira mai que es vigent: el fitxer i l'extraccio son files immutables i el
    `content_sha256` va desat a la propia cita. Per aixo continua responent el
    mateix quan arriba una versio nova o es torna a extreure.
    """
    document_name = None
    if ref.tender_document_id is not None:
        document_name = await session.scalar(
            sa.select(TenderDocument.name).where(TenderDocument.id == ref.tender_document_id)
        )

    version_number = None
    file_id = ref.tender_document_file_id
    if file_id is not None:
        version_number = await session.scalar(
            sa.select(TenderDocumentFile.version_number).where(
                TenderDocumentFile.id == file_id
            )
        )
    elif ref.extraction_id is not None:
        # Una cita de pagina s'ancora a l'extraccio, no a un fitxer; la versio
        # es dedueix del fitxer que porta aquells bytes DINS d'aquesta
        # licitacio, que es el que fa que la cita sigui d'aqui.
        row = (
            await session.execute(
                sa.select(TenderDocumentFile.id, TenderDocumentFile.version_number)
                .join(TenderDocument, TenderDocument.id == TenderDocumentFile.tender_document_id)
                .join(
                    TenderDocumentContent,
                    TenderDocumentContent.id == TenderDocumentFile.content_id,
                )
                .where(
                    TenderDocumentContent.sha256 == ref.content_sha256,
                    TenderDocument.tender_id == ref.tender_id,
                )
                .order_by(TenderDocumentFile.version_number)
                .limit(1)
            )
        ).first()
        if row is not None:
            file_id, version_number = row

    return ResolvedRef(
        ref=ref,
        document_id=ref.tender_document_id,
        document_name=document_name,
        file_id=file_id,
        version_number=version_number,
        content_sha256=ref.content_sha256,
        extraction_id=ref.extraction_id,
        page_id=ref.page_id,
        page_number=ref.page_number,
    )


async def resolve_source_refs(
    session: AsyncSession, refs: list[TenderSourceRef]
) -> dict[uuid.UUID, ResolvedRef]:
    """Com `resolve_source_ref`, per a moltes cites alhora (maxim 3 consultes).

    Mateixa semantica, sense una consulta per cita. Serveix les lectures que
    pinten les cites de molts elements d'una vegada.
    """
    if not refs:
        return {}

    document_ids = {r.tender_document_id for r in refs if r.tender_document_id is not None}
    names: dict[uuid.UUID, str] = {}
    if document_ids:
        names = dict(
            (
                await session.execute(
                    sa.select(TenderDocument.id, TenderDocument.name).where(
                        TenderDocument.id.in_(document_ids)
                    )
                )
            ).all()
        )

    file_ids = {r.tender_document_file_id for r in refs if r.tender_document_file_id is not None}
    versions: dict[uuid.UUID, int] = {}
    if file_ids:
        versions = dict(
            (
                await session.execute(
                    sa.select(TenderDocumentFile.id, TenderDocumentFile.version_number).where(
                        TenderDocumentFile.id.in_(file_ids)
                    )
                )
            ).all()
        )

    # Cites de pagina: fitxer amb els mateixos bytes DINS la mateixa licitacio.
    page_refs = [r for r in refs if r.tender_document_file_id is None and r.extraction_id]
    by_bytes: dict[tuple[str, uuid.UUID], tuple[uuid.UUID, int]] = {}
    if page_refs:
        rows = (
            await session.execute(
                sa.select(
                    TenderDocumentContent.sha256,
                    TenderDocument.tender_id,
                    TenderDocumentFile.id,
                    TenderDocumentFile.version_number,
                )
                .join(TenderDocument, TenderDocument.id == TenderDocumentFile.tender_document_id)
                .join(
                    TenderDocumentContent,
                    TenderDocumentContent.id == TenderDocumentFile.content_id,
                )
                .where(
                    TenderDocumentContent.sha256.in_({r.content_sha256 for r in page_refs}),
                    TenderDocument.tender_id.in_({r.tender_id for r in page_refs}),
                )
                .order_by(TenderDocumentFile.version_number)
            )
        ).all()
        for sha, tender_id, file_id, version_number in rows:
            by_bytes.setdefault((sha, tender_id), (file_id, version_number))

    result: dict[uuid.UUID, ResolvedRef] = {}
    for ref in refs:
        file_id = ref.tender_document_file_id
        version_number = versions.get(file_id) if file_id is not None else None
        if file_id is None and ref.extraction_id is not None:
            found = by_bytes.get((ref.content_sha256, ref.tender_id))
            if found is not None:
                file_id, version_number = found
        result[ref.id] = ResolvedRef(
            ref=ref,
            document_id=ref.tender_document_id,
            document_name=names.get(ref.tender_document_id)
            if ref.tender_document_id is not None
            else None,
            file_id=file_id,
            version_number=version_number,
            content_sha256=ref.content_sha256,
            extraction_id=ref.extraction_id,
            page_id=ref.page_id,
            page_number=ref.page_number,
        )
    return result


async def get_source_ref(
    session: AsyncSession, tender_id: uuid.UUID, ref_id: uuid.UUID
) -> TenderSourceRef:
    """Una cita, si es d'aquesta licitacio.

    El `tender_id` no es decoratiu: sense ell, coneixer l'UUID d'una cita
    equivaldria a poder-la llegir, i les cites parlen del contingut dels plecs.
    """
    ref = (
        await session.execute(
            sa.select(TenderSourceRef).where(
                TenderSourceRef.id == ref_id, TenderSourceRef.tender_id == tender_id
            )
        )
    ).scalars().one_or_none()
    if ref is None:
        raise NotFoundError("Cita no trobada.")
    return ref
