"""Esquemes de projectes (experiencia acreditable)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field, model_validator

from app.models.enums import (
    ClientType,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    ProjectStatus,
    VerificationStatus,
)
from app.schemas.common import APIModel, AuditFields, ORMModel
from app.schemas.cpv import ProjectCPVRead


class ProjectServiceRef(ORMModel):
    id: uuid.UUID
    name: str
    category: str | None = None


class AnnualAmountIn(APIModel):
    """Import EXECUTAT d'un projecte en un any (0052)."""

    year: int = Field(ge=1900, le=2200)
    amount: Decimal = Field(ge=0)


class AnnualAmountRead(ORMModel):
    year: int
    amount: Decimal


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
    #: Imports executats per any (0052). Enviar-los substitueix el desglossament
    #: sencer; una llista buida l'esborra. Sense desglossament, l'import d'un
    #: projecte plurianual no s'imputa a cap any.
    annual_amounts: list[AnnualAmountIn] | None = None
    #: Procedencia (0052). Sense `verification_status`: verificar nomes es pot per
    #: `/knowledge/records/{kind}/{id}/verify` (propietari o administracio).
    as_of: date | None = None
    lifecycle: KnowledgeLifecycle | None = None

    @model_validator(mode="after")
    def _check_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("La data de fi no pot ser anterior a la d'inici.")
        if self.annual_amounts:
            years = [a.year for a in self.annual_amounts]
            if len(set(years)) != len(years):
                raise ValueError("Un any nomes pot tenir un import executat.")
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
    annual_amounts: list[AnnualAmountRead] = []
    knowledge_origin: KnowledgeOrigin
    as_of: date | None = None
    verification_status: VerificationStatus
    lifecycle: KnowledgeLifecycle
