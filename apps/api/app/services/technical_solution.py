"""Solucio tecnica d'una candidatura: regles i persistencia.

Respon "com oferirem aquest requisit", que no es "el complim?"
(`CompanyRequirementAssessment`) ni "amb quin paper ho acreditem?"
(`WorkspaceEvidenceItem`).

**Dos camins de creacio, i cap d'automatic** (C-11). No hi ha `/sync` ni cap
regla que dedueixi quins requisits necessiten solucio: o be algu n'escriu una de
lliure, o be assenyala un requisit concret. El client no envia mai `source`: el
fixa el servidor. Si el pogues triar, "algu ho ha decidit" i "ho ha deduit el
sistema" deixarien de ser distingibles, i tot el domini es sosté sobre aquesta
distincio.

**Validar es signar.** Te el seu endpoint, el seu motiu i la seva firma, i el
`PATCH` generic no hi arriba mai. La firma no s'esborra quan les coses canvien
—es historia— i el que es deriva en llegir es si encara val.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.models.enums import (
    MembershipRole,
    TechnicalSolutionSource,
    TechnicalSolutionStatus,
)
from app.models.identity import User
from app.models.requirements import Requirement
from app.models.workspace import BidWorkspace, WorkspaceTechnicalSolution
from app.services.bid_workspace import assert_assignable
from app.workspace.checklist import applies_to_workspace_lot, checklist_title
from app.workspace.relevance import requirement_fingerprint
from app.workspace.technical_solution import (
    StaleInput,
    content_fingerprint,
    validation_stale_reasons,
)

#: Qui prepara la candidatura. A diferencia de `DECISION_ROLES`, `technical` hi
#: es: aquesta capa es precisament la seva feina, i signar una solucio no
#: compromet l'empresa com fa un GO.
SOLUTION_EDIT_ROLES = frozenset(
    {
        MembershipRole.OWNER,
        MembershipRole.ADMIN,
        MembershipRole.BID_MANAGER,
        MembershipRole.TECHNICAL,
    }
)

#: Validar es una afirmacio tecnica, no una decisio economica: mateixos rols.
SOLUTION_VALIDATION_ROLES = SOLUTION_EDIT_ROLES

#: Els estats que pot escriure una persona treballant. `validated` no hi es: no
#: s'hi arriba creant ni editant, nomes signant.
WORK_STATUSES = frozenset(
    {
        TechnicalSolutionStatus.NOT_STARTED,
        TechnicalSolutionStatus.DRAFTING,
        TechnicalSolutionStatus.NEEDS_VALIDATION,
    }
)

#: Sentinella per distingir "no toquis aquest camp" de "posa-hi NULL".
UNSET = object()


class SolutionStatusNotWritableError(ValidationError):
    """S'ha intentat escriure `validated` per un cami que no es la validacio."""

    code = "technical_solution_status_not_writable"


class SolutionNotValidatableError(ValidationError):
    """Falta el que s'havia de validar: no hi ha proposta escrita."""

    code = "technical_solution_not_validatable"


def assert_can_edit(role: MembershipRole) -> None:
    """403 i no 404: l'usuari hi te acces, el que no te es potestat d'escriure."""
    if role not in SOLUTION_EDIT_ROLES:
        raise PermissionDeniedError("El teu rol no pot editar solucions tecniques.")


def assert_can_validate(role: MembershipRole) -> None:
    if role not in SOLUTION_VALIDATION_ROLES:
        raise PermissionDeniedError("El teu rol no pot validar solucions tecniques.")


def _assert_work_status(status: TechnicalSolutionStatus | None) -> None:
    if status is not None and status not in WORK_STATUSES:
        raise SolutionStatusNotWritableError(
            "Una solucio nomes es pot donar per validada des de la validacio."
        )


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


async def list_solutions(
    session: AsyncSession, workspace: BidWorkspace
) -> list[WorkspaceTechnicalSolution]:
    return list(
        (
            await session.scalars(
                sa.select(WorkspaceTechnicalSolution)
                .where(
                    WorkspaceTechnicalSolution.workspace_id == workspace.id,
                    WorkspaceTechnicalSolution.company_id == workspace.company_id,
                )
                .order_by(WorkspaceTechnicalSolution.created_at)
            )
        ).all()
    )


async def get_solution(
    session: AsyncSession, workspace: BidWorkspace, solution_id: uuid.UUID
) -> WorkspaceTechnicalSolution:
    """Sempre filtrat per empresa i candidatura.

    404 i no 403 per a una fila d'un altre tenant: confirmar-ne l'existencia ja
    seria una fuita. Un identificador alie i un d'inventat responen igual.
    """
    solution = await session.scalar(
        sa.select(WorkspaceTechnicalSolution).where(
            WorkspaceTechnicalSolution.id == solution_id,
            WorkspaceTechnicalSolution.workspace_id == workspace.id,
            WorkspaceTechnicalSolution.company_id == workspace.company_id,
        )
    )
    if solution is None:
        raise NotFoundError("Solucio tecnica no trobada.")
    return solution


async def stale_reasons_for(
    session: AsyncSession, solutions: Sequence[WorkspaceTechnicalSolution]
) -> dict[uuid.UUID, list[str]]:
    """Per que ha deixat de valer cada validacio. No s'escriu: es deriva.

    Es resol en bloc per no fer una consulta per fila quan es llegeix el llistat.
    """
    requirement_ids = {
        solution.requirement_id for solution in solutions if solution.requirement_id
    }
    requirements: dict[uuid.UUID, Requirement] = {}
    if requirement_ids:
        rows = await session.scalars(
            sa.select(Requirement).where(Requirement.id.in_(requirement_ids))
        )
        requirements = {requirement.id: requirement for requirement in rows.all()}

    reasons: dict[uuid.UUID, list[str]] = {}
    for solution in solutions:
        requirement = (
            requirements.get(solution.requirement_id) if solution.requirement_id else None
        )
        reasons[solution.id] = validation_stale_reasons(
            StaleInput(
                validated_at=solution.validated_at,
                source=str(solution.source),
                current_content_fingerprint=_fingerprint_of(solution),
                validated_content_fingerprint=solution.validated_content_fingerprint,
                requirement_resolvable=requirement is not None,
                current_requirement_fingerprint=(
                    _requirement_fingerprint_of(requirement) if requirement else None
                ),
                validated_requirement_fingerprint=solution.validated_requirement_fingerprint,
            )
        )
    return reasons


def _fingerprint_of(solution: WorkspaceTechnicalSolution) -> str:
    return content_fingerprint(
        title=solution.title,
        proposed_solution=solution.proposed_solution,
        supplier_name=solution.supplier_name,
        product_name=solution.product_name,
    )


def _requirement_fingerprint_of(requirement: Requirement) -> str:
    return requirement_fingerprint(
        requirement.category, requirement.title, requirement.value_json
    )


# --------------------------------------------------------------------------- #
# Creacio                                                                       #
# --------------------------------------------------------------------------- #


async def create_manual(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    title: str,
    proposed_solution: str | None = None,
    supplier_name: str | None = None,
    product_name: str | None = None,
    owner_id: uuid.UUID | None = None,
    notes: str | None = None,
    status: TechnicalSolutionStatus | None = None,
    user: User,
) -> WorkspaceTechnicalSolution:
    """Una solucio que no surt de cap requisit del plec.

    `source` i `requirement_id` NO s'accepten del client: aquesta funcio nomes
    sap escriure files manuals, i per aixo no hi ha cap manera d'enganyar-la.
    """
    _assert_work_status(status)
    await assert_assignable(session, workspace.company_id, owner_id)

    solution = WorkspaceTechnicalSolution(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        requirement_id=None,
        source=TechnicalSolutionSource.MANUAL,
        title=title,
        proposed_solution=proposed_solution,
        supplier_name=supplier_name,
        product_name=product_name,
        owner_id=owner_id,
        notes=notes,
        status=status or TechnicalSolutionStatus.NOT_STARTED,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(solution)
    await session.commit()
    await session.refresh(solution)
    return solution


async def create_from_requirement(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    requirement_id: uuid.UUID,
    user: User,
) -> tuple[WorkspaceTechnicalSolution, bool]:
    """Algu ha assenyalat un requisit i ha dit que necessita solucio (C-11).

    Idempotent, com `create_workspace`: assenyalar dues vegades el mateix requisit
    obre el que ja hi havia, no en crea un segon. La unicitat ja la garanteix
    l'index parcial de la taula; aixo nomes evita fer-lo petar per una doble
    pulsacio.
    """
    requirement = await session.scalar(
        sa.select(Requirement).where(
            Requirement.id == requirement_id,
            Requirement.tender_id == workspace.tender_id,
        )
    )
    if requirement is None or not applies_to_workspace_lot(
        requirement, workspace.tender_lot_id
    ):
        # Mateixa resposta que per a un identificador inventat: si un requisit
        # d'una altra licitacio —o d'un altre lot— dones un error diferent,
        # provant identificadors se'n podria deduir l'existencia.
        #
        # El lot importa tant com la licitacio: un workspace de lot nomes hereta
        # els requisits del seu lot i els generals, igual que la checklist.
        raise NotFoundError("Requisit no trobat en aquesta licitacio.")

    existing = await session.scalar(
        sa.select(WorkspaceTechnicalSolution).where(
            WorkspaceTechnicalSolution.workspace_id == workspace.id,
            WorkspaceTechnicalSolution.requirement_id == requirement.id,
        )
    )
    if existing is not None:
        return existing, False

    solution = WorkspaceTechnicalSolution(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        requirement_id=requirement.id,
        source=TechnicalSolutionSource.REQUIREMENT,
        # El mateix generador que titula els items de checklist i del dossier:
        # una persona ha de poder llegir la fila sense obrir el plec.
        title=checklist_title(requirement)[:500],
        status=TechnicalSolutionStatus.NOT_STARTED,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(solution)
    await session.commit()
    await session.refresh(solution)
    return solution, True


# --------------------------------------------------------------------------- #
# Edicio                                                                        #
# --------------------------------------------------------------------------- #


async def update_solution(
    session: AsyncSession,
    workspace: BidWorkspace,
    solution: WorkspaceTechnicalSolution,
    *,
    title: str | None = None,
    proposed_solution: object = UNSET,
    supplier_name: object = UNSET,
    product_name: object = UNSET,
    owner_id: object = UNSET,
    notes: object = UNSET,
    status: TechnicalSolutionStatus | None = None,
    user: User,
) -> WorkspaceTechnicalSolution:
    """Els camps de treball. `source`, `requirement_id` i la firma no hi son.

    No s'accepten ni ignorant-los: simplement no existeixen com a parametre, que
    es l'unica manera que no reapareguin per descuit.
    """
    _assert_work_status(status)
    if owner_id is not UNSET:
        await assert_assignable(session, workspace.company_id, owner_id)

    if title is not None:
        solution.title = title
    if proposed_solution is not UNSET:
        solution.proposed_solution = proposed_solution
    if supplier_name is not UNSET:
        solution.supplier_name = supplier_name
    if product_name is not UNSET:
        solution.product_name = product_name
    if owner_id is not UNSET:
        solution.owner_id = owner_id
    if notes is not UNSET:
        solution.notes = notes
    if status is not None:
        solution.status = status

    solution.updated_by = user.id
    await session.commit()
    await session.refresh(solution)
    return solution


# --------------------------------------------------------------------------- #
# Validacio humana                                                              #
# --------------------------------------------------------------------------- #


async def validate_solution(
    session: AsyncSession,
    workspace: BidWorkspace,
    solution: WorkspaceTechnicalSolution,
    *,
    note: str,
    user: User,
) -> WorkspaceTechnicalSolution:
    """Una persona diu que aquesta solucio serveix, i ho diu sobre AIXO.

    Queda lligada al contingut i al requisit concrets amb dues empremtes. Si
    despres algu reescriu la proposta o el plec canvia el que demana, la firma
    segueix a la fila —amb el seu nom i la seva data— pero deixa de valer.

    Tornar a validar una solucio caducada reescriu la validacio vigent. L'historic
    transversal el guarda l'auditoria; aqui no hi ha taula d'append.
    """
    reason = (note or "").strip()
    if not reason:
        raise ValidationError("Cal escriure per que aquesta solucio serveix.")
    if not (solution.proposed_solution or "").strip():
        raise SolutionNotValidatableError(
            "No es pot validar una solucio sense proposta escrita."
        )

    requirement = (
        await session.get(Requirement, solution.requirement_id)
        if solution.requirement_id
        else None
    )

    solution.status = TechnicalSolutionStatus.VALIDATED
    solution.validated_by = user.id
    solution.validated_at = datetime.now(UTC)
    solution.validated_note = reason
    solution.validated_content_fingerprint = _fingerprint_of(solution)
    # Sense requisit no hi ha res a ancorar: inventar una empremta seria signar
    # sobre el no-res, i despres no es podria distingir de la d'un requisit real.
    solution.validated_requirement_fingerprint = (
        _requirement_fingerprint_of(requirement) if requirement is not None else None
    )
    solution.updated_by = user.id
    await session.commit()
    await session.refresh(solution)
    return solution


def audit_summary(
    solution: WorkspaceTechnicalSolution, *, stale: Iterable[str] = ()
) -> str:
    """Rastre curt i sense contingut sensible.

    `AuditLog` nomes te `summary` (text de 500): no hi ha camp de metadada
    estructurada, aixi que el que cal per seguir el rastre va aqui, i la proposta
    sencera no hi entra mai.
    """
    parts = [
        f"solucio={solution.id}",
        f"workspace={solution.workspace_id}",
        f"estat={solution.status}",
        f"origen={solution.source}",
    ]
    if solution.requirement_id:
        parts.append(f"requisit={solution.requirement_id}")
    reasons = list(stale)
    if reasons:
        parts.append(f"caducada={','.join(reasons)}")
    return " ".join(parts)
