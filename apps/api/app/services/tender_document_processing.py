"""Orquestracio del processat d'un document de licitacio.

L'unic modul de la capa documental que toca la base de dades i l'storage. Els
altres tres (guarda d'URL, descarregador, parser) son purs i es poden provar
sense cap sessio.

Deduplicacio: **el nivell son els bytes**. Els mateixos bytes = un sol objecte a
l'storage i una sola extraccio per `parser_version`, apuntin-hi els
`TenderDocument` que apuntin. El que NO es dedupliqua es la peticio HTTP: per
coneixer el SHA-256 cal baixar-los.

Concurrencia sense Redis ni cua: l'index unic parcial sobre els estats en curs
fa de lock. El segon request rep 409 en comptes de duplicar la feina.

Transaccions: cap peticio HTTP dins d'una transaccio oberta. Es reclama el
document en una transaccio curta, es descarrega i es parseja fora, i es tanca
amb una altra transaccio curta.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import NoContentToExtractError, ProcessingInProgressError
from app.models.enums import (
    DownloadStatus,
    ExtractionStatus,
    PageExtractionStatus,
    TenderDocumentOrigin,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
    TenderExtractionIssue,
)
from app.models.tenders import Tender, TenderDocument
from app.services.document_alias import assign_duplicate_alias
from app.services.document_download import DownloadError, DownloadResult, download_document
from app.services.document_parsers import extract_text_async, parser_version_for
from app.services.extraction_qa import PageInput as QaPageInput
from app.services.extraction_qa import analyse as analyse_extraction_quality
from app.services.pdf_text import ExtractionResult, should_retry, unsupported_result
from app.services.storage import build_tender_document_key, get_storage
from app.services.tender_lifecycle import record_revision
from app.services.url_guard import Resolver

logger = logging.getLogger(__name__)

TERMINAL_EXTRACTION_STATES = frozenset(
    {ExtractionStatus.EXTRACTED, ExtractionStatus.SKIPPED_UNSUPPORTED, ExtractionStatus.FAILED}
)


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    file: TenderDocumentFile
    #: Cert si no ha calgut ni baixar ni parsejar res de nou.
    reused: bool


async def current_file_for(
    session: AsyncSession, document_id: uuid.UUID
) -> TenderDocumentFile | None:
    return await session.scalar(
        sa.select(TenderDocumentFile)
        .where(
            TenderDocumentFile.tender_document_id == document_id,
            TenderDocumentFile.is_current.is_(True),
        )
        .order_by(TenderDocumentFile.version_number.desc())
        .limit(1)
    )


async def current_extraction_for(
    session: AsyncSession, content_id: uuid.UUID
) -> TenderDocumentExtraction | None:
    return await session.scalar(
        sa.select(TenderDocumentExtraction)
        # Els avisos de qualitat viatgen amb l'extraccio: sense ells, la vista
        # del document diria "Avisos" i no podria dir de que.
        .options(selectinload(TenderDocumentExtraction.qa_issues))
        .where(
            TenderDocumentExtraction.content_id == content_id,
            TenderDocumentExtraction.is_current.is_(True),
        )
    )


async def _reclaim_stale(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Allibera un processat que ha quedat penjat perque el proces va morir.

    Sense aixo, una caiguda entre el claim i el tancament bloquejaria el
    document per sempre: l'index parcial no deixaria reclamar-lo mai mes.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.DOCUMENT_PROCESSING_STALE_SECONDS)
    result = await session.execute(
        sa.update(TenderDocumentFile)
        .where(
            TenderDocumentFile.tender_document_id == document_id,
            TenderDocumentFile.download_status.in_(
                [DownloadStatus.PENDING, DownloadStatus.DOWNLOADING]
            ),
            TenderDocumentFile.started_at < cutoff,
        )
        .values(
            download_status=DownloadStatus.FAILED,
            error_code="stale",
            error_message="El processat es va interrompre i s'ha alliberat automaticament.",
        )
    )
    if result.rowcount:
        await session.commit()
        logger.warning(
            "document.process.stale_reclaimed",
            extra={"document_id": str(document_id), "rows": result.rowcount},
        )


async def _claim(session: AsyncSession, document: TenderDocument) -> TenderDocumentFile:
    """Reserva el document per a aquest processat. Llanca 409 si ja n'hi ha un en curs."""
    highest = await session.scalar(
        sa.select(sa.func.max(TenderDocumentFile.version_number)).where(
            TenderDocumentFile.tender_document_id == document.id
        )
    )
    file = TenderDocumentFile(
        tender_document_id=document.id,
        source_record_id=document.source_record_id,
        source_url=document.url,
        version_number=(highest or 0) + 1,
        is_current=False,
        download_status=DownloadStatus.DOWNLOADING,
        started_at=datetime.now(UTC),
    )
    session.add(file)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ProcessingInProgressError(
            "Aquest document ja s'esta processant. Torna-ho a provar d'aqui a un moment."
        ) from None
    await session.refresh(file)
    return file


async def get_or_create_content(
    session: AsyncSession,
    *,
    data: bytes,
    sha256: str,
    mime_type: str,
    detected_extension: str,
    commit: bool = True,
) -> tuple[TenderDocumentContent, bool]:
    """Retorna el contingut i si s'acaba de crear.

    Aquest es el punt exacte de la deduplicacio, i el nivell son els BYTES: si
    el SHA-256 ja hi es, ni es puja el blob ni es tornara a parsejar, vinguin
    d'una descarrega o d'algu que els ha pujat. Prendre els camps un per un i
    no un `DownloadResult` es el que permet que les dues entrades comparteixin
    aquest punt sense que la pujada hagi de fer veure que ha baixat res.

    Amb `commit=False` no tanca la transaccio de qui crida (incorporacio atomica
    de l'operador, 3B-2): la insercio va dins d'un SAVEPOINT i la carrera pel
    mateix sha es resol igual, sense desfer la resta. Qui crida ha d'haver
    comencat la transaccio amb alguna escriptura: pysqlite no emet `BEGIN` fins a
    la primera, i un SAVEPOINT fora de transaccio es confirmaria en alliberar-lo.
    """
    existing = await session.scalar(
        sa.select(TenderDocumentContent).where(TenderDocumentContent.sha256 == sha256)
    )
    if existing is not None:
        return existing, False

    key = build_tender_document_key(sha256, detected_extension)
    await get_storage().upload(key, data, mime_type)

    content = TenderDocumentContent(
        sha256=sha256,
        storage_key=key,
        file_size=len(data),
        mime_type=mime_type,
        detected_extension=detected_extension,
        is_text_extractable=mime_type in settings.document_text_extraction_mime_types,
    )
    if not commit:
        try:
            async with session.begin_nested():
                session.add(content)
        except IntegrityError:
            winner = await session.scalar(
                sa.select(TenderDocumentContent).where(TenderDocumentContent.sha256 == sha256)
            )
            return winner, False
        return content, True

    session.add(content)
    try:
        await session.commit()
    except IntegrityError:
        # Un altre processat ha creat el mateix contingut mentrestant. El blob
        # ja hi es i es identic: nomes cal quedar-se el que ha guanyat.
        await session.rollback()
        winner = await session.scalar(
            sa.select(TenderDocumentContent).where(TenderDocumentContent.sha256 == sha256)
        )
        return winner, False
    await session.refresh(content)
    return content, True


async def _make_current(session: AsyncSession, file: TenderDocumentFile) -> None:
    """Fa vigent `file` i destrona qualsevol altre vigent del mateix document.

    Un sol `is_current` per document (`uq_tender_document_files_current`). A
    PostgreSQL l'index no es diferible i la unitat de treball no garanteix l'ordre
    de dos UPDATE de la mateixa taula: per aixo el destronament es un UPDATE a
    part, enviat ABANS de marcar el nou.
    """
    await session.execute(
        sa.update(TenderDocumentFile)
        .where(
            TenderDocumentFile.tender_document_id == file.tender_document_id,
            TenderDocumentFile.is_current.is_(True),
            TenderDocumentFile.id != file.id,
        )
        .values(is_current=False)
        .execution_options(synchronize_session="fetch")
    )
    file.is_current = True


async def _get_or_create_content(
    session: AsyncSession, result: DownloadResult
) -> tuple[TenderDocumentContent, bool]:
    return await get_or_create_content(
        session,
        data=result.content,
        sha256=result.sha256,
        mime_type=result.mime_type,
        detected_extension=result.detected_extension,
    )


async def _store_extraction(
    session: AsyncSession,
    content: TenderDocumentContent,
    result: ExtractionResult,
    *,
    commit: bool = True,
) -> TenderDocumentExtraction:
    """Desa una revisio NOVA d'extraccio amb les seves pagines.

    Mai modifica ni esborra una extraccio anterior ni les seves pagines: son
    l'evidencia que la fase de requisits citara, i una cita ha de continuar
    dient el mateix demà. Repetir el mateix parser genera una revisio nova, no
    una sobreescriptura.

    La revisio nova passa a `is_current` si ha acabat be. Si falla i la vigent tenia
    text, aquella es queda: una reextraccio fallida no pot deixar un document sense
    text que abans en tenia. Si la vigent no en tenia (fallida, no suportada o buida),
    la nova la substitueix i en diu la causa real: si no, un reintent (`should_retry`)
    o un parser nou que tornes a fallar ho tornaria a provar a cada processat.
    """
    previous = await current_extraction_for(session, content.id)

    highest = await session.scalar(
        sa.select(sa.func.max(TenderDocumentExtraction.revision_number)).where(
            TenderDocumentExtraction.content_id == content.id
        )
    )
    promote = (
        result.status != ExtractionStatus.FAILED
        or previous is None
        or not previous.text_char_count
    )

    if promote and previous is not None:
        # L'index unic parcial nomes admet una vigent per contingut: cal
        # destronar l'anterior i buidar-ho abans d'inserir la nova.
        previous.is_current = False
        await session.flush()

    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version=result.parser_version,
        revision_number=(highest or 0) + 1,
        status=result.status,
        quality=result.quality,
        page_count=result.page_count,
        extracted_page_count=result.extracted_page_count,
        empty_page_count=result.empty_page_count,
        text_char_count=result.text_char_count,
        ocr_candidate=result.ocr_candidate,
        parse_ms=result.parse_ms,
        error_code=result.error_code,
        error_message=result.error_message,
        is_current=promote,
        completed_at=datetime.now(UTC),
    )
    session.add(extraction)
    await session.flush()

    for page in result.pages:
        session.add(
            TenderDocumentPage(
                extraction_id=extraction.id,
                page_number=page.page_number,
                text=page.text,
                char_count=page.char_count,
                text_hash=page.text_hash,
                status=page.status,
            )
        )

    # La QA es calcula aqui i no despres: pertany a AQUESTA passada de parser.
    # Si el dia de dema s'extreu el mateix document amb una biblioteca millor,
    # la revisio nova tindra la seva propia qualitat i aquesta es conservara tal
    # com esta, que es el que permet dir si l'extraccio ha millorat.
    qa = analyse_extraction_quality(
        [
            QaPageInput(
                page_number=page.page_number,
                text=page.text,
                failed=page.status == PageExtractionStatus.FAILED,
            )
            for page in result.pages
        ],
        extraction_quality=result.quality,
    )
    extraction.qa_status = qa.status.value
    extraction.qa_version = qa.qa_version
    for issue in qa.issues:
        session.add(
            TenderExtractionIssue(
                extraction_id=extraction.id,
                page_number=issue.page_number,
                code=issue.code.value,
                detail=issue.detail or None,
            )
        )

    if commit:
        await session.commit()
        await session.refresh(extraction)
    else:
        await session.flush()

    logger.info(
        "document.extract.done",
        extra={
            "content_id": str(content.id),
            "extraction_id": str(extraction.id),
            "revision": extraction.revision_number,
            "is_current": extraction.is_current,
            "parser_version": result.parser_version,
            "pages": result.page_count,
            # Nomes el recompte: el text dels plecs no es loguega mai.
            "chars": result.text_char_count,
            "quality": str(result.quality),
            "parse_ms": result.parse_ms,
            "error_code": result.error_code,
        },
    )
    return extraction


async def _extract(
    session: AsyncSession, content: TenderDocumentContent, *, commit: bool = True
) -> TenderDocumentExtraction:
    """Parseja els bytes ja guardats. No fa cap peticio HTTP.

    Si es llegeix o no, ho decideix el tipus amb la configuracio VIGENT
    (`is_text_extractable_mime`), no la columna `is_text_extractable` desada quan va
    arribar el contingut: un DOCX desat abans que hi hagues parser de DOCX s'ha de
    poder llegir ara sense tocar-ne la fila (els continguts son immutables).
    """
    if not is_text_extractable_mime(content.mime_type):
        return await _store_extraction(
            session, content, unsupported_result(content.mime_type), commit=commit
        )

    data = await get_storage().download(content.storage_key)
    return await _store_extraction(
        session, content, await extract_text_async(content.mime_type, data), commit=commit
    )


def is_text_extractable_mime(mime_type: str | None) -> bool:
    """Si d'aquest tipus se n'extreu text ara mateix (`DOCUMENT_TEXT_EXTRACTION_MIME_TYPES`)."""
    return bool(mime_type) and mime_type in settings.document_text_extraction_mime_types


async def _ensure_current_extraction(
    session: AsyncSession, content: TenderDocumentContent, *, force: bool, commit: bool = True
) -> tuple[TenderDocumentExtraction, bool]:
    """Retorna l'extraccio vigent i si s'ha hagut de fer feina.

    Es reextreu quan es demana explicitament, quan no n'hi ha cap, quan la que
    hi ha no va acabar, quan la `parser_version` vigent ha canviat, o quan la que
    hi ha va fallar per l'entorn i no pel document i l'entorn ja ho permet
    (`should_retry`: un PDF xifrat llegit sense la biblioteca de l'AES, tambe en la
    forma antiga `corrupt_pdf`). Amb `commit=False` nomes fa flush: la transaccio es
    de qui crida.
    """
    current = await current_extraction_for(session, content.id)
    up_to_date = (
        current is not None
        # La versio vigent es la del parser QUE TOCA a aquest tipus: comparar-la
        # amb una de global faria caducar un TXT cada cop que canviés el parser
        # de PDF.
        and current.parser_version == parser_version_for(content.mime_type)
        and current.status in TERMINAL_EXTRACTION_STATES
        and not should_retry(current.error_code, current.error_message)
    )
    if up_to_date and not force:
        return current, False
    return await _extract(session, content, commit=commit), True


async def process_tender_document(
    session: AsyncSession,
    document: TenderDocument,
    *,
    force_download: bool = False,
    force_reextract: bool = False,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver | None = None,
) -> ProcessOutcome:
    """Descarrega, guarda i extreu el text d'un document de licitacio.

    Sense flags es idempotent: si ja hi ha una versio descarregada amb una
    extraccio vigent, no es toca la xarxa.

    Un document pujat per l'operador (`operator_upload`, 3B-2 §2.2) no te cap URL
    d'on tornar-lo a baixar: els seus bytes son els pujats. `force_download` no hi
    fa res (la re-extraccio, si).
    """
    if force_download and document.origin == TenderDocumentOrigin.OPERATOR_UPLOAD:
        logger.info(
            "document.process.force_download_ignored",
            extra={"document_id": str(document.id), "origin": str(document.origin)},
        )
        force_download = False
    await _reclaim_stale(session, document.id)
    current = await current_file_for(session, document.id)
    has_content = current is not None and current.content_id is not None

    # Re-extreure sense re-descarregar: nomes cal si tenim els bytes.
    if force_reextract and not force_download:
        if not has_content:
            raise NoContentToExtractError(
                "Aquest document no s'ha descarregat mai correctament: "
                "no hi ha res per re-extreure."
            )
        await _extract(session, current.content)
        await session.refresh(current)
        return ProcessOutcome(file=current, reused=False)

    # Cami idempotent: ja tenim bytes bons i no ens demanen tornar a baixar.
    if has_content and not force_download:
        _, worked = await _ensure_current_extraction(session, current.content, force=False)
        await session.refresh(current)
        return ProcessOutcome(file=current, reused=not worked)

    previous = current if has_content else None
    file = await _claim(session, document)

    try:
        result = await download_document(document.url, client=client, resolver=resolver)
    except DownloadError as exc:
        file.download_status = DownloadStatus.FAILED
        # Una fallada no destrona una versio bona; si no n'hi ha cap, la fallada
        # nova substitueix la fallada vigent anterior (un sol vigent per document).
        if previous is None:
            await _make_current(session, file)
        file.error_code = exc.code
        file.error_message = str(exc)[:500]
        file.http_status = exc.http_status
        await session.commit()
        await session.refresh(file)
        logger.warning(
            "document.process.failed",
            extra={
                "document_id": str(document.id),
                "file_id": str(file.id),
                "error_code": exc.code,
                "http_status": exc.http_status,
            },
        )
        return ProcessOutcome(file=file, reused=False)

    # Cas A: els mateixos bytes que la versio vigent. La fila reclamada era
    # provisional i no representa cap versio nova, aixi que s'elimina.
    if previous is not None and previous.content.sha256 == result.sha256:
        await session.delete(file)
        previous.last_checked_at = datetime.now(UTC)
        await session.commit()
        await _ensure_current_extraction(session, previous.content, force=force_reextract)
        await session.refresh(previous)
        return ProcessOutcome(file=previous, reused=not force_reextract)

    content, created = await _get_or_create_content(session, result)
    # Si un altre processat ha creat el mateix contingut alhora, la via de la
    # carrera fa rollback i caduca `file` i `document`. Amb AsyncSession llegir-ne
    # un atribut caducat faria IO implicit (MissingGreenlet): es recarreguen.
    for instance in (file, document):
        if sa.inspect(instance).expired_attributes:
            await session.refresh(instance)

    file.content_id = content.id
    file.download_status = DownloadStatus.DOWNLOADED
    file.http_status = result.http_status
    file.final_url = result.final_url
    file.original_filename = result.original_filename
    file.detected_filename = result.detected_filename
    file.declared_mime_type = result.declared_mime_type
    file.redirect_count = result.redirect_count
    file.download_ms = result.download_ms
    file.downloaded_at = datetime.now(UTC)
    file.last_checked_at = datetime.now(UTC)
    await _make_current(session, file)
    await session.commit()

    # Dedup logic (3B-2 §2.1): si un altre document vigent del tender ja te
    # aquests bytes, el de menys prioritat queda com a alies i no es llegeix.
    await assign_duplicate_alias(session, document.tender_id, document)
    await session.commit()

    # Cas C: si el contingut ja hi era, aqui no es torna a parsejar res.
    await _ensure_current_extraction(session, content, force=force_reextract)
    await session.refresh(file)

    # Baixar un plec i descobrir que el contingut ha canviat tambe es una
    # modificacio de la licitacio, encara que el feed no digui res: es
    # exactament el cas "mateixa URL, SHA nou". Si el SHA es el mateix, aixo no
    # crea cap revisio.
    await _record_tender_revision(session, document.tender_id)

    logger.info(
        "document.process.ok",
        extra={
            "document_id": str(document.id),
            "file_id": str(file.id),
            "content_id": str(content.id),
            "sha256": content.sha256,
            "bytes": content.file_size,
            "download_ms": result.download_ms,
            "new_content": created,
            "version": file.version_number,
        },
    )
    return ProcessOutcome(file=file, reused=False)


async def _record_tender_revision(session: AsyncSession, tender_id: uuid.UUID) -> None:
    """Deixa constancia si el contingut del document ha canviat la licitacio.

    Es fa aqui i no a la ingestió perque el SHA d'un plec nomes es coneix
    despres de baixar-lo. Sense aquest punt, substituir un plec sota la mateixa
    URL seria invisible per al cicle de vida.
    """
    tender = await session.get(Tender, tender_id)
    if tender is None:  # pragma: no cover - FK ON DELETE CASCADE ho impedeix
        return
    await record_revision(session, tender, trigger="document_processing")
    await session.commit()
