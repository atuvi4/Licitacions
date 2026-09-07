"""Alineacio determinista entre el que prediu el model i el gold.

Comparar dos conjunts de requisits te una trampa: decidir si dos enunciats son
"el mateix requisit" es exactament el tipus de judici que aquesta iteracio vol
mesurar, no delegar. Si s'hi fes servir un LLM, el benchmark mesuraria el
matcher, no l'extractor.

Per aixo l'aparellament es explicit i conservador:

1. Candidat nomes si comparteixen categoria **i** pagina d'evidencia.
2. Entre candidats, es puntua per coincidencia de valor, operador i solapament
   textual de la cita.
3. Assignacio voraç un a un pel millor score, amb un llindar minim.

El que no casa es compta com a fals positiu o fals negatiu. Es preferible un
recompte pessimista i explicable que un d'optimista i opac.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.dataset import GoldAwardCriterion, GoldRequirement
from app.models.enums import RequirementCategory, RequirementObligation, StrEnum
from app.services.evidence_validation import normalize_quote

#: Per sota d'aquest score no es considera el mateix requisit.
MATCH_THRESHOLD = 0.45
#: Tolerancia de pagina: una cita pot caure a la pagina seguent per un salt.
PAGE_TOLERANCE = 1

_WORD = re.compile(r"[a-z0-9]+")


class ErrorKind(StrEnum):
    """Taxonomia d'errors. No es guarda nomes "incorrecte"."""

    MISSED_REQUIREMENT = "missed_requirement"
    HALLUCINATED_REQUIREMENT = "hallucinated_requirement"
    WRONG_OBLIGATION = "wrong_obligation"
    WRONG_CATEGORY = "wrong_category"
    WRONG_NUMERIC_VALUE = "wrong_numeric_value"
    WRONG_OPERATOR = "wrong_operator"
    WRONG_SCOPE = "wrong_scope"
    WRONG_EVIDENCE = "wrong_evidence"
    INCOMPLETE_STRUCTURE = "incomplete_structure"
    DUPLICATE = "duplicate"
    DOCUMENT_NOT_PROCESSED = "document_not_processed"
    PARSER_QUALITY = "parser_quality"


@dataclass(frozen=True, slots=True)
class PredictedRequirement:
    """Vista neutra d'un requisit predit. No lliga el benchmark a l'ORM."""

    id: Any
    category: RequirementCategory
    obligation: RequirementObligation
    operator: Any | None
    scope: Any
    title: str
    value: dict[str, Any] | None
    lot_labels: list[str] = field(default_factory=list)
    #: Evidencies: (document_name, page_number, quote, quote_localitzable)
    evidence: tuple[tuple[str | None, int, str, bool], ...] = ()

    @property
    def pages(self) -> set[int]:
        return {page for _doc, page, _q, _ok in self.evidence}

    @property
    def has_valid_evidence(self) -> bool:
        return any(ok for _doc, _page, _q, ok in self.evidence)


@dataclass(frozen=True, slots=True)
class MatchPair:
    gold: GoldRequirement
    predicted: PredictedRequirement
    score: float


@dataclass(slots=True)
class MatchOutcome:
    pairs: list[MatchPair] = field(default_factory=list)
    #: Gold sense prediccio: falsos negatius.
    missed: list[GoldRequirement] = field(default_factory=list)
    #: Prediccions sense gold: falsos positius.
    spurious: list[PredictedRequirement] = field(default_factory=list)
    #: Prediccions que apunten al mateix gold que una altra millor.
    duplicates: list[PredictedRequirement] = field(default_factory=list)


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(normalize_quote(text or "")))


def text_overlap(a: str, b: str) -> float:
    """Jaccard sobre paraules normalitzades. Determinista i explicable."""
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _numbers(value: dict[str, Any] | None) -> set[float]:
    if not value:
        return set()
    found: set[float] = set()
    for item in value.values():
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            found.add(float(item))
    return found


def value_agreement(gold: dict[str, Any] | None, predicted: dict[str, Any] | None) -> float:
    """Quant s'assemblen dos valors estructurats.

    Es miren els numeros perque son el que decideix un compliment: un llindar
    de 450.000 i un de 45.000 no son el mateix requisit encara que el text
    s'assembli.
    """
    gold_numbers, predicted_numbers = _numbers(gold), _numbers(predicted)
    if not gold_numbers and not predicted_numbers:
        return 0.5  # cap dels dos estructura res: neutre
    if not gold_numbers or not predicted_numbers:
        return 0.0
    return len(gold_numbers & predicted_numbers) / len(gold_numbers | predicted_numbers)


def pair_score(gold: GoldRequirement, predicted: PredictedRequirement) -> float:
    """Score 0..1. Nomes es crida sobre candidats de la mateixa categoria."""
    pages = predicted.pages
    if pages and not any(abs(page - gold.page_number) <= PAGE_TOLERANCE for page in pages):
        return 0.0

    quote_overlap = max(
        (text_overlap(gold.quote, quote) for _doc, _page, quote, _ok in predicted.evidence),
        default=0.0,
    )
    title_overlap = text_overlap(gold.quote, predicted.title)
    values = value_agreement(gold.value, predicted.value)

    # El valor pesa mes que el text: es el que canvia el veredicte.
    return 0.45 * values + 0.35 * max(quote_overlap, title_overlap) + 0.20


def match_requirements(
    gold_items: list[GoldRequirement], predictions: list[PredictedRequirement]
) -> MatchOutcome:
    """Aparella gold amb prediccions, un a un i pel millor score."""
    candidates: list[tuple[float, GoldRequirement, PredictedRequirement]] = []
    for gold in gold_items:
        for predicted in predictions:
            if predicted.category != gold.category:
                continue
            score = pair_score(gold, predicted)
            if score >= MATCH_THRESHOLD:
                candidates.append((score, gold, predicted))

    candidates.sort(key=lambda item: item[0], reverse=True)

    used_gold: set[str] = set()
    used_predicted: set[Any] = set()
    outcome = MatchOutcome()

    for score, gold, predicted in candidates:
        if gold.gold_id in used_gold:
            # Ja hi ha una prediccio millor per a aquest gold.
            if predicted.id not in used_predicted:
                outcome.duplicates.append(predicted)
                used_predicted.add(predicted.id)
            continue
        if predicted.id in used_predicted:
            continue
        outcome.pairs.append(MatchPair(gold=gold, predicted=predicted, score=score))
        used_gold.add(gold.gold_id)
        used_predicted.add(predicted.id)

    outcome.missed = [g for g in gold_items if g.gold_id not in used_gold]
    outcome.spurious = [p for p in predictions if p.id not in used_predicted]
    return outcome


def classify_pair_errors(pair: MatchPair) -> list[ErrorKind]:
    """Que ha fallat en un requisit que SI s'ha detectat."""
    errors: list[ErrorKind] = []
    gold, predicted = pair.gold, pair.predicted

    if gold.obligation != predicted.obligation:
        errors.append(ErrorKind.WRONG_OBLIGATION)
    if gold.operator is not None and gold.operator != predicted.operator:
        errors.append(ErrorKind.WRONG_OPERATOR)
    if gold.scope != predicted.scope:
        errors.append(ErrorKind.WRONG_SCOPE)

    gold_numbers, predicted_numbers = _numbers(gold.value), _numbers(predicted.value)
    if gold_numbers and not predicted_numbers:
        errors.append(ErrorKind.INCOMPLETE_STRUCTURE)
    elif gold_numbers and gold_numbers != predicted_numbers:
        errors.append(ErrorKind.WRONG_NUMERIC_VALUE)

    if not predicted.has_valid_evidence:
        errors.append(ErrorKind.WRONG_EVIDENCE)
    elif not any(
        abs(page - gold.page_number) <= PAGE_TOLERANCE for page in predicted.pages
    ):
        errors.append(ErrorKind.WRONG_EVIDENCE)

    return errors


@dataclass(frozen=True, slots=True)
class PredictedAward:
    id: Any
    name: str
    points: float | None
    evaluation_type: Any
    formula: str | None
    evidence: tuple[tuple[str | None, int, str, bool], ...] = ()

    @property
    def pages(self) -> set[int]:
        return {page for _doc, page, _q, _ok in self.evidence}


def match_award_criteria(
    gold_items: list[GoldAwardCriterion], predictions: list[PredictedAward]
) -> tuple[list[tuple[GoldAwardCriterion, PredictedAward]], list, list]:
    """Aparella criteris. Els punts pesen tant com el nom."""
    candidates: list[tuple[float, GoldAwardCriterion, PredictedAward]] = []
    for gold in gold_items:
        for predicted in predictions:
            name_overlap = text_overlap(gold.name, predicted.name)
            points_match = (
                1.0
                if gold.points is not None and gold.points == predicted.points
                else (0.0 if gold.points is not None else 0.5)
            )
            score = 0.5 * name_overlap + 0.5 * points_match
            if score >= MATCH_THRESHOLD:
                candidates.append((score, gold, predicted))

    candidates.sort(key=lambda item: item[0], reverse=True)
    used_gold: set[str] = set()
    used_predicted: set[Any] = set()
    pairs: list[tuple[GoldAwardCriterion, PredictedAward]] = []
    for _score, gold, predicted in candidates:
        if gold.gold_id in used_gold or predicted.id in used_predicted:
            continue
        pairs.append((gold, predicted))
        used_gold.add(gold.gold_id)
        used_predicted.add(predicted.id)

    missed = [g for g in gold_items if g.gold_id not in used_gold]
    spurious = [p for p in predictions if p.id not in used_predicted]
    return pairs, missed, spurious
