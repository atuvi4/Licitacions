"""Criteris d'adjudicacio a la revisio canonica per empresa (S-R4).

Mateixa disciplina que els requisits (`review_view`): l'extraccio
(`award_criteria`, `award_criterion_evidence`) no es toca mai; cada criteri
persistit de la run vigent es materialitza com un element `award_criterion`
d'AQUESTA empresa, amb una font primaria que hi apunta.

**Totes les files, cap de descartada.** Un plec repeteix els criteris: el PCAP
els dona, l'Informe de necessitats els torna a dir i el model d'oferta (Sobre 2)
els ensenya com caselles SI/NO. L'extraccio les desa totes i aqui es
materialitzen totes, per poder-les auditar. El que evita comptar-les dues
vegades es l'estructura que hi posa el revisor:

* el **paper** de cada criteri (`award_role`, accio `set_award_role`): nomes
  `scored` puntua; `parent_total` es el total d'un bloc, que ja es la suma dels
  fills; `offer_form_view` es el mateix bloc vist des del model d'oferta;
  `not_scored` i `desirable` no puntuen;
* les **relacions**: `parent_of` (el bloc agrupa les seves files puntuades) i
  `restatement_of` / `duplicate_of`, que PLEGUEN un criteri dins d'un altre.

**La vista de puntuacio** compta un criteri si i nomes si te el paper `scored`
i no esta plegat. Res no s'infereix del text ("es valorara" no dona paper) i
cap punt no es reescriu: les incoherencies surten com a avisos (`checks`).

**Formula.** `formula_status = verified` nomes si una cita valida prova la
formula (mecanisme `formula` comprovat). Tenir-ne el text no la verifica.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.enums import (
    ReviewAction,
    ReviewAwardRole,
    ReviewItemKind,
    ReviewOriginHint,
    ReviewRelationType,
    ReviewSourceRole,
    ReviewStatus,
    ReviewVerification,
)
from app.models.provenance import TenderSourceRef
from app.models.requirements import AwardCriterion, AwardCriterionEvidence
from app.models.review import ReviewItem, ReviewItemRelation, ReviewItemRevision, ReviewItemSource
from app.models.tenders import TenderDocument
from app.schemas.review import (
    AwardCheck,
    AwardCriteriaView,
    AwardItemRef,
    AwardMechanism,
    AwardSnapshot,
    AwardStartResult,
    CanonicalAwardCriterion,
    CanonicalEvidence,
)
from app.services.review_runs import (
    current_run_id,
    ensure_section_reconciled,
    needs_reconciliation,
    open_reconciliation,
    represented_award_run_ids,
    section_baseline,
)
from app.services.review_view import (
    EVIDENCE_CITED,
    PROVENANCE_LABELS,
    VERIFICATION_LABELS,
    _document_names,
    provenance_of,
)

# --------------------------------------------------------------------------- #
# Vocabulari de la interficie                                                   #
# --------------------------------------------------------------------------- #

ROLE_LABELS = {
    ReviewAwardRole.SCORED: "Puntua",
    ReviewAwardRole.PARENT_TOTAL: "Total del bloc",
    ReviewAwardRole.OFFER_FORM_VIEW: "Model d'oferta",
    ReviewAwardRole.NOT_SCORED: "No puntua",
    ReviewAwardRole.DESIRABLE: "Millora valorable",
}
ROLE_UNASSIGNED = "Paper per assignar"
FORMULA_LABELS = {
    "verified": "Fórmula confirmada al document",
    "not_evidence_verified": "Fórmula detectada, pendent de verificar",
}
MECHANISM_LABELS = {
    "verified": "Puntuació confirmada al document",
    "not_verified": "Puntuació pendent de verificar",
    "none": "Sense mecanisme de puntuació",
}
EVIDENCE_IN_DOCUMENT = "Al document"

#: Relacions que pleguen el `from` dins del `to`: no compta per separat.
FOLDING = frozenset({ReviewRelationType.DUPLICATE_OF, ReviewRelationType.RESTATEMENT_OF})
#: El maxim legal habitual; si el total canonic no hi arriba, s'avisa (no es corregeix).
EXPECTED_TOTAL = 100.0
_EPSILON = 1e-6

CHECK_LABELS = {
    "total_points_mismatch": "El total puntuable no suma 100 punts",
    "parent_total_mismatch": "El total del bloc no coincideix amb la suma dels seus criteris",
    "parent_without_children": "Total del bloc sense criteris agrupats",
    "scored_without_points": "Criteri que puntua sense punts",
    "formula_not_evidence_verified": FORMULA_LABELS["not_evidence_verified"],
    "restatement_excluded": "Reiteració exclosa del total (no es compta dues vegades)",
    "restatement_points_differ": "La reiteració no dona els mateixos punts que l'original",
    "role_unassigned": ROLE_UNASSIGNED,
}


# --------------------------------------------------------------------------- #
# Instantania                                                                   #
# --------------------------------------------------------------------------- #


def _mechanisms(basis: object) -> list[AwardMechanism]:
    if not isinstance(basis, Mapping):
        return []
    found = []
    for mechanism in basis.get("mechanisms") or []:
        if isinstance(mechanism, Mapping) and mechanism.get("kind") and mechanism.get("check"):
            found.append(
                AwardMechanism(kind=str(mechanism["kind"]), check=str(mechanism["check"]))
            )
    return found


def award_snapshot_of(criterion: AwardCriterion) -> AwardSnapshot:
    """El que l'empresa accepta al principi: exactament el que diu l'extraccio."""
    basis = criterion.adjudication_basis_json
    mechanisms = _mechanisms(basis)
    if any(m.check == "verified" for m in mechanisms):
        mechanism_status = "verified"
    else:
        mechanism_status = "not_verified" if mechanisms else "none"
    formula = (criterion.formula or "").strip() or None
    formula_status = None
    if formula is not None:
        proved = any(m.kind == "formula" and m.check == "verified" for m in mechanisms)
        formula_status = "verified" if proved else "not_evidence_verified"
    award_class = basis.get("award_class") if isinstance(basis, Mapping) else None
    if criterion.reviewed_at is None and criterion.reviewed_by is None:
        pipeline_flag = criterion.review_status == ReviewStatus.NEEDS_REVIEW
    else:
        # Una revisio LEGACY ha tocat el camp: ja no es senyal de la canonada.
        pipeline_flag = False
    return AwardSnapshot(
        name=criterion.name,
        description=criterion.description,
        points=criterion.points,
        weight_percent=criterion.weight_percent,
        criterion_type=criterion.criterion_type.value,
        evaluation_type=criterion.evaluation_type.value,
        declared_kind=basis.get("declared_kind") if isinstance(basis, Mapping) else None,
        award_class=award_class,
        mechanisms=mechanisms,
        mechanism_status=mechanism_status,
        formula=formula,
        formula_status=formula_status,
        needs_review_from_extraction=(
            pipeline_flag or award_class == "candidate_award_criterion"
        ),
    )


# --------------------------------------------------------------------------- #
# Materialitzacio                                                               #
# --------------------------------------------------------------------------- #


async def current_award_criteria(
    session: AsyncSession, tender_id: uuid.UUID
) -> list[AwardCriterion]:
    """Els criteris persistits de la run VIGENT. Els de runs velles no hi entren."""
    return await _criteria_of_run(session, await current_run_id(session, tender_id))


async def _criteria_of_run(
    session: AsyncSession, run_id: uuid.UUID | None
) -> list[AwardCriterion]:
    if run_id is None:
        return []
    return list(
        (
            await session.scalars(
                sa.select(AwardCriterion)
                .where(AwardCriterion.run_id == run_id)
                .order_by(
                    AwardCriterion.source_chunk_index, AwardCriterion.created_at,
                    AwardCriterion.id,
                )
            )
        ).all()
    )


async def _present_criterion_ids(
    session: AsyncSession, company_id: uuid.UUID, criterion_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    ids = list(criterion_ids)
    if not ids:
        return set()
    rows = await session.scalars(
        sa.select(ReviewItemSource.award_criterion_id).where(
            ReviewItemSource.company_id == company_id,
            ReviewItemSource.award_criterion_id.in_(ids),
            ReviewItemSource.link_role == ReviewSourceRole.PRIMARY,
            ReviewItemSource.unlinked_at.is_(None),
        )
    )
    return {row for row in rows if row is not None}


async def start_award_review(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    provenance: dict[str, str] | None = None,
) -> AwardStartResult:
    """Un element per criteri persistit de la run vigent que falti. Idempotent. Sense commit.

    Neixen SENSE paper: el paper el decideix el revisor, no s'endevina.
    """
    run_id = await current_run_id(session, tender_id)
    # Guarda de rerun: independent de la dels requisits, abans de cap escriptura.
    await ensure_section_reconciled(
        session, company_id, tender_id, ReviewItemKind.AWARD_CRITERION, run_id
    )
    criteria = await _criteria_of_run(session, run_id)
    present = await _present_criterion_ids(session, company_id, (c.id for c in criteria))
    missing = [c for c in criteria if c.id not in present]

    children: list[ReviewItemSource | ReviewItemRevision] = []
    for criterion in missing:
        snapshot = award_snapshot_of(criterion)
        status = (
            ReviewVerification.NEEDS_REVIEW
            if snapshot.needs_review_from_extraction
            else ReviewVerification.UNREVIEWED
        )
        accepted = snapshot.model_dump(mode="json")
        item = ReviewItem(
            id=uuid.uuid4(),
            company_id=company_id,
            tender_id=tender_id,
            kind=ReviewItemKind.AWARD_CRITERION,
            verification_status=status,
            award_role=None,
            accepted_json=accepted,
            origin_hint=ReviewOriginHint.EXTRACTION,
            revision=1,
            created_by=actor_id,
            updated_by=actor_id,
        )
        session.add(item)
        children.append(
            ReviewItemSource(
                item_id=item.id,
                company_id=company_id,
                award_criterion_id=criterion.id,
                link_role=ReviewSourceRole.PRIMARY,
                created_by=actor_id,
            )
        )
        children.append(
            ReviewItemRevision(
                item_id=item.id,
                company_id=company_id,
                revision_no=1,
                action=ReviewAction.CREATE,
                verification_status_after=status,
                accepted_json_after=accepted,
                payload_json={
                    "source": "extraction",
                    "run_id": str(criterion.run_id),
                    **(provenance or {}),
                },
                actor_id=actor_id,
            )
        )
    try:
        await session.flush()
        session.add_all(children)
        await session.flush()
    except IntegrityError:
        # Una materialitzacio concurrent ja les ha creat: res a fer.
        await session.rollback()
        present = await _present_criterion_ids(session, company_id, (c.id for c in criteria))
        return AwardStartResult(
            tender_id=tender_id, created=0, already_present=len(present),
            total_award_criteria=len(present),
        )
    return AwardStartResult(
        tender_id=tender_id,
        created=len(missing),
        already_present=len(criteria) - len(missing),
        total_award_criteria=len(criteria),
    )


# --------------------------------------------------------------------------- #
# Estructura de puntuacio                                                       #
# --------------------------------------------------------------------------- #


def award_items(company_id: uuid.UUID, tender_id: uuid.UUID) -> sa.Select:
    return sa.select(ReviewItem).where(
        ReviewItem.company_id == company_id,
        ReviewItem.tender_id == tender_id,
        ReviewItem.kind == ReviewItemKind.AWARD_CRITERION,
        ReviewItem.archived_at.is_(None),
    )


@dataclass
class _Structure:
    """El graf de puntuacio d'una empresa i licitacio, calculat una sola vegada."""

    items: dict[uuid.UUID, ReviewItem]
    snapshots: dict[uuid.UUID, AwardSnapshot]
    folded_into: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    parent: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    children: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=lambda: defaultdict(list))
    restatements: dict[uuid.UUID, list[uuid.UUID]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def role(self, item_id: uuid.UUID) -> ReviewAwardRole | None:
        role = self.items[item_id].award_role
        return ReviewAwardRole(role) if role is not None else None

    def counts(self, item_id: uuid.UUID) -> bool:
        return self.role(item_id) == ReviewAwardRole.SCORED and item_id not in self.folded_into

    def root(self, item_id: uuid.UUID) -> uuid.UUID:
        """El canonic on acaba una cadena de plegaments (A -> B -> C dona C)."""
        seen = {item_id}
        while item_id in self.folded_into:
            item_id = self.folded_into[item_id]
            if item_id in seen:  # pragma: no cover - la creacio ja ho impedeix
                break
            seen.add(item_id)
        return item_id

    def scored(self) -> list[uuid.UUID]:
        ids = [i for i in self.items if self.counts(i)]
        return sorted(
            ids,
            key=lambda i: (
                i in self.parent,
                -(self.snapshots[i].points or 0),
                self.snapshots[i].name,
            ),
        )


async def _structure(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> _Structure:
    items = list(
        (
            await session.scalars(
                award_items(company_id, tender_id).order_by(ReviewItem.created_at, ReviewItem.id)
            )
        ).all()
    )
    by_id = {i.id: i for i in items}
    structure = _Structure(
        items=by_id,
        snapshots={i.id: AwardSnapshot.model_validate(i.accepted_json) for i in items},
    )
    if not items:
        return structure
    relations = (
        await session.execute(
            sa.select(
                ReviewItemRelation.from_item_id,
                ReviewItemRelation.to_item_id,
                ReviewItemRelation.relation_type,
            )
            .where(
                ReviewItemRelation.company_id == company_id,
                ReviewItemRelation.tender_id == tender_id,
                ReviewItemRelation.archived_at.is_(None),
            )
            .order_by(ReviewItemRelation.created_at, ReviewItemRelation.id)
        )
    ).all()
    for source, target, relation_type in relations:
        if source not in by_id or target not in by_id:
            continue
        relation_type = ReviewRelationType(relation_type)
        if relation_type in FOLDING:
            structure.folded_into.setdefault(source, target)
            structure.restatements[target].append(source)
        elif relation_type == ReviewRelationType.PARENT_OF:
            structure.parent[target] = source
            structure.children[source].append(target)
    return structure


def _points_differ(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) > _EPSILON


def consistency_checks(structure: _Structure) -> list[AwardCheck]:
    """Avisos no destructius. Els punts no es toquen mai."""
    checks: list[AwardCheck] = []
    snaps = structure.snapshots
    scored = structure.scored()
    total = sum(snaps[i].points or 0 for i in scored)
    if abs(total - EXPECTED_TOTAL) > _EPSILON:
        checks.append(AwardCheck(
            code="total_points_mismatch", label=CHECK_LABELS["total_points_mismatch"],
            item_ids=scored, details={"total_points": total, "expected": EXPECTED_TOTAL},
        ))
    for item_id in structure.items:
        if structure.role(item_id) != ReviewAwardRole.PARENT_TOTAL:
            continue
        if item_id in structure.folded_into:
            continue  # es la reiteracio d'un bloc: el compara el bloc original
        kids = structure.children.get(item_id, [])
        if not kids:
            checks.append(AwardCheck(
                code="parent_without_children", label=CHECK_LABELS["parent_without_children"],
                item_ids=[item_id],
            ))
            continue
        children_sum = sum(snaps[k].points or 0 for k in kids)
        if snaps[item_id].points is None or _points_differ(snaps[item_id].points, children_sum):
            checks.append(AwardCheck(
                code="parent_total_mismatch", label=CHECK_LABELS["parent_total_mismatch"],
                item_ids=[item_id, *kids],
                details={"parent_points": snaps[item_id].points, "children_sum": children_sum},
            ))
    missing_points = [i for i in scored if snaps[i].points is None]
    if missing_points:
        checks.append(AwardCheck(
            code="scored_without_points", label=CHECK_LABELS["scored_without_points"],
            item_ids=missing_points,
        ))
    unproved = [
        i for i in structure.items
        if snaps[i].formula_status == "not_evidence_verified" and i not in structure.folded_into
    ]
    if unproved:
        checks.append(AwardCheck(
            code="formula_not_evidence_verified",
            label=CHECK_LABELS["formula_not_evidence_verified"], item_ids=unproved,
        ))
    excluded = [
        i for i in structure.folded_into if structure.role(i) == ReviewAwardRole.SCORED
    ]
    if excluded:
        checks.append(AwardCheck(
            code="restatement_excluded", label=CHECK_LABELS["restatement_excluded"],
            item_ids=excluded,
            details={"points_excluded": sum(snaps[i].points or 0 for i in excluded)},
        ))
    differ = [
        i for i in structure.folded_into
        if _points_differ(snaps[i].points, snaps[structure.root(i)].points)
    ]
    if differ:
        checks.append(AwardCheck(
            code="restatement_points_differ", label=CHECK_LABELS["restatement_points_differ"],
            item_ids=differ,
        ))
    unassigned = [i for i in structure.items if structure.role(i) is None]
    if unassigned:
        checks.append(AwardCheck(
            code="role_unassigned", label=CHECK_LABELS["role_unassigned"],
            item_ids=unassigned,
        ))
    return checks


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Evidence:
    rows: dict[uuid.UUID, list[CanonicalEvidence]]
    primary: set[uuid.UUID]


async def _evidence(
    session: AsyncSession, tender_id: uuid.UUID, item_ids: list[uuid.UUID]
) -> _Evidence:
    sources = (
        await session.execute(
            sa.select(
                ReviewItemSource.item_id,
                ReviewItemSource.award_criterion_id,
                ReviewItemSource.source_ref_id,
                ReviewItemSource.link_role,
            ).where(
                ReviewItemSource.item_id.in_(item_ids), ReviewItemSource.unlinked_at.is_(None)
            )
        )
    ).all()
    primary = {
        item_id: criterion_id
        for item_id, criterion_id, _ref, role in sources
        if role == ReviewSourceRole.PRIMARY and criterion_id is not None
    }
    evidence: dict[uuid.UUID, list[CanonicalEvidence]] = defaultdict(list)
    if primary:
        rows = list(
            (
                await session.scalars(
                    sa.select(AwardCriterionEvidence)
                    .where(AwardCriterionEvidence.award_criterion_id.in_(primary.values()))
                    .order_by(AwardCriterionEvidence.page_number, AwardCriterionEvidence.id)
                )
            ).all()
        )
        names = await _document_names(session, tender_id, (r.extraction_id for r in rows))
        by_criterion: dict[uuid.UUID, list[AwardCriterionEvidence]] = defaultdict(list)
        for row in rows:
            by_criterion[row.award_criterion_id].append(row)
        for item_id, criterion_id in primary.items():
            evidence[item_id] = [
                CanonicalEvidence(
                    document=names.get(row.extraction_id), page=row.page_number,
                    section=row.section, quote=row.quote, status_label=EVIDENCE_IN_DOCUMENT,
                )
                for row in by_criterion.get(criterion_id, [])
            ]
    ref_links = [(item_id, ref) for item_id, _c, ref, _r in sources if ref is not None]
    if ref_links:
        refs = {
            ref.id: ref
            for ref in (
                await session.scalars(
                    sa.select(TenderSourceRef).where(
                        TenderSourceRef.id.in_([ref for _i, ref in ref_links])
                    )
                )
            ).all()
        }
        doc_ids = {r.tender_document_id for r in refs.values() if r.tender_document_id}
        doc_names = dict(
            (
                await session.execute(
                    sa.select(TenderDocument.id, TenderDocument.name).where(
                        TenderDocument.id.in_(doc_ids)
                    )
                )
            ).all()
        ) if doc_ids else {}
        for item_id, ref_id in ref_links:
            ref = refs.get(ref_id)
            if ref is None:
                continue
            evidence[item_id].append(
                CanonicalEvidence(
                    document=doc_names.get(ref.tender_document_id),
                    page=ref.page_number, section=ref.location_label, quote=ref.quote,
                    status_label=EVIDENCE_CITED,
                )
            )
    return _Evidence(rows=evidence, primary=set(primary))


def _ref(structure: _Structure, ev: _Evidence, item_id: uuid.UUID) -> AwardItemRef:
    rows = ev.rows.get(item_id, [])
    return AwardItemRef(
        canonical_id=item_id,
        name=structure.snapshots[item_id].name,
        points=structure.snapshots[item_id].points,
        documents=sorted({r.document for r in rows if r.document}),
        pages=sorted({r.page for r in rows if r.page is not None}),
    )


def _canonical(structure: _Structure, ev: _Evidence, item_id: uuid.UUID) -> CanonicalAwardCriterion:
    item = structure.items[item_id]
    snap = structure.snapshots[item_id]
    role = structure.role(item_id)
    rows = ev.rows.get(item_id, [])
    provenance = provenance_of(item, item_id in ev.primary, corrected=False)
    verification = ReviewVerification(item.verification_status)
    parent = structure.parent.get(item_id)
    folded = structure.folded_into.get(item_id)
    return CanonicalAwardCriterion(
        canonical_id=item_id,
        name=snap.name,
        description=snap.description,
        points=snap.points,
        role=role.value if role else None,
        role_label=ROLE_LABELS[role] if role else ROLE_UNASSIGNED,
        counts_in_total=structure.counts(item_id),
        parent=_ref(structure, ev, parent) if parent else None,
        children=[_ref(structure, ev, k) for k in structure.children.get(item_id, [])],
        folded_into=_ref(structure, ev, folded) if folded else None,
        restatements=[_ref(structure, ev, r) for r in structure.restatements.get(item_id, [])],
        mechanism_status=snap.mechanism_status,
        mechanism_label=MECHANISM_LABELS[snap.mechanism_status],
        formula=snap.formula,
        formula_status=snap.formula_status,
        formula_label=FORMULA_LABELS[snap.formula_status] if snap.formula_status else None,
        provenance=provenance,
        provenance_label=PROVENANCE_LABELS[provenance],
        verification_status=verification.value,
        verification_label=VERIFICATION_LABELS[verification],
        evidence=rows,
        documents=sorted({r.document for r in rows if r.document}),
        pages=sorted({r.page for r in rows if r.page is not None}),
        review_revision=item.revision,
    )


async def list_award_criteria(
    session: AsyncSession, *, company_id: uuid.UUID, tender_id: uuid.UUID, view: str
) -> AwardCriteriaView:
    """`scoring`: els criteris que compten, sense plegats. `all`: totes les files."""
    structure = await _structure(session, company_id, tender_id)
    scored = structure.scored()
    ids = scored if view == "scoring" else list(structure.items)
    ev = await _evidence(session, tender_id, list(structure.items)) if structure.items else (
        _Evidence(rows={}, primary=set())
    )
    return AwardCriteriaView(
        view=view,
        criteria=[_canonical(structure, ev, i) for i in ids],
        scored_count=len(scored),
        total_points=sum(structure.snapshots[i].points or 0 for i in scored),
        checks=consistency_checks(structure),
        reconciliation_pending=await award_reconciliation_pending(session, company_id, tender_id),
    )


async def award_reconciliation_pending(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> bool:
    """Cert si l'estructura de puntuacio no representa (encara) la run vigent."""
    current = await current_run_id(session, tender_id)
    if needs_reconciliation(
        await represented_award_run_ids(session, company_id, tender_id), current
    ):
        return True
    reconciliation = await open_reconciliation(session, company_id, tender_id, current)
    return bool(section_baseline(reconciliation, ReviewItemKind.AWARD_CRITERION))


async def get_award_criterion(
    session: AsyncSession, *, company_id: uuid.UUID, tender_id: uuid.UUID, item_id: uuid.UUID
) -> CanonicalAwardCriterion:
    """Un criteri d'AQUESTA empresa. El d'una altra empresa dona 404."""
    structure = await _structure(session, company_id, tender_id)
    if item_id not in structure.items:
        raise NotFoundError("Element de revisio no trobat.")
    ev = await _evidence(session, tender_id, list(structure.items))
    return _canonical(structure, ev, item_id)
