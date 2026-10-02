"""Esquemes de projectes (experiencia acreditable)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from app.knowledge.vocabulary import SERVICE_LINE_CODES
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
    #: LEGACY (UI 4C antiga): serveis del cataleg actual. Substitueix el conjunt de
    #: `project_services`, que des de 0053 ja no llegeix ni la qualificacio ni l'analisi.
    service_ids: list[uuid.UUID] | None = Field(
        default=None,
        description=(
            "LEGACY (UI 4C antiga). Serveis del cataleg actual vinculats al projecte. NO "
            "modifica les linies que la referencia acredita ni la qualificacio: aixo es "
            "`service_line_codes`. No hi ha cap sincronitzacio entre els dos."
        ),
    )
    #: Linies de servei del vocabulari que la referencia ACREDITA (0053). Enviar-les
    #: substitueix el conjunt sencer (llista buida = cap); no enviar-les el deixa igual.
    service_line_codes: list[str] | None = Field(
        default=None,
        description=(
            "Linies de servei del vocabulari que la referencia ACREDITA (font de veritat de "
            "la qualificacio, 0053). Substitueix el conjunt sencer; llista buida = cap."
        ),
    )
    #: Imports executats per any (0052). Enviar-los substitueix el desglossament
    #: sencer; una llista buida l'esborra. Sense desglossament, l'import d'un
    #: projecte plurianual no s'imputa a cap any.
    annual_amounts: list[AnnualAmountIn] | None = None
    #: Procedencia (0052). Sense `verification_status`: verificar nomes es pot per
    #: `/knowledge/records/{kind}/{id}/verify` (propietari o administracio).
    as_of: date | None = None
    lifecycle: KnowledgeLifecycle | None = None

    @field_validator("service_line_codes")
    @classmethod
    def _known_service_lines(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - SERVICE_LINE_CODES)
        if unknown:
            raise ValueError(f"Linies de servei desconegudes: {', '.join(unknown)}.")
        return sorted(set(value))

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
    #: LEGACY: serveis del cataleg vinculats (UI 4C antiga).
    services: list[ProjectServiceRef] = Field(
        default=[],
        description="LEGACY: serveis del cataleg vinculats. No son les linies acreditades.",
    )
    #: Linies de servei que la referencia acredita (0053): les que llegeix la qualificacio.
    service_line_codes: list[str] = Field(
        default=[],
        description="Linies de servei que la referencia acredita (font de veritat, 0053).",
    )
    cpvs: list[ProjectCPVRead] = []
    annual_amounts: list[AnnualAmountRead] = []
    knowledge_origin: KnowledgeOrigin
    as_of: date | None = None
    verification_status: VerificationStatus
    lifecycle: KnowledgeLifecycle
