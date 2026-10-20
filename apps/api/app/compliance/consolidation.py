"""Consolidacio de requisits duplicats, en el moment de llegir.

Un extractor que llegeix el plec a trossos troba la mateixa exigencia a diversos
llocs: la solvencia economica de 91.904,74 EUR surt al quadre de
caracteristiques, al PCAP i a la clausula de solvencia. Son TRES files i UNA
exigencia. Comptar-les com a tres feia que el resum digues «tres requisits per
revisar» on n'hi ha un, i que acreditar-ne un deixes els altres dos pendents.

Aqui no s'esborra ni es fusiona cap fila: cada requisit continua tenint la seva
avaluacio, amb les seves cites. El que es calcula es a quin GRUP pertany, quin
n'es el CANONIC i com es resol el grup quan se'n fa el recompte.

Criteri del grup (determinista, sense similitud):

* mateixa categoria, obligacio, abast i lot, i
* el mateix valor NORMALITZAT decisiu (`Normalization.consolidation_value`:
  import, moneda, tipus de limit, periode, agregacio); o bé, si no n'hi ha,
  el mateix titol normalitzat (`title_keys.normalized_title`) I el mateix
  `value_json` exacte. Res de «s'assembla».

Mai no s'hi agrupen: requisits manuals (no son d'un run), requisits d'una via
alternativa (els resol `alternatives`) ni requisits de runs diferents.

El CANONIC es el de mes cites verificades; en empat, el mes antic
(`created_at`) i, si encara empaten, el d'identificador mes petit. Es estable:
la mateixa entrada dona sempre el mateix canonic.

La RESOLUCIO d'un grup per al recompte (`resolve_rows`) es la prudent: el pitjor
resultat dels seus membres (no compleix > cal revisar > sense dades > compleix
> no aplica) i el bloqueig mes fort. Si dos duplicats diuen coses diferents —no
hauria de passar amb el mateix valor, pero una revisio humana d'un sol membre
ho pot provocar— el grup no s'acredita fins que tots ho estan, i l'avaluacio ho
marca (`consolidation.divergent`).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.compliance.normalize import Normalization, canonical, verified_quotes
from app.models.enums import AssessmentResult, BlockerStatus, RequirementObligation
from app.services.title_keys import normalized_title

#: Clau de `explanation_params` on viu la pertinenca a un grup.
PARAMS_KEY = "consolidation"

#: Ordre de prudencia: el que mana quan els membres d'un grup no coincideixen.
RESULT_SEVERITY: dict[AssessmentResult, int] = {
    AssessmentResult.DOES_NOT_SATISFY: 4,
    AssessmentResult.NEEDS_REVIEW: 3,
    AssessmentResult.UNKNOWN: 2,
    AssessmentResult.SATISFIES: 1,
    AssessmentResult.NOT_APPLICABLE: 0,
}
BLOCKER_SEVERITY: dict[BlockerStatus, int] = {
    BlockerStatus.CONFIRMED: 2,
    BlockerStatus.POTENTIAL: 1,
    BlockerStatus.NONE: 0,
}


@dataclass(frozen=True, slots=True)
class Group:
    key: str
    canonical_id: uuid.UUID
    #: Tots els membres, el canonic primer.
    member_ids: tuple[uuid.UUID, ...]

    @property
    def size(self) -> int:
        return len(self.member_ids)


def consolidation_key(requirement: Any, normalization: Normalization | None) -> str | None:
    """La clau d'exigencia d'un requisit. `None` = no s'agrupa mai."""
    if getattr(requirement, "run_id", None) is None:
        return None
    if getattr(requirement, "alternative_group", None):
        return None
    base = [
        str(requirement.run_id),
        str(requirement.category),
        str(requirement.obligation),
        str(getattr(requirement, "scope", None)),
        str(getattr(requirement, "tender_lot_id", None)),
    ]
    value = normalization.consolidation_value() if normalization is not None else None
    if value is not None:
        return canonical(["value", *base, value])
    return canonical(
        [
            "title",
            *base,
            normalized_title(requirement.title or ""),
            canonical(getattr(requirement, "value_json", None)),
        ]
    )


def _canonical_order(requirement: Any) -> tuple[int, str, str]:
    created: datetime | None = getattr(requirement, "created_at", None)
    return (
        -len(verified_quotes(requirement)),
        created.isoformat() if created is not None else "",
        str(requirement.id),
    )


def build_groups(
    entries: Iterable[tuple[Any, Normalization | None]],
) -> dict[uuid.UUID, Group]:
    """Grup de cada requisit que en te (dos membres o mes). Els sols no hi son."""
    by_key: dict[str, list[Any]] = {}
    for requirement, normalization in entries:
        key = consolidation_key(requirement, normalization)
        if key is not None:
            by_key.setdefault(key, []).append(requirement)

    groups: dict[uuid.UUID, Group] = {}
    for key, members in by_key.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_canonical_order)
        group = Group(
            key=key,
            canonical_id=ordered[0].id,
            member_ids=tuple(member.id for member in ordered),
        )
        for member in ordered:
            groups[member.id] = group
    return groups


def params_for(group: Group, requirement_id: uuid.UUID, *, divergent: bool) -> dict[str, Any]:
    """El que es guarda a `explanation_params.consolidation` d'un membre."""
    return {
        "canonical_requirement_id": str(group.canonical_id),
        "is_canonical": requirement_id == group.canonical_id,
        "member_requirement_ids": [str(member) for member in group.member_ids],
        "group_size": group.size,
        "divergent": divergent,
    }


def group_marker(requirement_id: uuid.UUID, params: dict[str, Any] | None) -> str:
    """A quin grup compta una avaluacio guardada: el seu canonic, o ella sola."""
    consolidation = (params or {}).get(PARAMS_KEY) or {}
    canonical_id = consolidation.get("canonical_requirement_id")
    return str(canonical_id) if canonical_id else str(requirement_id)


def resolve(
    results: Iterable[tuple[AssessmentResult, BlockerStatus]],
) -> tuple[AssessmentResult, BlockerStatus]:
    """El resultat prudent d'un grup: el pitjor resultat i el bloqueig mes fort."""
    items = list(results)
    result = max((item[0] for item in items), key=RESULT_SEVERITY.__getitem__)
    blocker = max((item[1] for item in items), key=BLOCKER_SEVERITY.__getitem__)
    return result, blocker


def resolve_rows(
    items: Iterable[tuple[str, RequirementObligation, AssessmentResult, BlockerStatus]],
) -> list[tuple[RequirementObligation, AssessmentResult, BlockerStatus]]:
    """Una fila per EXIGENCIA per a `summarise`: els membres d'un grup en fan una.

    `items` son `(marcador de grup, obligacio, resultat efectiu, bloqueig)`; el
    marcador d'un requisit sol es el seu propi id (`group_marker`). L'ordre de
    sortida es el de la primera aparicio de cada grup.
    """
    grouped: dict[str, list[tuple[RequirementObligation, AssessmentResult, BlockerStatus]]] = {}
    for marker, obligation, result, blocker in items:
        grouped.setdefault(marker, []).append((obligation, result, blocker))
    rows = []
    for members in grouped.values():
        result, blocker = resolve((result, blocker) for _, result, blocker in members)
        # La clau del grup inclou l'obligacio: tots els membres la comparteixen.
        rows.append((members[0][0], result, blocker))
    return rows


__all__ = [
    "PARAMS_KEY",
    "Group",
    "build_groups",
    "consolidation_key",
    "group_marker",
    "params_for",
    "resolve",
    "resolve_rows",
]
