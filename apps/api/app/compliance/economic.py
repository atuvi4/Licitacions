"""Solvencia economica: comparar imports, no interpretar normativa.

La regla nomes decideix quan el requisit esta prou estructurat: un import, una
moneda i un operador. Tot el que impliqui llegir la LCSP —quin exercici compta,
si val la mitjana o el millor any, com es tracta una empresa de nova creacio—
queda fora: aixo es judici, i el judici es de la persona, no del motor.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import (
    EvidenceRef,
    ExplanationCode,
    RuleCode,
    RuleOutcome,
    needs_review,
    unknown,
)
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    CompanyEvidenceKind,
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

#: Periodes que entenem. Qualsevol altre es revisio.
_KNOWN_PERIODS = {None, "annual", "anual", "yearly", "year"}


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def assess_economic_solvency(
    requirement: Any, snapshot: ProfileSnapshot
) -> RuleOutcome:
    """Compara la facturacio de l'empresa amb el llindar del plec."""
    value = requirement.value_json or {}
    threshold = _decimal(value.get("amount"))
    if threshold is None:
        return needs_review(
            RuleCode.ECONOMIC_REVENUE_GTE, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )

    operator = requirement.operator or RequirementOperator.GTE
    compare = _NUMERIC_OPERATORS.get(operator)
    if compare is None:
        return needs_review(
            RuleCode.ECONOMIC_REVENUE_GTE,
            ExplanationCode.RULE_NOT_AUTOMATABLE,
            operator=str(operator),
        )

    period = (value.get("period") or None)
    if isinstance(period, str):
        period = period.strip().casefold()
    if period not in _KNOWN_PERIODS:
        # "els tres ultims exercicis acumulats", "mitjana triennal"... aixo es
        # una regla legal, no una comparacio.
        return needs_review(
            RuleCode.ECONOMIC_REVENUE_GTE,
            ExplanationCode.ECONOMIC_PERIOD_AMBIGUOUS,
            period=str(period),
        )

    currency = (value.get("currency") or "EUR").strip().upper()

    years = snapshot.financial_years
    if not years:
        return unknown(
            RuleCode.ECONOMIC_REVENUE_GTE,
            ExplanationCode.ECONOMIC_DATA_INCOMPLETE,
            reason="no_financial_years",
        )

    # Nombre d'exercicis que el plec permet mirar enrere, si ho diu.
    lookback = value.get("years")
    candidates = [year for year in years if year.revenue is not None]
    if isinstance(lookback, int) and lookback > 0:
        candidates = candidates[:lookback]
    if not candidates:
        return unknown(
            RuleCode.ECONOMIC_REVENUE_GTE,
            ExplanationCode.ECONOMIC_DATA_INCOMPLETE,
            reason="no_revenue_registered",
        )

    # Sense conversio de divisa: no s'inventa cap tipus de canvi.
    mismatched = [y for y in candidates if (y.currency or "EUR").upper() != currency]
    comparable = [y for y in candidates if (y.currency or "EUR").upper() == currency]
    if not comparable:
        return needs_review(
            RuleCode.ECONOMIC_REVENUE_GTE,
            ExplanationCode.ECONOMIC_CURRENCY_MISMATCH,
            required_currency=currency,
            company_currencies=sorted({(y.currency or "EUR").upper() for y in mismatched}),
        )

    best = max(comparable, key=lambda y: Decimal(y.revenue))
    best_revenue = Decimal(best.revenue)
    documents = snapshot.documents_for(LinkedEntityType.COMPANY, snapshot.company_id)
    evidence = (
        EvidenceRef(
            kind=CompanyEvidenceKind.FINANCIAL_YEAR,
            entity_id=best.id,
            label=f"Facturacio {best.year}: {best_revenue:.2f} {currency}",
            strength=EvidenceStrength.SUPPORTING if documents else EvidenceStrength.MISSING,
            document_id=documents[0].id if documents else None,
        ),
    )
    company_value = {
        "amount": float(best_revenue),
        "currency": currency,
        "year": best.year,
        "years_considered": [y.year for y in comparable],
    }

    if compare(best_revenue, threshold):
        return RuleOutcome(
            result=AssessmentResult.SATISFIES,
            rule_code=RuleCode.ECONOMIC_REVENUE_GTE,
            explanation_code=ExplanationCode.ECONOMIC_THRESHOLD_MET,
            params={
                "required": float(threshold),
                "actual": float(best_revenue),
                "currency": currency,
                "year": best.year,
                "operator": str(operator),
            },
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=evidence[0].strength,
        )

    # La facturacio esta registrada i no arriba: aixo SI que es un incompliment
    # demostrat, no una absencia de dada.
    return RuleOutcome(
        result=AssessmentResult.DOES_NOT_SATISFY,
        rule_code=RuleCode.ECONOMIC_REVENUE_GTE,
        explanation_code=ExplanationCode.ECONOMIC_THRESHOLD_NOT_MET,
        params={
            "required": float(threshold),
            "actual": float(best_revenue),
            "currency": currency,
            "year": best.year,
            "operator": str(operator),
        },
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=evidence[0].strength,
    )
