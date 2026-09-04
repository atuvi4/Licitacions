"""Parser del feed ATOM+CODICE de PLACSP.

Responsabilitat unica: **XML -> representacio intermedia**. Aqui no hi ha ni
base de dades, ni model canonic, ni traduccio de codis: nomes s'extreu el que
la font diu, tal com ho diu. La interpretacio la fa el normalitzador.

Principis:

* Una entrada malmesa no pot tombar tot un feed: `iter_entries` aplica el
  parseig entrada per entrada i qui crida decideix que fa amb els errors.
* Si un camp no hi es o no es pot llegir de manera fiable, queda a `None`.
  Un null es preferible a una dada inventada.
* Parseig endurit amb defusedxml: sense entitats externes ni DTD, de manera
  que un feed maliciós no pot llegir fitxers locals (XXE) ni fer-nos explotar
  la memòria amb expansió d'entitats.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from defusedxml.ElementTree import fromstring as safe_fromstring

PARSER_VERSION = "placsp-v1"

ATOM_NS = "http://www.w3.org/2005/Atom"
TOMBSTONE_NS = "http://purl.org/atompub/tombstones/1.0"

ATOM = f"{{{ATOM_NS}}}"
TOMBSTONE = f"{{{TOMBSTONE_NS}}}"

# Els elements CODICE es busquen amb comodi de namespace ({*}) a proposit: el
# feed barreja cac, cac-place-ext i cbc, i els prefixos han canviat entre
# versions de CODICE. El nom local si que es estable.


class ParseError(Exception):
    """L'entrada no es prou valida per continuar."""


@dataclass(slots=True)
class ParsedParty:
    platform_id: str | None = None
    tax_id: str | None = None
    registry_id: str | None = None
    name: str | None = None
    website: str | None = None
    profile_url: str | None = None
    party_type_code: str | None = None
    activity_code: str | None = None
    city: str | None = None
    postal_zone: str | None = None
    address_line: str | None = None
    country_code: str | None = None
    hierarchy: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedAmounts:
    estimated_overall: Decimal | None = None
    total: Decimal | None = None
    tax_exclusive: Decimal | None = None
    currency: str | None = None


@dataclass(slots=True)
class ParsedLocation:
    nuts_code: str | None = None
    country_subentity: str | None = None
    city: str | None = None
    country_code: str | None = None


@dataclass(slots=True)
class ParsedLot:
    external_lot_id: str | None = None
    title: str | None = None
    amounts: ParsedAmounts = field(default_factory=ParsedAmounts)
    cpv_codes: list[str] = field(default_factory=list)
    location: ParsedLocation = field(default_factory=ParsedLocation)


@dataclass(slots=True)
class ParsedDocument:
    # La font gairebe mai publica FileName per als plecs (verificat: 0 de 697
    # referencies del feed real en tenien). Quan no hi es, es deixa a None i el
    # normalitzador hi posa una etiqueta segons el tipus de document; NO
    # s'inventa un nom a partir de la URL, que nomes conte un identificador opac.
    name: str | None
    url: str
    source_element: str | None = None
    type_code: str | None = None
    issue_date: date | None = None


@dataclass(slots=True)
class ParsedEntry:
    """Una entrada del feed, encara en vocabulari de PLACSP."""

    external_id: str
    updated_at: datetime | None
    raw_xml: bytes
    title: str | None = None
    summary: str | None = None
    detail_url: str | None = None

    contract_folder_id: str | None = None
    status_code: str | None = None
    ted_uuid: str | None = None

    project_name: str | None = None
    contract_type_code: str | None = None
    contract_subtype_code: str | None = None
    amounts: ParsedAmounts = field(default_factory=ParsedAmounts)
    cpv_codes: list[str] = field(default_factory=list)
    location: ParsedLocation = field(default_factory=ParsedLocation)

    procedure_code: str | None = None
    deadline_date: date | None = None
    deadline_time: str | None = None
    publication_date: date | None = None

    buyer: ParsedParty = field(default_factory=ParsedParty)
    lots: list[ParsedLot] = field(default_factory=list)
    documents: list[ParsedDocument] = field(default_factory=list)

    # `at:deleted-entry`: la font marca l'expedient com anul.lat/tancat/arxivat.
    deleted: bool = False


# --------------------------------------------------------------------------
# Utilitats de lectura tolerant
# --------------------------------------------------------------------------


def _text(element: ET.Element | None, path: str) -> str | None:
    if element is None:
        return None
    value = element.findtext(path)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _datetime(value: str | None) -> datetime | None:
    """Les dates ATOM venen en RFC 3339, sovint amb fraccions de segon."""
    if not value:
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:  # p. ex. "2026-09-02T20:06:47.684+02:00" en Python antics
        return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _currency_of(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    return element.get("currencyID")


def _parse_amounts(container: ET.Element | None) -> ParsedAmounts:
    amounts = ParsedAmounts()
    if container is None:
        return amounts
    budget = container.find("{*}BudgetAmount")
    if budget is None:
        return amounts
    estimated = budget.find("{*}EstimatedOverallContractAmount")
    total = budget.find("{*}TotalAmount")
    tax_exclusive = budget.find("{*}TaxExclusiveAmount")
    amounts.estimated_overall = _decimal(estimated.text if estimated is not None else None)
    amounts.total = _decimal(total.text if total is not None else None)
    amounts.tax_exclusive = _decimal(tax_exclusive.text if tax_exclusive is not None else None)
    amounts.currency = (
        _currency_of(total) or _currency_of(estimated) or _currency_of(tax_exclusive)
    )
    return amounts


def _parse_cpvs(container: ET.Element | None) -> list[str]:
    """CPV de l'element. Es conserva l'ordre: el primer es el principal."""
    if container is None:
        return []
    codes: list[str] = []
    for node in container.findall("{*}RequiredCommodityClassification"):
        code = _text(node, "{*}ItemClassificationCode")
        if code and code not in codes:
            codes.append(code)
    return codes


def _parse_location(container: ET.Element | None) -> ParsedLocation:
    location = ParsedLocation()
    if container is None:
        return location
    realized = container.find("{*}RealizedLocation")
    if realized is None:
        return location
    location.nuts_code = _text(realized, "{*}CountrySubentityCode")
    location.country_subentity = _text(realized, "{*}CountrySubentity")
    location.city = _text(realized, "{*}Address/{*}CityName")
    location.country_code = _text(realized, "{*}Address/{*}Country/{*}IdentificationCode")
    return location


def _parse_party(located: ET.Element | None) -> ParsedParty:
    party = ParsedParty()
    if located is None:
        return party
    party.party_type_code = _text(located, "{*}ContractingPartyTypeCode")
    party.activity_code = _text(located, "{*}ActivityCode")
    party.profile_url = _text(located, "{*}BuyerProfileURIID")

    node = located.find("{*}Party")
    if node is not None:
        party.website = _text(node, "{*}WebsiteURI")
        party.name = _text(node, "{*}PartyName/{*}Name")
        for identification in node.findall("{*}PartyIdentification/{*}ID"):
            scheme = (identification.get("schemeName") or "").upper()
            value = (identification.text or "").strip()
            if not value:
                continue
            if scheme == "ID_PLATAFORMA":
                party.platform_id = value
            elif scheme == "NIF":
                party.tax_id = value
            elif scheme == "DIR3":
                party.registry_id = value
        address = node.find("{*}PostalAddress")
        if address is not None:
            party.city = _text(address, "{*}CityName")
            party.postal_zone = _text(address, "{*}PostalZone")
            party.address_line = _text(address, "{*}AddressLine/{*}Line")
            party.country_code = _text(address, "{*}Country/{*}IdentificationCode")

    # Jerarquia administrativa: cadena de ParentLocatedParty imbricats.
    parent = located.find("{*}ParentLocatedParty")
    guard = 0
    while parent is not None and guard < 12:
        name = _text(parent, "{*}PartyName/{*}Name")
        if name:
            party.hierarchy.append(name)
        parent = parent.find("{*}ParentLocatedParty")
        guard += 1
    return party


def _parse_documents(status: ET.Element) -> list[ParsedDocument]:
    """Metadata de documents publicats. NOMES nom, URL i tipus: cap descarrega."""
    documents: list[ParsedDocument] = []
    seen: set[str] = set()

    def add(name: str | None, url: str | None, element: str, type_code: str | None,
            issue_date: date | None) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        documents.append(
            ParsedDocument(
                name=name[:500] if name else None,
                url=url[:1000],
                source_element=element,
                type_code=type_code,
                issue_date=issue_date,
            )
        )

    for element_name in ("LegalDocumentReference", "TechnicalDocumentReference",
                         "AdditionalDocumentReference"):
        for node in status.findall(f".//{{*}}{element_name}"):
            add(
                _text(node, "{*}Attachment/{*}ExternalReference/{*}FileName"),
                _text(node, "{*}Attachment/{*}ExternalReference/{*}URI"),
                element_name,
                _text(node, "{*}DocumentTypeCode"),
                _date(_text(node, "{*}IssueDate")),
            )

    # Anuncis oficials i actes publicats sota ValidNoticeInfo.
    for notice in status.findall("{*}ValidNoticeInfo"):
        notice_type = _text(notice, "{*}NoticeTypeCode")
        for reference in notice.findall(".//{*}AdditionalPublicationDocumentReference"):
            add(
                _text(reference, "{*}Attachment/{*}ExternalReference/{*}FileName"),
                _text(reference, "{*}Attachment/{*}ExternalReference/{*}URI"),
                "ValidNoticeInfo",
                _text(reference, "{*}DocumentTypeCode") or notice_type,
                _date(_text(reference, "{*}IssueDate")),
            )
    return documents


# Anuncis que corresponen a la convocatoria de la licitacio (no a l'adjudicacio
# ni a la formalitzacio). Codis de TenderingNoticeTypeCode observats a la font:
# DOC_CN=anuncio de licitacion, DOC_CD=anuncio de convocatoria, DOC_PIN=previo.
CALL_NOTICE_CODES = {"DOC_CN", "DOC_CD", "DOC_PIN", "DOC_PIN_RTL"}


def _publication_date(status: ET.Element) -> date | None:
    """Data de publicacio de la convocatoria.

    PLACSP no te un camp unic "data de publicacio" de l'expedient: el que hi ha
    son les dates d'emissio de cada anunci. Es pren la mes antiga d'entre els
    anuncis de CONVOCATORIA; si no n'hi ha cap, la mes antiga de totes. Sense
    aquest matis, un expedient ja adjudicat mostraria com a data de publicacio
    la de l'anunci d'adjudicacio, posterior al propi termini.
    """
    call_dates: list[date] = []
    any_dates: list[date] = []
    for notice in status.findall("{*}ValidNoticeInfo"):
        code = (_text(notice, "{*}NoticeTypeCode") or "").upper()
        for reference in notice.findall(".//{*}AdditionalPublicationDocumentReference"):
            value = _date(_text(reference, "{*}IssueDate"))
            if value is None:
                continue
            any_dates.append(value)
            if code in CALL_NOTICE_CODES:
                call_dates.append(value)
    if call_dates:
        return min(call_dates)
    return min(any_dates) if any_dates else None


# --------------------------------------------------------------------------
# API publica del parser
# --------------------------------------------------------------------------


def parse_entry(element: ET.Element) -> ParsedEntry:
    """Converteix un <entry> d'ATOM en ParsedEntry. Llanca ParseError si no te id."""
    external_id = _text(element, f"{ATOM}id")
    if not external_id:
        raise ParseError("L'entrada no te <id>, no es pot identificar.")

    entry = ParsedEntry(
        external_id=external_id,
        updated_at=_datetime(_text(element, f"{ATOM}updated")),
        raw_xml=ET.tostring(element, encoding="utf-8"),
        title=_text(element, f"{ATOM}title"),
        summary=_text(element, f"{ATOM}summary"),
    )
    link = element.find(f"{ATOM}link")
    if link is not None:
        entry.detail_url = link.get("href")

    status = element.find("{*}ContractFolderStatus")
    if status is None:
        # Entrada sense carrega CODICE: es conserva el raw i es marca com a
        # incompleta, pero no es un error fatal.
        return entry

    entry.contract_folder_id = _text(status, "{*}ContractFolderID")
    entry.status_code = _text(status, "{*}ContractFolderStatusCode")
    entry.ted_uuid = _text(status, "{*}UUID")
    entry.buyer = _parse_party(status.find("{*}LocatedContractingParty"))

    project = status.find("{*}ProcurementProject")
    if project is not None:
        entry.project_name = _text(project, "{*}Name")
        entry.contract_type_code = _text(project, "{*}TypeCode")
        entry.contract_subtype_code = _text(project, "{*}SubTypeCode")
        entry.amounts = _parse_amounts(project)
        entry.cpv_codes = _parse_cpvs(project)
        entry.location = _parse_location(project)

    process = status.find("{*}TenderingProcess")
    if process is not None:
        entry.procedure_code = _text(process, "{*}ProcedureCode")
        deadline = process.find("{*}TenderSubmissionDeadlinePeriod")
        if deadline is not None:
            entry.deadline_date = _date(_text(deadline, "{*}EndDate"))
            entry.deadline_time = _text(deadline, "{*}EndTime")

    entry.publication_date = _publication_date(status)

    for lot_node in status.findall("{*}ProcurementProjectLot"):
        lot_project = lot_node.find("{*}ProcurementProject")
        entry.lots.append(
            ParsedLot(
                external_lot_id=_text(lot_node, "{*}ID"),
                title=_text(lot_project, "{*}Name") if lot_project is not None else None,
                amounts=_parse_amounts(lot_project),
                cpv_codes=_parse_cpvs(lot_project),
                location=_parse_location(lot_project),
            )
        )

    entry.documents = _parse_documents(status)
    return entry


@dataclass(slots=True)
class ParsedFeed:
    feed_id: str | None
    updated_at: datetime | None
    self_url: str | None
    next_url: str | None
    entries: list[ET.Element]
    deleted_entries: list[ET.Element]


def parse_feed(content: bytes) -> ParsedFeed:
    """Llegeix el document ATOM. Nomes estructura: les entrades no es toquen encara."""
    try:
        root = safe_fromstring(content)
    except Exception as exc:  # noqa: BLE001 - qualsevol XML invalid
        raise ParseError(f"El feed no es XML valid: {exc}") from exc

    links = {
        link.get("rel") or "alternate": link.get("href")
        for link in root.findall(f"{ATOM}link")
    }
    return ParsedFeed(
        feed_id=_text(root, f"{ATOM}id"),
        updated_at=_datetime(_text(root, f"{ATOM}updated")),
        self_url=links.get("self"),
        # `next` apunta al fitxer immediatament ANTERIOR en el temps: seguir-lo
        # es com es recorre l'historic cap enrere.
        next_url=links.get("next"),
        entries=root.findall(f"{ATOM}entry"),
        deleted_entries=root.findall(f"{TOMBSTONE}deleted-entry"),
    )


def parse_deleted_entry(element: ET.Element) -> ParsedEntry | None:
    """Una lapida (`at:deleted-entry`): l'expedient ha quedat anul.lat o tancat."""
    external_id = element.get("ref")
    if not external_id:
        return None
    return ParsedEntry(
        external_id=external_id,
        updated_at=_datetime(element.get("when")),
        raw_xml=ET.tostring(element, encoding="utf-8"),
        deleted=True,
    )
