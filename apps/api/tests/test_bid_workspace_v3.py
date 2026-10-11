"""Decisions conscients de l'analisi v3 (F-4b): avisos, fotografia i atomicitat.

El motiu obligatori d'un GO surt de l'analisi vigent del plec (v3), no del
compliment antic. La decisio desa la fotografia v1 de l'analisi que la persona
tenia a la vista, i decisio, canvi d'estat i auditoria s'escriuen en una sola
transaccio. Dades sintetiques; cap crida a cap model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.knowledge import vocabulary
from app.models.audit import AuditLog
from app.models.compliance import CompanyComplianceRun, CompanyRequirementAssessment
from app.models.enums import (
    BlockerStatus,
    EligibilityResult,
    MatchStatus,
    MembershipRole,
    WorkspaceDecisionType,
    WorkspaceStatus,
)
from app.models.identity import Company
from app.models.matching import CompanyTenderMatch
from app.models.requirements import RequirementExtractionRun
from app.models.tender_analysis import TenderAnalysisRun
from app.models.tender_lifecycle import TenderRevision
from app.models.tenders import Tender, TenderLot
from app.models.workspace import BidWorkspace, WorkspaceDecision
from app.schemas.workspace import DecisionRead
from app.services import audit
from app.services import bid_workspace as service
from app.services import tender_analysis as analysis
from app.services.compliance_analysis import analyze_company_compliance
from app.workspace.analysis_snapshot import parse_snapshot
from app.workspace.decision import AnalysisDecisionWarnings
from tests.fixtures.company_twin import build_company_twin
from tests.test_bid_workspace import add_member, open_workspace
from tests.test_tender_analysis_engine import _synthetic_tender
from tests.test_tender_scope_models import _public_tender

pytestmark = pytest.mark.asyncio

PAST_DEADLINE = datetime.now(UTC) - timedelta(days=3)


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


# --------------------------------------------------------------------------- #
# Ajudes                                                                        #
# --------------------------------------------------------------------------- #


async def v3_case(db, company, *, blocker: bool = False, analysed: bool = True, deadline_at=None):
    """Plec sintetic + besso d'empresa + (opcionalment) un run v3 vigent.

    Amb el besso net el plec sintetic no te cap `blocker_potential`; amb
    `blocker=True` l'empresa diu que no te suport 24x7 i n'apareix un. El
    termini forma part dels inputs de l'analisi: es fixa abans de calcular-la.
    """
    tender, *_ = await _synthetic_tender(db)
    tender.deadline_at = deadline_at
    rows = await build_company_twin(db, company)
    if blocker:
        rows["facts"]["support_24x7"].value = False
    await db.commit()
    run = None
    if analysed:
        run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
        await db.commit()
    return tender, rows, run


async def make_stale(db, rows):
    """Canviar coneixement sense recalcular deixa el run vigent desactualitzat."""
    rows["facts"]["own_ftth_infrastructure"].value = False
    await db.commit()


async def recalculate(db, company_id, tender_id) -> TenderAnalysisRun:
    run = (await analysis.analyze_tender(db, company_id=company_id, tender_id=tender_id)).run
    await db.commit()
    return run


def _detail_url(company_id, workspace_id) -> str:
    return f"/api/v1/companies/{company_id}/workspaces/{workspace_id}"


async def _decisions(db, workspace_id=None) -> int:
    query = sa.select(sa.func.count()).select_from(WorkspaceDecision)
    if workspace_id is not None:
        query = query.where(WorkspaceDecision.workspace_id == workspace_id)
    return await db.scalar(query)


async def _decision_audits(db) -> int:
    return await db.scalar(
        sa.select(sa.func.count())
        .select_from(AuditLog)
        .where(AuditLog.entity_type == "workspace_decision")
    )


async def go(db, workspace, user, *, reason=None, decision=WorkspaceDecisionType.GO, now=None):
    return await service.record_decision(
        db,
        workspace,
        decision=decision,
        user=user,
        role=MembershipRole.OWNER,
        reason=reason,
        now=now,
    )


# --------------------------------------------------------------------------- #
# Avisos v3 (§5) i motiu obligatori                                             #
# --------------------------------------------------------------------------- #


async def test_a_current_clean_analysis_lets_a_go_through_without_reason(db, tenant_a, client_a):
    """§10.2 i §10.4: v3 vigent, 0 possibles bloqueigs, termini obert, cap compliment."""
    user, company = tenant_a
    tender, _rows, run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await db.commit()

    detail = (await client_a.get(_detail_url(company.id, workspace.id))).json()
    assert detail["decision_warnings"] == []
    assert detail["decision_warning_basis"] == "analysis_v3"
    assert detail["go_requires_reason"] is False

    decision = await go(db, workspace, user)
    assert await db.scalar(sa.select(sa.func.count()).select_from(CompanyComplianceRun)) == 0
    assert decision.reason is None
    assert decision.analysis_run_id == run.id
    assert workspace.status == WorkspaceStatus.GO_APPROVED


async def test_a_potential_blocker_requires_a_reason_with_the_v3_basis(db, tenant_a, client_a):
    """§10.5: `blocker_potential` -> 422 amb `warnings` i `basis`."""
    user, company = tenant_a
    tender, _rows, run = await v3_case(db, company, blocker=True)
    assert analysis.summary_of(run)["blocker_potential_count"] >= 1
    workspace = await open_workspace(db, company.id, tender, user.id)
    await db.commit()

    refused = await client_a.post(
        f"{_detail_url(company.id, workspace.id)}/decisions", json={"decision": "go"}
    )

    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "decision_reason_required"
    assert error["details"] == {"warnings": ["blocker_potential"], "basis": "analysis_v3"}
    assert await _decisions(db, workspace.id) == 0


async def test_a_stale_analysis_requires_a_reason(db, tenant_a):
    """§10.6."""
    user, company = tenant_a
    tender, rows, _run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await make_stale(db, rows)

    with pytest.raises(service.DecisionReasonRequiredError) as caught:
        await go(db, workspace, user)

    assert caught.value.details == {"warnings": ["analysis_stale"], "basis": "analysis_v3"}


async def test_without_an_analysis_a_go_requires_a_reason(db, tenant_a):
    """§10.7: sense run v3 vigent -> `analysis_missing`."""
    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company, analysed=False)
    workspace = await open_workspace(db, company.id, tender, user.id)

    with pytest.raises(service.DecisionReasonRequiredError) as caught:
        await go(db, workspace, user)

    assert caught.value.details == {"warnings": ["analysis_missing"], "basis": "analysis_v3"}
    assert caught.value.code == "decision_reason_required"


async def test_a_passed_deadline_requires_a_reason(db, tenant_a):
    """§10.8: el termini es un fet del plec, independent del motor."""
    user, company = tenant_a
    # El termini es mira contra `now`: un `now` posterior al termini el fa passat
    # sense tocar cap input de l'analisi (que continua vigent).
    deadline = datetime.now(UTC) + timedelta(days=2)
    tender, _rows, _run = await v3_case(db, company, deadline_at=deadline)
    workspace = await open_workspace(db, company.id, tender, user.id)
    state = await service.load_state(db, workspace, with_analysis=True)
    assert state.analysis_warnings is not None
    assert state.analysis_warnings.codes() == []

    later = datetime.now(UTC) + timedelta(days=5)
    with pytest.raises(service.DecisionReasonRequiredError) as caught:
        await go(db, workspace, user, now=later)

    assert caught.value.details == {"warnings": ["deadline_passed"], "basis": "analysis_v3"}
    decision = await go(db, workspace, user, now=later, reason="Hi ha prorroga publicada.")
    assert decision.deadline_passed is True
    assert decision.analysis_snapshot_json["warnings"] == ["deadline_passed"]


@pytest.mark.parametrize("kind", [WorkspaceDecisionType.NO_GO, WorkspaceDecisionType.DEFER])
async def test_no_go_and_defer_never_require_a_reason(db, tenant_a, kind):
    """§10.9: amb tots els avisos v3 possibles oberts alhora."""
    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company, analysed=False)
    tender_row = await db.get(Tender, tender.id)
    tender_row.deadline_at = PAST_DEADLINE
    await db.commit()
    workspace = await open_workspace(db, company.id, tender, user.id)

    decision = await go(db, workspace, user, decision=kind)

    assert decision.reason is None
    assert decision.analysis_snapshot_json["warnings"] == ["analysis_missing", "deadline_passed"]


async def test_permissions_and_tenancy_are_unchanged(
    db, client_factory, client_b, tenant_a, tenant_b
):
    """§10.10: un viewer no decideix (403) i un altre tenant no hi arriba (404)."""
    from tests.conftest import login

    user, company = tenant_a
    _user_b, company_b = tenant_b
    tender, _rows, _run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    viewer = await add_member(db, company.id, role=MembershipRole.VIEWER)
    await db.commit()

    viewer_client = client_factory()
    await login(viewer_client, viewer.email)
    refused = await viewer_client.post(
        f"{_detail_url(company.id, workspace.id)}/decisions",
        json={"decision": "go", "reason": "Endavant"},
    )
    assert refused.status_code == 403
    other = await client_b.post(
        f"{_detail_url(company_b.id, workspace.id)}/decisions", json={"decision": "no_go"}
    )
    assert other.status_code == 404
    assert await _decisions(db, workspace.id) == 0
    assert await _decision_audits(db) == 0


# --------------------------------------------------------------------------- #
# Independencia del compliment antic (§10.3 i §10.11b)                          #
# --------------------------------------------------------------------------- #


async def _legacy_compliance(db, company, tender, variant: str) -> None:
    if variant == "none":
        return
    if variant == "unreliable_provider":
        run = await db.scalar(
            sa.select(RequirementExtractionRun).where(
                RequirementExtractionRun.tender_id == tender.id,
                RequirementExtractionRun.is_current.is_(True),
            )
        )
        run.provider = "fixture"
        await db.commit()
        return
    company_row = await db.get(Company, company.id)
    outcome = await analyze_company_compliance(db, company_row, tender, force=True)
    run = outcome.run
    if variant == "undetermined":
        run.overall_result = EligibilityResult.UNDETERMINED
    elif variant == "confirmed_blockers":
        assessment = await db.scalar(
            sa.select(CompanyRequirementAssessment).where(
                CompanyRequirementAssessment.assessment_run_id == run.id
            )
        )
        assessment.blocker_status = BlockerStatus.CONFIRMED
    await db.commit()


@pytest.mark.parametrize(
    "variant", ["none", "undetermined", "confirmed_blockers", "unreliable_provider"]
)
async def test_the_old_compliance_does_not_change_the_v3_warnings(db, tenant_a, client_a, variant):
    user, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await build_company_twin(db, company)
    await db.commit()
    await _legacy_compliance(db, company, await db.get(Tender, tender.id), variant)
    await recalculate(db, company.id, tender.id)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await db.commit()

    state = await service.load_state(db, workspace, with_analysis=True)
    # El cas es de veritat diferent per al motor antic...
    legacy = state.warnings.codes()
    expected_legacy = {
        "none": "missing_compliance",
        "undetermined": "undetermined_eligibility",
        "confirmed_blockers": "confirmed_blockers",
        "unreliable_provider": "unreliable_analysis",
    }[variant]
    assert expected_legacy in legacy
    # ...i no canvia res per a la v3.
    detail = (await client_a.get(_detail_url(company.id, workspace.id))).json()
    assert detail["decision_warnings"] == []
    assert detail["go_requires_reason"] is False
    assert expected_legacy in detail["open_warnings"], "open_warnings segueix sent legacy"
    decision = await go(db, workspace, user)
    assert decision.reason is None


# --------------------------------------------------------------------------- #
# La fila nova (§3)                                                             #
# --------------------------------------------------------------------------- #


async def test_a_new_decision_writes_no_legacy_snapshot_but_keeps_the_facts(db, tenant_a):
    user, company = tenant_a
    tender, _rows, run = await v3_case(db, company)
    company_row = await db.get(Company, company.id)
    await analyze_company_compliance(db, company_row, await db.get(Tender, tender.id), force=True)
    db.add(
        CompanyTenderMatch(
            company_id=company.id,
            tender_id=tender.id,
            score=77,
            status=MatchStatus.CANDIDATE,
            data_coverage=1.0,
            rules_version="prefit-v1",
        )
    )
    revision = TenderRevision(
        tender_id=tender.id,
        revision_number=1,
        normalized_snapshot={},
        normalized_hash="0" * 64,
        trigger="ingestion",
    )
    db.add(revision)
    await db.commit()
    run = await recalculate(db, company.id, tender.id)
    workspace = await open_workspace(db, company.id, tender, user.id)

    decision = await go(db, workspace, user, reason="Tot en ordre.")
    await db.refresh(decision)

    assert decision.compliance_run_id is None
    assert decision.requirement_run_id is None
    assert decision.automated_recommendation is None
    assert decision.open_warnings is None
    assert (
        decision.confirmed_blockers,
        decision.potential_blockers,
        decision.mandatory_unknown,
        decision.mandatory_needs_review,
    ) == (0, 0, 0, 0)
    assert decision.prefit_score == 77
    assert decision.deadline_passed is False
    assert decision.tender_revision_id == revision.id
    assert decision.analysis_run_id == run.id
    snapshot = parse_snapshot(decision.analysis_snapshot_json)
    assert snapshot.state == "current"
    assert decision.analysis_snapshot_json == snapshot.to_json_dict()
    read = DecisionRead.model_validate(decision)
    assert read.analysis_snapshot_json is not None, "la branca v3 es pel snapshot"


async def test_without_analysis_the_snapshot_is_absent(db, tenant_a):
    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company, analysed=False)
    workspace = await open_workspace(db, company.id, tender, user.id)

    decision = await go(db, workspace, user, reason="Ho decidim sense analisi.")

    assert decision.analysis_run_id is None
    assert decision.analysis_snapshot_json == {
        "schema_version": 1,
        "state": "absent",
        "warnings": ["analysis_missing"],
        # 3B-2 §4.4: l'estat del paquet es del plec, hi sigui o no l'analisi. Sense
        # passada de classificacio global: `unknown`.
        "package_status": "unknown",
        "open_gap_count": 0,
    }


async def test_the_snapshot_records_the_package_state_the_human_saw(db, tenant_a):
    """3B-2 §4.4: qui decideix veu si el paquet documental estava incomplet."""
    from tests.test_tender_analysis_package import _gap, _global_run

    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company, analysed=False)
    await _global_run(db, tender)
    await _gap(db, tender, "DOC-04", ["sites"])
    await _gap(db, tender, "DOC-05", ["award_criteria"], resolved=True)
    await db.commit()
    workspace = await open_workspace(db, company.id, tender, user.id)

    decision = await go(db, workspace, user, reason="Ho decidim amb el paquet incomplet.")

    snapshot = parse_snapshot(decision.analysis_snapshot_json)
    assert (snapshot.package_status, snapshot.open_gap_count) == ("incomplete", 1)


async def test_a_lot_workspace_uses_the_tender_run(db, tenant_a):
    user, company = tenant_a
    tender, _rows, run = await v3_case(db, company)
    lot = TenderLot(tender_id=tender.id, external_lot_id="1", lot_number=1, title="Lot 1")
    db.add(lot)
    await db.commit()
    run = await recalculate(db, company.id, tender.id)
    workspace = await open_workspace(db, company.id, tender, user.id, tender_lot_id=lot.id)

    state = await service.load_state(db, workspace, with_analysis=True)
    assert state.analysis.run is not None and state.analysis.run.id == run.id
    decision = await go(db, workspace, user)

    assert decision.analysis_run_id == run.id
    assert decision.analysis_snapshot_json["state"] == "current"


# --------------------------------------------------------------------------- #
# Fotografia immutable i coherent (§10.11, §10.11d, §10.12)                      #
# --------------------------------------------------------------------------- #


async def test_recalculating_later_leaves_the_decision_intact(db, tenant_a):
    """§10.11: run nou, decisio antiga intacta i apuntant al run antic."""
    user, company = tenant_a
    tender, rows, first = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    decision = await go(db, workspace, user)
    decision_id = decision.id
    saved = dict(decision.analysis_snapshot_json)

    await make_stale(db, rows)
    second = await recalculate(db, company.id, tender.id)

    assert second.id != first.id
    row = (
        await db.execute(
            sa.select(WorkspaceDecision)
            .where(WorkspaceDecision.id == decision_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert row.analysis_run_id == first.id
    assert row.analysis_snapshot_json == saved
    assert await _decisions(db, workspace.id) == 1


async def test_the_snapshot_warnings_are_the_detail_warnings(db, tenant_a, client_a):
    """§10.11d: mateix objecte, mateix ordre, amb diversos avisos alhora."""
    user, company = tenant_a
    tender, rows, _run = await v3_case(db, company, blocker=True)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await make_stale(db, rows)
    tender_row = await db.get(Tender, tender.id)
    tender_row.deadline_at = PAST_DEADLINE
    await db.commit()
    url = _detail_url(company.id, workspace.id)

    detail = (await client_a.get(url)).json()
    assert detail["decision_warnings"] == [
        "analysis_stale",
        "blocker_potential",
        "deadline_passed",
    ]
    assert detail["go_requires_reason"] is True

    decided = await client_a.post(
        f"{url}/decisions", json={"decision": "go", "reason": "Ho assumim."}
    )
    assert decided.status_code == 200, decided.text
    snapshot = decided.json()["latest_decision"]["analysis_snapshot_json"]
    assert snapshot["warnings"] == detail["decision_warnings"]
    assert snapshot["state"] == "stale"


async def test_the_analysis_endpoint_and_the_decision_agree_on_run_and_staleness(
    db, tenant_a, client_a
):
    """§10.12: `GET /analysis/current` i `load_state` conclouen el mateix."""
    user, company = tenant_a
    tender, rows, _run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await db.commit()
    current_url = f"/api/v1/companies/{company.id}/tenders/{tender.id}/analysis/current"

    for _ in ("current", "stale"):
        state = await service.load_state(db, workspace, with_analysis=True)
        body = (await client_a.get(current_url)).json()
        assert (str(state.analysis.run.id), state.analysis.stale) == (body["id"], body["stale"])
        await make_stale(db, rows)

    decision = await go(db, workspace, user, reason="Ho sabem.")
    body = (await client_a.get(current_url)).json()
    assert str(decision.analysis_run_id) == body["id"]
    assert decision.analysis_snapshot_json["state"] == ("stale" if body["stale"] else "current")


# --------------------------------------------------------------------------- #
# Atomicitat (§9, §10.11c)                                                      #
# --------------------------------------------------------------------------- #


async def test_the_api_writes_the_decision_audit_once(db, tenant_a, client_a):
    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await db.commit()

    decided = await client_a.post(
        f"{_detail_url(company.id, workspace.id)}/decisions", json={"decision": "go"}
    )
    assert decided.status_code == 200, decided.text

    entries = (
        await db.scalars(sa.select(AuditLog).where(AuditLog.entity_type == "workspace_decision"))
    ).all()
    assert len(entries) == 1
    assert str(entries[0].entity_id) == decided.json()["latest_decision"]["id"]
    assert entries[0].user_id == user.id and entries[0].company_id == company.id
    assert entries[0].summary == "Decisio go sobre la candidatura"


async def test_the_service_writes_the_audit_inside_its_own_commit(db, tenant_a):
    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    workspace_id = workspace.id

    decision_id = (await go(db, workspace, user)).id
    await db.rollback()  # res pendent: ja s'ha confirmat tot junt

    entry = await db.scalar(
        sa.select(AuditLog).where(
            AuditLog.entity_type == "workspace_decision", AuditLog.entity_id == decision_id
        )
    )
    assert entry is not None
    assert await _decisions(db, workspace_id) == 1


async def _assert_nothing_written(db, workspace_id) -> None:
    assert await _decisions(db, workspace_id) == 0
    assert await _decision_audits(db) == 0
    status = await db.scalar(sa.select(BidWorkspace.status).where(BidWorkspace.id == workspace_id))
    assert status == WorkspaceStatus.ANALYSIS


async def test_a_failing_audit_leaves_nothing_behind(db, tenant_a, monkeypatch):
    """§10.11c: ni decisio, ni canvi d'estat, ni auditoria parcial."""
    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    workspace_id = workspace.id

    async def boom(*_args, **_kwargs):
        raise RuntimeError("audit caigut")

    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError, match="audit caigut"):
        await go(db, workspace, user)

    await _assert_nothing_written(db, workspace_id)


async def test_a_failing_status_change_leaves_nothing_behind(db, tenant_a, monkeypatch):
    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    workspace_id = workspace.id

    def boom(*_args, **_kwargs):
        raise RuntimeError("estat caigut")

    monkeypatch.setattr(service, "set_status", boom)
    with pytest.raises(RuntimeError, match="estat caigut"):
        await go(db, workspace, user)

    await _assert_nothing_written(db, workspace_id)


# --------------------------------------------------------------------------- #
# Cost: les llistes no calculen staleness (§6)                                  #
# --------------------------------------------------------------------------- #


async def test_lists_never_compute_staleness(db, tenant_a, client_a, monkeypatch):
    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company)
    await db.commit()
    calls: list[uuid.UUID] = []
    original = analysis.is_stale

    async def spy(session, run, **kwargs):
        calls.append(run.id)
        return await original(session, run, **kwargs)

    monkeypatch.setattr(analysis, "is_stale", spy)

    created = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/workspace", json={}
    )
    assert created.status_code == 200, created.text
    listed = await client_a.get(f"/api/v1/companies/{company.id}/workspaces")
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert calls == [], "_summary no ha de cridar is_stale"

    workspace_id = created.json()["workspace"]["id"]
    detail = await client_a.get(_detail_url(company.id, workspace_id))
    assert detail.status_code == 200
    assert calls, "el detall si que la calcula"

    workspace = await db.get(BidWorkspace, uuid.UUID(workspace_id))
    calls.clear()
    state = await service.load_state(db, workspace)
    assert calls == [] and state.analysis is None and state.analysis_warnings is None


async def test_the_v3_warnings_are_built_from_the_status_and_the_deadline():
    status = analysis.AnalysisStatus(run=None, stale=False)
    warnings = AnalysisDecisionWarnings.from_status(status, deadline_passed=True)
    assert warnings.codes() == ["analysis_missing", "deadline_passed"]


async def test_a_public_tender_without_anything_reads_as_missing(db, tenant_a, client_a):
    user, company = tenant_a
    tender = await _public_tender(db, title="Plec sintetic buit")
    await db.commit()
    workspace = await open_workspace(db, company.id, tender, user.id)
    await db.commit()

    detail = (await client_a.get(_detail_url(company.id, workspace.id))).json()

    assert detail["decision_warnings"] == ["analysis_missing"]
    assert detail["decision_warning_basis"] == "analysis_v3"
    assert detail["go_requires_reason"] is True
    assert "missing_compliance" in detail["open_warnings"]


# --------------------------------------------------------------------------- #
# NULL de SQL, no JSON null (spec 3); el snapshot es revalida en llegir (spec 4) #
# --------------------------------------------------------------------------- #


async def _sql_nulls(db, decision_id) -> tuple[bool, bool]:
    """(open_warnings IS NULL, analysis_snapshot_json IS NULL) a nivell SQL."""
    row = (
        await db.execute(
            sa.select(
                WorkspaceDecision.open_warnings.is_(None),
                WorkspaceDecision.analysis_snapshot_json.is_(None),
            ).where(WorkspaceDecision.id == decision_id)
        )
    ).one()
    return bool(row[0]), bool(row[1])


async def test_a_new_v3_decision_stores_sql_null_in_open_warnings(db, tenant_a):
    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)

    decision_id = (await go(db, workspace, user)).id

    assert await _sql_nulls(db, decision_id) == (True, False)
    assert (
        decision_id
        in (
            await db.scalars(
                sa.select(WorkspaceDecision.id).where(WorkspaceDecision.open_warnings.is_(None))
            )
        ).all()
    )


async def test_a_legacy_row_stores_sql_null_in_both_columns(db, tenant_a):
    from tests.test_bid_workspace import legacy_decision

    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company, analysed=False)
    workspace = await open_workspace(db, company.id, tender, user.id)

    decision = await legacy_decision(db, workspace, user, open_warnings=None)

    assert await _sql_nulls(db, decision.id) == (True, True)
    assert (
        decision.id
        in (
            await db.scalars(
                sa.select(WorkspaceDecision.id).where(
                    WorkspaceDecision.open_warnings.is_(None),
                    WorkspaceDecision.analysis_snapshot_json.is_(None),
                )
            )
        ).all()
    )


async def test_reading_a_decision_revalidates_its_snapshot(db, tenant_a):
    from pydantic import ValidationError

    user, company = tenant_a
    tender, _rows, _run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    decision = await go(db, workspace, user)
    stored = dict(decision.analysis_snapshot_json)

    # Una fila valida fa el viatge sense canviar.
    assert DecisionRead.model_validate(decision).analysis_snapshot_json == stored

    # Una fila corrompuda (clau fora del contracte) falla en veu alta.
    corrupted = WorkspaceDecision(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        decision=WorkspaceDecisionType.DEFER,
        decided_by=user.id,
        decided_at=datetime.now(UTC),
        analysis_snapshot_json={**stored, "recommendation": "go"},
        confirmed_blockers=0,
        potential_blockers=0,
        mandatory_unknown=0,
        mandatory_needs_review=0,
        deadline_passed=False,
    )
    db.add(corrupted)
    await db.commit()
    await db.refresh(corrupted)
    with pytest.raises(ValidationError):
        DecisionRead.model_validate(corrupted)


@pytest.mark.postgres
async def test_deleting_the_analysis_run_leaves_the_decision_readable(db, tenant_a):
    """ON DELETE SET NULL: la decisio perd l'enllac al run pero no la fotografia."""
    user, company = tenant_a
    tender, _rows, run = await v3_case(db, company)
    workspace = await open_workspace(db, company.id, tender, user.id)
    decision = await go(db, workspace, user)
    decision_id = decision.id
    run_id = run.id
    assert decision.analysis_run_id == run_id
    stored = dict(decision.analysis_snapshot_json)
    await db.commit()

    await db.execute(sa.delete(TenderAnalysisRun).where(TenderAnalysisRun.id == run_id))
    await db.commit()

    row = (
        await db.execute(
            sa.select(WorkspaceDecision)
            .where(WorkspaceDecision.id == decision_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert row.analysis_run_id is None
    assert row.analysis_snapshot_json == stored
    read = DecisionRead.model_validate(row)
    assert read.analysis_run_id is None
    assert read.analysis_snapshot_json == stored
