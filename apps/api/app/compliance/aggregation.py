"""Combinar exercicis abans de comparar-los amb un llindar.

"El millor dels tres ultims" i "cadascun dels tres ultims" son requisits
diferents i la v1 els representava igual. Ara es distingeixen, i cada agregacio
te una regla explicita sobre **quantes dades li calen**:

* `max` i `any_year` es poden resoldre amb els exercicis que hi hagi: si un any
  ja supera el llindar, els que falten no poden canviar el resultat. Amb dades
  incompletes nomes es pot afirmar el SI, mai el NO.
* `each_year`, `average` i `cumulative` necessiten **tots** els exercicis del
  periode: sense un d'ells, la mitjana no es la mitjana i la suma no es la suma.
  Amb un any que falta, la resposta es "no ho sabem".

L'excepcio util: amb `each_year`, un any registrat que NO arriba al llindar ja
demostra l'incompliment encara que en faltin d'altres. Es l'unica manera de dir
que no amb dades parcials sense inventar-se res.

Modul pur: numeros i anys. Ni base de dades, ni model, ni dates de sistema.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.enums import AggregationType

#: Agregacions que exigeixen tenir tots els exercicis del periode.
NEEDS_EVERY_YEAR = frozenset(
    {AggregationType.EACH_YEAR, AggregationType.AVERAGE, AggregationType.CUMULATIVE}
)


@dataclass(frozen=True, slots=True)
class YearValue:
    year: int
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Aggregate:
    """El resultat de combinar exercicis, amb el detall que ho explica."""

    #: La xifra que s'ha de comparar amb el llindar. `None` = no calculable.
    value: Decimal | None
    #: Com s'ha obtingut, per poder-ho explicar sense tornar-ho a calcular.
    method: AggregationType
    years_used: tuple[int, ...] = ()
    years_missing: tuple[int, ...] = ()
    #: Cert quan falten exercicis que aquesta agregacio necessitava.
    incomplete: bool = False
    #: Un exercici concret que ja incompleix, quan l'agregacio es `each_year`.
    failing_year: int | None = None


def expected_years(reference_year: int, count: int) -> tuple[int, ...]:
    """Els `count` exercicis anteriors al de referencia, del mes recent al mes antic.

    L'exercici en curs no compta: mentre no s'ha tancat no hi ha comptes anuals
    dipositades i el plec parla d'"exercicis disponibles".
    """
    return tuple(reference_year - offset for offset in range(1, count + 1))


def aggregate_years(
    values: list[YearValue],
    *,
    aggregation: AggregationType,
    threshold: Decimal | None = None,
    window: tuple[int, ...] | None = None,
) -> Aggregate:
    """Combina els exercicis segons l'agregacio demanada.

    `window` son els anys que el plec exigeix mirar. Si s'informa, el que falti
    es tracta com a dada absent i no com a zero: un exercici sense registrar no
    es un exercici amb facturacio zero.
    """
    by_year = {value.year: value.amount for value in values}
    if window is not None:
        wanted = window
        present = tuple(year for year in wanted if year in by_year)
        missing = tuple(year for year in wanted if year not in by_year)
    else:
        present = tuple(sorted(by_year, reverse=True))
        missing = ()

    amounts = [by_year[year] for year in present]
    incomplete = bool(missing)

    if aggregation in NEEDS_EVERY_YEAR:
        if aggregation == AggregationType.EACH_YEAR and threshold is not None:
            # Un any registrat que no arriba ja demostra l'incompliment, encara
            # que en faltin: cap dada futura pot desfer-ho.
            failing = next(
                (year for year in present if by_year[year] < threshold), None
            )
            if failing is not None:
                return Aggregate(
                    value=by_year[failing],
                    method=aggregation,
                    years_used=present,
                    years_missing=missing,
                    incomplete=incomplete,
                    failing_year=failing,
                )
        if incomplete or not amounts:
            return Aggregate(
                value=None,
                method=aggregation,
                years_used=present,
                years_missing=missing,
                incomplete=True,
            )
        if aggregation == AggregationType.EACH_YEAR:
            value = min(amounts)
        elif aggregation == AggregationType.AVERAGE:
            value = sum(amounts) / Decimal(len(amounts))
        else:
            value = sum(amounts)
        return Aggregate(
            value=value, method=aggregation, years_used=present, years_missing=missing
        )

    if not amounts:
        return Aggregate(
            value=None,
            method=aggregation,
            years_used=present,
            years_missing=missing,
            incomplete=True,
        )

    # `max`, `any_year` i `unknown`: el millor exercici disponible. Amb dades
    # incompletes nomes en surt una afirmacio segura si ja arriba al llindar; qui
    # ho decideix es la regla, que rep `incomplete`.
    best = max(amounts)
    return Aggregate(
        value=best,
        method=aggregation,
        years_used=present,
        years_missing=missing,
        incomplete=incomplete,
    )
