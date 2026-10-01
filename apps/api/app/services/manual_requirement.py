"""Requisits escrits per una persona, i el conjunt EFECTIU (S3-S5).

Dues idees sostenen tot el modul.

**Un requisit manual no te run, i no se n'hi inventa cap.** Una
`RequirementExtractionRun` modela una execucio d'analisi amb el seu proveidor,
el seu model i el seu corpus d'entrada. Una persona que escriu un requisit no
en te res d'aixo, i omplir-ho —`provider="manual"`, un `input_fingerprint`
fabricat— faria que tot el que s'hi recolza digues una cosa que no ha passat
(D-09). Per aixo `run_id` es NULL i `origin` ho explica.

**El conjunt efectiu es la unio, i es una sola porta.** Els requisits vigents
d'una licitacio son els de la run extreta VIGENT mes els manuals no arxivats.
Ni tots els extrets historics —els d'una run vella ja no valen— ni els manuals
arxivats. Qui necessiti "els requisits d'ara" ha de passar per
`effective_requirements` i no reconstruir la unio pel seu compte: fer-ho dues
vegades acabaria amb dues respostes diferents a la mateixa pregunta.

**Que es manual no vol dir que estigui verificat.** Un requisit escrit a ma neix
`unreviewed`, i citar-ne la font tampoc no el converteix en `verified`: una cita
diu d'on surt l'afirmacio, no que algu l'hagi comprovada.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.enums import (
    ExtractionConfidence,
    MembershipRole,
    RequirementCategory,
    RequirementModality,
    RequirementObligation,
    RequirementOperator,
    RequirementOrigin,
    RequirementScope,
    ReviewStatus,
)
from app.models.identity import User
from app.models.provenance import RequirementSourceRefLink, TenderSourceRef
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.tenders import Tender, TenderLot

#: Qui pot escriure requisits. Es feina TECNICA, no una decisio economica: per
#: aixo `technical` hi es, igual que a les solucions tecniques, i no es
#: reutilitzen els rols de decisio, que l'exclourien.
REQUIREMENT_EDIT_ROLES = frozenset(
    {
        MembershipRole.OWNER,
        MembershipRole.ADMIN,
        MembershipRole.BID_MANAGER,
        MembershipRole.TECHNICAL,
    }
)

#: Els camps que una PERSONA pot definir. Deliberadament no hi son els que
#: nomes tenen sentit sortint d'un extractor —`confidence`, `dedup_key`,
#: `source_chunk_index`, `conflict_group`, `manual_override_json`—: deixar-los
#: escriure voldria dir deixar que algu digues "el model n'esta molt segur"
#: d'una cosa que ha escrit ell.
EDITABLE_FIELDS = (
    "title",
    "description",
    "category",
    "obligation",
    "operator",
    "scope",
    "modality",
    "value_json",
    "tender_lot_id",
    "alternative_group",
    "equivalence_allowed",
    "condition_note",
)


class RequirementNotManualError(ValidationError):
    """Nomes s'editen els requisits escrits a ma."""

    code = "requirement_not_manual"


class RequirementArchivedError(ConflictError):
    """Un requisit arxivat no s'edita: primer caldria desarxivar-lo."""

    code = "requirement_archived"


class SourceRefTenderMismatchError(ValidationError):
    """La cita es d'una altra licitacio."""

    code = "source_ref_tender_mismatch"


class LotTenderMismatchError(ValidationError):
    """El lot es d'una altra licitacio."""

    code = "lot_tender_mismatch"


def assert_can_edit(role: MembershipRole) -> None:
    if role not in REQUIREMENT_EDIT_ROLES:
        raise NotFoundError("No es poden escriure requisits amb aquest rol.")


def manual_dedup_key(tender_id: uuid.UUID, fields: dict[str, Any]) -> str:
    """Clau canonica d'un requisit manual.

    Es calcula pero **no s'imposa**: `UNIQUE(run_id, dedup_key)` no dispara amb
    `run_id` NULL, i aixo es correcte. Que dues persones escriguin el mateix
    requisit es cosa seva, i fusionar-los seria decidir-ho per elles; el que si
    que permet tenir-la es poder-ho detectar despres sense haver-hi de tornar.
    """
    parts = [
        str(tender_id),
        str(fields.get("category") or ""),
        str(fields.get("operator") or ""),
        str(fields.get("scope") or ""),
        " ".join((fields.get("title") or "").split()).casefold(),
        str(fields.get("value_json") or ""),
    ]
    return hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()[:64]


# --------------------------------------------------------------------------- #
# El conjunt efectiu                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EffectiveRequirements:
    """Els requisits vigents d'una licitacio, amb les dues fonts separades.

    Van separades a proposit: fondre-les faria semblar que els manuals venen de
    l'ultima analisi, i llavors caducarien quan es reextregues un PDF. Una
    afirmacio humana no caduca perque un document s'hagi tornat a llegir.
    """

    extracted: tuple[Requirement, ...]
    manual: tuple[Requirement, ...]
    run: RequirementExtractionRun | None

    @property
    def all(self) -> tuple[Requirement, ...]:
        return (*self.extracted, *self.manual)


def manual_filter(tender_id: uuid.UUID):
    """Els manuals VIUS d'aquesta licitacio. Un sol lloc ho defineix."""
    return sa.and_(
        Requirement.tender_id == tender_id,
        Requirement.origin == RequirementOrigin.MANUAL,
        Requirement.archived_at.is_(None),
    )


async def effective_requirements(
    session: AsyncSession, tender_id: uuid.UUID
) -> EffectiveRequirements:
    """La porta unica als requisits vigents.

    Run extreta VIGENT + manuals no arxivats. No els extrets historics: els
    d'una run que ja no es vigent parlen d'uns documents que ja no son els que
    es van analitzar.
    """
    run = await session.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.tender_id == tender_id,
            RequirementExtractionRun.is_current.is_(True),
        )
    )
    extracted: list[Requirement] = []
    if run is not None:
        extracted = list(
            (
                await session.scalars(
                    sa.select(Requirement)
                    .where(Requirement.run_id == run.id)
                    .order_by(Requirement.created_at, Requirement.id)
                )
            ).all()
        )
    manual = list(
        (
            await session.scalars(
                sa.select(Requirement)
                .where(manual_filter(tender_id))
                .order_by(Requirement.created_at, Requirement.id)
            )
        ).all()
    )
    return EffectiveRequirements(
        extracted=tuple(extracted), manual=tuple(manual), run=run
    )


async def effective_requirement_ids(
    session: AsyncSession, tender_id: uuid.UUID
) -> set[uuid.UUID]:
    """Nomes els identificadors, per a qui ha de comprovar pertinenca."""
    effective = await effective_requirements(session, tender_id)
    return {requirement.id for requirement in effective.all}


# --------------------------------------------------------------------------- #
# Escriptura                                                                    #
# --------------------------------------------------------------------------- #


async def _assert_lot_of_tender(
    session: AsyncSession, tender_id: uuid.UUID, lot_id: uuid.UUID | None
) -> None:
    if lot_id is None:
        return
    lot = await session.get(TenderLot, lot_id)
    if lot is None or lot.tender_id != tender_id:
        raise LotTenderMismatchError("Aquest lot no es d'aquesta licitacio.")


async def load_manual_requirement(
    session: AsyncSession, tender: Tender, requirement_id: uuid.UUID
) -> Requirement:
    """El requisit a editar, si es d'aquesta licitacio i l'ha escrit algu.

    Un requisit EXTRET no s'edita per aqui i no es cap oblit: seria reescriure
    la sortida d'una execucio concreta, i llavors la run ja no diria el que va
    produir. Els extrets tenen el seu propi cami, que es la revisio.
    """
    requirement = (
        await session.execute(
            sa.select(Requirement).where(
                Requirement.id == requirement_id, Requirement.tender_id == tender.id
            )
        )
    ).scalars().one_or_none()
    if requirement is None:
        raise NotFoundError("Requisit no trobat.")
    if requirement.origin != RequirementOrigin.MANUAL:
        raise RequirementNotManualError("Aquest requisit no s'ha escrit a ma.")
    return requirement


async def create_manual_requirement(
    session: AsyncSession,
    *,
    tender: Tender,
    user: User,
    fields: dict[str, Any],
) -> Requirement:
    """Crea un requisit escrit per una persona.

    El servidor fixa `origin`, `run_id`, `archived_at` i els actors: son fets
    sobre com ha nascut la fila, no dades que el client pugui declarar.

    Neix `unreviewed` a proposit. Que l'hagi escrit una persona no vol dir que
    estigui comprovat, i `verified` es una afirmacio que ha de fer algu
    explicitament.

    `confidence` es guarda al minim NOMES perque la columna es NOT NULL i ve de
    l'epoca en que tot requisit sortia d'un model. No vol dir "el model n'esta
    poc segur": no hi ha cap model. El read-model no l'exposa per als manuals, i
    la interficie n'ensenya l'atribucio humana.
    """
    await _assert_lot_of_tender(session, tender.id, fields.get("tender_lot_id"))

    requirement = Requirement(
        tender_id=tender.id,
        run_id=None,
        origin=RequirementOrigin.MANUAL,
        archived_at=None,
        review_status=ReviewStatus.UNREVIEWED,
        confidence=ExtractionConfidence.LOW,
        dedup_key=manual_dedup_key(tender.id, fields),
        created_by=user.id,
        updated_by=user.id,
        **{name: fields[name] for name in EDITABLE_FIELDS if name in fields},
    )
    session.add(requirement)
    await session.flush()
    return requirement


async def update_manual_requirement(
    session: AsyncSession,
    *,
    tender: Tender,
    requirement: Requirement,
    user: User,
    fields: dict[str, Any],
) -> bool:
    """Aplica els camps que arriben. Retorna si alguna cosa ha canviat de debo.

    Un PATCH que no canvia res no es un canvi: no mou l'autor, no deixa
    auditoria i no ha de fer caducar l'abast tecnic. Aixo ultim es el que
    importa: si cada desat repetit encengués `technical_scope_stale`, el senyal
    es gastaria.
    """
    if requirement.archived_at is not None:
        raise RequirementArchivedError("Aquest requisit esta arxivat.")
    if "tender_lot_id" in fields:
        await _assert_lot_of_tender(session, tender.id, fields["tender_lot_id"])

    changed = False
    for name in EDITABLE_FIELDS:
        if name not in fields:
            continue
        if getattr(requirement, name) != fields[name]:
            setattr(requirement, name, fields[name])
            changed = True

    if not changed:
        return False

    requirement.updated_by = user.id
    requirement.dedup_key = manual_dedup_key(
        tender.id,
        {name: getattr(requirement, name) for name in EDITABLE_FIELDS},
    )
    await session.flush()
    return True


async def archive_manual_requirement(
    session: AsyncSession, *, requirement: Requirement, user: User
) -> bool:
    """El treu del conjunt efectiu sense esborrar-lo.

    Idempotent: arxivar el que ja esta arxivat no mou la data. No hi ha cap
    esborrat dur, i els seus enllacos de provinenca es conserven: arxivar vol
    dir "ja no compta", no "no va existir".
    """
    if requirement.archived_at is not None:
        return False
    requirement.archived_at = datetime.now(UTC)
    requirement.updated_by = user.id
    await session.flush()
    return True


# --------------------------------------------------------------------------- #
# Provinenca                                                                    #
# --------------------------------------------------------------------------- #


async def link_source_ref(
    session: AsyncSession,
    *,
    requirement: Requirement,
    source_ref_id: uuid.UUID,
    user: User | None = None,
) -> RequirementSourceRefLink:
    """Enllaça una cita existent a un requisit.

    Comprova que la cita sigui de la MATEIXA licitacio. Una FK sola no ho
    demostra: dues licitacions poden tenir els mateixos bytes, i deixar que un
    requisit d'una citi la font de l'altra faria que la prova apuntes a un
    expedient que no es el seu.
    """
    ref = await session.get(TenderSourceRef, source_ref_id)
    if ref is None:
        raise NotFoundError("Cita no trobada.")
    if ref.tender_id != requirement.tender_id:
        raise SourceRefTenderMismatchError("Aquesta cita es d'una altra licitacio.")

    existing = await session.scalar(
        sa.select(RequirementSourceRefLink).where(
            RequirementSourceRefLink.requirement_id == requirement.id,
            RequirementSourceRefLink.source_ref_id == ref.id,
        )
    )
    if existing is not None:
        return existing

    link = RequirementSourceRefLink(
        requirement_id=requirement.id, source_ref_id=ref.id, created_by=user.id if user else None
    )
    session.add(link)
    await session.flush()
    return link


async def unlink_source_ref(
    session: AsyncSession, *, requirement: Requirement, source_ref_id: uuid.UUID
) -> bool:
    """Treu l'enllac. **La cita no s'esborra.**

    Treure un enllac vol dir "aquest requisit ja no s'hi recolza", no "aquella
    cita no va existir": pot sostenir-ne d'altres, i era certa el dia que es va
    crear.
    """
    link = await session.scalar(
        sa.select(RequirementSourceRefLink).where(
            RequirementSourceRefLink.requirement_id == requirement.id,
            RequirementSourceRefLink.source_ref_id == source_ref_id,
        )
    )
    if link is None:
        return False
    await session.delete(link)
    await session.flush()
    return True


async def source_refs_of(
    session: AsyncSession, requirement_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[TenderSourceRef]]:
    """Les cites de cada requisit, en una sola consulta."""
    if not requirement_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(RequirementSourceRefLink.requirement_id, TenderSourceRef)
            .join(TenderSourceRef, TenderSourceRef.id == RequirementSourceRefLink.source_ref_id)
            .where(RequirementSourceRefLink.requirement_id.in_(requirement_ids))
            .order_by(RequirementSourceRefLink.created_at)
        )
    ).all()
    result: dict[uuid.UUID, list[TenderSourceRef]] = {}
    for requirement_id, ref in rows:
        result.setdefault(requirement_id, []).append(ref)
    return result


#: Reexportats perque qui escriu un requisit no hagi d'importar de dos llocs.
__all__ = [
    "EDITABLE_FIELDS",
    "REQUIREMENT_EDIT_ROLES",
    "EffectiveRequirements",
    "ExtractionConfidence",
    "LotTenderMismatchError",
    "RequirementArchivedError",
    "RequirementCategory",
    "RequirementModality",
    "RequirementNotManualError",
    "RequirementObligation",
    "RequirementOperator",
    "RequirementScope",
    "SourceRefTenderMismatchError",
    "archive_manual_requirement",
    "assert_can_edit",
    "create_manual_requirement",
    "effective_requirement_ids",
    "effective_requirements",
    "link_source_ref",
    "load_manual_requirement",
    "manual_dedup_key",
    "manual_filter",
    "source_refs_of",
    "unlink_source_ref",
    "update_manual_requirement",
]
