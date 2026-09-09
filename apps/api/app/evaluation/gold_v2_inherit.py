"""Portar les anotacions ja verificades a l'esquema v2 sense reinterpretar-les.

Una anotacio verificada val perque una persona la va comparar amb la pagina. Si
en canviar d'esquema li afegissim el que la v2 sap representar —l'agregacio que
es llegeix al quote, el tipus de limit d'una polissa—, estariem posant sota una
signatura humana coses que aquella persona no va mirar. La verificacio deixaria
de significar el que diu.

Per aixo aqui nomes hi ha **renoms**: `coverage_amount` passa a ser un element
de `limits`, `lookback_years` passa a ser un `reference_period`. Tot el que la
v1 no deia queda sense informar, i el motiu queda escrit a `unresolved` de
l'anotacio, que es el que despres fa que el report pugui dir `n/d` en comptes
d'un zero.

El cas que ho justifica millor: `26/095/3` te `coverage_amount: 1000000` i el
seu quote diu "por siniestro y año". Mapar-ho a `kind: total` seria comodissim i
seria fals.
"""

from __future__ import annotations

from typing import Any

from app.ai.value_schemas import VALUE_SCHEMA_VERSION
from app.evaluation.dataset import GoldAwardCriterion, GoldRequirement
from app.evaluation.dataset_v2 import (
    AnnotationOrigin,
    GoldAwardCriterionV2,
    GoldRequirementV2,
)
from app.models.enums import RequirementCategory, RequirementModality

#: Els camps que un requisit v1 ja portava i que es copien tal qual.
_CARRIED = (
    "gold_id",
    "annotation_status",
    "annotated_by",
    "reviewed_by",
    "reviewed_at",
    "review_note",
    "category",
    "obligation",
    "operator",
    "scope",
    "lot_labels",
    "document_name",
    "page_number",
    "quote",
    "critical",
    "notes",
)

#: Nota reutilitzada: explica per que un camp v2 queda buit en comptes de
#: deduir-se del quote.
_NOT_ANNOTATED = (
    "{field}: no consta a l'anotacio v1 verificada. Es deixa sense informar en "
    "comptes de deduir-lo del quote, que canviaria el que una persona va verificar."
)


def _period(years: Any) -> dict[str, Any]:
    if years:
        return {"type": "last_n_years", "years": int(years)}
    return {"type": "unknown"}


def _economic(value: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    threshold = {"amount": value.get("amount")}
    if value.get("currency"):
        threshold["currency"] = value["currency"]
    out: dict[str, Any] = {
        "kind": "economic_solvency",
        "threshold": threshold,
        "reference_period": _period(value.get("years")),
        # L'agregacio es exactament el que la v1 no tenia.
        "aggregation": "unknown",
    }
    if value.get("period") == "annual":
        out["metric"] = "annual_turnover"
    return out, [
        _NOT_ANNOTATED.format(field="aggregation"),
    ]


def _experience(value: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    out: dict[str, Any] = {
        "kind": "experience",
        "reference_period": _period(value.get("lookback_years")),
        "aggregation": "unknown",
    }
    if value.get("cpv_codes"):
        out["cpv_codes"] = list(value["cpv_codes"])
    if value.get("similarity_condition"):
        out["similarity_note"] = value["similarity_condition"]
    return out, [
        _NOT_ANNOTATED.format(field="aggregation"),
        "Llindars quantitatius (import individual o acumulat, percentatge i base): "
        "l'anotacio v1 no en portava cap.",
    ]


def _insurance(value: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    limit: dict[str, Any] = {
        # `coverage_amount` no diu de quin limit parla. A `26/095/3` el quote
        # diu "por siniestro y año", que NO es el limit total: suposar-ho seria
        # inventar-se la part mes restrictiva d'una polissa.
        "kind": "unknown",
        "amount": value.get("coverage_amount"),
    }
    if value.get("currency"):
        limit["currency"] = value["currency"]
    out: dict[str, Any] = {"kind": "insurance", "limits": [limit]}
    if value.get("type"):
        # El text del plec, no l'enum: classificar-lo seria una decisio nova.
        out["insurance_type_note"] = value["type"]
    return out, [
        "Tipus de limit: `coverage_amount` de la v1 no distingeix total, per "
        "sinistre, per victima o agregat anual. Queda `unknown`.",
        "Sublimits: si la pagina en te, l'anotacio v1 els va aplanar en un sol "
        "import i no es poden recuperar sense tornar a anotar el cas.",
    ]


def _generic(value: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """El que no encaixa en cap forma tipada arriba sencer.

    Passa amb `2025-04671::cert-1`, que enumera tres certificacions
    alternatives: la forma tipada `certification` en te una de sola, i partir
    una anotacio verificada en tres seria reescriure-la, no adaptar-la.
    """
    payload = {key: item for key, item in value.items() if item is not None}
    payload["kind"] = "generic"
    return payload, [
        "Valor conservat sense tipus: la forma v1 no encaixa en cap contracte "
        "tipat de la v2 sense reescriure l'anotacio."
    ]


_ADAPTERS = {
    RequirementCategory.ECONOMIC_SOLVENCY: _economic,
    RequirementCategory.TECHNICAL_SOLVENCY: _experience,
    RequirementCategory.EXPERIENCE: _experience,
    RequirementCategory.INSURANCE: _insurance,
}


def adapt_requirement(item: GoldRequirement) -> GoldRequirementV2:
    """Un requisit v1 llegit amb l'esquema v2. Cap dada nova.

    L'estat de revisio es conserva: una anotacio verificada segueix verificada
    perque el que se n'afirma no ha canviat.
    """
    data = {name: getattr(item, name) for name in _CARRIED}
    value = item.value or {}
    unresolved: list[str] = []
    typed: dict[str, Any] | None = None

    if value:
        # Les categories sense forma tipada equivalent cauen al generic, que ho
        # conserva tot: `2025-04671::cert-1` n'es el cas.
        typed, unresolved = _ADAPTERS.get(item.category, _generic)(value)

    equivalence = bool(value.get("equivalents_allowed") or value.get("equivalence_allowed"))
    if not equivalence:
        unresolved.append(
            "equivalence_allowed: la v1 nomes ho deia en algun cas. `False` aqui vol dir "
            "'no anotat', no 'el plec no admet equivalents'."
        )

    return GoldRequirementV2(
        **data,
        origin=AnnotationOrigin.INHERITED,
        # SEMPRE `unknown`: la v1 no registrava la modalitat, i llegir-la del
        # quote seria afegir informacio a una anotacio ja verificada.
        modality=RequirementModality.UNKNOWN,
        equivalence_allowed=equivalence,
        value=typed,
        value_schema=VALUE_SCHEMA_VERSION if typed else None,
        unresolved=[
            "modality: la v1 no distingia exigencia de via alternativa; queda `unknown`.",
            *unresolved,
        ],
    )


def adapt_award_criterion(item: GoldAwardCriterion) -> GoldAwardCriterionV2:
    """Un criteri d'adjudicacio v1 llegit amb l'esquema v2."""
    unresolved = []
    if not item.formula:
        unresolved.append(
            "formula: l'anotacio v1 no en portava cap. No es dedueix del quote."
        )
    return GoldAwardCriterionV2(
        gold_id=item.gold_id,
        annotation_status=item.annotation_status,
        annotated_by=item.annotated_by,
        reviewed_by=item.reviewed_by,
        reviewed_at=item.reviewed_at,
        review_note=item.review_note,
        name=item.name,
        points=item.points,
        weight_percent=item.weight_percent,
        evaluation_type=item.evaluation_type,
        formula=item.formula,
        document_name=item.document_name,
        page_number=item.page_number,
        quote=item.quote,
        notes=item.notes,
        origin=AnnotationOrigin.INHERITED,
        # `criterion_type` i `formula_text` no existien a la v1: queden buits.
        unresolved=[
            "criterion_type: no consta a l'anotacio v1; queda `unknown`.",
            *unresolved,
        ],
    )
