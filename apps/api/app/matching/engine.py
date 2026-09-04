"""Motor del pre-fit: combina les quatre dimensions en un resultat explicable.

Tot el que hi ha aqui es determinista i no toca la base de dades: es pot provar
sense HTTP i sense Postgres.

## Com es tracten les dades desconegudes

Aquest es el punt delicat. Donar 0 punts a una dimensio que no hem pogut
avaluar seria penalitzar l'empresa per una dada que la font no publica. Donar-li
el maxim seria premiar-la pel mateix motiu.

La solucio: **una dimensio desconeguda queda fora del calcul**, tant del
numerador com del denominador. El score es la mitjana ponderada de les
dimensions que SI s'han pogut avaluar, reescalada a 100, i `data_coverage` diu
quina fraccio del pes total s'ha pogut mirar. Si la cobertura es massa baixa, el
resultat no es un score dolent: es `insufficient_data`.

Exemple: si nomes es coneixen CPV (40) i territori (25), un encaix perfecte dona
100 amb cobertura 0,65 — no 65, que suggeriria un mal encaix quan el que passa
es que falten dades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.matching import cpv as cpv_dimension
from app.matching import economic as economic_dimension
from app.matching import geography as geography_dimension
from app.matching import timing as timing_dimension
from app.matching.profile import CompanyMatchProfile
from app.matching.rules import (
    CANDIDATE_THRESHOLD,
    CPV_CANDIDATE_BLOCKERS,
    MIN_DATA_COVERAGE,
    RULES_VERSION,
    TOTAL_WEIGHT,
    DimensionOutcome,
)
from app.models.enums import ExclusionKind, MatchStatus


@dataclass(slots=True)
class MatchOutcome:
    """Resultat d'avaluar una licitacio o un lot per a una empresa."""

    status: MatchStatus
    score: int | None
    data_coverage: float
    cpv_score: float | None = None
    geographic_score: float | None = None
    economic_score: float | None = None
    timing_score: float | None = None
    exclusion_kind: ExclusionKind | None = None
    exclusion_reason_code: str | None = None
    factors: list[dict[str, Any]] = field(default_factory=list)
    rules_version: str = RULES_VERSION

    @property
    def is_candidate(self) -> bool:
        return self.status is MatchStatus.CANDIDATE


def evaluate(
    profile: CompanyMatchProfile,
    *,
    cpv_codes: list[str],
    nuts_code: str | None,
    place_name: str | None,
    amount: Decimal | None,
    tender_status: str,
    deadline: datetime | None,
    now: datetime | None = None,
) -> MatchOutcome:
    """Puntua una licitacio o un lot. No fa cap consulta a la base de dades."""
    timing = timing_dimension.evaluate(tender_status, deadline, now)
    cpv = cpv_dimension.evaluate(profile, cpv_codes)
    geography = geography_dimension.evaluate(profile, nuts_code, place_name)
    economic = economic_dimension.evaluate(profile, amount)

    dimensions: list[DimensionOutcome] = [cpv, geography, economic, timing]
    factors = [dimension.factor.as_dict() for dimension in dimensions]

    scores = {
        "cpv_score": cpv.score,
        "geographic_score": geography.score,
        "economic_score": economic.score,
        "timing_score": timing.score,
    }

    # 1. Exclusions per estat: la licitacio ja no es accionable. Manen sobre
    #    qualsevol altra consideracio, perque encara que encaixi perfectament
    #    no s'hi pot presentar ningu.
    if timing.excluded:
        return MatchOutcome(
            status=MatchStatus.CLOSED,
            score=None,
            data_coverage=_coverage(dimensions),
            exclusion_kind=ExclusionKind.STATUS,
            exclusion_reason_code=timing.factor.reason_code,
            factors=factors,
            **scores,
        )

    # 2. Exclusions de negoci: l'empresa ha decidit que aixo no li interessa.
    #    No es cap blocker legal; els legals arribaran amb els plecs.
    business = next(
        (dimension for dimension in (cpv, geography, economic) if dimension.excluded), None
    )
    if business is not None:
        return MatchOutcome(
            status=MatchStatus.EXCLUDED,
            score=None,
            data_coverage=_coverage(dimensions),
            exclusion_kind=ExclusionKind.BUSINESS,
            exclusion_reason_code=business.factor.reason_code,
            factors=factors,
            **scores,
        )

    # 3. Score sobre les dimensions realment avaluables.
    known = [dimension for dimension in dimensions if not dimension.is_unknown]
    known_weight = sum(dimension.weight for dimension in known)
    coverage = known_weight / TOTAL_WEIGHT

    if coverage < MIN_DATA_COVERAGE:
        return MatchOutcome(
            status=MatchStatus.INSUFFICIENT_DATA,
            score=None,
            data_coverage=coverage,
            factors=factors,
            **scores,
        )

    earned = sum(dimension.score or 0.0 for dimension in known)
    score = round(earned / known_weight * 100)

    # Sostre per CPV: sense una interseccio prou concreta amb l'activitat
    # declarada, la licitacio no pot ser candidata per bona que sigui la resta.
    # No es una exclusio (l'empresa no ha decidit res): es que no tenim motiu
    # per posar-la sobre la taula.
    capped = cpv.factor.reason_code in CPV_CANDIDATE_BLOCKERS
    status = (
        MatchStatus.CANDIDATE
        if score >= CANDIDATE_THRESHOLD and not capped
        else MatchStatus.LOW_FIT
    )

    return MatchOutcome(
        status=status,
        score=score,
        data_coverage=coverage,
        factors=factors,
        **scores,
    )


def _coverage(dimensions: list[DimensionOutcome]) -> float:
    known = sum(dimension.weight for dimension in dimensions if not dimension.is_unknown)
    return known / TOTAL_WEIGHT


def aggregate_lots(lot_outcomes: list[MatchOutcome]) -> MatchOutcome:
    """Resum d'un expedient a partir dels seus lots.

    El resultat de l'expedient es el del MILLOR lot, mai una mitjana: amagar un
    lot que encaixa al 91 darrere d'una mitjana de 55 seria justament perdre la
    informacio util. El recompte de lots candidats acompanya el numero.
    """
    if not lot_outcomes:  # pragma: no cover - qui crida ja ho comprova
        raise ValueError("Cal com a minim un lot")

    scored = [outcome for outcome in lot_outcomes if outcome.score is not None]
    if scored:
        return max(scored, key=lambda outcome: outcome.score or 0)

    # Cap lot puntuable: mana l'estat menys terminal que hagi sortit.
    for status in (
        MatchStatus.INSUFFICIENT_DATA,
        MatchStatus.EXCLUDED,
        MatchStatus.CLOSED,
    ):
        match = next((outcome for outcome in lot_outcomes if outcome.status is status), None)
        if match is not None:
            return match
    return lot_outcomes[0]
