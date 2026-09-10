"""Workspace de candidatura: que la decisio final sigui d'una persona.

Aquesta iteracio no calcula res de nou. El que aporta —i el que aquests tests
han de sostenir— es una frontera: el sistema recomana, la persona decideix, i
qui va decidir que, quan i amb quina informacio a la vista queda escrit i no es
pot reescriure.

Els casos que decideixen si aixo es fiable: que `go_approved` no pugui apareixer
sol, que un GO amb avisos exigeixi motiu, que canviar d'opinio no esborri
l'opinio anterior, que regenerar la checklist no trepitgi feina humana i que una
empresa no vegi res de l'altra.

Cap crida a cap proveidor: el compliment es determinista i el workspace tambe.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.core.security import hash_password
from app.models.compliance import CompanyComplianceRun
from app.models.enums import (
    AnalysisRunStatus,
    ChecklistItemSource,
    ChecklistItemStatus,
    ExtractionConfidence,
    MatchStatus,
    MembershipRole,
    RecommendationResult,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
    ReviewStatus,
    SourceType,
    WorkspaceDecisionType,
    WorkspaceStatus,
)
from app.models.identity import Company, CompanyMembership, User
from app.models.matching import CompanyTenderMatch
from app.models.profile import CompanyFinancialYear
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tenders import Tender, TenderLot
from app.models.workspace import BidWorkspace, WorkspaceChecklistItem, WorkspaceDecision
from app.services import bid_workspace as service
from app.services.compliance_analysis import analyze_company_compliance
from app.workspace.checklist import checklist_title, format_amount
from app.workspace.decision import DecisionWarnings, requires_reason
from app.workspace.transitions import (
    ALLOWED_TRANSITIONS,
    FINAL_APPROVAL_STATUSES,
    HUMAN_ONLY_STATUSES,
    AutomaticTransitionForbiddenError,
    InvalidTransitionError,
    TransitionActor,
    assert_transition,
    can_transition,
    is_human_only,
    status_for,
)
from tests.conftest import TEST_PASSWORD, login

FUTURE_DEADLINE = datetime.now(UTC) + timedelta(days=30)
PAST_DEADLINE = datetime.now(UTC) - timedelta(days=3)


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
        expediente_number="1/2026",
        title="Servei de manteniment integral",
        deadline_at=FUTURE_DEADLINE,
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


@pytest.fixture
async def extraction_run(db, tender):
    return await add_extraction_run(db, tender)


async def add_extraction_run(db, tender, *, current: bool = True) -> RequirementExtractionRun:
    if current:
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
        is_current=current,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run



@pytest.fixture
async def workspace_with_analysis(db, tenant_a, tender, extraction_run):
    """Una candidatura amb una analisi de SIMULACIO al darrere.

    El proveidor `fixture` es el que fa que hi hagi un avis obert de veritat
    quan es decideix, que es el que aquests tests han de veure desat.
    """
    user, company = tenant_a
    extraction_run.provider = "fixture"
    extraction_run.model = "fixture-plec-v1"
    await db.commit()
    outcome = await service.create_workspace(
        db, company_id=company.id, tender=tender, user_id=user.id
    )
    return outcome.workspace, user


async def add_requirement(
    db,
    run,
    tender,
    *,
    category=RequirementCategory.ECONOMIC_SOLVENCY,
    obligation=RequirementObligation.MANDATORY,
    value=None,
    operator=RequirementOperator.GTE,
    title="Requisit",
    tender_lot_id=None,
) -> Requirement:
    requirement = Requirement(
        run_id=run.id,
        tender_id=tender.id,
        tender_lot_id=tender_lot_id,
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


async def add_member(db, company_id, *, role: MembershipRole) -> User:
    """Un usuari mes dins d'una empresa que ja existeix, amb el rol que calgui."""
    user = User(
        email=f"{role.value}-{uuid.uuid4().hex[:8]}@empresa-a.com",
        hashed_password=hash_password(TEST_PASSWORD),
        full_name=f"Persona {role.value}",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(CompanyMembership(user_id=user.id, company_id=company_id, role=role))
    await db.commit()
    await db.refresh(user)
    return user


async def open_workspace(db, company_id, tender, user_id, tender_lot_id=None) -> BidWorkspace:
    outcome = await service.create_workspace(
        db,
        company_id=company_id,
        tender=tender,
        user_id=user_id,
        tender_lot_id=tender_lot_id,
    )
    return outcome.workspace


async def run_compliance(db, company_id, tender) -> CompanyComplianceRun:
    company = await db.get(Company, company_id)
    outcome = await analyze_company_compliance(db, company, tender, force=True)
    return outcome.run


# --------------------------------------------------------------------------- #
# Regles pures: de requisit a tasca                                             #
# --------------------------------------------------------------------------- #


class FakeRequirement:
    """Prou per provar la plantilla sense base de dades."""

    def __init__(self, **fields):
        self.id = fields.pop("id", uuid.uuid4())
        self.category = fields.pop("category", RequirementCategory.OTHER)
        self.obligation = fields.pop("obligation", RequirementObligation.MANDATORY)
        self.operator = fields.pop("operator", None)
        self.title = fields.pop("title", "Requisit")
        self.value_json = fields.pop("value_json", None)
        self.tender_lot_id = fields.pop("tender_lot_id", None)


def test_a_certification_requirement_becomes_an_accreditation_task():
    """§10: ISO 27001 obligatoria -> "Acreditar ISO 27001"."""
    requirement = FakeRequirement(
        category=RequirementCategory.CERTIFICATION,
        value_json={"name": "ISO 27001"},
        title="Certificacio ISO 27001 vigent",
    )

    assert checklist_title(requirement) == "Acreditar ISO 27001"


def test_an_insurance_threshold_becomes_a_policy_task():
    """§10: RC >= 1.000.000 EUR -> "Aportar polissa RC >= 1.000.000 EUR"."""
    requirement = FakeRequirement(
        category=RequirementCategory.INSURANCE,
        operator=RequirementOperator.GTE,
        value_json={"coverage_amount": 1000000, "currency": "EUR", "type": "civil_liability"},
    )

    assert checklist_title(requirement) == "Aportar polissa RC ≥ 1.000.000 €"


def test_the_same_requirement_always_produces_the_same_title():
    """Deterministic: cap redaccio, cap model, cap variacio entre execucions."""
    requirement = FakeRequirement(
        category=RequirementCategory.EXPERIENCE, value_json={"minimum_projects": 3}
    )

    assert {checklist_title(requirement) for _ in range(10)} == {
        "Acreditar 3 contractes similars"
    }


def test_amounts_are_formatted_for_a_catalan_reader():
    assert format_amount(1000000, "EUR") == "1.000.000 €"
    assert format_amount("450000.50", "EUR") == "450.000,50 €"
    assert format_amount("no es un numero") is None


def test_a_requirement_without_structured_value_falls_back_to_its_title():
    """Millor el titol que va sortir de 4B que un text inventat aqui."""
    requirement = FakeRequirement(
        category=RequirementCategory.CERTIFICATION, value_json={}, title="Certificat X"
    )

    assert checklist_title(requirement) == "Acreditar certificacio: Certificat X"


# --------------------------------------------------------------------------- #
# Regles pures: quan cal justificar                                             #
# --------------------------------------------------------------------------- #


def test_a_clean_go_needs_no_written_reason():
    assert requires_reason(WorkspaceDecisionType.GO, DecisionWarnings()) is False


@pytest.mark.parametrize(
    "warning",
    [
        DecisionWarnings(confirmed_blockers=1),
        DecisionWarnings(potential_blockers=1),
        DecisionWarnings(mandatory_unknown=1),
        DecisionWarnings(mandatory_needs_review=1),
        DecisionWarnings(deadline_passed=True),
        # Els tres de sota son els que faltaven: una decisio presa sobre una
        # analisi que no diu res, que no es de fiar, o que descriu un estat
        # anterior de la licitacio.
        DecisionWarnings(undetermined_eligibility=True),
        DecisionWarnings(unreliable_analysis=True),
        DecisionWarnings(outdated_analysis=True),
    ],
)
def test_a_go_with_an_open_warning_needs_a_written_reason(warning):
    assert requires_reason(WorkspaceDecisionType.GO, warning) is True


def test_un_go_sense_veredicte_exigeix_que_algu_ho_escrigui():
    """La persona pot decidir igualment. El que no pot es fer-ho en silenci.

    No es bloqueja el GO a proposit: prohibir-lo empenyeria la decisio fora de
    l'eina, que es on deixa de quedar registrada. El que si que ha de passar es
    que consti que es va decidir sense saber si l'empresa complia.
    """
    warnings = DecisionWarnings(undetermined_eligibility=True)

    assert warnings.any_open is True
    assert "undetermined_eligibility" in warnings.codes()
    assert requires_reason(WorkspaceDecisionType.GO, warnings) is True


def test_els_codis_davis_arriben_sencers_a_la_interficie():
    """La pantalla en deriva el text: si el codi no hi es, l'avis no es veu."""
    warnings = DecisionWarnings(
        undetermined_eligibility=True,
        unreliable_analysis=True,
        outdated_analysis=True,
    )

    assert set(warnings.codes()) == {
        "undetermined_eligibility",
        "unreliable_analysis",
        "outdated_analysis",
    }


def test_a_no_go_never_needs_to_justify_itself_against_a_warning():
    """Descartar-se davant d'un problema es el que l'avis ja suggereix."""
    warning = DecisionWarnings(confirmed_blockers=3)

    assert requires_reason(WorkspaceDecisionType.NO_GO, warning) is False
    assert requires_reason(WorkspaceDecisionType.DEFER, warning) is False


def test_a_deferral_does_not_approve_anything():
    assert status_for(WorkspaceDecisionType.DEFER) == WorkspaceStatus.AWAITING_DECISION
    assert status_for(WorkspaceDecisionType.GO) == WorkspaceStatus.GO_APPROVED
    assert status_for(WorkspaceDecisionType.NO_GO) == WorkspaceStatus.NO_GO_APPROVED


# --------------------------------------------------------------------------- #
# Creacio                                                                       #
# --------------------------------------------------------------------------- #


async def test_opening_a_workspace_derives_the_checklist_from_the_requirements(
    db, tenant_a, tender, extraction_run
):
    """§9-10: la checklist surt dels Requirements, no de cap redaccio."""
    user, company = tenant_a
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.CERTIFICATION,
        value={"name": "ISO 27001"},
        operator=RequirementOperator.EXISTS,
        title="Certificacio ISO 27001",
    )
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.INSURANCE,
        value={"coverage_amount": 1000000, "currency": "EUR", "type": "civil_liability"},
        title="Assegurança de responsabilitat civil",
    )

    outcome = await service.create_workspace(
        db, company_id=company.id, tender=tender, user_id=user.id
    )

    assert outcome.created is True
    assert outcome.workspace.status == WorkspaceStatus.ANALYSIS
    items = await service.checklist_items(db, outcome.workspace.id)
    assert {item.title for item in items} == {
        "Acreditar ISO 27001",
        "Aportar polissa RC ≥ 1.000.000 €",
    }
    # La traçabilitat cap al requisit original es el que permet "Veure requisit".
    assert all(item.requirement_id is not None for item in items)
    assert all(item.source == ChecklistItemSource.REQUIREMENT for item in items)


async def test_opening_the_same_workspace_twice_does_not_duplicate_it(
    db, tenant_a, tender, extraction_run
):
    """§21: el boto de la fitxa no pot crear dos workspaces per la mateixa cosa."""
    user, company = tenant_a
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})

    first = await service.create_workspace(
        db, company_id=company.id, tender=tender, user_id=user.id
    )
    second = await service.create_workspace(
        db, company_id=company.id, tender=tender, user_id=user.id
    )

    assert second.created is False
    assert second.workspace.id == first.workspace.id
    total = await db.scalar(sa.select(sa.func.count()).select_from(BidWorkspace))
    assert total == 1
    # I tampoc no ha tornat a generar la checklist.
    items = await service.checklist_items(db, first.workspace.id)
    assert len(items) == 1


async def test_a_lot_workspace_is_a_different_candidacy(db, tenant_a, tender, extraction_run):
    """§3: la unicitat es company + tender + lot, no company + tender."""
    user, company = tenant_a
    lot = TenderLot(tender_id=tender.id, external_lot_id="1", lot_number=1, title="Lot 1")
    db.add(lot)
    await db.commit()
    await db.refresh(lot)

    whole = await open_workspace(db, company.id, tender, user.id)
    per_lot = await open_workspace(db, company.id, tender, user.id, tender_lot_id=lot.id)

    assert whole.id != per_lot.id
    assert per_lot.tender_lot_id == lot.id
    # I el de tot l'expedient segueix sent unic malgrat el NULL.
    again = await open_workspace(db, company.id, tender, user.id)
    assert again.id == whole.id


async def test_a_lot_workspace_only_inherits_its_own_requirements(
    db, tenant_a, tender, extraction_run
):
    user, company = tenant_a
    lot_a = TenderLot(tender_id=tender.id, external_lot_id="1", lot_number=1, title="Lot 1")
    lot_b = TenderLot(tender_id=tender.id, external_lot_id="2", lot_number=2, title="Lot 2")
    db.add_all([lot_a, lot_b])
    await db.commit()
    await db.refresh(lot_a)
    await db.refresh(lot_b)

    await add_requirement(
        db, extraction_run, tender, title="General", value={"amount": 1000}
    )
    await add_requirement(
        db,
        extraction_run,
        tender,
        title="Nomes del lot 2",
        value={"amount": 2000},
        tender_lot_id=lot_b.id,
    )

    workspace = await open_workspace(db, company.id, tender, user.id, tender_lot_id=lot_a.id)

    items = await service.checklist_items(db, workspace.id)
    assert len(items) == 1, "el requisit del lot 2 no ha d'entrar al workspace del lot 1"


# --------------------------------------------------------------------------- #
# La porta humana                                                               #
# --------------------------------------------------------------------------- #


async def test_a_fresh_workspace_has_no_human_decision(db, tenant_a, tender, extraction_run):
    """§5: la decisio humana neix PENDENT, no la dedueix cap regla."""
    user, company = tenant_a
    workspace = await open_workspace(db, company.id, tender, user.id)

    state = await service.load_state(db, workspace)

    assert state.latest_decision is None
    assert workspace.status == WorkspaceStatus.ANALYSIS
    assert workspace.status not in (
        WorkspaceStatus.GO_APPROVED,
        WorkspaceStatus.NO_GO_APPROVED,
    )


async def test_a_go_records_who_when_and_why(db, tenant_a, tender, extraction_run):
    """§6 i §19: la decisio queda signada, datada i amb el seu context."""
    user, company = tenant_a
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("2450000"), currency="EUR"
        )
    )
    await db.commit()
    run = await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)

    decision = await service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Tenim la documentacio a punt.",
    )

    assert decision.decided_by == user.id
    assert decision.decided_at is not None
    assert decision.reason == "Tenim la documentacio a punt."
    assert decision.compliance_run_id == run.id
    assert decision.requirement_run_id == extraction_run.id
    assert workspace.status == WorkspaceStatus.GO_APPROVED


async def test_a_viewer_cannot_approve_a_candidacy(db, tenant_a, tender, extraction_run):
    """§7: cal usuari autoritzat, no nomes autenticat."""
    from app.core.errors import PermissionDeniedError

    user, company = tenant_a
    viewer = await add_member(db, company.id, role=MembershipRole.VIEWER)
    workspace = await open_workspace(db, company.id, tender, user.id)

    with pytest.raises(PermissionDeniedError):
        await service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=viewer,
            role=MembershipRole.VIEWER,
            reason="Endavant",
        )

    assert workspace.status == WorkspaceStatus.ANALYSIS
    assert await db.scalar(sa.select(sa.func.count()).select_from(WorkspaceDecision)) == 0


async def test_a_go_with_a_blocker_requires_a_written_reason(
    db, tenant_a, tender, extraction_run
):
    """§8: no es prohibeix, pero s'ha d'escriure el perque."""
    user, company = tenant_a
    # Sense cap dada de facturacio: el requisit obligatori queda sense resoldre.
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)

    state = await service.load_state(db, workspace)
    assert state.warnings.any_open, "el cas ha de tenir algun avis obert"

    with pytest.raises(service.DecisionReasonRequiredError) as caught:
        await service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=user,
            role=MembershipRole.OWNER,
            reason=None,
        )
    assert caught.value.details["warnings"], "l'error ha de dir quins avisos hi havia"

    # Amb motiu, la mateixa decisio passa: qui decideix es la persona.
    decision = await service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="La polissa es signa dilluns i el plec admet acreditar-la despres.",
    )
    assert workspace.status == WorkspaceStatus.GO_APPROVED
    assert decision.reason is not None
    # I l'historial guarda els avisos que hi havia a la pantalla.
    assert (
        decision.mandatory_unknown
        + decision.mandatory_needs_review
        + decision.potential_blockers
        + decision.confirmed_blockers
    ) > 0


async def test_whitespace_is_not_a_reason(db, tenant_a, tender, extraction_run):
    user, company = tenant_a
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)

    with pytest.raises(service.DecisionReasonRequiredError):
        await service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=user,
            role=MembershipRole.OWNER,
            reason="   \n  ",
        )


async def test_changing_your_mind_keeps_the_previous_decision(
    db, tenant_a, tender, extraction_run
):
    """§19: si despres canvia a NO-GO, el GO anterior es conserva."""
    user, company = tenant_a
    workspace = await open_workspace(db, company.id, tender, user.id)

    await service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Revisats els dos avisos i disposem de la documentacio.",
    )
    await service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.NO_GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="El client ens ha demanat prioritzar un altre projecte.",
    )

    history = await service.decision_history(db, workspace.id)
    assert [entry.decision for entry in history] == [
        WorkspaceDecisionType.NO_GO,
        WorkspaceDecisionType.GO,
    ]
    assert history[1].reason == "Revisats els dos avisos i disposem de la documentacio."
    assert workspace.status == WorkspaceStatus.NO_GO_APPROVED


async def test_the_automated_recommendation_never_becomes_the_human_decision(
    db, tenant_a, tender, extraction_run
):
    """§2 i §5: el motor pot dir GO_CANDIDATE i la decisio segueix sent PENDENT."""
    user, company = tenant_a
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("9000000"), currency="EUR"
        )
    )
    db.add(
        CompanyTenderMatch(
            company_id=company.id,
            tender_id=tender.id,
            score=91,
            status=MatchStatus.CANDIDATE,
            data_coverage=1.0,
            rules_version="prefit-v1",
        )
    )
    await db.commit()
    run = await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)

    state = await service.load_state(db, workspace)

    assert run.recommendation in set(RecommendationResult)
    assert state.latest_decision is None, "cap recomanacio no escriu una decisio"
    assert workspace.status == WorkspaceStatus.ANALYSIS
    # Les quatre dimensions viatgen separades.
    assert state.prefit_score == 91
    assert state.compliance_run.overall_result is not None
    assert state.compliance_run.recommendation is not None


# --------------------------------------------------------------------------- #
# Checklist: regenerar sense trepitjar                                          #
# --------------------------------------------------------------------------- #


async def test_regenerating_the_checklist_neither_duplicates_nor_overwrites(
    db, tenant_a, tender, extraction_run
):
    """§11: una analisi nova afegeix el que falta i no toca la feina humana."""
    user, company = tenant_a
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.CERTIFICATION,
        value={"name": "ISO 9001"},
        title="ISO 9001",
    )
    workspace = await open_workspace(db, company.id, tender, user.id)

    items = await service.checklist_items(db, workspace.id)
    assert len(items) == 1
    items[0].status = ChecklistItemStatus.COMPLETED
    items[0].assigned_to = user.id
    await db.commit()

    # Una analisi nova amb un requisit mes.
    new_run = await add_extraction_run(db, tender)
    await add_requirement(
        db,
        new_run,
        tender,
        category=RequirementCategory.CERTIFICATION,
        value={"name": "ISO 9001"},
        title="ISO 9001",
    )
    await add_requirement(
        db,
        new_run,
        tender,
        category=RequirementCategory.CERTIFICATION,
        value={"name": "ISO 27001"},
        title="ISO 27001",
    )

    created = await service.regenerate_checklist(db, workspace, user_id=user.id)

    items = await service.checklist_items(db, workspace.id)
    assert created == 2, "els dos requisits del run nou son items nous"
    titles = [item.title for item in items]
    assert titles.count("Acreditar ISO 9001") == 2, (
        "el requisit del run antic conserva el seu item; el del run nou n'afegeix un"
    )
    completed = [item for item in items if item.status == ChecklistItemStatus.COMPLETED]
    assert len(completed) == 1
    assert completed[0].assigned_to == user.id, "la feina humana no s'ha tocat"


async def test_a_manual_checklist_item_is_never_regenerated_away(
    db, tenant_a, tender, extraction_run
):
    user, company = tenant_a
    workspace = await open_workspace(db, company.id, tender, user.id)
    db.add(
        WorkspaceChecklistItem(
            company_id=company.id,
            workspace_id=workspace.id,
            title="Demanar certificat bancari",
            source=ChecklistItemSource.MANUAL,
        )
    )
    await db.commit()

    await service.regenerate_checklist(db, workspace, user_id=user.id)

    items = await service.checklist_items(db, workspace.id)
    assert any(item.title == "Demanar certificat bancari" for item in items)


# --------------------------------------------------------------------------- #
# Analisi endarrerida                                                           #
# --------------------------------------------------------------------------- #


async def test_a_new_requirement_run_marks_the_workspace_as_outdated(
    db, tenant_a, tender, extraction_run
):
    """§11: no se substitueix res en silenci; es diu que ha quedat endarrerit."""
    user, company = tenant_a
    workspace = await open_workspace(db, company.id, tender, user.id)
    assert (await service.load_state(db, workspace)).analysis_outdated is False

    await add_extraction_run(db, tender)

    state = await service.load_state(db, workspace)
    assert state.analysis_outdated is True
    assert "requirement_run_changed" in state.outdated_reasons


async def test_a_new_compliance_run_marks_the_workspace_as_outdated(
    db, tenant_a, tender, extraction_run
):
    user, company = tenant_a
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)
    assert (await service.load_state(db, workspace)).analysis_outdated is False

    await run_compliance(db, company.id, tender)

    state = await service.load_state(db, workspace)
    assert state.analysis_outdated is True
    assert "compliance_run_changed" in state.outdated_reasons


async def test_a_human_decision_survives_a_recalculation(db, tenant_a, tender, extraction_run):
    """El calcul es pot repetir; el que va decidir una persona, no es recalcula."""
    user, company = tenant_a
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("2450000"), currency="EUR"
        )
    )
    await db.commit()
    first_run = await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)
    decision = await service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Complim tots els requisits.",
    )

    # Es refa l'analisi i el compliment sencers.
    new_extraction = await add_extraction_run(db, tender)
    await add_requirement(db, new_extraction, tender, value={"amount": 999999999})
    second_run = await run_compliance(db, company.id, tender)
    await service.regenerate_checklist(db, workspace, user_id=user.id)

    await db.refresh(workspace)
    history = await service.decision_history(db, workspace.id)
    assert second_run.id != first_run.id
    assert workspace.status == WorkspaceStatus.GO_APPROVED, "la decisio humana no es recalcula"
    assert len(history) == 1
    assert history[0].id == decision.id
    assert history[0].compliance_run_id == first_run.id, (
        "la decisio segueix apuntant al calcul que hi havia quan es va prendre"
    )


# --------------------------------------------------------------------------- #
# Termini                                                                       #
# --------------------------------------------------------------------------- #


async def test_a_passed_deadline_is_shown_and_forces_a_justification(
    db, tenant_a, extraction_run, tender
):
    """§15: el termini passat no s'amaga; i decidir GO llavors s'ha de justificar."""
    user, company = tenant_a
    tender.deadline_at = PAST_DEADLINE
    await db.commit()
    workspace = await open_workspace(db, company.id, tender, user.id)

    state = await service.load_state(db, workspace)

    assert state.deadline_at == PAST_DEADLINE
    assert state.deadline_passed is True
    assert state.days_remaining is not None and state.days_remaining < 0
    assert "deadline_passed" in state.warnings.codes()

    with pytest.raises(service.DecisionReasonRequiredError):
        await service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=user,
            role=MembershipRole.OWNER,
            reason=None,
        )


async def test_days_remaining_is_calculated_not_stored(db, tenant_a, tender, extraction_run):
    user, company = tenant_a
    workspace = await open_workspace(db, company.id, tender, user.id)

    now = FUTURE_DEADLINE - timedelta(days=5, hours=2)
    state = await service.load_state(db, workspace, now=now)

    assert state.days_remaining == 5
    assert state.deadline_passed is False
    assert not hasattr(workspace, "days_remaining"), "no es persisteix: dema seria mentida"


# --------------------------------------------------------------------------- #
# Multi-tenant                                                                  #
# --------------------------------------------------------------------------- #


async def test_a_task_can_only_be_assigned_within_the_company(
    db, tenant_a, tenant_b, tender, extraction_run
):
    """§13 i §24: els responsables surten dels memberships, no de cap llista lliure."""
    user_a, company_a = tenant_a
    user_b, _company_b = tenant_b

    await service.assert_assignable(db, company_a.id, user_a.id)

    with pytest.raises(service.NotAssignableError):
        await service.assert_assignable(db, company_a.id, user_b.id)


async def test_one_company_cannot_reach_another_companys_workspace(
    db, tenant_a, tenant_b, tender, extraction_run
):
    from app.core.errors import NotFoundError

    user_a, company_a = tenant_a
    _user_b, company_b = tenant_b
    workspace = await open_workspace(db, company_a.id, tender, user_a.id)

    with pytest.raises(NotFoundError):
        await service.get_workspace(db, company_b.id, workspace.id)

    assert await service.find_workspace(db, company_b.id, tender.id) is None


async def test_two_companies_can_bid_for_the_same_tender(
    db, tenant_a, tenant_b, tender, extraction_run
):
    user_a, company_a = tenant_a
    user_b, company_b = tenant_b

    first = await open_workspace(db, company_a.id, tender, user_a.id)
    second = await open_workspace(db, company_b.id, tender, user_b.id)

    assert first.id != second.id


# --------------------------------------------------------------------------- #
# API                                                                           #
# --------------------------------------------------------------------------- #


async def test_the_api_separates_prefit_eligibility_recommendation_and_decision(
    db, client_a, tenant_a, tender, extraction_run
):
    """§5: quatre camps diferents. Cap no es fusiona amb cap altre."""
    user, company = tenant_a
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    await run_compliance(db, company.id, tender)

    created = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/workspace", json={}
    )
    assert created.status_code == 200, created.text
    workspace_id = created.json()["workspace"]["id"]

    detail = await client_a.get(f"/api/v1/companies/{company.id}/workspaces/{workspace_id}")
    body = detail.json()

    assert {"prefit_score", "eligibility", "recommendation", "human_decision"} <= set(body)
    assert body["human_decision"] is None, "PENDENT mentre ningu no decideixi"
    assert body["eligibility"] is not None
    assert body["counters"]["mandatory_total"] >= 1
    assert body["can_decide"] is True


async def test_the_api_refuses_a_go_without_reason_when_there_are_warnings(
    db, client_a, tenant_a, tender, extraction_run
):
    user, company = tenant_a
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)

    refused = await client_a.post(
        f"/api/v1/companies/{company.id}/workspaces/{workspace.id}/decisions",
        json={"decision": "go"},
    )

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "decision_reason_required"
    assert refused.json()["error"]["details"]["warnings"]


async def test_the_api_lets_a_viewer_look_but_not_decide(
    db, client_factory, tenant_a, tender, extraction_run
):
    user, company = tenant_a
    viewer = await add_member(db, company.id, role=MembershipRole.VIEWER)
    workspace = await open_workspace(db, company.id, tender, user.id)

    viewer_client = client_factory()
    await login(viewer_client, viewer.email)

    seen = await viewer_client.get(f"/api/v1/companies/{company.id}/workspaces/{workspace.id}")
    assert seen.status_code == 200
    assert seen.json()["can_decide"] is False

    refused = await viewer_client.post(
        f"/api/v1/companies/{company.id}/workspaces/{workspace.id}/decisions",
        json={"decision": "go", "reason": "Endavant"},
    )
    assert refused.status_code == 403


async def test_the_api_hides_a_workspace_from_another_tenant(
    db, client_b, tenant_a, tenant_b, tender, extraction_run
):
    """§24: ni veure, ni editar, ni decidir, ni assignar."""
    user_a, company_a = tenant_a
    _user_b, company_b = tenant_b
    workspace = await open_workspace(db, company_a.id, tender, user_a.id)

    # Amb l'empresa de l'altre a la URL: no hi pertany.
    assert (
        await client_b.get(f"/api/v1/companies/{company_a.id}/workspaces/{workspace.id}")
    ).status_code == 404
    # Amb la seva propia empresa a la URL: el workspace no hi es.
    assert (
        await client_b.get(f"/api/v1/companies/{company_b.id}/workspaces/{workspace.id}")
    ).status_code == 404
    assert (
        await client_b.post(
            f"/api/v1/companies/{company_b.id}/workspaces/{workspace.id}/decisions",
            json={"decision": "no_go"},
        )
    ).status_code == 404
    assert (
        await client_b.post(
            f"/api/v1/companies/{company_b.id}/workspaces/{workspace.id}/notes",
            json={"text": "hola"},
        )
    ).status_code == 404


async def test_the_api_refuses_an_assignee_from_another_company(
    db, client_a, tenant_a, tenant_b, tender, extraction_run
):
    user_a, company_a = tenant_a
    user_b, _company_b = tenant_b
    workspace = await open_workspace(db, company_a.id, tender, user_a.id)

    response = await client_a.post(
        f"/api/v1/companies/{company_a.id}/workspaces/{workspace.id}/tasks",
        json={"title": "Demanar certificat bancari", "assigned_to": str(user_b.id)},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "assignee_not_in_company"


async def test_the_decision_history_endpoint_is_append_only(
    db, client_a, tenant_a, tender, extraction_run
):
    user, company = tenant_a
    workspace = await open_workspace(db, company.id, tender, user.id)
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"

    await client_a.post(f"{base}/decisions", json={"decision": "go", "reason": "Endavant."})
    await client_a.post(f"{base}/decisions", json={"decision": "no_go", "reason": "Millor no."})

    history = (await client_a.get(f"{base}/decisions")).json()

    assert [entry["decision"] for entry in history] == ["no_go", "go"]
    assert history[1]["reason"] == "Endavant."
    assert history[1]["decided_by_name"], "l'historial ha de dir qui, no un UUID"
    # No hi ha cap endpoint per editar-les ni esborrar-les.
    assert (await client_a.delete(f"{base}/decisions")).status_code in (404, 405)


# --------------------------------------------------------------------------- #
# Acceptacio (§27)                                                              #
# --------------------------------------------------------------------------- #


async def test_the_full_candidacy_workflow(db, client_a, tenant_a, tender, extraction_run):
    """Licitacio -> workspace -> revisar -> checklist -> tasca -> nota -> GO.

    I despres tornar-hi i veure la decisio, immutable, amb qui, quan i per que.
    """
    user, company = tenant_a
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.CERTIFICATION,
        value={"name": "ISO 27001"},
        title="Certificacio ISO 27001",
    )
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("2450000"), currency="EUR"
        )
    )
    await db.commit()
    await run_compliance(db, company.id, tender)

    # 1. Des de la fitxa de la licitacio, obrir la candidatura.
    created = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/workspace", json={}
    )
    assert created.status_code == 200, created.text
    assert created.json()["created"] is True
    workspace_id = created.json()["workspace"]["id"]
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace_id}"

    # 2. Veure pre-fit, compliment i requisits sense fusionar-los, amb blocadors.
    detail = (await client_a.get(base)).json()
    assert detail["expediente_number"] == "1/2026"
    assert detail["days_remaining"] is not None
    assert detail["human_decision"] is None
    assert "open_warnings" in detail
    assert len(detail["checklist"]) == 1
    assert detail["checklist"][0]["title"] == "Acreditar ISO 27001"

    # 3. Assignar una tasca a algu de l'empresa.
    task = await client_a.post(
        f"{base}/tasks",
        json={"title": "Demanar certificat bancari", "assigned_to": str(user.id)},
    )
    assert task.status_code == 200, task.text
    assert task.json()["assigned_to_name"]

    # 4. Escriure una nota.
    note = await client_a.post(f"{base}/notes", json={"text": "Parlat amb el responsable."})
    assert note.status_code == 200, note.text

    # 5. Decidir GO, amb motiu.
    decided = await client_a.post(
        f"{base}/decisions",
        json={
            "decision": "go",
            "reason": "Revisats els dos avisos i disposem de la documentacio.",
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "go_approved"
    assert decided.json()["human_decision"] == "go"

    # 6. Tornar-hi: la decisio hi es, signada i datada.
    again = (await client_a.get(base)).json()
    assert again["human_decision"] == "go"
    assert again["latest_decision"]["decided_by"] == str(user.id)
    assert again["latest_decision"]["decided_at"]
    assert (
        again["latest_decision"]["reason"]
        == "Revisats els dos avisos i disposem de la documentacio."
    )
    assert len(again["tasks"]) == 1
    assert len(again["notes"]) == 1
    assert len(again["decisions"]) == 1


# --------------------------------------------------------------------------- #
# Comptadors de revisio humana (§22)                                            #
# --------------------------------------------------------------------------- #


async def test_the_mandatory_breakdown_adds_up_to_the_mandatory_total(
    db, tenant_a, tender, extraction_run
):
    """§22: si les xifres no sumen el total, la taula no s'enten."""
    user, company = tenant_a
    # Un obligatori que es compleix.
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    # Un obligatori sense dades al perfil per resoldre'l.
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.CERTIFICATION,
        value={"name": "ISO 27001"},
        title="ISO 27001",
    )
    # Un de puntuable: NO ha d'entrar al desglossament d'obligatoris.
    await add_requirement(
        db,
        extraction_run,
        tender,
        obligation=RequirementObligation.SCORED,
        value={"amount": 100},
        title="Millora puntuable",
    )
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("2450000"), currency="EUR"
        )
    )
    await db.commit()
    await run_compliance(db, company.id, tender)
    workspace = await open_workspace(db, company.id, tender, user.id)

    c = (await service.load_state(db, workspace)).counters

    assert c.total == 3
    assert c.mandatory_total == 2, "el requisit puntuable no es obligatori"
    assert (
        c.mandatory_satisfies
        + c.mandatory_does_not_satisfy
        + c.mandatory_unknown
        + c.mandatory_needs_review
        + c.mandatory_not_applicable
    ) == c.mandatory_total
    assert c.mandatory_satisfies == 1


# --------------------------------------------------------------------------- #
# Transicions d'estat: l'unica porta cap a `status`                             #
# --------------------------------------------------------------------------- #


def test_every_status_declares_where_it_can_go():
    """Un estat sense entrada al graf seria un cul-de-sac silencios."""
    assert set(ALLOWED_TRANSITIONS) == set(WorkspaceStatus)
    for current, targets in ALLOWED_TRANSITIONS.items():
        assert targets <= set(WorkspaceStatus), current


def test_an_impossible_transition_is_rejected():
    """El graf no es decoratiu: el que no hi es, no passa."""
    with pytest.raises(InvalidTransitionError):
        assert_transition(
            WorkspaceStatus.ANALYSIS,
            WorkspaceStatus.PREPARING_BID,
            actor=TransitionActor.HUMAN_DECISION,
        )
    with pytest.raises(InvalidTransitionError):
        assert_transition(
            WorkspaceStatus.SUBMITTED,
            WorkspaceStatus.ANALYSIS,
            actor=TransitionActor.HUMAN_DECISION,
        )


def test_a_decision_can_change_its_mind_but_not_skip_ahead():
    assert can_transition(
        WorkspaceStatus.GO_APPROVED,
        WorkspaceStatus.NO_GO_APPROVED,
        actor=TransitionActor.HUMAN_DECISION,
    )
    assert can_transition(
        WorkspaceStatus.GO_APPROVED,
        WorkspaceStatus.GO_APPROVED,
        actor=TransitionActor.HUMAN_DECISION,
    ), "reafirmar un GO amb un motiu nou es una accio real"
    assert not can_transition(
        WorkspaceStatus.GO_APPROVED,
        WorkspaceStatus.READY_FOR_FINAL_REVIEW,
        actor=TransitionActor.HUMAN_DECISION,
    ), "entre aprovar i tenir l'oferta a punt hi ha feina pel mig"


@pytest.mark.parametrize(
    "target",
    # Escrits a ma i NO derivats de `HUMAN_ONLY_STATUSES`: un test parametritzat
    # sobre la constant que ha de vigilar deixa de provar res si algu buida la
    # constant. Aquesta llista es el contracte; la constant es la implementacio.
    [
        WorkspaceStatus.GO_APPROVED,
        WorkspaceStatus.NO_GO_APPROVED,
        WorkspaceStatus.SUBMITTED,
    ],
)
def test_no_automatic_process_can_write_an_approved_status(target):
    """Cap regla, cap recalcul i cap recomanacio no pot aprovar res."""
    assert target in HUMAN_ONLY_STATUSES

    reachable = [
        current for current, targets in ALLOWED_TRANSITIONS.items() if target in targets
    ]
    assert reachable, target

    for current in reachable:
        with pytest.raises(AutomaticTransitionForbiddenError):
            assert_transition(current, target, actor=TransitionActor.SYSTEM)


# --------------------------------------------------------------------------- #
# Invariant: `submitted` (§23)                                                  #
# --------------------------------------------------------------------------- #


def test_submitted_is_unreachable_for_every_actor_that_exists_today():
    """L'invariant central: cap cami actual del codi no presenta una oferta.

    Ni un proces automatic ni una decisio GO. `HUMAN_FINAL_APPROVAL` si que
    existeix ara —aprova el PAQUET de presentacio— pero no escriu `submitted`
    enlloc: aprovar per presentar i presentar son dos actes diferents.
    """
    assert is_human_only(WorkspaceStatus.SUBMITTED)
    assert WorkspaceStatus.SUBMITTED in FINAL_APPROVAL_STATUSES

    for actor in (TransitionActor.SYSTEM, TransitionActor.HUMAN_DECISION):
        with pytest.raises(AutomaticTransitionForbiddenError):
            assert_transition(
                WorkspaceStatus.APPROVED_FOR_SUBMISSION,
                WorkspaceStatus.SUBMITTED,
                actor=actor,
            )

    # I quan existeixi, nomes des d'un paquet aprovat per una persona.
    origins = {
        current
        for current, targets in ALLOWED_TRANSITIONS.items()
        if WorkspaceStatus.SUBMITTED in targets
    }
    assert origins == {WorkspaceStatus.APPROVED_FOR_SUBMISSION}


def test_no_human_decision_ever_maps_to_submitted():
    """Presentar no es una decisio de Go/No-Go: es un acte posterior i separat."""
    outcomes = {status_for(decision) for decision in WorkspaceDecisionType}

    assert outcomes == {
        WorkspaceStatus.GO_APPROVED,
        WorkspaceStatus.NO_GO_APPROVED,
        WorkspaceStatus.AWAITING_DECISION,
    }
    assert WorkspaceStatus.SUBMITTED not in outcomes


def test_the_final_approval_actor_is_used_by_exactly_one_service():
    """L'actor mes fort del sistema no pot estar escampat.

    L'unic que el fa servir es l'aprovacio d'un paquet de presentacio. Si algun
    dia se n'afegeix un altre cami —la presentacio, per exemple— aquest test
    l'obliga a passar per aqui i a llegir per que existeix aquest actor.

    Es busca l'US (`TransitionActor.HUMAN_FINAL_APPROVAL`) i no el nom sol:
    altres moduls poden —i han de poder— explicar als seus comentaris que
    `submitted` l'exigeix.
    """
    import app

    used = sorted(
        path.name
        for path in Path(app.__file__).parent.rglob("*.py")
        if "TransitionActor.HUMAN_FINAL_APPROVAL" in path.read_text(encoding="utf-8")
    )

    assert used == ["submission_package.py", "transitions.py"]


def test_submitted_is_never_written_outside_the_transitions_module():
    """Cap servei, router o motor no anomena `SUBMITTED` per assignar-lo."""
    import app

    mentions = {
        path.name
        for path in Path(app.__file__).parent.rglob("*.py")
        if "SUBMITTED" in path.read_text(encoding="utf-8")
    }

    assert mentions == {"enums.py", "transitions.py"}, (
        "`SUBMITTED` nomes s'ha de definir i governar; cap servei no l'ha d'escriure"
    )


def test_the_service_writes_status_through_a_single_gate():
    """Estructural: una sola assignacio a `status`, i passa per `set_status`.

    Si algu n'obre una altra, aquest test l'ha de fer visible en comptes de
    deixar-la passar dins d'un PATCH generic.
    """
    import inspect

    writers = [
        line.strip()
        for line in inspect.getsource(service).splitlines()
        if "workspace.status =" in line
    ]

    assert writers == ["workspace.status = set_status("]


async def test_a_recalculation_never_advances_the_state(db, tenant_a, tender, extraction_run):
    """D'extrem a extrem: recalcular no aprova, no presenta i no toca l'estat.

    Recomanacio nova, compliment nou i checklist regenerada: l'estat es el
    mateix abans i despres, i en cap moment no arriba a `submitted`.
    """
    user, company = tenant_a
    await add_requirement(db, extraction_run, tender, value={"amount": 450000})
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal("9000000"), currency="EUR"
        )
    )
    db.add(
        CompanyTenderMatch(
            company_id=company.id,
            tender_id=tender.id,
            score=98,
            status=MatchStatus.CANDIDATE,
            data_coverage=1.0,
            rules_version="prefit-v1",
        )
    )
    await db.commit()
    workspace = await open_workspace(db, company.id, tender, user.id)
    before = workspace.status

    run = await run_compliance(db, company.id, tender)
    new_extraction = await add_extraction_run(db, tender)
    await add_requirement(db, new_extraction, tender, value={"amount": 1000})
    await service.regenerate_checklist(db, workspace, user_id=user.id)
    await run_compliance(db, company.id, tender)

    await db.refresh(workspace)
    assert run.recommendation == RecommendationResult.GO_CANDIDATE
    assert workspace.status == before == WorkspaceStatus.ANALYSIS
    assert workspace.status != WorkspaceStatus.SUBMITTED
    assert await db.scalar(sa.select(sa.func.count()).select_from(WorkspaceDecision)) == 0


async def test_a_submitted_workspace_refuses_a_new_go(db, tenant_a, tender, extraction_run):
    """El graf tambe protegeix cap enrere: presentada ja no es torna a aprovar."""
    user, company = tenant_a
    workspace = await open_workspace(db, company.id, tender, user.id)
    # S'hi arriba per la porta del darrere, com si la fase futura ja hi fos.
    workspace.status = WorkspaceStatus.SUBMITTED
    await db.commit()

    with pytest.raises(service.WorkspaceTransitionRejectedError) as caught:
        await service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=user,
            role=MembershipRole.OWNER,
            reason="Tornem-hi",
        )

    assert caught.value.details["current"] == "submitted"
    assert caught.value.details["target"] == "go_approved"


# --------------------------------------------------------------------------- #
# La decisio conserva els avisos que la persona va acceptar (N4)                #
# --------------------------------------------------------------------------- #


async def test_una_decisio_desa_els_avisos_que_hi_havia(db, workspace_with_analysis):
    """L'historial ha de poder dir "es va aprovar sabent que era una simulacio".

    Els comptadors de bloquejos no basten: els tres zeros que ensenyava
    l'historial eren certs i no explicaven el que de veritat pesava a la
    pantalla.
    """
    workspace, user = workspace_with_analysis

    decision = await service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Endavant tot i la simulacio.",
    )

    assert decision.open_warnings is not None
    assert "unreliable_analysis" in decision.open_warnings


async def test_la_fotografia_no_canvia_quan_canvia_lestat(db, workspace_with_analysis):
    """Es una fotografia, no una consulta.

    Si l'historial reconstruis els avisos a partir de l'estat d'avui,
    explicaria la decisio amb informacio que qui la va prendre no tenia.
    """
    workspace, user = workspace_with_analysis
    decision = await service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Endavant tot i la simulacio.",
    )
    desats = list(decision.open_warnings or [])

    # L'analisi passa a ser real DESPRES de decidir.
    run = await db.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.tender_id == workspace.tender_id
        )
    )
    run.provider = "anthropic"
    await db.commit()
    await db.refresh(decision)

    assert list(decision.open_warnings or []) == desats


async def test_una_decisio_antiga_diu_que_no_es_va_registrar(db, workspace_with_analysis):
    """`NULL` vol dir "no es va anotar", no "no hi havia cap avis"."""
    workspace, user = workspace_with_analysis
    decision = await service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Endavant.",
    )
    # Com quedaria una decisio anterior a la columna.
    decision.open_warnings = None
    await db.commit()
    await db.refresh(decision)

    assert decision.open_warnings is None
