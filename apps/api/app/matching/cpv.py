"""Dimensio CPV.

La jerarquia CPV es llegeix del propi codi: 8 digits on els 2 primers son la
divisio, 3 el grup, 4 la classe i 5 la categoria. Comparar prefixos no es
inventar equivalencies, es aplicar l'estructura oficial del vocabulari. No cal
tenir el cataleg descarregat i no s'utilitza cap similitud semantica.

Limitacio documentada: el nostre cataleg `cpv_codes` te descripcions
provisionals per als codis que arriben de PLACSP, aixi que NOMES es compara per
codi. Amb el cataleg oficial importat es podria afinar, pero no canviaria la
naturalesa determinista d'aixo.
"""

from __future__ import annotations

from app.matching.rules import (
    CPV_INACTIVE_FACTOR,
    CPV_PREFIX_LEVELS,
    CPV_PRIORITY_FACTORS,
    CPV_PRIORITY_HIGH,
    CPV_PRIORITY_MEDIUM,
    WEIGHT_CPV,
    DimensionOutcome,
    Factor,
    ReasonCode,
    priority_band,
)
from app.matching.profile import CPVPreference, CompanyMatchProfile
from app.models.enums import DimensionResult

# Motiu de coincidencia exacta segons la prioritat declarada per l'empresa.
_EXACT_REASONS = {
    "high": ReasonCode.CPV_EXACT_HIGH_PRIORITY,
    "medium": ReasonCode.CPV_EXACT_MEDIUM_PRIORITY,
    "low": ReasonCode.CPV_EXACT_LOW_PRIORITY,
}
_RELATED_REASONS = {
    "CATEGORY": ReasonCode.CPV_RELATED_CATEGORY,
    "CLASS": ReasonCode.CPV_RELATED_CLASS,
    "GROUP": ReasonCode.CPV_RELATED_GROUP,
    "DIVISION": ReasonCode.CPV_RELATED_DIVISION,
}


def _digits(code: str) -> str:
    """Els CPV poden venir amb digit de control (`12345678-9`): es descarta."""
    return "".join(character for character in code if character.isdigit())[:8]


def _overlap(company_code: str, tender_code: str) -> tuple[str, float] | None:
    """Nivell de coincidencia entre dos codis, o None si no en comparteixen cap."""
    left, right = _digits(company_code), _digits(tender_code)
    if not left or not right:
        return None
    for length, level, factor in CPV_PREFIX_LEVELS:
        if left[:length] and left[:length] == right[:length]:
            return level, factor
    return None


def evaluate(profile: CompanyMatchProfile, tender_cpvs: list[str]) -> DimensionOutcome:
    """Puntua l'encaix de CPV entre l'empresa i una licitacio o lot."""
    if not profile.has_cpv_preferences:
        # L'empresa no ha dit amb quins CPV treballa: no en sabem res, no es
        # que no encaixi.
        return DimensionOutcome(
            Factor(
                dimension="cpv",
                result=DimensionResult.UNKNOWN,
                reason_code=ReasonCode.CPV_NO_COMPANY_PREFERENCES,
                weight=WEIGHT_CPV,
                score=None,
            )
        )
    if not tender_cpvs:
        return DimensionOutcome(
            Factor(
                dimension="cpv",
                result=DimensionResult.UNKNOWN,
                reason_code=ReasonCode.CPV_UNKNOWN,
                weight=WEIGHT_CPV,
                score=None,
            )
        )

    excluded: list[CPVPreference] = [pref for pref in profile.cpv_preferences if pref.excluded]
    usable = [pref for pref in profile.cpv_preferences if not pref.excluded]

    best: tuple[float, CPVPreference, str, str, float] | None = None
    for preference in usable:
        for code in tender_cpvs:
            overlap = _overlap(preference.code, code)
            if overlap is None:
                continue
            level, level_factor = overlap
            band = priority_band(preference.priority, CPV_PRIORITY_HIGH, CPV_PRIORITY_MEDIUM)
            factor = level_factor * CPV_PRIORITY_FACTORS[band]
            if not preference.active:
                factor *= CPV_INACTIVE_FACTOR
            if best is None or factor > best[0]:
                best = (factor, preference, code, level, level_factor)

    # Exclusio de negoci: la licitacio nomes encaixa amb CPV que l'empresa ha
    # marcat com a descartats. No es cap blocker legal, es una decisio seva.
    excluded_hit = next(
        (
            (preference, code)
            for preference in excluded
            for code in tender_cpvs
            if _overlap(preference.code, code) is not None
            and _digits(preference.code) == _digits(code)
        ),
        None,
    )
    if excluded_hit is not None and best is None:
        preference, code = excluded_hit
        return DimensionOutcome(
            Factor(
                dimension="cpv",
                result=DimensionResult.NO_MATCH,
                reason_code=ReasonCode.CPV_EXCLUDED,
                weight=WEIGHT_CPV,
                score=0.0,
                company_value=preference.code,
                source_value=code,
            ),
            excluded=True,
        )

    if best is None:
        return DimensionOutcome(
            Factor(
                dimension="cpv",
                result=DimensionResult.NO_MATCH,
                reason_code=ReasonCode.CPV_NO_OVERLAP,
                weight=WEIGHT_CPV,
                score=0.0,
                company_value=", ".join(sorted({p.code for p in usable})[:5]),
                source_value=", ".join(sorted(tender_cpvs)[:5]),
            )
        )

    factor, preference, code, level, _ = best
    band = priority_band(preference.priority, CPV_PRIORITY_HIGH, CPV_PRIORITY_MEDIUM)
    if not preference.active:
        reason = ReasonCode.CPV_INACTIVE_ONLY
        result = DimensionResult.PARTIAL
    elif level == "EXACT":
        reason = _EXACT_REASONS[band]
        result = DimensionResult.MATCH
    else:
        reason = _RELATED_REASONS[level]
        result = DimensionResult.PARTIAL

    return DimensionOutcome(
        Factor(
            dimension="cpv",
            result=result,
            reason_code=reason,
            weight=WEIGHT_CPV,
            score=WEIGHT_CPV * factor,
            company_value=preference.code,
            source_value=code,
            detail={
                "level": level,
                "priority": preference.priority,
                "priority_band": band,
                "active": preference.active,
            },
        )
    )
