"""Calcul de puntuacions a partir d'un `ScoringSystem`. Prudent per disseny.

* Nomes s'aplica una regla `verified`. Si cal una regla absent, `pending` o `conflict`, NO es
  calcula res: `ScoringRuleMissing` amb la llista exacta del que falta.
* El judici de valor el puntua la mesa: Tuvi no l'estima. Cal que l'oferta porti la puntuacio de
  la mesa per a cada criteri (dins de la rubrica) o el resultat de cada escena; si no,
  `ScoringInputMissing`.
* Cap valor intermedi inventat: un nivell que no existeix a la regla, una baixa negativa o amb mes
  decimals dels permesos, o una resposta mal formada es `ScoringInputInvalid`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.scoring.model import Evaluation, Node, Rule, RuleKind, ScoringSystem, Status

CENT = Decimal("0.01")


class ScoringRuleMissing(Exception):
    def __init__(self, missing: list[str]):
        super().__init__("Falten regles verificades: " + "; ".join(missing))
        self.missing = missing


class ScoringInputMissing(Exception):
    def __init__(self, missing: list[str]):
        super().__init__("Falten dades de l'oferta: " + "; ".join(missing))
        self.missing = missing


class ScoringInputInvalid(ValueError):
    pass


def _rule(system: ScoringSystem, node: Node, kind: RuleKind) -> Rule | None:
    for rule_id in node.rules:
        rule = system.rule(rule_id)
        if rule.kind is kind:
            return rule
    return None


def _verified(system: ScoringSystem, node: Node, kind: RuleKind) -> Rule:
    rule = _rule(system, node, kind)
    if rule is None or rule.status is not Status.VERIFIED:
        state = "absent" if rule is None else rule.status.value
        raise ScoringRuleMissing([f"{node.id}: regla {kind.value} {state}"])
    return rule


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def offered_amount(system: ScoringSystem, node_id: str, discount_pct: Decimal) -> Decimal:
    """Oi = arrodonir(PLi x (1 - Bi/100), 2). Baixa >= 0 i amb els decimals permesos."""
    node = system.node(node_id)
    rule = _verified(system, node, RuleKind.FORMULA)
    # La formula de puntuacio declara quina regla calcula l'import ofert (Oi).
    amount_rule = (
        system.rule(rule.variables["amount_rule"]) if "amount_rule" in rule.variables else None
    )
    if amount_rule is None or amount_rule.status is not Status.VERIFIED:
        raise ScoringRuleMissing([f"{node_id}: regla de l'import ofert absent o no verificada"])
    decimals = int(amount_rule.params.get("max_discount_decimals", Decimal(0)))
    if discount_pct < 0:
        raise ScoringInputInvalid("La baixa no pot ser negativa.")
    if discount_pct.as_tuple().exponent < -decimals:
        raise ScoringInputInvalid(f"La baixa admet com a molt {decimals} decimals.")
    return _round(node.params["PL"] * (1 - discount_pct / 100))


def economic_points(
    system: ScoringSystem, node_id: str, offered: Decimal, lowest_admitted: Decimal
) -> Decimal:
    """Pv = [1 - ((Ov - Om)/PL) x (1/VP)] x P, arrodonida a 2 decimals i dins de [0, P]."""
    node = system.node(node_id)
    rule = _verified(system, node, RuleKind.FORMULA)
    if offered < lowest_admitted:
        raise ScoringInputInvalid("Ov no pot ser inferior a Om (el menor import admes).")
    pl, vp, p = node.params["PL"], rule.params["VP"], node.max_points
    value = (1 - ((offered - lowest_admitted) / pl) * (1 / vp)) * p
    return min(max(_round(value), Decimal(0)), p)


def automatic_points(system: ScoringSystem, node_id: str, answer: dict) -> Decimal:
    node = system.node(node_id)
    if node.evaluation is Evaluation.YES_NO:
        _verified(system, node, RuleKind.YES_NO)
        if not isinstance(answer.get("yes"), bool):
            raise ScoringInputInvalid(f"{node_id}: cal una resposta Sí/No.")
        if not answer["yes"]:
            return Decimal(0)
        if node.requires_accreditation:
            if not isinstance(answer.get("accredited"), bool):
                raise ScoringInputMissing([f"{node_id}: cal saber si la millora queda acreditada"])
            if not answer["accredited"]:
                return Decimal(0)
        return node.max_points
    if node.evaluation is Evaluation.LEVELS:
        rule = _verified(system, node, RuleKind.LEVELS)
        selected = answer.get("levels")
        if not isinstance(selected, list) or not selected:
            raise ScoringInputInvalid(f"{node_id}: cal la llista d'opcions seleccionades.")
        if len(selected) > 1:
            return Decimal(0)  # mes d'una opcio seleccionada: 0 punts (regla del plec)
        for level in rule.levels:
            if level.value == Decimal(str(selected[0])):
                return level.points
        raise ScoringInputInvalid(f"{node_id}: {selected[0]} no es cap dels nivells admesos.")
    raise ScoringInputInvalid(f"{node_id} no es un criteri automatic.")


def judgment_points(system: ScoringSystem, node_id: str, committee_score: Decimal) -> Decimal:
    node = system.node(node_id)
    rule = _verified(system, node, RuleKind.RUBRIC)
    if committee_score not in rule.rubric:
        raise ScoringInputInvalid(
            f"{node_id}: {committee_score} no es un valor de la rubrica {list(rule.rubric)}."
        )
    return committee_score


def scene_points(system: ScoringSystem, node_id: str, outcome: str) -> Decimal:
    node = system.node(node_id)
    rule = _verified(system, node, RuleKind.SCENES)
    if outcome not in rule.shares:
        raise ScoringInputInvalid(
            f"{node_id}: resultat {outcome!r} no previst ({sorted(rule.shares)})."
        )
    return _round(node.max_points * rule.shares[outcome])


@dataclass
class OfferScore:
    total: Decimal
    by_node: dict[str, Decimal] = field(default_factory=dict)


def score_offer(system: ScoringSystem, offer: dict) -> OfferScore:
    """Puntua una oferta SENCERA o no la puntua. `offer`:
    {"economic": {node: {"discount": "5", "lowest_admitted": "70000"}},
     "automatic": {node: {"yes": true, "accredited": true} | {"levels": [20]}},
     "judgment": {node: "3"}, "scenes": {node: "complete"|"minor"|"none"}}"""
    missing_rules: list[str] = []
    missing_inputs: list[str] = []
    for node in system.leaves():
        needed = {
            Evaluation.ECONOMIC_FORMULA: RuleKind.FORMULA,
            Evaluation.YES_NO: RuleKind.YES_NO,
            Evaluation.LEVELS: RuleKind.LEVELS,
            Evaluation.JUDGMENT: RuleKind.RUBRIC,
            Evaluation.SCENE: RuleKind.SCENES,
        }.get(node.evaluation)
        rule = _rule(system, node, needed) if needed else None
        if needed is None or rule is None or rule.status is not Status.VERIFIED:
            missing_rules.append(
                f"{node.id}: regla {needed.value if needed else '?'} "
                f"{'absent' if rule is None else rule.status.value}"
            )
    if missing_rules:
        raise ScoringRuleMissing(missing_rules)
    by_node: dict[str, Decimal] = {}
    for node in system.leaves():
        if node.evaluation is Evaluation.ECONOMIC_FORMULA:
            data = (offer.get("economic") or {}).get(node.id)
            if not data:
                missing_inputs.append(f"{node.id}: baixa i menor import admes")
                continue
            offered = offered_amount(system, node.id, Decimal(str(data["discount"])))
            by_node[node.id] = economic_points(
                system, node.id, offered, Decimal(str(data["lowest_admitted"]))
            )
        elif node.evaluation in (Evaluation.YES_NO, Evaluation.LEVELS):
            data = (offer.get("automatic") or {}).get(node.id)
            if data is None:
                missing_inputs.append(f"{node.id}: resposta del model d'oferta")
                continue
            by_node[node.id] = automatic_points(system, node.id, data)
        elif node.evaluation is Evaluation.JUDGMENT:
            value = (offer.get("judgment") or {}).get(node.id)
            if value is None:
                missing_inputs.append(f"{node.id}: puntuacio de la mesa (judici de valor)")
                continue
            by_node[node.id] = judgment_points(system, node.id, Decimal(str(value)))
        elif node.evaluation is Evaluation.SCENE:
            value = (offer.get("scenes") or {}).get(node.id)
            if value is None:
                missing_inputs.append(f"{node.id}: resultat de l'escena (acta de la demostracio)")
                continue
            by_node[node.id] = scene_points(system, node.id, value)
    if missing_inputs:
        raise ScoringInputMissing(missing_inputs)
    total = sum(by_node.values(), Decimal(0))
    if total > system.total_points:
        raise ScoringInputInvalid(
            f"Total {total} > {system.total_points}: el sistema no es coherent."
        )
    return OfferScore(total=total, by_node=by_node)
