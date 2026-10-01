"""Abast i semantica del plec (0049): la VIA INTERNA d'escriptura.

Aquest modul persisteix l'estructura del costat plec sense cap politica d'acces:
el fan servir el router de tenant (que hi aplica la politica de la licitacio
oberta a ma) i, en el pas seguent, l'extraccio, la revisio i les fixtures que
carreguin una licitacio publica. **Cap empresa tenant hi arriba directament.**
Les funcions reben objectes ja carregats i payloads ja validats per l'esquema.

Les regles del model que la base no pot garantir sola, i que aqui s'imposen:

* un bloc, un lot o una seu referenciats son del mateix expedient;
* un criteri enllaçat a un requisit o a una necessitat es del mateix expedient;
* `billing_terms` es nomes facturacio general: **mai** porta enllacos amb blocs
  (invariant de servei: travessa dues taules i no es cap CHECK);
* els conjunts (territoris, blocs d'un fet, criteris d'un requisit) es
  substitueixen; una repeticio no es cap canvi.

Res d'aixo llegeix el coneixement de cap empresa ni crea cap resultat.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models.enums import ContractFactCode, TerritoryCompleteness, TerritoryMatchMode
from app.models.requirements import AwardCriterion, Requirement
from app.models.tender_scope import (
    ContractFactBlock,
    RequirementAwardLink,
    RequirementCapabilityNeed,
    RequirementCapabilityNeedSourceRefLink,
    RequirementTerritory,
    TenderContractFact,
    TenderContractFactSourceRefLink,
    TenderScopeItem,
    TenderScopeItemSourceRefLink,
)
from app.models.tender_structure import TenderBlock, TenderSite
from app.models.tenders import Tender, TenderLot
from app.schemas.tender_scope import PROOF_SCHEMA_V1, validate_proof
from app.services import tender_structure

# Les tres entitats citables de 0049 entren al mateix mecanisme de cites de 0048.
tender_structure._LINKS.update(
    {
        TenderScopeItem: (TenderScopeItemSourceRefLink, TenderScopeItemSourceRefLink.scope_item_id),
        RequirementCapabilityNeed: (
            RequirementCapabilityNeedSourceRefLink,
            RequirementCapabilityNeedSourceRefLink.need_id,
        ),
        TenderContractFact: (
            TenderContractFactSourceRefLink,
            TenderContractFactSourceRefLink.fact_id,
        ),
    }
)
link_source_ref = tender_structure.link_source_ref
unlink_source_ref = tender_structure.unlink_source_ref
source_refs_of = tender_structure.source_refs_of

SCOPE_FIELDS = (
    "service_line_code",
    "block_id",
    "tender_lot_id",
    "site_id",
    "quantity",
    "unit",
    "specs_json",
    "specs_schema",
    "unit_base_price_ex_vat",
)
NEED_FIELDS = (
    "capability_code",
    "unmapped_capability",
    "comparator",
    "value_json",
    "value_schema",
    "need_type",
    "points",
    "award_criterion_id",
)


class ScopeBlockXorLotError(ValidationError):
    code = "scope_block_xor_lot"


class ScopeSpecsUnversionedError(ValidationError):
    code = "scope_specs_unversioned"


class BlockTenderMismatchError(ValidationError):
    code = "block_tender_mismatch"


class LotTenderMismatchError(ValidationError):
    code = "lot_tender_mismatch"


class SiteTenderMismatchError(ValidationError):
    code = "site_tender_mismatch"


class CriterionTenderMismatchError(ValidationError):
    code = "criterion_tender_mismatch"


class NeedIncoherentError(ValidationError):
    code = "capability_need_incoherent"


class BillingTermsBlockLinkError(ValidationError):
    """`billing_terms` es facturacio general de contracte: cap bloc."""

    code = "billing_terms_contract_wide"


class ContractFactScopeConflictError(ValidationError):
    """Ja hi ha un fet del mateix codi amb exactament el mateix abast de blocs."""

    code = "contract_fact_scope_conflict"


class TerritoryCompletenessConflictError(ValidationError):
    """La completesa declarada no quadra amb el nombre de territoris."""

    code = "territory_completeness_conflict"


# --------------------------------------------------------------------------- #
# Comprovacions de pertinenca                                                   #
# --------------------------------------------------------------------------- #


async def _assert_block(session: AsyncSession, tender: Tender, block_id: uuid.UUID | None) -> None:
    if block_id is None:
        return
    block = await session.get(TenderBlock, block_id)
    if block is None or block.tender_id != tender.id:
        raise BlockTenderMismatchError("Aquest bloc no es d'aquesta licitacio.")


async def _assert_lot(session: AsyncSession, tender: Tender, lot_id: uuid.UUID | None) -> None:
    if lot_id is None:
        return
    lot = await session.get(TenderLot, lot_id)
    if lot is None or lot.tender_id != tender.id:
        raise LotTenderMismatchError("Aquest lot no es d'aquesta licitacio.")


async def _assert_site(session: AsyncSession, tender: Tender, site_id: uuid.UUID | None) -> None:
    if site_id is None:
        return
    site = await session.get(TenderSite, site_id)
    if site is None or site.tender_id != tender.id:
        raise SiteTenderMismatchError("Aquesta seu no es d'aquesta licitacio.")


async def _assert_criterion(
    session: AsyncSession, tender: Tender, criterion_id: uuid.UUID | None
) -> None:
    if criterion_id is None:
        return
    criterion = await session.get(AwardCriterion, criterion_id)
    if criterion is None or criterion.tender_id != tender.id:
        raise CriterionTenderMismatchError("Aquest criteri no es d'aquesta licitacio.")


# --------------------------------------------------------------------------- #
# Elements d'abast                                                              #
# --------------------------------------------------------------------------- #


def _check_scope(fields: dict[str, Any]) -> None:
    if fields.get("block_id") is not None and fields.get("tender_lot_id") is not None:
        raise ScopeBlockXorLotError("Un element d'abast va a un bloc o a un lot, no a tots dos.")
    if fields.get("specs_json") is not None and fields.get("specs_schema") is None:
        raise ScopeSpecsUnversionedError("`specs_json` necessita `specs_schema`.")


async def load_scope_item(
    session: AsyncSession, tender: Tender, item_id: uuid.UUID
) -> TenderScopeItem:
    item = await session.scalar(
        sa.select(TenderScopeItem).where(
            TenderScopeItem.id == item_id, TenderScopeItem.tender_id == tender.id
        )
    )
    if item is None:
        raise NotFoundError("Element d'abast no trobat.")
    return item


async def create_scope_item(
    session: AsyncSession, tender: Tender, fields: dict[str, Any]
) -> TenderScopeItem:
    _check_scope(fields)
    await _assert_block(session, tender, fields.get("block_id"))
    await _assert_lot(session, tender, fields.get("tender_lot_id"))
    await _assert_site(session, tender, fields.get("site_id"))
    item = TenderScopeItem(tender_id=tender.id, **{k: fields.get(k) for k in SCOPE_FIELDS})
    session.add(item)
    await session.flush()
    return item


async def update_scope_item(
    session: AsyncSession, tender: Tender, item: TenderScopeItem, fields: dict[str, Any]
) -> bool:
    """S'aplica nomes el que arriba; la coherencia es comprova sobre el resultat."""
    merged = {k: fields[k] if k in fields else getattr(item, k) for k in SCOPE_FIELDS}
    _check_scope(merged)
    if "block_id" in fields:
        await _assert_block(session, tender, fields["block_id"])
    if "tender_lot_id" in fields:
        await _assert_lot(session, tender, fields["tender_lot_id"])
    if "site_id" in fields:
        await _assert_site(session, tender, fields["site_id"])
    changed = False
    for name, value in fields.items():
        if name not in SCOPE_FIELDS:  # pragma: no cover - el schema ja ho impedeix
            raise ValidationError(f"Camp no editable: {name}")
        if getattr(item, name) != value:
            setattr(item, name, value)
            changed = True
    if changed:
        await session.flush()
    return changed


async def delete_scope_item(session: AsyncSession, item: TenderScopeItem) -> None:
    """Esborra l'element; els enllacos amb cites se'n van, les cites es queden."""
    await session.delete(item)
    await session.flush()


async def scope_items_of(session: AsyncSession, tender_id: uuid.UUID) -> list[TenderScopeItem]:
    return list(
        await session.scalars(
            sa.select(TenderScopeItem)
            .where(TenderScopeItem.tender_id == tender_id)
            .order_by(TenderScopeItem.created_at, TenderScopeItem.id)
        )
    )


# --------------------------------------------------------------------------- #
# Territoris, proves i criteris d'un requisit                                   #
# --------------------------------------------------------------------------- #


def set_territory_match_mode(requirement: Requirement, mode: TerritoryMatchMode) -> bool:
    if requirement.territory_match_mode == mode:
        return False
    requirement.territory_match_mode = mode
    return True


def _normalise_text(value: str | None) -> str | None:
    """Espais normalitzats; majuscules i accents es conserven (son part del nom)."""
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def territory_identity(t: Any) -> tuple:
    """El que fa que dos territoris siguin el mateix: tipus, seu, sistema de codi,
    codi, nom i matis amb els espais normalitzats. Cap resolucio geografica: dos
    noms diferents son dos territoris, encara que siguin el mateix municipi."""
    get = (lambda k: t.get(k)) if isinstance(t, dict) else (lambda k: getattr(t, k))
    return (
        str(get("kind")),
        get("site_id") or None,
        _normalise_text(get("code_system")),
        _normalise_text(get("code")),
        _normalise_text(get("label")),
        _normalise_text(get("qualifier")),
    )


def dedupe_territories(territories: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """Els territoris sense repeticions, per identitat, en l'ordre d'arribada."""
    unique: dict[tuple, dict[str, Any]] = {}
    for territory in territories:
        unique.setdefault(territory_identity(territory), territory)
    return unique


def check_territory_completeness(
    completeness: TerritoryCompleteness | str, territory_count: int
) -> None:
    """Regles minimes (0050): `not_applicable` = 0 territoris; `complete` i
    `partial` = almenys 1; `unknown` = 0..N. Res mes: ni GIS ni comparacio."""
    value = str(completeness)
    if value == TerritoryCompleteness.NOT_APPLICABLE.value and territory_count:
        raise TerritoryCompletenessConflictError(
            "Un requisit sense dimensio territorial no pot tenir territoris."
        )
    if value in (TerritoryCompleteness.COMPLETE.value, TerritoryCompleteness.PARTIAL.value):
        if not territory_count:
            raise TerritoryCompletenessConflictError(
                f"`{value}` vol dir que hi ha territoris: en cal almenys un."
            )


async def set_territory_completeness(
    session: AsyncSession, requirement: Requirement, completeness: TerritoryCompleteness
) -> bool:
    """Canvia nomes la completesa; es comprova contra els territoris que hi ha."""
    await session.refresh(requirement, attribute_names=["territories"])
    check_territory_completeness(completeness, len(requirement.territories))
    if requirement.territory_completeness == completeness:
        return False
    requirement.territory_completeness = completeness
    return True


async def set_territories(
    session: AsyncSession,
    tender: Tender,
    requirement: Requirement,
    territories: list[dict[str, Any]],
) -> bool:
    """Substitueix els territoris del requisit. **Son un conjunt**: la mateixa
    identitat nomes hi es una vegada, l'ordre no compta i el mateix contingut no
    es cap canvi. Els textos es desen amb els espais normalitzats. Nomes
    s'emmagatzema: cap comparacio amb cap cobertura. La completesa declarada
    del requisit ha de quadrar amb el resultat."""
    for territory in territories:
        await _assert_site(session, tender, territory.get("site_id"))
    await session.refresh(requirement, attribute_names=["territories"])
    wanted = dedupe_territories(territories)
    check_territory_completeness(requirement.territory_completeness, len(wanted))
    current: dict[tuple, RequirementTerritory] = {}
    stale: list[RequirementTerritory] = []
    for row in requirement.territories:
        key = territory_identity(row)
        if key in current:
            stale.append(row)  # una repeticio anterior: s'elimina
        else:
            current[key] = row
    if set(current) == set(wanted) and not stale:
        return False
    for row in stale:
        requirement.territories.remove(row)
    for key, row in current.items():
        if key not in wanted:
            requirement.territories.remove(row)
    for key, territory in wanted.items():
        if key not in current:
            requirement.territories.append(
                RequirementTerritory(
                    requirement_id=requirement.id,
                    kind=territory["kind"],
                    label=_normalise_text(territory.get("label")),
                    code=_normalise_text(territory.get("code")),
                    code_system=_normalise_text(territory.get("code_system")),
                    qualifier=_normalise_text(territory.get("qualifier")),
                    site_id=territory.get("site_id"),
                )
            )
    await session.flush()
    return True


async def set_territory_semantics(
    session: AsyncSession,
    tender: Tender,
    requirement: Requirement,
    *,
    completeness: TerritoryCompleteness | None = None,
    territories: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Completesa i territoris alhora: la regla es comprova sobre l'estat FINAL
    (canviar a `not_applicable` i buidar la llista en una sola peticio es valid)
    i, si no quadra, no es toca res. Retorna que ha canviat."""
    await session.refresh(requirement, attribute_names=["territories"])
    final_completeness = (
        requirement.territory_completeness if completeness is None else completeness
    )
    final_count = (
        len(requirement.territories)
        if territories is None
        else len(dedupe_territories(territories))
    )
    check_territory_completeness(final_completeness, final_count)
    touched: list[str] = []
    if completeness is not None and requirement.territory_completeness != completeness:
        requirement.territory_completeness = completeness
        touched.append(f"completesa={completeness}")
    if territories is not None and await set_territories(session, tender, requirement, territories):
        touched.append(f"territoris={final_count}")
    return touched


def set_proof(requirement: Requirement, proof: list[Any] | None) -> bool:
    """`None` = encara no estructurat; `[]` = cap prova identificada. La forma
    es `proof_v1` i es valida abans d'escriure."""
    if proof is None:
        changed = requirement.proof_json is not None or requirement.proof_schema is not None
        requirement.proof_json = None
        requirement.proof_schema = None
        return changed
    items = validate_proof(proof)
    if requirement.proof_json == items and requirement.proof_schema == PROOF_SCHEMA_V1:
        return False
    requirement.proof_json = items
    requirement.proof_schema = PROOF_SCHEMA_V1
    return True


async def set_award_links(
    session: AsyncSession, tender: Tender, requirement: Requirement, criterion_ids: list[uuid.UUID]
) -> bool:
    """El requisit es la cara exigible d'aquests criteris. Ni punts ni text es copien."""
    wanted = list(dict.fromkeys(criterion_ids))
    for criterion_id in wanted:
        await _assert_criterion(session, tender, criterion_id)
    await session.refresh(requirement, attribute_names=["award_links"])
    current = {link.award_criterion_id: link for link in requirement.award_links}
    if set(current) == set(wanted):
        return False
    for criterion_id, link in current.items():
        if criterion_id not in wanted:
            requirement.award_links.remove(link)
    for criterion_id in wanted:
        if criterion_id not in current:
            requirement.award_links.append(
                RequirementAwardLink(requirement_id=requirement.id, award_criterion_id=criterion_id)
            )
    await session.flush()
    return True


# --------------------------------------------------------------------------- #
# Necessitats tecniques                                                         #
# --------------------------------------------------------------------------- #


def _check_need(fields: dict[str, Any]) -> None:
    mapped, unmapped = fields.get("capability_code"), fields.get("unmapped_capability")
    if (mapped is None) == (unmapped is None):
        raise NeedIncoherentError(
            "Cal una capacitat del vocabulari o un nom sense mapar, no cap ni totes dues."
        )
    comparator = str(fields["comparator"])
    if comparator == "present" and fields.get("value_json") is not None:
        raise NeedIncoherentError("`present` no porta valor.")
    if comparator != "present" and fields.get("value_json") is None:
        raise NeedIncoherentError(f"`{comparator}` necessita un valor.")
    if fields.get("value_json") is not None and fields.get("value_schema") is None:
        raise NeedIncoherentError("`value_json` necessita `value_schema`.")
    if fields.get("points") is not None and fields["points"] < 0:
        raise NeedIncoherentError("Els punts no poden ser negatius.")


async def load_need(
    session: AsyncSession, tender: Tender, need_id: uuid.UUID
) -> RequirementCapabilityNeed:
    need = await session.scalar(
        sa.select(RequirementCapabilityNeed).where(
            RequirementCapabilityNeed.id == need_id,
            RequirementCapabilityNeed.tender_id == tender.id,
        )
    )
    if need is None:
        raise NotFoundError("Necessitat no trobada.")
    return need


async def create_need(
    session: AsyncSession, tender: Tender, requirement: Requirement, fields: dict[str, Any]
) -> RequirementCapabilityNeed:
    _check_need(fields)
    await _assert_criterion(session, tender, fields.get("award_criterion_id"))
    need = RequirementCapabilityNeed(
        requirement_id=requirement.id,
        tender_id=tender.id,
        **{k: fields.get(k) for k in NEED_FIELDS},
    )
    session.add(need)
    await session.flush()
    return need


async def update_need(
    session: AsyncSession, tender: Tender, need: RequirementCapabilityNeed, fields: dict[str, Any]
) -> bool:
    merged = {k: fields[k] if k in fields else getattr(need, k) for k in NEED_FIELDS}
    _check_need(merged)
    if "award_criterion_id" in fields:
        await _assert_criterion(session, tender, fields["award_criterion_id"])
    changed = False
    for name, value in fields.items():
        if name not in NEED_FIELDS:  # pragma: no cover - el schema ja ho impedeix
            raise ValidationError(f"Camp no editable: {name}")
        if getattr(need, name) != value:
            setattr(need, name, value)
            changed = True
    if changed:
        await session.flush()
    return changed


async def delete_need(session: AsyncSession, need: RequirementCapabilityNeed) -> None:
    await session.delete(need)
    await session.flush()


# --------------------------------------------------------------------------- #
# Fets contractuals                                                             #
# --------------------------------------------------------------------------- #


async def load_fact(
    session: AsyncSession, tender: Tender, fact_id: uuid.UUID
) -> TenderContractFact:
    fact = await session.scalar(
        sa.select(TenderContractFact).where(
            TenderContractFact.id == fact_id, TenderContractFact.tender_id == tender.id
        )
    )
    if fact is None:
        raise NotFoundError("Fet contractual no trobat.")
    return fact


def _check_fact_blocks(fact_code: ContractFactCode | str, block_ids: list[uuid.UUID]) -> None:
    """Invariant de servei: `billing_terms` es nomes facturacio general."""
    if str(fact_code) == ContractFactCode.BILLING_TERMS.value and block_ids:
        raise BillingTermsBlockLinkError(
            "`billing_terms` es la facturacio general del contracte: no s'aplica a blocs. "
            "La d'un bloc viu a `tender_blocks.billing_terms_json`."
        )


async def _fact_block_ids(session: AsyncSession, fact: TenderContractFact) -> list[uuid.UUID]:
    return list(
        await session.scalars(
            sa.select(ContractFactBlock.block_id)
            .where(ContractFactBlock.fact_id == fact.id)
            .order_by(ContractFactBlock.created_at)
        )
    )


async def _assert_scope_free(
    session: AsyncSession,
    tender: Tender,
    *,
    fact_code: ContractFactCode | str,
    block_ids: list[uuid.UUID],
    except_id: uuid.UUID | None,
) -> None:
    """Invariant de servei: per a un mateix expedient, (codi + conjunt exacte de
    blocs) nomes pot existir una vegada. `billing_terms` sempre te el conjunt
    buit, aixi que n'hi ha com a maxim un per expedient."""
    wanted = frozenset(block_ids)
    query = sa.select(TenderContractFact.id).where(
        TenderContractFact.tender_id == tender.id,
        TenderContractFact.fact_code == fact_code,
    )
    if except_id is not None:
        query = query.where(TenderContractFact.id != except_id)
    siblings = list(await session.scalars(query))
    if not siblings:
        return
    blocks_by_fact = await fact_block_ids(session, siblings)
    for sibling in siblings:
        if frozenset(blocks_by_fact.get(sibling, [])) == wanted:
            raise ContractFactScopeConflictError(
                f"Ja hi ha un fet `{fact_code}` amb exactament aquest abast de blocs.",
                details={"conflicting_fact_id": str(sibling)},
            )


async def create_fact(
    session: AsyncSession,
    tender: Tender,
    *,
    fact_code: ContractFactCode,
    value_json: dict[str, Any],
    value_schema: str,
    block_ids: list[uuid.UUID],
) -> TenderContractFact:
    _check_fact_blocks(fact_code, block_ids)
    wanted = list(dict.fromkeys(block_ids))
    for block_id in wanted:
        await _assert_block(session, tender, block_id)
    await _assert_scope_free(session, tender, fact_code=fact_code, block_ids=wanted, except_id=None)
    fact = TenderContractFact(
        tender_id=tender.id, fact_code=fact_code, value_json=value_json, value_schema=value_schema
    )
    session.add(fact)
    await session.flush()
    for block_id in wanted:
        session.add(ContractFactBlock(fact_id=fact.id, block_id=block_id))
    await session.flush()
    return fact


async def update_fact(
    session: AsyncSession,
    tender: Tender,
    fact: TenderContractFact,
    *,
    fact_code: ContractFactCode | None = None,
    value_json: dict[str, Any] | None = None,
    value_schema: str | None = None,
    block_ids: list[uuid.UUID] | None = None,
) -> bool:
    """S'aplica nomes el que arriba. Les invariants (facturacio general sense
    blocs, un sol fet per codi + abast) es comproven sobre l'estat FINAL abans
    de tocar res: un conflicte no deixa cap modificacio parcial."""
    final_code = fact.fact_code if fact_code is None else fact_code
    current_blocks = await _fact_block_ids(session, fact)
    final_blocks = current_blocks if block_ids is None else list(dict.fromkeys(block_ids))
    _check_fact_blocks(final_code, final_blocks)
    for block_id in final_blocks:
        if block_id not in current_blocks:
            await _assert_block(session, tender, block_id)
    if final_code != fact.fact_code or set(final_blocks) != set(current_blocks):
        await _assert_scope_free(
            session, tender, fact_code=final_code, block_ids=final_blocks, except_id=fact.id
        )

    changed = False
    if final_code != fact.fact_code:
        fact.fact_code = final_code
        changed = True
    if value_json is not None and fact.value_json != value_json:
        fact.value_json = value_json
        changed = True
    if value_schema is not None and fact.value_schema != value_schema:
        fact.value_schema = value_schema
        changed = True
    if set(final_blocks) != set(current_blocks):
        await session.execute(
            sa.delete(ContractFactBlock).where(
                ContractFactBlock.fact_id == fact.id,
                ContractFactBlock.block_id.notin_(final_blocks) if final_blocks else sa.true(),
            )
        )
        for block_id in final_blocks:
            if block_id not in current_blocks:
                session.add(ContractFactBlock(fact_id=fact.id, block_id=block_id))
        changed = True
    if changed:
        await session.flush()
    return changed


async def delete_fact(session: AsyncSession, fact: TenderContractFact) -> None:
    await session.delete(fact)
    await session.flush()


async def facts_of(session: AsyncSession, tender_id: uuid.UUID) -> list[TenderContractFact]:
    return list(
        await session.scalars(
            sa.select(TenderContractFact)
            .where(TenderContractFact.tender_id == tender_id)
            .order_by(TenderContractFact.fact_code, TenderContractFact.created_at)
        )
    )


async def fact_block_ids(
    session: AsyncSession, fact_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    if not fact_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(ContractFactBlock.fact_id, ContractFactBlock.block_id)
            .where(ContractFactBlock.fact_id.in_(fact_ids))
            .order_by(ContractFactBlock.created_at)
        )
    ).all()
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for fact_id, block_id in rows:
        out.setdefault(fact_id, []).append(block_id)
    return out
