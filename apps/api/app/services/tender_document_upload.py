"""Pujar un plec a una licitacio oberta a ma (S3-S2).

Aixo **no** es una pipeline nova: es una entrada nova a la que ja hi havia. Els
bytes acaben al mateix `TenderDocumentContent` deduplicat per SHA-256, el parser
es el mateix i l'extraccio es guarda igual. L'unic que canvia es d'on venen els
bytes, i aixo ho diu `TenderDocumentFile.origin`.

Tres decisions que val la pena llegir abans de tocar res.

**Manen els magic bytes.** El `Content-Type` d'un navegador el posa el sistema
operatiu a partir de l'extensio, aixi que renombrar un fitxer canviaria el que
el sistema creu que ha rebut. La regla es la mateixa que a la descarrega: el que
diuen els bytes primer, el que declara el client nomes per afinar els formats
que comparteixen firma (tot ZIP sembla un ZIP; tot OLE2, un OLE2).

**Cada pujada es un document logic nou.** La identitat d'un `TenderDocument` es
`(tender_id, url)`, i un fitxer pujat no te URL: se li'n dona una de sintetica i
opaca. Pujar dues vegades els mateixos bytes dona, per tant, DOS documents
logics que comparteixen UN sol contingut. Es deliberat: a S3-S2 no hi ha
versions —arriben a S3-S3— i fer que la segona pujada es convertis en silenci en
la primera seria decidir per l'usuari que volia dir. La resposta diu
`content_reused` perque qui ho hagi fet sense voler ho vegi.

**Guardar i llegir son dos fets separats.** Un DOCX es guarda perfectament i no
se'n llegeix el text. Dir-ne error seria mentir sobre el fitxer i dir-ne
analitzat seria mentir sobre el text.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import PayloadTooLargeError, UnsupportedMediaTypeError, ValidationError
from app.models.enums import DocumentFileOrigin, DownloadStatus, TenderDocumentType
from app.models.identity import User
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
)
from app.models.tenders import Tender, TenderDocument
from app.services.document_download import (
    OLE2_GENERIC,
    OLE2_REFINEMENTS,
    ZIP_REFINEMENTS,
    extension_for,
    sniff_mime_type,
)
from app.services.files import compute_sha256, sanitize_filename
from app.services.tender_document_processing import (
    _ensure_current_extraction,
    _record_tender_revision,
    get_or_create_content,
)

#: L'esquema de la URL sintetica d'un document pujat. No es resoluble i no
#: apunta enlloc: nomes dona identitat a una fila que la necessita.
UPLOAD_URL_SCHEME = "upload"


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    document: TenderDocument
    file: TenderDocumentFile
    content: TenderDocumentContent
    extraction: TenderDocumentExtraction
    #: Cert quan aquests bytes ja eren al sistema i no s'han tornat a guardar.
    content_reused: bool


def upload_url_for(token: uuid.UUID) -> str:
    return f"{UPLOAD_URL_SCHEME}://{token}"


def resolve_upload_mime_type(data: bytes, declared: str | None) -> str:
    """El tipus real d'uns bytes pujats.

    Mateixa jerarquia que a la descarrega —firma primer, declaracio despres—
    amb una diferencia: aqui no hi ha URL de la qual sospitar, i un fitxer sense
    firma coneguda (un TXT) es legitim. El que no s'accepta mai es que els bytes
    contradiguin el que s'anuncia.
    """
    declared = (declared or "").split(";")[0].strip().lower() or None
    sniffed = sniff_mime_type(data[:2048])

    if declared == "application/pdf" and sniffed != "application/pdf":
        raise ValidationError(
            "El fitxer s'anuncia com a PDF pero el contingut no ho es.",
            details={"declared_mime_type": declared, "detected_mime_type": sniffed},
        )
    if sniffed == "application/zip" and declared in ZIP_REFINEMENTS:
        return declared
    if sniffed == OLE2_GENERIC and declared in OLE2_REFINEMENTS:
        return declared
    if sniffed:
        return sniffed
    if declared:
        return declared
    raise ValidationError("No s'ha pogut determinar el tipus del fitxer.")


def validate_tender_upload(data: bytes, declared: str | None) -> str:
    """Mida i tipus contra el contracte de formats dels plecs. Retorna el MIME.

    El contracte es el de `DOCUMENT_ALLOWED_MIME_TYPES`, que es el dels plecs, i
    no el de la biblioteca documental de l'empresa: son dues llistes diferents
    perque son dues coses diferents.
    """
    if not data:
        raise ValidationError("El fitxer es buit.")
    if len(data) > settings.document_max_size_bytes:
        raise PayloadTooLargeError(
            f"El fitxer supera el maxim permes de {settings.DOCUMENT_MAX_SIZE_MB} MB."
        )
    mime_type = resolve_upload_mime_type(data, declared)
    if mime_type not in settings.document_allowed_mime_types:
        raise UnsupportedMediaTypeError(
            "Aquest tipus de fitxer no s'admet com a plec.",
            details={
                "mime_type": mime_type,
                "allowed": sorted(settings.document_allowed_mime_types),
            },
        )
    return mime_type


async def upload_tender_document(
    session: AsyncSession,
    *,
    tender: Tender,
    user: User,
    filename: str | None,
    declared_mime_type: str | None,
    data: bytes,
) -> UploadOutcome:
    """Guarda el fitxer i intenta llegir-ne el text. En aquest ordre.

    Primer el contingut i despres les files que hi apunten: aixi no pot existir
    mai un `TenderDocumentFile` que assenyali uns bytes que no s'han arribat a
    guardar. El sentit contrari —uns bytes guardats que al final ningu referencia
    perque la transaccio ha caigut— es inert: el contingut s'adreca pel seu
    SHA-256 i la seguent pujada dels mateixos bytes el reaprofita.
    """
    mime_type = validate_tender_upload(data, declared_mime_type)
    name = sanitize_filename(filename)
    digest = compute_sha256(data)

    content, created = await get_or_create_content(
        session,
        data=data,
        sha256=digest,
        mime_type=mime_type,
        detected_extension=extension_for(mime_type),
    )

    document = TenderDocument(
        tender_id=tender.id,
        name=name,
        # No s'endevina de que es un plec pel nom del fitxer: aixo seria
        # inventar-se una classificacio que ningu ha fet.
        document_type=TenderDocumentType.OTHER,
        url=upload_url_for(uuid.uuid4()),
        mime_type=mime_type,
    )
    session.add(document)
    await session.flush()

    file = TenderDocumentFile(
        tender_document_id=document.id,
        content_id=content.id,
        # Els tres camps que diuen que aixo no ve de cap descarrega.
        source_url=None,
        origin=DocumentFileOrigin.UPLOADED,
        uploaded_by=user.id,
        original_filename=name,
        declared_mime_type=(declared_mime_type or "").split(";")[0].strip().lower() or None,
        version_number=1,
        is_current=True,
        # Els bytes hi son: no hi ha res pendent de baixar.
        download_status=DownloadStatus.DOWNLOADED,
        downloaded_at=datetime.now(UTC),
        last_checked_at=datetime.now(UTC),
    )
    session.add(file)
    await session.commit()
    await session.refresh(file)

    extraction, _done = await _ensure_current_extraction(session, content, force=False)
    # Un plec nou es un canvi de la licitacio, exactament igual que si hagues
    # arribat per la font. Es la mateixa porta i el mateix `DOCUMENT_ADDED`.
    await _record_tender_revision(session, tender.id)
    await session.refresh(file)
    return UploadOutcome(
        document=document,
        file=file,
        content=content,
        extraction=extraction,
        content_reused=not created,
    )
