"""Que passa amb la feina feta quan el plec es torna a analitzar.

Una reanalisi no es un cas de laboratori: passa quan l'organ publica una versio
nova del plec, quan l'extractor millora, quan algu corregeix un requisit a ma o
quan simplement es torna a executar. Cada vegada neixen files de `Requirement`
noves, amb identificadors nous, i les d'abans queden en un run que ja no es
`is_current`.

El que feia el sistema era afegir el que faltava i prou. Ningu no retirava els
items vinculats als requisits vells, aixi que el dossier acumulava files que
deien "Falta document" per exigencies que ja no existien, i la checklist podia
quedar sencera penjada d'una extraccio superada mentre el workspace ja es
declarava reconciliat. La readiness comptava tot aquell soroll.

Aquest modul decideix **nomes una cosa**: per cada item existent, si la feina
que porta a sobre es pot continuar aplicant al requisit vigent equivalent o si
l'item ja es historia. No toca la base de dades, no llegeix res: rep fets i
torna un pla.

La identitat que fa servir es `requirement_fingerprint`, la mateixa que ja
lliga una confirmacio humana de pertinença al que demanava el plec quan es va
signar. Aixo no es una coincidencia comoda: es exactament la propietat que ens
cal. Si l'empremta coincideix, el requisit demana el mateix i el que una
persona va afirmar segueix sent cert. Si no coincideix, el requisit ha canviat
materialment i **res del que es va confirmar no s'hereta**, per molt que els
dos titols s'assemblin.

Tres desenllaços per item:

* **rebind** -> el requisit vigent demana el mateix: l'item hi passa a apuntar
  i conserva document, tria, notes i confirmacio.
* **supersede** -> no hi ha equivalent vigent, o ja hi ha un item millor
  ocupant-ne el lloc. La fila es queda —es historia— pero deixa de ser activa.
* **drop** -> era una proposta automatica que ningu no havia tocat i ara esta
  duplicada. Esborrar-la no perd res: no hi havia cap afirmacio humana a dins.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ItemFacts:
    """El minim que cal saber d'un item per decidir-ne el desti."""

    id: uuid.UUID
    #: El requisit al qual apunta ara. `None` = peca solta, no es reconcilia.
    requirement_id: uuid.UUID | None
    #: Empremta del requisit al qual apunta. `None` si no n'hi ha cap.
    fingerprint: str | None
    #: Cert quan el seu requisit pertany al run vigent.
    on_current_run: bool
    #: Quanta feina humana porta a sobre. Veure `human_weight`.
    weight: int
    #: Amb quin document. Dos items del mateix requisit no poden compartir-lo,
    #: i `None` (el marcador de forat) tampoc es pot duplicar.
    document_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Rebind:
    item_id: uuid.UUID
    requirement_id: uuid.UUID


@dataclass(frozen=True)
class Plan:
    """Que fer amb cada item. Els requisits vigents que no surten enlloc son
    els que encara no tenen item: els crea qui ja ho feia."""

    rebind: tuple[Rebind, ...] = ()
    supersede: tuple[uuid.UUID, ...] = ()
    drop: tuple[uuid.UUID, ...] = ()
    #: Empremtes vigents que ja tenen algu que les cobreix despres del pla.
    covered: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_empty(self) -> bool:
        return not (self.rebind or self.supersede or self.drop)


#: Res de tot aixo no ho ha escrit una persona: es una proposta del motor que
#: ningu no ha mirat. Es el unic cas en que una fila es pot esborrar.
PRISTINE = 0


def human_weight(
    *,
    relevance_confirmed: bool = False,
    selected: bool = False,
    touched: bool = False,
    manual: bool = False,
) -> int:
    """Quanta feina humana porta l'item. Com mes alt, mes val la pena salvar-lo.

    L'ordre no es arbitrari: una confirmacio de pertinença es una afirmacio
    signada amb nom i motiu, triar un document es una decisio, i haver-hi
    escrit una nota o haver-lo posat a ma es, com a minim, haver-hi passat.
    """
    if relevance_confirmed:
        return 3
    if selected:
        return 2
    if touched or manual:
        return 1
    return PRISTINE


def plan_migration(
    items: Sequence[ItemFacts], current_by_fingerprint: Mapping[str, uuid.UUID]
) -> Plan:
    """El desti de cada item, agrupant per empremta.

    `current_by_fingerprint` son els requisits del run vigent indexats per la
    seva empremta. El que no hi surt es que el plec ja no ho demana.
    """
    groups: dict[str, list[ItemFacts]] = {}
    rebind: list[Rebind] = []
    supersede: list[uuid.UUID] = []
    drop: list[uuid.UUID] = []
    covered: set[str] = set()

    for item in items:
        if item.requirement_id is None or item.fingerprint is None:
            # Peces soltes: no depenen de cap requisit i no es reconcilien.
            continue
        groups.setdefault(item.fingerprint, []).append(item)

    for fingerprint, group in groups.items():
        target = current_by_fingerprint.get(fingerprint)
        if target is None:
            # El plec ja no demana aixo. Tot el grup passa a historia, tambe el
            # que algu hagues seleccionat: seguir comptant-ho seria preparar
            # documentacio per a una exigencia que no existeix.
            supersede.extend(item.id for item in group)
            continue

        covered.add(fingerprint)
        on_target = [item for item in group if item.requirement_id == target]
        stale = [item for item in group if item.requirement_id != target]
        # Els llocs ja ocupats al requisit vigent: dos items del mateix requisit
        # no poden compartir document, ni pot haver-hi dos marcadors de forat.
        taken = {item.document_id for item in on_target}

        for item in sorted(stale, key=lambda i: (-i.weight, i.id.bytes)):
            if item.weight == PRISTINE:
                # Una proposta que ningu no ha tocat i que el run nou ja torna a
                # proposar. Esborrar-la no perd cap afirmacio.
                drop.append(item.id)
                continue
            if item.document_id in taken:
                # El lloc esta ocupat per un item que ja penja del requisit
                # vigent. Moure'l alla xocaria; es queda com a historia.
                supersede.append(item.id)
                continue
            rebind.append(Rebind(item_id=item.id, requirement_id=target))
            taken.add(item.document_id)

    return Plan(
        rebind=tuple(rebind),
        supersede=tuple(supersede),
        drop=tuple(drop),
        covered=frozenset(covered),
    )


def superseded_ids(items: Iterable[ItemFacts], plan: Plan) -> frozenset[uuid.UUID]:
    """Quins items queden inactius un cop aplicat el pla.

    Es deriva, no es guarda: un item es actiu si el seu requisit es del run
    vigent. Guardar-ho en una columna voldria dir que un dia podria dir una cosa
    diferent de la que diuen les dades, i aquest es precisament el problema que
    aquest modul existeix per no tenir.
    """
    moved = {entry.item_id for entry in plan.rebind}
    removed = set(plan.drop)
    return frozenset(
        item.id
        for item in items
        if item.id not in removed
        and item.id not in moved
        and item.requirement_id is not None
        and not item.on_current_run
    )
