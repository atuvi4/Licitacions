"""Snapshot v1 i avisos v3: purs, sense BD."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.enums import WorkspaceDecisionType
from app.services.tender_analysis import AnalysisStatus, summary_of
from app.workspace.analysis_snapshot import (
    AnalysisDecisionSnapshotV1,
    build_snapshot,
    parse_snapshot,
)
from app.workspace.decision import (
    AnalysisDecisionWarnings,
    DecisionWarnings,
    requires_reason,
)

W = AnalysisDecisionWarnings
ABSENT_KEYS = {"schema_version", "state", "warnings"}
RUN_KEYS = {
    "schema_version",
    "state",
    "rules_version",
    "run_created_at",
    "fit",
    "points",
    "blocker_potential_count",
    "findings_count",
    "warnings",
}


def fake_run():
    fits = [SimpleNamespace(fit_result=r) for r in ("satisfies", "satisfies", "unknown")]
    crits = [
        SimpleNamespace(
            secured_points=1.5,
            at_risk_points=2.0,
            lost_points=0.0,
            not_assessable_points=0.5,
            max_points=4.0,
        )
    ]
    findings = [
        SimpleNamespace(finding_type="blocker_potential", explanation_code="FACT_CONTRADICTS"),
        SimpleNamespace(finding_type="review_required", explanation_code="FACT_TRUE_DECLARED"),
        SimpleNamespace(finding_type="blocker_potential", explanation_code="FACT_CONTRADICTS"),
    ]
    return SimpleNamespace(
        fit_assessments=fits,
        criterion_assessments=crits,
        findings=findings,
        delivery_candidates=[],
        questions=[],
        rules_version="v3.1",
        created_at=datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
    )


def test_codes_fixed_order():
    assert W().codes() == []
    all_four = W(
        deadline_passed=True, blocker_potential=True, analysis_stale=True, analysis_missing=True
    )
    assert all_four.codes() == [
        "analysis_missing",
        "analysis_stale",
        "blocker_potential",
        "deadline_passed",
    ]
    three = W(deadline_passed=True, analysis_stale=True, blocker_potential=True)
    assert three.codes() == ["analysis_stale", "blocker_potential", "deadline_passed"]


def test_any_open():
    assert not W().any_open
    assert W(blocker_potential=True).any_open


def test_from_status():
    w = W.from_status(AnalysisStatus(run=None, stale=False), deadline_passed=False)
    assert w.codes() == ["analysis_missing"]
    w = W.from_status(AnalysisStatus(run=fake_run(), stale=True), deadline_passed=True)
    assert w.codes() == ["analysis_stale", "blocker_potential", "deadline_passed"]


@pytest.mark.parametrize(
    "warnings", [W(analysis_missing=True), DecisionWarnings(confirmed_blockers=1)]
)
def test_requires_reason_both_families(warnings):
    assert requires_reason(WorkspaceDecisionType.GO, warnings) is True
    assert requires_reason(WorkspaceDecisionType.NO_GO, warnings) is False
    assert requires_reason(WorkspaceDecisionType.DEFER, warnings) is False


def test_requires_reason_clean_go():
    assert requires_reason(WorkspaceDecisionType.GO, W()) is False


@pytest.mark.parametrize("stale,state", [(False, "current"), (True, "stale")])
def test_snapshot_with_run(stale, state):
    run = fake_run()
    w = W(analysis_stale=stale, blocker_potential=True)
    snap = build_snapshot(AnalysisStatus(run=run, stale=stale), w)
    dumped = snap.to_json_dict()
    assert set(dumped) == RUN_KEYS
    json.dumps(snap.model_dump())
    assert dumped["state"] == state
    summary = summary_of(run)
    assert dumped["fit"] == summary["fit"]
    assert set(dumped["points"]) == {"secured", "at_risk", "lost", "not_assessable", "max"}
    assert dumped["points"]["secured"] == summary["secured_points"]
    assert dumped["blocker_potential_count"] == summary["blocker_potential_count"] == 2
    assert dumped["findings_count"] == len(run.findings) == 3
    assert dumped["rules_version"] == "v3.1"
    assert dumped["warnings"] == w.codes()
    assert isinstance(dumped["run_created_at"], str)
    again = parse_snapshot(json.loads(json.dumps(dumped)))
    assert again.to_json_dict() == dumped


def test_snapshot_absent():
    w = W(analysis_missing=True)
    snap = build_snapshot(AnalysisStatus(run=None, stale=False), w)
    dumped = snap.to_json_dict()
    json.dumps(snap.model_dump())
    assert set(dumped) == ABSENT_KEYS
    assert dumped["state"] == "absent"
    assert dumped["warnings"] == ["analysis_missing"]
    assert parse_snapshot(json.loads(json.dumps(dumped))).to_json_dict() == dumped


@pytest.mark.parametrize("with_run", [False, True])
def test_snapshot_carries_the_package_state(with_run):
    """3B-2 §4.4: l'estat del paquet i el nombre de forats oberts, amb o sense run.
    Una fotografia antiga (sense aquestes claus) continua sent valida."""
    from app.analysis.fingerprint import PackageGapView
    from app.services.tender_analysis import PackageState

    gaps = (PackageGapView(code="referenced_document_missing", detail={"key": "DOC-04"}),)
    package = PackageState(status="incomplete", gaps=gaps, has_run=True)
    if with_run:
        status = AnalysisStatus(run=fake_run(), stale=False)
        w = W(blocker_potential=True)
    else:
        status = AnalysisStatus(run=None, stale=False)
        w = W(analysis_missing=True)
    dumped = build_snapshot(status, w, package).to_json_dict()
    assert (dumped["package_status"], dumped["open_gap_count"]) == ("incomplete", 1)
    assert set(dumped) == (RUN_KEYS if with_run else ABSENT_KEYS) | {
        "package_status",
        "open_gap_count",
    }
    assert parse_snapshot(json.loads(json.dumps(dumped))).to_json_dict() == dumped
    with pytest.raises(ValidationError):
        parse_snapshot({**dumped, "package_status": "partial"})
    with pytest.raises(ValidationError):
        parse_snapshot({**dumped, "open_gap_count": -1})


@pytest.mark.parametrize("key", ["score", "recommendation", "go", "blocker_confirmed", "foo"])
def test_schema_rejects_extra_keys(key):
    base = build_snapshot(AnalysisStatus(run=None, stale=False), W(analysis_missing=True))
    data = base.to_json_dict()
    data[key] = 1
    with pytest.raises(ValidationError):
        parse_snapshot(data)


def test_schema_rejects_extra_in_submodels():
    status = AnalysisStatus(run=fake_run(), stale=False)
    data = build_snapshot(status, W(blocker_potential=True)).to_json_dict()
    data["fit"]["x"] = 1
    with pytest.raises(ValidationError):
        parse_snapshot(data)


def test_schema_invariants():
    with pytest.raises(ValidationError):
        AnalysisDecisionSnapshotV1(
            schema_version=1, state="absent", rules_version="v1", warnings=[]
        )
    with pytest.raises(ValidationError):
        AnalysisDecisionSnapshotV1(schema_version=1, state="current", warnings=[])
    with pytest.raises(ValidationError):
        AnalysisDecisionSnapshotV1(schema_version=2, state="absent", warnings=[])


@pytest.mark.parametrize(
    "bad", [["bogus"], ["deadline_passed", "analysis_missing"], ["analysis_stale"] * 2]
)
def test_warnings_vocabulary_and_order(bad):
    with pytest.raises(ValidationError):
        AnalysisDecisionSnapshotV1(schema_version=1, state="absent", warnings=bad)


def test_schema_version_strict():
    with pytest.raises(ValidationError):
        AnalysisDecisionSnapshotV1(schema_version=True, state="absent", warnings=[])


def _run_data(state, warnings, blockers=0):
    status = AnalysisStatus(run=fake_run(), stale=False)
    data = build_snapshot(status, W(blocker_potential=True)).to_json_dict()
    data["state"] = state
    data["warnings"] = warnings
    data["blocker_potential_count"] = blockers
    return data


@pytest.mark.parametrize(
    "data",
    [
        {"schema_version": 1, "state": "absent", "warnings": []},
        {"schema_version": 1, "state": "absent", "warnings": ["analysis_stale"]},
        _run_data("current", ["analysis_missing"]),
        _run_data("current", ["analysis_stale"]),
        _run_data("stale", []),
        _run_data("stale", ["blocker_potential"], blockers=1),
        _run_data("current", ["blocker_potential"], blockers=0),
        _run_data("current", [], blockers=2),
    ],
    ids=[
        "absent-sense-missing",
        "absent-amb-stale",
        "current-amb-missing",
        "current-amb-stale",
        "stale-sense-stale",
        "stale-sense-stale-amb-blocker",
        "blocker-avis-sense-comptador",
        "comptador-sense-avis",
    ],
)
def test_schema_state_warning_implications(data):
    with pytest.raises(ValidationError):
        parse_snapshot(data)


def test_schema_consistent_combinations_valid():
    parse_snapshot({"schema_version": 1, "state": "absent", "warnings": ["analysis_missing"]})
    parse_snapshot(_run_data("current", ["blocker_potential"], blockers=2))
    parse_snapshot(_run_data("stale", ["analysis_stale", "blocker_potential"], blockers=2))
