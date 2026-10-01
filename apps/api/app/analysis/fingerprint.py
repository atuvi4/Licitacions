"""`tender_structure_fingerprint` i `inputs_hash`: purs i deterministes.

El fingerprint es l'SHA-256 de la forma canonica de l'estructura del plec
(0048 + 0049 + 0050): tot el que l'analisi llegeix, i nomes aixo. Les cites
(`tender_source_refs`) en queden fora: canviar nomes d'on surt un valor no
canvia el que el plec exigeix. Els conjunts entren ordenats i sense duplicats
semantics; els ids es substitueixen per claus naturals (codi de bloc, identitat
d'una seu, `dedup_key` d'un requisit o criteri); Decimal, dates, booleans i
nuls tenen una sola representacio.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any


def _norm(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def jsonable(value: Any) -> Any:
    """Una sola forma per a cada tipus: cap Decimal, data o enum sense normalitzar."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((jsonable(v) for v in value), key=canonical_json)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Vista de l'estructura del plec                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TerritoryView:
    kind: str
    label: str | None = None
    code: str | None = None
    code_system: str | None = None
    qualifier: str | None = None
    #: Identitat de la seu ancorada (`(code, name)` canonic), no cap id.
    site_key: str | None = None

    def identity(self) -> tuple:
        return (
            self.kind,
            self.site_key,
            _norm(self.code_system),
            _norm(self.code),
            _norm(self.label),
            _norm(self.qualifier),
        )

    def canonical(self) -> dict[str, Any]:
        kind, site_key, code_system, code, label, qualifier = self.identity()
        return {
            "kind": kind,
            "site": site_key,
            "code_system": code_system,
            "code": code,
            "label": label,
            "qualifier": qualifier,
        }


@dataclass(frozen=True, slots=True)
class NeedView:
    id: str
    capability_code: str | None
    unmapped_capability: str | None
    comparator: str
    value_json: dict[str, Any] | None
    value_schema: str | None
    need_type: str
    points: float | None
    criterion_key: str | None

    def canonical(self) -> dict[str, Any]:
        return {
            "capability_code": self.capability_code,
            "unmapped_capability": _norm(self.unmapped_capability),
            "comparator": self.comparator,
            "value_json": self.value_json,
            "value_schema": self.value_schema,
            "need_type": self.need_type,
            "points": self.points,
            "criterion": self.criterion_key,
        }


@dataclass(frozen=True, slots=True)
class RequirementView:
    id: str
    key: str
    title: str
    category: str
    obligation: str
    timing: str
    block_codes: tuple[str, ...] = ()
    territory_match_mode: str = "any"
    territory_completeness: str = "unknown"
    territories: tuple[TerritoryView, ...] = ()
    proof_json: list[Any] | None = None
    proof_schema: str | None = None
    needs: tuple[NeedView, ...] = ()
    criterion_keys: tuple[str, ...] = ()
    value_json: dict[str, Any] | None = None

    def canonical(self) -> dict[str, Any]:
        territories = {t.identity(): t.canonical() for t in self.territories}
        needs = {canonical_json(n.canonical()): n.canonical() for n in self.needs}
        return {
            "key": self.key,
            "title": _norm(self.title),
            "category": self.category,
            "obligation": self.obligation,
            "timing": self.timing,
            "blocks": sorted(set(self.block_codes)),
            "territory_match_mode": self.territory_match_mode,
            "territory_completeness": self.territory_completeness,
            "territories": [territories[k] for k in sorted(territories, key=canonical_json)],
            "proof_json": self.proof_json,
            "proof_schema": self.proof_schema,
            "needs": [needs[k] for k in sorted(needs)],
            "criteria": sorted(set(self.criterion_keys)),
            "value_json": self.value_json,
        }


@dataclass(frozen=True, slots=True)
class CriterionView:
    id: str
    key: str
    name: str
    points: float | None
    criterion_type: str
    evaluation_type: str
    timing: str
    block_codes: tuple[str, ...] = ()
    tranches: list[Any] | None = None
    formula_text: str | None = None

    def canonical(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": _norm(self.name),
            "points": self.points,
            "criterion_type": self.criterion_type,
            "evaluation_type": self.evaluation_type,
            "timing": self.timing,
            "blocks": sorted(set(self.block_codes)),
            "tranches": self.tranches,
            "formula_text": _norm(self.formula_text),
        }


@dataclass(frozen=True, slots=True)
class BlockView:
    code: str
    name: str
    base_price_ex_vat: Decimal | None = None
    price_points: float | None = None
    separately_awardable: bool | None = None
    billing_terms_json: dict[str, Any] | None = None
    billing_schema: str | None = None
    lot_key: str | None = None

    def canonical(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": _norm(self.name),
            "base_price_ex_vat": self.base_price_ex_vat,
            "price_points": self.price_points,
            "separately_awardable": self.separately_awardable,
            "billing_terms_json": self.billing_terms_json,
            "billing_schema": self.billing_schema,
            "lot": self.lot_key,
        }


@dataclass(frozen=True, slots=True)
class SiteView:
    code: str | None
    name: str | None
    role: str
    is_critical: bool | None = None
    address: str | None = None
    municipality_code: str | None = None
    municipality_code_system: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None

    @property
    def key(self) -> str:
        return canonical_json([_norm(self.code), _norm(self.name)])

    def canonical(self) -> dict[str, Any]:
        return {
            "code": _norm(self.code),
            "name": _norm(self.name),
            "role": self.role,
            "is_critical": self.is_critical,
            "address": _norm(self.address),
            "municipality_code": self.municipality_code,
            "municipality_code_system": self.municipality_code_system,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


@dataclass(frozen=True, slots=True)
class ScopeItemView:
    id: str
    service_line_code: str
    block_code: str | None = None
    lot_key: str | None = None
    site_key: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    specs_json: dict[str, Any] | None = None
    specs_schema: str | None = None
    unit_base_price_ex_vat: Decimal | None = None

    def canonical(self) -> dict[str, Any]:
        return {
            "service_line": self.service_line_code,
            "block": self.block_code,
            "lot": self.lot_key,
            "site": self.site_key,
            "quantity": self.quantity,
            "unit": _norm(self.unit),
            "specs_json": self.specs_json,
            "specs_schema": self.specs_schema,
            "unit_base_price_ex_vat": self.unit_base_price_ex_vat,
        }


@dataclass(frozen=True, slots=True)
class FactView:
    fact_code: str
    value_json: dict[str, Any]
    value_schema: str
    block_codes: tuple[str, ...] = ()

    def canonical(self) -> dict[str, Any]:
        return {
            "fact_code": self.fact_code,
            "value_json": self.value_json,
            "value_schema": self.value_schema,
            "blocks": sorted(set(self.block_codes)),
        }


@dataclass(frozen=True, slots=True)
class TenderStructureView:
    tender_id: str
    duration_months: int | None = None
    extension_months: int | None = None
    regulated_harmonised: bool | None = None
    planned_start_date: date | None = None
    blocks: tuple[BlockView, ...] = ()
    sites: tuple[SiteView, ...] = ()
    scope_items: tuple[ScopeItemView, ...] = ()
    requirements: tuple[RequirementView, ...] = ()
    criteria: tuple[CriterionView, ...] = ()
    facts: tuple[FactView, ...] = ()
    #: Nomes per a la relectura: no entra al fingerprint.
    source: dict[str, Any] = field(default_factory=dict)

    def canonical(self) -> dict[str, Any]:
        def as_set(items: Any) -> list[dict[str, Any]]:
            unique = {canonical_json(item.canonical()): item.canonical() for item in items}
            return [unique[k] for k in sorted(unique)]

        return {
            "duration_months": self.duration_months,
            "extension_months": self.extension_months,
            "regulated_harmonised": self.regulated_harmonised,
            "planned_start_date": self.planned_start_date,
            "blocks": as_set(self.blocks),
            "sites": as_set(self.sites),
            "scope_items": as_set(self.scope_items),
            "requirements": as_set(self.requirements),
            "criteria": as_set(self.criteria),
            "facts": as_set(self.facts),
        }


def structure_fingerprint(view: TenderStructureView) -> str:
    """SHA-256 de la forma canonica. Mateixa semantica, mateix hash."""
    return sha256_of(view.canonical())


# --------------------------------------------------------------------------- #
# inputs_hash                                                                   #
# --------------------------------------------------------------------------- #

INPUT_FIELDS = (
    "tender_revision_id",
    "extraction_run_id",
    "classification_run_id",
    "review_revision_hash",
    "tender_structure_fingerprint",
    "knowledge_fingerprint",
    "rules_version",
)


def inputs_hash(
    *,
    tender_revision_id: str | None,
    extraction_run_id: str | None,
    classification_run_id: str | None,
    review_revision_hash: str | None,
    tender_structure_fingerprint: str,
    knowledge_fingerprint: str,
    rules_version: str,
) -> str:
    """EXACTAMENT aquests set inputs, en forma canonica. Un `None` es un `None`."""
    return sha256_of(
        {
            "tender_revision_id": tender_revision_id,
            "extraction_run_id": extraction_run_id,
            "classification_run_id": classification_run_id,
            "review_revision_hash": review_revision_hash,
            "tender_structure_fingerprint": tender_structure_fingerprint,
            "knowledge_fingerprint": knowledge_fingerprint,
            "rules_version": rules_version,
        }
    )


def review_revision_hash(items: list[dict[str, Any]]) -> str | None:
    """L'estat de la revisio humana dels requisits i criteris: `(clau, estat,
    correccio)` en ordre canonic. `None` si no hi ha res a revisar."""
    if not items:
        return None
    return sha256_of(sorted((jsonable(item) for item in items), key=canonical_json))
