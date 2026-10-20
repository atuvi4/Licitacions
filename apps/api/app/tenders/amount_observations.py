"""Els imports de la PSCP a la fitxa (F-06a fase 3, F-06e). Nomes lectura.

Llegeix `tender_amount_observations` (0063) i en treu, per a la fitxa d'una licitacio,
l'observacio que s'ha d'ensenyar per a l'expedient (PBL amb IVA) i per a cada lot vigent
(PBL amb IVA i, des de F-06e, PBL sense IVA i VEC). **No calcula res ni toca cap altre
import**: el cataleg, els filtres i l'ordenacio continuen llegint nomes `tenders`; el
pre-fit i l'oferta per lot fan servir el resolutor comu (`app.tenders.lot_amounts`).

Que s'ensenya:

* Nomes observacions vigents (`valid_to IS NULL`) de la font PSCP (`SOURCE_CODE`): la
  fitxa ho presenta com a "segons la PSCP". Expedient: nomes `pbl_with_tax`. Lots: els
  tres tipus, cadascun al seu diccionari (`lots`, `lots_pbl_without_tax`,
  `lots_estimated_value`), amb la mateixa regla de seleccio.
* Estats `accepted`, `conflict` i `no_value`. Una `superseded` **no surt mai**, ni com a
  valor ni com a avis: nomes en queda la traca a la taula.
* Expedient: `tender_lot_id` NULL i que no sigui una fila de LOT de la font sense lot
  nostre. L'adaptador desa aquestes files amb `tender_lot_id` NULL, `source_entity_key
  = <uuid>#lot=<n>` i `validation.level = "lot"`; qualsevol dels dos senyals la
  descarta. No s'enganxa a cap lot (no en te cap de nostre) ni passa per l'expedient.
* Lots: nomes els lots vigents de la licitacio (`removed_at` NULL).

Regla de seleccio, si un mateix objectiu (l'expedient o un lot) te mes d'una observacio
vigent ensenyable:

1. per estat: `accepted` > `conflict` > `no_value`. L'estat passa per davant de la data:
   una `conflict` vigent guanya a una `no_value` mes nova del mateix objectiu (l'adaptador
   no tanca les observacions de files desaparegudes, per decisio del propietari; es el
   criteri conservador: una discrepancia no s'amaga perque n'arribi una lectura buida);
2. DINS DEL MATEIX ESTAT, la canonica abans que la d'una fila antiga: en la forma `mixed`
   amb la fila exacta no explicada, l'adaptador desa al mateix run (mateix `valid_from`)
   la de l'expedient (`<uuid>_*`) i la de la fila antiga (`validation.role =
   "unexplained_row"`). Si totes dues tenen el mateix estat, s'ensenya la canonica (import,
   enllac i comprovacions). Entre estats diferents mana l'estat (punt 1): si la canonica
   es `no_value` (les files de lot publiquen l'import de l'expedient nul o a 0) i la de la
   fila antiga es `conflict`, s'ensenya la `conflict` de la fila antiga.
   `evaluation_error` es la lectura de l'expedient: compta com a canonica;
3. la de `valid_from` mes recent;
4. a igualtat, l'`id` mes petit (en text).

Determinista: la mateixa base dona sempre la mateixa resposta.

Atribucio: el text oficial de la llicencia tal com l'ensenya la fitxa
(`OFFICIAL_ATTRIBUTION`), sense data; la data del dataset va a part
(`source_updated_at`). No es la constant `ATTRIBUTION` de l'adaptador: aquella es la
procedencia que ja s'ha escrit a les observacions i no es toca.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from app.ingestion.pscp_open_data import SOURCE_CODE
from app.models.enums import AmountObservationStatus, TenderAmountKind
from app.models.tender_amounts import TenderAmountObservation
from app.schemas.tenders import AmountObservationRead, BudgetWithTaxObservations

ACCEPTED = AmountObservationStatus.ACCEPTED
CONFLICT = AmountObservationStatus.CONFLICT
NO_VALUE = AmountObservationStatus.NO_VALUE

#: Estats que la fitxa ensenya, en ordre de precedencia (el primer guanya).
SHOWN_STATUSES: tuple[AmountObservationStatus, ...] = (ACCEPTED, CONFLICT, NO_VALUE)
_RANK = {status: index for index, status in enumerate(SHOWN_STATUSES)}

#: Codis estables de les comprovacions que fallen, en l'ordre en que es llisten.
#: `organ`, `pbl_without_tax`, `vec` i `lots` comparen la PSCP amb la PLACSP; `lot_sum`
#: es interna de la PSCP (la suma dels seus lots contra el seu total de l'expedient).
FAILED_CHECKS: tuple[str, ...] = ("organ", "pbl_without_tax", "vec", "lots", "lot_sum")
#: Guardes de l'adaptador (`guards.py`) -> codi de la fitxa. Les que no hi son
#: (identitat, ordre de l'IVA, coherencia, lectura, files) no es llisten.
_EXPEDIENT_GUARDS = {
    "organ": "organ",
    "net": "pbl_without_tax",
    "vec": "vec",
    "lots": "lots",
    "lot_sum": "lot_sum",
}
_LOT_GUARDS = {
    "net": "pbl_without_tax",
    "vec": "vec",
    "match": "lots",
    # F-06e: la suma dels PBL sense IVA (o dels VEC) dels lots de la PSCP contra el seu
    # total de l'expedient. Interna de la PSCP, com `lot_sum`: el mateix codi.
    "lot_net_sum": "lot_sum",
    "lot_vec_sum": "lot_sum",
}
#: Tipus d'import de lot -> camp de `BudgetWithTaxObservations`.
LOT_FIELDS: dict[TenderAmountKind, str] = {
    TenderAmountKind.PBL_WITH_TAX: "lots",
    TenderAmountKind.PBL_WITHOUT_TAX: "lots_pbl_without_tax",
    TenderAmountKind.ESTIMATED_VALUE: "lots_estimated_value",
}

LOT_KEY_MARKER = "#lot="
#: Rols de `validation` d'una fila antiga de l'expedient (no la lectura canonica).
NON_CANONICAL_ROLES = frozenset({"unexplained_row", "superseded_row"})

#: L'atribucio que exigeix la llicencia, tal com l'ensenya la fitxa (sense la data, que
#: va a `source_updated_at`). Una sola font de veritat: la web pinta aquest text.
OFFICIAL_ATTRIBUTION = (
    "Font: Generalitat de Catalunya. Departament d'Economia i Finances \u2014 "
    "Contractaci\u00f3 p\u00fablica a Catalunya: publicacions a la Plataforma de serveis "
    "de contractaci\u00f3 p\u00fablica (dades obertes). Llic\u00e8ncia oberta d'\u00fas "
    "d'informaci\u00f3 \u2013 Catalunya."
)


def is_unmapped_lot_row(observation: TenderAmountObservation) -> bool:
    """Una fila de lot de la font sense lot nostre (`tender_lot_id` NULL)."""
    validation = observation.validation if isinstance(observation.validation, dict) else {}
    return validation.get("level") == "lot" or LOT_KEY_MARKER in (
        observation.source_entity_key or ""
    )


def is_canonical(observation: TenderAmountObservation) -> bool:
    """No es la d'una fila antiga de l'expedient (`validation.role`)."""
    validation = observation.validation if isinstance(observation.validation, dict) else {}
    return validation.get("role") not in NON_CANONICAL_ROLES


def _sort_key(observation: TenderAmountObservation) -> tuple:
    valid_from = observation.valid_from
    if valid_from.tzinfo is None:  # SQLite torna les dates sense zona (son UTC).
        valid_from = valid_from.replace(tzinfo=UTC)
    return (
        _RANK[observation.validation_status],
        0 if is_canonical(observation) else 1,
        -valid_from.timestamp(),
        str(observation.id),
    )


def select_observation(
    observations: Iterable[TenderAmountObservation],
) -> TenderAmountObservation | None:
    """La que s'ensenya d'un mateix objectiu (vegeu la regla al docstring del modul)."""
    shown = [o for o in observations if o.validation_status in _RANK]
    return min(shown, key=_sort_key) if shown else None


def failed_checks(observation: TenderAmountObservation) -> list[str]:
    """Les comprovacions que fallen, en codis estables. Buida si no es `conflict`."""
    if observation.validation_status != CONFLICT:
        return []
    validation = observation.validation if isinstance(observation.validation, dict) else {}
    found: set[str] = set()
    if isinstance(validation.get("blocking_failed"), list):
        # F-06e (PBL sense IVA i VEC de lot): nomes les guardes que han bloquejat AQUEST
        # tipus (`expedient.<g>` / `lot.<g>`); mai, p. ex., el PBL net per a un VEC.
        for entry in _as_list(validation["blocking_failed"]):
            level, _, name = entry.partition(".")
            mapping = _EXPEDIENT_GUARDS if level == "expedient" else _LOT_GUARDS
            if name in mapping:
                found.add(mapping[name])
        return [code for code in FAILED_CHECKS if code in found]
    for name in _as_list(validation.get("failed")) + _as_list(validation.get("expedient_failed")):
        if name in _EXPEDIENT_GUARDS:
            found.add(_EXPEDIENT_GUARDS[name])
    for name in _as_list(validation.get("lot_failed")):
        if name in _LOT_GUARDS:
            found.add(_LOT_GUARDS[name])
    return [code for code in FAILED_CHECKS if code in found]


def is_evaluation_error(observation: TenderAmountObservation) -> bool:
    """La lectura de la font no s'ha pogut avaluar (`guards.error_evaluation`)."""
    if observation.validation_status != CONFLICT:
        return False
    validation = observation.validation if isinstance(observation.validation, dict) else {}
    return validation.get("role") == "evaluation_error" or "evaluation_error" in _as_list(
        validation.get("failed")
    )


def _as_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def publication_url(provenance: Any) -> str | None:
    """L'`enllac_publicacio` de la primera fila que en te un (nomes http/https)."""
    rows = provenance.get("rows") if isinstance(provenance, dict) else None
    for row in rows if isinstance(rows, list) else []:
        link = row.get("enllac_publicacio") if isinstance(row, dict) else None
        if isinstance(link, str) and link.startswith(("https://", "http://")):
            return link
    return None


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def source_updated_at(observation: TenderAmountObservation) -> datetime | None:
    """`rowsUpdatedAt` del dataset: la columna, o la procedencia si la columna es buida."""
    if observation.source_dataset_updated_at is not None:
        return _aware(observation.source_dataset_updated_at)
    provenance = observation.provenance if isinstance(observation.provenance, dict) else {}
    text = provenance.get("rows_updated_at")
    if isinstance(text, str):
        try:
            return _aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def observation_read(observation: TenderAmountObservation) -> AmountObservationRead:
    status = observation.validation_status
    amount = observation.amount
    return AmountObservationRead(
        amount_kind=observation.amount_kind.value,
        status=status.value,
        amount=amount,
        currency=observation.currency,
        published_zero=status == NO_VALUE and amount is not None and amount == 0,
        failed_checks=failed_checks(observation),
        evaluation_error=is_evaluation_error(observation),
        publication_url=publication_url(observation.provenance),
        source_updated_at=source_updated_at(observation),
        retrieved_at=_aware(observation.retrieved_at),
        source_code=observation.source_code,
    )


def build(
    observations: Iterable[TenderAmountObservation], active_lot_ids: set[uuid.UUID]
) -> BudgetWithTaxObservations:
    """Agrupa per objectiu i tipus i tria (funcio pura: la consulta es a `load`).

    L'expedient, nomes el PBL amb IVA; cada lot vigent, els tres tipus per separat.
    """
    expedient: list[TenderAmountObservation] = []
    by_lot: dict[TenderAmountKind, dict[uuid.UUID, list[TenderAmountObservation]]] = {
        kind: {} for kind in LOT_FIELDS
    }
    for observation in observations:
        kind = observation.amount_kind
        if observation.tender_lot_id is None:
            if kind == TenderAmountKind.PBL_WITH_TAX and not is_unmapped_lot_row(observation):
                expedient.append(observation)
        elif observation.tender_lot_id in active_lot_ids and kind in by_lot:
            by_lot[kind].setdefault(observation.tender_lot_id, []).append(observation)

    chosen = select_observation(expedient)
    lots: dict[TenderAmountKind, dict[uuid.UUID, TenderAmountObservation]] = {}
    for kind, grouped in by_lot.items():
        lots[kind] = {}
        for lot_id in sorted(grouped, key=str):
            selected = select_observation(grouped[lot_id])
            if selected is not None:
                lots[kind][lot_id] = selected
    shown = ([chosen] if chosen is not None else []) + [
        o for selected in lots.values() for o in selected.values()
    ]
    dates = [moment for moment in (source_updated_at(o) for o in shown) if moment is not None]
    return BudgetWithTaxObservations(
        expedient=observation_read(chosen) if chosen is not None else None,
        **{
            LOT_FIELDS[kind]: {lot_id: observation_read(o) for lot_id, o in selected.items()}
            for kind, selected in lots.items()
        },
        attribution=OFFICIAL_ATTRIBUTION if shown else None,
        source_updated_at=max(dates) if dates else None,
    )


async def load(
    session, tender_id: uuid.UUID, active_lot_ids: set[uuid.UUID]
) -> BudgetWithTaxObservations:
    """Una sola consulta: totes les vigents ensenyables de la licitacio (expedient i lots,
    els tres tipus)."""
    result = await session.execute(
        sa.select(TenderAmountObservation).where(
            TenderAmountObservation.tender_id == tender_id,
            TenderAmountObservation.valid_to.is_(None),
            TenderAmountObservation.amount_kind.in_(tuple(LOT_FIELDS)),
            TenderAmountObservation.source_code == SOURCE_CODE,
            TenderAmountObservation.validation_status.in_(SHOWN_STATUSES),
        )
    )
    return build(result.scalars().all(), active_lot_ids)
