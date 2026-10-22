"""Dubtes i discrepancies: HTTP (S3-S10).

Dues coses que val la pena llegir aqui.

**Hi ha quatre escriptures i no una.** Corregir, resoldre, descartar i decidir si
bloqueja son accions diferents, i cadascuna te el seu endpoint. Si totes
passessin pel PATCH general, tancar un dubte sense resposta o aturar una
candidatura serien un camp mes del formulari.

**No hi ha DELETE.** Un dubte es tanca resolent-lo o descartant-lo. Esborrar-lo
faria desapareixer la pregunta, la seva provinenca i la seva resposta, que es
justament el que aquesta taula existeix per conservar.

**Es treballen en l'analisi, abans de decidir** (ajust del propietari,
22-10-2026): obrir, corregir, resoldre, descartar i citar dubtes no demana cap
decisio; nomes un NO-GO vigent ho tanca (`assert_workspace_study_open`). Decidir
si un dubte impedeix la revisio final es una decisio d'oferta i continua
demanant GO.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from fastapi import status as http_status

from app.api.deps import CompanyCtx, SessionDep
from app.models.enums import AuditAction, IssueStatus
from app.models.issues import WorkspaceIssue
from app.schemas.issues import (
    IssueBlockWrite,
    IssueCreate,
    IssueDismiss,
    IssueRead,
    IssueResolve,
    IssueSourceRefWrite,
    IssueUpdate,
)
from app.schemas.provenance import read_of
from app.services import audit
from app.services import bid_workspace as workspace_service
from app.services import issues as service
from app.services.source_refs import resolve_source_ref
from app.services.workspace_gate import assert_workspace_go, assert_workspace_study_open

router = APIRouter(prefix="/workspaces/{workspace_id}/issues", tags=["dubtes"])


async def _read(session, issue: WorkspaceIssue, refs=None) -> IssueRead:
    """La fitxa d'un dubte, amb les seves cites resoltes.

    Nomes identificadors de les entitats enllacades: incrustar-hi la linia
    economica sencera hi faria entrar el cost per una via sense redaccio.
    """
    if refs is None:
        refs = (await service.source_refs_of(session, [issue.id])).get(issue.id, [])
    read = IssueRead.model_validate(issue)
    read.source_refs = [
        read_of(await resolve_source_ref(session, ref)) for ref in refs
    ]
    return read


@router.get("", response_model=list[IssueRead], summary="Dubtes i discrepancies")
async def list_issues(
    workspace_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    status: IssueStatus | None = None,
):
    """Lectura per a qualsevol membre: un dubte obert no es cap secret."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    rows = await service.list_issues(session, workspace, status=status)
    refs = await service.source_refs_of(session, [row.id for row in rows])
    return [await _read(session, row, refs.get(row.id, [])) for row in rows]


@router.get("/{issue_id}", response_model=IssueRead, summary="Detall d'un dubte")
async def get_issue(
    workspace_id: uuid.UUID, issue_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    return await _read(session, await service.load_issue(session, workspace, issue_id))


@router.post(
    "",
    response_model=IssueRead,
    status_code=http_status.HTTP_201_CREATED,
    summary="Obre un dubte",
)
async def create_issue(
    workspace_id: uuid.UUID, payload: IssueCreate, session: SessionDep, ctx: CompanyCtx
):
    """El crea una PERSONA.

    Res del sistema n'obre cap: ni una dimensio en disputa, ni una cotitzacio
    pendent, ni una linia divergida. Tot aixo ja te el seu avis, i convertir-ho
    en dubtes ompliria la llista de preguntes que ningu no s'ha fet.

    Neix `open` i sense bloquejar res.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_study_open(session, workspace)

    issue = await service.create_issue(
        session, workspace=workspace, user=ctx.user, fields=payload.model_dump()
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="workspace_issue",
        entity_id=issue.id,
        summary=f"candidatura={workspace_id} tipus={issue.issue_type.value}",
    )
    await session.commit()
    await session.refresh(issue)
    return await _read(session, issue, [])


@router.patch("/{issue_id}", response_model=IssueRead, summary="Corregeix un dubte")
async def update_issue(
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    payload: IssueUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """El text, el context i el moviment entre `open` i `waiting`.

    Tancar-lo no hi cap, i `blocks_final_review` tampoc: cadascun te la seva
    accio perque cadascun es una decisio diferent.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_study_open(session, workspace)
    issue = await service.load_issue(session, workspace, issue_id)

    sent = payload.model_fields_set
    fields = {
        name: getattr(payload, name) for name in service.EDITABLE_FIELDS if name in sent
    }
    changed = await service.update_issue(
        session,
        workspace=workspace,
        issue=issue,
        user=ctx.user,
        fields=fields,
        status=payload.status if "status" in sent else None,
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_issue",
            entity_id=issue.id,
            summary=f"dubte={issue.id} camps={','.join(sorted(sent)) or '-'}",
        )
    await session.commit()
    await session.refresh(issue)
    return await _read(session, issue)


@router.post("/{issue_id}/resolve", response_model=IssueRead, summary="Resol el dubte")
async def resolve_issue(
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    payload: IssueResolve,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Tanca el dubte amb la resposta escrita.

    **No decideix res mes.** Resoldre un dubte sobre una magnitud no la decideix,
    ni tria una opcio, ni valida una solucio: encadenar-ho faria que respondre una
    pregunta mogues xifres que ningu no ha mirat.

    **I no toca `blocks_final_review`**: que algu decidis un dia que aixo impedia
    presentar-se continua sent cert.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_study_open(session, workspace)
    issue = await service.load_issue(session, workspace, issue_id)

    await service.resolve_issue(
        session,
        issue=issue,
        user=ctx.user,
        resolution=payload.resolution,
        resolution_source=payload.resolution_source,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="workspace_issue",
        entity_id=issue.id,
        summary=f"dubte={issue.id} resolt",
    )
    await session.commit()
    await session.refresh(issue)
    return await _read(session, issue)


@router.post("/{issue_id}/dismiss", response_model=IssueRead, summary="Descarta el dubte")
async def dismiss_issue(
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    payload: IssueDismiss,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Es tanca perque ja no ve al cas.

    **Descartar no es resoldre**: `resolved_by` i `resolved_at` es queden buits.
    Qui el descarta i quan viu a l'auditoria i a `updated_by`/`updated_at`.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_study_open(session, workspace)
    issue = await service.load_issue(session, workspace, issue_id)

    await service.dismiss_issue(session, issue=issue, user=ctx.user)
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="workspace_issue",
        entity_id=issue.id,
        summary=f"dubte={issue.id} descartat",
    )
    await session.commit()
    await session.refresh(issue)
    assert payload is not None
    return await _read(session, issue)


@router.post(
    "/{issue_id}/final-review-block",
    response_model=IssueRead,
    summary="Decideix si impedeix la revisio final",
)
async def set_final_review_block(
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    payload: IssueBlockWrite,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Nomes `DECISION_ROLES`.

    Dir que una pregunta impedeix presentar-se es una decisio d'oferta, no una
    propietat de la pregunta: qui prepara la candidatura pot obrir el dubte, i
    qui respon de l'oferta decideix si atura res.

    **No toca l'estat.** Marcar-lo no el fa passar a `waiting` ni a res mes.
    """
    service.assert_can_edit(ctx.membership.role)
    service.assert_can_block(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    # Decisio d'oferta, no d'estudi: a diferencia de la resta del dubte, demana GO.
    await assert_workspace_go(session, workspace)
    issue = await service.load_issue(session, workspace, issue_id)

    changed = await service.set_final_review_block(
        session, issue=issue, user=ctx.user, blocks=payload.blocks_final_review
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_issue",
            entity_id=issue.id,
            summary=(
                f"dubte={issue.id} "
                f"{'bloqueja' if payload.blocks_final_review else 'ja no bloqueja'} "
                "la revisio final"
            ),
        )
    await session.commit()
    await session.refresh(issue)
    return await _read(session, issue)


@router.post(
    "/{issue_id}/source-refs",
    response_model=IssueRead,
    summary="Enganxa una cita al dubte",
)
async def link_source_ref(
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    payload: IssueSourceRefWrite,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """La cita ha de ser de la MATEIXA licitacio.

    Els mateixos bytes poden ser a dos expedients, i que el SHA coincideixi no
    fa que la cita sigui d'aquest. Idempotent.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_study_open(session, workspace)
    issue = await service.load_issue(session, workspace, issue_id)

    linked = await service.link_source_ref(
        session,
        workspace=workspace,
        issue=issue,
        source_ref_id=payload.source_ref_id,
        user=ctx.user,
    )
    if linked:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_issue",
            entity_id=issue.id,
            summary=f"dubte={issue.id} cita={payload.source_ref_id} enganxada",
        )
    await session.commit()
    await session.refresh(issue)
    return await _read(session, issue)


@router.delete(
    "/{issue_id}/source-refs/{source_ref_id}",
    response_model=IssueRead,
    summary="Desenganxa una cita",
)
async def unlink_source_ref(
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    source_ref_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Treu l'enllac. **NO esborra la cita.**

    El `TenderSourceRef` es queda: pot sostenir-ne d'altres i era cert el dia
    que es va crear. Aixo NO es un esborrat de dubte —no n'hi ha cap.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_study_open(session, workspace)
    issue = await service.load_issue(session, workspace, issue_id)

    removed = await service.unlink_source_ref(
        session, issue=issue, source_ref_id=source_ref_id
    )
    if removed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_issue",
            entity_id=issue.id,
            summary=f"dubte={issue.id} cita={source_ref_id} desenganxada",
        )
    await session.commit()
    await session.refresh(issue)
    return await _read(session, issue)
