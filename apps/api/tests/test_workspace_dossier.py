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

from app.core.errors import ValidationError
from app.models.compliance import AssessmentEvidence, CompanyRequirementAssessment
from app.models.credentials import CompanyCertification, CompanyInsurance
from app.models.documents import Document, DocumentLink
from app.models.enums import (
    AnalysisRunStatus,
    AssessmentResult,
    ChecklistItemStatus,
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
from app.workspace.checklist import checklist_title
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
from app.workspace.lineage import ItemFacts, human_weight, plan_migration
from app.workspace.relevance import (
    ESTABLISHED,
    RelevanceInput,
    RelevanceState,
    relevance_of,
    required_document_count,
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
        # Proveidor REAL a proposit: aquests tests aproven paquets, i un paquet
        # sostingut per una simulacio no s'ha de poder aprovar. Veure
        # `PackageSimulationError`.
        provider="anthropic",
        model="claude-sonnet-5",
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


def piece(
    status,
    validity=DocumentValidity.VALID,
    has_document=True,
    relevance=RelevanceState.LINKED,
    required_pieces=1,
):
    """Una peca del dossier.

    La pertinença per defecte es `LINKED` perque aquests tests parlen de la
    MECANICA de la cobertura —triat, caducat, per revisar— i no de si el
    document acredita res. El cas contrari te els seus tests a
    `TestPertinenciaDocumental`.
    """
    return EvidenceView(
        item_id=uuid.uuid4(),
        requirement_id=uuid.uuid4(),
        status=status,
        validity=validity,
        has_document=has_document,
        mandatory=True,
        relevance=relevance,
        required_pieces=required_pieces,
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
    """§10: quan el plec no diu quants en calen, amb un de triat ja n'hi ha prou."""
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


async def item_of(db, workspace) -> WorkspaceEvidenceItem:
    """La peca del dossier d'AQUEST workspace.

    Mai `select(WorkspaceEvidenceItem)` sense filtre abans de modificar res: en una
    base compartida retorna la primera fila de qualsevol workspace, i el test hi
    escriu. Aixi es van modificar sis files reals el 10/09/2026.
    """
    return await db.scalar(
        sa.select(WorkspaceEvidenceItem).where(WorkspaceEvidenceItem.workspace_id == workspace.id)
    )


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
    item = await item_of(db, workspace)

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
    item = await item_of(db, workspace)
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
    item = await item_of(db, workspace)
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
    item = await item_of(db, workspace)
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
    item = await item_of(db, workspace)
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
    item = await item_of(db, workspace)
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
    item = await item_of(db, workspace)
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
    item = await item_of(db, workspace)
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
    item = await item_of(db, workspace)

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

    # 6. Triar el document NO es dir que acredita el requisit. La categoria
    #    encaixa, pero res no lliga aquesta polissa amb aquesta exigencia: el
    #    dossier ho diu i no dona la cobertura per bona.
    pending = (await client_a.get(f"{base}/readiness")).json()
    assert pending["state"] == "incomplete"
    assert "mandatory_relevance_unproven" in pending["blockers"]

    # 7. Ho confirma una persona, amb el seu motiu.
    confirmed = await client_a.post(
        f"{base}/dossier/{rc_item['id']}/relevance",
        json={"note": "Es la polissa vigent que cobreix aquest contracte."},
    )
    assert confirmed.status_code == 200, confirmed.text
    row = next(
        item
        for group in confirmed.json()["groups"]
        for item in group["items"]
        if item["id"] == rc_item["id"]
    )
    assert row["relevance"] == "human_confirmed"
    assert row["relevance_confirmed_by_name"]
    assert row["relevance_note"]

    # 8. Ara si: el dossier esta a punt per a la revisio final.
    readiness = (await client_a.get(f"{base}/readiness")).json()
    assert readiness["state"] == "ready_for_final_review", readiness["blockers"]

    advanced = (await client_a.post(f"{base}/readiness/advance")).json()
    assert advanced["state"] == "ready_for_final_review"

    # 9. I encara NO s'ha presentat res.
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
        # Proveidor REAL a proposit: aquests tests aproven paquets, i un paquet
        # sostingut per una simulacio no s'ha de poder aprovar. Veure
        # `PackageSimulationError`.
        provider="anthropic",
        model="claude-sonnet-5",
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
    item = await item_of(db, workspace)
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


async def test_reading_the_dossier_shows_the_real_state_without_writing_it(
    db, client_a, certified, tender
):
    """Mirar-ho diu la veritat; nomes una mutacio explicita la desa.

    Abans la lectura del dossier corregia l'etiqueta guardada. Aixo va fer que
    una simple comprovacio de lectura degradés un workspace real. Ara la
    resposta ensenya l'estat efectiu, la fila no es toca, i es la reconciliacio
    explicita la que el persisteix.
    """
    workspace, company = certified["workspace"], certified["company"]
    await make_ready(db, certified)
    await new_extraction_run(db, tender)
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"

    body = (await client_a.get(f"{base}/dossier")).json()

    assert body["workspace_status"] == "preparing_bid", "la resposta diu l'estat efectiu"
    assert body["readiness"]["state"] == "incomplete"
    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW, "llegir no escriu"

    # Sincronitzar el dossier es una mutacio explicita que no resol la causa
    # (l'analisi segueix endarrerida): la reconciliacio que fa si que es desa.
    assert (await client_a.post(f"{base}/dossier/sync")).status_code == 200
    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.PREPARING_BID, "la mutacio explicita si"


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
    assert "is_ready" in source
    # El recalcul ha d'anar ABANS de qualsevol lectura de l'estat guardat: si
    # mires primer l'etiqueta, ja t'hi has fiat.
    assert source.index("reconcile_readiness") < source.index("workspace.status ==")


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


# --------------------------------------------------------------------------- #
# Seleccionat no vol dir documentalment cobert (N1)                             #
# --------------------------------------------------------------------------- #


class TestPertinenciaDocumental:
    """Triar un paper i confirmar que es el paper adequat son dues afirmacions.

    L'auditoria va assignar un titol universitari per acreditar una xifra anual
    de negoci. El dossier va dir «Cobert», la readiness va passar a «a punt» i
    el paquet es va congelar sense una sola friccio.

    La categoria es un **filtre, no una prova**: que un document sigui de la
    categoria correcta no vol dir que sigui EL document que el plec demana. La
    cobertura exigeix o be un enllaç amb l'evidencia que el compliment va citar,
    o be que una persona ho confirmi amb el seu nom i el seu motiu.
    """

    def test_una_categoria_incompatible_no_cobreix_res(self):
        """El cas exacte: un titol universitari per acreditar facturacio."""
        state = relevance_of(
            RelevanceInput(
                requirement_category=RequirementCategory.ECONOMIC_SOLVENCY,
                document_category=DocumentCategory.CV,
            )
        )

        assert state == RelevanceState.INCOMPATIBLE
        assert state not in ESTABLISHED

    def test_la_categoria_correcta_tota_sola_tampoc_basta(self):
        """Hi ha cinc certificats a la biblioteca i el plec en demana un."""
        state = relevance_of(
            RelevanceInput(
                requirement_category=RequirementCategory.CERTIFICATION,
                document_category=DocumentCategory.CERTIFICATION,
            )
        )

        assert state == RelevanceState.UNPROVEN
        assert state not in ESTABLISHED

    def test_lenllac_amb_levidencia_del_compliment_si_que_acredita(self):
        """El document penja de la mateixa entitat que el motor va citar."""
        state = relevance_of(
            RelevanceInput(
                requirement_category=RequirementCategory.INSURANCE,
                document_category=DocumentCategory.INSURANCE,
                linked_to_compliance_evidence=True,
            )
        )

        assert state == RelevanceState.LINKED
        assert state in ESTABLISHED

    def test_una_confirmacio_humana_mana_sobre_qualsevol_filtre(self):
        """Pot haver-hi una rao legitima que el sistema no coneix."""
        state = relevance_of(
            RelevanceInput(
                requirement_category=RequirementCategory.ECONOMIC_SOLVENCY,
                document_category=DocumentCategory.CV,
                human_confirmed=True,
            )
        )

        assert state == RelevanceState.CONFIRMED
        assert state in ESTABLISHED

    def test_una_familia_de_requisit_que_no_sabem_que_acredita_demana_revisio(self):
        """El que no es a la taula no queda "permes per defecte"."""
        state = relevance_of(
            RelevanceInput(
                requirement_category=RequirementCategory.CONTRACT_CONDITION,
                document_category=DocumentCategory.LEGAL,
            )
        )

        assert state == RelevanceState.UNKNOWN_CATEGORY

    def test_un_document_triat_sense_pertinença_deixa_el_requisit_en_revisio(self):
        """La regla, aplicada a la cobertura: es el que va fallar a l'auditoria."""
        assert coverage_for_requirement(
            [piece(EvidenceItemStatus.SELECTED, relevance=RelevanceState.UNPROVEN)]
        ) == DossierCoverage.NEEDS_REVIEW

    def test_un_requisit_que_demana_tres_documents_no_es_cobreix_amb_un(self):
        """§: tenir-ne un de la categoria correcta no cobreix una exigencia de tres."""
        assert coverage_for_requirement(
            [
                piece(
                    EvidenceItemStatus.SELECTED,
                    relevance=RelevanceState.LINKED,
                    required_pieces=3,
                )
            ]
        ) == DossierCoverage.NEEDS_REVIEW

    def test_amb_els_tres_documents_si_que_queda_cobert(self):
        pieces = [
            piece(
                EvidenceItemStatus.SELECTED,
                relevance=RelevanceState.LINKED,
                required_pieces=3,
            )
            for _ in range(3)
        ]

        assert coverage_for_requirement(pieces) == DossierCoverage.COVERED

    def test_quants_documents_demana_el_requisit_es_llegeix_del_valor(self):
        assert required_document_count({"minimum_projects": 3}) == 3
        assert required_document_count({"count": 2}) == 2
        # Sense xifra explicita no s'inventa: un.
        assert required_document_count({"amount": 250000}) == 1
        assert required_document_count(None) == 1


class TestPertinenciaContraLaBaseDeDades:
    """El mateix, pero pel cami real: dossier, confirmacio i readiness."""

    async def test_un_document_incompatible_no_dona_cobertura(
        self, db, tenant_a, tender, extraction_run
    ):
        """Titol universitari (CV) per acreditar solvencia economica."""
        user, company = tenant_a
        requirement = await add_requirement(
            db,
            extraction_run,
            tender,
            category=RequirementCategory.ECONOMIC_SOLVENCY,
            value={"amount": 250000, "currency": "EUR"},
            title="Volum de negoci",
        )
        document = await add_document(
            db, company.id, name="Titol universitari.pdf", category=DocumentCategory.CV
        )
        workspace = await open_workspace(db, company.id, tender, user.id)
        item = WorkspaceEvidenceItem(
            company_id=company.id,
            workspace_id=workspace.id,
            requirement_id=requirement.id,
            label="Volum de negoci",
            status=EvidenceItemStatus.MISSING,
            source=EvidenceItemSource.MANUAL,
        )
        db.add(item)
        await db.commit()

        await service.update_item(
            db,
            workspace,
            item,
            document_id=document.id,
            status=EvidenceItemStatus.SELECTED,
            user=user,
        )
        dossier = await service.load_dossier(db, workspace)

        assert dossier.coverage_by_requirement[requirement.id] == (
            DossierCoverage.NEEDS_REVIEW
        )
        row = next(r for r in dossier.rows if r.item.id == item.id)
        assert row.relevance == RelevanceState.INCOMPATIBLE

    async def test_una_confirmacio_humana_el_cobreix_i_deixa_rastre(
        self, db, tenant_a, tender, extraction_run
    ):
        user, company = tenant_a
        requirement = await add_requirement(
            db,
            extraction_run,
            tender,
            category=RequirementCategory.ECONOMIC_SOLVENCY,
            value={"amount": 250000, "currency": "EUR"},
            title="Volum de negoci",
        )
        document = await add_document(
            db, company.id, name="Comptes 2025.pdf", category=DocumentCategory.FINANCIAL
        )
        workspace = await open_workspace(db, company.id, tender, user.id)
        item = WorkspaceEvidenceItem(
            company_id=company.id,
            workspace_id=workspace.id,
            requirement_id=requirement.id,
            label="Volum de negoci",
            status=EvidenceItemStatus.MISSING,
            source=EvidenceItemSource.MANUAL,
        )
        db.add(item)
        await db.commit()
        await service.update_item(
            db,
            workspace,
            item,
            document_id=document.id,
            status=EvidenceItemStatus.SELECTED,
            user=user,
        )

        # Categoria compatible, pero res no lliga aquest document amb el requisit.
        dossier = await service.load_dossier(db, workspace)
        assert dossier.coverage_by_requirement[requirement.id] == (
            DossierCoverage.NEEDS_REVIEW
        )

        await service.confirm_relevance(
            db, workspace, item, note="Son els comptes de l'exercici que acredita.", user=user
        )
        dossier = await service.load_dossier(db, workspace)

        assert dossier.coverage_by_requirement[requirement.id] == DossierCoverage.COVERED
        row = next(r for r in dossier.rows if r.item.id == item.id)
        assert row.relevance == RelevanceState.CONFIRMED
        assert row.item.relevance_confirmed_by == user.id
        assert row.item.relevance_note

    async def test_una_confirmacio_exigeix_motiu(
        self, db, tenant_a, tender, extraction_run
    ):
        user, company = tenant_a
        requirement = await add_requirement(db, extraction_run, tender)
        document = await add_document(db, company.id)
        workspace = await open_workspace(db, company.id, tender, user.id)
        item = WorkspaceEvidenceItem(
            company_id=company.id,
            workspace_id=workspace.id,
            requirement_id=requirement.id,
            label="Requisit",
            status=EvidenceItemStatus.MISSING,
            source=EvidenceItemSource.MANUAL,
            document_id=document.id,
        )
        db.add(item)
        await db.commit()

        with pytest.raises(ValidationError):
            await service.confirm_relevance(db, workspace, item, note="   ", user=user)

    async def test_canviar_el_document_no_hereta_la_confirmacio(
        self, db, tenant_a, tender, extraction_run
    ):
        """Confirmar que un paper era el bo no diu res del paper seguent."""
        user, company = tenant_a
        requirement = await add_requirement(
            db,
            extraction_run,
            tender,
            category=RequirementCategory.CERTIFICATION,
            value={"name": "ISO 14001"},
            title="ISO 14001",
        )
        first = await add_document(db, company.id, name="ISO 14001.pdf")
        second = await add_document(db, company.id, name="Un altre certificat.pdf")
        workspace = await open_workspace(db, company.id, tender, user.id)
        item = WorkspaceEvidenceItem(
            company_id=company.id,
            workspace_id=workspace.id,
            requirement_id=requirement.id,
            label="ISO 14001",
            status=EvidenceItemStatus.MISSING,
            source=EvidenceItemSource.MANUAL,
        )
        db.add(item)
        await db.commit()
        await service.update_item(
            db,
            workspace,
            item,
            document_id=first.id,
            status=EvidenceItemStatus.SELECTED,
            user=user,
        )
        await service.confirm_relevance(
            db, workspace, item, note="Es el certificat que demana el plec.", user=user
        )
        assert (await service.load_dossier(db, workspace)).coverage_by_requirement[
            requirement.id
        ] == DossierCoverage.COVERED

        await service.update_item(db, workspace, item, document_id=second.id, user=user)
        dossier = await service.load_dossier(db, workspace)

        assert dossier.coverage_by_requirement[requirement.id] == (
            DossierCoverage.NEEDS_REVIEW
        )
        await db.refresh(item)
        assert item.relevance_confirmed_at is None

    async def test_un_requisit_sense_pertinença_bloqueja_la_readiness(
        self, db, tenant_a, tender, extraction_run
    ):
        """Si afecta una evidencia obligatoria, la readiness ho ha de respectar."""
        user, company = tenant_a
        requirement = await add_requirement(
            db,
            extraction_run,
            tender,
            category=RequirementCategory.ECONOMIC_SOLVENCY,
            value={"amount": 250000, "currency": "EUR"},
            title="Volum de negoci",
        )
        document = await add_document(
            db, company.id, name="Titol universitari.pdf", category=DocumentCategory.CV
        )
        workspace = await open_workspace(db, company.id, tender, user.id)
        await approve(db, workspace, user)
        item = WorkspaceEvidenceItem(
            company_id=company.id,
            workspace_id=workspace.id,
            requirement_id=requirement.id,
            label="Volum de negoci",
            status=EvidenceItemStatus.MISSING,
            source=EvidenceItemSource.MANUAL,
        )
        db.add(item)
        await db.commit()
        await service.update_item(
            db,
            workspace,
            item,
            document_id=document.id,
            status=EvidenceItemStatus.SELECTED,
            user=user,
        )

        readiness = await service.evaluate_workspace_readiness(db, workspace)

        assert readiness.is_ready is False


# --------------------------------------------------------------------------- #
# Reconciliar amb una analisi nova                                              #
# --------------------------------------------------------------------------- #


async def reanalyse(db, tender, previous):
    """Una reanalisi legitima: neix un run nou i el d'abans deixa de ser vigent.

    Passa quan l'organ publica una versio nova del plec, quan millora
    l'extractor, quan algu corregeix un requisit o quan simplement es torna a
    executar. Cada vegada neixen `Requirement` amb identificadors nous.
    """
    previous.is_current = False
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        provider="anthropic",
        model="claude-sonnet-5",
        is_current=True,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


class TestLlinatgeDeRequisits:
    """La decisio, sense base de dades pel mig.

    L'empremta que fa de clau es la mateixa que lliga una confirmacio humana de
    pertinença: si coincideix, el requisit demana el mateix i el que algu va
    afirmar segueix sent cert; si no, ha canviat materialment i no s'hereta res.
    """

    def test_el_mateix_requisit_despres_dun_rerun_es_mou_amb_la_feina(self):
        vell, nou = uuid.uuid4(), uuid.uuid4()
        plan = plan_migration(
            [
                ItemFacts(
                    id=uuid.uuid4(),
                    requirement_id=vell,
                    fingerprint="f1",
                    on_current_run=False,
                    weight=human_weight(selected=True),
                )
            ],
            {"f1": nou},
        )

        assert [entry.requirement_id for entry in plan.rebind] == [nou]
        assert plan.supersede == ()

    def test_un_requisit_materialment_modificat_no_hereta_res(self):
        """Empremta diferent, exigencia diferent: la firma d'abans no hi val."""
        item = ItemFacts(
            id=uuid.uuid4(),
            requirement_id=uuid.uuid4(),
            fingerprint="abans",
            on_current_run=False,
            weight=human_weight(relevance_confirmed=True),
        )

        plan = plan_migration([item], {"despres": uuid.uuid4()})

        assert plan.rebind == ()
        assert plan.supersede == (item.id,)

    def test_un_requisit_eliminat_deixa_de_comptar(self):
        item = ItemFacts(
            id=uuid.uuid4(),
            requirement_id=uuid.uuid4(),
            fingerprint="f1",
            on_current_run=False,
            weight=human_weight(selected=True),
        )

        plan = plan_migration([item], {})

        assert plan.supersede == (item.id,)
        assert plan.rebind == ()

    def test_una_proposta_que_ningu_no_ha_tocat_i_esta_duplicada_sesborra(self):
        """No hi ha cap afirmacio humana a dins: conservar-la nomes fa soroll."""
        document = uuid.uuid4()
        nou = uuid.uuid4()
        vell_id, nou_id = uuid.uuid4(), uuid.uuid4()
        plan = plan_migration(
            [
                ItemFacts(
                    id=vell_id,
                    requirement_id=uuid.uuid4(),
                    fingerprint="f1",
                    on_current_run=False,
                    weight=0,
                    document_id=document,
                ),
                ItemFacts(
                    id=nou_id,
                    requirement_id=nou,
                    fingerprint="f1",
                    on_current_run=True,
                    weight=0,
                    document_id=document,
                ),
            ],
            {"f1": nou},
        )

        assert plan.drop == (vell_id,)
        assert plan.rebind == ()

    def test_dues_peces_amb_feina_humana_no_es_trepitgen(self):
        """El lloc del requisit nou ja esta ocupat: la vella es queda d'historia.

        Sense aixo, moure-la-hi violaria la unicitat (requisit, document) i el
        que es perdria seria precisament la feina que s'intentava salvar.
        """
        document = uuid.uuid4()
        nou = uuid.uuid4()
        vell_id = uuid.uuid4()
        plan = plan_migration(
            [
                ItemFacts(
                    id=vell_id,
                    requirement_id=uuid.uuid4(),
                    fingerprint="f1",
                    on_current_run=False,
                    weight=human_weight(selected=True),
                    document_id=document,
                ),
                ItemFacts(
                    id=uuid.uuid4(),
                    requirement_id=nou,
                    fingerprint="f1",
                    on_current_run=True,
                    weight=human_weight(relevance_confirmed=True),
                    document_id=document,
                ),
            ],
            {"f1": nou},
        )

        assert plan.supersede == (vell_id,)
        assert plan.rebind == ()

    def test_es_salva_primer_el_que_mes_feina_humana_porta(self):
        """Nomes hi cap un marcador de forat per requisit: hi va el millor."""
        nou = uuid.uuid4()
        confirmat, tocat = uuid.uuid4(), uuid.uuid4()
        plan = plan_migration(
            [
                ItemFacts(
                    id=tocat,
                    requirement_id=uuid.uuid4(),
                    fingerprint="f1",
                    on_current_run=False,
                    weight=human_weight(touched=True),
                ),
                ItemFacts(
                    id=confirmat,
                    requirement_id=uuid.uuid4(),
                    fingerprint="f1",
                    on_current_run=False,
                    weight=human_weight(relevance_confirmed=True),
                ),
            ],
            {"f1": nou},
        )

        assert [entry.item_id for entry in plan.rebind] == [confirmat]
        assert plan.supersede == (tocat,)

    def test_una_peca_solta_no_es_reconcilia(self):
        """No penja de cap requisit: cap analisi la pot deixar obsoleta."""
        plan = plan_migration(
            [
                ItemFacts(
                    id=uuid.uuid4(),
                    requirement_id=None,
                    fingerprint=None,
                    on_current_run=False,
                    weight=human_weight(manual=True),
                )
            ],
            {"f1": uuid.uuid4()},
        )

        assert plan.is_empty


class TestReconciliacioContraLaBaseDeDades:
    """El cami real: reanalitzar, reconciliar, i mirar que ha quedat viu.

    Es el cas que va apareixer a l'E2E: la checklist es va quedar sencera
    penjada d'una extraccio superada mentre el workspace ja es declarava
    reconciliat, i la readiness comptava aquell soroll.
    """

    async def test_despres_de_reconciliar_cap_item_actiu_penja_dun_run_superat(
        self, db, tenant_a, tender, extraction_run
    ):
        user, company = tenant_a
        await add_requirement(
            db, extraction_run, tender, value={"name": "ISO 14001"}, title="ISO 14001"
        )
        workspace = await open_workspace(db, company.id, tender, user.id)
        await workspace_service.generate_checklist(db, workspace, user_id=user.id)
        await db.commit()

        nou = await reanalyse(db, tender, extraction_run)
        await add_requirement(
            db, nou, tender, value={"name": "ISO 14001"}, title="ISO 14001"
        )

        await service.reconcile_with_current_analysis(db, workspace, user_id=user.id)

        actius = await service.active_checklist_items(db, workspace)
        vius = await service.live_requirement_ids(db, tender.id)
        assert actius
        assert all(item.requirement_id in vius for item in actius)

    async def test_el_workspace_deixa_de_dir_que_esta_endarrerit(
        self, db, tenant_a, tender, extraction_run
    ):
        user, company = tenant_a
        await add_requirement(db, extraction_run, tender)
        workspace = await open_workspace(db, company.id, tender, user.id)
        nou = await reanalyse(db, tender, extraction_run)
        await add_requirement(db, nou, tender)

        await service.reconcile_with_current_analysis(db, workspace, user_id=user.id)

        assert workspace.current_requirement_run_id == nou.id

    async def test_un_rerun_equivalent_conserva_document_tria_i_confirmacio(
        self, db, tenant_a, tender, extraction_run
    ):
        """Mateixa exigencia, identificador nou: la feina humana hi ha de ser."""
        user, company = tenant_a
        requirement = await add_requirement(
            db,
            extraction_run,
            tender,
            category=RequirementCategory.ECONOMIC_SOLVENCY,
            value={"amount": 250000, "currency": "EUR"},
            title="Volum de negoci",
        )
        document = await add_document(
            db, company.id, name="Comptes 2025.pdf", category=DocumentCategory.FINANCIAL
        )
        workspace = await open_workspace(db, company.id, tender, user.id)
        item = WorkspaceEvidenceItem(
            company_id=company.id,
            workspace_id=workspace.id,
            requirement_id=requirement.id,
            label="Volum de negoci",
            status=EvidenceItemStatus.MISSING,
            source=EvidenceItemSource.MANUAL,
        )
        db.add(item)
        await db.commit()
        await service.update_item(
            db,
            workspace,
            item,
            document_id=document.id,
            status=EvidenceItemStatus.SELECTED,
            user=user,
        )
        await service.confirm_relevance(
            db, workspace, item, note="Son els comptes de l'exercici.", user=user
        )

        nou = await reanalyse(db, tender, extraction_run)
        equivalent = await add_requirement(
            db,
            nou,
            tender,
            category=RequirementCategory.ECONOMIC_SOLVENCY,
            value={"amount": 250000, "currency": "EUR"},
            title="Volum de negoci",
        )

        outcome = await service.reconcile_with_current_analysis(
            db, workspace, user_id=user.id
        )
        await db.refresh(item)
        dossier = await service.load_dossier(db, workspace)

        assert outcome.carried >= 1
        assert item.requirement_id == equivalent.id
        assert item.document_id == document.id
        assert item.relevance_confirmed_by == user.id
        assert item.relevance_note
        row = next(r for r in dossier.rows if r.item.id == item.id)
        assert row.relevance == RelevanceState.CONFIRMED
        assert dossier.coverage_by_requirement[equivalent.id] == DossierCoverage.COVERED

    async def test_si_el_plec_canvia_el_que_demana_la_confirmacio_no_viatja(
        self, db, tenant_a, tender, extraction_run
    ):
        """250.000 EUR i 400.000 EUR no son la mateixa exigencia."""
        user, company = tenant_a
        requirement = await add_requirement(
            db,
            extraction_run,
            tender,
            category=RequirementCategory.ECONOMIC_SOLVENCY,
            value={"amount": 250000, "currency": "EUR"},
            title="Volum de negoci",
        )
        document = await add_document(
            db, company.id, name="Comptes 2025.pdf", category=DocumentCategory.FINANCIAL
        )
        workspace = await open_workspace(db, company.id, tender, user.id)
        item = WorkspaceEvidenceItem(
            company_id=company.id,
            workspace_id=workspace.id,
            requirement_id=requirement.id,
            label="Volum de negoci",
            status=EvidenceItemStatus.MISSING,
            source=EvidenceItemSource.MANUAL,
        )
        db.add(item)
        await db.commit()
        await service.update_item(
            db,
            workspace,
            item,
            document_id=document.id,
            status=EvidenceItemStatus.SELECTED,
            user=user,
        )
        await service.confirm_relevance(
            db, workspace, item, note="Son els comptes de l'exercici.", user=user
        )

        nou = await reanalyse(db, tender, extraction_run)
        exigent = await add_requirement(
            db,
            nou,
            tender,
            category=RequirementCategory.ECONOMIC_SOLVENCY,
            value={"amount": 400000, "currency": "EUR"},
            title="Volum de negoci",
        )

        await service.reconcile_with_current_analysis(db, workspace, user_id=user.id)
        dossier = await service.load_dossier(db, workspace)

        # L'item d'abans es historia i no cobreix res.
        assert any(r.item.id == item.id for r in dossier.superseded)
        assert all(r.item.id != item.id for r in dossier.rows)
        assert dossier.coverage_by_requirement.get(exigent.id) != DossierCoverage.COVERED

    async def test_un_requisit_nou_apareix(self, db, tenant_a, tender, extraction_run):
        user, company = tenant_a
        await add_requirement(db, extraction_run, tender, title="ISO 14001")
        workspace = await open_workspace(db, company.id, tender, user.id)
        await workspace_service.generate_checklist(db, workspace, user_id=user.id)
        await db.commit()

        nou = await reanalyse(db, tender, extraction_run)
        await add_requirement(db, nou, tender, title="ISO 14001")
        await add_requirement(db, nou, tender, title="ISO 45001")

        await service.reconcile_with_current_analysis(db, workspace, user_id=user.id)
        titles = {
            item.title for item in await service.active_checklist_items(db, workspace)
        }

        assert any("ISO 45001" in title for title in titles)

    async def test_un_requisit_eliminat_deixa_de_bloquejar_la_readiness(
        self, db, tenant_a, tender, extraction_run
    ):
        """El plec ja no ho demana: seguir comptant-ho seria preparar-se per a res."""
        user, company = tenant_a
        await add_requirement(
            db,
            extraction_run,
            tender,
            category=RequirementCategory.ECONOMIC_SOLVENCY,
            value={"amount": 250000, "currency": "EUR"},
            title="Volum de negoci",
        )
        workspace = await open_workspace(db, company.id, tender, user.id)
        await workspace_service.generate_checklist(db, workspace, user_id=user.id)
        await db.commit()
        items = await service.active_checklist_items(db, workspace)
        items[0].status = ChecklistItemStatus.BLOCKED
        await db.commit()

        abans = await service.evaluate_workspace_readiness(db, workspace)
        assert "mandatory_checklist_blocked" in abans.blockers

        nou = await reanalyse(db, tender, extraction_run)
        await add_requirement(db, nou, tender, title="Una altra cosa")
        await service.reconcile_with_current_analysis(db, workspace, user_id=user.id)
        despres = await service.evaluate_workspace_readiness(db, workspace)

        assert "mandatory_checklist_blocked" not in despres.blockers

    async def test_els_titols_els_torna_a_derivar_el_requisit_vigent(
        self, db, tenant_a, tender, extraction_run
    ):
        """El cas de l'E2E: el titol es va quedar congelat a la versio antiga.

        Es el mateix sublimit, pero la primera extraccio no en deia el tipus i
        la segona si. La checklist ha de dir el que diu el plec d'ara.
        """
        user, company = tenant_a
        await add_requirement(
            db,
            extraction_run,
            tender,
            category=RequirementCategory.INSURANCE,
            value={"coverage_amount": 600000, "currency": "EUR"},
            title="Responsabilitat civil",
        )
        workspace = await open_workspace(db, company.id, tender, user.id)
        await workspace_service.generate_checklist(db, workspace, user_id=user.id)
        await db.commit()
        assert all(
            "limit total" not in item.title
            for item in await service.active_checklist_items(db, workspace)
        )

        nou = await reanalyse(db, tender, extraction_run)
        await add_requirement(
            db,
            nou,
            tender,
            category=RequirementCategory.INSURANCE,
            value={"coverage_amount": 600000, "currency": "EUR", "limit_kind": "total"},
            title="Responsabilitat civil",
        )

        await service.reconcile_with_current_analysis(db, workspace, user_id=user.id)
        titles = {
            item.title for item in await service.active_checklist_items(db, workspace)
        }

        assert any("limit total" in title for title in titles)

    async def test_reconciliar_dues_vegades_no_canvia_res_mes(
        self, db, tenant_a, tender, extraction_run
    ):
        """Idempotent: la segona passada no ha de trobar feina."""
        user, company = tenant_a
        await add_requirement(db, extraction_run, tender)
        workspace = await open_workspace(db, company.id, tender, user.id)
        nou = await reanalyse(db, tender, extraction_run)
        await add_requirement(db, nou, tender)

        await service.reconcile_with_current_analysis(db, workspace, user_id=user.id)
        segona = await service.reconcile_with_current_analysis(
            db, workspace, user_id=user.id
        )

        # `historic` no hi surt a proposit: es un estat derivat, no una cosa que
        # aquesta crida hagi fet. Reconciliar dues vegades el compta igual.
        assert segona.carried == 0
        assert segona.discarded == 0
        assert segona.created_checklist == 0
        assert segona.created_evidence == 0

    async def test_moltes_reanalisis_seguides_no_acumulen_soroll(
        self, db, tenant_a, tender, extraction_run
    ):
        """El cas exacte que vaig reproduir a l'E2E, amb sis reanalisis."""
        user, company = tenant_a
        await add_requirement(db, extraction_run, tender, title="ISO 14001")
        workspace = await open_workspace(db, company.id, tender, user.id)
        await workspace_service.generate_checklist(db, workspace, user_id=user.id)
        await db.commit()

        run = extraction_run
        for _ in range(6):
            run = await reanalyse(db, tender, run)
            await add_requirement(db, run, tender, title="ISO 14001")
            await service.reconcile_with_current_analysis(db, workspace, user_id=user.id)

        actius = await service.active_checklist_items(db, workspace)
        vius = await service.live_requirement_ids(db, tender.id)

        assert len(actius) == 1
        assert actius[0].requirement_id in vius


# --------------------------------------------------------------------------- #
# Defensa en profunditat: els tests no poden tocar files d'un altre workspace  #
# --------------------------------------------------------------------------- #


async def test_the_test_helpers_never_touch_another_workspace(db, certified, tenant_b, tender):
    """Una peca aliena, creada abans que les nostres, ha de sortir intacta.

    El 10/09/2026 uns tests que agafaven `select(WorkspaceEvidenceItem)` sense
    filtre van modificar sis peces d'un workspace real que compartia base de
    dades. El harness ja aïlla cada test, pero dins del mateix schema tambe hi
    pot haver files que no son del test: aquesta es la primera que retornaria
    una consulta sense filtre, i el flux sencer no la pot tocar.
    """
    other_user, other_company = tenant_b
    other = (
        await workspace_service.create_workspace(
            db, company_id=other_company.id, tender=tender, user_id=other_user.id
        )
    ).workspace
    foreign = WorkspaceEvidenceItem(
        company_id=other_company.id,
        workspace_id=other.id,
        label="Peca d'una altra empresa",
        status=EvidenceItemStatus.SUGGESTED,
        source=EvidenceItemSource.MANUAL,
    )
    db.add(foreign)
    await db.commit()
    table = WorkspaceEvidenceItem.__table__
    snapshot = sa.select(table).where(table.c.id == foreign.id)
    before = (await db.execute(snapshot)).one()

    item = await make_ready(db, certified)
    await service.update_item(
        db, certified["workspace"], item, status=EvidenceItemStatus.NEEDS_REVIEW,
        user=certified["user"],
    )

    assert item.workspace_id == certified["workspace"].id
    assert (await db.execute(snapshot)).one() == before, "la peca aliena no s'ha tocat"


class TestReconciliacioPerLAPI:
    """El cami complet tal com el fa la interficie: reanalisi, reconciliacio i lectura.

    Els tests de `TestReconciliacioContraLaBaseDeDades` comproven la decisio
    contra la base de dades. Aquest la comprova per HTTP, que es on va apareixer
    el problema: una checklist que continuava penjada d'una extraccio superada
    mentre el workspace ja es declarava reconciliat.
    """

    async def test_reanalitzar_reconciliar_i_llegir(
        self, db, client_a, tenant_a, tender, extraction_run
    ):
        user, company = tenant_a
        estable = await add_requirement(
            db, extraction_run, tender, title="Certificat ISO 14001 vigent",
            value={"name": "ISO 14001"}, category=RequirementCategory.CERTIFICATION,
        )
        canviara = await add_requirement(
            db, extraction_run, tender, title="Polissa de responsabilitat civil",
            value={"amount": 150000, "currency": "EUR"},
            category=RequirementCategory.INSURANCE,
        )
        desapareixera = await add_requirement(
            db, extraction_run, tender, title="Memoria tecnica signada",
            value={"name": "Memoria"}, category=RequirementCategory.TECHNICAL_REQUIREMENT,
        )
        iso = await add_document(
            db, company.id, name="ISO 14001.pdf", category=DocumentCategory.CERTIFICATION
        )
        polissa = await add_document(
            db, company.id, name="Polissa RC.pdf", category=DocumentCategory.INSURANCE
        )
        workspace = await open_workspace(db, company.id, tender, user.id)
        base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"

        async def dossier_items() -> dict[str, dict]:
            body = (await client_a.get(f"{base}/dossier")).json()
            return {i["requirement_id"]: i for g in body["groups"] for i in g["items"]}

        # 1. La feina de partida: checklist, dossier, tria i confirmacio humana.
        # La checklist ja neix amb el workspace: reconciliar-la no hi afegeix res.
        primera = await client_a.post(f"{base}/checklist/regenerate")
        assert primera.status_code == 200
        assert primera.json()["total"] == 3
        # Sense compliment previ, `sync` no proposa res: les peces s'afegeixen a ma,
        # que es el que fa qui prepara la candidatura.
        assert (await client_a.post(f"{base}/dossier/sync")).status_code == 200
        for requirement in (estable, canviara, desapareixera):
            afegit = await client_a.post(
                f"{base}/dossier", json={"requirement_id": str(requirement.id)}
            )
            assert afegit.status_code == 200, afegit.text

        items = await dossier_items()
        assert set(items) == {str(estable.id), str(canviara.id), str(desapareixera.id)}
        for requirement, document in ((estable, iso), (canviara, polissa)):
            item_id = items[str(requirement.id)]["id"]
            tria = await client_a.patch(
                f"{base}/dossier/{item_id}",
                json={"document_id": str(document.id), "status": "selected"},
            )
            assert tria.status_code == 200, tria.text
            confirmada = await client_a.post(
                f"{base}/dossier/{item_id}/relevance",
                json={"note": "Comprovat contra el plec."},
            )
            assert confirmada.status_code == 200, confirmada.text

        # 2. Una reanalisi legitima: mateixa exigencia, una de modificada, una que
        #    desapareix i una de nova.
        nou_run = await reanalyse(db, tender, extraction_run)
        estable_nou = await add_requirement(
            db, nou_run, tender, title="Certificat ISO 14001 vigent",
            value={"name": "ISO 14001"}, category=RequirementCategory.CERTIFICATION,
        )
        canviat = await add_requirement(
            db, nou_run, tender, title="Polissa de responsabilitat civil",
            value={"amount": 600000, "currency": "EUR"},
            category=RequirementCategory.INSURANCE,
        )
        afegit = await add_requirement(
            db, nou_run, tender, title="Certificat digital de representant",
            value={"name": "Certificat digital"}, category=RequirementCategory.CERTIFICATION,
        )

        # 3. Reconciliar des de la interficie.
        resultat = await client_a.post(f"{base}/checklist/regenerate")
        assert resultat.status_code == 200
        cos = resultat.json()
        assert cos["carried"] >= 1 and cos["historic"] >= 1

        actius = await dossier_items()
        vius = {str(r) for r in await service.live_requirement_ids(db, tender.id)}

        # (5) cap item actiu penja d'un run superat
        assert set(actius) <= vius
        assert str(estable.id) not in actius and str(canviara.id) not in actius

        # (1) rerun equivalent: es conserva el que era segur conservar
        conservat = actius[str(estable_nou.id)]
        assert conservat["document"]["id"] == str(iso.id)
        assert conservat["status"] == "selected"
        assert conservat["relevance_confirmed_at"] is not None
        assert conservat["relevance_note"] == "Comprovat contra el plec."

        # (2) materialment modificat: la peca d'abans es historia i la nova neix
        #     sense cap confirmacio. Ningu no hereta una firma a cegues.
        assert str(canviat.id) not in actius, "no s'arrossega la peca del requisit vell"
        nova = await client_a.post(
            f"{base}/dossier", json={"requirement_id": str(canviat.id)}
        )
        assert nova.status_code == 200
        refet = (await dossier_items())[str(canviat.id)]
        assert refet["relevance_confirmed_at"] is None
        assert refet["document"] is None
        assert refet["coverage"] != "covered"

        # (3) eliminat: ja no compta enlloc
        assert str(desapareixera.id) not in actius
        assert str(desapareixera.id) not in await dossier_items()

        # (4) el requisit nou apareix a la feina pendent
        detall = (await client_a.get(base)).json()
        checklist = {i["requirement_id"]: i for i in detall["checklist"]}
        assert set(checklist) == vius, "la checklist activa es exactament l'analisi vigent"
        assert str(afegit.id) in checklist
        # (6) els titols visibles els deriva el requisit VIGENT, no el que hi havia
        #     escrit: es comparen amb el que el producte generaria ara mateix.
        current = {
            str(r.id): checklist_title(r)
            for r in (await service._current_requirements(db, workspace))[1]
        }
        for requirement_id, item in checklist.items():
            assert item["title"] == current[requirement_id]

        # (7) la readiness es recalcula: el que ja no es demana no bloqueja, i el
        #     que ha canviat torna a demanar una mirada humana.
        readiness = (await client_a.get(f"{base}/readiness")).json()
        dossier = (await client_a.get(f"{base}/dossier")).json()
        assert readiness["state"] == "incomplete"
        # La cobertura parla de les peces vives: el requisit nou encara no en te.
        assert dossier["coverage"]["mandatory_total"] == len(await dossier_items())
        assert dossier["coverage"]["covered"] == 1, "nomes el que segueix confirmat"
        assert dossier["superseded_count"] >= 1, "la feina d'abans es conserva com a historia"
