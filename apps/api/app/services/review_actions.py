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
    ReviewAwardRole,
    ReviewItemKind,
    ReviewOriginHint,
    ReviewSourceRole,
    ReviewVerification,
    SourceRefKind,
)
from app.models.identity import User
from app.models.provenance import TenderSourceRef
from app.models.review import ReviewItem, ReviewItemRevision, ReviewItemSource
from app.schemas.review import (
    AwardSnapshot,
    ConfirmPayload,
    CorrectPayload,
    DocumentFileCitation,
    ExtractedPageCitation,
    LinkSourcePayload,
    ManualRequirementCreate,
    NotePayload,
    ReasonPayload,
    RequirementSnapshot,
    ReviewHistoryEntry,
    SetAwardRolePayload,
    SetStatusPayload,
)
from app.services import source_refs
from app.services.manual_requirement import REQUIREMENT_EDIT_ROLES
from app.services.review_view import VERIFICATION_LABELS

ACTION_LABELS = {
    ReviewAction.CREATE: "Creat",
    ReviewAction.CONFIRM: "Confirmat",
    ReviewAction.CORRECT: "Corregit",
    ReviewAction.REJECT: "Descartat",
    ReviewAction.MARK_PARTIAL: "Marcat com a parcial",
    ReviewAction.NOTE: "Nota",
    ReviewAction.SET_STATUS: "Canvi d'estat",
    ReviewAction.SET_AWARD_ROLE: "Canvi de paper",
    ReviewAction.LINK_SOURCE: "Evidència afegida",
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
    """L'element actiu d'aquesta empresa i licitacio, de qualsevol tipus, bloquejat."""
    item = await session.scalar(
        sa.select(ReviewItem)
        .where(
            ReviewItem.id == item_id,
            ReviewItem.company_id == company_id,
            ReviewItem.tender_id == tender_id,
            ReviewItem.archived_at.is_(None),
        )
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
    user: User | None = None,
    provenance: dict[str, str] | None = None,
) -> ReviewItem:
    """Aplica una accio i n'afegeix la revisio. No fa commit.

    `user` nomes cal per a `link_source`: la cita nova en guarda l'autor.
    `provenance` (opcional) son identificadors segurs que s'afegeixen a la
    carrega de la revisio (p. ex. el lot d'importacio); mai text del plec.
    """
    item = await _locked_item(session, company_id, tender_id, item_id)
    if item.revision != expected_revision:
        raise StaleRevisionError(
            "Algu altre ha revisat aquest element. Torna'l a carregar.",
            details={"current_revision": item.revision, "expected_revision": expected_revision},
        )
    is_award = item.kind == ReviewItemKind.AWARD_CRITERION
    current: Any = (AwardSnapshot if is_award else RequirementSnapshot).model_validate(
        item.accepted_json
    )
    before = ReviewVerification(item.verification_status)
    accepted = current
    note: str | None
    changes: dict[str, Any] = {}
    if action == ReviewAction.SET_AWARD_ROLE and not is_award:
        raise ValidationError("Nomes un criteri d'adjudicacio te paper de puntuacio.")
    if action == ReviewAction.CORRECT and is_award:
        # Els punts i la formula no es reescriuen en aquesta fase: s'assenyalen.
        raise ValidationError("Un criteri d'adjudicacio no es pot corregir encara.")

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
    elif action == ReviewAction.LINK_SOURCE:
        parsed = _parse(LinkSourcePayload, payload)
        changes = await _link_extra_evidence(session, item, tender_id, user, parsed.citation)
        after, note = before, parsed.note
    elif action == ReviewAction.SET_AWARD_ROLE:
        parsed = _parse(SetAwardRolePayload, payload)
        role_before = ReviewAwardRole(item.award_role) if item.award_role else None
        if role_before == parsed.role:
            raise ValidationError("El criteri ja te aquest paper.")
        changes = {
            "role_before": role_before.value if role_before else None,
            "role_after": parsed.role.value,
        }
        # Assignar un paper no verifica el criteri: l'estat no es mou.
        item.award_role = parsed.role
        after, note = before, parsed.note
    else:  # pragma: no cover - l'esquema de la peticio ja ho limita
        raise ValidationError("Accio desconeguda.")

    changes.update(_status_change(before, after))
    changes.update(provenance or {})
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


class SourceAlreadyLinkedError(ConflictError):
    """Aquesta mateixa cita ja es evidencia de l'element."""

    code = "review_source_already_linked"


def _same(column: Any, value: Any) -> Any:
    return column.is_(None) if value is None else column == value


async def _link_extra_evidence(
    session: AsyncSession,
    item: ReviewItem,
    tender_id: uuid.UUID,
    user: User | None,
    citation: Any,
) -> dict[str, Any]:
    """Crea la cita i l'enllaca com a evidencia addicional. Torna nomes identificadors.

    La mateixa ancora (mateixa pagina i posicions, o mateix fitxer i lloc) ja
    enllacada a l'element es 409: no s'hi crea cap cita ni cap enllac nous.
    """
    if isinstance(citation, ExtractedPageCitation):
        anchor = sa.and_(
            TenderSourceRef.ref_kind == SourceRefKind.EXTRACTED_PAGE,
            TenderSourceRef.extraction_id == citation.extraction_id,
            TenderSourceRef.page_id == citation.page_id,
            _same(TenderSourceRef.char_start, citation.char_start),
            _same(TenderSourceRef.char_end, citation.char_end),
        )
    else:
        anchor = sa.and_(
            TenderSourceRef.ref_kind == SourceRefKind.DOCUMENT_FILE,
            TenderSourceRef.tender_document_file_id == citation.file_id,
            _same(TenderSourceRef.location_label, citation.location_label),
            _same(TenderSourceRef.quote, citation.quote),
        )
    existing = await session.scalar(
        sa.select(TenderSourceRef.id)
        .join(ReviewItemSource, ReviewItemSource.source_ref_id == TenderSourceRef.id)
        .where(
            ReviewItemSource.item_id == item.id,
            ReviewItemSource.unlinked_at.is_(None),
            anchor,
        )
    )
    if existing is not None:
        raise SourceAlreadyLinkedError(
            "Aquesta cita ja es evidencia d'aquest element.",
            details={"source_ref_id": str(existing)},
        )
    ref = await _citation_ref(session, tender_id, user, citation)
    session.add(
        ReviewItemSource(
            item_id=item.id,
            company_id=item.company_id,
            source_ref_id=ref.id,
            link_role=ReviewSourceRole.EXTRA_EVIDENCE,
            created_by=user.id if user else None,
        )
    )
    return {
        "source_ref_id": str(ref.id),
        "ref_kind": ref.ref_kind.value,
        "extraction_id": str(ref.extraction_id) if ref.extraction_id else None,
        "page_id": str(ref.page_id) if ref.page_id else None,
        "page_number": ref.page_number,
        "file_id": str(ref.tender_document_file_id) if ref.tender_document_file_id else None,
        "location_label": ref.location_label,
    }


async def _citation_ref(
    session: AsyncSession, tender_id: uuid.UUID, user: User | None, citation: Any
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
    provenance: dict[str, str] | None = None,
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
            payload_json={
                "source": "manual_review",
                "citations": len(data.citations),
                **(provenance or {}),
            },
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

