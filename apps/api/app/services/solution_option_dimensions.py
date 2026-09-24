"""De quines magnituds depèn una opció (S3-S7b).

Dues regles governen el mòdul.

**Declarar una dependència no copia cap xifra.** Enllaçar una opció amb una
magnitud de 181 no escriu 181 enlloc, i canviar-la després a 209 no toca
l'opció. Aquí no hi ha cap trigger, cap event d'ORM i cap càlcul: si algú vol
que la quantitat de l'opció canviï, ho ha de dir. Automatitzar-ho faria que una
oferta canviés de xifres sense que ningú no ho hagués decidit.

**Enllaçar és configurar l'opció, no una segona entitat amb vida pròpia.** Per
això no hi ha cap comptador de revisions nou: un attach o un detach real mou el
testimoni de `WorkspaceSolutionOption`, i passa pel mateix CAS atòmic. Amb dos
comptadors, dues pantalles podrien creure totes dues que tenen la versió bona.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models.dimensions import WorkspaceDimension
from app.models.enums import DimensionStatus, OptionSelection, QuoteStatus
from app.models.identity import User
from app.models.offer import EconomicOfferLine
from app.models.solution_option_dimensions import WorkspaceSolutionOptionDimension
from app.models.solution_options import WorkspaceSolutionOption
from app.models.workspace import BidWorkspace
from app.services.solution_options import assert_writable, claim_revision


class DimensionWorkspaceMismatchError(ValidationError):
    """La magnitud no és d'aquesta candidatura."""

    code = "solution_option_dimension_workspace_mismatch"


class DimensionArchivedError(ValidationError):
    """Una magnitud arxivada no es pot començar a fer servir."""

    code = "solution_option_dimension_archived"


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


async def dimensions_of(
    session: AsyncSession, option_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[WorkspaceDimension]]:
    """Les magnituds de cada opció, en una sola consulta.

    En batch i no una per opció: amb una llista de vint opcions, fer-ho fila a
    fila serien vint-i-una consultes per pintar una taula.
    """
    if not option_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(
                WorkspaceSolutionOptionDimension.solution_option_id, WorkspaceDimension
            )
            .join(
                WorkspaceDimension,
                WorkspaceDimension.id == WorkspaceSolutionOptionDimension.dimension_id,
            )
            .where(WorkspaceSolutionOptionDimension.solution_option_id.in_(option_ids))
            .order_by(WorkspaceDimension.key, WorkspaceDimension.scope_label)
        )
    ).all()

    result: dict[uuid.UUID, list[WorkspaceDimension]] = {}
    for option_id, dimension in rows:
        result.setdefault(option_id, []).append(dimension)
    return result


async def _link(
    session: AsyncSession, option_id: uuid.UUID, dimension_id: uuid.UUID
) -> WorkspaceSolutionOptionDimension | None:
    return await session.scalar(
        sa.select(WorkspaceSolutionOptionDimension).where(
            WorkspaceSolutionOptionDimension.solution_option_id == option_id,
            WorkspaceSolutionOptionDimension.dimension_id == dimension_id,
        )
    )


# --------------------------------------------------------------------------- #
# Escriptura                                                                    #
# --------------------------------------------------------------------------- #


async def _load_dimension(
    session: AsyncSession, workspace: BidWorkspace, dimension_id: uuid.UUID
) -> WorkspaceDimension:
    """Una magnitud d'AQUESTA candidatura.

    La FK no ho garanteix: apunta a `workspace_dimensions`, que conté les de
    totes les candidatures. Sense aquesta comprovació, una opció podria dependre
    d'una magnitud d'una altra candidatura de la mateixa empresa, i el número
    que algú hi llegiria seria d'un altre expedient.
    """
    dimension = await session.get(WorkspaceDimension, dimension_id)
    if dimension is None or dimension.workspace_id != workspace.id:
        raise DimensionWorkspaceMismatchError(
            "Aquesta magnitud no és d'aquesta candidatura.",
            details={"dimension_id": str(dimension_id)},
        )
    return dimension


async def attach_dimension(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    option: WorkspaceSolutionOption,
    dimension_id: uuid.UUID,
    user: User,
    expected_revision: int,
) -> bool:
    """Declara que l'opció depèn d'aquesta magnitud. Idempotent.

    **No copia cap valor.** Que la magnitud valgui 181 avui no escriu 181 a
    `option.quantity`, ni ara ni quan canviï.

    Es pot dependre d'una magnitud encara `undecided` o `disputed`: representar
    una dependència no és afirmar que el valor ja estigui resolt. Justament al
    revés —saber de què depèn una opció és el que fa evident que cal decidir-ho.
    """
    assert_writable(option)
    dimension = await _load_dimension(session, workspace, dimension_id)

    if dimension.archived_at is not None:
        # Les que ja hi estaven es queden; el que no es pot és començar a
        # dependre d'una magnitud que ja s'ha donat de baixa.
        raise DimensionArchivedError(
            "Aquesta magnitud està arxivada.", details={"dimension_id": str(dimension_id)}
        )

    if await _link(session, option.id, dimension_id) is not None:
        # Ja hi era: no és cap canvi, i per tant no mou el testimoni ni deixa
        # caducada la lectura de ningú altre.
        return False

    await claim_revision(session, option, expected_revision)
    session.add(
        WorkspaceSolutionOptionDimension(
            solution_option_id=option.id, dimension_id=dimension_id, created_by=user.id
        )
    )
    await session.flush()
    return True


async def detach_dimension(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    option: WorkspaceSolutionOption,
    dimension_id: uuid.UUID,
    expected_revision: int,
) -> bool:
    """Treu la dependència. Idempotent.

    Només desapareix la relació: ni l'opció ni la magnitud es toquen. Es pot
    desenganxar una magnitud arxivada —és justament com es neteja el rastre
    d'una que ja no compta.
    """
    assert_writable(option)
    await _load_dimension(session, workspace, dimension_id)

    link = await _link(session, option.id, dimension_id)
    if link is None:
        return False

    await claim_revision(session, option, expected_revision)
    await session.delete(link)
    await session.flush()
    return True


async def dimensions_undecided_in_use(
    session: AsyncSession, workspace: BidWorkspace
) -> list[tuple[WorkspaceDimension, WorkspaceSolutionOption]]:
    """Magnituds sense decidir de les quals depen una linia economica VIVA.

    La cadena es real i explicita, sense cap heuristica de text:

        EconomicOfferLine viva
          -> solution_option_id
          -> workspace_solution_option_dimensions
          -> WorkspaceDimension activa i `undecided`

    Abans que existeixi la linia no hi ha res a bloquejar: una opcio triada que
    depen d'una magnitud per decidir es feina normal. El que no pot passar es
    que una xifra que ja ha entrat a l'oferta es recolzi en un numero que ningu
    no ha fixat.

    `disputed` no hi entra: te el seu propi avis, i convertir-la en blocador
    canviaria el que significa.
    """
    rows = (
        await session.execute(
            sa.select(WorkspaceDimension, WorkspaceSolutionOption)
            .join(
                WorkspaceSolutionOptionDimension,
                WorkspaceSolutionOptionDimension.dimension_id == WorkspaceDimension.id,
            )
            .join(
                WorkspaceSolutionOption,
                WorkspaceSolutionOption.id
                == WorkspaceSolutionOptionDimension.solution_option_id,
            )
            .join(
                EconomicOfferLine,
                EconomicOfferLine.solution_option_id == WorkspaceSolutionOption.id,
            )
            .where(
                EconomicOfferLine.archived_at.is_(None),
                WorkspaceDimension.workspace_id == workspace.id,
                WorkspaceDimension.archived_at.is_(None),
                WorkspaceDimension.status == DimensionStatus.UNDECIDED,
            )
            .distinct()
        )
    ).all()
    return [(dimension, option) for dimension, option in rows]


async def options_with_expired_quote_in_use(
    session: AsyncSession, workspace: BidWorkspace
) -> list[WorkspaceSolutionOption]:
    """Opcions triades amb la cotitzacio caducada que ja son a l'oferta.

    El quote viu a l'OPCIO i no a la linia: la linia es el snapshot que es va
    adoptar, i no ha de saber res de com va la gestio amb el proveidor. El que
    aquest blocador diu es que el preu que s'ofereix es recolza en una xifra que
    ja no es vigent.
    """
    return list(
        (
            await session.scalars(
                sa.select(WorkspaceSolutionOption)
                .join(
                    EconomicOfferLine,
                    EconomicOfferLine.solution_option_id == WorkspaceSolutionOption.id,
                )
                .where(
                    EconomicOfferLine.archived_at.is_(None),
                    WorkspaceSolutionOption.workspace_id == workspace.id,
                    WorkspaceSolutionOption.selection == OptionSelection.SELECTED,
                    WorkspaceSolutionOption.quote_status == QuoteStatus.EXPIRED,
                )
                .distinct()
            )
        ).all()
    )
