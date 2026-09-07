"""Gold dataset: la veritat de referencia, anotada a ma.

Es un fitxer JSON versionat a Git, no una taula. El motiu no es simplicitat sino
auditabilitat: una persona ha de poder obrir-lo, llegir-lo, discutir-lo en una
revisio de codi i veure'n l'historial. Una taula de base de dades no dona res
d'aixo.

Dues regles que el governen:

* **L'output del model NO es gold.** Si s'anota amb el que ha dit l'extractor,
  el benchmark mesura si el model es coherent amb ell mateix, no si encerta.
* **El gold no es canvia perque el model falli.** Nomes es corregeix quan la
  revisio humana determina que l'etiqueta era erronia, i llavors la correccio
  queda registrada a `revisions` amb motiu, revisor i data.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    AwardEvaluationType,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
    StrEnum,
)

#: Els datasets viuen fora del paquet, al costat del codi, versionats a Git.
DATASETS_DIR = Path(__file__).resolve().parents[2] / "datasets" / "requirements"

#: Categories que poden canviar una decisio Go/No-Go. Son les que es mesuren
#: amb mes exigencia; encertar `other` no salva ningu.
CRITICAL_CATEGORIES: frozenset[RequirementCategory] = frozenset(
    {
        RequirementCategory.ECONOMIC_SOLVENCY,
        RequirementCategory.TECHNICAL_SOLVENCY,
        RequirementCategory.EXPERIENCE,
        RequirementCategory.CERTIFICATION,
        RequirementCategory.REGISTRATION,
        RequirementCategory.BUSINESS_CLASSIFICATION,
        RequirementCategory.INSURANCE,
        RequirementCategory.PERSONNEL,
        RequirementCategory.PROFESSIONAL_QUALIFICATION,
        RequirementCategory.DEADLINE,
    }
)


class AnnotationStatus(StrEnum):
    """Fins a quin punt una anotacio es pot considerar veritat de referencia.

    Una anotacio acabada d'escriure es un ESBORRANY, per be feta que estigui.
    Nomes despres que una persona l'hagi comparada amb la pagina original passa
    a `VERIFIED` i entra a les metriques oficials. Dir que un esborrany es gold
    truth es exactament la mena d'afirmacio que aquest camp existeix per evitar.
    """

    DRAFT = "draft"
    VERIFIED = "verified"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnnotatedItem(StrictModel):
    """Procedencia de l'anotacio, compartida per requisits i criteris."""

    #: Comenca sempre com a esborrany. Ningu no s'autoverifica.
    annotation_status: AnnotationStatus = AnnotationStatus.DRAFT
    #: Qui l'ha escrita.
    annotated_by: str | None = None
    #: Qui l'ha revisada contra la pagina original. Diferent d'`annotated_by`.
    reviewed_by: str | None = None
    reviewed_at: date | None = None
    review_note: str | None = None

    @property
    def is_verified(self) -> bool:
        return self.annotation_status == AnnotationStatus.VERIFIED


class GoldRequirement(AnnotatedItem):
    """Un requisit anotat a ma sobre una pagina concreta."""

    gold_id: str
    category: RequirementCategory
    obligation: RequirementObligation
    operator: RequirementOperator | None = None
    scope: RequirementScope = RequirementScope.TENDER
    lot_labels: list[str] = Field(default_factory=list)

    #: Valor estructurat esperat. `None` quan el plec no en dona cap.
    value: dict[str, Any] | None = None

    #: On ho diu, per poder comprovar l'evidencia del model.
    document_name: str
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1)

    #: Cert si perdre'l pot canviar un Go/No-Go. Es deriva si no s'informa.
    critical: bool | None = None
    notes: str | None = None

    @property
    def is_critical(self) -> bool:
        if self.critical is not None:
            return self.critical
        return (
            self.obligation == RequirementObligation.MANDATORY
            and self.category in CRITICAL_CATEGORIES
        )


class GoldAwardCriterion(AnnotatedItem):
    gold_id: str
    name: str
    points: float | None = None
    weight_percent: float | None = None
    evaluation_type: AwardEvaluationType = AwardEvaluationType.UNKNOWN
    #: `None` quan el plec no en dona cap. Anotar-ne una seria inventar-la.
    formula: str | None = None
    document_name: str
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1)
    notes: str | None = None


class GoldCase(StrictModel):
    """Un expedient anotat."""

    case_id: str
    expediente_number: str | None = None
    #: Identificador a la font. Es el que permet resoldre el Tender real.
    tender_external_id: str | None = None
    title: str | None = None
    buyer: str | None = None
    has_lots: bool = False
    #: Trets que fan el cas interessant: plec llarg, taula complexa, lots...
    traits: list[str] = Field(default_factory=list)
    notes: str | None = None

    requirements: list[GoldRequirement] = Field(default_factory=list)
    award_criteria: list[GoldAwardCriterion] = Field(default_factory=list)

    @field_validator("requirements")
    @classmethod
    def _unique_ids(cls, value: list[GoldRequirement]) -> list[GoldRequirement]:
        ids = [item.gold_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("gold_id repetit dins del cas")
        return value

    @property
    def critical_requirements(self) -> list[GoldRequirement]:
        return [r for r in self.requirements if r.is_critical]

    @property
    def verified_requirements(self) -> list[GoldRequirement]:
        """Les uniques que poden entrar al denominador oficial."""
        return [r for r in self.requirements if r.is_verified]

    @property
    def verified_award_criteria(self) -> list[GoldAwardCriterion]:
        return [c for c in self.award_criteria if c.is_verified]


class GoldRevision(StrictModel):
    """Correccio del gold. Mai silenciosa."""

    date: date
    reviewer: str
    case_id: str
    gold_id: str | None = None
    reason: str


class GoldDataset(StrictModel):
    dataset_id: str
    version: int
    created_at: date
    description: str
    #: Historial de correccions del gold. Buit al principi.
    revisions: list[GoldRevision] = Field(default_factory=list)
    cases: list[GoldCase] = Field(default_factory=list)

    @field_validator("cases")
    @classmethod
    def _unique_case_ids(cls, value: list[GoldCase]) -> list[GoldCase]:
        ids = [case.case_id for case in value]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id repetit al dataset")
        return value

    @property
    def total_requirements(self) -> int:
        return sum(len(case.requirements) for case in self.cases)

    @property
    def total_critical(self) -> int:
        return sum(len(case.critical_requirements) for case in self.cases)

    @property
    def total_award_criteria(self) -> int:
        return sum(len(case.award_criteria) for case in self.cases)

    @property
    def verified_requirements(self) -> int:
        return sum(len(case.verified_requirements) for case in self.cases)

    @property
    def verified_award_criteria(self) -> int:
        return sum(len(case.verified_award_criteria) for case in self.cases)

    @property
    def cases_with_verified(self) -> int:
        return sum(1 for case in self.cases if case.verified_requirements)

    def coverage(self) -> dict[str, int]:
        """Recompte per estat. El report ho ensenya: draft NO es gold."""
        counts = dict.fromkeys((s.value for s in AnnotationStatus), 0)
        for case in self.cases:
            for item in [*case.requirements, *case.award_criteria]:
                counts[item.annotation_status.value] += 1
        counts["cases"] = len(self.cases)
        counts["cases_with_verified"] = self.cases_with_verified
        return counts

    def case(self, case_id: str) -> GoldCase | None:
        return next((c for c in self.cases if c.case_id == case_id), None)


def dataset_path(dataset_id: str) -> Path:
    return DATASETS_DIR / f"{dataset_id}.json"


def load_dataset(dataset_id: str, *, base_dir: Path | None = None) -> GoldDataset:
    """Carrega i valida un gold dataset. Falla clar si no compleix l'esquema."""
    path = (base_dir / f"{dataset_id}.json") if base_dir else dataset_path(dataset_id)
    if not path.exists():
        raise FileNotFoundError(f"No existeix el dataset {dataset_id!r} a {path}")
    return GoldDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_dataset(dataset: GoldDataset, *, base_dir: Path | None = None) -> Path:
    """Desa el dataset amb format estable, per fer diffs llegibles a Git."""
    directory = base_dir or DATASETS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{dataset.dataset_id}.json"
    path.write_text(
        json.dumps(
            dataset.model_dump(mode="json", exclude_none=False),
            indent=2,
            ensure_ascii=False,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def available_datasets(base_dir: Path | None = None) -> list[str]:
    directory = base_dir or DATASETS_DIR
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))
