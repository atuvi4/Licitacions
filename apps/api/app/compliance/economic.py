"""Solvencia economica: combinar exercicis i comparar. Res mes.

El que la regla sap fer ara i abans no: llegir **quins** exercicis mira el plec i
**com** els combina. "El de major volum dels tres ultims" i "cadascun dels tres
ultims" son exigencies diferents, i tractar-les igual convertia una en l'altra.

El que segueix sense fer, i a proposit:

* **No converteix divises.** Si el plec demana euros i la facturacio es en una
  altra moneda, aixo es revisio, no una estimacio amb un tipus de canvi inventat.
* **No dedueix la base d'un percentatge.** "El 70% del pressupost" nomes es pot
  comparar si el pressupost es pot llegir de l'expedient sense ambiguitat.
* **No omple exercicis que falten.** Un any no registrat no es un any amb
  facturacio zero.

La linia que ho governa tot: amb dades incompletes nomes es pot afirmar el SI.
Un `max` que ja supera el llindar no pot deixar de superar-lo perque aparegui un
altre exercici; un `max` que no hi arriba, si.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.compliance.aggregation import NEEDS_EVERY_YEAR, YearValue, aggregate_years, expected_years
from app.compliance.percentages import ThresholdResolution, resolve_threshold
from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import (
    EvidenceRef,
    ExplanationCode,
    RuleCode,
    RuleOutcome,
    needs_review,
    unknown,
)
from app.compliance.specs import EconomicSpec, read_economic
from app.models.enums import (
    AggregationType,
    AssessmentMode,
    AssessmentResult,
    CompanyEvidenceKind,
    EconomicMetric,
    EvidenceStrength,
    LinkedEntityType,
    RequirementOperator,
)

#: Operadors que sabem comparar amb un import.
_NUMERIC_OPERATORS = {
    RequirementOperator.GTE: lambda a, b: a >= b,
    RequirementOperator.GT: lambda a, b: a > b,
    RequirementOperator.LTE: lambda a, b: a <= b,
    RequirementOperator.LT: lambda a, b: a < b,
    RequirementOperator.EQ: lambda a, b: a == b,
}

#: Metriques que la facturacio anual registrada pot respondre. La resta
#: —patrimoni net, fons de maniobra, ratios— demanen dades que el perfil no te.
_SUPPORTED_METRICS = frozenset(
    {EconomicMetric.ANNUAL_TURNOVER, EconomicMetric.SPECIFIC_TURNOVER}
)


def _reference_year(deadline: date | None, years: list[Any]) -> int:
    """L'exercici a partir del qual es compten els "N ultims".

    El termini de presentacio mana: es el moment en que l'organ mira els comptes.
    Sense termini, es parteix de l'exercici mes recent que l'empresa te
    registrat, que fa el calcul deterministic amb les dades que hi ha.
    """
    if deadline is not None:
        return deadline.year
    return max((year.year for year in years), default=date.today().year) + 1


def assess_economic_solvency(
    requirement: Any,
    snapshot: ProfileSnapshot,
    deadline: date | None = None,
    tender: Any = None,
) -> RuleOutcome:
    spec = read_economic(requirement)
    if spec is None:
        return needs_review(
            RuleCode.ECONOMIC_REVENUE_GTE, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )
    if spec.period_ambiguous:
        # "els tres ultims exercicis acumulats" escrit en text lliure a la v1:
        # aixo es una regla legal, no una comparacio.
        return needs_review(
            RuleCode.ECONOMIC_REVENUE_GTE, ExplanationCode.ECONOMIC_PERIOD_AMBIGUOUS
        )
    if spec.metric not in _SUPPORTED_METRICS:
        return needs_review(
            RuleCode.ECONOMIC_AGGREGATE,
            ExplanationCode.ECONOMIC_METRIC_UNSUPPORTED,
            metric=str(spec.metric),
        )

    compare = _NUMERIC_OPERATORS.get(spec.operator)
    if compare is None:
        return needs_review(
            RuleCode.ECONOMIC_REVENUE_GTE,
            ExplanationCode.RULE_NOT_AUTOMATABLE,
            operator=str(spec.operator),
        )

    resolution = resolve_threshold(spec.threshold, tender)
    if resolution.amount is None:
        return needs_review(
            RuleCode.ECONOMIC_AGGREGATE,
            ExplanationCode.PERCENTAGE_BASE_UNAVAILABLE,
            **resolution.explain(),
        )
    threshold = resolution.amount
    currency = spec.threshold.currency

    if not snapshot.financial_years:
        return unknown(
            RuleCode.ECONOMIC_AGGREGATE,
            ExplanationCode.ECONOMIC_DATA_INCOMPLETE,
            reason="no_financial_years",
        )

    registered = [year for year in snapshot.financial_years if year.revenue is not None]
    comparable = [y for y in registered if (y.currency or "EUR").upper() == currency]
    if not comparable:
        if registered:
            return needs_review(
                RuleCode.ECONOMIC_AGGREGATE,
                ExplanationCode.ECONOMIC_CURRENCY_MISMATCH,
                required_currency=currency,
                company_currencies=sorted({(y.currency or "EUR").upper() for y in registered}),
            )
        return unknown(
            RuleCode.ECONOMIC_AGGREGATE,
            ExplanationCode.ECONOMIC_DATA_INCOMPLETE,
            reason="no_revenue_registered",
        )

    window = None
    if spec.years:
        window = expected_years(_reference_year(deadline, comparable), spec.years)

    aggregate = aggregate_years(
        [YearValue(year=y.year, amount=Decimal(y.revenue)) for y in comparable],
        aggregation=spec.aggregation,
        threshold=threshold,
        window=window,
    )

    by_year = {y.year: y for y in comparable}
    detail = {
        "required": float(threshold),
        "currency": currency,
        "operator": str(spec.operator),
        "aggregation": str(spec.aggregation),
        "years_required": spec.years,
        "years_used": list(aggregate.years_used),
        "years_missing": list(aggregate.years_missing),
        "revenue_by_year": {
            str(year): float(Decimal(by_year[year].revenue)) for year in aggregate.years_used
        },
        **resolution.explain(),
    }

    if aggregate.value is None:
        # Una mitjana o una suma amb un exercici que falta no es una mitjana.
        return unknown(
            RuleCode.ECONOMIC_AGGREGATE,
            ExplanationCode.ECONOMIC_PERIOD_INCOMPLETE,
            **detail,
        )

    actual = aggregate.value
    detail["actual"] = float(actual)
    evidence = _evidence(snapshot, by_year, aggregate, currency)
    company_value = {
        "amount": float(actual),
        "currency": currency,
        "aggregation": str(aggregate.method),
        "years_used": list(aggregate.years_used),
        "years_missing": list(aggregate.years_missing),
    }

    if aggregate.failing_year is not None:
        # `each_year`: un exercici registrat que no arriba ja ho decideix, i cap
        # exercici que falti pot desfer-ho.
        return _outcome(
            AssessmentResult.DOES_NOT_SATISFY,
            ExplanationCode.ECONOMIC_YEAR_BELOW_THRESHOLD,
            {**detail, "failing_year": aggregate.failing_year},
            company_value,
            evidence,
        )

    if compare(actual, threshold):
        return _outcome(
            AssessmentResult.SATISFIES,
            ExplanationCode.ECONOMIC_THRESHOLD_MET,
            detail,
            company_value,
            evidence,
        )

    if aggregate.incomplete:
        # No arriba amb el que hi ha, pero encara falten exercicis del periode:
        # un any no registrat podria ser el que compleix.
        return unknown(
            RuleCode.ECONOMIC_AGGREGATE,
            ExplanationCode.ECONOMIC_PERIOD_INCOMPLETE,
            **detail,
        )

    return _outcome(
        AssessmentResult.DOES_NOT_SATISFY,
        ExplanationCode.ECONOMIC_THRESHOLD_NOT_MET,
        detail,
        company_value,
        evidence,
    )


def _evidence(
    snapshot: ProfileSnapshot, by_year: dict[int, Any], aggregate: Any, currency: str
) -> tuple[EvidenceRef, ...]:
    """Els exercicis que sostenen el calcul, no nomes el guanyador.

    Amb `average` o `cumulative` el veredicte depen de tots, i ensenyar-ne un de
    sol faria que l'explicacio no quadres amb la xifra.
    """
    documents = snapshot.documents_for(LinkedEntityType.COMPANY, snapshot.company_id)
    strength = EvidenceStrength.SUPPORTING if documents else EvidenceStrength.MISSING
    document_id = documents[0].id if documents else None

    if aggregate.method in NEEDS_EVERY_YEAR:
        years = aggregate.years_used
    elif aggregate.value is not None:
        years = tuple(
            year
            for year in aggregate.years_used
            if Decimal(by_year[year].revenue) == aggregate.value
        )[:1] or aggregate.years_used[:1]
    else:
        years = aggregate.years_used[:1]

    return tuple(
        EvidenceRef(
            kind=CompanyEvidenceKind.FINANCIAL_YEAR,
            entity_id=by_year[year].id,
            label=f"Facturacio {year}: {Decimal(by_year[year].revenue):.2f} {currency}",
            strength=strength,
            document_id=document_id,
        )
        for year in years
    )


def _outcome(
    result: AssessmentResult,
    explanation: str,
    params: dict[str, Any],
    company_value: dict[str, Any],
    evidence: tuple[EvidenceRef, ...],
) -> RuleOutcome:
    strength = evidence[0].strength if evidence else EvidenceStrength.MISSING
    return RuleOutcome(
        result=result,
        rule_code=RuleCode.ECONOMIC_AGGREGATE,
        explanation_code=explanation,
        params=params,
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=strength,
    )


__all__ = ["assess_economic_solvency", "EconomicSpec", "AggregationType", "ThresholdResolution"]
