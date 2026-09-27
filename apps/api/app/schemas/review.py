"""Esquemes de la capa de revisio canonica (S-R1, S-R2).

Tres families, i no es barregen:

* **Instantania interna** (`RequirementSnapshot`): el que es desa a
  `review_items.accepted_json`. Tipada i tancada (`extra="forbid"`) perque la
  forma no depengui de qui l'escrigui.
* **Entrades del revisor** (`ManualRequirementCreate`, `ReviewActionRequest` i
  les carregues de cada accio): validacio estricta. Les unitats son text lliure
  i ACOTAT (`ms`, `trucades`, `usuaris`...): no es reutilitza l'enum tancat de
  l'extraccio v3.2, que no les coneix.
* **Lectures per a la interficie** (`Canonical*`, `ReviewOverview`,
  `ReviewHistoryEntry`): etiquetes en catala i CAP codi tecnic d'extraccio.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    RequirementCategory,
    RequirementModality,
    RequirementObligation,
    RequirementOperator,
    ReviewVerification,
)

#: Caracters de control (tret de salt de linia i tabulador): mai en un text de revisio.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: Clau d'un valor: la mateixa forma que un identificador de claim.
VALUE_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,59}$"
MAX_UNIT_LABEL = 32
MAX_TITLE = 500
MAX_DESCRIPTION = 5000
MAX_NOTE = 2000
MAX_TEXT_VALUE = 500
MAX_VALUES = 50
MAX_CITATIONS = 20
#: Operadors acceptats: els de l'extraccio i els simbols que escriu una persona.
OPERATORS = frozenset(
    {op.value for op in RequirementOperator} | {"<", "<=", ">", ">=", "=", "!="}
)

ScalarValue = bool | int | float | str | None


def _clean_text(value: str | None, *, multiline: bool) -> str | None:
    """Treu espais dels extrems i rebutja caracters de control."""
    if value is None:
        return None
    if _CONTROL.search(value) or (not multiline and ("\n" in value or "\t" in value)):
        raise ValueError("conte caracters de control")
    return value.strip()


def _check_unit(value: str | None) -> str | None:
    cleaned = _clean_text(value, multiline=False)
    return cleaned or None


def _check_operator(value: str | None) -> str | None:
    if value is not None and value not in OPERATORS:
        raise ValueError("operador desconegut")
    return value


def _check_scalar(value: ScalarValue) -> ScalarValue:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("el valor ha de ser un numero finit")
    if isinstance(value, str):
        cleaned = _clean_text(value, multiline=False)
        if cleaned is not None and len(cleaned) > MAX_TEXT_VALUE:
            raise ValueError(f"el text del valor passa de {MAX_TEXT_VALUE} caracters")
        return cleaned
    return value


# --------------------------------------------------------------------------- #
# Instantania interna (`accepted_json`)                                         #
# --------------------------------------------------------------------------- #


class AcceptedValue(BaseModel):
    """Un valor acceptat. `source` diu si surt del document o l'afirma el revisor."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=VALUE_KEY_PATTERN)
    operator: str | None = None
    value: ScalarValue = None
    unit_label: str | None = Field(default=None, max_length=MAX_UNIT_LABEL)
    source: Literal["document", "reviewer"]

    @field_validator("value")
    @classmethod
    def _value(cls, value: ScalarValue) -> ScalarValue:
        return _check_scalar(value)

    @field_validator("unit_label")
    @classmethod
    def _unit(cls, value: str | None) -> str | None:
        return _check_unit(value)

    @field_validator("operator")
    @classmethod
    def _operator(cls, value: str | None) -> str | None:
        return _check_operator(value)


class RequirementSnapshot(BaseModel):
    """El requisit tal com l'accepta l'empresa."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    category: RequirementCategory
    obligation: RequirementObligation
    modality: RequirementModality
    values: list[AcceptedValue] = Field(default_factory=list, max_length=MAX_VALUES)
    needs_review_from_extraction: bool

    @model_validator(mode="after")
    def _unique_keys(self) -> RequirementSnapshot:
        keys = [v.key for v in self.values]
        if len(keys) != len(set(keys)):
            raise ValueError("claus de valor repetides")
        return self


# --------------------------------------------------------------------------- #
# Entrades del revisor                                                          #
# --------------------------------------------------------------------------- #


class ReviewerValueInput(BaseModel):
    """Un valor que escriu el revisor. La font sempre es `reviewer`."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=VALUE_KEY_PATTERN)
    operator: str | None = None
    value: ScalarValue
    unit_label: str | None = Field(default=None, max_length=MAX_UNIT_LABEL)

    @field_validator("value")
    @classmethod
    def _value(cls, value: ScalarValue) -> ScalarValue:
        return _check_scalar(value)

    @field_validator("unit_label")
    @classmethod
    def _unit(cls, value: str | None) -> str | None:
        return _check_unit(value)

    @field_validator("operator")
    @classmethod
    def _operator(cls, value: str | None) -> str | None:
        return _check_operator(value)

    def accepted(self) -> AcceptedValue:
        return AcceptedValue(**self.model_dump(), source="reviewer")


class _TextFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "note", "reason", check_fields=False)
    @classmethod
    def _single_line(cls, value: str | None) -> str | None:
        return _clean_text(value, multiline=True)

    @field_validator("description", check_fields=False)
    @classmethod
    def _multi_line(cls, value: str | None) -> str | None:
        return _clean_text(value, multiline=True)


class ExtractedPageCitation(BaseModel):
    """Cita d'una pagina extreta. El text citat el treu el servidor de les posicions."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["extracted_page"]
    extraction_id: uuid.UUID
    page_id: uuid.UUID
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=1)
    location_label: str | None = Field(default=None, max_length=200)


class DocumentFileCitation(BaseModel):
    """Cita d'una versio d'un document sense pagines (XLSX, DOCX...)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["document_file"]
    file_id: uuid.UUID
    location_label: str | None = Field(default=None, max_length=200)
    quote: str | None = Field(default=None, max_length=MAX_DESCRIPTION)


Citation = Annotated[ExtractedPageCitation | DocumentFileCitation, Field(discriminator="kind")]


class ManualRequirementCreate(_TextFields):
    """Un requisit que el revisor afegeix. Viu NOMES a la revisio d'aquesta empresa."""

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    category: RequirementCategory
    obligation: RequirementObligation = RequirementObligation.MANDATORY
    modality: RequirementModality = RequirementModality.MANDATORY
    values: list[ReviewerValueInput] = Field(default_factory=list, max_length=MAX_VALUES)
    #: Afegir-lo no vol dir haver-lo comprovat: neix pendent si no es diu res.
    verification_status: Literal["unreviewed", "needs_review", "verified"] = "unreviewed"
    note: str | None = Field(default=None, max_length=MAX_NOTE)
    citations: list[Citation] = Field(default_factory=list, max_length=MAX_CITATIONS)

    @model_validator(mode="after")
    def _unique_keys(self) -> ManualRequirementCreate:
        keys = [v.key for v in self.values]
        if len(keys) != len(set(keys)):
            raise ValueError("claus de valor repetides")
        return self


ReviewActionName = Literal["confirm", "correct", "reject", "mark_partial", "note", "set_status"]


class ReviewActionRequest(BaseModel):
    """Una accio sobre un element, amb la revisio que el client creu vigent."""

    model_config = ConfigDict(extra="forbid")

    action: ReviewActionName
    expected_revision: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ConfirmPayload(_TextFields):
    note: str | None = Field(default=None, max_length=MAX_NOTE)


class CorrectPayload(_TextFields):
    """Corregeix NOMES la instantania acceptada. Els valors es fusionen per clau."""

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    category: RequirementCategory | None = None
    obligation: RequirementObligation | None = None
    modality: RequirementModality | None = None
    #: Crea o substitueix el valor amb aquesta clau (font: revisor).
    values: list[ReviewerValueInput] = Field(default_factory=list, max_length=MAX_VALUES)
    #: Claus que el revisor treu de la instantania.
    remove_values: list[Annotated[str, Field(pattern=VALUE_KEY_PATTERN)]] = Field(
        default_factory=list, max_length=MAX_VALUES
    )
    verification_status: ReviewVerification = ReviewVerification.VERIFIED
    note: str | None = Field(default=None, max_length=MAX_NOTE)

    @model_validator(mode="after")
    def _coherent(self) -> CorrectPayload:
        keys = [v.key for v in self.values]
        if len(keys) != len(set(keys)):
            raise ValueError("claus de valor repetides")
        if set(keys) & set(self.remove_values):
            raise ValueError("una clau no es pot corregir i treure alhora")
        if self.verification_status in (
            ReviewVerification.REJECTED, ReviewVerification.PARTIAL
        ) and not self.note:
            raise ValueError("descartar o marcar parcial demana una nota")
        return self


class ReasonPayload(_TextFields):
    reason: str = Field(min_length=1, max_length=MAX_NOTE)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cal un motiu")
        return value.strip()


class NotePayload(_TextFields):
    note: str = Field(min_length=1, max_length=MAX_NOTE)

    @field_validator("note")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cal una nota")
        return value.strip()


class SetStatusPayload(_TextFields):
    status: ReviewVerification
    note: str | None = Field(default=None, max_length=MAX_NOTE)

    @model_validator(mode="after")
    def _reasons(self) -> SetStatusPayload:
        if self.status in (ReviewVerification.REJECTED, ReviewVerification.PARTIAL) and not (
            self.note and self.note.strip()
        ):
            raise ValueError("descartar o marcar parcial demana una nota")
        return self


# --------------------------------------------------------------------------- #
# Lectures per a la interficie                                                  #
# --------------------------------------------------------------------------- #


class CanonicalEvidence(BaseModel):
    document: str | None
    page: int | None
    section: str | None
    quote: str | None
    status_label: str


class CanonicalValue(BaseModel):
    key: str
    operator: str | None
    value: Any
    unit_label: str | None
    source_label: str
    status_label: str


class CanonicalSourceRef(BaseModel):
    kind: Literal["extracted_requirement", "citation"]
    id: uuid.UUID


class CanonicalRequirement(BaseModel):
    canonical_id: uuid.UUID
    title: str
    description: str | None
    category: str
    category_label: str
    obligation_label: str
    provenance: str
    provenance_label: str
    verification_status: str
    verification_label: str
    needs_review: bool
    values: list[CanonicalValue]
    evidence_label: str
    evidence: list[CanonicalEvidence]
    documents: list[str]
    pages: list[int]
    sections: list[str]
    source_refs: list[CanonicalSourceRef]
    review_revision: int


class CountLabel(BaseModel):
    code: str
    label: str
    count: int


class ReviewOverview(BaseModel):
    tender_id: uuid.UUID
    review_started: bool
    total_requirements: int
    #: Requisits vigents de l'extraccio que encara no tenen element de revisio.
    pending_materialisation: int
    needs_review_count: int
    verification_counts: list[CountLabel]
    provenance_counts: list[CountLabel]


class ReviewStartResult(BaseModel):
    tender_id: uuid.UUID
    created: int
    already_present: int
    total_requirements: int


class ReviewHistoryEntry(BaseModel):
    revision_no: int
    action: str
    action_label: str
    status_after: str
    status_label: str
    accepted: dict[str, Any]
    changes: dict[str, Any]
    note: str | None
    actor_id: uuid.UUID | None
    created_at: datetime
