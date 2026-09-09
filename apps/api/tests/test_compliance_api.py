"""Compliment d'extrem a extrem: requisits reals, perfil real, veredicte real.

Prova la cadena sencera contra la base de dades: Requirement -> regla ->
perfil -> evidencia -> resultat, i el resum d'elegibilitat que se'n deriva.

Inclou els casos que decideixen si el producte es fiable: bloqueig confirmat
contra potencial, `unknown` que no descarta, revisio que no esborra el calcul,
i aillament entre empreses.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.compliance import CompanyComplianceRun, CompanyRequirementAssessment
from app.models.credentials import CompanyCertification
from app.models.enums import (
    AnalysisRunStatus,
    AssessmentResult,
    BlockerStatus,
    EligibilityResult,
    ExtractionConfidence,
    RecommendationResult,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
    ReviewStatus,
    SourceType,
    VerificationStatus,
)
from app.models.identity import Company
from app.models.profile import CompanyFinancialYear
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tenders import Tender
from app.services.compliance_analysis import (
    NoRequirementsError,
    analyze_company_compliance,
)

DEADLINE = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


@pytest.fixture
async def tender(db):
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}",
        name="Font",
        source_type=SourceType.SYNDICATION_ATOM,
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        title="Servei de manteniment",
        deadline_at=DEADLINE,
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


@pytest.fixture
async def extraction_run(db, tender):
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint="f" * 64,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        provider="fake",
        model="fake-model-v1",
        is_current=True,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def add_requirement(
    db,
    run,
    tender,
    *,
    category=RequirementCategory.ECONOMIC_SOLVENCY,
    obligation=RequirementObligation.MANDATORY,
    value=None,
    operator=RequirementOperator.GTE,
    review_status=ReviewStatus.UNREVIEWED,
    title="Requisit",
) -> Requirement:
    requirement = Requirement(
        run_id=run.id,
        tender_id=tender.id,
        category=category,
        obligation=obligation,
        operator=operator,
        scope=RequirementScope.TENDER,
        title=title,
        value_json=value,
        confidence=ExtractionConfidence.HIGH,
        review_status=review_status,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.commit()
    await db.refresh(requirement)
    return requirement


async def company_of(db, tenant):
    _user, company = tenant
    return await db.get(Company, company.id)


# --------------------------------------------------------------------------- #
# Cami complet                                                                  #
# --------------------------------------------------------------------------- #


async def test_a_met_threshold_produces_a_satisfying_assessment(
    db, tenant_a, tender, extraction_run
):
    company = await company_of(db, tenant_a)
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("2450000"), currency="EUR"
        )
    )
    await db.commit()
    requirement = await add_requirement(
        db,
        extraction_run,
        tender,
        value={"amount": 450000, "currency": "EUR", "period": "annual"},
        title="Facturacio anual minima 450.000 EUR",
    )

    outcome = await analyze_company_compliance(db, company, tender)

    assert outcome.reused is False
    run = outcome.run
    assert run.overall_result == EligibilityResult.ELIGIBLE
    assert run.satisfies_count == 1
    assert run.rules_version == "company-compliance-v2"

    assessment = await db.scalar(sa.select(CompanyRequirementAssessment))
    assert assessment.requirement_id == requirement.id
    assert assessment.automatic_result == AssessmentResult.SATISFIES
    assert assessment.rule_code == "economic_turnover_aggregate_v2"
    assert assessment.explanation_code == "ECONOMIC_THRESHOLD_MET"
    assert assessment.explanation_params["required"] == 450000
    assert assessment.explanation_params["actual"] == 2450000
    # El nou company_value diu QUINS exercicis han entrat al calcul, no nomes un.
    assert assessment.company_value_json["years_used"] == [2025]
    assert assessment.requirement_value_json["amount"] == 450000
    assert assessment.blocker_status == BlockerStatus.NONE
    # La cadena de provenance corporativa hi es.
    assert len(assessment.evidence) == 1
    assert assessment.evidence[0].label.startswith("Facturacio 2025")


async def test_a_tender_without_requirements_cannot_be_assessed(db, tenant_a, tender):
    company = await company_of(db, tenant_a)

    with pytest.raises(NoRequirementsError):
        await analyze_company_compliance(db, company, tender)


# --------------------------------------------------------------------------- #
# Bloquejos                                                                     #
# --------------------------------------------------------------------------- #


async def test_an_unreviewed_breach_is_only_a_potential_blocker(
    db, tenant_a, tender, extraction_run
):
    """§58: una extraccio no verificada NO pot provocar un NO-GO definitiu."""
    company = await company_of(db, tenant_a)
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("120000"), currency="EUR"
        )
    )
    await db.commit()
    await add_requirement(
        db,
        extraction_run,
        tender,
        value={"amount": 450000, "currency": "EUR", "period": "annual"},
        review_status=ReviewStatus.UNREVIEWED,
    )

    run = (await analyze_company_compliance(db, company, tender)).run

    assert run.potential_blockers_count == 1
    assert run.confirmed_blockers_count == 0
    assert run.overall_result == EligibilityResult.REVIEW_REQUIRED
    assert run.recommendation == RecommendationResult.REVIEW


async def test_a_verified_breach_is_a_confirmed_blocker_and_a_no_go(
    db, tenant_a, tender, extraction_run
):
    """§57: amb el requisit verificat, l'incompliment ja descarta."""
    company = await company_of(db, tenant_a)
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("120000"), currency="EUR"
        )
    )
    await db.commit()
    await add_requirement(
        db,
        extraction_run,
        tender,
        value={"amount": 450000, "currency": "EUR", "period": "annual"},
        review_status=ReviewStatus.VERIFIED,
    )

    run = (await analyze_company_compliance(db, company, tender)).run

    assert run.confirmed_blockers_count == 1
    assert run.overall_result == EligibilityResult.NO_GO
    assert run.recommendation == RecommendationResult.NO_GO


async def test_a_mandatory_unknown_requires_review_and_does_not_block(
    db, tenant_a, tender, extraction_run
):
    """§59: sense dades es demana completar el perfil, no descartar."""
    company = await company_of(db, tenant_a)
    await add_requirement(
        db,
        extraction_run,
        tender,
        value={"amount": 450000, "currency": "EUR", "period": "annual"},
        review_status=ReviewStatus.VERIFIED,
    )

    run = (await analyze_company_compliance(db, company, tender)).run

    assert run.unknown_count == 1
    assert run.confirmed_blockers_count == 0
    assert run.potential_blockers_count == 0
    assert run.overall_result == EligibilityResult.REVIEW_REQUIRED


async def test_a_rejected_requirement_is_not_assessed(db, tenant_a, tender, extraction_run):
    company = await company_of(db, tenant_a)
    await add_requirement(
        db, extraction_run, tender, value={"amount": 1}, review_status=ReviewStatus.REJECTED
    )

    run = (await analyze_company_compliance(db, company, tender)).run

    assert run.requirements_total == 0
    total = await db.scalar(
        sa.select(sa.func.count()).select_from(CompanyRequirementAssessment)
    )
    assert total == 0


# --------------------------------------------------------------------------- #
# Cache, stale i immutabilitat                                                  #
# --------------------------------------------------------------------------- #


async def test_the_same_profile_and_requirements_reuse_the_run(
    db, tenant_a, tender, extraction_run
):
    company = await company_of(db, tenant_a)
    await add_requirement(db, extraction_run, tender, value={"amount": 1000})

    first = await analyze_company_compliance(db, company, tender)
    second = await analyze_company_compliance(db, company, tender)

    assert second.reused is True
    assert second.run.id == first.run.id
    assert await db.scalar(sa.select(sa.func.count()).select_from(CompanyComplianceRun)) == 1


async def test_changing_the_profile_makes_the_run_stale_and_recalculates(
    db, tenant_a, tender, extraction_run
):
    """§63: tocar una certificacio ha de canviar el fingerprint."""
    company = await company_of(db, tenant_a)
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.CERTIFICATION,
        value={"name": "ISO 27001"},
        operator=None,
    )

    first = (await analyze_company_compliance(db, company, tender)).run
    fingerprint_before = first.company_profile_fingerprint

    db.add(
        CompanyCertification(
            company_id=company.id,
            name="ISO 27001",
            verification_status=VerificationStatus.VERIFIED,
        )
    )
    await db.commit()

    second = await analyze_company_compliance(db, company, tender)

    assert second.reused is False, "el perfil ha canviat: cal recalcular"
    assert second.run.company_profile_fingerprint != fingerprint_before
    await db.refresh(first)
    assert first.is_current is False, "el run anterior deixa de ser el vigent"
    assert second.run.is_current is True


async def test_a_new_extraction_run_forces_a_new_compliance_run(
    db, tenant_a, tender, extraction_run
):
    """§64: canviar l'extraccio de requisits invalida el compliment anterior."""
    company = await company_of(db, tenant_a)
    await add_requirement(db, extraction_run, tender, value={"amount": 1000})

    first = (await analyze_company_compliance(db, company, tender)).run

    extraction_run.is_current = False
    newer = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint="g" * 64,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        provider="fake",
        model="fake-model-v1",
        is_current=True,
    )
    db.add(newer)
    await db.commit()
    await add_requirement(db, newer, tender, value={"amount": 1000})

    second = await analyze_company_compliance(db, company, tender)

    assert second.reused is False
    assert second.run.id != first.id
    # El calcul anterior segueix consultable tal com es va fer.
    await db.refresh(first)
    assert first.requirements_fingerprint != second.run.requirements_fingerprint


# --------------------------------------------------------------------------- #
# Pre-fit i elegibilitat son dimensions diferents                               #
# --------------------------------------------------------------------------- #


async def test_a_high_prefit_does_not_hide_a_confirmed_blocker(
    db, tenant_a, tender, extraction_run
):
    """§60: el pre-fit i l'elegibilitat no es fusionen mai."""
    from app.models.enums import MatchStatus
    from app.models.matching import CompanyTenderMatch

    company = await company_of(db, tenant_a)
    db.add(
        CompanyTenderMatch(
            company_id=company.id,
            tender_id=tender.id,
            score=95,
            status=MatchStatus.CANDIDATE,
            candidate_lot_count=0,
            total_lot_count=0,
            data_coverage=1.0,
            factors={},
            rules_version="v1",
        )
    )
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("1000"), currency="EUR"
        )
    )
    await db.commit()
    await add_requirement(
        db,
        extraction_run,
        tender,
        value={"amount": 450000, "currency": "EUR", "period": "annual"},
        review_status=ReviewStatus.VERIFIED,
    )

    run = (await analyze_company_compliance(db, company, tender)).run

    assert run.prefit_score == 95
    assert run.overall_result == EligibilityResult.NO_GO
    assert run.recommendation == RecommendationResult.NO_GO


# --------------------------------------------------------------------------- #
# Revisio manual                                                                #
# --------------------------------------------------------------------------- #


async def test_a_manual_override_keeps_the_automatic_result(
    db, tenant_a, tender, extraction_run
):
    """§65: el resultat del motor i el de la persona conviuen."""
    from app.services.compliance_analysis import review_assessment

    user, _ = tenant_a
    company = await company_of(db, tenant_a)
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("100"), currency="EUR"
        )
    )
    await db.commit()
    await add_requirement(
        db, extraction_run, tender, value={"amount": 450000, "currency": "EUR", "period": "annual"}
    )
    await analyze_company_compliance(db, company, tender)

    assessment = await db.scalar(sa.select(CompanyRequirementAssessment))
    assert assessment.automatic_result == AssessmentResult.DOES_NOT_SATISFY

    updated = await review_assessment(
        db,
        assessment,
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Acreditat amb comptes consolidats del grup.",
        user_id=user.id,
    )

    assert updated.automatic_result == AssessmentResult.DOES_NOT_SATISFY, "no s'esborra"
    assert updated.reviewed_result == AssessmentResult.SATISFIES
    assert updated.effective_result == AssessmentResult.SATISFIES
    assert updated.reviewed_by == user.id
    assert updated.reviewed_at is not None
    assert updated.review_note.startswith("Acreditat")
    # I el bloqueig desapareix perque el resultat efectiu ja no es incompliment.
    assert updated.blocker_status == BlockerStatus.NONE

    run = await db.get(CompanyComplianceRun, updated.assessment_run_id)
    assert run.confirmed_blockers_count == 0
    assert run.overall_result == EligibilityResult.ELIGIBLE


# --------------------------------------------------------------------------- #
# Multi-tenant                                                                  #
# --------------------------------------------------------------------------- #


async def test_two_companies_get_independent_results(
    db, tenant_a, tenant_b, tender, extraction_run
):
    """§62: mateixa licitacio, perfils diferents, veredictes diferents."""
    company_a = await company_of(db, tenant_a)
    company_b = await company_of(db, tenant_b)

    db.add(
        CompanyCertification(
            company_id=company_a.id,
            name="ISO 27001",
            verification_status=VerificationStatus.VERIFIED,
        )
    )
    await db.commit()
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.CERTIFICATION,
        value={"name": "ISO 27001"},
        operator=None,
    )

    run_a = (await analyze_company_compliance(db, company_a, tender)).run
    run_b = (await analyze_company_compliance(db, company_b, tender)).run

    assert run_a.id != run_b.id
    assert run_a.company_id != run_b.company_id

    a = await db.scalar(
        sa.select(CompanyRequirementAssessment).where(
            CompanyRequirementAssessment.company_id == company_a.id
        )
    )
    b = await db.scalar(
        sa.select(CompanyRequirementAssessment).where(
            CompanyRequirementAssessment.company_id == company_b.id
        )
    )
    assert a.automatic_result == AssessmentResult.SATISFIES
    assert b.automatic_result != AssessmentResult.SATISFIES


async def test_a_company_cannot_read_another_companys_assessments(
    client_a, client_b, db, tenant_a, tenant_b, tender, extraction_run
):
    company_a = await company_of(db, tenant_a)
    await add_requirement(db, extraction_run, tender, value={"amount": 1000})
    await analyze_company_compliance(db, company_a, tender)
    assessment = await db.scalar(sa.select(CompanyRequirementAssessment))

    _user_b, company_b = tenant_b
    response = await client_b.get(
        f"/api/v1/companies/{company_b.id}/assessments/{assessment.id}"
    )

    assert response.status_code == 404, "no existeix per a una altra empresa"


async def test_the_api_returns_the_full_chain(client_a, db, tenant_a, tender, extraction_run):
    """§76: el test d'acceptacio. Plec exigeix X, empresa te Y, regla Z."""
    from app.models.enums import (
        ExtractionQuality,
        ExtractionStatus,
        PageExtractionStatus,
        TenderDocumentType,
    )
    from app.models.requirements import RequirementEvidence
    from app.models.tender_documents import (
        TenderDocumentContent,
        TenderDocumentExtraction,
        TenderDocumentPage,
    )
    from app.models.tenders import TenderDocument

    _user, company = tenant_a
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("2450000"), currency="EUR"
        )
    )
    # Cita del plec, perque l'assessment hi pugui apuntar.
    page_text = "El volumen anual de negocios debera ser al menos de 450.000 euros."
    content = TenderDocumentContent(
        sha256="a" * 64, storage_key="k", file_size=1, mime_type="application/pdf",
        is_text_extractable=True,
    )
    db.add(content)
    await db.flush()
    extraction = TenderDocumentExtraction(
        content_id=content.id, parser_version="pypdf-v1", revision_number=1,
        status=ExtractionStatus.EXTRACTED, quality=ExtractionQuality.GOOD,
        page_count=1, is_current=True,
    )
    db.add(extraction)
    document = TenderDocument(
        tender_id=tender.id, name="PCAP", document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
        url="https://example.com/pcap.pdf",
    )
    db.add(document)
    await db.flush()
    page = TenderDocumentPage(
        extraction_id=extraction.id, page_number=27, text=page_text,
        char_count=len(page_text), text_hash="h" * 64, status=PageExtractionStatus.EXTRACTED,
    )
    db.add(page)
    await db.flush()

    requirement = await add_requirement(
        db, extraction_run, tender,
        value={"amount": 450000, "currency": "EUR", "period": "annual"},
        title="Facturacio anual minima de 450.000 EUR",
    )
    db.add(
        RequirementEvidence(
            requirement_id=requirement.id, extraction_id=extraction.id, page_id=page.id,
            tender_document_id=document.id, page_number=27,
            quote="El volumen anual de negocios debera ser al menos de 450.000 euros",
            char_start=0, char_end=64,
        )
    )
    await db.commit()

    posted = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/compliance"
    )
    assert posted.status_code == 200
    assert posted.json()["run"]["overall_result"] == "eligible"

    response = await client_a.get(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/compliance"
    )
    assert response.status_code == 200
    body = response.json()

    assert body["run"]["rules_version"] == "company-compliance-v2"
    assert body["is_stale"] is False

    item = body["assessments"][0]
    # El plec exigeix X...
    assert item["requirement_value_json"]["amount"] == 450000
    # ...l'empresa acredita Y...
    assert item["company_value_json"]["amount"] == 2450000
    # ...per aquesta regla Z el resultat es aquest...
    assert item["rule_code"] == "economic_turnover_aggregate_v2"
    assert item["explanation_code"] == "ECONOMIC_THRESHOLD_MET"
    assert item["effective_result"] == "satisfies"
    # ...i aqui tens les dues evidencies.
    assert item["evidence"][0]["entity_type"] == "financial_year"
    assert item["requirement"]["page_number"] == 27
    assert item["requirement"]["extraction_revision"] == 1
    assert "450.000 euros" in item["requirement"]["quote"]
