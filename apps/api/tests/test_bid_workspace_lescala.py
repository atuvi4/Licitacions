"""Acceptacio de L'Escala per a les decisions v3 (F-4b, spec §5 i §10.13).

El plec public de l'Escala (gold v2 + DOC-04) contra el besso sintetic de
l'empresa: un GO sense motiu es permet, la fotografia desa els vectors reals de
l'analisi i, si el coneixement canvia, la decisio antiga no es toca.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.evaluation import tender_gold
from app.knowledge import vocabulary
from app.models.enums import MembershipRole, WorkspaceDecisionType
from app.models.tenders import Tender
from app.models.workspace import WorkspaceDecision
from app.services import bid_workspace as service
from app.services import tender_analysis as analysis
from app.workspace.analysis_snapshot import parse_snapshot
from app.workspace.decision import AnalysisDecisionWarnings
from tests.fixtures.company_twin import build_company_twin
from tests.test_bid_workspace import open_workspace
from tests.test_company_knowledge_records_api import _link_evidence
from tests.test_tender_gold_lescala import CASE

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def _go(db, workspace, user, *, reason=None):
    return await service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason=reason,
    )


async def _reload(db, decision_id) -> WorkspaceDecision:
    return (
        await db.execute(
            sa.select(WorkspaceDecision)
            .where(WorkspaceDecision.id == decision_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def test_lescala_go_without_reason_snapshot_and_immutability_after_stale(
    db, tenant_a, client_a
):
    user, company = tenant_a
    loaded = await tender_gold.load_tender_gold(db, tender_gold.load_dataset(CASE))
    tender_id = loaded.tender.id
    rows = await build_company_twin(db, company)
    await db.commit()  # l'API d'evidencia va en una altra transaccio
    await _link_evidence(client_a, company, "company_certification", rows["certification"].id)
    q = rows["qualification"]
    for entity_type, entity_id in (
        ("company_financial_year", q["years"][max(q["years"])].id),
        ("company_project", q["projects"]["network"].id),
        ("company_insurance", q["insurance"].id),
    ):
        await _link_evidence(client_a, company, entity_type, entity_id)
    await db.commit()

    # --- Sense analisi: avis analysis_missing i un GO sense motiu es rebutja (spec §5).
    tender = await db.get(Tender, tender_id)
    workspace = await open_workspace(db, company.id, tender, user.id)
    await db.commit()
    state = await service.load_state(db, workspace, with_analysis=True)
    assert state.analysis_warnings is not None
    assert state.analysis_warnings.codes() == ["analysis_missing"]
    with pytest.raises(service.DecisionReasonRequiredError) as missing:
        await _go(db, workspace, user)
    assert missing.value.details == {"warnings": ["analysis_missing"], "basis": "analysis_v3"}

    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender_id)).run
    await db.commit()
    await db.refresh(run)
    run_id = run.id

    # --- Analisi vigent, no caducada, amb els vectors del gold.
    status = await analysis.current_analysis(db, company.id, tender_id)
    assert status.run is not None and status.run.id == run_id and status.stale is False
    c1 = next(a for a in run.criterion_assessments if a.lost_points)
    assert c1.lost_points == 5.0
    summary = analysis.summary_of(run)
    assert summary["fit"] == {
        "satisfies": 4,
        "does_not_satisfy": 1,
        "unknown": 19,
        "not_applicable": 10,
    }
    assert (
        summary["secured_points"],
        summary["at_risk_points"],
        summary["lost_points"],
        summary["not_assessable_points"],
        summary["max_points"],
    ) == (0.0, 27.0, 5.0, 68.0, 100.0)
    assert summary["blocker_potential_count"] == 0
    assert tender.deadline_at is None

    # --- Avisos i API: cap avis, el GO no demana motiu, i les dues vies coincideixen.
    state = await service.load_state(db, workspace, with_analysis=True)
    assert state.analysis_warnings is not None and state.analysis_warnings.codes() == []
    # Termini passat (sense tocar tenders.deadline_at: forma part de l'empremta).
    assert AnalysisDecisionWarnings.from_status(state.analysis, deadline_passed=True).codes() == [
        "deadline_passed"
    ]
    detail_url = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"
    detail_response = await client_a.get(detail_url)
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["deadline_passed"] is False
    assert detail["decision_warnings"] == []
    assert detail["go_requires_reason"] is False
    current_url = f"/api/v1/companies/{company.id}/tenders/{tender_id}/analysis/current"
    response = await client_a.get(current_url)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (str(state.analysis.run.id), state.analysis.stale) == (str(run_id), False)
    assert (body["id"], body["stale"]) == (str(run_id), False)

    # --- GO sense motiu: permes, amb la fotografia v1 de l'analisi.
    decision = await _go(db, workspace, user)
    decision_id = decision.id
    decision = await _reload(db, decision_id)
    assert decision.reason is None
    assert decision.analysis_run_id == run_id
    snap = decision.analysis_snapshot_json
    assert parse_snapshot(snap).to_json_dict() == snap
    assert snap["state"] == "current"
    assert snap["fit"] == {
        "satisfies": 4,
        "does_not_satisfy": 1,
        "unknown": 19,
        "not_applicable": 10,
    }
    assert snap["points"] == {
        "secured": 0.0,
        "at_risk": 27.0,
        "lost": 5.0,
        "not_assessable": 68.0,
        "max": 100.0,
    }
    assert snap["blocker_potential_count"] == 0
    assert snap["findings_count"] == len(run.findings)
    assert snap["warnings"] == []
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
    saved = dict(snap)

    # --- Canvi de coneixement: l'analisi queda caducada i totes dues vies ho diuen.
    rows["facts"]["own_ftth_infrastructure"].value = False
    await db.commit()
    state = await service.load_state(db, workspace, with_analysis=True)
    response = await client_a.get(current_url)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (str(state.analysis.run.id), state.analysis.stale) == (str(run_id), True)
    assert (body["id"], body["stale"]) == (str(run_id), True)
    count_before = await db.scalar(
        sa.select(sa.func.count())
        .select_from(WorkspaceDecision)
        .where(WorkspaceDecision.workspace_id == workspace.id)
    )
    status_before = workspace.status
    with pytest.raises(service.DecisionReasonRequiredError) as caught:
        await _go(db, workspace, user)
    count_after = await db.scalar(
        sa.select(sa.func.count())
        .select_from(WorkspaceDecision)
        .where(WorkspaceDecision.workspace_id == workspace.id)
    )
    assert count_after == count_before == 1
    assert workspace.status == status_before
    assert caught.value.details == {"warnings": ["analysis_stale"], "basis": "analysis_v3"}

    # --- Recalcular: run nou vigent; la decisio antiga continua byte a byte igual.
    new_run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender_id)).run
    await db.commit()
    assert new_run.id != run_id
    status = await analysis.current_analysis(db, company.id, tender_id)
    assert status.run.id == new_run.id and status.stale is False
    old = await _reload(db, decision_id)
    assert old.analysis_run_id == run_id
    assert old.analysis_snapshot_json == saved
