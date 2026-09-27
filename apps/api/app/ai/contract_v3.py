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
from collections.abc import Iterable, Mapping
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
    DocumentNoteOut,
    RequirementOutV3,
    UncertaintyOut,
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
        present = cm.contains_term(quote, str(claim.value))
        return ClaimCheck.SUPPORTED if present else ClaimCheck.SEMANTIC_UNVERIFIED
    return _check_numeric(claim, quote)


def _check_numeric(claim: ClaimOutV3, quote: str) -> ClaimCheck:
    """Un claim numeric contra una cita: valor amb la seva unitat, ancora, i lligam.

    Lligam (v3.1): per a cada aparicio de l'ancora, es miren els valors del
    mateix tipus del tros que l'envolta, FORA de l'ancora. Si n'hi ha, tots han
    de ser el valor del claim. Si no n'hi ha cap, compten els de DINS de
    l'ancora ("agenda amb mes de 500 entrades"). Un valor rival fa el lligam
    ambigu: mai `supported`. Si l'ancora surt mes d'un cop, totes les aparicions
    ho han de provar.
    """
    number = float(claim.value)  # type: ignore[arg-type]
    wanted = cm.canonical(number, claim.unit)
    found = [occ for row in cm.rows(quote) for occ in cm.occurrences(row)]

    if not any(cm.same_value(t, wanted) for t in cm.typed(found, claim.kind)):
        # El numero pot ser-hi sense la unitat, o dins d'una etiqueta ("800/900"):
        # aixo es `unverified`, no `value_not_in_quote`.
        loose = any(abs(o.value - number) < 0.005 for o in found) or number in cm.raw_numbers(
            quote
        )
        return ClaimCheck.UNIT_NOT_IN_QUOTE if loose else ClaimCheck.VALUE_NOT_IN_QUOTE
    if claim.anchor is None:
        return ClaimCheck.ANCHOR_MISSING
    if not cm.contains_term(quote, claim.anchor):
        return ClaimCheck.ANCHOR_NOT_IN_QUOTE
    verdicts = []
    for window in cm.anchor_windows(quote, claim.anchor):
        values = cm.typed(window.outside, claim.kind) or cm.typed(window.inside, claim.kind)
        verdicts.append(bool(values) and all(cm.same_value(v, wanted) for v in values))
    # Una ancora que surt mes d'un cop no discrimina: "Tarifa plana de dades" de la
    # Tarifa 1 i de la Tarifa 2. Nomes compta si TOTES les aparicions ho proven.
    if verdicts and (all(verdicts) if len(verdicts) > 1 else verdicts[0]):
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

    valued = {
        claim_id: not (claim.value is None or claim.certainty == ClaimCertainty.UNKNOWN)
        for claim_id, claim in claims.items()
    }
    per_claim, level, status = _aggregate(
        valued, per_evidence.values(), requirement.claims_complete
    )

    return RequirementSupport(
        per_evidence=per_evidence,
        per_claim=per_claim,
        unknown_refs=unknown_refs,
        level=level,
        status=status,
    )


def _aggregate(
    valued: Mapping[str, bool],
    per_evidence: Iterable[Mapping[str, ClaimCheck]],
    completeness: Completeness,
) -> tuple[dict[str, ClaimStatus], EvidenceLevel, RequirementSupportStatus]:
    """Estat de cada claim i del requisit. L'unica regla, per escriure i per llegir.

    `valued[claim_id]` es fals si el claim no te valor (desconegut).
    """
    checks = list(per_evidence)
    per_claim: dict[str, ClaimStatus] = {}
    for claim_id, has_value in valued.items():
        if not has_value:
            per_claim[claim_id] = ClaimStatus.UNKNOWN
            continue
        statuses = [status_of(c[claim_id]) for c in checks if claim_id in c]
        per_claim[claim_id] = (
            max(statuses, key=_RANK.__getitem__) if statuses else ClaimStatus.NOT_SUPPORTED
        )

    values = list(per_claim.values())
    if not values:
        return per_claim, EvidenceLevel.QUOTE_VALID, RequirementSupportStatus.QUOTE_ONLY
    all_supported = all(v == ClaimStatus.SUPPORTED for v in values)
    if all_supported and completeness == Completeness.COMPLETE:
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
    return per_claim, level, status


# --------------------------------------------------------------------------- #
# Lectura del que s'ha desat                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StoredSupport:
    """El suport d'un requisit v3 reconstruit a partir de les files desades."""

    per_claim: dict[str, str]
    level: str
    status: str
    review_reasons: list[str]


_CLAIM_REASON = {
    ClaimStatus.NOT_SUPPORTED: "claim_not_supported",
    ClaimStatus.UNVERIFIED: "claim_unverified",
    ClaimStatus.UNKNOWN: "claim_value_unknown",
}
_COMPLETENESS_REASON = {
    Completeness.PARTIAL: "claims_partial",
    Completeness.UNKNOWN: "claims_completeness_unknown",
}


def stored_requirement_support(
    value_schema: str | None,
    value_json: Mapping[str, Any] | None,
    evidence_supports: Iterable[Mapping[str, Any] | None],
) -> StoredSupport | None:
    """Estat de claims, nivell i motius de revisio d'un requisit v3 desat.

    `None` si no es v3 (v1/v2 o manual): cap d'aquests conceptes hi existeix, i
    inventar-ne un estat seria afirmar una comprovacio que no es va fer. Nomes
    llegeix codis: un codi desconegut s'ignora, i un claim sense cap cita que
    el declari queda `not_supported`, com al pipeline.
    """
    if value_schema != VALUE_SCHEMA_V3 or not isinstance(value_json, Mapping):
        return None
    claims = [c for c in value_json.get("claims") or [] if isinstance(c, Mapping)]
    valued = {
        str(c.get("id")): not (c.get("value") is None or c.get("certainty") == "unknown")
        for c in claims
        if c.get("id")
    }
    known = {check.value: check for check in ClaimCheck}
    per_evidence: list[dict[str, ClaimCheck]] = []
    for support in evidence_supports:
        stored = (support or {}).get("claims") if isinstance(support, Mapping) else None
        if isinstance(stored, Mapping):
            per_evidence.append(
                {str(k): known[v] for k, v in stored.items() if v in known}
            )
    try:
        completeness = Completeness(value_json.get("claims_complete"))
    except ValueError:
        completeness = Completeness.UNKNOWN
    per_claim, level, status = _aggregate(valued, per_evidence, completeness)

    reasons: list[str] = []
    for claim_status in per_claim.values():
        reason = _CLAIM_REASON.get(claim_status)
        if reason and reason not in reasons:
            reasons.append(reason)
    if per_claim and completeness in _COMPLETENESS_REASON:
        reasons.append(_COMPLETENESS_REASON[completeness])
    return StoredSupport(
        per_claim={k: v.value for k, v in per_claim.items()},
        level=level.value,
        status=status.value,
        review_reasons=reasons,
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
    #: v3 i v3.1: anava a revisio. Es conserva per llegir runs antics.
    KIND_CONFLICTS_WITH_MECHANISM = "kind_conflicts_with_mechanism"
    #: v3.2: la classe la decideix l'evidencia, no el que el model declara.
    KIND_OVERRIDDEN_BY_EVIDENCE = "kind_overridden_by_evidence"
    DECLARED_DESIRABLE = "declared_desirable"
    DECLARED_INFORMATIVE = "declared_informative"


# Marcadors literals, ja normalitzats (sense accents). "Es valorara" NO hi es a
# proposit: una frase del PPT que diu que una cosa es valorara no es cap mecanisme.

#: El nom de la clausula de criteris ("criteris d'adjudicacio", "criteris a
#: tenir en compte en l'adjudicacio", "criterios de valoracion"...).
_CRITERIA_SECTION_TITLE = re.compile(
    r"criteri[so]s?\b.{0,40}?\badjudicaci"
    r"|criteri[so]s?\s+(?:de\s+)?(?:valoraci|puntuaci)"
    r"|criteris\s+avaluables|criterios\s+evaluables"
)
#: Una linia de titol: numerada ("21.", "21.1", "clausula 12.") o en majuscules.
_NUMBERED_HEADING = re.compile(r"^\s*(?:clausula\s+)?\d+(?:\.\d+)*\.?\s")
_OFFER_FORM_MARKERS = (
    "( )", "(  )", "[ ]", "☐", "□", "model d'oferta", "model de proposicio",
    "modelo de oferta", "modelo de proposicion", "sobre 2", "sobre num. 2", "sobre n. 2",
)
#: v3.2: el formulari SI/NO dit amb paraules ("cal assenyalar amb una X la opcio
#: corresponent 'SI' o 'NO'"). Patrons literals sobre text aplanat; res de semblanca.
_QUOTE_MARK = r"[\"'«»]?"
_OFFER_FORM_PATTERNS = (
    re.compile(r"\b(?:assenyal|marc|marqu|indiqu|indic)\w*\b.{0,60}?\bamb una "
               + _QUOTE_MARK + r"x" + _QUOTE_MARK + r"(?!\w)"),
    re.compile(r"\b(?:senal|marc|marqu|indiqu|indic)\w*\b.{0,60}?\bcon una "
               + _QUOTE_MARK + r"x" + _QUOTE_MARK + r"(?!\w)"),
    re.compile(r"[\"'«]si[\"'»]\s*(?:o|/)\s*[\"'«]no[\"'»]"),
    re.compile(r"\(\s*\)\s*si\b"),
)
#: Declarat pel model, n'hi ha prou amb "SI/NO"; inferit, cal la casella o la X.
_DECLARED_ONLY_OFFER_FORM = re.compile(r"\bsi\s*/\s*no\b")
#: Caselles literals: les uniques marques del model d'oferta que valen per inferir.
_CHECKBOX_MARKERS = ("( )", "(  )", "[ ]", "\u2610", "\u25a1")
_POINTS = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s?(?:punts|punt|puntos|punto|pts?\.?)(?!\w)")
#: La capcalera de columna d'una taula de puntuacio: "DESCRIPCIO PUNTS".
_SCORING_HEADER = re.compile(r"\b(?:punts|puntos|puntuacio|puntuacion|pts)\b")
#: Una puntuacio nua al final d'una fila: "... atencio en catala 1".
_TRAILING_SCORE = re.compile(r"(?<![\w.,/])(\d+(?:[.,]\d+)?)\s*$")
#: Quantes linies com a molt entre la capcalera i la fila: la mateixa taula.
_TABLE_SPAN = 30
TABLE_HEADER_CONTEXT = "table_header_on_page"


def _table_row_score(
    candidate: AwardCandidateOutV3, quote: str, page_text: str | None
) -> bool:
    """La fila citada acaba en la puntuacio del candidat, sota una capcalera de punts.

    Tot ha de ser literal i de la MATEIXA pagina validada:
    1. el candidat declara `points`;
    2. la cita te files que acaben en un numero nu, i TOTES acaben en aquell
       numero (una cita amb files de puntuacions diferents es ambigua: no se sap
       quina es la del candidat, i va a revisio);
    3. la cita es localitza a la pagina, i a les `_TABLE_SPAN` linies que acaben
       on acaba la cita hi ha una capcalera: una linia curta, sense xifres, que
       comenca o acaba en "punts"/"puntuacio" ("DESCRIPCIO PUNTS").
    Un numero nu sense capcalera no es mai una puntuacio, i la puntuacio d'una
    altra fila no serveix: ha de ser la del final de la fila citada.
    """
    if candidate.points is None or not page_text:
        return False
    scores = []
    for row in cm.rows(quote):
        match = _TRAILING_SCORE.search(row)
        if match:
            value = cm.parse_number(match.group(1))
            if value is not None:
                scores.append(value)
    if not scores or any(abs(score - candidate.points) >= 0.005 for score in scores):
        return False

    lines = [flat for flat in (cm.flatten(line) for line in page_text.split("\n")) if flat]
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    at = " ".join(lines).find(cm.flatten(quote))
    if at < 0:
        return False
    last = at + len(cm.flatten(quote)) - 1
    end_line = max(n for n, begin in enumerate(starts) if begin <= last)
    return any(
        _is_scoring_header(lines[n])
        for n in range(end_line, max(-1, end_line - _TABLE_SPAN), -1)
    )


def _is_scoring_header(line: str) -> bool:
    """"DESCRIPCIO PUNTS", "Puntuacio maxima": curta, sense xifres, amb la paraula a una punta."""
    if len(line) > 60 or re.search(r"\d", line):
        return False
    words = re.findall(r"[a-z]+", line)
    return bool(words) and (
        _SCORING_HEADER.fullmatch(words[0]) is not None
        or _SCORING_HEADER.fullmatch(words[-1]) is not None
    )


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
        flat = cm.flatten(quote)
        ok = (
            any(marker in flat for marker in _OFFER_FORM_MARKERS)
            or bool(_DECLARED_ONLY_OFFER_FORM.search(flat))
            or _offer_form_instruction(quote)
        )
    else:  # CRITERIA_SECTION
        ok = _is_criteria_heading(quote)
    return MechanismCheck.VERIFIED if ok else MechanismCheck.NOT_IN_QUOTE


def _offer_form_instruction(quote: str) -> bool:
    """La cita porta una casella o la instruccio de marcar SI/NO amb una X."""
    flat = cm.flatten(quote)
    return any(marker in flat for marker in _CHECKBOX_MARKERS) or any(
        pattern.search(flat) for pattern in _OFFER_FORM_PATTERNS
    )


def _inferred_mechanism(
    basis: AdjudicationBasis, candidate: AwardCandidateOutV3, quote: str
) -> bool:
    """Un mecanisme NO declarat, provat per la cita del mateix candidat. Mes estricte.

    * punts: el candidat ha de dir QUANTS (`points`) i la cita ha de dir aquell
      numero amb la unitat ("35 punts"). Uns punts qualssevol no son els seus.
    * model d'oferta: una casella literal o la instruccio "marcar amb una X
      SI/NO". "Sobre 2" o "model d'oferta" sols no ho proven.
    """
    if basis == AdjudicationBasis.POINTS:
        return candidate.points is not None and candidate.points in _points_in(
            cm.normalise(quote)
        )
    if basis == AdjudicationBasis.OFFER_FORM:
        return _offer_form_instruction(quote)
    return False


def _is_criteria_heading(quote: str) -> bool:
    """La cita conte el TITOL de la clausula de criteris, no una mencio.

    "d'acord amb els criteris d'adjudicacio" apareix a clausules de procediment
    (obertura de sobres, garanties): citar-la no prova que un criteri en formi
    part. Nomes compta si el nom es a una linia de titol: numerada o en
    majuscules, com "21. CRITERIS A TENIR EN COMPTE EN L'ADJUDICACIO".
    """
    for line in quote.splitlines():
        text = cm.normalise(line)
        if not _CRITERIA_SECTION_TITLE.search(text):
            continue
        letters = [ch for ch in line if ch.isalpha()]
        if _NUMBERED_HEADING.match(text) or (letters and all(ch.isupper() for ch in letters)):
            return True
    return False


@dataclass(frozen=True, slots=True)
class AwardDecision:
    award_class: AwardClass
    #: `{mecanisme: (comprovacio, [ids de cita que el verifiquen])}`.
    mechanisms: dict[AdjudicationBasis, tuple[MechanismCheck, list[str]]]
    reasons: list[AwardReason] = field(default_factory=list)
    #: `{evidence_id: [refs]}` declarats a `supports` que no son cap mecanisme declarat.
    unknown_refs: dict[str, list[str]] = field(default_factory=dict)
    #: v3.2: com s'ha provat un mecanisme quan no n'hi ha prou amb la cita sola
    #: (p. ex. `table_header_on_page`). Sense text.
    contexts: dict[AdjudicationBasis, str] = field(default_factory=dict)
    #: v3.2: mecanismes que el model NO va declarar pero que la seva evidencia prova.
    inferred: frozenset[AdjudicationBasis] = frozenset()

    @property
    def needs_review(self) -> bool:
        return self.award_class == AwardClass.CANDIDATE_AWARD_CRITERION


#: Mecanismes que el backend busca a l'evidencia encara que el model no els
#: declari: nomes els que lliguen la puntuacio o la casella a la FILA citada.
#: La clausula de criteris o una taula generica no, perque no diuen de quin item parlen.
_INFERABLE = (AdjudicationBasis.OFFER_FORM, AdjudicationBasis.POINTS)


def classify_award(
    candidate: AwardCandidateOutV3,
    valid_quotes: Mapping[str, str],
    page_texts: Mapping[str, str] | None = None,
) -> AwardDecision:
    """Classe final d'un candidat. Mai no el descarta: com a molt, a revisio.

    v3.2: la classe la decideix l'EVIDENCIA.

    * Mecanisme formal provat per una cita valida -> criteri formal, ho digui el
      model o no. Els declarats es comproven amb les cites que els declaren; el
      formulari d'oferta i els punts de fila tambe es busquen a les altres cites
      valides del candidat (`inferred`).
    * Cap mecanisme provat -> el que el model declara: criteri (a revisio),
      desitjable o informatiu. "Es valorara" sol no prova res.
    * Mai formal perque el model ho digui. Ni l'origen (PCAP) no hi compta.

    `valid_quotes` es `{evidence_id: text exacte localitzat}`; `page_texts`,
    opcional, `{evidence_id: text de la pagina validada}` per a la regla de taula.
    """
    page_texts = page_texts or {}
    declared = {basis.value for basis in candidate.adjudication_basis}
    mechanisms: dict[AdjudicationBasis, tuple[MechanismCheck, list[str]]] = {}
    contexts: dict[AdjudicationBasis, str] = {}
    unknown_refs: dict[str, list[str]] = {}

    for evidence in candidate.evidence:
        if evidence.id not in valid_quotes:
            continue
        for ref in evidence.supports:
            if ref not in declared:
                unknown_refs.setdefault(evidence.id, []).append(ref)

    def evaluate(
        basis: AdjudicationBasis, evidence_ids: list[str], *, declared: bool
    ) -> tuple[MechanismCheck, list[str], str | None]:
        checks = {
            eid: check_mechanism(basis, candidate, valid_quotes[eid]) if declared
            else (MechanismCheck.VERIFIED
                  if _inferred_mechanism(basis, candidate, valid_quotes[eid])
                  else MechanismCheck.NOT_IN_QUOTE)
            for eid in evidence_ids
        }
        verified = [eid for eid, check in checks.items() if check == MechanismCheck.VERIFIED]
        if verified:
            return MechanismCheck.VERIFIED, verified, None
        if basis in (AdjudicationBasis.POINTS, AdjudicationBasis.CRITERIA_TABLE):
            table = [
                eid for eid in evidence_ids
                if _table_row_score(candidate, valid_quotes[eid], page_texts.get(eid))
            ]
            if table:
                return MechanismCheck.VERIFIED, table, TABLE_HEADER_CONTEXT
        return next(iter(checks.values())), [], None

    for basis in candidate.adjudication_basis:
        declaring = [
            e.id for e in candidate.evidence if basis.value in e.supports and e.id in valid_quotes
        ]
        if not declaring:
            mechanisms[basis] = (MechanismCheck.NOT_EVIDENCED, [])
            continue
        check, ids, context = evaluate(basis, declaring, declared=True)
        mechanisms[basis] = (check, ids)
        if context:
            contexts[basis] = context

    inferred: set[AdjudicationBasis] = set()
    own = [e.id for e in candidate.evidence if e.id in valid_quotes]
    for basis in _INFERABLE:
        if basis in mechanisms or not own:
            continue
        check, ids, context = evaluate(basis, own, declared=False)
        if check == MechanismCheck.VERIFIED:
            mechanisms[basis] = (check, ids)
            inferred.add(basis)
            if context:
                contexts[basis] = context

    any_verified = any(check == MechanismCheck.VERIFIED for check, _ in mechanisms.values())
    reasons: list[AwardReason]
    if any_verified:
        award_class = AwardClass.FORMAL_AWARD_CRITERION
        reasons = [AwardReason.MECHANISM_VERIFIED]
        if candidate.kind != AwardCandidateKind.AWARD_CRITERION:
            reasons.append(AwardReason.KIND_OVERRIDDEN_BY_EVIDENCE)
    elif candidate.kind == AwardCandidateKind.AWARD_CRITERION:
        award_class = AwardClass.CANDIDATE_AWARD_CRITERION
        reasons = [
            AwardReason.NO_MECHANISM_VERIFIED if declared else AwardReason.NO_MECHANISM_DECLARED
        ]
    elif candidate.kind == AwardCandidateKind.DESIRABLE_FEATURE:
        award_class, reasons = AwardClass.TECHNICAL_DESIRABLE, [AwardReason.DECLARED_DESIRABLE]
    else:
        award_class, reasons = AwardClass.INFORMATIVE, [AwardReason.DECLARED_INFORMATIVE]

    return AwardDecision(
        award_class=award_class, mechanisms=mechanisms, reasons=reasons,
        unknown_refs=unknown_refs, contexts=contexts, inferred=frozenset(inferred),
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
    #: v3.2: una clau d'arrel desconeguda, descartada.
    ROOT = "root"
    #: v3.2: una nota o incertesa (metadada auxiliar) descartada.
    AUXILIARY = "auxiliary"
    SCHEMA = "schema"
    EVIDENCE = "evidence"
    ITEM = "item"
    CLAIM = "claim"
    DEDUP = "dedup"


class RejectionItem(StrEnum):
    REQUIREMENT = "requirement"
    AWARD_CANDIDATE = "award_candidate"
    ROOT = "root"
    DOCUMENT_NOTE = "document_note"
    UNCERTAINTY = "uncertainty"


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
        "string_type", "int_type", "int_parsing", "float_type", "bool_type", "list_type",
        "dict_type", "model_attributes_type",
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
    """Valida el sobre i cada element per separat.

    v3.2: si el sobre no es usable (no es un objecte, li falta
    `requirements` o `award_candidates`, o no son llistes), aixeca
    `ValidationError`: el chunk sencer es `schema_invalid`, com a v1. Les claus
    d'arrel desconegudes i les notes/incerteses mal formades es descarten amb un
    rebuig (`_prevalidate_envelope`). Si nomes un element es invalid, es
    descarta NOMES aquell, amb un rebuig `schema_invalid` que diu el tipus
    d'error i el camp de primer nivell. Mai el valor, i mai el nom d'una clau
    inventada.
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
    #: Elements SENCERS descartats per esquema (el que compta `requirements_rejected`).
    items_rejected: int = 0
    #: Claims descartats un a un dins de requisits que s'han conservat (v3.1).
    claims_rejected: int = 0
    #: Posicio ORIGINAL dels requisits conservats sense algun claim: van a revisio.
    salvaged_requirements: frozenset[int] = frozenset()
    #: v3.2: claus d'arrel desconegudes descartades (mai es fan servir).
    root_fields_rejected: int = 0
    #: v3.2: notes i incerteses descartades (element a element o el camp sencer).
    auxiliary_rejected: int = 0

    @property
    def schema_rejected(self) -> int:
        """Elements sencers descartats. Un claim descartat no hi compta."""
        return self.items_rejected


def parse_chunk_v3_result(payload: Any) -> ParsedChunkV3:
    """`parse_chunk_v3` en la forma que espera `ExtractorProfile.parse`."""
    log = RejectionLog()
    payload, root_rejected, auxiliary, auxiliary_rejected = _prevalidate_envelope(payload, log)
    envelope = {key: [] for key, _, _ in _ITEM_LISTS}
    kept: dict[str, list[BaseModel]] = {}
    indexes: dict[str, list[int]] = {}
    items_rejected = claims_rejected = 0
    salvaged: set[int] = set()
    for key, model, item in _ITEM_LISTS:
        kept[key], indexes[key] = [], []
        for index, raw in enumerate(payload.get(key) or []):
            try:
                kept[key].append(model.model_validate(raw))
                indexes[key].append(index)
                continue
            except ValidationError as exc:
                rescued, claim_errors = (
                    _salvage_requirement(raw, exc) if model is RequirementOutV3 else (None, [])
                )
                if rescued is None:
                    items_rejected += 1
                    log.add(_schema_rejection(item, index, exc, model))
                    continue
            kept[key].append(rescued)
            indexes[key].append(index)
            salvaged.add(index)
            for claim_index, error_type, field_name in claim_errors:
                claims_rejected += 1
                log.add(rejection(
                    item, index, RejectionStage.CLAIM, RejectionReason.SCHEMA_INVALID,
                    claim=claim_index, detail=error_type, field_name=field_name,
                ))
    output = ChunkExtractionOutV3.model_validate({**envelope, **kept, **auxiliary})
    return ParsedChunkV3(
        output=output,
        schema_rejections=log,
        requirement_indexes=tuple(indexes["requirements"]),
        award_indexes=tuple(indexes["award_candidates"]),
        items_rejected=items_rejected,
        claims_rejected=claims_rejected,
        salvaged_requirements=frozenset(salvaged),
        root_fields_rejected=root_rejected,
        auxiliary_rejected=auxiliary_rejected,
    )


#: Les llistes d'extraccio: si hi son, han de ser llistes, o el chunk falla sencer.
CORE_ROOT_FIELDS = ("requirements", "award_candidates")
#: Metadades auxiliars: mai no fan caure l'extraccio.
_AUXILIARY_FIELDS = (
    ("document_notes", DocumentNoteOut, RejectionItem.DOCUMENT_NOTE),
    ("uncertainties", UncertaintyOut, RejectionItem.UNCERTAINTY),
)
_KNOWN_ROOT = frozenset(CORE_ROOT_FIELDS) | {name for name, _, _ in _AUXILIARY_FIELDS}
_ROOT_KEY_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")


class _CoreEnvelope(BaseModel):
    """Nomes el que fa usable una resposta: les DUES llistes d'extraccio.

    Com a `ChunkExtractionOutV3`: obligatories, llistes i no nul·les. Una que
    falti no es completa mai amb `[]`. Les claus desconegudes s'ignoren aqui
    perque `_prevalidate_envelope` ja les descarta i les registra.
    """

    model_config = {"extra": "ignore"}

    requirements: list[Any]
    award_candidates: list[Any]


def _masked_key(key: Any) -> str:
    """El nom d'una clau inventada nomes si no pot ser text del plec."""
    text = str(key)
    return text if _ROOT_KEY_NAME.fullmatch(text) else f"<text,{len(text)} chars>"


def _prevalidate_envelope(
    payload: Any, log: RejectionLog
) -> tuple[dict[str, Any], int, dict[str, list[Any]], int]:
    """El sobre, abans de validar cap element. v3.2.

    * No es un objecte, li falta `requirements` o `award_candidates`, o alguna
      no es una llista (tambe `null`) -> `ValidationError`: el chunk falla
      sencer. Una llista d'extraccio absent o inservible mai no es converteix
      en buida.
    * Claus d'arrel desconegudes -> es descarten, cadascuna amb un rebuig
      (`stage: root`, nom emmascarat si podria ser text). El seu contingut no es
      fa servir mai.
    * `document_notes` / `uncertainties` -> element a element: el que no valida
      es descarta amb un rebuig; si el camp sencer no es una llista, es descarta
      el camp. Mai no fan caure requisits ni criteris.
    """
    if not isinstance(payload, dict):
        ChunkExtractionOutV3.model_validate(payload)  # llanca el ValidationError
    _CoreEnvelope.model_validate(payload)

    root_rejected = 0
    for key in payload:
        if key not in _KNOWN_ROOT:
            root_rejected += 1
            log.add(rejection(
                RejectionItem.ROOT, 0, RejectionStage.ROOT, RejectionReason.SCHEMA_INVALID,
                detail="extra_forbidden", field_name=_masked_key(key),
            ))

    auxiliary: dict[str, list[Any]] = {}
    auxiliary_rejected = 0
    for name, model, item in _AUXILIARY_FIELDS:
        if name not in payload:
            continue
        value = payload[name]
        if not isinstance(value, list):
            auxiliary_rejected += 1
            log.add(rejection(
                item, 0, RejectionStage.AUXILIARY, RejectionReason.SCHEMA_INVALID,
                detail="list_type", field_name=name,
            ))
            continue
        kept: list[Any] = []
        for index, element in enumerate(value):
            try:
                kept.append(model.model_validate(element))
            except ValidationError as exc:
                auxiliary_rejected += 1
                first = exc.errors(include_input=False, include_url=False,
                                   include_context=False)[0]
                loc = first.get("loc") or ()
                field_name = (
                    loc[0] if loc and isinstance(loc[0], str) and loc[0] in model.model_fields
                    else None
                )
                log.add(rejection(
                    item, index, RejectionStage.AUXILIARY, RejectionReason.SCHEMA_INVALID,
                    detail=str(first.get("type", "other")), field_name=field_name,
                ))
        auxiliary[name] = kept
    known = {key: value for key, value in payload.items() if key in CORE_ROOT_FIELDS}
    return known, root_rejected, auxiliary, auxiliary_rejected


def _salvage_requirement(
    raw: Any, exc: ValidationError
) -> tuple[RequirementOutV3 | None, list[tuple[int, str, str | None]]]:
    """Un requisit que nomes falla per claims concrets, sense aquells claims.

    Nomes si TOTS els errors son dins d'un claim (`claims[i]...`). Qualsevol
    altre error (categoria, titol, cites, ids repetits, massa claims) descarta
    el requisit com sempre. El requisit rescatat porta `claims_complete =
    partial` (se sap que en falta algun) i qui el desa l'envia a revisio. Un
    claim descartat no es converteix mai en res: simplement no hi es, i el seu
    motiu queda als rebutjos. Retorna el requisit, o `None`, i per a cada claim
    descartat: (posicio, tipus d'error, camp).
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("claims"), list):
        return None, []
    errors = exc.errors(include_input=False, include_url=False, include_context=False)
    bad: set[int] = set()
    for error in errors:
        loc = error.get("loc") or ()
        if len(loc) >= 2 and loc[0] == "claims" and isinstance(loc[1], int):
            bad.add(loc[1])
        else:
            return None, []
    if not bad:
        return None, []
    claim_errors: list[tuple[int, str, str | None]] = []
    for position in sorted(bad):
        try:
            ClaimOutV3.model_validate(raw["claims"][position])
            first: dict[str, Any] = {"type": "other", "loc": ()}
        except ValidationError as claim_exc:
            first = claim_exc.errors(include_input=False, include_url=False,
                                     include_context=False)[0]
        loc = first.get("loc") or ()
        field_name = (
            loc[0] if loc and isinstance(loc[0], str) and loc[0] in ClaimOutV3.model_fields
            else None
        )
        claim_errors.append((position, str(first.get("type", "other")), field_name))
    remaining = [claim for n, claim in enumerate(raw["claims"]) if n not in bad]
    try:
        rescued = RequirementOutV3.model_validate(
            {**raw, "claims": remaining, "claims_complete": Completeness.PARTIAL.value}
        )
    except ValidationError:
        return None, []
    return rescued, claim_errors


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
                **({"context": decision.contexts[basis]} if basis in decision.contexts else {}),
                **({"inferred": True} if basis in decision.inferred else {}),
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


def adjudication_review_reasons(basis_json: Mapping[str, Any] | None) -> list[str]:
    """Per que un candidat v3 no es formal. Buit per a v1/v2 i per als formals.

    Codis de vocabulari tancat: el motiu de la classe (`no_mechanism_verified`...)
    i cada mecanisme declarat que no s'ha provat, com `<mecanisme>:<comprovacio>`.
    """
    if not isinstance(basis_json, Mapping):
        return []
    if basis_json.get("award_class") == AwardClass.FORMAL_AWARD_CRITERION.value:
        return []
    known_reasons = {r.value for r in AwardReason}
    known_kinds = {b.value for b in AdjudicationBasis}
    known_checks = {c.value for c in MechanismCheck} - {MechanismCheck.VERIFIED.value}
    reasons = [r for r in basis_json.get("reasons") or [] if r in known_reasons]
    for mechanism in basis_json.get("mechanisms") or []:
        if not isinstance(mechanism, Mapping):
            continue
        kind, check = mechanism.get("kind"), mechanism.get("check")
        if kind in known_kinds and check in known_checks:
            reasons.append(f"{kind}:{check}")
    return reasons


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
