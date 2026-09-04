"""Esquemes d'autenticacio i sessio."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.core.security import MIN_PASSWORD_LENGTH
from app.models.enums import MembershipRole
from app.schemas.common import APIModel, ORMModel


class LoginRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(APIModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)

    @field_validator("new_password")
    @classmethod
    def _strength(cls, value: str) -> str:
        if value.isdigit() or value.isalpha():
            raise ValueError("La contrasenya ha de combinar lletres i numeros o simbols.")
        return value


class CompanySummary(ORMModel):
    id: uuid.UUID
    legal_name: str
    trade_name: str | None = None


class MembershipRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    role: MembershipRole
    is_default: bool
    company: CompanySummary


class UserRead(ORMModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    is_active: bool
    last_login_at: datetime | None = None
    memberships: list[MembershipRead] = []
