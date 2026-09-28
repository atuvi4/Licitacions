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
    ReviewAwardRole,
    ReviewRelationType,
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


class AwardMechanism(BaseModel):
    """Un mecanisme de puntuacio declarat i la seva comprovacio. Codis INTERNS."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=40)
    check: str = Field(max_length=40)


class AwardSnapshot(BaseModel):
    """El criteri d'adjudicacio tal com l'accepta l'empresa.

    Els codis de l'extraccio (`declared_kind`, `award_class`, `mechanisms`) es
    desen per poder-ho auditar, pero NO surten a la lectura normal: alla nomes hi
    ha etiquetes. `formula_status` nomes val `verified` si una cita valida prova
    la formula; tenir-ne el text no n'hi ha prou.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    points: float | None = None
    weight_percent: float | None = None
    criterion_type: str = Field(max_length=40)
    evaluation_type: str = Field(max_length=40)
    declared_kind: str | None = Field(default=None, max_length=40)
    award_class: str | None = Field(default=None, max_length=40)
    mechanisms: list[AwardMechanism] = Field(default_factory=list, max_length=MAX_VALUES)
    mechanism_status: Literal["verified", "not_verified", "none"]
    formula: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    formula_status: Literal["verified", "not_evidence_verified"] | None = None
    needs_review_from_extraction: bool

    @field_validator("points", "weight_percent")
    @classmethod
    def _finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("ha de ser un numero finit")
        return value

    @model_validator(mode="after")
    def _formula_state(self) -> AwardSnapshot:
        if (self.formula is None) != (self.formula_status is None):
            raise ValueError("una formula porta estat, i nomes una formula en porta")
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


ReviewActionName = Literal[
    "confirm", "correct", "reject", "mark_partial", "note", "set_status", "link_source",
    "set_award_role",
]


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


class LinkSourcePayload(_TextFields):
    """Afegeix una cita com a evidencia addicional. No verifica ni corregeix res.

    La cita es crea al servidor: d'una pagina, el text citat surt de les
    posicions sobre el text desat; mai no s'accepta una cita del client com a
    autoritativa.
    """

    citation: Citation
    note: str | None = Field(default=None, max_length=MAX_NOTE)


class SetAwardRolePayload(_TextFields):
    """Paper del criteri a l'estructura de puntuacio. No el verifica ni en toca els punts."""

    role: ReviewAwardRole
    note: str | None = Field(default=None, max_length=MAX_NOTE)


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


class RelationCreate(_TextFields):
    """Una relacio dirigida `from -> to` entre dos elements de la MATEIXA empresa i licitacio.

    Direccio (llegiu-la "from <relacio> to"):

    * `duplicate_of`: `from` es un duplicat de `to` i es plegara dins seu a la
      vista canonica. Un element es duplicat d'UN sol altre.
    * `restatement_of`: `from` repeteix `to` en un altre lloc o document (p. ex.
      el criteri de l'Informe repeteix el criteri original del PCAP).
    * `parent_of`: `from` agrupa `to` (p. ex. el bloc qualitatiu de 20 punts
      agrupa cadascuna de les 8 files puntuades). Un element te UN sol pare.
    * `partial_match_of`: `from` coincideix en part amb `to`, pero `to` NO el
      cobreix del tot (p. ex. M06, alta disponibilitat al nuvol, respecte de la
      fila de dimensionament extreta de la p32).
    * `supersedes`: `from` substitueix `to` al model acceptat.
    * `related_to`: nomes informativa (p. ex. M21, accés directe DDI, respecte
      de la numeracio DDI del troncal SIP: relacionats, no equivalents). Es
      simetrica: es desa una sola vegada, en la direccio en que es crea.
    """

    from_item_id: uuid.UUID
    to_item_id: uuid.UUID
    relation_type: ReviewRelationType
    note: str | None = Field(default=None, max_length=MAX_NOTE)


class CanonicalRelation(BaseModel):
    relation_id: uuid.UUID
    #: `outgoing`: aquest element es el `from`; `incoming`: n'es el `to`.
    direction: Literal["outgoing", "incoming"]
    relation_type: str
    relation_label: str
    other_item_id: uuid.UUID
    other_item_title: str | None
    note: str | None
    #: Una relacio `duplicate_of`: la vista canonica la podra plegar.
    foldable: bool
    created_at: datetime
    archived_at: datetime | None = None


class RelationArchiveResult(BaseModel):
    changed: bool
    relation: CanonicalRelation


class CanonicalSourceRef(BaseModel):
    kind: Literal["extracted_requirement", "citation"]
    id: uuid.UUID


class EditableRequirement(BaseModel):
    """La instantania acceptada, tal com la necessita l'editor de correccions.

    NOMES transport: porta els codis estables (`category`, `obligation`,
    `modality`, `source`, operador) perque el client pugui construir un
    `CorrectPayload` sense endevinar-los a partir d'etiquetes. La interficie no
    els ha d'ensenyar mai com a text: per a aixo hi ha els camps `*_label`.
    """

    title: str
    description: str | None
    category: str
    obligation: str
    modality: str
    values: list[AcceptedValue]


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
    #: Relacions actives que no pleguen l'element (tot tret de `duplicate_of`).
    visible_relations: list[CanonicalRelation] = Field(default_factory=list)
    #: Relacions `duplicate_of` actives, en qualsevol direccio. Encara NO s'amaga
    #: cap element de la llista: aixo ho fara la vista canonica seguent.
    folded_relations: list[CanonicalRelation] = Field(default_factory=list)
    #: Per a l'editor de correccions: la instantania acceptada amb codis estables.
    editable: EditableRequirement


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
    #: La revisio representa una altra run (o mes d'una) que la vigent: `start`
    #: refusa materialitzar fins que es reconciliï. Nomes ids, cap recompte.
    reconciliation_required: bool = False
    current_run_id: uuid.UUID | None = None
    represented_requirement_run_ids: list[uuid.UUID] = Field(default_factory=list)
    represented_award_run_ids: list[uuid.UUID] = Field(default_factory=list)


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


# --------------------------------------------------------------------------- #
# Criteris d'adjudicacio canonics                                              #
# --------------------------------------------------------------------------- #


class AwardItemRef(BaseModel):
    """Un altre criteri canonic, pel nom (pare, fill, reiteracio...)."""

    canonical_id: uuid.UUID
    name: str
    points: float | None
    documents: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)


class CanonicalAwardCriterion(BaseModel):
    """Un criteri tal com el llegeix la interficie. Cap codi d'extraccio."""

    canonical_id: uuid.UUID
    name: str
    description: str | None
    points: float | None
    #: `null` mentre ningu no li ha assignat paper.
    role: str | None
    role_label: str
    #: Compta al total de puntuacio: paper `scored` i no plegat dins d'un altre.
    counts_in_total: bool
    parent: AwardItemRef | None
    children: list[AwardItemRef] = Field(default_factory=list)
    #: Si aquest criteri en repeteix un altre, el canonic on es plega.
    folded_into: AwardItemRef | None = None
    #: Els criteris que repeteixen aquest (Informe, model d'oferta...).
    restatements: list[AwardItemRef] = Field(default_factory=list)
    mechanism_status: Literal["verified", "not_verified", "none"]
    mechanism_label: str
    formula: str | None
    formula_status: Literal["verified", "not_evidence_verified"] | None
    formula_label: str | None
    provenance: str
    provenance_label: str
    verification_status: str
    verification_label: str
    evidence: list[CanonicalEvidence]
    documents: list[str]
    pages: list[int]
    review_revision: int


class AwardCheck(BaseModel):
    """Un avis de coherencia. No reescriu res: nomes ho assenyala."""

    code: str
    label: str
    item_ids: list[uuid.UUID] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AwardCriteriaView(BaseModel):
    view: Literal["scoring", "all"]
    criteria: list[CanonicalAwardCriterion]
    #: Sempre de l'estructura de puntuacio, sigui quina sigui la vista demanada.
    scored_count: int
    total_points: float
    checks: list[AwardCheck]


class AwardStartResult(BaseModel):
    tender_id: uuid.UUID
    created: int
    already_present: int
    total_award_criteria: int
