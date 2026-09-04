"""Dimensio economica.

Compara l'import de la licitacio (o del lot) amb la capacitat contractual que
l'empresa ha declarat.

Cal ser molt precis amb el llenguatge: aixo NO es solvencia. La solvencia
economica l'exigeix el plec i el plec encara no s'ha llegit. Si un import supera
el maxim assumible, el motiu no es "no compleix solvencia" sino "supera el maxim
assumible configurat per l'empresa": es una decisio de capacitat interna.
"""

from __future__ import annotations

from decimal import Decimal

from app.matching.profile import CompanyMatchProfile
from app.matching.rules import (
    AMOUNT_FACTORS,
    WEIGHT_ECONOMIC,
    DimensionOutcome,
    Factor,
    ReasonCode,
)
from app.models.enums import DimensionResult


def evaluate(profile: CompanyMatchProfile, amount: Decimal | None) -> DimensionOutcome:
    capacity = profile.capacity

    if not capacity.is_configured:
        return DimensionOutcome(
            Factor(
                dimension="economic",
                result=DimensionResult.UNKNOWN,
                reason_code=ReasonCode.AMOUNT_NO_COMPANY_PREFERENCES,
                weight=WEIGHT_ECONOMIC,
                score=None,
            )
        )
    if amount is None:
        # La font no publica import per a aquesta licitacio o lot. No inventem
        # cap valor ni el substituim pel del expedient sencer.
        return DimensionOutcome(
            Factor(
                dimension="economic",
                result=DimensionResult.UNKNOWN,
                reason_code=ReasonCode.AMOUNT_UNKNOWN,
                weight=WEIGHT_ECONOMIC,
                score=None,
            )
        )

    value = Decimal(amount)
    formatted = f"{value:,.0f}".replace(",", ".")

    def outcome(
        reason: str, factor_key: str, result: DimensionResult, excluded: bool = False
    ) -> DimensionOutcome:
        return DimensionOutcome(
            Factor(
                dimension="economic",
                result=result,
                reason_code=reason,
                weight=WEIGHT_ECONOMIC,
                score=WEIGHT_ECONOMIC * AMOUNT_FACTORS[factor_key] if not excluded else 0.0,
                company_value=_capacity_summary(profile),
                source_value=formatted,
            ),
            excluded=excluded,
        )

    # Exclusio de capacitat: per sobre del que l'empresa diu que pot assumir.
    if capacity.maximum_assumable is not None and value > capacity.maximum_assumable:
        return DimensionOutcome(
            Factor(
                dimension="economic",
                result=DimensionResult.NO_MATCH,
                reason_code=ReasonCode.AMOUNT_ABOVE_MAXIMUM,
                weight=WEIGHT_ECONOMIC,
                score=0.0,
                company_value=_capacity_summary(profile),
                source_value=formatted,
                detail={"maximum_assumable": str(capacity.maximum_assumable)},
            ),
            excluded=True,
        )

    if capacity.maximum_recommended is not None and value > capacity.maximum_recommended:
        return outcome(
            ReasonCode.AMOUNT_ABOVE_RECOMMENDED, "above_recommended", DimensionResult.PARTIAL
        )

    if capacity.preferred_max is not None and value > capacity.preferred_max:
        return outcome(
            ReasonCode.AMOUNT_ABOVE_PREFERRED, "above_preferred", DimensionResult.PARTIAL
        )

    if capacity.minimum_interesting is not None and value < capacity.minimum_interesting:
        return outcome(ReasonCode.AMOUNT_BELOW_MINIMUM, "below_minimum", DimensionResult.PARTIAL)

    within_preferred = (
        capacity.preferred_min is None or value >= capacity.preferred_min
    ) and (capacity.preferred_max is None or value <= capacity.preferred_max)
    if within_preferred and capacity.preferred_min is not None:
        return outcome(ReasonCode.AMOUNT_PREFERRED, "preferred", DimensionResult.MATCH)

    # Per sobre del minim interessant pero per sota del rang preferit.
    return outcome(ReasonCode.AMOUNT_INTERESTING, "interesting", DimensionResult.MATCH)


def _capacity_summary(profile: CompanyMatchProfile) -> str:
    capacity = profile.capacity

    def euros(value: Decimal | None) -> str:
        return f"{value:,.0f}".replace(",", ".") if value is not None else "-"

    return (
        f"min {euros(capacity.minimum_interesting)} · "
        f"pref {euros(capacity.preferred_min)}-{euros(capacity.preferred_max)} · "
        f"max {euros(capacity.maximum_assumable)}"
    )
