"""Gold v2: el mateix dataset, capac de puntuar el que la v2 va afegir.

La v1 del gold podia dir "aquest requisit existeix, es obligatori i val 60.750
euros". No podia dir si els 60.750 s'exigeixen **cada any o el millor dels
tres**, si una classificacio es **una via alternativa** o una exigencia, ni
quina part d'una polissa es **sublimit**. Mesurar la `requirements-v2` amb un
gold que no distingeix aquestes coses donaria xifres que semblen mesures i no
ho son.

Tres decisions que governen aquest modul:

* **El gold v1 revisat no es toca.** Aquesta versio n'hereta les anotacions; el
  fitxer original queda immutable i citable. Una mesura ha de poder dir amb
  quina versio exacta del gold es va fer.
* **El valor del gold es valida ESTRICTAMENT.** A l'extractor, un valor que no
  encaixa cau a `GenericValue` perque perdre'l seria pitjor. Aqui es al reves:
  el gold l'escriu una persona, i un valor que no valida es un error a
  corregir, no una dada a tolerar.
* **Les dimensions mesurables es DERIVEN, no es declaren.** Una llista escrita
  a ma pot dir que una anotacio mesura l'agregacio quan el camp es `unknown`.
  Derivar-ho del contingut fa que la cobertura no pugui mentir.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai.value_schemas import VALUE_MODELS, VALUE_SCHEMA_VERSION
from app.evaluation.dataset import (
    DATASETS_DIR,
    AnnotationStatus,
    GoldAwardCriterion,
    GoldCase,
    GoldDataset,
    GoldRequirement,
    StrictModel,
)
from app.models.enums import (
    AggregationType,
    AwardCriterionType,
    InsuranceLimitKind,
    RequirementModality,
    RequirementObligation,
    StrEnum,
)

#: Versio de l'esquema del gold. Es el que fa que un fitxer v2 no es carregui
#: com si fos v1, i el que un benchmark ha de citar al costat del `dataset_id`.
GOLD_SCHEMA_VERSION = "gold-schema-v2"


class BenchmarkDimension(StrEnum):
    """Les propietats que la v2 va afegir i que el benchmark ha de puntuar.

    Cada una existeix perque la revisio humana del gold va trobar que la v1 la
    perdia. Si cap anotacio verificada no la fixa, la metrica corresponent no
    es 0%: **no existeix**, i el report ha de dir `n/d`.
    """

    OBLIGATION_MODALITY = "obligation_modality"
    ALTERNATIVES = "alternatives"
    AGGREGATION = "aggregation"
    PERCENTAGE_BASE = "percentage_base"
    INSURANCE_LIMITS = "insurance_limits"
    EQUIVALENCE = "equivalence"
    AWARD_FORMULA = "award_formula"


class AnnotationOrigin(StrEnum):
    """D'on surt una anotacio d'aquest dataset.

    Serveix per no barrejar mai dues coses: el que ja estava verificat i el que
    aquesta iteracio nomes proposa. Una proposta no hereta la verificacio de
    res.
    """

    #: Ve de `gold-v1-reviewed`, adaptada mecanicament a l'esquema v2.
    INHERITED = "inherited"
    #: Correccio nova, escrita per a la revisio humana. Neix sense verificar.
    PROPOSAL = "proposal"


class GoldEvidence(StrictModel):
    """Una cita addicional, quan una anotacio es reparteix en mes d'una pagina.

    El cas real: la frase que anuncia la formula es a la pagina 70 i les
    definicions de les variables a la 71. Amb una sola cita s'hauria de triar
    quina meitat es perd.
    """

    document_name: str
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1)
    #: Que aporta aquesta cita respecte de la principal.
    role: str | None = None


class V2Annotation(BaseModel):
    """El que comparteixen requisits i criteris a la v2 del gold."""

    model_config = ConfigDict(extra="forbid")

    origin: AnnotationOrigin = AnnotationOrigin.INHERITED
    #: `gold_id` d'una anotacio del mateix cas que aquesta afina sense
    #: substituir-la. Serveix per afegir el que la v1 no podia representar
    #: **sense tocar una anotacio ja verificada**, i per poder detectar despres
    #: que dues anotacions parlen del mateix i no s'han de comptar dues vegades.
    refines: str | None = None
    #: Cites addicionals. La principal continua sent `page_number` + `quote`.
    extra_evidence: list[GoldEvidence] = Field(default_factory=list)
    #: Que NO decideix el text. Escrit explicitament perque ningu ho ompli
    #: despres per intuicio: si no hi es a la pagina, no s'anota.
    unresolved: list[str] = Field(default_factory=list)
    #: Per que existeix la proposta. Buit a les heretades.
    proposal_reason: str | None = None

    #: `gold_id` de l'anotacio que l'ha substituida DINS DEL GOLD V2. Nomes
    #: l'escriu una decisio humana explicita.
    #:
    #: No s'esborra res ni es toca `annotation_status`: una anotacio superada
    #: segueix dient que va ser verificada, per qui i quan, i es pot auditar. El
    #: que canvia es que deixa de comptar al denominador, perque la seva versio
    #: afinada descriu la mateixa exigencia amb mes detall i comptar-les totes
    #: dues demanaria dues coses on el plec en demana una.
    superseded_by: str | None = None
    superseded_at: date | None = None

    @property
    def is_proposal(self) -> bool:
        return self.origin == AnnotationOrigin.PROPOSAL

    @property
    def is_superseded(self) -> bool:
        return self.superseded_by is not None

    @property
    def counts_for_metrics(self) -> bool:
        """Si aquesta anotacio pot entrar al denominador d'una metrica.

        `verified` ja no basta: una anotacio verificada pero superada segueix
        sent certa i segueix sent auditable, pero la seva versio afinada es la
        que s'ha de comparar amb el que extreu el model.
        """
        return self.is_verified and not self.is_superseded


def _validate_v2_value(value: dict[str, Any] | None, value_schema: str | None) -> None:
    """El valor del gold ha de validar EXACTAMENT contra el contracte tipat.

    L'extractor pot caure a `GenericValue` quan el model retorna una forma
    rara; el gold, no. Aqui un valor que no valida es una errada d'anotacio.
    """
    if value_schema is None:
        return
    if value_schema != VALUE_SCHEMA_VERSION:
        raise ValueError(f"value_schema desconegut: {value_schema!r}")
    if not value:
        raise ValueError("value_schema informat pero sense value")
    kind = str(value.get("kind") or "")
    model = VALUE_MODELS.get(kind)
    if model is None:
        raise ValueError(f"kind de valor desconegut al gold: {kind!r}")
    model.model_validate(value)


class GoldRequirementV2(GoldRequirement, V2Annotation):
    """Un requisit anotat amb el matis que la v1 no podia expressar."""

    #: `alternative`/`conditional` no son exigencies: l'obligatorietat que les
    #: acompanya ha de ser `unknown`, no `mandatory`.
    modality: RequirementModality = RequirementModality.UNKNOWN
    #: Etiqueta de la disjuncio "A o B". Al gold es llegible per una persona; el
    #: que es mesura es l'agrupacio, no la cadena.
    alternative_group: str | None = None
    #: "o equivalent". Nomes `True` afirma res: `False` es el valor per defecte
    #: de l'esquema i no es distingeix de "no anotat".
    equivalence_allowed: bool = False
    condition_note: str | None = None
    #: Present quan `value` segueix el contracte tipat de la v2.
    value_schema: str | None = None

    @model_validator(mode="after")
    def _check_value(self) -> GoldRequirementV2:
        _validate_v2_value(self.value, self.value_schema)
        if (
            self.modality in (RequirementModality.ALTERNATIVE, RequirementModality.CONDITIONAL)
            and self.obligation == RequirementObligation.MANDATORY
        ):
            raise ValueError(
                "una via alternativa o condicional no pot ser `mandatory`: "
                "seria exactament el blocador fals que la v2 evita"
            )
        return self

    @property
    def value_model(self) -> Any:
        """El valor tipat, o `None` si l'anotacio encara es v1."""
        if self.value_schema is None or not self.value:
            return None
        return VALUE_MODELS[str(self.value["kind"])].model_validate(self.value)


class GoldFormulaVariable(StrictModel):
    """Un simbol i el que en diu el plec, copiat literalment."""

    symbol: str = Field(min_length=1, max_length=40)
    meaning: str = Field(min_length=1, max_length=300)


class GoldAwardCriterionV2(GoldAwardCriterion, V2Annotation):
    """Un criteri d'adjudicacio amb la formula conservada, mai executada."""

    criterion_type: AwardCriterionType = AwardCriterionType.UNKNOWN
    #: La frase sencera que conte la formula, literal. `points` ja es el maxim
    #: del criteri: no s'hi afegeix un segon camp que digui el mateix.
    formula_text: str | None = None
    formula_variables: list[GoldFormulaVariable] = Field(default_factory=list)
    tranches: list[dict[str, Any]] = Field(default_factory=list)


class GoldCaseV2(GoldCase):
    requirements: list[GoldRequirementV2] = Field(default_factory=list)
    award_criteria: list[GoldAwardCriterionV2] = Field(default_factory=list)

    # Aquestes dues propietats son el que el benchmark fa servir com a
    # denominador (`runner.py`). Filtrar aqui es el que fa que una anotacio
    # superada quedi fora de les metriques sense haver-la d'esborrar ni de
    # tocar-li l'estat.

    @property
    def verified_requirements(self) -> list[GoldRequirementV2]:
        return [item for item in self.requirements if item.counts_for_metrics]

    @property
    def verified_award_criteria(self) -> list[GoldAwardCriterionV2]:
        return [item for item in self.award_criteria if item.counts_for_metrics]

    @property
    def superseded(self) -> list[GoldItemV2]:
        return [
            item
            for item in [*self.requirements, *self.award_criteria]
            if item.is_superseded
        ]


GoldItemV2 = GoldRequirementV2 | GoldAwardCriterionV2


# --------------------------------------------------------------------------- #
# Dimensions: derivades del contingut, mai declarades                           #
# --------------------------------------------------------------------------- #


def _amounts(value: dict[str, Any]) -> list[dict[str, Any]]:
    keys = ("threshold", "min_individual_amount", "min_accumulated_amount")
    return [value[key] for key in keys if isinstance(value.get(key), dict)]


def dimensions_of(item: GoldItemV2) -> set[BenchmarkDimension]:
    """Que pot puntuar aquesta anotacio. Es llegeix del que hi ha escrit.

    Una anotacio "mesura" una dimensio nomes si la fixa de veritat. Un camp amb
    el valor per defecte de l'esquema (`unknown`, `False`, llista buida) no
    distingeix "el plec no ho diu" de "ningu no ho va anotar", i per tant no es
    pot comparar amb res.
    """
    found: set[BenchmarkDimension] = set()

    if isinstance(item, GoldAwardCriterionV2):
        if item.formula or item.formula_text:
            found.add(BenchmarkDimension.AWARD_FORMULA)
        return found

    if item.modality != RequirementModality.UNKNOWN:
        found.add(BenchmarkDimension.OBLIGATION_MODALITY)
    if item.alternative_group:
        found.add(BenchmarkDimension.ALTERNATIVES)
    if item.equivalence_allowed:
        found.add(BenchmarkDimension.EQUIVALENCE)

    value = item.value or {}
    if item.value_schema and value:
        aggregation = value.get("aggregation")
        if aggregation and aggregation != AggregationType.UNKNOWN.value:
            found.add(BenchmarkDimension.AGGREGATION)
        for amount in _amounts(value):
            if amount.get("percentage") is not None:
                found.add(BenchmarkDimension.PERCENTAGE_BASE)
        if value.get("kind") == "insurance":
            typed = [
                limit
                for limit in value.get("limits") or []
                if limit.get("kind") not in (None, InsuranceLimitKind.UNKNOWN.value)
            ]
            if typed:
                found.add(BenchmarkDimension.INSURANCE_LIMITS)

    return found


class GoldDatasetV2(GoldDataset):
    """El dataset sencer. Segueix sent un `GoldDataset` per a qui ja el llegia."""

    #: Presencia obligatoria: es el que separa un fitxer v2 d'un v1 en carregar.
    gold_schema_version: Literal["gold-schema-v2"] = GOLD_SCHEMA_VERSION
    #: De quin dataset hereta. La versio d'origen queda immutable.
    derived_from: str | None = None
    cases: list[GoldCaseV2] = Field(default_factory=list)

    @field_validator("cases")
    @classmethod
    def _unique_case_ids_v2(cls, value: list[GoldCaseV2]) -> list[GoldCaseV2]:
        ids = [case.case_id for case in value]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id repetit al dataset")
        return value

    @property
    def items(self) -> list[tuple[GoldCaseV2, GoldItemV2]]:
        return [
            (case, item)
            for case in self.cases
            for item in [*case.requirements, *case.award_criteria]
        ]

    def proposals(self) -> list[tuple[GoldCaseV2, GoldItemV2]]:
        return [(case, item) for case, item in self.items if item.is_proposal]

    def inherited(self) -> list[tuple[GoldCaseV2, GoldItemV2]]:
        return [(case, item) for case, item in self.items if not item.is_proposal]

    def dimension_coverage(self) -> dict[str, dict[str, int]]:
        """Quantes anotacions fixen cada dimensio, per estat.

        `verified` es l'unic recompte que pot sostenir una metrica. `pending`
        diu que hi haura **si** la revisio humana ho accepta, i no s'ha de
        llegir com una mesura.
        """
        coverage = {
            dimension.value: {"verified": 0, "pending": 0} for dimension in BenchmarkDimension
        }
        for _, item in self.items:
            bucket = "verified" if item.counts_for_metrics else "pending"
            for dimension in dimensions_of(item):
                coverage[dimension.value][bucket] += 1
        return coverage

    def double_counted(self) -> list[tuple[str, str]]:
        """Parelles (afinada, afinadora) que estan verificades TOTES DUES.

        Una anotacio que n'afina una altra descriu **la mateixa exigencia** amb
        mes detall. Si les dues acaben verificades, el gold demana dues coses on
        el plec en demana una: el denominador s'infla i un extractor correcte,
        que n'extreu una, apareixeria com si se n'hagues deixat una.

        Resoldre-ho es una decisio humana —quina de les dues es queda—, aixi que
        aqui nomes es detecta i es diu.
        """
        by_case: list[tuple[str, str]] = []
        for case in self.cases:
            index = {item.gold_id: item for item in [*case.requirements, *case.award_criteria]}
            for item in index.values():
                refined = index.get(item.refines or "")
                if (
                    refined is not None
                    and item.counts_for_metrics
                    and refined.counts_for_metrics
                ):
                    by_case.append(
                        (f"{case.case_id}::{refined.gold_id}", f"{case.case_id}::{item.gold_id}")
                    )
        return sorted(by_case)

    def categories_covered(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for _, item in self.items:
            if not isinstance(item, GoldRequirementV2):
                continue
            entry = counts.setdefault(item.category.value, {"verified": 0, "pending": 0})
            entry["verified" if item.counts_for_metrics else "pending"] += 1
        return dict(sorted(counts.items()))

    def coverage(self) -> dict[str, int]:
        """El recompte de sempre, mes el que la v2 hi afegeix.

        `verified` segueix sent el recompte per ESTAT, perque es el que llegeix
        tot el que ja existia. El que de veritat sosté una metrica es
        `counts_for_metrics`: una anotacio superada segueix sent `verified` i ja
        no compta.
        """
        counts = super().coverage()
        counts["superseded"] = sum(1 for _, item in self.items if item.is_superseded)
        counts["counts_for_metrics"] = sum(
            1 for _, item in self.items if item.counts_for_metrics
        )
        return counts

    def status_counts(self) -> dict[str, int]:
        counts = dict.fromkeys((status.value for status in AnnotationStatus), 0)
        for _, item in self.items:
            counts[item.annotation_status.value] += 1
        # Fora del recompte per estat: una anotacio superada segueix constant
        # com a `verified`, i sense aquesta xifra el total no quadraria amb el
        # denominador real.
        counts["superseded"] = sum(1 for _, item in self.items if item.is_superseded)
        counts["counts_for_metrics"] = sum(
            1 for _, item in self.items if item.counts_for_metrics
        )
        return counts


def load_dataset_v2(dataset_id: str, *, base_dir: Path | None = None) -> GoldDatasetV2:
    path = (base_dir / f"{dataset_id}.json") if base_dir else DATASETS_DIR / f"{dataset_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No existeix el dataset {dataset_id!r} a {path}")
    return GoldDatasetV2.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_gold(dataset_id: str, *, base_dir: Path | None = None) -> GoldDataset:
    """Carrega un gold sigui de la versio d'esquema que sigui.

    Existeix perque les eines (benchmark, revisio) no hagin de saber amb quina
    versio esta escrit el fitxer que els demanen. Un fitxer v2 carregat amb el
    lector v1 falla, i ha de fallar: son contractes diferents.
    """
    from app.evaluation.dataset import load_dataset

    path = (base_dir / f"{dataset_id}.json") if base_dir else DATASETS_DIR / f"{dataset_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No existeix el dataset {dataset_id!r} a {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("gold_schema_version") == GOLD_SCHEMA_VERSION:
        return GoldDatasetV2.model_validate(raw)
    return load_dataset(dataset_id, base_dir=base_dir)
