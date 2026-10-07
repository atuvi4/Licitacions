"""Dimensionament: fonts, discrepàncies i decisions humanes (S3-S6).

La regla que governa tot el modul: **el sistema detecta, la persona decideix.**

Detectar es determinista i sense cap model: si dues observacions donen numeros
DIFERENTS, hi ha discrepancia. Res mes. No es dedueix que "cent vuitanta-una"
i "181" son el mateix, no es normalitza res amb llenguatge natural i no es tria
la font mes fiable. Sortir d'una discrepancia exigeix una persona i un motiu
escrit.

I una segona regla que la sosté: **una decisio no es reobre sola.** Les dues
observacions que van motivar una decisio continuen a l'historial per sempre, i
si es tornessin a comptar, la dimensio quedaria disputada per sempre tot just
despres de decidir-la. Nomes compta l'evidencia POSTERIOR a la decisio.

I "posterior" NO es una comparacio de dates. `created_at` el posa el rellotge
de la base de dades i `decided_at` el del proces; comparar-los es comparar dos
rellotges, i a mes `CURRENT_TIMESTAMP` te resolucio de segon, aixi que una
observacio que arriba el mateix segon que la decisio semblaria anterior. Com que
`add_observation` es l'UNICA manera de crear-ne una, una observacio nova es,
literalment, la que s'acaba d'afegir: no cal preguntar-ho a cap rellotge.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.dimensions import WorkspaceDimension, WorkspaceDimensionObservation
from app.models.enums import DimensionStatus, MembershipRole, ObservationSourceKind
from app.models.identity import User
from app.models.provenance import TenderSourceRef
from app.models.workspace import BidWorkspace
from app.services.source_refs import assert_ref_citable

#: Qui prepara la candidatura. Els mateixos que editen solucions tecniques:
#: dimensionar es feina tecnica, i deixar-ho nomes als rols de decisio
#: obligaria a molestar algu per escriure un numero que ja se sap.
DIMENSION_EDIT_ROLES = frozenset(
    {
        MembershipRole.OWNER,
        MembershipRole.ADMIN,
        MembershipRole.BID_MANAGER,
        MembershipRole.TECHNICAL,
    }
)

#: Les metadades que una persona pot corregir. `status`, els valors de treball
#: i tot el que envolta la decisio NO hi son: decidir te el seu endpoint, i
#: deixar-ho passar per aqui seria poder decidir sense donar cap motiu.
EDITABLE_FIELDS = ("key", "label", "unit", "scope_label")


class DimensionRevisionConflictError(ConflictError):
    """Algu altre l'ha tocada des que la vas llegir."""

    code = "dimension_revision_conflict"


class DimensionDuplicateError(ConflictError):
    """Ja hi ha una dimensio amb aquesta clau i aquest ambit."""

    code = "dimension_duplicate_key"


class DimensionArchivedError(ConflictError):
    """Una dimensio arxivada no es toca."""

    code = "dimension_archived"


class DecisionReasonRequiredError(ValidationError):
    """Decidir exigeix dir per que."""

    code = "dimension_decision_reason_required"


class SourceRefTenderMismatchError(ValidationError):
    """La cita es d'una altra licitacio."""

    code = "source_ref_tender_mismatch"


def assert_can_edit(role: MembershipRole) -> None:
    if role not in DIMENSION_EDIT_ROLES:
        raise NotFoundError("No es poden dimensionar amb aquest rol.")


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


def active_filter(workspace_id: uuid.UUID):
    return sa.and_(
        WorkspaceDimension.workspace_id == workspace_id,
        WorkspaceDimension.archived_at.is_(None),
    )


async def list_dimensions(
    session: AsyncSession, workspace: BidWorkspace, *, include_archived: bool = False
) -> list[WorkspaceDimension]:
    statement = sa.select(WorkspaceDimension).where(
        WorkspaceDimension.workspace_id == workspace.id
    )
    if not include_archived:
        statement = statement.where(WorkspaceDimension.archived_at.is_(None))
    return list(
        (
            await session.scalars(
                statement.order_by(WorkspaceDimension.key, WorkspaceDimension.scope_label)
            )
        ).all()
    )


async def observations_of(
    session: AsyncSession, dimension_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[WorkspaceDimensionObservation]]:
    if not dimension_ids:
        return {}
    rows = (
        await session.scalars(
            sa.select(WorkspaceDimensionObservation)
            .where(WorkspaceDimensionObservation.dimension_id.in_(dimension_ids))
            .order_by(WorkspaceDimensionObservation.created_at)
        )
    ).all()
    result: dict[uuid.UUID, list[WorkspaceDimensionObservation]] = {}
    for observation in rows:
        result.setdefault(observation.dimension_id, []).append(observation)
    return result


async def load_dimension(
    session: AsyncSession, workspace: BidWorkspace, dimension_id: uuid.UUID
) -> WorkspaceDimension:
    dimension = (
        await session.execute(
            sa.select(WorkspaceDimension).where(
                WorkspaceDimension.id == dimension_id,
                WorkspaceDimension.workspace_id == workspace.id,
            )
        )
    ).scalars().one_or_none()
    if dimension is None:
        raise NotFoundError("Dimensio no trobada.")
    return dimension


# --------------------------------------------------------------------------- #
# El testimoni de CAS                                                           #
# --------------------------------------------------------------------------- #


async def _claim(
    session: AsyncSession, dimension: WorkspaceDimension, expected_revision: int
) -> None:
    """Reserva la dimensio per a aquesta escriptura, o falla.

    Es un `UPDATE ... WHERE revision = :expected` i no un `if` seguit d'un
    desat: entre llegir i escriure hi cap una altra peticio, i el resultat
    seria que la segona escriptura es menja la primera sense que ningu ho
    sapiga. Aixi, qui perd rep un `409` i pot tornar a llegir.
    """
    result = await session.execute(
        sa.update(WorkspaceDimension)
        .where(
            WorkspaceDimension.id == dimension.id,
            WorkspaceDimension.revision == expected_revision,
        )
        .values(revision=expected_revision + 1)
    )
    if result.rowcount != 1:
        raise DimensionRevisionConflictError(
            "La dimensio ha canviat des que la vas llegir.",
            details={"expected_revision": expected_revision},
        )
    await session.refresh(dimension)


# --------------------------------------------------------------------------- #
# La regla determinista                                                         #
# --------------------------------------------------------------------------- #


def conflicting_values(
    observations: list[WorkspaceDimensionObservation],
) -> set[Decimal]:
    """Els numeros distints que afirmen les fonts. Res mes.

    Nomes compta `value_num`: una observacio sense numero no es pot comparar, i
    decidir si "181 linies" i "cent vuitanta-una linies" son el mateix es
    justament el que no s'ha de fer sense preguntar-ho a algu.
    """
    return {
        observation.value_num
        for observation in observations
        if observation.value_num is not None
    }


def disputes_decision(
    dimension: WorkspaceDimension, arriving: WorkspaceDimensionObservation
) -> bool:
    """Si una font que acaba d'arribar contradiu el valor ja decidit."""
    return (
        arriving.value_num is not None
        and arriving.value_num != dimension.working_value_num
    )


async def _apply_arrival(
    session: AsyncSession,
    dimension: WorkspaceDimension,
    arriving: WorkspaceDimensionObservation,
) -> None:
    """Torna a mirar l'estat just despres que hagi arribat una font.

    Es incremental a proposit. Recalcular-ho des de tot l'historial obligaria a
    distingir les observacions velles de les noves, i l'unic que tenim per
    fer-ho son dues dates que venen de rellotges diferents. Aqui no cal: la
    font que acaba d'arribar la tenim a la ma.
    """
    if dimension.status == DimensionStatus.DECIDED:
        # Una decisio ja presa nomes la reobre evidencia NOVA. Les observacions
        # que la van motivar continuen a l'historial i no compten: si
        # comptessin, la dimensio quedaria disputada tot just despres de
        # resoldre-la i l'avis no serviria de res.
        if disputes_decision(dimension, arriving):
            dimension.status = DimensionStatus.DISPUTED
        await session.flush()
        return

    observations = list(
        (
            await session.scalars(
                sa.select(WorkspaceDimensionObservation).where(
                    WorkspaceDimensionObservation.dimension_id == dimension.id
                )
            )
        ).all()
    )
    if len(conflicting_values(observations)) >= 2:
        dimension.status = DimensionStatus.DISPUTED
    await session.flush()


# --------------------------------------------------------------------------- #
# Escriptura                                                                    #
# --------------------------------------------------------------------------- #


async def _assert_unique(
    session: AsyncSession,
    workspace: BidWorkspace,
    key: str,
    scope_label: str | None,
    *,
    exclude: uuid.UUID | None = None,
) -> None:
    """Comprova la unicitat que la base de dades no pot garantir sola.

    `UNIQUE(workspace_id, key, scope_label)` no dispara quan `scope_label` es
    NULL: a PostgreSQL dos NULL son distints, aixi que dues dimensions amb la
    mateixa clau i sense ambit hi passarien totes dues. El constraint es queda
    tal com esta —canviar-lo en silenci per arreglar aixo seria tocar l'esquema
    congelat— i el servei ho complementa.
    """
    statement = sa.select(WorkspaceDimension.id).where(
        WorkspaceDimension.workspace_id == workspace.id,
        WorkspaceDimension.key == key,
        WorkspaceDimension.scope_label.is_(None)
        if scope_label is None
        else WorkspaceDimension.scope_label == scope_label,
    )
    if exclude is not None:
        statement = statement.where(WorkspaceDimension.id != exclude)
    if (await session.scalar(statement)) is not None:
        raise DimensionDuplicateError(
            "Ja hi ha una dimensio amb aquesta clau i aquest ambit.",
            details={"key": key, "scope_label": scope_label},
        )


async def create_dimension(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    user: User,
    fields: dict[str, Any],
) -> WorkspaceDimension:
    """Crea el CONCEPTE. Crear no es decidir.

    Neix `undecided` i sense cap valor de treball, encara que qui la crea ja
    sapiga el numero: fixar-lo exigeix dir per que, i te el seu endpoint.
    """
    await _assert_unique(session, workspace, fields["key"], fields.get("scope_label"))
    dimension = WorkspaceDimension(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        status=DimensionStatus.UNDECIDED,
        revision=0,
        **{name: fields[name] for name in EDITABLE_FIELDS if name in fields},
    )
    session.add(dimension)
    await session.flush()
    return dimension


async def update_dimension(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    dimension: WorkspaceDimension,
    expected_revision: int,
    fields: dict[str, Any],
) -> bool:
    """Corregeix les metadades. Mai l'estat ni el valor decidit."""
    if dimension.archived_at is not None:
        raise DimensionArchivedError("Aquesta dimensio esta arxivada.")
    if "key" in fields or "scope_label" in fields:
        await _assert_unique(
            session,
            workspace,
            fields.get("key", dimension.key),
            fields.get("scope_label", dimension.scope_label),
            exclude=dimension.id,
        )

    changed = any(
        name in fields and getattr(dimension, name) != fields[name]
        for name in EDITABLE_FIELDS
    )
    if not changed:
        # Un desat que no canvia res no es un canvi: no mou el testimoni i no
        # fa que la lectura de ningu altre quedi caducada.
        if dimension.revision != expected_revision:
            raise DimensionRevisionConflictError(
                "La dimensio ha canviat des que la vas llegir.",
                details={"expected_revision": expected_revision},
            )
        return False

    await _claim(session, dimension, expected_revision)
    for name in EDITABLE_FIELDS:
        if name in fields:
            setattr(dimension, name, fields[name])
    await session.flush()
    return True


async def add_observation(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    dimension: WorkspaceDimension,
    user: User,
    expected_revision: int,
    source_kind: ObservationSourceKind,
    value_text: str,
    value_num: Decimal | None = None,
    note: str | None = None,
    source_ref_id: uuid.UUID | None = None,
) -> WorkspaceDimensionObservation:
    """Registra que una font diu aixo. Append-only.

    Despres de desar-la es torna a mirar si hi ha contradiccio numerica, i si
    n'hi ha, la dimensio passa a `disputed` tota sola. Es l'unic que el sistema
    decideix aqui: que hi ha un problema, no quina xifra es bona.
    """
    if dimension.archived_at is not None:
        raise DimensionArchivedError("Aquesta dimensio esta arxivada.")
    if source_ref_id is not None:
        ref = await session.get(TenderSourceRef, source_ref_id)
        if ref is None:
            raise NotFoundError("Cita no trobada.")
        if ref.tender_id != workspace.tender_id:
            # Els mateixos bytes poden ser de dues licitacions. Que el SHA
            # coincideixi no fa que la cita sigui d'aquesta.
            raise SourceRefTenderMismatchError("Aquesta cita es d'una altra licitacio.")
        # Una observacio nova es un input nou (pot fer `disputed` la dimensio i
        # arriba a la preparacio): mai sobre un plec ja retirat de la font.
        await assert_ref_citable(session, ref)

    await _claim(session, dimension, expected_revision)
    observation = WorkspaceDimensionObservation(
        dimension_id=dimension.id,
        source_kind=source_kind,
        source_ref_id=source_ref_id,
        value_text=value_text,
        value_num=value_num,
        note=note,
        created_by=user.id,
    )
    session.add(observation)
    await session.flush()
    await _apply_arrival(session, dimension, observation)
    return observation


async def decide_dimension(
    session: AsyncSession,
    *,
    dimension: WorkspaceDimension,
    user: User,
    expected_revision: int,
    working_value_text: str,
    working_value_num: Decimal | None,
    reason: str,
) -> WorkspaceDimension:
    """Una persona fixa el valor que fara servir la candidatura.

    El motiu es obligatori i no pot ser espais: el que fa util una decisio no
    es el numero sino saber per que es va triar aquell entre els que deien les
    fonts. Les observacions no s'esborren mai —ni les que contradeien la que
    s'ha triat—, perque son justament el que explica la decisio.

    Es pot decidir sense cap discrepancia: una sola font fiable pot ser prou. El
    que no pot passar es el contrari, que la maquina converteixi una sola
    observacio en decisio.
    """
    if dimension.archived_at is not None:
        raise DimensionArchivedError("Aquesta dimensio esta arxivada.")
    if not (reason or "").strip():
        raise DecisionReasonRequiredError("Cal escriure per que es tria aquest valor.")

    await _claim(session, dimension, expected_revision)
    dimension.working_value_text = working_value_text
    dimension.working_value_num = working_value_num
    dimension.decision_reason = reason.strip()
    dimension.decided_by = user.id
    dimension.decided_at = datetime.now(UTC)
    dimension.status = DimensionStatus.DECIDED
    await session.flush()
    return dimension


async def archive_dimension(
    session: AsyncSession,
    *,
    dimension: WorkspaceDimension,
    expected_revision: int,
) -> bool:
    """La treu de la llista activa sense esborrar res.

    Les observacions es conserven i deixa de generar avisos. Idempotent.
    """
    if dimension.archived_at is not None:
        return False
    await _claim(session, dimension, expected_revision)
    dimension.archived_at = datetime.now(UTC)
    await session.flush()
    return True


async def disputed_dimensions(
    session: AsyncSession, workspace: BidWorkspace
) -> list[WorkspaceDimension]:
    """Les dimensions actives amb una contradiccio oberta."""
    return list(
        (
            await session.scalars(
                sa.select(WorkspaceDimension).where(
                    active_filter(workspace.id),
                    WorkspaceDimension.status == DimensionStatus.DISPUTED,
                )
            )
        ).all()
    )
