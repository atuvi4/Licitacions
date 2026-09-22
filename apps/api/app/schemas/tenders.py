"""Esquemes del feed de licitacions.

Dades publiques: cap d'aquests objectes porta `company_id` ni depen del tenant.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ContractType,
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    IngestionStatus,
    ProcedureType,
    ProcessingStatus,
    TenderDocumentType,
    TenderStatus,
)
from app.schemas.common import ORMModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Pagina de resultats. Paginacio server-side per limit/offset.

    S'ha triat limit/offset i no cursor perque el feed s'ordena per camps que
    l'usuari pot canviar (data, import, termini) i perque cal poder saltar a
    una pagina concreta. Amb volums de milions caldra revisar-ho.
    """

    items: list[T]
    total: int
    limit: int
    offset: int


class BuyerSummary(ORMModel):
    id: uuid.UUID
    name: str
    tax_identifier: str | None = None
    city: str | None = None
    nuts_code: str | None = None


class BuyerRead(BuyerSummary):
    external_identifier: str
    registry_identifier: str | None = None
    buyer_type: str | None = None
    activity: str | None = None
    website: str | None = None
    buyer_profile_url: str | None = None
    address: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    hierarchy: str | None = None


class TenderCPVRead(ORMModel):
    cpv_code: str
    is_main: bool
    description: str | None = None


class TenderLotRead(ORMModel):
    id: uuid.UUID
    external_lot_id: str
    lot_number: int | None = None
    title: str | None = None
    description: str | None = None
    estimated_value: Decimal | None = None
    base_budget: Decimal | None = None
    base_budget_without_tax: Decimal | None = None
    currency: str
    place_of_execution: str | None = None
    nuts_code: str | None = None
    deadline_at: datetime | None = None
    cpvs: list[TenderCPVRead] = []


class TenderDocumentFileSummary(ORMModel):
    """Estat documental d'un document, prou per pintar una fila de la llista.

    Es planer expressament: la fitxa no ha de fer una crida per document per
    saber si un plec ja s'ha processat.
    """

    file_id: uuid.UUID
    version_number: int
    download_status: DownloadStatus
    file_size: int | None = None
    sha256: str | None = None
    extraction_status: ExtractionStatus | None = None
    quality: ExtractionQuality | None = None
    page_count: int | None = None
    text_char_count: int | None = None
    ocr_candidate: bool = False
    extraction_id: uuid.UUID | None = None
    processed_at: datetime | None = None
    error_code: str | None = None


class TenderDocumentRead(ORMModel):
    id: uuid.UUID
    name: str
    document_type: TenderDocumentType
    source_document_type_code: str | None = None
    url: str
    mime_type: str | None = None
    published_at: date | None = None
    #: Versio actual descarregada, si n'hi ha cap. `None` = mai processat.
    latest_file: TenderDocumentFileSummary | None = None


class TenderListItem(ORMModel):
    """Fila del feed. Prou informacio per decidir si val la pena obrir la fitxa."""

    id: uuid.UUID
    external_id: str
    expediente_number: str | None = None
    title: str
    status: TenderStatus
    procedure_type: ProcedureType
    contract_type: ContractType
    estimated_value: Decimal | None = None
    base_budget: Decimal | None = None
    currency: str
    publication_date: date | None = None
    deadline_at: datetime | None = None
    nuts_code: str | None = None
    place_of_execution: str | None = None
    buyer: BuyerSummary | None = None
    cpvs: list[TenderCPVRead] = []
    lot_count: int = 0
    #: Quantes vegades la FONT ha publicat aquest expedient. No es el nombre de
    #: canvis: la font pot republicar sense tocar cap dada. La historia del que
    #: ha canviat de veritat viu a `tender_change_events`, i son dos conceptes
    #: diferents que no poden dir-se tots dos "versions".
    source_publication_count: int = 1
    source_code: str | None = None
    # True quan l'expedient ha canviat despres de la primera vegada que el vam veure.
    is_modified: bool = False


class TenderRevisionRead(ORMModel):
    """Una versio rebuda de l'expedient. Es el SourceRecord, no una taula a part."""

    id: uuid.UUID
    revision_number: int
    raw_hash: str
    raw_size: int
    fetched_at: datetime
    source_updated_at: datetime | None = None
    source_url: str | None = None
    parser_version: str
    processing_status: ProcessingStatus
    error_message: str | None = None


class TenderTraceability(BaseModel):
    """Zona tecnica de la fitxa: d'on surt aquesta dada."""

    source_code: str
    source_name: str
    external_id: str
    source_url: str | None = None
    latest_source_record_id: uuid.UUID | None = None
    fetched_at: datetime | None = None
    parser_version: str | None = None
    raw_hash: str | None = None


class TenderDetail(ORMModel):
    id: uuid.UUID
    external_id: str
    expediente_number: str | None = None
    title: str
    description: str | None = None
    status: TenderStatus
    source_status_code: str | None = None
    procedure_type: ProcedureType
    contract_type: ContractType
    estimated_value: Decimal | None = None
    base_budget: Decimal | None = None
    base_budget_without_tax: Decimal | None = None
    currency: str
    publication_date: date | None = None
    deadline_at: datetime | None = None
    place_of_execution: str | None = None
    nuts_code: str | None = None
    country_code: str | None = None
    source_url: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime | None = None
    #: Publicacions rebudes de la font. Veure la nota a `TenderListItem`.
    source_publication_count: int
    buyer: BuyerRead | None = None
    cpvs: list[TenderCPVRead] = []
    lots: list[TenderLotRead] = []
    documents: list[TenderDocumentRead] = []
    traceability: TenderTraceability | None = None


class IngestionRunRead(ORMModel):
    """Auditoria d'una execucio d'ingesta."""

    id: uuid.UUID
    source_id: uuid.UUID
    status: IngestionStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    records_fetched: int
    records_new: int
    records_changed: int
    records_unchanged: int
    records_failed: int
    tenders_created: int
    tenders_updated: int
    checkpoint_before: str | None = None
    checkpoint_after: str | None = None
    parameters: str | None = None
    error_summary: str | None = None


class TenderFacets(BaseModel):
    """Valors disponibles per als filtres del feed, calculats sobre les dades reals."""

    statuses: list[str]
    procedure_types: list[str]
    contract_types: list[str]
    nuts_codes: list[str] = Field(default_factory=list)
    total_tenders: int


class TenderChangeEventRead(ORMModel):
    """Un canvi concret publicat per la font, amb el seu impacte."""

    change_code: str
    #: Que identifica l'element afectat: el codi d'un lot, la URL d'un document.
    subject: str | None = None
    old_value: Any = None
    new_value: Any = None
    impacts: list[str]
    #: Cert si invalida alguna cosa. Un canvi de titol no ho fa.
    is_material: bool
    detected_at: datetime


class TenderHistoryRevisionRead(ORMModel):
    """Una fotografia de l'estat de la licitacio.

    No es el mateix que `TenderRevisionRead`, que es una versio *rebuda* de la
    font. Aquesta es una versio de l'estat *normalitzat*: una descarrega de
    document que canvia un SHA en genera una sense que la font hagi publicat res.

    No hi va `normalized_snapshot`: es gran, i el que interessa llegir es que ha
    canviat, no l'estat sencer repetit a cada revisio.
    """

    id: uuid.UUID
    revision_number: int
    trigger: str
    created_at: datetime
    change_summary: dict[str, Any] | None = None
    events: list[TenderChangeEventRead] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Alta manual (S3-S1)                                                           #
# --------------------------------------------------------------------------- #


class ManualBuyerCreate(BaseModel):
    """Un organisme escrit a ma. Privat de l'empresa que l'escriu."""

    #: Estable durant tots els reintents de la MATEIXA alta. Diferent del token
    #: de la licitacio: el mateix organisme pot servir per a mes d'un expedient.
    client_token: uuid.UUID
    name: str = Field(min_length=1, max_length=500)
    tax_identifier: str | None = Field(default=None, max_length=64)
    nuts_code: str | None = Field(default=None, max_length=10)


class ManualTenderCreate(BaseModel):
    """El minim per obrir un expedient. La resta s'edita despres."""

    model_config = ConfigDict(extra="forbid")

    #: El genera el formulari en obrir-se i no canvia entre reintents. D'aqui
    #: surt `external_id`, que no es deriva mai del numero d'expedient.
    client_token: uuid.UUID
    title: str = Field(min_length=1, max_length=1000)
    #: El que dona l'organisme. **No es unic**: dos organismes diferents en
    #: poden fer servir el mateix.
    expediente_number: str | None = Field(default=None, max_length=200)
    #: Un organisme que ja existeix —global o de la mateixa empresa— o un de nou.
    #: Excloents. Cap dels dos tambe val: l'expedient pot comencar sense.
    buyer_id: uuid.UUID | None = None
    buyer_manual: ManualBuyerCreate | None = None


class ManualTenderCreateResult(BaseModel):
    """`created=False` vol dir que el reintent ha trobat la que ja hi havia."""

    created: bool
    tender: TenderDetail


class ManualTenderUpdate(BaseModel):
    """Correccions sobre una licitacio oberta a ma.

    Tots els camps son opcionals i s'apliquen **nomes si arriben**: es fa servir
    `model_fields_set`, que es la manera fiable de distingir «no toquis aixo»
    d'«escriu-hi NULL». Per aixo no cal cap indicador del tipus `buyer_clear`:
    enviar `buyer_id: null` treu l'organisme i ometre'l no el toca.

    La identitat no hi es i no s'hi pot colar: `extra="forbid"` rebutja
    `id`, `source_id`, `external_id` i `owner_company_id` amb un 422 en comptes
    d'ignorar-los en silenci.
    """

    model_config = ConfigDict(extra="forbid")

    #: La revisio que el client creu vigent. Si ja no ho es, 409.
    expected_revision: int = Field(ge=0)

    title: str | None = Field(default=None, min_length=1, max_length=1000)
    description: str | None = None
    expediente_number: str | None = Field(default=None, max_length=200)
    procedure_type: ProcedureType | None = None
    contract_type: ContractType | None = None
    estimated_value: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    base_budget: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    base_budget_without_tax: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    publication_date: date | None = None
    deadline_at: datetime | None = None
    place_of_execution: str | None = Field(default=None, max_length=500)
    nuts_code: str | None = Field(default=None, max_length=10)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)

    #: Excloents, com al POST. `null` explicit treu l'organisme.
    buyer_id: uuid.UUID | None = None
    buyer_manual: ManualBuyerCreate | None = None


class ManualTenderUpdateResult(BaseModel):
    """`revised` vol dir **s'ha creat una `TenderRevision`**, res mes.

    Es fals nomes quan els valors ja eren aquests. Tota escriptura real en crea
    una, sigui material o informativa: si la materialitat decidis l'existencia
    de la revisio, el testimoni de CAS no es mouria i dos clients que han
    llegit la mateixa revisio podrien escriure tots dos.

    Si un canvi obliga a tornar a mirar-se un paquet aprovat es una pregunta
    **separada**, i qui la respon es `evaluate_staleness` amb la taxonomia
    material/informativa que ja existia.
    """

    #: S'ha creat una revisio nova (i per tant `revision` ha pujat).
    revised: bool
    #: La revisio vigent despres de la peticio. El seguent PATCH ha de portar
    #: aquest numero a `expected_revision`.
    revision: int
    tender: TenderDetail
