"""Esquemes de la capa de revisio canonica (S-R1).

Dues families, i no es barregen:

* **Instantania interna** (`RequirementSnapshot`): el que es desa a
  `review_items.accepted_json`. Tipada perque la forma no depengui de qui
  l'escrigui; els codis hi son interns (categoria, obligacio...).
* **Lectures per a la interficie** (`Canonical*`, `ReviewOverview`): el que
  consumeix la UI. Porten etiquetes en catala i CAP codi tecnic d'extraccio
  (chunks, normalitzacions, codis de suport de claims...).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Instantania interna (`accepted_json`)                                         #
# --------------------------------------------------------------------------- #


class AcceptedValue(BaseModel):
    """Un valor acceptat. `source` diu si surt del document o l'afirma el revisor."""

    model_config = ConfigDict(extra="forbid")

    key: str
    operator: str | None = None
    value: Any = None
    unit_label: str | None = None
    source: Literal["document", "reviewer"]


class RequirementSnapshot(BaseModel):
    """El requisit tal com l'accepta l'empresa. Neix copiant l'extraccio."""

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str | None = None
    category: str
    obligation: str
    modality: str
    values: list[AcceptedValue] = Field(default_factory=list)
    needs_review_from_extraction: bool


# --------------------------------------------------------------------------- #
# Lectures per a la interficie                                                  #
# --------------------------------------------------------------------------- #


class CanonicalEvidence(BaseModel):
    document: str | None
    page: int
    section: str | None
    quote: str
    status_label: str


class CanonicalValue(BaseModel):
    key: str
    operator: str | None
    value: Any
    unit_label: str | None
    source_label: str
    status_label: str


class CanonicalSourceRef(BaseModel):
    kind: Literal["extracted_requirement"]
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
