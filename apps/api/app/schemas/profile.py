"""Esquemes de serveis, territoris i exercicis economics."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from app.knowledge.vocabulary import SERVICE_LINE_CODES
from app.models.enums import (
    DeliveryMode,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    SpecializationLevel,
    TerritoryType,
    VerificationStatus,
)
from app.schemas.common import APIModel, AuditFields
from app.schemas.coverage import CoverageIn, CoverageRead

CURRENT_YEAR_MAX = 2200


class ServiceBase(APIModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    category: str | None = Field(default=None, max_length=120)
    description: str | None = None
    specialization_level: SpecializationLevel | None = None
    active: bool | None = None
    notes: str | None = None
    #: Linia del vocabulari global: el que permet comparar-lo amb un plec.
    service_line_code: str | None = Field(default=None, max_length=40)
    delivery_mode: DeliveryMode | None = None
    as_of: date | None = None
    valid_until: date | None = None
    verification_status: VerificationStatus | None = None
    #: Cobertura territorial (0047). Opcional: sense enviar-la, res no canvia.
    coverage: CoverageIn | None = None

    @field_validator("service_line_code")
    @classmethod
    def _known_service_line(cls, value: str | None) -> str | None:
        if value is not None and value not in SERVICE_LINE_CODES:
            raise ValueError(f"Linia de servei desconeguda: {value}.")
        return value

    @model_validator(mode="after")
    def _validity_after_as_of(self):
        if self.as_of and self.valid_until and self.valid_until < self.as_of:
            raise ValueError("`valid_until` no pot ser anterior a `as_of`.")
        return self


class ServiceCreate(ServiceBase):
    name: str = Field(min_length=2, max_length=160)
    active: bool = True


class ServiceUpdate(ServiceBase):
    pass


class ServiceRead(AuditFields):
    company_id: uuid.UUID
    name: str
    category: str | None = None
    description: str | None = None
    specialization_level: SpecializationLevel | None = None
    active: bool
    notes: str | None = None
    service_line_code: str | None = None
    delivery_mode: DeliveryMode | None = None
    knowledge_origin: KnowledgeOrigin
    as_of: date
    valid_until: date | None = None
    verification_status: VerificationStatus
    coverage: CoverageRead


class TerritoryBase(APIModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    territory_type: TerritoryType | None = None
    code: str | None = Field(default=None, max_length=32)
    priority: int | None = Field(default=None, ge=0, le=100)
    willing_to_work: bool | None = None
    notes: str | None = None


class TerritoryCreate(TerritoryBase):
    name: str = Field(min_length=2, max_length=160)
    territory_type: TerritoryType
    priority: int = Field(default=0, ge=0, le=100)
    willing_to_work: bool = True


class TerritoryUpdate(TerritoryBase):
    pass


class TerritoryRead(AuditFields):
    name: str
    territory_type: TerritoryType
    code: str | None = None
    priority: int
    willing_to_work: bool
    notes: str | None = None


class FinancialYearBase(APIModel):
    year: int | None = Field(default=None, ge=1900, le=CURRENT_YEAR_MAX)
    revenue: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = None
    #: Procedencia (0052). Sense `verification_status`: verificar nomes es pot per
    #: `/knowledge/records/{kind}/{id}/verify` (propietari o administracio).
    as_of: date | None = None
    lifecycle: KnowledgeLifecycle | None = None


class FinancialYearCreate(FinancialYearBase):
    year: int = Field(ge=1900, le=CURRENT_YEAR_MAX)
    currency: str = Field(default="EUR", min_length=3, max_length=3)


class FinancialYearUpdate(FinancialYearBase):
    pass


class FinancialYearRead(AuditFields):
    year: int
    revenue: Decimal | None = None
    currency: str
    notes: str | None = None
    knowledge_origin: KnowledgeOrigin
    as_of: date | None = None
    verification_status: VerificationStatus
    lifecycle: KnowledgeLifecycle
