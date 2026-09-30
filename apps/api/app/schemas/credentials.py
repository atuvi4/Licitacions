"""Esquemes de certificacions, registres i assegurances."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.knowledge.vocabulary import SERVICE_LINE_CODES, validate_level
from app.models.enums import (
    InsuranceLimitKind,
    InsuranceType,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    RegistrationType,
    VerificationStatus,
)
from app.schemas.common import APIModel, AuditFields, ExpirableMixin


class _DateOrderMixin(APIModel):
    @model_validator(mode="after")
    def _check_dates(self):
        start = getattr(self, "issued_at", None) or getattr(self, "starts_at", None)
        end = getattr(self, "expires_at", None)
        if start and end and end < start:
            raise ValueError("La data de caducitat no pot ser anterior a la d'emissio.")
        return self


class CertificationBase(_DateOrderMixin):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    issuer: str | None = Field(default=None, max_length=200)
    certificate_number: str | None = Field(default=None, max_length=120)
    scope: str | None = None
    issued_at: date | None = None
    expires_at: date | None = None
    verification_status: VerificationStatus | None = None
    notes: str | None = None
    #: Norma i nivell del vocabulari global (`ENS` + `medium`). Opcionals: una
    #: certificacio sense norma continua existint, pero el motor nomes la pot
    #: comparar pel nom.
    standard_code: str | None = Field(default=None, max_length=40)
    level: str | None = Field(default=None, max_length=20)
    as_of: date | None = None
    lifecycle: KnowledgeLifecycle | None = None
    #: Abast ESTRUCTURAT: linies de servei que cobreix. El text de `scope` es nomes
    #: una nota; el motor no en dedueix cobertura.
    scope_service_lines: list[str] | None = Field(default=None, min_length=1)

    @field_validator("scope_service_lines")
    @classmethod
    def _known_service_lines(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - SERVICE_LINE_CODES)
        if unknown:
            raise ValueError(f"Linies de servei desconegudes: {', '.join(unknown)}.")
        return sorted(set(value))

    @model_validator(mode="after")
    def _level_of_standard(self):
        if self.standard_code is not None or self.level is not None:
            validate_level(self.standard_code, self.level)
        return self


class CertificationCreate(CertificationBase):
    name: str = Field(min_length=2, max_length=200)
    verification_status: VerificationStatus = VerificationStatus.PENDING_VERIFICATION


class CertificationUpdate(CertificationBase):
    pass


class CertificationRead(AuditFields, ExpirableMixin):
    name: str
    issuer: str | None = None
    certificate_number: str | None = None
    scope: str | None = None
    issued_at: date | None = None
    verification_status: VerificationStatus
    notes: str | None = None
    standard_code: str | None = None
    level: str | None = None
    scope_service_lines: list[str] | None = None
    knowledge_origin: KnowledgeOrigin
    as_of: date
    lifecycle: KnowledgeLifecycle


class RegistrationBase(_DateOrderMixin):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    type: RegistrationType | None = None
    registration_number: str | None = Field(default=None, max_length=120)
    issuer: str | None = Field(default=None, max_length=200)
    issued_at: date | None = None
    expires_at: date | None = None
    notes: str | None = None


class RegistrationCreate(RegistrationBase):
    name: str = Field(min_length=2, max_length=200)
    type: RegistrationType = RegistrationType.OTHER


class RegistrationUpdate(RegistrationBase):
    pass


class RegistrationRead(AuditFields, ExpirableMixin):
    name: str
    type: RegistrationType
    registration_number: str | None = None
    issuer: str | None = None
    issued_at: date | None = None
    notes: str | None = None


class InsuranceBase(_DateOrderMixin):
    type: InsuranceType | None = None
    provider: str | None = Field(default=None, max_length=200)
    policy_number: str | None = Field(default=None, max_length=120)
    coverage_description: str | None = None
    coverage_amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    starts_at: date | None = None
    expires_at: date | None = None
    #: `None` vol dir "no consta". Un `false` per defecte convertiria un buit en
    #: una afirmacio, i el compliment la llegiria com a incompliment.
    additional_insured: bool | None = None
    notes: str | None = None


class InsuranceCreate(InsuranceBase):
    type: InsuranceType = InsuranceType.OTHER
    currency: str = Field(default="EUR", min_length=3, max_length=3)


class InsuranceUpdate(InsuranceBase):
    pass


class InsuranceRead(AuditFields, ExpirableMixin):
    type: InsuranceType
    provider: str | None = None
    policy_number: str | None = None
    coverage_description: str | None = None
    coverage_amount: Decimal | None = None
    currency: str
    starts_at: date | None = None
    additional_insured: bool | None = None
    notes: str | None = None


class InsuranceLimitBase(APIModel):
    kind: InsuranceLimitKind | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = None


class InsuranceLimitCreate(InsuranceLimitBase):
    #: Sense valor per defecte a proposit: quin limit es aixo es precisament la
    #: dada que la fitxa antiga no tenia, i triar-la per l'usuari la tornaria a
    #: perdre.
    kind: InsuranceLimitKind
    amount: Decimal = Field(ge=0)
    currency: str = Field(default="EUR", min_length=3, max_length=3)


class InsuranceLimitUpdate(InsuranceLimitBase):
    pass


class InsuranceLimitRead(AuditFields):
    insurance_id: UUID
    kind: InsuranceLimitKind
    amount: Decimal
    currency: str
    notes: str | None = None
