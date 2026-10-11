"""Normalitzador: representacio de PLACSP -> model canonic.

Aquesta es la frontera del sistema. A partir d'aqui ningu no ha de saber que
existeix PLACSP: quan s'afegeixi TED nomes caldra un altre parser i un altre
normalitzador que produeixin aquestes mateixes estructures.

Enrichment permes en aquesta fase: nomes determinista (codis, dates, imports,
moneda, NUTS explicit). Cap IA, cap inferencia sobre text lliure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.ingestion.placsp import codes
from app.ingestion.placsp.parser import ParsedDocument, ParsedEntry, ParsedLot
from app.models.enums import ContractType, ProcedureType, TenderDocumentType, TenderStatus
from app.tenders.search import is_feed_summary_template

# Els terminis de PLACSP venen com a data + hora local sense zona horaria.
# Espanya peninsular; es documenta com a assumpcio coneguda.
SPAIN_TZ = ZoneInfo("Europe/Madrid")

DEFAULT_CURRENCY = "EUR"


@dataclass(slots=True)
class NormalizedBuyer:
    external_identifier: str
    name: str
    tax_identifier: str | None = None
    registry_identifier: str | None = None
    buyer_type: str | None = None
    activity: str | None = None
    website: str | None = None
    buyer_profile_url: str | None = None
    address: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    nuts_code: str | None = None
    hierarchy: str | None = None


@dataclass(slots=True)
class NormalizedCPV:
    code: str
    is_main: bool = False


@dataclass(slots=True)
class NormalizedLot:
    external_lot_id: str
    lot_number: int | None = None
    title: str | None = None
    estimated_value: Decimal | None = None
    base_budget: Decimal | None = None
    base_budget_without_tax: Decimal | None = None
    currency: str = DEFAULT_CURRENCY
    place_of_execution: str | None = None
    nuts_code: str | None = None
    cpvs: list[NormalizedCPV] = field(default_factory=list)


# Etiqueta llegible quan la font no publica el nom del fitxer.
DOCUMENT_LABELS: dict[TenderDocumentType, str] = {
    TenderDocumentType.ADMINISTRATIVE_TERMS: "Plec de clausules administratives",
    TenderDocumentType.TECHNICAL_TERMS: "Plec de prescripcions tecniques",
    TenderDocumentType.NOTICE: "Anunci de licitacio",
    TenderDocumentType.AWARD: "Document d'adjudicacio",
    TenderDocumentType.ADDITIONAL: "Documentacio addicional",
    TenderDocumentType.OTHER: "Document",
}


@dataclass(slots=True)
class NormalizedDocument:
    name: str
    url: str
    document_type: TenderDocumentType
    source_document_type_code: str | None = None
    published_at: date | None = None


@dataclass(slots=True)
class NormalizedTender:
    external_id: str
    title: str
    expediente_number: str | None = None
    description: str | None = None
    procedure_type: ProcedureType = ProcedureType.UNKNOWN
    contract_type: ContractType = ContractType.UNKNOWN
    status: TenderStatus = TenderStatus.UNKNOWN
    source_status_code: str | None = None
    estimated_value: Decimal | None = None
    base_budget: Decimal | None = None
    base_budget_without_tax: Decimal | None = None
    currency: str = DEFAULT_CURRENCY
    publication_date: date | None = None
    deadline_at: datetime | None = None
    place_of_execution: str | None = None
    nuts_code: str | None = None
    country_code: str | None = None
    source_url: str | None = None
    buyer: NormalizedBuyer | None = None
    cpvs: list[NormalizedCPV] = field(default_factory=list)
    lots: list[NormalizedLot] = field(default_factory=list)
    documents: list[NormalizedDocument] = field(default_factory=list)
    is_deleted: bool = False
    # `atom:updated` de la publicacio, sempre amb zona horaria (None si la font no la dona).
    source_updated_at: datetime | None = None
    # Provenance especifica de la font; claus tancades (vegeu `SOURCE_METADATA_KEYS`).
    source_metadata: dict[str, str] = field(default_factory=dict)


def _deadline(entry: ParsedEntry) -> datetime | None:
    """Combina data i hora del termini. Sense hora, s'assumeix final del dia."""
    if entry.deadline_date is None:
        return None
    end_time = time(23, 59, 59)
    if entry.deadline_time:
        try:
            end_time = time.fromisoformat(entry.deadline_time.strip()[:8])
        except ValueError:
            pass
    return datetime.combine(entry.deadline_date, end_time, tzinfo=SPAIN_TZ)


def _cpvs(raw_codes: list[str]) -> list[NormalizedCPV]:
    """El primer CPV que publica la font es el principal."""
    return [NormalizedCPV(code=code, is_main=index == 0) for index, code in enumerate(raw_codes)]


def _lot_number(external_lot_id: str | None) -> int | None:
    if not external_lot_id:
        return None
    try:
        return int(external_lot_id.strip())
    except ValueError:
        return None


def _normalize_lot(lot: ParsedLot, position: int) -> NormalizedLot:
    external_id = (lot.external_lot_id or str(position + 1)).strip()
    return NormalizedLot(
        external_lot_id=external_id[:100],
        lot_number=_lot_number(external_id) or position + 1,
        title=(lot.title or None),
        estimated_value=lot.amounts.estimated_overall,
        base_budget=lot.amounts.total,
        base_budget_without_tax=lot.amounts.tax_exclusive,
        currency=lot.amounts.currency or DEFAULT_CURRENCY,
        place_of_execution=lot.location.country_subentity or lot.location.city,
        nuts_code=lot.location.nuts_code,
        cpvs=_cpvs(lot.cpv_codes),
    )


def _normalize_buyer(entry: ParsedEntry) -> NormalizedBuyer | None:
    party = entry.buyer
    # Identitat: ID_PLATAFORMA identifica l'organ de contractacio. El NIF no
    # serveix, perque un mateix ajuntament te diversos organs amb el mateix NIF.
    # ID_OC_PLAT (feed agregat 1044) nomes s'usa quan no n'hi ha cap dels altres.
    identifier = party.platform_id or party.registry_id or party.tax_id or party.oc_plat_id
    if not identifier or not party.name:
        return None
    return NormalizedBuyer(
        external_identifier=identifier[:200],
        name=party.name[:500],
        tax_identifier=party.tax_id,
        registry_identifier=party.registry_id,
        buyer_type=party.party_type_code,
        activity=party.activity_code,
        website=party.website,
        buyer_profile_url=party.profile_url,
        address=party.address_line,
        city=party.city,
        postal_code=party.postal_zone,
        country_code=party.country_code,
        hierarchy=" > ".join(reversed(party.hierarchy))[:1000] if party.hierarchy else None,
    )


def _normalize_document(document: ParsedDocument) -> NormalizedDocument:
    document_type = codes.map_document_type(document.source_element, document.type_code)
    return NormalizedDocument(
        name=document.name or DOCUMENT_LABELS.get(document_type, "Document"),
        url=document.url,
        document_type=document_type,
        source_document_type_code=document.type_code,
        published_at=document.issue_date,
    )


SOURCE_METADATA_KEYS = (
    "ted_uuid",
    "contract_subtype_code",
    "contract_type_code",
    "procedure_code",
    "placsp_organ_id",
    "pscp_id_intern",
    #: El `summary` cru del feed quan es la plantilla fixa (3B-2 §7.1.2): no es la
    #: descripcio de l'objecte i no va a `description`, pero no es perd.
    "feed_summary",
)


def _description(entry: ParsedEntry) -> str | None:
    """El `summary` com a descripcio, nomes si no es la plantilla del feed agregat.

    La plantilla («Id licitación…; Órgano de Contratación…») son metadades de la
    font, no l'objecte del contracte: amb ella, `description` queda nul i la fitxa
    diu «Objecte no publicat al feed» (mai el titol com a objecte inventat).
    """
    if is_feed_summary_template(entry.summary):
        return None
    return entry.summary


def _source_metadata(entry: ParsedEntry) -> dict[str, str]:
    """Provenance de la font amb claus tancades; les absents no hi son."""
    candidates = {
        "ted_uuid": entry.ted_uuid,
        "contract_subtype_code": entry.contract_subtype_code,
        "contract_type_code": entry.contract_type_code,
        "procedure_code": entry.procedure_code,
        "feed_summary": entry.summary if is_feed_summary_template(entry.summary) else None,
        "placsp_organ_id": entry.buyer.oc_plat_id,
        "pscp_id_intern": entry.pscp_id_intern,
    }
    return {key: value for key, value in candidates.items() if value}


def _source_updated_at(entry: ParsedEntry) -> datetime | None:
    value = entry.updated_at
    if value is not None and value.tzinfo is None:
        # Les dates ATOM porten offset; si el parseig de reserva n'ha perdut, hora d'Espanya.
        value = value.replace(tzinfo=SPAIN_TZ)
    return value


def normalize(entry: ParsedEntry) -> NormalizedTender:
    """ParsedEntry -> NormalizedTender. No toca la base de dades."""
    title = entry.project_name or entry.title or entry.contract_folder_id or entry.external_id

    return NormalizedTender(
        external_id=entry.external_id,
        # Sense retall: `ProcurementProject/Name` es text lliure sense limit a la
        # font (n'hi ha de 1.999 caracters) i `tenders.title` es Text (0057).
        title=title,
        expediente_number=(entry.contract_folder_id or None),
        description=_description(entry),
        procedure_type=codes.map_procedure_type(entry.procedure_code),
        contract_type=codes.map_contract_type(entry.contract_type_code),
        status=codes.map_status(entry.status_code),
        source_status_code=entry.status_code,
        estimated_value=entry.amounts.estimated_overall,
        base_budget=entry.amounts.total,
        base_budget_without_tax=entry.amounts.tax_exclusive,
        currency=entry.amounts.currency or DEFAULT_CURRENCY,
        publication_date=entry.publication_date,
        deadline_at=_deadline(entry),
        place_of_execution=entry.location.country_subentity or entry.location.city,
        nuts_code=entry.location.nuts_code,
        country_code=entry.location.country_code,
        source_url=entry.detail_url,
        buyer=_normalize_buyer(entry),
        cpvs=_cpvs(entry.cpv_codes),
        lots=[_normalize_lot(lot, index) for index, lot in enumerate(entry.lots)],
        documents=[_normalize_document(document) for document in entry.documents],
        is_deleted=entry.deleted,
        source_updated_at=_source_updated_at(entry),
        source_metadata=_source_metadata(entry),
    )
