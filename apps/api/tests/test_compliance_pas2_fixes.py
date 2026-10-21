# ruff: noqa: F811  (els fixtures `tender` i `extraction_run` s'importen de test_compliance_api)
"""Validacio integral, Pas 2: tres correccions del motor de compliment.

1. Un "Recalcula" sobre la MATEIXA analisi conserva les revisions humanes (abans es perdien).
2. Els limits de les polisses entren a l'empremta del perfil: afegir-ne o canviar-ne un deixa el
   compliment desactualitzat i no es reutilitza el run antic.
3. Revisar a ma una avaluacio refa el bloqueig amb la modalitat del requisit, com el motor.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa

from app.compliance.profile import compute_profile_fingerprint, load_profile_snapshot
from app.models.compliance import CompanyRequirementAssessment
from app.models.credentials import CompanyInsurance, CompanyInsuranceLimit
from app.models.enums import (
    AnalysisRunStatus,
    AssessmentResult,
    BlockerStatus,
    InsuranceLimitKind,
    InsuranceType,
    RequirementCategory,
    RequirementModality,
    ReviewStatus,
)
from app.models.profile import CompanyFinancialYear
from app.models.requirements import RequirementExtractionRun
from app.services.compliance_analysis import (
    CARRIED_REVIEW_KEY,
    analyze_company_compliance,
    is_run_stale,
    review_assessment,
)
from tests.test_compliance_api import (  # noqa: F401  (fixtures)
    add_requirement,
    company_of,
    extraction_run,
    tender,
)

ECONOMIC_450K = {"amount": 450000, "currency": "EUR", "period": "annual"}


async def _only_assessment(db, run_id) -> CompanyRequirementAssessment:
    return await db.scalar(
        sa.select(CompanyRequirementAssessment).where(
            CompanyRequirementAssessment.assessment_run_id == run_id
        )
    )


# --------------------------------------------------------------------------- #
# 1. «Recalcula» conserva les revisions humanes                                 #
# --------------------------------------------------------------------------- #


async def test_recalculate_on_the_same_analysis_keeps_the_human_review(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.TECHNICAL_REQUIREMENT,
        value=None,
        title="Memoria tecnica signada",
    )
    first = (await analyze_company_compliance(db, company, tender)).run
    assessment = await _only_assessment(db, first.id)
    assert assessment.automatic_result == AssessmentResult.NEEDS_REVIEW
    await review_assessment(
        db,
        assessment,
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="La memoria es presenta signada.",
        user_id=user.id,
    )

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    assert second.id != first.id
    carried = await _only_assessment(db, second.id)
    assert carried.reviewed_result == AssessmentResult.SATISFIES
    assert carried.review_status == ReviewStatus.VERIFIED
    assert carried.review_note == "La memoria es presenta signada."
    assert carried.reviewed_by == user.id
    assert carried.reviewed_at is not None
    assert carried.automatic_result == AssessmentResult.NEEDS_REVIEW, "el calcul no s'esborra"
    trace = carried.explanation_params[CARRIED_REVIEW_KEY]
    assert trace["from_run"] == str(first.id)
    assert trace["automatic_changed"] is False
    # El recompte del run nou ja compta la revisio (efectiu = compleix), com `_recount_run`.
    assert second.satisfies_count == 1
    assert second.needs_review_count == 0


async def test_a_carried_review_says_when_the_automatic_result_changed(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    await add_requirement(db, extraction_run, tender, value=ECONOMIC_450K)
    first = (await analyze_company_compliance(db, company, tender)).run
    assessment = await _only_assessment(db, first.id)
    assert assessment.automatic_result == AssessmentResult.UNKNOWN
    await review_assessment(
        db,
        assessment,
        reviewed_result=AssessmentResult.DOES_NOT_SATISFY,
        review_status=ReviewStatus.VERIFIED,
        note="Sense comptes dipositats.",
        user_id=user.id,
    )
    # Dades noves al perfil: el motor ara pot decidir sol.
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("900000"), currency="EUR"
        )
    )
    await db.commit()

    second = (await analyze_company_compliance(db, company, tender)).run

    carried = await _only_assessment(db, second.id)
    assert carried.automatic_result == AssessmentResult.SATISFIES
    assert carried.reviewed_result == AssessmentResult.DOES_NOT_SATISFY, (
        "la persona mana fins que hi torni"
    )
    assert carried.explanation_params[CARRIED_REVIEW_KEY] == {
        "from_run": str(first.id),
        "previous_automatic_result": "unknown",
        "automatic_changed": True,
    }
    # Revisat i verificat: el bloqueig continua confirmat, igual que al run anterior.
    assert carried.blocker_status == BlockerStatus.CONFIRMED
    assert second.confirmed_blockers_count == 1


async def test_a_new_requirement_analysis_starts_without_reviews(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """Una revisio parla d'un requisit concret: una analisi nova (uns altres ids) comenca neta."""
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    await add_requirement(
        db, extraction_run, tender, category=RequirementCategory.TECHNICAL_REQUIREMENT, value=None
    )
    first = (await analyze_company_compliance(db, company, tender)).run
    await review_assessment(
        db,
        await _only_assessment(db, first.id),
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Revisat.",
        user_id=user.id,
    )
    extraction_run.is_current = False
    await db.commit()
    newer = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint="e" * 64,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        provider="fake",
        model="fake-model-v1",
        run_basis="real",
        is_current=True,
    )
    db.add(newer)
    await db.commit()
    await add_requirement(
        db, newer, tender, category=RequirementCategory.TECHNICAL_REQUIREMENT, value=None
    )

    second = (await analyze_company_compliance(db, company, tender)).run

    fresh = await _only_assessment(db, second.id)
    assert fresh.reviewed_result is None
    assert fresh.review_status == ReviewStatus.UNREVIEWED
    assert CARRIED_REVIEW_KEY not in (fresh.explanation_params or {})


async def test_without_any_review_the_recount_is_unchanged(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """Sense revisions, el run nou es identic al d'abans de la correccio (res a traspassar)."""
    company = await company_of(db, tenant_a)
    await add_requirement(db, extraction_run, tender, value=ECONOMIC_450K)
    first = (await analyze_company_compliance(db, company, tender)).run
    second = (await analyze_company_compliance(db, company, tender, force=True)).run
    fresh = await _only_assessment(db, second.id)
    assert CARRIED_REVIEW_KEY not in (fresh.explanation_params or {})
    assert (second.unknown_count, second.satisfies_count) == (
        first.unknown_count,
        first.satisfies_count,
    )


# --------------------------------------------------------------------------- #
# 2. Els limits de la polissa a l'empremta del perfil                           #
# --------------------------------------------------------------------------- #


async def test_insurance_limits_are_part_of_the_profile_fingerprint(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    company = await company_of(db, tenant_a)
    await add_requirement(
        db, extraction_run, tender, category=RequirementCategory.TECHNICAL_REQUIREMENT, value=None
    )
    policy = CompanyInsurance(
        company_id=company.id,
        type=InsuranceType.CIVIL_LIABILITY,
        currency="EUR",
        expires_at=date(2027, 12, 31),
    )
    db.add(policy)
    await db.commit()
    first = (await analyze_company_compliance(db, company, tender)).run
    assert await is_run_stale(db, first, company) is False

    # Afegir un limit: el perfil ha canviat i el compliment ja no es vigent.
    limit = CompanyInsuranceLimit(
        company_id=company.id,
        insurance_id=policy.id,
        kind=InsuranceLimitKind.PER_CLAIM,
        amount=Decimal("300000"),
        currency="EUR",
    )
    db.add(limit)
    await db.commit()
    # La col·leccio `limits` de la polissa ja carregada a la sessio no se n'assabenta sola.
    db.expire(policy)
    assert await is_run_stale(db, first, company) is True
    second = await analyze_company_compliance(db, company, tender)
    assert second.reused is False, "amb un limit nou, no es reutilitza el run antic"
    fingerprint = second.run.company_profile_fingerprint

    # Canviar-ne l'import tambe.
    limit.amount = Decimal("150000")
    await db.commit()
    db.expire(policy)
    snapshot = await load_profile_snapshot(db, company)
    assert compute_profile_fingerprint(snapshot) != fingerprint
    assert await is_run_stale(db, second.run, company) is True


# --------------------------------------------------------------------------- #
# 3. Revisar a ma respecta la modalitat                                          #
# --------------------------------------------------------------------------- #


async def _reviewed_breach(db, tenant_a, tender, extraction_run, modality):
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("100"), currency="EUR"
        )
    )
    await db.commit()
    requirement = await add_requirement(db, extraction_run, tender, value=ECONOMIC_450K)
    requirement.modality = modality
    if modality == RequirementModality.ALTERNATIVE:
        requirement.alternative_group = f"g-{uuid.uuid4().hex[:6]}"
    await db.commit()
    run = (await analyze_company_compliance(db, company, tender)).run
    assessment = await _only_assessment(db, run.id)
    return await review_assessment(
        db,
        assessment,
        reviewed_result=AssessmentResult.DOES_NOT_SATISFY,
        review_status=ReviewStatus.VERIFIED,
        note="Confirmat: no s'arriba al volum.",
        user_id=user.id,
    )


async def test_reviewing_an_alternative_branch_does_not_confirm_a_blocker(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    reviewed = await _reviewed_breach(
        db, tenant_a, tender, extraction_run, RequirementModality.ALTERNATIVE
    )
    assert reviewed.effective_result == AssessmentResult.DOES_NOT_SATISFY
    assert reviewed.blocker_status == BlockerStatus.NONE, (
        "una via alternativa no bloqueja tota sola"
    )


async def test_reviewing_a_conditional_requirement_does_not_confirm_a_blocker(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    reviewed = await _reviewed_breach(
        db, tenant_a, tender, extraction_run, RequirementModality.CONDITIONAL
    )
    assert reviewed.blocker_status == BlockerStatus.NONE


async def test_reviewing_a_plain_mandatory_breach_still_confirms_the_blocker(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    reviewed = await _reviewed_breach(
        db, tenant_a, tender, extraction_run, RequirementModality.UNKNOWN
    )
    assert reviewed.blocker_status == BlockerStatus.CONFIRMED
