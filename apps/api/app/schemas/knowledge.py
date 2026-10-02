"""Contracte extern del coneixement de l'empresa (fase 2 del motor d'analisi)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.knowledge import proposals as P
from app.knowledge import vocabulary
from app.knowledge.snapshot import Entry, KnowledgeSnapshot
from app.models.enums import (
    CapabilityStatus,
    EquipmentAccessMode,
    EquipmentType,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    ObservationStatus,
    ObservationSubject,
    OperatingFactKey,
    PartnerRole,
)
from app.schemas.common import APIModel, ORMModel
from app.schemas.coverage import CoverageIn, CoverageRead


class ClaimRead(BaseModel):
    record_id: str
    source: str
    value: Any
    as_of: date
    valid_until: date | None
    accreditation_pending: bool
    #: Te algun document enllacat (encara que no estigui verificada).
    has_evidence: bool = False
    #: Nomes per a certificacions: linies cobertes i `structured` o
    #: `scope_unknown` (abast sense estructurar: no es dedueix del text).
    scope_service_lines: list[str] | None = None
    scope_status: str | None = None
    #: Cobertura territorial (fets, serveis), en la forma canonica. Absent (`None`)
    #: quan la dada no en diu res, com abans de la 0047.
    coverage: CoverageRead | None = None


class HintRead(BaseModel):
    observation_id: str
    statement: str
    observed_on: date | None
    source_ref: str | None


class EntryRead(BaseModel):
    key: str
    #: `current_verified` · `current_declared` · `historic` · `unknown`.
    source: str
    accreditation_pending: bool
    claims: list[ClaimRead]
    #: El que diu l'historic. Nomes informa.
    hints: list[HintRead]


class PendingItemRead(BaseModel):
    kind: str
    subject: str
    key: str
    detail: str
    record_id: str | None
    observation_id: str | None
    superseded_by_current: bool


class KnowledgeSnapshotRead(BaseModel):
    company_id: str
    evaluated_on: date
    fingerprint: str
    certifications: list[EntryRead]
    facts: list[EntryRead]
    services: list[EntryRead]
    solutions: list[EntryRead]
    capabilities: list[EntryRead]
    products: list[EntryRead]
    partners: list[EntryRead]
    #: Qualificacio i mitjans (0052): per any, per projecte, per tipus de polissa, per
    #: tipus de registre, per perfil professional i per tipus d'equip.
    financial_years: list[EntryRead] = []
    execution_references: list[EntryRead] = []
    insurances: list[EntryRead] = []
    registrations: list[EntryRead] = []
    profiles: list[EntryRead] = []
    equipment: list[EntryRead] = []
    #: Que falta validar: dades declarades sense evidencia, en tramit, caducades i
    #: suggeriments de l'historic que ningu no ha revisat.
    pending_validation: list[PendingItemRead]


def _entry(entry: Entry) -> EntryRead:
    return EntryRead(
        key=entry.key,
        source=str(entry.source),
        accreditation_pending=entry.accreditation_pending,
        claims=[
            ClaimRead(
                record_id=c.record_id,
                source=str(c.source),
                value=c.value,
                as_of=c.as_of,
                valid_until=c.valid_until,
                accreditation_pending=c.accreditation_pending,
                has_evidence=c.has_evidence,
                scope_service_lines=list(c.scope) if c.scope is not None else None,
                scope_status=c.scope_status,
                coverage=c.coverage,
            )
            for c in entry.claims
        ],
        hints=[
            HintRead(
                observation_id=h.observation_id,
                statement=h.statement,
                observed_on=h.observed_on,
                source_ref=h.source_ref,
            )
            for h in entry.hints
        ],
    )


def snapshot_read(snapshot: KnowledgeSnapshot) -> KnowledgeSnapshotRead:
    return KnowledgeSnapshotRead(
        company_id=snapshot.company_id,
        evaluated_on=snapshot.evaluated_on,
        fingerprint=snapshot.fingerprint,
        certifications=[_entry(e) for e in snapshot.certifications.values()],
        facts=[_entry(e) for e in snapshot.facts.values()],
        services=[_entry(e) for e in snapshot.services.values()],
        solutions=[_entry(e) for e in snapshot.solutions.values()],
        capabilities=[_entry(e) for e in snapshot.capabilities.values()],
        products=[_entry(e) for e in snapshot.products.values()],
        partners=[_entry(e) for e in snapshot.partners.values()],
        financial_years=[_entry(e) for e in snapshot.financial_years.values()],
        execution_references=[_entry(e) for e in snapshot.execution_references.values()],
        insurances=[_entry(e) for e in snapshot.insurances.values()],
        registrations=[_entry(e) for e in snapshot.registrations.values()],
        profiles=[_entry(e) for e in snapshot.profiles.values()],
        equipment=[_entry(e) for e in snapshot.equipment.values()],
        pending_validation=[
            PendingItemRead(
                kind=str(p.kind),
                subject=p.subject,
                key=p.key,
                detail=p.detail,
                record_id=p.record_id,
                observation_id=p.observation_id,
                superseded_by_current=p.superseded_by_current,
            )
            for p in snapshot.pending_validation
        ],
    )


class ObservationRead(ORMModel):
    id: uuid.UUID
    historic_bid_id: uuid.UUID | None
    subject: ObservationSubject
    subject_key: str
    statement: str
    proposal: dict[str, Any]
    observed_on: date | None
    source_ref: str | None
    status: ObservationStatus
    reviewed_at: datetime | None
    review_note: str | None
    promoted_entity_type: str | None
    promoted_entity_id: uuid.UUID | None


class PromoteObservation(APIModel):
    note: str | None = Field(default=None, max_length=2000)


class DismissObservation(APIModel):
    note: str = Field(min_length=3, max_length=2000)


class VerifiedRecord(BaseModel):
    kind: str
    record_id: uuid.UUID
    verification_status: str


# --------------------------------------------------------------------------- #
# Vocabulari global (nomes lectura: cap API no hi escriu)                      #
# --------------------------------------------------------------------------- #


class ServiceLineRead(BaseModel):
    code: str
    label: str


class CapabilityDefinitionRead(BaseModel):
    code: str
    service_line_code: str
    label: str
    value_type: str
    comparator: str
    unit: str | None
    ordinal_scale: list[str] | None


class CertificationStandardRead(BaseModel):
    code: str
    label: str
    levels: list[str] | None


class CodeLabelRead(BaseModel):
    code: str
    label: str


class VocabularyRead(BaseModel):
    service_lines: list[ServiceLineRead]
    capabilities: list[CapabilityDefinitionRead]
    standards: list[CertificationStandardRead]
    #: Qualificacio i mitjans (0052).
    professional_profiles: list[CodeLabelRead] = []
    equipment_types: list[CodeLabelRead] = []


def vocabulary_read() -> VocabularyRead:
    return VocabularyRead(
        service_lines=[
            ServiceLineRead(code=s.code, label=s.label) for s in vocabulary.SERVICE_LINES
        ],
        capabilities=[
            CapabilityDefinitionRead(
                code=c.code,
                service_line_code=c.service_line_code,
                label=c.label,
                value_type=str(c.value_type),
                comparator=str(c.comparator),
                unit=c.unit,
                ordinal_scale=list(c.ordinal_scale) if c.ordinal_scale else None,
            )
            for c in vocabulary.CAPABILITIES
        ],
        standards=[
            CertificationStandardRead(
                code=s.code, label=s.label, levels=list(s.levels) if s.levels else None
            )
            for s in vocabulary.CERTIFICATION_STANDARDS
        ],
        professional_profiles=[
            CodeLabelRead(code=p.code, label=p.label) for p in vocabulary.PROFESSIONAL_PROFILES
        ],
        equipment_types=[
            CodeLabelRead(code=code, label=label)
            for code, label in vocabulary.EQUIPMENT_TYPE_LABELS.items()
        ],
    )


# --------------------------------------------------------------------------- #
# Declaracio directa de coneixement actual                                     #
# --------------------------------------------------------------------------- #
#
# El que es declara a ma neix `declared` i `pending_verification`: cap payload no
# porta `knowledge_origin` ni `verification_status`. Una alta es la mateixa
# proposta que portaria una observacio de l'historic, mes la procedencia.


class _ProvenanceIn(APIModel):
    as_of: date | None = None
    valid_until: date | None = None
    lifecycle: KnowledgeLifecycle | None = None

    @model_validator(mode="after")
    def _validity_after_as_of(self):
        if self.as_of and self.valid_until and self.valid_until < self.as_of:
            raise ValueError("`valid_until` no pot ser anterior a `as_of`.")
        return self


class _WithServiceLineIn(_ProvenanceIn):
    @field_validator("service_line_code", check_fields=False)
    @classmethod
    def _known_service_line(cls, value: str | None) -> str | None:
        if value is not None and value not in vocabulary.SERVICE_LINE_CODES:
            raise ValueError(f"Linia de servei desconeguda: {value}.")
        return value


class OperatingFactDeclare(P.OperatingFactProposal, _ProvenanceIn):
    #: Opcional: sense `coverage`, el fet neix sense dir on es cert (`unknown`).
    coverage: CoverageIn | None = None


class OperatingFactUpdate(_ProvenanceIn):
    value: bool | None = None
    value_number: Decimal | None = Field(default=None, ge=0)
    detail: str | None = Field(default=None, max_length=500)
    #: Enviar-la substitueix tota la cobertura; no enviar-la la deixa com esta.
    coverage: CoverageIn | None = None


class SolutionDeclare(P.SolutionProposal, _ProvenanceIn):
    pass


class SolutionUpdate(_WithServiceLineIn):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    service_line_code: str | None = None
    vendor: str | None = Field(default=None, max_length=160)
    edition: str | None = Field(default=None, max_length=80)


class _CapabilityValueIn(_ProvenanceIn):
    value_number: Decimal | None = None
    value_level: str | None = Field(default=None, max_length=20)
    limits_note: str | None = None


class SolutionCapabilityDeclare(_CapabilityValueIn):
    solution_id: uuid.UUID
    capability_code: str = Field(max_length=80)
    status: CapabilityStatus

    @model_validator(mode="after")
    def _value_of_capability(self):
        vocabulary.validate_capability_value(
            self.capability_code, self.value_number, self.value_level
        )
        return self


class SolutionCapabilityUpdate(_CapabilityValueIn):
    status: CapabilityStatus | None = None


class ProductDeclare(P.ProductProposal, _ProvenanceIn):
    pass


class ProductUpdate(_WithServiceLineIn):
    manufacturer: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    service_line_code: str | None = None


class SolutionProductDeclare(_ProvenanceIn):
    solution_id: uuid.UUID
    product_id: uuid.UUID


class SolutionProductUpdate(_ProvenanceIn):
    pass


class PartnerDeclare(P.PartnerProposal, _ProvenanceIn):
    tax_id: str | None = Field(default=None, max_length=32)


class PartnerUpdate(_ProvenanceIn):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tax_id: str | None = Field(default=None, max_length=32)


class PartnerRoleDeclare(P.PartnerRoleProposal, _ProvenanceIn):
    partner_id: uuid.UUID
    coverage: CoverageIn | None = None


class PartnerRoleUpdate(_WithServiceLineIn):
    role: PartnerRole | None = None
    service_line_code: str | None = None
    territory: str | None = Field(default=None, max_length=160)
    coverage: CoverageIn | None = None


class _ProvenanceRead(ORMModel):
    """Les files tal com son, per editar-les. **Sense `verification_status`**:
    l'estat efectiu (verificat, declarat, caducat...) el diu la fotografia, que
    ja te en compte l'evidencia."""

    id: uuid.UUID
    knowledge_origin: KnowledgeOrigin
    as_of: date
    valid_until: date | None
    lifecycle: KnowledgeLifecycle


class OperatingFactRead(_ProvenanceRead):
    key: OperatingFactKey
    value: bool
    value_number: Decimal | None = None
    detail: str | None
    coverage: CoverageRead


class SolutionRead(_ProvenanceRead):
    name: str
    service_line_code: str
    vendor: str | None
    edition: str | None


class SolutionCapabilityRead(_ProvenanceRead):
    solution_id: uuid.UUID
    capability_code: str
    status: CapabilityStatus
    value_number: Decimal | None
    value_level: str | None
    limits_note: str | None


class ProductRead(_ProvenanceRead):
    manufacturer: str
    model: str
    service_line_code: str | None


class SolutionProductRead(_ProvenanceRead):
    solution_id: uuid.UUID
    product_id: uuid.UUID


class PartnerRead(_ProvenanceRead):
    name: str
    tax_id: str | None


class PartnerRoleRead(_ProvenanceRead):
    partner_id: uuid.UUID
    role: PartnerRole
    service_line_code: str | None
    territory: str | None
    coverage: CoverageRead


class EquipmentDeclare(_ProvenanceIn):
    """Un equip o instrument adscrivible (0052). No es cap producte que es vengui."""

    equipment_type: EquipmentType
    manufacturer: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    serial_reference: str | None = Field(default=None, max_length=120)
    access_mode: EquipmentAccessMode | None = None
    quantity: int = Field(default=1, ge=1)
    #: `None` = no consta. Una data de calibratge demana `true`.
    calibration_required: bool | None = None
    calibration_valid_until: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _calibration_consistent(self):
        if self.calibration_valid_until is not None and self.calibration_required is not True:
            raise ValueError(
                "Una data de calibratge nomes te sentit amb `calibration_required = true`."
            )
        return self


class EquipmentUpdate(_ProvenanceIn):
    equipment_type: EquipmentType | None = None
    manufacturer: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    serial_reference: str | None = Field(default=None, max_length=120)
    access_mode: EquipmentAccessMode | None = None
    quantity: int | None = Field(default=None, ge=1)
    calibration_required: bool | None = None
    calibration_valid_until: date | None = None
    notes: str | None = None


class EquipmentRead(_ProvenanceRead):
    equipment_type: EquipmentType
    manufacturer: str | None
    model: str | None
    serial_reference: str | None
    access_mode: EquipmentAccessMode | None
    quantity: int
    calibration_required: bool | None
    calibration_valid_until: date | None
    notes: str | None


class KnowledgeRecordsRead(BaseModel):
    operating_facts: list[OperatingFactRead]
    solutions: list[SolutionRead]
    solution_capabilities: list[SolutionCapabilityRead]
    products: list[ProductRead]
    solution_products: list[SolutionProductRead]
    partners: list[PartnerRead]
    partner_roles: list[PartnerRoleRead]
    equipment: list[EquipmentRead] = []


#: Tipus declarable → (alta, modificacio, lectura). Les certificacions i els
#: serveis no hi son: es declaren amb els seus CRUD.
RECORD_SCHEMAS: dict[str, tuple[type[APIModel], type[APIModel], type[_ProvenanceRead]]] = {
    "operating_fact": (OperatingFactDeclare, OperatingFactUpdate, OperatingFactRead),
    "solution": (SolutionDeclare, SolutionUpdate, SolutionRead),
    "solution_capability": (
        SolutionCapabilityDeclare,
        SolutionCapabilityUpdate,
        SolutionCapabilityRead,
    ),
    "product": (ProductDeclare, ProductUpdate, ProductRead),
    "solution_product": (SolutionProductDeclare, SolutionProductUpdate, SolutionProductRead),
    "partner": (PartnerDeclare, PartnerUpdate, PartnerRead),
    "partner_role": (PartnerRoleDeclare, PartnerRoleUpdate, PartnerRoleRead),
    "equipment": (EquipmentDeclare, EquipmentUpdate, EquipmentRead),
}
