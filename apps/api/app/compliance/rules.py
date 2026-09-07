"""Vocabulari compartit del motor de compliment.

Tres coses viuen aqui i enlloc mes: la versio de les regles, els codis de regla
i els codis d'explicacio. Son identificadors **tancats i estables**: la
interficie en deriva el text, i un resultat guardat fa mesos s'ha de poder
tornar a llegir sense endevinar que volia dir.

Res d'aquest paquet fa servir cap LLM. La IA ja ha fet la seva feina
estructurant el plec; decidir si 2.450.000 >= 450.000 no es una tasca per a un
model de llenguatge, es una comparacio. Les regles han de ser explicites,
reproduibles, explicables i auditables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    CompanyEvidenceKind,
    EvidenceStrength,
)

#: Canviar el significat d'una regla obliga a pujar aixo. Cada assessment el
#: guarda: sense aixo, resultats calculats amb criteris diferents semblarien
#: comparables.
RULES_VERSION = "company-compliance-v1"


class RuleCode:
    """Identificadors de regla. Versionats un a un."""

    ECONOMIC_REVENUE_GTE = "economic_revenue_gte_v1"
    CERTIFICATION_EXACT = "certification_exact_v1"
    REGISTRATION_EXACT = "registration_exact_v1"
    INSURANCE_COVERAGE_GTE = "insurance_coverage_gte_v1"
    EXPERIENCE_COUNT_AMOUNT = "experience_count_amount_v1"
    QUALIFICATION_EXACT = "qualification_exact_v1"
    PERSONNEL_COUNT = "personnel_count_v1"
    #: Cap regla determinista cobreix aquesta categoria encara.
    NOT_AUTOMATABLE = "not_automatable_v1"
    #: El requisit no aplica en aquest context (lot, abast...).
    NOT_APPLICABLE = "not_applicable_v1"


class ExplanationCode:
    """Motiu del veredicte. El text de la UI se'n deriva; aqui no hi ha frases."""

    ECONOMIC_THRESHOLD_MET = "ECONOMIC_THRESHOLD_MET"
    ECONOMIC_THRESHOLD_NOT_MET = "ECONOMIC_THRESHOLD_NOT_MET"
    ECONOMIC_DATA_INCOMPLETE = "ECONOMIC_DATA_INCOMPLETE"
    ECONOMIC_CURRENCY_MISMATCH = "ECONOMIC_CURRENCY_MISMATCH"
    ECONOMIC_PERIOD_AMBIGUOUS = "ECONOMIC_PERIOD_AMBIGUOUS"

    CERTIFICATION_FOUND_VALID = "CERTIFICATION_FOUND_VALID"
    CERTIFICATION_FOUND_EXPIRED = "CERTIFICATION_FOUND_EXPIRED"
    CERTIFICATION_FOUND_NO_EXPIRY = "CERTIFICATION_FOUND_NO_EXPIRY"
    CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE = "CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE"
    CERTIFICATION_DATA_INCOMPLETE = "CERTIFICATION_DATA_INCOMPLETE"
    CERTIFICATION_NAME_AMBIGUOUS = "CERTIFICATION_NAME_AMBIGUOUS"

    REGISTRATION_FOUND_VALID = "REGISTRATION_FOUND_VALID"
    REGISTRATION_FOUND_EXPIRED = "REGISTRATION_FOUND_EXPIRED"
    REGISTRATION_NOT_FOUND_PROFILE_COMPLETE = "REGISTRATION_NOT_FOUND_PROFILE_COMPLETE"
    REGISTRATION_DATA_INCOMPLETE = "REGISTRATION_DATA_INCOMPLETE"
    REGISTRATION_CLASSIFICATION_REVIEW = "REGISTRATION_CLASSIFICATION_REVIEW"

    INSURANCE_COVERAGE_MET = "INSURANCE_COVERAGE_MET"
    INSURANCE_COVERAGE_NOT_MET = "INSURANCE_COVERAGE_NOT_MET"
    INSURANCE_EXPIRED = "INSURANCE_EXPIRED"
    INSURANCE_NOT_FOUND_PROFILE_COMPLETE = "INSURANCE_NOT_FOUND_PROFILE_COMPLETE"
    INSURANCE_DATA_INCOMPLETE = "INSURANCE_DATA_INCOMPLETE"
    INSURANCE_CURRENCY_MISMATCH = "INSURANCE_CURRENCY_MISMATCH"

    EXPERIENCE_COUNT_MET = "EXPERIENCE_COUNT_MET"
    EXPERIENCE_INSUFFICIENT = "EXPERIENCE_INSUFFICIENT"
    EXPERIENCE_DATA_INCOMPLETE = "EXPERIENCE_DATA_INCOMPLETE"
    EXPERIENCE_SIMILARITY_REVIEW = "EXPERIENCE_SIMILARITY_REVIEW"
    EXPERIENCE_EVIDENCE_MISSING = "EXPERIENCE_EVIDENCE_MISSING"

    QUALIFICATION_FOUND = "QUALIFICATION_FOUND"
    QUALIFICATION_NOT_FOUND_PROFILE_COMPLETE = "QUALIFICATION_NOT_FOUND_PROFILE_COMPLETE"
    QUALIFICATION_UNKNOWN = "QUALIFICATION_UNKNOWN"
    QUALIFICATION_AMBIGUOUS = "QUALIFICATION_AMBIGUOUS"

    PERSONNEL_COUNT_MET = "PERSONNEL_COUNT_MET"
    PERSONNEL_COUNT_NOT_MET = "PERSONNEL_COUNT_NOT_MET"
    PERSONNEL_DATA_INCOMPLETE = "PERSONNEL_DATA_INCOMPLETE"

    #: El requisit no esta prou estructurat per decidir-lo automaticament.
    RULE_NOT_AUTOMATABLE = "RULE_NOT_AUTOMATABLE"
    REQUIREMENT_VALUE_MISSING = "REQUIREMENT_VALUE_MISSING"
    SCOPE_NOT_APPLICABLE = "SCOPE_NOT_APPLICABLE"
    SCOPE_AMBIGUOUS = "SCOPE_AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Una dada corporativa que sosté el veredicte."""

    kind: CompanyEvidenceKind
    entity_id: Any
    label: str
    strength: EvidenceStrength = EvidenceStrength.MISSING
    document_id: Any = None


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """El que retorna una regla. Sense text lliure: nomes codis i parametres."""

    result: AssessmentResult
    rule_code: str
    explanation_code: str
    params: dict[str, Any] = field(default_factory=dict)
    mode: AssessmentMode = AssessmentMode.AUTOMATIC
    company_value: dict[str, Any] | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    strength: EvidenceStrength = EvidenceStrength.MISSING


def needs_review(
    rule_code: str, explanation_code: str, **params: Any
) -> RuleOutcome:
    """Atall per al cas "aixo demana judici huma"."""
    return RuleOutcome(
        result=AssessmentResult.NEEDS_REVIEW,
        rule_code=rule_code,
        explanation_code=explanation_code,
        params=params,
        mode=AssessmentMode.REVIEW_REQUIRED,
    )


def unknown(rule_code: str, explanation_code: str, **params: Any) -> RuleOutcome:
    """Atall per al cas "no tenim prou dada".

    Diferent de `does_not_satisfy`: no sabem, no vol dir que no es compleixi.
    """
    return RuleOutcome(
        result=AssessmentResult.UNKNOWN,
        rule_code=rule_code,
        explanation_code=explanation_code,
        params=params,
        mode=AssessmentMode.REVIEW_REQUIRED,
    )
