"""La fotografia efectiva del coneixement d'una empresa. Pura i determinista.

Rep les files de coneixement (ja carregades, d'una sola empresa) i una data de
referencia, i retorna el que val. No toca cap sessio ni cap xarxa.

**Precedencia**, per a cada clau de coneixement (una norma, un fet, una
capacitat...):

1. **actual verificat**: `lifecycle = current`, vigent a la data,
   `verification_status = verified` **i amb evidencia enllacada** (un `verified`
   sense cap document es compta com a declarat: la regla es a
   `app/services/knowledge_evidence.py`);
2. **actual declarat**: igual pero sense verificar. Pot satisfer un requisit,
   amb `accreditation_pending = true`;
3. **historic**: nomes observacions `candidate`. Es mostren com a pistes
   (`hints`) i generen preguntes. **No poden satisfer res mai**;
4. **desconegut**: no hi ha res. L'absencia no vol dir "no": vol dir `unknown`.

Una fila `in_progress`, `retired` o caducada (`valid_until` anterior a la data)
no compta; les dues primeres i la caducada surten a `pending_validation`.

**Abast d'una certificacio.** Es compara nomes el que s'ha estructurat
(`scope_service_lines`). Sense estructurar, l'abast es `scope_unknown`: el text
lliure no es llegeix per deduir-ne cap cobertura.

**Empremta.** `fingerprint` es l'SHA-256 de les files actuals que compten, amb
els seus valors i la seva procedencia, en ordre canonic. No inclou
l'historic: canviar una observacio no canvia el que l'empresa pot acreditar, i
per tant no ha de fer caducar cap avaluacio feta amb aquesta fotografia.

**Cobertura territorial** (0047). Un fet, un servei o un paper de partner
poden dir on son certs (`Coverage`: completesa + arees). L'estat neutre
`unknown` sense cap area es el que tenia tot abans de la 0047 i **s'omet del
payload canonic**: la migracio no canvia cap empremta. Qualsevol altre estat
(`complete` o `partial`, amb arees o sense) hi entra, amb aquests camps i prou:
`completeness` i, per area, `kind`, `label` (espais normalitzats), `code` i
`code_system`, ordenades per (kind, code_system, code, label). Aqui nomes es
conserva la dada; comparar-la amb un plec es cosa del comparador territorial.

**Qualificacio i mitjans** (0052). Exercicis economics, referencies d'execucio
(projectes; mai `historic_bids`), assegurances amb els seus limits, registres
amb abast estructurat, persones amb perfils del vocabulari i equips amb
calibratge entren a la fotografia amb la mateixa precedencia i a l'empremta amb
els seus valors. Una empresa sense cap fila d'aquestes no canvia d'empremta; un
fet boolea sense nombre, tampoc. Les avaluacions (`evaluate_turnover`,
`evaluate_execution`, `evaluate_insurance`, `evaluate_registration`,
`evaluate_profiles`, `evaluate_equipment`, `evaluate_numeric_fact`) nomes
comparen el que hi ha: «no consta» es `unknown`, i disponible no vol dir adscrit.
Les finestres temporals (exercicis, anys d'execucio) no depenen mai del rellotge:
o de la dada mateixa o d'una data de referencia del plec.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.knowledge.coverage import (
    NEUTRAL_COVERAGE,
    CoverageIn,
    canonical_coverage,
)
from app.knowledge.vocabulary import CAPABILITIES_BY_CODE, STANDARDS_BY_CODE
from app.models.enums import (
    CapabilityComparator,
    CapabilityStatus,
    CapabilityValueType,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    ObservationStatus,
    ObservationSubject,
    VerificationStatus,
)


class KnowledgeSource(StrEnum):
    CURRENT_VERIFIED = "current_verified"
    CURRENT_DECLARED = "current_declared"
    HISTORIC = "historic"
    UNKNOWN = "unknown"


class EvaluationResult(StrEnum):
    SATISFIES = "satisfies"
    DOES_NOT_SATISFY = "does_not_satisfy"
    UNKNOWN = "unknown"


class PendingKind(StrEnum):
    #: Dada actual declarada sense evidencia verificada.
    ACCREDITATION_PENDING = "accreditation_pending"
    #: Certificacio amb l'abast sense estructurar: no se sap quines linies cobreix.
    SCOPE_UNKNOWN = "scope_unknown"
    #: En tramit: no compta fins que sigui `current`.
    IN_PROGRESS = "in_progress"
    #: Era actual pero ha caducat.
    EXPIRED = "expired"
    #: Suggeriment de l'historic que ningu no ha validat ni descartat.
    HISTORIC_CANDIDATE = "historic_candidate"


_PENDING_ORDER = {kind: index for index, kind in enumerate(PendingKind)}


# --------------------------------------------------------------------------- #
# Entrada                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Provenance:
    record_id: str
    origin: KnowledgeOrigin
    as_of: date
    valid_until: date | None
    verification: VerificationStatus
    lifecycle: KnowledgeLifecycle
    #: Te almenys un document enllacat com a evidencia.
    has_evidence: bool = False

    def counts(self, on: date) -> bool:
        return self.lifecycle == KnowledgeLifecycle.CURRENT and not self.expired(on)

    def expired(self, on: date) -> bool:
        return self.valid_until is not None and self.valid_until < on

    @property
    def source(self) -> KnowledgeSource:
        if self.verification == VerificationStatus.VERIFIED and self.has_evidence:
            return KnowledgeSource.CURRENT_VERIFIED
        return KnowledgeSource.CURRENT_DECLARED

    @property
    def verified_without_evidence(self) -> bool:
        return self.verification == VerificationStatus.VERIFIED and not self.has_evidence

    def canonical(self) -> dict[str, Any]:
        return {
            "id": self.record_id,
            "origin": str(self.origin),
            "as_of": self.as_of.isoformat(),
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "verification": str(self.verification),
            "evidence": self.has_evidence,
        }


@dataclass(frozen=True, slots=True)
class CertificationIn:
    prov: Provenance
    name: str
    standard_code: str | None
    level: str | None
    #: Linies de servei que cobreix, estructurades. `None` = abast desconegut.
    scope: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class FactIn:
    prov: Provenance
    key: str
    value: bool
    detail: str | None = None
    coverage: CoverageIn = NEUTRAL_COVERAGE
    #: Nomes als fets quantitatius (0052). Nul als booleans: no deixa rastre.
    value_number: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ServiceIn:
    prov: Provenance
    name: str
    service_line_code: str | None
    delivery_mode: str | None = None
    coverage: CoverageIn = NEUTRAL_COVERAGE


@dataclass(frozen=True, slots=True)
class SolutionIn:
    prov: Provenance
    name: str
    service_line_code: str
    vendor: str | None = None
    edition: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityIn:
    prov: Provenance
    solution_id: str
    capability_code: str
    status: CapabilityStatus
    value_number: Decimal | None = None
    value_level: str | None = None


@dataclass(frozen=True, slots=True)
class ProductIn:
    prov: Provenance
    manufacturer: str
    model: str
    service_line_code: str | None = None

    @property
    def label(self) -> str:
        return f"{self.manufacturer} {self.model}"


@dataclass(frozen=True, slots=True)
class SolutionProductIn:
    prov: Provenance
    solution_id: str
    product_id: str


@dataclass(frozen=True, slots=True)
class PartnerIn:
    prov: Provenance
    name: str


@dataclass(frozen=True, slots=True)
class PartnerRoleIn:
    prov: Provenance
    partner_id: str
    role: str
    service_line_code: str | None = None
    territory: str | None = None
    coverage: CoverageIn = NEUTRAL_COVERAGE


@dataclass(frozen=True, slots=True)
class FinancialYearIn:
    prov: Provenance
    year: int
    revenue: Decimal | None
    currency: str = "EUR"


@dataclass(frozen=True, slots=True)
class ExecutionReferenceIn:
    """Un projecte executat. NO es cap oferta historica (`historic_bids`)."""

    prov: Provenance
    name: str
    status: str
    amount: Decimal | None
    currency: str = "EUR"
    client_type: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    service_lines: tuple[str, ...] = ()
    #: Imports EXECUTATS per any (any, import). Buit = sense desglossament.
    annual_amounts: tuple[tuple[int, Decimal], ...] = ()


@dataclass(frozen=True, slots=True)
class InsuranceIn:
    prov: Provenance
    insurance_type: str
    provider: str | None = None
    currency: str = "EUR"
    #: (tipus de limit, import). Cap entrada = no declarat, que no es «zero».
    limits: tuple[tuple[str, Decimal], ...] = ()
    additional_insured: bool | None = None


@dataclass(frozen=True, slots=True)
class RegistrationIn:
    prov: Provenance
    name: str
    registration_type: str
    issuer: str | None = None
    registration_number: str | None = None
    #: Abast estructurat. `None` = sense estructurar (desconegut).
    scope: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class PersonIn:
    """Sense nom: a la fotografia nomes hi entra el que es compara."""

    prov: Provenance
    availability: str
    profile_codes: tuple[str, ...] | None = None
    years_experience: int | None = None
    role: str | None = None


@dataclass(frozen=True, slots=True)
class EquipmentIn:
    prov: Provenance
    equipment_type: str
    quantity: int = 1
    manufacturer: str | None = None
    model: str | None = None
    serial_reference: str | None = None
    access_mode: str | None = None
    calibration_required: bool | None = None
    calibration_valid_until: date | None = None


@dataclass(frozen=True, slots=True)
class ObservationIn:
    observation_id: str
    subject: ObservationSubject
    subject_key: str
    statement: str
    status: ObservationStatus
    proposal: dict[str, Any] = field(default_factory=dict)
    observed_on: date | None = None
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeInputs:
    company_id: str
    certifications: Sequence[CertificationIn] = ()
    facts: Sequence[FactIn] = ()
    services: Sequence[ServiceIn] = ()
    solutions: Sequence[SolutionIn] = ()
    capabilities: Sequence[CapabilityIn] = ()
    products: Sequence[ProductIn] = ()
    solution_products: Sequence[SolutionProductIn] = ()
    partners: Sequence[PartnerIn] = ()
    partner_roles: Sequence[PartnerRoleIn] = ()
    observations: Sequence[ObservationIn] = ()
    # Qualificacio i mitjans (0052).
    financial_years: Sequence[FinancialYearIn] = ()
    execution_references: Sequence[ExecutionReferenceIn] = ()
    insurances: Sequence[InsuranceIn] = ()
    registrations: Sequence[RegistrationIn] = ()
    people: Sequence[PersonIn] = ()
    equipment: Sequence[EquipmentIn] = ()


# --------------------------------------------------------------------------- #
# Sortida                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Claim:
    """Una afirmacio actual que compta, amb la seva font."""

    record_id: str
    source: KnowledgeSource
    value: Any
    as_of: date
    valid_until: date | None
    #: Nomes per a certificacions: linies cobertes, i si l'abast es estructurat
    #: (`structured`) o no se sap (`scope_unknown`). Nuls per a la resta.
    scope: tuple[str, ...] | None = None
    scope_status: str | None = None
    #: Te algun document enllacat. Una dada declarada amb evidencia esta a un pas
    #: de verificar-se; sense evidencia, primer cal aportar-ne.
    has_evidence: bool = False
    #: Cobertura territorial, en la mateixa forma canonica que l'empremta.
    #: `None` = l'estat neutre (res dit), igual que abans de la 0047.
    coverage: dict[str, Any] | None = None
    #: Nomes als fets quantitatius (0052): quants.
    number: Decimal | None = None

    @property
    def accreditation_pending(self) -> bool:
        return self.source == KnowledgeSource.CURRENT_DECLARED


@dataclass(frozen=True, slots=True)
class Hint:
    """El que diu l'historic. Informacio, mai acreditacio."""

    observation_id: str
    statement: str
    observed_on: date | None
    source_ref: str | None


@dataclass(frozen=True, slots=True)
class Entry:
    key: str
    claims: tuple[Claim, ...]
    hints: tuple[Hint, ...]

    @property
    def effective(self) -> Claim | None:
        return self.claims[0] if self.claims else None

    @property
    def source(self) -> KnowledgeSource:
        if self.claims:
            return self.claims[0].source
        return KnowledgeSource.HISTORIC if self.hints else KnowledgeSource.UNKNOWN

    @property
    def accreditation_pending(self) -> bool:
        return self.effective is not None and self.effective.accreditation_pending


@dataclass(frozen=True, slots=True)
class PendingItem:
    kind: PendingKind
    subject: str
    key: str
    detail: str
    record_id: str | None = None
    observation_id: str | None = None
    #: Per a un candidat historic: hi ha coneixement actual sobre la mateixa clau,
    #: que mana. El candidat nomes pot confirmar-lo o contradir-lo.
    superseded_by_current: bool = False


@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    company_id: str
    evaluated_on: date
    certifications: dict[str, Entry]
    facts: dict[str, Entry]
    services: dict[str, Entry]
    solutions: dict[str, Entry]
    capabilities: dict[str, Entry]
    products: dict[str, Entry]
    partners: dict[str, Entry]
    pending_validation: tuple[PendingItem, ...]
    fingerprint: str
    #: Per avaluar capacitats: solucions historiques per linia de servei.
    historic_solutions_by_line: dict[str, tuple[Hint, ...]] = field(default_factory=dict)
    #: Qualificacio i mitjans (0052): per any, per projecte (id), per tipus de polissa,
    #: per tipus de registre, per perfil professional i per tipus d'equip.
    financial_years: dict[str, Entry] = field(default_factory=dict)
    execution_references: dict[str, Entry] = field(default_factory=dict)
    insurances: dict[str, Entry] = field(default_factory=dict)
    registrations: dict[str, Entry] = field(default_factory=dict)
    profiles: dict[str, Entry] = field(default_factory=dict)
    equipment: dict[str, Entry] = field(default_factory=dict)

    def claim_by_record(self, record_id: str) -> Claim | None:
        """La reclamacio d'una fila concreta, sigui del tipus que sigui."""
        for entries in (
            self.certifications, self.facts, self.services, self.solutions, self.capabilities,
            self.products, self.partners, self.financial_years, self.execution_references,
            self.insurances, self.registrations, self.profiles, self.equipment,
        ):  # fmt: skip
            for entry in entries.values():
                for claim in entry.claims:
                    if claim.record_id == record_id:
                        return claim
        return None


# --------------------------------------------------------------------------- #
# Construccio                                                                  #
# --------------------------------------------------------------------------- #


def _claim_order(claim: Claim, rank: int = 0) -> tuple:
    verified_first = 0 if claim.source == KnowledgeSource.CURRENT_VERIFIED else 1
    return (verified_first, -rank, -claim.as_of.toordinal(), claim.record_id)


def _hint(observation: ObservationIn) -> Hint:
    return Hint(
        observation_id=observation.observation_id,
        statement=observation.statement,
        observed_on=observation.observed_on,
        source_ref=observation.source_ref,
    )


def _entries(
    claims_by_key: dict[str, list[tuple[Claim, int]]],
    hints_by_key: dict[str, list[Hint]],
) -> dict[str, Entry]:
    keys = sorted(set(claims_by_key) | set(hints_by_key))
    return {
        key: Entry(
            key=key,
            claims=tuple(
                claim
                for claim, _ in sorted(
                    claims_by_key.get(key, []), key=lambda item: _claim_order(*item)
                )
            ),
            hints=tuple(sorted(hints_by_key.get(key, []), key=lambda h: h.observation_id)),
        )
        for key in keys
    }


def _claim(prov: Provenance, value: Any) -> Claim:
    return Claim(
        record_id=prov.record_id,
        source=prov.source,
        value=value,
        as_of=prov.as_of,
        valid_until=prov.valid_until,
        has_evidence=prov.has_evidence,
    )


def _coverage_payload(coverage: dict[str, Any] | None) -> dict[str, Any]:
    """Nomes hi ha clau `coverage` quan hi ha cobertura: l'estat neutre no deixa rastre."""
    return {"coverage": coverage} if coverage is not None else {}


def _level_rank(standard_code: str | None, level: str | None) -> int:
    standard = STANDARDS_BY_CODE.get(standard_code or "")
    if standard is None or standard.levels is None or level is None:
        return -1
    return standard.rank(level)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def build_snapshot(inputs: KnowledgeInputs, *, on: date) -> KnowledgeSnapshot:
    pending: list[PendingItem] = []
    canonical: list[dict[str, Any]] = []

    def counted(kind: str, prov: Provenance, key: str, payload: dict[str, Any]) -> bool:
        """Registra la fila a l'empremta i als pendents. Cert si compta."""
        if prov.lifecycle == KnowledgeLifecycle.IN_PROGRESS:
            pending.append(
                PendingItem(PendingKind.IN_PROGRESS, kind, key, "En tramit", prov.record_id)
            )
            return False
        if prov.lifecycle != KnowledgeLifecycle.CURRENT:
            return False
        if prov.expired(on):
            pending.append(
                PendingItem(
                    PendingKind.EXPIRED,
                    kind,
                    key,
                    f"Caducat el {prov.valid_until.isoformat()}",  # type: ignore[union-attr]
                    prov.record_id,
                )
            )
            return False
        canonical.append(
            {
                "kind": kind,
                "key": key,
                **prov.canonical(),
                **{k: _jsonable(v) for k, v in payload.items()},
            }
        )
        if prov.source == KnowledgeSource.CURRENT_DECLARED:
            if prov.verified_without_evidence:
                detail = "Marcat verificat pero sense cap evidencia enllacada"
            elif prov.has_evidence:
                detail = "Amb evidencia enllacada, pendent de verificar"
            else:
                detail = "Declarat sense evidencia verificada"
            pending.append(
                PendingItem(PendingKind.ACCREDITATION_PENDING, kind, key, detail, prov.record_id)
            )
        return True

    # Certificacions: una entrada per norma; les que no en tenen, pel nom.
    cert_claims: dict[str, list[tuple[Claim, int]]] = {}
    for cert in inputs.certifications:
        key = cert.standard_code or f"name:{cert.name}"
        scope = tuple(sorted(cert.scope)) if cert.scope is not None else None
        payload = {
            "standard": cert.standard_code,
            "level": cert.level,
            "name": cert.name,
            "scope": list(scope) if scope is not None else None,
        }
        if counted("certification", cert.prov, key, payload):
            rank = _level_rank(cert.standard_code, cert.level)
            claim = replace(
                _claim(cert.prov, cert.level),
                scope=scope,
                scope_status="structured" if scope is not None else "scope_unknown",
            )
            cert_claims.setdefault(key, []).append((claim, rank))
            if scope is None:
                pending.append(
                    PendingItem(
                        PendingKind.SCOPE_UNKNOWN,
                        "certification",
                        key,
                        "Abast sense estructurar: no se sap quines linies de servei cobreix",
                        cert.prov.record_id,
                    )
                )

    fact_claims: dict[str, list[tuple[Claim, int]]] = {}
    for fact in inputs.facts:
        coverage = canonical_coverage(fact.coverage)
        payload = {"value": fact.value, "detail": fact.detail, **_coverage_payload(coverage)}
        if fact.value_number is not None:
            payload["number"] = fact.value_number
        if counted("operating_fact", fact.prov, fact.key, payload):
            fact_claims.setdefault(fact.key, []).append(
                (
                    replace(
                        _claim(fact.prov, fact.value), coverage=coverage, number=fact.value_number
                    ),
                    0,
                )
            )

    service_claims: dict[str, list[tuple[Claim, int]]] = {}
    for service in inputs.services:
        if service.service_line_code is None:
            continue
        coverage = canonical_coverage(service.coverage)
        payload = {
            "name": service.name,
            "delivery_mode": service.delivery_mode,
            **_coverage_payload(coverage),
        }
        if counted("service", service.prov, service.service_line_code, payload):
            service_claims.setdefault(service.service_line_code, []).append(
                (
                    replace(
                        _claim(service.prov, {"name": service.name, "mode": service.delivery_mode}),
                        coverage=coverage,
                    ),
                    0,
                )
            )

    solution_claims: dict[str, list[tuple[Claim, int]]] = {}
    live_solutions: dict[str, SolutionIn] = {}
    for solution in inputs.solutions:
        payload = {
            "line": solution.service_line_code,
            "vendor": solution.vendor,
            "edition": solution.edition,
        }
        if counted("solution", solution.prov, solution.name, payload):
            live_solutions[solution.prov.record_id] = solution
            solution_claims.setdefault(solution.name, []).append(
                (_claim(solution.prov, {"line": solution.service_line_code}), 0)
            )

    capability_claims: dict[str, list[tuple[Claim, int]]] = {}
    for cap in inputs.capabilities:
        solution = live_solutions.get(cap.solution_id)
        if solution is None:  # la solucio no compta: la capacitat tampoc
            continue
        payload = {
            "solution": solution.name,
            "status": cap.status,
            "number": cap.value_number,
            "level": cap.value_level,
        }
        if counted("solution_capability", cap.prov, cap.capability_code, payload):
            value = {
                "solution": solution.name,
                "status": str(cap.status),
                "number": cap.value_number,
                "level": cap.value_level,
            }
            capability_claims.setdefault(cap.capability_code, []).append(
                (_claim(cap.prov, value), 0)
            )

    product_claims: dict[str, list[tuple[Claim, int]]] = {}
    live_products: dict[str, ProductIn] = {}
    for product in inputs.products:
        payload = {"line": product.service_line_code}
        if counted("product", product.prov, product.label, payload):
            live_products[product.prov.record_id] = product
            product_claims.setdefault(product.label, []).append(
                (_claim(product.prov, {"line": product.service_line_code}), 0)
            )
    for link in inputs.solution_products:
        solution = live_solutions.get(link.solution_id)
        product = live_products.get(link.product_id)
        if solution is None or product is None:
            continue
        counted(
            "solution_product",
            link.prov,
            f"{solution.name} / {product.label}",
            {"solution": solution.name, "product": product.label},
        )

    # Un rol nomes compta si el soci compta: primer els socis, despres els rols.
    partner_claims: dict[str, list[tuple[Claim, int]]] = {}
    live_partners: dict[str, PartnerIn] = {
        partner.prov.record_id: partner
        for partner in inputs.partners
        if counted("partner", partner.prov, partner.name, {})
    }
    roles_by_partner: dict[str, list[dict[str, Any]]] = {}
    for role in inputs.partner_roles:
        partner = live_partners.get(role.partner_id)
        if partner is None:
            continue
        coverage = canonical_coverage(role.coverage)
        payload = {
            "line": role.service_line_code,
            "territory": role.territory,
            **_coverage_payload(coverage),
        }
        if counted("partner_role", role.prov, f"{partner.name} / {role.role}", payload):
            roles_by_partner.setdefault(role.partner_id, []).append(
                {
                    "role": str(role.role),
                    "line": role.service_line_code,
                    "territory": role.territory,
                    **_coverage_payload(coverage),
                }
            )
    for record_id, partner in live_partners.items():
        roles = sorted(
            roles_by_partner.get(record_id, []),
            key=lambda r: (r["role"], r["line"] or "", r["territory"] or ""),
        )
        partner_claims.setdefault(partner.name, []).append(
            (_claim(partner.prov, {"roles": roles}), 0)
        )

    # --- Qualificacio i mitjans (0052). Sense historic: cap pista.
    year_claims: dict[str, list[tuple[Claim, int]]] = {}
    for fy in inputs.financial_years:
        value = {"year": fy.year, "revenue": fy.revenue, "currency": fy.currency}
        if counted("financial_year", fy.prov, str(fy.year), dict(value)):
            year_claims.setdefault(str(fy.year), []).append((_claim(fy.prov, value), 0))

    reference_claims: dict[str, list[tuple[Claim, int]]] = {}
    for ref in inputs.execution_references:
        lines = tuple(sorted(set(ref.service_lines)))
        payload = {
            "name": ref.name,
            "status": ref.status,
            "amount": ref.amount,
            "currency": ref.currency,
            "client_type": ref.client_type,
            "start": ref.start_date,
            "end": ref.end_date,
            "lines": list(lines),
        }
        annual = {str(year): amount for year, amount in sorted(ref.annual_amounts)}
        if annual:
            payload["annual"] = annual
        if counted("execution_reference", ref.prov, ref.prov.record_id, payload):
            value = {
                "name": ref.name,
                "status": ref.status,
                "amount": ref.amount,
                "currency": ref.currency,
                "client_type": ref.client_type,
                "start_year": ref.start_date.year if ref.start_date else None,
                "end_year": ref.end_date.year if ref.end_date else None,
                "annual": {int(year): amount for year, amount in annual.items()},
                "lines": lines,
            }
            reference_claims.setdefault(ref.prov.record_id, []).append((_claim(ref.prov, value), 0))

    insurance_claims: dict[str, list[tuple[Claim, int]]] = {}
    for ins in inputs.insurances:
        limits = dict(sorted(ins.limits))
        value = {
            "provider": ins.provider,
            "currency": ins.currency,
            "limits": limits,
            "additional_insured": ins.additional_insured,
        }
        if counted("insurance", ins.prov, ins.insurance_type, dict(value)):
            insurance_claims.setdefault(ins.insurance_type, []).append((_claim(ins.prov, value), 0))

    registration_claims: dict[str, list[tuple[Claim, int]]] = {}
    for reg in inputs.registrations:
        scope = tuple(sorted(reg.scope)) if reg.scope is not None else None
        payload = {
            "name": reg.name,
            "issuer": reg.issuer,
            "number": reg.registration_number,
            "scope": list(scope) if scope is not None else None,
        }
        if counted("registration", reg.prov, reg.registration_type, payload):
            registration_claims.setdefault(reg.registration_type, []).append(
                (
                    replace(
                        _claim(reg.prov, {"name": reg.name, "issuer": reg.issuer}),
                        scope=scope,
                        scope_status="structured" if scope is not None else "scope_unknown",
                    ),
                    0,
                )
            )
            if scope is None:
                pending.append(
                    PendingItem(
                        PendingKind.SCOPE_UNKNOWN,
                        "registration",
                        reg.registration_type,
                        "Abast sense estructurar: no se sap quins serveis inscriu",
                        reg.prov.record_id,
                    )
                )

    profile_claims: dict[str, list[tuple[Claim, int]]] = {}
    for person in inputs.people:
        codes = tuple(sorted(set(person.profile_codes))) if person.profile_codes else None
        payload = {
            "availability": person.availability,
            "profiles": list(codes) if codes is not None else None,
            "years_experience": person.years_experience,
            "role": person.role,
        }
        if counted("person", person.prov, person.prov.record_id, payload):
            value = {
                "person": person.prov.record_id,
                "availability": person.availability,
                "years_experience": person.years_experience,
            }
            for code in codes or ():
                profile_claims.setdefault(code, []).append((_claim(person.prov, value), 0))

    equipment_claims: dict[str, list[tuple[Claim, int]]] = {}
    for unit in inputs.equipment:
        value = {
            "manufacturer": unit.manufacturer,
            "model": unit.model,
            "serial": unit.serial_reference,
            "access_mode": unit.access_mode,
            "quantity": unit.quantity,
            "calibration_required": unit.calibration_required,
            "calibration_valid_until": unit.calibration_valid_until,
        }
        if counted("equipment", unit.prov, unit.equipment_type, dict(value)):
            equipment_claims.setdefault(unit.equipment_type, []).append(
                (_claim(unit.prov, value), 0)
            )

    # Historic: nomes candidats, i nomes com a pistes.
    hints: dict[ObservationSubject, dict[str, list[Hint]]] = {s: {} for s in ObservationSubject}
    historic_solutions: dict[str, list[Hint]] = {}
    current_keys = {
        ObservationSubject.CERTIFICATION: cert_claims,
        ObservationSubject.OPERATING_FACT: fact_claims,
        ObservationSubject.SERVICE: service_claims,
        ObservationSubject.SOLUTION: solution_claims,
        ObservationSubject.PRODUCT: product_claims,
        ObservationSubject.PARTNER: partner_claims,
    }
    for observation in inputs.observations:
        if observation.status != ObservationStatus.CANDIDATE:
            continue
        subject = ObservationSubject(observation.subject)
        hint = _hint(observation)
        hints[subject].setdefault(observation.subject_key, []).append(hint)
        if subject == ObservationSubject.SOLUTION:
            line = observation.proposal.get("service_line_code")
            if line:
                historic_solutions.setdefault(line, []).append(hint)
        pending.append(
            PendingItem(
                PendingKind.HISTORIC_CANDIDATE,
                str(subject),
                observation.subject_key,
                observation.statement,
                observation_id=observation.observation_id,
                superseded_by_current=observation.subject_key in current_keys[subject],
            )
        )

    canonical.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()

    pending.sort(
        key=lambda p: (
            _PENDING_ORDER[p.kind],
            p.subject,
            p.key,
            p.record_id or "",
            p.observation_id or "",
        )
    )
    return KnowledgeSnapshot(
        company_id=inputs.company_id,
        evaluated_on=on,
        certifications=_entries(cert_claims, hints[ObservationSubject.CERTIFICATION]),
        facts=_entries(fact_claims, hints[ObservationSubject.OPERATING_FACT]),
        services=_entries(service_claims, hints[ObservationSubject.SERVICE]),
        solutions=_entries(solution_claims, hints[ObservationSubject.SOLUTION]),
        capabilities=_entries(capability_claims, {}),
        products=_entries(product_claims, hints[ObservationSubject.PRODUCT]),
        partners=_entries(partner_claims, hints[ObservationSubject.PARTNER]),
        pending_validation=tuple(pending),
        fingerprint=fingerprint,
        historic_solutions_by_line={
            line: tuple(sorted(items, key=lambda h: h.observation_id))
            for line, items in sorted(historic_solutions.items())
        },
        financial_years=_entries(year_claims, {}),
        execution_references=_entries(reference_claims, {}),
        insurances=_entries(insurance_claims, {}),
        registrations=_entries(registration_claims, {}),
        profiles=_entries(profile_claims, {}),
        equipment=_entries(equipment_claims, {}),
    )


# --------------------------------------------------------------------------- #
# Avaluacio contra un requisit                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Evaluation:
    result: EvaluationResult
    source: KnowledgeSource
    reason: str
    accreditation_pending: bool = False
    record_ids: tuple[str, ...] = ()
    hints: tuple[Hint, ...] = ()


def _decide(
    claims: Iterable[Claim], meets, hints: tuple[Hint, ...], *, has_data: bool
) -> Evaluation:
    """`meets(claim)` diu True/False, o None si la dada no permet decidir."""
    claims = list(claims)
    verdicts = [(claim, meets(claim)) for claim in claims]
    for wanted in (KnowledgeSource.CURRENT_VERIFIED, KnowledgeSource.CURRENT_DECLARED):
        winners = [c for c, ok in verdicts if ok and c.source == wanted]
        if winners:
            return Evaluation(
                result=EvaluationResult.SATISFIES,
                source=wanted,
                reason="satisfied_verified"
                if wanted == KnowledgeSource.CURRENT_VERIFIED
                else "satisfied_declared",
                accreditation_pending=wanted == KnowledgeSource.CURRENT_DECLARED,
                record_ids=tuple(c.record_id for c in winners),
                hints=hints,
            )
    failing = [c for c, ok in verdicts if ok is False]
    if failing:
        return Evaluation(
            result=EvaluationResult.DOES_NOT_SATISFY,
            source=failing[0].source,
            reason="current_does_not_meet",
            record_ids=tuple(c.record_id for c in failing),
            hints=hints,
        )
    if has_data:
        return Evaluation(
            result=EvaluationResult.UNKNOWN,
            source=claims[0].source,
            reason="current_value_missing",
            record_ids=tuple(c.record_id for c in claims),
            hints=hints,
        )
    # Sense dada actual: l'historic, si n'hi ha, nomes informa.
    return Evaluation(
        result=EvaluationResult.UNKNOWN,
        source=KnowledgeSource.HISTORIC if hints else KnowledgeSource.UNKNOWN,
        reason="historic_only" if hints else "no_current_data",
        hints=hints,
    )


def evaluate_certification(
    snapshot: KnowledgeSnapshot,
    standard_code: str,
    min_level: str | None = None,
    service_lines: Sequence[str] | None = None,
) -> Evaluation:
    """Si l'empresa te la norma, al nivell i (si es demana) amb l'abast requerits.

    Amb `service_lines`, cada linia ha de ser a l'abast ESTRUCTURAT del certificat.
    Un abast sense estructurar dona `unknown` amb motiu `scope_unknown`: el text
    lliure no es llegeix per deduir-ne cobertura.
    """
    standard = STANDARDS_BY_CODE[standard_code]
    required = standard.rank(min_level) if min_level is not None else None
    wanted = set(service_lines or ())
    entry = snapshot.certifications.get(standard_code)
    claims = entry.claims if entry else ()
    hints = entry.hints if entry else ()

    def verdict(claim: Claim) -> tuple[bool | None, str | None]:
        if required is not None:
            if claim.value is None:
                return None, "level_missing"  # certificat de la norma sense nivell
            if standard.rank(claim.value) < required:
                return False, "level_below"
        if wanted:
            if claim.scope is None:
                return None, "scope_unknown"
            if not wanted <= set(claim.scope):
                return False, "scope_not_covered"
        return True, None

    verdicts = [(claim, *verdict(claim)) for claim in claims]
    for source in (KnowledgeSource.CURRENT_VERIFIED, KnowledgeSource.CURRENT_DECLARED):
        winners = [c for c, ok, _ in verdicts if ok and c.source == source]
        if winners:
            return Evaluation(
                result=EvaluationResult.SATISFIES,
                source=source,
                reason="satisfied_verified"
                if source == KnowledgeSource.CURRENT_VERIFIED
                else "satisfied_declared",
                accreditation_pending=source == KnowledgeSource.CURRENT_DECLARED,
                record_ids=tuple(c.record_id for c in winners),
                hints=hints,
            )
    for outcome, result in (
        (False, EvaluationResult.DOES_NOT_SATISFY),
        (None, EvaluationResult.UNKNOWN),
    ):
        matching = [(c, why) for c, ok, why in verdicts if ok is outcome]
        if matching:
            return Evaluation(
                result=result,
                source=matching[0][0].source,
                reason=matching[0][1] or "current_does_not_meet",
                record_ids=tuple(c.record_id for c, _ in matching),
                hints=hints,
            )
    return Evaluation(
        result=EvaluationResult.UNKNOWN,
        source=KnowledgeSource.HISTORIC if hints else KnowledgeSource.UNKNOWN,
        reason="historic_only" if hints else "no_current_data",
        hints=hints,
    )


def evaluate_fact(snapshot: KnowledgeSnapshot, key: str, expected: bool = True) -> Evaluation:
    entry = snapshot.facts.get(key)
    claims = entry.claims if entry else ()
    hints = entry.hints if entry else ()
    return _decide(claims, lambda claim: claim.value is expected, hints, has_data=bool(claims))


def evaluate_capability(
    snapshot: KnowledgeSnapshot, code: str, required: Decimal | int | str | None = None
) -> Evaluation:
    definition = CAPABILITIES_BY_CODE[code]
    if definition.value_type != CapabilityValueType.BOOLEAN and required is None:
        raise ValueError(f"{code} necessita un valor requerit.")
    entry = snapshot.capabilities.get(code)
    claims = entry.claims if entry else ()
    hints = snapshot.historic_solutions_by_line.get(definition.service_line_code, ())

    def meets(claim: Claim) -> bool | None:
        status = claim.value["status"]
        if status == CapabilityStatus.NOT_SUPPORTED:
            return False
        if definition.value_type == CapabilityValueType.BOOLEAN:
            return True
        if definition.value_type == CapabilityValueType.NUMBER:
            number = claim.value["number"]
            if number is None:
                return None
            if definition.comparator == CapabilityComparator.GTE:
                return Decimal(number) >= Decimal(str(required))
            return Decimal(number) <= Decimal(str(required))
        level = claim.value["level"]
        if level is None:
            return None
        return definition.rank(level) >= definition.rank(str(required))

    evaluation = _decide(claims, meets, hints, has_data=bool(claims))
    limited = [
        c
        for c in claims
        if c.record_id in evaluation.record_ids
        and c.value["status"] == CapabilityStatus.SUPPORTED_WITH_LIMITS
    ]
    if evaluation.result == EvaluationResult.SATISFIES and len(limited) == len(
        evaluation.record_ids
    ):
        return Evaluation(
            result=evaluation.result,
            source=evaluation.source,
            reason="satisfied_with_limits",
            accreditation_pending=evaluation.accreditation_pending,
            record_ids=evaluation.record_ids,
            hints=evaluation.hints,
        )
    return evaluation


# --------------------------------------------------------------------------- #
# Qualificacio i mitjans (0052)                                                 #
# --------------------------------------------------------------------------- #
#
# Cap d'aquestes avaluacions inventa res: «no consta» es `unknown`. Nomes es diu
# `does_not_satisfy` quan la dada actual, completa, no arriba (tots els exercicis
# de la finestra per sota, un limit declarat per sota, un calibratge caducat).


def _source_of(claims: Sequence[Claim]) -> KnowledgeSource:
    if claims and all(c.source == KnowledgeSource.CURRENT_VERIFIED for c in claims):
        return KnowledgeSource.CURRENT_VERIFIED
    return KnowledgeSource.CURRENT_DECLARED


def _satisfied(claims: Sequence[Claim]) -> Evaluation:
    source = _source_of(claims)
    return Evaluation(
        result=EvaluationResult.SATISFIES,
        source=source,
        reason="satisfied_verified"
        if source == KnowledgeSource.CURRENT_VERIFIED
        else "satisfied_declared",
        accreditation_pending=source == KnowledgeSource.CURRENT_DECLARED,
        record_ids=tuple(c.record_id for c in claims),
    )


def _nothing(reason: str = "no_current_data") -> Evaluation:
    return Evaluation(
        result=EvaluationResult.UNKNOWN, source=KnowledgeSource.UNKNOWN, reason=reason
    )


def _verified_first(claims: Sequence[Claim]) -> list[Claim]:
    return sorted(claims, key=_claim_order)


def evaluate_turnover(
    snapshot: KnowledgeSnapshot,
    minimum: Decimal,
    years: int = 3,
    reference_date: date | None = None,
) -> Evaluation:
    """Volum anual de negoci: el millor dels `years` ULTIMS EXERCICIS >= `minimum` (EUR).

    «Ultims» es relatiu al plec, no a la base: sense data de referencia (el termini de
    presentacio) no se sap quins exercicis son els exigibles i el resultat es
    `unknown` (`reference_date_missing`). Mai el rellotge ni «els mes recents que hi
    ha»: un plec antic no es pot satisfer amb exercicis posteriors.

    Un exercici es una fila de `company_financial_years` (l'any de l'exercici), i
    nomes compta si es dada actual. Els exigibles son els `years` mes recents
    ANTERIORS a l'any de referencia, consecutius:

    * si no hi ha `years` exercicis consecutius que acabin al mes recent, no es pot
      demostrar quins son els exigibles: `unknown` (`exercises_incomplete`), encara
      que algun hi arribi;
    * algun dels exigibles hi arriba: `satisfies` (el millor; verificat abans);
    * tots tres coneguts i cap hi arriba: `does_not_satisfy`;
    * un import que falta o una altra moneda: `unknown`.
    Un exercici antic, fora dels exigibles, no pot satisfer mai.
    """
    current = [e.effective for e in snapshot.financial_years.values() if e.effective]
    if not current:
        return _nothing()
    if reference_date is None:
        return Evaluation(
            result=EvaluationResult.UNKNOWN,
            source=_source_of(current),
            reason="reference_date_missing",
            record_ids=tuple(c.record_id for c in current),
        )
    represented: dict[int, Claim] = {}
    for claim in current:
        year = int(claim.value["year"])
        if year >= reference_date.year:
            continue  # exercici no tancat a la data de referencia
        represented[year] = claim
    if not represented:
        return _nothing("none_before_reference")
    latest = max(represented)
    exigible = [represented.get(latest - offset) for offset in range(years)]
    known = [c for c in exigible if c is not None]
    if len(known) < years:
        return Evaluation(
            result=EvaluationResult.UNKNOWN,
            source=_source_of(known),
            reason="exercises_incomplete",
            record_ids=tuple(c.record_id for c in known),
        )
    comparable = [
        c for c in known if c.value["revenue"] is not None and c.value["currency"] == "EUR"
    ]
    meeting = [c for c in comparable if Decimal(c.value["revenue"]) >= minimum]
    if meeting:
        best = max(
            meeting,
            key=lambda c: (
                Decimal(c.value["revenue"]),
                c.source == KnowledgeSource.CURRENT_VERIFIED,
                c.record_id,
            ),
        )
        return _satisfied([best])
    if len(comparable) == years:
        return Evaluation(
            result=EvaluationResult.DOES_NOT_SATISFY,
            source=_source_of(comparable),
            reason="turnover_below",
            record_ids=tuple(c.record_id for c in comparable),
        )
    reason = (
        "revenue_missing"
        if any(c.value["revenue"] is None for c in known)
        else "currency_not_comparable"
    )
    return Evaluation(
        result=EvaluationResult.UNKNOWN,
        source=_source_of(known),
        reason=reason,
        record_ids=tuple(c.record_id for c in known),
    )


def evaluate_execution(
    snapshot: KnowledgeSnapshot,
    minimum: Decimal,
    years: int = 3,
    service_lines: Sequence[str] | None = None,
    client_types: Sequence[str] | None = None,
    reference_date: date | None = None,
) -> Evaluation:
    """Referencies d'execucio: l'ANY DE MAJOR EXECUCIO dels `years` darrers >= `minimum`.

    La finestra son els `years` anys naturals anteriors a l'any de la data de
    referencia (el termini de presentacio del plec). Sense data de referencia, no
    se sap quins anys son «els darrers»: `unknown` (`reference_date_missing`). Cap
    rellotge.

    La unitat material es l'IMPORT EXECUTAT en un any (`company_project_annual_amounts`),
    no l'estat del projecte: un projecte en curs compta pels anys que ja ha executat i
    acreditat. Per any de la finestra se sumen els imports executats d'aquell any dels
    projectes rellevants (linies de servei i tipus de client, si es demanen). Sense
    desglossament, nomes un projecte `completed` que cau sencer dins d'un sol any
    aporta el seu import a aquell any. NO compten: el total d'un plurianual sense
    desglossament, l'import contractat d'un projecte en curs o cancel·lat sense
    execucio anual, ni un projecte `planned` (cap execucio).

    * algun any hi arriba: `satisfies` (els projectes d'aquell any);
    * cap any hi arriba pero hi ha imports que no es poden imputar (o que no
      consten) i que podrien tocar la finestra: `unknown` (`amount_unallocated`);
    * cap projecte rellevant a la finestra: `unknown` (`none_in_window`);
    * el conjunt es complet i cap any hi arriba: `does_not_satisfy`.
    Les ofertes historiques no hi son: no acrediten res.
    """
    wanted_lines = set(service_lines or ())
    wanted_clients = set(client_types or ())
    references = [e.effective for e in snapshot.execution_references.values() if e.effective]
    relevant = [
        c
        for c in references
        if c.value["status"] != "planned"
        and (not wanted_lines or wanted_lines & set(c.value["lines"]))
        and (not wanted_clients or c.value["client_type"] in wanted_clients)
    ]
    if not relevant:
        return _nothing("none_relevant" if references else "no_current_data")
    if reference_date is None:
        return Evaluation(
            result=EvaluationResult.UNKNOWN,
            source=_source_of(relevant),
            reason="reference_date_missing",
            record_ids=tuple(c.record_id for c in relevant),
        )
    window = range(reference_date.year - years, reference_date.year)

    def may_touch_window(value: dict[str, Any]) -> bool:
        first = value["start_year"] or value["end_year"]
        last = value["end_year"] or value["start_year"]
        if first is None:
            return True  # sense dates: no es pot descartar
        return any(year in window for year in range(first, last + 1))

    totals = {year: Decimal(0) for year in window}
    contributors: dict[int, list[Claim]] = {year: [] for year in window}
    unallocated: list[Claim] = []
    for claim in relevant:
        value = claim.value
        if value["currency"] != "EUR":
            if may_touch_window(value):
                unallocated.append(claim)
            continue
        annual: dict[int, Any] = value["annual"]
        for year, amount in annual.items():
            if year in window:
                totals[year] += Decimal(amount)
                contributors[year].append(claim)
        if annual:
            allocated = sum((Decimal(a) for a in annual.values()), Decimal(0))
            rest = None if value["amount"] is None else Decimal(value["amount"]) - allocated
            if rest is not None and rest > 0 and may_touch_window(value):
                unallocated.append(claim)  # part sense desglossar
            continue
        if value["amount"] is None:
            if may_touch_window(value):
                unallocated.append(claim)
            continue
        single = value["start_year"] is not None and value["start_year"] == value["end_year"]
        if single and value["status"] == "completed":
            if value["start_year"] in window:
                totals[value["start_year"]] += Decimal(value["amount"])
                contributors[value["start_year"]].append(claim)
        elif may_touch_window(value):
            # Plurianual, o en curs/cancel·lat, sense execucio anual: l'import es
            # contractat, no executat d'un any concret. No s'inventa; deixa obert.
            unallocated.append(claim)
    best = max(window, key=lambda year: (totals[year], year))
    if totals[best] >= minimum:
        return _satisfied(contributors[best])
    if unallocated:
        return Evaluation(
            result=EvaluationResult.UNKNOWN,
            source=_source_of(relevant),
            reason="amount_unallocated",
            record_ids=tuple(c.record_id for c in unallocated),
        )
    counted_claims = [c for year in window for c in contributors[year]]
    if not counted_claims:
        return _nothing("none_in_window")
    return Evaluation(
        result=EvaluationResult.DOES_NOT_SATISFY,
        source=_source_of(counted_claims),
        reason="execution_below",
        record_ids=tuple(dict.fromkeys(c.record_id for c in counted_claims)),
    )


def evaluate_insurance(
    snapshot: KnowledgeSnapshot, insurance_type: str, required: dict[str, Decimal]
) -> Evaluation:
    """Una polissa vigent del tipus amb cada limit exigit declarat i >= l'import (EUR).

    Un limit que no consta deixa la polissa `unknown`: l'absencia d'un sublimit no es
    cap «zero». Un limit declarat per sota es `does_not_satisfy`.
    """
    entry = snapshot.insurances.get(insurance_type)
    claims = entry.claims if entry else ()

    def meets(claim: Claim) -> bool | None:
        if claim.value["currency"] != "EUR":
            return None
        limits = claim.value["limits"]
        if any(limits.get(k) is not None and Decimal(limits[k]) < v for k, v in required.items()):
            return False
        if any(limits.get(k) is None for k in required):
            return None
        return True

    return _decide(claims, meets, (), has_data=bool(claims))


def _norm_issuer(text: str | None) -> str | None:
    return " ".join(text.casefold().split()) if text else None


def evaluate_registration(
    snapshot: KnowledgeSnapshot,
    registration_type: str,
    service_lines: Sequence[str] | None = None,
    issuer: str | None = None,
) -> Evaluation:
    """Una inscripcio vigent del tipus i, si es demana, amb l'abast ESTRUCTURAT que
    cobreix cada linia. Un abast sense estructurar es `scope_unknown`; un abast que
    no cobreix alguna linia es `scope_not_covered`: tots dos `unknown`, per revisar
    (ampliar una inscripcio es un tramit, no una incompatibilitat)."""
    wanted = set(service_lines or ())
    entry = snapshot.registrations.get(registration_type)
    claims = entry.claims if entry else ()
    wanted_issuer = _norm_issuer(issuer)
    if wanted_issuer is not None:
        # Un registre d'un altre emissor no es el que es demana; un sense emissor, no se sap.
        claims = tuple(
            c for c in claims if _norm_issuer(c.value["issuer"]) in (None, wanted_issuer)
        )

    def verdict(claim: Claim) -> tuple[bool | None, str | None]:
        if wanted_issuer is not None and claim.value["issuer"] is None:
            return None, "issuer_unknown"
        if not wanted:
            return True, None
        if claim.scope is None:
            return None, "scope_unknown"
        if wanted <= set(claim.scope):
            return True, None
        return None, "scope_not_covered"

    verdicts = [(claim, *verdict(claim)) for claim in claims]
    for source in (KnowledgeSource.CURRENT_VERIFIED, KnowledgeSource.CURRENT_DECLARED):
        winners = [c for c, ok, _ in verdicts if ok and c.source == source]
        if winners:
            return _satisfied(winners)
    open_ones = [(c, why) for c, ok, why in verdicts if ok is None]
    if open_ones:
        return Evaluation(
            result=EvaluationResult.UNKNOWN,
            source=open_ones[0][0].source,
            reason=open_ones[0][1] or "current_value_missing",
            record_ids=tuple(c.record_id for c, _ in open_ones),
        )
    return _nothing()


#: Disponibilitats que compten per cobrir un perfil. Disponible no vol dir adscrit.
AVAILABLE_FOR_PROFILE = frozenset({"available", "partially_available"})


def profile_people(snapshot: KnowledgeSnapshot, code: str) -> tuple[Claim, ...]:
    """Les persones actuals amb el perfil i disponibles (verificades primer)."""
    entry = snapshot.profiles.get(code)
    claims = entry.claims if entry else ()
    return tuple(c for c in claims if c.value["availability"] in AVAILABLE_FOR_PROFILE)


def evaluate_profiles(snapshot: KnowledgeSnapshot, required: dict[str, int]) -> Evaluation:
    """Cada perfil amb almenys N persones disponibles. Mai `does_not_satisfy`: que no
    hi hagi ningu avui no diu que no n'hi pugui haver (contractar es possible)."""
    counted: list[Claim] = []
    missing = False
    for code, n in required.items():
        people = profile_people(snapshot, code)
        if len(people) >= n:
            counted.extend(people[:n])
        else:
            missing = True
    if not missing:
        return _satisfied(counted)
    return Evaluation(
        result=EvaluationResult.UNKNOWN,
        source=_source_of(counted) if counted else KnowledgeSource.UNKNOWN,
        reason="profiles_missing",
        record_ids=tuple(c.record_id for c in counted),
    )


def equipment_units(
    snapshot: KnowledgeSnapshot, equipment_type: str, *, calibration_required: bool
) -> dict[str, list[Claim]]:
    """Els equips actuals del tipus, classificats: `ok`, `unsure` (sense data de
    calibratge, o sense dir si en cal) i `expired` (calibratge caducat)."""
    entry = snapshot.equipment.get(equipment_type)
    claims = entry.claims if entry else ()
    out: dict[str, list[Claim]] = {"ok": [], "unsure": [], "expired": []}
    for claim in claims:
        if not calibration_required:
            out["ok"].append(claim)
            continue
        needs, until = claim.value["calibration_required"], claim.value["calibration_valid_until"]
        if needs is not True or until is None:
            out["unsure"].append(claim)
        elif until >= snapshot.evaluated_on:
            out["ok"].append(claim)
        else:
            out["expired"].append(claim)
    return out


def evaluate_equipment(
    snapshot: KnowledgeSnapshot, required: dict[str, int], *, calibration_required: bool
) -> Evaluation:
    """Cada tipus amb almenys N unitats actuals i, si cal, amb calibratge vigent.

    `does_not_satisfy` nomes si d'algun tipus TOT el que hi ha te el calibratge
    caducat; si falta dada (sense data, sense dir si en cal, o cap equip), `unknown`.
    """
    counted: list[Claim] = []
    expired: list[Claim] = []
    unsure = False
    missing = False
    for equipment_type, n in required.items():
        units = equipment_units(snapshot, equipment_type, calibration_required=calibration_required)
        if sum(c.value["quantity"] for c in units["ok"]) >= n:
            counted.extend(units["ok"])
        elif units["unsure"]:
            unsure = True
        elif units["expired"]:
            expired.extend(units["expired"])
        else:
            missing = True
    if not (unsure or missing or expired):
        return _satisfied(counted)
    if expired and not unsure and not missing:
        return Evaluation(
            result=EvaluationResult.DOES_NOT_SATISFY,
            source=_source_of(expired),
            reason="calibration_expired",
            record_ids=tuple(c.record_id for c in expired),
        )
    return Evaluation(
        result=EvaluationResult.UNKNOWN,
        source=_source_of(counted) if counted else KnowledgeSource.UNKNOWN,
        reason="calibration_unknown" if unsure else "equipment_missing",
        record_ids=tuple(c.record_id for c in counted + expired),
    )


def evaluate_numeric_fact(
    snapshot: KnowledgeSnapshot, key: str, comparator: str, required: Decimal
) -> Evaluation:
    """Un fet quantitatiu (`value_number`) contra un llindar: `gte`, `lte` o `eq`.
    Un fet negat (`value = false`) no hi arriba; un fet afirmat sense nombre no es
    pot comparar (`unknown`)."""
    entry = snapshot.facts.get(key)
    claims = entry.claims if entry else ()
    hints = entry.hints if entry else ()

    def meets(claim: Claim) -> bool | None:
        if claim.value is False:
            return False
        if claim.number is None:
            return None
        number = Decimal(claim.number)
        if comparator == "gte":
            return number >= required
        if comparator == "lte":
            return number <= required
        if comparator == "eq":
            return number == required
        return None

    return _decide(claims, meets, hints, has_data=bool(claims))
