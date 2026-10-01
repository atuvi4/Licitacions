"""Punts d'un criteri en quatre cubells: assegurats, en risc, perduts, no avaluables.

`secured + at_risk + lost + not_assessable = max_points`, amb tolerancia de Float.
Un criteri per trams es reparteix per increments entre trams: el que es demostra
fins a un tram queda assegurat (o en risc), el que una dada actual demostra que no
s'assoleix queda perdut, i el que no se sap queda sense avaluar. Mai s'inventa un
unic estat fals per a tot el criteri.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.models.enums import PointsStatus

TOLERANCE = 1e-6

BUCKETS = ("secured", "at_risk", "lost", "not_assessable")


@dataclass(frozen=True, slots=True)
class Buckets:
    secured: float = 0.0
    at_risk: float = 0.0
    lost: float = 0.0
    not_assessable: float = 0.0

    @property
    def total(self) -> float:
        return self.secured + self.at_risk + self.lost + self.not_assessable

    def add(self, bucket: str, points: float) -> Buckets:
        values = {b: getattr(self, b) for b in BUCKETS}
        values[bucket] = values[bucket] + points
        return Buckets(**values)

    def as_dict(self) -> dict[str, float]:
        return {b: round(getattr(self, b), 6) for b in BUCKETS}


def all_in(bucket: str, max_points: float) -> Buckets:
    return Buckets().add(bucket, max_points)


def points_status(buckets: Buckets, max_points: float) -> str:
    """`secured` / `at_risk` / `lost` / `not_assessable` si tot hi es; `mixed` si no."""
    assert abs(buckets.total - max_points) <= TOLERANCE, (buckets, max_points)
    present = [b for b in BUCKETS if getattr(buckets, b) > TOLERANCE]
    if max_points <= TOLERANCE or not present:
        return PointsStatus.NOT_ASSESSABLE.value
    if len(present) == 1:
        return PointsStatus(present[0]).value
    return PointsStatus.MIXED.value


def tranche_increments(
    max_points: float, tranches: Sequence[dict[str, Any]]
) -> list[tuple[Any, float]]:
    """`[(llindar, increment)]` en ordre creixent de punts; el darrer increment
    tanca exactament a `max_points`."""
    ordered = sorted(
        (dict(t) for t in tranches if t.get("points") is not None),
        key=lambda t: float(t["points"]),
    )
    out: list[tuple[Any, float]] = []
    previous = 0.0
    for tranche in ordered:
        points = float(tranche["points"])
        threshold = tranche.get("min", tranche.get("threshold"))
        out.append((threshold, max(points - previous, 0.0)))
        previous = points
    if out and abs(previous - max_points) > TOLERANCE:
        threshold, increment = out[-1]
        out[-1] = (threshold, increment + (max_points - previous))
    return out


def allocate_tranches(
    max_points: float,
    tranches: Sequence[dict[str, Any]],
    outcome_for_threshold,
) -> Buckets:
    """Cada increment va al cubell que `outcome_for_threshold(llindar)` diu."""
    buckets = Buckets()
    for threshold, increment in tranche_increments(max_points, tranches):
        bucket = outcome_for_threshold(threshold)
        assert bucket in BUCKETS, bucket
        buckets = buckets.add(bucket, increment)
    if abs(buckets.total - max_points) > TOLERANCE:
        buckets = buckets.add("not_assessable", max_points - buckets.total)
    return buckets
