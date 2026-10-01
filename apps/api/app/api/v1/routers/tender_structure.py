"""Estructura del plec (fase 3A, pas 2): metadades del contracte, blocs, seus i
a que aplica cada requisit o criteri.

Cap politica nova. Escriure metadades, blocs i seus es editar la licitacio, i
per tant hi mana la politica de la licitacio oberta a ma (`manual_tender`:
propietari, administracio i responsable d'ofertes, nomes a la propia
licitacio manual). Anotar un requisit o un criteri es feina tecnica, i hi mana
la dels requisits (`manual_requirement`, que hi afegeix el perfil tecnic). La
lectura hereta la visibilitat de la licitacio.

Res d'aixo analitza res: no crea abast, necessitats, fets de contracte ni
resultats. Aixo es 0049/0050.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Response, status

from app.api.deps import CompanyCtx, SessionDep
from app.core.errors import NotFoundError
from app.models.audit import AuditAction
from app.models.tender_structure import TenderBlock, TenderSite
from app.models.tenders import Tender
from app.schemas.provenance import read_of
from app.schemas.tender_structure import (
    AwardCriterionStructureRead,
    RequirementStructureRead,
    RequirementStructureUpdate,
    StructureCitationLink,
    TenderBlockCreate,
    TenderBlockRead,
    TenderBlockUpdate,
    TenderSiteCreate,
    TenderSiteRead,
    TenderSiteUpdate,
    TenderStructureRead,
    TenderStructureUpdate,
)
from app.services import audit, manual_requirement, manual_tender
from app.services import tender_structure as structure
from app.services.source_refs import resolve_source_ref
from app.tenders.visibility import visible_to_company

router = APIRouter(prefix="/companies/{company_id}/tenders/{tender_id}", tags=["tender-structure"])


async def _visible_tender(session, company_id: uuid.UUID, tender_id: uuid.UUID) -> Tender:
    tender = await session.scalar(
        sa.select(Tender).where(Tender.id == tender_id, visible_to_company(company_id))
    )
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


async def _editable(session, ctx: CompanyCtx, tender_id: uuid.UUID) -> Tender:
    """Dades de la licitacio: la politica de la licitacio oberta a ma."""
    manual_tender.assert_can_edit(ctx.membership.role)
    return await manual_tender.load_editable_tender(session, ctx.company_id, tender_id)


async def _annotatable(session, ctx: CompanyCtx, tender_id: uuid.UUID) -> Tender:
    """Requisits i criteris: la politica dels requisits (tambe el perfil tecnic)."""
    manual_requirement.assert_can_edit(ctx.membership.role)
    return await manual_tender.load_editable_tender(session, ctx.company_id, tender_id)


async def _block_read(session, block: TenderBlock) -> TenderBlockRead:
    # `updated_at` el posa el servidor: despres d'escriure cal tornar-lo a llegir.
    await session.refresh(block)
    read = TenderBlockRead.model_validate(block)
    refs = (await structure.source_refs_of(session, TenderBlock, [block.id])).get(block.id, [])
    read.citations = [read_of(await resolve_source_ref(session, ref)) for ref in refs]
    return read


async def _site_read(session, site: TenderSite) -> TenderSiteRead:
    await session.refresh(site)
    read = TenderSiteRead.model_validate(site)
    refs = (await structure.source_refs_of(session, TenderSite, [site.id])).get(site.id, [])
    read.citations = [read_of(await resolve_source_ref(session, ref)) for ref in refs]
    return read


async def _structure_read(session, tender: Tender) -> TenderStructureRead:
    # Importacio local: el router d'abast (0049) reutilitza els ajudants d'aquest.
    from app.api.v1.routers.tender_scope import contract_facts_read, scope_items_read

    await session.refresh(tender, attribute_names=["blocks", "sites"])
    return TenderStructureRead(
        tender_id=tender.id,
        duration_months=tender.duration_months,
        extension_months=tender.extension_months,
        regulated_harmonised=tender.regulated_harmonised,
        planned_start_date=tender.planned_start_date,
        blocks=[await _block_read(session, block) for block in tender.blocks],
        sites=[await _site_read(session, site) for site in tender.sites],
        scope_items=await scope_items_read(session, tender.id),
        contract_facts=await contract_facts_read(session, tender.id),
    )


async def _audit(
    session, ctx: CompanyCtx, action: AuditAction, entity_type: str, entity_id, summary
):
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# Metadades del contracte                                                       #
# --------------------------------------------------------------------------- #


@router.get("/structure", response_model=TenderStructureRead, summary="Estructura del plec")
async def read_structure(tender_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    tender = await _visible_tender(session, ctx.company_id, tender_id)
    return await _structure_read(session, tender)


@router.patch(
    "/structure",
    response_model=TenderStructureRead,
    summary="Corregeix les dades estructurals del contracte",
)
async def update_structure(
    tender_id: uuid.UUID, payload: TenderStructureUpdate, session: SessionDep, ctx: CompanyCtx
):
    """S'aplica nomes el que arriba; `null` deixa el camp com a desconegut.

    No crea cap revisio de la licitacio: aquestes dades no son part del que
    publica l'organ al feed, i la fotografia de `tender_lifecycle` no les mira.
    """
    tender = await _editable(session, ctx, tender_id)
    fields = {name: getattr(payload, name) for name in payload.model_fields_set}
    changed = structure.update_structure(tender, fields)
    if changed:
        await _audit(
            session,
            ctx,
            AuditAction.UPDATE,
            "tender",
            tender.id,
            f"licitacio={tender.id} estructura={','.join(changed)}",
        )
    await session.commit()
    return await _structure_read(session, tender)


# --------------------------------------------------------------------------- #
# Blocs                                                                         #
# --------------------------------------------------------------------------- #


@router.post(
    "/blocks",
    response_model=TenderBlockRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un bloc del contracte",
)
async def create_block(
    tender_id: uuid.UUID, payload: TenderBlockCreate, session: SessionDep, ctx: CompanyCtx
):
    tender = await _editable(session, ctx, tender_id)
    block = await structure.create_block(session, tender, payload.model_dump())
    await _audit(
        session,
        ctx,
        AuditAction.CREATE,
        "tender_block",
        block.id,
        f"licitacio={tender.id} bloc={block.code}",
    )
    await session.commit()
    return await _block_read(session, block)


@router.patch("/blocks/{block_id}", response_model=TenderBlockRead, summary="Corregeix un bloc")
async def update_block(
    tender_id: uuid.UUID,
    block_id: uuid.UUID,
    payload: TenderBlockUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    block = await structure.load_block(session, tender, block_id)
    fields = {name: getattr(payload, name) for name in payload.model_fields_set}
    if await structure.update_block(session, tender, block, fields):
        await _audit(
            session,
            ctx,
            AuditAction.UPDATE,
            "tender_block",
            block.id,
            f"licitacio={tender.id} bloc={block.code} camps={','.join(sorted(fields))}",
        )
    await session.commit()
    return await _block_read(session, block)


@router.delete(
    "/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Esborra un bloc"
)
async def delete_block(
    tender_id: uuid.UUID, block_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    tender = await _editable(session, ctx, tender_id)
    block = await structure.load_block(session, tender, block_id)
    code = block.code
    await structure.delete_block(session, block)
    await _audit(
        session,
        ctx,
        AuditAction.DELETE,
        "tender_block",
        block_id,
        f"licitacio={tender.id} bloc={code}",
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/blocks/{block_id}/citations",
    response_model=TenderBlockRead,
    summary="Enllaça una cita a un bloc",
)
async def link_block_citation(
    tender_id: uuid.UUID,
    block_id: uuid.UUID,
    payload: StructureCitationLink,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    block = await structure.load_block(session, tender, block_id)
    await structure.link_source_ref(
        session, entity=block, source_ref_id=payload.source_ref_id, user=ctx.user
    )
    await _audit(
        session,
        ctx,
        AuditAction.UPDATE,
        "tender_block",
        block.id,
        f"bloc={block.id} cita+={payload.source_ref_id}",
    )
    await session.commit()
    return await _block_read(session, block)


@router.delete(
    "/blocks/{block_id}/citations/{source_ref_id}",
    response_model=TenderBlockRead,
    summary="Treu una cita d'un bloc",
)
async def unlink_block_citation(
    tender_id: uuid.UUID,
    block_id: uuid.UUID,
    source_ref_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    block = await structure.load_block(session, tender, block_id)
    if await structure.unlink_source_ref(session, entity=block, source_ref_id=source_ref_id):
        await _audit(
            session,
            ctx,
            AuditAction.UPDATE,
            "tender_block",
            block.id,
            f"bloc={block.id} cita-={source_ref_id}",
        )
    await session.commit()
    return await _block_read(session, block)


# --------------------------------------------------------------------------- #
# Seus                                                                          #
# --------------------------------------------------------------------------- #


@router.post(
    "/sites",
    response_model=TenderSiteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix una seu del plec",
)
async def create_site(
    tender_id: uuid.UUID, payload: TenderSiteCreate, session: SessionDep, ctx: CompanyCtx
):
    tender = await _editable(session, ctx, tender_id)
    site = await structure.create_site(session, tender, payload.model_dump())
    await _audit(
        session,
        ctx,
        AuditAction.CREATE,
        "tender_site",
        site.id,
        f"licitacio={tender.id} seu={site.code}",
    )
    await session.commit()
    return await _site_read(session, site)


@router.patch("/sites/{site_id}", response_model=TenderSiteRead, summary="Corregeix una seu")
async def update_site(
    tender_id: uuid.UUID,
    site_id: uuid.UUID,
    payload: TenderSiteUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    site = await structure.load_site(session, tender, site_id)
    fields = {name: getattr(payload, name) for name in payload.model_fields_set}
    if await structure.update_site(session, tender, site, fields):
        await _audit(
            session,
            ctx,
            AuditAction.UPDATE,
            "tender_site",
            site.id,
            f"licitacio={tender.id} seu={site.code} camps={','.join(sorted(fields))}",
        )
    await session.commit()
    return await _site_read(session, site)


@router.delete(
    "/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Esborra una seu"
)
async def delete_site(
    tender_id: uuid.UUID, site_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    tender = await _editable(session, ctx, tender_id)
    site = await structure.load_site(session, tender, site_id)
    code = site.code
    await structure.delete_site(session, site)
    await _audit(
        session,
        ctx,
        AuditAction.DELETE,
        "tender_site",
        site_id,
        f"licitacio={tender.id} seu={code}",
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sites/{site_id}/citations",
    response_model=TenderSiteRead,
    summary="Enllaça una cita a una seu",
)
async def link_site_citation(
    tender_id: uuid.UUID,
    site_id: uuid.UUID,
    payload: StructureCitationLink,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    site = await structure.load_site(session, tender, site_id)
    await structure.link_source_ref(
        session, entity=site, source_ref_id=payload.source_ref_id, user=ctx.user
    )
    await _audit(
        session,
        ctx,
        AuditAction.UPDATE,
        "tender_site",
        site.id,
        f"seu={site.id} cita+={payload.source_ref_id}",
    )
    await session.commit()
    return await _site_read(session, site)


@router.delete(
    "/sites/{site_id}/citations/{source_ref_id}",
    response_model=TenderSiteRead,
    summary="Treu una cita d'una seu",
)
async def unlink_site_citation(
    tender_id: uuid.UUID,
    site_id: uuid.UUID,
    source_ref_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    site = await structure.load_site(session, tender, site_id)
    if await structure.unlink_source_ref(session, entity=site, source_ref_id=source_ref_id):
        await _audit(
            session,
            ctx,
            AuditAction.UPDATE,
            "tender_site",
            site.id,
            f"seu={site.id} cita-={source_ref_id}",
        )
    await session.commit()
    return await _site_read(session, site)


# --------------------------------------------------------------------------- #
# Requisits i criteris: temporalitat i blocs                                    #
# --------------------------------------------------------------------------- #


@router.patch(
    "/requirements/{requirement_id}/structure",
    response_model=RequirementStructureRead,
    summary="Temporalitat i blocs d'un requisit",
)
async def update_requirement_structure(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: RequirementStructureUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """`block_ids: []` vol dir «a tot el contracte». No se'n deriva cap
    resultat de compliment: nomes es desa que diu el plec."""
    tender = await _annotatable(session, ctx, tender_id)
    requirement = await structure.load_requirement(session, tender, requirement_id)
    touched: list[str] = []
    if payload.timing is not None and structure.set_timing(requirement, payload.timing):
        touched.append(f"timing={payload.timing}")
    if payload.block_ids is not None and await structure.set_requirement_blocks(
        session, tender, requirement, payload.block_ids
    ):
        touched.append(f"blocs={len(payload.block_ids)}")
    if touched:
        await _audit(
            session,
            ctx,
            AuditAction.UPDATE,
            "requirement",
            requirement.id,
            f"requisit={requirement.id} {' '.join(touched)}",
        )
    await session.commit()
    await session.refresh(requirement, attribute_names=["block_links"])
    return RequirementStructureRead(
        id=requirement.id,
        tender_id=requirement.tender_id,
        timing=requirement.timing,
        block_ids=requirement.block_ids,
    )


@router.patch(
    "/award-criteria/{criterion_id}/structure",
    response_model=AwardCriterionStructureRead,
    summary="Temporalitat i blocs d'un criteri d'adjudicacio",
)
async def update_criterion_structure(
    tender_id: uuid.UUID,
    criterion_id: uuid.UUID,
    payload: RequirementStructureUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Els punts del criteri no es reparteixen entre els blocs: `points` es el
    mateix abans i despres, sigui quants blocs enllaci."""
    tender = await _annotatable(session, ctx, tender_id)
    criterion = await structure.load_criterion(session, tender, criterion_id)
    touched: list[str] = []
    if payload.timing is not None and structure.set_timing(criterion, payload.timing):
        touched.append(f"timing={payload.timing}")
    if payload.block_ids is not None and await structure.set_criterion_blocks(
        session, tender, criterion, payload.block_ids
    ):
        touched.append(f"blocs={len(payload.block_ids)}")
    if touched:
        await _audit(
            session,
            ctx,
            AuditAction.UPDATE,
            "award_criterion",
            criterion.id,
            f"criteri={criterion.id} {' '.join(touched)}",
        )
    await session.commit()
    await session.refresh(criterion, attribute_names=["block_links"])
    return AwardCriterionStructureRead(
        id=criterion.id,
        tender_id=criterion.tender_id,
        timing=criterion.timing,
        block_ids=criterion.block_ids,
        points=criterion.points,
    )
