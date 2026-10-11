"""Classificacio global d'una licitacio publica: via de l'operador (3B-2 §3.2, §3.3).

Nomes l'Administracio Tuvi (`PlatformAdmin`: superusuari sense empresa):

* `POST /platform/tenders/{id}/classification/run`: torna a classificar a
  peticio (`operator_request`). Idempotent per empremta.
* `GET /platform/tenders/{id}/classification`: la passada vigent amb els seus
  suggeriments i troballes (el que va PROPOSAR el classificador, immutable), les
  revisions de l'operador (a part, amb actor i motiu), els rols efectius de la
  lectura compartida i els forats del paquet.
* `POST /platform/tenders/{id}/documents/{d}/role-review`: revisa el rol global
  d'un document. Nomes d'afegir; motiu i versio del fitxer obligatoris.

No hi ha cap endpoint de propostes de rol (fora d'abast de 3B-2). Una empresa
no hi entra (403); els seus endpoints de classificacio refusen els tenders
publics (`tender_not_manual`).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter

from app.api.deps import PlatformAdmin, SessionDep
from app.core.errors import NotFoundError
from app.models.enums import AuditAction, GlobalClassificationTrigger
from app.models.global_classification import GlobalDocumentRoleSuggestion
from app.models.tenders import TenderDocument
from app.schemas.classification import (
    DocumentRoleReviewRead,
    EffectiveRoleRead,
    GlobalClassificationFindingRead,
    GlobalClassificationRead,
    GlobalClassificationRunRead,
    GlobalRoleSuggestionRead,
    PackageGapRead,
    RoleReviewWrite,
)
from app.services import audit, document_roles
from app.services import global_document_classification as service
from app.services.public_document_intake import load_public_tender

router = APIRouter(prefix="/platform/tenders/{tender_id}", tags=["platform"])


async def _read(session: SessionDep, tender_id: uuid.UUID) -> GlobalClassificationRead:
    view = await service.load_global_view(session, tender_id)
    roles = await document_roles.effective_document_roles(session, tender_id, None)
    return GlobalClassificationRead(
        run=GlobalClassificationRunRead.model_validate(view.run) if view.run else None,
        suggestions=[GlobalRoleSuggestionRead.model_validate(s) for s in view.suggestions],
        findings=[GlobalClassificationFindingRead.model_validate(f) for f in view.findings],
        reviews=[DocumentRoleReviewRead.model_validate(r) for r in view.reviews],
        effective_roles=[
            EffectiveRoleRead(
                tender_document_id=document_id,
                role=role.role,
                document_type=role.document_type,
                source=role.source,
                content_sha256=role.content_sha256,
                fallback_source=role.fallback_source,
            )
            for document_id, role in sorted(roles.items(), key=lambda item: str(item[0]))
        ],
        gaps=[PackageGapRead.model_validate(g) for g in view.gaps],
    )


@router.get(
    "/classification",
    response_model=GlobalClassificationRead,
    summary="Classificacio global vigent, revisions i rols efectius",
)
async def get_classification(
    tender_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep
) -> GlobalClassificationRead:
    await load_public_tender(session, tender_id)
    return await _read(session, tender_id)


@router.post(
    "/classification/run",
    response_model=GlobalClassificationRead,
    summary="Torna a classificar els documents analitzables (si han canviat)",
)
async def run_classification(
    tender_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep
) -> GlobalClassificationRead:
    await load_public_tender(session, tender_id)
    previous = await service.current_global_run(session, tender_id)
    previous_id = previous.id if previous else None  # abans de cap rollback (cursa)
    run = await service.classify_tender_globally(
        session, tender_id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    # Nomes s'audita una passada que ha creat aquesta peticio: si el worker l'ha
    # desat alhora (cursa), es torna la seva sense atribuir-la a l'operador.
    if (
        run is not None
        and run.id != previous_id
        and run.trigger == GlobalClassificationTrigger.OPERATOR_REQUEST
    ):
        await audit.record(
            session,
            action=AuditAction.CREATE,
            company_id=None,
            user_id=admin.id,
            entity_type="global_document_classification",
            entity_id=run.id,
            summary=f"licitacio={tender_id}; documents={run.document_count}",
        )
        await session.commit()
    return await _read(session, tender_id)


@router.post(
    "/documents/{document_id}/role-review",
    response_model=GlobalClassificationRead,
    summary="Revisa el rol global d'un document (nomes d'afegir, amb motiu)",
)
async def review_role(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: RoleReviewWrite,
    admin: PlatformAdmin,
    session: SessionDep,
) -> GlobalClassificationRead:
    await load_public_tender(session, tender_id)
    document = await session.scalar(
        sa.select(TenderDocument).where(
            TenderDocument.id == document_id, TenderDocument.tender_id == tender_id
        )
    )
    if document is None:
        raise NotFoundError("Document no trobat.")

    based_on_run_id = payload.based_on_run_id
    based_on_suggestion_id = payload.based_on_suggestion_id
    if based_on_run_id is None:
        # Per defecte, la passada vigent i el que va proposar per a aquest document.
        current = await service.current_global_run(session, tender_id)
        if current is not None:
            based_on_run_id = current.id
            if based_on_suggestion_id is None:
                based_on_suggestion_id = await session.scalar(
                    sa.select(GlobalDocumentRoleSuggestion.id).where(
                        GlobalDocumentRoleSuggestion.run_id == current.id,
                        GlobalDocumentRoleSuggestion.tender_document_id == document_id,
                    )
                )

    await document_roles.record_role_review(
        session,
        document_id=document_id,
        content_sha256=payload.content_sha256,
        role=payload.role,
        actor=admin,
        reason=payload.reason,
        based_on_run_id=based_on_run_id,
        based_on_suggestion_id=based_on_suggestion_id,
        source_ref_id=payload.source_ref_id,
    )
    await session.commit()
    return await _read(session, tender_id)
