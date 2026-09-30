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


@dataclass(frozen=True, slots=True)
class ServiceIn:
    prov: Provenance
    name: str
    service_line_code: str | None
    delivery_mode: str | None = None


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
    )


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
            pending.append(
                PendingItem(
                    PendingKind.ACCREDITATION_PENDING,
                    kind,
                    key,
                    "Marcat verificat pero sense cap evidencia enllacada"
                    if prov.verified_without_evidence
                    else "Declarat sense evidencia verificada",
                    prov.record_id,
                )
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
        payload = {"value": fact.value, "detail": fact.detail}
        if counted("operating_fact", fact.prov, fact.key, payload):
            fact_claims.setdefault(fact.key, []).append((_claim(fact.prov, fact.value), 0))

    service_claims: dict[str, list[tuple[Claim, int]]] = {}
    for service in inputs.services:
        if service.service_line_code is None:
            continue
        payload = {"name": service.name, "delivery_mode": service.delivery_mode}
        if counted("service", service.prov, service.service_line_code, payload):
            service_claims.setdefault(service.service_line_code, []).append(
                (_claim(service.prov, {"name": service.name, "mode": service.delivery_mode}), 0)
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
        payload = {"line": role.service_line_code, "territory": role.territory}
        if counted("partner_role", role.prov, f"{partner.name} / {role.role}", payload):
            roles_by_partner.setdefault(role.partner_id, []).append(
                {
                    "role": str(role.role),
                    "line": role.service_line_code,
                    "territory": role.territory,
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
