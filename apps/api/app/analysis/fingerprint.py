"""`tender_structure_fingerprint` i `inputs_hash`: purs i deterministes.

El fingerprint es l'SHA-256 de la forma canonica de l'estructura del plec
(0048 + 0049 + 0050): tot el que l'analisi llegeix, i nomes aixo. Les cites
(`tender_source_refs`) en queden fora: canviar nomes d'on surt un valor no
canvia el que el plec exigeix. Els conjunts entren ordenats i sense duplicats
semantics; els ids es substitueixen per claus naturals (codi de bloc, identitat
d'una seu, `dedup_key` d'un requisit o criteri); els nombres (int, float, Decimal)
son un objecte etiquetat `{"$num": "<decimal canonic>"}`; dates, booleans i nuls tenen una sola
representacio.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any


def _norm(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


#: Clau de l'objecte que representa un nombre a la forma canonica. Un nombre mai es
#: igual a cap cadena (`20` != `"20"`), i com que les claus d'usuari que comencen per
#: "$" s'escapen, cap dict ni cap valor d'usuari pot produir `{"$num": ...}`.
NUMBER_TAG = "$num"


def _number(value: Decimal | int | float) -> str:
    """Una sola cadena decimal per a qualsevol nombre: 20, 20.0 i Decimal("20.00")
    son "20". Un float entra per la seva representacio mes curta (0.1 -> "0.1"), no
    per l'expansio binaria. NaN i infinits no tenen forma canonica: es rebutgen."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"nombre no finit a l'empremta: {value!r}")
        value = Decimal(repr(value))
    elif isinstance(value, int):
        value = Decimal(value)
    elif not value.is_finite():
        raise ValueError(f"nombre no finit a l'empremta: {value!r}")
    if value == 0:
        return "0"
    # Sense perdre digits: la normalitzacio per defecte arrodoneix a 28 xifres.
    digits = len(value.as_tuple().digits)
    with localcontext() as ctx:
        ctx.prec = max(28, digits + 1)
        return format(value.normalize(), "f")


def _encode(value: Any, *, canonical_numbers: bool) -> Any:
    def rec(v: Any) -> Any:
        return _encode(v, canonical_numbers=canonical_numbers)

    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal) and not canonical_numbers:
        return format(value.normalize(), "f")
    if canonical_numbers and isinstance(value, (int, float, Decimal)):
        return {NUMBER_TAG: _number(value)}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: str(kv[0]))
        if not canonical_numbers:
            return {str(k): rec(v) for k, v in items}
        # Una clau que comenca per "$" s'escapa (prefix "$"): cap dict d'usuari no pot
        # ser igual a un nombre etiquetat.
        return {(f"${k}" if str(k).startswith("$") else str(k)): rec(v) for k, v in items}
    if isinstance(value, (list, tuple)):
        return [rec(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (rec(v) for v in value),
            key=lambda v: json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
    return value


def jsonable(value: Any) -> Any:
    """Forma canonica per a empremtes: tot nombre (int, float, Decimal) es un unic
    `{"$num": "20"}` (20, 20.0 i Decimal("20.00") coincideixen) i mai es igual a la
    cadena "20"; un boolea no es un nombre."""
    return _encode(value, canonical_numbers=True)


def plain_jsonable(value: Any) -> Any:
    """Per a JSON que es desa i es mostra (`support_json`): els int i float es
    conserven com a nombres, nomes el Decimal passa a text. No serveix per a empremtes."""
    return _encode(value, canonical_numbers=False)


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


# --------------------------------------------------------------------------- #
# Estat del paquet documental (3B-2 §4.4)                                         #
# --------------------------------------------------------------------------- #

#: `package_status` es la COBERTURA del paquet: `incomplete` nomes si hi ha algun forat
#: de cobertura obert (sabem que un document forma part del paquet public i Tuvi no el
#: te); si no, `complete` amb passada de classificacio vigent i `unknown` sense.
#: Que un document present no es pugui llegir (format, extraccio fallida, sense text)
#: es una altra dimensio, la PROCESSABILITAT (`PROCESSING_GAP_CODES`): no fa mai el
#: paquet incomplet.
PACKAGE_COMPLETE = "complete"
PACKAGE_INCOMPLETE = "incomplete"
PACKAGE_UNKNOWN = "unknown"

#: `section_status` de cada seccio de l'estructura.
SECTION_UNKNOWN = "unknown"
SECTION_NONE_DECLARED = "none_declared"
SECTION_PRESENT = "present"

#: Les seccions amb estat propi, amb els noms que fa servir `would_provide`.
PACKAGE_SECTIONS = (
    "blocks",
    "sites",
    "scope_items",
    "territories",
    "award_criteria",
    "contract_facts",
)


@dataclass(frozen=True, slots=True)
class PackageGapView:
    """Un forat OBERT del paquet (`tender_package_gaps` sense `resolved_at`).

    La vista nomes porta forats oberts: un forat resolt no es carrega mai, i per aixo
    no hi ha cap camp `resolved`. `would_provide` diu quines seccions (o
    `requirements`) aportaria el document que falta; buit vol dir impacte desconegut.
    """

    code: str
    detail: dict[str, Any] | None = None
    would_provide: tuple[str, ...] = ()
    producer: str | None = None

    @property
    def key(self) -> str | None:
        value = (self.detail or {}).get("key")
        return _norm(str(value)) if value is not None else None

    def canonical(self) -> dict[str, Any]:
        """Nomes el que diu que falta: el codi, la clau i que aportaria. El `detail`
        sencer (cites, fragments) i el productor en queden fora: d'on surt la troballa
        no canvia el que falta al plec."""
        return {
            "code": self.code,
            "key": self.key,
            "would_provide": sorted(set(self.would_provide)),
        }


#: Forats de PROCESSABILITAT: el document hi es, pero Tuvi encara no en pot llegir
#: (tot) el contingut. No son cap document que falti.
PROCESSING_GAP_CODES = frozenset({"no_text", "format_not_analyzable"})


def is_coverage_gap(gap: Any) -> bool:
    """Un forat de COBERTURA: falta un document que sabem que forma part del paquet.

    Qualsevol codi que no sigui de processabilitat ho es (rol obligatori que falta,
    document citat que falta, annex que falta, forat declarat per l'operador), tambe
    un codi que encara no coneguem: davant del dubte, el paquet no es diu complet.
    """
    return str(gap.code) not in PROCESSING_GAP_CODES


def coverage_gaps(gaps: Any) -> tuple[Any, ...]:
    return tuple(gap for gap in gaps if is_coverage_gap(gap))


def processing_gaps(gaps: Any) -> tuple[Any, ...]:
    return tuple(gap for gap in gaps if not is_coverage_gap(gap))


def section_presence(view: TenderStructureView) -> dict[str, bool]:
    """Si cada seccio te alguna fila. Els territoris son els dels requisits."""
    return {
        "blocks": bool(view.blocks),
        "sites": bool(view.sites),
        "scope_items": bool(view.scope_items),
        "territories": any(r.territories for r in view.requirements),
        "award_criteria": bool(view.criteria),
        "contract_facts": bool(view.facts),
    }


#: Codis de forat que, amb impacte desconegut (`would_provide` buit), poden afectar
#: QUALSEVOL seccio buida: falta un document del plec (citat, obligatori) o el seu
#: text. Un format que no sabem llegir (`format_not_analyzable`) o un forat declarat
#: per l'operador sense `would_provide` fan el paquet incomplet, pero no deixen cap
#: seccio `unknown`: nomes en surt la troballa.
IMPACT_UNKNOWN_BROADENS = frozenset(
    {"referenced_document_missing", "required_role_missing", "no_text"}
)


def section_status_for(presence: dict[str, bool], gaps: Any, *, has_run: bool) -> dict[str, str]:
    """L'estat de cada seccio.

    * `present`: te files. Les files son fets citats: un forat que nomena la seccio no
      les desfa (en surt igualment la troballa i el paquet es `incomplete`).
    * `unknown`: buida i, o un forat obert la nomena a `would_provide`, o un forat
      d'`IMPACT_UNKNOWN_BROADENS` te impacte desconegut, o no hi ha cap passada de
      classificacio vigent. Mai `none_declared` en aquests casos.
    * `none_declared`: buida, amb passada vigent i cap forat que la pugui afectar.
    """
    gaps = tuple(gaps)
    named = {section for gap in gaps for section in gap.would_provide}
    broadens = any(not gap.would_provide and gap.code in IMPACT_UNKNOWN_BROADENS for gap in gaps)
    status: dict[str, str] = {}
    for section in PACKAGE_SECTIONS:
        if presence.get(section, False):
            status[section] = SECTION_PRESENT
        elif section in named or broadens or not has_run:
            status[section] = SECTION_UNKNOWN
        else:
            status[section] = SECTION_NONE_DECLARED
    return status


def package_status_for(gaps: Any, *, has_run: bool) -> str:
    """La cobertura del paquet. Els forats de cobertura oberts son fets (tambe en una
    licitacio manual, que no te mai passada global): amb algun, `incomplete`. Sense
    cap, `complete` amb passada vigent i `unknown` sense.

    Els forats de processabilitat (`PROCESSING_GAP_CODES`) no hi compten: un document
    present i verificat tanca el forat documental encara que no en tinguem text."""
    if coverage_gaps(gaps):
        return PACKAGE_INCOMPLETE
    return PACKAGE_COMPLETE if has_run else PACKAGE_UNKNOWN


def gap_section_status(view: TenderStructureView) -> dict[str, str]:
    """L'estat de les seccions que es deriva NOMES dels forats i de les files: el que
    entra a l'empremta i el que el motor llegeix. La manca d'una passada de
    classificacio no hi compta (no es una propietat del plec)."""
    return section_status_for(section_presence(view), view.gaps, has_run=True)


def gap_unknown_sections(view: TenderStructureView) -> frozenset[str]:
    """Les seccions `unknown` per culpa d'un forat obert. Buit si no n'hi ha cap."""
    if not view.gaps:
        return frozenset()
    return frozenset(s for s, st in gap_section_status(view).items() if st == SECTION_UNKNOWN)


@dataclass(frozen=True, slots=True)
class TenderStructureView:
    tender_id: str
    duration_months: int | None = None
    extension_months: int | None = None
    regulated_harmonised: bool | None = None
    planned_start_date: date | None = None
    #: Data de referencia de les finestres temporals de la qualificacio (0052): el
    #: termini de presentacio (`tenders.deadline_at`, en UTC). Nul = el plec no la dona.
    submission_deadline: date | None = None
    blocks: tuple[BlockView, ...] = ()
    sites: tuple[SiteView, ...] = ()
    scope_items: tuple[ScopeItemView, ...] = ()
    requirements: tuple[RequirementView, ...] = ()
    criteria: tuple[CriterionView, ...] = ()
    facts: tuple[FactView, ...] = ()
    #: Nomes per a la relectura: no entra al fingerprint.
    source: dict[str, Any] = field(default_factory=dict)
    #: Estat del paquet documental (3B-2 §4.4). Vegeu `package_status_for`.
    package_status: str = PACKAGE_UNKNOWN
    #: Els forats OBERTS del paquet (mai els resolts).
    gaps: tuple[PackageGapView, ...] = ()
    #: Seccio -> `unknown` | `none_declared` | `present`. Vegeu `section_status_for`.
    section_status: dict[str, str] = field(default_factory=dict)

    def canonical(self) -> dict[str, Any]:
        """La forma canonica de l'estructura.

        L'estat del paquet hi entra NOMES si hi ha algun forat obert (precedent de
        `submission_deadline`): un plec sense forats conserva exactament l'empremta
        d'abans. I aleshores hi entra el que es deriva dels forats i de les files
        (`package_status` de cobertura, els forats en forma canonica i
        `gap_section_status`), mai la presencia o absencia d'una passada de
        classificacio global: l'empremta descriu que exigeix el plec i que hi falta,
        i que encara no s'hagi classificat no es cap propietat del plec. Per aixo
        `package_status = unknown` per si sol (sense passada, sense forats) no canvia
        res.
        """

        def as_set(items: Any) -> list[dict[str, Any]]:
            unique = {canonical_json(item.canonical()): item.canonical() for item in items}
            return [unique[k] for k in sorted(unique)]

        canonical = {
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
        # Nomes quan hi es: un plec sense termini conserva l'empremta d'abans.
        if self.submission_deadline is not None:
            canonical["submission_deadline"] = self.submission_deadline
        # Nomes amb forats oberts: vegeu el docstring. L'estat es la cobertura: amb
        # nomes forats de processabilitat el paquet es `complete` (els forats hi entren
        # igualment, i les seccions que deixen `unknown` tambe).
        if self.gaps:
            canonical["package_status"] = package_status_for(self.gaps, has_run=True)
            canonical["gaps"] = as_set(self.gaps)
            canonical["section_status"] = gap_section_status(self)
        return canonical


def structure_fingerprint(view: TenderStructureView) -> str:
    """SHA-256 de la forma canonica. Mateixa semantica, mateix hash."""
    return sha256_of(view.canonical())


# --------------------------------------------------------------------------- #
# inputs_hash                                                                   #
# --------------------------------------------------------------------------- #

#: 3B-2 §3.2: la classificacio que llegeix l'analisi ja no es la passada de
#: l'empresa sino la GLOBAL (`global_classification_run_id`) mes els overrides de
#: rol de l'empresa ancorats a la versio del fitxer (`role_overrides_hash`).
INPUT_FIELDS = (
    "tender_revision_id",
    "extraction_run_id",
    "global_classification_run_id",
    "role_overrides_hash",
    "review_revision_hash",
    "tender_structure_fingerprint",
    "knowledge_fingerprint",
    "rules_version",
)


def inputs_hash(
    *,
    tender_revision_id: str | None,
    extraction_run_id: str | None,
    global_classification_run_id: str | None,
    role_overrides_hash: str | None,
    review_revision_hash: str | None,
    tender_structure_fingerprint: str,
    knowledge_fingerprint: str,
    rules_version: str,
) -> str:
    """EXACTAMENT aquests vuit inputs, en forma canonica. Un `None` es un `None`."""
    return sha256_of(
        {
            "tender_revision_id": tender_revision_id,
            "extraction_run_id": extraction_run_id,
            "global_classification_run_id": global_classification_run_id,
            "role_overrides_hash": role_overrides_hash,
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
