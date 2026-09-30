"""Classificacio documental d'una licitacio (capa 3 del motor d'analisi).

Tot penja de `/companies/{company_id}/tenders/{tender_id}/document-classification`.
No depen de per on van arribar els documents: classifica els de la licitacio.

* Llegir-la: qualsevol membre que vegi la licitacio.
* Demanar-la de nou o fixar un rol: rols que editen, i nomes en una licitacio
  oberta a ma i de la propia empresa. La classificacio pot canviar el
  `document_type` que llegeix l'analisi, i en una licitacio publica aquest camp
  es de tothom.

L'auditoria porta identificadors i codis: mai noms de fitxer ni text del plec.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CompanyCtx, SessionDep
from app.models.enums import AuditAction, ClassificationTrigger, RoleSource
from app.schemas.classification import (
    ClassificationFindingRead,
    ClassificationRead,
    ClassificationRunRead,
    ClassifiedDocumentRead,
    RoleDecisionWrite,
)
from app.services import audit, manual_tender
from app.services import document_classification as service
from app.services.review_view import load_visible_tender

router = APIRouter(
    prefix="/tenders/{tender_id}/document-classification", tags=["classificacio documental"]
)


def build_read(view: service.ClassificationView) -> ClassificationRead:
    documents = []
    for item in view.documents:
        suggestion, decision = item.suggestion, item.decision
        documents.append(
            ClassifiedDocumentRead(
                tender_document_id=item.document.id,
                name=item.document.name,
                path=item.path,
                mime_type=item.mime_type,
                sha256=item.sha256,
                document_type=item.document.document_type,
                role=item.role,
                role_source=(
                    RoleSource.PERSON if decision else RoleSource.CLASSIFIER if suggestion else None
                ),
                suggested_role=suggestion.role if suggestion else None,
                suggested_confidence=suggestion.confidence if suggestion else None,
                decided_at=decision.decided_at if decision else None,
                needs_review=item.needs_review,
                signals=suggestion.signals if suggestion else None,
            )
        )
    return ClassificationRead(
        run=ClassificationRunRead.model_validate(view.run) if view.run else None,
        documents=documents,
        findings=[ClassificationFindingRead.model_validate(f) for f in view.findings],
    )


@router.get("", response_model=ClassificationRead, summary="Classificacio vigent dels documents")
async def get_classification(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    await load_visible_tender(session, ctx.company_id, tender_id)
    return build_read(await service.load_view(session, ctx.company_id, tender_id))


@router.post("", response_model=ClassificationRead, summary="Torna a classificar els documents")
async def reclassify(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Classifica tots els documents de la licitacio. Si no han canviat, no fa res.

    Serveix despres d'una pujada individual o de qualsevol altra via que no
    classifiqui sola.
    """
    manual_tender.assert_can_edit(ctx.membership.role)
    tender = await manual_tender.load_editable_tender(session, ctx.company_id, tender_id)
    run = await service.classify_tender(
        session,
        tender=tender,
        company_id=ctx.company_id,
        trigger=ClassificationTrigger.REQUEST,
        user=ctx.user,
    )
    if run is not None:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.CREATE,
            entity_type="document_classification",
            entity_id=run.id,
            summary=manual_tender.audit_summary(licitacio=tender_id, documents=run.document_count),
        )
        await session.commit()
    return build_read(await service.load_view(session, ctx.company_id, tender_id))


@router.put(
    "/documents/{document_id}/role",
    response_model=ClassificationRead,
    summary="Fixa el rol d'un document",
)
async def decide_role(
    tender_id: uuid.UUID,
    document_id: uuid.UUID,
    body: RoleDecisionWrite,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Confirma o corregeix el rol. El que va proposar el classificador es conserva."""
    manual_tender.assert_can_edit(ctx.membership.role)
    tender = await manual_tender.load_editable_tender(session, ctx.company_id, tender_id)
    decision = await service.decide_role(
        session,
        tender=tender,
        company_id=ctx.company_id,
        document_id=document_id,
        role=body.role,
        user=ctx.user,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="tender_document_role",
        entity_id=decision.tender_document_id,
        summary=manual_tender.audit_summary(licitacio=tender_id, rol=body.role),
    )
    await session.commit()
    return build_read(await service.load_view(session, ctx.company_id, tender_id))
