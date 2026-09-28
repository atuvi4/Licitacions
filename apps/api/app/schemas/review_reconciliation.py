"""Previsualitzacio de reconciliacio despres d'una nova extraccio (S-R8). Nomes lectura.

Cap d'aquests objectes no es desa: es deriven cada vegada de la revisio de
l'empresa i de les files extretes (immutables). Els codis son estables per a
una futura aplicacio; les etiquetes son per a la interficie. Cap camp porta
chunks, prompts, tokens ni sortida del model.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel

ReconciliationState = Literal[
    "no_current_run",
    "not_started",
    "up_to_date",
    "new_extraction_available",
    "mixed_source_runs",
]
MatchClass = Literal["exact_auto", "review_candidate", "ambiguous", "new", "removed"]


class MatchCounts(BaseModel):
    exact_auto: int = 0
    review_candidate: int = 0
    ambiguous: int = 0
    new: int = 0
    removed: int = 0


class HumanState(BaseModel):
    """L'estat huma d'un element canonic existent. Nomes es llegeix."""

    review_item_id: uuid.UUID
    review_revision: int
    verification_status: str
    verification_label: str
    provenance: str
    provenance_label: str
    has_human_correction: bool
    extra_evidence_count: int
    relation_count: int
    #: Nomes criteris d'adjudicacio.
    award_role: str | None = None
    award_role_label: str | None = None
    #: Titol o nom de la instantania ACCEPTADA (pot diferir de la font).
    accepted_title: str


class SourceLocation(BaseModel):
    document: str | None
    page: int


class SourceValue(BaseModel):
    key: str
    operator: str | None
    value: Any
    unit_label: str | None


class RequirementSource(BaseModel):
    """Una fila extreta d'un requisit, tal com la llegiria la materialitzacio."""

    source_id: uuid.UUID
    run_id: uuid.UUID
    title: str
    description: str | None
    category: str
    category_label: str
    obligation_label: str
    values: list[SourceValue]
    locations: list[SourceLocation]


class AwardSource(BaseModel):
    source_id: uuid.UUID
    run_id: uuid.UUID
    name: str
    description: str | None
    points: float | None
    weight_percent: float | None
    criterion_type: str
    formula_status: str | None
    locations: list[SourceLocation]


class RequirementSourceDiff(BaseModel):
    """Font vella extreta contra font nova extreta."""

    title_changed: bool
    description_changed: bool
    category_changed: bool
    obligation_changed: bool
    modality_changed: bool
    values_changed: bool
    evidence_changed: bool


class RequirementAcceptedDiff(BaseModel):
    """Instantania ACCEPTADA per l'empresa contra font nova extreta."""

    title_changed: bool
    description_changed: bool
    category_changed: bool
    obligation_changed: bool
    modality_changed: bool
    values_changed: bool


class AwardSourceDiff(BaseModel):
    name_changed: bool
    description_changed: bool
    points_changed: bool
    weight_changed: bool
    kind_changed: bool
    mechanism_changed: bool
    evidence_changed: bool


class AwardAcceptedDiff(BaseModel):
    name_changed: bool
    description_changed: bool
    points_changed: bool
    weight_changed: bool
    kind_changed: bool
    mechanism_changed: bool


class RequirementMatch(BaseModel):
    """`exact_auto` o `review_candidate`: un element vell, una font nova."""

    match_class: MatchClass
    match_label: str
    item: HumanState
    old_source: RequirementSource
    new_source: RequirementSource
    source_diff: RequirementSourceDiff
    accepted_diff: RequirementAcceptedDiff
    #: Derivat: la font nova diu una altra cosa que el que l'empresa ha acceptat.
    requires_reverification: bool


class RequirementAmbiguity(BaseModel):
    match_class: MatchClass = "ambiguous"
    match_label: str
    reason: str
    reason_label: str
    items: list[HumanState]
    old_sources: list[RequirementSource]
    candidate_sources: list[RequirementSource]


class RequirementRemoved(BaseModel):
    match_class: MatchClass = "removed"
    match_label: str
    item: HumanState
    old_source: RequirementSource


class RequirementNew(BaseModel):
    match_class: MatchClass = "new"
    match_label: str
    source: RequirementSource


class AwardMatch(BaseModel):
    match_class: MatchClass
    match_label: str
    item: HumanState
    old_source: AwardSource
    new_source: AwardSource
    source_diff: AwardSourceDiff
    accepted_diff: AwardAcceptedDiff
    requires_reverification: bool


class AwardAmbiguity(BaseModel):
    match_class: MatchClass = "ambiguous"
    match_label: str
    reason: str
    reason_label: str
    items: list[HumanState]
    old_sources: list[AwardSource]
    candidate_sources: list[AwardSource]


class AwardRemoved(BaseModel):
    match_class: MatchClass = "removed"
    match_label: str
    item: HumanState
    old_source: AwardSource


class AwardNew(BaseModel):
    match_class: MatchClass = "new"
    match_label: str
    source: AwardSource


class _Section(BaseModel):
    state: ReconciliationState
    state_label: str
    represented_run_ids: list[uuid.UUID]
    #: Elements canonics extrets actius (els manuals no hi compten).
    canonical_item_count: int
    #: Files de la run vigent d'aquesta seccio.
    current_source_count: int
    counts: MatchCounts


class RequirementSection(_Section):
    matches: list[RequirementMatch] = []
    ambiguous: list[RequirementAmbiguity] = []
    removed: list[RequirementRemoved] = []
    new: list[RequirementNew] = []


class AwardScoring(BaseModel):
    """L'estructura de puntuacio canonica que hi ha ara. Informativa."""

    scored_count: int
    total_points: float
    scoring_reconciliation_pending: bool


class AwardSection(_Section):
    scoring: AwardScoring | None = None
    matches: list[AwardMatch] = []
    ambiguous: list[AwardAmbiguity] = []
    removed: list[AwardRemoved] = []
    new: list[AwardNew] = []


class ReconciliationPreview(BaseModel):
    tender_id: uuid.UUID
    state: ReconciliationState
    state_label: str
    current_run_id: uuid.UUID | None
    #: Nomes amb `new_extraction_available`. Canvia si el pla quedaria desfasat.
    plan_hash: str | None
    requirements: RequirementSection
    awards: AwardSection
