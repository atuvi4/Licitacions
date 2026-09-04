"""Esquemes de serveis, territoris i exercicis economics."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import Field

from app.models.enums import SpecializationLevel, TerritoryType
from app.schemas.common import APIModel, AuditFields

CURRENT_YEAR_MAX = 2200


class ServiceBase(APIModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    category: str | None = Field(default=None, max_length=120)
    description: str | None = None
    specialization_level: SpecializationLevel | None = None
    active: bool | None = None
    notes: str | None = None


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
