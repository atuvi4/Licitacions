"""Abast i semantica del plec (fase 3A, pas 3): elements d'abast, fets
contractuals, i territoris, proves, necessitats i criteris d'un requisit.

Mateixa politica que l'estructura (0048): les dades de la licitacio (abast,
fets) les escriu qui pot editar la licitacio oberta a ma; les anotacions d'un
requisit (territoris, proves, necessitats, criteris), qui pot escriure
requisits. Una licitacio publica es llegeix i no s'edita per aqui: la via
interna es `app.services.tender_scope`.

Res d'aixo compara res amb cap empresa ni crea cap resultat: aixo es 0050.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.routers.tender_structure import _annotatable, _audit, _editable, _visible_tender
from app.models.audit import AuditAction
from app.models.requirements import Requirement
from app.models.tender_scope import RequirementCapabilityNeed, TenderContractFact, TenderScopeItem
from app.schemas.provenance import read_of
from app.schemas.tender_scope import (
    CapabilityNeedCreate,
    CapabilityNeedRead,
    CapabilityNeedUpdate,
    ContractFactCreate,
    ContractFactRead,
    ContractFactUpdate,
    RequirementSemanticsRead,
    RequirementSemanticsUpdate,
    ScopeItemCreate,
    ScopeItemRead,
    ScopeItemUpdate,
)
from app.schemas.tender_structure import StructureCitationLink
from app.services import tender_scope as scope
from app.services import tender_structure as structure
from app.services.source_refs import resolve_source_ref

router = APIRouter(prefix="/companies/{company_id}/tenders/{tender_id}", tags=["tender-scope"])


# --------------------------------------------------------------------------- #
# Lectures                                                                      #
# --------------------------------------------------------------------------- #


async def _citations(session, model, entity_id: uuid.UUID) -> list:
    refs = (await scope.source_refs_of(session, model, [entity_id])).get(entity_id, [])
    return [read_of(await resolve_source_ref(session, ref)) for ref in refs]


async def scope_item_read(session, item: TenderScopeItem) -> ScopeItemRead:
    await session.refresh(item)
    read = ScopeItemRead.model_validate(item)
    read.citations = await _citations(session, TenderScopeItem, item.id)
    return read


async def fact_read(session, fact: TenderContractFact) -> ContractFactRead:
    await session.refresh(fact)
    read = ContractFactRead.model_validate(fact)
    read.block_ids = (await scope.fact_block_ids(session, [fact.id])).get(fact.id, [])
    read.citations = await _citations(session, TenderContractFact, fact.id)
    return read


async def need_read(session, need: RequirementCapabilityNeed) -> CapabilityNeedRead:
    await session.refresh(need)
    read = CapabilityNeedRead.model_validate(need)
    read.citations = await _citations(session, RequirementCapabilityNeed, need.id)
    return read


async def scope_items_read(session, tender_id: uuid.UUID) -> list[ScopeItemRead]:
    items = await scope.scope_items_of(session, tender_id)
    return [await scope_item_read(session, item) for item in items]


async def contract_facts_read(session, tender_id: uuid.UUID) -> list[ContractFactRead]:
    return [await fact_read(session, fact) for fact in await scope.facts_of(session, tender_id)]


async def _semantics_read(session, requirement: Requirement) -> RequirementSemanticsRead:
    await session.refresh(
        requirement, attribute_names=["territories", "capability_needs", "award_links"]
    )
    return RequirementSemanticsRead(
        id=requirement.id,
        tender_id=requirement.tender_id,
        territory_match_mode=requirement.territory_match_mode,
        territory_completeness=requirement.territory_completeness,
        territories=requirement.territories,
        proof_json=requirement.proof_json,
        proof_schema=requirement.proof_schema,
        award_criterion_ids=requirement.award_criterion_ids,
        capability_needs=[await need_read(session, need) for need in requirement.capability_needs],
    )


# --------------------------------------------------------------------------- #
# Elements d'abast                                                              #
# --------------------------------------------------------------------------- #


@router.post(
    "/scope-items",
    response_model=ScopeItemRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un element d'abast (que es compra)",
)
async def create_scope_item(
    tender_id: uuid.UUID, payload: ScopeItemCreate, session: SessionDep, ctx: CompanyCtx
):
    tender = await _editable(session, ctx, tender_id)
    item = await scope.create_scope_item(session, tender, payload.model_dump())
    await _audit(
        session, ctx, AuditAction.CREATE, "tender_scope_item", item.id,
        f"licitacio={tender.id} abast={item.service_line_code}",
    )  # fmt: skip
    await session.commit()
    return await scope_item_read(session, item)


@router.patch(
    "/scope-items/{item_id}", response_model=ScopeItemRead, summary="Corregeix un element d'abast"
)
async def update_scope_item(
    tender_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ScopeItemUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    item = await scope.load_scope_item(session, tender, item_id)
    fields = {name: getattr(payload, name) for name in payload.model_fields_set}
    if await scope.update_scope_item(session, tender, item, fields):
        await _audit(
            session, ctx, AuditAction.UPDATE, "tender_scope_item", item.id,
            f"licitacio={tender.id} abast={item.id} camps={','.join(sorted(fields))}",
        )  # fmt: skip
    await session.commit()
    return await scope_item_read(session, item)


@router.delete(
    "/scope-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Esborra un element d'abast",
)
async def delete_scope_item(
    tender_id: uuid.UUID, item_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    tender = await _editable(session, ctx, tender_id)
    item = await scope.load_scope_item(session, tender, item_id)
    await scope.delete_scope_item(session, item)
    await _audit(
        session, ctx, AuditAction.DELETE, "tender_scope_item", item_id, f"licitacio={tender.id}"
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/scope-items/{item_id}/citations",
    response_model=ScopeItemRead,
    summary="Enllaça una cita a un element d'abast",
)
async def link_scope_item_citation(
    tender_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: StructureCitationLink,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    item = await scope.load_scope_item(session, tender, item_id)
    await scope.link_source_ref(
        session, entity=item, source_ref_id=payload.source_ref_id, user=ctx.user
    )
    await _audit(
        session, ctx, AuditAction.UPDATE, "tender_scope_item", item.id,
        f"abast={item.id} cita+={payload.source_ref_id}",
    )  # fmt: skip
    await session.commit()
    return await scope_item_read(session, item)


@router.delete(
    "/scope-items/{item_id}/citations/{source_ref_id}",
    response_model=ScopeItemRead,
    summary="Treu una cita d'un element d'abast",
)
async def unlink_scope_item_citation(
    tender_id: uuid.UUID,
    item_id: uuid.UUID,
    source_ref_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    item = await scope.load_scope_item(session, tender, item_id)
    if await scope.unlink_source_ref(session, entity=item, source_ref_id=source_ref_id):
        await _audit(
            session, ctx, AuditAction.UPDATE, "tender_scope_item", item.id,
            f"abast={item.id} cita-={source_ref_id}",
        )  # fmt: skip
    await session.commit()
    return await scope_item_read(session, item)


# --------------------------------------------------------------------------- #
# Fets contractuals                                                             #
# --------------------------------------------------------------------------- #


@router.post(
    "/contract-facts",
    response_model=ContractFactRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un fet contractual",
)
async def create_contract_fact(
    tender_id: uuid.UUID, payload: ContractFactCreate, session: SessionDep, ctx: CompanyCtx
):
    tender = await _editable(session, ctx, tender_id)
    fact = await scope.create_fact(
        session,
        tender,
        fact_code=payload.fact_code,
        value_json=payload.value_json,
        value_schema=payload.value_schema,
        block_ids=payload.block_ids,
    )
    await _audit(
        session, ctx, AuditAction.CREATE, "tender_contract_fact", fact.id,
        f"licitacio={tender.id} fet={fact.fact_code}",
    )  # fmt: skip
    await session.commit()
    return await fact_read(session, fact)


@router.patch(
    "/contract-facts/{fact_id}",
    response_model=ContractFactRead,
    summary="Corregeix un fet contractual o a quins blocs aplica",
)
async def update_contract_fact(
    tender_id: uuid.UUID,
    fact_id: uuid.UUID,
    payload: ContractFactUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    fact = await scope.load_fact(session, tender, fact_id)
    if await scope.update_fact(
        session,
        tender,
        fact,
        fact_code=payload.fact_code,
        value_json=payload.value_json,
        value_schema=payload.value_schema,
        block_ids=payload.block_ids,
    ):
        await _audit(
            session, ctx, AuditAction.UPDATE, "tender_contract_fact", fact.id,
            f"licitacio={tender.id} fet={fact.fact_code} "
            f"camps={','.join(sorted(payload.model_fields_set))}",
        )  # fmt: skip
    await session.commit()
    return await fact_read(session, fact)


@router.delete(
    "/contract-facts/{fact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Esborra un fet contractual",
)
async def delete_contract_fact(
    tender_id: uuid.UUID, fact_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    tender = await _editable(session, ctx, tender_id)
    fact = await scope.load_fact(session, tender, fact_id)
    code = fact.fact_code
    await scope.delete_fact(session, fact)
    await _audit(
        session, ctx, AuditAction.DELETE, "tender_contract_fact", fact_id,
        f"licitacio={tender.id} fet={code}",
    )  # fmt: skip
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/contract-facts/{fact_id}/citations",
    response_model=ContractFactRead,
    summary="Enllaça una cita a un fet contractual",
)
async def link_fact_citation(
    tender_id: uuid.UUID,
    fact_id: uuid.UUID,
    payload: StructureCitationLink,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    fact = await scope.load_fact(session, tender, fact_id)
    await scope.link_source_ref(
        session, entity=fact, source_ref_id=payload.source_ref_id, user=ctx.user
    )
    await _audit(
        session, ctx, AuditAction.UPDATE, "tender_contract_fact", fact.id,
        f"fet={fact.id} cita+={payload.source_ref_id}",
    )  # fmt: skip
    await session.commit()
    return await fact_read(session, fact)


@router.delete(
    "/contract-facts/{fact_id}/citations/{source_ref_id}",
    response_model=ContractFactRead,
    summary="Treu una cita d'un fet contractual",
)
async def unlink_fact_citation(
    tender_id: uuid.UUID,
    fact_id: uuid.UUID,
    source_ref_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _editable(session, ctx, tender_id)
    fact = await scope.load_fact(session, tender, fact_id)
    if await scope.unlink_source_ref(session, entity=fact, source_ref_id=source_ref_id):
        await _audit(
            session, ctx, AuditAction.UPDATE, "tender_contract_fact", fact.id,
            f"fet={fact.id} cita-={source_ref_id}",
        )  # fmt: skip
    await session.commit()
    return await fact_read(session, fact)


# --------------------------------------------------------------------------- #
# Semantica d'un requisit: territoris, proves, criteris                         #
# --------------------------------------------------------------------------- #


@router.get(
    "/requirements/{requirement_id}/semantics",
    response_model=RequirementSemanticsRead,
    summary="Territoris, proves, necessitats i criteris d'un requisit",
)
async def read_requirement_semantics(
    tender_id: uuid.UUID, requirement_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
):
    tender = await _visible_tender(session, ctx.company_id, tender_id)
    requirement = await structure.load_requirement(session, tender, requirement_id)
    return await _semantics_read(session, requirement)


@router.patch(
    "/requirements/{requirement_id}/semantics",
    response_model=RequirementSemanticsRead,
    summary="Territoris, proves i criteris d'un requisit",
)
async def update_requirement_semantics(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: RequirementSemanticsUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Tot s'emmagatzema i res no es compara: ni territori amb cobertura, ni
    prova amb documents, ni criteri amb punts assegurats."""
    tender = await _annotatable(session, ctx, tender_id)
    requirement = await structure.load_requirement(session, tender, requirement_id)
    touched: list[str] = []
    if payload.territory_match_mode is not None and scope.set_territory_match_mode(
        requirement, payload.territory_match_mode
    ):
        touched.append(f"mode={payload.territory_match_mode}")
    touched += await scope.set_territory_semantics(
        session,
        tender,
        requirement,
        completeness=payload.territory_completeness,
        territories=(
            None if payload.territories is None else [t.model_dump() for t in payload.territories]
        ),
    )
    if "proof" in payload.model_fields_set and scope.set_proof(
        requirement, None if payload.proof is None else [p.model_dump() for p in payload.proof]
    ):
        touched.append("proves")
    if payload.award_criterion_ids is not None and await scope.set_award_links(
        session, tender, requirement, payload.award_criterion_ids
    ):
        touched.append(f"criteris={len(payload.award_criterion_ids)}")
    if touched:
        await _audit(
            session, ctx, AuditAction.UPDATE, "requirement", requirement.id,
            f"requisit={requirement.id} {' '.join(touched)}",
        )  # fmt: skip
    await session.commit()
    return await _semantics_read(session, requirement)


# --------------------------------------------------------------------------- #
# Necessitats tecniques                                                         #
# --------------------------------------------------------------------------- #


@router.post(
    "/requirements/{requirement_id}/capability-needs",
    response_model=CapabilityNeedRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix una necessitat tecnica a un requisit",
)
async def create_capability_need(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: CapabilityNeedCreate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _annotatable(session, ctx, tender_id)
    requirement = await structure.load_requirement(session, tender, requirement_id)
    need = await scope.create_need(session, tender, requirement, payload.model_dump())
    await _audit(
        session, ctx, AuditAction.CREATE, "requirement_capability_need", need.id,
        f"requisit={requirement.id} necessitat={need.capability_code or need.unmapped_capability}",
    )  # fmt: skip
    await session.commit()
    return await need_read(session, need)


@router.patch(
    "/capability-needs/{need_id}",
    response_model=CapabilityNeedRead,
    summary="Corregeix una necessitat tecnica",
)
async def update_capability_need(
    tender_id: uuid.UUID,
    need_id: uuid.UUID,
    payload: CapabilityNeedUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _annotatable(session, ctx, tender_id)
    need = await scope.load_need(session, tender, need_id)
    fields = {name: getattr(payload, name) for name in payload.model_fields_set}
    if await scope.update_need(session, tender, need, fields):
        await _audit(
            session, ctx, AuditAction.UPDATE, "requirement_capability_need", need.id,
            f"necessitat={need.id} camps={','.join(sorted(fields))}",
        )  # fmt: skip
    await session.commit()
    return await need_read(session, need)


@router.delete(
    "/capability-needs/{need_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Esborra una necessitat tecnica",
)
async def delete_capability_need(
    tender_id: uuid.UUID, need_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    tender = await _annotatable(session, ctx, tender_id)
    need = await scope.load_need(session, tender, need_id)
    await scope.delete_need(session, need)
    await _audit(
        session, ctx, AuditAction.DELETE, "requirement_capability_need", need_id,
        f"licitacio={tender.id}",
    )  # fmt: skip
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/capability-needs/{need_id}/citations",
    response_model=CapabilityNeedRead,
    summary="Enllaça una cita a una necessitat tecnica",
)
async def link_need_citation(
    tender_id: uuid.UUID,
    need_id: uuid.UUID,
    payload: StructureCitationLink,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _annotatable(session, ctx, tender_id)
    need = await scope.load_need(session, tender, need_id)
    await scope.link_source_ref(
        session, entity=need, source_ref_id=payload.source_ref_id, user=ctx.user
    )
    await _audit(
        session, ctx, AuditAction.UPDATE, "requirement_capability_need", need.id,
        f"necessitat={need.id} cita+={payload.source_ref_id}",
    )  # fmt: skip
    await session.commit()
    return await need_read(session, need)


@router.delete(
    "/capability-needs/{need_id}/citations/{source_ref_id}",
    response_model=CapabilityNeedRead,
    summary="Treu una cita d'una necessitat tecnica",
)
async def unlink_need_citation(
    tender_id: uuid.UUID,
    need_id: uuid.UUID,
    source_ref_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    tender = await _annotatable(session, ctx, tender_id)
    need = await scope.load_need(session, tender, need_id)
    if await scope.unlink_source_ref(session, entity=need, source_ref_id=source_ref_id):
        await _audit(
            session, ctx, AuditAction.UPDATE, "requirement_capability_need", need.id,
            f"necessitat={need.id} cita-={source_ref_id}",
        )  # fmt: skip
    await session.commit()
    return await need_read(session, need)
