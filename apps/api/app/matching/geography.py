"""Dimensio territorial.

Dues formes deterministes de decidir si una licitacio cau en un territori de
l'empresa, per ordre de fiabilitat:

1. **Codi NUTS.** Si el territori de l'empresa te un codi, es comprova si es
   prefix del NUTS de la licitacio (`ES51` cobreix `ES512`). Es jerarquia
   oficial, no una suposicio.
2. **Nom exacte normalitzat.** Si no hi ha codi, es compara el nom del
   territori amb el `CountrySubentity` que publica la font (que es el nom de
   la provincia, un camp controlat), sense accents ni majuscules.

El que NO es fa es inferir territoris de text lliure. I sobretot: si la
licitacio no diu on s'executa, el resultat es UNKNOWN, mai NO_MATCH. Que ens
falti la dada no vol dir que no encaixi.
"""

from __future__ import annotations

from app.matching.profile import CompanyMatchProfile, TerritoryPreference, normalize_place
from app.matching.rules import (
    TERRITORY_NOT_DECLARED_FACTOR,
    TERRITORY_PRIORITY_FACTORS,
    TERRITORY_PRIORITY_HIGH,
    TERRITORY_PRIORITY_MEDIUM,
    WEIGHT_GEOGRAPHY,
    DimensionOutcome,
    Factor,
    ReasonCode,
    priority_band,
)
from app.models.enums import DimensionResult

_PRIORITY_REASONS = {
    "high": ReasonCode.TERRITORY_HIGH_PRIORITY,
    "medium": ReasonCode.TERRITORY_MEDIUM_PRIORITY,
    "low": ReasonCode.TERRITORY_ALLOWED,
}


def _matches(
    territory: TerritoryPreference, nuts_code: str | None, place_name: str | None
) -> str | None:
    """Retorna com ha coincidit (`nuts` o `name`), o None."""
    if territory.code and nuts_code and nuts_code.upper().startswith(territory.code):
        return "nuts"
    if territory.normalized_name and place_name:
        # La font publica de vegades el nom bilingue ("Valencia/Valencia",
        # "Gipuzkoa/Guipuzcoa"). Cal partir ABANS de normalitzar, perque la
        # normalitzacio elimina la barra i deixaria "valenciavalencia".
        parts = {
            normalize_place(part) for part in (place_name, *place_name.split("/"))
        }
        if territory.normalized_name in parts:
            return "name"
    return None


def evaluate(
    profile: CompanyMatchProfile, nuts_code: str | None, place_name: str | None
) -> DimensionOutcome:
    if not profile.has_territories:
        return DimensionOutcome(
            Factor(
                dimension="geography",
                result=DimensionResult.UNKNOWN,
                reason_code=ReasonCode.TERRITORY_NO_COMPANY_DATA,
                weight=WEIGHT_GEOGRAPHY,
                score=None,
            )
        )
    if not nuts_code and not place_name:
        return DimensionOutcome(
            Factor(
                dimension="geography",
                result=DimensionResult.UNKNOWN,
                reason_code=ReasonCode.TERRITORY_UNKNOWN,
                weight=WEIGHT_GEOGRAPHY,
                score=None,
            )
        )

    matched: list[tuple[TerritoryPreference, str]] = []
    for territory in profile.territories:
        how = _matches(territory, nuts_code, place_name)
        if how is not None:
            matched.append((territory, how))

    if not matched:
        # Cap territori declarat cobreix aquesta ubicacio. NO es una exclusio i
        # tampoc un zero: l'empresa ha declarat preferencies, no prohibicions.
        # Puntua poc, de manera que una oportunitat molt bona en CPV i import
        # encara pugui aparèixer, pero sempre per darrere de les del seu territori.
        return DimensionOutcome(
            Factor(
                dimension="geography",
                result=DimensionResult.PARTIAL,
                reason_code=ReasonCode.TERRITORY_NOT_DECLARED,
                weight=WEIGHT_GEOGRAPHY,
                score=WEIGHT_GEOGRAPHY * TERRITORY_NOT_DECLARED_FACTOR,
                company_value=", ".join(t.name for t in profile.territories[:5]),
                source_value=place_name or nuts_code,
            )
        )

    # Si algun territori aplicable esta marcat com a "no hi volem treballar",
    # aixo mana: es una decisio de negoci explicita.
    refused = [pair for pair in matched if not pair[0].willing_to_work]
    if refused and not any(pair[0].willing_to_work for pair in matched):
        territory, how = refused[0]
        return DimensionOutcome(
            Factor(
                dimension="geography",
                result=DimensionResult.NO_MATCH,
                reason_code=ReasonCode.TERRITORY_EXCLUDED,
                weight=WEIGHT_GEOGRAPHY,
                score=0.0,
                company_value=territory.name,
                source_value=place_name or nuts_code,
                detail={"matched_by": how},
            ),
            excluded=True,
        )

    # El millor encaix mana: si l'empresa cobreix el lloc com a provincia
    # prioritaria i tambe com a comunitat autonoma, val la prioritaria.
    territory, how = max(
        (pair for pair in matched if pair[0].willing_to_work),
        key=lambda pair: pair[0].priority,
    )
    band = priority_band(territory.priority, TERRITORY_PRIORITY_HIGH, TERRITORY_PRIORITY_MEDIUM)
    return DimensionOutcome(
        Factor(
            dimension="geography",
            result=DimensionResult.MATCH,
            reason_code=_PRIORITY_REASONS[band],
            weight=WEIGHT_GEOGRAPHY,
            score=WEIGHT_GEOGRAPHY * TERRITORY_PRIORITY_FACTORS[band],
            company_value=territory.name,
            source_value=place_name or nuts_code,
            detail={
                "matched_by": how,
                "priority": territory.priority,
                "priority_band": band,
                "territory_type": territory.territory_type,
            },
        )
    )
