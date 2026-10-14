"""Validacio estructural d'un sistema de puntuacio. Pura: no calcula cap oferta."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from app.scoring.model import Evaluation, RuleKind, ScoringSystem, Status

#: Quin tipus de regla necessita cada avaluacio per poder puntuar.
REQUIRED_RULE = {
    Evaluation.ECONOMIC_FORMULA: RuleKind.FORMULA,
    Evaluation.YES_NO: RuleKind.YES_NO,
    Evaluation.LEVELS: RuleKind.LEVELS,
    Evaluation.JUDGMENT: RuleKind.RUBRIC,
    Evaluation.SCENE: RuleKind.SCENES,
}


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    #: Nodes i regles que no poden puntuar encara (regla `pending`/`conflict` o absent).
    not_computable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _sum(values) -> Decimal:
    return sum(values, Decimal(0))


def validate_system(system: ScoringSystem) -> ValidationReport:
    report = ValidationReport()
    errors = report.errors
    ids = Counter(n.id for n in system.nodes)
    errors += [f"node duplicat: {i}" for i, n in ids.items() if n > 1]
    rule_ids = Counter(r.id for r in system.rules)
    errors += [f"regla duplicada: {i}" for i, n in rule_ids.items() if n > 1]
    known_rules = {r.id: r for r in system.rules}
    known_nodes = {n.id for n in system.nodes}

    for node in system.nodes:
        if node.parent is not None and node.parent not in known_nodes:
            errors.append(f"{node.id}: pare inexistent {node.parent}")
        if not node.sources:
            errors.append(f"{node.id}: sense procedencia documental")
        for source in node.sources:
            if source.source not in system.documents:
                errors.append(f"{node.id}: font desconeguda {source.source}")
        for rule_id in node.rules:
            if rule_id not in known_rules:
                errors.append(f"{node.id}: regla inexistent {rule_id}")
    for rule in system.rules:
        if not rule.sources:
            errors.append(f"{rule.id}: regla sense procedencia documental")
        if rule.status is Status.VERIFIED and rule.kind is RuleKind.FORMULA and not rule.expression:
            errors.append(f"{rule.id}: formula verificada sense expressio")
        amount_rule = rule.variables.get("amount_rule")
        if amount_rule is not None and amount_rule not in known_rules:
            errors.append(f"{rule.id}: amount_rule inexistent {amount_rule}")
        expression = rule.expression or ""
        if rule.kind is RuleKind.FORMULA and rule.status is Status.VERIFIED and "VP" in expression:
            if rule.params.get("VP", Decimal(0)) <= 0:
                errors.append(f"{rule.id}: VP absent o no positiu")

    # Sumes: arrel = total; cada grup = suma dels fills (si tots tenen maxim).
    roots = system.children(None)
    if _sum(n.max_points or Decimal(0) for n in roots) != system.total_points:
        errors.append(f"les families no sumen {system.total_points}")
    for node in system.nodes:
        kids = system.children(node.id)
        if kids and node.max_points is not None:
            if any(k.max_points is None for k in kids):
                report.not_computable.append(f"{node.id}: fills sense puntuacio maxima")
            elif _sum(k.max_points for k in kids) != node.max_points:
                errors.append(
                    f"{node.id}: els fills sumen {_sum(k.max_points for k in kids)}, "
                    f"no {node.max_points}"
                )
    leaves_total = _sum(n.max_points or Decimal(0) for n in system.leaves())
    if leaves_total != system.total_points:
        errors.append(f"les fulles sumen {leaves_total}, no {system.total_points}")

    # Cada fulla (i cada node de judici amb escenes) ha de tenir la regla del seu tipus.
    for node in system.nodes:
        needed = REQUIRED_RULE.get(node.evaluation)
        if needed is None:
            continue
        rules = [
            known_rules[r] for r in node.rules if r in known_rules and known_rules[r].kind is needed
        ]
        if not rules:
            report.not_computable.append(f"{node.id}: falta una regla {needed.value}")
            continue
        if (
            node.evaluation is Evaluation.ECONOMIC_FORMULA
            and node.params.get("PL", Decimal(0)) <= 0
        ):
            errors.append(f"{node.id}: PL absent o no positiu")
        for rule in rules:
            if rule.status is not Status.VERIFIED:
                report.not_computable.append(f"{node.id}: regla {rule.id} {rule.status.value}")
            if rule.kind is RuleKind.LEVELS and rule.levels:
                if max(lv.points for lv in rule.levels) != node.max_points:
                    errors.append(
                        f"{node.id}: el nivell maxim de {rule.id} no es {node.max_points}"
                    )
            if rule.kind is RuleKind.RUBRIC and rule.rubric and max(rule.rubric) != node.max_points:
                errors.append(f"{node.id}: la rubrica de {rule.id} no arriba a {node.max_points}")
    return report
