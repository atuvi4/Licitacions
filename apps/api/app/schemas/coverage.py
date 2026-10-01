"""Cobertura territorial d'una dada de coneixement, tal com entra i surt de l'API.

Opcional a totes les altes i modificacions: si no s'envia, la dada es comporta
com abans de la 0047 (`unknown`, cap area). A la lectura hi es sempre.

Les arees son un conjunt: l'esquema normalitza cada area (espais del nom, codis
buits com a `null`) i **desduplica deterministicament** les que tenen la
mateixa identitat canonica, conservant la primera, igual que
`scope_service_lines` d'una certificacio es desduplica en lloc de refusar-se.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.knowledge.coverage import AreaIn, dedupe_areas, normalise_label
from app.models.enums import AreaKind, CoverageCompleteness
from app.schemas.common import APIModel


class CoverageAreaIn(APIModel):
    kind: AreaKind
    #: Es normalitza (espais) al `CoverageIn`; un nom nomes d'espais no passa.
    label: str = Field(min_length=1, max_length=200, pattern=r"\S")
    code: str | None = Field(default=None, max_length=40)
    code_system: str | None = Field(default=None, max_length=20)


class CoverageIn(APIModel):
    """Enviar-la substitueix tota la cobertura de la dada (completesa i arees)."""

    completeness: CoverageCompleteness
    areas: list[CoverageAreaIn] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _areas_are_a_set(self):
        unique = dedupe_areas(
            tuple(
                AreaIn(
                    kind=area.kind,
                    label=normalise_label(area.label),
                    code=area.code or None,
                    code_system=area.code_system or None,
                )
                for area in self.areas
            )
        )
        self.areas = [
            CoverageAreaIn(kind=a.kind, label=a.label, code=a.code, code_system=a.code_system)
            for a in unique
        ]
        return self


class CoverageAreaRead(BaseModel):
    kind: AreaKind
    label: str
    code: str | None = None
    code_system: str | None = None


class CoverageRead(BaseModel):
    completeness: CoverageCompleteness
    areas: list[CoverageAreaRead]
