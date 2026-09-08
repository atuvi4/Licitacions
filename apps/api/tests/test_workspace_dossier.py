"""Dossier documental: de "sabem que complim" a "aixo es el que presentarem".

Son dues preguntes diferents i el sistema no les pot confondre. El cas que
aquests tests protegeixen per sobre de tots: un requisit en `satisfies` amb el
dossier en `missing`. Saber que facturem prou no vol dir tenir els comptes
anuals a la carpeta, i un producte que ho dones per equivalent enviaria algu a
presentar-se sense papers.

I la frontera de 5A segueix intacta: arribar a `ready_for_final_review` no es
presentar res.

Cap crida a cap proveidor: proposar un document es una consulta.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.compliance import AssessmentEvidence, CompanyRequirementAssessment
from app.models.credentials import CompanyCertification, CompanyInsurance
from app.models.documents import Document, DocumentLink
from app.models.enums import (
    AnalysisRunStatus,
    AssessmentResult,
    DocumentCategory,
    DocumentValidity,
    DossierCoverage,
    EvidenceItemSource,
    EvidenceItemStatus,
    ExtractionConfidence,
    InsuranceType,
    LinkedEntityType,
    MembershipRole,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
    ReviewStatus,
    SourceType,
    WorkspaceDecisionType,
    WorkspaceReadiness,
    WorkspaceStatus,
    WorkspaceTaskStatus,
)
from app.models.identity import Company
from app.models.profile import CompanyFinancialYear
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tenders import Tender
from app.models.workspace import WorkspaceEvidenceItem, WorkspaceTask
from app.services import bid_workspace as workspace_service
from app.services import workspace_dossier as service
from app.services.compliance_analysis import analyze_company_compliance
from app.workspace.dossier import (
    BLOCKER_ANALYSIS_OUTDATED,
    BLOCKER_DECISION_NOT_GO,
    BLOCKER_EXPIRED_DOCUMENTS,
    BLOCKER_MISSING_EVIDENCE,
    BLOCKER_NEEDS_REVIEW,
    BLOCKER_OPEN_TASKS,
    EvidenceView,
    ReadinessInput,
    coverage_for_requirement,
    document_validity,
    evaluate_readiness,
    task_title_for_gap,
)
from app.workspace.transitions import TransitionActor

DEADLINE = datetime.now(UTC) + timedelta(days=40)
TODAY = date.today()


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def tender(db):
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="5B/2026",
        title="Servei de manteniment integral",
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
        input_fingerprint=uuid.uuid4().hex * 2,
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
    category=RequirementCategory.CERTIFICATION,
    obligation=RequirementObligation.MANDATORY,
    value=None,
    operator=RequirementOperator.EXISTS,
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
        review_status=ReviewStatus.UNREVIEWED,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.commit()
    await db.refresh(requirement)
    return requirement


async def add_document(
    db,
    company_id,
    *,
    name="ISO 27001.pdf",
    expires_at=None,
    category=DocumentCategory.CERTIFICATION,
) -> Document:
    document = Document(
        company_id=company_id,
        name=name,
        original_filename=name,
        storage_key=f"docs/{uuid.uuid4().hex}.pdf",
        mime_type="application/pdf",
        file_size=1024,
        sha256=uuid.uuid4().hex * 2,
        category=category,
        expires_at=expires_at,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


async def link_document(db, company_id, document, entity_type, entity_id) -> DocumentLink:
    link = DocumentLink(
        company_id=company_id,
        document_id=document.id,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    db.add(link)
    await db.commit()
    return link


async def add_certification(db, company_id, *, name="ISO 27001", expires_at=None):
    certification = CompanyCertification(
        company_id=company_id, name=name, expires_at=expires_at
    )
    db.add(certification)
    await db.commit()
    await db.refresh(certification)
    return certification


async def run_compliance(db, company_id, tender):
    company = await db.get(Company, company_id)
    outcome = await analyze_company_compliance(db, company, tender, force=True)
    return outcome.run


async def open_workspace(db, company_id, tender, user_id):
    outcome = await workspace_service.create_workspace(
        db, company_id=company_id, tender=tender, user_id=user_id
    )
    return outcome.workspace


async def approve(db, workspace, user):
    return await workspace_service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Ens hi presentem.",
    )


@pytest.fixture
async def certified(db, tenant_a, tender, extraction_run):
    """Una empresa amb ISO 27001 acreditada amb document, i el plec que la demana.

    Es el cas complet de 4C: certificacio + document enllacat, de manera que
    l'avaluacio en deixa una `AssessmentEvidence` amb `document_id`. Es
    exactament el que el dossier ha de saber reaprofitar.
    """
    user, company = tenant_a
    certification = await add_certification(
        db, company.id, expires_at=TODAY + timedelta(days=365)
    )
    document = await add_document(db, company.id, expires_at=TODAY + timedelta(days=365))
    await link_document(
        db, company.id, document, LinkedEntityType.COMPANY_CERTIFICATION, certification.id
    )
    requirement = await add_requirement(
        db,
        extraction_run,
        tender,
        value={"name": "ISO 27001"},
        title="Certificacio ISO 27001 vigent",
    )
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)
    return {
        "user": user,
        "company": company,
        "workspace": workspace,
        "requirement": requirement,
        "document": document,
        "certification": certification,
    }


# --------------------------------------------------------------------------- #
# Regles pures: vigencia                                                        #
# --------------------------------------------------------------------------- #


def test_a_document_that_expires_before_the_deadline_does_not_serve():
    """§13: vigent avui i caducat el dia de presentar-se. Es el cas que enganya."""
    deadline = datetime(2026, 12, 1, tzinfo=UTC)

    validity = document_validity(
        date(2026, 11, 15), deadline=deadline, today=date(2026, 9, 8)
    )

    assert validity == DocumentValidity.EXPIRES_BEFORE_DEADLINE


def test_document_validity_distinguishes_every_case():
    deadline = datetime(2026, 12, 1, tzinfo=UTC)
    today = date(2026, 9, 8)

    assert document_validity(date(2027, 1, 1), deadline=deadline, today=today) == (
        DocumentValidity.VALID
    )
    assert document_validity(date(2026, 8, 1), deadline=deadline, today=today) == (
        DocumentValidity.EXPIRED
    )
    assert document_validity(None, deadline=deadline, today=today) == (
        DocumentValidity.NO_EXPIRY
    )
    assert document_validity(
        None, deadline=deadline, today=today, has_document=False
    ) == DocumentValidity.NO_DOCUMENT


def test_a_tender_without_deadline_only_checks_todays_expiry():
    assert document_validity(date(2026, 10, 1), deadline=None, today=date(2026, 9, 8)) == (
        DocumentValidity.VALID
    )


# --------------------------------------------------------------------------- #
# Regles pures: cobertura                                                       #
# --------------------------------------------------------------------------- #


def piece(status, validity=DocumentValidity.VALID, has_document=True):
    return EvidenceView(
        item_id=uuid.uuid4(),
        requirement_id=uuid.uuid4(),
        status=status,
        validity=validity,
        has_document=has_document,
        mandatory=True,
    )


def test_a_suggested_document_does_not_cover_anything():
    """§3: proposar no es triar. Fins que algu no ho selecciona, no hi ha res."""
    assert coverage_for_requirement([piece(EvidenceItemStatus.SUGGESTED)]) == (
        DossierCoverage.MISSING_DOCUMENT
    )


def test_a_selected_document_covers_the_requirement():
    assert coverage_for_requirement([piece(EvidenceItemStatus.SELECTED)]) == (
        DossierCoverage.COVERED
    )


def test_a_selected_but_expired_document_is_a_gap_not_a_coverage():
    """Triat i inservible es un forat. Dir el contrari seria el pitjor error."""
    assert coverage_for_requirement(
        [piece(EvidenceItemStatus.SELECTED, DocumentValidity.EXPIRES_BEFORE_DEADLINE)]
    ) == DossierCoverage.MISSING_DOCUMENT


def test_one_piece_marked_for_review_holds_the_whole_requirement():
    assert coverage_for_requirement(
        [piece(EvidenceItemStatus.SELECTED), piece(EvidenceItemStatus.NEEDS_REVIEW)]
    ) == DossierCoverage.NEEDS_REVIEW


def test_a_requirement_with_several_documents_needs_only_one_usable():
    """§10: tres certificats de bona execucio; amb un de triat ja hi ha cobertura."""
    assert coverage_for_requirement(
        [
            piece(EvidenceItemStatus.SUGGESTED),
            piece(EvidenceItemStatus.SELECTED),
            piece(EvidenceItemStatus.SUGGESTED),
        ]
    ) == DossierCoverage.COVERED


def test_a_requirement_entirely_marked_not_applicable_is_not_a_gap():
    assert coverage_for_requirement([piece(EvidenceItemStatus.NOT_APPLICABLE)]) == (
        DossierCoverage.NOT_APPLICABLE
    )


# --------------------------------------------------------------------------- #
# Regles pures: readiness                                                       #
# --------------------------------------------------------------------------- #


def test_readiness_without_a_dossier_is_not_started():
    result = evaluate_readiness(ReadinessInput(dossier_started=False, decision_is_go=True))

    assert result.state == WorkspaceReadiness.NOT_STARTED


def test_readiness_needs_a_human_go_first():
    """§6: sense decisio humana de GO no hi ha res a preparar."""
    result = evaluate_readiness(ReadinessInput(dossier_started=True, decision_is_go=False))

    assert result.state == WorkspaceReadiness.INCOMPLETE
    assert BLOCKER_DECISION_NOT_GO in result.blockers


def test_readiness_says_exactly_what_is_missing():
    """No hi ha cap puntuacio: hi ha una llista del que falta."""
    result = evaluate_readiness(
        ReadinessInput(
            dossier_started=True,
            decision_is_go=True,
            missing_mandatory_evidence=2,
            open_mandatory_tasks=1,
        )
    )

    assert result.state == WorkspaceReadiness.INCOMPLETE
    assert set(result.blockers) == {BLOCKER_MISSING_EVIDENCE, BLOCKER_OPEN_TASKS}
    assert not hasattr(result, "score"), "una nota opaca no diu a ningu que ha de fer"


def test_a_review_request_alone_leaves_the_dossier_in_needs_review():
    result = evaluate_readiness(
        ReadinessInput(dossier_started=True, decision_is_go=True, needs_review_items=1)
    )

    assert result.state == WorkspaceReadiness.NEEDS_REVIEW
    assert result.blockers == (BLOCKER_NEEDS_REVIEW,)


def test_an_expired_mandatory_document_blocks_readiness():
    result = evaluate_readiness(
        ReadinessInput(
            dossier_started=True, decision_is_go=True, expired_mandatory_documents=1
        )
    )

    assert result.state == WorkspaceReadiness.INCOMPLETE
    assert BLOCKER_EXPIRED_DOCUMENTS in result.blockers


def test_a_clean_dossier_after_a_go_is_ready():
    result = evaluate_readiness(ReadinessInput(dossier_started=True, decision_is_go=True))

    assert result.state == WorkspaceReadiness.READY_FOR_FINAL_REVIEW
    assert result.blockers == ()
    assert result.is_ready


def test_a_task_title_reuses_the_checklist_template():
    """§12: sense IA. El verb canvia; el text ve de la plantilla de 5A."""
    assert task_title_for_gap("Acreditar ISO 27001", None) == (
        "Obtenir el document que acredita ISO 27001"
    )
    assert task_title_for_gap("Aportar polissa RC ≥ 1.000.000 €", None) == (
        "Aconseguir polissa RC ≥ 1.000.000 €"
    )
    assert task_title_for_gap(None, None) == "Aconseguir el document acreditatiu"


# --------------------------------------------------------------------------- #
# Propostes a partir del compliment                                             #
# --------------------------------------------------------------------------- #


async def test_the_dossier_reuses_the_document_compliance_already_used(db, certified):
    """§2: no es torna a buscar res. El que 4C va fer servir, es la proposta."""
    workspace = certified["workspace"]

    outcome = await service.sync_dossier(db, workspace, user_id=certified["user"].id)
    await db.commit()

    assert outcome.suggested == 1
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    assert item.document_id == certified["document"].id
    assert item.requirement_id == certified["requirement"].id
    assert item.source == EvidenceItemSource.COMPLIANCE
    # I apunta a la dada corporativa, no nomes al fitxer.
    assert item.company_evidence_id == certified["certification"].id


async def test_a_suggestion_is_not_a_selection(db, certified):
    """§3: el sistema proposa; no dona per fet que aquest es el que presentarem."""
    workspace = certified["workspace"]
    await service.sync_dossier(db, workspace, user_id=certified["user"].id)
    await db.commit()

    item = await db.scalar(sa.select(WorkspaceEvidenceItem))

    assert item.status == EvidenceItemStatus.SUGGESTED
    assert item.selected_by is None
    assert item.selected_at is None
    dossier = await service.load_dossier(db, workspace)
    assert dossier.coverage_by_requirement[certified["requirement"].id] == (
        DossierCoverage.MISSING_DOCUMENT
    )


async def test_selecting_a_document_records_who_and_when(db, certified):
    workspace, user = certified["workspace"], certified["user"]
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))

    updated = await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )

    assert updated.status == EvidenceItemStatus.SELECTED
    assert updated.selected_by == user.id
    assert updated.selected_at is not None
    dossier = await service.load_dossier(db, workspace)
    assert dossier.coverage_by_requirement[certified["requirement"].id] == (
        DossierCoverage.COVERED
    )
    assert dossier.counters.covered == 1


async def test_replacing_a_document_keeps_one_piece_and_a_fresh_signature(db, certified):
    """§14: substituir no es esborrar i tornar a crear; es una tria nova."""
    workspace, user = certified["workspace"], certified["user"]
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )
    first_choice = item.selected_at

    better = await add_document(db, certified["company"].id, name="ISO 27001 renovat.pdf")
    updated = await service.update_item(
        db, workspace, item, document_id=better.id, user=user
    )

    assert updated.document_id == better.id
    assert updated.label == "ISO 27001 renovat.pdf"
    assert updated.source == EvidenceItemSource.MANUAL
    assert updated.selected_by == user.id
    assert updated.selected_at >= first_choice
    total = await db.scalar(sa.select(sa.func.count()).select_from(WorkspaceEvidenceItem))
    assert total == 1, "substituir no ha de deixar peces orfes"


async def test_deselecting_clears_the_signature(db, certified):
    workspace, user = certified["workspace"], certified["user"]
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )

    updated = await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SUGGESTED, user=user
    )

    assert updated.selected_by is None
    assert updated.selected_at is None, "una firma que ja no es certa no s'ha de conservar"


async def test_a_mandatory_requirement_without_any_document_is_a_visible_gap(
    db, tenant_a, tender, extraction_run
):
    """§5 i §10 de 4C: complir no es tenir el paper. El forat s'ha de VEURE."""
    user, company = tenant_a
    # Factura prou: el requisit es `satisfies`. Pero no hi ha cap document.
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.ECONOMIC_SOLVENCY,
        operator=RequirementOperator.GTE,
        value={"amount": 450000, "currency": "EUR", "period": "annual"},
        title="Facturacio anual minima",
    )
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("2450000"), currency="EUR"
        )
    )
    await db.commit()
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)

    outcome = await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    assert outcome.gaps == 1
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    assert item.status == EvidenceItemStatus.MISSING
    assert item.document_id is None

    assessment = await db.scalar(sa.select(CompanyRequirementAssessment))
    dossier = await service.load_dossier(db, workspace)
    assert assessment.effective_result == AssessmentResult.SATISFIES, "el compliment diu que si"
    assert dossier.counters.missing_document == 1, "i el dossier diu que falta el document"
    assert dossier.counters.covered == 0


async def test_syncing_twice_does_not_duplicate_or_overwrite(db, certified):
    workspace, user = certified["workspace"], certified["user"]
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )

    outcome = await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    assert outcome.suggested == 0
    await db.refresh(item)
    assert item.status == EvidenceItemStatus.SELECTED, "la tria humana no es toca"
    total = await db.scalar(sa.select(sa.func.count()).select_from(WorkspaceEvidenceItem))
    assert total == 1


# --------------------------------------------------------------------------- #
# Un document / diversos requisits i a l'inreves                                #
# --------------------------------------------------------------------------- #


async def test_one_document_can_cover_several_requirements_without_being_copied(
    db, tenant_a, tender, extraction_run
):
    """§9: enllacos, no copies. El mateix fitxer, dos requisits, una sola fila a
    `documents`."""
    user, company = tenant_a
    certification = await add_certification(db, company.id, name="ISO 9001")
    document = await add_document(db, company.id, name="Certificats ISO.pdf")
    await link_document(
        db, company.id, document, LinkedEntityType.COMPANY_CERTIFICATION, certification.id
    )
    first = await add_requirement(
        db, extraction_run, tender, value={"name": "ISO 9001"}, title="ISO 9001"
    )
    second = await add_requirement(
        db,
        extraction_run,
        tender,
        value={"name": "ISO 9001"},
        title="ISO 9001 vigent durant el contracte",
    )
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    items = list((await db.scalars(sa.select(WorkspaceEvidenceItem))).all())

    assert {item.requirement_id for item in items} == {first.id, second.id}
    assert {item.document_id for item in items} == {document.id}
    documents = await db.scalar(sa.select(sa.func.count()).select_from(Document))
    assert documents == 1, "el fitxer no s'ha duplicat enlloc"


async def test_a_requirement_can_hold_several_documents(db, certified):
    """§10: experiencia amb tres certificats de bona execucio."""
    workspace, user, company = certified["workspace"], certified["user"], certified["company"]
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    second = await add_document(db, company.id, name="Annex 2.pdf")
    await service.add_item(
        db,
        workspace,
        requirement_id=certified["requirement"].id,
        document_id=second.id,
        notes="Segon document acreditatiu",
        user=user,
    )

    items = list(
        (
            await db.scalars(
                sa.select(WorkspaceEvidenceItem).where(
                    WorkspaceEvidenceItem.requirement_id == certified["requirement"].id
                )
            )
        ).all()
    )
    assert len(items) == 2
    assert {item.document_id for item in items} == {certified["document"].id, second.id}


async def test_the_same_document_cannot_enter_the_same_requirement_twice(db, certified):
    from app.core.errors import ConflictError

    workspace, user = certified["workspace"], certified["user"]
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    with pytest.raises(ConflictError):
        await service.add_item(
            db,
            workspace,
            requirement_id=certified["requirement"].id,
            document_id=certified["document"].id,
            notes=None,
            user=user,
        )


# --------------------------------------------------------------------------- #
# Caducitat contra el termini                                                   #
# --------------------------------------------------------------------------- #


async def test_a_document_expiring_before_the_deadline_breaks_the_coverage(
    db, tenant_a, tender, extraction_run
):
    """§13: vigent avui, inutil el dia de presentar-se. I la readiness ho nota."""
    user, company = tenant_a
    certification = await add_certification(db, company.id, name="ISO 14001")
    # Caduca DESPRES d'avui pero ABANS del termini de la licitacio.
    document = await add_document(
        db,
        company.id,
        name="ISO 14001.pdf",
        expires_at=(DEADLINE - timedelta(days=10)).date(),
    )
    await link_document(
        db, company.id, document, LinkedEntityType.COMPANY_CERTIFICATION, certification.id
    )
    requirement = await add_requirement(
        db, extraction_run, tender, value={"name": "ISO 14001"}, title="ISO 14001"
    )
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await approve(db, workspace, user)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )

    dossier = await service.load_dossier(db, workspace)

    row = next(row for row in dossier.rows if row.item.id == item.id)
    assert row.validity == DocumentValidity.EXPIRES_BEFORE_DEADLINE
    assert dossier.coverage_by_requirement[requirement.id] == DossierCoverage.MISSING_DOCUMENT
    assert dossier.counters.expired_documents == 1
    assert BLOCKER_EXPIRED_DOCUMENTS in dossier.readiness.blockers


# --------------------------------------------------------------------------- #
# Del forat a la tasca                                                          #
# --------------------------------------------------------------------------- #


async def test_a_gap_becomes_an_assignable_task(db, tenant_a, tender, extraction_run):
    """§12: crear la tasca del forat, sense IA i sense duplicats."""
    user, company = tenant_a
    db.add(
        CompanyInsurance(
            company_id=company.id,
            type=InsuranceType.CIVIL_LIABILITY,
            coverage_amount=Decimal("2000000"),
            currency="EUR",
        )
    )
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.INSURANCE,
        operator=RequirementOperator.GTE,
        value={"coverage_amount": 1000000, "currency": "EUR", "type": "civil_liability"},
        title="Responsabilitat civil",
    )
    await db.commit()
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    gap = await db.scalar(
        sa.select(WorkspaceEvidenceItem).where(
            WorkspaceEvidenceItem.status == EvidenceItemStatus.MISSING
        )
    )
    assert gap is not None

    task = await service.create_task_for_gap(db, workspace, gap, user=user)

    assert task.title.startswith("Aconseguir polissa RC")
    assert task.mandatory is True, "el forat es sobre un requisit obligatori"
    assert task.status == WorkspaceTaskStatus.PENDING

    again = await service.create_task_for_gap(db, workspace, gap, user=user)
    assert again.id == task.id, "prement dues vegades no s'han de crear dues tasques"
    total = await db.scalar(sa.select(sa.func.count()).select_from(WorkspaceTask))
    assert total == 1


# --------------------------------------------------------------------------- #
# Readiness d'extrem a extrem                                                   #
# --------------------------------------------------------------------------- #


async def test_readiness_is_incomplete_while_a_document_is_missing(db, certified):
    workspace, user = certified["workspace"], certified["user"]
    await approve(db, workspace, user)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    readiness = await service.evaluate_workspace_readiness(db, workspace)

    assert readiness.state == WorkspaceReadiness.INCOMPLETE
    assert BLOCKER_MISSING_EVIDENCE in readiness.blockers


async def test_readiness_is_needs_review_when_someone_asked_to_look_at_it(db, certified):
    workspace, user = certified["workspace"], certified["user"]
    await approve(db, workspace, user)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.NEEDS_REVIEW, user=user
    )

    readiness = await service.evaluate_workspace_readiness(db, workspace)

    assert readiness.state == WorkspaceReadiness.NEEDS_REVIEW
    assert readiness.blockers == (BLOCKER_NEEDS_REVIEW,)


async def test_readiness_becomes_ready_when_everything_is_in_place(db, certified):
    workspace, user = certified["workspace"], certified["user"]
    await approve(db, workspace, user)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )

    readiness = await service.evaluate_workspace_readiness(db, workspace)

    assert readiness.state == WorkspaceReadiness.READY_FOR_FINAL_REVIEW
    assert readiness.blockers == ()


async def test_ready_for_final_review_is_not_submitted(db, certified):
    """§7 i §19: el dossier a punt no es una oferta presentada."""
    workspace, user = certified["workspace"], certified["user"]
    await approve(db, workspace, user)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )

    await service.advance_to_final_review(
        db, workspace, user=user, role=MembershipRole.OWNER
    )

    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW
    assert workspace.status != WorkspaceStatus.SUBMITTED
    # I l'invariant de 5A segueix intacte: ni el sistema ni una decisio GO hi
    # arriben. Nomes `HUMAN_FINAL_APPROVAL`, que no fa servir cap codi.
    from app.workspace.transitions import AutomaticTransitionForbiddenError, assert_transition

    for actor in (TransitionActor.SYSTEM, TransitionActor.HUMAN_DECISION):
        with pytest.raises(AutomaticTransitionForbiddenError):
            assert_transition(workspace.status, WorkspaceStatus.SUBMITTED, actor=actor)


async def test_you_cannot_force_final_review_with_an_incomplete_dossier(db, certified):
    """§18: no es pot marcar a ma saltant-se les regles; l'error diu que falta."""
    workspace, user = certified["workspace"], certified["user"]
    await approve(db, workspace, user)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    with pytest.raises(service.WorkspaceNotReadyError) as caught:
        await service.advance_to_final_review(
            db, workspace, user=user, role=MembershipRole.OWNER
        )

    assert BLOCKER_MISSING_EVIDENCE in caught.value.details["blockers"]
    await db.refresh(workspace)
    assert workspace.status != WorkspaceStatus.READY_FOR_FINAL_REVIEW


async def test_a_viewer_cannot_move_the_dossier_to_final_review(db, certified):
    from app.core.errors import PermissionDeniedError

    workspace, user = certified["workspace"], certified["user"]
    await approve(db, workspace, user)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    with pytest.raises(PermissionDeniedError):
        await service.advance_to_final_review(
            db, workspace, user=user, role=MembershipRole.VIEWER
        )


async def test_opening_the_dossier_after_a_go_starts_bid_preparation(db, certified):
    """§19: `go_approved` -> `preparing_bid`. No es cap aprovacio, es feina."""
    workspace, user = certified["workspace"], certified["user"]
    await approve(db, workspace, user)
    assert workspace.status == WorkspaceStatus.GO_APPROVED

    await service.sync_dossier(db, workspace, user_id=user.id)
    await service.start_bid_preparation(db, workspace)
    await db.commit()

    assert workspace.status == WorkspaceStatus.PREPARING_BID


async def test_a_recalculation_does_not_touch_the_human_decision(db, certified):
    """El dossier es pot resincronitzar tantes vegades com calgui: la decisio no."""
    workspace, user = certified["workspace"], certified["user"]
    decision = await approve(db, workspace, user)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    tender = await db.get(Tender, workspace.tender_id)
    await run_compliance(db, certified["company"].id, tender)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    history = await workspace_service.decision_history(db, workspace.id)
    assert len(history) == 1
    assert history[0].id == decision.id
    assert history[0].decision == WorkspaceDecisionType.GO


# --------------------------------------------------------------------------- #
# Multi-tenant                                                                  #
# --------------------------------------------------------------------------- #


async def test_a_document_from_another_company_cannot_be_selected(db, certified, tenant_b):
    """Un identificador escrit al cos de la peticio no dona acces a res."""
    workspace, user = certified["workspace"], certified["user"]
    _user_b, company_b = tenant_b
    foreign = await add_document(db, company_b.id, name="Secret.pdf")
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))

    with pytest.raises(service.DocumentNotAvailableError):
        await service.update_item(
            db, workspace, item, document_id=foreign.id, user=user
        )


async def test_another_tenant_cannot_reach_the_dossier(db, client_b, certified, tenant_b):
    workspace = certified["workspace"]
    _user_b, company_b = tenant_b
    await service.sync_dossier(db, workspace, user_id=certified["user"].id)
    await db.commit()

    assert (
        await client_b.get(
            f"/api/v1/companies/{company_b.id}/workspaces/{workspace.id}/dossier"
        )
    ).status_code == 404
    assert (
        await client_b.post(
            f"/api/v1/companies/{company_b.id}/workspaces/{workspace.id}/dossier/sync"
        )
    ).status_code == 404


async def test_an_item_of_another_workspace_is_not_reachable(db, certified, tenant_a, tender):
    from app.core.errors import NotFoundError

    workspace, user = certified["workspace"], certified["user"]
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))

    other_tender = Tender(
        source_id=tender.source_id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        title="Un altre expedient",
    )
    db.add(other_tender)
    await db.commit()
    await db.refresh(other_tender)
    other = await open_workspace(db, certified["company"].id, other_tender, user.id)

    with pytest.raises(NotFoundError):
        await service.get_item(db, other, item.id)


# --------------------------------------------------------------------------- #
# API                                                                           #
# --------------------------------------------------------------------------- #


async def test_the_api_exposes_coverage_and_readiness_separately(db, client_a, certified):
    """§5: cobertura no es compliment, i readiness no es cap d'elles."""
    workspace, company = certified["workspace"], certified["company"]
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"

    synced = await client_a.post(f"{base}/dossier/sync")
    assert synced.status_code == 200, synced.text
    body = synced.json()["dossier"]

    assert body["coverage"]["mandatory_total"] == 1
    assert body["readiness"]["state"] in {"incomplete", "not_started"}
    assert body["readiness"]["is_submitted"] is False
    item = body["groups"][0]["items"][0]
    assert item["status"] == "suggested"
    assert item["assessment_result"] == "satisfies", "el compliment diu que si"
    assert item["coverage"] == "missing_document", "i el dossier, que encara no"
    assert item["document"]["sha256"]


async def test_the_api_refuses_to_advance_and_says_why(db, client_a, certified):
    workspace, company, user = certified["workspace"], certified["company"], certified["user"]
    await approve(db, workspace, user)
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"
    await client_a.post(f"{base}/dossier/sync")

    refused = await client_a.post(f"{base}/readiness/advance")

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "workspace_not_ready"
    assert BLOCKER_MISSING_EVIDENCE in refused.json()["error"]["details"]["blockers"]


async def test_the_workspace_detail_carries_the_readiness(db, client_a, certified):
    workspace, company = certified["workspace"], certified["company"]
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"
    await client_a.post(f"{base}/dossier/sync")

    detail = (await client_a.get(base)).json()

    assert detail["readiness"] == "incomplete"
    assert detail["readiness_blockers"]
    assert detail["dossier_coverage"]["mandatory_total"] == 1
    # Les quatre dimensions de 5A hi segueixen, i readiness es una cinquena.
    assert {"prefit_score", "eligibility", "recommendation", "human_decision"} <= set(detail)


# --------------------------------------------------------------------------- #
# Acceptacio (§21)                                                              #
# --------------------------------------------------------------------------- #


async def test_the_full_dossier_workflow(db, client_a, tenant_a, tender, extraction_run):
    """GO -> dossier -> ISO suggerit i triat -> RC falta -> tasca -> document ->
    READY. I `submitted` segueix sent fals.
    """
    user, company = tenant_a

    # ISO 27001: acreditada amb document. RC: la polissa hi es pero sense fitxer.
    certification = await add_certification(
        db, company.id, name="ISO 27001", expires_at=TODAY + timedelta(days=400)
    )
    iso_document = await add_document(
        db, company.id, name="ISO 27001.pdf", expires_at=TODAY + timedelta(days=400)
    )
    await link_document(
        db, company.id, iso_document, LinkedEntityType.COMPANY_CERTIFICATION, certification.id
    )
    db.add(
        CompanyInsurance(
            company_id=company.id,
            type=InsuranceType.CIVIL_LIABILITY,
            coverage_amount=Decimal("2000000"),
            currency="EUR",
        )
    )
    await add_requirement(
        db, extraction_run, tender, value={"name": "ISO 27001"}, title="ISO 27001"
    )
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.INSURANCE,
        operator=RequirementOperator.GTE,
        value={"coverage_amount": 1000000, "currency": "EUR", "type": "civil_liability"},
        title="Responsabilitat civil",
    )
    await db.commit()
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"

    # 1. Decisio humana de GO.
    decided = await client_a.post(
        f"{base}/decisions", json={"decision": "go", "reason": "Ens hi presentem."}
    )
    assert decided.status_code == 200, decided.text

    # 2. Obrir el dossier: propostes i forats.
    synced = (await client_a.post(f"{base}/dossier/sync")).json()
    assert synced["suggested"] == 1
    assert synced["gaps"] == 1
    assert synced["dossier"]["coverage"]["mandatory_total"] == 2
    assert synced["dossier"]["readiness"]["state"] == "incomplete"

    items = [item for group in synced["dossier"]["groups"] for item in group["items"]]
    iso_item = next(item for item in items if item["status"] == "suggested")
    rc_item = next(item for item in items if item["status"] == "missing")

    # 3. Seleccionar el document proposat per a la ISO.
    after_select = (
        await client_a.patch(f"{base}/dossier/{iso_item['id']}", json={"status": "selected"})
    ).json()
    assert after_select["coverage"]["covered"] == 1
    assert after_select["coverage"]["missing_document"] == 1

    # 4. La RC no te document: crear-ne la tasca.
    task = (await client_a.post(f"{base}/dossier/{rc_item['id']}/task")).json()
    assert task["mandatory"] is True
    assert (await client_a.get(f"{base}/readiness")).json()["state"] == "incomplete"

    # 5. Completar la tasca i aportar el document.
    completed = await client_a.patch(f"{base}/tasks/{task['id']}", json={"status": "completed"})
    assert completed.status_code == 200
    policy = await add_document(
        db,
        company.id,
        name="Polissa RC 2026.pdf",
        category=DocumentCategory.INSURANCE,
        expires_at=(DEADLINE + timedelta(days=200)).date(),
    )
    attached = await client_a.patch(
        f"{base}/dossier/{rc_item['id']}",
        json={"document_id": str(policy.id), "status": "selected"},
    )
    assert attached.status_code == 200, attached.text

    # 6. El dossier esta a punt per a la revisio final.
    readiness = (await client_a.get(f"{base}/readiness")).json()
    assert readiness["state"] == "ready_for_final_review", readiness["blockers"]

    advanced = (await client_a.post(f"{base}/readiness/advance")).json()
    assert advanced["state"] == "ready_for_final_review"

    # 7. I encara NO s'ha presentat res.
    assert advanced["is_submitted"] is False
    detail = (await client_a.get(base)).json()
    assert detail["status"] == "ready_for_final_review"
    assert detail["status"] != "submitted"
    assert detail["human_decision"] == "go"


# --------------------------------------------------------------------------- #
# Cap IA                                                                        #
# --------------------------------------------------------------------------- #


def test_the_dossier_never_calls_a_model():
    """§22: ni proveidor real ni fake. Proposar un document es una consulta."""
    import inspect

    from app.services import workspace_dossier
    from app.workspace import dossier

    # Es busquen CRIDES i imports, no la paraula: els dos moduls diuen als seus
    # comentaris que no fan servir cap LLM, i aixo ha de poder seguir escrit.
    for module in (workspace_dossier, dossier):
        source = inspect.getsource(module)
        for forbidden in (
            "get_provider",
            "StructuredLLMProvider",
            "anthropic",
            "from app.ai",
            "import app.ai",
        ):
            assert forbidden not in source, f"{module.__name__} fa servir {forbidden}"


async def test_evidence_rows_only_reference_documents_that_already_existed(db, certified):
    """§11: no hi ha una segona biblioteca. El dossier nomes hi apunta."""
    workspace, user = certified["workspace"], certified["user"]
    before = await db.scalar(sa.select(sa.func.count()).select_from(Document))

    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()

    after = await db.scalar(sa.select(sa.func.count()).select_from(Document))
    assert after == before, "sincronitzar el dossier no crea cap document"
    evidence = await db.scalar(sa.select(sa.func.count()).select_from(AssessmentEvidence))
    assert evidence >= 1, "i reaprofita l'evidencia que ja hi havia"


# --------------------------------------------------------------------------- #
# Invalidacio: un workspace no pot quedar-se dient que esta llest              #
# --------------------------------------------------------------------------- #


async def new_extraction_run(db, tender) -> RequirementExtractionRun:
    """Una analisi de requisits nova, que deixa el workspace endarrerit."""
    await db.execute(
        sa.update(RequirementExtractionRun)
        .where(RequirementExtractionRun.tender_id == tender.id)
        .values(is_current=False)
    )
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
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


async def make_ready(db, certified) -> WorkspaceEvidenceItem:
    """Porta la candidatura fins a `ready_for_final_review` de veritat.

    Passa per tot el cami real —GO, dossier, seleccio i el gate— perque els
    tests d'invalidacio parteixin d'un estat que el sistema hagi acceptat, no
    d'un que s'hagi escrit a ma.
    """
    workspace, user = certified["workspace"], certified["user"]
    await approve(db, workspace, user)
    await service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )
    await service.advance_to_final_review(db, workspace, user=user, role=MembershipRole.OWNER)
    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW
    return item


async def test_removing_a_mandatory_document_takes_the_workspace_back(db, certified):
    """ready -> es deselecciona el document -> incomplete -> `preparing_bid`."""
    workspace, user = certified["workspace"], certified["user"]
    item = await make_ready(db, certified)

    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SUGGESTED, user=user
    )

    await db.refresh(workspace)
    readiness = await service.evaluate_workspace_readiness(db, workspace)
    assert readiness.state == WorkspaceReadiness.INCOMPLETE
    assert BLOCKER_MISSING_EVIDENCE in readiness.blockers
    assert workspace.status == WorkspaceStatus.PREPARING_BID, (
        "la feina feta es conserva; el que es perd es la certificacio que estava completa"
    )


async def test_clearing_the_document_of_a_selected_piece_takes_it_back(db, certified):
    workspace, user = certified["workspace"], certified["user"]
    item = await make_ready(db, certified)

    await service.update_item(
        db, workspace, item, clear_document=True, status=EvidenceItemStatus.MISSING, user=user
    )

    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.PREPARING_BID


async def test_reopening_a_mandatory_task_takes_the_workspace_back(db, client_a, certified):
    """ready -> es reobre una tasca obligatoria -> `preparing_bid`."""
    workspace, company, user = certified["workspace"], certified["company"], certified["user"]
    await make_ready(db, certified)
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"

    created = await client_a.post(
        f"{base}/tasks", json={"title": "Renovar la polissa RC", "mandatory": True}
    )
    assert created.status_code == 200, created.text

    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.PREPARING_BID
    readiness = await service.evaluate_workspace_readiness(db, workspace)
    assert BLOCKER_OPEN_TASKS in readiness.blockers

    # I en tancar-la, es pot tornar a certificar el dossier.
    task_id = created.json()["id"]
    await client_a.patch(f"{base}/tasks/{task_id}", json={"status": "completed"})
    assert (await client_a.get(f"{base}/readiness")).json()["state"] == "ready_for_final_review"

    # Reobrir-la el torna a treure de llest.
    await service.advance_to_final_review(db, workspace, user=user, role=MembershipRole.OWNER)
    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW

    await client_a.patch(f"{base}/tasks/{task_id}", json={"status": "in_progress"})
    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.PREPARING_BID


async def test_marking_an_evidence_for_review_takes_the_workspace_back(db, certified):
    """ready -> algu demana revisar una evidencia -> `preparing_bid`."""
    workspace, user = certified["workspace"], certified["user"]
    item = await make_ready(db, certified)

    await service.update_item(
        db, workspace, item, status=EvidenceItemStatus.NEEDS_REVIEW, user=user
    )

    await db.refresh(workspace)
    readiness = await service.evaluate_workspace_readiness(db, workspace)
    assert readiness.state == WorkspaceReadiness.NEEDS_REVIEW
    assert workspace.status == WorkspaceStatus.PREPARING_BID


async def test_a_document_that_stops_being_valid_takes_the_workspace_back(db, certified):
    """El document ja no serveix per al termini: tampoc no cobreix res."""
    workspace = certified["workspace"]
    await make_ready(db, certified)

    document = await db.get(Document, certified["document"].id)
    document.expires_at = (DEADLINE - timedelta(days=5)).date()
    await db.commit()

    readiness = await service.reconcile_readiness(db, workspace)

    assert readiness.state == WorkspaceReadiness.INCOMPLETE
    assert BLOCKER_EXPIRED_DOCUMENTS in readiness.blockers
    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.PREPARING_BID


async def test_a_human_no_go_overrides_a_ready_dossier(db, certified):
    """ready -> NO-GO huma -> `no_go_approved`. La decisio mana sobre la feina."""
    workspace, user = certified["workspace"], certified["user"]
    await make_ready(db, certified)

    await workspace_service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.NO_GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Ens hem quedat sense equip per executar-la.",
    )

    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.NO_GO_APPROVED
    readiness = await service.evaluate_workspace_readiness(db, workspace)
    assert readiness.state == WorkspaceReadiness.INCOMPLETE
    assert BLOCKER_DECISION_NOT_GO in readiness.blockers
    # I l'historial conserva el GO anterior: la decisio segueix sent append-only.
    history = await workspace_service.decision_history(db, workspace.id)
    assert [entry.decision for entry in history] == [
        WorkspaceDecisionType.NO_GO,
        WorkspaceDecisionType.GO,
    ]


async def test_a_new_requirements_run_stops_the_dossier_from_claiming_to_be_ready(
    db, certified, tender
):
    """ready -> analisi nova -> no es pot seguir presentant com a llest."""
    workspace = certified["workspace"]
    await make_ready(db, certified)

    await new_extraction_run(db, tender)

    readiness = await service.reconcile_readiness(db, workspace)

    assert readiness.state == WorkspaceReadiness.INCOMPLETE
    assert BLOCKER_ANALYSIS_OUTDATED in readiness.blockers
    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.PREPARING_BID


async def test_a_new_compliance_run_stops_the_dossier_from_claiming_to_be_ready(
    db, certified, tender
):
    workspace = certified["workspace"]
    await make_ready(db, certified)

    await run_compliance(db, certified["company"].id, tender)

    readiness = await service.reconcile_readiness(db, workspace)

    assert BLOCKER_ANALYSIS_OUTDATED in readiness.blockers
    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.PREPARING_BID


async def test_reading_the_dossier_repairs_a_stale_label(db, client_a, certified, tender):
    """Nomes mirar-ho ja el corregeix: no cal recordar-se de premer res."""
    workspace, company = certified["workspace"], certified["company"]
    await make_ready(db, certified)
    await new_extraction_run(db, tender)

    body = (
        await client_a.get(
            f"/api/v1/companies/{company.id}/workspaces/{workspace.id}/dossier"
        )
    ).json()

    assert body["workspace_status"] == "preparing_bid"
    assert body["readiness"]["state"] == "incomplete"
    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.PREPARING_BID


async def test_invalidation_goes_through_the_transition_graph(db, certified):
    """No hi ha assignacions directes: tot passa per `set_status`."""
    import inspect

    from app.services import workspace_dossier

    # Assignacions, no comparacions: `==` tambe conte un `=`.
    writers = [
        line.strip()
        for line in inspect.getsource(workspace_dossier).splitlines()
        if line.strip().startswith("workspace.status =")
        and not line.strip().startswith("workspace.status ==")
    ]

    assert set(writers) == {"workspace.status = workspace_service.set_status("}
    assert len(writers) == 4, (
        "preparacio d'oferta, invalidacio i els dos passos cap a la revisio final"
    )


# --------------------------------------------------------------------------- #
# Invariant preparatori per a l'aprovacio final                                 #
# --------------------------------------------------------------------------- #


async def test_the_stored_status_is_never_a_credential_to_present(db, certified, tender):
    """L'etiqueta pot quedar enrere; la readiness no.

    La reconciliacio nomes actua quan algu mira o toca el workspace. Un canvi
    extern que ningu no hagi consultat encara deixa l'estat guardat dient
    `ready_for_final_review` mentre el calcul ja diu que no. Per aixo la futura
    aprovacio final NO podra fiar-se de `workspace.status`: haura de tornar a
    cridar `evaluate_workspace_readiness()`, com ja fa `advance_to_final_review`.
    """
    workspace, user = certified["workspace"], certified["user"]
    await make_ready(db, certified)

    # Canvi extern: ningu no ha obert el workspace ni ha tocat el dossier.
    await new_extraction_run(db, tender)

    assert workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW, (
        "l'etiqueta encara diu que si..."
    )
    assert not (await service.evaluate_workspace_readiness(db, workspace)).is_ready, (
        "...pero el calcul ja diu que no"
    )

    # I el gate real, que recalcula, no es deixa enganyar per l'etiqueta.
    with pytest.raises(service.WorkspaceNotReadyError):
        await service.advance_to_final_review(
            db, workspace, user=user, role=MembershipRole.OWNER
        )


def test_the_final_review_gate_recomputes_instead_of_trusting_the_flag():
    """Estructural: `advance_to_final_review` no llegeix mai `status` per decidir.

    Quan s'implementi l'aprovacio final, el test que hi correspongui haura de dir
    el mateix. Deixar-ho escrit ara evita que aquell codi neixi fiant-se d'un
    booleà.
    """
    import inspect

    source = inspect.getsource(service.advance_to_final_review)

    assert "reconcile_readiness" in source
    assert "if workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW" not in source
    assert "is_ready" in source


def test_there_is_no_manual_expired_status():
    """Una sola font de veritat per a la caducitat: el document i el termini.

    Un estat manual `expired` conviuria amb `DocumentValidity` i podria dir el
    contrari que les dates. Es va treure.
    """
    assert {status.value for status in EvidenceItemStatus} == {
        "suggested",
        "selected",
        "missing",
        "needs_review",
        "not_applicable",
    }
    assert not hasattr(EvidenceItemStatus, "EXPIRED")
