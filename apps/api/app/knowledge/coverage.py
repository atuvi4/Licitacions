"""Cobertura territorial d'una dada de coneixement: on es certa i si la llista es tota.

Pur: cap sessio ni xarxa. Aqui hi ha la forma de la dada i la seva representacio
canonica; comparar-la amb el territori d'un plec es cosa del comparador
territorial (fase posterior) i no es fa aqui.

**Estat neutre.** `completeness = unknown` sense cap area es exactament el que
tota dada tenia abans de la migracio 0047: no afirma res. Per aixo
`canonical_coverage` en retorna `None` i la fotografia l'omet de l'empremta,
de manera que migrar no canvia cap fingerprint. Qualsevol altre estat afirma
alguna cosa (fins i tot `complete` sense arees: «no cobrim enlloc») i hi entra.

**Representacio canonica** (la que fa l'empremta i l'ordre de lectura):
`completeness` i, per area, `kind`, `label` amb els espais normalitzats, `code`
i `code_system`; arees ordenades per `(kind, code_system, code, label)`. Un
canvi de rètol o de codi es, per tant, un canvi material.

**Les arees son un conjunt.** La identitat d'una area es `(kind, code_system,
code, label normalitzat)`: dues arees amb la mateixa identitat son la mateixa
area, i la representacio canonica nomes en conserva una (la primera). Un
duplicat no afegeix res, no canvia l'empremta i no es cap canvi material. No
hi ha cap resolucio geografica: "Banyoles" i "17015/INE" son dues identitats.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.enums import AreaKind, CoverageCompleteness


@dataclass(frozen=True, slots=True)
class AreaIn:
    kind: AreaKind
    label: str
    code: str | None = None
    code_system: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageIn:
    completeness: CoverageCompleteness = CoverageCompleteness.UNKNOWN
    areas: tuple[AreaIn, ...] = ()

    @property
    def is_neutral(self) -> bool:
        return self.completeness == CoverageCompleteness.UNKNOWN and not self.areas


NEUTRAL_COVERAGE = CoverageIn()


def normalise_label(label: str) -> str:
    """Espais normalitzats; majuscules i accents es conserven (son part del nom)."""
    return " ".join(label.split())


def area_dict(area: AreaIn) -> dict[str, Any]:
    return {
        "kind": str(area.kind),
        "label": normalise_label(area.label),
        "code": area.code,
        "code_system": area.code_system,
    }


def area_sort_key(area: dict[str, Any]) -> tuple[str, str, str, str]:
    return (area["kind"], area["code_system"] or "", area["code"] or "", area["label"])


def area_identity(area: AreaIn) -> tuple[str, str, str, str]:
    """El que fa que dues arees siguin la mateixa: tipus, sistema de codis, codi i
    nom amb els espais normalitzats (majuscules i accents compten)."""
    return area_sort_key(area_dict(area))


def dedupe_areas(areas: tuple[AreaIn, ...]) -> tuple[AreaIn, ...]:
    """Les arees sense repeticions, en l'ordre d'arribada (la primera es queda)."""
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[AreaIn] = []
    for area in areas:
        identity = area_identity(area)
        if identity not in seen:
            seen.add(identity)
            unique.append(area)
    return tuple(unique)


def sorted_areas(coverage: CoverageIn) -> list[dict[str, Any]]:
    return sorted((area_dict(area) for area in dedupe_areas(coverage.areas)), key=area_sort_key)


def coverage_dict(coverage: CoverageIn) -> dict[str, Any]:
    """La cobertura tal com es llegeix per l'API: sempre present, tambe la neutra."""
    return {"completeness": str(coverage.completeness), "areas": sorted_areas(coverage)}


def canonical_coverage(coverage: CoverageIn) -> dict[str, Any] | None:
    """El que entra a l'empremta, o `None` si es l'estat neutre."""
    if coverage.is_neutral:
        return None
    return coverage_dict(coverage)
