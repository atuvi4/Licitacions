"""Convertir "el 70% de X" en un import, nomes quan X es pot llegir sense dubtes.

Un llindar en percentatge no es comparable amb res fins que se'n coneix la base.
I la base ha de sortir de l'expedient de manera inequivoca: si cal triar entre
dos camps que podrien ser-la, o calcular-la amb una durada que no consta, el
resultat correcte es que no es pot resoldre.

Dos casos surten resolts i val la pena separar-los:

* **El plec ja dona l'import.** "El 70% de l'anualitat mitjana, es a dir
  21.406,71 EUR": l'organ ja ha fet el calcul i el llindar es aquell. El
  percentatge es conserva per poder-ho explicar, pero no cal deduir cap base.
* **La base es un camp unic de l'expedient**, com el valor estimat.

La resta —"anualitat mitjana" sense durada del contracte, "valor del contracte"
quan n'hi ha dos de possibles— no es dedueix. Estimar la base voldria dir
estimar el llindar, i comparar-hi la facturacio d'una empresa seria fabricar un
veredicte.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.compliance.specs import Threshold, to_decimal
from app.models.enums import PercentageBase
from app.tenders.amounts import useful_amount

#: Bases que es poden llegir directament d'un camp de l'expedient.
DIRECT_BASES: dict[PercentageBase, str] = {
    PercentageBase.ESTIMATED_VALUE: "estimated_value",
    PercentageBase.BASE_BUDGET: "base_budget",
}

#: Bases que necessiten una dada que el sistema no te. S'enumeren perque
#: l'explicacio pugui dir QUE falta, no nomes que falta alguna cosa.
UNRESOLVABLE_BASES: dict[PercentageBase, str] = {
    #: Caldria la durada del contracte, que no es un camp de l'expedient.
    PercentageBase.CONTRACT_ANNUITY: "contract_duration_unknown",
    #: "Valor del contracte" pot ser el valor estimat o el pressupost base.
    PercentageBase.CONTRACT_VALUE: "ambiguous_between_estimated_value_and_base_budget",
    PercentageBase.LOT_VALUE: "lot_value_not_resolved",
    PercentageBase.OTHER: "base_not_identified",
    PercentageBase.UNKNOWN: "base_not_stated",
}


@dataclass(frozen=True, slots=True)
class ThresholdResolution:
    """L'import contra el qual comparar, i d'on ha sortit."""

    amount: Decimal | None
    source: str
    percentage: Decimal | None = None
    percentage_base: PercentageBase = PercentageBase.UNKNOWN
    base_amount: Decimal | None = None
    #: Per que no s'ha pogut resoldre, quan `amount` es `None`.
    reason: str | None = None

    def explain(self) -> dict[str, Any]:
        """Els parametres que ha de guardar l'assessment per ser explicable."""
        detail: dict[str, Any] = {"threshold_source": self.source}
        if self.percentage is not None:
            detail["percentage"] = float(self.percentage)
            detail["percentage_base"] = str(self.percentage_base)
        if self.base_amount is not None:
            detail["percentage_base_amount"] = float(self.base_amount)
        if self.reason:
            detail["unresolved_reason"] = self.reason
        return detail


def resolve_threshold(threshold: Threshold, tender: Any = None) -> ThresholdResolution:
    """L'import a comparar. `amount=None` vol dir que s'ha de revisar."""
    if threshold.amount is not None:
        # El plec dona la xifra. Encara que tambe doni el percentatge, la xifra
        # es la del propi organ i no cal deduir res.
        return ThresholdResolution(
            amount=threshold.amount,
            source="absolute_amount",
            percentage=threshold.percentage,
            percentage_base=threshold.percentage_base,
        )

    if threshold.percentage is None:
        return ThresholdResolution(
            amount=None, source="none", reason="no_amount_and_no_percentage"
        )

    base = threshold.percentage_base
    field = DIRECT_BASES.get(base)
    if field is None:
        return ThresholdResolution(
            amount=None,
            source="percentage",
            percentage=threshold.percentage,
            percentage_base=base,
            reason=UNRESOLVABLE_BASES.get(base, "base_not_resolvable"),
        )

    # Un `0.00` publicat no es una base: el 70% de zero donaria un llindar de 0 que
    # qualsevol empresa compliria. Es com si no hi fos (`app.tenders.amounts`).
    base_amount = (
        useful_amount(to_decimal(getattr(tender, field, None))) if tender is not None else None
    )
    if base_amount is None:
        return ThresholdResolution(
            amount=None,
            source="percentage",
            percentage=threshold.percentage,
            percentage_base=base,
            reason=f"{field}_not_available",
        )

    tender_currency = str(getattr(tender, "currency", None) or "EUR").upper()
    if tender_currency != threshold.currency:
        # Aplicar un percentatge sobre una base en una altra moneda seria
        # barrejar unitats.
        return ThresholdResolution(
            amount=None,
            source="percentage",
            percentage=threshold.percentage,
            percentage_base=base,
            reason="base_currency_mismatch",
        )

    return ThresholdResolution(
        amount=base_amount * threshold.percentage / Decimal(100),
        source="percentage_of_base",
        percentage=threshold.percentage,
        percentage_base=base,
        base_amount=base_amount,
    )
