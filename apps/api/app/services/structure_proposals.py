"""Propostes d'estructura del plec: crear, promoure, rebutjar (0060, 3B-2 §4.2, §4.4).

Una proposta es una afirmacio immutable sobre l'estructura d'una licitacio
publica: que diu el plec d'un bloc, d'una seu, d'un element d'abast, d'una
necessitat, d'un fet, d'un territori, de la temporalitat d'un requisit o de les
cites d'un criteri. Porta sempre almenys una cita (`source_ref_ids`), de la
mateixa licitacio i citable (cap document retirat ni pendent de verificar).

**Promoure = cridar la via interna** (`tender_structure`, `tender_scope`) amb
`origin=promoted` (o `operator` quan l'operador escriu directament a traves
d'una proposta seva), `proposal_id` i l'autoritat, i enllacar-hi les cites. No
hi ha cap segona implementacio de persistencia.

**`unknown` no es coacciona mai.** Una proposta que diu `timing=unknown`,
`is_critical=None`, `separately_awardable=None` o
`territory_completeness=unknown` no es cap afirmacio sobre aquell camp:

* en una entitat nova es desa tal qual (desconegut);
* en una entitat que ja hi era, NO rebaixa un valor conegut.

Tornar un valor a desconegut es una operacio explicita (`reset_to_unknown`), no
un esborrat ni l'efecte secundari d'una promocio.

En 3B-2 nomes l'operador de plataforma crea propostes (`producer=operator`).
`extraction` queda per a un productor automatic (cap en aquesta fase: l'LLM
d'estructura es 3B-3); `company_proposal` esta reservat i fora d'abast.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.enums import (
    RequirementTiming,
    StructureEntityKind,
    StructureOrigin,
    StructureProposalProducer,
    StructureProposalStatus,
    TerritoryCompleteness,
)
from app.models.identity import User
from app.models.provenance import TenderSourceRef
from app.models.requirements import AwardCriterion, Requirement
from app.models.structure_proposals import TenderStructureProposal
from app.models.tender_scope import (
    RequirementCapabilityNeed,
    RequirementTerritory,
    TenderContractFact,
    TenderScopeItem,
)
from app.models.tender_structure import TenderBlock, TenderSite
from app.models.tenders import Tender, TenderLot
from app.schemas.structure_proposals import PAYLOAD_SCHEMAS, UNKNOWN_FIELDS
from app.services import tender_scope as scope
from app.services import tender_structure as structure
from app.services.source_refs import assert_ref_citable
from app.services.structure_provenance import Provenance

#: Longitud maxima de `natural_key` (la columna).
NATURAL_KEY_LENGTH = 300


class ProposalCitationRequiredError(ValidationError):
    """Cap proposta (ni cap valor conegut) sense almenys una cita."""

    code = "structure_proposal_citation_required"


class ProposalPayloadError(ValidationError):
    code = "structure_proposal_payload_invalid"


class ProposalProducerNotAllowedError(ValidationError):
    """`company_proposal` esta reservat: fora d'abast de 3B-2."""

    code = "structure_proposal_producer_not_allowed"


class ProposalNotPendingError(ConflictError):
    """Una proposta decidida no es torna a decidir."""

    code = "structure_proposal_not_pending"


class SourceRefTenderMismatchError(ValidationError):
    code = "source_ref_tender_mismatch"


class ResetUnknownNotAllowedError(ValidationError):
    """Aquell camp no te «desconegut», o aquella entitat no en te cap."""

    code = "structure_reset_field_invalid"


class LastCitationError(ValidationError):
    """Treure la darrera cita deixaria un valor conegut sense cap cita (§4.4)."""

    code = "last_citation"


# --------------------------------------------------------------------------- #
# Cites                                                                         #
# --------------------------------------------------------------------------- #


async def citable_refs(
    session: AsyncSession, tender_id: uuid.UUID, source_ref_ids: list[uuid.UUID]
) -> list[TenderSourceRef]:
    """Les cites, sense repeticions: almenys una, totes de la licitacio i citables."""
    wanted = list(dict.fromkeys(uuid.UUID(str(ref_id)) for ref_id in source_ref_ids))
    if not wanted:
        raise ProposalCitationRequiredError("Cal almenys una cita.")
    refs: list[TenderSourceRef] = []
    for ref_id in wanted:
        ref = await session.get(TenderSourceRef, ref_id)
        if ref is None:
            raise NotFoundError("Cita no trobada.", details={"source_ref_id": str(ref_id)})
        if ref.tender_id != tender_id:
            raise SourceRefTenderMismatchError("Aquesta cita es d'una altra licitacio.")
        await assert_ref_citable(session, ref)
        refs.append(ref)
    return refs


# --------------------------------------------------------------------------- #
# Payload i clau natural                                                        #
# --------------------------------------------------------------------------- #


def validate_payload(entity_kind: StructureEntityKind, payload: dict[str, Any]) -> BaseModel:
    schema = PAYLOAD_SCHEMAS[StructureEntityKind(entity_kind)]
    try:
        return schema.model_validate(payload)
    except PydanticValidationError as exc:
        raise ProposalPayloadError(
            "La proposta no te la forma del servei intern.",
            details={"errors": exc.errors(include_url=False, include_context=False)},
        ) from exc


def _bounded(key: str) -> str:
    """La clau tal qual si hi cap; si no, un prefix i el seu hash (estable)."""
    if len(key) <= NATURAL_KEY_LENGTH:
        return key
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"{key[: NATURAL_KEY_LENGTH - 66]}#{digest}"


def _norm(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def site_key(code: str | None, name: str | None) -> str:
    """Identitat d'una seu: el codi del document si en te; si no, el nom real."""
    return f"code:{_norm(code)}" if code else f"name:{_norm(name).casefold()}"


async def _block_code(session: AsyncSession, block_id: uuid.UUID | None) -> str:
    if block_id is None:
        return ""
    block = await session.get(TenderBlock, block_id)
    return block.code if block is not None else str(block_id)


async def _site_identity(session: AsyncSession, site_id: uuid.UUID | None) -> str:
    if site_id is None:
        return ""
    site = await session.get(TenderSite, site_id)
    return site_key(site.code, site.name) if site is not None else str(site_id)


async def _lot_key(session: AsyncSession, lot_id: uuid.UUID | None) -> str:
    if lot_id is None:
        return ""
    lot = await session.get(TenderLot, lot_id)
    return lot.external_lot_id if lot is not None and lot.external_lot_id else str(lot_id)


async def scope_item_key(session: AsyncSession, fields: dict[str, Any]) -> str:
    """Identitat d'un element d'abast: linia, bloc, lot, seu i unitat."""
    return "|".join(
        (
            str(fields.get("service_line_code")),
            f"block={await _block_code(session, fields.get('block_id'))}",
            f"lot={await _lot_key(session, fields.get('tender_lot_id'))}",
            f"site={await _site_identity(session, fields.get('site_id'))}",
            f"unit={_norm(fields.get('unit')).casefold()}",
        )
    )


def need_key(requirement: Requirement, fields: dict[str, Any]) -> str:
    capability = (
        fields.get("capability_code")
        or f"unmapped:{_norm(fields.get('unmapped_capability')).casefold()}"
    )
    return (
        f"{requirement.dedup_key}|{capability}|{fields.get('need_type')}|{fields.get('comparator')}"
    )


async def fact_key(session: AsyncSession, fact_code: Any, block_ids: list[uuid.UUID]) -> str:
    codes = sorted({await _block_code(session, block_id) for block_id in block_ids})
    return f"{fact_code}|blocks={','.join(codes)}"


def territory_key(requirement: Requirement, territory: dict[str, Any] | None) -> str:
    if territory is None:
        return f"{requirement.dedup_key}|completeness"
    identity = scope.territory_identity(territory)
    return f"{requirement.dedup_key}|" + "|".join(
        "" if part is None else str(part) for part in identity
    )


async def natural_key(
    session: AsyncSession, tender: Tender, entity_kind: StructureEntityKind, data: dict[str, Any]
) -> str:
    """La clau natural de la proposta (comprova de passada que el que referencia
    es de la licitacio)."""
    kind = StructureEntityKind(entity_kind)
    if kind == StructureEntityKind.BLOCK:
        key = data["code"]
    elif kind == StructureEntityKind.SITE:
        structure.check_site_identity(data.get("code"), data.get("name"))
        key = site_key(data.get("code"), data.get("name"))
    elif kind == StructureEntityKind.SCOPE_ITEM:
        key = await scope_item_key(session, data)
    elif kind == StructureEntityKind.CAPABILITY_NEED:
        requirement = await structure.load_requirement(session, tender, data["requirement_id"])
        key = need_key(requirement, data)
    elif kind == StructureEntityKind.AWARD_LINK:
        requirement = await structure.load_requirement(session, tender, data["requirement_id"])
        criterion = await structure.load_criterion(session, tender, data["award_criterion_id"])
        key = f"{requirement.dedup_key}|{criterion.dedup_key}"
    elif kind == StructureEntityKind.CONTRACT_FACT:
        key = await fact_key(session, data["fact_code"], data.get("block_ids") or [])
    elif kind == StructureEntityKind.TERRITORY:
        requirement = await structure.load_requirement(session, tender, data["requirement_id"])
        key = territory_key(requirement, data.get("territory"))
    elif kind == StructureEntityKind.REQUIREMENT_TIMING:
        requirement = await structure.load_requirement(session, tender, data["requirement_id"])
        key = requirement.dedup_key
    else:  # CRITERION_CITATION
        criterion = await structure.load_criterion(session, tender, data["award_criterion_id"])
        key = criterion.dedup_key
    return _bounded(str(key))


# --------------------------------------------------------------------------- #
# Crear, llistar, rebutjar                                                      #
# --------------------------------------------------------------------------- #


async def create_proposal(
    session: AsyncSession,
    tender: Tender,
    *,
    entity_kind: StructureEntityKind,
    payload: dict[str, Any],
    source_ref_ids: list[uuid.UUID],
    producer: StructureProposalProducer | str,
    actor: User | None = None,
    producer_run_id: uuid.UUID | None = None,
) -> TenderStructureProposal:
    """Desa una proposta immutable. Valida la forma, la clau natural i les cites
    (almenys una, de la licitacio, citables) ABANS de desar res."""
    try:
        producer = StructureProposalProducer(producer)
    except ValueError as exc:
        raise ProposalProducerNotAllowedError(
            f"El productor {producer!r} no pot proposar estructura en aquesta fase."
        ) from exc
    if producer == StructureProposalProducer.OPERATOR and actor is None:
        raise ProposalPayloadError("Una proposta de l'operador necessita qui la fa.")
    if producer == StructureProposalProducer.EXTRACTION and producer_run_id is None:
        raise ProposalPayloadError("Una proposta d'un productor necessita la seva passada.")
    refs = await citable_refs(session, tender.id, source_ref_ids)
    model = validate_payload(entity_kind, payload)
    key = await natural_key(session, tender, entity_kind, model.model_dump())
    proposal = TenderStructureProposal(
        tender_id=tender.id,
        producer=producer,
        producer_run_id=producer_run_id,
        entity_kind=StructureEntityKind(entity_kind),
        natural_key=key,
        payload_json=model.model_dump(mode="json"),
        source_ref_ids=[str(ref.id) for ref in refs],
        status=StructureProposalStatus.PROPOSED,
        created_by=actor.id if actor is not None else None,
    )
    session.add(proposal)
    await session.flush()
    return proposal


async def list_proposals(
    session: AsyncSession, tender_id: uuid.UUID, *, status: StructureProposalStatus | None = None
) -> list[TenderStructureProposal]:
    query = sa.select(TenderStructureProposal).where(TenderStructureProposal.tender_id == tender_id)
    if status is not None:
        query = query.where(TenderStructureProposal.status == status)
    return list(
        await session.scalars(
            query.order_by(TenderStructureProposal.created_at, TenderStructureProposal.id)
        )
    )


async def load_proposal(
    session: AsyncSession, tender_id: uuid.UUID, proposal_id: uuid.UUID, *, for_update: bool = False
) -> TenderStructureProposal:
    query = sa.select(TenderStructureProposal).where(
        TenderStructureProposal.id == proposal_id,
        TenderStructureProposal.tender_id == tender_id,
    )
    if for_update:
        query = query.with_for_update()
    proposal = await session.scalar(query)
    if proposal is None:
        raise NotFoundError("Proposta no trobada.")
    return proposal


def _assert_pending(proposal: TenderStructureProposal) -> None:
    if proposal.status != StructureProposalStatus.PROPOSED:
        raise ProposalNotPendingError(
            "Aquesta proposta ja esta decidida.", details={"status": str(proposal.status)}
        )


async def reject_proposal(
    session: AsyncSession,
    tender_id: uuid.UUID,
    proposal_id: uuid.UUID,
    *,
    actor: User,
    reason: str,
) -> TenderStructureProposal:
    """La proposta es queda (immutable) amb el motiu del rebuig."""
    if not (reason or "").strip():
        raise ProposalPayloadError("Cal el motiu del rebuig.")
    proposal = await load_proposal(session, tender_id, proposal_id, for_update=True)
    _assert_pending(proposal)
    proposal.status = StructureProposalStatus.REJECTED
    proposal.rejected_by = actor.id
    proposal.rejected_at = datetime.now(UTC)
    proposal.rejected_reason = reason.strip()
    await session.flush()
    return proposal


# --------------------------------------------------------------------------- #
# Promoure                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PromotionResult:
    proposal: TenderStructureProposal
    entity_kind: StructureEntityKind
    entity_id: uuid.UUID


def _is_unknown(value: Any) -> bool:
    return value is None or value in (
        RequirementTiming.UNKNOWN,
        RequirementTiming.UNKNOWN.value,
        TerritoryCompleteness.UNKNOWN,
        TerritoryCompleteness.UNKNOWN.value,
    )


def _claims(fields: dict[str, Any]) -> dict[str, Any]:
    """Nomes el que la proposta AFIRMA: un desconegut no rebaixa cap valor."""
    return {name: value for name, value in fields.items() if not _is_unknown(value)}


async def _promote_block(session, tender, data, provenance) -> TenderBlock:
    existing = await session.scalar(
        sa.select(TenderBlock).where(
            TenderBlock.tender_id == tender.id, TenderBlock.code == data["code"]
        )
    )
    if existing is None:
        return await structure.create_block(session, tender, data, provenance=provenance)
    await structure.update_block(session, tender, existing, _claims(data), provenance=provenance)
    return existing


async def _promote_site(session, tender, data, provenance) -> TenderSite:
    wanted = site_key(data.get("code"), data.get("name"))
    sites = await session.scalars(sa.select(TenderSite).where(TenderSite.tender_id == tender.id))
    existing = next((s for s in sites if site_key(s.code, s.name) == wanted), None)
    if existing is None:
        return await structure.create_site(session, tender, data, provenance=provenance)
    await structure.update_site(session, tender, existing, _claims(data), provenance=provenance)
    return existing


async def _promote_scope_item(session, tender, data, provenance) -> TenderScopeItem:
    wanted = await scope_item_key(session, data)
    existing = None
    for item in await scope.scope_items_of(session, tender.id):
        current = {name: getattr(item, name) for name in scope.SCOPE_FIELDS}
        if await scope_item_key(session, current) == wanted:
            existing = item
            break
    if existing is None:
        return await scope.create_scope_item(session, tender, data, provenance=provenance)
    await scope.update_scope_item(session, tender, existing, _claims(data), provenance=provenance)
    return existing


async def _promote_need(session, tender, data, provenance) -> RequirementCapabilityNeed:
    requirement = await structure.load_requirement(session, tender, data.pop("requirement_id"))
    wanted = need_key(requirement, data)
    needs = await session.scalars(
        sa.select(RequirementCapabilityNeed).where(
            RequirementCapabilityNeed.requirement_id == requirement.id
        )
    )
    existing = next(
        (
            n
            for n in needs
            if need_key(requirement, {name: getattr(n, name) for name in scope.NEED_FIELDS})
            == wanted
        ),
        None,
    )
    if existing is None:
        return await scope.create_need(session, tender, requirement, data, provenance=provenance)
    await scope.update_need(session, tender, existing, _claims(data), provenance=provenance)
    return existing


async def _promote_award_link(session, tender, data, provenance) -> Requirement:
    requirement = await structure.load_requirement(session, tender, data["requirement_id"])
    await session.refresh(requirement, attribute_names=["award_links"])
    current = [link.award_criterion_id for link in requirement.award_links]
    await scope.set_award_links(
        session, tender, requirement, [*current, data["award_criterion_id"]], provenance=provenance
    )
    return requirement


async def _promote_fact(session, tender, data, provenance) -> TenderContractFact:
    block_ids = list(dict.fromkeys(data.get("block_ids") or []))
    wanted = await fact_key(session, data["fact_code"], block_ids)
    facts = await scope.facts_of(session, tender.id)
    blocks_by_fact = await scope.fact_block_ids(session, [f.id for f in facts])
    existing = None
    for fact in facts:
        if await fact_key(session, fact.fact_code, blocks_by_fact.get(fact.id, [])) == wanted:
            existing = fact
            break
    if existing is None:
        return await scope.create_fact(
            session,
            tender,
            fact_code=data["fact_code"],
            value_json=data["value_json"],
            value_schema=data["value_schema"],
            block_ids=block_ids,
            provenance=provenance,
        )
    await scope.update_fact(
        session,
        tender,
        existing,
        value_json=data["value_json"],
        value_schema=data["value_schema"],
        provenance=provenance,
    )
    return existing


def _territory_dict(row: RequirementTerritory) -> dict[str, Any]:
    return {
        "kind": row.kind,
        "label": row.label,
        "code": row.code,
        "code_system": row.code_system,
        "qualifier": row.qualifier,
        "site_id": row.site_id,
    }


async def _promote_territory(session, tender, data, provenance) -> Requirement:
    requirement = await structure.load_requirement(session, tender, data["requirement_id"])
    await session.refresh(requirement, attribute_names=["territories"])
    territories = None
    if data.get("territory") is not None:
        territories = [_territory_dict(row) for row in requirement.territories]
        territories.append(data["territory"])
    completeness = data.get("territory_completeness")
    if _is_unknown(completeness):
        completeness = None  # cap afirmacio: no rebaixa una completesa coneguda
    await scope.set_territory_semantics(
        session,
        tender,
        requirement,
        completeness=completeness,
        territories=territories,
        provenance=provenance,
    )
    return requirement


async def _promote_timing(session, tender, data, provenance) -> Requirement:
    requirement = await structure.load_requirement(session, tender, data["requirement_id"])
    if not _is_unknown(data["timing"]):
        structure.set_timing(requirement, data["timing"])
        await session.flush()
    return requirement


async def _promote_criterion_citation(session, tender, data, provenance) -> AwardCriterion:
    return await structure.load_criterion(session, tender, data["award_criterion_id"])


_PROMOTERS = {
    StructureEntityKind.BLOCK: _promote_block,
    StructureEntityKind.SITE: _promote_site,
    StructureEntityKind.SCOPE_ITEM: _promote_scope_item,
    StructureEntityKind.CAPABILITY_NEED: _promote_need,
    StructureEntityKind.AWARD_LINK: _promote_award_link,
    StructureEntityKind.CONTRACT_FACT: _promote_fact,
    StructureEntityKind.TERRITORY: _promote_territory,
    StructureEntityKind.REQUIREMENT_TIMING: _promote_timing,
    StructureEntityKind.CRITERION_CITATION: _promote_criterion_citation,
}

#: Les entitats amb taula de cites propia: la promocio hi enllaca les cites. La
#: resta (territoris, enllacos, temporalitat) les conserva a la proposta, que la
#: fila apunta amb `proposal_id`.
_CITABLE = frozenset(
    {
        StructureEntityKind.BLOCK,
        StructureEntityKind.SITE,
        StructureEntityKind.SCOPE_ITEM,
        StructureEntityKind.CAPABILITY_NEED,
        StructureEntityKind.CONTRACT_FACT,
        StructureEntityKind.CRITERION_CITATION,
    }
)


async def promote_proposal(
    session: AsyncSession,
    tender_id: uuid.UUID,
    proposal_id: uuid.UUID,
    *,
    actor: User,
    origin: StructureOrigin = StructureOrigin.PROMOTED,
) -> PromotionResult:
    """Escriu la proposta per la via interna i la marca `promoted`.

    `origin=operator` es nomes per a l'escriptura directa de l'operador que passa
    per una proposta seva (entitats sense taula de cites); la resta, `promoted`.
    Les cites es tornen a comprovar: un document retirat despres de proposar fa
    que la proposta ja no es pugui promoure.
    """
    proposal = await load_proposal(session, tender_id, proposal_id, for_update=True)
    _assert_pending(proposal)
    tender = await session.get(Tender, tender_id)
    if tender is None:  # pragma: no cover - la FK ho impedeix
        raise NotFoundError("Licitacio no trobada.")
    refs = await citable_refs(session, tender_id, [uuid.UUID(r) for r in proposal.source_ref_ids])

    if StructureOrigin(origin) == StructureOrigin.OPERATOR:
        provenance = Provenance.operator(actor.id, proposal_id=proposal.id)
    else:
        provenance = Provenance.promoted(
            actor.id, proposal.id, producer_run_id=proposal.producer_run_id
        )
    kind = StructureEntityKind(proposal.entity_kind)
    data = validate_payload(kind, proposal.payload_json).model_dump()
    entity = await _PROMOTERS[kind](session, tender, data, provenance)
    if kind in _CITABLE:
        for ref in refs:
            await structure.link_source_ref(
                session, entity=entity, source_ref_id=ref.id, provenance=provenance
            )

    proposal.status = StructureProposalStatus.PROMOTED
    proposal.promoted_by = actor.id
    proposal.promoted_at = datetime.now(UTC)
    await session.flush()
    return PromotionResult(proposal=proposal, entity_kind=kind, entity_id=entity.id)


# --------------------------------------------------------------------------- #
# Tornar a desconegut (operacio explicita)                                      #
# --------------------------------------------------------------------------- #


async def reset_to_unknown(
    session: AsyncSession,
    tender: Tender,
    *,
    entity_kind: StructureEntityKind,
    entity_id: uuid.UUID,
    fields: list[str],
    provenance: Provenance,
) -> list[str]:
    """Torna camps a «no se sap» (NULL o `unknown`). Nomes els camps que tenen
    desconegut (`UNKNOWN_FIELDS`); retorna els que han canviat. No esborra res:
    l'entitat i les seves cites es queden."""
    kind = StructureEntityKind(entity_kind)
    allowed = UNKNOWN_FIELDS.get(kind, ())
    invalid = sorted(set(fields) - set(allowed))
    if not allowed or invalid:
        raise ResetUnknownNotAllowedError(
            "Aquests camps no tenen «desconegut».",
            details={"entity_kind": str(kind), "fields": invalid or list(fields)},
        )
    wanted = list(dict.fromkeys(fields))
    if kind == StructureEntityKind.BLOCK:
        block = await structure.load_block(session, tender, entity_id)
        values = {name: None for name in wanted}
        if "billing_terms_json" in values or "billing_schema" in values:
            values.update(billing_terms_json=None, billing_schema=None)
        changed = [n for n in values if getattr(block, n) is not None]
        await structure.update_block(session, tender, block, values, provenance=provenance)
        return changed
    if kind == StructureEntityKind.SITE:
        site = await structure.load_site(session, tender, entity_id)
        values = {name: None for name in wanted}
        changed = [n for n in values if getattr(site, n) is not None]
        await structure.update_site(session, tender, site, values, provenance=provenance)
        return changed
    if kind == StructureEntityKind.SCOPE_ITEM:
        item = await scope.load_scope_item(session, tender, entity_id)
        values = {name: None for name in wanted}
        if "specs_json" in values or "specs_schema" in values:
            values.update(specs_json=None, specs_schema=None)
        changed = [n for n in values if getattr(item, n) is not None]
        await scope.update_scope_item(session, tender, item, values, provenance=provenance)
        return changed
    requirement = await structure.load_requirement(session, tender, entity_id)
    changed = []
    if "timing" in wanted and structure.set_timing(requirement, RequirementTiming.UNKNOWN):
        changed.append("timing")
    if "territory_completeness" in wanted and await scope.set_territory_completeness(
        session, requirement, TerritoryCompleteness.UNKNOWN
    ):
        changed.append("territory_completeness")
    await session.flush()
    return changed
