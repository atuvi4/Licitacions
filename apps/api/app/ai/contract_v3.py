"""Extraction Contract v3: validacio pura del suport i de l'adjudicacio. **No connectat.**

Cap perfil (`app/ai/profiles.py`) ni cap servei no fa servir aquest modul. Les
columnes que n'han de guardar el resultat ja existeixen (0038, 0039), pero
res no les escriu encara. Els esquemes de sortida son a `app.ai.schemas_v3`.

Nivells d'evidencia, de menys a mes:

* `QUOTE_VALID`: la cita existeix literalment a la pagina (`locate_quote`, com a
  la v1). **No prova cap valor.**
* `CLAIM_SUPPORTED`: almenys un claim esta sostingut: una cita valida el declara
  a `supports`, el valor hi es amb la seva unitat, el terme que diu de que es el
  valor (`anchor`) hi es, i al mateix tros de la cita no hi ha cap altre valor
  del mateix tipus que el contradigui.
* `REQUIREMENT_SUPPORTED`: TOTS els claims sostinguts i `claims_complete` es
  `complete`. Parcial o desconegut no es complet.

Si una cosa no es pot provar literalment (una parafrasi, un booleà, un numero
sense ancora, dos valors al mateix tros) el resultat es `unverified`, que va a
revisio. **Mai `supported`.** Aqui no hi ha ni fuzzy ni LLM.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from app.ai import claim_matching as cm
from app.ai.schemas_v3 import (
    AdjudicationBasis,
    AwardCandidateKind,
    AwardCandidateOutV3,
    ChunkExtractionOutV3,
    ClaimCertainty,
    ClaimKind,
    ClaimOutV3,
    ClaimUnit,
    Completeness,
    RequirementOutV3,
)

#: `Requirement.value_schema` d'un requisit v3: `value_json` son els claims.
VALUE_SCHEMA_V3 = "requirements-claims-v3"
EVIDENCE_SUPPORT_CONTRACT = "claim-support-v1"
ADJUDICATION_BASIS_CONTRACT = "adjudication-basis-v1"


# --------------------------------------------------------------------------- #
# Claims                                                                       #
# --------------------------------------------------------------------------- #


class ClaimCheck(StrEnum):
    """Resultat de comprovar UN claim contra UNA cita que el declara."""

    SUPPORTED = "supported"
    #: El valor no hi es, ni amb una altra unitat. La cita no el sosté.
    VALUE_NOT_IN_QUOTE = "value_not_in_quote"
    #: El numero hi es, pero no amb la unitat del claim (`600` sense `Mbps`).
    UNIT_NOT_IN_QUOTE = "unit_not_in_quote"
    #: Un claim numeric sense `anchor`: no es pot saber de que es el numero.
    ANCHOR_MISSING = "anchor_missing"
    #: El numero hi es, el terme no: pot ser una coincidencia.
    ANCHOR_NOT_IN_QUOTE = "anchor_not_in_quote"
    #: Terme i valor hi son, pero no en un tros sense cap altre valor rival.
    BINDING_AMBIGUOUS = "binding_ambiguous"
    #: Text parafrasejat o booleà: no es pot provar literalment.
    SEMANTIC_UNVERIFIED = "semantic_unverified"
    #: El claim no te valor (`certainty = unknown`): no hi ha res a sostenir.
    UNKNOWN_VALUE = "unknown_value"


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    #: No provat ni refutat: a revisio.
    UNVERIFIED = "unverified"
    #: Cap cita valida el declara, o les que el declaren no contenen el valor.
    NOT_SUPPORTED = "not_supported"
    #: Valor desconegut. No es "fals" ni "no sostingut".
    UNKNOWN = "unknown"


_STATUS_OF: dict[ClaimCheck, ClaimStatus] = {
    ClaimCheck.SUPPORTED: ClaimStatus.SUPPORTED,
    ClaimCheck.VALUE_NOT_IN_QUOTE: ClaimStatus.NOT_SUPPORTED,
    ClaimCheck.UNKNOWN_VALUE: ClaimStatus.UNKNOWN,
}
_RANK = {ClaimStatus.SUPPORTED: 2, ClaimStatus.UNVERIFIED: 1, ClaimStatus.NOT_SUPPORTED: 0}


def status_of(check: ClaimCheck) -> ClaimStatus:
    return _STATUS_OF.get(check, ClaimStatus.UNVERIFIED)


def check_claim(claim: ClaimOutV3, quote: str) -> ClaimCheck:
    """Comprova UN claim contra UNA cita ja validada literalment. Pur i determinista."""
    if claim.value is None or claim.certainty == ClaimCertainty.UNKNOWN:
        return ClaimCheck.UNKNOWN_VALUE
    if claim.kind == ClaimKind.BOOLEAN:
        return ClaimCheck.SEMANTIC_UNVERIFIED
    if claim.kind == ClaimKind.TEXT:
        present = cm.contains_term(cm.normalise(quote), str(claim.value))
        return ClaimCheck.SUPPORTED if present else ClaimCheck.SEMANTIC_UNVERIFIED
    return _check_numeric(claim, quote)


def _check_numeric(claim: ClaimOutV3, quote: str) -> ClaimCheck:
    number = float(claim.value)  # type: ignore[arg-type]
    wanted = cm.canonical(number, claim.unit)
    per_part = [(part, cm.occurrences(part)) for part in cm.segments(quote)]

    typed_hit = any(
        cm.same_value(t, wanted) for _, occs in per_part for t in cm.typed(occs, claim.kind)
    )
    if not typed_hit:
        loose_hit = any(abs(o.value - number) < 0.005 for _, occs in per_part for o in occs)
        return ClaimCheck.UNIT_NOT_IN_QUOTE if loose_hit else ClaimCheck.VALUE_NOT_IN_QUOTE
    if claim.anchor is None:
        return ClaimCheck.ANCHOR_MISSING
    if not cm.contains_term(cm.normalise(quote), claim.anchor):
        return ClaimCheck.ANCHOR_NOT_IN_QUOTE
    for part, occs in per_part:
        if not cm.contains_term(part, claim.anchor):
            continue
        values = cm.typed(occs, claim.kind)
        if values and all(cm.same_value(v, wanted) for v in values):
            return ClaimCheck.SUPPORTED
    return ClaimCheck.BINDING_AMBIGUOUS


class EvidenceLevel(StrEnum):
    QUOTE_VALID = "quote_valid"
    CLAIM_SUPPORTED = "claim_supported"
    REQUIREMENT_SUPPORTED = "requirement_supported"


class RequirementSupportStatus(StrEnum):
    SUPPORTED = "supported"
    #: Sense cap claim: la cita es valida, pero no prova cap valor. Com a v1.
    QUOTE_ONLY = "quote_only"
    NEEDS_REVIEW = "needs_review"
    NOT_SUPPORTED = "not_supported"


@dataclass(frozen=True, slots=True)
class RequirementSupport:
    #: `{evidence_id: {claim_id: ClaimCheck}}`, nomes les parelles declarades.
    per_evidence: dict[str, dict[str, ClaimCheck]]
    #: Estat final de cada claim: el millor entre les cites valides que el declaren.
    per_claim: dict[str, ClaimStatus]
    #: `{evidence_id: [refs]}` declarats a `supports` que no son cap claim.
    unknown_refs: dict[str, list[str]]
    level: EvidenceLevel
    status: RequirementSupportStatus

    @property
    def requirement_supported(self) -> bool:
        return self.level == EvidenceLevel.REQUIREMENT_SUPPORTED

    def claims_with(self, status: ClaimStatus) -> list[str]:
        return [key for key, value in self.per_claim.items() if value == status]


def requirement_support(
    requirement: RequirementOutV3, valid_quotes: Mapping[str, str]
) -> RequirementSupport:
    """Suport de cada claim a partir de les cites que han passat `locate_quote`.

    `valid_quotes` es `{evidence_id: text exacte localitzat}`. Una cita que no
    hi es (rebutjada abans) no sosté res, encara que ho declari.
    """
    claims = {claim.id: claim for claim in requirement.claims}
    per_evidence: dict[str, dict[str, ClaimCheck]] = {}
    unknown_refs: dict[str, list[str]] = {}
    per_claim: dict[str, ClaimStatus] = {}

    for evidence in requirement.evidence:
        quote = valid_quotes.get(evidence.id)
        if quote is None:
            continue
        checks: dict[str, ClaimCheck] = {}
        for ref in dict.fromkeys(evidence.supports):
            claim = claims.get(ref)
            if claim is None:
                unknown_refs.setdefault(evidence.id, []).append(ref)
                continue
            checks[ref] = check_claim(claim, quote)
        per_evidence[evidence.id] = checks

    for claim_id, claim in claims.items():
        if claim.value is None or claim.certainty == ClaimCertainty.UNKNOWN:
            per_claim[claim_id] = ClaimStatus.UNKNOWN
            continue
        statuses = [
            status_of(checks[claim_id]) for checks in per_evidence.values() if claim_id in checks
        ]
        per_claim[claim_id] = (
            max(statuses, key=_RANK.__getitem__) if statuses else ClaimStatus.NOT_SUPPORTED
        )

    values = list(per_claim.values())
    if not values:
        level, status = EvidenceLevel.QUOTE_VALID, RequirementSupportStatus.QUOTE_ONLY
    else:
        all_supported = all(v == ClaimStatus.SUPPORTED for v in values)
        if all_supported and requirement.claims_complete == Completeness.COMPLETE:
            level = EvidenceLevel.REQUIREMENT_SUPPORTED
        elif ClaimStatus.SUPPORTED in values:
            level = EvidenceLevel.CLAIM_SUPPORTED
        else:
            level = EvidenceLevel.QUOTE_VALID
        if level == EvidenceLevel.REQUIREMENT_SUPPORTED:
            status = RequirementSupportStatus.SUPPORTED
        elif ClaimStatus.NOT_SUPPORTED in values:
            status = RequirementSupportStatus.NOT_SUPPORTED
        else:
            status = RequirementSupportStatus.NEEDS_REVIEW

    return RequirementSupport(
        per_evidence=per_evidence,
        per_claim=per_claim,
        unknown_refs=unknown_refs,
        level=level,
        status=status,
    )


# --------------------------------------------------------------------------- #
# Criteris d'adjudicacio                                                       #
# --------------------------------------------------------------------------- #


class AwardClass(StrEnum):
    FORMAL_AWARD_CRITERION = "formal_award_criterion"
    #: Diu que es criteri, pero cap mecanisme no s'ha pogut provar: a revisio.
    CANDIDATE_AWARD_CRITERION = "candidate_award_criterion"
    TECHNICAL_DESIRABLE = "technical_desirable"
    INFORMATIVE = "informative"


class MechanismCheck(StrEnum):
    VERIFIED = "verified"
    #: Cap cita valida no declara aquest mecanisme a `supports`.
    NOT_EVIDENCED = "not_evidenced"
    #: Una cita el declara, pero el marcador del mecanisme no hi es.
    NOT_IN_QUOTE = "not_in_quote"
    #: `formula` declarada sense el text de la formula.
    MISSING_VALUE = "missing_value"


class AwardReason(StrEnum):
    MECHANISM_VERIFIED = "mechanism_verified"
    NO_MECHANISM_DECLARED = "no_mechanism_declared"
    NO_MECHANISM_VERIFIED = "no_mechanism_verified"
    #: El model diu "desitjable" o "informatiu" i una cita prova un mecanisme.
    KIND_CONFLICTS_WITH_MECHANISM = "kind_conflicts_with_mechanism"
    DECLARED_DESIRABLE = "declared_desirable"
    DECLARED_INFORMATIVE = "declared_informative"


# Marcadors literals, ja normalitzats (sense accents). "Es valorara" NO hi es a
# proposit: una frase del PPT que diu que una cosa es valorara no es cap mecanisme.
_CRITERIA_SECTION_MARKERS = (
    "criteris d'adjudicacio", "criterios de adjudicacion", "criteris de valoracio",
    "criterios de valoracion", "criteris avaluables", "criterios evaluables",
    "criteris de puntuacio", "criterios de puntuacion",
)
_OFFER_FORM_MARKERS = (
    "( )", "(  )", "[ ]", "☐", "□", "model d'oferta", "model de proposicio",
    "modelo de oferta", "modelo de proposicion", "sobre 2", "sobre num. 2", "sobre n. 2",
)
_POINTS = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s?(?:punts|punt|puntos|punto|pts?\.?)(?!\w)")


def _points_in(text: str) -> list[float]:
    values = (cm.parse_number(m.group(1)) for m in _POINTS.finditer(text))
    return [v for v in values if v is not None]


def _percents_in(text: str) -> list[float]:
    return [o.value for o in cm.occurrences(text) if o.unit == ClaimUnit.PERCENT]


def check_mechanism(
    basis: AdjudicationBasis, candidate: AwardCandidateOutV3, quote: str
) -> MechanismCheck:
    """Comprova UN mecanisme contra UNA cita valida que el declara."""
    text = cm.normalise(quote)
    if basis == AdjudicationBasis.POINTS:
        points = _points_in(text)
        ok = candidate.points in points if candidate.points is not None else bool(points)
    elif basis == AdjudicationBasis.WEIGHT:
        percents = _percents_in(text)
        ok = (
            candidate.weight_percent in percents
            if candidate.weight_percent is not None
            else bool(percents)
        )
    elif basis == AdjudicationBasis.FORMULA:
        if not (candidate.formula and candidate.formula.strip()):
            return MechanismCheck.MISSING_VALUE
        ok = cm.compact(candidate.formula) in cm.compact(quote)
    elif basis == AdjudicationBasis.CRITERIA_TABLE:
        # Una taula de puntuacio te, com a minim, dues puntuacions o pesos.
        ok = len(_points_in(text)) + len(_percents_in(text)) >= 2
    elif basis == AdjudicationBasis.OFFER_FORM:
        ok = any(marker in text for marker in _OFFER_FORM_MARKERS)
    else:  # CRITERIA_SECTION
        ok = any(marker in text for marker in _CRITERIA_SECTION_MARKERS)
    return MechanismCheck.VERIFIED if ok else MechanismCheck.NOT_IN_QUOTE


@dataclass(frozen=True, slots=True)
class AwardDecision:
    award_class: AwardClass
    #: `{mecanisme: (comprovacio, [ids de cita que el verifiquen])}`.
    mechanisms: dict[AdjudicationBasis, tuple[MechanismCheck, list[str]]]
    reasons: list[AwardReason] = field(default_factory=list)
    #: `{evidence_id: [refs]}` declarats a `supports` que no son cap mecanisme declarat.
    unknown_refs: dict[str, list[str]] = field(default_factory=dict)

    @property
    def needs_review(self) -> bool:
        return self.award_class == AwardClass.CANDIDATE_AWARD_CRITERION


def classify_award(
    candidate: AwardCandidateOutV3, valid_quotes: Mapping[str, str]
) -> AwardDecision:
    """Classe final d'un candidat. Mai no el descarta: com a molt, a revisio.

    Formal NOMES si almenys un mecanisme declarat queda VERIFICAT per una cita
    valida que el declara. Ni l'origen (PCAP) ni "es valorara" no hi compten.
    `valid_quotes` es `{evidence_id: text exacte localitzat}`.
    """
    declared = {basis.value for basis in candidate.adjudication_basis}
    mechanisms: dict[AdjudicationBasis, tuple[MechanismCheck, list[str]]] = {}
    unknown_refs: dict[str, list[str]] = {}

    for evidence in candidate.evidence:
        if evidence.id not in valid_quotes:
            continue
        for ref in evidence.supports:
            if ref not in declared:
                unknown_refs.setdefault(evidence.id, []).append(ref)

    for basis in candidate.adjudication_basis:
        declaring = [
            e.id for e in candidate.evidence if basis.value in e.supports and e.id in valid_quotes
        ]
        if not declaring:
            mechanisms[basis] = (MechanismCheck.NOT_EVIDENCED, [])
            continue
        checks = {eid: check_mechanism(basis, candidate, valid_quotes[eid]) for eid in declaring}
        verified = [eid for eid, check in checks.items() if check == MechanismCheck.VERIFIED]
        if verified:
            mechanisms[basis] = (MechanismCheck.VERIFIED, verified)
        else:
            mechanisms[basis] = (next(iter(checks.values())), [])

    any_verified = any(check == MechanismCheck.VERIFIED for check, _ in mechanisms.values())
    if candidate.kind == AwardCandidateKind.AWARD_CRITERION:
        if any_verified:
            award_class, reason = AwardClass.FORMAL_AWARD_CRITERION, AwardReason.MECHANISM_VERIFIED
        else:
            award_class = AwardClass.CANDIDATE_AWARD_CRITERION
            reason = (
                AwardReason.NO_MECHANISM_VERIFIED if declared else AwardReason.NO_MECHANISM_DECLARED
            )
    elif any_verified:
        # El model diu que no puntua, pero la cita prova un mecanisme: ni formal
        # per decisio del backend, ni amagat. A revisio.
        award_class = AwardClass.CANDIDATE_AWARD_CRITERION
        reason = AwardReason.KIND_CONFLICTS_WITH_MECHANISM
    elif candidate.kind == AwardCandidateKind.DESIRABLE_FEATURE:
        award_class, reason = AwardClass.TECHNICAL_DESIRABLE, AwardReason.DECLARED_DESIRABLE
    else:
        award_class, reason = AwardClass.INFORMATIVE, AwardReason.DECLARED_INFORMATIVE

    return AwardDecision(
        award_class=award_class, mechanisms=mechanisms, reasons=[reason], unknown_refs=unknown_refs
    )


# --------------------------------------------------------------------------- #
# Rebutjos                                                                     #
# --------------------------------------------------------------------------- #


class RejectionReason(StrEnum):
    """Per que un element, una cita o un claim no ha arribat sencer. Nomes codis."""

    SCHEMA_INVALID = "schema_invalid"
    #: El text citat no es a la pagina citada (`quote_not_found` a v1/v2).
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    PAGE_NOT_IN_CHUNK = "page_not_in_chunk"
    EXTRACTION_MISMATCH = "extraction_mismatch"
    NO_VALID_EVIDENCE = "no_valid_evidence"
    CLAIM_NOT_SUPPORTED = "claim_not_supported"
    DUPLICATE_KEY = "duplicate_key"


class RejectionStage(StrEnum):
    SCHEMA = "schema"
    EVIDENCE = "evidence"
    ITEM = "item"
    CLAIM = "claim"
    DEDUP = "dedup"


class RejectionItem(StrEnum):
    REQUIREMENT = "requirement"
    AWARD_CANDIDATE = "award_candidate"


#: Els codis v1/v2 (fase 1) llegits amb el vocabulari v3. Les files antigues no
#: es reescriuen: qui les llegeix les tradueix.
LEGACY_REASONS: dict[str, RejectionReason] = {
    "quote_not_found": RejectionReason.EVIDENCE_NOT_FOUND,
}

#: Quantes entrades es desen per chunk, com a molt (igual que la fase 1).
MAX_REJECTIONS_PER_CHUNK = 50

#: El vocabulari tancat de `detail`. Res que vingui del model hi pot entrar.
_DETAILS: frozenset[str] = frozenset(
    {c.value for c in ClaimCheck}
    | {c.value for c in MechanismCheck}
    | {
        "not_declared", "unknown_ref", "missing", "extra_forbidden", "enum", "value_error",
        "string_too_long", "string_too_short", "too_long", "string_pattern_mismatch",
        "string_type", "int_type", "float_type", "bool_type", "list_type", "dict_type",
        "model_type", "literal_error", "greater_than_equal", "other",
    }
)


def rejection(
    item: RejectionItem,
    index: int,
    stage: RejectionStage,
    reason: RejectionReason,
    *,
    claim: int | None = None,
    detail: str | None = None,
    field_name: str | None = None,
) -> dict[str, Any]:
    """Una entrada de diagnostic. Posicions i codis; mai text del plec ni del model.

    `claim` es la POSICIO del claim (no el seu id, que ve del model). `detail`
    nomes accepta el vocabulari tancat (la resta es `other`); `field_name`
    nomes l'omple qui el treu dels camps coneguts de l'esquema.
    """
    entry: dict[str, Any] = {
        "item": item.value, "index": int(index), "stage": stage.value, "reason": reason.value,
    }
    if claim is not None:
        entry["claim"] = int(claim)
    if detail is not None:
        entry["detail"] = detail if detail in _DETAILS else "other"
    if field_name is not None:
        entry["field"] = field_name
    return entry


class RejectionLog:
    """Acumula els rebutjos d'un chunk amb el limit aplicat; compta els que no hi caben."""

    def __init__(self, limit: int = MAX_REJECTIONS_PER_CHUNK) -> None:
        self.entries: list[dict[str, Any]] = []
        self.dropped = 0
        self._limit = limit

    def add(self, entry: dict[str, Any]) -> None:
        if len(self.entries) < self._limit:
            self.entries.append(entry)
        else:
            self.dropped += 1


def record_requirement_support(
    log: RejectionLog, index: int, requirement: RequirementOutV3, support: RequirementSupport
) -> None:
    """Claims no sostinguts i referencies desconegudes, com a rebutjos de claim.

    El claim NO s'esborra del requisit: queda amb el seu estat i el requisit va
    a revisio. El rebuig es el diagnostic, no una eliminacio.
    """
    position = {claim.id: n for n, claim in enumerate(requirement.claims)}
    for claim_id in support.claims_with(ClaimStatus.NOT_SUPPORTED):
        checks = [c[claim_id] for c in support.per_evidence.values() if claim_id in c]
        log.add(rejection(
            RejectionItem.REQUIREMENT, index, RejectionStage.CLAIM,
            RejectionReason.CLAIM_NOT_SUPPORTED,
            claim=position[claim_id],
            detail=checks[0].value if checks else "not_declared",
        ))
    for refs in support.unknown_refs.values():
        for _ref in refs:
            log.add(rejection(
                RejectionItem.REQUIREMENT, index, RejectionStage.CLAIM,
                RejectionReason.CLAIM_NOT_SUPPORTED, detail="unknown_ref",
            ))


# --------------------------------------------------------------------------- #
# Parse element a element                                                      #
# --------------------------------------------------------------------------- #

_ITEM_LISTS: tuple[tuple[str, type[BaseModel], RejectionItem], ...] = (
    ("requirements", RequirementOutV3, RejectionItem.REQUIREMENT),
    ("award_candidates", AwardCandidateOutV3, RejectionItem.AWARD_CANDIDATE),
)


def parse_chunk_v3(payload: Any) -> tuple[ChunkExtractionOutV3, RejectionLog]:
    """Valida el sobre estrictament i cada element per separat.

    Si el sobre no es valid (no es un objecte, claus de mes, llistes que no ho
    son), aixeca `ValidationError`: el chunk sencer es `schema_invalid`, com a
    v1. Si nomes un element es invalid, es descarta NOMES aquell, amb un rebuig
    `schema_invalid` que diu el tipus d'error i el camp de primer nivell. Mai el
    valor, i mai el nom d'una clau inventada.
    """
    result = parse_chunk_v3_result(payload)
    return result.output, result.schema_rejections


@dataclass(frozen=True, slots=True)
class ParsedChunkV3:
    """El que retorna `V3.parse`: la sortida valida i els elements descartats.

    Els elements invalids no hi son, aixi que la posicio a `output` ja no es la
    posicio a la resposta. `requirement_indexes[n]` i `award_indexes[n]` son la
    posicio ORIGINAL de l'element `n`: els rebutjos posteriors l'han de fer
    servir, o apuntarien a un altre element.
    """

    output: ChunkExtractionOutV3
    schema_rejections: RejectionLog
    requirement_indexes: tuple[int, ...] = ()
    award_indexes: tuple[int, ...] = ()

    @property
    def schema_rejected(self) -> int:
        return len(self.schema_rejections.entries) + self.schema_rejections.dropped


def parse_chunk_v3_result(payload: Any) -> ParsedChunkV3:
    """`parse_chunk_v3` en la forma que espera `ExtractorProfile.parse`."""
    envelope = payload
    if isinstance(payload, dict):
        envelope = {
            key: ([] if key in {k for k, _, _ in _ITEM_LISTS} and isinstance(value, list)
                  else value)
            for key, value in payload.items()
        }
    ChunkExtractionOutV3.model_validate(envelope)

    log = RejectionLog()
    kept: dict[str, list[BaseModel]] = {}
    indexes: dict[str, list[int]] = {}
    for key, model, item in _ITEM_LISTS:
        kept[key], indexes[key] = [], []
        for index, raw in enumerate(payload.get(key) or []):
            try:
                kept[key].append(model.model_validate(raw))
                indexes[key].append(index)
            except ValidationError as exc:
                log.add(_schema_rejection(item, index, exc, model))
    output = ChunkExtractionOutV3.model_validate({**envelope, **kept})
    return ParsedChunkV3(
        output=output,
        schema_rejections=log,
        requirement_indexes=tuple(indexes["requirements"]),
        award_indexes=tuple(indexes["award_candidates"]),
    )


def _schema_rejection(
    item: RejectionItem, index: int, exc: ValidationError, model: type[BaseModel]
) -> dict[str, Any]:
    errors = exc.errors(include_input=False, include_url=False, include_context=False)
    first = errors[0] if errors else {}
    loc = first.get("loc") or ()
    top = loc[0] if loc and isinstance(loc[0], str) and loc[0] in model.model_fields else None
    return rejection(
        item, index, RejectionStage.SCHEMA, RejectionReason.SCHEMA_INVALID,
        detail=str(first.get("type", "other")), field_name=top,
    )


# --------------------------------------------------------------------------- #
# Persistencia (les formes; encara no les escriu ningu)                        #
# --------------------------------------------------------------------------- #


def requirement_value_json(requirement: RequirementOutV3) -> dict[str, Any]:
    """`Requirement.value_json` d'un requisit v3, amb `value_schema = VALUE_SCHEMA_V3`.

    Els claims SON el valor: no hi ha un `value` a banda que es pugui
    desincronitzar. L'estat de suport no s'hi copia: es deriva de les files de
    `RequirementEvidence.claim_support_json`.
    """
    return {
        "claims": [claim.model_dump(mode="json") for claim in requirement.claims],
        "claims_complete": requirement.claims_complete.value,
    }


def evidence_support_json(evidence_id: str, support: RequirementSupport) -> dict[str, Any]:
    """`RequirementEvidence.claim_support_json` d'una cita valida. Sense text."""
    checks = support.per_evidence.get(evidence_id, {})
    return {
        "contract": EVIDENCE_SUPPORT_CONTRACT,
        "ref": evidence_id,
        "claims": {claim_id: check.value for claim_id, check in checks.items()},
    }


def adjudication_basis_json(
    candidate: AwardCandidateOutV3,
    decision: AwardDecision,
    evidence_row_ids: Mapping[str, uuid.UUID],
) -> dict[str, Any]:
    """`AwardCriterion.adjudication_basis_json`. Les cites, per id de fila; sense text."""
    return {
        "contract": ADJUDICATION_BASIS_CONTRACT,
        "declared_kind": candidate.kind.value,
        "award_class": decision.award_class.value,
        "reasons": [reason.value for reason in decision.reasons],
        "mechanisms": [
            {
                "kind": basis.value,
                "check": check.value,
                "evidence_ids": [
                    str(evidence_row_ids[eid]) for eid in ids if eid in evidence_row_ids
                ],
            }
            for basis, (check, ids) in decision.mechanisms.items()
        ],
    }


def counts_as_award_criterion(basis_json: dict[str, Any] | None) -> bool:
    """Si una fila d'`award_criteria` es un criteri d'adjudicacio a efectes de lectura.

    A la v3, la taula tambe guarda candidats a revisio, caracteristiques
    desitjables i text informatiu: res no es descarta. Qualsevol lector que
    compti, sumi o ensenyi "criteris" ha de passar per aqui, o un candidat
    semblaria un criteri formal.

    `None` es una fila v1/v2: es llegeix com sempre. Una base v3 nomes compta si
    el backend l'ha classificat `formal_award_criterion`; qualsevol altra cosa,
    inclosa una base mal formada, no compta.
    """
    if basis_json is None:
        return True
    return (
        isinstance(basis_json, dict)
        and basis_json.get("award_class") == AwardClass.FORMAL_AWARD_CRITERION.value
    )


def award_class_of(basis_json: dict[str, Any] | None) -> str | None:
    """La classe v3 desada, o `None` per a una fila v1/v2."""
    if not isinstance(basis_json, dict):
        return None
    value = basis_json.get("award_class")
    return value if value in {c.value for c in AwardClass} else None


def remap_evidence_ids(
    basis_json: dict[str, Any] | None, mapping: Mapping[str, str]
) -> dict[str, Any] | None:
    """La mateixa base d'adjudicacio amb les cites apuntant a les files noves.

    La represa d'un run copia les cites amb id nou; sense aixo, la base del
    criteri copiat apuntaria a les cites del run d'origen. `None` (v1/v2) es
    torna tal qual. Un id sense correspondencia es treu: millor cap enllac que
    un enllac a una fila d'un altre run.
    """
    if basis_json is None:
        return None
    return {
        **basis_json,
        "mechanisms": [
            {
                **mechanism,
                "evidence_ids": [
                    mapping[old] for old in mechanism.get("evidence_ids", []) if old in mapping
                ],
            }
            for mechanism in basis_json.get("mechanisms", [])
        ],
    }
