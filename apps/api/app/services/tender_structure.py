"""Estructura del plec (0048): escriure blocs, seus, metadades del contracte i a
que aplica cada requisit o criteri.

Cap politica d'acces viu aqui: qui pot escriure ho decideix `manual_tender`
(dades de la licitacio) i `manual_requirement` (requisits i criteris), exactament
com a la resta de dades estructurades d'una licitacio oberta a ma. Aqui nomes
hi ha les regles del model:

* un bloc o una seu tenen un `code` unic dins de la licitacio;
* el lot d'un bloc, si n'hi ha, es del mateix expedient;
* un requisit o criteri nomes s'enllaça a blocs del seu propi expedient;
* els punts d'un criteri NO es reparteixen entre els blocs que enllaça;
* una cita es de la mateixa licitacio que el bloc o la seu que la fa servir.

Res d'aixo crea cap element d'abast, cap necessitat de capacitat, cap fet de
contracte ni cap resultat d'analisi: aixo es 0049/0050.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.enums import RequirementTiming
from app.models.identity import User
from app.models.provenance import TenderSourceRef
from app.models.requirements import AwardCriterion, Requirement
from app.models.tender_structure import (
    AwardCriterionBlock,
    RequirementBlock,
    TenderBlock,
    TenderBlockSourceRefLink,
    TenderSite,
    TenderSiteSourceRefLink,
)
from app.models.tenders import Tender, TenderLot

#: Les dades estructurals del contracte que es corregeixen per aqui. Fora de la
#: fotografia de `tender_lifecycle` a proposit: no son el que publica l'organ
#: al feed, i afegir-les-hi faria «canviar» totes les licitacions existents.
EDITABLE_FIELDS = (
    "duration_months",
    "extension_months",
    "regulated_harmonised",
    "planned_start_date",
)
BLOCK_FIELDS = (
    "code",
    "name",
    "tender_lot_id",
    "base_price_ex_vat",
    "price_points",
    "separately_awardable",
    "billing_terms_json",
    "billing_schema",
)
SITE_FIELDS = (
    "code",
    "name",
    "role",
    "is_critical",
    "address",
    "municipality_code",
    "municipality_code_system",
    "latitude",
    "longitude",
)


class BlockCodeTakenError(ConflictError):
    code = "tender_block_code_taken"


class SiteCodeTakenError(ConflictError):
    code = "tender_site_code_taken"


class LotTenderMismatchError(ValidationError):
    code = "lot_tender_mismatch"


class BlockTenderMismatchError(ValidationError):
    """Un bloc d'una altra licitacio: una FK sola no ho impedeix."""

    code = "block_tender_mismatch"


class SourceRefTenderMismatchError(ValidationError):
    code = "source_ref_tender_mismatch"


# --------------------------------------------------------------------------- #
# Metadades del contracte                                                       #
# --------------------------------------------------------------------------- #


def update_structure(tender: Tender, fields: dict[str, Any]) -> list[str]:
    """Aplica els camps demanats. Retorna els que han canviat de valor."""
    changed: list[str] = []
    for name, value in fields.items():
        if name not in EDITABLE_FIELDS:  # pragma: no cover - el schema ja ho impedeix
            raise ValidationError(f"Camp no editable: {name}")
        if getattr(tender, name) != value:
            setattr(tender, name, value)
            changed.append(name)
    return changed


# --------------------------------------------------------------------------- #
# Blocs                                                                         #
# --------------------------------------------------------------------------- #


async def _assert_lot_of_tender(
    session: AsyncSession, tender_id: uuid.UUID, lot_id: uuid.UUID | None
) -> None:
    if lot_id is None:
        return
    lot = await session.get(TenderLot, lot_id)
    if lot is None or lot.tender_id != tender_id:
        raise LotTenderMismatchError("Aquest lot no es d'aquesta licitacio.")


async def _assert_block_code_free(
    session: AsyncSession, tender_id: uuid.UUID, code: str, *, except_id: uuid.UUID | None
) -> None:
    query = sa.select(TenderBlock.id).where(
        TenderBlock.tender_id == tender_id, TenderBlock.code == code
    )
    if except_id is not None:
        query = query.where(TenderBlock.id != except_id)
    if await session.scalar(query) is not None:
        raise BlockCodeTakenError(f"Ja hi ha un bloc amb el codi {code!r} en aquesta licitacio.")


async def load_block(session: AsyncSession, tender: Tender, block_id: uuid.UUID) -> TenderBlock:
    block = await session.scalar(
        sa.select(TenderBlock).where(TenderBlock.id == block_id, TenderBlock.tender_id == tender.id)
    )
    if block is None:
        raise NotFoundError("Bloc no trobat.")
    return block


async def create_block(
    session: AsyncSession, tender: Tender, fields: dict[str, Any]
) -> TenderBlock:
    await _assert_lot_of_tender(session, tender.id, fields.get("tender_lot_id"))
    await _assert_block_code_free(session, tender.id, fields["code"], except_id=None)
    block = TenderBlock(tender_id=tender.id, **{k: fields.get(k) for k in BLOCK_FIELDS})
    session.add(block)
    await session.flush()
    return block


async def update_block(
    session: AsyncSession, tender: Tender, block: TenderBlock, fields: dict[str, Any]
) -> bool:
    """S'aplica nomes el que arriba. Cert si algun valor ha canviat."""
    if "tender_lot_id" in fields:
        await _assert_lot_of_tender(session, tender.id, fields["tender_lot_id"])
    if "code" in fields and fields["code"] != block.code:
        await _assert_block_code_free(session, tender.id, fields["code"], except_id=block.id)
    changed = False
    for name, value in fields.items():
        if name not in BLOCK_FIELDS:  # pragma: no cover - el schema ja ho impedeix
            raise ValidationError(f"Camp no editable: {name}")
        if getattr(block, name) != value:
            setattr(block, name, value)
            changed = True
    if changed:
        await session.flush()
    return changed


async def delete_block(session: AsyncSession, block: TenderBlock) -> None:
    """Esborra el bloc. Els enllacos amb requisits, criteris i cites se'n van
    amb ell (CASCADE); les cites en si es queden."""
    await session.delete(block)
    await session.flush()


# --------------------------------------------------------------------------- #
# Seus                                                                          #
# --------------------------------------------------------------------------- #


async def _assert_site_code_free(
    session: AsyncSession, tender_id: uuid.UUID, code: str, *, except_id: uuid.UUID | None
) -> None:
    query = sa.select(TenderSite.id).where(
        TenderSite.tender_id == tender_id, TenderSite.code == code
    )
    if except_id is not None:
        query = query.where(TenderSite.id != except_id)
    if await session.scalar(query) is not None:
        raise SiteCodeTakenError(f"Ja hi ha una seu amb el codi {code!r} en aquesta licitacio.")


async def load_site(session: AsyncSession, tender: Tender, site_id: uuid.UUID) -> TenderSite:
    site = await session.scalar(
        sa.select(TenderSite).where(TenderSite.id == site_id, TenderSite.tender_id == tender.id)
    )
    if site is None:
        raise NotFoundError("Seu no trobada.")
    return site


async def create_site(session: AsyncSession, tender: Tender, fields: dict[str, Any]) -> TenderSite:
    await _assert_site_code_free(session, tender.id, fields["code"], except_id=None)
    site = TenderSite(tender_id=tender.id, **{k: fields.get(k) for k in SITE_FIELDS})
    if site.is_critical is None:
        site.is_critical = False
    session.add(site)
    await session.flush()
    return site


async def update_site(
    session: AsyncSession, tender: Tender, site: TenderSite, fields: dict[str, Any]
) -> bool:
    if "code" in fields and fields["code"] != site.code:
        await _assert_site_code_free(session, tender.id, fields["code"], except_id=site.id)
    changed = False
    for name, value in fields.items():
        if name not in SITE_FIELDS:  # pragma: no cover - el schema ja ho impedeix
            raise ValidationError(f"Camp no editable: {name}")
        if name == "is_critical" and value is None:
            continue
        if getattr(site, name) != value:
            setattr(site, name, value)
            changed = True
    if changed:
        await session.flush()
    return changed


async def delete_site(session: AsyncSession, site: TenderSite) -> None:
    await session.delete(site)
    await session.flush()


# --------------------------------------------------------------------------- #
# Requisits i criteris: temporalitat i blocs                                    #
# --------------------------------------------------------------------------- #


async def load_requirement(
    session: AsyncSession, tender: Tender, requirement_id: uuid.UUID
) -> Requirement:
    """Qualsevol requisit de la licitacio, extret o manual: la temporalitat i
    els blocs son anotacions d'estructura, no una reescriptura del que va
    produir una run."""
    requirement = await session.scalar(
        sa.select(Requirement).where(
            Requirement.id == requirement_id, Requirement.tender_id == tender.id
        )
    )
    if requirement is None:
        raise NotFoundError("Requisit no trobat.")
    return requirement


async def load_criterion(
    session: AsyncSession, tender: Tender, criterion_id: uuid.UUID
) -> AwardCriterion:
    criterion = await session.scalar(
        sa.select(AwardCriterion).where(
            AwardCriterion.id == criterion_id, AwardCriterion.tender_id == tender.id
        )
    )
    if criterion is None:
        raise NotFoundError("Criteri no trobat.")
    return criterion


def set_timing(entity: Requirement | AwardCriterion, timing: RequirementTiming) -> bool:
    if entity.timing == timing:
        return False
    entity.timing = timing
    return True


async def _blocks_of_tender(
    session: AsyncSession, tender: Tender, block_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Els identificadors demanats, sense repeticions, tots de la licitacio."""
    wanted = list(dict.fromkeys(block_ids))
    if not wanted:
        return []
    found = set(
        await session.scalars(
            sa.select(TenderBlock.id).where(
                TenderBlock.id.in_(wanted), TenderBlock.tender_id == tender.id
            )
        )
    )
    missing = [block_id for block_id in wanted if block_id not in found]
    if missing:
        raise BlockTenderMismatchError(
            "Algun bloc no es d'aquesta licitacio.",
            details={"block_ids": [str(block_id) for block_id in missing]},
        )
    return wanted


async def set_requirement_blocks(
    session: AsyncSession, tender: Tender, requirement: Requirement, block_ids: list[uuid.UUID]
) -> bool:
    """Substitueix el conjunt de blocs. Buit = a tot el contracte. Cert si canvia."""
    wanted = await _blocks_of_tender(session, tender, block_ids)
    await session.refresh(requirement, attribute_names=["block_links"])
    current = {link.block_id: link for link in requirement.block_links}
    if set(current) == set(wanted):
        return False
    for block_id, link in current.items():
        if block_id not in wanted:
            requirement.block_links.remove(link)
    for block_id in wanted:
        if block_id not in current:
            requirement.block_links.append(
                RequirementBlock(requirement_id=requirement.id, block_id=block_id)
            )
    await session.flush()
    return True


async def set_criterion_blocks(
    session: AsyncSession, tender: Tender, criterion: AwardCriterion, block_ids: list[uuid.UUID]
) -> bool:
    """Igual que per a un requisit. **Els punts no es toquen**: un criteri de 6
    punts enllacat a dos blocs continua valent 6, no 3 + 3."""
    wanted = await _blocks_of_tender(session, tender, block_ids)
    await session.refresh(criterion, attribute_names=["block_links"])
    current = {link.block_id: link for link in criterion.block_links}
    if set(current) == set(wanted):
        return False
    for block_id, link in current.items():
        if block_id not in wanted:
            criterion.block_links.remove(link)
    for block_id in wanted:
        if block_id not in current:
            criterion.block_links.append(
                AwardCriterionBlock(award_criterion_id=criterion.id, block_id=block_id)
            )
    await session.flush()
    return True


# --------------------------------------------------------------------------- #
# Cites de blocs i seus: el patro de `manual_requirement`                       #
# --------------------------------------------------------------------------- #

#: (model d'enllac, columna que apunta a l'entitat)
_LINKS = {
    TenderBlock: (TenderBlockSourceRefLink, TenderBlockSourceRefLink.block_id),
    TenderSite: (TenderSiteSourceRefLink, TenderSiteSourceRefLink.site_id),
}


async def link_source_ref(
    session: AsyncSession,
    *,
    entity: TenderBlock | TenderSite,
    source_ref_id: uuid.UUID,
    user: User,
) -> Any:
    """Enllaça una cita existent. Ha de ser de la MATEIXA licitacio."""
    ref = await session.get(TenderSourceRef, source_ref_id)
    if ref is None:
        raise NotFoundError("Cita no trobada.")
    if ref.tender_id != entity.tender_id:
        raise SourceRefTenderMismatchError("Aquesta cita es d'una altra licitacio.")
    link_model, column = _LINKS[type(entity)]
    existing = await session.scalar(
        sa.select(link_model).where(column == entity.id, link_model.source_ref_id == ref.id)
    )
    if existing is not None:
        return existing
    link = link_model(source_ref_id=ref.id, created_by=user.id)
    setattr(link, column.key, entity.id)
    session.add(link)
    await session.flush()
    return link


async def unlink_source_ref(
    session: AsyncSession, *, entity: TenderBlock | TenderSite, source_ref_id: uuid.UUID
) -> bool:
    """Treu l'enllac. La cita no s'esborra."""
    link_model, column = _LINKS[type(entity)]
    link = await session.scalar(
        sa.select(link_model).where(column == entity.id, link_model.source_ref_id == source_ref_id)
    )
    if link is None:
        return False
    await session.delete(link)
    await session.flush()
    return True


async def source_refs_of(
    session: AsyncSession, model: type[TenderBlock] | type[TenderSite], ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[TenderSourceRef]]:
    """Les cites de cada bloc o seu, en una sola consulta."""
    if not ids:
        return {}
    link_model, column = _LINKS[model]
    rows = (
        await session.execute(
            sa.select(column, TenderSourceRef)
            .join(TenderSourceRef, TenderSourceRef.id == link_model.source_ref_id)
            .where(column.in_(ids))
            .order_by(link_model.created_at)
        )
    ).all()
    result: dict[uuid.UUID, list[TenderSourceRef]] = {}
    for entity_id, ref in rows:
        result.setdefault(entity_id, []).append(ref)
    return result
