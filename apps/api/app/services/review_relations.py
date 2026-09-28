"""Relacions estructurades entre elements de revisio (S-R3).

Una relacio es dirigida (`from -> to`) i nomes pot lligar dos elements ACTIUS
de la mateixa empresa, la mateixa licitacio i el mateix tipus. La base ho
garanteix tambe: `review_item_relations` referencia els elements per
`(item_id, company_id, tender_id)`.

**On viu l'historial d'una relacio.** A la propia fila (`created_at/by`,
`archived_at/by`) i a `audit_logs`. Crear o arxivar una relacio NO crea cap
revisio de l'element: `review_items.revision` es el control de concurrencia de
la instantania i de l'estat, i si una relacio l'incrementes, un revisor que
relaciona dos elements faria fallar amb 409 la correccio que un altre revisor
esta fent sobre un d'ells sense haver-la tocat.

**Semantica que la base no pot expressar sola:**

* cap cicle per a `parent_of`, `duplicate_of` ni `supersedes` (un element no
  pot ser, directament o no, pare, duplicat o substitut de si mateix);
* un element es `duplicate_of` d'UN sol altre (plegar-lo en dos no te sentit);
* un element te UN sol pare (`parent_of` entrant), perque puntuar-lo dues
  vegades inflaria el total;
* `related_to` es simetrica: A-B i B-A son la mateixa relacio i es desa una
  sola vegada;
* entre criteris d'adjudicacio, `restatement_of` i `duplicate_of` PLEGUEN el
  criteri a la vista de puntuacio: cadascun es plega dins d'un sol altre i
  sense cicles (`review_awards`). Entre requisits no canvia res.

A PostgreSQL, les escriptures de relacions d'una licitacio es serialitzen amb un
lock d'assessorament de transaccio: dues peticions simultanies no poden tancar
un cicle entre les dues.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.enums import ReviewItemKind, ReviewRelationType
from app.models.review import ReviewItem, ReviewItemRelation
from app.schemas.review import CanonicalRelation

#: Tipus que han de formar un graf aciclic.
ACYCLIC = frozenset(
    {ReviewRelationType.PARENT_OF, ReviewRelationType.DUPLICATE_OF, ReviewRelationType.SUPERSEDES}
)
SYMMETRIC = frozenset({ReviewRelationType.RELATED_TO})
#: Entre criteris d'adjudicacio, aquests pleguen el `from` dins del `to` a la
#: vista de puntuacio: un sol desti per criteri i cap cicle, combinats. Entre
#: requisits `restatement_of` continua sent informativa i admet cicles.
AWARD_FOLDING = (ReviewRelationType.DUPLICATE_OF, ReviewRelationType.RESTATEMENT_OF)
#: Etiqueta vista des del `from` (sortint) i des del `to` (entrant).
LABELS: dict[ReviewRelationType, tuple[str, str]] = {
    ReviewRelationType.DUPLICATE_OF: ("Duplicat de", "Té un duplicat"),
    ReviewRelationType.RESTATEMENT_OF: ("També apareix com", "Reiterat a"),
    ReviewRelationType.PARENT_OF: ("Agrupa", "Forma part de"),
    ReviewRelationType.PARTIAL_MATCH_OF: ("Cobert parcialment per", "Cobreix parcialment"),
    ReviewRelationType.SUPERSEDES: ("Substitueix", "Substituït per"),
    ReviewRelationType.RELATED_TO: ("Relacionat amb", "Relacionat amb"),
}
#: Com a molt aquests salts en buscar un cicle. Amb l'escala actual (centenars
#: d'elements per licitacio) mai no s'hi arriba; es una fita de seguretat.
MAX_TRAVERSAL = 10_000


class RelationExistsError(ConflictError):
    code = "review_relation_exists"


def label(relation_type: ReviewRelationType, direction: str) -> str:
    outgoing, incoming = LABELS[relation_type]
    return outgoing if direction == "outgoing" else incoming


async def _serialise_tender(session: AsyncSession, company_id: uuid.UUID,
                            tender_id: uuid.UUID) -> None:
    """Lock de transaccio per (empresa, licitacio). No-op fora de PostgreSQL."""
    if session.get_bind().dialect.name != "postgresql":
        return
    key = uuid.uuid5(company_id, str(tender_id)).int % (2**63)
    await session.execute(sa.select(sa.func.pg_advisory_xact_lock(key)))


async def _active_item(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID, item_id: uuid.UUID
) -> ReviewItem:
    item = await session.scalar(
        sa.select(ReviewItem).where(
            ReviewItem.id == item_id,
            ReviewItem.company_id == company_id,
            ReviewItem.tender_id == tender_id,
        )
    )
    if item is None:
        raise NotFoundError("Element de revisio no trobat.")
    if item.archived_at is not None:
        raise ValidationError("Un element arxivat no es pot relacionar.")
    return item


async def _active_edges(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID,
    relation_type: ReviewRelationType,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    rows = await session.execute(
        sa.select(ReviewItemRelation.from_item_id, ReviewItemRelation.to_item_id).where(
            ReviewItemRelation.company_id == company_id,
            ReviewItemRelation.tender_id == tender_id,
            ReviewItemRelation.relation_type == relation_type,
            ReviewItemRelation.archived_at.is_(None),
        )
    )
    return [(a, b) for a, b in rows.all()]


def _reaches(edges: list[tuple[uuid.UUID, uuid.UUID]], start: uuid.UUID,
             target: uuid.UUID) -> bool:
    """Hi ha un cami `start -> ... -> target` seguint les arestes?"""
    graph: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for a, b in edges:
        graph[a].append(b)
    seen, queue, steps = {start}, deque([start]), 0
    while queue and steps < MAX_TRAVERSAL:
        node = queue.popleft()
        steps += 1
        if node == target:
            return True
        for nxt in graph[node]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    if queue:  # pragma: no cover - fita de seguretat
        raise ValidationError("La cadena de relacions es massa llarga per comprovar-la.")
    return False


async def create_relation(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    from_item_id: uuid.UUID,
    to_item_id: uuid.UUID,
    relation_type: ReviewRelationType,
    note: str | None,
    actor_id: uuid.UUID | None,
) -> ReviewItemRelation:
    """Crea la relacio si es valida. No toca cap element ni l'extraccio. No fa commit."""
    if from_item_id == to_item_id:
        raise ValidationError("Un element no es pot relacionar amb si mateix.")
    await _serialise_tender(session, company_id, tender_id)
    source = await _active_item(session, company_id, tender_id, from_item_id)
    target = await _active_item(session, company_id, tender_id, to_item_id)
    if source.kind != target.kind:
        raise ValidationError("Nomes es poden relacionar elements del mateix tipus.")

    edges = await _active_edges(session, company_id, tender_id, relation_type)
    pairs = {(from_item_id, to_item_id)}
    if relation_type in SYMMETRIC:
        pairs.add((to_item_id, from_item_id))
    existing = await session.scalar(
        sa.select(ReviewItemRelation.id).where(
            ReviewItemRelation.company_id == company_id,
            ReviewItemRelation.tender_id == tender_id,
            ReviewItemRelation.relation_type == relation_type,
            ReviewItemRelation.archived_at.is_(None),
            sa.tuple_(ReviewItemRelation.from_item_id, ReviewItemRelation.to_item_id).in_(
                list(pairs)
            ),
        )
    )
    if existing is not None:
        raise RelationExistsError(
            "Aquesta relacio ja existeix.", details={"relation_id": str(existing)}
        )
    if relation_type == ReviewRelationType.DUPLICATE_OF and any(
        a == from_item_id for a, _b in edges
    ):
        raise ValidationError("Aquest element ja es duplicat d'un altre.")
    if relation_type == ReviewRelationType.PARENT_OF and any(b == to_item_id for _a, b in edges):
        raise ValidationError("Aquest element ja forma part d'un altre.")
    if source.kind == ReviewItemKind.AWARD_CRITERION and relation_type in AWARD_FOLDING:
        # Un criteri plegat no puntua per separat: s'ha de plegar dins d'UN sol
        # altre, i la cadena no pot tornar a ell (el total seria indecidible).
        folding: list[tuple[uuid.UUID, uuid.UUID]] = []
        for kind in AWARD_FOLDING:
            folding += await _active_edges(session, company_id, tender_id, kind)
        if any(a == from_item_id for a, _b in folding):
            raise ValidationError("Aquest criteri ja es plega dins d'un altre.")
        if _reaches(folding, to_item_id, from_item_id):
            raise ValidationError(
                "Aquesta relacio tancaria un cicle.",
                details={"relation_type": relation_type.value},
            )
    if relation_type in ACYCLIC and _reaches(edges, to_item_id, from_item_id):
        raise ValidationError(
            "Aquesta relacio tancaria un cicle.", details={"relation_type": relation_type.value}
        )

    relation = ReviewItemRelation(
        company_id=company_id,
        tender_id=tender_id,
        from_item_id=from_item_id,
        to_item_id=to_item_id,
        relation_type=relation_type,
        note=note,
        created_by=actor_id,
    )
    session.add(relation)
    await session.flush()
    return relation


async def load_relation(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID, relation_id: uuid.UUID
) -> ReviewItemRelation:
    relation = await session.scalar(
        sa.select(ReviewItemRelation).where(
            ReviewItemRelation.id == relation_id,
            ReviewItemRelation.company_id == company_id,
            ReviewItemRelation.tender_id == tender_id,
        )
    )
    if relation is None:
        raise NotFoundError("Relacio no trobada.")
    return relation


async def archive_relation(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    relation_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> tuple[ReviewItemRelation, bool]:
    """Arxiu tou i idempotent: arxivar-la dues vegades no mou la data."""
    relation = await load_relation(session, company_id, tender_id, relation_id)
    if relation.archived_at is not None:
        return relation, False
    relation.archived_at = datetime.now(UTC)
    relation.archived_by = actor_id
    await session.flush()
    return relation, True


async def relations_of(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    item_ids: list[uuid.UUID],
    include_archived: bool = False,
) -> dict[uuid.UUID, list[CanonicalRelation]]:
    """Les relacions de cada element, sortints i entrants, amb el titol de l'altre."""
    if not item_ids:
        return {}
    query = sa.select(ReviewItemRelation).where(
        ReviewItemRelation.company_id == company_id,
        sa.or_(
            ReviewItemRelation.from_item_id.in_(item_ids),
            ReviewItemRelation.to_item_id.in_(item_ids),
        ),
    )
    if not include_archived:
        query = query.where(ReviewItemRelation.archived_at.is_(None))
    relations = list(
        (await session.scalars(query.order_by(ReviewItemRelation.created_at))).all()
    )
    other_ids = {r.from_item_id for r in relations} | {r.to_item_id for r in relations}
    titles: dict[uuid.UUID, str | None] = {}
    if other_ids:
        rows = await session.execute(
            sa.select(ReviewItem.id, ReviewItem.accepted_json).where(
                ReviewItem.id.in_(other_ids), ReviewItem.company_id == company_id
            )
        )
        # Un requisit te `title`; un criteri d'adjudicacio, `name`.
        titles = {
            item_id: (accepted or {}).get("title") or (accepted or {}).get("name")
            for item_id, accepted in rows.all()
        }

    wanted = set(item_ids)
    result: dict[uuid.UUID, list[CanonicalRelation]] = defaultdict(list)
    for relation in relations:
        kind = ReviewRelationType(relation.relation_type)
        for item_id, direction, other in (
            (relation.from_item_id, "outgoing", relation.to_item_id),
            (relation.to_item_id, "incoming", relation.from_item_id),
        ):
            if item_id not in wanted:
                continue
            result[item_id].append(
                CanonicalRelation(
                    relation_id=relation.id,
                    direction=direction,
                    relation_type=kind.value,
                    relation_label=label(kind, direction),
                    other_item_id=other,
                    other_item_title=titles.get(other),
                    note=relation.note,
                    foldable=kind == ReviewRelationType.DUPLICATE_OF,
                    created_at=relation.created_at,
                    archived_at=relation.archived_at if include_archived else None,
                )
            )
    return result
