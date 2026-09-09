"""Contracte del `value` d'un requisit, versio 2.

A la v1 el valor era un diccionari lliure. Va servir per arrencar i la revisio
humana del gold en va trobar el limit: `{"amount": 60750, "years": 3}` no
distingeix "cada un dels tres ultims exercicis" de "el de major volum dels tres
ultims", i son requisits diferents. El mateix passava amb una polissa que exigeix
1.000.000 de cobertura I 300.000 per victima: dos limits sobre la mateixa
polissa que el valor lliure aplanava en un de sol.

Aquest modul dona forma a aquests casos **sense inventar-los**. Tres regles:

* **Cap camp obligatori mes enlla del minim.** Si el plec no diu l'agregacio, hi
  ha `unknown`, no una suposicio. Un `unknown` explicit es informacio; un valor
  per defecte silencios es una mentida.
* **Estructura, no interpretacio.** Aqui es representa el que diu el text. Si
  una certificacio concreta val com a equivalent d'una altra ho decideix el
  compliment o una persona, no l'extractor.
* **El que no encaixa no es perd.** `GenericValue` accepta qualsevol objecte, de
  manera que un cas no previst arriba sencer al domini en comptes de ser
  descartat.

Es un modul pur: no toca base de dades ni HTTP.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AggregationType,
    EconomicMetric,
    InsuranceLimitKind,
    InsuranceType,
    PercentageBase,
    ProofType,
    ReferencePeriodType,
    RequirementOperator,
)

#: Versio del contracte de valors. Es desa a `Requirement.value_schema`, de
#: manera que un run antic segueix sent interpretable amb les regles d'aleshores.
VALUE_SCHEMA_VERSION = "requirements-value-v2"


class ValueModel(BaseModel):
    """Base comuna: cap clau inventada, i tot opcional tret del que identifica."""

    model_config = ConfigDict(extra="forbid")


class ReferencePeriod(ValueModel):
    """Sobre quin periode es mesura. Sense periode, un llindar es ambigu."""

    type: ReferencePeriodType = ReferencePeriodType.UNKNOWN
    #: Nomes per a `last_n_years`.
    years: int | None = Field(default=None, ge=1, le=20)
    #: Nomes per a `explicit_range` o `fiscal_year`.
    from_year: int | None = None
    to_year: int | None = None
    note: str | None = None


class MonetaryAmount(ValueModel):
    amount: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    #: Alternativa a l'import absolut: un percentatge sobre una base.
    percentage: float | None = None
    percentage_base: PercentageBase = PercentageBase.UNKNOWN
    #: Text de la base quan no encaixa a l'enum, copiat del plec.
    percentage_base_note: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.amount is None and self.percentage is None


class EconomicSolvencyValue(ValueModel):
    """Solvencia economica amb periode i agregacio explicits.

    L'agregacio es el camp que la v1 no tenia i que el gold va trobar a faltar.
    Amb `aggregation=max` i `years=3` es representa exactament "el de major volum
    dels tres ultims"; amb `each_year`, "cadascun dels tres ultims".
    """

    kind: Literal["economic_solvency"] = "economic_solvency"
    metric: EconomicMetric = EconomicMetric.ANNUAL_TURNOVER
    operator: RequirementOperator = RequirementOperator.GTE
    threshold: MonetaryAmount = Field(default_factory=MonetaryAmount)
    reference_period: ReferencePeriod = Field(default_factory=ReferencePeriod)
    #: Com es combinen els anys del periode. `unknown` si el text no ho diu.
    aggregation: AggregationType = AggregationType.UNKNOWN
    #: Ambit al qual es limita el volum ("en el ambito del objeto del contrato").
    scope_note: str | None = None
    proof: ProofType = ProofType.UNKNOWN


class ExperienceValue(ValueModel):
    """Experiencia amb llindars quantitatius.

    Cobreix el cas que el gold va marcar com a perdut: "import anual acumulat de
    serveis similars en l'any de major execucio >= 70% de l'anualitat mitjana",
    que necessita alhora percentatge, base, agregacio i periode.
    """

    kind: Literal["experience"] = "experience"
    work_type: str | None = None
    reference_period: ReferencePeriod = Field(default_factory=ReferencePeriod)
    #: Nombre minim de contractes/treballs.
    min_count: int | None = Field(default=None, ge=1)
    #: Import minim de CADA contracte individual.
    min_individual_amount: MonetaryAmount | None = None
    #: Import minim de la SUMA. No es el mateix que l'anterior.
    min_accumulated_amount: MonetaryAmount | None = None
    aggregation: AggregationType = AggregationType.UNKNOWN
    cpv_codes: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    similarity_note: str | None = None
    proof: ProofType = ProofType.UNKNOWN


class InsuranceLimit(ValueModel):
    """Un limit concret d'una polissa. N'hi pot haver diversos alhora."""

    kind: InsuranceLimitKind = InsuranceLimitKind.UNKNOWN
    amount: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    note: str | None = None


class InsuranceValue(ValueModel):
    """Polissa amb els seus limits separats.

    Cobertura total i sublimit per victima son condicions diferents sobre la
    mateixa polissa: es guarden com a entrades diferents de `limits` perque
    fusionar-les perdria la mes restrictiva.
    """

    kind: Literal["insurance"] = "insurance"
    insurance_type: InsuranceType | None = None
    insurance_type_note: str | None = None
    limits: list[InsuranceLimit] = Field(default_factory=list)
    #: Fins quan ha d'estar vigent, tal com ho diu el plec.
    validity_note: str | None = None
    must_be_valid_through_contract: bool | None = None
    #: El plec exigeix incloure l'organ de contractacio com a assegurat.
    additional_insured: bool | None = None
    proof: ProofType = ProofType.UNKNOWN

    def limit_for(self, kind: InsuranceLimitKind) -> InsuranceLimit | None:
        return next((limit for limit in self.limits if limit.kind == kind), None)


class ClassificationValue(ValueModel):
    """Classificacio empresarial: grup, subgrup i categoria.

    Sovint apareix com a via ALTERNATIVA d'acreditacio ("podra acreditarse
    mediante clasificacion..."). Aixo no ho decideix aquest model: ho diu la
    modalitat del requisit i el seu grup d'alternatives.
    """

    kind: Literal["classification"] = "classification"
    group: str | None = None
    subgroup: str | None = None
    category: str | None = None
    registry: str | None = None
    equivalence_allowed: bool = False
    proof: ProofType = ProofType.UNKNOWN


class CertificationValue(ValueModel):
    kind: Literal["certification"] = "certification"
    name: str | None = None
    issuer: str | None = None
    scope_note: str | None = None
    #: "ISO 9001 o equivalent". Quina certificacio concreta hi equival NO ho
    #: decideix l'extractor: es una decisio de compliment o d'una persona.
    equivalence_allowed: bool = False
    must_be_valid_at_submission: bool | None = None
    proof: ProofType = ProofType.UNKNOWN


class PersonnelValue(ValueModel):
    kind: Literal["personnel"] = "personnel"
    min_people: int | None = Field(default=None, ge=1)
    role: str | None = None
    qualification: str | None = None
    experience_years: int | None = Field(default=None, ge=0)
    equivalence_allowed: bool = False
    proof: ProofType = ProofType.UNKNOWN


class GenericValue(ValueModel):
    """El que no encaixa enlloc. Arriba sencer en comptes de perdre's."""

    model_config = ConfigDict(extra="allow")

    kind: Literal["generic"] = "generic"
    note: str | None = None


#: Els models tipats, indexats per `kind`.
VALUE_MODELS: dict[str, type[ValueModel]] = {
    "economic_solvency": EconomicSolvencyValue,
    "experience": ExperienceValue,
    "insurance": InsuranceValue,
    "classification": ClassificationValue,
    "certification": CertificationValue,
    "personnel": PersonnelValue,
    "generic": GenericValue,
}


def parse_value(raw: dict[str, Any] | None) -> ValueModel | None:
    """Valida un valor v2 i el retorna tipat.

    Un `kind` desconegut o una forma que no valida NO fan caure l'extraccio:
    cauen a `GenericValue`, que ho conserva tot. Perdre el valor seria pitjor
    que tenir-lo sense tipus.
    """
    if not raw:
        return None
    kind = str(raw.get("kind") or "generic")
    model = VALUE_MODELS.get(kind, GenericValue)
    try:
        return model.model_validate(raw)
    except Exception:  # noqa: BLE001 - qualsevol error de forma cau al generic
        payload = {k: v for k, v in raw.items() if k != "kind"}
        return GenericValue(note=None, **payload)


def dump_value(value: ValueModel | None) -> dict[str, Any] | None:
    """Serialitza per desar a `value_json`. Sense `None`s: soroll inutil."""
    if value is None:
        return None
    return value.model_dump(mode="json", exclude_none=True)
