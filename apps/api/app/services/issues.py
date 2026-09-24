"""Dubtes i discrepancies: regles (S3-S10).

Tres coses governen el modul.

**Un issue no es una tasca.** Una tasca es feina per fer i es tanca fent-la; un
dubte es coneixement que falta i es tanca sabent alguna cosa. Per aixo resoldre
exigeix escriure la resposta: un dubte "resolt" sense resposta no seria resolt,
seria oblidat.

**Cap issue neix sol.** Aqui no hi ha cap regla que en generi: ni per una
dimensio en disputa, ni per una cotitzacio pendent, ni per una linia divergida.
Tot aixo ja te el seu avis a `FinalReadiness`. Omplir la llista de preguntes que
ningu no s'ha fet hi enterraria les que si.

**Marcar que un dubte impedeix presentar-se es una decisio d'oferta.** Nomes
`DECISION_ROLES`, i per una accio propia: si es pogues fer amb el PATCH general,
qualsevol editor bloquejaria la candidatura sense adonar-se'n.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.models.enums import IssueStatus, MembershipRole
from app.models.identity import CompanyMembership, User
from app.models.issues import WorkspaceIssue, WorkspaceIssueSourceRef
from app.models.provenance import TenderSourceRef
from app.models.workspace import BidWorkspace
from app.services.bid_workspace import DECISION_ROLES

#: Qui pot obrir i treballar dubtes: tothom qui prepara la candidatura. Que
#: falti una dada ho ha de poder dir qui se n'adona, i qui se n'adona sol ser
#: qui esta fent la feina.
ISSUE_EDIT_ROLES = frozenset(
    {
        MembershipRole.OWNER,
        MembershipRole.ADMIN,
        MembershipRole.BID_MANAGER,
        MembershipRole.TECHNICAL,
    }
)

#: Els estats en que una pregunta encara no te resposta.
OPEN_STATUSES = frozenset({IssueStatus.OPEN, IssueStatus.WAITING})

#: El que una persona pot corregir amb el PATCH general. `status`,
#: `blocks_final_review`, els actors i les dates de resolucio NO hi son: cadascun
#: te la seva accio, i deixar-los passar per aqui seria poder tancar un dubte o
#: bloquejar una candidatura sense dir-ho.
EDITABLE_FIELDS = (
    "issue_type",
    "title",
    "description",
    "requirement_id",
    "award_criterion_id",
    "technical_solution_id",
    "solution_option_id",
    "economic_offer_line_id",
    "dimension_id",
    "supplier_name",
    "owner_id",
    "due_at",
    "resolution_source",
)

#: Els estats que el PATCH general pot moure. `resolved` i `dismissed` no: tancar
#: un dubte exigeix dir com, i te la seva accio.
PATCHABLE_STATUSES = frozenset({IssueStatus.OPEN, IssueStatus.WAITING})


class ResolutionRequiredError(ValidationError):
    """Resoldre exigeix escriure la resposta."""

    code = "issue_resolution_required"


class IssueClosedError(ValidationError):
    """Un dubte tancat no es torna a editar per la porta del PATCH."""

    code = "issue_closed"


class IssueStatusNotPatchableError(ValidationError):
    """Tancar un dubte te la seva accio."""

    code = "issue_status_not_patchable"


class IssueLinkMismatchError(ValidationError):
    """Aixo que s'hi enllaca no es d'aquesta candidatura."""

    code = "issue_link_mismatch"


class SourceRefTenderMismatchError(ValidationError):
    """La cita es d'una altra licitacio.

    Que el SHA coincideixi no fa que la cita sigui d'aquesta: els mateixos bytes
    poden ser a dos expedients, i la provinenca correcta no es contingut igual.
    """

    code = "issue_source_ref_tender_mismatch"


def assert_can_edit(role: MembershipRole) -> None:
    if role not in ISSUE_EDIT_ROLES:
        raise PermissionDeniedError("El teu rol no pot obrir ni editar dubtes.")


def assert_can_block(role: MembershipRole) -> None:
    """403 i no 404: l'usuari hi te acces; el que no te es potestat sobre l'oferta."""
    if role not in DECISION_ROLES:
        raise PermissionDeniedError(
            "El teu rol no pot decidir si un dubte impedeix la revisio final."
        )


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


async def list_issues(
    session: AsyncSession, workspace: BidWorkspace, *, status: IssueStatus | None = None
) -> list[WorkspaceIssue]:
    statement = sa.select(WorkspaceIssue).where(
        WorkspaceIssue.workspace_id == workspace.id
    )
    if status is not None:
        statement = statement.where(WorkspaceIssue.status == status)
    return list(
        (
            await session.scalars(
                statement.order_by(WorkspaceIssue.created_at.desc(), WorkspaceIssue.id)
            )
        ).all()
    )


async def load_issue(
    session: AsyncSession, workspace: BidWorkspace, issue_id: uuid.UUID
) -> WorkspaceIssue:
    """Un dubte d'AQUESTA candidatura. Un identificador no es cap autoritzacio."""
    issue = await session.scalar(
        sa.select(WorkspaceIssue).where(
            WorkspaceIssue.id == issue_id,
            WorkspaceIssue.workspace_id == workspace.id,
        )
    )
    if issue is None:
        raise NotFoundError("Dubte no trobat.")
    return issue


async def source_refs_of(
    session: AsyncSession, issue_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[TenderSourceRef]]:
    """Les cites de cada dubte, en una sola consulta."""
    if not issue_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(WorkspaceIssueSourceRef.issue_id, TenderSourceRef)
            .join(TenderSourceRef, TenderSourceRef.id == WorkspaceIssueSourceRef.source_ref_id)
            .where(WorkspaceIssueSourceRef.issue_id.in_(issue_ids))
            .order_by(WorkspaceIssueSourceRef.created_at)
        )
    ).all()
    out: dict[uuid.UUID, list[TenderSourceRef]] = {}
    for issue_id, ref in rows:
        out.setdefault(issue_id, []).append(ref)
    return out


# --------------------------------------------------------------------------- #
# Coherencia del que s'hi enllaca                                               #
# --------------------------------------------------------------------------- #


async def _assert_links_belong(
    session: AsyncSession, workspace: BidWorkspace, fields: dict[str, Any]
) -> None:
    """Les FK soles no ho demostren.

    Apunten a taules que contenen files de totes les candidatures i de totes les
    licitacions. Sense aixo, un dubte podria assenyalar una magnitud d'un altre
    expedient, i qui el llegis creuria que parla del seu.
    """
    from app.models.dimensions import WorkspaceDimension
    from app.models.offer import (
        EconomicOffer,
        EconomicOfferLine,
        EconomicOfferLot,
        EconomicOfferSection,
    )
    from app.models.requirements import AwardCriterion, Requirement
    from app.models.solution_options import WorkspaceSolutionOption
    from app.models.workspace import WorkspaceTechnicalSolution

    async def _check(field: str, model, column, expected) -> None:
        value = fields.get(field)
        if value is None:
            return
        row = await session.get(model, value)
        if row is None or getattr(row, column) != expected:
            raise IssueLinkMismatchError(
                "Aixo no es d'aquesta candidatura.", details={"field": field}
            )

    await _check("requirement_id", Requirement, "tender_id", workspace.tender_id)
    await _check("award_criterion_id", AwardCriterion, "tender_id", workspace.tender_id)
    await _check(
        "technical_solution_id", WorkspaceTechnicalSolution, "workspace_id", workspace.id
    )
    await _check("solution_option_id", WorkspaceSolutionOption, "workspace_id", workspace.id)
    await _check("dimension_id", WorkspaceDimension, "workspace_id", workspace.id)

    # La linia no porta `workspace_id`: penja de seccio -> lot -> oferta.
    line_id = fields.get("economic_offer_line_id")
    if line_id is not None:
        owner = await session.scalar(
            sa.select(EconomicOffer.workspace_id)
            .join(EconomicOfferLot, EconomicOfferLot.offer_id == EconomicOffer.id)
            .join(
                EconomicOfferSection,
                EconomicOfferSection.offer_lot_id == EconomicOfferLot.id,
            )
            .join(
                EconomicOfferLine,
                EconomicOfferLine.section_id == EconomicOfferSection.id,
            )
            .where(EconomicOfferLine.id == line_id)
        )
        if owner != workspace.id:
            raise IssueLinkMismatchError(
                "Aquesta linia no es d'aquesta candidatura.",
                details={"field": "economic_offer_line_id"},
            )

    # El responsable ha de ser algu d'aquesta empresa.
    owner_id = fields.get("owner_id")
    if owner_id is not None:
        member = await session.scalar(
            sa.select(CompanyMembership.id).where(
                CompanyMembership.user_id == owner_id,
                CompanyMembership.company_id == workspace.company_id,
            )
        )
        if member is None:
            raise IssueLinkMismatchError(
                "Aquesta persona no es d'aquesta empresa.", details={"field": "owner_id"}
            )


# --------------------------------------------------------------------------- #
# Escriptura                                                                    #
# --------------------------------------------------------------------------- #


async def create_issue(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    user: User,
    fields: dict[str, Any],
) -> WorkspaceIssue:
    """Obre un dubte. **El crea una persona, sempre.**

    Neix `open` i sense bloquejar res: que una pregunta impedeixi presentar-se es
    una decisio a part, i de qui respon de l'oferta.
    """
    await _assert_links_belong(session, workspace, fields)

    issue = WorkspaceIssue(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        status=IssueStatus.OPEN,
        blocks_final_review=False,
        created_by=user.id,
        updated_by=user.id,
        **{name: fields[name] for name in EDITABLE_FIELDS if name in fields},
    )
    session.add(issue)
    await session.flush()
    return issue


def _assert_open(issue: WorkspaceIssue) -> None:
    if issue.status not in OPEN_STATUSES:
        raise IssueClosedError(
            "Aquest dubte ja esta tancat.", details={"status": issue.status.value}
        )


async def update_issue(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    issue: WorkspaceIssue,
    user: User,
    fields: dict[str, Any],
    status: IssueStatus | None = None,
) -> bool:
    """Corregeix el que va escriure una persona, i mou entre `open` i `waiting`.

    Tancar-lo no: `resolved` i `dismissed` tenen la seva accio perque exigeixen
    dir com es tanca.
    """
    _assert_open(issue)
    if status is not None and status not in PATCHABLE_STATUSES:
        raise IssueStatusNotPatchableError(
            "Tancar un dubte te la seva propia accio.", details={"status": status.value}
        )
    await _assert_links_belong(session, workspace, fields)

    changed = any(
        name in fields and getattr(issue, name) != fields[name] for name in EDITABLE_FIELDS
    )
    if status is not None and status != issue.status:
        changed = True
    if not changed:
        return False

    for name in EDITABLE_FIELDS:
        if name in fields:
            setattr(issue, name, fields[name])
    if status is not None:
        issue.status = status
    issue.updated_by = user.id
    await session.flush()
    return True


async def resolve_issue(
    session: AsyncSession,
    *,
    issue: WorkspaceIssue,
    user: User,
    resolution: str,
    resolution_source: str | None = None,
) -> WorkspaceIssue:
    """Tanca el dubte amb la resposta escrita.

    El text es obligatori i no pot ser espais: el que fa util un dubte resolt no
    es l'estat, es saber que se'n va concloure.

    **No decideix res mes.** Resoldre un dubte sobre una magnitud no la decideix,
    ni tria una opcio, ni valida una solucio: son decisions relacionades pero
    diferents, i encadenar-les faria que respondre una pregunta mogues xifres que
    ningu no ha mirat.

    **I no toca `blocks_final_review`**: que algu decidis un dia que aixo
    impedia presentar-se continua sent cert, i esborrar-ho seria perdre per que
    la candidatura va estar aturada.
    """
    _assert_open(issue)
    if not (resolution or "").strip():
        raise ResolutionRequiredError("Cal escriure com s'ha resolt el dubte.")

    issue.status = IssueStatus.RESOLVED
    issue.resolution = resolution.strip()
    if resolution_source is not None:
        issue.resolution_source = resolution_source.strip() or None
    issue.resolved_by = user.id
    issue.resolved_at = datetime.now(UTC)
    issue.updated_by = user.id
    await session.flush()
    return issue


async def dismiss_issue(
    session: AsyncSession, *, issue: WorkspaceIssue, user: User
) -> WorkspaceIssue:
    """Tanca el dubte perque ja no ve al cas.

    **Descartar no es resoldre**, i per aixo `resolved_by` i `resolved_at` es
    queden buits: si els omplissim, `resolved_at IS NOT NULL` voldria dir dues
    coses diferents i "ho sabem" inclouria coses que ningu no ha contestat mai.
    Qui descarta i quan queda a `AuditLog` i a `updated_by`/`updated_at`.
    """
    _assert_open(issue)
    issue.status = IssueStatus.DISMISSED
    issue.updated_by = user.id
    await session.flush()
    return issue


async def set_final_review_block(
    session: AsyncSession, *, issue: WorkspaceIssue, user: User, blocks: bool
) -> bool:
    """Decideix si aquest dubte impedeix la revisio final.

    **No toca l'estat.** Marcar-lo no el fa passar a `waiting` ni a res: nomes
    diu que mentre continui obert, la candidatura no es pot donar per preparada.
    """
    if issue.blocks_final_review == blocks:
        return False
    issue.blocks_final_review = blocks
    issue.updated_by = user.id
    await session.flush()
    return True


# --------------------------------------------------------------------------- #
# Provinenca                                                                    #
# --------------------------------------------------------------------------- #


async def link_source_ref(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    issue: WorkspaceIssue,
    source_ref_id: uuid.UUID,
    user: User,
) -> bool:
    """Enganxa una cita ja creada. Idempotent.

    La cita ha de ser de la MATEIXA licitacio: els mateixos bytes poden ser a
    dos expedients, i que el SHA coincideixi no fa que la cita sigui d'aquest.
    """
    ref = await session.get(TenderSourceRef, source_ref_id)
    if ref is None:
        raise NotFoundError("Cita no trobada.")
    if ref.tender_id != workspace.tender_id:
        raise SourceRefTenderMismatchError("Aquesta cita es d'una altra licitacio.")

    existing = await session.scalar(
        sa.select(WorkspaceIssueSourceRef).where(
            WorkspaceIssueSourceRef.issue_id == issue.id,
            WorkspaceIssueSourceRef.source_ref_id == source_ref_id,
        )
    )
    if existing is not None:
        return False

    session.add(
        WorkspaceIssueSourceRef(
            issue_id=issue.id, source_ref_id=source_ref_id, created_by=user.id
        )
    )
    await session.flush()
    return True


async def unlink_source_ref(
    session: AsyncSession, *, issue: WorkspaceIssue, source_ref_id: uuid.UUID
) -> bool:
    """Treu l'enllac. **NO esborra la cita.**

    El `TenderSourceRef` es queda: pot sostenir-ne d'altres i era cert el dia
    que es va crear. Desenganxar vol dir "aquest dubte ja no s'hi recolza".
    """
    link = await session.scalar(
        sa.select(WorkspaceIssueSourceRef).where(
            WorkspaceIssueSourceRef.issue_id == issue.id,
            WorkspaceIssueSourceRef.source_ref_id == source_ref_id,
        )
    )
    if link is None:
        return False
    await session.delete(link)
    await session.flush()
    return True


# --------------------------------------------------------------------------- #
# El que n'ha de saber la preparacio                                            #
# --------------------------------------------------------------------------- #


async def blocking_open_issues(
    session: AsyncSession, workspace: BidWorkspace
) -> list[WorkspaceIssue]:
    """Dubtes oberts que algu ha marcat com a impediment per presentar-se."""
    return list(
        (
            await session.scalars(
                sa.select(WorkspaceIssue).where(
                    WorkspaceIssue.workspace_id == workspace.id,
                    WorkspaceIssue.blocks_final_review.is_(True),
                    WorkspaceIssue.status.in_(OPEN_STATUSES),
                )
            )
        ).all()
    )


async def open_issue_count(session: AsyncSession, workspace: BidWorkspace) -> int:
    """Quants dubtes oberts hi ha que NO bloquegen.

    Els que bloquegen ja tenen el seu blocador; comptar-los tambe aqui diria el
    mateix problema dues vegades.
    """
    return (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(WorkspaceIssue)
            .where(
                WorkspaceIssue.workspace_id == workspace.id,
                WorkspaceIssue.status.in_(OPEN_STATUSES),
                WorkspaceIssue.blocks_final_review.is_(False),
            )
        )
    ) or 0
