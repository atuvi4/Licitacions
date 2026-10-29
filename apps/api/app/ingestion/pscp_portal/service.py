"""Sincronitzacio dels documents d'una licitacio amb el portal de la PSCP.

La crida la feina `sync_portal_documents` del worker, per a una licitacio publica amb
`pscp_id_intern`. Quatre fases, i cap peticio de xarxa amb una transaccio oberta:

1. **Lectura completa** (`fetch_state`). De la publicacio de la fitxa (`Tender.source_url`,
   de qualsevol fase: se'n llegeix nomes la navegacio) s'arriba a la versio vigent de
   l'anunci de licitacio, i d'aquesta a totes les versions anteriors (esmenes). Despres el
   tauler, totes les preguntes i respostes i els avisos de les categories que poden afectar
   una oferta. Cada llista ha de quadrar amb el comptador del tauler. Les respostes i els
   avisos publicats abans de l'anunci son de fases anteriors (p. ex. una consulta
   preliminar del mercat): no es presenten com a aclariments de la licitacio. Si res falla o
   no quadra, s'atura aqui: **una lectura incompleta no retira mai res**.
2. **Reconciliacio** (una transaccio, dues revisions). Govern dels documents, quan una mateixa
   URL o un mateix contingut el poden reclamar diverses vies:
   * el feed mana: una URL que el feed publica no es toca, i un contingut (MD5) que el feed
     ja te sota una altra URL no es duplica. Al reves, quan el feed comenca a publicar una URL
     del portal, el feed se'n queda el govern (`placsp.service`);
   * una URL que el feed ha deixat de publicar i el portal encara publica passa al portal;
   * un fitxer que el portal torna a publicar amb un identificador nou (una esmena) i el
     mateix MD5 es el MATEIX document: se'n mou la URL, sense retirar-lo ni tornar-lo a
     baixar (es conserven l'identificador, els fitxers i les decisions de rol);
   * un `operator_url` amb la mateixa URL es **cedeix al portal** si no es rebutjat ni retirat
     i, si ja en tenia els bytes, l'MD5 d'aquests es el que publica el portal (prova a
     l'origen, en lloc de la verificacio de l'operador). Queda a l'auditoria, sense actor;
   * el que la font ja no publica es marca retirat (`removed_at`), mai s'esborra; si torna,
     es restaura. Una publicacio sense cap fitxer quan el connector en governa es una lectura
     sospitosa i no retira res;
   * les preguntes i respostes, els avisos i les esmenes es generen com a text (`render`):
     un contingut nou es una versio nova del mateix document; el mateix contingut no en crea.
   El que publica la font es una revisio `portal_sync` (historial `source`); les cessions i
   els regrupaments d'alies, una `document_intake` (historial `internal`), com al feed.
3. **Descarregues**: cada fitxer del portal sense bytes es baixa amb la cadencia del client,
   amb l'MD5 comprovat abans del tipus (`tender_document_processing`). Els mateixos bytes que
   un altre document queden com a alies (feed > portal > operador): el contingut es
   reaprofita pel SHA-256, mai es duplica. Una fallada del fitxer publicat (tipus no admes amb
   l'MD5 bo, massa gran) no es repeteix; una de la descarrega, al cap de
   `PSCP_PORTAL_RETRY_FAILED_SECONDS`.
4. **Tancament**, en una sessio nova (mai amb objectes d'abans de les descarregues): una sola
   revisio `document_processing` i la classificacio global. Si queda algun fitxer del portal
   sense bytes, la feina es `partial`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.errors import NoContentToExtractError, ProcessingInProgressError
from app.ingestion.pscp_portal import (
    LICITACIO_PHASE,
    PORTAL_API,
    PORTAL_SYNC_TRIGGER,
    SOURCE_CODE,
    SOURCE_NAME,
)
from app.ingestion.pscp_portal.client import PortalClient
from app.ingestion.pscp_portal.parser import (
    CATEGORY_BY_ID,
    Answer,
    ListedItem,
    Notice,
    PortalFile,
    PortalParseError,
    Publication,
    PublicationRef,
    canonical_download_url,
    expected_md5,
    generated_url,
    is_generated_url,
    parse_answer,
    parse_board,
    parse_navigation,
    parse_notice,
    parse_page,
    parse_publication,
    sync_target,
)
from app.ingestion.pscp_portal.render import (
    QA_TITLE,
    Amendment,
    amendment_name,
    notice_name,
    render_amendment,
    render_answers,
    render_notice,
)
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
from app.models.sources import Source
from app.models.tender_documents import TenderDocumentFile
from app.models.tenders import Tender, TenderDocument
from app.services import audit
from app.services.document_alias import (
    assign_duplicate_alias,
    promote_alias_if_canonical_retired,
)
from app.services.global_document_classification import classify_safely
from app.services.storage import StorageError, get_storage
from app.services.tender_document_processing import (
    _ensure_current_extraction,
    _make_current,
    current_file_for,
    get_or_create_content,
    permanently_failed,
    process_tender_document,
)
from app.services.tender_lifecycle import DOCUMENT_INTAKE_TRIGGER, record_revision
from app.services.url_guard import Resolver
from app.tenders.deadline import DEADLINE_TIME_ZONE

logger = logging.getLogger(__name__)

#: Tipus de document de cada grup de la publicacio (els grups desconeguts son annexos).
GROUP_TYPES: dict[str, TenderDocumentType] = {
    "plecsDeClausulesAdministratives": TenderDocumentType.ADMINISTRATIVE_TERMS,
    "plecsDePrescripcionsTecniques": TenderDocumentType.TECHNICAL_TERMS,
    "memoriaJustificativaContracte": TenderDocumentType.OTHER,
    "documentsAprovacio": TenderDocumentType.OTHER,
    "altresDocuments": TenderDocumentType.ADDITIONAL,
}
#: Els adjunts d'una resposta es llegeixen amb el plec; els d'un avis, no.
ANSWER_FILE_TYPE = TenderDocumentType.ADDITIONAL
NOTICE_FILE_TYPE = TenderDocumentType.OTHER

PAGE_SIZE = 100
#: Cap llista del tauler d'una licitacio no te tantes pagines: si n'hi ha mes, s'atura.
MAX_PAGES = 50
#: Cap publicacio real no s'hi acosta: una resposta amb mes documents es anomala.
MAX_DOCUMENTS = 500
#: Ni un tauler: mes peticions que aixo per llegir-lo sencer es una resposta anomala.
MAX_BOARD_REQUESTS = 2000
_NAME_MAX = 500
_CODE_MAX = 50
_NOTE_IN_AUDIT = 120
AUDIT_ENTITY = "tender_document"


class PortalSyncNotAvailableError(Exception):
    """La licitacio no es pot sincronitzar amb el portal (privada, sense fitxa de la PSCP,
    sense anunci de licitacio...)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PortalSyncConflictError(Exception):
    """Una altra escriptura dels documents de la licitacio ha guanyat la carrera."""


# --------------------------------------------------------------------------- #
# Lectura                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class WantedDocument:
    """Un document que el portal publica ara per a la licitacio."""

    url: str
    name: str
    document_type: TenderDocumentType
    source_code: str
    external_id: str | None
    published_at: date | None
    #: Fitxer del portal: l'MD5 publicat. `None` en un document generat.
    md5: str | None = None
    #: Document generat: el text, ja codificat. `None` en un fitxer del portal.
    content: bytes | None = None

    def identity(self) -> list[Any]:
        return [
            self.url,
            self.name,
            str(self.document_type),
            self.source_code,
            self.external_id,
            self.published_at.isoformat() if self.published_at else None,
            self.md5,
            hashlib.sha256(self.content).hexdigest() if self.content is not None else None,
        ]


@dataclass(frozen=True, slots=True)
class PortalState:
    """Tot el que el portal publica ara per a la licitacio, llegit sencer."""

    ref: PublicationRef
    publication: Publication
    wanted: tuple[WantedDocument, ...]
    answers: int
    notices: int
    amendments: int
    #: Titols dels fitxers publicats buits (MD5 de fitxer buit): no hi ha res a baixar.
    skipped_empty: tuple[str, ...] = ()
    #: Respostes i avisos de fases anteriors a l'anunci (no es presenten com a de la licitacio).
    answers_before: int = 0
    notices_before: int = 0

    @property
    def fingerprint(self) -> str:
        """Empremta del que publica el portal: si no canvia, la sincronitzacio no fa res."""
        canonical = sorted(document.identity() for document in self.wanted)
        payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _local_date(moment: datetime | None) -> date | None:
    return moment.astimezone(DEADLINE_TIME_ZONE).date() if moment else None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _published_order(moment: datetime | None, ident: int) -> tuple:
    return (moment is None, moment or datetime.min, ident)


async def _read_list(
    client: PortalClient, path: str, *, purpose: str
) -> tuple[dict[int, ListedItem], int]:
    items: dict[int, ListedItem] = {}
    total = 0
    for number in range(MAX_PAGES):
        page = parse_page(
            await client.get_json(f"{path}?page={number}&size={PAGE_SIZE}", purpose=purpose)
        )
        if page.number != number:
            raise PortalParseError("La llista no ha tornat la pagina demanada.")
        total = page.total_elements
        for item in page.items:
            items.setdefault(item.id, item)
        if page.last or number + 1 >= page.total_pages:
            return items, total
    raise PortalParseError("La llista te massa pagines.")


async def _list_all(
    client: PortalClient, path: str, expected: int, *, purpose: str
) -> list[ListedItem]:
    """Totes les pagines d'una llista. Ha de quadrar amb el comptador del tauler: si no hi
    quadra, es torna a llegir sencera una vegada (una pagina pot haver canviat entremig)."""
    for _attempt in range(2):
        items, total = await _read_list(client, path, purpose=purpose)
        if total == expected and len(items) == expected:
            return list(items.values())
    raise PortalParseError("La llista no quadra amb el comptador del tauler.")


async def fetch_state(client: PortalClient, ref: PublicationRef) -> PortalState:
    """Llegeix sencer el que el portal publica per a l'expedient. No toca la base."""
    expedient = ref.expedient_id
    publications: dict[int, Publication] = {}

    async def publication(publication_id: int, data: Any = None) -> Publication:
        if publication_id not in publications:
            if data is None:
                data = await client.get_json(
                    f"detall-publicacio-expedient/{expedient}/{publication_id}",
                    purpose="publicacio",
                )
            parsed = parse_publication(data)
            if parsed.expedient_id != expedient or parsed.publication_id != publication_id:
                raise PortalParseError("La publicacio no es la demanada.")
            publications[publication_id] = parsed
        return publications[publication_id]

    # De la publicacio de la fitxa nomes cal la navegacio: pot ser d'una altra fase.
    entry_data = await client.get_json(
        f"detall-publicacio-expedient/{expedient}/{ref.publication_id}", purpose="publicacio"
    )
    entry = parse_navigation(entry_data)
    if entry.expedient_id != expedient or entry.publication_id != ref.publication_id:
        raise PortalParseError("La publicacio no es la demanada.")
    current_id = entry.phase_publication(LICITACIO_PHASE)
    if current_id is None:
        raise PortalSyncNotAvailableError("no_licitacio_phase")
    current = await publication(
        current_id, entry_data if current_id == ref.publication_id else None
    )
    if current.phase != LICITACIO_PHASE:
        raise PortalParseError("La publicacio vigent no es l'anunci de licitacio.")

    # Versions de l'anunci, de l'original a la vigent: cada una despres de la primera es
    # una esmena, i se'n diu que ha canviat respecte de l'anterior.
    version_ids = [item.publication_id for item in current.amendments] or [current_id]
    if current_id not in version_ids:
        raise PortalParseError("La versio vigent no es a la navegacio d'esmenes.")
    version_ids = version_ids[: version_ids.index(current_id) + 1]
    if current.amendment_reason and len(version_ids) < 2:
        raise PortalParseError("Una versio esmenada sense la navegacio de les seves versions.")
    versions = [await publication(version_id) for version_id in version_ids]
    amendments = [
        Amendment(version=versions[index], previous=versions[index - 1])
        for index in range(1, len(versions))
    ]
    # Data de publicacio de cada fitxer: la de la primera versio que en porta el contingut
    # (en esmenar, el portal torna a publicar els mateixos bytes amb identificadors nous).
    first_seen: dict[str, date | None] = {}
    for version in versions:
        for file in version.files:
            first_seen.setdefault(file.md5, _local_date(version.published_at))
    # El que es publica abans de l'anunci de licitacio es d'una fase anterior.
    since = current.amendments[0].published_at if current.amendments else current.published_at

    board = parse_board(
        await client.get_json(
            f"detall-publicacio-expedient/tauler-avisos/{expedient}", purpose="tauler"
        )
    )
    counts = [board.qa_count, *(entry.count for entry in board.entries)]
    needed = sum(counts) + sum(math.ceil(count / PAGE_SIZE) for count in counts)
    if needed > MAX_BOARD_REQUESTS:
        raise PortalParseError("El tauler anuncia mes elements dels que es poden llegir.")
    client.allow(needed)

    answers: list[Answer] = []
    if board.qa_count:
        listed = await _list_all(
            client, f"respostes/{expedient}", board.qa_count, purpose="respostes"
        )
        for item in listed:
            answer = parse_answer(
                await client.get_json(f"detall-avis/resposta/{item.id}", purpose="resposta"),
                item.id,
                expedient,
            )
            if not answer.unpublished:
                answers.append(answer)

    notices: list[Notice] = []
    for board_entry in board.entries:
        if board_entry.count == 0:
            continue
        category = board_entry.category
        if board_entry.direct_id is not None:
            ids = [board_entry.direct_id]
        else:
            ids = [
                item.id
                for item in await _list_all(
                    client,
                    f"avisos-tauler/{category.category_id}/{expedient}",
                    board_entry.count,
                    purpose=f"avisos:{category.key}",
                )
            ]
        for notice_id in ids:
            notice = parse_notice(
                await client.get_json(
                    f"detall-avis/avis-recurs/{notice_id}", purpose=f"avis:{category.key}"
                ),
                notice_id,
                expedient,
                category.category_id,
            )
            if not notice.unpublished:
                notices.append(notice)

    def before_licitacio(moment: datetime | None) -> bool:
        return since is not None and moment is not None and moment < since

    answers.sort(key=lambda answer: _published_order(answer.published_at, answer.id))
    notices.sort(key=lambda notice: _published_order(notice.published_at, notice.id))
    kept_answers = [answer for answer in answers if not before_licitacio(answer.published_at)]
    kept_notices = [notice for notice in notices if not before_licitacio(notice.published_at)]
    state = _state(
        ref,
        current,
        first_seen,
        kept_answers,
        kept_notices,
        amendments,
        answers_before=len(answers) - len(kept_answers),
        notices_before=len(notices) - len(kept_notices),
    )
    if len(state.wanted) > MAX_DOCUMENTS:
        raise PortalParseError("La publicacio porta mes documents dels que es poden incorporar.")
    return state


def _superseded(answers: list[Answer]) -> dict[int, int]:
    """Resposta -> la versio que la substitueix, quan totes dues son a la llista."""
    present = {answer.id for answer in answers}
    replaced: dict[int, int] = {}
    for answer in answers:
        if len(answer.versions) < 2:
            continue
        latest = answer.versions[-1]
        for version in answer.versions[:-1]:
            if version in present and version != latest:
                replaced[version] = latest
    return replaced


def _state(
    ref: PublicationRef,
    current: Publication,
    first_seen: dict[str, date | None],
    answers: list[Answer],
    notices: list[Notice],
    amendments: list[Amendment],
    *,
    answers_before: int = 0,
    notices_before: int = 0,
) -> PortalState:
    expedient = ref.expedient_id
    wanted: dict[str, WantedDocument] = {}
    skipped_empty: list[str] = []

    def add_file(file: PortalFile, document_type: TenderDocumentType, code: str, day) -> None:
        if file.is_empty:
            skipped_empty.append(file.title)
            return
        # El mateix fitxer a la publicacio i adjunt a una resposta es un sol document.
        wanted.setdefault(
            file.url,
            WantedDocument(
                url=file.url,
                name=_clip(file.title, _NAME_MAX),
                document_type=document_type,
                source_code=code[:_CODE_MAX],
                external_id=str(file.document_id),
                published_at=day,
                md5=file.md5,
            ),
        )

    def add_generated(
        url: str, name: str, kind: TenderDocumentType, code: str, ident, day, content: bytes
    ) -> None:
        wanted[url] = WantedDocument(
            url=url,
            name=_clip(name, _NAME_MAX),
            document_type=kind,
            source_code=code[:_CODE_MAX],
            external_id=ident,
            published_at=day,
            content=content,
        )

    for file in current.files:
        add_file(
            file,
            GROUP_TYPES.get(file.group, TenderDocumentType.ADDITIONAL),
            file.group,
            first_seen.get(file.md5),
        )
    if answers:
        days = [_local_date(answer.published_at) for answer in answers]
        add_generated(
            generated_url(expedient, "preguntes-respostes"),
            QA_TITLE,
            TenderDocumentType.ADDITIONAL,
            "preguntes-respostes",
            None,
            max((day for day in days if day), default=None),
            render_answers(
                current, answers, excluded=answers_before, superseded=_superseded(answers)
            ),
        )
        for answer in answers:
            for file in answer.files:
                add_file(file, ANSWER_FILE_TYPE, "resposta", _local_date(answer.published_at))
    for notice in notices:
        category = CATEGORY_BY_ID.get(notice.category_id)
        code = f"avis:{category.key if category else notice.category_id}"
        add_generated(
            generated_url(expedient, "avis", notice.id),
            notice_name(notice),
            TenderDocumentType.NOTICE,
            code,
            str(notice.id),
            _local_date(notice.published_at),
            render_notice(current, notice),
        )
        for file in notice.files:
            add_file(file, NOTICE_FILE_TYPE, code, _local_date(notice.published_at))
    for amendment in amendments:
        version = amendment.version
        add_generated(
            generated_url(expedient, "esmena", version.publication_id),
            amendment_name(amendment),
            TenderDocumentType.NOTICE,
            "esmena",
            str(version.publication_id),
            _local_date(version.published_at),
            render_amendment(amendment),
        )
    return PortalState(
        ref=ref,
        publication=current,
        wanted=tuple(wanted.values()),
        answers=len(answers),
        notices=len(notices),
        amendments=len(amendments),
        skipped_empty=tuple(skipped_empty),
        answers_before=answers_before,
        notices_before=notices_before,
    )


# --------------------------------------------------------------------------- #
# Reconciliacio                                                                #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Reconciled:
    created: list[TenderDocument] = field(default_factory=list)
    updated: list[TenderDocument] = field(default_factory=list)
    restored: list[TenderDocument] = field(default_factory=list)
    removed: list[TenderDocument] = field(default_factory=list)
    #: El mateix fitxer (MD5) publicat amb un identificador nou: s'ha mogut la URL.
    repointed: list[TenderDocument] = field(default_factory=list)
    #: URL que el feed ha deixat de publicar i el portal encara publica: ara la governa ell.
    adopted_from_feed: list[TenderDocument] = field(default_factory=list)
    taken_over: list[TenderDocument] = field(default_factory=list)
    #: La URL o el contingut ja els te el feed (feed > portal): no s'ha tocat res.
    feed_owned: list[TenderDocument] = field(default_factory=list)
    #: La URL la te un document que no es pot cedir (rebutjat, retirat, MD5 diferent...).
    not_governed: list[TenderDocument] = field(default_factory=list)
    #: Documents generats amb un text nou (una versio nova del fitxer).
    generated_versions: list[TenderDocument] = field(default_factory=list)
    revision_changed: bool = False

    @property
    def changed(self) -> bool:
        return bool(
            self.created
            or self.updated
            or self.restored
            or self.removed
            or self.repointed
            or self.adopted_from_feed
            or self.taken_over
            or self.generated_versions
            or self.revision_changed
        )

    def counts(self) -> dict[str, int]:
        return {
            "created": len(self.created),
            "updated": len(self.updated),
            "restored": len(self.restored),
            "removed": len(self.removed),
            "repointed": len(self.repointed),
            "adopted_from_feed": len(self.adopted_from_feed),
            "taken_over": len(self.taken_over),
            "feed_owned": len(self.feed_owned),
            "not_governed": len(self.not_governed),
            "generated_versions": len(self.generated_versions),
        }


async def portal_source(session: AsyncSession) -> Source:
    """La font `pscp_portal` (es crea des del codi, no per migracio). Fa commit si la crea."""
    query = sa.select(Source).where(Source.code == SOURCE_CODE)
    source = await session.scalar(query)
    if source is not None:
        return source
    session.add(
        Source(
            code=SOURCE_CODE,
            name=SOURCE_NAME,
            source_type=SourceType.API,
            base_url=PORTAL_API,
            enabled=True,
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # Una altra sincronitzacio l'ha creat alhora: es la mateixa font.
        await session.rollback()
    return await session.scalar(query)


def _assign(document: TenderDocument, name: str, value: Any) -> bool:
    if getattr(document, name) == value:
        return False
    setattr(document, name, value)
    return True


def _apply_metadata(document: TenderDocument, wanted: WantedDocument) -> bool:
    changed = False
    for name, value in (
        ("name", wanted.name),
        ("document_type", wanted.document_type),
        ("source_document_type_code", wanted.source_code),
        ("external_id", wanted.external_id),
        ("published_at", wanted.published_at),
    ):
        changed = _assign(document, name, value) or changed
    return changed


#: Els bytes vigents hi son pero no es poden llegir: l'MD5 no es pot comprovar.
UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class StoredBytes:
    """Els bytes vigents d'un document en el moment de llegir-los (abans de cap escriptura)."""

    #: `None` si no en te; `UNREADABLE` si no es poden llegir de l'emmagatzematge.
    md5: str | None
    content_id: uuid.UUID | None


async def _stored_bytes(session: AsyncSession, document: TenderDocument) -> StoredBytes:
    """MD5 i contingut dels bytes vigents (si no es poden llegir, no es pot provar res i no
    es cedeix)."""
    current = await current_file_for(session, document.id)
    if current is None or current.content is None:
        return StoredBytes(None, None)
    try:
        data = await get_storage().download(current.content.storage_key)
    except StorageError:
        logger.warning("pscp_portal.takeover.unreadable", extra={"document_id": str(document.id)})
        return StoredBytes(UNREADABLE, current.content_id)
    return StoredBytes(
        hashlib.md5(data, usedforsecurity=False).hexdigest().upper(), current.content_id
    )


def _current_content_is(document_id: uuid.UUID, content_id: uuid.UUID | None):
    """Condicio SQL: el fitxer vigent del document te aquest contingut (o cap, amb `None`)."""
    with_content = sa.exists().where(
        TenderDocumentFile.tender_document_id == document_id,
        TenderDocumentFile.is_current.is_(True),
        TenderDocumentFile.content_id.is_not(None),
    )
    if content_id is None:
        return ~with_content
    return sa.exists().where(
        TenderDocumentFile.tender_document_id == document_id,
        TenderDocumentFile.is_current.is_(True),
        TenderDocumentFile.content_id == content_id,
    )


def _may_take_over(document: TenderDocument, wanted: WantedDocument) -> bool:
    return (
        document.origin == TenderDocumentOrigin.OPERATOR_URL
        and wanted.md5 is not None
        and document.contribution_status != DocumentContributionStatus.REJECTED
        and document.removed_at is None
        and document.retired_by is None
    )


async def _take_over(
    session: AsyncSession,
    document: TenderDocument,
    source: Source,
    wanted: WantedDocument,
    stored: StoredBytes,
    now: datetime,
) -> bool:
    """Cedeix al portal un `operator_url` amb la mateixa URL, amb l'MD5 com a prova.

    UPDATE condicional: si mentrestant l'operador l'ha rebutjat, retirat o cedit al feed, la
    seva decisio mana i no es cedeix res. I els bytes vigents han de ser els que s'han
    comprovat (o cap, si no en tenia): uns bytes arribats entremig no es cedeixen sense MD5.
    """
    md5 = stored.md5
    verified_at = document.verified_at.isoformat() if document.verified_at else "-"
    previous_note = " ".join((document.verification_note or "-").split())[:_NOTE_IN_AUDIT]
    summary = (
        f"cedit al portal; md5={wanted.md5} ({'comprovat' if md5 else 'sense bytes encara'}); "
        f"abans={document.origin}/{document.contribution_status}; "
        f"aportat_per={document.contributed_by}; verificat_per={document.verified_by}; "
        f"verificat_el={verified_at}; nota_anterior={previous_note}"
    )
    note = (
        f"Cedit al portal de la PSCP el {now.date().isoformat()}: la URL es de la publicacio "
        f"vigent i l'MD5 publicat {'coincideix' if md5 else 'es comprovara en baixar-lo'}."
    )[:500]
    result = await session.execute(
        sa.update(TenderDocument)
        .where(
            TenderDocument.id == document.id,
            TenderDocument.origin == TenderDocumentOrigin.OPERATOR_URL,
            TenderDocument.contribution_status != DocumentContributionStatus.REJECTED,
            TenderDocument.removed_at.is_(None),
            TenderDocument.retired_by.is_(None),
            _current_content_is(document.id, stored.content_id),
        )
        .values(
            origin=TenderDocumentOrigin.PORTAL,
            governing_source_id=source.id,
            contribution_status=DocumentContributionStatus.NOT_APPLICABLE,
            verified_by=None,
            verified_at=None,
            verification_note=note,
        )
        .execution_options(synchronize_session=False)
    )
    await session.refresh(document)
    if result.rowcount != 1:
        return False
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=None,
        user_id=None,
        entity_type=AUDIT_ENTITY,
        entity_id=document.id,
        summary=summary,
    )
    return True


async def _adopt_from_feed(session: AsyncSession, document: TenderDocument, source: Source) -> bool:
    """Una URL que el feed ha retirat i el portal encara publica: passa al portal (auditat).

    UPDATE condicional: si el feed l'ha tornat a publicar mentrestant, es del feed.
    """
    result = await session.execute(
        sa.update(TenderDocument)
        .where(
            TenderDocument.id == document.id,
            TenderDocument.origin == TenderDocumentOrigin.FEED,
            TenderDocument.removed_at.is_not(None),
        )
        .values(
            origin=TenderDocumentOrigin.PORTAL,
            governing_source_id=source.id,
            removed_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    await session.refresh(document)
    if result.rowcount != 1:
        return False
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=None,
        user_id=None,
        entity_type=AUDIT_ENTITY,
        entity_id=document.id,
        summary="cedit al portal: el feed l'havia retirat i la publicacio vigent del portal "
        f"el continua publicant; md5={expected_md5(document.url)}",
    )
    return True


async def _store_generated(
    session: AsyncSession, document: TenderDocument, data: bytes, now: datetime
) -> bool:
    """Fa vigent el text generat com a versio nova del document, si no es el que ja hi ha."""
    sha256 = hashlib.sha256(data).hexdigest()
    current = await current_file_for(session, document.id)
    if current is not None and current.content is not None and current.content.sha256 == sha256:
        return False
    highest = await session.scalar(
        sa.select(sa.func.max(TenderDocumentFile.version_number)).where(
            TenderDocumentFile.tender_document_id == document.id
        )
    )
    file = TenderDocumentFile(
        tender_document_id=document.id,
        source_url=document.url,
        origin=DocumentFileOrigin.FETCHED,
        original_filename=f"{(document.source_document_type_code or 'pscp').replace(':', '-')}.txt",
        version_number=(highest or 0) + 1,
        is_current=False,
        download_status=DownloadStatus.DOWNLOADED,
        started_at=now,
        downloaded_at=now,
        last_checked_at=now,
    )
    # El fitxer primer: obre la transaccio amb una escriptura abans del SAVEPOINT del
    # contingut (vegeu `get_or_create_content`).
    session.add(file)
    await session.flush()
    content, _created = await get_or_create_content(
        session,
        data=data,
        sha256=sha256,
        mime_type="text/plain",
        detected_extension="txt",
        commit=False,
    )
    file.content_id = content.id
    await session.flush()
    await _make_current(session, file)
    await session.flush()
    await _ensure_current_extraction(session, content, force=False, commit=False)
    return True


def _from_publication(document: TenderDocument) -> bool:
    """Un fitxer de la publicacio (no generat, ni adjunt d'una resposta o d'un avis)."""
    code = document.source_document_type_code or ""
    return (
        not is_generated_url(document.url) and code != "resposta" and not code.startswith("avis:")
    )


def _key(document: TenderDocument) -> str:
    """La URL d'un document per comparar-la amb les del portal (la de descarrega, canonica)."""
    return canonical_download_url(document.url) or document.url


async def reconcile(
    session: AsyncSession, tender: Tender, source: Source, state: PortalState, now: datetime
) -> Reconciled:
    """Aplica `state` als documents de la licitacio. No fa commit."""
    query = sa.select(TenderDocument).where(TenderDocument.tender_id == tender.id)
    rows = list((await session.scalars(query)).all())
    by_url: dict[str, TenderDocument] = {}
    for document in rows:
        by_url.setdefault(document.url, document)
    for document in rows:
        # Una URL de descarrega del portal escrita d'una altra manera es la mateixa URL.
        canonical = canonical_download_url(document.url)
        if canonical is not None:
            by_url.setdefault(canonical, document)

    def governed(document: TenderDocument) -> bool:
        return (
            document.origin == TenderDocumentOrigin.PORTAL
            and document.governing_source_id == source.id
        )

    # Una publicacio sense cap fitxer quan el connector en governa de la publicacio es una
    # lectura sospitosa: retirar-ho tot per una resposta anomala no es pot desfer sense una
    # altra lectura. Els adjunts de respostes i avisos no hi compten (no son de la publicacio).
    if not state.publication.files and any(
        governed(document) and document.removed_at is None and _from_publication(document)
        for document in rows
    ):
        raise PortalParseError("La publicacio vigent no porta cap fitxer: lectura sospitosa.")

    wanted_keys = {wanted.url for wanted in state.wanted}
    # Lectures de l'emmagatzematge abans de cap escriptura: cap fila bloquejada mentrestant.
    candidates: dict[uuid.UUID, StoredBytes] = {}
    for wanted in state.wanted:
        document = by_url.get(wanted.url)
        if document is not None and _may_take_over(document, wanted):
            candidates[document.id] = await _stored_bytes(session, document)

    # El mateix fitxer (MD5) sota una URL nova: es el mateix document, que es mou.
    governed_by_md5: dict[str, list[TenderDocument]] = defaultdict(list)
    feed_by_md5: dict[str, TenderDocument] = {}
    for document in rows:
        md5 = expected_md5(document.url)
        if md5 is None:
            continue
        if governed(document) and _key(document) not in wanted_keys:
            governed_by_md5[md5].append(document)
        elif document.origin == TenderDocumentOrigin.FEED and document.removed_at is None:
            feed_by_md5.setdefault(md5, document)
    repoint: dict[str, TenderDocument] = {}
    for wanted in state.wanted:
        if wanted.md5 is None or wanted.url in by_url:
            continue
        options = governed_by_md5.get(wanted.md5)
        if options:
            options.sort(key=lambda d: (d.removed_at is not None, d.created_at, str(d.id)))
            repoint[wanted.url] = options.pop(0)
    moving = {document.id for document in repoint.values()}

    outcome = Reconciled()
    # --- El que publica la font: revisio `portal_sync` -----------------------------------
    for document in rows:
        if (
            governed(document)
            and document.id not in moving
            and _key(document) not in wanted_keys
            and document.removed_at is None
        ):
            document.removed_at = now
            outcome.removed.append(document)

    generated: list[tuple[TenderDocument, bytes]] = []
    takeovers: list[tuple[TenderDocument, WantedDocument]] = []
    for wanted in state.wanted:
        document = by_url.get(wanted.url)
        if document is None and wanted.url in repoint:
            document = repoint[wanted.url]
            document.url = wanted.url
            if document.removed_at is not None:
                document.removed_at = None
                outcome.restored.append(document)
            _apply_metadata(document, wanted)
            outcome.repointed.append(document)
        elif document is None:
            if wanted.md5 is not None and wanted.md5 in feed_by_md5:
                # El feed ja te aquest contingut sota una altra URL (feed > portal).
                outcome.feed_owned.append(feed_by_md5[wanted.md5])
                continue
            document = TenderDocument(
                tender_id=tender.id,
                url=wanted.url,
                origin=TenderDocumentOrigin.PORTAL,
                governing_source_id=source.id,
                contribution_status=DocumentContributionStatus.NOT_APPLICABLE,
            )
            _apply_metadata(document, wanted)
            session.add(document)
            outcome.created.append(document)
        elif governed(document):
            if document.removed_at is not None:
                document.removed_at = None
                outcome.restored.append(document)
            if _apply_metadata(document, wanted):
                outcome.updated.append(document)
        elif document.origin == TenderDocumentOrigin.FEED:
            if document.removed_at is None or not await _adopt_from_feed(session, document, source):
                outcome.feed_owned.append(document)
                continue
            _apply_metadata(document, wanted)
            outcome.adopted_from_feed.append(document)
        elif document.id in candidates:
            takeovers.append((document, wanted))
            continue
        else:
            logger.warning(
                "pscp_portal.document.not_governed",
                extra={
                    "tender_id": str(tender.id),
                    "document_id": str(document.id),
                    "origin": str(document.origin),
                },
            )
            outcome.not_governed.append(document)
            continue
        if wanted.content is not None:
            generated.append((document, wanted.content))
    await session.flush()
    for document, data in generated:
        if await _store_generated(session, document, data, now):
            outcome.generated_versions.append(document)
    await session.flush()
    revision = await record_revision(session, tender, trigger=PORTAL_SYNC_TRIGGER)
    outcome.revision_changed = not revision.unchanged

    # --- Decisions internes (cessions, alies): revisio `document_intake` -----------------
    for document, wanted in takeovers:
        stored = candidates[document.id]
        if (stored.md5 is None or stored.md5 == wanted.md5) and await _take_over(
            session, document, source, wanted, stored, now
        ):
            _apply_metadata(document, wanted)
            outcome.taken_over.append(document)
        else:
            outcome.not_governed.append(document)
    for document in outcome.removed:
        await promote_alias_if_canonical_retired(session, document, record=False)
    regroup = (
        outcome.restored
        + outcome.repointed
        + outcome.adopted_from_feed
        + outcome.taken_over
        + outcome.generated_versions
    )
    for document in {item.id: item for item in regroup}.values():
        await assign_duplicate_alias(session, tender.id, document)
    await session.flush()
    internal = await record_revision(session, tender, trigger=DOCUMENT_INTAKE_TRIGGER)
    outcome.revision_changed = outcome.revision_changed or not internal.unchanged
    return outcome


# --------------------------------------------------------------------------- #
# Sincronitzacio                                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PortalSyncResult:
    state: PortalState
    reconciled: Reconciled
    downloads: list[dict[str, Any]]
    #: Fitxers del portal que no s'han intentat baixar (fallada definitiva o massa recent).
    skipped: list[dict[str, Any]]
    #: Fitxers del portal que, en acabar, encara no tenen bytes.
    missing_content: list[str]
    requests: list[dict[str, Any]]
    any_failed: bool
    all_reused: bool

    @property
    def changed(self) -> bool:
        return self.reconciled.changed or not self.all_reused

    @property
    def partial(self) -> bool:
        return self.any_failed or bool(self.missing_content)

    def as_dict(self) -> dict[str, Any]:
        state = self.state
        return {
            "expedient_id": state.ref.expedient_id,
            "publication_id": state.publication.publication_id,
            "fingerprint": state.fingerprint,
            "changed": self.changed,
            "portal": {
                "documents": len(state.wanted),
                "answers": state.answers,
                "answers_before_licitacio": state.answers_before,
                "notices": state.notices,
                "notices_before_licitacio": state.notices_before,
                "amendments": state.amendments,
                "skipped_empty": list(state.skipped_empty),
                "unknown_groups": list(state.publication.unknown_groups),
                "requests": len(self.requests),
            },
            "reconciliation": self.reconciled.counts(),
            "downloads": self.downloads,
            "skipped": self.skipped,
            "missing_content": self.missing_content,
            # Cami, hora, estat, bytes i SHA-256 de cada consulta JSON (cap secret ni dada
            # de cap empresa): la traça de que s'ha llegit. No arriba mai a l'API dels tenants.
            "request_log": self.requests,
        }


async def load_sync_target(session: AsyncSession, tender_id: uuid.UUID) -> PublicationRef:
    """La publicacio de la PSCP d'una licitacio publica, o `PortalSyncNotAvailableError`."""
    tender = await session.get(Tender, tender_id)
    if tender is None:
        raise PortalSyncNotAvailableError("tender_not_found")
    ref, reason = sync_target(
        tender.owner_company_id,
        tender.source_url,
        (tender.source_metadata or {}).get("pscp_id_intern"),
    )
    if ref is None:
        raise PortalSyncNotAvailableError(reason or "no_pscp_publication")
    return ref


def _portal_files(tender_id: uuid.UUID, source_id: uuid.UUID) -> sa.Select:
    """Els fitxers del portal vigents i canonics d'una licitacio (els generats a part)."""
    return (
        sa.select(TenderDocument)
        .where(
            TenderDocument.tender_id == tender_id,
            TenderDocument.origin == TenderDocumentOrigin.PORTAL,
            TenderDocument.governing_source_id == source_id,
            TenderDocument.removed_at.is_(None),
            TenderDocument.duplicate_of_document_id.is_(None),
            ~TenderDocument.url.startswith("pscp-portal://"),
        )
        .order_by(TenderDocument.name, TenderDocument.id)
    )


async def _download_plan(
    session: AsyncSession, tender_id: uuid.UUID, source_id: uuid.UUID, now: datetime
) -> tuple[list[uuid.UUID], set[uuid.UUID], list[dict[str, Any]]]:
    """Els fitxers del portal a processar, quins toquen la xarxa i quins es deixen estar."""
    documents = (await session.scalars(_portal_files(tender_id, source_id))).all()
    retry_after = timedelta(seconds=settings.PSCP_PORTAL_RETRY_FAILED_SECONDS)
    ids: list[uuid.UUID] = []
    network: set[uuid.UUID] = set()
    skipped: list[dict[str, Any]] = []
    for document in documents:
        current = await current_file_for(session, document.id)
        if current is None or current.content_id is None:
            if permanently_failed(document, current):
                skipped.append(
                    {
                        "document_id": str(document.id),
                        "reason": "permanent",
                        "error_code": current.error_code,
                    }
                )
                continue
            if (
                current is not None
                and current.download_status == DownloadStatus.FAILED
                and current.source_url == document.url
                and current.started_at is not None
                and now - _aware(current.started_at) < retry_after
            ):
                # Una fallada de la descarrega (xarxa, MD5 diferent...) es torna a provar,
                # pero no a cada sincronitzacio: cada intent deixa una fila nova.
                skipped.append(
                    {
                        "document_id": str(document.id),
                        "reason": "recent",
                        "error_code": current.error_code,
                    }
                )
                continue
            network.add(document.id)
        ids.append(document.id)
    return ids, network, skipped


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def _close(
    session_factory: async_sessionmaker[AsyncSession], tender_id: uuid.UUID, source_id: uuid.UUID
) -> list[str]:
    """Tancament en sessions noves: la revisio dels bytes baixats (sense objectes d'abans, que
    podrien desfer un canvi del feed fet mentrestant) i la classificacio global. Torna els
    fitxers del portal que encara no tenen bytes."""
    for attempt in range(2):
        async with session_factory() as session:
            tender = await session.get(Tender, tender_id)
            if tender is None:  # pragma: no cover - FK ON DELETE CASCADE
                return []
            try:
                await record_revision(session, tender, trigger="document_processing")
                await session.commit()
                break
            except IntegrityError:
                # Una altra revisio (p. ex. la ingesta) ha pres el mateix numero: es repeteix.
                await session.rollback()
                if attempt:
                    logger.warning(
                        "pscp_portal.close.revision_conflict", extra={"tender_id": str(tender_id)}
                    )
    async with session_factory() as session:
        await classify_safely(session, tender_id, GlobalClassificationTrigger.DOCUMENT_PROCESSING)
    async with session_factory() as session:
        missing = []
        for document in (await session.scalars(_portal_files(tender_id, source_id))).all():
            current = await current_file_for(session, document.id)
            if current is None or current.content_id is None:
                missing.append(str(document.id))
        return missing


async def sync_tender(
    session_factory: async_sessionmaker[AsyncSession],
    tender_id: uuid.UUID,
    *,
    portal: PortalClient,
    resolver: Resolver | None = None,
    should_continue: Callable[[], Awaitable[bool]] | None = None,
    report_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> PortalSyncResult | None:
    """Sincronitza la licitacio amb el portal (amb `portal` ja obert). `None` si s'ha
    interromput (el worker ha perdut el lease): el que s'ha desat es tanca igualment."""

    async def keep_going() -> bool:
        return await should_continue() if should_continue is not None else True

    async def progress(current: int, total: int) -> None:
        if report_progress is not None:
            await report_progress(current, total)

    http_client: httpx.AsyncClient | None = portal.http_client
    async with session_factory() as session:
        ref = await load_sync_target(session, tender_id)

    state = await fetch_state(portal, ref)
    if not await keep_going():
        return None

    async with session_factory() as session:
        source = await portal_source(session)
        source_id = source.id
        # Bloquejat fins al commit: un canvi del feed fet mentre es reconcilia (p. ex. el
        # termini) no queda desfet a la fotografia de les revisions d'aquesta transaccio. Nomes
        # la fila (per l'id): el `Tender` porta relacions amb join extern, i PostgreSQL no
        # bloqueja el costat nul·lable d'un join.
        await session.execute(sa.select(Tender.id).where(Tender.id == tender_id).with_for_update())
        tender = await session.get(Tender, tender_id, populate_existing=True)
        try:
            reconciled = await reconcile(session, tender, source, state, datetime.now(UTC))
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise PortalSyncConflictError(
                "Els documents de la licitacio han canviat mentre es sincronitzaven."
            ) from exc
        except BaseException:
            await session.rollback()
            raise

    downloads: list[dict[str, Any]] = []
    any_failed = False
    all_reused = True
    interrupted = False
    try:
        async with session_factory() as session:
            ids, network, skipped = await _download_plan(
                session, tender_id, source_id, datetime.now(UTC)
            )
            await progress(0, len(ids))
            for position, document_id in enumerate(ids):
                if not await keep_going():
                    interrupted = True
                    break
                document = await session.get(TenderDocument, document_id)
                if document is None:  # pragma: no cover - esborrat entremig
                    continue
                needs_network = document_id in network
                if needs_network:
                    await portal.wait_turn()
                try:
                    outcome = await process_tender_document(
                        session, document, client=http_client, resolver=resolver, record=False
                    )
                except (ProcessingInProgressError, NoContentToExtractError) as exc:
                    await session.rollback()
                    downloads.append(_item(document_id, None, None, exc.code, False))
                    any_failed = True
                    all_reused = False
                except DBAPIError:
                    # Un document que no es pot desar no atura la resta.
                    await session.rollback()
                    logger.exception(
                        "pscp_portal.download.database_error",
                        extra={"document_id": str(document_id)},
                    )
                    downloads.append(_item(document_id, None, None, "database_error", False))
                    any_failed = True
                    all_reused = False
                else:
                    file = outcome.file
                    failed = file.download_status == DownloadStatus.FAILED
                    downloads.append(
                        _item(
                            document_id,
                            file.id,
                            str(file.download_status),
                            file.error_code,
                            outcome.reused,
                        )
                    )
                    any_failed = any_failed or (failed and not outcome.reused)
                    all_reused = all_reused and outcome.reused
                finally:
                    if needs_network:
                        portal.mark()
                await progress(position + 1, len(ids))
    finally:
        # Tant si acaba be com si s'interromp o falla, el que ja es a la base es tanca.
        missing = await _close(session_factory, tender_id, source_id)

    if interrupted:
        return None
    return PortalSyncResult(
        state=state,
        reconciled=reconciled,
        downloads=downloads,
        skipped=skipped,
        missing_content=missing,
        requests=list(portal.log),
        any_failed=any_failed,
        all_reused=all_reused,
    )


def _item(
    document_id: uuid.UUID,
    file_id: uuid.UUID | None,
    status: str | None,
    error_code: str | None,
    reused: bool,
) -> dict[str, Any]:
    return {
        "document_id": str(document_id),
        "file_id": str(file_id) if file_id else None,
        "download_status": status,
        "error_code": error_code,
        "reused": reused,
    }
