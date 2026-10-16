"""Dimensio economica.

Compara l'import de la licitacio (o del lot) amb la capacitat contractual que
l'empresa ha declarat.

Cal ser molt precis amb el llenguatge: aixo NO es solvencia. La solvencia
economica l'exigeix el plec i el plec encara no s'ha llegit. Si un import supera
el maxim assumible, el motiu no es "no compleix solvencia" sino "supera el maxim
assumible configurat per l'empresa": es una decisio de capacitat interna.

## Quin import (v2, F-06b)

El VEC (art. 101 LCSP: pròrrogues i modificacions incloses, sense IVA) i el PBL
(art. 100: el maxim de l'oferta del contracte inicial) NO son el mateix. El PBL
sense IVA, pero, mai pot superar el VEC: n'es una **cota inferior**. Per aixo:

- amb VEC (> 0), mana el VEC, com a v1;
- sense VEC, el PBL nomes serveix per dir que el contracte SUPERA un llindar
  (si el PBL ja el supera, el VEC tambe); per sota d'un llindar no se'n treu res,
  i la dimensio queda desconeguda (`AMOUNT_BUDGET_LOWER_BOUND`);
- si la font publica un VEC inferior al PBL, les dades son incoherents i no se'n
  treu cap conclusio (`AMOUNT_INCOHERENT`);
- la base es desa sempre al factor (`detail.amount_basis`). Mai es dedueix un
  import d'un altre ni es calcula cap IVA. Un zero continua sent absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.matching.profile import CompanyMatchProfile
from app.matching.rules import (
    AMOUNT_FACTORS,
    WEIGHT_ECONOMIC,
    DimensionOutcome,
    Factor,
    ReasonCode,
)
from app.models.enums import DimensionResult


class AmountBasis(StrEnum):
    VEC = "vec"
    PBL_NET = "pbl_net"
    PBL_GROSS = "pbl_gross"


@dataclass(frozen=True, slots=True)
class AmountReference:
    """L'import de referencia d'una licitacio o d'un lot, amb la seva base."""

    value: Decimal | None
    basis: AmountBasis | None
    #: El PBL sense IVA quan supera el VEC publicat (dades incoherents).
    incoherent_budget: Decimal | None = None


def _positive(value: Decimal | None) -> Decimal | None:
    # Un zero (acords marc, contractes centralitzats) no es un import util: absent.
    return value if value is not None and value > 0 else None


def reference_amount(
    estimated_value: Decimal | None,
    base_budget_without_tax: Decimal | None,
    base_budget: Decimal | None,
) -> AmountReference:
    """VEC → PBL sense IVA → PBL amb IVA, el mateix ordre que el filtre del cataleg.

    Sempre del mateix ambit: la licitacio amb els seus imports, el lot amb els seus.
    """
    vec = _positive(estimated_value)
    net = _positive(base_budget_without_tax)
    gross = _positive(base_budget)
    if vec is not None:
        incoherent = net if net is not None and net > vec else None
        return AmountReference(vec, AmountBasis.VEC, incoherent_budget=incoherent)
    if net is not None:
        return AmountReference(net, AmountBasis.PBL_NET)
    if gross is not None:
        return AmountReference(gross, AmountBasis.PBL_GROSS)
    return AmountReference(None, None)


#: Els trams que una cota inferior pot demostrar: si el PBL ja supera el llindar,
#: el VEC tambe. Els altres (dins la franja, per sobre del minim, per sota del minim)
#: dependrien del VEC real, que pot ser mes alt.
_LOWER_BOUND_REASONS = frozenset(
    {
        ReasonCode.AMOUNT_ABOVE_MAXIMUM,
        ReasonCode.AMOUNT_ABOVE_RECOMMENDED,
        ReasonCode.AMOUNT_ABOVE_PREFERRED,
    }
)


def evaluate(
    profile: CompanyMatchProfile, amount: AmountReference | Decimal | None
) -> DimensionOutcome:
    """`amount` com a `Decimal` es un VEC (la crida de v1)."""
    reference = (
        amount
        if isinstance(amount, AmountReference)
        else AmountReference(amount, AmountBasis.VEC if amount is not None else None)
    )
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
    if reference.value is None:
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

    value = Decimal(reference.value)
    formatted = f"{value:,.0f}".replace(",", ".")
    basis = reference.basis or AmountBasis.VEC
    detail_basis: dict[str, object] = {"amount_basis": basis.value}

    if reference.incoherent_budget is not None:
        # VEC < PBL: una de les dues xifres esta malament (un VEC anual, d'un sol
        # lot...). Cap conclusio de capacitat, ni a favor ni en contra.
        return DimensionOutcome(
            Factor(
                dimension="economic",
                result=DimensionResult.UNKNOWN,
                reason_code=ReasonCode.AMOUNT_INCOHERENT,
                weight=WEIGHT_ECONOMIC,
                score=None,
                company_value=_capacity_summary(profile),
                source_value=formatted,
                detail={
                    **detail_basis,
                    "estimated_value": str(value),
                    "base_budget_without_tax": str(reference.incoherent_budget),
                },
            )
        )

    outcome_by_vec = _by_thresholds(profile, value, formatted, detail_basis)
    if basis is AmountBasis.VEC:
        return outcome_by_vec
    if outcome_by_vec.factor.reason_code in _LOWER_BOUND_REASONS:
        # El PBL ja supera el llindar: el VEC tambe. Es diu que es una cota.
        outcome_by_vec.factor.detail["lower_bound"] = True
        return outcome_by_vec
    return DimensionOutcome(
        Factor(
            dimension="economic",
            result=DimensionResult.UNKNOWN,
            reason_code=ReasonCode.AMOUNT_BUDGET_LOWER_BOUND,
            weight=WEIGHT_ECONOMIC,
            score=None,
            company_value=_capacity_summary(profile),
            source_value=formatted,
            detail={**detail_basis, "lower_bound": True},
        )
    )


def _by_thresholds(
    profile: CompanyMatchProfile,
    value: Decimal,
    formatted: str,
    detail_basis: dict[str, object],
) -> DimensionOutcome:
    """Els trams de v1 sobre un import concret."""
    capacity = profile.capacity

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
                detail=dict(detail_basis),
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
                detail={**detail_basis, "maximum_assumable": str(capacity.maximum_assumable)},
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
