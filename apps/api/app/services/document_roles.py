"""Rol efectiu de cada document d'una licitacio: el resolutor (3B-2 §3.2).

Combina, NOMES EN LLEGIR, tres capes que no s'escriuen mai l'una sobre l'altra
(mes l'override de cada empresa):

1. override de l'empresa (`tender_document_role_decisions`), si es de la MATEIXA
   versio del fitxer (`content_sha256`). Si es d'una versio anterior s'ignora i
   es diu: `source = stale_override`, amb el rol de la capa seguent;
2. revisio global de l'operador (`document_role_reviews`), de la mateixa versio;
3. suggeriment de la classificacio global vigent, nomes amb confianca alta i si
   no es un duplicat;
4. el `document_type` del feed (o de la propietaria, en una licitacio manual).

`company_id=None` es la lectura compartida: la que fa servir l'extraccio global
de requisits. L'override d'una empresa no la canvia mai (opcio A, §3.2).

Aqui tambe viu la revisio de l'operador (`record_role_review`): nomes d'afegir,
amb actor, motiu, versio del fitxer i passada de base.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.classification.roles import ROLE_TO_DOCUMENT_TYPE
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.classification import TenderDocumentRoleDecision
from app.models.enums import (
    AuditAction,
    DocumentRole,
    RoleConfidence,
    RoleReviewAuthority,
    RoleSource,
    TenderDocumentType,
)
from app.models.global_classification import (
    DocumentRoleReview,
    GlobalDocumentClassificationRun,
    GlobalDocumentRoleSuggestion,
    review_heads,
    tender_reviews_query,
)
from app.models.identity import User
from app.models.provenance import TenderSourceRef
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tenders import Tender, TenderDocument
from app.services import audit
from app.services.global_document_classification import current_global_run, run_suggestions
from app.services.tender_lifecycle import DOCUMENT_INTAKE_TRIGGER, record_revision
from app.tenders.documents import analyzable_documents


class DocumentVersionChangedError(ConflictError):
    """La revisio es d'una versio del fitxer que ja no es la vigent."""

    code = "document_version_changed"


class DocumentNotProcessedError(ConflictError):
    """El document encara no te cap fitxer amb contingut: no hi ha res a revisar."""

    code = "document_not_processed"


class SourceRefNotInTenderError(ValidationError):
    """La cita de la revisio no es de la licitacio (o del document) revisat."""

    code = "source_ref_not_in_tender"


class DocumentNotReviewableError(ConflictError):
    """Un document retirat no es revisa: ja no es de la publicacio vigent."""

    code = "document_not_reviewable"


@dataclass(frozen=True, slots=True)
class EffectiveRole:
    #: `None` quan nomes hi ha el tipus del feed (cap capa n'ha dit el rol).
    role: DocumentRole | None
    document_type: TenderDocumentType
    source: RoleSource
    #: La versio del fitxer vigent (`None` si encara no s'ha baixat).
    content_sha256: str | None
    #: Amb `stale_override`: la capa que mana de fet.
    fallback_source: RoleSource | None = None


async def current_shas(
    session: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """El sha del fitxer vigent de cada document (els no baixats no hi son)."""
    if not document_ids:
        return {}
    rows = await session.execute(
        sa.select(TenderDocumentFile.tender_document_id, TenderDocumentContent.sha256)
        .join(TenderDocumentContent, TenderDocumentContent.id == TenderDocumentFile.content_id)
        .where(
            TenderDocumentFile.tender_document_id.in_(document_ids),
            TenderDocumentFile.is_current.is_(True),
        )
    )
    return dict(rows.all())


async def current_reviews(
    session: AsyncSession, tender_id: uuid.UUID
) -> dict[str, DocumentRoleReview]:
    """La revisio vigent de cada contingut de la licitacio (vegeu `review_heads`).

    Per contingut i no per document: si la font torna a publicar els mateixos
    bytes en una URL nova, la revisio continua valent per al document nou.
    """
    return review_heads(await session.scalars(tender_reviews_query(tender_id)))


async def _decisions(
    session: AsyncSession, company_id: uuid.UUID, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, TenderDocumentRoleDecision]:
    if not document_ids:
        return {}
    rows = await session.scalars(
        sa.select(TenderDocumentRoleDecision).where(
            TenderDocumentRoleDecision.company_id == company_id,
            TenderDocumentRoleDecision.tender_document_id.in_(document_ids),
        )
    )
    return {d.tender_document_id: d for d in rows}


def _from_suggestion(
    suggestion: GlobalDocumentRoleSuggestion | None, sha: str | None
) -> EffectiveRole | None:
    if suggestion is None or sha is None or suggestion.content_sha256 != sha:
        return None
    if suggestion.confidence != RoleConfidence.HIGH or (suggestion.signals or {}).get(
        "duplicate_of"
    ):
        return None
    return EffectiveRole(
        role=DocumentRole(suggestion.role),
        document_type=TenderDocumentType(suggestion.document_type),
        source=RoleSource.GLOBAL_CLASSIFIER,
        content_sha256=sha,
    )


async def effective_document_roles(
    session: AsyncSession, tender_id: uuid.UUID, company_id: uuid.UUID | None
) -> dict[uuid.UUID, EffectiveRole]:
    """El rol efectiu de cada document ANALITZABLE de la licitacio."""
    documents = await analyzable_documents(session, tender_id)
    ids = [d.id for d in documents]
    shas = await current_shas(session, ids)
    reviews = await current_reviews(session, tender_id)
    suggestions = await run_suggestions(session, await current_global_run(session, tender_id))
    decisions = await _decisions(session, company_id, ids) if company_id else {}

    roles: dict[uuid.UUID, EffectiveRole] = {}
    for document in documents:
        sha = shas.get(document.id)
        effective = EffectiveRole(
            role=None,
            document_type=TenderDocumentType(document.document_type),
            source=RoleSource.FEED,
            content_sha256=sha,
        )
        effective = _from_suggestion(suggestions.get(document.id), sha) or effective
        review = reviews.get(sha) if sha else None
        if review is not None:
            effective = EffectiveRole(
                role=DocumentRole(review.role),
                document_type=ROLE_TO_DOCUMENT_TYPE[DocumentRole(review.role)],
                source=RoleSource.OPERATOR,
                content_sha256=sha,
            )
        decision = decisions.get(document.id)
        if decision is not None:
            if sha is not None and decision.content_sha256 == sha:
                effective = EffectiveRole(
                    role=DocumentRole(decision.role),
                    document_type=ROLE_TO_DOCUMENT_TYPE[DocumentRole(decision.role)],
                    source=RoleSource.PERSON,
                    content_sha256=sha,
                )
            else:
                # Decisio d'una versio anterior: no mana, pero es diu.
                effective = replace(
                    effective,
                    source=RoleSource.STALE_OVERRIDE,
                    fallback_source=effective.source,
                )
        roles[document.id] = effective
    return roles


async def role_overrides_hash(
    session: AsyncSession, tender_id: uuid.UUID, company_id: uuid.UUID
) -> str | None:
    """sha256 dels overrides de l'empresa en aquesta licitacio. `None` si no en te cap."""
    rows = (
        await session.execute(
            sa.select(
                TenderDocumentRoleDecision.tender_document_id,
                TenderDocumentRoleDecision.content_sha256,
                TenderDocumentRoleDecision.role,
            )
            .join(
                TenderDocument, TenderDocument.id == TenderDocumentRoleDecision.tender_document_id
            )
            .where(
                TenderDocumentRoleDecision.company_id == company_id,
                TenderDocument.tender_id == tender_id,
            )
        )
    ).all()
    if not rows:
        return None
    canonical = sorted([str(document_id), sha, str(role)] for document_id, sha, role in rows)
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Revisio de l'operador                                                         #
# --------------------------------------------------------------------------- #


async def record_role_review(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    content_sha256: str,
    role: DocumentRole,
    actor: User,
    reason: str,
    based_on_run_id: uuid.UUID | None,
    based_on_suggestion_id: uuid.UUID | None = None,
    source_ref_id: uuid.UUID | None = None,
    authority: RoleReviewAuthority = RoleReviewAuthority.PLATFORM_OPERATOR,
) -> DocumentRoleReview:
    """Afegeix una revisio de rol (mai en modifica cap). No fa commit.

    Ha de ser de la versio VIGENT del fitxer: qui revisa respon del que ha vist.
    Substitueix (`supersedes_id`) la revisio vigent del mateix contingut al tender.
    Deixa auditoria i una revisio del tender `document_intake` (la fotografia
    porta el rol revisat; vegeu `tender_lifecycle._document_entry`).
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Cal el motiu de la revisio del rol.")
    document = await session.get(TenderDocument, document_id)
    if document is None:
        raise NotFoundError("Document no trobat.")
    if document.removed_at is not None:
        raise DocumentNotReviewableError("El document esta retirat: no es revisa.")
    sha = (await current_shas(session, [document_id])).get(document_id)
    if sha is None:
        raise DocumentNotProcessedError("El document encara no s'ha baixat.")
    if sha != content_sha256:
        raise DocumentVersionChangedError(
            "El fitxer ha canviat des que s'ha revisat: cal tornar-lo a mirar.",
            details={"current_sha256": sha},
        )
    if based_on_run_id is not None:
        run = await session.get(GlobalDocumentClassificationRun, based_on_run_id)
        if run is None or run.tender_id != document.tender_id:
            raise ValidationError("La passada de base no es d'aquesta licitacio.")
    if based_on_suggestion_id is not None:
        suggestion = await session.get(GlobalDocumentRoleSuggestion, based_on_suggestion_id)
        if (
            suggestion is None
            or suggestion.tender_document_id != document_id
            or (based_on_run_id is not None and suggestion.run_id != based_on_run_id)
        ):
            raise ValidationError("El suggeriment de base no es d'aquest document.")
    if source_ref_id is not None:
        ref = await session.get(TenderSourceRef, source_ref_id)
        if (
            ref is None
            or ref.tender_id != document.tender_id
            or (ref.tender_document_id is not None and ref.tender_document_id != document_id)
        ):
            raise SourceRefNotInTenderError(
                "La cita no es d'aquesta licitacio o d'aquest document.",
                details={"source_ref_id": str(source_ref_id)},
            )

    previous = (await current_reviews(session, document.tender_id)).get(sha)
    review = DocumentRoleReview(
        tender_document_id=document_id,
        content_sha256=sha,
        role=role,
        authority=authority,
        actor_id=actor.id,
        reason=reason[:500],
        based_on_run_id=based_on_run_id,
        based_on_suggestion_id=based_on_suggestion_id,
        source_ref_id=source_ref_id,
        supersedes_id=previous.id if previous else None,
        # Explicit: la lectura de les vigents (`review_heads`) el fa servir dins
        # de la mateixa sessio, abans de cap refresc.
        created_at=datetime.now(UTC),
    )
    session.add(review)
    await session.flush()
    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=None,
        user_id=actor.id,
        entity_type="document_role_review",
        entity_id=review.id,
        summary=f"document={document_id}; rol={role}; abans={previous.role if previous else '-'}",
    )
    tender = await session.get(Tender, document.tender_id)
    if tender is not None:
        await record_revision(session, tender, trigger=DOCUMENT_INTAKE_TRIGGER)
    return review


__all__ = [
    "DocumentNotProcessedError",
    "DocumentNotReviewableError",
    "DocumentVersionChangedError",
    "SourceRefNotInTenderError",
    "EffectiveRole",
    "current_reviews",
    "current_shas",
    "effective_document_roles",
    "record_role_review",
    "role_overrides_hash",
]
