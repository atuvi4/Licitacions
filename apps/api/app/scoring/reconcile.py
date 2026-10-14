"""Reconciliacio dels registres bruts de criteris (`award_criteria` d'un run) amb el sistema
canonic.

No esborra ni modifica cap registre: construeix una VISTA que, per a cada node canonic, conserva els
identificadors de tots els registres que el descriuen (el canonic i els duplicats). L'assignacio
registre -> node(s) es una entrada explicita (revisada per una persona o un revisor); aqui nomes es
VALIDA mecanicament: cada registre assignat un sol cop, nodes existents, i punts del registre = suma
del maxim dels nodes que cobreix.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.scoring.model import ScoringSystem

ROLES = {
    "main",
    "subcriterion",
    "duplicate",
    "reference",
    "parameter",
    "formula",
    "penalty",
    "threshold",
    "informative",
}


@dataclass
class Reconciliation:
    records: int
    by_role: dict[str, int]
    canonical_records: int  # registres que no son duplicats
    nodes_covered: list[str]
    nodes_missing: list[str]
    view: dict[str, dict[str, list[str]]]  # node -> {"canonical", "duplicates", "references"}
    raw_points_sum: Decimal
    canonical_points_sum: Decimal
    raw_points_by_node: dict[str, Decimal]
    problems: list[str] = field(default_factory=list)


def _points(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def reconcile(
    system: ScoringSystem, records: list[dict], assignments: dict[str, dict]
) -> Reconciliation:
    """`records`: [{"id", "points", ...}].

    `assignments`: {id: {"role": ..., "nodes": [node ids]}}.
    """
    problems: list[str] = []
    ids = [r["id"] for r in records]
    for dup, n in Counter(ids).items():
        if n > 1:
            problems.append(f"registre repetit a l'entrada: {dup}")
    unassigned = [i for i in ids if i not in assignments]
    problems += [f"registre sense assignar: {i}" for i in unassigned]
    problems += [
        f"assignacio d'un registre inexistent: {i}" for i in assignments if i not in set(ids)
    ]
    known = {n.id: n for n in system.nodes}
    view: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"canonical": [], "duplicates": [], "references": []}
    )
    raw_by_node: dict[str, Decimal] = defaultdict(Decimal)
    raw_sum = Decimal(0)
    roles = Counter()
    for record in records:
        a = assignments.get(record["id"])
        if a is None:
            continue
        role = a.get("role")
        if role not in ROLES:
            problems.append(f"{record['id']}: rol desconegut {role!r}")
            continue
        roles[role] += 1
        nodes = list(a.get("nodes") or [])
        for node_id in nodes:
            if node_id not in known:
                problems.append(f"{record['id']}: node inexistent {node_id}")
        points = _points(record.get("points"))
        if points is not None:
            raw_sum += points
            key = "+".join(nodes) if nodes else "(cap node)"
            raw_by_node[key] += points
        if nodes and all(n in known for n in nodes):
            expected = sum((known[n].max_points or Decimal(0) for n in nodes), Decimal(0))
            if points is not None and points != expected:
                problems.append(
                    f"{record['id']}: {points} punts, pero {'+'.join(nodes)} en val {expected}"
                )
            # Una referencia (sense punts propis) no es mai el registre canonic d'un node.
            bucket = {"duplicate": "duplicates", "reference": "references"}.get(role, "canonical")
            for node_id in nodes:
                view[node_id][bucket].append(record["id"])
        elif role in ("main", "subcriterion", "duplicate"):
            problems.append(f"{record['id']}: rol {role} sense cap node")
    scoring_nodes = [n.id for n in system.nodes if n.evaluation.value != "scene"]
    covered = sorted(n for n in scoring_nodes if view.get(n, {}).get("canonical"))
    missing = sorted(set(scoring_nodes) - set(covered))
    canonical_sum = sum((n.max_points or Decimal(0) for n in system.leaves()), Decimal(0))
    return Reconciliation(
        records=len(records),
        by_role=dict(roles),
        canonical_records=sum(c for r, c in roles.items() if r != "duplicate"),
        nodes_covered=covered,
        nodes_missing=missing,
        view={k: dict(v) for k, v in view.items()},
        raw_points_sum=raw_sum,
        canonical_points_sum=canonical_sum,
        raw_points_by_node=dict(raw_by_node),
        problems=problems,
    )
