"""Estructura del plec (0048): metadades del contracte, blocs, seus i a que
aplica cada requisit o criteri. Tot opcional i tri-estat on cal: `null` vol dir
«no se sap», mai «no».
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.enums import RequirementTiming, TenderSiteRole
from app.schemas.common import ORMModel
from app.schemas.provenance import TenderSourceRefRead


class TenderStructureUpdate(BaseModel):
    """Correccions a les dades estructurals del contracte. S'apliquen **nomes els
    camps que arriben** (`model_fields_set`): ometre'n un no el toca, enviar-hi
    `null` el deixa com a desconegut."""

    model_config = ConfigDict(extra="forbid")

    duration_months: int | None = Field(default=None, ge=0)
    extension_months: int | None = Field(default=None, ge=0)
    #: Tri-estat: `true` SARA, `false` expressament no, `null` no se sap.
    regulated_harmonised: bool | None = None
    planned_start_date: date | None = None


class TenderBlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=500)
    tender_lot_id: uuid.UUID | None = None
    base_price_ex_vat: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    price_points: float | None = Field(default=None, ge=0)
    separately_awardable: bool | None = None
    billing_terms_json: dict[str, Any] | None = None
    billing_schema: str | None = Field(default=None, max_length=40)


class TenderBlockUpdate(BaseModel):
    """S'apliquen nomes els camps que arriben."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=500)
    tender_lot_id: uuid.UUID | None = None
    base_price_ex_vat: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    price_points: float | None = Field(default=None, ge=0)
    separately_awardable: bool | None = None
    billing_terms_json: dict[str, Any] | None = None
    billing_schema: str | None = Field(default=None, max_length=40)


class TenderBlockRead(ORMModel):
    id: uuid.UUID
    tender_id: uuid.UUID
    tender_lot_id: uuid.UUID | None = None
    code: str
    name: str
    base_price_ex_vat: Decimal | None = None
    price_points: float | None = None
    #: `null` = el plec no ho diu.
    separately_awardable: bool | None = None
    billing_terms_json: dict[str, Any] | None = None
    billing_schema: str | None = None
    created_at: datetime
    updated_at: datetime
    citations: list[TenderSourceRefRead] = Field(default_factory=list)


class TenderSiteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    #: Codi del document (`D02`) o nom real: almenys un dels dos. Mai l'un derivat de l'altre.
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=500)
    role: TenderSiteRole
    #: Tri-estat: `true` la font ho diu, `false` diu que no, `null`/omes = no se sap.
    is_critical: bool | None = None
    address: str | None = Field(default=None, max_length=500)
    municipality_code: str | None = Field(default=None, max_length=10)
    #: Lliure ("INE", "IDESCAT"...). No s'infereix: sense sistema, el codi no es compara.
    municipality_code_system: str | None = Field(default=None, max_length=20)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)

    @model_validator(mode="after")
    def _has_identity(self) -> TenderSiteCreate:
        if self.code is None and self.name is None:
            raise ValueError("Cal el codi del document o el nom real de la seu.")
        return self


class TenderSiteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=500)
    role: TenderSiteRole | None = None
    is_critical: bool | None = None
    address: str | None = Field(default=None, max_length=500)
    municipality_code: str | None = Field(default=None, max_length=10)
    #: Lliure ("INE", "IDESCAT"...). No s'infereix: sense sistema, el codi no es compara.
    municipality_code_system: str | None = Field(default=None, max_length=20)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)


class TenderSiteRead(ORMModel):
    id: uuid.UUID
    tender_id: uuid.UUID
    code: str | None = None
    name: str | None = None
    role: TenderSiteRole
    #: `null` = no se sap si es critica.
    is_critical: bool | None = None
    address: str | None = None
    municipality_code: str | None = None
    municipality_code_system: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    created_at: datetime
    updated_at: datetime
    citations: list[TenderSourceRefRead] = Field(default_factory=list)


class TenderStructureRead(BaseModel):
    tender_id: uuid.UUID
    duration_months: int | None = None
    extension_months: int | None = None
    regulated_harmonised: bool | None = None
    planned_start_date: date | None = None
    blocks: list[TenderBlockRead] = Field(default_factory=list)
    sites: list[TenderSiteRead] = Field(default_factory=list)
    #: 0049: que es compra i els fets contractuals sense columna propia.
    scope_items: list[Any] = Field(default_factory=list)
    contract_facts: list[Any] = Field(default_factory=list)


class StructureCitationLink(BaseModel):
    """Enllaça una cita JA CREADA (`tender_source_refs`) a un bloc, a una seu o a un
    criteri d'adjudicacio."""

    model_config = ConfigDict(extra="forbid")

    source_ref_id: uuid.UUID


class RequirementStructureUpdate(BaseModel):
    """Temporalitat i blocs d'un requisit o d'un criteri. S'aplica nomes el que
    arriba: `block_ids: []` vol dir «a tot el contracte»; ometre'l no el toca."""

    model_config = ConfigDict(extra="forbid")

    timing: RequirementTiming | None = None
    block_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class RequirementStructureRead(BaseModel):
    id: uuid.UUID
    tender_id: uuid.UUID
    timing: RequirementTiming
    #: Buit = aplica a tot el contracte.
    block_ids: list[uuid.UUID] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contract_wide(self) -> bool:
        return not self.block_ids


class AwardCriterionStructureRead(RequirementStructureRead):
    #: Els punts del criteri, sense repartir: son els mateixos a cada bloc.
    points: float | None = None
    #: 0050: les cites del criteri, amb el mateix patro que blocs i seus.
    citations: list[TenderSourceRefRead] = Field(default_factory=list)
