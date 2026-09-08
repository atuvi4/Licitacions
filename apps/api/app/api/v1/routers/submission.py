"""Paquet de presentacio: HTTP.

Penja del workspace, que penja de `/companies/{company_id}`: la pertinenca es
comprova a cada crida i els paquets es filtren sempre per empresa.

Aqui no hi ha cap `PATCH` de paquet ni cap endpoint per afegir o treure
documents d'un que ja estigui congelat. No es un oblit: **un paquet congelat no
s'edita**, se'n congela una revisio nova.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Response

from app.api.deps import CompanyCtx, SessionDep
from app.models.documents import Document
from app.models.enums import AuditAction, SubmissionPackageStatus
from app.models.identity import User
from app.models.submission import SubmissionPackage
from app.schemas.submission import (
    ApprovalRead,
    ApprovalRequest,
    FreezeResult,
    PackageCoverageRead,
    PackageDetail,
    PackageItemRead,
    PackageSummary,
)
from app.services import audit
from app.services import bid_workspace as workspace_service
from app.services import submission_package as service
from app.services import workspace_dossier as dossier_service
from app.workspace.dossier import document_validity
from app.workspace.transitions import is_submitted

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["presentacio"])


async def _names(session: SessionDep, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    ids = {uid for uid in user_ids if uid}
    if not ids:
        return {}
    rows = (
        await session.execute(
            sa.select(User.id, User.full_name, User.email).where(User.id.in_(ids))
        )
    ).all()
    return {row[0]: row[1] or row[2] for row in rows}


def _approval_read(approval, names: dict[uuid.UUID, str | None]) -> ApprovalRead:
    read = ApprovalRead.model_validate(approval)
    read.approved_by_name = names.get(approval.approved_by)
    return read


def _short(digest: str | None) -> str | None:
    return digest[:12] if digest else None


async def _summary(session: SessionDep, package: SubmissionPackage) -> PackageSummary:
    approval = service.approval_of(package)
    names = await _names(session, {approval.approved_by} if approval else set())
    return PackageSummary(
        id=package.id,
        workspace_id=package.workspace_id,
        revision_number=package.revision_number,
        status=package.status,
        manifest_hash=package.manifest_hash,
        manifest_hash_short=_short(package.manifest_hash),
        document_count=package.document_count,
        mandatory_total=package.mandatory_total,
        mandatory_covered=package.mandatory_covered,
        deadline_at=package.deadline_at,
        frozen_at=package.frozen_at,
        created_at=package.created_at,
        is_approved=approval is not None,
        approved_at=approval.approved_at if approval else None,
        approved_by_name=names.get(approval.approved_by) if approval else None,
    )


async def _detail(
    session: SessionDep, ctx: CompanyCtx, workspace, package: SubmissionPackage
) -> PackageDetail:
    state = await workspace_service.load_state(session, workspace)
    readiness = await dossier_service.reconcile_readiness(session, workspace)
    stale = await service.evaluate_staleness(session, workspace, package)

    documents = {
        d.id: d
        for d in (
            await session.scalars(
                sa.select(Document).where(
                    Document.id.in_([i.document_id for i in package.items if i.document_id]),
                    Document.company_id == ctx.company_id,
                )
            )
        ).all()
    }

    items: list[PackageItemRead] = []
    for item in package.items:
        current = documents.get(item.document_id) if item.document_id else None
        read = PackageItemRead.model_validate(item)
        read.document_expires_at = current.expires_at if current else None
        read.validity = (
            document_validity(
                current.expires_at, deadline=state.deadline_at, has_document=True
            )
            if current
            else None
        )
        # El hash congelat contra el d'ara: aixo es el que detecta que algu ha
        # substituit el fitxer sense canviar l'identificador.
        read.changed_since_freeze = current is None or current.sha256 != item.document_sha256
        items.append(read)

    approvals = list(package.approvals)
    names = await _names(session, {a.approved_by for a in approvals})
    approval = service.approval_of(package)

    return PackageDetail(
        id=package.id,
        workspace_id=package.workspace_id,
        company_id=package.company_id,
        revision_number=package.revision_number,
        status=package.status,
        manifest_hash=package.manifest_hash,
        manifest_hash_short=_short(package.manifest_hash),
        tender_title=state.tender.title,
        expediente_number=state.tender.expediente_number,
        lot_number=state.lot.lot_number if state.lot else None,
        deadline_at=package.deadline_at,
        days_remaining=state.days_remaining,
        compliance_run_id=package.compliance_run_id,
        requirement_run_id=package.requirement_run_id,
        prefit_score=package.prefit_score,
        coverage=PackageCoverageRead(
            mandatory_total=package.mandatory_total,
            mandatory_covered=package.mandatory_covered,
            document_count=package.document_count,
        ),
        items=items,
        approvals=[_approval_read(a, names) for a in approvals],
        approval=_approval_read(approval, names) if approval else None,
        readiness=readiness.state,
        readiness_blockers=list(readiness.blockers),
        stale_reasons=list(stale.reasons),
        is_stale=stale.is_stale,
        can_approve=(
            package.status == SubmissionPackageStatus.FROZEN
            and readiness.is_ready
            and not stale.is_stale
        ),
        can_sign=ctx.membership.role in service.FINAL_APPROVAL_ROLES,
        workspace_status=workspace.status,
        # Explicit a la resposta: aprovar el paquet no es haver presentat res.
        is_submitted=is_submitted(workspace.status),
        frozen_at=package.frozen_at,
        created_at=package.created_at,
    )


@router.get("/packages", response_model=list[PackageSummary], summary="Revisions del paquet")
async def list_packages(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Totes les revisions, de la mes recent a la mes antiga. Cap se n'esborra."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    packages = await service.list_packages(session, workspace)
    return [await _summary(session, package) for package in packages]


@router.post("/packages", response_model=FreezeResult, summary="Congela una revisio")
async def freeze(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Recalcula la readiness i congela el que hi ha ara. No s'edita despres."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    outcome = await service.freeze_package(
        session, workspace, user=ctx.user, role=ctx.membership.role
    )
    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        entity_type="submission_package",
        entity_id=outcome.package.id,
        summary=f"Paquet revisio {outcome.package.revision_number} congelat",
    )
    await session.commit()
    return FreezeResult(
        created=outcome.created,
        package=await _detail(session, ctx, workspace, outcome.package),
    )


@router.get(
    "/packages/{package_id}", response_model=PackageDetail, summary="Revisio final"
)
async def get_package(
    workspace_id: uuid.UUID, package_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    package = await service.get_package(session, ctx.company_id, package_id)
    return await _detail(session, ctx, workspace, package)


@router.get(
    "/packages/{package_id}/manifest",
    summary="Manifest del paquet",
    response_class=Response,
)
async def manifest(
    workspace_id: uuid.UUID, package_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    """El manifest tal com es va congelar, byte a byte.

    Es serveix el JSON GUARDAT i no un de regenerat: el hash que hi ha a
    l'aprovacio es el d'aquests bytes, i tornar-lo a construir podria donar-ne
    un altre si alguna cosa hagues canviat pel cami.
    """
    await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    package = await service.get_package(session, ctx.company_id, package_id)
    payload = json.dumps(package.manifest_json or {}, ensure_ascii=False, indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="manifest-r{package.revision_number}.json"'
            ),
            "X-Manifest-Sha256": package.manifest_hash or "",
        },
    )


@router.post(
    "/packages/{package_id}/approvals",
    response_model=PackageDetail,
    summary="Aprovacio humana final",
)
async def approve(
    workspace_id: uuid.UUID,
    package_id: uuid.UUID,
    payload: ApprovalRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """L'unic cami cap a `approved_for_submission`.

    Recalcula readiness, obsolescencia i integritat abans de signar. Aprovar
    **no presenta res**: `submitted` es una altra iteracio.
    """
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    package = await service.get_package(session, ctx.company_id, package_id)
    approval = await service.approve_package(
        session,
        workspace,
        package,
        user=ctx.user,
        role=ctx.membership.role,
        decision=payload.decision,
        comment=payload.comment,
    )
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        entity_type="submission_package_approval",
        entity_id=approval.id,
        summary=(
            f"{approval.decision.value} revisio {package.revision_number} "
            f"manifest {(package.manifest_hash or '')[:12]}"
        ),
    )
    await session.commit()
    return await _detail(session, ctx, workspace, package)


@router.get(
    "/packages/{package_id}/approvals",
    response_model=list[ApprovalRead],
    summary="Historial d'aprovacions",
)
async def list_approvals(
    workspace_id: uuid.UUID, package_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    """Append-only: no hi ha cap endpoint que editi ni esborri una aprovacio."""
    await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    package = await service.get_package(session, ctx.company_id, package_id)
    names = await _names(session, {a.approved_by for a in package.approvals})
    return [_approval_read(approval, names) for approval in package.approvals]
