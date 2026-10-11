"""Incorporacio de documents publics oficials per l'operador de plataforma (3B-2 §2.2).

El feed no sempre enumera tot el paquet d'una licitacio publica (L'Escala: el
feed porta PCAP i PPT; els annexos son al perfil del contractant). Aquest servei
es l'UNICA via d'escriptura de `tender_documents` d'un tender public fora del
connector que el governa, i nomes la fa servir l'operador (`PlatformAdmin`).

Prioritat d'obtencio i provenance:

1. **Per URL oficial, descarregada pel sistema** (`add_document_by_url`): el host
   ha de ser de `DOCUMENT_OFFICIAL_HOSTS` (sufix de domini), a la URL i a cada
   salt de redireccio, a mes de la guarda SSRF. Es conserven `source_url`,
   `final_url`, `http_status`, `downloaded_at`, el sha del contingut, la pagina
   oficial (`contribution_source_url`) i l'actor.
2. **Pujada per l'operador** (`upload_public_document`) quan la URL oficial no es
   descarregable (SPA, sessio): URL sintetica `operator-upload://<uuid>` i
   `contribution_source_url` obligatoria.
3. **Verificacio** (`verify_document`): fins llavors el document es
   `pending_verification`, es veu als llistats pero no es analitzable
   (`ANALYZABLE_DOCUMENT`) i no mou cap empremta. A la fotografia de la
   licitacio porta la marca `contribution`, de manera que la seva arribada nomes
   dona `DOCUMENT_CONTRIBUTION_CHANGED` (informatiu) i la verificacio dona el
   `DOCUMENT_ADDED` (`requirements_affecting`) de sempre.

   **Sobreinvalidacio acceptada:** si el document verificat es un alies d'un del
   feed (mateixos bytes), continua sense ser analitzable i cap empremta no es mou,
   pero la revisio porta igualment un `DOCUMENT_ADDED` material. Es la mateixa
   semantica que un alies que arriba pel feed (Task 2): el detector de canvis no
   distingeix alies, i avisar de mes es preferible a no avisar.

La pagina d'origen (`contribution_source_url`) ha de ser http(s), sense
credencials (`invalid_page_url`) i d'un host oficial (`host_not_official`).

Incorporar es **atomic**: la descarrega HTTP es fa fora de cap transaccio
oberta; despres, contingut, document, fitxer, extraccio, alies, auditoria i
revisio es desen en UNA sola transaccio (tot o res). L'unic que pot quedar d'una
fallada es el blob a l'storage, adrecat pel sha i inert.

Retirar i restaurar (§2.4) son de l'operador i nomes sobre documents seus
(`operator_url`, `operator_upload`): els del feed els governa el connector i les
pujades propies, l'empresa propietaria d'un tender manual. Mai esborrat fisic.

**Cedir al feed** (`hand_over_to_feed`). Si el feed comenca a publicar una URL que
l'operador ja havia incorporat, la ingesta (Task 2) no se l'apropia: la deixa
intacta i en deixa un avis (`placsp.document.not_governed`). La resolucio es
de l'operador, pero amb prova, no amb la seva paraula. Regla:

* nomes un `operator_url` vigent (un `operator_upload` te URL sintetica i cap
  feed la pot publicar) d'un tender public;
* nomes si ja esta **verificat** (`document_not_verified`): cedir no pot saltar-se
  la verificacio ni rescatar un rebutjat;
* nomes si la **darrera entrada de la font** del tender (`latest_source_record_id`,
  el raw desat a l'storage i llegit amb el parser de la ingesta) publica
  exactament aquesta URL (`feed_does_not_publish_url`). Si el feed no la
  publiques, el sync seguent la retiraria sense actor i l'operador ja no la
  podria restaurar.

El document passa a `origin=feed`, governat per la font del tender, amb
`contribution_status=not_applicable` (i, per CHECK, sense `verified_*`). La
provenance de l'aportacio (`contributed_by/at`, `contribution_source_url`) es
queda a la fila; el contingut, els fitxers i l'historial, tambe. L'auditoria
porta l'entrada de la font usada com a prova.

Cada accio deixa una revisio `document_intake` (historial `internal`) i una fila
d'auditoria amb `company_id=None` (cap empresa hi intervé).
"""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
import sqlalchemy as sa
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.ingestion.placsp.parser import ParseError, parse_entry
from app.models.enums import (
    AuditAction,
    DocumentContributionStatus,
    DocumentFileOrigin,
    DownloadStatus,
    GlobalClassificationTrigger,
    SourceType,
    TenderDocumentOrigin,
    TenderDocumentType,
)
from app.models.identity import User
from app.models.sources import Source, SourceRecord
from app.models.tender_documents import TenderDocumentFile
from app.models.tenders import Tender, TenderDocument
from app.services import audit
from app.services.document_alias import (
    assign_duplicate_alias,
    promote_alias_if_canonical_retired,
)
from app.services.document_download import (
    DownloadError,
    download_document,
    extension_for,
    is_official_host,
)
from app.services.files import compute_sha256, sanitize_filename
from app.services.global_document_classification import classify_safely
from app.services.storage import StorageError, get_storage
from app.services.tender_document_processing import (
    _ensure_current_extraction,
    get_or_create_content,
)
from app.services.tender_document_upload import validate_tender_upload
from app.services.tender_lifecycle import DOCUMENT_INTAKE_TRIGGER, record_revision
from app.services.url_guard import Resolver

logger = logging.getLogger(__name__)

#: Esquema de la URL sintetica d'un document pujat per l'operador. No es
#: resoluble: nomes dona identitat a la fila (`(tender_id, url)` es unic).
OPERATOR_UPLOAD_URL_SCHEME = "operator-upload"

#: Origens que gestiona l'operador. Els altres els governa algu altre.
OPERATOR_ORIGINS = frozenset(
    {TenderDocumentOrigin.OPERATOR_URL, TenderDocumentOrigin.OPERATOR_UPLOAD}
)

AUDIT_ENTITY = "tender_document"

#: La UNIQUE que fa de `document_url_exists` (cap altra violacio ho es).
URL_UNIQUE_CONSTRAINT = "uq_tender_documents_tender_url"
_SQLITE_URL_UNIQUE = "tender_documents.tender_id, tender_documents.url"

#: Mida maxima d'`audit_logs.summary` (`services/audit.py`) i del motiu que hi cap.
AUDIT_SUMMARY_MAX = 500
HAND_OVER_REASON_MAX = 120


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #


class HostNotOfficialError(ValidationError):
    code = "host_not_official"


class TenderNotPublicError(ValidationError):
    """L'operador no escriu (ni llegeix) documents d'una licitacio d'empresa."""

    code = "tender_not_public"


class PageUrlError(ValidationError):
    code = "invalid_page_url"


class DocumentDownloadFailedError(ValidationError):
    """La descarrega no ha donat un document utilitzable. El codi es el de la descarrega."""

    def __init__(self, exc: DownloadError) -> None:
        super().__init__(
            str(exc), details={"http_status": exc.http_status} if exc.http_status else None
        )
        self.code = exc.code


class DocumentUrlExistsError(ConflictError):
    code = "document_url_exists"


class DocumentNotOperatorManagedError(ConflictError):
    code = "document_not_operator_managed"


class DocumentNotOperatorUrlError(ConflictError):
    code = "document_not_operator_url"


class DocumentNotVerifiedError(ConflictError):
    code = "document_not_verified"


class FeedDoesNotPublishUrlError(ConflictError):
    code = "feed_does_not_publish_url"


class DocumentAlreadyVerifiedError(ConflictError):
    code = "document_already_verified"


class DocumentAlreadyRejectedError(ConflictError):
    code = "document_already_rejected"


class DocumentRetiredError(ConflictError):
    code = "document_retired"


class DocumentAlreadyRetiredError(ConflictError):
    code = "document_already_retired"


class DocumentNotRetiredError(ConflictError):
    code = "document_not_retired"


# --------------------------------------------------------------------------- #
# Carrega i regles                                                             #
# --------------------------------------------------------------------------- #


async def load_public_tender(session: AsyncSession, tender_id: uuid.UUID) -> Tender:
    """La licitacio, si existeix i es publica (d'una font, sense propietari).

    Una licitacio oberta a ma es d'una empresa: l'operador no hi entra mai, ni
    per llegir-ne els documents (l'Administracio Tuvi no llegeix dades d'empresa).
    """
    row = (
        await session.execute(
            sa.select(Tender, Source.source_type)
            .join(Source, Source.id == Tender.source_id)
            .where(Tender.id == tender_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Licitacio no trobada.")
    tender, source_type = row
    if tender.owner_company_id is not None or source_type == SourceType.MANUAL:
        raise TenderNotPublicError("Aquesta licitacio no es publica.")
    return tender


async def load_document(
    session: AsyncSession, tender_id: uuid.UUID, document_id: uuid.UUID
) -> TenderDocument:
    """El document, si es d'aquesta licitacio (la ruta afirma la relacio)."""
    document = await session.scalar(
        sa.select(TenderDocument).where(
            TenderDocument.id == document_id, TenderDocument.tender_id == tender_id
        )
    )
    if document is None:
        raise NotFoundError("Document no trobat.")
    return document


def _assert_operator_managed(document: TenderDocument) -> None:
    if document.origin not in OPERATOR_ORIGINS:
        raise DocumentNotOperatorManagedError(
            "Aquest document no l'ha aportat l'operador: el governa la font o la "
            "seva empresa propietaria.",
            details={"origin": str(document.origin)},
        )


def _assert_active(document: TenderDocument) -> None:
    if document.removed_at is not None:
        raise DocumentRetiredError("El document esta retirat: cal restaurar-lo abans.")


def _check_page_url(url: str | None, *, required: bool) -> str | None:
    """La pagina oficial on es publicat el document.

    http(s), sense credencials (mai es desen ni es retornen) i d'un host oficial.
    No es descarrega: nomes es la provenance que l'operador declara.
    """
    value = (url or "").strip() or None
    if value is None:
        if required:
            raise PageUrlError("Cal la pagina oficial on es publicat el document.")
        return None
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise PageUrlError("La pagina d'origen ha de ser una URL http(s).")
    if parts.username or parts.password:
        raise PageUrlError("La pagina d'origen no pot portar credencials.")
    if not is_official_host(value, settings.DOCUMENT_OFFICIAL_HOSTS):
        raise HostNotOfficialError(
            "La pagina d'origen ha de ser d'una font oficial.",
            details={"allowed_hosts": list(settings.DOCUMENT_OFFICIAL_HOSTS)},
        )
    return value


def is_url_conflict(exc: IntegrityError) -> bool:
    """Si la violacio es la UNIQUE `(tender_id, url)` i no cap altra (un CHECK, una FK).

    PostgreSQL (asyncpg) dona el nom de la restriccio a l'error real, que
    SQLAlchemy deixa a `orig.__cause__` (psycopg, a `orig`); SQLite nomes en dona
    el missatge amb les columnes. Mateixa lectura que `app.ingestion.errors`.
    """
    orig = exc.orig
    cause = getattr(orig, "__cause__", None)
    real = cause if isinstance(cause, Exception) else orig
    for candidate in (real, orig):
        if getattr(candidate, "constraint_name", None) == URL_UNIQUE_CONSTRAINT:
            return True
    message = str(orig or "")
    return "UNIQUE constraint failed" in message and _SQLITE_URL_UNIQUE in message


def _now() -> datetime:
    return datetime.now(UTC)


async def _record(session: AsyncSession, tender_id: uuid.UUID) -> None:
    """Revisio `document_intake` (no en crea cap si la fotografia no ha canviat)."""
    tender = await session.get(Tender, tender_id)
    if tender is not None:
        await record_revision(session, tender, trigger=DOCUMENT_INTAKE_TRIGGER)


async def _audit(
    session: AsyncSession,
    action: AuditAction,
    actor: User,
    document: TenderDocument,
    summary: str,
) -> None:
    await audit.record(
        session,
        action=action,
        company_id=None,
        user_id=actor.id,
        entity_type=AUDIT_ENTITY,
        entity_id=document.id,
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# Incorporar                                                                   #
# --------------------------------------------------------------------------- #


async def _store_contribution(
    session: AsyncSession,
    *,
    tender: Tender,
    actor: User,
    document: TenderDocument,
    file: TenderDocumentFile,
    data: bytes,
    sha256: str,
    mime_type: str,
) -> TenderDocument:
    """Desa-ho tot en UNA transaccio: contingut, document, fitxer, alies, text,
    auditoria i revisio. Si res falla, no queda cap fila (el blob es inert)."""
    try:
        # El document primer: obre la transaccio amb una escriptura, de manera que
        # el SAVEPOINT del contingut hi queda niat (pysqlite no comenca la
        # transaccio fins a la primera escriptura i, sense, el RELEASE confirmaria).
        session.add(document)
        await session.flush()
        content, _created = await get_or_create_content(
            session,
            data=data,
            sha256=sha256,
            mime_type=mime_type,
            detected_extension=extension_for(mime_type),
            commit=False,
        )
        file.tender_document_id = document.id
        file.content_id = content.id
        session.add(file)
        await session.flush()
        await assign_duplicate_alias(session, tender.id, document)
        await _ensure_current_extraction(session, content, force=False, commit=False)
        await _audit(
            session,
            AuditAction.CREATE,
            actor,
            document,
            f"licitacio={tender.id}; origen={document.origin}; sha256={content.sha256}",
        )
        await _record(session, tender.id)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if is_url_conflict(exc):
            raise DocumentUrlExistsError(
                "Aquesta licitacio ja te un document amb aquesta URL."
            ) from None
        raise
    except BaseException:
        await session.rollback()
        raise
    await session.refresh(document)
    return document


async def _assert_url_free(session: AsyncSession, tender_id: uuid.UUID, url: str) -> None:
    existing = await session.scalar(
        sa.select(TenderDocument.id).where(
            TenderDocument.tender_id == tender_id, TenderDocument.url == url
        )
    )
    if existing is not None:
        raise DocumentUrlExistsError(
            "Aquesta licitacio ja te un document amb aquesta URL.",
            details={"document_id": str(existing)},
        )


async def add_document_by_url(
    session: AsyncSession,
    tender_id: uuid.UUID,
    url: str,
    source_page_url: str | None,
    actor: User,
    *,
    name: str | None = None,
    document_type: TenderDocumentType = TenderDocumentType.OTHER,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver | None = None,
) -> TenderDocument:
    """Descarrega un document d'una URL oficial i l'incorpora `pending_verification`.

    `governing_source_id` queda NULL: cap connector el governa, nomes l'operador.
    Cap peticio HTTP dins d'una transaccio oberta: es valida, es tanca la
    transaccio de lectura, es descarrega i nomes llavors s'escriu (atomicament).
    """
    url = url.strip()
    tender = await load_public_tender(session, tender_id)
    if not is_official_host(url, settings.DOCUMENT_OFFICIAL_HOSTS):
        raise HostNotOfficialError(
            "Nomes s'incorporen documents de fonts oficials.",
            details={"allowed_hosts": list(settings.DOCUMENT_OFFICIAL_HOSTS)},
        )
    page_url = _check_page_url(source_page_url, required=False)
    await _assert_url_free(session, tender.id, url)
    await session.commit()

    try:
        result = await download_document(
            url,
            client=client,
            resolver=resolver,
            allowed_hosts=settings.DOCUMENT_OFFICIAL_HOSTS,
        )
    except DownloadError as exc:
        if exc.code == HostNotOfficialError.code:
            raise HostNotOfficialError(str(exc)) from exc
        raise DocumentDownloadFailedError(exc) from exc

    now = _now()
    document = TenderDocument(
        tender_id=tender.id,
        name=(name or "").strip()
        or result.detected_filename
        or result.original_filename
        or "document",
        document_type=document_type,
        url=url,
        mime_type=result.mime_type,
        origin=TenderDocumentOrigin.OPERATOR_URL,
        governing_source_id=None,
        contribution_status=DocumentContributionStatus.PENDING_VERIFICATION,
        contributed_by=actor.id,
        contributed_at=now,
        contribution_source_url=page_url,
    )
    file = TenderDocumentFile(
        source_url=url,
        origin=DocumentFileOrigin.FETCHED,
        uploaded_by=actor.id,
        final_url=result.final_url,
        http_status=result.http_status,
        original_filename=result.original_filename,
        detected_filename=result.detected_filename,
        declared_mime_type=result.declared_mime_type,
        redirect_count=result.redirect_count,
        download_ms=result.download_ms,
        version_number=1,
        is_current=True,
        download_status=DownloadStatus.DOWNLOADED,
        started_at=now,
        downloaded_at=now,
        last_checked_at=now,
    )
    return await _store_contribution(
        session,
        tender=tender,
        actor=actor,
        document=document,
        file=file,
        data=result.content,
        sha256=result.sha256,
        mime_type=result.mime_type,
    )


async def upload_public_document(
    session: AsyncSession,
    tender_id: uuid.UUID,
    *,
    actor: User,
    filename: str | None,
    declared_mime_type: str | None,
    data: bytes,
    contribution_source_url: str | None,
    name: str | None = None,
    document_type: TenderDocumentType = TenderDocumentType.OTHER,
) -> TenderDocument:
    """Incorpora un fitxer pujat per l'operador, `pending_verification`.

    Mateixa validacio que les pujades propies (mida, MIME, magic bytes); no passa
    per `load_editable_tender`, que es de l'empresa propietaria d'un tender manual.
    """
    tender = await load_public_tender(session, tender_id)
    page_url = _check_page_url(contribution_source_url, required=True)
    mime_type = validate_tender_upload(data, declared_mime_type)
    original = sanitize_filename(filename)
    now = _now()
    document = TenderDocument(
        tender_id=tender.id,
        name=(name or "").strip() or original,
        document_type=document_type,
        url=f"{OPERATOR_UPLOAD_URL_SCHEME}://{uuid.uuid4()}",
        mime_type=mime_type,
        origin=TenderDocumentOrigin.OPERATOR_UPLOAD,
        governing_source_id=None,
        contribution_status=DocumentContributionStatus.PENDING_VERIFICATION,
        contributed_by=actor.id,
        contributed_at=now,
        contribution_source_url=page_url,
    )
    file = TenderDocumentFile(
        source_url=None,
        origin=DocumentFileOrigin.UPLOADED,
        uploaded_by=actor.id,
        original_filename=original,
        declared_mime_type=(declared_mime_type or "").split(";")[0].strip().lower() or None,
        version_number=1,
        is_current=True,
        download_status=DownloadStatus.DOWNLOADED,
        started_at=now,
        downloaded_at=now,
        last_checked_at=now,
    )
    return await _store_contribution(
        session,
        tender=tender,
        actor=actor,
        document=document,
        file=file,
        data=data,
        sha256=compute_sha256(data),
        mime_type=mime_type,
    )


# --------------------------------------------------------------------------- #
# Verificar, rebutjar, retirar, restaurar                                      #
# --------------------------------------------------------------------------- #


async def _finish(
    session: AsyncSession,
    document: TenderDocument,
    actor: User,
    action: AuditAction,
    summary: str,
) -> TenderDocument:
    """Tanca una accio: auditoria, revisio `document_intake`, commit.

    Despres, la classificacio global (3B-2 §3.2): verificar, rebutjar, retirar,
    restaurar o cedir canvia el conjunt analitzable. Idempotent per empremta (si
    el conjunt no ha canviat no fa res) i una fallada no desfa l'accio ja desada.
    """
    await session.flush()
    await _audit(session, action, actor, document, summary)
    await _record(session, document.tender_id)
    await session.commit()
    await classify_safely(session, document.tender_id, GlobalClassificationTrigger.DOCUMENT_INTAKE)
    await session.refresh(document)
    return document


async def verify_document(
    session: AsyncSession,
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: User,
    note: str | None = None,
) -> TenderDocument:
    """L'operador confirma que es el document publicat oficialment: passa a analitzable.

    Es l'unic moment en que el document entra a l'analisi, i per aixo la revisio
    porta `DOCUMENT_ADDED` (`requirements_affecting`); tambe si es un alies (vegeu
    la capcalera: sobreinvalidacio acceptada). Un rebutjat es pot verificar
    (l'operador rectifica); un retirat, no (cal restaurar-lo).
    """
    await load_public_tender(session, tender_id)
    document = await load_document(session, tender_id, document_id)
    _assert_operator_managed(document)
    _assert_active(document)
    if document.contribution_status == DocumentContributionStatus.VERIFIED:
        raise DocumentAlreadyVerifiedError("El document ja esta verificat.")

    previous = str(document.contribution_status)
    document.contribution_status = DocumentContributionStatus.VERIFIED
    document.verified_by = actor.id
    document.verified_at = _now()
    document.verification_note = (note or "").strip() or None
    await session.flush()
    # Ara es analitzable: pot passar a ser el canonic del seu grup de bytes.
    await assign_duplicate_alias(session, tender_id, document)
    return await _finish(
        session, document, actor, AuditAction.VERIFY, f"verificat; abans={previous}"
    )


async def reject_document(
    session: AsyncSession,
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: User,
    reason: str,
) -> TenderDocument:
    """No es el document publicat: queda `rejected`, visible i mai analitzable."""
    await load_public_tender(session, tender_id)
    document = await load_document(session, tender_id, document_id)
    _assert_operator_managed(document)
    _assert_active(document)
    if document.contribution_status == DocumentContributionStatus.REJECTED:
        raise DocumentAlreadyRejectedError("El document ja esta rebutjat.")

    previous = str(document.contribution_status)
    document.contribution_status = DocumentContributionStatus.REJECTED
    document.verified_by = None
    document.verified_at = None
    document.verification_note = reason.strip()
    await session.flush()
    await assign_duplicate_alias(session, tender_id, document)
    return await _finish(
        session, document, actor, AuditAction.UPDATE, f"rebutjat; abans={previous}"
    )


async def retire_document(
    session: AsyncSession,
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: User,
    reason: str,
) -> TenderDocument:
    """Retira un document de l'operador (`removed_at`, actor i motiu). Mai l'esborra.

    Si era el canonic d'un grup de bytes, el millor dels seus alies el substitueix
    (`promote_alias_if_canonical_retired`), dins de la mateixa revisio.
    """
    await load_public_tender(session, tender_id)
    document = await load_document(session, tender_id, document_id)
    _assert_operator_managed(document)
    if document.removed_at is not None:
        raise DocumentAlreadyRetiredError("El document ja esta retirat.")

    document.removed_at = _now()
    document.retired_by = actor.id
    document.retired_reason = reason.strip()
    await session.flush()
    await promote_alias_if_canonical_retired(session, document, record=False)
    return await _finish(session, document, actor, AuditAction.RETIRE, "retirat")


async def restore_document(
    session: AsyncSession,
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: User,
) -> TenderDocument:
    """Torna a fer vigent un document retirat per l'operador i el reagrupa per bytes."""
    await load_public_tender(session, tender_id)
    document = await load_document(session, tender_id, document_id)
    _assert_operator_managed(document)
    if document.removed_at is None:
        raise DocumentNotRetiredError("El document no esta retirat.")

    previous_reason = document.retired_reason
    document.removed_at = None
    document.retired_by = None
    document.retired_reason = None
    await session.flush()
    await assign_duplicate_alias(session, tender_id, document)
    return await _finish(
        session,
        document,
        actor,
        AuditAction.RESTORE,
        f"restaurat; motiu de la retirada={previous_reason or '-'}",
    )


# --------------------------------------------------------------------------- #
# Cedir al feed                                                                #
# --------------------------------------------------------------------------- #


async def feed_published_urls(
    session: AsyncSession, tender: Tender
) -> tuple[uuid.UUID | None, frozenset[str]]:
    """Les URL de documents que publica la darrera entrada de la font del tender.

    Llegeix el raw desat de `latest_source_record_id` i el passa pel mateix parser
    de la ingesta (`parse_entry`, que enumera els documents amb `_parse_documents`).
    Sense entrada, sense raw (absent o il.legible a l'storage: `StorageError`) o
    amb un raw que no es parseja (una lapida), la resposta es buida: no hi ha
    prova i cedir falla tancat amb 409, mai amb l'error de l'storage. El log
    nomes porta l'id del registre, la clau i la classe de l'error (cap XML).
    """
    if tender.latest_source_record_id is None:
        return None, frozenset()
    record = await session.get(SourceRecord, tender.latest_source_record_id)
    if record is None or record.source_id != tender.source_id:
        return None, frozenset()
    try:
        raw = await get_storage().download(record.raw_storage_key)
        entry = parse_entry(safe_fromstring(raw))
    except (
        StorageError,
        OSError,
        ParseError,
        ET.ParseError,
        DefusedXmlException,
        ValueError,
    ) as exc:
        logger.warning(
            "public_document.hand_over.raw_unreadable",
            extra={
                "source_record_id": str(record.id),
                "raw_storage_key": record.raw_storage_key,
                "error": type(exc).__name__,
            },
        )
        return record.id, frozenset()
    return record.id, frozenset(document.url for document in entry.documents)


def _hand_over_summary(
    document: TenderDocument, record_id: uuid.UUID, actor: User, reason: str
) -> str:
    """Resum d'auditoria que no es retalla mai pels fets: el motiu, retallat, al final."""
    verified_at = document.verified_at.isoformat() if document.verified_at else "-"
    fixed = (
        f"cedit al feed; source_record={record_id}; actor={actor.id}; "
        f"abans={document.origin}/{document.contribution_status}; "
        f"aportat_per={document.contributed_by}; "
        f"verificat_per={document.verified_by}; verificat_el={verified_at}; motiu="
    )
    room = max(0, min(HAND_OVER_REASON_MAX, AUDIT_SUMMARY_MAX - len(fixed)))
    return fixed + reason[:room]


async def hand_over_to_feed(
    session: AsyncSession,
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    actor: User,
    *,
    reason: str,
) -> TenderDocument:
    """Cedeix un `operator_url` verificat al feed que el publica (regla a la capcalera).

    El document passa a `origin=feed` governat per la font del tender: a partir
    d'ara el sync en pot actualitzar el nom i el tipus i retirar-lo. Contingut,
    fitxers, historial i provenance de l'aportacio es conserven; la verificacio
    (`verified_*`) es buida perque el CHECK ho exigeix, i queda a l'auditoria.
    """
    tender = await load_public_tender(session, tender_id)
    document = await load_document(session, tender_id, document_id)
    _assert_operator_managed(document)
    if document.origin != TenderDocumentOrigin.OPERATOR_URL:
        raise DocumentNotOperatorUrlError(
            "Nomes es pot cedir al feed un document incorporat per URL: una pujada "
            "no te cap URL que la font pugui publicar."
        )
    _assert_active(document)
    if document.contribution_status != DocumentContributionStatus.VERIFIED:
        raise DocumentNotVerifiedError(
            "Nomes es pot cedir al feed un document verificat.",
            details={"contribution_status": str(document.contribution_status)},
        )
    record_id, published = await feed_published_urls(session, tender)
    if record_id is None or document.url not in published:
        raise FeedDoesNotPublishUrlError(
            "La darrera entrada de la font no publica aquesta URL.",
            details={"source_record_id": str(record_id) if record_id else None},
        )

    reason = reason.strip()
    summary = _hand_over_summary(document, record_id, actor, reason)
    document.origin = TenderDocumentOrigin.FEED
    document.governing_source_id = tender.source_id
    document.contribution_status = DocumentContributionStatus.NOT_APPLICABLE
    document.verified_by = None
    document.verified_at = None
    document.verification_note = (
        f"Cedit al feed el {_now().date().isoformat()} per {actor.id} "
        f"(entrada {record_id}): {reason}"
    )[:500]
    await session.flush()
    # L'origen entra a l'ordre del canonic (feed > operator_url): es reagrupa.
    await assign_duplicate_alias(session, tender_id, document)
    return await _finish(session, document, actor, AuditAction.UPDATE, summary)


async def list_documents(session: AsyncSession, tender_id: uuid.UUID) -> list[TenderDocument]:
    """Tots els documents de la licitacio, retirats inclosos, en ordre d'arribada."""
    await load_public_tender(session, tender_id)
    return list(
        (
            await session.scalars(
                sa.select(TenderDocument)
                .where(TenderDocument.tender_id == tender_id)
                .order_by(TenderDocument.created_at, TenderDocument.id)
            )
        ).all()
    )


__all__ = [
    "OPERATOR_UPLOAD_URL_SCHEME",
    "add_document_by_url",
    "feed_published_urls",
    "hand_over_to_feed",
    "is_url_conflict",
    "list_documents",
    "load_public_tender",
    "reject_document",
    "restore_document",
    "retire_document",
    "upload_public_document",
    "verify_document",
]
