"""Esquemes de CPV."""

from __future__ import annotations

import re
import uuid

from pydantic import Field, field_validator

from app.schemas.common import APIModel, ORMModel

# Un CPV es 8 digits, opcionalment amb digit de control (12345678-9).
CPV_PATTERN = re.compile(r"^[0-9]{8}(-[0-9])?$")


def normalize_cpv(value: str) -> str:
    code = value.strip().replace(" ", "")
    if not CPV_PATTERN.match(code):
        raise ValueError("El codi CPV ha de tenir 8 digits (opcionalment amb digit de control).")
    return code


class CPVCodeCreate(APIModel):
    code: str = Field(min_length=8, max_length=10)
    description: str = Field(min_length=2, max_length=500)
    parent_code: str | None = Field(default=None, max_length=10)

    @field_validator("code")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_cpv(value)


class CPVCodeRead(ORMModel):
    code: str
    description: str
    parent_code: str | None = None
    is_active: bool


class CPVPreferenceCreate(APIModel):
    cpv_code: str = Field(min_length=8, max_length=10)
    # Si el codi encara no es al cataleg, s'hi dona d'alta amb aquesta descripcio.
    # Evita haver de gestionar el cataleg a part mentre no s'importi l'oficial.
    description: str | None = Field(default=None, max_length=500)
    priority: int = Field(default=0, ge=0, le=100)
    active: bool = True
    excluded: bool = False
    notes: str | None = None

    @field_validator("cpv_code")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_cpv(value)


class CPVPreferenceUpdate(APIModel):
    priority: int | None = Field(default=None, ge=0, le=100)
    active: bool | None = None
    excluded: bool | None = None
    notes: str | None = None


class CPVPreferenceRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    cpv_code: str
    description: str | None = None
    priority: int
    active: bool
    excluded: bool
    notes: str | None = None


class ProjectCPVCreate(APIModel):
    cpv_code: str = Field(min_length=8, max_length=10)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("cpv_code")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_cpv(value)


class ProjectCPVRead(ORMModel):
    cpv_code: str
    description: str | None = None
