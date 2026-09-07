"""Motor: dispatch per categoria i derivacio d'elegibilitat.

Aqui viuen les decisions que mes facil seria equivocar, i per aixo son les mes
explicites del fitxer:

* **Un `unknown` no es un NO-GO.** No saber no es incomplir. Un mandatory sense
  dades demana completar el perfil, no descartar la licitacio.
* **Un `needs_review` tampoc.** Si la regla no es prou determinista, la decisio
  es de la persona.
* **Un blocador d'un requisit no verificat es `POTENTIAL`.** Mentre la qualitat
  de l'extractor real no estigui mesurada, deixar que una extraccio automatica
  provoqui un NO-GO definitiu seria regalar falsos negatius.
* **Els blocadors no es promitgen mai.** Vuit requisits complerts i un
  d'obligatori incomplert no fan un 89 % acceptable: fan un bloqueig.

L'elegibilitat i el pre-fit es mantenen separats a proposit. El pre-fit respon
"ens interessa?"; aixo respon "podem acreditar que complim?". Un 95 de pre-fit
amb un blocador confirmat es un NO-GO, i el 95 no pot amagar-ho.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.compliance.certifications import assess_certification
from app.compliance.economic import assess_economic_solvency
from app.compliance.experience import assess_experience
from app.compliance.insurance import assess_insurance
from app.compliance.personnel import assess_personnel, assess_qualification, assess_registration
from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import ExplanationCode, RuleCode, RuleOutcome, needs_review
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    BlockerStatus,
    EligibilityResult,
    RecommendationResult,
    RequirementCategory,
    RequirementObligation,
    RequirementScope,
    ReviewStatus,
)

#: Pre-fit a partir del qual una licitacio elegible es candidata de veritat.
GO_CANDIDATE_PREFIT_THRESHOLD = 60


def assess_requirement(
    requirement: Any, snapshot: ProfileSnapshot, *, deadline: date | None = None
) -> RuleOutcome:
    """Tria la regla segons la categoria del requisit.

    Una categoria sense regla determinista NO s'endevina: retorna revisio. Es
    preferible dir "aixo ho ha de mirar algu" que fabricar un veredicte.
    """
    category = requirement.category

    if category == RequirementCategory.ECONOMIC_SOLVENCY:
        return assess_economic_solvency(requirement, snapshot)
    if category == RequirementCategory.CERTIFICATION:
        return assess_certification(requirement, snapshot, deadline)
    if category == RequirementCategory.INSURANCE:
        return assess_insurance(requirement, snapshot, deadline)
    if category in (RequirementCategory.EXPERIENCE, RequirementCategory.TECHNICAL_SOLVENCY):
        return assess_experience(requirement, snapshot)
    if category in (
        RequirementCategory.REGISTRATION,
        RequirementCategory.BUSINESS_CLASSIFICATION,
    ):
        return assess_registration(requirement, snapshot, deadline)
    if category == RequirementCategory.PROFESSIONAL_QUALIFICATION:
        return assess_qualification(requirement, snapshot, deadline)
    if category == RequirementCategory.PERSONNEL:
        return assess_personnel(requirement, snapshot)

    # Terminis, documentacio administrativa, condicions tecniques i de
    # contracte: no son acreditables comparant dades del perfil.
    return needs_review(
        RuleCode.NOT_AUTOMATABLE,
        ExplanationCode.RULE_NOT_AUTOMATABLE,
        category=str(category),
    )


def applies_to_lot(
    requirement: Any, lot_id: Any | None, *, tender_has_lots: bool = True
) -> bool | None:
    """Diu si el requisit aplica al lot demanat. `None` = no es pot saber.

    Un abast desconegut nomes es ambigu si l'expedient te lots. Si no en te, la
    pregunta "a quin lot aplica?" no existeix, i tractar-la com a ambigua
    silenciaria l'avaluacio real de tots els requisits.
    """
    scope = requirement.scope
    if scope == RequirementScope.TENDER:
        return True
    if scope == RequirementScope.UNKNOWN:
        return True if not tender_has_lots else None
    if requirement.tender_lot_id is None:
        # Diu que es de lot pero no s'ha pogut resoldre quin.
        return None
    if lot_id is None:
        return True
    return requirement.tender_lot_id == lot_id


def derive_blocker_status(
    *,
    obligation: RequirementObligation,
    effective_result: AssessmentResult,
    requirement_review_status: ReviewStatus,
    assessment_reviewed: bool,
) -> BlockerStatus:
    """Quan un incompliment es un bloqueig, i de quina classe.

    Nomes `mandatory` + `does_not_satisfy` bloqueja. `unknown` i `needs_review`
    NO bloquegen mai: demanen accio, no descarten.

    Es `CONFIRMED` quan algu ha validat la font: o be el requisit esta verificat,
    o be una persona ha revisat aquest mateix assessment. Si no, `POTENTIAL`.
    """
    if obligation != RequirementObligation.MANDATORY:
        return BlockerStatus.NONE
    if effective_result != AssessmentResult.DOES_NOT_SATISFY:
        return BlockerStatus.NONE
    if requirement_review_status == ReviewStatus.VERIFIED or assessment_reviewed:
        return BlockerStatus.CONFIRMED
    return BlockerStatus.POTENTIAL


@dataclass(frozen=True, slots=True)
class EligibilitySummary:
    eligibility: EligibilityResult
    recommendation: RecommendationResult
    satisfies: int = 0
    does_not_satisfy: int = 0
    unknown: int = 0
    needs_review: int = 0
    not_applicable: int = 0
    mandatory_total: int = 0
    potential_blockers: int = 0
    confirmed_blockers: int = 0


def summarise(
    rows: list[tuple[RequirementObligation, AssessmentResult, BlockerStatus]],
    *,
    prefit_score: int | None = None,
) -> EligibilitySummary:
    """Agrega els assessments en elegibilitat i recomanacio.

    Cap mitjana: els comptadors es miren un a un i el bloqueig sempre domina.
    """
    counts = dict.fromkeys(AssessmentResult, 0)
    mandatory_total = 0
    potential = confirmed = 0
    mandatory_unresolved = 0

    for obligation, result, blocker in rows:
        counts[result] += 1
        if obligation == RequirementObligation.MANDATORY:
            mandatory_total += 1
            if result in (AssessmentResult.UNKNOWN, AssessmentResult.NEEDS_REVIEW):
                mandatory_unresolved += 1
        if blocker == BlockerStatus.CONFIRMED:
            confirmed += 1
        elif blocker == BlockerStatus.POTENTIAL:
            potential += 1

    if confirmed:
        eligibility = EligibilityResult.NO_GO
    elif potential or mandatory_unresolved:
        eligibility = EligibilityResult.REVIEW_REQUIRED
    else:
        eligibility = EligibilityResult.ELIGIBLE

    if eligibility == EligibilityResult.NO_GO:
        recommendation = RecommendationResult.NO_GO
    elif eligibility == EligibilityResult.REVIEW_REQUIRED:
        recommendation = RecommendationResult.REVIEW
    elif prefit_score is not None and prefit_score < GO_CANDIDATE_PREFIT_THRESHOLD:
        # Legal no vol dir interessant: son dimensions diferents i es mostren
        # per separat.
        recommendation = RecommendationResult.ELIGIBLE_LOW_FIT
    else:
        recommendation = RecommendationResult.GO_CANDIDATE

    return EligibilitySummary(
        eligibility=eligibility,
        recommendation=recommendation,
        satisfies=counts[AssessmentResult.SATISFIES],
        does_not_satisfy=counts[AssessmentResult.DOES_NOT_SATISFY],
        unknown=counts[AssessmentResult.UNKNOWN],
        needs_review=counts[AssessmentResult.NEEDS_REVIEW],
        not_applicable=counts[AssessmentResult.NOT_APPLICABLE],
        mandatory_total=mandatory_total,
        potential_blockers=potential,
        confirmed_blockers=confirmed,
    )


def not_applicable(reason: str = ExplanationCode.SCOPE_NOT_APPLICABLE) -> RuleOutcome:
    return RuleOutcome(
        result=AssessmentResult.NOT_APPLICABLE,
        rule_code=RuleCode.NOT_APPLICABLE,
        explanation_code=reason,
        mode=AssessmentMode.AUTOMATIC,
    )
