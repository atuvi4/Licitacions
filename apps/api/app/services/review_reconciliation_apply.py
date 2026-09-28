"""Aplicar una reconciliacio: NOMES les coincidencies exactes (S-R9).

Consumeix la MATEIXA previsualitzacio (`review_reconciliation.preview`): no hi
ha cap segon aparellador. El client nomes envia la run desti i el `plan_hash`
que ha vist; les correspondencies les torna a calcular el servidor sota lock, i
si el pla ha canviat en res es refusa (409 `reconciliation_plan_stale`) sense
cap escriptura.

**Invariant: el mateix `review_item.id`.** Per a cada `exact_auto`, la font
primaria vella es marca `unlinked_at` (no s'esborra: continua a l'historial i
protegint la seva run) i s'afegeix una font primaria nova cap a la fila de la
run desti. `accepted_json`, `verification_status`, `award_role`, relacions,
cites afegides i historial no es toquen. Cada reenllaç afegeix UNA revisio
`reconcile` amb els ids de la transicio.

**Cicle de vida.** Una `review_reconciliations` per (empresa, licitacio, run
desti), amb la base d'origen per seccio desada en crear-la. Queda
`needs_review` mentre hi hagi candidats, ambigus, nous o retirats, i
`completed` quan no en queda cap i totes les fonts extretes son de la run
desti. `in_progress` nomes existeix dins de la transaccio.

**Ordre de locks** (una sola transaccio): lock d'assessorament per (empresa,
licitacio) a PostgreSQL -> fila de reconciliacio -> elements per id -> tornar a
calcular i validar el pla -> escriure. Dues persones amb el mateix pla: la
primera aplica; la segona troba el pla ja aplicat i rep `changed=false`.

**Cap coincidencia exacta.** Es crea (o es conserva) la reconciliacio en
`needs_review`, amb `changed=false` i cap escriptura d'elements, fonts ni
revisions. Tornar-ho a enviar no fa res mes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.enums import (
    AuditAction,
    ReviewAction,
    ReviewItemKind,
    ReviewReconciliationState,
    ReviewSourceRole,
)
from app.models.review import (
    ReviewItem,
    ReviewItemRevision,
    ReviewItemSource,
    ReviewReconciliation,
)
from app.schemas.review_reconciliation import (
    ReconciliationApplyResult,
    ReconciliationPreview,
    RemainingWork,
)
from app.services import audit, review_runs
from app.services import review_reconciliation as rec
from app.services.review_relations import _serialise_tender

STALE_MESSAGE = "La previsualització ha canviat. Torna-la a carregar abans d'aplicar-la."
NOT_APPLICABLE_MESSAGE = "Ara mateix no hi ha cap reconciliació per aplicar."


class ReconciliationPlanStaleError(ConflictError):
    """El pla que el client ha vist ja no es el que calcula el servidor."""

    code = "reconciliation_plan_stale"


class ReconciliationNotApplicableError(ConflictError):
    """No hi ha res a reconciliar (al dia, no iniciada, barrejada, sense run)."""

    code = "reconciliation_not_applicable"


def _stale(reason: str, **details: Any) -> ReconciliationPlanStaleError:
    return ReconciliationPlanStaleError(STALE_MESSAGE, details={"reason": reason, **details})


def _remaining(section) -> RemainingWork:
    return RemainingWork(
        review_candidate=section.counts.review_candidate,
        ambiguous=section.counts.ambiguous,
        new=section.counts.new,
        removed=section.counts.removed,
    )


def _baseline(section, current: uuid.UUID) -> list[str]:
    """Les runs d'origen d'una seccio en el moment de crear la reconciliacio."""
    return sorted(str(r) for r in section.represented_run_ids if r != current)


def final_state(preview: ReconciliationPreview) -> ReviewReconciliationState:
    """`completed` nomes si cap seccio no te feina i totes son de la run desti."""
    settled = (rec.UP_TO_DATE, rec.NOT_STARTED)
    if preview.requirements.state in settled and preview.awards.state in settled:
        return ReviewReconciliationState.COMPLETED
    return ReviewReconciliationState.NEEDS_REVIEW


def _result(
    row: ReviewReconciliation, *, changed: bool, applied: int, preview: ReconciliationPreview
) -> ReconciliationApplyResult:
    state = ReviewReconciliationState(row.state)
    return ReconciliationApplyResult(
        reconciliation_id=row.id,
        state=state.value,
        state_label=rec.RECONCILIATION_STATE_LABELS[state],
        changed=changed,
        exact_applied_count=applied,
        total_exact_applied=row.exact_applied_count,
        target_run_id=row.target_run_id,
        applied_plan_hash=row.applied_plan_hash,
        remaining_requirements=_remaining(preview.requirements),
        remaining_awards=_remaining(preview.awards),
    )


async def _relink(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    reconciliation: ReviewReconciliation,
    exact: list[tuple[ReviewItemKind, Any]],
    plan_hash: str,
    actor_id: uuid.UUID | None,
    now: datetime,
) -> None:
    """Reenllaça en lot. Primer es tanquen les fonts velles i despres s'obren les noves:
    l'index d'una sola primaria activa per element no admet l'ordre contrari."""
    ids = [match.item.review_item_id for _kind, match in exact]
    items = {
        item.id: item
        for item in (
            await session.scalars(
                sa.select(ReviewItem).where(
                    ReviewItem.id.in_(ids), ReviewItem.company_id == company_id
                )
            )
        ).all()
    }
    active = {
        source.item_id: source
        for source in (
            await session.scalars(
                sa.select(ReviewItemSource).where(
                    ReviewItemSource.item_id.in_(ids),
                    ReviewItemSource.company_id == company_id,
                    ReviewItemSource.link_role == ReviewSourceRole.PRIMARY,
                    ReviewItemSource.unlinked_at.is_(None),
                )
            )
        ).all()
    }
    for kind, match in exact:
        item = items.get(match.item.review_item_id)
        source = active.get(match.item.review_item_id)
        linked = None
        if source is not None:
            linked = (
                source.requirement_id
                if kind == ReviewItemKind.REQUIREMENT
                else source.award_criterion_id
            )
        # Xarxa de seguretat: el hash ja ho cobreix, pero mai no es reenllaça a cegues.
        if item is None or item.revision != match.item.review_revision:
            raise _stale("revision_changed")
        if linked != match.old_source.source_id:
            raise _stale("source_changed")
        source.unlinked_at = now
    await session.flush()

    for kind, match in exact:
        item = items[match.item.review_item_id]
        is_requirement = kind == ReviewItemKind.REQUIREMENT
        session.add(
            ReviewItemSource(
                item_id=item.id,
                company_id=company_id,
                requirement_id=match.new_source.source_id if is_requirement else None,
                award_criterion_id=None if is_requirement else match.new_source.source_id,
                link_role=ReviewSourceRole.PRIMARY,
                created_by=actor_id,
            )
        )
        revision_no = item.revision + 1
        session.add(
            ReviewItemRevision(
                item_id=item.id,
                company_id=company_id,
                revision_no=revision_no,
                action=ReviewAction.RECONCILE,
                # L'estat i la instantania acceptada NO canvien: nomes la font.
                verification_status_after=item.verification_status,
                accepted_json_after=item.accepted_json,
                note=None,
                payload_json={
                    "kind": kind.value,
                    "old_source_id": str(match.old_source.source_id),
                    "new_source_id": str(match.new_source.source_id),
                    "old_run_id": str(match.old_source.run_id),
                    "new_run_id": str(match.new_source.run_id),
                    "reconciliation_id": str(reconciliation.id),
                    "plan_hash": plan_hash,
                },
                actor_id=actor_id,
            )
        )
        item.revision = revision_no
        item.updated_by = actor_id
        item.updated_at = now
    await session.flush()


async def apply_exact(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    target_run_id: uuid.UUID,
    plan_hash: str,
    actor_id: uuid.UUID | None,
) -> ReconciliationApplyResult:
    """Aplica les `exact_auto` del pla vigent si es exactament el que el client ha vist.

    No fa commit: el decideix qui crida. Qualsevol error abans del commit ho desfa tot.
    """
    await _serialise_tender(session, company_id, tender_id)
    existing = await session.scalar(
        sa.select(ReviewReconciliation)
        .where(
            ReviewReconciliation.company_id == company_id,
            ReviewReconciliation.tender_id == tender_id,
            ReviewReconciliation.target_run_id == target_run_id,
        )
        .with_for_update()
    )
    if existing is not None and existing.applied_plan_hash == plan_hash:
        # Idempotent: el mateix pla ja s'ha aplicat (o una altra persona s'hi ha avançat).
        preview = await rec.preview(session, company_id=company_id, tender_id=tender_id)
        return _result(existing, changed=False, applied=0, preview=preview)

    current = await review_runs.current_run_id(session, tender_id)
    if current != target_run_id:
        raise _stale(
            "current_run_changed",
            current_run_id=str(current) if current is not None else None,
            target_run_id=str(target_run_id),
        )
    # Elements en ordre estable abans de tornar a calcular: cap accio concurrent
    # no pot canviar una revisio entre la validacio i l'escriptura.
    await session.execute(
        sa.select(ReviewItem.id)
        .where(ReviewItem.company_id == company_id, ReviewItem.tender_id == tender_id)
        .order_by(ReviewItem.id)
        .with_for_update()
    )
    preview = await rec.preview(session, company_id=company_id, tender_id=tender_id)
    if preview.state not in (rec.NEW_EXTRACTION, rec.IN_PROGRESS):
        raise ReconciliationNotApplicableError(
            NOT_APPLICABLE_MESSAGE, details={"state": preview.state}
        )
    if preview.plan_hash != plan_hash:
        raise _stale("plan_changed")

    exact = [
        (ReviewItemKind.REQUIREMENT, m)
        for m in preview.requirements.matches
        if m.match_class == rec.EXACT_AUTO
    ] + [
        (ReviewItemKind.AWARD_CRITERION, m)
        for m in preview.awards.matches
        if m.match_class == rec.EXACT_AUTO
    ]
    if existing is not None and not exact:
        # Res a aplicar: el pla acceptat abans es conserva tal com era.
        return _result(existing, changed=False, applied=0, preview=preview)

    now = datetime.now(UTC)
    created = existing is None
    reconciliation = existing
    if reconciliation is None:
        reconciliation = ReviewReconciliation(
            id=uuid.uuid4(),
            company_id=company_id,
            tender_id=tender_id,
            target_run_id=target_run_id,
            requirement_source_run_ids=_baseline(preview.requirements, target_run_id),
            award_source_run_ids=_baseline(preview.awards, target_run_id),
            state=ReviewReconciliationState.IN_PROGRESS,
            applied_plan_hash=plan_hash,
            exact_applied_count=0,
            created_by=actor_id,
            updated_by=actor_id,
        )
        session.add(reconciliation)
    else:
        reconciliation.state = ReviewReconciliationState.IN_PROGRESS
    await session.flush()

    if exact:
        await _relink(
            session,
            company_id=company_id,
            reconciliation=reconciliation,
            exact=exact,
            plan_hash=plan_hash,
            actor_id=actor_id,
            now=now,
        )
    reconciliation.exact_applied_count += len(exact)
    reconciliation.applied_plan_hash = plan_hash
    reconciliation.updated_by = actor_id
    reconciliation.updated_at = now
    await session.flush()

    # La feina que queda, amb la reconciliacio ja oberta i les fonts ja reenllaçades.
    after = await rec.preview(session, company_id=company_id, tender_id=tender_id)
    reconciliation.state = final_state(after)
    await session.flush()
    await audit.record(
        session,
        company_id=company_id,
        user_id=actor_id,
        action=AuditAction.CREATE if created else AuditAction.UPDATE,
        entity_type="review_reconciliation",
        entity_id=reconciliation.id,
        summary=(
            f"licitacio={tender_id} run_desti={target_run_id} "
            f"reenllacos_exactes={len(exact)} estat={reconciliation.state.value} "
            f"pla={plan_hash[:16]}"
        ),
    )
    return _result(reconciliation, changed=bool(exact), applied=len(exact), preview=after)
