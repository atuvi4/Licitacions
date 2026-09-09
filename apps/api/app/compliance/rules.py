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
#: v2: les regles entenen agregacions, percentatges amb base, limits
#: d'assegurança separats, vies alternatives i equivalencies. Un resultat
#: calculat amb la v1 no es comparable amb un d'ara.
RULES_VERSION = "company-compliance-v2"


class RuleCode:
    """Identificadors de regla. Versionats un a un."""

    ECONOMIC_REVENUE_GTE = "economic_revenue_gte_v1"
    #: Facturacio combinada segons l'agregacio que demana el plec.
    ECONOMIC_AGGREGATE = "economic_turnover_aggregate_v2"
    CERTIFICATION_EXACT = "certification_exact_v1"
    REGISTRATION_EXACT = "registration_exact_v1"
    INSURANCE_COVERAGE_GTE = "insurance_coverage_gte_v1"
    #: Cada limit de la polissa comparat per separat.
    INSURANCE_LIMITS = "insurance_limits_v2"
    EXPERIENCE_COUNT_AMOUNT = "experience_count_amount_v1"
    #: Recompte, import individual i import acumulat, amb periode i agregacio.
    EXPERIENCE_THRESHOLDS = "experience_thresholds_v2"
    QUALIFICATION_EXACT = "qualification_exact_v1"
    PERSONNEL_COUNT = "personnel_count_v1"
    #: Cap regla determinista cobreix aquesta categoria encara.
    NOT_AUTOMATABLE = "not_automatable_v1"
    #: El requisit ve amb una estructura v2 que cap regla actual sap llegir.
    V2_STRUCTURE_UNSUPPORTED = "v2_structure_unsupported_v1"
    #: El requisit es una via d'una disjuncio: el resol el grup, no ell sol.
    ALTERNATIVE_GROUP = "alternative_group_v2"
    #: El requisit no aplica en aquest context (lot, abast...).
    NOT_APPLICABLE = "not_applicable_v1"


class ExplanationCode:
    """Motiu del veredicte. El text de la UI se'n deriva; aqui no hi ha frases."""

    ECONOMIC_THRESHOLD_MET = "ECONOMIC_THRESHOLD_MET"
    ECONOMIC_THRESHOLD_NOT_MET = "ECONOMIC_THRESHOLD_NOT_MET"
    ECONOMIC_DATA_INCOMPLETE = "ECONOMIC_DATA_INCOMPLETE"
    ECONOMIC_CURRENCY_MISMATCH = "ECONOMIC_CURRENCY_MISMATCH"
    ECONOMIC_PERIOD_AMBIGUOUS = "ECONOMIC_PERIOD_AMBIGUOUS"
    #: Falten exercicis que l'agregacio demanada necessitava.
    ECONOMIC_PERIOD_INCOMPLETE = "ECONOMIC_PERIOD_INCOMPLETE"
    #: Un exercici registrat no arriba al llindar que s'exigeix a cadascun.
    ECONOMIC_YEAR_BELOW_THRESHOLD = "ECONOMIC_YEAR_BELOW_THRESHOLD"
    #: Patrimoni net, fons de maniobra, ratios: el perfil no els te.
    ECONOMIC_METRIC_UNSUPPORTED = "ECONOMIC_METRIC_UNSUPPORTED"

    CERTIFICATION_FOUND_VALID = "CERTIFICATION_FOUND_VALID"
    CERTIFICATION_FOUND_EXPIRED = "CERTIFICATION_FOUND_EXPIRED"
    CERTIFICATION_FOUND_NO_EXPIRY = "CERTIFICATION_FOUND_NO_EXPIRY"
    CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE = "CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE"
    CERTIFICATION_DATA_INCOMPLETE = "CERTIFICATION_DATA_INCOMPLETE"
    CERTIFICATION_NAME_AMBIGUOUS = "CERTIFICATION_NAME_AMBIGUOUS"
    #: El plec admet equivalents, l'empresa no te l'exacta i en te d'altres:
    #: quina hi equival no ho decideix una regla.
    CERTIFICATION_EQUIVALENCE_REVIEW = "CERTIFICATION_EQUIVALENCE_REVIEW"

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
    #: La polissa no declara un dels limits que el plec exigeix. No saber-lo no
    #: es incomplir-lo.
    INSURANCE_LIMIT_UNKNOWN = "INSURANCE_LIMIT_UNKNOWN"
    #: Un limit declarat es inferior al que demana el plec.
    INSURANCE_LIMIT_BELOW = "INSURANCE_LIMIT_BELOW"
    INSURANCE_LIMITS_MET = "INSURANCE_LIMITS_MET"
    #: El plec exigeix l'organ com a assegurat addicional i la polissa diu que no.
    INSURANCE_ADDITIONAL_INSURED_MISSING = "INSURANCE_ADDITIONAL_INSURED_MISSING"
    #: L'exigeix i la polissa no ho declara. No consta no es "no".
    INSURANCE_ADDITIONAL_INSURED_UNKNOWN = "INSURANCE_ADDITIONAL_INSURED_UNKNOWN"
    #: Els imports encaixen pero queda una condicio que aquesta capa no
    #: comprova: vigencia durant l'execucio, periode de garantia, document
    #: acreditatiu concret. Amb una condicio sense mirar no hi ha `satisfies`.
    INSURANCE_CONDITION_NOT_CHECKED = "INSURANCE_CONDITION_NOT_CHECKED"

    EXPERIENCE_COUNT_MET = "EXPERIENCE_COUNT_MET"
    EXPERIENCE_INSUFFICIENT = "EXPERIENCE_INSUFFICIENT"
    EXPERIENCE_DATA_INCOMPLETE = "EXPERIENCE_DATA_INCOMPLETE"
    EXPERIENCE_SIMILARITY_REVIEW = "EXPERIENCE_SIMILARITY_REVIEW"
    EXPERIENCE_EVIDENCE_MISSING = "EXPERIENCE_EVIDENCE_MISSING"
    #: La suma dels contractes del periode no arriba a l'import acumulat.
    EXPERIENCE_ACCUMULATED_NOT_MET = "EXPERIENCE_ACCUMULATED_NOT_MET"
    EXPERIENCE_ACCUMULATED_MET = "EXPERIENCE_ACCUMULATED_MET"

    QUALIFICATION_FOUND = "QUALIFICATION_FOUND"
    QUALIFICATION_NOT_FOUND_PROFILE_COMPLETE = "QUALIFICATION_NOT_FOUND_PROFILE_COMPLETE"
    QUALIFICATION_UNKNOWN = "QUALIFICATION_UNKNOWN"
    QUALIFICATION_AMBIGUOUS = "QUALIFICATION_AMBIGUOUS"

    PERSONNEL_COUNT_MET = "PERSONNEL_COUNT_MET"
    PERSONNEL_COUNT_NOT_MET = "PERSONNEL_COUNT_NOT_MET"
    PERSONNEL_DATA_INCOMPLETE = "PERSONNEL_DATA_INCOMPLETE"

    #: El requisit no esta prou estructurat per decidir-lo automaticament.
    RULE_NOT_AUTOMATABLE = "RULE_NOT_AUTOMATABLE"

    # --- Estructures v2 que cap regla no sap resoldre encara ----------------
    #: Una de diverses vies d'acreditacio. Decidir-la sol seria inventar-se que
    #: l'empresa ha triat aquesta.
    V2_ALTERNATIVE_PATH = "V2_ALTERNATIVE_PATH"
    #: El requisit nomes obliga si es dona una condicio que el motor no avalua.
    V2_CONDITIONAL = "V2_CONDITIONAL"
    #: "o equivalent": quina certificacio hi equival no ho decideix una regla.
    V2_EQUIVALENCE_ALLOWED = "V2_EQUIVALENCE_ALLOWED"
    #: "el de major volum dels tres ultims", mitjanes, acumulats...
    V2_AGGREGATION_UNSUPPORTED = "V2_AGGREGATION_UNSUPPORTED"
    #: Un llindar expressat com a percentatge d'una base del contracte.
    V2_PERCENTAGE_THRESHOLD = "V2_PERCENTAGE_THRESHOLD"
    #: El percentatge te base pero la base no es pot llegir de l'expedient.
    PERCENTAGE_BASE_UNAVAILABLE = "PERCENTAGE_BASE_UNAVAILABLE"
    #: Sublimits (per victima, per sinistre) que la regla actual no compara.
    V2_INSURANCE_SUBLIMITS = "V2_INSURANCE_SUBLIMITS"
    #: Valor v2 sense forma reconeguda.
    V2_VALUE_NOT_INTERPRETABLE = "V2_VALUE_NOT_INTERPRETABLE"
    # --- Vies alternatives -------------------------------------------------
    #: Una altra via de la disjuncio ja acredita el requisit.
    ALTERNATIVE_GROUP_SATISFIED = "ALTERNATIVE_GROUP_SATISFIED"
    #: Cap via coneguda acredita el requisit i la disjuncio esta completa.
    ALTERNATIVE_GROUP_NOT_SATISFIED = "ALTERNATIVE_GROUP_NOT_SATISFIED"
    #: Alguna via es desconeguda, o el plec nomes n'ha donat una: no es pot
    #: descartar que l'altra acrediti la solvencia.
    ALTERNATIVE_GROUP_INCOMPLETE = "ALTERNATIVE_GROUP_INCOMPLETE"

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
