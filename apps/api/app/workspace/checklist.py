"""De requisit a tasca acreditativa. **Deterministic, sense cap LLM.**

Un requisit diu que exigeix el plec ("ISO 27001", "RC >= 1.000.000 EUR"). Una
checklist diu que ha de fer una persona ("Acreditar ISO 27001", "Aportar
polissa de RC >= 1.000.000 EUR"). La traduccio entre les dues coses es una
plantilla per categoria, no una redaccio: ha de sortir igual cada vegada, ha de
ser revisable llegint aquest fitxer i no pot costar ni un token.

Si el requisit no porta prou dades estructurades per formular la tasca, es cau
al titol del requisit tal com el va extreure 4B. Aixo es preferible a inventar
un text: el titol ja va passar per la revisio de requisits.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from app.models.enums import RequirementCategory, RequirementObligation, RequirementOperator

#: Simbol per operador. Els que no son comparacions no en tenen.
OPERATOR_SYMBOLS: dict[RequirementOperator, str] = {
    RequirementOperator.GTE: "≥",
    RequirementOperator.GT: ">",
    RequirementOperator.LTE: "≤",
    RequirementOperator.LT: "<",
    RequirementOperator.EQ: "=",
}

#: Categories que no generen cap tasca acreditativa: no hi ha res a aportar.
#: Un termini es una data del calendari, no un document.
SKIPPED_CATEGORIES = frozenset({RequirementCategory.DEADLINE})

#: Obligatorietats que generen checklist. Un requisit `informative` no obliga a
#: res i omplir-ne la llista amagaria el que si que cal fer.
CHECKLIST_OBLIGATIONS = frozenset(
    {RequirementObligation.MANDATORY, RequirementObligation.SCORED, RequirementObligation.UNKNOWN}
)


class RequirementLike(Protocol):
    """El minim que necessita la plantilla. Permet provar-la sense ORM."""

    id: Any
    category: RequirementCategory
    obligation: RequirementObligation
    operator: RequirementOperator | None
    title: str
    value_json: dict[str, Any] | None


def format_amount(value: Any, currency: str | None = None) -> str | None:
    """1000000 -> `1.000.000 EUR`. Format catala: punt de milers."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    quantized = amount.quantize(Decimal(1)) if amount == amount.to_integral_value() else amount
    whole = quantized == quantized.to_integral_value()
    text = f"{quantized:,.0f}" if whole else f"{quantized:,.2f}"
    text = text.replace(",", " ").replace(".", ",").replace(" ", ".")
    code = (currency or "EUR").strip().upper()
    symbol = "€" if code == "EUR" else code
    return f"{text} {symbol}"


def _threshold(value: dict[str, Any], operator: RequirementOperator | None) -> str | None:
    amount = value.get("coverage_amount")
    if amount is None:
        amount = value.get("amount")
    if amount is None:
        return None
    formatted = format_amount(amount, value.get("currency"))
    if formatted is None:
        return None
    symbol = OPERATOR_SYMBOLS.get(operator or RequirementOperator.GTE, "≥")
    return f"{symbol} {formatted}"


def _named(value: dict[str, Any]) -> str | None:
    for key in ("name", "qualification", "type"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _insurance_label(value: dict[str, Any]) -> str:
    """`civil_liability` -> `RC`. Sense pista, "responsabilitat" generica."""
    raw = f"{value.get('type') or ''}".strip().casefold()
    if "civil" in raw or raw in {"rc", "civil_liability"}:
        return "RC"
    if "professional" in raw:
        return "responsabilitat professional"
    if raw:
        return raw.replace("_", " ")
    return "responsabilitat civil"


def checklist_title(requirement: RequirementLike) -> str:
    """El text de la tasca. Mateixa entrada, mateixa sortida, sempre."""
    value = requirement.value_json or {}
    category = requirement.category
    title = (requirement.title or "").strip()

    if category == RequirementCategory.CERTIFICATION:
        name = _named(value)
        return f"Acreditar {name}" if name else f"Acreditar certificacio: {title}"

    if category == RequirementCategory.INSURANCE:
        threshold = _threshold(value, requirement.operator)
        label = _insurance_label(value)
        if threshold:
            return f"Aportar polissa {label} {threshold}"
        return f"Aportar polissa {label}"

    if category == RequirementCategory.ECONOMIC_SOLVENCY:
        threshold = _threshold(value, requirement.operator)
        if threshold:
            period = value.get("period")
            scope = " anual" if isinstance(period, str) and "annual" in period else ""
            return f"Acreditar volum de negoci{scope} {threshold}"
        return f"Acreditar solvencia economica: {title}"

    if category == RequirementCategory.TECHNICAL_SOLVENCY:
        threshold = _threshold(value, requirement.operator)
        if threshold:
            return f"Acreditar solvencia tecnica {threshold}"
        return f"Acreditar solvencia tecnica: {title}"

    if category == RequirementCategory.EXPERIENCE:
        minimum = value.get("minimum_projects")
        if isinstance(minimum, int) and minimum >= 1:
            plural = "contractes similars" if minimum > 1 else "contracte similar"
            return f"Acreditar {minimum} {plural}"
        return f"Acreditar experiencia: {title}"

    if category == RequirementCategory.REGISTRATION:
        name = _named(value)
        return f"Acreditar inscripcio a {name}" if name else f"Acreditar inscripcio: {title}"

    if category == RequirementCategory.BUSINESS_CLASSIFICATION:
        parts = [
            f"{value.get(key)}".strip()
            for key in ("group", "subgroup", "category")
            if value.get(key)
        ]
        if parts:
            return f"Acreditar classificacio empresarial {'-'.join(parts)}"
        return f"Acreditar classificacio empresarial: {title}"

    if category in (
        RequirementCategory.PROFESSIONAL_QUALIFICATION,
        RequirementCategory.PERSONNEL,
    ):
        name = _named(value)
        if name:
            return f"Acreditar personal amb {name}"
        minimum = value.get("minimum_people") or value.get("count")
        if isinstance(minimum, int) and minimum >= 1:
            return f"Acreditar {minimum} persones adscrites al contracte"
        return f"Acreditar personal: {title}"

    if category == RequirementCategory.EQUIPMENT_OR_RESOURCES:
        return f"Acreditar mitjans materials: {title}"

    if category == RequirementCategory.ADMINISTRATIVE_DOCUMENTATION:
        return f"Preparar {title}"

    if category == RequirementCategory.CONTRACT_CONDITION:
        return f"Revisar condicio d'execucio: {title}"

    return f"Preparar: {title}"


def generates_checklist_item(requirement: RequirementLike) -> bool:
    """Aquest requisit demana feina a algu?"""
    if requirement.category in SKIPPED_CATEGORIES:
        return False
    return requirement.obligation in CHECKLIST_OBLIGATIONS


def applies_to_workspace_lot(requirement: RequirementLike, tender_lot_id: Any) -> bool:
    """Un workspace de lot nomes hereta els requisits del seu lot i els generals.

    Es la mateixa regla que el motor de compliment: si el requisit no diu de quin
    lot es, val per a tots. Inventar-ne l'abast seria pitjor que aplicar-lo.
    """
    if tender_lot_id is None:
        return True
    requirement_lot = getattr(requirement, "tender_lot_id", None)
    return requirement_lot is None or requirement_lot == tender_lot_id
