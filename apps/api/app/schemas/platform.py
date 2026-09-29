"""Esquemes de l'administracio de la plataforma (alta d'empresa i propietari)."""

from __future__ import annotations

import uuid

from pydantic import EmailStr, Field

from app.schemas.common import APIModel


class CompanyOnboardingRequest(APIModel):
    legal_name: str = Field(min_length=1, max_length=255)
    trade_name: str | None = Field(default=None, max_length=255)
    tax_id: str | None = Field(default=None, max_length=64)
    owner_email: EmailStr
    owner_full_name: str = Field(min_length=1, max_length=200)


class OnboardedCompanyRead(APIModel):
    id: uuid.UUID
    legal_name: str
    trade_name: str | None = None
    tax_id: str | None = None


class OnboardedOwnerRead(APIModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None


class CompanyOnboardingRead(APIModel):
    company: OnboardedCompanyRead
    owner: OnboardedOwnerRead
    #: Nomes aquesta vegada. No es desa en clar enlloc.
    temporary_password: str
