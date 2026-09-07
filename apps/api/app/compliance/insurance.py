"""Assegurances: cobertura, moneda i vigencia.

Com a la solvencia economica, no hi ha conversio de divisa: si el plec demana
euros i la polissa es en una altra moneda, la resposta es revisio, no una
estimacio.
"""

from __future__ import annotations

import re
from datetime import date
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
    DocumentCategory,
    EvidenceStrength,
    InsuranceType,
    LinkedEntityType,
)

#: Com anomena el plec cada tipus de polissa. Nomes families inequivoques.
_TYPE_HINTS: tuple[tuple[re.Pattern[str], InsuranceType], ...] = (
    (re.compile(r"responsabilidad\s+civil|responsabilitat\s+civil|civil\s+liability", re.I),
     InsuranceType.CIVIL_LIABILITY),
    (re.compile(r"indemnizaci[oó]n\s+profesional|professional\s+indemnity", re.I),
     InsuranceType.PROFESSIONAL_INDEMNITY),
    (re.compile(r"todo\s+riesgo|all\s+risk", re.I), InsuranceType.ALL_RISK),
    (re.compile(r"aval|surety|caucion|cauci[oó]n", re.I), InsuranceType.SURETY_BOND),
)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def infer_insurance_type(raw: str | None) -> InsuranceType | None:
    if not raw:
        return None
    for pattern, kind in _TYPE_HINTS:
        if pattern.search(raw):
            return kind
    return None


def assess_insurance(
    requirement: Any, snapshot: ProfileSnapshot, deadline: date | None
) -> RuleOutcome:
    value = requirement.value_json or {}
    required_amount = _decimal(value.get("coverage_amount") or value.get("amount"))
    if required_amount is None:
        return needs_review(
            RuleCode.INSURANCE_COVERAGE_GTE, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )

    currency = (value.get("currency") or "EUR").strip().upper()
    required_type = infer_insurance_type(value.get("type") or requirement.title)

    candidates = list(snapshot.insurances)
    if required_type is not None:
        typed = [ins for ins in candidates if ins.type == required_type]
        # Si el plec demana un tipus concret i no en tenim cap d'aquell tipus,
        # no val comparar-hi una polissa d'una altra familia.
        candidates = typed

    if not candidates:
        if snapshot.is_section_complete("insurances"):
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.INSURANCE_COVERAGE_GTE,
                explanation_code=ExplanationCode.INSURANCE_NOT_FOUND_PROFILE_COMPLETE,
                params={
                    "required_amount": float(required_amount),
                    "currency": currency,
                    "required_type": str(required_type) if required_type else None,
                },
                mode=AssessmentMode.AUTOMATIC,
            )
        return unknown(
            RuleCode.INSURANCE_COVERAGE_GTE,
            ExplanationCode.INSURANCE_DATA_INCOMPLETE,
            required_amount=float(required_amount),
            currency=currency,
        )

    with_amount = [ins for ins in candidates if ins.coverage_amount is not None]
    if not with_amount:
        return unknown(
            RuleCode.INSURANCE_COVERAGE_GTE,
            ExplanationCode.INSURANCE_DATA_INCOMPLETE,
            reason="no_coverage_amount",
        )

    comparable = [ins for ins in with_amount if (ins.currency or "EUR").upper() == currency]
    if not comparable:
        return needs_review(
            RuleCode.INSURANCE_COVERAGE_GTE,
            ExplanationCode.INSURANCE_CURRENCY_MISMATCH,
            required_currency=currency,
            company_currencies=sorted({(i.currency or "EUR").upper() for i in with_amount}),
        )

    best = max(comparable, key=lambda i: Decimal(i.coverage_amount))
    coverage = Decimal(best.coverage_amount)

    documents = snapshot.documents_for(LinkedEntityType.COMPANY_INSURANCE, best.id)
    policy = next((d for d in documents if d.category == DocumentCategory.INSURANCE), None)
    best_document = policy or (documents[0] if documents else None)
    strength = (
        EvidenceStrength.STRONG
        if policy
        else (EvidenceStrength.SUPPORTING if best_document else EvidenceStrength.MISSING)
    )
    evidence = (
        EvidenceRef(
            kind=CompanyEvidenceKind.INSURANCE,
            entity_id=best.id,
            label=f"{best.type}: {coverage:.2f} {currency}",
            strength=strength,
            document_id=best_document.id if best_document else None,
        ),
    )
    company_value = {
        "type": str(best.type),
        "coverage_amount": float(coverage),
        "currency": currency,
        "expires_at": str(best.expires_at) if best.expires_at else None,
    }
    params = {
        "required_amount": float(required_amount),
        "actual_amount": float(coverage),
        "currency": currency,
        "type": str(best.type),
    }

    if coverage < required_amount:
        return RuleOutcome(
            result=AssessmentResult.DOES_NOT_SATISFY,
            rule_code=RuleCode.INSURANCE_COVERAGE_GTE,
            explanation_code=ExplanationCode.INSURANCE_COVERAGE_NOT_MET,
            params=params,
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=strength,
        )

    # La cobertura arriba; queda mirar que sigui vigent al termini.
    if best.expires_at is not None and deadline is not None and best.expires_at < deadline:
        return RuleOutcome(
            result=AssessmentResult.DOES_NOT_SATISFY,
            rule_code=RuleCode.INSURANCE_COVERAGE_GTE,
            explanation_code=ExplanationCode.INSURANCE_EXPIRED,
            params={**params, "expires_at": str(best.expires_at), "deadline": str(deadline)},
            mode=AssessmentMode.AUTOMATIC,
            company_value=company_value,
            evidence=evidence,
            strength=strength,
        )

    return RuleOutcome(
        result=AssessmentResult.SATISFIES,
        rule_code=RuleCode.INSURANCE_COVERAGE_GTE,
        explanation_code=ExplanationCode.INSURANCE_COVERAGE_MET,
        params=params,
        mode=AssessmentMode.AUTOMATIC,
        company_value=company_value,
        evidence=evidence,
        strength=strength,
    )
