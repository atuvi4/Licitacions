"""Sistema de puntuacio d'una licitacio, estructurat i tracable (QUALITY-01).

Representacio PURA (sense base de dades ni model): un arbre de nodes
(familia -> subcriteri -> escena)
i unes regles (formules, nivells, rubriques, llindars) amb la seva procedencia documental. Res no
s'omple per suposicio: un camp que la font no dona queda absent i la regla es `pending`.

Estats:
* `verified`: el contingut s'ha comprovat contra la font oficial (cita literal a la pagina o cel·la
  indicada) i, si es una formula, s'ha recalculat l'exemple oficial quan n'hi ha.
* `pending`: falta informacio o no s'ha pogut comprovar. Una regla `pending` mai no puntua.
* `conflict`: dues fonts oficials diuen coses diferents. Tampoc no puntua.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    VERIFIED = "verified"
    PENDING = "pending"
    CONFLICT = "conflict"


class Evaluation(StrEnum):
    GROUP = "group"  # suma dels fills
    ECONOMIC_FORMULA = "economic_formula"
    YES_NO = "yes_no"
    LEVELS = "levels"
    JUDGMENT = "judgment"  # el puntua la mesa; Tuvi nomes en coneix el maxim i la rubrica
    SCENE = "scene"  # escena d'una demostracio (fill d'un node de judici)


class RuleKind(StrEnum):
    FORMULA = "formula"
    YES_NO = "yes_no"
    LEVELS = "levels"
    RUBRIC = "rubric"
    SCENES = "scenes"
    THRESHOLD = "threshold"  # regla separada: no puntua (p. ex. oferta anormalment baixa)
    CONDITION = "condition"


class SpecError(ValueError):
    """L'especificacio no es valida (camp desconegut, tipus incorrecte...)."""


@dataclass(frozen=True, slots=True)
class SourceRef:
    source: str  # clau de `documents`
    quote: str
    page: int | None = None
    locator: str | None = None  # p. ex. "Automatics!F7" (xlsx)


@dataclass(frozen=True, slots=True)
class Level:
    value: Decimal
    points: Decimal


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    kind: RuleKind
    status: Status
    description: str
    sources: tuple[SourceRef, ...]
    expression: str | None = None
    variables: dict[str, str] = field(default_factory=dict)
    params: dict[str, Decimal] = field(default_factory=dict)
    levels: tuple[Level, ...] = ()
    rubric: tuple[Decimal, ...] = ()
    shares: dict[str, Decimal] = field(default_factory=dict)  # escenes: resultat -> fraccio
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    name: str
    evaluation: Evaluation
    status: Status
    sources: tuple[SourceRef, ...]
    parent: str | None = None
    max_points: Decimal | None = None
    rules: tuple[str, ...] = ()
    params: dict[str, Decimal] = field(default_factory=dict)
    requires_accreditation: bool = False
    envelope: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ScoringSystem:
    tender: dict[str, str]
    spec_version: str
    total_points: Decimal
    documents: dict[str, dict[str, str]]
    nodes: tuple[Node, ...]
    rules: tuple[Rule, ...]
    pending: tuple[str, ...] = ()

    def node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def rule(self, rule_id: str) -> Rule:
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        raise KeyError(rule_id)

    def children(self, node_id: str | None) -> list[Node]:
        return [node for node in self.nodes if node.parent == node_id]

    def leaves(self) -> list[Node]:
        """Nodes que puntuen directament: cap fill (les escenes son fills d'un node de judici)."""
        parents = {node.parent for node in self.nodes}
        return [node for node in self.nodes if node.id not in parents]


# --------------------------------------------------------------------------- #
# Carrega estricta                                                              #
# --------------------------------------------------------------------------- #


def _decimal(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SpecError(f"{where}: cal un decimal en text o enter, no {type(value).__name__}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise SpecError(f"{where}: decimal no valid {value!r}") from exc
    # Punts, parametres, nivells, rubriques i fraccions d'un plec: sempre finits i no negatius.
    if not number.is_finite() or number < 0:
        raise SpecError(f"{where}: cal un decimal finit i no negatiu, no {value!r}")
    return number


def _share(value: Any, where: str) -> Decimal:
    share = _decimal(value, f"{where}.shares")
    if share > 1:
        raise SpecError(f"{where}.shares: una fraccio no pot passar de 1 ({value!r})")
    return share


def _only(data: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SpecError(f"{where}: camps desconeguts {unknown}")


def _sources(items: Any, where: str) -> tuple[SourceRef, ...]:
    if not isinstance(items, list):
        raise SpecError(f"{where}.sources: cal una llista")
    out = []
    for i, item in enumerate(items):
        _only(item, {"source", "quote", "page", "locator"}, f"{where}.sources[{i}]")
        if not item.get("source") or not item.get("quote"):
            raise SpecError(f"{where}.sources[{i}]: cal `source` i `quote`")
        out.append(
            SourceRef(
                source=item["source"],
                quote=item["quote"],
                page=item.get("page"),
                locator=item.get("locator"),
            )
        )
    return tuple(out)


def _flag(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise SpecError(f"{where}: cal true o false, no {value!r}")
    return value


def load_system(data: dict) -> ScoringSystem:
    """Carrega estricta: qualsevol camp absent, desconegut o mal tipat es `SpecError`."""
    try:
        return _load_system(data)
    except SpecError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise SpecError(f"spec: camp obligatori absent o valor no valid ({exc!r})") from exc


def _load_system(data: dict) -> ScoringSystem:
    _only(
        data,
        {"tender", "spec_version", "total_points", "documents", "nodes", "rules", "pending"},
        "spec",
    )
    rules = []
    for i, raw in enumerate(data.get("rules") or []):
        where = f"rules[{i}]"
        _only(
            raw,
            {
                "id",
                "kind",
                "status",
                "description",
                "sources",
                "expression",
                "variables",
                "params",
                "levels",
                "rubric",
                "shares",
                "notes",
            },
            where,
        )
        rules.append(
            Rule(
                id=raw["id"],
                kind=RuleKind(raw["kind"]),
                status=Status(raw["status"]),
                description=raw["description"],
                sources=_sources(raw.get("sources", []), where),
                expression=raw.get("expression"),
                variables=dict(raw.get("variables") or {}),
                params={
                    k: _decimal(v, f"{where}.params.{k}")
                    for k, v in (raw.get("params") or {}).items()
                },
                levels=tuple(
                    Level(_decimal(lv["value"], where), _decimal(lv["points"], where))
                    for lv in raw.get("levels") or []
                ),
                rubric=tuple(_decimal(v, where) for v in raw.get("rubric") or []),
                shares={k: _share(v, where) for k, v in (raw.get("shares") or {}).items()},
                notes=raw.get("notes"),
            )
        )
    nodes = []
    for i, raw in enumerate(data.get("nodes") or []):
        where = f"nodes[{i}]"
        _only(
            raw,
            {
                "id",
                "name",
                "evaluation",
                "status",
                "sources",
                "parent",
                "max_points",
                "rules",
                "params",
                "requires_accreditation",
                "envelope",
                "notes",
            },
            where,
        )
        nodes.append(
            Node(
                id=raw["id"],
                name=raw["name"],
                evaluation=Evaluation(raw["evaluation"]),
                status=Status(raw["status"]),
                sources=_sources(raw.get("sources", []), where),
                parent=raw.get("parent"),
                max_points=_decimal(raw["max_points"], where)
                if raw.get("max_points") is not None
                else None,
                rules=tuple(raw.get("rules") or ()),
                params={
                    k: _decimal(v, f"{where}.params.{k}")
                    for k, v in (raw.get("params") or {}).items()
                },
                requires_accreditation=_flag(
                    raw.get("requires_accreditation", False), f"{where}.requires_accreditation"
                ),
                envelope=raw.get("envelope"),
                notes=raw.get("notes"),
            )
        )
    return ScoringSystem(
        tender=dict(data.get("tender") or {}),
        spec_version=data["spec_version"],
        total_points=_decimal(data["total_points"], "total_points"),
        documents={k: dict(v) for k, v in (data.get("documents") or {}).items()},
        nodes=tuple(nodes),
        rules=tuple(rules),
        pending=tuple(data.get("pending") or ()),
    )
