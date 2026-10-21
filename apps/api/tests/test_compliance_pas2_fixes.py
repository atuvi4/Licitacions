# ruff: noqa: F811  (els fixtures `tender` i `extraction_run` s'importen de test_compliance_api)
"""Validacio integral, Pas 2: tres correccions del motor de compliment.

1. Un "Recalcula" sobre la MATEIXA analisi conserva les revisions humanes (abans es perdien),
   pero nomes les que encara parlen del mateix: una confirmacio d'un resultat que ha canviat,
   o la revisio d'un requisit que s'ha editat, no es conserven.
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
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
    ReviewStatus,
)
from app.models.profile import CompanyFinancialYear
from app.models.requirements import RequirementExtractionRun
from app.models.tenders import TenderLot
from app.services import manual_requirement
from app.services.compliance_analysis import (
    CARRIED_REVIEW_KEY,
    DROPPED_REVIEW_KEY,
    REQUIREMENT_IDENTITY_VERSION,
    REVIEW_BASIS_KEY,
    analyze_company_compliance,
    is_run_stale,
    requirement_identity,
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
        "reviewed_against": "unknown",
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


async def _all_assessments(db, run_id) -> list[CompanyRequirementAssessment]:
    return list(
        (
            await db.scalars(
                sa.select(CompanyRequirementAssessment).where(
                    CompanyRequirementAssessment.assessment_run_id == run_id
                )
            )
        ).all()
    )


async def _revenue(db, company, amount: str) -> CompanyFinancialYear:
    year = CompanyFinancialYear(
        company_id=company.id, year=2025, revenue=Decimal(amount), currency="EUR"
    )
    db.add(year)
    await db.commit()
    return year


async def test_a_confirmation_is_not_carried_when_the_automatic_result_changed(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """«Confirma» diu «el sistema te rao» sobre UN resultat: no confirma el resultat nou."""
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    year = await _revenue(db, company, "900000")
    await add_requirement(db, extraction_run, tender, value=ECONOMIC_450K)
    first = (await analyze_company_compliance(db, company, tender)).run
    confirmed = await _only_assessment(db, first.id)
    assert confirmed.automatic_result == AssessmentResult.SATISFIES
    await review_assessment(
        db,
        confirmed,
        reviewed_result=None,
        review_status=ReviewStatus.VERIFIED,
        note=None,
        user_id=user.id,
    )
    # La xifra del perfil es corregeix: ara no s'arriba al volum.
    year.revenue = Decimal("100")
    await db.commit()

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    fresh = await _only_assessment(db, second.id)
    assert fresh.automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert fresh.review_status == ReviewStatus.UNREVIEWED
    assert fresh.reviewed_by is None
    assert fresh.blocker_status == BlockerStatus.POTENTIAL, "ningu ha confirmat cap incompliment"
    assert (second.confirmed_blockers_count, second.potential_blockers_count) == (0, 1)
    assert CARRIED_REVIEW_KEY not in fresh.explanation_params
    assert fresh.explanation_params[DROPPED_REVIEW_KEY] == {
        "from_run": str(first.id),
        "reviewed_against": "satisfies",
        "reviewed_result": None,
        "review_note": None,
        "reason": "automatic_changed",
    }

    # Un altre recalcul: la fila continua sense revisar (segur); l'avis d'abans ja no hi passa.
    third = (await analyze_company_compliance(db, company, tender, force=True)).run
    again = await _only_assessment(db, third.id)
    assert (again.review_status, again.blocker_status) == (
        ReviewStatus.UNREVIEWED,
        BlockerStatus.POTENTIAL,
    )
    assert DROPPED_REVIEW_KEY not in (again.explanation_params or {})


async def test_a_confirmation_is_kept_when_the_automatic_result_did_not_change(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    await _revenue(db, company, "100")
    await add_requirement(db, extraction_run, tender, value=ECONOMIC_450K)
    first = (await analyze_company_compliance(db, company, tender)).run
    reviewed = await review_assessment(
        db,
        await _only_assessment(db, first.id),
        reviewed_result=None,
        review_status=ReviewStatus.VERIFIED,
        note=None,
        user_id=user.id,
    )
    assert reviewed.blocker_status == BlockerStatus.CONFIRMED

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    carried = await _only_assessment(db, second.id)
    assert carried.review_status == ReviewStatus.VERIFIED
    assert carried.reviewed_result is None
    assert carried.blocker_status == BlockerStatus.CONFIRMED, "la persona va confirmar aixo mateix"
    assert carried.explanation_params[CARRIED_REVIEW_KEY]["automatic_changed"] is False
    assert second.confirmed_blockers_count == 1


async def _reviewed_unknown(db, tenant_a, tender, extraction_run, verdict, note):
    """Una revisio explicita sobre `unknown` (el perfil encara no te la xifra)."""
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    await add_requirement(db, extraction_run, tender, value=ECONOMIC_450K)
    first = (await analyze_company_compliance(db, company, tender)).run
    await review_assessment(
        db,
        await _only_assessment(db, first.id),
        reviewed_result=verdict,
        review_status=ReviewStatus.VERIFIED,
        note=note,
        user_id=user.id,
    )
    return user, company, first


async def _reviewed_breach_then_engine_satisfied(db, tenant_a, tender, extraction_run):
    """Una persona diu «no compleix» sobre `unknown`; despres el perfil diu que si s'hi arriba."""
    user, company, first = await _reviewed_unknown(
        db,
        tenant_a,
        tender,
        extraction_run,
        AssessmentResult.DOES_NOT_SATISFY,
        "Els comptes de 2025 no estan dipositats.",
    )
    await _revenue(db, company, "900000")
    second = (await analyze_company_compliance(db, company, tender, force=True)).run
    return user, company, first, second


async def test_the_change_notice_survives_a_second_recalculation(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """L'avis compara amb el que va revisar la persona, no amb el darrer run."""
    _user, company, first, second = await _reviewed_breach_then_engine_satisfied(
        db, tenant_a, tender, extraction_run
    )
    expected = {
        "from_run": str(first.id),
        "reviewed_against": "unknown",
        "automatic_changed": True,
    }
    carried = await _only_assessment(db, second.id)
    assert carried.explanation_params[CARRIED_REVIEW_KEY] == expected

    third = (await analyze_company_compliance(db, company, tender, force=True)).run

    carried = await _only_assessment(db, third.id)
    assert carried.automatic_result == AssessmentResult.SATISFIES
    assert carried.reviewed_result == AssessmentResult.DOES_NOT_SATISFY, "la lectura prudent"
    assert carried.blocker_status == BlockerStatus.CONFIRMED
    assert carried.explanation_params[CARRIED_REVIEW_KEY] == expected


async def test_a_new_review_clears_the_change_notice_and_becomes_the_new_basis(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    user, company, _first, second = await _reviewed_breach_then_engine_satisfied(
        db, tenant_a, tender, extraction_run
    )
    again = await review_assessment(
        db,
        await _only_assessment(db, second.id),
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Revisat de nou: ara els comptes hi son.",
        user_id=user.id,
    )
    assert CARRIED_REVIEW_KEY not in again.explanation_params, "l'avis ja esta ates"
    basis = again.explanation_params[REVIEW_BASIS_KEY]
    assert (basis["run"], basis["automatic_result"]) == (str(second.id), "satisfies")
    assert basis["identity_version"] == REQUIREMENT_IDENTITY_VERSION

    third = (await analyze_company_compliance(db, company, tender, force=True)).run

    carried = await _only_assessment(db, third.id)
    assert carried.reviewed_result == AssessmentResult.SATISFIES
    assert carried.explanation_params[CARRIED_REVIEW_KEY] == {
        "from_run": str(second.id),
        "reviewed_against": "satisfies",
        "automatic_changed": False,
    }


async def test_a_kept_satisfies_review_does_not_hide_a_new_breach(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """El motor passa a «no compleix» despres d'un «compleix» huma: el bloqueig s'ha de veure."""
    _user, company, first = await _reviewed_unknown(
        db,
        tenant_a,
        tender,
        extraction_run,
        AssessmentResult.SATISFIES,
        "Comptes dipositats.",
    )
    await _revenue(db, company, "100")

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    fresh = await _only_assessment(db, second.id)
    assert fresh.automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert (fresh.reviewed_result, fresh.review_status) == (None, ReviewStatus.UNREVIEWED)
    assert fresh.blocker_status == BlockerStatus.POTENTIAL
    assert (second.confirmed_blockers_count, second.potential_blockers_count) == (0, 1)
    assert fresh.explanation_params[DROPPED_REVIEW_KEY] == {
        "from_run": str(first.id),
        "reviewed_against": "unknown",
        "reviewed_result": "satisfies",
        "review_note": "Comptes dipositats.",
        "reason": "automatic_now_fails",
    }


async def test_an_override_of_a_breach_the_person_saw_is_kept(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """La persona ja va veure el «no compleix» i el va contradir amb motiu: es conserva."""
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    await _revenue(db, company, "100")
    await add_requirement(db, extraction_run, tender, value=ECONOMIC_450K)
    first = (await analyze_company_compliance(db, company, tender)).run
    await review_assessment(
        db,
        await _only_assessment(db, first.id),
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Es presenta la facturacio consolidada del grup.",
        user_id=user.id,
    )

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    carried = await _only_assessment(db, second.id)
    assert carried.automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert carried.reviewed_result == AssessmentResult.SATISFIES
    assert carried.blocker_status == BlockerStatus.NONE
    assert carried.explanation_params[CARRIED_REVIEW_KEY]["automatic_changed"] is False


async def test_no_change_notice_when_the_engine_now_agrees(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    _user, company, first = await _reviewed_unknown(
        db,
        tenant_a,
        tender,
        extraction_run,
        AssessmentResult.SATISFIES,
        "Comptes dipositats.",
    )
    await _revenue(db, company, "900000")

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    carried = await _only_assessment(db, second.id)
    assert carried.automatic_result == AssessmentResult.SATISFIES
    assert carried.reviewed_result == AssessmentResult.SATISFIES
    assert carried.explanation_params[CARRIED_REVIEW_KEY] == {
        "from_run": str(first.id),
        "reviewed_against": "unknown",
        "automatic_changed": False,
    }


MANUAL_VOLUME = {
    "title": "Volum anual de negoci",
    "category": RequirementCategory.ECONOMIC_SOLVENCY,
    "obligation": RequirementObligation.MANDATORY,
    "operator": RequirementOperator.GTE,
    "scope": RequirementScope.TENDER,
    "value_json": ECONOMIC_450K,
}


async def _reviewed_manual_requirement(db, tenant_a, tender):
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    manual = await manual_requirement.create_manual_requirement(
        db, tender=tender, user=user, fields=dict(MANUAL_VOLUME)
    )
    await db.commit()
    first = (await analyze_company_compliance(db, company, tender)).run
    assessment = await _only_assessment(db, first.id)
    assert assessment.requirement_id == manual.id
    await review_assessment(
        db,
        assessment,
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Tenim 500.000.",
        user_id=user.id,
    )
    return user, company, manual


async def test_an_edited_manual_requirement_does_not_keep_the_old_review(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    user, company, manual = await _reviewed_manual_requirement(db, tenant_a, tender)
    assert await manual_requirement.update_manual_requirement(
        db,
        tender=tender,
        requirement=manual,
        user=user,
        fields={"value_json": {**ECONOMIC_450K, "amount": 9000000}},
    )
    await db.commit()

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    fresh = await _only_assessment(db, second.id)
    assert fresh.automatic_result == AssessmentResult.UNKNOWN, "el motor tampoc no ho sap ara"
    assert fresh.reviewed_result is None, "el «compleix» era sobre 450.000, no sobre 9 milions"
    assert fresh.review_status == ReviewStatus.UNREVIEWED
    dropped = fresh.explanation_params[DROPPED_REVIEW_KEY]
    assert (dropped["reason"], dropped["reviewed_result"]) == ("requirement_changed", "satisfies")


async def test_a_retitled_manual_requirement_does_not_keep_the_old_review(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """L'empremta desada en revisar veu el que no es copia a l'avaluacio (titol, modalitat...)."""
    user, company, manual = await _reviewed_manual_requirement(db, tenant_a, tender)
    assert await manual_requirement.update_manual_requirement(
        db,
        tender=tender,
        requirement=manual,
        user=user,
        fields={"title": "Volum anual de negoci en serveis de telecomunicacions"},
    )
    await db.commit()

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    fresh = await _only_assessment(db, second.id)
    assert fresh.review_status == ReviewStatus.UNREVIEWED
    assert fresh.explanation_params[DROPPED_REVIEW_KEY]["reason"] == "requirement_changed"


async def test_switching_equivalents_on_a_manual_requirement_drops_the_review(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """La regla de certificacions llegeix `equivalence_allowed`: canviar-lo canvia el requisit."""
    user, company, manual = await _reviewed_manual_requirement(db, tenant_a, tender)
    assert await manual_requirement.update_manual_requirement(
        db,
        tender=tender,
        requirement=manual,
        user=user,
        fields={"equivalence_allowed": not bool(manual.equivalence_allowed)},
    )
    await db.commit()

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    fresh = await _only_assessment(db, second.id)
    assert fresh.review_status == ReviewStatus.UNREVIEWED
    assert fresh.explanation_params[DROPPED_REVIEW_KEY]["reason"] == "requirement_changed"


async def test_a_basis_from_another_identity_version_is_not_dropped_en_masse(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """Canviar la llista de camps de l'empremta no descarta totes les revisions de cop."""
    _user, company, manual = await _reviewed_manual_requirement(db, tenant_a, tender)
    first = await db.scalar(
        sa.select(CompanyRequirementAssessment).where(
            CompanyRequirementAssessment.requirement_id == manual.id
        )
    )
    old = first.explanation_params[REVIEW_BASIS_KEY]
    first.explanation_params = {
        **first.explanation_params,
        REVIEW_BASIS_KEY: {**old, "identity_version": 0, "requirement": "0" * 64},
    }
    await db.commit()

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    carried = await _only_assessment(db, second.id)
    assert carried.reviewed_result == AssessmentResult.SATISFIES, "valor, obligacio i lot iguals"
    basis = carried.explanation_params[REVIEW_BASIS_KEY]
    assert basis["identity_version"] == REQUIREMENT_IDENTITY_VERSION
    assert basis["requirement"] == requirement_identity(manual)


async def test_an_unchanged_manual_requirement_keeps_its_review(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    user, company, manual = await _reviewed_manual_requirement(db, tenant_a, tender)
    assert not await manual_requirement.update_manual_requirement(
        db, tender=tender, requirement=manual, user=user, fields=dict(MANUAL_VOLUME)
    )

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    carried = await _only_assessment(db, second.id)
    assert carried.reviewed_result == AssessmentResult.SATISFIES
    assert DROPPED_REVIEW_KEY not in carried.explanation_params


async def test_a_single_lot_calculation_does_not_inherit_reviews(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """El run no desa el lot: una revisio d'un altre abast no decideix el calcul d'un lot."""
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    lot = TenderLot(tender_id=tender.id, external_lot_id="1", lot_number=1, title="Lot 1")
    db.add(lot)
    await db.commit()
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

    by_lot = (
        await analyze_company_compliance(db, company, tender, force=True, tender_lot_id=lot.id)
    ).run

    fresh = await _only_assessment(db, by_lot.id)
    assert fresh.review_status == ReviewStatus.UNREVIEWED
    assert CARRIED_REVIEW_KEY not in (fresh.explanation_params or {})


async def test_carried_reviews_of_duplicates_still_count_once(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    """Una exigencia repetida al plec es una sola fila, abans i despres del «Recalcula»."""
    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    for _ in range(2):
        await add_requirement(
            db, extraction_run, tender, value=ECONOMIC_450K, title="Volum anual de negoci"
        )
    first = (await analyze_company_compliance(db, company, tender)).run
    assert first.requirements_total == 1
    # La web revisa la fila sencera: tots els membres del grup.
    for assessment in await _all_assessments(db, first.id):
        await review_assessment(
            db,
            assessment,
            reviewed_result=AssessmentResult.SATISFIES,
            review_status=ReviewStatus.VERIFIED,
            note="Comptes dipositats.",
            user_id=user.id,
        )

    second = (await analyze_company_compliance(db, company, tender, force=True)).run

    assert second.requirements_total == 1
    assert (second.satisfies_count, second.unknown_count) == (1, 0)
    carried = await _all_assessments(db, second.id)
    assert len(carried) == 2
    assert {a.reviewed_result for a in carried} == {AssessmentResult.SATISFIES}


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


async def test_reviewing_an_explicitly_mandatory_breach_confirms_the_blocker(
    db,
    tenant_a,
    tender,
    extraction_run,
):
    reviewed = await _reviewed_breach(
        db, tenant_a, tender, extraction_run, RequirementModality.MANDATORY
    )
    assert reviewed.blocker_status == BlockerStatus.CONFIRMED


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
