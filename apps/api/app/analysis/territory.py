"""Comparador territorial PUR (0051): territoris d'un requisit contra la cobertura
declarada d'una dada de l'empresa.

Nomes identitat explicita: dues arees son la mateixa si tenen el mateix `kind`
i el mateix codi explicit (mateix sistema), o el mateix `kind` i el mateix nom
normalitzat sense cap codi que ho contradigui (un codi absent no contradiu res).
Cap contencio (un municipi no es dins d'una provincia aqui), cap GIS, cap
geocodificacio: el que no es pot demostrar es `unknown`.

Completesa:

* requisit `not_applicable` → `not_applicable`;
* requisit `unknown` sense territoris → `unknown` (mai cobert per buit);
* requisit `partial` → mai `matched` global amb mode `all`;
* cobertura `complete` i l'area no hi es → `unmatched`; cobertura `partial` o
  `unknown` → `unknown`, no `unmatched`.

Agregacio `all`: alguna `unmatched` → `unmatched`; totes `matched` i requisit
`complete` → `matched`; altrament `unknown`. Agregacio `any`: alguna `matched` →
`matched`; totes `unmatched` amb requisit `complete` i cobertura `complete` →
`unmatched`; altrament `unknown`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import (
    CoverageCompleteness,
    TerritoryCompleteness,
    TerritoryMatchMode,
    TerritoryVerdict,
)


def _norm(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned.casefold() or None


@dataclass(frozen=True, slots=True)
class Area:
    kind: str
    label: str | None = None
    code: str | None = None
    code_system: str | None = None
    qualifier: str | None = None
    #: Territori ancorat a una seu del plec: no te cap area comparable.
    site_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "code": self.code,
            "code_system": self.code_system,
            "qualifier": self.qualifier,
            "site": self.site_key,
        }


@dataclass(frozen=True, slots=True)
class Coverage:
    completeness: str = CoverageCompleteness.UNKNOWN.value
    areas: tuple[Area, ...] = ()


@dataclass(frozen=True, slots=True)
class AreaVerdict:
    area: dict[str, Any]
    verdict: str
    reason: str
    matched: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "verdict": self.verdict,
            "reason": self.reason,
            "matched": self.matched,
        }


@dataclass(frozen=True, slots=True)
class TerritoryComparison:
    verdict: str
    reason: str
    area_verdicts: tuple[AreaVerdict, ...] = field(default_factory=tuple)

    def as_dicts(self) -> list[dict[str, Any]]:
        return [v.as_dict() for v in self.area_verdicts]


def same_area(required: Area, covered: Area) -> bool:
    """Identitat explicita compatible. Mai cap deduccio geografica."""
    if required.kind != covered.kind:
        return False
    if (
        required.code is not None
        and covered.code is not None
        and _norm(required.code_system) == _norm(covered.code_system)
        and _norm(required.code) == _norm(covered.code)
    ):
        return True
    if _norm(required.label) is None or _norm(required.label) != _norm(covered.label):
        return False
    # Mateix nom: els codis no poden contradir-se (un codi absent no contradiu res).
    if required.code is None or covered.code is None:
        return True
    return _norm(required.code_system) == _norm(covered.code_system) and _norm(
        required.code
    ) == _norm(covered.code)


def compare_area(required: Area, coverage: Coverage | None) -> AreaVerdict:
    where = required.as_dict()
    if required.site_key is not None and required.label is None and required.code is None:
        return AreaVerdict(where, TerritoryVerdict.UNKNOWN.value, "site_anchored_not_comparable")
    if coverage is None:
        return AreaVerdict(where, TerritoryVerdict.UNKNOWN.value, "no_coverage_declared")
    hit = next((c for c in coverage.areas if same_area(required, c)), None)
    if hit is not None:
        if _norm(required.qualifier) is not None:
            return AreaVerdict(
                where, TerritoryVerdict.UNKNOWN.value, "qualifier_not_comparable", hit.as_dict()
            )
        return AreaVerdict(where, TerritoryVerdict.MATCHED.value, "identity", hit.as_dict())
    if coverage.completeness == CoverageCompleteness.COMPLETE.value:
        return AreaVerdict(where, TerritoryVerdict.UNMATCHED.value, "not_in_complete_coverage")
    return AreaVerdict(where, TerritoryVerdict.UNKNOWN.value, "coverage_not_complete")


def compare_territories(
    *,
    territories: Sequence[Area],
    completeness: str,
    match_mode: str,
    coverage: Coverage | None,
) -> TerritoryComparison:
    completeness = str(completeness)
    match_mode = str(match_mode)
    if completeness == TerritoryCompleteness.NOT_APPLICABLE.value:
        return TerritoryComparison(TerritoryVerdict.NOT_APPLICABLE.value, "not_applicable")
    if not territories:
        reason = (
            "list_unavailable"
            if completeness == TerritoryCompleteness.UNKNOWN.value
            else "no_territories"
        )
        return TerritoryComparison(TerritoryVerdict.UNKNOWN.value, reason)

    verdicts = tuple(compare_area(area, coverage) for area in territories)
    outcomes = {v.verdict for v in verdicts}
    matched, unmatched = TerritoryVerdict.MATCHED.value, TerritoryVerdict.UNMATCHED.value
    complete = completeness == TerritoryCompleteness.COMPLETE.value
    coverage_complete = (
        coverage is not None and coverage.completeness == CoverageCompleteness.COMPLETE.value
    )

    if match_mode == TerritoryMatchMode.ALL.value:
        if unmatched in outcomes:
            return TerritoryComparison(unmatched, "some_area_unmatched", verdicts)
        if outcomes == {matched} and complete:
            return TerritoryComparison(matched, "all_areas_matched", verdicts)
        reason = "requirement_not_complete" if outcomes == {matched} else "some_area_unknown"
        return TerritoryComparison(TerritoryVerdict.UNKNOWN.value, reason, verdicts)

    if matched in outcomes:
        return TerritoryComparison(matched, "some_area_matched", verdicts)
    if outcomes == {unmatched} and complete and coverage_complete:
        return TerritoryComparison(unmatched, "no_area_matched", verdicts)
    return TerritoryComparison(TerritoryVerdict.UNKNOWN.value, "cannot_conclude", verdicts)
