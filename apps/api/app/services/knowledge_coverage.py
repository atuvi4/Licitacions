"""Llegir i escriure la cobertura territorial d'una dada de coneixement.

Les arees pengen de la dada per `entity_type` + `entity_id`, sense FK (com
l'evidencia), aixi que aqui hi ha les tres coses que la base no pot fer sola:

* **llegir-la** per a la fotografia (`coverage_input`) i per a l'API
  (`coverage_payload`), sempre en l'ordre canonic;
* **escriure-la** com un tot (`apply_coverage`): una peticio que porta
  `coverage` substitueix completesa i arees. Nomes toca res si la representacio
  canonica canvia: el mateix payload es un no-op i no invalida cap verificacio.
  **Un canvi material de cobertura es un canvi material de la dada**: qui crida
  n'ha de tornar la verificacio a `pending_verification` com amb qualsevol
  altre camp (l'evidencia es conserva);
* **esborrar-la** quan la dada desapareix (`delete_coverage_for_entity`).

Tot en memoria fins al `flush`: `apply_coverage` no consulta la base, perque
una consulta faria autoflush i esborraria l'historial dels camps que
`require_evidence_if_verified` encara ha de llegir.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.coverage import (
    AreaIn,
    CoverageIn,
    area_identity,
    canonical_coverage,
    coverage_dict,
    dedupe_areas,
    normalise_label,
)
from app.models.enums import CoverageEntityType, VerificationStatus
from app.models.knowledge import CompanyOperatingFact, KnowledgeCoverageArea, PartnerRoleRow
from app.models.profile import CompanyService

ENTITY_TYPE_BY_MODEL: dict[type, CoverageEntityType] = {
    CompanyOperatingFact: CoverageEntityType.OPERATING_FACT,
    CompanyService: CoverageEntityType.SERVICE,
    PartnerRoleRow: CoverageEntityType.PARTNER_ROLE,
}


def supports_coverage(instance: Any) -> bool:
    return type(instance) in ENTITY_TYPE_BY_MODEL


def coverage_input(row: Any) -> CoverageIn:
    """La cobertura d'una fila, per a la fotografia. Les arees ja venen carregades."""
    return CoverageIn(
        completeness=row.coverage_completeness,
        areas=tuple(
            AreaIn(kind=area.kind, label=area.label, code=area.code, code_system=area.code_system)
            for area in row.coverage_areas
        ),
    )


def coverage_payload(row: Any) -> dict[str, Any]:
    """La cobertura tal com la llegeix l'API: sempre, tambe quan es neutra."""
    return coverage_dict(coverage_input(row))


def _from_payload(payload: Any) -> CoverageIn:
    """Normalitzada i sense repeticions, vingui d'on vingui (l'esquema ja ho fa)."""
    return CoverageIn(
        completeness=payload.completeness,
        areas=dedupe_areas(
            tuple(
                AreaIn(
                    kind=area.kind,
                    label=normalise_label(area.label),
                    code=area.code or None,
                    code_system=area.code_system or None,
                )
                for area in payload.areas
            )
        ),
    )


def asked_verified(instance: Any) -> bool:
    """Si aquesta peticio ha demanat `verified` (abans de cap flush)."""
    state = sa.inspect(instance)
    return not state.persistent or state.attrs.verification_status.history.has_changes()


async def _current_areas(session: AsyncSession, instance: Any) -> list[KnowledgeCoverageArea]:
    """Les arees que la dada te ara. Carregades amb la fila (selectin) o, per a
    una fila nova (pendent o acabada de fer flush), cap: sense cap consulta que
    faci autoflush, perque l'historial dels camps ha de sobreviure."""
    state = sa.inspect(instance)
    if not state.persistent or "coverage_areas" not in state.unloaded:
        return list(instance.coverage_areas)
    with session.no_autoflush:
        rows = await session.scalars(
            sa.select(KnowledgeCoverageArea).where(
                KnowledgeCoverageArea.entity_type == ENTITY_TYPE_BY_MODEL[type(instance)],
                KnowledgeCoverageArea.entity_id == instance.id,
            )
        )
    return list(rows)


async def apply_coverage(session: AsyncSession, instance: Any, payload: Any) -> bool:
    """Substitueix la cobertura de la dada. Cert si ha canviat materialment.

    Compara amb les arees que la fila ja te i deixa els canvis a la sessio per
    al flush de qui crida, sense cap autoflush pel mig. Les arees son un
    conjunt: nomes s'esborren les que ja no hi son i s'afegeixen les noves, per
    identitat, de manera que l'index unic de la base no veu mai la mateixa
    area dues vegades (ni en el mateix flush).
    """
    entity_type = ENTITY_TYPE_BY_MODEL[type(instance)]
    wanted = _from_payload(payload)
    current = await _current_areas(session, instance)
    present = CoverageIn(
        completeness=instance.coverage_completeness,
        areas=tuple(AreaIn(a.kind, a.label, a.code, a.code_system) for a in current),
    )
    if canonical_coverage(present) == canonical_coverage(wanted):
        return False
    if instance.id is None:
        instance.id = uuid.uuid4()
    instance.coverage_completeness = wanted.completeness
    kept: set[tuple[str, str, str, str]] = set()
    wanted_ids = {area_identity(area) for area in wanted.areas}
    for row in current:
        identity = area_identity(AreaIn(row.kind, row.label, row.code, row.code_system))
        if identity in wanted_ids and identity not in kept:
            kept.add(identity)
        else:
            await session.delete(row)
    for area in wanted.areas:
        if area_identity(area) in kept:
            continue
        session.add(
            KnowledgeCoverageArea(
                company_id=instance.company_id,
                entity_type=entity_type,
                entity_id=instance.id,
                kind=area.kind,
                label=area.label,
                code=area.code,
                code_system=area.code_system,
            )
        )
    return True


def unverify_for_coverage_change(instance: Any, *, asked: bool) -> bool:
    """Una cobertura nova sobre una dada verificada la torna a pendent, llevat
    que la mateixa peticio demani `verified` expressament (llavors ja passa per
    la regla d'evidencia). Cert si l'ha desverificada."""
    if asked or instance.verification_status != VerificationStatus.VERIFIED:
        return False
    instance.verification_status = VerificationStatus.PENDING_VERIFICATION
    return True


async def refresh_coverage(session: AsyncSession, instance: Any) -> None:
    """Torna a carregar les arees despres d'escriure-les (la relacio es de lectura)."""
    await session.refresh(instance, attribute_names=["coverage_areas"])


async def delete_coverage_for_entity(
    session: AsyncSession, entity_type: CoverageEntityType, entity_id: uuid.UUID
) -> None:
    await session.execute(
        sa.delete(KnowledgeCoverageArea).where(
            KnowledgeCoverageArea.entity_type == entity_type,
            KnowledgeCoverageArea.entity_id == entity_id,
        )
    )
