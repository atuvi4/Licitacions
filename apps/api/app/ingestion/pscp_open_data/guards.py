"""Guardes de les observacions d'import de la PSCP (F-06a). Pures: sense base ni xarxa.

Entrada: el que ja sabem de la licitacio (`TenderSnapshot`: organ, PBL sense IVA, VEC i
lots vigents) i les files del dataset que porten el seu `pscp_id_intern` (`id_intern =
<uuid>` o `<uuid>_<sufix>`). Sortida: les observacions a desar, cadascuna amb el seu
estat i la `validation` (determinista) que l'explica.

Forma de les files:

* `single`: una sola fila exacta (`id_intern = <uuid>`), expedient sense lots.
* `lots`: nomes files de lot (`<uuid>_<sufix>`, el lot es `numero_lot`).
* `mixed`: una fila exacta antiga i files de lot. La fila exacta nomes s'ignora si es pot
  DEMOSTRAR que una publicacio posterior la substitueix (`supersession_evidence`): ha de
  tenir un identificador de publicacio mes petit, una fase anterior i dates no
  posteriors que les files de lot, les tres coses alhora. Llavors es desa com a
  `superseded`, amb les files que la substitueixen i l'evidencia. Si l'evidencia no es
  concloent, `conflict`: mai no es descarta una discrepancia perque sembli antiga.

Guardes de l'expedient (sobre les files seleccionades):

* `identity`: el `pscp_id_intern` es d'una sola licitacio nostra.
* `organ` (a): `codi_organ` = `source_metadata.placsp_organ_id`.
* `net` (b): el PBL sense IVA publicat = `base_budget_without_tax`, al centim.
* `vec` (c): el VEC publicat = `estimated_value`, al centim; no s'aplica si no en tenim.
* `lots` (d): files de lot = lots vigents, `numero_lot` unics, no nuls i iguals als
  nostres; sense lots, exactament una fila exacta.
* `lot_sum` (e): la suma del PBL amb IVA dels lots = el de l'expedient, amb 0,01 EUR de
  tolerancia per lot (l'arrodoniment de la font). No s'aplica si algun lot no en te.
* `tax_order`: PBL amb IVA >= PBL sense IVA (igual: IVA del 0 %, legitim).
* `consistent`: els camps d'expedient es repeteixen identics a totes les files.
* `parse`: els imports de contrast son decimals plans de com a molt 2 decimals.
* `rows` (d0): cap fila sense explicar (la fila antiga d'un `mixed` nomes queda
  explicada si esta demostradament substituida).

Estat, per ordre de precedencia:

1. `no_value`: el PBL amb IVA no es publica (NULL: `amount` NULL) o es publica a 0
   (`amount` 0, tal com es publica). Mai no es un import valid. Te precedencia sobre
   tot: tambe una fila substituida o en conflicte que no porta import es `no_value` (la
   substitucio o el conflicte queden a `validation`), perque la base no admet `amount`
   NULL amb cap altre estat.
2. `superseded`: la fila antiga d'un `mixed`, demostradament substituida.
3. `conflict`: alguna guarda (de l'expedient o del lot) falla.
4. `accepted`.

Un import que no es pot desar sense alterar-lo (mes de 2 decimals, negatiu, no
decimal, o el camp d'expedient diferent entre files) NO es desa: es una `Refusal`, i la
resta d'observacions de l'expedient queden en `conflict` per la guarda `parse` o
`consistent`.

Convencions de les claus:

* Expedient: `source_entity_key = <uuid>`; `source_row_id = <uuid>` si surt de la fila
  exacta, o `<uuid>_*` si surt de les files de lot (la llista completa de files va a
  `validation.rows` i a la procedencia).
* Lot: `source_entity_key = <uuid>#lot=<numero_lot>` (`#lot=null` si la font no en dona)
  i `source_row_id = <id_intern>` de la fila. Una fila de lot sense lot nostre
  identificable de manera unica es desa amb `tender_lot_id` NULL i en `conflict`.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.ingestion.pscp_open_data import parser as p
from app.models.enums import AmountObservationStatus

#: Versio de l'esquema de `validation` (canviar-la fa que totes les observacions es
#: tornin a escriure al seguent run).
VALIDATION_VERSION = 1
CENT = Decimal("0.01")

ACCEPTED = AmountObservationStatus.ACCEPTED
CONFLICT = AmountObservationStatus.CONFLICT
NO_VALUE = AmountObservationStatus.NO_VALUE
SUPERSEDED = AmountObservationStatus.SUPERSEDED


@dataclass(frozen=True)
class LotSnapshot:
    id: uuid.UUID
    lot_number: int | None
    net: Decimal | None = None
    vec: Decimal | None = None


@dataclass(frozen=True)
class TenderSnapshot:
    id: uuid.UUID
    pscp_id: str
    organ_id: str | None
    net: Decimal | None
    vec: Decimal | None
    lots: tuple[LotSnapshot, ...] = ()
    #: False si el mateix `pscp_id_intern` es de mes d'una licitacio nostra.
    identity_unique: bool = True


@dataclass
class Draft:
    """Una observacio a desar (`amount_kind = pbl_with_tax`)."""

    tender_id: uuid.UUID
    tender_lot_id: uuid.UUID | None
    level: str  # "expedient" | "lot"
    source_entity_key: str
    source_row_id: str
    source_phase: str | None
    status: AmountObservationStatus
    amount: Decimal | None
    field: str
    published_text: str | None
    validation: dict[str, Any]
    rows: list[dict[str, Any]]
    #: Camps i valors publicats de contrast (no es desen com a import).
    cross_check: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple:
        return (self.tender_id, self.tender_lot_id, self.source_row_id)


@dataclass(frozen=True)
class Refusal:
    """Un import publicat que no es pot desar sense alterar-lo."""

    tender_id: uuid.UUID
    level: str
    source_row_id: str
    field: str
    published_text: str | None
    problem: str


@dataclass
class TenderEvaluation:
    tender_id: uuid.UUID
    pscp_id: str
    shape: str
    drafts: list[Draft] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Substitucio demostrada                                                        #
# --------------------------------------------------------------------------- #


def _iso(moment) -> str | None:
    return moment.isoformat() if moment is not None else None


def supersession_evidence(old: dict[str, Any], newer: list[dict[str, Any]]) -> dict[str, Any]:
    """Si `old` queda substituida per `newer`: identificador, fase i dates, alhora.

    * Identificador de publicacio (`enllac_publicacio .../<uuid>/<id>`): el de `old` ha
      de ser estrictament mes petit que el de cada fila nova.
    * Fase (`PHASE_ORDER`): la de `old` estrictament anterior a la de cada fila nova.
    * Dates: cap fila nova amb `:updated_at` anterior; cada fila nova te alguna data
      d'anunci, avaluacio o adjudicacio, i totes son >= la data de publicacio mes
      recent de `old`; i cada una d'aquestes dates que `old` tingui, la fila nova
      tambe la te i no es anterior.

    Qualsevol dada que falti o no es pugui llegir fa l'evidencia no concloent.
    """
    old_pub = p.publication_id(old)
    new_pubs = [p.publication_id(row) for row in newer]
    pub_ok = (
        bool(newer)
        and old_pub is not None
        and None not in new_pubs
        and all(pub > old_pub for pub in new_pubs)
    )
    old_rank = p.phase_rank(old.get("fase_publicacio"))
    new_ranks = [p.phase_rank(row.get("fase_publicacio")) for row in newer]
    phase_ok = (
        bool(newer)
        and old_rank is not None
        and None not in new_ranks
        and all(rank > old_rank for rank in new_ranks)
    )

    old_updated = p.system_timestamp(old.get(":updated_at"))
    new_updated = [p.system_timestamp(row.get(":updated_at")) for row in newer]
    updated_ok = (
        old_updated is not None
        and None not in new_updated
        and all(moment >= old_updated for moment in new_updated)
    )
    old_latest = p.latest_publication_date(old)
    earliest_new = None
    publication_ok = old_latest is not None and bool(newer)
    for row in newer:
        dates = [p.floating_date(row.get(name)) for name in p.SUPERSESSION_DATE_FIELDS]
        present = [moment for moment in dates if moment is not None]
        if not present:
            publication_ok = False
            continue
        first = min(present)
        earliest_new = first if earliest_new is None else min(earliest_new, first)
        if old_latest is not None and first < old_latest:
            publication_ok = False
        for name in p.SUPERSESSION_DATE_FIELDS:
            before = p.floating_date(old.get(name))
            if before is None:
                continue
            after = p.floating_date(row.get(name))
            if after is None or after < before:
                publication_ok = False
    dates_ok = updated_ok and publication_ok

    verdict = "superseded" if (pub_ok and phase_ok and dates_ok) else "inconclusive"
    return {
        "verdict": verdict,
        "superseded_by": sorted(str(row.get("id_intern")) for row in newer),
        "checks": {
            "publication_id": {
                "ok": pub_ok,
                "old": old_pub,
                "new": sorted({pub for pub in new_pubs if pub is not None}),
                "new_missing": sum(1 for pub in new_pubs if pub is None),
            },
            "phase": {
                "ok": phase_ok,
                "old": old.get("fase_publicacio"),
                "new": sorted({str(row.get("fase_publicacio")) for row in newer}),
            },
            "dates": {
                "ok": dates_ok,
                "updated_at_ok": updated_ok,
                "publication_dates_ok": publication_ok,
                "old_updated_at": _iso(old_updated),
                "new_updated_at_min": _iso(
                    min((m for m in new_updated if m is not None), default=None)
                ),
                "old_latest_publication": _iso(old_latest),
                "new_earliest_publication": _iso(earliest_new),
            },
        },
    }


# --------------------------------------------------------------------------- #
# Guardes                                                                       #
# --------------------------------------------------------------------------- #


def _equal_to_cent(published: p.ParsedAmount, ours: Decimal | None) -> dict[str, Any]:
    result: dict[str, Any] = {"published": published.text, "ours": p.cents(ours)}
    if ours is None:
        return {**result, "ok": False, "problem": "ours_missing"}
    if not published.published:
        return {**result, "ok": False, "problem": "not_published"}
    if published.problem:
        return {**result, "ok": False, "problem": published.problem}
    return {**result, "ok": p.cents(published.value) == p.cents(ours)}


def _optional_equal_to_cent(published: p.ParsedAmount, ours: Decimal | None) -> dict[str, Any]:
    """(c) i les guardes de lot: nomes s'apliquen si nosaltres en tenim el valor."""
    if ours is None:
        return {"ok": None, "published": published.text, "ours": None, "problem": "ours_missing"}
    return _equal_to_cent(published, ours)


def _tax_order(with_tax: p.ParsedAmount, without_tax: p.ParsedAmount) -> dict[str, Any]:
    if not (with_tax.usable and without_tax.usable):
        return {"ok": None}
    return {"ok": with_tax.value >= without_tax.value}


def _phase(rows: list[dict[str, Any]]) -> str | None:
    phases = sorted({str(row.get("fase_publicacio")) for row in rows if row.get("fase_publicacio")})
    if not phases:
        return None
    return "|".join(phases)[:80]


def _no_value_reason(amount: p.ParsedAmount) -> str | None:
    if amount.value is None:
        return "null_published"
    if amount.value == 0:
        return "zero_published"
    return None


def _status(
    amount: p.ParsedAmount, *, failed: bool, superseded: bool = False
) -> AmountObservationStatus:
    if _no_value_reason(amount):
        return NO_VALUE
    if superseded:
        return SUPERSEDED
    return CONFLICT if failed else ACCEPTED


def _expedient_value(rows: list[dict[str, Any]], name: str) -> tuple[p.ParsedAmount, bool]:
    """El camp d'expedient (repetit a cada fila) i si es identic a totes."""
    values = {row.get(name) for row in rows}
    if len(values) != 1:
        texts = sorted("" if value is None else str(value) for value in values)
        return p.ParsedAmount("|".join(texts), None, "inconsistent_across_rows"), False
    return p.parse_amount(values.pop()), True


def evaluate(snapshot: TenderSnapshot, rows: list[dict[str, Any]]) -> TenderEvaluation:
    """Les observacions d'una licitacio a partir de les seves files del dataset."""
    uid = snapshot.pscp_id
    rows = sorted(rows, key=lambda row: (str(row.get("id_intern")), str(row.get(":id"))))
    exact = [row for row in rows if str(row.get("id_intern")).lower() == uid]
    lot_rows = [row for row in rows if str(row.get("id_intern")).lower() != uid]
    if not rows:
        return TenderEvaluation(snapshot.id, uid, "not_found")
    if exact and lot_rows:
        shape = "mixed"
    elif lot_rows:
        shape = "lots"
    else:
        shape = "single" if len(exact) == 1 else "multi_exact"
    evaluation = TenderEvaluation(snapshot.id, uid, shape)

    superseded_rows: list[dict[str, Any]] = []
    unexplained: list[dict[str, Any]] = []
    supersession: dict[str, Any] | None = None
    if lot_rows:
        selected = lot_rows
        if len(exact) == 1:
            supersession = supersession_evidence(exact[0], lot_rows)
            if supersession["verdict"] == "superseded":
                superseded_rows = exact
            else:
                unexplained = exact
        elif exact:
            unexplained = exact
            supersession = {"verdict": "inconclusive", "problem": "duplicate_exact_rows"}
    else:
        selected = exact
    by_lot = bool(lot_rows)

    # --- Guardes de l'expedient ------------------------------------------------- #
    with_tax, with_tax_consistent = _expedient_value(selected, p.EXPEDIENT_WITH_TAX)
    without_tax, net_consistent = _expedient_value(selected, p.EXPEDIENT_WITHOUT_TAX)
    estimated, vec_consistent = _expedient_value(selected, p.EXPEDIENT_ESTIMATED)
    guards: dict[str, dict[str, Any]] = {}
    guards["identity"] = {"ok": snapshot.identity_unique}
    organs = sorted({str(row.get("codi_organ")) for row in selected})
    guards["organ"] = {
        "ok": snapshot.organ_id is not None and organs == [snapshot.organ_id],
        "published": organs,
        "ours": snapshot.organ_id,
    }
    guards["net"] = _equal_to_cent(without_tax, snapshot.net)
    guards["vec"] = _optional_equal_to_cent(estimated, snapshot.vec)
    guards["tax_order"] = _tax_order(with_tax, without_tax)
    inconsistent = [
        name
        for name, ok in (
            (p.EXPEDIENT_WITH_TAX, with_tax_consistent),
            (p.EXPEDIENT_WITHOUT_TAX, net_consistent),
            (p.EXPEDIENT_ESTIMATED, vec_consistent),
        )
        if not ok
    ]
    guards["consistent"] = {"ok": not inconsistent, "fields": inconsistent}
    parse_problems = {
        name: amount.problem
        for name, amount in (
            (p.EXPEDIENT_WITHOUT_TAX, without_tax),
            (p.EXPEDIENT_ESTIMATED, estimated),
        )
        if amount.problem and amount.problem != "inconsistent_across_rows"
    }
    if with_tax.problem and with_tax.problem != "inconsistent_across_rows":
        parse_problems[p.EXPEDIENT_WITH_TAX] = with_tax.problem

    # (d) lots, i la correspondencia numero_lot <-> lot_number.
    our_numbers = [lot.lot_number for lot in snapshot.lots]
    our_counts = Counter(our_numbers)
    our_by_number = {
        str(lot.lot_number): lot
        for lot in snapshot.lots
        if lot.lot_number is not None and our_counts[lot.lot_number] == 1
    }
    source_numbers = [p.normalize_lot_number(row.get("numero_lot")) for row in lot_rows]
    source_counts = Counter(source_numbers)
    ours_text = sorted(str(n) for n in our_numbers if n is not None)
    lots_guard: dict[str, Any] = {
        "rows": len(selected) if by_lot else 0,
        "ours": len(snapshot.lots),
        "numbers": sorted(n for n in source_numbers if n is not None),
        "ours_numbers": ours_text,
    }
    if snapshot.lots:
        if not by_lot:
            lots_guard.update(ok=False, problem="expected_lot_rows")
        elif None in our_numbers or len(our_by_number) != len(snapshot.lots):
            lots_guard.update(ok=False, problem="ours_ambiguous")
        else:
            lots_guard["ok"] = (
                len(lot_rows) == len(snapshot.lots)
                and None not in source_numbers
                and len(set(source_numbers)) == len(source_numbers)
                and set(source_numbers) == set(our_by_number)
            )
    else:
        lots_guard["ok"] = not by_lot and len(selected) == 1
        if by_lot:
            lots_guard["problem"] = "unexpected_lot_rows"
    guards["lots"] = lots_guard
    missing = sorted(set(our_by_number) - {n for n in source_numbers if n is not None})
    if missing:
        evaluation.notes["our_lots_without_row"] = missing

    # (e) suma dels lots, amb la tolerancia de l'arrodoniment de la font.
    lot_amounts = [p.parse_amount(row.get(p.LOT_WITH_TAX)) for row in lot_rows]
    if by_lot and with_tax.usable and lot_amounts and all(a.usable for a in lot_amounts):
        total = sum((a.value for a in lot_amounts), Decimal("0"))
        difference = total - with_tax.value
        tolerance = CENT * len(lot_amounts)
        guards["lot_sum"] = {
            "ok": abs(difference) <= tolerance,
            "sum": p.cents(total),
            "expedient": p.cents(with_tax.value),
            "difference": p.cents(difference),
            "tolerance": p.cents(tolerance),
        }
    else:
        guards["lot_sum"] = {"ok": None}
    guards["parse"] = {"ok": not parse_problems, "problems": parse_problems}
    rows_guard: dict[str, Any] = {
        "ok": not unexplained,
        "unexplained": sorted(str(row.get("id_intern")) for row in unexplained),
    }
    if supersession is not None:
        rows_guard["supersession"] = supersession["verdict"]
    guards["rows"] = rows_guard
    failed = sorted(name for name, guard in guards.items() if guard.get("ok") is False)
    evaluation.failed = failed
    common = {"v": VALIDATION_VERSION, "shape": shape}

    # --- Observacio de l'expedient ------------------------------------------- #
    expedient_row_id = f"{uid}_*" if by_lot else uid
    selected_ids = [str(row.get("id_intern")) for row in selected]
    if with_tax.problem:
        evaluation.refusals.append(
            Refusal(
                snapshot.id,
                "expedient",
                expedient_row_id,
                p.EXPEDIENT_WITH_TAX,
                with_tax.text,
                with_tax.problem,
            )
        )
    else:
        evaluation.drafts.append(
            Draft(
                tender_id=snapshot.id,
                tender_lot_id=None,
                level="expedient",
                source_entity_key=uid,
                source_row_id=expedient_row_id,
                source_phase=_phase(selected),
                status=_status(with_tax, failed=bool(failed)),
                amount=with_tax.value,
                field=p.EXPEDIENT_WITH_TAX,
                published_text=with_tax.text,
                validation={
                    **common,
                    "level": "expedient",
                    "no_value_reason": _no_value_reason(with_tax),
                    "guards": guards,
                    "failed": failed,
                    "rows": selected_ids,
                },
                rows=selected,
                cross_check={
                    p.EXPEDIENT_WITHOUT_TAX: without_tax.text,
                    p.EXPEDIENT_ESTIMATED: estimated.text,
                },
            )
        )

    # --- La fila antiga d'un `mixed` ----------------------------------------- #
    for row in superseded_rows + unexplained:
        amount = p.parse_amount(row.get(p.EXPEDIENT_WITH_TAX))
        is_superseded = row in superseded_rows
        if amount.problem:
            evaluation.refusals.append(
                Refusal(
                    snapshot.id, "expedient", uid, p.EXPEDIENT_WITH_TAX, amount.text, amount.problem
                )
            )
            continue
        evaluation.drafts.append(
            Draft(
                tender_id=snapshot.id,
                tender_lot_id=None,
                level="expedient",
                source_entity_key=uid,
                source_row_id=uid,
                source_phase=_phase([row]),
                status=_status(amount, failed=True, superseded=is_superseded),
                amount=amount.value,
                field=p.EXPEDIENT_WITH_TAX,
                published_text=amount.text,
                validation={
                    **common,
                    "level": "expedient",
                    "role": "superseded_row" if is_superseded else "unexplained_row",
                    "no_value_reason": _no_value_reason(amount),
                    "supersession": supersession,
                    "failed": [] if is_superseded else ["rows"],
                    "rows": [uid],
                },
                rows=[row],
                cross_check={
                    p.EXPEDIENT_WITHOUT_TAX: p.parse_amount(row.get(p.EXPEDIENT_WITHOUT_TAX)).text,
                    p.EXPEDIENT_ESTIMATED: p.parse_amount(row.get(p.EXPEDIENT_ESTIMATED)).text,
                },
            )
        )

    # --- Observacions de lot ---------------------------------------------------- #
    expedient_guards = {name: guard.get("ok") for name, guard in guards.items()}
    for row, number, amount in zip(lot_rows, source_numbers, lot_amounts, strict=True):
        row_id = str(row.get("id_intern"))
        if amount.problem:
            evaluation.refusals.append(
                Refusal(snapshot.id, "lot", row_id, p.LOT_WITH_TAX, amount.text, amount.problem)
            )
            continue
        matched = None
        if number is None:
            match = {"ok": False, "problem": "no_number"}
        elif source_counts[number] > 1:
            match = {"ok": False, "problem": "duplicate_in_source"}
        elif our_counts.get(int(number) if number.isdigit() else number, 0) > 1:
            match = {"ok": False, "problem": "ambiguous_in_ours"}
        elif number in our_by_number:
            matched = our_by_number[number]
            match = {"ok": True}
        else:
            match = {"ok": False, "problem": "no_lot_in_ours"}
        lot_net = p.parse_amount(row.get(p.LOT_WITHOUT_TAX))
        lot_vec = p.parse_amount(row.get(p.LOT_ESTIMATED))
        lot_guards: dict[str, dict[str, Any]] = {
            "match": match,
            "net": _optional_equal_to_cent(lot_net, matched.net if matched else None),
            "vec": _optional_equal_to_cent(lot_vec, matched.vec if matched else None),
            "tax_order": _tax_order(amount, lot_net),
            "parse": {
                "ok": not (lot_net.problem or lot_vec.problem),
                "problems": {
                    name: value.problem
                    for name, value in ((p.LOT_WITHOUT_TAX, lot_net), (p.LOT_ESTIMATED, lot_vec))
                    if value.problem
                },
            },
        }
        lot_failed = sorted(name for name, guard in lot_guards.items() if guard.get("ok") is False)
        evaluation.drafts.append(
            Draft(
                tender_id=snapshot.id,
                tender_lot_id=matched.id if matched else None,
                level="lot",
                source_entity_key=f"{uid}#lot={number if number is not None else 'null'}",
                source_row_id=row_id,
                source_phase=_phase([row]),
                status=_status(amount, failed=bool(failed or lot_failed)),
                amount=amount.value,
                field=p.LOT_WITH_TAX,
                published_text=amount.text,
                validation={
                    **common,
                    "level": "lot",
                    "numero_lot": None if row.get("numero_lot") is None else str(row["numero_lot"]),
                    "no_value_reason": _no_value_reason(amount),
                    "lot_guards": lot_guards,
                    "lot_failed": lot_failed,
                    "expedient_guards": expedient_guards,
                    "expedient_failed": failed,
                    "rows": [row_id],
                },
                rows=[row],
                cross_check={p.LOT_WITHOUT_TAX: lot_net.text, p.LOT_ESTIMATED: lot_vec.text},
            )
        )
    return evaluation
