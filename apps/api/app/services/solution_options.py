"""Opcions de solucio: proveidors, cotitzacions i tria humana (S3-S7).

Tres regles governen aquest modul.

**Una opcio no es una linia d'oferta.** Aqui hi ha cost, no preu. Res del que
passi en aquest fitxer crea, toca ni sincronitza cap `EconomicOfferLine`. La
relacio entre totes dues coses arriba mes endavant, i fer-la servir abans
d'hora voldria dir que el preu de venda tindria dos amos.

**Triar-ne una no en descarta cap altra.** Sewan, Cable i Orange poden coexistir,
i que algu triï Sewan no vol dir que hagi mirat les altres dues. Auto-rebutjar
les germanes convertiria una decisio que no s'ha pres en una que sembla presa,
amb un motiu que ningu no ha escrit.

**El cost es economia interna.** `unit_cost_ex_vat` i `internal_note` nomes els
veuen i els escriuen els rols economics, i pel mateix cami que a l'Sprint 2: el
bloc `internal_economics` existeix o no existeix. Un `null` voldria dir "encara
no ho sabem", que es una altra cosa.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.models.enums import MembershipRole, OptionSelection, QuoteStatus
from app.models.identity import User
from app.models.requirements import Requirement
from app.models.solution_options import WorkspaceSolutionOption
from app.models.workspace import BidWorkspace, WorkspaceTechnicalSolution
from app.services.economic_offer import ECONOMIC_INTERNAL_ROLES
from app.services.manual_requirement import effective_requirements
from app.services.technical_solution import SOLUTION_EDIT_ROLES

#: Qui prepara la candidatura. Es reutilitza la constant de les solucions
#: tecniques a proposit: configurar opcions es la mateixa feina, i tenir-ne dues
#: llistes faria que un dia divergissin sense que ningu ho volgues.
OPTION_EDIT_ROLES = SOLUTION_EDIT_ROLES

#: Qui veu i escriu cost. La constant d'Sprint 2, sense cap canal paral·lel.
OPTION_INTERNAL_ROLES = ECONOMIC_INTERNAL_ROLES

#: Sentinella per distingir "no toquis aquest camp" de "posa-hi NULL".
UNSET = object()

#: El que pot escriure qualsevol rol que prepari la candidatura. El cost NO hi
#: es: te el seu contracte, i barrejar-los faria que un PATCH tecnic el pogues
#: netejar sense voler.
PUBLIC_FIELDS = (
    "supplier_name",
    "product_name",
    "description",
    "quantity",
    "unit",
    "pricing_basis",
    "duration_months_override",
    "quote_status",
    "quote_date",
    "quote_reference",
    "quote_source",
    "technical_solution_id",
    "requirement_id",
)

#: El que nomes toquen els rols economics.
INTERNAL_FIELDS = ("unit_cost_ex_vat", "internal_note")

#: Una cotitzacio demanada pero encara no rebuda, en una opcio que ja s'ha triat.
PENDING_QUOTE_STATUSES = frozenset({QuoteStatus.REQUESTED, QuoteStatus.PENDING})


class OptionRevisionConflictError(ConflictError):
    """Algu altre l'ha tocada des que la vas llegir."""

    code = "solution_option_revision_conflict"


class OptionArchivedError(ConflictError):
    """Una opcio arxivada no es toca."""

    code = "solution_option_archived"


class SelectionReasonRequiredError(ValidationError):
    """Descartar exigeix dir per que."""

    code = "solution_option_reason_required"


class OptionLinkMismatchError(ValidationError):
    """La solucio tecnica o el requisit no son d'aquesta candidatura."""

    code = "solution_option_link_mismatch"


class InternalEconomicsForbiddenError(PermissionDeniedError):
    """Aquest rol no pot escriure cost.

    Es un error i no un silenci: ignorar el camp deixaria qui l'ha enviat
    convencut que l'ha desat.
    """

    code = "solution_option_internal_economics_forbidden"


def assert_can_edit(role: MembershipRole) -> None:
    if role not in OPTION_EDIT_ROLES:
        raise PermissionDeniedError("El teu rol no pot editar les opcions de solucio.")


def can_view_internal_economics(role: MembershipRole) -> bool:
    return role in OPTION_INTERNAL_ROLES


def assert_can_write_internal(role: MembershipRole) -> None:
    if role not in OPTION_INTERNAL_ROLES:
        raise InternalEconomicsForbiddenError("El teu rol no pot escriure el cost intern.")


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


async def list_options(
    session: AsyncSession, workspace: BidWorkspace, *, include_archived: bool = False
) -> list[WorkspaceSolutionOption]:
    statement = sa.select(WorkspaceSolutionOption).where(
        WorkspaceSolutionOption.workspace_id == workspace.id
    )
    if not include_archived:
        statement = statement.where(WorkspaceSolutionOption.archived_at.is_(None))
    return list(
        (
            await session.scalars(
                statement.order_by(
                    WorkspaceSolutionOption.created_at, WorkspaceSolutionOption.id
                )
            )
        ).all()
    )


async def load_option(
    session: AsyncSession, workspace: BidWorkspace, option_id: uuid.UUID
) -> WorkspaceSolutionOption:
    """Carrega una opcio d'AQUESTA candidatura. Un id no es cap autoritzacio."""
    option = await session.scalar(
        sa.select(WorkspaceSolutionOption).where(
            WorkspaceSolutionOption.id == option_id,
            WorkspaceSolutionOption.workspace_id == workspace.id,
        )
    )
    if option is None:
        raise NotFoundError("Opcio no trobada.")
    return option


# --------------------------------------------------------------------------- #
# Coherencia de les referencies                                                 #
# --------------------------------------------------------------------------- #


async def _assert_links_belong(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    technical_solution_id: uuid.UUID | None,
    requirement_id: uuid.UUID | None,
) -> None:
    """Les FK soles no demostren que aixo sigui coherent.

    `technical_solution_id` apunta a `workspace_technical_solutions` i prou: la
    base acceptaria una solucio d'una altra candidatura. I `requirement_id`
    apunta a `requirements`, que son globals per licitacio: acceptaria un
    requisit d'un altre expedient. Ho comprova el servei perque no ho pot
    comprovar cap constraint.
    """
    if technical_solution_id is not None:
        solution = await session.get(WorkspaceTechnicalSolution, technical_solution_id)
        if solution is None or solution.workspace_id != workspace.id:
            raise OptionLinkMismatchError(
                "Aquesta solucio tecnica no es d'aquesta candidatura.",
                details={"technical_solution_id": str(technical_solution_id)},
            )

    if requirement_id is not None:
        requirement = await session.get(Requirement, requirement_id)
        if requirement is None or requirement.tender_id != workspace.tender_id:
            raise OptionLinkMismatchError(
                "Aquest requisit no es d'aquesta licitacio.",
                details={"requirement_id": str(requirement_id)},
            )
        # Nomes s'hi pot ENLLACAR un requisit del conjunt efectiu: un d'extret
        # per una run antiga parla d'uns documents que ja no son els vigents, i
        # un manual arxivat ja no compta. Les opcions que ja hi apuntaven NO es
        # toquen ni s'esborren: nomes es diu, a la lectura, que el vincle ha
        # deixat de ser vigent. Veure `effective_requirement_ids`.
        effective = await effective_requirements(session, workspace.tender_id)
        if requirement_id not in {item.id for item in effective.all}:
            raise OptionLinkMismatchError(
                "Aquest requisit ja no forma part del conjunt vigent.",
                details={"requirement_id": str(requirement_id)},
            )


async def effective_requirement_ids(
    session: AsyncSession, workspace: BidWorkspace
) -> set[uuid.UUID]:
    effective = await effective_requirements(session, workspace.tender_id)
    return {item.id for item in effective.all}


# --------------------------------------------------------------------------- #
# El testimoni de CAS                                                           #
# --------------------------------------------------------------------------- #


async def _claim(
    session: AsyncSession, option: WorkspaceSolutionOption, expected_revision: int
) -> None:
    """Reserva l'opcio per a aquesta escriptura, o falla.

    Es un `UPDATE ... WHERE revision = :expected` i no un `if` seguit d'un
    desat: entre llegir i escriure hi cap una altra peticio, i el resultat seria
    que la segona escriptura es menja la primera sense que ningu ho sapiga. Qui
    perd rep un `409` i pot tornar a llegir.
    """
    result = await session.execute(
        sa.update(WorkspaceSolutionOption)
        .where(
            WorkspaceSolutionOption.id == option.id,
            WorkspaceSolutionOption.revision == expected_revision,
        )
        .values(revision=expected_revision + 1)
    )
    if result.rowcount != 1:
        raise OptionRevisionConflictError(
            "L'opcio ha canviat des que la vas llegir.",
            details={"expected_revision": expected_revision},
        )
    await session.refresh(option)


def _assert_writable(option: WorkspaceSolutionOption) -> None:
    if option.archived_at is not None:
        raise OptionArchivedError("Aquesta opcio esta arxivada.")


# --------------------------------------------------------------------------- #
# Escriptura                                                                    #
# --------------------------------------------------------------------------- #


async def create_option(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    role: MembershipRole,
    fields: dict[str, Any],
    internal: dict[str, Any] | None = None,
) -> WorkspaceSolutionOption:
    """Crea una opcio. Neix sense decidir i sense cotitzar.

    `selection = undecided` i `quote_status = draft` sempre: cap opcio neix
    triada, i cap cotitzacio neix demanada. Les dues coses les fa una persona.
    """
    await _assert_links_belong(
        session,
        workspace,
        technical_solution_id=fields.get("technical_solution_id"),
        requirement_id=fields.get("requirement_id"),
    )
    if internal:
        # Si un rol sense potestat economica envia cost, es un error i no un
        # camp que s'ignora en silenci.
        assert_can_write_internal(role)

    option = WorkspaceSolutionOption(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        selection=OptionSelection.UNDECIDED,
        quote_status=fields.get("quote_status") or QuoteStatus.DRAFT,
        revision=0,
        **{
            name: fields[name]
            for name in PUBLIC_FIELDS
            if name in fields and name != "quote_status"
        },
        **{name: (internal or {}).get(name) for name in INTERNAL_FIELDS},
    )
    session.add(option)
    await session.flush()
    return option


async def update_public(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    option: WorkspaceSolutionOption,
    expected_revision: int,
    fields: dict[str, Any],
) -> bool:
    """La fitxa publica: proveidor, producte, configuracio i cotitzacio.

    El cost NO hi es. Aquest metode el pot cridar un `technical`, i si el cost
    hi viatgés, un desat seu el podria netejar sense que ni tan sols el vegi.
    """
    _assert_writable(option)
    if "technical_solution_id" in fields or "requirement_id" in fields:
        await _assert_links_belong(
            session,
            workspace,
            technical_solution_id=fields.get(
                "technical_solution_id", option.technical_solution_id
            ),
            requirement_id=fields.get("requirement_id", option.requirement_id),
        )

    changed = any(
        name in fields and getattr(option, name) != fields[name] for name in PUBLIC_FIELDS
    )
    if not changed:
        if option.revision != expected_revision:
            raise OptionRevisionConflictError(
                "L'opcio ha canviat des que la vas llegir.",
                details={"expected_revision": expected_revision},
            )
        return False

    await _claim(session, option, expected_revision)
    for name in PUBLIC_FIELDS:
        if name in fields:
            setattr(option, name, fields[name])
    await session.flush()
    return True


async def update_internal(
    session: AsyncSession,
    *,
    option: WorkspaceSolutionOption,
    role: MembershipRole,
    expected_revision: int,
    unit_cost_ex_vat: Decimal | None | Any = UNSET,
    internal_note: str | None | Any = UNSET,
) -> bool:
    """El cost i les notes de dins. Nomes rols economics.

    El sentinella `UNSET` fa que "no envio aquest camp" i "posa'l a NULL" siguin
    dues peticions diferents. Sense ell, esborrar un cost i no parlar-ne
    s'escriurien igual.
    """
    _assert_writable(option)
    assert_can_write_internal(role)

    incoming = {
        "unit_cost_ex_vat": unit_cost_ex_vat,
        "internal_note": internal_note,
    }
    pending = {name: value for name, value in incoming.items() if value is not UNSET}
    changed = any(getattr(option, name) != value for name, value in pending.items())
    if not changed:
        if option.revision != expected_revision:
            raise OptionRevisionConflictError(
                "L'opcio ha canviat des que la vas llegir.",
                details={"expected_revision": expected_revision},
            )
        return False

    await _claim(session, option, expected_revision)
    for name, value in pending.items():
        setattr(option, name, value)
    await session.flush()
    return True


async def set_selection(
    session: AsyncSession,
    *,
    option: WorkspaceSolutionOption,
    user: User,
    expected_revision: int,
    selection: OptionSelection,
    reason: str | None,
) -> WorkspaceSolutionOption:
    """Una persona tria, descarta o torna enrere.

    Descartar exigeix motiu. Seleccionar no: el motiu d'una tria sol ser que les
    altres s'han descartat, i exigir-lo faria escriure una frase buida.

    **No toca cap germana.** Que aquesta estigui triada no diu res de les
    altres, i posar-les a `rejected` seria escriure una decisio que ningu no ha
    pres, amb un motiu que ningu no ha donat.
    """
    _assert_writable(option)
    if selection == OptionSelection.REJECTED and not (reason or "").strip():
        raise SelectionReasonRequiredError("Cal escriure per que es descarta aquesta opcio.")

    await _claim(session, option, expected_revision)
    option.selection = selection
    if reason is not None:
        option.selection_reason = reason.strip() or None
    elif selection != OptionSelection.REJECTED:
        # Tornar a "sense decidir" o triar-la sense motiu no ha de conservar
        # l'explicacio d'un descart anterior: diria una cosa que ja no es.
        option.selection_reason = None

    if selection == OptionSelection.SELECTED:
        option.selected_by = user.id
        option.selected_at = datetime.now(UTC)
    else:
        # Deixa de ser la triada: mantenir qui i quan la va triar faria creure
        # que encara ho es.
        option.selected_by = None
        option.selected_at = None

    await session.flush()
    return option


async def archive_option(
    session: AsyncSession,
    *,
    option: WorkspaceSolutionOption,
    expected_revision: int,
) -> bool:
    """La treu de la llista activa sense esborrar res. Idempotent.

    Es conserva tot: proveidor, producte, cotitzacio, cost i la tria que es va
    fer. I no en selecciona cap altra: arxivar no es decidir.
    """
    if option.archived_at is not None:
        return False
    await _claim(session, option, expected_revision)
    option.archived_at = datetime.now(UTC)
    await session.flush()
    return True


# --------------------------------------------------------------------------- #
# El que n'ha de saber la preparacio                                            #
# --------------------------------------------------------------------------- #


async def options_awaiting_quote(
    session: AsyncSession, workspace: BidWorkspace
) -> list[WorkspaceSolutionOption]:
    """Opcions vives, triades, i amb la cotitzacio encara demanada o pendent.

    Nomes les triades: que una alternativa que ningu no fara servir no tingui
    cotitzacio no es cap problema pendent.
    """
    return list(
        (
            await session.scalars(
                sa.select(WorkspaceSolutionOption).where(
                    WorkspaceSolutionOption.workspace_id == workspace.id,
                    WorkspaceSolutionOption.archived_at.is_(None),
                    WorkspaceSolutionOption.selection == OptionSelection.SELECTED,
                    WorkspaceSolutionOption.quote_status.in_(PENDING_QUOTE_STATUSES),
                )
            )
        ).all()
    )
