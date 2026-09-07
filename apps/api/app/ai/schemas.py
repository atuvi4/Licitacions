"""Contracte de sortida del model. Pydantic, no markdown ni text lliure.

Aquest fitxer es el limit entre "el que diu el model" i "el que entra al
domini". Tot el que arriba d'un LLM passa per aqui abans de tocar res mes: si
no valida, no continua.

Notes de disseny:

* Els enums son tancats: el model no pot inventar categories ni operadors.
  Pydantic rebutja qualsevol valor fora de la llista, i aixo es exactament el
  que volem que passi.
* `page_number` i `extraction_id` els proposa el model, pero **no s'hi confia**:
  el backend els revalida contra el context que va enviar (vegeu
  `app.services.evidence_validation`).
* No hi ha cap camp d'offset: `char_start`/`char_end` els calcula el backend
  localitzant el quote. Un model no ha d'inventar posicions.
* No hi ha cap camp de compliment, de blocador ni de puntuacio de l'empresa:
  aquesta iteracio nomes respon que exigeix el plec.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    AwardEvaluationType,
    EvidenceRole,
    ExtractionConfidence,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
)

#: Versio del contracte. Canviar-lo de significat obliga a pujar la versio.
SCHEMA_VERSION = "requirements-schema-v1"


class StrictModel(BaseModel):
    """Cap camp inventat: si el model afegeix claus, es un error de contracte."""

    model_config = ConfigDict(extra="forbid")


class EvidenceOut(StrictModel):
    """Una cita. Ha d'apuntar a una pagina del context que se li ha donat."""

    extraction_id: str
    page_number: int = Field(ge=1)
    #: Fragment literal de la pagina. El backend comprova que hi sigui de veritat.
    quote: str = Field(min_length=1)
    section: str | None = None
    evidence_role: EvidenceRole = EvidenceRole.PRIMARY


class RequirementOut(StrictModel):
    category: RequirementCategory
    obligation: RequirementObligation = RequirementObligation.UNKNOWN
    operator: RequirementOperator | None = None
    scope: RequirementScope = RequirementScope.UNKNOWN
    #: Etiquetes de lot tal com apareixen al plec ("Lote 2", "Lots 1 i 3").
    lot_labels: list[str] = Field(default_factory=list)

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    #: Valor estructurat segons la categoria. Lliure a proposit: perseguir una
    #: ontologia perfecta a l'MVP costa mes del que aporta.
    value: dict[str, Any] | None = None

    confidence: ExtractionConfidence = ExtractionConfidence.LOW
    evidence: list[EvidenceOut] = Field(default_factory=list)

    @field_validator("lot_labels")
    @classmethod
    def _cap_lot_labels(cls, value: list[str]) -> list[str]:
        return value[:20]


class AwardCriterionOut(StrictModel):
    name: str = Field(min_length=1, max_length=500)
    category: str | None = None
    description: str | None = None
    points: float | None = None
    weight_percent: float | None = None
    #: NULL si el plec no la dona. No se n'inventa cap.
    formula: str | None = None
    evaluation_type: AwardEvaluationType = AwardEvaluationType.UNKNOWN
    scope: RequirementScope = RequirementScope.UNKNOWN
    lot_labels: list[str] = Field(default_factory=list)
    confidence: ExtractionConfidence = ExtractionConfidence.LOW
    evidence: list[EvidenceOut] = Field(default_factory=list)


class DocumentNoteOut(StrictModel):
    """Observacio sobre el document, no un requisit."""

    note: str
    page_number: int | None = None


class UncertaintyOut(StrictModel):
    """El model no ho ha vist clar. Val mes registrar-ho que endevinar."""

    note: str
    page_number: int | None = None


class ChunkExtractionOut(StrictModel):
    """El que el model retorna per a UN chunk."""

    requirements: list[RequirementOut] = Field(default_factory=list)
    award_criteria: list[AwardCriterionOut] = Field(default_factory=list)
    document_notes: list[DocumentNoteOut] = Field(default_factory=list)
    uncertainties: list[UncertaintyOut] = Field(default_factory=list)


#: JSON Schema que s'envia al proveidor com a contracte de sortida.
def chunk_output_json_schema() -> dict[str, Any]:
    return ChunkExtractionOut.model_json_schema()
