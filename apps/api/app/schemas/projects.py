"""Esquemes de projectes (experiencia acreditable)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field, model_validator

from app.models.enums import ClientType, ProjectStatus
from app.schemas.common import APIModel, AuditFields, ORMModel
from app.schemas.cpv import ProjectCPVRead


class ProjectServiceRef(ORMModel):
    id: uuid.UUID
    name: str
    category: str | None = None


class ProjectBase(APIModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    client_name: str | None = Field(default=None, max_length=255)
    client_type: ClientType | None = None
    sector: str | None = Field(default=None, max_length=160)
    description: str | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = Field(default=None, max_length=255)
    status: ProjectStatus | None = None
    reference_contact: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    # Substitueix el conjunt complet de serveis vinculats quan s'informa.
    service_ids: list[uuid.UUID] | None = None

    @model_validator(mode="after")
    def _check_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("La data de fi no pot ser anterior a la d'inici.")
        return self


class ProjectCreate(ProjectBase):
    name: str = Field(min_length=2, max_length=255)
    client_type: ClientType = ClientType.OTHER
    status: ProjectStatus = ProjectStatus.COMPLETED
    currency: str = Field(default="EUR", min_length=3, max_length=3)


class ProjectUpdate(ProjectBase):
    pass


class ProjectRead(AuditFields):
    company_id: uuid.UUID
    name: str
    client_name: str | None = None
    client_type: ClientType
    sector: str | None = None
    description: str | None = None
    amount: Decimal | None = None
    currency: str
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    status: ProjectStatus
    reference_contact: str | None = None
    notes: str | None = None
    services: list[ProjectServiceRef] = []
    cpvs: list[ProjectCPVRead] = []
