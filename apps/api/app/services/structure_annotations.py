"""Anotacions d'estructura que sobreviuen a una re-extraccio (0060, 3B-2 §4.3).

Les anotacions d'estructura d'un requisit extret (temporalitat, blocs,
territoris amb la seva completesa i mode, proves, necessitats, enllacos amb
criteris) pengen de la fila del requisit (`requirement_id`). Una run nova crea
files noves, i sense aquest pas les anotacions es quedarien a la run antiga.

`carry_over_annotations(session, old_run_id, new_run_id)` les copia als
requisits de la run nova amb el MATEIX `dedup_key` (unic dins d'una run i mai
reescrit; vegeu `manual_requirement.run_requirements_by_dedup_key`). Mateix
patro per als criteris (temporalitat, blocs, cites) per `dedup_key` del criteri.

Regles (el model de `review_reconciliations`):

* **cap escriptura sobre la run antiga**: nomes es llegeix;
* es creen files NOVES (enllacos, territoris, necessitats) amb la provenance de
  l'original (qui ho va dir no canvia perque el PDF s'hagi tornat a llegir);
* un valor que la run nova ja porta (no `unknown`) no es trepitja: queda com a
  `conflicts`, i guanya el de la run nova;
* un `dedup_key` que ja no hi es: `orphaned` (log
  `structure_annotations.orphaned`), i no es copia res;
* idempotent: una segona crida no crea cap fila nova.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RequirementTiming, TerritoryCompleteness, TerritoryMatchMode
from app.models.requirements import AwardCriterion, Requirement
from app.models.tender_scope import (
    RequirementAwardLink,
    RequirementCapabilityNeed,
    RequirementCapabilityNeedSourceRefLink,
    RequirementTerritory,
)
from app.models.tender_structure import (
    AwardCriterionBlock,
    AwardCriterionSourceRefLink,
    RequirementBlock,
)
from app.services import manual_requirement
from app.services import tender_scope as scope
from app.services.structure_proposals import need_key

logger = logging.getLogger(__name__)

#: Les columnes de provenance que viatgen amb una fila copiada.
_PROVENANCE = ("origin", "authority", "producer_run_id", "proposal_id", "created_by", "updated_by")


@dataclass(slots=True)
class CarryOverReport:
    old_run_id: uuid.UUID
    new_run_id: uuid.UUID
    #: `dedup_key` dels requisits (i criteris, prefix `criterion:`) amb anotacions
    #: que tenen parella a la run nova.
    carried: list[str] = field(default_factory=list)
    #: `dedup_key` amb anotacions que ja no tenen parella: no es copia res.
    orphaned: list[str] = field(default_factory=list)
    #: Valors que la run nova ja portava diferents: guanya la run nova.
    conflicts: list[str] = field(default_factory=list)
    #: Files noves creades (0 en una segona crida).
    rows_created: int = 0


def _provenance_of(row) -> dict:
    return {name: getattr(row, name) for name in _PROVENANCE}


def _need_fields(need: RequirementCapabilityNeed) -> dict:
    return {name: getattr(need, name) for name in scope.NEED_FIELDS}


def _need_identity(need: RequirementCapabilityNeed, criterion_key: str | None) -> tuple:
    return (
        need.capability_code,
        need.unmapped_capability,
        str(need.comparator),
        str(need.need_type),
        repr(need.value_json),
        need.value_schema,
        need.points,
        criterion_key,
    )


def _has_annotations(requirement: Requirement) -> bool:
    return bool(
        requirement.timing != RequirementTiming.UNKNOWN
        or requirement.territory_completeness != TerritoryCompleteness.UNKNOWN
        or requirement.territory_match_mode != TerritoryMatchMode.ANY
        or requirement.proof_json is not None
        or requirement.block_links
        or requirement.territories
        or requirement.capability_needs
        or requirement.award_links
    )


async def _criteria_by_key(session: AsyncSession, run_id: uuid.UUID) -> dict[str, AwardCriterion]:
    rows = await session.scalars(sa.select(AwardCriterion).where(AwardCriterion.run_id == run_id))
    return {criterion.dedup_key: criterion for criterion in rows}


async def carry_over_annotations(
    session: AsyncSession, old_run_id: uuid.UUID, new_run_id: uuid.UUID
) -> CarryOverReport:
    """Copia les anotacions de la run antiga a la nova per `dedup_key`. No fa commit."""
    report = CarryOverReport(old_run_id=old_run_id, new_run_id=new_run_id)
    if old_run_id == new_run_id:
        return report
    old_requirements = await manual_requirement.run_requirements_by_dedup_key(session, old_run_id)
    new_requirements = await manual_requirement.run_requirements_by_dedup_key(session, new_run_id)
    old_criteria = await _criteria_by_key(session, old_run_id)
    new_criteria = await _criteria_by_key(session, new_run_id)
    criterion_key_by_old_id = {c.id: key for key, c in old_criteria.items()}
    new_criterion_by_key = {key: c.id for key, c in new_criteria.items()}
    criterion_key_by_new_id = {c.id: key for key, c in new_criteria.items()}

    def mapped_criterion(old_id: uuid.UUID | None) -> uuid.UUID | None:
        if old_id is None:
            return None
        return new_criterion_by_key.get(criterion_key_by_old_id.get(old_id, ""))

    for key, old in old_requirements.items():
        if not _has_annotations(old):
            continue
        new = new_requirements.get(key)
        if new is None:
            report.orphaned.append(key)
            continue
        report.carried.append(key)
        await session.refresh(
            new, attribute_names=["block_links", "territories", "capability_needs", "award_links"]
        )

        # Valors de la fila: nomes si la run nova no en diu res.
        for name, default in (
            ("timing", RequirementTiming.UNKNOWN),
            ("territory_match_mode", TerritoryMatchMode.ANY),
        ):
            old_value, new_value = getattr(old, name), getattr(new, name)
            if old_value == default or old_value == new_value:
                continue
            if new_value == default:
                setattr(new, name, old_value)
            else:
                report.conflicts.append(f"{key}:{name}")
        if old.proof_json is not None:
            if new.proof_json is None:
                new.proof_json, new.proof_schema = old.proof_json, old.proof_schema
            elif new.proof_json != old.proof_json:
                report.conflicts.append(f"{key}:proof")

        # Blocs: unio.
        have_blocks = {link.block_id for link in new.block_links}
        for link in old.block_links:
            if link.block_id not in have_blocks:
                new.block_links.append(
                    RequirementBlock(requirement_id=new.id, block_id=link.block_id)
                )
                report.rows_created += 1

        # Territoris: unio per identitat, amb la provenance de l'original.
        have_territories = {scope.territory_identity(t) for t in new.territories}
        for territory in old.territories:
            if scope.territory_identity(territory) in have_territories:
                continue
            new.territories.append(
                RequirementTerritory(
                    requirement_id=new.id,
                    kind=territory.kind,
                    label=territory.label,
                    code=territory.code,
                    code_system=territory.code_system,
                    qualifier=territory.qualifier,
                    site_id=territory.site_id,
                    **_provenance_of(territory),
                )
            )
            report.rows_created += 1
        if old.territory_completeness != TerritoryCompleteness.UNKNOWN:
            if new.territory_completeness == TerritoryCompleteness.UNKNOWN:
                new.territory_completeness = old.territory_completeness
            elif new.territory_completeness != old.territory_completeness:
                report.conflicts.append(f"{key}:territory_completeness")

        # Enllacos amb criteris: el criteri equivalent de la run nova.
        have_links = {link.award_criterion_id for link in new.award_links}
        for link in old.award_links:
            target = mapped_criterion(link.award_criterion_id)
            if target is None:
                report.orphaned.append(
                    f"{key}->criterion:{criterion_key_by_old_id.get(link.award_criterion_id)}"
                )
                continue
            if target not in have_links:
                new.award_links.append(
                    RequirementAwardLink(
                        requirement_id=new.id, award_criterion_id=target, **_provenance_of(link)
                    )
                )
                have_links.add(target)
                report.rows_created += 1

        # Necessitats, per clau natural (`need_key`: dedup|capacitat|tipus|comparador):
        # sense parella a la run nova es copia (amb les seves cites); amb parella
        # identica ja hi es (idempotent); amb parella diferent guanya la nova.
        # La identitat es compara amb el criteri MAPEJAT a la run nova (None si
        # el criteri ja no hi es): aixi la copia d'un criteri orfe es troba a la
        # segona passada.
        new_needs = {
            need_key(new, _need_fields(n)): (
                n,
                _need_identity(n, criterion_key_by_new_id.get(n.award_criterion_id)),
            )
            for n in new.capability_needs
        }
        for need in old.capability_needs:
            criterion_key = criterion_key_by_old_id.get(need.award_criterion_id)
            target = mapped_criterion(need.award_criterion_id)
            if need.award_criterion_id is not None and target is None:
                report.orphaned.append(f"{key}->criterion:{criterion_key}")
            natural = need_key(old, _need_fields(need))
            identity = _need_identity(need, criterion_key_by_new_id.get(target))
            if natural in new_needs:
                if new_needs[natural][1] != identity:
                    report.conflicts.append(f"{key}:need:{natural}")
                continue
            copy = RequirementCapabilityNeed(
                requirement_id=new.id,
                tender_id=need.tender_id,
                award_criterion_id=target,
                **{
                    name: getattr(need, name)
                    for name in scope.NEED_FIELDS
                    if name != "award_criterion_id"
                },
                **_provenance_of(need),
            )
            session.add(copy)
            await session.flush()
            new_needs[natural] = (copy, identity)
            report.rows_created += 1
            links = await session.scalars(
                sa.select(RequirementCapabilityNeedSourceRefLink).where(
                    RequirementCapabilityNeedSourceRefLink.need_id == need.id
                )
            )
            for link in links:
                session.add(
                    RequirementCapabilityNeedSourceRefLink(
                        need_id=copy.id,
                        source_ref_id=link.source_ref_id,
                        created_by=link.created_by,
                        producer_run_id=link.producer_run_id,
                    )
                )
                report.rows_created += 1

    # Criteris: temporalitat, blocs i cites.
    for key, old in old_criteria.items():
        old_links = list(
            await session.scalars(
                sa.select(AwardCriterionSourceRefLink).where(
                    AwardCriterionSourceRefLink.award_criterion_id == old.id
                )
            )
        )
        if old.timing == RequirementTiming.UNKNOWN and not old.block_links and not old_links:
            continue
        new = new_criteria.get(key)
        if new is None:
            report.orphaned.append(f"criterion:{key}")
            continue
        report.carried.append(f"criterion:{key}")
        await session.refresh(new, attribute_names=["block_links"])
        if old.timing != RequirementTiming.UNKNOWN and old.timing != new.timing:
            if new.timing == RequirementTiming.UNKNOWN:
                new.timing = old.timing
            else:
                report.conflicts.append(f"criterion:{key}:timing")
        have_blocks = {link.block_id for link in new.block_links}
        for link in old.block_links:
            if link.block_id not in have_blocks:
                new.block_links.append(
                    AwardCriterionBlock(award_criterion_id=new.id, block_id=link.block_id)
                )
                report.rows_created += 1
        have_refs = set(
            await session.scalars(
                sa.select(AwardCriterionSourceRefLink.source_ref_id).where(
                    AwardCriterionSourceRefLink.award_criterion_id == new.id
                )
            )
        )
        for link in old_links:
            if link.source_ref_id not in have_refs:
                session.add(
                    AwardCriterionSourceRefLink(
                        award_criterion_id=new.id,
                        source_ref_id=link.source_ref_id,
                        created_by=link.created_by,
                        producer_run_id=link.producer_run_id,
                    )
                )
                report.rows_created += 1

    await session.flush()
    # Una sola entrada per fet (un criteri orfe en surt per l'enllac i per la necessitat).
    report.orphaned = list(dict.fromkeys(report.orphaned))
    report.conflicts = list(dict.fromkeys(report.conflicts))
    if report.carried:
        logger.info(
            "structure_annotations.carried",
            extra={
                "old_run_id": str(old_run_id),
                "new_run_id": str(new_run_id),
                "carried": len(report.carried),
                "rows_created": report.rows_created,
                "conflicts": report.conflicts[:50],
            },
        )
    if report.orphaned:
        logger.warning(
            "structure_annotations.orphaned",
            extra={
                "old_run_id": str(old_run_id),
                "new_run_id": str(new_run_id),
                "orphaned": report.orphaned[:50],
            },
        )
    return report
