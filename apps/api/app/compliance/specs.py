"""El requisit, llegit d'una sola manera tant si es v1 com si es v2.

Les regles de 4C no han de saber amb quin extractor es va treure un requisit.
Si ho sabessin, cada regla tindria dues branques i les dues aniran divergint,
que es exactament el que ja va passar amb la resolucio de tenders al benchmark.

Aqui es converteix qualsevol de les dues formes en una estructura tipada:

* el valor lliure de la v1 (`{"amount": 60750, "years": 3}`);
* el valor tipat de la v2 (`app.ai.value_schemas`), que a mes distingeix
  l'agregacio, la base d'un percentatge i els limits d'una polissa.

El que la v1 no deia arriba com a `unknown`, no com a valor per defecte. Aquesta
diferencia es tot el sentit del modul: `aggregation=unknown` vol dir "el plec no
ho concreta i s'ha de resoldre prudentment", mentre que `each_year` vol dir "el
plec exigeix que ho compleixin tots els exercicis". Confondre-les canvia
veredictes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai.value_schemas import VALUE_SCHEMA_VERSION
from app.models.enums import (
    AggregationType,
    EconomicMetric,
    InsuranceLimitKind,
    InsuranceType,
    PercentageBase,
    ProofType,
    RequirementModality,
    RequirementObligation,
    RequirementOperator,
)

#: Periodes de la v1 que volen dir "anual". Qualsevol altre text es ambigu.
ANNUAL_PERIODS = frozenset({None, "annual", "anual", "yearly", "year"})


def to_decimal(value: Any) -> Decimal | None:
    """Decimal o `None`. Mai una excepcio: una dada rara no tomba una avaluacio."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _currency(raw: Any, default: str = "EUR") -> str:
    text = str(raw or default).strip().upper()
    return text or default


def is_v2(requirement: Any) -> bool:
    return getattr(requirement, "value_schema", None) == VALUE_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# Peces compartides                                                             #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Threshold:
    """Un llindar economic: un import, un percentatge sobre una base, o tots dos.

    Quan el plec dona les dues formes —"el 70% de l'anualitat mitjana, es a dir
    21.406,71 EUR"— es conserven totes dues i mana l'import: ja esta calculat pel
    propi organ i no cal deduir cap base.
    """

    amount: Decimal | None = None
    currency: str = "EUR"
    percentage: Decimal | None = None
    percentage_base: PercentageBase = PercentageBase.UNKNOWN

    @property
    def is_empty(self) -> bool:
        return self.amount is None and self.percentage is None

    @property
    def needs_base(self) -> bool:
        """Cert quan nomes hi ha percentatge i cal resoldre'n la base."""
        return self.amount is None and self.percentage is not None

    @classmethod
    def from_v2(cls, raw: Any, default_currency: str = "EUR") -> Threshold | None:
        if not isinstance(raw, dict):
            return None
        threshold = cls(
            amount=to_decimal(raw.get("amount")),
            currency=_currency(raw.get("currency"), default_currency),
            percentage=to_decimal(raw.get("percentage")),
            percentage_base=_enum(
                PercentageBase, raw.get("percentage_base"), PercentageBase.UNKNOWN
            ),
        )
        return None if threshold.is_empty else threshold


def _enum(enum_cls: Any, raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    try:
        return enum_cls(str(raw))
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# Modalitat i obligatorietat efectiva                                           #
# --------------------------------------------------------------------------- #

#: Modalitats que MAI son una exigencia directa. Una via alternativa o una
#: condicio no avaluada no poden bloquejar per si soles.
NON_BLOCKING_MODALITIES = frozenset(
    {
        RequirementModality.ALTERNATIVE,
        RequirementModality.CONDITIONAL,
        RequirementModality.OPTIONAL,
        RequirementModality.INFORMATIONAL,
    }
)


def is_effectively_mandatory(requirement: Any) -> bool:
    """Si aquest requisit, tot sol, pot arribar a bloquejar.

    Un requisit v1 no porta modalitat i es comporta com sempre: mana
    l'obligatorietat. Un requisit v2 amb modalitat `alternative` o `conditional`
    no bloqueja encara que l'obligatorietat digui `mandatory`, perque el que
    obliga no es aquesta branca sino la disjuncio sencera.
    """
    if getattr(requirement, "obligation", None) != RequirementObligation.MANDATORY:
        return False
    modality = getattr(requirement, "modality", RequirementModality.UNKNOWN)
    return modality not in NON_BLOCKING_MODALITIES


# --------------------------------------------------------------------------- #
# Solvencia economica                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EconomicSpec:
    operator: RequirementOperator
    threshold: Threshold
    metric: EconomicMetric = EconomicMetric.ANNUAL_TURNOVER
    #: Quants exercicis mira el plec. `None` = no ho diu.
    years: int | None = None
    aggregation: AggregationType = AggregationType.UNKNOWN
    #: Cert quan el periode de la v1 no es reconeixia com a anual.
    period_ambiguous: bool = False


def read_economic(requirement: Any) -> EconomicSpec | None:
    """Llegeix el llindar economic. `None` si el requisit no en porta cap."""
    value = getattr(requirement, "value_json", None) or {}
    operator = getattr(requirement, "operator", None) or RequirementOperator.GTE

    if is_v2(requirement) and value.get("kind") == "economic_solvency":
        threshold = Threshold.from_v2(value.get("threshold"))
        if threshold is None:
            return None
        period = value.get("reference_period") or {}
        return EconomicSpec(
            operator=_enum(RequirementOperator, value.get("operator"), operator),
            threshold=threshold,
            metric=_enum(EconomicMetric, value.get("metric"), EconomicMetric.ANNUAL_TURNOVER),
            years=period.get("years") if period.get("type") == "last_n_years" else None,
            aggregation=_enum(
                AggregationType, value.get("aggregation"), AggregationType.UNKNOWN
            ),
        )

    amount = to_decimal(value.get("amount"))
    if amount is None:
        return None
    period = value.get("period")
    if isinstance(period, str):
        period = period.strip().casefold()
    years = value.get("years")
    return EconomicSpec(
        operator=operator,
        threshold=Threshold(amount=amount, currency=_currency(value.get("currency"))),
        years=years if isinstance(years, int) and years > 0 else None,
        # La v1 no distingia l'agregacio. `unknown` es la resposta correcta.
        aggregation=AggregationType.UNKNOWN,
        period_ambiguous=period not in ANNUAL_PERIODS,
    )


# --------------------------------------------------------------------------- #
# Experiencia                                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExperienceSpec:
    years: int | None = None
    min_count: int | None = None
    #: Import minim de CADA contracte.
    min_individual: Threshold | None = None
    #: Import minim de la SUMA. No es el mateix.
    min_accumulated: Threshold | None = None
    aggregation: AggregationType = AggregationType.UNKNOWN
    cpv_codes: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    similarity_note: str | None = None

    @property
    def has_threshold(self) -> bool:
        return bool(self.min_count or self.min_individual or self.min_accumulated)


def read_experience(requirement: Any) -> ExperienceSpec:
    value = getattr(requirement, "value_json", None) or {}

    if is_v2(requirement) and value.get("kind") == "experience":
        period = value.get("reference_period") or {}
        return ExperienceSpec(
            years=period.get("years") if period.get("type") == "last_n_years" else None,
            min_count=value.get("min_count"),
            min_individual=Threshold.from_v2(value.get("min_individual_amount")),
            min_accumulated=Threshold.from_v2(value.get("min_accumulated_amount")),
            aggregation=_enum(
                AggregationType, value.get("aggregation"), AggregationType.UNKNOWN
            ),
            cpv_codes=tuple(
                str(c).strip() for c in (value.get("cpv_codes") or []) if str(c).strip()
            ),
            services=tuple(
                str(s).strip().casefold() for s in (value.get("categories") or []) if str(s).strip()
            ),
            similarity_note=value.get("similarity_note"),
        )

    minimum_each = to_decimal(value.get("minimum_amount"))
    if minimum_each is None:
        minimum_each = to_decimal(value.get("minimum_amount_each"))
    currency = _currency(value.get("currency"))
    lookback = value.get("lookback_years")
    count = value.get("minimum_projects")
    return ExperienceSpec(
        years=lookback if isinstance(lookback, int) and lookback > 0 else None,
        min_count=count if isinstance(count, int) and count > 0 else None,
        min_individual=(
            Threshold(amount=minimum_each, currency=currency) if minimum_each is not None else None
        ),
        cpv_codes=tuple(str(c).strip() for c in (value.get("cpv_codes") or []) if str(c).strip()),
        services=tuple(
            str(s).strip().casefold() for s in (value.get("services") or []) if str(s).strip()
        ),
        similarity_note=value.get("similarity_condition"),
    )


# --------------------------------------------------------------------------- #
# Assegurances                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LimitSpec:
    kind: InsuranceLimitKind
    amount: Decimal
    currency: str = "EUR"


#: Com anomena un plec cada TIPUS DE LIMIT. No es el tipus de polissa: una
#: mateixa responsabilitat civil te un limit total i sublimits per victima o per
#: sinistre, i confondre'ls es el que feia semblar coberta una empresa que
#: nomes tenia una xifra global.
#:
#: Llista tancada i inequivoca a proposit. El que no hi encaixi NO es converteix
#: en cobertura generica en silenci: es marca com a ambigu i acaba en revisio.
_LIMIT_KIND_HINTS: tuple[tuple[re.Pattern[str], InsuranceLimitKind], ...] = (
    (
        re.compile(r"per[_\s-]*v[ií]ctim|por[_\s-]*v[ií]ctim|per[_\s-]*victim", re.I),
        InsuranceLimitKind.PER_VICTIM,
    ),
    (
        re.compile(r"per[_\s-]*sinistr|por[_\s-]*siniestr|per[_\s-]*claim", re.I),
        InsuranceLimitKind.PER_CLAIM,
    ),
    (
        re.compile(r"agregat[_\s-]*anual|agregado[_\s-]*anual|annual[_\s-]*aggregate", re.I),
        InsuranceLimitKind.ANNUAL_AGGREGATE,
    ),
    (
        re.compile(r"l[ií]mit[_\s-]*total|limite[_\s-]*total|total[_\s-]*limit|\btotal\b", re.I),
        InsuranceLimitKind.TOTAL,
    ),
)

#: Text que anuncia un limit PER UNITAT sense que cap patro de sobre l'hagi
#: sabut llegir. Es el cas perillos: el qualificador hi es —el plec no parla de
#: la xifra global— pero no el podem mapar, i tractar-lo com a cobertura
#: generica el compararia contra el total de la polissa.
#:
#: Es deliberadament ample: val mes demanar una revisio de mes que deixar passar
#: un sublimit disfressat de cobertura total.
_SUBLIMIT_HINT = re.compile(
    r"sub[_\s-]*l[ií]mit"
    r"|sublimite"
    r"|\bpor\s+(?:cada\s+)?(?:v[ií]ctima|siniestro|asegurado|ocurrencia)"
    r"|\bper\s+(?:cada\s+)?(?:v[ií]ctima|sinistre|assegurat)"
    r"|\bper\s+(?:victim|claim|occurrence)",
    re.I,
)


def read_limit_kind(raw: str | None) -> tuple[InsuranceLimitKind, bool]:
    """El tipus de limit que declara un text lliure, i si ha quedat ambigu.

    Retorna `(kind, ambiguous)`. `ambiguous` cert vol dir que el text anuncia un
    sublimit pero no diu quin: llavors no es pot comparar amb res i el veredicte
    honest es demanar revisio, no assumir que parlem del limit total.
    """
    if not raw:
        return InsuranceLimitKind.UNKNOWN, False
    text = str(raw)
    for pattern, kind in _LIMIT_KIND_HINTS:
        if pattern.search(text):
            return kind, False
    if _SUBLIMIT_HINT.search(text):
        return InsuranceLimitKind.UNKNOWN, True
    return InsuranceLimitKind.UNKNOWN, False


@dataclass(frozen=True, slots=True)
class InsuranceSpec:
    limits: tuple[LimitSpec, ...] = ()
    insurance_type: InsuranceType | None = None
    type_note: str | None = None
    #: El plec exigeix incloure l'organ com a assegurat addicional.
    additional_insured_required: bool = False
    #: Condicions estructurades que el plec imposa i que cap regla comprova
    #: encara. Existeixen com a llista i no com a booleans solts perque el que
    #: importa es que NO se'n pugui perdre cap: si en queda una sense mirar, el
    #: veredicte no pot ser `satisfies` per molt que els imports encaixin.
    unchecked_conditions: tuple[str, ...] = ()
    #: El plec parla d'un sublimit pero no diu de quin. No es pot comparar amb
    #: cap limit declarat, i convertir-ho en cobertura generica seria justament
    #: l'aplanament que aquest modul existeix per evitar.
    ambiguous_limit: str | None = None
    #: El valor surt del complement `product-v2` (`app.compliance.normalize`). Nomes
    #: llavors el sublimit patronal i el generic tampoc no els respon la xifra
    #: global (v5); per a tot altre cami, la regla de la v4 sense canvis.
    normalized: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.limits and not self.additional_insured_required


def read_insurance(requirement: Any) -> InsuranceSpec:
    value = getattr(requirement, "value_json", None) or {}

    if is_v2(requirement) and value.get("kind") == "insurance":
        limits = []
        for raw in value.get("limits") or []:
            amount = to_decimal(raw.get("amount"))
            if amount is None:
                continue
            limits.append(
                LimitSpec(
                    kind=_enum(
                        InsuranceLimitKind, raw.get("kind"), InsuranceLimitKind.UNKNOWN
                    ),
                    amount=amount,
                    currency=_currency(raw.get("currency")),
                )
            )
        return InsuranceSpec(
            limits=tuple(limits),
            insurance_type=_enum(InsuranceType, value.get("insurance_type"), None),
            type_note=value.get("insurance_type_note"),
            additional_insured_required=value.get("additional_insured") is True,
            unchecked_conditions=_unchecked_insurance_conditions(value),
            normalized=getattr(requirement, "_v5_normalized", False) is True,
        )

    amount = to_decimal(value.get("coverage_amount") or value.get("amount"))
    if amount is None:
        return InsuranceSpec(type_note=value.get("type"))

    # La v1 no tenia camp per al tipus de limit, pero el qualificador sovint hi
    # es igualment, escrit dins de `type` o de `limit_kind`
    # ("civil_liability_per_victim"). Perdre'l era el que feia que un sublimit
    # per victima es compares contra la xifra global de la polissa.
    raw_kind = value.get("limit_kind") or value.get("kind") or value.get("type")
    kind, ambiguous = read_limit_kind(raw_kind)
    return InsuranceSpec(
        limits=(
            LimitSpec(
                kind=kind,
                amount=amount,
                currency=_currency(value.get("currency")),
            ),
        ),
        type_note=value.get("type"),
        ambiguous_limit=str(raw_kind) if ambiguous else None,
    )


#: Condicions de `InsuranceValue` que cap regla determinista sap comprovar
#: encara, amb el nom que apareixera a l'explicacio. Es una llista tancada a
#: proposit: afegir un camp al contracte v2 sense decidir que se'n fa el deixaria
#: ignorat en silenci, i el veredicte diria que compleix sense haver-ho mirat.
UNCHECKED_INSURANCE_FIELDS: tuple[tuple[str, str], ...] = (
    # Caldria la data de fi d'execucio del contracte, que no es un camp de
    # l'expedient.
    ("must_be_valid_through_contract", "valid_through_contract"),
    # Text lliure sobre la vigencia ("durant el periode de garantia").
    ("validity_note", "validity_note"),
)


def _unchecked_insurance_conditions(value: dict[str, Any]) -> tuple[str, ...]:
    """Les condicions que el plec imposa i que aqui no es comproven."""
    pending = [
        label
        for field, label in UNCHECKED_INSURANCE_FIELDS
        if value.get(field) not in (None, False, "")
    ]
    proof = value.get("proof")
    if proof and str(proof) != ProofType.UNKNOWN.value:
        # El plec diu COM s'acredita. Que el document existeixi i sigui el que
        # toca no ho comprova aquesta capa; ho fa el dossier.
        pending.append(f"proof:{proof}")
    return tuple(pending)


# --------------------------------------------------------------------------- #
# Certificacions                                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CertificationSpec:
    #: Els noms acceptats. La v1 en podia enumerar diversos amb operador `one_of`.
    names: tuple[str, ...] = ()
    equivalence_allowed: bool = False
    valid_at_deadline: bool = False


def read_certification(requirement: Any) -> CertificationSpec:
    value = getattr(requirement, "value_json", None) or {}
    equivalence = bool(getattr(requirement, "equivalence_allowed", False))

    if is_v2(requirement) and value.get("kind") == "certification":
        name = (value.get("name") or "").strip()
        return CertificationSpec(
            names=(name,) if name else (),
            equivalence_allowed=equivalence or bool(value.get("equivalence_allowed")),
            valid_at_deadline=bool(value.get("must_be_valid_at_submission")),
        )

    names = [str(n).strip() for n in (value.get("names") or []) if str(n).strip()]
    single = (value.get("name") or "").strip()
    if single:
        names.insert(0, single)
    return CertificationSpec(
        names=tuple(dict.fromkeys(names)),
        equivalence_allowed=(
            equivalence
            or bool(value.get("equivalents_allowed"))
            or bool(value.get("equivalence_allowed"))
        ),
        valid_at_deadline=bool(value.get("valid_at_deadline")),
    )
