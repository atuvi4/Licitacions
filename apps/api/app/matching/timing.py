"""Dimensio temporal: quant marge hi ha per preparar l'oferta.

Un termini curt NO vol dir "impossible presentar-s'hi": vol dir menys marge, i
per tant menys punts en aquesta dimensio (que nomes val 10 sobre 100). El que
si que treu una licitacio del cercle de candidates es que el termini ja hagi
vencut o que l'expedient ja no sigui accionable (adjudicat, resolt, anul.lat).
"""

from __future__ import annotations

from datetime import datetime

from app.matching.rules import (
    DEADLINE_COMFORTABLE_DAYS,
    DEADLINE_CRITICAL_DAYS,
    DEADLINE_SHORT_DAYS,
    NON_ACTIONABLE_STATUSES,
    WEIGHT_TIMING,
    DimensionOutcome,
    Factor,
    ReasonCode,
)
from app.models.enums import DimensionResult
from app.tenders.deadline import days_until_deadline, deadline_has_passed

# Fraccio del pes segons el marge disponible.
_BANDS = (
    (DEADLINE_COMFORTABLE_DAYS, ReasonCode.DEADLINE_COMFORTABLE, 1.00, DimensionResult.MATCH),
    (DEADLINE_SHORT_DAYS, ReasonCode.DEADLINE_ADEQUATE, 0.80, DimensionResult.MATCH),
    (DEADLINE_CRITICAL_DAYS, ReasonCode.DEADLINE_SHORT, 0.50, DimensionResult.PARTIAL),
    (0, ReasonCode.DEADLINE_CRITICAL, 0.25, DimensionResult.PARTIAL),
)


def days_left(deadline: datetime | None, now: datetime | None = None) -> int | None:
    return days_until_deadline(deadline, now)


def evaluate(
    status: str, deadline: datetime | None, now: datetime | None = None
) -> DimensionOutcome:
    if status in NON_ACTIONABLE_STATUSES:
        return DimensionOutcome(
            Factor(
                dimension="timing",
                result=DimensionResult.NO_MATCH,
                reason_code=ReasonCode.STATUS_NOT_OPEN,
                weight=WEIGHT_TIMING,
                score=0.0,
                source_value=status,
            ),
            excluded=True,
        )

    remaining = days_left(deadline, now)
    if remaining is None:
        return DimensionOutcome(
            Factor(
                dimension="timing",
                result=DimensionResult.UNKNOWN,
                reason_code=ReasonCode.DEADLINE_UNKNOWN,
                weight=WEIGHT_TIMING,
                score=None,
            )
        )

    # Vencut: o ja ha passat l'instant (encara que sigui avui mateix), o falten dies negatius.
    if remaining < 0 or deadline_has_passed(deadline, now):
        return DimensionOutcome(
            Factor(
                dimension="timing",
                result=DimensionResult.NO_MATCH,
                reason_code=ReasonCode.DEADLINE_EXPIRED,
                weight=WEIGHT_TIMING,
                score=0.0,
                source_value=f"{remaining} dies",
            ),
            excluded=True,
        )

    for threshold, reason, factor, result in _BANDS:
        if remaining >= threshold:
            return DimensionOutcome(
                Factor(
                    dimension="timing",
                    result=result,
                    reason_code=reason,
                    weight=WEIGHT_TIMING,
                    score=WEIGHT_TIMING * factor,
                    source_value=f"{remaining} dies",
                    detail={"days_left": remaining},
                )
            )

    # Inabastable: l'ultima banda cobreix des de 0 dies.
    raise AssertionError("banda de termini no coberta")  # pragma: no cover
