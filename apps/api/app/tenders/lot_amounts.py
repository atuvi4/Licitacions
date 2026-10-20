"""Resolutor comu dels imports d'un lot (F-06e). Nomes lectura.

Per a cada lot i per a cada import (VEC, PBL sense IVA, PBL amb IVA), el valor UTIL que
es fa servir i d'on surt:

1. el de `tender_lots` (la PLACSP) si es util (`useful_amount`, > 0) -> font `placsp`;
2. si no, l'observacio de la PSCP **vigent** (`valid_to` NULL) i **`accepted`** del
   mateix lot (`tender_lot_id`) i tipus (`estimated_value` / `pbl_without_tax` /
   `pbl_with_tax`) amb `amount > 0` -> font `pscp`, amb l'id de l'observacio;
3. si no, cap valor (`value` None): ni `conflict`, ni `no_value`, ni un `0.00` no son
   mai un import (regla unica de `app.tenders.amounts`).

Incoherencia (`LotAmounts.vec_incoherent`): si el VEC del lot no es resol i la PSCP el te
en `conflict` perque es inferior al PBL sense IVA del lot (`vec_order`), el resolutor en
dona els dos imports publicats, perque el pre-fit ho tracti com a incoherent (com a
l'expedient) i no faci servir el PBL net com a cota inferior. Mai no es un import.

Mai l'import de l'expedient per a un lot, i mai no escriu res (ni a `tender_lots`: la
ingesta de la PLACSP no es toca). Els consumidors son la taula de lots de la fitxa, el
maxim de l'oferta economica per lot i el pre-fit per lot.

Rendiment: `load_lot_amounts` fa UNA consulta per a N lots (per trams de
`CHUNK_SIZE` identificadors en un recalcul massiu), mai una per lot.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

import sqlalchemy as sa

from app.ingestion.pscp_open_data import SOURCE_CODE
from app.models.enums import AmountObservationStatus, TenderAmountKind
from app.models.tender_amounts import TenderAmountObservation
from app.tenders.amounts import useful_amount

AmountSource = Literal["placsp", "pscp"]

#: Import del lot (columna de `tender_lots`) -> tipus d'observacio de la PSCP.
LOT_AMOUNT_KINDS: dict[str, TenderAmountKind] = {
    "estimated_value": TenderAmountKind.ESTIMATED_VALUE,
    "base_budget_without_tax": TenderAmountKind.PBL_WITHOUT_TAX,
    "base_budget": TenderAmountKind.PBL_WITH_TAX,
}
#: Identificadors de lot per consulta (parametres d'un `IN`).
CHUNK_SIZE = 500


class LotLike(Protocol):
    id: uuid.UUID
    estimated_value: Any
    base_budget_without_tax: Any
    base_budget: Any


@dataclass(frozen=True, slots=True)
class ResolvedAmount:
    """Un import resolt: el valor util (> 0) o `None`, i d'on surt."""

    value: Decimal | None = None
    source: AmountSource | None = None
    #: Nomes amb `source = "pscp"`: l'observacio d'on surt.
    observation_id: uuid.UUID | None = None


NO_AMOUNT = ResolvedAmount()


@dataclass(frozen=True, slots=True)
class LotAmounts:
    estimated_value: ResolvedAmount = NO_AMOUNT
    base_budget_without_tax: ResolvedAmount = NO_AMOUNT
    base_budget: ResolvedAmount = NO_AMOUNT
    #: (VEC, PBL sense IVA) tal com es publiquen, si el VEC no es resol i la PSCP el te en
    #: `conflict` per `vec_order` (VEC < PBL net). Nomes informa de la incoherencia.
    vec_incoherent: tuple[Decimal, Decimal] | None = None


VEC_ORDER_BLOCK = "lot.vec_order"


def _vec_incoherence(observations: Iterable[Any]) -> tuple[Decimal, Decimal] | None:
    """Els imports d'un VEC de lot en `conflict` vigent per `vec_order`, si n'hi ha."""
    for obs in sorted(observations, key=lambda o: (-_when(o.valid_from), str(o.id))):
        validation = getattr(obs, "validation", None)
        if (
            obs.validation_status != AmountObservationStatus.CONFLICT
            or obs.valid_to is not None
            or not isinstance(validation, dict)
            or VEC_ORDER_BLOCK not in (validation.get("blocking_failed") or [])
        ):
            continue
        guard = (validation.get("lot_guards") or {}).get("vec_order") or {}
        try:
            return Decimal(str(guard["high"])), Decimal(str(guard["low"]))
        except (KeyError, ArithmeticError, ValueError):
            continue
    return None


def _when(moment: datetime | None) -> float:
    if moment is None:
        return float("-inf")
    if moment.tzinfo is None:  # SQLite torna les dates sense zona (son UTC).
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def _pick(observations: Iterable[TenderAmountObservation]) -> TenderAmountObservation | None:
    """La `accepted` vigent amb import util. N'hi ha com a molt una (index unic de 0063);
    si n'hi hagues mes, la mes nova i, a igualtat, l'`id` mes petit (determinista)."""
    usable = [
        obs
        for obs in observations
        if obs.validation_status == AmountObservationStatus.ACCEPTED
        and obs.valid_to is None
        and useful_amount(obs.amount) is not None
    ]
    if not usable:
        return None
    return min(usable, key=lambda obs: (-_when(obs.valid_from), str(obs.id)))


def resolve(
    lot: LotLike, observations: Mapping[TenderAmountKind, Iterable[TenderAmountObservation]]
) -> LotAmounts:
    """Funcio pura: els tres imports d'un lot a partir de les seves observacions."""
    resolved: dict[str, ResolvedAmount] = {}
    for column, kind in LOT_AMOUNT_KINDS.items():
        placsp = useful_amount(getattr(lot, column, None))
        if placsp is not None:
            resolved[column] = ResolvedAmount(placsp, "placsp")
            continue
        chosen = _pick(observations.get(kind, ()))
        if chosen is not None:
            resolved[column] = ResolvedAmount(useful_amount(chosen.amount), "pscp", chosen.id)
        else:
            resolved[column] = NO_AMOUNT
    incoherent = None
    if resolved["estimated_value"].value is None:
        incoherent = _vec_incoherence(observations.get(TenderAmountKind.ESTIMATED_VALUE, ()))
    return LotAmounts(**resolved, vec_incoherent=incoherent)


def observations_query(lot_ids: list[uuid.UUID]) -> sa.Select:
    """Les observacions que poden resoldre un import dels lots donats (una consulta).

    Les `accepted` amb import > 0 i, per a la incoherencia, els VEC en `conflict`. Nomes
    les columnes que calen: la `validation` nomes dels `conflict` (cap `provenance`); en
    un recalcul massiu en surten milers.
    """
    obs = TenderAmountObservation
    conflict = obs.validation_status == AmountObservationStatus.CONFLICT
    return sa.select(
        obs.id,
        obs.tender_lot_id,
        obs.amount_kind,
        obs.amount,
        obs.validation_status,
        obs.valid_from,
        obs.valid_to,
        sa.case((conflict, obs.validation), else_=sa.null()).label("validation"),
    ).where(
        obs.tender_lot_id.in_(lot_ids),
        obs.source_code == SOURCE_CODE,
        obs.valid_to.is_(None),
        sa.or_(
            sa.and_(
                obs.validation_status == AmountObservationStatus.ACCEPTED,
                obs.amount_kind.in_(tuple(LOT_AMOUNT_KINDS.values())),
                obs.amount > 0,
            ),
            sa.and_(conflict, obs.amount_kind == TenderAmountKind.ESTIMATED_VALUE),
        ),
    )


async def load_lot_amounts(session, lots: Iterable[LotLike]) -> dict[uuid.UUID, LotAmounts]:
    """Els imports resolts de cada lot, amb una consulta per tram de `CHUNK_SIZE` lots.

    Si tots els imports d'un lot ja son de la PLACSP, no cal mirar-ne cap observacio.
    """
    lots = list(lots)
    needed = sorted(
        {
            lot.id
            for lot in lots
            if any(useful_amount(getattr(lot, c, None)) is None for c in LOT_AMOUNT_KINDS)
        },
        key=str,
    )
    found: dict[uuid.UUID, dict[TenderAmountKind, list[TenderAmountObservation]]] = {}
    for start in range(0, len(needed), CHUNK_SIZE):
        chunk = needed[start : start + CHUNK_SIZE]
        rows = (await session.execute(observations_query(chunk))).all()
        for obs in rows:
            found.setdefault(obs.tender_lot_id, {}).setdefault(obs.amount_kind, []).append(obs)
    return {lot.id: resolve(lot, found.get(lot.id, {})) for lot in lots}
