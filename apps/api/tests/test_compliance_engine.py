"""Bloqueig, elegibilitat i recomanacio.

Aqui es prova la part que decideix si una licitacio queda descartada. Les
propietats que es fixen son les que costarien diners si es trenquessin: que un
`unknown` no descarti mai, que una extraccio no verificada no provoqui un NO-GO
definitiu, i que cap mitjana pugui dissoldre un blocador.
"""

from __future__ import annotations

import pytest

from app.compliance.engine import (
    GO_CANDIDATE_PREFIT_THRESHOLD,
    derive_blocker_status,
    summarise,
)
from app.models.enums import (
    AssessmentResult,
    BlockerStatus,
    EligibilityResult,
    RecommendationResult,
    RequirementObligation,
    ReviewStatus,
)

MANDATORY = RequirementObligation.MANDATORY
SCORED = RequirementObligation.SCORED


# --------------------------------------------------------------------------- #
# Bloqueig                                                                      #
# --------------------------------------------------------------------------- #


def test_a_verified_mandatory_breach_is_a_confirmed_blocker():
    status = derive_blocker_status(
        obligation=MANDATORY,
        effective_result=AssessmentResult.DOES_NOT_SATISFY,
        requirement_review_status=ReviewStatus.VERIFIED,
        assessment_reviewed=False,
    )

    assert status == BlockerStatus.CONFIRMED


def test_an_unreviewed_mandatory_breach_is_only_potential():
    """Mentre l'extractor no estigui mesurat, no pot provocar un NO-GO definitiu."""
    status = derive_blocker_status(
        obligation=MANDATORY,
        effective_result=AssessmentResult.DOES_NOT_SATISFY,
        requirement_review_status=ReviewStatus.UNREVIEWED,
        assessment_reviewed=False,
    )

    assert status == BlockerStatus.POTENTIAL


def test_a_reviewed_assessment_confirms_the_blocker():
    """Si una persona respon del veredicte, ja es pot confirmar."""
    status = derive_blocker_status(
        obligation=MANDATORY,
        effective_result=AssessmentResult.DOES_NOT_SATISFY,
        requirement_review_status=ReviewStatus.UNREVIEWED,
        assessment_reviewed=True,
    )

    assert status == BlockerStatus.CONFIRMED


@pytest.mark.parametrize(
    "result",
    [AssessmentResult.UNKNOWN, AssessmentResult.NEEDS_REVIEW, AssessmentResult.NOT_APPLICABLE],
)
def test_only_a_demonstrated_breach_can_block(result: AssessmentResult):
    """No saber no es incomplir. Aquesta es la regla que evita NO-GOs falsos."""
    status = derive_blocker_status(
        obligation=MANDATORY,
        effective_result=result,
        requirement_review_status=ReviewStatus.VERIFIED,
        assessment_reviewed=True,
    )

    assert status == BlockerStatus.NONE


def test_a_scored_requirement_never_blocks():
    """"Se valorara" dona punts; no complir-ho no exclou ningu."""
    status = derive_blocker_status(
        obligation=SCORED,
        effective_result=AssessmentResult.DOES_NOT_SATISFY,
        requirement_review_status=ReviewStatus.VERIFIED,
        assessment_reviewed=True,
    )

    assert status == BlockerStatus.NONE


# --------------------------------------------------------------------------- #
# Elegibilitat                                                                  #
# --------------------------------------------------------------------------- #


def test_all_mandatory_satisfied_is_eligible():
    rows = [
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (SCORED, AssessmentResult.DOES_NOT_SATISFY, BlockerStatus.NONE),
    ]

    summary = summarise(rows, prefit_score=80)

    assert summary.eligibility == EligibilityResult.ELIGIBLE
    assert summary.recommendation == RecommendationResult.GO_CANDIDATE


def test_a_confirmed_blocker_is_a_no_go():
    rows = [
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (MANDATORY, AssessmentResult.DOES_NOT_SATISFY, BlockerStatus.CONFIRMED),
    ]

    summary = summarise(rows, prefit_score=80)

    assert summary.eligibility == EligibilityResult.NO_GO
    assert summary.recommendation == RecommendationResult.NO_GO
    assert summary.confirmed_blockers == 1


def test_a_potential_blocker_is_review_not_a_no_go():
    rows = [(MANDATORY, AssessmentResult.DOES_NOT_SATISFY, BlockerStatus.POTENTIAL)]

    summary = summarise(rows, prefit_score=90)

    assert summary.eligibility == EligibilityResult.REVIEW_REQUIRED
    assert summary.recommendation == RecommendationResult.REVIEW
    assert summary.potential_blockers == 1


def test_a_mandatory_unknown_requires_review():
    """Falten dades: cal completar el perfil, no descartar la licitacio."""
    rows = [
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (MANDATORY, AssessmentResult.UNKNOWN, BlockerStatus.NONE),
    ]

    summary = summarise(rows, prefit_score=90)

    assert summary.eligibility == EligibilityResult.REVIEW_REQUIRED
    assert summary.recommendation == RecommendationResult.REVIEW


def test_a_mandatory_needs_review_requires_review():
    rows = [(MANDATORY, AssessmentResult.NEEDS_REVIEW, BlockerStatus.NONE)]

    assert summarise(rows).eligibility == EligibilityResult.REVIEW_REQUIRED


def test_a_scored_unknown_does_not_hold_back_eligibility():
    rows = [
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (SCORED, AssessmentResult.UNKNOWN, BlockerStatus.NONE),
    ]

    assert summarise(rows, prefit_score=80).eligibility == EligibilityResult.ELIGIBLE


def test_not_applicable_counts_as_resolved():
    rows = [
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (MANDATORY, AssessmentResult.NOT_APPLICABLE, BlockerStatus.NONE),
    ]

    assert summarise(rows, prefit_score=80).eligibility == EligibilityResult.ELIGIBLE


# --------------------------------------------------------------------------- #
# El pre-fit no pot amagar un bloqueig                                          #
# --------------------------------------------------------------------------- #


def test_a_high_prefit_cannot_hide_a_confirmed_blocker():
    """§60: el 95 no pot dissoldre un bloqueig. Cap mitjana."""
    rows = [
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (MANDATORY, AssessmentResult.DOES_NOT_SATISFY, BlockerStatus.CONFIRMED),
    ]

    summary = summarise(rows, prefit_score=95)

    assert summary.eligibility == EligibilityResult.NO_GO
    assert summary.recommendation == RecommendationResult.NO_GO
    assert summary.satisfies == 3, "els altres tres es compten, pero no compensen"


def test_a_low_prefit_with_no_blockers_is_eligible_but_low_fit():
    """§61: legal i poc interessant son coses diferents."""
    rows = [(MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE)]

    summary = summarise(rows, prefit_score=GO_CANDIDATE_PREFIT_THRESHOLD - 1)

    assert summary.eligibility == EligibilityResult.ELIGIBLE
    assert summary.recommendation == RecommendationResult.ELIGIBLE_LOW_FIT


def test_eligibility_does_not_depend_on_the_prefit():
    rows = [(MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE)]

    low = summarise(rows, prefit_score=5)
    high = summarise(rows, prefit_score=99)

    assert low.eligibility == high.eligibility == EligibilityResult.ELIGIBLE
    assert low.recommendation != high.recommendation, "nomes canvia la recomanacio"


def test_counts_are_reported_one_by_one_never_averaged():
    rows = [
        (MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE),
        (MANDATORY, AssessmentResult.DOES_NOT_SATISFY, BlockerStatus.POTENTIAL),
        (MANDATORY, AssessmentResult.UNKNOWN, BlockerStatus.NONE),
        (MANDATORY, AssessmentResult.NEEDS_REVIEW, BlockerStatus.NONE),
        (SCORED, AssessmentResult.SATISFIES, BlockerStatus.NONE),
    ]

    summary = summarise(rows)

    assert summary.satisfies == 2
    assert summary.does_not_satisfy == 1
    assert summary.unknown == 1
    assert summary.needs_review == 1
    assert summary.mandatory_total == 4
    assert summary.potential_blockers == 1
    assert not hasattr(summary, "score"), "no hi ha cap puntuacio agregada, a proposit"
