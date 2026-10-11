"""La porta final i la revisio de l'abast tecnic: HTTP.

Dos endpoints i una sola idea: que es pugui veure, amb noms concrets, **per que**
una candidatura encara no es pot tancar, i que hi hagi un lloc on una persona
digui "he mirat quins requisits necessiten solucio tecnica".

Aquesta segona afirmacio es la que fa que zero solucions tecniques vulgui dir
alguna cosa (C-14). Sense ella, zero solucions es indistingible de "ningu no s'ho
ha mirat", i el sistema no te cap manera de saber-ho: C-11 li prohibeix deduir-ho.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CompanyCtx, SessionDep
from app.models.enums import AuditAction
from app.schemas.readiness import (
    DocumentReviewRead,
    DossierReadinessRead,
    EconomicReadinessRead,
    FinalReadinessFinding,
    FinalReadinessRead,
    TechnicalReadinessRead,
)
from app.services import audit, document_review
from app.services import bid_workspace as workspace_service
from app.services import economic_offer as offer_service
from app.services import technical_scope as scope_service
from app.services import workspace_readiness as service
from app.services.workspace_gate import assert_workspace_go
from app.workspace.readiness import DOMAIN_ECONOMIC, DOMAIN_TECHNICAL

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["readiness"])


def _findings(findings) -> list[FinalReadinessFinding]:
    return [
        FinalReadinessFinding(
            code=f.code,
            severity=f.severity,
            domain=f.domain,
            entity_id=f.entity_id,
            context=dict(f.context),
        )
        for f in findings
    ]


async def _read(session: SessionDep, workspace) -> FinalReadinessRead:
    final = await service.final_readiness(session, workspace)
    scope = await scope_service.resolve(session, workspace)
    documents = await document_review.review_state(session, workspace)
    review = await scope_service.review_state(session, workspace, scope)
    try:
        await offer_service.get_offer(session, workspace)
        offer_exists = True
    except Exception:  # noqa: BLE001 - l'absencia d'oferta ja es un finding
        offer_exists = False

    return FinalReadinessRead(
        workspace_id=workspace.id,
        tender_id=workspace.tender_id,
        ready_for_final_review=final.ready,
        findings=_findings(final.findings),
        dossier=DossierReadinessRead(
            state=final.dossier_state,
            blockers=[
                f.code for f in final.findings if f.domain in ("dossier", "workspace")
            ],
        ),
        documents=DocumentReviewRead(
            reviewed=documents.reviewed,
            stale=documents.stale,
            reviewed_at=documents.reviewed_at,
            documents=documents.document_count,
        ),
        technical=TechnicalReadinessRead(
            scope_reviewed=review.reviewed,
            scope_stale=review.stale,
            scope_reviewed_at=review.reviewed_at,
            scope_reviewed_by=review.reviewed_by,
            requirements_in_scope=len(scope.requirements),
            findings=_findings(final.by_domain(DOMAIN_TECHNICAL)),
        ),
        economic=EconomicReadinessRead(
            offer_exists=offer_exists,
            findings=_findings(final.by_domain(DOMAIN_ECONOMIC)),
        ),
    )


@router.get(
    "/final-readiness",
    response_model=FinalReadinessRead,
    summary="Per que la candidatura encara no es pot tancar",
)
async def get_final_readiness(
    workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    """Lectura per a qualsevol membre: aqui no hi ha res intern."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    return await _read(session, workspace)


@router.post(
    "/technical-scope/review",
    response_model=FinalReadinessRead,
    summary="Confirma que s'ha revisat l'abast tecnic",
)
async def review_technical_scope(
    workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    """Una persona afirma: "he revisat els requisits d'ara i he creat les
    solucions que calen".

    No demana cap motiu escrit: no es una decisio que comprometi res, es dir que
    algu ho ha mirat. Reconfirmar el mateix abast es idempotent i no deixa rastre
    nou; si l'abast ha canviat, es una afirmacio nova i s'actualitza qui i quan.
    """
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_go(session, workspace)
    # Politica propia (`TECHNICAL_SCOPE_REVIEW_ROLES`), encara que avui tingui
    # els mateixos membres que l'edicio de solucions: son dues afirmacions
    # diferents i no han de quedar lligades per compartir una constant.
    scope_service.assert_can_review(ctx.membership.role)
    _workspace, changed = await scope_service.confirm(session, workspace, user=ctx.user)
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_technical_scope",
            entity_id=workspace.id,
            summary=f"workspace={workspace.id}",
        )
        await session.commit()
    return await _read(session, workspace)


@router.post(
    "/documents/review",
    response_model=FinalReadinessRead,
    summary="Confirma que s'han revisat els plecs",
)
async def review_documents(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Una persona afirma: "he mirat els plecs que hi ha ara".

    Aixo es tot el que vol dir. **No** extreu requisits, **no** valida cap
    solucio tecnica, **no** aprova cap paquet i **no** afirma que no hi hagi
    canvis: nomes deixa escrit fins on ha arribat la lectura d'algu. Llegir els
    plecs i analitzar-los son dues accions diferents i es fan per separat.

    Repetir-ho sobre el mateix conjunt es idempotent i no mou ni la data ni
    l'auditoria: la revisio vigent segueix sent la mateixa.
    """
    # Sense guard de GO (3B-2, ruling C3): llegir els plecs es feina de Decidir,
    # no del pas Preparar. L'abast tecnic (`technical-scope/review`) si que ho es.
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    document_review.assert_can_review(ctx.membership.role)
    _state, changed = await document_review.mark_reviewed(session, workspace)
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_documents_review",
            entity_id=workspace.id,
            summary=f"workspace={workspace.id}",
        )
    await session.commit()
    return await _read(session, workspace)
