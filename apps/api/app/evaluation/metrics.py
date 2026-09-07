"""Metriques del benchmark d'extraccio.

Dues idees governen aquest fitxer:

* **El recall critic no s'amaga dins del F1 global.** Un plec amb deu blocadors
  potencials del qual el model en perd dos te un recall critic del 80 %, i aixo
  s'ha de veure sol. Diluir-ho en una mitjana amb els requisits informatius
  seria amagar exactament el que importa.
* **Detectar que un requisit existeix no es encertar-lo.** Un llindar de 450.000
  extret com a 45.000 es una deteccio correcta i un compliment equivocat. Per
  aixo hi ha metriques d'estructura separades de les de deteccio.

`automatable_rate` no s'estima: es mesura executant el motor determinista de 4C
sobre el requisit predit i mirant si pot decidir sol.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from app.compliance.engine import assess_requirement
from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import ExplanationCode
from app.evaluation.matching import ErrorKind, MatchOutcome, MatchPair, PredictedRequirement
from app.models.enums import RequirementObligation

#: Motius que volen dir "el motor de 4C no pot decidir aixo automaticament".
#: Es la definicio operativa d'`automatable_rate`: no una estimacio, una prova.
NOT_AUTOMATABLE_CODES = frozenset(
    {
        ExplanationCode.REQUIREMENT_VALUE_MISSING,
        ExplanationCode.RULE_NOT_AUTOMATABLE,
        ExplanationCode.ECONOMIC_PERIOD_AMBIGUOUS,
        ExplanationCode.EXPERIENCE_SIMILARITY_REVIEW,
        ExplanationCode.REGISTRATION_CLASSIFICATION_REVIEW,
        ExplanationCode.CERTIFICATION_NAME_AMBIGUOUS,
        ExplanationCode.QUALIFICATION_AMBIGUOUS,
        ExplanationCode.SCOPE_AMBIGUOUS,
    }
)


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True, slots=True)
class DetectionMetrics:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float:
        return _safe_div(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        return _safe_div(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        return _safe_div(2 * precision * recall, precision + recall)

    def as_dict(self) -> dict[str, Any]:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass(slots=True)
class StructureMetrics:
    """Sobre els requisits SI detectats: quants camps son correctes."""

    total: int = 0
    category_ok: int = 0
    obligation_ok: int = 0
    operator_ok: int = 0
    amount_ok: int = 0
    currency_ok: int = 0
    lookback_ok: int = 0
    minimum_projects_ok: int = 0
    scope_ok: int = 0
    #: Tots els camps anteriors correctes alhora.
    fully_correct: int = 0

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in list(data):
            if key != "total":
                data[f"{key}_rate"] = round(_safe_div(data[key], self.total), 4)
        return data


@dataclass(slots=True)
class EvidenceMetrics:
    total: int = 0
    quote_locatable: int = 0
    page_correct: int = 0
    document_correct: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "quote_locatable": self.quote_locatable,
            "page_correct": self.page_correct,
            "document_correct": self.document_correct,
            "quote_locatable_rate": round(_safe_div(self.quote_locatable, self.total), 4),
            "page_correct_rate": round(_safe_div(self.page_correct, self.total), 4),
            "document_correct_rate": round(_safe_div(self.document_correct, self.total), 4),
        }


@dataclass(slots=True)
class CostMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    duration_ms: int = 0
    tenders: int = 0
    estimated_cost: float | None = None

    def as_dict(self, correct_requirements: int = 0) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "requests": self.requests,
            "duration_ms": self.duration_ms,
            "tenders": self.tenders,
            "estimated_cost": self.estimated_cost,
            "cost_per_tender": (
                round(self.estimated_cost / self.tenders, 6)
                if self.estimated_cost is not None and self.tenders
                else None
            ),
            "cost_per_correct_requirement": (
                round(self.estimated_cost / correct_requirements, 6)
                if self.estimated_cost is not None and correct_requirements
                else None
            ),
        }


@dataclass(slots=True)
class CriticalMiss:
    """Un requisit obligatori que el model no ha detectat. Llista prioritaria."""

    case_id: str
    gold_id: str
    category: str
    obligation: str
    document_name: str
    page_number: int
    quote: str
    likely_cause: str


@dataclass(slots=True)
class FalseBlocker:
    """El model diu `mandatory` i el gold no. Pot provocar un NO-GO fals."""

    case_id: str
    predicted_title: str
    predicted_category: str
    gold_obligation: str | None
    reason: str


@dataclass(slots=True)
class EvaluationSummary:
    dataset_id: str
    #: Versio exacta del dataset amb que s'ha mesurat. Un benchmark ha de poder
    #: dir contra que es va comparar.
    dataset_version: int = 0
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    extractor_version: str = ""
    schema_version: str = ""
    #: Recompte d'anotacions per estat. Deixa veure que es gold i que es esborrany.
    coverage: dict[str, int] = field(default_factory=dict)
    #: Cert si s'han inclos esborranys: llavors NO son metriques oficials.
    include_draft: bool = False

    overall: DetectionMetrics = field(default_factory=DetectionMetrics)
    mandatory: DetectionMetrics = field(default_factory=DetectionMetrics)
    critical: DetectionMetrics = field(default_factory=DetectionMetrics)
    award_criteria: DetectionMetrics = field(default_factory=DetectionMetrics)
    by_category: dict[str, DetectionMetrics] = field(default_factory=dict)

    structure: StructureMetrics = field(default_factory=StructureMetrics)
    evidence: EvidenceMetrics = field(default_factory=EvidenceMetrics)
    cost: CostMetrics = field(default_factory=CostMetrics)

    errors: Counter = field(default_factory=Counter)
    critical_misses: list[CriticalMiss] = field(default_factory=list)
    false_blockers: list[FalseBlocker] = field(default_factory=list)

    #: Requisits critics correctes que 4C pot avaluar sol.
    automatable_correct: int = 0
    critical_correct: int = 0
    #: Casos que no s'han pogut analitzar (documents sense text, etc.).
    skipped_cases: list[dict[str, Any]] = field(default_factory=list)

    @property
    def automatable_rate(self) -> float:
        return _safe_div(self.automatable_correct, self.critical_correct)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "coverage": self.coverage,
            "include_draft": self.include_draft,
            "official": not self.include_draft,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "extractor_version": self.extractor_version,
            "schema_version": self.schema_version,
            "detection": {
                "overall": self.overall.as_dict(),
                "mandatory": self.mandatory.as_dict(),
                "critical": self.critical.as_dict(),
                "award_criteria": self.award_criteria.as_dict(),
                "by_category": {k: v.as_dict() for k, v in sorted(self.by_category.items())},
            },
            "structure": self.structure.as_dict(),
            "evidence": self.evidence.as_dict(),
            "automatable": {
                "critical_correct": self.critical_correct,
                "automatable_correct": self.automatable_correct,
                "automatable_rate": round(self.automatable_rate, 4),
            },
            "errors": dict(sorted(self.errors.items())),
            "critical_misses": [asdict(item) for item in self.critical_misses],
            "false_blockers": [asdict(item) for item in self.false_blockers],
            "skipped_cases": self.skipped_cases,
            "cost": self.cost.as_dict(self.overall.true_positives),
        }


def is_automatable(predicted: PredictedRequirement) -> bool:
    """Pot el motor de 4C decidir aquest requisit sol?

    No s'estima mirant si `value` te claus: s'executa el motor de veritat sobre
    un perfil complet pero buit. Si retorna un motiu de la llista de "no
    automatitzable", vol dir que 4C hauria de demanar revisio humana.
    """

    class _Shim:
        """Prou API perque les regles de 4C el puguin llegir."""

        category = predicted.category
        obligation = predicted.obligation
        operator = predicted.operator
        scope = predicted.scope
        value_json = predicted.value
        title = predicted.title
        tender_lot_id = None

    # Perfil buit pero amb totes les seccions completes: aixi el resultat depen
    # nomes de si el REQUISIT es prou estructurat, no de si l'empresa compleix.
    snapshot = ProfileSnapshot(
        company_id=None,
        section_ratio=dict.fromkeys(
            ["certifications", "insurances", "registrations", "experience", "team"], 1.0
        ),
    )
    outcome = assess_requirement(_Shim(), snapshot, deadline=None)
    # Nomes compta el motiu, no el mode: amb el perfil buit moltes regles
    # retornen `unknown` per falta de dades de l'EMPRESA, i aixo no diu res
    # sobre si el REQUISIT esta prou estructurat, que es el que es mesura aqui.
    return outcome.explanation_code not in NOT_AUTOMATABLE_CODES


def _values_match(gold: dict[str, Any] | None, predicted: dict[str, Any] | None, key: str) -> bool:
    gold_value = (gold or {}).get(key)
    predicted_value = (predicted or {}).get(key)
    if gold_value is None:
        return True  # el gold no ho exigeix
    if isinstance(gold_value, str) and isinstance(predicted_value, str):
        return gold_value.strip().casefold() == predicted_value.strip().casefold()
    return gold_value == predicted_value


def update_structure(metrics: StructureMetrics, pair: MatchPair) -> None:
    gold, predicted = pair.gold, pair.predicted
    metrics.total += 1

    checks = {
        "category": gold.category == predicted.category,
        "obligation": gold.obligation == predicted.obligation,
        "operator": gold.operator is None or gold.operator == predicted.operator,
        "amount": _values_match(gold.value, predicted.value, "amount"),
        "currency": _values_match(gold.value, predicted.value, "currency"),
        "lookback": _values_match(gold.value, predicted.value, "lookback_years"),
        "minimum_projects": _values_match(gold.value, predicted.value, "minimum_projects"),
        "scope": gold.scope == predicted.scope,
    }
    if checks["category"]:
        metrics.category_ok += 1
    if checks["obligation"]:
        metrics.obligation_ok += 1
    if checks["operator"]:
        metrics.operator_ok += 1
    if checks["amount"]:
        metrics.amount_ok += 1
    if checks["currency"]:
        metrics.currency_ok += 1
    if checks["lookback"]:
        metrics.lookback_ok += 1
    if checks["minimum_projects"]:
        metrics.minimum_projects_ok += 1
    if checks["scope"]:
        metrics.scope_ok += 1
    if all(checks.values()):
        metrics.fully_correct += 1


def likely_cause(gold_page: int, case_pages: set[int]) -> str:
    """Hipotesi barata sobre per que s'ha perdut un requisit."""
    if not case_pages:
        return "document_not_processed"
    if gold_page not in case_pages:
        return "page_not_analysed"
    return "model_miss"


def accumulate_case(
    summary: EvaluationSummary,
    *,
    case_id: str,
    outcome: MatchOutcome,
    analysed_pages: set[int],
) -> None:
    """Agrega un cas al resum global."""
    for pair in outcome.pairs:
        gold = pair.gold
        # Un requisit sense cita valida NO compta com a encert complet.
        valid = pair.predicted.has_valid_evidence
        bucket = summary.by_category.setdefault(str(gold.category), DetectionMetrics())

        if valid:
            summary.overall = DetectionMetrics(
                summary.overall.true_positives + 1,
                summary.overall.false_positives,
                summary.overall.false_negatives,
            )
            summary.by_category[str(gold.category)] = DetectionMetrics(
                bucket.true_positives + 1, bucket.false_positives, bucket.false_negatives
            )
            if gold.obligation == RequirementObligation.MANDATORY:
                summary.mandatory = DetectionMetrics(
                    summary.mandatory.true_positives + 1,
                    summary.mandatory.false_positives,
                    summary.mandatory.false_negatives,
                )
            if gold.is_critical:
                summary.critical = DetectionMetrics(
                    summary.critical.true_positives + 1,
                    summary.critical.false_positives,
                    summary.critical.false_negatives,
                )
                summary.critical_correct += 1
                if is_automatable(pair.predicted):
                    summary.automatable_correct += 1
        else:
            # Detectat pero sense evidencia verificable: no es un encert.
            summary.overall = DetectionMetrics(
                summary.overall.true_positives,
                summary.overall.false_positives + 1,
                summary.overall.false_negatives + 1,
            )
            summary.errors[ErrorKind.WRONG_EVIDENCE.value] += 1

        update_structure(summary.structure, pair)
        for error in _pair_errors(pair):
            summary.errors[error.value] += 1

        # Fals blocador: el model diu obligatori i el gold no.
        if (
            pair.predicted.obligation == RequirementObligation.MANDATORY
            and gold.obligation != RequirementObligation.MANDATORY
        ):
            summary.false_blockers.append(
                FalseBlocker(
                    case_id=case_id,
                    predicted_title=pair.predicted.title[:160],
                    predicted_category=str(pair.predicted.category),
                    gold_obligation=str(gold.obligation),
                    reason="el gold no el considera obligatori",
                )
            )

    for gold in outcome.missed:
        bucket = summary.by_category.setdefault(str(gold.category), DetectionMetrics())
        summary.by_category[str(gold.category)] = DetectionMetrics(
            bucket.true_positives, bucket.false_positives, bucket.false_negatives + 1
        )
        summary.overall = DetectionMetrics(
            summary.overall.true_positives,
            summary.overall.false_positives,
            summary.overall.false_negatives + 1,
        )
        if gold.obligation == RequirementObligation.MANDATORY:
            summary.mandatory = DetectionMetrics(
                summary.mandatory.true_positives,
                summary.mandatory.false_positives,
                summary.mandatory.false_negatives + 1,
            )
        if gold.is_critical:
            summary.critical = DetectionMetrics(
                summary.critical.true_positives,
                summary.critical.false_positives,
                summary.critical.false_negatives + 1,
            )
            summary.critical_misses.append(
                CriticalMiss(
                    case_id=case_id,
                    gold_id=gold.gold_id,
                    category=str(gold.category),
                    obligation=str(gold.obligation),
                    document_name=gold.document_name,
                    page_number=gold.page_number,
                    quote=gold.quote[:220],
                    likely_cause=likely_cause(gold.page_number, analysed_pages),
                )
            )
        summary.errors[ErrorKind.MISSED_REQUIREMENT.value] += 1

    for predicted in outcome.spurious:
        bucket = summary.by_category.setdefault(str(predicted.category), DetectionMetrics())
        summary.by_category[str(predicted.category)] = DetectionMetrics(
            bucket.true_positives, bucket.false_positives + 1, bucket.false_negatives
        )
        summary.overall = DetectionMetrics(
            summary.overall.true_positives,
            summary.overall.false_positives + 1,
            summary.overall.false_negatives,
        )
        if predicted.obligation == RequirementObligation.MANDATORY:
            summary.mandatory = DetectionMetrics(
                summary.mandatory.true_positives,
                summary.mandatory.false_positives + 1,
                summary.mandatory.false_negatives,
            )
            summary.false_blockers.append(
                FalseBlocker(
                    case_id=case_id,
                    predicted_title=predicted.title[:160],
                    predicted_category=str(predicted.category),
                    gold_obligation=None,
                    reason="no hi ha cap requisit al gold que hi correspongui",
                )
            )
        summary.errors[ErrorKind.HALLUCINATED_REQUIREMENT.value] += 1

    for predicted in outcome.duplicates:
        summary.errors[ErrorKind.DUPLICATE.value] += 1
        summary.overall = DetectionMetrics(
            summary.overall.true_positives,
            summary.overall.false_positives + 1,
            summary.overall.false_negatives,
        )
        _ = predicted


def _pair_errors(pair: MatchPair):
    from app.evaluation.matching import classify_pair_errors

    return classify_pair_errors(pair)


def update_evidence(
    metrics: EvidenceMetrics,
    *,
    gold_page: int,
    gold_document: str,
    predicted: PredictedRequirement,
) -> None:
    for document_name, page, _quote, locatable in predicted.evidence:
        metrics.total += 1
        if locatable:
            metrics.quote_locatable += 1
        if abs(page - gold_page) <= 1:
            metrics.page_correct += 1
        if document_name and gold_document:
            if document_name.strip().casefold() == gold_document.strip().casefold():
                metrics.document_correct += 1
