"""Accions de revisio per empresa (S-R2): confirmar, corregir, descartar...

Cada accio fa, en aquest ordre i en una sola transaccio:

1. carrega l'element d'AQUESTA empresa i licitacio, bloquejant-lo
   (`SELECT ... FOR UPDATE` a PostgreSQL);
2. compara la revisio que el client creu vigent (`expected_revision`) amb la
   real: si no coincideix, 409 i cap escriptura;
3. afegeix UNA fila a `review_item_revisions`;
4. actualitza l'estat vigent de `review_items` (instantania, estat, revisio).

L'auditoria (`audit.record`) la fa el router, amb nomes identificadors.

**L'extraccio no es toca mai.** Corregir canvia la instantania acceptada de la
revisio, no `requirements` ni `requirement_evidence`; i els camps de revisio
LEGACY d'aquelles files tampoc no s'escriuen.

**Un requisit manual nou no va a `requirements`.** La taula de requisits es
global: el que hi escrivis una empresa ho veurien totes. Viu nomes com a element
de revisio d'aquesta empresa (`origin_hint = manual_review`), amb les seves cites
(`tender_source_refs`) enllacades com a evidencia addicional.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.models.enums import (
    MembershipRole,
    ReviewAction,
    ReviewItemKind,
    ReviewOriginHint,
    ReviewSourceRole,
    ReviewVerification,
)
from app.models.identity import User
from app.models.review import ReviewItem, ReviewItemRevision, ReviewItemSource
from app.schemas.review import (
    ConfirmPayload,
    CorrectPayload,
    DocumentFileCitation,
    ExtractedPageCitation,
    ManualRequirementCreate,
    NotePayload,
    ReasonPayload,
    RequirementSnapshot,
    ReviewHistoryEntry,
    SetStatusPayload,
)
from app.services import source_refs
from app.services.manual_requirement import REQUIREMENT_EDIT_ROLES
from app.services.review_view import VERIFICATION_LABELS, requirement_items

ACTION_LABELS = {
    ReviewAction.CREATE: "Creat",
    ReviewAction.CONFIRM: "Confirmat",
    ReviewAction.CORRECT: "Corregit",
    ReviewAction.REJECT: "Descartat",
    ReviewAction.MARK_PARTIAL: "Marcat com a parcial",
    ReviewAction.NOTE: "Nota",
    ReviewAction.SET_STATUS: "Canvi d'estat",
    ReviewAction.SET_AWARD_ROLE: "Canvi de paper",
    ReviewAction.LINK_SOURCE: "Font enllacada",
    ReviewAction.ARCHIVE: "Arxivat",
}
_SNAPSHOT_FIELDS = ("title", "description", "category", "obligation", "modality")


class StaleRevisionError(ConflictError):
    """Algu altre ha revisat l'element: el client ha de tornar-lo a llegir."""

    code = "review_revision_conflict"


def assert_can_review(role: MembershipRole) -> None:
    """Revisar es feina tecnica: els mateixos rols que escriuen requisits."""
    if role not in REQUIREMENT_EDIT_ROLES:
        raise PermissionDeniedError("El teu rol no pot revisar requisits.")


def _parse(model: type[BaseModel], payload: dict[str, Any]) -> Any:
    """La carrega de l'accio, validada. Un error surt com a 422 amb els camps."""
    try:
        return model.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValidationError(
            "La carrega de l'accio no es valida.",
            details={
                "fields": [
                    {
                        "field": ".".join(str(part) for part in error.get("loc", ())),
                        "type": error.get("type", ""),
                        "message": error.get("msg", ""),
                    }
                    for error in exc.errors(include_input=False, include_url=False)
                ]
            },
        ) from None


async def _locked_item(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID, item_id: uuid.UUID
) -> ReviewItem:
    item = await session.scalar(
        requirement_items(company_id, tender_id)
        .where(ReviewItem.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise NotFoundError("Element de revisio no trobat.")
    return item


def _status_change(before: ReviewVerification, after: ReviewVerification) -> dict[str, str]:
    return {"status_before": before.value, "status_after": after.value}


def _corrected(current: RequirementSnapshot, change: CorrectPayload) -> tuple[
    RequirementSnapshot, dict[str, Any]
]:
    """La instantania nova i un resum dels camps tocats (sense valors de text)."""
    data = current.model_dump()
    touched = [
        field for field in _SNAPSHOT_FIELDS
        if field in change.model_fields_set and data[field] != getattr(change, field)
    ]
    for field in touched:
        data[field] = getattr(change, field)
    if "title" in change.model_fields_set and change.title is None:
        raise ValidationError("El titol no es pot buidar.")

    values = {v["key"]: v for v in data["values"]}
    missing = [key for key in change.remove_values if key not in values]
    if missing:
        raise ValidationError(
            "No es pot treure un valor que no hi es.", details={"keys": missing}
        )
    for key in change.remove_values:
        del values[key]
    for value in change.values:
        values[value.key] = value.accepted().model_dump()
    data["values"] = list(values.values())
    snapshot = RequirementSnapshot.model_validate(data)
    if snapshot.model_dump(mode="json") == current.model_dump(mode="json"):
        raise ValidationError("La correccio no canvia res.")
    return snapshot, {
        "fields": touched,
        "values_set": [v.key for v in change.values],
        "values_removed": list(change.remove_values),
    }


async def apply_review_action(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    item_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    expected_revision: int,
    action: str,
    payload: dict[str, Any],
) -> ReviewItem:
    """Aplica una accio i n'afegeix la revisio. No fa commit."""
    item = await _locked_item(session, company_id, tender_id, item_id)
    if item.revision != expected_revision:
        raise StaleRevisionError(
            "Algu altre ha revisat aquest element. Torna'l a carregar.",
            details={"current_revision": item.revision, "expected_revision": expected_revision},
        )
    current = RequirementSnapshot.model_validate(item.accepted_json)
    before = ReviewVerification(item.verification_status)
    accepted = current
    note: str | None
    changes: dict[str, Any] = {}

    if action == ReviewAction.CONFIRM:
        parsed = _parse(ConfirmPayload, payload)
        after, note = ReviewVerification.VERIFIED, parsed.note
    elif action == ReviewAction.CORRECT:
        parsed = _parse(CorrectPayload, payload)
        accepted, changes = _corrected(current, parsed)
        after, note = parsed.verification_status, parsed.note
    elif action == ReviewAction.REJECT:
        parsed = _parse(ReasonPayload, payload)
        after, note = ReviewVerification.REJECTED, parsed.reason
    elif action == ReviewAction.MARK_PARTIAL:
        parsed = _parse(NotePayload, payload)
        after, note = ReviewVerification.PARTIAL, parsed.note
    elif action == ReviewAction.NOTE:
        parsed = _parse(NotePayload, payload)
        after, note = before, parsed.note
    elif action == ReviewAction.SET_STATUS:
        parsed = _parse(SetStatusPayload, payload)
        after, note = parsed.status, parsed.note
    else:  # pragma: no cover - l'esquema de la peticio ja ho limita
        raise ValidationError("Accio desconeguda.")

    changes.update(_status_change(before, after))
    revision_no = item.revision + 1
    accepted_json = accepted.model_dump(mode="json")
    session.add(
        ReviewItemRevision(
            item_id=item.id,
            company_id=company_id,
            revision_no=revision_no,
            action=ReviewAction(action),
            verification_status_after=after,
            accepted_json_after=accepted_json,
            note=note,
            payload_json=changes,
            actor_id=actor_id,
        )
    )
    item.revision = revision_no
    item.verification_status = after
    item.accepted_json = accepted_json
    item.updated_by = actor_id
    item.updated_at = datetime.now(UTC)
    try:
        await session.flush()
    except IntegrityError:
        # Xarxa de seguretat: la unicitat (element, revisio) ha fet de lock.
        await session.rollback()
        raise StaleRevisionError(
            "Algu altre ha revisat aquest element. Torna'l a carregar.",
            details={"expected_revision": expected_revision},
        ) from None
    return item


async def _citation_ref(
    session: AsyncSession, tender_id: uuid.UUID, user: User, citation: Any
):
    if isinstance(citation, ExtractedPageCitation):
        return await source_refs.create_extracted_page_ref(
            session,
            tender_id=tender_id,
            extraction_id=citation.extraction_id,
            page_id=citation.page_id,
            user=user,
            location_label=citation.location_label,
            char_start=citation.char_start,
            char_end=citation.char_end,
        )
    assert isinstance(citation, DocumentFileCitation)
    return await source_refs.create_document_file_ref(
        session,
        tender_id=tender_id,
        file_id=citation.file_id,
        user=user,
        location_label=citation.location_label,
        quote=citation.quote,
    )


async def create_manual_requirement(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    user: User,
    data: ManualRequirementCreate,
) -> ReviewItem:
    """Un requisit afegit pel revisor, NOMES per a aquesta empresa. No fa commit.

    No escriu a `requirements` (global). Neix `unreviewed` si no es diu res:
    afegir-lo no vol dir haver-lo comprovat. Les cites es creen aqui mateix, a
    partir d'una pagina o d'un fitxer d'AQUESTA licitacio, i s'enllacen com a
    evidencia addicional; mai no hi ha font primaria extreta.
    """
    snapshot = RequirementSnapshot(
        title=data.title,
        description=data.description,
        category=data.category,
        obligation=data.obligation,
        modality=data.modality,
        values=[value.accepted() for value in data.values],
        needs_review_from_extraction=False,
    )
    status = ReviewVerification(data.verification_status)
    accepted_json = snapshot.model_dump(mode="json")
    item = ReviewItem(
        id=uuid.uuid4(),
        company_id=company_id,
        tender_id=tender_id,
        kind=ReviewItemKind.REQUIREMENT,
        verification_status=status,
        award_role=None,
        accepted_json=accepted_json,
        origin_hint=ReviewOriginHint.MANUAL_REVIEW,
        revision=1,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(item)
    await session.flush()
    for citation in data.citations:
        ref = await _citation_ref(session, tender_id, user, citation)
        session.add(
            ReviewItemSource(
                item_id=item.id,
                company_id=company_id,
                source_ref_id=ref.id,
                link_role=ReviewSourceRole.EXTRA_EVIDENCE,
                created_by=user.id,
            )
        )
    session.add(
        ReviewItemRevision(
            item_id=item.id,
            company_id=company_id,
            revision_no=1,
            action=ReviewAction.CREATE,
            verification_status_after=status,
            accepted_json_after=accepted_json,
            note=data.note,
            payload_json={"source": "manual_review", "citations": len(data.citations)},
            actor_id=user.id,
        )
    )
    await session.flush()
    return item


async def history(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    item_id: uuid.UUID,
) -> list[ReviewHistoryEntry]:
    """L'historial d'un element d'AQUESTA empresa, en ordre. D'una altra: 404."""
    owned = await session.scalar(
        sa.select(ReviewItem.id).where(
            ReviewItem.id == item_id,
            ReviewItem.company_id == company_id,
            ReviewItem.tender_id == tender_id,
        )
    )
    if owned is None:
        raise NotFoundError("Element de revisio no trobat.")
    rows = await session.scalars(
        sa.select(ReviewItemRevision)
        .where(
            ReviewItemRevision.item_id == item_id,
            ReviewItemRevision.company_id == company_id,
        )
        .order_by(ReviewItemRevision.revision_no)
    )
    return [
        ReviewHistoryEntry(
            revision_no=row.revision_no,
            action=ReviewAction(row.action).value,
            action_label=ACTION_LABELS[ReviewAction(row.action)],
            status_after=ReviewVerification(row.verification_status_after).value,
            status_label=VERIFICATION_LABELS[ReviewVerification(row.verification_status_after)],
            accepted=row.accepted_json_after,
            changes=row.payload_json or {},
            note=row.note,
            actor_id=row.actor_id,
            created_at=row.created_at,
        )
        for row in rows
    ]

