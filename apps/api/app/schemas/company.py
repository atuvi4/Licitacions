"""Esquemes de Company i preferencies contractuals."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, field_validator

from app.schemas.common import APIModel, ORMModel

_TAX_ID = re.compile(r"^[A-Za-z0-9\-\. ]{4,64}$")


class CompanyBase(APIModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=255)
    trade_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(default=None, max_length=64)
    legal_form: str | None = Field(default=None, max_length=120)
    incorporation_date: date | None = None
    website: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    autonomous_region: str | None = Field(default=None, max_length=120)
    province: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=16)
    address: str | None = Field(default=None, max_length=255)
    description: str | None = None
    employee_count: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("tax_id")
    @classmethod
    def _validate_tax_id(cls, value: str | None) -> str | None:
        if value and not _TAX_ID.match(value):
            raise ValueError("Format de NIF/CIF no valid.")
        return value.upper().strip() if value else value

    @field_validator("country")
    @classmethod
    def _upper_country(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("website")
    @classmethod
    def _validate_website(cls, value: str | None) -> str | None:
        if value and not value.startswith(("http://", "https://")):
            return f"https://{value}"
        return value


class CompanyCreate(CompanyBase):
    legal_name: str = Field(min_length=2, max_length=255)


class CompanyUpdate(CompanyBase):
    """PATCH: nomes s'apliquen els camps enviats explicitament."""


class CompanyRead(ORMModel):
    """Company no porta created_by/updated_by: la crea el sistema, no un usuari."""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    legal_name: str
    trade_name: str | None = None
    tax_id: str | None = None
    legal_form: str | None = None
    incorporation_date: date | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None
    country: str | None = None
    autonomous_region: str | None = None
    province: str | None = None
    city: str | None = None
    postal_code: str | None = None
    address: str | None = None
    description: str | None = None
    employee_count: int | None = None


class ContractPreferencesBase(APIModel):
    minimum_interesting_contract: Decimal | None = Field(default=None, ge=0)
    preferred_contract_min: Decimal | None = Field(default=None, ge=0)
    preferred_contract_max: Decimal | None = Field(default=None, ge=0)
    maximum_recommended_contract: Decimal | None = Field(default=None, ge=0)
    maximum_assumable_contract: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    notes: str | None = None


class ContractPreferencesUpdate(ContractPreferencesBase):
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class ContractPreferencesRead(ORMModel):
    """Les preferencies d'una empresa, o els valors per defecte si mai no s'han desat.

    `id`, `created_at` i `updated_at` son `None` mentre no hi ha fila: consultar-les
    no la crea (un GET no escriu), i un identificador inventat faria creure que
    existeix un registre que no hi es. La primera escriptura explicita la crea.
    """

    id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None
    company_id: uuid.UUID
    minimum_interesting_contract: Decimal | None = None
    preferred_contract_min: Decimal | None = None
    preferred_contract_max: Decimal | None = None
    maximum_recommended_contract: Decimal | None = None
    maximum_assumable_contract: Decimal | None = None
    currency: str
    notes: str | None = None
