"""Contracte de sortida del model, versio 2.

La v1 (`app.ai.schemas`) queda intacta: els runs antics s'han de poder tornar a
llegir amb el contracte amb que es van fer. Aquesta versio afegeix el que la
revisio humana del gold va demostrar que faltava:

* **modalitat** separada de l'obligatorietat, per poder dir "alternativa" o
  "condicional" sense convertir-ho en una exigencia;
* **grup d'alternatives**, perque "A o B" no generi dos blocadors independents;
* **equivalencia explicita**, perque "ISO 9001 o equivalent" no es llegeixi com
  "exactament ISO 9001";
* **valor tipat** (`app.ai.value_schemas`), que distingeix agregacions, limits
  d'assegurança i percentatges amb la seva base;
* **formula i variables** dels criteris d'adjudicacio, conservades tal com estan.

El que NO canvia: el model segueix sense poder inventar categories, ni offsets,
ni decidir equivalencies. Aixo son decisions de compliment o d'una persona.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.schemas import DocumentNoteOut, EvidenceOut, UncertaintyOut
from app.models.enums import (
    AwardCriterionType,
    AwardEvaluationType,
    ExtractionConfidence,
    RequirementCategory,
    RequirementModality,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
)

#: Versio del contracte. Es desa a cada Run i forma part de la clau de cache.
SCHEMA_VERSION_V2 = "requirements-schema-v2"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequirementOutV2(StrictModel):
    category: RequirementCategory
    #: Obligatorietat "classica". Es conserva perque 4C i el gold v1 la llegeixen.
    obligation: RequirementObligation = RequirementObligation.UNKNOWN
    #: El matis. `alternative` i `conditional` NO deriven mai en `mandatory`.
    modality: RequirementModality = RequirementModality.UNKNOWN
    operator: RequirementOperator | None = None
    scope: RequirementScope = RequirementScope.UNKNOWN
    lot_labels: list[str] = Field(default_factory=list)

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    #: Valor tipat segons `app.ai.value_schemas`. El backend el valida i, si no
    #: encaixa, el conserva com a generic en comptes de descartar-lo.
    value: dict[str, Any] | None = None

    #: Identificador LOCAL del chunk que agrupa alternatives entre elles. El
    #: backend el converteix en una clau estable; el model nomes ha de fer que
    #: dues opcions de la mateixa disjuncio comparteixin cadena.
    alternative_group: str | None = Field(default=None, max_length=80)
    #: "o equivalent", "o similar", "u otro de analogas caracteristicas".
    equivalence_allowed: bool = False
    #: Condicio que activa el requisit ("en su caso", "si el licitador es UTE").
    condition_note: str | None = Field(default=None, max_length=500)

    confidence: ExtractionConfidence = ExtractionConfidence.LOW
    evidence: list[EvidenceOut] = Field(default_factory=list)

    @field_validator("lot_labels")
    @classmethod
    def _cap_lot_labels(cls, value: list[str]) -> list[str]:
        return value[:20]


class FormulaVariableOut(StrictModel):
    """Que significa cada simbol d'una formula. Copiat, no deduit."""

    symbol: str = Field(min_length=1, max_length=40)
    meaning: str = Field(min_length=1, max_length=300)


class AwardTrancheOut(StrictModel):
    """Un tram de puntuacio quan el criteri no es una formula continua."""

    label: str | None = Field(default=None, max_length=200)
    from_value: float | None = None
    to_value: float | None = None
    points: float | None = None


class AwardCriterionOutV2(StrictModel):
    name: str = Field(min_length=1, max_length=500)
    criterion_type: AwardCriterionType = AwardCriterionType.UNKNOWN
    category: str | None = None
    description: str | None = None
    #: Puntuacio maxima del criteri.
    points: float | None = None
    weight_percent: float | None = None

    #: L'expressio tal com apareix ("P = 75 x (OM / OF)"). NULL si no n'hi ha.
    formula: str | None = Field(default=None, max_length=1000)
    #: La frase sencera que conte la formula, literal. Serveix per entendre-la
    #: quan l'expressio sola no basta, i per poder-la citar.
    formula_text: str | None = Field(default=None, max_length=2000)
    formula_variables: list[FormulaVariableOut] = Field(default_factory=list)
    tranches: list[AwardTrancheOut] = Field(default_factory=list)

    evaluation_type: AwardEvaluationType = AwardEvaluationType.UNKNOWN
    scope: RequirementScope = RequirementScope.UNKNOWN
    lot_labels: list[str] = Field(default_factory=list)
    confidence: ExtractionConfidence = ExtractionConfidence.LOW
    evidence: list[EvidenceOut] = Field(default_factory=list)

    @field_validator("formula_variables")
    @classmethod
    def _cap_variables(cls, value: list[FormulaVariableOut]) -> list[FormulaVariableOut]:
        return value[:20]

    @field_validator("tranches")
    @classmethod
    def _cap_tranches(cls, value: list[AwardTrancheOut]) -> list[AwardTrancheOut]:
        return value[:30]


class ChunkExtractionOutV2(StrictModel):
    """El que el model retorna per a UN chunk, amb el contracte v2."""

    requirements: list[RequirementOutV2] = Field(default_factory=list)
    award_criteria: list[AwardCriterionOutV2] = Field(default_factory=list)
    document_notes: list[DocumentNoteOut] = Field(default_factory=list)
    uncertainties: list[UncertaintyOut] = Field(default_factory=list)


def chunk_output_json_schema_v2() -> dict[str, Any]:
    return ChunkExtractionOutV2.model_json_schema()
