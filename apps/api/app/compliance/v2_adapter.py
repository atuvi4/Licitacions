"""Pont entre els requisits v2 i les regles de compliment, que son v1.

El motor de 4C sap comparar un llindar amb una dada del perfil. No sap decidir
entre dues vies d'acreditacio, ni que compta com a equivalent, ni que vol dir
"el de major volum dels tres ultims" quan el perfil nomes te la facturacio any
per any.

Aquest modul fa dues coses i cap mes:

* **Detecta el que la regla NO pot resoldre** i ho diu amb un codi. Aleshores el
  motor retorna `needs_review`, que demana una mirada humana, en comptes de
  fabricar un `satisfies` o —pitjor— un `does_not_satisfy` que es convertiria en
  un blocador fals.
* **Tradueix el que SI que es equivalent.** Un `threshold.amount` v2 amb
  agregacio `each_year` diu exactament el mateix que un `{"amount": ...}` v1: si
  no es traduis, activar la v2 deixaria el compliment sense veredictes.

La frontera entre les dues coses es explicita i esta escrita aqui, no repartida
per condicionals dins de cada regla.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.value_schemas import VALUE_SCHEMA_VERSION
from app.compliance.rules import ExplanationCode
from app.models.enums import (
    AggregationType,
    EconomicMetric,
    InsuranceLimitKind,
    RequirementCategory,
    RequirementModality,
)

#: Agregacions que una regla "compara el llindar amb el que te l'empresa" pot
#: resoldre sense inventar-se res. `unknown` hi entra perque el text no deia res
#: i el comportament v1 ja era aquest.
SUPPORTED_AGGREGATIONS = frozenset({AggregationType.EACH_YEAR, AggregationType.UNKNOWN})

#: Modalitats que no son una exigencia directa i que, per tant, cap regla no ha
#: de resoldre sola.
UNRESOLVABLE_MODALITIES = {
    RequirementModality.ALTERNATIVE: ExplanationCode.V2_ALTERNATIVE_PATH,
    RequirementModality.CONDITIONAL: ExplanationCode.V2_CONDITIONAL,
}


@dataclass(frozen=True, slots=True)
class Adaptation:
    """El resultat d'intentar llegir un requisit v2 amb ulls de v1."""

    #: Valor amb la forma que esperen les regles, o `None` si no es tradueix.
    value: dict[str, Any] | None
    #: Codi del motiu pel qual no es pot resoldre. `None` = es pot.
    unsupported: str | None = None

    @property
    def is_supported(self) -> bool:
        return self.unsupported is None


def _supported(value: dict[str, Any] | None) -> Adaptation:
    return Adaptation(value=value)


def _blocked(code: str) -> Adaptation:
    return Adaptation(value=None, unsupported=code)


def is_v2(requirement: Any) -> bool:
    return getattr(requirement, "value_schema", None) == VALUE_SCHEMA_VERSION


def adapt(requirement: Any) -> Adaptation:
    """Que en pot fer una regla, d'aquest requisit.

    L'ordre importa: primer el que fa que el requisit no sigui una exigencia
    directa (alternativa, condicio, equivalencia) i despres la forma del valor.
    Un requisit que es una alternativa no s'ha d'avaluar encara que el seu valor
    sigui perfectament llegible.
    """
    modality = getattr(requirement, "modality", RequirementModality.UNKNOWN)
    if modality in UNRESOLVABLE_MODALITIES:
        return _blocked(UNRESOLVABLE_MODALITIES[modality])
    if getattr(requirement, "alternative_group", None):
        return _blocked(ExplanationCode.V2_ALTERNATIVE_PATH)
    if getattr(requirement, "equivalence_allowed", False):
        return _blocked(ExplanationCode.V2_EQUIVALENCE_ALLOWED)

    if not is_v2(requirement):
        # Requisit v1: el valor ja te la forma que les regles esperen.
        return _supported(getattr(requirement, "value_json", None))

    value = getattr(requirement, "value_json", None) or {}
    kind = str(value.get("kind") or "generic")
    adapter = _ADAPTERS.get(kind)
    if adapter is None:
        return _blocked(ExplanationCode.V2_VALUE_NOT_INTERPRETABLE)
    return adapter(value)


# --------------------------------------------------------------------------- #
# Adaptadors per forma de valor                                                 #
# --------------------------------------------------------------------------- #


def _adapt_economic(value: dict[str, Any]) -> Adaptation:
    threshold = value.get("threshold") or {}
    if threshold.get("percentage") is not None:
        # "el 70% de l'anualitat mitjana": caldria coneixer la base del contracte
        # i aplicar-hi el percentatge. La regla no ho fa, i estimar-ho seria
        # inventar-se el llindar.
        return _blocked(ExplanationCode.V2_PERCENTAGE_THRESHOLD)

    aggregation = AggregationType(value.get("aggregation") or AggregationType.UNKNOWN.value)
    if aggregation not in SUPPORTED_AGGREGATIONS:
        # `max`, `average` i `cumulative` demanen combinar exercicis abans de
        # comparar. La regla compara any a any; dir-ne res mes seria fals.
        return _blocked(ExplanationCode.V2_AGGREGATION_UNSUPPORTED)

    metric = EconomicMetric(value.get("metric") or EconomicMetric.ANNUAL_TURNOVER.value)
    if metric not in (EconomicMetric.ANNUAL_TURNOVER, EconomicMetric.SPECIFIC_TURNOVER):
        return _blocked(ExplanationCode.V2_VALUE_NOT_INTERPRETABLE)

    period = value.get("reference_period") or {}
    return _supported(
        {
            "amount": threshold.get("amount"),
            "currency": threshold.get("currency"),
            "period": "annual",
            "years": period.get("years"),
        }
    )


def _adapt_experience(value: dict[str, Any]) -> Adaptation:
    for key in ("min_individual_amount", "min_accumulated_amount"):
        amount = value.get(key) or {}
        if amount.get("percentage") is not None:
            return _blocked(ExplanationCode.V2_PERCENTAGE_THRESHOLD)
    if value.get("min_accumulated_amount"):
        # Un import acumulat exigeix sumar contractes del periode; la regla
        # actual compta contractes i mira imports individuals.
        return _blocked(ExplanationCode.V2_AGGREGATION_UNSUPPORTED)

    aggregation = AggregationType(value.get("aggregation") or AggregationType.UNKNOWN.value)
    if aggregation not in SUPPORTED_AGGREGATIONS:
        return _blocked(ExplanationCode.V2_AGGREGATION_UNSUPPORTED)

    individual = value.get("min_individual_amount") or {}
    period = value.get("reference_period") or {}
    return _supported(
        {
            "minimum_projects": value.get("min_count"),
            "minimum_amount": individual.get("amount"),
            "currency": individual.get("currency"),
            "lookback_years": period.get("years"),
            "cpv_codes": value.get("cpv_codes") or [],
            "services": value.get("categories") or [],
            "similarity_condition": value.get("similarity_note"),
        }
    )


def _adapt_insurance(value: dict[str, Any]) -> Adaptation:
    limits = value.get("limits") or []
    usable = [limit for limit in limits if limit.get("amount") is not None]
    if not usable:
        return _supported({"type": value.get("insurance_type")})

    kinds = {limit.get("kind") for limit in usable}
    if kinds - {InsuranceLimitKind.TOTAL.value, InsuranceLimitKind.UNKNOWN.value}:
        # Hi ha sublimits (per victima, per sinistre, agregat anual). El perfil
        # corporatiu nomes guarda la cobertura, aixi que comparar-ne nomes el
        # total diria que es compleix quan potser no.
        return _blocked(ExplanationCode.V2_INSURANCE_SUBLIMITS)
    if len(usable) > 1:
        return _blocked(ExplanationCode.V2_INSURANCE_SUBLIMITS)

    limit = usable[0]
    return _supported(
        {
            "coverage_amount": limit.get("amount"),
            "currency": limit.get("currency"),
            "type": value.get("insurance_type") or value.get("insurance_type_note"),
        }
    )


def _adapt_certification(value: dict[str, Any]) -> Adaptation:
    return _supported({"name": value.get("name")})


def _adapt_classification(value: dict[str, Any]) -> Adaptation:
    return _supported(
        {
            "name": value.get("registry"),
            "type": value.get("registry"),
            "group": value.get("group"),
            "subgroup": value.get("subgroup"),
            "category": value.get("category"),
        }
    )


def _adapt_personnel(value: dict[str, Any]) -> Adaptation:
    return _supported(
        {
            "minimum_people": value.get("min_people"),
            "name": value.get("qualification"),
            "qualification": value.get("qualification"),
        }
    )


_ADAPTERS = {
    "economic_solvency": _adapt_economic,
    "experience": _adapt_experience,
    "insurance": _adapt_insurance,
    "certification": _adapt_certification,
    "classification": _adapt_classification,
    "personnel": _adapt_personnel,
}


#: Categories on una equivalencia o una alternativa son especialment perilloses:
#: son les que generen blocadors.
BLOCKING_CATEGORIES = frozenset(
    {
        RequirementCategory.CERTIFICATION,
        RequirementCategory.REGISTRATION,
        RequirementCategory.BUSINESS_CLASSIFICATION,
        RequirementCategory.ECONOMIC_SOLVENCY,
        RequirementCategory.TECHNICAL_SOLVENCY,
        RequirementCategory.EXPERIENCE,
        RequirementCategory.INSURANCE,
    }
)
