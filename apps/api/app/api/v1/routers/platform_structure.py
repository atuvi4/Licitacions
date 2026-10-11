"""Estructura i abast d'una licitacio PUBLICA: via de l'operador (3B-2 §4.1-§4.4).

Nomes l'Administracio Tuvi (`PlatformAdmin`: superusuari sense empresa), i nomes
sobre licitacions publiques (d'una font, sense propietari). Com als routers
germans (`platform_documents`, `platform_classification`): una licitacio oberta a
ma (o el gold d'avaluacio, de font `MANUAL`) no es de l'operador i rep 422
`tender_not_public`; una que no existeix, 404. Una empresa no hi entra
(403) i els seus routers de tenant continuen refusant tota escriptura sobre una
licitacio publica (`tender_not_manual`).

Tot passa per la via interna (`tender_structure`, `tender_scope`) amb
`origin=operator` i `authority=platform_operator`:

* blocs, seus, elements d'abast, necessitats i fets: alta, correccio i baixa.
  Tota alta o correccio porta **almenys una cita** (`source_ref_ids`), de la
  mateixa licitacio i citable. Un `null` en un camp amb «no se sap» no s'accepta
  aqui: tornar a desconegut es `POST .../reset-unknown`, explicit;
* territoris d'un requisit: no tenen taula de cites, aixi que la cita queda a una
  proposta de l'operador que es promou al moment (`proposal_id` a la fila);
* cites: enllacar i treure (la cita no s'esborra mai). Una correccio valida porta
  les seves cites encara que no canvii cap valor (evidencia nova d'un valor
  correcte), i la darrera cita d'una entitat no es pot treure (`last_citation`):
  cap valor conegut sense cita;
* propostes immutables: crear, llistar, promoure i rebutjar.

Cada accio deixa auditoria amb `company_id=None`. Cap d'aquestes escriptures crea
cap revisio de la licitacio (com al router del tenant): no son el que publica
l'organ al feed.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Query, Response, status

from app.api.deps import PlatformAdmin, SessionDep
from app.core.errors import NotFoundError
from app.models.enums import (
    AuditAction,
    StructureEntityKind,
    StructureOrigin,
    StructureProposalProducer,
    StructureProposalStatus,
)
from app.models.requirements import AwardCriterion
from app.models.tender_scope import (
    RequirementCapabilityNeed,
    RequirementTerritory,
    TenderContractFact,
    TenderScopeItem,
)
from app.models.tender_structure import TenderBlock, TenderSite
from app.models.tenders import Tender
from app.schemas.provenance import read_of
from app.schemas.structure_proposals import (
    PlatformBlockCreate,
    PlatformBlockRead,
    PlatformBlockUpdate,
    PlatformCitationLink,
    PlatformFactCreate,
    PlatformFactRead,
    PlatformFactUpdate,
    PlatformNeedCreate,
    PlatformNeedRead,
    PlatformNeedUpdate,
    PlatformRequirementTerritoriesRead,
    PlatformScopeItemCreate,
    PlatformScopeItemRead,
    PlatformScopeItemUpdate,
    PlatformSiteCreate,
    PlatformSiteRead,
    PlatformSiteUpdate,
    PlatformStructureRead,
    PlatformTerritoryCreate,
    PlatformTerritoryRead,
    ProvenanceRead,
    ResetUnknownRead,
    ResetUnknownRequest,
    StructurePromotionRead,
    StructureProposalCreate,
    StructureProposalRead,
    StructureProposalReject,
)
from app.services import audit
from app.services import structure_proposals as proposals
from app.services import tender_scope as scope
from app.services import tender_structure as structure
from app.services.public_document_intake import load_public_tender
from app.services.source_refs import resolve_source_ref
from app.services.structure_provenance import Provenance

router = APIRouter(prefix="/platform/tenders/{tender_id}/structure", tags=["platform"])


# --------------------------------------------------------------------------- #
# Ajudants                                                                      #
# --------------------------------------------------------------------------- #


async def _public(session, tender_id: uuid.UUID) -> Tender:
    """Nomes una licitacio publica: inexistent -> 404; d'empresa o gold -> 422
    `tender_not_public` (el mateix que `load_public_tender` als routers germans)."""
    return await load_public_tender(session, tender_id)


def _operator(admin) -> Provenance:
    return Provenance.operator(admin.id)


async def _audit(session, admin, action: AuditAction, entity_type: str, entity_id, summary: str):
    await audit.record(
        session,
        action=action,
        company_id=None,
        user_id=admin.id,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
    )


async def _link_all(session, entity, source_ref_ids, provenance: Provenance) -> int:
    """Enllaca les cites (idempotent). Retorna quantes son noves per a l'entitat."""
    current = await structure.source_refs_of(session, type(entity), [entity.id])
    existing = {ref.id for ref in current.get(entity.id, [])}
    added = 0
    for ref in await proposals.citable_refs(session, entity.tender_id, source_ref_ids):
        added += ref.id not in existing
        await structure.link_source_ref(
            session, entity=entity, source_ref_id=ref.id, provenance=provenance
        )
    return added


def _fields(payload, exclude: set[str] | None = None) -> dict:
    skip = {"source_ref_ids"} | (exclude or set())
    return {name: getattr(payload, name) for name in payload.model_fields_set if name not in skip}


async def _citations(session, model, entity_id: uuid.UUID) -> list:
    refs = (await structure.source_refs_of(session, model, [entity_id])).get(entity_id, [])
    return [read_of(await resolve_source_ref(session, ref)) for ref in refs]


async def _block_read(session, block: TenderBlock) -> PlatformBlockRead:
    await session.refresh(block)
    read = PlatformBlockRead.model_validate(
        {
            **{c.key: getattr(block, c.key) for c in sa.inspect(TenderBlock).columns},
            "provenance": ProvenanceRead.model_validate(block),
        }
    )
    read.citations = await _citations(session, TenderBlock, block.id)
    return read


async def _site_read(session, site: TenderSite) -> PlatformSiteRead:
    await session.refresh(site)
    read = PlatformSiteRead.model_validate(
        {
            **{c.key: getattr(site, c.key) for c in sa.inspect(TenderSite).columns},
            "provenance": ProvenanceRead.model_validate(site),
        }
    )
    read.citations = await _citations(session, TenderSite, site.id)
    return read


async def _item_read(session, item: TenderScopeItem) -> PlatformScopeItemRead:
    await session.refresh(item)
    read = PlatformScopeItemRead.model_validate(
        {
            **{c.key: getattr(item, c.key) for c in sa.inspect(TenderScopeItem).columns},
            "provenance": ProvenanceRead.model_validate(item),
        }
    )
    read.citations = await _citations(session, TenderScopeItem, item.id)
    return read


async def _need_read(session, need: RequirementCapabilityNeed) -> PlatformNeedRead:
    await session.refresh(need)
    read = PlatformNeedRead.model_validate(
        {
            **{c.key: getattr(need, c.key) for c in sa.inspect(RequirementCapabilityNeed).columns},
            "provenance": ProvenanceRead.model_validate(need),
        }
    )
    read.citations = await _citations(session, RequirementCapabilityNeed, need.id)
    return read


async def _fact_read(session, fact: TenderContractFact) -> PlatformFactRead:
    await session.refresh(fact)
    read = PlatformFactRead.model_validate(
        {
            **{c.key: getattr(fact, c.key) for c in sa.inspect(TenderContractFact).columns},
            "block_ids": (await scope.fact_block_ids(session, [fact.id])).get(fact.id, []),
            "provenance": ProvenanceRead.model_validate(fact),
        }
    )
    read.citations = await _citations(session, TenderContractFact, fact.id)
    return read


async def _territories_read(session, requirement) -> PlatformRequirementTerritoriesRead:
    await session.refresh(requirement, attribute_names=["territories"])
    return PlatformRequirementTerritoriesRead(
        requirement_id=requirement.id,
        territory_completeness=requirement.territory_completeness,
        territories=[
            PlatformTerritoryRead.model_validate(
                {
                    **{c.key: getattr(t, c.key) for c in sa.inspect(RequirementTerritory).columns},
                    "provenance": ProvenanceRead.model_validate(t),
                }
            )
            for t in requirement.territories
        ],
    )


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


@router.get("", response_model=PlatformStructureRead, summary="Estructura amb provenance")
async def read_structure(tender_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep):
    tender = await _public(session, tender_id)
    await session.refresh(tender, attribute_names=["blocks", "sites"])
    return PlatformStructureRead(
        tender_id=tender.id,
        blocks=[await _block_read(session, b) for b in tender.blocks],
        sites=[await _site_read(session, s) for s in tender.sites],
        scope_items=[
            await _item_read(session, i) for i in await scope.scope_items_of(session, tender.id)
        ],
        contract_facts=[
            await _fact_read(session, f) for f in await scope.facts_of(session, tender.id)
        ],
    )


# --------------------------------------------------------------------------- #
# Blocs                                                                         #
# --------------------------------------------------------------------------- #


@router.post(
    "/blocks",
    response_model=PlatformBlockRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un bloc citat",
)
async def create_block(
    tender_id: uuid.UUID, payload: PlatformBlockCreate, admin: PlatformAdmin, session: SessionDep
):
    tender = await _public(session, tender_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    block = await structure.create_block(
        session, tender, payload.model_dump(exclude={"source_ref_ids"}), provenance=provenance
    )
    await _link_all(session, block, payload.source_ref_ids, provenance)
    await _audit(
        session, admin, AuditAction.CREATE, "tender_block", block.id,
        f"licitacio={tender.id} bloc={block.code} cites={len(payload.source_ref_ids)}",
    )  # fmt: skip
    await session.commit()
    return await _block_read(session, block)


@router.patch("/blocks/{block_id}", response_model=PlatformBlockRead, summary="Corregeix un bloc")
async def update_block(
    tender_id: uuid.UUID,
    block_id: uuid.UUID,
    payload: PlatformBlockUpdate,
    admin: PlatformAdmin,
    session: SessionDep,
):
    tender = await _public(session, tender_id)
    block = await structure.load_block(session, tender, block_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    fields = _fields(payload)
    changed = await structure.update_block(session, tender, block, fields, provenance=provenance)
    # Les cites d'un PATCH valid s'enllacen sempre: poden ser evidencia nova d'un valor correcte.
    added = await _link_all(session, block, payload.source_ref_ids, provenance)
    if changed or added:
        await _audit(
            session, admin, AuditAction.UPDATE, "tender_block", block.id,
            f"licitacio={tender.id} bloc={block.code} camps={','.join(sorted(fields))} "
            f"cites+={added}",
        )  # fmt: skip
    await session.commit()
    return await _block_read(session, block)


@router.delete(
    "/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Esborra un bloc"
)
async def delete_block(
    tender_id: uuid.UUID, block_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep
) -> Response:
    tender = await _public(session, tender_id)
    block = await structure.load_block(session, tender, block_id)
    code = block.code
    await structure.delete_block(session, block)
    await _audit(
        session, admin, AuditAction.DELETE, "tender_block", block_id,
        f"licitacio={tender.id} bloc={code}",
    )  # fmt: skip
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Seus                                                                          #
# --------------------------------------------------------------------------- #


@router.post(
    "/sites",
    response_model=PlatformSiteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix una seu citada",
)
async def create_site(
    tender_id: uuid.UUID, payload: PlatformSiteCreate, admin: PlatformAdmin, session: SessionDep
):
    tender = await _public(session, tender_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    site = await structure.create_site(
        session, tender, payload.model_dump(exclude={"source_ref_ids"}), provenance=provenance
    )
    await _link_all(session, site, payload.source_ref_ids, provenance)
    await _audit(
        session, admin, AuditAction.CREATE, "tender_site", site.id,
        f"licitacio={tender.id} seu={site.code or site.id}",
    )  # fmt: skip
    await session.commit()
    return await _site_read(session, site)


@router.patch("/sites/{site_id}", response_model=PlatformSiteRead, summary="Corregeix una seu")
async def update_site(
    tender_id: uuid.UUID,
    site_id: uuid.UUID,
    payload: PlatformSiteUpdate,
    admin: PlatformAdmin,
    session: SessionDep,
):
    tender = await _public(session, tender_id)
    site = await structure.load_site(session, tender, site_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    fields = _fields(payload)
    changed = await structure.update_site(session, tender, site, fields, provenance=provenance)
    added = await _link_all(session, site, payload.source_ref_ids, provenance)
    if changed or added:
        await _audit(
            session, admin, AuditAction.UPDATE, "tender_site", site.id,
            f"licitacio={tender.id} seu={site.id} camps={','.join(sorted(fields))} "
            f"cites+={added}",
        )  # fmt: skip
    await session.commit()
    return await _site_read(session, site)


@router.delete(
    "/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Esborra una seu"
)
async def delete_site(
    tender_id: uuid.UUID, site_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep
) -> Response:
    tender = await _public(session, tender_id)
    site = await structure.load_site(session, tender, site_id)
    await structure.delete_site(session, site)
    await _audit(
        session, admin, AuditAction.DELETE, "tender_site", site_id, f"licitacio={tender.id}"
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Elements d'abast                                                              #
# --------------------------------------------------------------------------- #


@router.post(
    "/scope-items",
    response_model=PlatformScopeItemRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un element d'abast citat",
)
async def create_scope_item(
    tender_id: uuid.UUID,
    payload: PlatformScopeItemCreate,
    admin: PlatformAdmin,
    session: SessionDep,
):
    tender = await _public(session, tender_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    item = await scope.create_scope_item(
        session, tender, payload.model_dump(exclude={"source_ref_ids"}), provenance=provenance
    )
    await _link_all(session, item, payload.source_ref_ids, provenance)
    await _audit(
        session, admin, AuditAction.CREATE, "tender_scope_item", item.id,
        f"licitacio={tender.id} abast={item.service_line_code}",
    )  # fmt: skip
    await session.commit()
    return await _item_read(session, item)


@router.patch(
    "/scope-items/{item_id}",
    response_model=PlatformScopeItemRead,
    summary="Corregeix un element d'abast",
)
async def update_scope_item(
    tender_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: PlatformScopeItemUpdate,
    admin: PlatformAdmin,
    session: SessionDep,
):
    tender = await _public(session, tender_id)
    item = await scope.load_scope_item(session, tender, item_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    fields = _fields(payload)
    changed = await scope.update_scope_item(session, tender, item, fields, provenance=provenance)
    added = await _link_all(session, item, payload.source_ref_ids, provenance)
    if changed or added:
        await _audit(
            session, admin, AuditAction.UPDATE, "tender_scope_item", item.id,
            f"licitacio={tender.id} abast={item.id} camps={','.join(sorted(fields))} "
            f"cites+={added}",
        )  # fmt: skip
    await session.commit()
    return await _item_read(session, item)


@router.delete(
    "/scope-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Esborra un element d'abast",
)
async def delete_scope_item(
    tender_id: uuid.UUID, item_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep
) -> Response:
    tender = await _public(session, tender_id)
    item = await scope.load_scope_item(session, tender, item_id)
    await scope.delete_scope_item(session, item)
    await _audit(
        session, admin, AuditAction.DELETE, "tender_scope_item", item_id, f"licitacio={tender.id}"
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Necessitats tecniques                                                         #
# --------------------------------------------------------------------------- #


@router.post(
    "/requirements/{requirement_id}/capability-needs",
    response_model=PlatformNeedRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix una necessitat tecnica citada a un requisit",
)
async def create_need(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: PlatformNeedCreate,
    admin: PlatformAdmin,
    session: SessionDep,
):
    tender = await _public(session, tender_id)
    requirement = await structure.load_requirement(session, tender, requirement_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    need = await scope.create_need(
        session,
        tender,
        requirement,
        payload.model_dump(exclude={"source_ref_ids"}),
        provenance=provenance,
    )
    await _link_all(session, need, payload.source_ref_ids, provenance)
    await _audit(
        session, admin, AuditAction.CREATE, "requirement_capability_need", need.id,
        f"requisit={requirement.id} necessitat={need.capability_code or need.unmapped_capability}",
    )  # fmt: skip
    await session.commit()
    return await _need_read(session, need)


@router.patch(
    "/capability-needs/{need_id}",
    response_model=PlatformNeedRead,
    summary="Corregeix una necessitat tecnica",
)
async def update_need(
    tender_id: uuid.UUID,
    need_id: uuid.UUID,
    payload: PlatformNeedUpdate,
    admin: PlatformAdmin,
    session: SessionDep,
):
    tender = await _public(session, tender_id)
    need = await scope.load_need(session, tender, need_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    fields = _fields(payload)
    changed = await scope.update_need(session, tender, need, fields, provenance=provenance)
    added = await _link_all(session, need, payload.source_ref_ids, provenance)
    if changed or added:
        await _audit(
            session, admin, AuditAction.UPDATE, "requirement_capability_need", need.id,
            f"necessitat={need.id} camps={','.join(sorted(fields))} cites+={added}",
        )  # fmt: skip
    await session.commit()
    return await _need_read(session, need)


@router.delete(
    "/capability-needs/{need_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Esborra una necessitat tecnica",
)
async def delete_need(
    tender_id: uuid.UUID, need_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep
) -> Response:
    tender = await _public(session, tender_id)
    need = await scope.load_need(session, tender, need_id)
    await scope.delete_need(session, need)
    await _audit(
        session, admin, AuditAction.DELETE, "requirement_capability_need", need_id,
        f"licitacio={tender.id}",
    )  # fmt: skip
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Fets contractuals                                                             #
# --------------------------------------------------------------------------- #


@router.post(
    "/contract-facts",
    response_model=PlatformFactRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un fet contractual citat",
)
async def create_fact(
    tender_id: uuid.UUID, payload: PlatformFactCreate, admin: PlatformAdmin, session: SessionDep
):
    tender = await _public(session, tender_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    fact = await scope.create_fact(
        session,
        tender,
        fact_code=payload.fact_code,
        value_json=payload.value_json,
        value_schema=payload.value_schema,
        block_ids=payload.block_ids,
        provenance=provenance,
    )
    await _link_all(session, fact, payload.source_ref_ids, provenance)
    await _audit(
        session, admin, AuditAction.CREATE, "tender_contract_fact", fact.id,
        f"licitacio={tender.id} fet={fact.fact_code}",
    )  # fmt: skip
    await session.commit()
    return await _fact_read(session, fact)


@router.patch(
    "/contract-facts/{fact_id}",
    response_model=PlatformFactRead,
    summary="Corregeix un fet contractual",
)
async def update_fact(
    tender_id: uuid.UUID,
    fact_id: uuid.UUID,
    payload: PlatformFactUpdate,
    admin: PlatformAdmin,
    session: SessionDep,
):
    tender = await _public(session, tender_id)
    fact = await scope.load_fact(session, tender, fact_id)
    await proposals.citable_refs(session, tender.id, payload.source_ref_ids)
    provenance = _operator(admin)
    changed = await scope.update_fact(
        session,
        tender,
        fact,
        fact_code=payload.fact_code,
        value_json=payload.value_json,
        value_schema=payload.value_schema,
        block_ids=payload.block_ids,
        provenance=provenance,
    )
    added = await _link_all(session, fact, payload.source_ref_ids, provenance)
    if changed or added:
        await _audit(
            session, admin, AuditAction.UPDATE, "tender_contract_fact", fact.id,
            f"licitacio={tender.id} fet={fact.fact_code} "
            f"camps={','.join(sorted(_fields(payload)))} cites+={added}",
        )  # fmt: skip
    await session.commit()
    return await _fact_read(session, fact)


@router.delete(
    "/contract-facts/{fact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Esborra un fet contractual",
)
async def delete_fact(
    tender_id: uuid.UUID, fact_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep
) -> Response:
    tender = await _public(session, tender_id)
    fact = await scope.load_fact(session, tender, fact_id)
    code = fact.fact_code
    await scope.delete_fact(session, fact)
    await _audit(
        session, admin, AuditAction.DELETE, "tender_contract_fact", fact_id,
        f"licitacio={tender.id} fet={code}",
    )  # fmt: skip
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Territoris d'un requisit                                                      #
# --------------------------------------------------------------------------- #


@router.get(
    "/requirements/{requirement_id}/territories",
    response_model=PlatformRequirementTerritoriesRead,
    summary="Territoris d'un requisit amb provenance",
)
async def read_territories(
    tender_id: uuid.UUID, requirement_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep
):
    tender = await _public(session, tender_id)
    # Llegir el d'una run superada es legitim; anotar-lo no (M6).
    requirement = await structure.load_requirement(session, tender, requirement_id, for_write=False)
    return await _territories_read(session, requirement)


@router.post(
    "/requirements/{requirement_id}/territories",
    response_model=PlatformRequirementTerritoriesRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix un territori citat (i/o la completesa) a un requisit",
)
async def add_territory(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: PlatformTerritoryCreate,
    admin: PlatformAdmin,
    session: SessionDep,
):
    """La cita no te on penjar d'un territori: queda a una proposta de l'operador
    que es promou al moment amb `origin=operator`, i la fila hi apunta."""
    tender = await _public(session, tender_id)
    requirement = await structure.load_requirement(session, tender, requirement_id)
    proposal = await proposals.create_proposal(
        session,
        tender,
        entity_kind=StructureEntityKind.TERRITORY,
        payload={
            "requirement_id": str(requirement.id),
            "territory": payload.territory.model_dump(mode="json") if payload.territory else None,
            "territory_completeness": payload.territory_completeness,
        },
        source_ref_ids=payload.source_ref_ids,
        producer=StructureProposalProducer.OPERATOR,
        actor=admin,
    )
    await proposals.promote_proposal(
        session, tender.id, proposal.id, actor=admin, origin=StructureOrigin.OPERATOR
    )
    await _audit(
        session, admin, AuditAction.UPDATE, "requirement", requirement.id,
        f"requisit={requirement.id} territori+ proposta={proposal.id}",
    )  # fmt: skip
    await session.commit()
    return await _territories_read(session, requirement)


@router.delete(
    "/requirements/{requirement_id}/territories/{territory_id}",
    response_model=PlatformRequirementTerritoriesRead,
    summary="Treu un territori d'un requisit",
)
async def remove_territory(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    territory_id: uuid.UUID,
    admin: PlatformAdmin,
    session: SessionDep,
):
    """Treure no afirma cap valor: no cal cita. La completesa declarada s'ha de
    continuar complint (`territory_completeness_conflict` si no)."""
    tender = await _public(session, tender_id)
    requirement = await structure.load_requirement(session, tender, requirement_id)
    await session.refresh(requirement, attribute_names=["territories"])
    remaining = [t for t in requirement.territories if t.id != territory_id]
    if len(remaining) == len(requirement.territories):
        raise NotFoundError("Territori no trobat.")
    scope.check_territory_completeness(requirement.territory_completeness, len(remaining))
    for territory in requirement.territories:
        if territory.id == territory_id:
            requirement.territories.remove(territory)
    await session.flush()
    await _audit(
        session, admin, AuditAction.UPDATE, "requirement", requirement.id,
        f"requisit={requirement.id} territori-={territory_id}",
    )  # fmt: skip
    await session.commit()
    return await _territories_read(session, requirement)


# --------------------------------------------------------------------------- #
# Cites i «tornar a desconegut»                                                 #
# --------------------------------------------------------------------------- #

_CITATION_TARGETS = {
    StructureEntityKind.BLOCK: structure.load_block,
    StructureEntityKind.SITE: structure.load_site,
    StructureEntityKind.SCOPE_ITEM: scope.load_scope_item,
    StructureEntityKind.CAPABILITY_NEED: scope.load_need,
    StructureEntityKind.CONTRACT_FACT: scope.load_fact,
    StructureEntityKind.CRITERION_CITATION: structure.load_criterion,
}


async def _citation_target(session, tender, kind: StructureEntityKind, entity_id: uuid.UUID):
    loader = _CITATION_TARGETS.get(StructureEntityKind(kind))
    if loader is None:
        raise proposals.ProposalPayloadError(
            "Aquesta entitat no te cites propies (la cita viu a una proposta)."
        )
    return await loader(session, tender, entity_id)


def _entity_type(entity) -> str:
    return "award_criterion" if isinstance(entity, AwardCriterion) else entity.__tablename__


@router.post("/citations", status_code=status.HTTP_204_NO_CONTENT, summary="Enllaça una cita")
async def link_citation(
    tender_id: uuid.UUID, payload: PlatformCitationLink, admin: PlatformAdmin, session: SessionDep
) -> Response:
    tender = await _public(session, tender_id)
    entity = await _citation_target(session, tender, payload.entity_kind, payload.entity_id)
    (ref,) = await proposals.citable_refs(session, tender.id, [payload.source_ref_id])
    await structure.link_source_ref(
        session, entity=entity, source_ref_id=ref.id, provenance=_operator(admin)
    )
    await _audit(
        session, admin, AuditAction.UPDATE, _entity_type(entity), entity.id,
        f"{payload.entity_kind}={entity.id} cita+={ref.id}",
    )  # fmt: skip
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/citations/{entity_kind}/{entity_id}/{source_ref_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Treu una cita (la cita no s'esborra)",
)
async def unlink_citation(
    tender_id: uuid.UUID,
    entity_kind: StructureEntityKind,
    entity_id: uuid.UUID,
    source_ref_id: uuid.UUID,
    admin: PlatformAdmin,
    session: SessionDep,
) -> Response:
    tender = await _public(session, tender_id)
    entity = await _citation_target(session, tender, entity_kind, entity_id)
    current = await structure.source_refs_of(session, type(entity), [entity.id])
    cited = {ref.id for ref in current.get(entity.id, [])}
    if cited == {source_ref_id}:
        # Cap valor conegut sense cita (§4.4): primer una de substituta.
        raise proposals.LastCitationError(
            "No es pot treure la darrera cita: enllaca'n primer una de substituta."
        )
    if await structure.unlink_source_ref(session, entity=entity, source_ref_id=source_ref_id):
        await _audit(
            session, admin, AuditAction.UPDATE, _entity_type(entity), entity.id,
            f"{entity_kind}={entity.id} cita-={source_ref_id}",
        )  # fmt: skip
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/reset-unknown",
    response_model=ResetUnknownRead,
    summary="Torna camps a «no se sap» (operacio explicita, no un esborrat)",
)
async def reset_unknown(
    tender_id: uuid.UUID, payload: ResetUnknownRequest, admin: PlatformAdmin, session: SessionDep
):
    tender = await _public(session, tender_id)
    changed = await proposals.reset_to_unknown(
        session,
        tender,
        entity_kind=payload.entity_kind,
        entity_id=payload.entity_id,
        fields=payload.fields,
        provenance=_operator(admin),
    )
    if changed:
        await _audit(
            session, admin, AuditAction.UPDATE, str(payload.entity_kind), payload.entity_id,
            f"licitacio={tender.id} desconegut={','.join(changed)}",
        )  # fmt: skip
    await session.commit()
    return ResetUnknownRead(
        entity_kind=payload.entity_kind, entity_id=payload.entity_id, changed=changed
    )


# --------------------------------------------------------------------------- #
# Propostes                                                                     #
# --------------------------------------------------------------------------- #


@router.post(
    "/proposals",
    response_model=StructureProposalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Desa una proposta d'estructura citada (immutable)",
)
async def create_proposal(
    tender_id: uuid.UUID,
    payload: StructureProposalCreate,
    admin: PlatformAdmin,
    session: SessionDep,
):
    tender = await _public(session, tender_id)
    proposal = await proposals.create_proposal(
        session,
        tender,
        entity_kind=payload.entity_kind,
        payload=payload.payload,
        source_ref_ids=payload.source_ref_ids,
        producer=StructureProposalProducer.OPERATOR,
        actor=admin,
    )
    await _audit(
        session, admin, AuditAction.CREATE, "tender_structure_proposal", proposal.id,
        f"licitacio={tender.id} {proposal.entity_kind}={proposal.natural_key[:80]}",
    )  # fmt: skip
    await session.commit()
    await session.refresh(proposal)
    return StructureProposalRead.model_validate(proposal)


@router.get(
    "/proposals",
    response_model=list[StructureProposalRead],
    summary="Propostes d'estructura de la licitacio",
)
async def list_proposals(
    tender_id: uuid.UUID,
    admin: PlatformAdmin,
    session: SessionDep,
    status_filter: StructureProposalStatus | None = Query(default=None, alias="status"),
):
    tender = await _public(session, tender_id)
    rows = await proposals.list_proposals(session, tender.id, status=status_filter)
    return [StructureProposalRead.model_validate(row) for row in rows]


@router.post(
    "/proposals/{proposal_id}/promote",
    response_model=StructurePromotionRead,
    summary="Promou una proposta per la via interna",
)
async def promote_proposal(
    tender_id: uuid.UUID, proposal_id: uuid.UUID, admin: PlatformAdmin, session: SessionDep
):
    tender = await _public(session, tender_id)
    result = await proposals.promote_proposal(session, tender.id, proposal_id, actor=admin)
    await _audit(
        session, admin, AuditAction.UPDATE, "tender_structure_proposal", proposal_id,
        f"licitacio={tender.id} promoguda {result.entity_kind}={result.entity_id}",
    )  # fmt: skip
    await session.commit()
    await session.refresh(result.proposal)
    return StructurePromotionRead(
        proposal=StructureProposalRead.model_validate(result.proposal),
        entity_kind=result.entity_kind,
        entity_id=result.entity_id,
    )


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=StructureProposalRead,
    summary="Rebutja una proposta (amb motiu)",
)
async def reject_proposal(
    tender_id: uuid.UUID,
    proposal_id: uuid.UUID,
    payload: StructureProposalReject,
    admin: PlatformAdmin,
    session: SessionDep,
):
    tender = await _public(session, tender_id)
    proposal = await proposals.reject_proposal(
        session, tender.id, proposal_id, actor=admin, reason=payload.reason
    )
    await _audit(
        session, admin, AuditAction.UPDATE, "tender_structure_proposal", proposal.id,
        f"licitacio={tender.id} rebutjada",
    )  # fmt: skip
    await session.commit()
    await session.refresh(proposal)
    return StructureProposalRead.model_validate(proposal)
