"""Lectura pura de les respostes del portal de la PSCP. Sense xarxa ni base.

Cada funcio rep el JSON ja descodificat i en torna dades propies. Si hi falta el que cal o
te un format inesperat, `PortalParseError`: una lectura incompleta no ha de retirar mai cap
document, i per aixo el servei s'atura sencer abans de tocar res.

Tambe hi viuen les URL: la de la fitxa publica (`Tender.source_url`, d'on surten l'expedient
i la publicacio), la de descarrega d'un fitxer (`descarrega-document/<id>/<MD5>`, que porta
l'MD5 que se n'espera) i les sintetiques dels documents generats (`pscp-portal://`).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.ingestion.pscp_portal import GENERATED_SCHEME, PORTAL_API
from app.tenders.deadline import DEADLINE_TIME_ZONE

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MD5_RE = re.compile(r"^[0-9A-F]{32}$")
#: L'MD5 d'un fitxer buit: el portal en publica alguns i no hi ha res a baixar.
EMPTY_MD5 = "D41D8CD98F00B204E9800998ECF8427E"

_SOURCE_URL_RE = re.compile(
    r"^https://(?:www\.)?contractaciopublica\.cat/(?:[a-z]{2}/)?detall-publicacio/"
    r"(?P<expedient>[0-9a-fA-F-]{36})/(?P<publication>\d{1,18})/?(?:[?#].*)?$"
)
_DOWNLOAD_URL_RE = re.compile(
    r"^https://(?:www\.)?contractaciopublica\.cat/portal-api/descarrega-document/"
    r"(?P<id>\d{1,18})/(?P<md5>[0-9A-Fa-f]{32})$"
)
#: Etiquetes HTML que el portal pot posar en un text. Nomes aquestes activen la neteja: un
#: `<CIF>` o un `2 < 3` d'un text pla es queden com son.
_HTML_TAGS = (
    r"p|br|div|span|ul|ol|li|strong|b|i|em|u|a|table|thead|tbody|tr|td|th|h[1-6]|font|sup|sub"
    r"|blockquote|pre|hr"
)
_HTML_TAG_RE = re.compile(rf"(?i)</?(?:{_HTML_TAGS})\b[^<>]*>")
_BLOCK_TAG_RE = re.compile(
    r"(?i)<br\s*/?>|<hr\b[^<>]*>|</?(?:p|div|tr|h[1-6]|ul|ol|table|blockquote|pre)\b[^<>]*>"
)
_ITEM_TAG_RE = re.compile(r"(?i)<li\b[^<>]*>")
_CELL_TAG_RE = re.compile(r"(?i)</t[dh]\s*>")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

#: Grups de documents de la publicacio, en l'ordre en que el portal els presenta.
PUBLICATION_GROUPS = (
    "plecsDeClausulesAdministratives",
    "plecsDePrescripcionsTecniques",
    "memoriaJustificativaContracte",
    "documentsAprovacio",
    "altresDocuments",
)

#: Clau del comptador de preguntes i respostes al tauler.
QA_COUNTER = "comptadorPreguntesRespostes"


@dataclass(frozen=True, slots=True)
class BoardCategory:
    """Una categoria del tauler d'avisos que es llegeix."""

    key: str
    counter: str
    direct: str
    category_id: int
    label: str


#: Categories del tauler que poden afectar una oferta. Les altres (fase d'execucio,
#: manteniments, empreses admeses al sistema, consultes preliminars) no es llegeixen.
BOARD_CATEGORIES: tuple[BoardCategory, ...] = (
    BoardCategory("avisos", "comptadorAvisos", "urlAvis", 1008176, "Avisos"),
    BoardCategory("esmenes", "comptadorEsmenes", "urlAvisEsmena", 1008195, "Esmenes"),
    BoardCategory(
        "mesa",
        "comptadorAvisosRelacionatsMesa",
        "urlAvisRelacionatMesa",
        1008175,
        "Avisos relacionats amb la mesa",
    ),
    BoardCategory(
        "empreses_licitadores",
        "comptadorAnuncisEmpresesLicitadores",
        "urlAvisPerEmpresesLicitadores",
        1008199,
        "Anuncis per a empreses licitadores",
    ),
    BoardCategory("recursos", "comptadorRecursos", "urlRecurs", 1008174, "Recursos"),
)
CATEGORY_BY_ID = {category.category_id: category for category in BOARD_CATEGORIES}


class PortalParseError(ValueError):
    """La resposta no te la forma esperada: la lectura no es completa."""


# --------------------------------------------------------------------------- #
# URL                                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PublicationRef:
    expedient_id: str
    publication_id: int


def parse_source_url(url: str | None) -> PublicationRef | None:
    """L'expedient i la publicacio d'una fitxa publica de la PSCP (o `None`)."""
    match = _SOURCE_URL_RE.match((url or "").strip())
    if match is None:
        return None
    expedient = match.group("expedient").lower()
    if not UUID_RE.match(expedient):
        return None
    return PublicationRef(expedient, int(match.group("publication")))


def sync_target(
    owner_company_id: object | None, source_url: str | None, pscp_id_intern: object | None
) -> tuple[PublicationRef | None, str | None]:
    """La publicacio a sincronitzar d'una licitacio, o el motiu pel qual no n'hi ha cap.

    Nomes una licitacio publica (sense propietaria) amb la fitxa de la PSCP com a font i el
    mateix expedient que `pscp_id_intern`.
    """
    if owner_company_id is not None:
        return None, "private_tender"
    ref = parse_source_url(source_url)
    if ref is None or not pscp_id_intern:
        return None, "no_pscp_publication"
    if ref.expedient_id != str(pscp_id_intern).strip().lower():
        return None, "pscp_id_mismatch"
    return ref, None


def download_url(document_id: int, md5: str) -> str:
    """La URL de descarrega canonica (la mateixa que publica el feed de PLACSP)."""
    return f"{PORTAL_API}/descarrega-document/{document_id}/{md5.upper()}"


def parse_download_url(url: str | None) -> tuple[int, str] | None:
    """`(id, MD5 en majuscules)` d'una URL de descarrega del portal (o `None`)."""
    match = _DOWNLOAD_URL_RE.match((url or "").strip())
    if match is None:
        return None
    return int(match.group("id")), match.group("md5").upper()


def canonical_download_url(url: str | None) -> str | None:
    """La forma canonica d'una URL de descarrega del portal, per comparar-ne dues."""
    parsed = parse_download_url(url)
    return download_url(*parsed) if parsed else None


def expected_md5(url: str | None) -> str | None:
    """L'MD5 que el portal publica per al fitxer d'aquesta URL (o `None`)."""
    parsed = parse_download_url(url)
    return parsed[1] if parsed else None


def generated_url(expedient_id: str, kind: str, ident: int | None = None) -> str:
    """URL sintetica d'un document generat: `pscp-portal://<expedient>/<tipus>[/<id>]`."""
    tail = f"/{ident}" if ident is not None else ""
    return f"{GENERATED_SCHEME}{expedient_id}/{kind}{tail}"


def is_generated_url(url: str | None) -> bool:
    return bool(url) and url.startswith(GENERATED_SCHEME)


# --------------------------------------------------------------------------- #
# Valors                                                                       #
# --------------------------------------------------------------------------- #


def _dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PortalParseError(f"Falta l'objecte {what}.")
    return value


def _list(value: Any, what: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PortalParseError(f"{what} no es una llista.")
    return value


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise PortalParseError(f"{what} no es un enter.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise PortalParseError(f"{what} no es un enter.")


def _opt_int(value: Any, what: str) -> int | None:
    return None if value is None else _int(value, what)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _instant(value: Any) -> datetime | None:
    """Un instant ISO del portal (`2026-10-13T13:42:05.757Z`); `None` si no se'n pot llegir cap."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _expedient(value: Any) -> str:
    if not isinstance(value, str) or not UUID_RE.match(value.strip().lower()):
        raise PortalParseError("L'identificador d'expedient no es valid.")
    return value.strip().lower()


def clean_text(value: Any) -> str:
    """Text llegible i estable: salts de linia normalitzats, sense etiquetes HTML ni espais
    al final de linia. El que el portal publica com a text pla es queda igual."""
    if not isinstance(value, str):
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if _HTML_TAG_RE.search(text):
        text = _ITEM_TAG_RE.sub("\n- ", text)
        text = _CELL_TAG_RE.sub(" ", text)
        text = _BLOCK_TAG_RE.sub("\n", text)
        text = _HTML_TAG_RE.sub("", text)
        text = html.unescape(text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def _versions(root: dict[str, Any]) -> tuple[int, ...]:
    """Les versions d'una publicacio (original i esmenes), segons la seva navegacio."""
    return tuple(
        _int(_dict(item, "versio").get("publicacioId"), "La versio")
        for item in _list(root.get("navegacioEsmenes"), "La navegacio d'esmenes")
    )


# --------------------------------------------------------------------------- #
# Fitxers                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PortalFile:
    """Un fitxer publicat al portal: es descarrega de `url` i n'ha de sortir l'MD5 `md5`."""

    document_id: int
    title: str
    md5: str
    size: int | None
    language: str | None
    #: D'on surt: el grup de la publicacio, `resposta` o `avis`.
    group: str

    @property
    def url(self) -> str:
        return download_url(self.document_id, self.md5)

    @property
    def is_empty(self) -> bool:
        """Publicat buit: no hi ha res a baixar. Nomes l'MD5 ho prova (una `mida` 0 amb un
        MD5 d'un fitxer amb contingut es una dada incoherent i es baixa igualment)."""
        return self.md5 == EMPTY_MD5


def parse_file(item: Any, group: str) -> PortalFile:
    entry = _dict(item, "document")
    document_id = _int(entry.get("id"), "L'identificador del document")
    md5 = entry.get("hash")
    if not isinstance(md5, str) or not MD5_RE.match(md5.strip().upper()):
        raise PortalParseError("Un document no porta l'MD5 publicat.")
    title = _text(entry.get("titol")) or f"document-{document_id}"
    size = entry.get("mida")
    return PortalFile(
        document_id=document_id,
        title=" ".join(title.split()),
        md5=md5.strip().upper(),
        size=size if isinstance(size, int) and not isinstance(size, bool) else None,
        language=_text(entry.get("idioma")),
        group=group,
    )


def _files(items: Any, group: str) -> tuple[PortalFile, ...]:
    return tuple(parse_file(item, group) for item in _list(items, "La llista de documents"))


# --------------------------------------------------------------------------- #
# Publicacio                                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PhaseRef:
    phase: int
    publication_id: int
    active: bool


@dataclass(frozen=True, slots=True)
class AmendmentRef:
    publication_id: int
    published_at: datetime | None
    #: Nul a la publicacio original; un codi del portal a cada esmena.
    kind: int | None


@dataclass(frozen=True, slots=True)
class Navigation:
    """El que diu qualsevol publicacio sobre l'expedient: les fases i les versions."""

    expedient_id: str
    publication_id: int
    phase: int
    phases: tuple[PhaseRef, ...]
    amendments: tuple[AmendmentRef, ...]

    def phase_publication(self, phase: int) -> int | None:
        """La publicacio vigent d'una fase, segons la navegacio de fases."""
        for item in self.phases:
            if item.phase == phase:
                return item.publication_id
        return None


def parse_navigation(data: Any) -> Navigation:
    """Nomes la navegacio d'una publicacio de qualsevol fase: no n'exigeix els documents."""
    root = _dict(data, "publicacio")
    return Navigation(
        expedient_id=_expedient(root.get("expedientId")),
        publication_id=_int(root.get("publicacioId"), "La publicacio"),
        phase=_int(root.get("fase"), "La fase de la publicacio"),
        phases=tuple(
            PhaseRef(
                phase=_int(_dict(item, "fase").get("fase"), "La fase"),
                publication_id=_int(item.get("publicacioId"), "La publicacio de la fase"),
                active=bool(item.get("faseActiva")),
            )
            for item in _list(root.get("navegacioFases"), "La navegacio de fases")
        ),
        amendments=tuple(
            AmendmentRef(
                publication_id=_int(_dict(item, "esmena").get("publicacioId"), "L'esmena"),
                published_at=_instant(item.get("dataPublicacio")),
                kind=_opt_int(item.get("tipusEsmena"), "El tipus d'esmena"),
            )
            for item in _list(root.get("navegacioEsmenes"), "La navegacio d'esmenes")
        ),
    )


@dataclass(frozen=True, slots=True)
class Publication:
    expedient_id: str
    publication_id: int
    phase: int
    code: str | None
    title: str | None
    published_at: datetime | None
    amendment_reason: str | None
    deadline: datetime | None
    phases: tuple[PhaseRef, ...]
    amendments: tuple[AmendmentRef, ...]
    files: tuple[PortalFile, ...]
    #: Grups amb documents fora de `PUBLICATION_GROUPS` (es llegeixen igual, com a annexos).
    unknown_groups: tuple[str, ...] = ()

    def phase_publication(self, phase: int) -> int | None:
        """La publicacio vigent d'una fase, segons la navegacio de fases."""
        for item in self.phases:
            if item.phase == phase:
                return item.publication_id
        return None


def _collect_docs(node: Any, key: str | None, out: list[tuple[str, Any]]) -> None:
    """Totes les llistes `docs` d'un arbre, amb la clau del grup que les conte."""
    if isinstance(node, dict):
        for child_key, value in node.items():
            if child_key == "docs":
                out.append((key or "docs", _list(value, "La llista de documents")))
            else:
                _collect_docs(value, child_key, out)
    elif isinstance(node, list):
        for item in node:
            _collect_docs(item, key, out)


def parse_publication(data: Any) -> Publication:
    """La resposta de `detall-publicacio-expedient/<expedient>/<publicacio>`."""
    navigation = parse_navigation(data)
    root = _dict(data, "publicacio")
    dades = _dict(root.get("dades"), "dades")
    publicacio = _dict(dades.get("publicacio"), "dades.publicacio")
    dades_publicacio = _dict(publicacio.get("dadesPublicacio"), "dadesPublicacio")

    found: list[tuple[str, Any]] = []
    try:
        _collect_docs(publicacio, None, found)
    except RecursionError:
        raise PortalParseError("La publicacio te una forma massa niada.") from None
    order = {group: index for index, group in enumerate(PUBLICATION_GROUPS)}
    found.sort(key=lambda pair: order.get(pair[0], len(order)))
    files: list[PortalFile] = []
    seen: set[int] = set()
    for group, items in found:
        for file in _files(items, group):
            # El mateix fitxer en dos llocs de la publicacio es un sol document.
            if file.document_id not in seen:
                seen.add(file.document_id)
                files.append(file)
    unknown = tuple(sorted({group for group, _ in found if group not in order}))

    return Publication(
        expedient_id=navigation.expedient_id,
        publication_id=navigation.publication_id,
        phase=navigation.phase,
        code=_text(root.get("codiExpedient")) or _text(dades.get("codiExpedient")),
        title=_text(root.get("titol")),
        published_at=_instant(dades.get("dataPublicacioReal")),
        amendment_reason=_text(root.get("motiuEsmena")),
        deadline=_instant(dades_publicacio.get("dataTerminiPresentacioOSolicitud")),
        phases=navigation.phases,
        amendments=navigation.amendments,
        files=tuple(files),
        unknown_groups=unknown,
    )


# --------------------------------------------------------------------------- #
# Tauler                                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BoardEntry:
    category: BoardCategory
    count: int
    #: Quan n'hi ha exactament un, el tauler ja diu quin es.
    direct_id: int | None


@dataclass(frozen=True, slots=True)
class Board:
    qa_count: int
    entries: tuple[BoardEntry, ...]


def _counter(root: dict[str, Any], key: str) -> int:
    if key not in root:
        raise PortalParseError(f"Al tauler hi falta `{key}`.")
    value = root.get(key)
    return 0 if value is None else _int(value, f"El comptador `{key}`")


def parse_board(data: Any) -> Board:
    """La resposta de `detall-publicacio-expedient/tauler-avisos/<expedient>`."""
    root = _dict(data, "tauler")
    entries = []
    for category in BOARD_CATEGORIES:
        count = _counter(root, category.counter)
        direct = root.get(category.direct)
        direct_id = None
        if count == 1 and isinstance(direct, dict) and direct.get("idPublicacio") is not None:
            direct_id = _int(direct.get("idPublicacio"), "L'avis del tauler")
        entries.append(BoardEntry(category, count, direct_id))
    return Board(qa_count=_counter(root, QA_COUNTER), entries=tuple(entries))


@dataclass(frozen=True, slots=True)
class ListedItem:
    id: int
    published_at: datetime | None
    is_amendment: bool


@dataclass(frozen=True, slots=True)
class Page:
    items: tuple[ListedItem, ...]
    total_elements: int
    total_pages: int
    number: int
    last: bool


def parse_page(data: Any) -> Page:
    """Una pagina d'una llista paginada (`respostes/...` o `avisos-tauler/...`)."""
    root = _dict(data, "pagina")
    items = tuple(
        ListedItem(
            id=_int(_dict(item, "element").get("id"), "L'element de la llista"),
            published_at=_instant(item.get("dataPublicacio")),
            is_amendment=bool(item.get("esEsmena")),
        )
        for item in _list(root.get("content"), "El contingut de la pagina")
    )
    return Page(
        items=items,
        total_elements=_int(root.get("totalElements"), "El total d'elements"),
        total_pages=_int(root.get("totalPages"), "El total de pagines"),
        number=_int(root.get("number"), "El numero de pagina"),
        last=bool(root.get("last")),
    )


# --------------------------------------------------------------------------- #
# Preguntes i respostes, avisos                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Answer:
    """Una pregunta i la resposta oficial de l'organ."""

    id: int
    question: str
    answer: str
    published_at: datetime | None
    is_amendment: bool
    unpublished: bool
    files: tuple[PortalFile, ...]
    #: Text de l'esmena, quan l'organ ha corregit la resposta.
    amendment_description: str = ""
    links: tuple[str, ...] = ()
    #: Versions de la resposta (original i esmenes), de la navegacio del detall.
    versions: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Notice:
    """Un avis del tauler (avisos, esmenes, mesa, empreses licitadores o recursos)."""

    id: int
    category_id: int
    title: str
    description: str
    published_at: datetime | None
    unpublished: bool
    amendment_description: str
    #: Dades d'un recurs (o d'altres camps de text que l'avis porti), en ordre estable.
    details: tuple[tuple[str, str], ...]
    links: tuple[str, ...]
    files: tuple[PortalFile, ...]
    versions: tuple[int, ...] = ()


def _detail_root(data: Any, expedient_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _dict(data, "detall")
    if _expedient(root.get("expedientId")) != expedient_id:
        raise PortalParseError("El detall es d'un altre expedient.")
    return root, _dict(root.get("dades"), "dades del detall")


def _link(item: Any) -> str | None:
    if isinstance(item, str):
        return clean_text(item) or None
    if isinstance(item, dict):
        # Les claus per ordre: el mateix enllac dona sempre el mateix text.
        parts = [clean_text(item[key]) for key in sorted(item) if isinstance(item[key], str)]
        joined = " - ".join(part for part in parts if part)
        return joined or None
    return None


def _links(dades: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        link
        for link in (_link(item) for item in _list(dades.get("linkInteres"), "Els enllacos"))
        if link
    )


def parse_answer(data: Any, answer_id: int, expedient_id: str) -> Answer:
    """La resposta de `detall-avis/resposta/<id>`."""
    root, dades = _detail_root(data, expedient_id)
    return Answer(
        id=answer_id,
        question=clean_text(dades.get("titol")),
        answer=clean_text(dades.get("descripcio")),
        published_at=_instant(root.get("dataPublicacio")),
        is_amendment=bool(dades.get("tipusEsmena")) or bool(_text(dades.get("descripcioEsmena"))),
        unpublished=bool(root.get("despublicat")),
        files=_files(dades.get("documents"), "resposta"),
        amendment_description=clean_text(dades.get("descripcioEsmena")),
        links=_links(dades),
        versions=_versions(root),
    )


#: Camps d'un recurs que val la pena dir, amb la seva etiqueta (ordre estable).
_NOTICE_DETAILS = (
    ("objecteRecurs", "Objecte del recurs"),
    ("dataInterposicio", "Data d'interposició"),
    ("dataResolucio", "Data de resolució"),
    ("sentitResolucio", "Sentit de la resolució"),
    ("suspenProcediment", "Suspèn el procediment"),
    ("sancio", "Sanció"),
    ("altres", "Altres"),
)


def _detail_value(value: Any) -> str | None:
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        moment = _instant(value)
        if moment is not None:
            # Data oficial: el portal desa els instants en UTC (el dia pot ser un altre).
            return moment.astimezone(DEADLINE_TIME_ZONE).date().isoformat()
        return clean_text(value) or None
    return None


def parse_notice(data: Any, notice_id: int, expedient_id: str, category_id: int) -> Notice:
    """La resposta de `detall-avis/avis-recurs/<id>`. La categoria real es `dades.tipusAvis`."""
    root, dades = _detail_root(data, expedient_id)
    details = []
    for key, label in _NOTICE_DETAILS:
        value = _detail_value(dades.get(key))
        if value:
            details.append((label, value))
    kind = dades.get("tipusAvis")
    return Notice(
        id=notice_id,
        category_id=_int(kind, "La categoria de l'avis") if kind is not None else category_id,
        title=" ".join(clean_text(dades.get("titol")).split()),
        description=clean_text(dades.get("descripcio")),
        published_at=_instant(root.get("dataPublicacio")),
        unpublished=bool(root.get("despublicat")),
        amendment_description=clean_text(dades.get("descripcioEsmena")),
        details=tuple(details),
        links=_links(dades),
        files=_files(dades.get("documents"), "avis"),
        versions=_versions(root),
    )
