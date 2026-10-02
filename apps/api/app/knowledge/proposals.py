"""La forma de la proposta que porta una observacio historica.

Una observacio diu que l'historic suggereix alguna cosa ("a Sant Feliu hi posava
que tenien CPD Tier-3 propi") i porta la fila exacta que en naixeria si una
persona la validés. Es valida en dos moments: quan s'importa l'observacio (una
proposta mal formada no entra) i quan es promou.

**Una afirmacio negativa no es pot promoure a certificacio.** "No tenien ENS" es
informacio per preguntar, no una fila de `company_certifications`: el perfil no
guarda certificacions que no es tenen, i l'absencia ja vol dir "desconegut".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.knowledge.vocabulary import SERVICE_LINE_CODES, validate_fact_value, validate_level
from app.models.enums import (
    DeliveryMode,
    ObservationSubject,
    OperatingFactKey,
    PartnerRole,
)

_Text = Annotated[str, Field(min_length=1, max_length=160)]


class _Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _WithServiceLine(_Proposal):
    """Una `service_line_code`, si n'hi ha, ha de ser del vocabulari global."""

    @field_validator("service_line_code", check_fields=False)
    @classmethod
    def _known_service_line(cls, value: str | None) -> str | None:
        if value is not None and value not in SERVICE_LINE_CODES:
            raise ValueError(f"Linia de servei desconeguda: {value}.")
        return value


class OperatingFactProposal(_Proposal):
    key: OperatingFactKey
    value: bool
    detail: str | None = Field(default=None, max_length=500)
    #: Nomes als fets quantitatius (0052): quants. El vocabulari diu quines claus.
    value_number: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _value_of_fact(self):
        validate_fact_value(self.key, self.value, self.value_number)
        return self


class CertificationProposal(_Proposal):
    standard_code: str
    level: str | None = None
    name: _Text | None = None
    #: `False` = l'historic diu que NO la tenien. No es pot promoure.
    held: bool = True
    scope_service_lines: list[str] | None = Field(default=None, min_length=1)

    @field_validator("scope_service_lines")
    @classmethod
    def _known_service_lines(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and set(value) - SERVICE_LINE_CODES:
            raise ValueError("Linies de servei desconegudes a l'abast.")
        return sorted(set(value)) if value is not None else None

    @model_validator(mode="after")
    def _level_of_standard(self) -> CertificationProposal:
        validate_level(self.standard_code, self.level)
        return self


class ServiceProposal(_WithServiceLine):
    name: _Text
    service_line_code: str
    delivery_mode: DeliveryMode | None = None


class SolutionProposal(_WithServiceLine):
    name: _Text
    service_line_code: str
    vendor: str | None = Field(default=None, max_length=160)
    edition: str | None = Field(default=None, max_length=80)


class ProductProposal(_WithServiceLine):
    manufacturer: _Text
    model: _Text
    service_line_code: str | None = None


class PartnerRoleProposal(_WithServiceLine):
    role: PartnerRole
    service_line_code: str | None = None
    territory: str | None = Field(default=None, max_length=160)


class PartnerProposal(_Proposal):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    roles: list[PartnerRoleProposal] = Field(default_factory=list)


PROPOSALS: dict[ObservationSubject, type[_Proposal]] = {
    ObservationSubject.OPERATING_FACT: OperatingFactProposal,
    ObservationSubject.CERTIFICATION: CertificationProposal,
    ObservationSubject.SERVICE: ServiceProposal,
    ObservationSubject.SOLUTION: SolutionProposal,
    ObservationSubject.PRODUCT: ProductProposal,
    ObservationSubject.PARTNER: PartnerProposal,
}


def parse_proposal(subject: ObservationSubject, payload: dict) -> _Proposal:
    """La proposta validada. `pydantic.ValidationError` si no te la forma del subjecte."""
    return PROPOSALS[ObservationSubject(subject)].model_validate(payload)


def is_promotable(subject: ObservationSubject, payload: dict) -> bool:
    proposal = parse_proposal(subject, payload)
    return not (isinstance(proposal, CertificationProposal) and not proposal.held)


__all__ = [
    "CertificationProposal",
    "OperatingFactProposal",
    "PartnerProposal",
    "PartnerRoleProposal",
    "ProductProposal",
    "ServiceProposal",
    "SolutionProposal",
    "is_promotable",
    "parse_proposal",
]
