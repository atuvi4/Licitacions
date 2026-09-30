"""Esquemes de l'Administracio Tuvi: alta d'empreses i consola de clients."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import EmailStr, Field

from app.models.enums import AuditAction, CompanyPlatformStatus, WorkspaceStatus
from app.schemas.common import APIModel

# --------------------------------------------------------------------------- #
# Alta                                                                         #
# --------------------------------------------------------------------------- #


class CompanyOnboardingRequest(APIModel):
    legal_name: str = Field(min_length=1, max_length=255)
    trade_name: str | None = Field(default=None, max_length=255)
    #: Obligatori: identifica el client i no es pot repetir.
    tax_id: str = Field(min_length=1, max_length=64)
    owner_email: EmailStr
    owner_full_name: str = Field(min_length=1, max_length=200)
    #: Un client nou comenca en pilot (60 dies per defecte) o actiu.
    initial_status: Literal["pilot", "active"] = "pilot"
    pilot_ends_at: date | None = None


class OnboardedCompanyRead(APIModel):
    id: uuid.UUID
    legal_name: str
    trade_name: str | None = None
    tax_id: str | None = None
    platform_status: CompanyPlatformStatus
    pilot_ends_at: date | None = None


class OnboardedOwnerRead(APIModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None


class CompanyOnboardingRead(APIModel):
    company: OnboardedCompanyRead
    owner: OnboardedOwnerRead
    #: Nomes aquesta vegada. No es desa en clar enlloc.
    temporary_password: str


# --------------------------------------------------------------------------- #
# Consola: llistat i fitxa                                                     #
# --------------------------------------------------------------------------- #


class PlatformPerson(APIModel):
    id: uuid.UUID
    full_name: str | None = None
    email: str


class PlatformOwner(PlatformPerson):
    assigned_at: datetime
    last_login_at: datetime | None = None


class PlatformCompanyRow(APIModel):
    id: uuid.UUID
    legal_name: str
    trade_name: str | None = None
    tax_id: str | None = None
    platform_status: CompanyPlatformStatus
    pilot_ends_at: date | None = None
    owner: PlatformOwner | None = None
    users_count: int
    tenders_count: int
    last_activity_at: datetime | None = None
    created_at: datetime


class PlatformStatusCounts(APIModel):
    all: int
    pilot: int
    active: int
    paused: int


class PlatformCompanyList(APIModel):
    items: list[PlatformCompanyRow]
    #: Sobre totes les empreses: les pestanyes no depenen del filtre.
    counts: PlatformStatusCounts


class PlatformCompanyStatus(APIModel):
    platform_status: CompanyPlatformStatus
    pilot_started_at: date | None = None
    pilot_ends_at: date | None = None
    status_changed_at: datetime | None = None


class PlatformTenderSummary(APIModel):
    title: str
    buyer_name: str | None = None
    status: WorkspaceStatus
    deadline_at: datetime | None = None


class PlatformTenderCounts(APIModel):
    total: int
    active: int
    preparing: int
    submitted: int


class PlatformActivity(APIModel):
    at: datetime
    action: AuditAction
    entity_type: str | None = None
    summary: str | None = None
    actor: PlatformPerson | None = None
    #: L'accio la va fer l'Administracio Tuvi, no el client.
    by_platform: bool = False


class PlatformUsersSummary(APIModel):
    total: int
    active_last_7_days: int


class PlatformClientSpace(APIModel):
    """Vista de seguiment: resums de nomes lectura, mai documents ni edicio."""

    tenders: PlatformTenderCounts
    recent_tenders: list[PlatformTenderSummary]
    recent_activity: list[PlatformActivity]
    users: PlatformUsersSummary


class PlatformCompanyDetail(APIModel):
    id: uuid.UUID
    legal_name: str
    trade_name: str | None = None
    tax_id: str | None = None
    created_at: datetime
    onboarded_by: PlatformPerson | None = None
    status: PlatformCompanyStatus
    owner: PlatformOwner | None = None
    client_space: PlatformClientSpace


class PlatformStatusChangeRequest(APIModel):
    status: CompanyPlatformStatus
    #: Nomes per a `pilot`: la fi nova (allargar) o la d'un pilot que comenca.
    pilot_ends_at: date | None = None
