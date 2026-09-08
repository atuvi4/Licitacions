"""Dossier documental: de "complim" a "aixo es el que presentarem".

Reutilitza el que ja hi ha i no en construeix cap copia:

* les **evidencies del compliment** (4C) ja diuen quina dada corporativa sosté
  cada veredicte i, sovint, amb quin document. Aixo es el que es proposa;
* la **biblioteca corporativa** (`documents`) es l'unica; el workspace nomes hi
  apunta. Un mateix PDF pot cobrir cinc requisits sense duplicar-se enlloc;
* la **checklist** de 5A dona el text de la tasca quan falta un document.

Cap crida a cap LLM. Proposar un document es una consulta; decidir quin es
presenta es d'una persona.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.compliance import CompanyRequirementAssessment
from app.models.documents import Document
from app.models.enums import (
    ChecklistItemStatus,
    DocumentValidity,
    DossierCoverage,
    EvidenceItemSource,
    EvidenceItemStatus,
    MembershipRole,
    RequirementCategory,
    RequirementObligation,
    WorkspaceDecisionType,
    WorkspaceStatus,
    WorkspaceTaskStatus,
)
from app.models.identity import User
from app.models.requirements import Requirement
from app.models.workspace import (
    BidWorkspace,
    WorkspaceChecklistItem,
    WorkspaceEvidenceItem,
    WorkspaceTask,
)
from app.services import bid_workspace as workspace_service
from app.workspace.dossier import (
    DOSSIER_GROUPS,
    GROUP_ORDER,
    DossierCoverageCounters,
    EvidenceView,
    ReadinessInput,
    ReadinessResult,
    coverage_for_requirement,
    document_validity,
    evaluate_readiness,
    summarise_coverage,
    task_title_for_gap,
)
from app.workspace.transitions import TransitionActor

#: Estats de checklist que compten com a resolts.
CLOSED_CHECKLIST = frozenset({ChecklistItemStatus.COMPLETED, ChecklistItemStatus.NOT_APPLICABLE})
#: Estats de tasca que compten com a tancats.
CLOSED_TASKS = frozenset({WorkspaceTaskStatus.COMPLETED, WorkspaceTaskStatus.CANCELLED})


class WorkspaceNotReadyError(ConflictError):
    """S'ha demanat passar a revisio final amb el dossier incomplet."""

    code = "workspace_not_ready"


class DocumentNotAvailableError(ValidationError):
    """El document no existeix o no es d'aquesta empresa."""

    code = "document_not_available"


# --------------------------------------------------------------------------- #
# Sincronitzacio de propostes                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    suggested: int
    gaps: int
    total: int


async def _relevant_assessments(
    session: AsyncSession, workspace: BidWorkspace
) -> list[CompanyRequirementAssessment]:
    run = await workspace_service.current_compliance_run(
        session, workspace.company_id, workspace.tender_id
    )
    if run is None:
        return []
    conditions = [
        CompanyRequirementAssessment.assessment_run_id == run.id,
        CompanyRequirementAssessment.company_id == workspace.company_id,
    ]
    if workspace.tender_lot_id is not None:
        conditions.append(
            sa.or_(
                CompanyRequirementAssessment.tender_lot_id == workspace.tender_lot_id,
                CompanyRequirementAssessment.tender_lot_id.is_(None),
            )
        )
    return list(
        (
            await session.scalars(
                sa.select(CompanyRequirementAssessment)
                .options(selectinload(CompanyRequirementAssessment.evidence))
                .where(*conditions)
            )
        ).all()
    )


async def sync_dossier(
    session: AsyncSession, workspace: BidWorkspace, *, user_id: uuid.UUID | None
) -> SyncOutcome:
    """Proposa documents a partir del compliment. **Mai n'esborra ni en tria cap.**

    Per cada requisit avaluat: si el motor va fer servir un document corporatiu
    per decidir, aquell document es una proposta (`suggested`). Si no n'hi ha cap
    i el requisit es obligatori, es deixa un marcador `missing` perque el forat
    sigui visible en comptes de ser una absencia silenciosa.

    Es idempotent: tornar-hi despres d'un recalcul afegeix el que falti i no toca
    res del que una persona ja hagi seleccionat, substituit o descartat.
    """
    assessments = await _relevant_assessments(session, workspace)
    if not assessments:
        return SyncOutcome(0, 0, await _count_items(session, workspace.id))

    requirement_ids = [a.requirement_id for a in assessments]
    requirements = {
        r.id: r
        for r in (
            await session.scalars(
                sa.select(Requirement).where(Requirement.id.in_(requirement_ids))
            )
        ).all()
    }
    checklist_by_requirement = {
        item.requirement_id: item
        for item in await workspace_service.checklist_items(session, workspace.id)
        if item.requirement_id
    }

    existing_pairs = set(
        (
            await session.execute(
                sa.select(
                    WorkspaceEvidenceItem.requirement_id, WorkspaceEvidenceItem.document_id
                ).where(WorkspaceEvidenceItem.workspace_id == workspace.id)
            )
        ).all()
    )
    requirements_with_item = {pair[0] for pair in existing_pairs}

    suggested = gaps = 0
    for assessment in assessments:
        requirement = requirements.get(assessment.requirement_id)
        if requirement is None:  # pragma: no cover - FK ho impedeix
            continue
        checklist_item = checklist_by_requirement.get(requirement.id)
        mandatory = requirement.obligation == RequirementObligation.MANDATORY

        with_document = [
            evidence for evidence in assessment.evidence if evidence.document_id is not None
        ]
        for evidence in with_document:
            if (requirement.id, evidence.document_id) in existing_pairs:
                continue
            session.add(
                WorkspaceEvidenceItem(
                    company_id=workspace.company_id,
                    workspace_id=workspace.id,
                    requirement_id=requirement.id,
                    checklist_item_id=checklist_item.id if checklist_item else None,
                    document_id=evidence.document_id,
                    company_evidence_type=evidence.entity_type,
                    company_evidence_id=evidence.entity_id,
                    label=evidence.label[:300],
                    # Proposta, no tria: cap `selected_by`, cap `selected_at`.
                    status=EvidenceItemStatus.SUGGESTED,
                    source=EvidenceItemSource.COMPLIANCE,
                    created_by=user_id,
                )
            )
            existing_pairs.add((requirement.id, evidence.document_id))
            requirements_with_item.add(requirement.id)
            suggested += 1

        # Un forat nomes es marca si el requisit obliga a alguna cosa i encara no
        # te cap peca: si ja hi ha una proposta, el forat no existeix.
        if not mandatory or requirement.id in requirements_with_item:
            continue
        session.add(
            WorkspaceEvidenceItem(
                company_id=workspace.company_id,
                workspace_id=workspace.id,
                requirement_id=requirement.id,
                checklist_item_id=checklist_item.id if checklist_item else None,
                document_id=None,
                label=(checklist_item.title if checklist_item else requirement.title)[:300],
                status=EvidenceItemStatus.MISSING,
                source=EvidenceItemSource.COMPLIANCE,
                created_by=user_id,
            )
        )
        existing_pairs.add((requirement.id, None))
        requirements_with_item.add(requirement.id)
        gaps += 1

    await session.flush()
    return SyncOutcome(suggested, gaps, await _count_items(session, workspace.id))


async def _count_items(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(WorkspaceEvidenceItem)
            .where(WorkspaceEvidenceItem.workspace_id == workspace_id)
        )
        or 0
    )


async def start_bid_preparation(session: AsyncSession, workspace: BidWorkspace) -> None:
    """Un GO aprovat que obre el dossier passa a `preparing_bid`.

    No es cap aprovacio: es constatar que s'hi esta treballant. Per aixo l'actor
    es `SYSTEM` i per aixo aquesta funcio no pot arribar mai a `go_approved` ni
    a `submitted` —les transicions ho impedeixen.
    """
    if workspace.status != WorkspaceStatus.GO_APPROVED:
        return
    workspace.status = workspace_service.set_status(
        workspace, WorkspaceStatus.PREPARING_BID, actor=TransitionActor.SYSTEM
    )


# --------------------------------------------------------------------------- #
# Lectura del dossier                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DossierRow:
    """Una fila de la vista: requisit, evidencia, document i estat."""

    item: WorkspaceEvidenceItem
    requirement: Requirement | None
    checklist_item: WorkspaceChecklistItem | None
    document: Document | None
    validity: DocumentValidity
    mandatory: bool
    group: str


@dataclass(frozen=True, slots=True)
class Dossier:
    rows: list[DossierRow]
    coverage_by_requirement: dict[uuid.UUID, DossierCoverage]
    counters: DossierCoverageCounters
    readiness: ReadinessResult
    deadline: datetime | None

    def grouped(self) -> list[tuple[str, list[DossierRow]]]:
        buckets: dict[str, list[DossierRow]] = {group: [] for group in GROUP_ORDER}
        for row in self.rows:
            buckets[row.group].append(row)
        return [(group, buckets[group]) for group in GROUP_ORDER if buckets[group]]


async def load_dossier(
    session: AsyncSession, workspace: BidWorkspace, *, today: date | None = None
) -> Dossier:
    """La vista completa, amb la vigencia resolta contra el termini d'aquest plec."""
    state = await workspace_service.load_state(session, workspace)
    items = list(
        (
            await session.scalars(
                sa.select(WorkspaceEvidenceItem)
                .where(WorkspaceEvidenceItem.workspace_id == workspace.id)
                .order_by(WorkspaceEvidenceItem.created_at, WorkspaceEvidenceItem.id)
            )
        ).all()
    )

    requirement_ids = {item.requirement_id for item in items if item.requirement_id}
    requirements = {
        r.id: r
        for r in (
            await session.scalars(
                sa.select(Requirement).where(Requirement.id.in_(requirement_ids))
            )
        ).all()
    } if requirement_ids else {}

    document_ids = {item.document_id for item in items if item.document_id}
    documents = {
        d.id: d
        for d in (
            await session.scalars(
                sa.select(Document).where(
                    Document.id.in_(document_ids),
                    Document.company_id == workspace.company_id,
                )
            )
        ).all()
    } if document_ids else {}

    checklist = {
        item.id: item for item in await workspace_service.checklist_items(session, workspace.id)
    }

    rows: list[DossierRow] = []
    views: dict[uuid.UUID, list[EvidenceView]] = {}
    mandatory_requirements: set[uuid.UUID] = set()
    expired_mandatory = 0

    for item in items:
        requirement = requirements.get(item.requirement_id) if item.requirement_id else None
        document = documents.get(item.document_id) if item.document_id else None
        mandatory = bool(
            requirement and requirement.obligation == RequirementObligation.MANDATORY
        )
        validity = document_validity(
            document.expires_at if document else None,
            deadline=state.deadline_at,
            today=today,
            has_document=document is not None,
        )
        category = requirement.category if requirement else RequirementCategory.OTHER

        rows.append(
            DossierRow(
                item=item,
                requirement=requirement,
                checklist_item=(
                    checklist.get(item.checklist_item_id) if item.checklist_item_id else None
                ),
                document=document,
                validity=validity,
                mandatory=mandatory,
                group=DOSSIER_GROUPS.get(category, "other"),
            )
        )

        if item.requirement_id is None:
            continue
        if mandatory:
            mandatory_requirements.add(item.requirement_id)
            if (
                item.status == EvidenceItemStatus.SELECTED
                and validity
                in (DocumentValidity.EXPIRED, DocumentValidity.EXPIRES_BEFORE_DEADLINE)
            ):
                expired_mandatory += 1
        views.setdefault(item.requirement_id, []).append(
            EvidenceView(
                item_id=item.id,
                requirement_id=item.requirement_id,
                status=item.status,
                validity=validity,
                has_document=document is not None,
                mandatory=mandatory,
            )
        )

    coverage = {
        requirement_id: coverage_for_requirement(pieces)
        for requirement_id, pieces in views.items()
    }
    counters = summarise_coverage(
        coverage, mandatory=mandatory_requirements, expired_documents=expired_mandatory
    )

    readiness = await _readiness(
        session,
        workspace,
        state=state,
        counters=counters,
        started=bool(items),
        needs_review_items=sum(
            1 for item in items if item.status == EvidenceItemStatus.NEEDS_REVIEW
        ),
    )

    return Dossier(
        rows=rows,
        coverage_by_requirement=coverage,
        counters=counters,
        readiness=readiness,
        deadline=state.deadline_at,
    )


# --------------------------------------------------------------------------- #
# Readiness                                                                     #
# --------------------------------------------------------------------------- #


async def _readiness(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    state: workspace_service.WorkspaceState,
    counters: DossierCoverageCounters,
    started: bool,
    needs_review_items: int,
) -> ReadinessResult:
    blocked = int(
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(WorkspaceChecklistItem)
            .where(
                WorkspaceChecklistItem.workspace_id == workspace.id,
                WorkspaceChecklistItem.mandatory.is_(True),
                WorkspaceChecklistItem.status == ChecklistItemStatus.BLOCKED,
            )
        )
        or 0
    )
    open_tasks = int(
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(WorkspaceTask)
            .where(
                WorkspaceTask.workspace_id == workspace.id,
                WorkspaceTask.mandatory.is_(True),
                WorkspaceTask.status.not_in(list(CLOSED_TASKS)),
            )
        )
        or 0
    )
    decision = state.latest_decision

    return evaluate_readiness(
        ReadinessInput(
            dossier_started=started,
            decision_is_go=bool(
                decision and decision.decision == WorkspaceDecisionType.GO
            ),
            analysis_outdated=state.analysis_outdated,
            mandatory_unresolved=state.counters.mandatory_unresolved,
            blocked_checklist_items=blocked,
            missing_mandatory_evidence=counters.missing_document,
            expired_mandatory_documents=counters.expired_documents,
            open_mandatory_tasks=open_tasks,
            needs_review_items=needs_review_items,
        )
    )


async def evaluate_workspace_readiness(
    session: AsyncSession, workspace: BidWorkspace
) -> ReadinessResult:
    """L'estat del dossier i per que. Es el que consulta la interficie i el gate.

    **No llegeix `workspace.status`: el calcula des de zero.** Aquesta es la
    unica font de veritat sobre si el dossier esta a punt; l'estat guardat nomes
    n'es un reflex, i un reflex pot quedar enrere.
    """
    return (await load_dossier(session, workspace)).readiness


async def reconcile_readiness(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    readiness: ReadinessResult | None = None,
) -> ReadinessResult:
    """Invalidacio centralitzada: un workspace no pot quedar-se dient que esta
    llest quan ja no ho esta.

    Es l'UNIC lloc que treu un workspace de `ready_for_final_review`, i ho fa
    sempre pel mateix cami —recalcular i comparar— en comptes de tenir una regla
    diferent a cada mutacio. Deseleccionar un document, reobrir una tasca
    obligatoria, demanar una revisio, un recalcul de compliment: totes acaben
    aqui.

    El retroces es a `preparing_bid` i no a `analysis`: la feina feta no
    desapareix, nomes deixa d'estar certificada com a completa.

    Nota deliberada: aixo NO converteix `workspace.status` en una garantia.
    Nomes es reconcilia quan algu mira o toca el workspace; un canvi extern que
    ningu no hagi consultat encara pot deixar l'etiqueta enrere fins a la
    propera lectura. Per aixo qualsevol aprovacio final futura ha de tornar a
    cridar `evaluate_workspace_readiness()` i no fiar-se de l'estat.
    """
    result = readiness or await evaluate_workspace_readiness(session, workspace)
    if workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW and not result.is_ready:
        workspace.status = workspace_service.set_status(
            workspace, WorkspaceStatus.PREPARING_BID, actor=TransitionActor.SYSTEM
        )
        await session.commit()
        await session.refresh(workspace)
    return result


async def advance_to_final_review(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    user: User,
    role: MembershipRole,
) -> ReadinessResult:
    """Passa a `ready_for_final_review`, i nomes si les regles ho permeten.

    No es pot marcar a ma saltant-se el calcul: si el dossier no esta a punt,
    l'error diu exactament que falta. I arribar-hi **no es presentar**: nomes vol
    dir que el dossier sembla complet perque una persona el revisi. `submitted`
    segueix exigint `HUMAN_FINAL_APPROVAL`, que no fa servir cap codi.
    """
    workspace_service.assert_can_decide(role)
    # Es recalcula aqui i ara. L'estat guardat no serveix de credencial ni per
    # arribar a la revisio final ni, el dia que existeixi, per presentar.
    readiness = await reconcile_readiness(session, workspace)
    if not readiness.is_ready:
        raise WorkspaceNotReadyError(
            "El dossier encara no esta a punt per a la revisio final.",
            details={"blockers": list(readiness.blockers)},
        )

    if workspace.status == WorkspaceStatus.GO_APPROVED:
        # Pas intermedi explicit: el graf no permet saltar-se `preparing_bid`.
        workspace.status = workspace_service.set_status(
            workspace, WorkspaceStatus.PREPARING_BID, actor=TransitionActor.SYSTEM
        )
    workspace.status = workspace_service.set_status(
        workspace, WorkspaceStatus.READY_FOR_FINAL_REVIEW, actor=TransitionActor.HUMAN_DECISION
    )
    await session.commit()
    await session.refresh(workspace)
    return readiness


# --------------------------------------------------------------------------- #
# Seleccio manual                                                               #
# --------------------------------------------------------------------------- #


async def get_item(
    session: AsyncSession, workspace: BidWorkspace, item_id: uuid.UUID
) -> WorkspaceEvidenceItem:
    item = await session.scalar(
        sa.select(WorkspaceEvidenceItem).where(
            WorkspaceEvidenceItem.id == item_id,
            WorkspaceEvidenceItem.workspace_id == workspace.id,
            WorkspaceEvidenceItem.company_id == workspace.company_id,
        )
    )
    if item is None:
        raise NotFoundError("Peca del dossier no trobada.")
    return item


async def _company_document(
    session: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    document = await session.scalar(
        sa.select(Document).where(
            Document.id == document_id, Document.company_id == company_id
        )
    )
    if document is None:
        # 422 i no 404: el recurs demanat (la peca) si que existeix; el que no
        # val es el document que s'hi vol posar.
        raise DocumentNotAvailableError("Aquest document no es de la teva empresa.")
    return document


async def update_item(
    session: AsyncSession,
    workspace: BidWorkspace,
    item: WorkspaceEvidenceItem,
    *,
    status: EvidenceItemStatus | None = None,
    document_id: uuid.UUID | None = None,
    clear_document: bool = False,
    notes: str | None = None,
    user: User,
) -> WorkspaceEvidenceItem:
    """Selecciona, substitueix, descarta o marca per revisar. Sempre amb firma.

    Substituir un document es la mateixa operacio que seleccionar-ne un: no cal
    esborrar la peca i crear-ne una altra, perque el que interessa conservar es
    QUI va decidir que aquest era el document, i aixo s'actualitza.
    """
    if clear_document:
        item.document_id = None
        item.company_evidence_type = None
        item.company_evidence_id = None
    elif document_id is not None:
        document = await _company_document(session, workspace.company_id, document_id)
        item.document_id = document.id
        item.label = document.name[:300]
        item.source = EvidenceItemSource.MANUAL

    if notes is not None:
        item.notes = notes.strip() or None

    if status is not None:
        item.status = status
        if status == EvidenceItemStatus.SELECTED:
            if item.document_id is None:
                raise DocumentNotAvailableError(
                    "No es pot seleccionar una peca sense document."
                )
            item.selected_by = user.id
            item.selected_at = datetime.now(UTC)
        else:
            # Deseleccionar esborra la firma: deixaria de ser certa.
            item.selected_by = None
            item.selected_at = None
    elif document_id is not None and item.status == EvidenceItemStatus.SELECTED:
        # Substitucio d'un document ja triat: la tria es nova, la firma tambe.
        item.selected_by = user.id
        item.selected_at = datetime.now(UTC)

    item.updated_by = user.id
    await session.commit()
    await session.refresh(item)
    await reconcile_readiness(session, workspace)
    return item


async def add_item(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    requirement_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
    notes: str | None,
    user: User,
) -> WorkspaceEvidenceItem:
    """Afegeix una peca a ma: un document mes per a un requisit que en demana N.

    El mateix document pot entrar a diversos requisits; el que no pot es entrar
    dues vegades al mateix (l'index unic parcial ho impedeix).
    """
    document = (
        await _company_document(session, workspace.company_id, document_id)
        if document_id
        else None
    )
    checklist_item = None
    if requirement_id:
        checklist_item = await session.scalar(
            sa.select(WorkspaceChecklistItem).where(
                WorkspaceChecklistItem.workspace_id == workspace.id,
                WorkspaceChecklistItem.requirement_id == requirement_id,
            )
        )

    item = WorkspaceEvidenceItem(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        requirement_id=requirement_id,
        checklist_item_id=checklist_item.id if checklist_item else None,
        document_id=document.id if document else None,
        label=(document.name if document else "Evidencia pendent")[:300],
        status=(
            EvidenceItemStatus.SELECTED if document else EvidenceItemStatus.MISSING
        ),
        source=EvidenceItemSource.MANUAL,
        notes=(notes or "").strip() or None,
        created_by=user.id,
        updated_by=user.id,
        selected_by=user.id if document else None,
        selected_at=datetime.now(UTC) if document else None,
    )
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "Aquest document ja consta com a evidencia d'aquest requisit."
        ) from exc
    await session.refresh(item)
    await reconcile_readiness(session, workspace)
    return item


# --------------------------------------------------------------------------- #
# Del forat a la tasca                                                          #
# --------------------------------------------------------------------------- #


async def create_task_for_gap(
    session: AsyncSession,
    workspace: BidWorkspace,
    item: WorkspaceEvidenceItem,
    *,
    user: User,
) -> WorkspaceTask:
    """Converteix un forat del dossier en feina assignable. Sense IA.

    El titol surt de la plantilla de `dossier.py`, que reaprofita el de la
    checklist. Si ja hi ha una tasca oberta amb el mateix titol no se'n crea una
    altra: prement el boto dues vegades no s'ha d'omplir la llista de duplicats.
    """
    checklist_item = (
        await session.get(WorkspaceChecklistItem, item.checklist_item_id)
        if item.checklist_item_id
        else None
    )
    requirement = (
        await session.get(Requirement, item.requirement_id) if item.requirement_id else None
    )
    title = task_title_for_gap(
        checklist_item.title if checklist_item else None,
        requirement.title if requirement else item.label,
    )

    existing = await session.scalar(
        sa.select(WorkspaceTask).where(
            WorkspaceTask.workspace_id == workspace.id,
            WorkspaceTask.title == title,
            WorkspaceTask.status.not_in(list(CLOSED_TASKS)),
        )
    )
    if existing is not None:
        return existing

    mandatory = bool(
        requirement and requirement.obligation == RequirementObligation.MANDATORY
    )
    task = WorkspaceTask(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        title=title,
        description=(
            f"Forat del dossier documental: {item.label}" if item.label else None
        ),
        mandatory=mandatory,
        status=WorkspaceTaskStatus.PENDING,
        created_by=user.id,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    # Una tasca obligatoria nova es exactament el que ha de treure el workspace
    # de "llest": queda feina pendent.
    await reconcile_readiness(session, workspace)
    return task


