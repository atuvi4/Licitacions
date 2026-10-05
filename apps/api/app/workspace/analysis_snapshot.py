"""Fotografia v1 de l'analisi vigent en el moment de decidir.

Schema tancat i versionat: nomes fets de l'analisi (comptadors i punts). Cap
puntuacio global, recomanacio, GO ni bloqueig confirmat.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator, model_validator

from app.services.tender_analysis import AnalysisStatus, summary_of
from app.workspace.decision import AnalysisDecisionWarnings

WarningCode = Literal["analysis_missing", "analysis_stale", "blocker_potential", "deadline_passed"]
_WARNING_ORDER = ("analysis_missing", "analysis_stale", "blocker_potential", "deadline_passed")


class FitCountsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    satisfies: int
    does_not_satisfy: int
    unknown: int
    not_applicable: int


class PointsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secured: float
    at_risk: float
    lost: float
    not_assessable: float
    max: float


class AnalysisDecisionSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    state: Literal["current", "stale", "absent"]
    rules_version: str | None = None
    run_created_at: datetime | None = None
    fit: FitCountsV1 | None = None
    points: PointsV1 | None = None
    blocker_potential_count: int | None = None
    findings_count: int | None = None
    warnings: list[WarningCode]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _no_bool_version(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("schema_version must be an int")
        return value

    @field_serializer("run_created_at")
    def _ser_created_at(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    def to_json_dict(self) -> dict[str, Any]:
        """Forma de persistencia: JSON pur, sense claus nul.les."""
        return self.model_dump(mode="json", exclude_none=True)

    @model_validator(mode="after")
    def _check_run_fields(self) -> AnalysisDecisionSnapshotV1:
        idx = [_WARNING_ORDER.index(w) for w in self.warnings]
        if idx != sorted(set(idx)):
            raise ValueError("warnings must be unique and in canonical order")
        fields = (
            self.rules_version,
            self.run_created_at,
            self.fit,
            self.points,
            self.blocker_potential_count,
            self.findings_count,
        )
        if self.state == "absent":
            if any(f is not None for f in fields):
                raise ValueError("absent snapshot must not carry run fields")
        elif any(f is None for f in fields):
            raise ValueError("current/stale snapshot requires all run fields")
        return self

    @model_validator(mode="after")
    def _check_state_warning_implications(self) -> AnalysisDecisionSnapshotV1:
        if (self.state == "absent") != ("analysis_missing" in self.warnings):
            raise ValueError("analysis_missing requires state absent and vice versa")
        if (self.state == "stale") != ("analysis_stale" in self.warnings):
            raise ValueError("analysis_stale requires state stale and vice versa")
        if ((self.blocker_potential_count or 0) > 0) != ("blocker_potential" in self.warnings):
            raise ValueError("blocker_potential must match blocker_potential_count > 0")
        return self


def build_snapshot(
    status: AnalysisStatus, warnings: AnalysisDecisionWarnings
) -> AnalysisDecisionSnapshotV1:
    run = status.run
    if run is None:
        return AnalysisDecisionSnapshotV1(
            schema_version=1, state="absent", warnings=warnings.codes()
        )
    summary = summary_of(run)
    return AnalysisDecisionSnapshotV1(
        schema_version=1,
        state="stale" if status.stale else "current",
        rules_version=run.rules_version,
        run_created_at=run.created_at,
        fit=FitCountsV1(**summary["fit"]),
        points=PointsV1(
            secured=summary["secured_points"],
            at_risk=summary["at_risk_points"],
            lost=summary["lost_points"],
            not_assessable=summary["not_assessable_points"],
            max=summary["max_points"],
        ),
        blocker_potential_count=summary["blocker_potential_count"],
        findings_count=len(run.findings),
        warnings=warnings.codes(),
    )


def parse_snapshot(data: dict[str, Any]) -> AnalysisDecisionSnapshotV1:
    return AnalysisDecisionSnapshotV1.model_validate(data)
