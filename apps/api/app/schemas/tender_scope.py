"""Abast i semantica del plec (0049): que es compra, territoris d'un requisit,
proves exigides, necessitats tecniques, requisit ↔ criteri i fets contractuals.

Tot opcional i neutre per defecte. Res d'aixo diu si l'empresa ho compleix.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.knowledge import vocabulary
from app.models.enums import (
    AreaKind,
    ContractFactCode,
    NeedComparator,
    NeedType,
    ProofKind,
    TerritoryMatchMode,
)
from app.schemas.common import ORMModel
from app.schemas.provenance import TenderSourceRefRead

#: La versio actual de la forma de `proof_json`.
PROOF_SCHEMA_V1 = "proof_v1"


def _known_service_line(value: str) -> str:
    if value not in vocabulary.SERVICE_LINE_CODES:
        raise ValueError(f"Linia de servei desconeguda: {value}.")
    return value


def _known_capability(value: str | None) -> str | None:
    if value is not None and value not in vocabulary.CAPABILITIES_BY_CODE:
        raise ValueError(f"Capacitat desconeguda al vocabulari: {value}.")
    return value


# --------------------------------------------------------------------------- #
# Que es compra                                                                 #
# --------------------------------------------------------------------------- #


class ScopeItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service_line_code: str = Field(max_length=40)
    #: Exclusius: el bloc ja determina el lot si en te.
    block_id: uuid.UUID | None = None
    tender_lot_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    unit: str | None = Field(default=None, max_length=40)
    specs_json: dict[str, Any] | None = None
    specs_schema: str | None = Field(default=None, max_length=50)
    unit_base_price_ex_vat: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )

    @field_validator("service_line_code")
    @classmethod
    def _line(cls, value: str) -> str:
        return _known_service_line(value)


class ScopeItemCreate(ScopeItemBase):
    @model_validator(mode="after")
    def _coherent(self):
        if self.block_id is not None and self.tender_lot_id is not None:
            raise ValueError("Un element d'abast va a un bloc o a un lot, no a tots dos.")
        if self.specs_json is not None and self.specs_schema is None:
            raise ValueError("`specs_json` necessita `specs_schema`.")
        return self


class ScopeItemUpdate(BaseModel):
    """S'aplica nomes el que arriba."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service_line_code: str | None = Field(default=None, max_length=40)
    block_id: uuid.UUID | None = None
    tender_lot_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    unit: str | None = Field(default=None, max_length=40)
    specs_json: dict[str, Any] | None = None
    specs_schema: str | None = Field(default=None, max_length=50)
    unit_base_price_ex_vat: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )

    @field_validator("service_line_code")
    @classmethod
    def _line(cls, value: str | None) -> str | None:
        return None if value is None else _known_service_line(value)


class ScopeItemRead(ORMModel):
    id: uuid.UUID
    tender_id: uuid.UUID
    block_id: uuid.UUID | None = None
    tender_lot_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    service_line_code: str
    quantity: Decimal | None = None
    unit: str | None = None
    specs_json: dict[str, Any] | None = None
    specs_schema: str | None = None
    unit_base_price_ex_vat: Decimal | None = None
    created_at: datetime
    updated_at: datetime
    citations: list[TenderSourceRefRead] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contract_wide(self) -> bool:
        return self.block_id is None and self.tender_lot_id is None


# --------------------------------------------------------------------------- #
# Territoris i proves d'un requisit                                             #
# --------------------------------------------------------------------------- #


class RequirementTerritoryIn(BaseModel):
    """Una area amb el vocabulari de 0047. Nomes s'emmagatzema: cap comparacio."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: AreaKind
    label: str | None = Field(default=None, max_length=200)
    code: str | None = Field(default=None, max_length=40)
    code_system: str | None = Field(default=None, max_length=20)
    #: Matis literal del plec ("trama urbana", "nucli antic").
    qualifier: str | None = Field(default=None, max_length=200)
    site_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _references_something(self):
        if self.label is None and self.code is None and self.site_id is None:
            raise ValueError("Un territori necessita un nom, un codi o una seu.")
        return self


class RequirementTerritoryRead(ORMModel):
    id: uuid.UUID
    kind: AreaKind
    label: str | None = None
    code: str | None = None
    code_system: str | None = None
    qualifier: str | None = None
    site_id: uuid.UUID | None = None


class ProofItem(BaseModel):
    """Una prova exigida pel plec. `mandatory` descriu el plec, no el requisit."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: ProofKind
    text: str = Field(min_length=1, max_length=1000)
    mandatory: bool = True


def validate_proof(items: list[Any]) -> list[dict[str, Any]]:
    """La forma `proof_v1`: una llista (pot ser buida) d'elements valids."""
    return [ProofItem.model_validate(item).model_dump(mode="json") for item in items]


# --------------------------------------------------------------------------- #
# Necessitats tecniques                                                         #
# --------------------------------------------------------------------------- #


class CapabilityNeedBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    #: Exclusius: una capacitat del vocabulari, o el nom tal com el diu el plec
    #: si encara no hi es. Mai se'n crea cap de global des d'aqui.
    capability_code: str | None = Field(default=None, max_length=80)
    unmapped_capability: str | None = Field(default=None, max_length=200)
    comparator: NeedComparator
    value_json: dict[str, Any] | None = None
    value_schema: str | None = Field(default=None, max_length=50)
    need_type: NeedType
    #: Mateix tipus que `award_criteria.points`. No se suma ni puntua.
    points: float | None = Field(default=None, ge=0)
    award_criterion_id: uuid.UUID | None = None

    @field_validator("capability_code")
    @classmethod
    def _capability(cls, value: str | None) -> str | None:
        return _known_capability(value)


def _check_need(
    capability_code: str | None,
    unmapped: str | None,
    comparator: NeedComparator,
    value_json: dict[str, Any] | None,
    value_schema: str | None,
) -> None:
    if (capability_code is None) == (unmapped is None):
        raise ValueError(
            "Cal una capacitat del vocabulari o un nom sense mapar, no cap ni totes dues."
        )
    if comparator == NeedComparator.PRESENT:
        if value_json is not None:
            raise ValueError("`present` no porta valor.")
    elif value_json is None:
        raise ValueError(f"`{comparator}` necessita un valor.")
    if value_json is not None and value_schema is None:
        raise ValueError("`value_json` necessita `value_schema`.")


class CapabilityNeedCreate(CapabilityNeedBase):
    @model_validator(mode="after")
    def _coherent(self):
        _check_need(
            self.capability_code, self.unmapped_capability, self.comparator, self.value_json,
            self.value_schema,
        )  # fmt: skip
        return self


class CapabilityNeedUpdate(BaseModel):
    """S'aplica nomes el que arriba; la coherencia es comprova sobre el resultat."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    capability_code: str | None = Field(default=None, max_length=80)
    unmapped_capability: str | None = Field(default=None, max_length=200)
    comparator: NeedComparator | None = None
    value_json: dict[str, Any] | None = None
    value_schema: str | None = Field(default=None, max_length=50)
    need_type: NeedType | None = None
    points: float | None = Field(default=None, ge=0)
    award_criterion_id: uuid.UUID | None = None

    @field_validator("capability_code")
    @classmethod
    def _capability(cls, value: str | None) -> str | None:
        return _known_capability(value)


class CapabilityNeedRead(ORMModel):
    id: uuid.UUID
    requirement_id: uuid.UUID
    tender_id: uuid.UUID
    award_criterion_id: uuid.UUID | None = None
    capability_code: str | None = None
    unmapped_capability: str | None = None
    comparator: NeedComparator
    value_json: dict[str, Any] | None = None
    value_schema: str | None = None
    need_type: NeedType
    points: float | None = None
    created_at: datetime
    updated_at: datetime
    citations: list[TenderSourceRefRead] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Fets contractuals                                                             #
# --------------------------------------------------------------------------- #


class ContractFactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fact_code: ContractFactCode
    value_json: dict[str, Any]
    value_schema: str = Field(min_length=1, max_length=50)
    #: Buit = tot el contracte. `billing_terms` no en pot portar (invariant de servei).
    block_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class ContractFactUpdate(BaseModel):
    """S'aplica nomes el que arriba. Un conflicte d'abast (mateix codi + mateix
    conjunt de blocs que un altre fet) es refusa sencer."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fact_code: ContractFactCode | None = None
    value_json: dict[str, Any] | None = None
    value_schema: str | None = Field(default=None, min_length=1, max_length=50)
    block_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class ContractFactRead(ORMModel):
    id: uuid.UUID
    tender_id: uuid.UUID
    fact_code: ContractFactCode
    value_json: dict[str, Any]
    value_schema: str
    block_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    citations: list[TenderSourceRefRead] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contract_wide(self) -> bool:
        return not self.block_ids


# --------------------------------------------------------------------------- #
# Semantica d'un requisit (amplia l'endpoint d'estructura de 0048)              #
# --------------------------------------------------------------------------- #


class RequirementSemanticsRead(BaseModel):
    id: uuid.UUID
    tender_id: uuid.UUID
    territory_match_mode: TerritoryMatchMode
    territories: list[RequirementTerritoryRead] = Field(default_factory=list)
    proof_json: list[Any] | None = None
    proof_schema: str | None = None
    award_criterion_ids: list[uuid.UUID] = Field(default_factory=list)
    capability_needs: list[CapabilityNeedRead] = Field(default_factory=list)


class RequirementSemanticsUpdate(BaseModel):
    """`territories` i `award_criterion_ids` son conjunts que es substitueixen;
    `proof` substitueix la llista (`[]` = cap prova identificada). Ometre un
    camp no el toca."""

    model_config = ConfigDict(extra="forbid")

    territory_match_mode: TerritoryMatchMode | None = None
    territories: list[RequirementTerritoryIn] | None = Field(default=None, max_length=500)
    proof: list[ProofItem] | None = Field(default=None, max_length=100)
    award_criterion_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
