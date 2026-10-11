"""Esquemes de les propostes d'estructura i de la via de l'operador (0060, 3B-2 §4).

Dues families:

* **Payloads de proposta**: la forma EXACTA de l'entrada del servei intern per a
  cada `entity_kind` (els mateixos esquemes de creacio del tenant, mes la
  referencia al requisit o criteri quan l'entitat en penja). Es validen en crear
  la proposta i es tornen a validar en promoure-la.
* **Escriptures de l'operador**: les mateixes formes amb `source_ref_ids`
  obligatori (almenys una cita). Un valor desconegut no s'escriu amb un `null`:
  «tornar a desconegut» es l'operacio explicita `reset-unknown`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    RequirementTiming,
    StructureAuthority,
    StructureEntityKind,
    StructureOrigin,
    StructureProposalProducer,
    StructureProposalStatus,
    TerritoryCompleteness,
)
from app.schemas.common import ORMModel
from app.schemas.tender_scope import (
    CapabilityNeedCreate,
    CapabilityNeedRead,
    CapabilityNeedUpdate,
    ContractFactCreate,
    ContractFactRead,
    ContractFactUpdate,
    RequirementTerritoryIn,
    RequirementTerritoryRead,
    ScopeItemCreate,
    ScopeItemRead,
    ScopeItemUpdate,
)
from app.schemas.tender_structure import (
    TenderBlockCreate,
    TenderBlockRead,
    TenderBlockUpdate,
    TenderSiteCreate,
    TenderSiteRead,
    TenderSiteUpdate,
)

#: Una escriptura de l'operador porta sempre almenys una cita.
SourceRefIds = Field(min_length=1, max_length=50)

# --------------------------------------------------------------------------- #
# Payloads de proposta (forma del servei intern)                                #
# --------------------------------------------------------------------------- #


class NeedProposalPayload(CapabilityNeedCreate):
    requirement_id: uuid.UUID


class AwardLinkProposalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: uuid.UUID
    award_criterion_id: uuid.UUID


class TerritoryProposalPayload(BaseModel):
    """Un territori mes per al requisit i/o la completesa del conjunt. `unknown`
    (o ometre-la) no es cap afirmacio: no rebaixa una completesa coneguda."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: uuid.UUID
    territory: RequirementTerritoryIn | None = None
    territory_completeness: TerritoryCompleteness | None = None

    @model_validator(mode="after")
    def _says_something(self):
        if self.territory is None and self.territory_completeness is None:
            raise ValueError("Cal un territori o una completesa.")
        return self


class TimingProposalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: uuid.UUID
    timing: RequirementTiming


class CriterionCitationProposalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    award_criterion_id: uuid.UUID


PAYLOAD_SCHEMAS: dict[StructureEntityKind, type[BaseModel]] = {
    StructureEntityKind.BLOCK: TenderBlockCreate,
    StructureEntityKind.SITE: TenderSiteCreate,
    StructureEntityKind.SCOPE_ITEM: ScopeItemCreate,
    StructureEntityKind.CAPABILITY_NEED: NeedProposalPayload,
    StructureEntityKind.AWARD_LINK: AwardLinkProposalPayload,
    StructureEntityKind.CONTRACT_FACT: ContractFactCreate,
    StructureEntityKind.TERRITORY: TerritoryProposalPayload,
    StructureEntityKind.REQUIREMENT_TIMING: TimingProposalPayload,
    StructureEntityKind.CRITERION_CITATION: CriterionCitationProposalPayload,
}


# --------------------------------------------------------------------------- #
# Propostes                                                                     #
# --------------------------------------------------------------------------- #


class StructureProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_kind: StructureEntityKind
    payload: dict[str, Any]
    source_ref_ids: list[uuid.UUID] = SourceRefIds


class StructureProposalReject(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class StructureProposalRead(ORMModel):
    id: uuid.UUID
    tender_id: uuid.UUID
    producer: StructureProposalProducer
    producer_run_id: uuid.UUID | None = None
    entity_kind: StructureEntityKind
    natural_key: str
    payload_json: dict[str, Any]
    source_ref_ids: list[uuid.UUID]
    status: StructureProposalStatus
    created_by: uuid.UUID | None = None
    promoted_by: uuid.UUID | None = None
    promoted_at: datetime | None = None
    rejected_by: uuid.UUID | None = None
    rejected_at: datetime | None = None
    rejected_reason: str | None = None
    created_at: datetime


class StructurePromotionRead(BaseModel):
    proposal: StructureProposalRead
    #: L'entitat que ha quedat escrita (bloc, seu, requisit, criteri...).
    entity_kind: StructureEntityKind
    entity_id: uuid.UUID


# --------------------------------------------------------------------------- #
# Escriptures directes de l'operador                                            #
# --------------------------------------------------------------------------- #


#: Els camps on «no se sap» es un valor (NULL o `unknown`). Una escriptura de
#: l'operador no hi posa un `null`: tornar-hi es l'operacio explicita
#: `reset-unknown`. (Un `null` d'un altre camp —treure el bloc d'un element, el
#: lot d'un bloc— es una afirmacio estructural i si que es pot escriure.)
UNKNOWN_FIELDS: dict[StructureEntityKind, tuple[str, ...]] = {
    StructureEntityKind.BLOCK: (
        "separately_awardable",
        "base_price_ex_vat",
        "price_points",
        "billing_terms_json",
        "billing_schema",
    ),
    StructureEntityKind.SITE: (
        "is_critical",
        "address",
        "municipality_code",
        "municipality_code_system",
        "latitude",
        "longitude",
    ),
    StructureEntityKind.SCOPE_ITEM: (
        "quantity",
        "unit",
        "unit_base_price_ex_vat",
        "specs_json",
        "specs_schema",
    ),
    StructureEntityKind.REQUIREMENT_TIMING: ("timing",),
    StructureEntityKind.TERRITORY: ("territory_completeness",),
}


def _no_unknown_nulls(model: BaseModel, kind: StructureEntityKind) -> BaseModel:
    nulls = sorted(
        name
        for name in model.model_fields_set
        if name in UNKNOWN_FIELDS[kind] and getattr(model, name) is None
    )
    if nulls:
        raise ValueError(
            "Un null no es cap valor citat; per tornar a desconegut fes servir reset-unknown: "
            + ", ".join(nulls)
        )
    return model


class PlatformBlockCreate(TenderBlockCreate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds


class PlatformBlockUpdate(TenderBlockUpdate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds

    @model_validator(mode="after")
    def _cited_values(self):
        return _no_unknown_nulls(self, StructureEntityKind.BLOCK)


class PlatformSiteCreate(TenderSiteCreate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds


class PlatformSiteUpdate(TenderSiteUpdate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds

    @model_validator(mode="after")
    def _cited_values(self):
        return _no_unknown_nulls(self, StructureEntityKind.SITE)


class PlatformScopeItemCreate(ScopeItemCreate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds


class PlatformScopeItemUpdate(ScopeItemUpdate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds

    @model_validator(mode="after")
    def _cited_values(self):
        return _no_unknown_nulls(self, StructureEntityKind.SCOPE_ITEM)


class PlatformNeedCreate(CapabilityNeedCreate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds


class PlatformNeedUpdate(CapabilityNeedUpdate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds


class PlatformFactCreate(ContractFactCreate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds


class PlatformFactUpdate(ContractFactUpdate):
    source_ref_ids: list[uuid.UUID] = SourceRefIds


class PlatformTerritoryCreate(BaseModel):
    """Un territori mes per a un requisit (i, si cal, la completesa), amb cita.
    La cita queda a una proposta de l'operador promoguda al moment: els
    territoris no tenen taula de cites propia."""

    model_config = ConfigDict(extra="forbid")

    territory: RequirementTerritoryIn | None = None
    territory_completeness: TerritoryCompleteness | None = None
    source_ref_ids: list[uuid.UUID] = SourceRefIds


class PlatformCitationLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_kind: StructureEntityKind
    entity_id: uuid.UUID
    source_ref_id: uuid.UUID


class ResetUnknownRequest(BaseModel):
    """Tornar camps tri-estat o «encara no se sap» a desconegut: operacio explicita."""

    model_config = ConfigDict(extra="forbid")

    entity_kind: StructureEntityKind
    entity_id: uuid.UUID
    fields: list[str] = Field(min_length=1, max_length=20)


class ResetUnknownRead(BaseModel):
    entity_kind: StructureEntityKind
    entity_id: uuid.UUID
    changed: list[str]


# --------------------------------------------------------------------------- #
# Lectures amb provenance                                                       #
# --------------------------------------------------------------------------- #


class ProvenanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    origin: StructureOrigin
    authority: StructureAuthority | None = None
    producer_run_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None


class PlatformBlockRead(TenderBlockRead):
    provenance: ProvenanceRead


class PlatformSiteRead(TenderSiteRead):
    provenance: ProvenanceRead


class PlatformScopeItemRead(ScopeItemRead):
    provenance: ProvenanceRead


class PlatformNeedRead(CapabilityNeedRead):
    provenance: ProvenanceRead


class PlatformFactRead(ContractFactRead):
    provenance: ProvenanceRead


class PlatformTerritoryRead(RequirementTerritoryRead):
    provenance: ProvenanceRead


class PlatformRequirementTerritoriesRead(BaseModel):
    requirement_id: uuid.UUID
    territory_completeness: TerritoryCompleteness
    territories: list[PlatformTerritoryRead] = Field(default_factory=list)


class PlatformStructureRead(BaseModel):
    tender_id: uuid.UUID
    blocks: list[PlatformBlockRead] = Field(default_factory=list)
    sites: list[PlatformSiteRead] = Field(default_factory=list)
    scope_items: list[PlatformScopeItemRead] = Field(default_factory=list)
    contract_facts: list[PlatformFactRead] = Field(default_factory=list)
