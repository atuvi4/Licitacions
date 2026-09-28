"""Previsualitzacio de reconciliacio despres d'una nova extraccio (S-R8). NOMES LECTURA.

Quan la revisio d'una empresa representa la run A i la vigent es B, aquest
modul diu que CALDRIA fer per posar-la al dia. No escriu res: ni fonts, ni
revisions, ni auditoria. La guarda de `start` (S-R7) continua bloquejant.

**Fonts velles.** Nomes element actiu -> font PRIMARIA activa -> fila extreta
(`review_runs.active_primary_links`, la mateixa definicio que la guarda). Els
manuals no entren mai a l'aparellament: no poden quedar `removed`.

**Instantanies.** Es construeixen amb les MATEIXES funcions que la
materialitzacio (`snapshot_of`, `award_snapshot_of`): el que es compara es el
que `start` hauria acceptat.

**Classes** (cap no escriu en aquesta fase):

* `exact_auto`: la mateixa font, demostrat de manera determinista. Vol dir
  "candidata segura per a un futur reenllaç automatic", no "reenllaça-la".
  Predicat: les dues runs tenen el mateix `input_fingerprint` i el mateix
  contracte (versions d'extractor, prompt i esquema); i la signatura exacta
  coincideix: `dedup_key` + instantania de materialitzacio sencera + el
  multiconjunt de cites (extraccio, document, pagina, posicions, hash del text
  literal, seccio, paper). Ha de ser u a u; qualsevol col·lisio es `ambiguous`.
  No cal el mateix proveidor/model: si tot el contingut desat i totes les cites
  son identics, la fila diu el mateix; el contracte i l'entrada si que calen.
* `review_candidate`: una sola correspondencia plausible per ancoratge de cita
  (mateix document logic + mateix text normalitzat, >= 16 caracters; en
  criteris, tambe el mateix nom normalitzat) i la mateixa familia (categoria o
  tipus de criteri). Cal una persona.
* `ambiguous`: mes d'una correspondencia, una cita ja presa per una
  coincidencia exacta, families diferents, o signatura exacta repetida. Es
  torna tot el conjunt, sense guanyador.
* `new`: fila de la run vigent sense cap contrapart. `removed`: element vell
  sense cap font a la run vigent.

`dedup_key` es un senyal exacte, mai la identitat: sol no aparella res i mai
no es compara de manera aproximada. Tampoc els ids de fila entre runs.

Cost: diccionaris per signatura i per ancoratge + union-find, O(vells + nous
+ col·lisions). Consultes en lot, sense N+1.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    ReviewAction,
    ReviewAwardRole,
    ReviewItemKind,
    ReviewReconciliationState,
    ReviewSourceRole,
    ReviewVerification,
)
from app.models.requirements import AwardCriterion, Requirement, RequirementExtractionRun
from app.models.review import (
    ReviewItem,
    ReviewItemRelation,
    ReviewItemRevision,
    ReviewItemSource,
    ReviewReconciliation,
)
from app.schemas.review import AcceptedValue, AwardSnapshot, RequirementSnapshot
from app.schemas.review_reconciliation import (
    AwardAcceptedDiff,
    AwardAmbiguity,
    AwardMatch,
    AwardNew,
    AwardRemoved,
    AwardScoring,
    AwardSection,
    AwardSource,
    AwardSourceDiff,
    HumanState,
    MatchCounts,
    ReconciliationPreview,
    ReconciliationSummary,
    RequirementAcceptedDiff,
    RequirementAmbiguity,
    RequirementMatch,
    RequirementNew,
    RequirementRemoved,
    RequirementSection,
    RequirementSource,
    RequirementSourceDiff,
    SourceLocation,
    SourceValue,
)
from app.services import review_runs
from app.services.review_awards import (
    ROLE_LABELS,
    ROLE_UNASSIGNED,
    _points_differ,
    _structure,
    award_snapshot_of,
)
from app.services.review_view import (
    CATEGORY_LABELS,
    PROVENANCE_LABELS,
    VERIFICATION_LABELS,
    _document_names,
    obligation_label,
    provenance_of,
    snapshot_of,
)

# --------------------------------------------------------------------------- #
# Vocabulari                                                                    #
# --------------------------------------------------------------------------- #

NO_CURRENT_RUN = "no_current_run"
NOT_STARTED = "not_started"
UP_TO_DATE = "up_to_date"
NEW_EXTRACTION = "new_extraction_available"
MIXED = "mixed_source_runs"
#: Una reconciliacio oberta cap a la run vigent explica que les fonts barregin
#: la base d'origen i la run desti. Qualsevol altra barreja continua sent MIXED.
IN_PROGRESS = "reconciliation_in_progress"

STATE_LABELS = {
    NO_CURRENT_RUN: "Encara no hi ha cap extracció vigent",
    NOT_STARTED: "Revisió no iniciada",
    UP_TO_DATE: "Al dia amb l'extracció vigent",
    NEW_EXTRACTION: "Hi ha una extracció nova",
    MIXED: "La revisió barreja extraccions diferents",
    IN_PROGRESS: "Reconciliació en curs",
}
EXACT_AUTO = "exact_auto"
REVIEW_CANDIDATE = "review_candidate"
AMBIGUOUS = "ambiguous"
NEW = "new"
REMOVED = "removed"
MATCH_LABELS = {
    EXACT_AUTO: "Coincidència exacta",
    REVIEW_CANDIDATE: "Possible coincidència (cal revisar)",
    AMBIGUOUS: "Ambigu",
    NEW: "Nou a l'extracció",
    REMOVED: "Ja no apareix a l'extracció",
}
REASON_LABELS = {
    "duplicate_exact_signature": "La mateixa font apareix més d'una vegada",
    "multiple_candidates": "Hi ha més d'una correspondència possible",
    "conflicting_family": "La cita coincideix però el tipus no",
    "candidate_already_matched": "La cita ja correspon a un altre element",
}

#: Un ancoratge mes curt ("Si", "Preu") no identifica res: no es fa servir.
MIN_ANCHOR_CHARS = 16
_OPERATOR_CODES = {"<": "lt", "<=": "lte", ">": "gt", ">=": "gte", "=": "eq"}


# --------------------------------------------------------------------------- #
# Normalitzacio explicita i signatures                                          #
# --------------------------------------------------------------------------- #


def normalise_text(text: str | None) -> str:
    """NFKC + minuscules + espais col·lapsats. Res mes: sense similitud aproximada."""
    if not text:
        return ""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                         default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def evidence_signature(evidence: Iterable[Any]) -> list[list[Any]]:
    """Cites desades, sense ids de fila: extraccio, document, pagina, posicions, text."""
    rows = [
        [
            str(e.extraction_id),
            str(e.tender_document_id) if e.tender_document_id else None,
            e.page_number,
            e.char_start,
            e.char_end,
            _sha(e.quote),
            e.section,
            e.evidence_role.value if e.evidence_role is not None else None,
        ]
        for e in evidence
    ]
    return sorted(rows, key=lambda r: json.dumps(r, default=str))


def anchor_keys(evidence: Iterable[Any]) -> set[str]:
    """Mateix document logic + mateix text normalitzat. La pagina no hi entra."""
    keys = set()
    for e in evidence:
        text = normalise_text(e.quote)
        if len(text) < MIN_ANCHOR_CHARS:
            continue
        slot = str(e.tender_document_id) if e.tender_document_id else f"x:{e.extraction_id}"
        keys.add("q:" + _digest([slot, text]))
    return keys


def _scalar(value: Any) -> list[Any]:
    if isinstance(value, bool):
        return ["b", value]
    if isinstance(value, int | float):
        return ["n", float(value) if math.isfinite(float(value)) else str(value)]
    if isinstance(value, str):
        return ["s", normalise_text(value)]
    return ["z", None]


def values_signature(values: Sequence[AcceptedValue]) -> list[list[Any]]:
    """Operador + valor + unitat. La clau i l'origen (document/revisor) NO hi compten:
    una correccio humana `lt 70 ms` i una extraccio `lt 70 ms` diuen el mateix."""
    rows = [
        [
            _OPERATOR_CODES.get(v.operator, v.operator) if v.operator else None,
            _scalar(v.value),
            normalise_text(v.unit_label) or None,
        ]
        for v in values
    ]
    return sorted(rows, key=lambda r: json.dumps(r, default=str))


def _text_changed(a: str | None, b: str | None) -> bool:
    return (a or "").strip() != (b or "").strip()


def _number_changed(a: float | None, b: float | None) -> bool:
    """Com `_points_differ` de la puntuacio, pero aparèixer o desaparèixer tambe es canvi."""
    return (a is None) != (b is None) or _points_differ(a, b)


# --------------------------------------------------------------------------- #
# Classificador pur                                                             #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Source:
    """Una fila extreta vista pel classificador."""

    id: uuid.UUID
    exact_key: str
    anchors: frozenset[str]
    #: Categoria (requisits) o tipus de criteri: si difereix, no es candidat.
    family: str


@dataclass(frozen=True)
class Old:
    item_id: uuid.UUID
    source: Source


@dataclass
class Ambiguity:
    reason: str
    olds: list[Old]
    candidates: list[Source]


@dataclass
class Plan:
    exact: list[tuple[Old, Source]] = field(default_factory=list)
    candidates: list[tuple[Old, Source]] = field(default_factory=list)
    ambiguous: list[Ambiguity] = field(default_factory=list)
    new: list[Source] = field(default_factory=list)
    removed: list[Old] = field(default_factory=list)

    def counts(self) -> MatchCounts:
        return MatchCounts(
            exact_auto=len(self.exact),
            review_candidate=len(self.candidates),
            ambiguous=len(self.ambiguous),
            new=len(self.new),
            removed=len(self.removed),
        )


def classify(
    olds: Sequence[Old],
    news: Sequence[Source],
    *,
    exact_allowed: bool,
    claimed: Sequence[Source] = (),
) -> Plan:
    """Pura i determinista. Cap desempat arbitrari.

    `claimed`: files de la run desti que ja son la font primaria d'un element
    (reconciliacio en curs). No s'aparellen de nou, pero un element vell que hi
    toca per ancoratge queda `ambiguous` (`candidate_already_matched`), igual
    que abans d'aplicar: aplicar no canvia la classificacio de la resta.
    """
    plan = Plan()
    used_old: set[uuid.UUID] = set()
    used_new: set[uuid.UUID] = set()

    if exact_allowed:
        old_by: dict[str, list[Old]] = defaultdict(list)
        new_by: dict[str, list[Source]] = defaultdict(list)
        for old in olds:
            old_by[old.source.exact_key].append(old)
        for new in news:
            new_by[new.exact_key].append(new)
        for key in sorted(old_by.keys() & new_by.keys()):
            group_old, group_new = old_by[key], new_by[key]
            if len(group_old) == 1 and len(group_new) == 1:
                plan.exact.append((group_old[0], group_new[0]))
            else:
                plan.ambiguous.append(
                    Ambiguity("duplicate_exact_signature", group_old, group_new))
            used_old.update(o.item_id for o in group_old)
            used_new.update(n.id for n in group_new)

    rest_old = [o for o in olds if o.item_id not in used_old]
    rest_new = [n for n in news if n.id not in used_new]
    taken = {n.id: n for n in news if n.id in used_new} | {n.id: n for n in claimed}
    free_by_anchor: dict[str, list[Source]] = defaultdict(list)
    taken_by_anchor: dict[str, list[Source]] = defaultdict(list)
    for new in news:
        target = taken_by_anchor if new.id in used_new else free_by_anchor
        for anchor in new.anchors:
            target[anchor].append(new)
    for new in claimed:
        for anchor in new.anchors:
            taken_by_anchor[anchor].append(new)

    parent: dict[tuple[str, uuid.UUID], tuple[str, uuid.UUID]] = {}

    def find(node: tuple[str, uuid.UUID]) -> tuple[str, uuid.UUID]:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: tuple[str, uuid.UUID], b: tuple[str, uuid.UUID]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb, key=str)] = min(ra, rb, key=str)

    blocked: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for old in rest_old:
        find(("o", old.item_id))
        for anchor in old.source.anchors:
            for new in free_by_anchor.get(anchor, ()):
                union(("o", old.item_id), ("n", new.id))
            for new in taken_by_anchor.get(anchor, ()):
                blocked[old.item_id].add(new.id)

    groups: dict[tuple[str, uuid.UUID], tuple[list[Old], list[Source]]] = defaultdict(
        lambda: ([], []))
    for old in rest_old:
        groups[find(("o", old.item_id))][0].append(old)
    for new in rest_new:
        groups[find(("n", new.id))][1].append(new)

    for root in sorted(groups, key=str):
        group_old, group_new = groups[root]
        if not group_old:
            plan.new.extend(group_new)
            continue
        hits = sorted({h for o in group_old for h in blocked.get(o.item_id, ())}, key=str)
        if hits:
            plan.ambiguous.append(Ambiguity(
                "candidate_already_matched", group_old, group_new + [taken[h] for h in hits]))
        elif not group_new:
            plan.removed.extend(group_old)
        elif len(group_old) == 1 and len(group_new) == 1:
            if group_old[0].source.family == group_new[0].family:
                plan.candidates.append((group_old[0], group_new[0]))
            else:
                plan.ambiguous.append(Ambiguity("conflicting_family", group_old, group_new))
        else:
            plan.ambiguous.append(Ambiguity("multiple_candidates", group_old, group_new))

    by_item = lambda pair: str(pair[0].item_id)  # noqa: E731
    plan.exact.sort(key=by_item)
    plan.candidates.sort(key=by_item)
    for amb in plan.ambiguous:
        amb.olds.sort(key=lambda o: str(o.item_id))
        amb.candidates.sort(key=lambda n: str(n.id))
    plan.ambiguous.sort(key=lambda a: (str(a.olds[0].item_id), a.reason))
    plan.new.sort(key=lambda n: str(n.id))
    plan.removed.sort(key=lambda o: str(o.item_id))
    return plan


def compatible_runs(old: RequirementExtractionRun, new: RequirementExtractionRun) -> bool:
    """Mateixa entrada i mateix contracte d'extraccio: condicio per a `exact_auto`."""
    return (
        old.input_fingerprint == new.input_fingerprint
        and (old.extractor_version, old.prompt_version, old.schema_version)
        == (new.extractor_version, new.prompt_version, new.schema_version)
    )


# --------------------------------------------------------------------------- #
# Carrega en lot                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class _Loaded:
    """Una seccio carregada.

    `rows`: font primaria activa de CADA element (item_id -> fila).
    `old_rows`: els que encara apunten fora de la run desti (per reconciliar).
    `claimed_rows`: els que ja apunten a la run desti (reconciliacio en curs).
    `new_rows`: files de la run desti que cap element no te com a font.
    """

    items: dict[uuid.UUID, ReviewItem]
    rows: dict[uuid.UUID, Any]
    old_rows: dict[uuid.UUID, Any]
    claimed_rows: dict[uuid.UUID, Any]
    new_rows: list[Any]
    exact_allowed: bool


async def _load(
    session: AsyncSession,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    kind: ReviewItemKind,
    current: RequirementExtractionRun,
) -> _Loaded:
    row = Requirement if kind == ReviewItemKind.REQUIREMENT else AwardCriterion
    links = (
        await session.execute(
            review_runs.active_primary_links(company_id, tender_id, kind, ReviewItem, row)
        )
    ).all()
    items = {item.id: item for item, _row in links}
    rows = {item.id: source for item, source in links}
    old_rows = {i: r for i, r in rows.items() if r.run_id != current.id}
    claimed_rows = {i: r for i, r in rows.items() if r.run_id == current.id}
    claimed_ids = {r.id for r in claimed_rows.values()}
    new_rows = [
        r
        for r in (
            await session.scalars(
                sa.select(row).where(row.run_id == current.id).order_by(row.created_at, row.id)
            )
        ).all()
        if r.id not in claimed_ids
    ]
    old_run_ids = {r.run_id for r in old_rows.values()}
    runs = {
        r.id: r
        for r in (
            await session.scalars(
                sa.select(RequirementExtractionRun).where(
                    RequirementExtractionRun.id.in_(old_run_ids)
                )
            )
        ).all()
    }
    exact_allowed = len(runs) == 1 and all(compatible_runs(r, current) for r in runs.values())
    return _Loaded(items, rows, old_rows, claimed_rows, new_rows, exact_allowed)


async def _human_states(
    session: AsyncSession, company_id: uuid.UUID, items: Sequence[ReviewItem]
) -> dict[uuid.UUID, HumanState]:
    ids = [i.id for i in items]
    if not ids:
        return {}
    corrected = set(
        (
            await session.scalars(
                sa.select(ReviewItemRevision.item_id)
                .where(
                    ReviewItemRevision.item_id.in_(ids),
                    ReviewItemRevision.action == ReviewAction.CORRECT,
                )
                .distinct()
            )
        ).all()
    )
    extra = dict(
        (
            await session.execute(
                sa.select(ReviewItemSource.item_id, sa.func.count())
                .where(
                    ReviewItemSource.item_id.in_(ids),
                    ReviewItemSource.company_id == company_id,
                    ReviewItemSource.link_role == ReviewSourceRole.EXTRA_EVIDENCE,
                    ReviewItemSource.unlinked_at.is_(None),
                )
                .group_by(ReviewItemSource.item_id)
            )
        ).all()
    )
    relation_counts: Counter[uuid.UUID] = Counter()
    edges = (
        await session.execute(
            sa.select(ReviewItemRelation.from_item_id, ReviewItemRelation.to_item_id).where(
                ReviewItemRelation.company_id == company_id,
                ReviewItemRelation.archived_at.is_(None),
                sa.or_(
                    ReviewItemRelation.from_item_id.in_(ids),
                    ReviewItemRelation.to_item_id.in_(ids),
                ),
            )
        )
    ).all()
    wanted = set(ids)
    for from_id, to_id in edges:
        for node in {from_id, to_id} & wanted:
            relation_counts[node] += 1
    states = {}
    for item in items:
        provenance = provenance_of(item, True, item.id in corrected)
        role = ReviewAwardRole(item.award_role) if item.award_role else None
        states[item.id] = HumanState(
            review_item_id=item.id,
            review_revision=item.revision,
            verification_status=str(item.verification_status),
            verification_label=VERIFICATION_LABELS[ReviewVerification(item.verification_status)],
            provenance=provenance,
            provenance_label=PROVENANCE_LABELS[provenance],
            has_human_correction=item.id in corrected,
            extra_evidence_count=int(extra.get(item.id, 0)),
            relation_count=relation_counts[item.id],
            award_role=role.value if role else None,
            award_role_label=(ROLE_LABELS[role] if role else ROLE_UNASSIGNED)
            if item.kind == ReviewItemKind.AWARD_CRITERION
            else None,
            accepted_title=str(
                item.accepted_json.get("title") or item.accepted_json.get("name") or ""
            ),
        )
    return states


def _locations(evidence: Iterable[Any], names: dict[uuid.UUID, str]) -> list[SourceLocation]:
    seen = sorted(
        {(names.get(e.extraction_id), e.page_number) for e in evidence},
        key=lambda p: (p[0] or "", p[1]),
    )
    return [SourceLocation(document=doc, page=page) for doc, page in seen]


# --------------------------------------------------------------------------- #
# Requisits                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class _Built:
    section: Any
    plan_part: dict[str, Any]


def _requirement_source(r: Requirement, snap: RequirementSnapshot, names) -> RequirementSource:
    return RequirementSource(
        source_id=r.id,
        run_id=r.run_id,
        title=snap.title,
        description=snap.description,
        category=snap.category.value,
        category_label=CATEGORY_LABELS.get(snap.category.value, snap.category.value),
        obligation_label=obligation_label(snap.obligation, snap.modality),
        values=[
            SourceValue(key=v.key, operator=v.operator, value=v.value, unit_label=v.unit_label)
            for v in snap.values
        ],
        locations=_locations(r.evidence, names),
    )


def requirement_source_diff(
    old: RequirementSnapshot, new: RequirementSnapshot, old_ev: list, new_ev: list
) -> RequirementSourceDiff:
    return RequirementSourceDiff(
        title_changed=_text_changed(old.title, new.title),
        description_changed=_text_changed(old.description, new.description),
        category_changed=old.category != new.category,
        obligation_changed=old.obligation != new.obligation,
        modality_changed=old.modality != new.modality,
        values_changed=values_signature(old.values) != values_signature(new.values),
        evidence_changed=old_ev != new_ev,
    )


def requirement_accepted_diff(
    accepted: RequirementSnapshot, new: RequirementSnapshot
) -> tuple[RequirementAcceptedDiff, bool]:
    """Contra el que l'empresa ACCEPTA, no contra la font vella.

    Torna tambe si la diferencia es MATERIAL: el que obliga (valors, categoria,
    obligacio, modalitat). Una redaccio diferent del titol no ho es.
    """
    diff = RequirementAcceptedDiff(
        title_changed=_text_changed(accepted.title, new.title),
        description_changed=_text_changed(accepted.description, new.description),
        category_changed=accepted.category != new.category,
        obligation_changed=accepted.obligation != new.obligation,
        modality_changed=accepted.modality != new.modality,
        values_changed=values_signature(accepted.values) != values_signature(new.values),
    )
    material = (
        diff.values_changed
        or diff.category_changed
        or diff.obligation_changed
        or diff.modality_changed
    )
    return diff, material


def requires_reverification(accepted_material: bool, source_material: bool) -> bool:
    """Nomes si l'extraccio porta alguna cosa NOVA que contradiu el que s'ha acceptat.

    * font igual (p. ex. `exact_auto`) i correccio humana: la persona ja va
      decidir contra aquesta mateixa font; no hi ha res nou -> no.
    * font canviada que coincideix amb l'acceptat (la nova extraccio diu el que
      el revisor ja havia corregit) -> no.
    * font canviada que difereix de l'acceptat -> si.
    """
    return accepted_material and source_material


def requirement_source_material(diff: RequirementSourceDiff) -> bool:
    return (
        diff.values_changed
        or diff.category_changed
        or diff.obligation_changed
        or diff.modality_changed
    )


def award_source_material(diff: AwardSourceDiff) -> bool:
    return diff.points_changed or diff.weight_changed or diff.kind_changed or (
        diff.mechanism_changed
    )


def _requirement_source_of(r: Requirement) -> tuple[Source, RequirementSnapshot, list]:
    snap = snapshot_of(r, r.evidence)
    evidence = evidence_signature(r.evidence)
    exact = _digest({"dedup_key": r.dedup_key, "snapshot": snap.model_dump(mode="json"),
                     "evidence": evidence})
    source = Source(id=r.id, exact_key=exact, anchors=frozenset(anchor_keys(r.evidence)),
                    family=snap.category.value)
    return source, snap, evidence


async def _requirements_section(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID,
    state: str, represented: set[uuid.UUID], current: RequirementExtractionRun | None,
) -> _Built:
    counts_only = await _section_counts(
        session, company_id, tender_id, ReviewItemKind.REQUIREMENT, current)
    if state not in (NEW_EXTRACTION, IN_PROGRESS) or current is None:
        return _Built(RequirementSection(
            state=state, state_label=STATE_LABELS[state],
            represented_run_ids=sorted(represented, key=str), counts=MatchCounts(),
            **counts_only), _base(state, represented))

    loaded = await _load(session, company_id, tender_id, ReviewItemKind.REQUIREMENT, current)
    built_old = {i: _requirement_source_of(r) for i, r in loaded.old_rows.items()}
    built_new = {r.id: (r, *_requirement_source_of(r)) for r in loaded.new_rows}
    olds = [Old(item_id=i, source=b[0]) for i, b in built_old.items()]
    plan = classify(olds, [b[1] for b in built_new.values()],
                    exact_allowed=loaded.exact_allowed,
                    claimed=[_requirement_source_of(r)[0] for r in loaded.claimed_rows.values()])
    state = _settled(state, plan, represented, current)
    names = await _document_names(session, tender_id, (
        e.extraction_id
        for rows in (*loaded.old_rows.values(), *loaded.new_rows) for e in rows.evidence))
    humans = await _human_states(session, company_id, list(loaded.items.values()))

    def old_summary(item_id):
        return _requirement_source(loaded.old_rows[item_id], built_old[item_id][1], names)

    def new_summary(source_id):
        row, _src, snap, _ev = built_new[source_id]
        return _requirement_source(row, snap, names)

    matches, plan_pairs = [], []
    for match_class, pairs in ((EXACT_AUTO, plan.exact), (REVIEW_CANDIDATE, plan.candidates)):
        for old, new in pairs:
            _row, _src, new_snap, new_ev = built_new[new.id]
            _old_src, old_snap, old_ev = built_old[old.item_id]
            accepted = RequirementSnapshot.model_validate(loaded.items[old.item_id].accepted_json)
            accepted_diff, material = requirement_accepted_diff(accepted, new_snap)
            source_diff = requirement_source_diff(old_snap, new_snap, old_ev, new_ev)
            reverify = requires_reverification(material, requirement_source_material(source_diff))
            matches.append(RequirementMatch(
                match_class=match_class, match_label=MATCH_LABELS[match_class],
                item=humans[old.item_id], old_source=old_summary(old.item_id),
                new_source=new_summary(new.id), source_diff=source_diff,
                accepted_diff=accepted_diff, requires_reverification=reverify))
            plan_pairs.append([match_class, str(old.item_id), str(old.source.id), str(new.id),
                               new.exact_key, source_diff.model_dump(),
                               accepted_diff.model_dump(), reverify])
    ambiguous = [
        RequirementAmbiguity(
            match_label=MATCH_LABELS[AMBIGUOUS], reason=a.reason,
            reason_label=REASON_LABELS[a.reason],
            items=[humans[o.item_id] for o in a.olds],
            old_sources=[old_summary(o.item_id) for o in a.olds],
            candidate_sources=[new_summary(n.id) for n in a.candidates])
        for a in plan.ambiguous
    ]
    removed = [
        RequirementRemoved(match_label=MATCH_LABELS[REMOVED], item=humans[o.item_id],
                           old_source=old_summary(o.item_id))
        for o in plan.removed
    ]
    new = [RequirementNew(match_label=MATCH_LABELS[NEW], source=new_summary(n.id))
           for n in plan.new]
    section = RequirementSection(
        state=state, state_label=STATE_LABELS[state],
        represented_run_ids=sorted(represented, key=str), counts=plan.counts(),
        already_applied_count=len(loaded.claimed_rows),
        matches=matches, ambiguous=ambiguous, removed=removed, new=new, **counts_only)
    return _Built(section, {**_base(state, represented), **_plan_part(loaded, plan, plan_pairs)})


# --------------------------------------------------------------------------- #
# Criteris d'adjudicacio                                                        #
# --------------------------------------------------------------------------- #


def _mechanism_sig(snap: AwardSnapshot) -> list[Any]:
    return [
        sorted([m.kind, m.check] for m in snap.mechanisms),
        snap.mechanism_status,
        normalise_text(snap.formula),
        snap.formula_status,
    ]


def _kind_sig(snap: AwardSnapshot) -> list[Any]:
    return [snap.criterion_type, snap.evaluation_type, snap.declared_kind, snap.award_class]


def award_source_diff(
    old: AwardSnapshot, new: AwardSnapshot, old_ev: list, new_ev: list
) -> AwardSourceDiff:
    return AwardSourceDiff(
        name_changed=_text_changed(old.name, new.name),
        description_changed=_text_changed(old.description, new.description),
        points_changed=_number_changed(old.points, new.points),
        weight_changed=_number_changed(old.weight_percent, new.weight_percent),
        kind_changed=_kind_sig(old) != _kind_sig(new),
        mechanism_changed=_mechanism_sig(old) != _mechanism_sig(new),
        evidence_changed=old_ev != new_ev,
    )


def award_accepted_diff(
    accepted: AwardSnapshot, new: AwardSnapshot
) -> tuple[AwardAcceptedDiff, bool]:
    diff = AwardAcceptedDiff(
        name_changed=_text_changed(accepted.name, new.name),
        description_changed=_text_changed(accepted.description, new.description),
        points_changed=_number_changed(accepted.points, new.points),
        weight_changed=_number_changed(accepted.weight_percent, new.weight_percent),
        kind_changed=_kind_sig(accepted) != _kind_sig(new),
        mechanism_changed=_mechanism_sig(accepted) != _mechanism_sig(new),
    )
    material = diff.points_changed or diff.weight_changed or diff.kind_changed or (
        diff.mechanism_changed
    )
    return diff, material


def _award_source_of(c: AwardCriterion) -> tuple[Source, AwardSnapshot, list]:
    snap = award_snapshot_of(c)
    evidence = evidence_signature(c.evidence)
    exact = _digest({"dedup_key": c.dedup_key, "snapshot": snap.model_dump(mode="json"),
                     "evidence": evidence})
    anchors = anchor_keys(c.evidence)
    name = normalise_text(c.name)
    if name:
        anchors.add("n:" + _digest(name))
    return Source(id=c.id, exact_key=exact, anchors=frozenset(anchors),
                  family=snap.criterion_type), snap, evidence


def _award_source(c: AwardCriterion, snap: AwardSnapshot, names) -> AwardSource:
    return AwardSource(
        source_id=c.id, run_id=c.run_id, name=snap.name, description=snap.description,
        points=snap.points, weight_percent=snap.weight_percent,
        criterion_type=snap.criterion_type, formula_status=snap.formula_status,
        locations=_locations(c.evidence, names))


async def _awards_section(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID,
    state: str, represented: set[uuid.UUID], current: RequirementExtractionRun | None,
) -> _Built:
    counts_only = await _section_counts(
        session, company_id, tender_id, ReviewItemKind.AWARD_CRITERION, current)
    scoring = None
    if counts_only["canonical_item_count"]:
        structure = await _structure(session, company_id, tender_id)
        scored = structure.scored()
        scoring = AwardScoring(
            scored_count=len(scored),
            total_points=sum(structure.snapshots[i].points or 0 for i in scored),
            scoring_reconciliation_pending=state in (NEW_EXTRACTION, MIXED, IN_PROGRESS),
        )
    if state not in (NEW_EXTRACTION, IN_PROGRESS) or current is None:
        return _Built(AwardSection(
            state=state, state_label=STATE_LABELS[state],
            represented_run_ids=sorted(represented, key=str), counts=MatchCounts(),
            scoring=scoring, **counts_only), _base(state, represented))

    loaded = await _load(session, company_id, tender_id, ReviewItemKind.AWARD_CRITERION, current)
    built_old = {i: _award_source_of(c) for i, c in loaded.old_rows.items()}
    built_new = {c.id: (c, *_award_source_of(c)) for c in loaded.new_rows}
    olds = [Old(item_id=i, source=b[0]) for i, b in built_old.items()]
    plan = classify(olds, [b[1] for b in built_new.values()],
                    exact_allowed=loaded.exact_allowed,
                    claimed=[_award_source_of(c)[0] for c in loaded.claimed_rows.values()])
    state = _settled(state, plan, represented, current)
    if scoring is not None:
        scoring.scoring_reconciliation_pending = state != UP_TO_DATE
    names = await _document_names(session, tender_id, (
        e.extraction_id
        for rows in (*loaded.old_rows.values(), *loaded.new_rows) for e in rows.evidence))
    humans = await _human_states(session, company_id, list(loaded.items.values()))

    def old_summary(item_id):
        return _award_source(loaded.old_rows[item_id], built_old[item_id][1], names)

    def new_summary(source_id):
        row, _src, snap, _ev = built_new[source_id]
        return _award_source(row, snap, names)

    matches, plan_pairs = [], []
    for match_class, pairs in ((EXACT_AUTO, plan.exact), (REVIEW_CANDIDATE, plan.candidates)):
        for old, new in pairs:
            _row, _src, new_snap, new_ev = built_new[new.id]
            _old_src, old_snap, old_ev = built_old[old.item_id]
            accepted = AwardSnapshot.model_validate(loaded.items[old.item_id].accepted_json)
            accepted_diff, material = award_accepted_diff(accepted, new_snap)
            source_diff = award_source_diff(old_snap, new_snap, old_ev, new_ev)
            reverify = requires_reverification(material, award_source_material(source_diff))
            matches.append(AwardMatch(
                match_class=match_class, match_label=MATCH_LABELS[match_class],
                item=humans[old.item_id], old_source=old_summary(old.item_id),
                new_source=new_summary(new.id), source_diff=source_diff,
                accepted_diff=accepted_diff, requires_reverification=reverify))
            plan_pairs.append([match_class, str(old.item_id), str(old.source.id), str(new.id),
                               new.exact_key, source_diff.model_dump(),
                               accepted_diff.model_dump(), reverify])
    section = AwardSection(
        state=state, state_label=STATE_LABELS[state],
        represented_run_ids=sorted(represented, key=str), counts=plan.counts(),
        scoring=scoring, matches=matches,
        ambiguous=[
            AwardAmbiguity(
                match_label=MATCH_LABELS[AMBIGUOUS], reason=a.reason,
                reason_label=REASON_LABELS[a.reason],
                items=[humans[o.item_id] for o in a.olds],
                old_sources=[old_summary(o.item_id) for o in a.olds],
                candidate_sources=[new_summary(n.id) for n in a.candidates])
            for a in plan.ambiguous
        ],
        removed=[
            AwardRemoved(match_label=MATCH_LABELS[REMOVED], item=humans[o.item_id],
                         old_source=old_summary(o.item_id))
            for o in plan.removed
        ],
        new=[AwardNew(match_label=MATCH_LABELS[NEW], source=new_summary(n.id))
             for n in plan.new],
        already_applied_count=len(loaded.claimed_rows),
        **counts_only)
    return _Built(section, {**_base(state, represented), **_plan_part(loaded, plan, plan_pairs)})


# --------------------------------------------------------------------------- #
# Comuns                                                                        #
# --------------------------------------------------------------------------- #


async def _section_counts(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID,
    kind: ReviewItemKind, current: RequirementExtractionRun | None,
) -> dict[str, int]:
    row = Requirement if kind == ReviewItemKind.REQUIREMENT else AwardCriterion
    canonical = await session.scalar(
        review_runs.active_primary_links(company_id, tender_id, kind, sa.func.count(ReviewItem.id))
    )
    current_count = 0
    if current is not None:
        current_count = await session.scalar(
            sa.select(sa.func.count()).select_from(row).where(row.run_id == current.id))
    return {"canonical_item_count": int(canonical or 0),
            "current_source_count": int(current_count or 0)}


def _base(state: str, represented: set[uuid.UUID]) -> dict[str, Any]:
    return {"state": state, "represented": sorted(map(str, represented))}


def _settled(
    state: str, plan: Plan, represented: set[uuid.UUID], current: RequirementExtractionRun
) -> str:
    """Una seccio en curs sense cap feina i amb totes les fonts a la run desti: al dia."""
    if state == IN_PROGRESS and represented == {current.id} and not any(
        plan.counts().model_dump().values()
    ):
        return UP_TO_DATE
    return state


def _plan_part(loaded: _Loaded, plan: Plan, pairs: list[list[Any]]) -> dict[str, Any]:
    """Tot el que, si canvia, deixa el pla desfasat. Sense marques de temps.

    `items` inclou TOTS els elements extrets (tambe els ja reenllaçats): la seva
    revisio i la seva font primaria activa.
    """
    return {
        "exact_allowed": loaded.exact_allowed,
        "items": sorted(
            [str(i), item.revision, str(loaded.rows[i].id)]
            for i, item in loaded.items.items()
        ),
        "pairs": sorted(pairs, key=lambda p: json.dumps(p, sort_keys=True, default=str)),
        "ambiguous": sorted(
            [a.reason, sorted(str(o.item_id) for o in a.olds),
             sorted(str(o.source.id) for o in a.olds),
             sorted([str(n.id), n.exact_key] for n in a.candidates)]
            for a in plan.ambiguous
        ),
        "new": sorted([str(n.id), n.exact_key] for n in plan.new),
        "removed": sorted([str(o.item_id), str(o.source.id)] for o in plan.removed),
    }


def section_state(
    represented: set[uuid.UUID],
    current: uuid.UUID | None,
    baseline: set[uuid.UUID] | None = None,
) -> str:
    """Estat d'una seccio. `baseline`: runs d'origen desades d'una reconciliacio
    oberta cap a `current`. Nomes aquesta explica fonts de mes d'una run, i nomes
    si totes son de la base o de la desti; qualsevol altra barreja es MIXED."""
    if (
        baseline
        and current is not None
        and represented
        and represented <= baseline | {current}
    ):
        return IN_PROGRESS
    if len(represented) > 1:
        return MIXED
    if current is None:
        return NO_CURRENT_RUN
    if not represented:
        return NOT_STARTED
    return UP_TO_DATE if represented == {current} else NEW_EXTRACTION


def overall_state(sections: Iterable[str], current: uuid.UUID | None) -> str:
    states = set(sections)
    if MIXED in states:
        return MIXED
    if current is None:
        return NO_CURRENT_RUN
    for state in (IN_PROGRESS, NEW_EXTRACTION, UP_TO_DATE):
        if state in states:
            return state
    return NOT_STARTED


async def preview(
    session: AsyncSession, *, company_id: uuid.UUID, tender_id: uuid.UUID
) -> ReconciliationPreview:
    """La previsualitzacio d'aquesta empresa. No escriu res i no fa commit."""
    current_id = await review_runs.current_run_id(session, tender_id)
    current = await session.get(RequirementExtractionRun, current_id) if current_id else None
    requirement_runs = await review_runs.represented_requirement_run_ids(
        session, company_id, tender_id)
    award_runs = await review_runs.represented_award_run_ids(session, company_id, tender_id)
    reconciliation = await review_runs.open_reconciliation(
        session, company_id, tender_id, current_id)
    requirement_state = section_state(
        requirement_runs, current_id,
        review_runs.section_baseline(reconciliation, ReviewItemKind.REQUIREMENT))
    award_state = section_state(
        award_runs, current_id,
        review_runs.section_baseline(reconciliation, ReviewItemKind.AWARD_CRITERION))
    requirements = await _requirements_section(
        session, company_id, tender_id, requirement_state, requirement_runs, current)
    awards = await _awards_section(
        session, company_id, tender_id, award_state, award_runs, current)
    state = overall_state((requirements.section.state, awards.section.state), current_id)
    plan_hash = None
    if state in (NEW_EXTRACTION, IN_PROGRESS):
        plan_hash = _digest({
            "company_id": str(company_id),
            "tender_id": str(tender_id),
            "current_run_id": str(current_id),
            "requirements": requirements.plan_part,
            "awards": awards.plan_part,
        })
    return ReconciliationPreview(
        tender_id=tender_id,
        state=state,
        state_label=STATE_LABELS[state],
        current_run_id=current_id,
        plan_hash=plan_hash,
        requirements=requirements.section,
        awards=awards.section,
        reconciliation=await reconciliation_summary(session, company_id, tender_id, current_id),
    )


RECONCILIATION_STATE_LABELS = {
    ReviewReconciliationState.IN_PROGRESS: "En curs",
    ReviewReconciliationState.NEEDS_REVIEW: "Cal revisar",
    ReviewReconciliationState.COMPLETED: "Completada",
}


async def reconciliation_summary(
    session: AsyncSession,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    current_id: uuid.UUID | None,
) -> ReconciliationSummary | None:
    """La reconciliacio d'aquesta empresa cap a la run vigent, sigui quin sigui l'estat."""
    if current_id is None:
        return None
    row = await session.scalar(
        sa.select(ReviewReconciliation).where(
            ReviewReconciliation.company_id == company_id,
            ReviewReconciliation.tender_id == tender_id,
            ReviewReconciliation.target_run_id == current_id,
        )
    )
    if row is None:
        return None
    state = ReviewReconciliationState(row.state)
    return ReconciliationSummary(
        reconciliation_id=row.id,
        state=state.value,
        state_label=RECONCILIATION_STATE_LABELS[state],
        target_run_id=row.target_run_id,
        applied_plan_hash=row.applied_plan_hash,
        exact_applied_count=row.exact_applied_count,
    )
