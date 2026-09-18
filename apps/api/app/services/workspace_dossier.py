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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.compliance import CompanyRequirementAssessment
from app.models.documents import Document, DocumentLink
from app.models.enums import (
    ChecklistItemSource,
    ChecklistItemStatus,
    CompanyEvidenceKind,
    DocumentValidity,
    DossierCoverage,
    EvidenceItemSource,
    EvidenceItemStatus,
    MembershipRole,
    RequirementCategory,
    RequirementObligation,
    SubmissionPackageStatus,
    WorkspaceDecisionType,
    WorkspaceStatus,
    WorkspaceTaskStatus,
)
from app.models.identity import User
from app.models.requirements import Requirement
from app.models.submission import SubmissionPackage
from app.models.workspace import (
    BidWorkspace,
    WorkspaceChecklistItem,
    WorkspaceEvidenceItem,
    WorkspaceTask,
)
from app.services import bid_workspace as workspace_service
from app.workspace.checklist import checklist_title
from app.workspace.dossier import (
    COVERING_STATUSES,
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
from app.workspace.lineage import (
    ItemFacts,
    human_weight,
    plan_migration,
)
from app.workspace.manifest import MANIFEST_VERSION
from app.workspace.relevance import (
    ESTABLISHED,
    RelevanceInput,
    RelevanceState,
    link_type_for,
    relevance_of,
    required_document_count,
    requirement_fingerprint,
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



async def _linked_documents(
    session: AsyncSession, items: list[WorkspaceEvidenceItem]
) -> set[tuple[uuid.UUID, str, uuid.UUID]]:
    """Quins documents estan enllaçats a quina entitat corporativa.

    Es la via forta de pertinença: si el document penja de la MATEIXA polissa o
    del MATEIX projecte que el compliment va citar, no cal que ho confirmi
    ningu. Es consulta d'un cop per a tot el dossier.
    """
    wanted = [
        (item.document_id, link_type_for(item.company_evidence_type), item.company_evidence_id)
        for item in items
        if item.document_id and item.company_evidence_type and item.company_evidence_id
    ]
    wanted = [entry for entry in wanted if entry[1] is not None]
    if not wanted:
        return set()
    rows = (
        await session.execute(
            sa.select(
                DocumentLink.document_id, DocumentLink.entity_type, DocumentLink.entity_id
            ).where(
                DocumentLink.document_id.in_({d for d, _, _ in wanted}),
                DocumentLink.entity_id.in_({e for _, _, e in wanted}),
            )
        )
    ).all()
    return {(row[0], str(row[1]), row[2]) for row in rows}


def _relevance_of(
    item: WorkspaceEvidenceItem,
    requirement: Requirement | None,
    document: Document | None,
    linked: set[tuple[uuid.UUID, str, uuid.UUID]],
) -> str:
    """L'estat de pertinença d'una peça, amb la confirmacio ja validada.

    La confirmacio humana nomes compta si es va signar sobre AQUEST document i
    sobre AQUESTA versio del requisit. No s'esborra res quan deixa de valer: la
    fila conserva qui la va fer i quan, i el que canvia es que ja no cobreix.
    """
    confirmed = bool(
        item.relevance_confirmed_at
        and document is not None
        and item.relevance_document_id == document.id
        and (
            requirement is None
            or item.relevance_requirement_fingerprint
            == requirement_fingerprint(
                requirement.category, requirement.title, requirement.value_json
            )
        )
    )
    link_type = link_type_for(item.company_evidence_type)
    is_linked = bool(
        document is not None
        and link_type is not None
        and item.company_evidence_id
        and (document.id, str(link_type), item.company_evidence_id) in linked
    )
    if (
        not is_linked
        and document is not None
        and str(item.company_evidence_type or "") == str(CompanyEvidenceKind.DOCUMENT)
    ):
        # El compliment va citar un document directament: la pertinença es
        # identitat, no enllaç.
        is_linked = item.company_evidence_id == document.id
    return relevance_of(
        RelevanceInput(
            requirement_category=requirement.category if requirement else None,
            document_category=document.category if document else None,
            linked_to_compliance_evidence=is_linked,
            human_confirmed=confirmed,
        )
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
    #: Com de sostinguda esta la pertinença del document (`RelevanceState`). La
    #: fila ho ha de dir: triar un paper i acreditar-hi una exigencia son coses
    #: diferents.
    relevance: str = RelevanceState.UNPROVEN


@dataclass(frozen=True, slots=True)
class Dossier:
    rows: list[DossierRow]
    coverage_by_requirement: dict[uuid.UUID, DossierCoverage]
    counters: DossierCoverageCounters
    readiness: ReadinessResult
    deadline: datetime | None
    #: Files que pengen d'una extraccio superada. No compten per a la cobertura
    #: ni per a la readiness, i no s'amaguen: son la feina que es va fer quan el
    #: plec deia una altra cosa.
    superseded: list[DossierRow] = field(default_factory=list)

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
    superseded: list[DossierRow] = []
    views: dict[uuid.UUID, list[EvidenceView]] = {}
    mandatory_requirements: set[uuid.UUID] = set()
    expired_mandatory = 0
    linked = await _linked_documents(session, items)
    # Un item es actiu si el seu requisit es del run vigent. No es guarda en cap
    # columna: es deriva, i per tant no pot dir mai una cosa diferent de la que
    # diuen les dades. Sense run vigent no hi ha res de superat.
    live = await live_requirement_ids(session, workspace.tender_id)

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
        relevance = _relevance_of(item, requirement, document, linked)
        # Una peca soltra (sense requisit) no depen de cap extraccio: sempre activa.
        stale = (
            live is not None
            and item.requirement_id is not None
            and item.requirement_id not in live
        )

        (superseded if stale else rows).append(
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
                relevance=relevance,
            )
        )

        if item.requirement_id is None or stale:
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
                relevance=relevance,
                required_pieces=required_document_count(
                    requirement.value_json if requirement else None
                ),
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
        # "Ha comencat" vol dir que hi ha feina de dossier, encara que el plec
        # hagi canviat i tota hagi passat a historia. Que despres no pugui estar
        # a punt ho diu `analysis_outdated`, que es una altra cosa.
        started=bool(items),
        needs_review_items=sum(
            1
            for row in rows
            if row.item.status == EvidenceItemStatus.NEEDS_REVIEW
        ),
        unproven_mandatory=sum(
            1
            for pieces in views.values()
            for view in pieces
            if view.mandatory
            and view.status in COVERING_STATUSES
            and view.relevance not in ESTABLISHED
        ),
    )

    return Dossier(
        rows=rows,
        coverage_by_requirement=coverage,
        counters=counters,
        readiness=readiness,
        deadline=state.deadline_at,
        superseded=superseded,
    )


# --------------------------------------------------------------------------- #
# Manifest del que es presentaria                                               #
# --------------------------------------------------------------------------- #


def selected_rows(dossier: Dossier) -> list[DossierRow]:
    """Les peces triades, en un ordre estable.

    L'ordre no pot dependre de com la base de dades retorni les files: el hash
    del manifest hi va a sobre.
    """
    rows = [
        row
        for row in dossier.rows
        if row.item.status == EvidenceItemStatus.SELECTED and row.document is not None
    ]
    rows.sort(key=lambda row: (row.document.sha256, str(row.item.id)))
    return rows


async def current_manifest(
    session: AsyncSession, workspace: BidWorkspace, *, revision_number: int
) -> tuple[list[DossierRow], dict, str]:
    """El manifest del que es presentaria ARA, amb el seu hash.

    Des d'S7 el manifest cobreix tres dominis —dossier, solucio tecnica i oferta
    economica— i muntar-lo ja no es feina del dossier: viu a
    `services/submission_manifest`. Aquesta funcio es queda com a punt d'entrada
    perque qui la cridava no hagi de saber que ha canviat de casa, i perque
    segueixi havent-hi UNA sola implementacio: congelar i detectar obsolescencia
    han de produir exactament el mateix hash.

    La importacio va aqui dins perque aquell modul consumeix aquest: al reves
    seria un cicle.
    """
    from app.services import submission_manifest

    return await submission_manifest.current_manifest(
        session, workspace, revision_number=revision_number
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
    unproven_mandatory: int = 0,
) -> ReadinessResult:
    live = await live_requirement_ids(session, workspace.tender_id)
    blocked_conditions = [
        WorkspaceChecklistItem.workspace_id == workspace.id,
        WorkspaceChecklistItem.mandatory.is_(True),
        WorkspaceChecklistItem.status == ChecklistItemStatus.BLOCKED,
    ]
    if live is not None:
        # Un bloqueig sobre una exigencia que el plec ja no fa no bloqueja res.
        blocked_conditions.append(
            sa.or_(
                WorkspaceChecklistItem.requirement_id.is_(None),
                WorkspaceChecklistItem.requirement_id.in_(live),
            )
        )
    blocked = int(
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(WorkspaceChecklistItem)
            .where(
                *blocked_conditions,
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
            unproven_mandatory_evidence=unproven_mandatory,
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


@dataclass(frozen=True, slots=True)
class ReadinessPlan:
    """El que l'estat guardat HAURIA de dir ara. Nomes calculat: no toca res.

    Les lectures el fan servir per respondre amb l'estat efectiu. Les mutacions
    el passen per `reconcile_readiness()`, que es l'unic que el desa.
    """

    readiness: ReadinessResult
    #: L'estat que hauria de tenir el workspace. Es el guardat si no cal canviar-lo.
    status: WorkspaceStatus
    #: El paquet que ja no descriu el dossier i hauria de quedar superat, si n'hi ha.
    superseded_package_id: uuid.UUID | None = None

    def package_status(self, package: SubmissionPackage) -> SubmissionPackageStatus:
        """L'estat efectiu d'un paquet segons aquest pla."""
        if package.id == self.superseded_package_id:
            return SubmissionPackageStatus.SUPERSEDED
        return package.status


async def final_gate(
    session: AsyncSession, workspace: BidWorkspace
) -> ReadinessResult:
    """El veredicte que decideix si una candidatura pot estar a revisio final.

    El dossier per si sol ja no es prou: des d'S6 tambe hi compten la solucio
    tecnica i l'oferta economica. Aquest modul segueix sent el dossier i no sap
    avaluar-los; nomes demana el veredicte agregat i el rep amb la forma que ja
    feia servir, per no obrir una segona transicio en paral·lel.

    La importacio va aqui dins i no a dalt perque l'agregador consumeix aquest
    modul: al reves seria un cicle. Es l'unic punt de contacte entre els dos.
    """
    from app.services import workspace_readiness

    return (await workspace_readiness.final_readiness(session, workspace)).as_readiness_result()


async def plan_readiness(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    readiness: ReadinessResult | None = None,
) -> ReadinessPlan:
    """L'estat efectiu del workspace, calculat des de zero i SENSE persistir-lo.

    Es el que fan servir els GET: poden ensenyar que un workspace ja no esta a
    punt encara que l'etiqueta guardada digui el contrari, pero no la canvien.
    Una lectura que escriu va ser exactament el que va degradar un workspace
    real mentre nomes s'estava comprovant que les dades es llegien be.
    """
    result = readiness or await evaluate_workspace_readiness(session, workspace)
    # El que es MOSTRA es el dossier; el que DECIDEIX l'estat es la porta final.
    # Separar-ho es el que permet ensenyar "el dossier esta complet" i alhora no
    # deixar arribar a revisio final una candidatura sense oferta.
    target, superseded = await _reconciled_status(
        session, workspace, await final_gate(session, workspace)
    )
    return ReadinessPlan(
        readiness=result,
        status=target or workspace.status,
        superseded_package_id=superseded,
    )


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

    **Nomes des de camins de mutacio explicits.** Les lectures fan servir
    `plan_readiness()` i no persisteixen res. Per aixo `workspace.status` no es
    cap garantia: un canvi extern pot deixar l'etiqueta enrere fins a la propera
    mutacio. Qualsevol aprovacio final ha de tornar a cridar
    `evaluate_workspace_readiness()` i no fiar-se de l'estat.
    """
    plan = await plan_readiness(session, workspace, readiness=readiness)
    if plan.superseded_package_id is not None:
        package = await session.get(SubmissionPackage, plan.superseded_package_id)
        if package is not None and package.status != SubmissionPackageStatus.SUPERSEDED:
            package.status = SubmissionPackageStatus.SUPERSEDED
    if plan.status != workspace.status:
        workspace.status = workspace_service.set_status(
            workspace, plan.status, actor=TransitionActor.SYSTEM
        )
    if session.dirty or session.new:
        await session.commit()
        await session.refresh(workspace)
    return plan.readiness


async def _reconciled_status(
    session: AsyncSession, workspace: BidWorkspace, readiness: ReadinessResult
) -> tuple[WorkspaceStatus | None, uuid.UUID | None]:
    """A quin estat ha de tornar el workspace, i quin paquet queda superat.

    Nomes calcula: no assigna res a cap objecte de la sessio.

    Dues etiquetes que no poden mentir:

    * `ready_for_final_review` vol dir "el dossier esta complet". Si ja no ho
      esta, cap enrere.
    * `approved_for_submission` vol dir "la revisio ACTUAL del paquet, coherent
      amb el dossier actual, esta aprovada". No vol dir "algun dia es va aprovar
      alguna cosa": aixo ho diu l'historial d'aprovacions, que es immutable i no
      es toca mai.
    """
    latest = await _latest_package(session, workspace)

    # Un manifest d'un format anterior no es pot comparar per hash —son dues
    # serialitzacions diferents i el resultat seria sempre "ha canviat"— aixi que
    # el que compta es si algu ja el va aprovar (C-17):
    #
    # * aprovat: es queda aprovat. L'estat diu que va passar amb AQUELLA
    #   fotografia, no si avui seria la candidata;
    # * nomes congelat: queda superat ja, sense esperar que ningu provi
    #   d'aprovar-lo, perque amb el contracte d'avui no ho es.
    #
    # Aixo es mira ABANS que l'estat del workspace: passa igual si la
    # candidatura esta a revisio final o ja aprovada.
    outdated = (
        latest is not None
        and latest.status == SubmissionPackageStatus.FROZEN
        and (latest.manifest_json or {}).get("manifest_version")
        not in (None, MANIFEST_VERSION)
    )
    legacy_superseded = latest.id if outdated else None

    if workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW:
        return (
            None if readiness.is_ready else WorkspaceStatus.PREPARING_BID
        ), legacy_superseded

    if workspace.status != WorkspaceStatus.APPROVED_FOR_SUBMISSION:
        return None, legacy_superseded

    latest_status = latest.status if latest is not None else None
    superseded: uuid.UUID | None = legacy_superseded
    if outdated:
        latest_status = SubmissionPackageStatus.SUPERSEDED
    elif latest is not None and latest.status in (
        SubmissionPackageStatus.FROZEN,
        SubmissionPackageStatus.APPROVED,
    ):
        # El paquet ja no descriu el que hi ha: queda superat. L'aprovacio que
        # tingues segueix sent valida per a AQUELLA fotografia; el que deixa de
        # ser cert es que sigui la fotografia d'ara.
        version = (latest.manifest_json or {}).get("manifest_version")
        if version is None or version == MANIFEST_VERSION:
            _rows, _payload, digest = await current_manifest(
                session, workspace, revision_number=latest.revision_number
            )
            if digest != latest.manifest_hash:
                superseded = latest.id
                latest_status = SubmissionPackageStatus.SUPERSEDED

    if latest_status == SubmissionPackageStatus.APPROVED and readiness.is_ready:
        return None, superseded
    return (
        WorkspaceStatus.READY_FOR_FINAL_REVIEW
        if readiness.is_ready
        else WorkspaceStatus.PREPARING_BID
    ), superseded


async def _latest_package(
    session: AsyncSession, workspace: BidWorkspace
) -> SubmissionPackage | None:
    """L'ultima revisio del paquet. Es consulta el model i no el servei de
    paquets: al reves hi hauria una dependencia circular, i qui mana sobre
    l'estat del workspace es aquesta reconciliacio."""
    return await session.scalar(
        sa.select(SubmissionPackage)
        .where(SubmissionPackage.workspace_id == workspace.id)
        .order_by(SubmissionPackage.revision_number.desc())
        .limit(1)
    )


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
    await reconcile_readiness(session, workspace)
    readiness = await final_gate(session, workspace)
    if not readiness.is_ready:
        raise WorkspaceNotReadyError(
            "La candidatura encara no esta a punt per a la revisio final.",
            details={"blockers": list(readiness.blockers)},
        )

    if workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW:
        # Ja hi es: demanar-ho dues vegades no es un error, es el mateix estat.
        return readiness

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
    previous_document = item.document_id
    if clear_document:
        item.document_id = None
        item.company_evidence_type = None
        item.company_evidence_id = None
    elif document_id is not None:
        document = await _company_document(session, workspace.company_id, document_id)
        item.document_id = document.id
        item.label = document.name[:300]
        item.source = EvidenceItemSource.MANUAL

    if clear_document or (document_id is not None and document_id != previous_document):
        # Confirmar que un paper era el bo no diu res del paper seguent. La
        # confirmacio no s'hereta: s'esborra i es torna a demanar.
        _forget_relevance(item)

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



def _forget_relevance(item: WorkspaceEvidenceItem) -> None:
    """Oblida una confirmacio de pertinença. Nomes quan deixa de tenir subjecte."""
    item.relevance_confirmed_by = None
    item.relevance_confirmed_at = None
    item.relevance_note = None
    item.relevance_document_id = None
    item.relevance_requirement_fingerprint = None


async def confirm_relevance(
    session: AsyncSession,
    workspace: BidWorkspace,
    item: WorkspaceEvidenceItem,
    *,
    note: str,
    user: User,
) -> WorkspaceEvidenceItem:
    """Una persona diu que aquest document SI que acredita aquest requisit.

    Es una afirmacio diferent de "l'he triat", i per aixo te la seva firma i el
    seu motiu. El sistema no la pot fer sol: sap que la categoria encaixa, no
    que aquest sigui EL document que el plec demana.

    Queda lligada al document i a la versio del requisit. Si despres se'n tria
    un altre o el plec canvia el que demana, la confirmacio segueix a la fila
    —es historia— pero deixa de donar cobertura.
    """
    reason = (note or "").strip()
    if not reason:
        raise ValidationError("Cal escriure per que aquest document acredita el requisit.")
    if item.document_id is None:
        raise DocumentNotAvailableError(
            "No es pot confirmar la pertinença d'una peca sense document."
        )

    requirement = (
        await session.get(Requirement, item.requirement_id) if item.requirement_id else None
    )
    item.relevance_confirmed_by = user.id
    item.relevance_confirmed_at = datetime.now(UTC)
    item.relevance_note = reason
    item.relevance_document_id = item.document_id
    item.relevance_requirement_fingerprint = (
        requirement_fingerprint(
            requirement.category, requirement.title, requirement.value_json
        )
        if requirement is not None
        else None
    )
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




# --------------------------------------------------------------------------- #
# Reconciliacio amb l'analisi vigent                                            #
# --------------------------------------------------------------------------- #


async def live_requirement_ids(
    session: AsyncSession, tender_id: uuid.UUID
) -> set[uuid.UUID] | None:
    """Els requisits de l'extraccio vigent. `None` quan no n'hi ha cap.

    `None` i conjunt buit volen dir coses diferents: sense analisi vigent no hi
    ha res de superat —no hi ha contra que comparar—, mentre que una analisi
    vigent sense cap requisit si que deixa tot el que hi havia com a historia.
    """
    run = await workspace_service.current_requirement_run(session, tender_id)
    if run is None:
        return None
    return {
        r
        for r in (
            await session.scalars(
                sa.select(Requirement.id).where(Requirement.run_id == run.id)
            )
        ).all()
    }


async def active_checklist_items(
    session: AsyncSession, workspace: BidWorkspace
) -> list[WorkspaceChecklistItem]:
    """La checklist que l'usuari ha de fer, sense el que ja es historia."""
    items = await workspace_service.checklist_items(session, workspace.id)
    live = await live_requirement_ids(session, workspace.tender_id)
    if live is None:
        return items
    return [i for i in items if i.requirement_id is None or i.requirement_id in live]


async def _current_requirements(
    session: AsyncSession, workspace: BidWorkspace
) -> tuple[uuid.UUID | None, list[Requirement]]:
    """Els requisits del run vigent que afecten aquest workspace."""
    run = await workspace_service.current_requirement_run(session, workspace.tender_id)
    if run is None:
        return None, []
    return run.id, await workspace_service.relevant_requirements(session, workspace)


def _fingerprint(requirement: Requirement) -> str:
    return requirement_fingerprint(
        requirement.category, requirement.title, requirement.value_json
    )


def _evidence_weight(item: WorkspaceEvidenceItem) -> int:
    return human_weight(
        relevance_confirmed=item.relevance_confirmed_at is not None,
        selected=item.selected_at is not None
        or item.status == EvidenceItemStatus.SELECTED,
        touched=bool(item.notes) or item.status
        in {EvidenceItemStatus.NEEDS_REVIEW, EvidenceItemStatus.NOT_APPLICABLE},
        manual=item.source == EvidenceItemSource.MANUAL,
    )


def _checklist_weight(item: WorkspaceChecklistItem) -> int:
    return human_weight(
        selected=item.status == ChecklistItemStatus.COMPLETED,
        touched=item.status != ChecklistItemStatus.PENDING
        or item.assigned_to is not None
        or bool(item.note),
        manual=item.source == ChecklistItemSource.MANUAL,
    )


async def _requirements_of(
    session: AsyncSession, ids: set[uuid.UUID]
) -> dict[uuid.UUID, Requirement]:
    if not ids:
        return {}
    return {
        r.id: r
        for r in (
            await session.scalars(sa.select(Requirement).where(Requirement.id.in_(ids)))
        ).all()
    }


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """Que ha passat amb la feina que ja hi havia."""

    #: Items que ara pengen del requisit vigent equivalent, amb tot el que
    #: portaven: document, tria, notes i confirmacio de pertinença.
    carried: int
    #: Quants items han quedat fora del que es demana ara. Es un ESTAT, no una
    #: cosa que hagi fet aquesta crida: qui els va deixar enrere va ser la
    #: reanalisi. Reconciliar dues vegades el torna a comptar igual, i per aixo
    #: no s'ha de llegir mai com "acaba de passar".
    historic: int
    #: Propostes automatiques duplicades que ningu no havia tocat.
    discarded: int
    #: Items nous creats per als requisits que abans no en tenien.
    created_checklist: int
    created_evidence: int


async def reconcile_with_current_analysis(
    session: AsyncSession, workspace: BidWorkspace, *, user_id: uuid.UUID | None
) -> ReconcileOutcome:
    """Posa la candidatura a treballar contra l'analisi vigent.

    L'ordre importa i no es negociable:

    1. **Primer es mou el que es pot moure.** Un item la exigencia del qual
       segueix sent la mateixa —mateixa empremta— passa a apuntar al requisit
       nou sencer: document, tria, notes i confirmacio de pertinença. Si es
       creessin items nous abans, el lloc estaria ocupat i la feina humana
       quedaria orfe en una fila inactiva.
    2. **Despres es crea el que falta**, que es el que ja feien `generate_checklist`
       i `sync_dossier`.
    3. **I al final s'actualitzen els punters** del workspace.

    El que **no** fa, en cap cas, es heretar una confirmacio humana cap a un
    requisit que demana una altra cosa. L'empremta ho impedeix: si el plec ha
    canviat el que demana, l'empremta canvia, no hi ha correspondencia i l'item
    vell es historia mentre el nou neix sense cap firma a sobre.
    """
    run_id, current = await _current_requirements(session, workspace)
    if run_id is None:
        # Sense analisi vigent no hi ha res contra que reconciliar.
        return ReconcileOutcome(0, 0, 0, 0, 0)

    current_by_fingerprint = {_fingerprint(r): r.id for r in current}
    current_ids = {r.id for r in current}

    evidence = list(
        (
            await session.scalars(
                sa.select(WorkspaceEvidenceItem).where(
                    WorkspaceEvidenceItem.workspace_id == workspace.id
                )
            )
        ).all()
    )
    checklist = await workspace_service.checklist_items(session, workspace.id)

    known = await _requirements_of(
        session,
        {i.requirement_id for i in evidence if i.requirement_id}
        | {i.requirement_id for i in checklist if i.requirement_id},
    )

    def facts(item, weight: int) -> ItemFacts:
        requirement = known.get(item.requirement_id) if item.requirement_id else None
        return ItemFacts(
            id=item.id,
            requirement_id=item.requirement_id,
            fingerprint=_fingerprint(requirement) if requirement else None,
            on_current_run=item.requirement_id in current_ids,
            weight=weight,
            document_id=getattr(item, "document_id", None),
        )

    evidence_plan = plan_migration(
        [facts(item, _evidence_weight(item)) for item in evidence], current_by_fingerprint
    )
    checklist_plan = plan_migration(
        [facts(item, _checklist_weight(item)) for item in checklist], current_by_fingerprint
    )

    by_id: dict[uuid.UUID, object] = {item.id: item for item in evidence}
    by_id.update({item.id: item for item in checklist})

    def apply(plan) -> None:
        for entry in plan.rebind:
            by_id[entry.item_id].requirement_id = entry.requirement_id

    apply(evidence_plan)
    apply(checklist_plan)
    for item_id in (*evidence_plan.drop, *checklist_plan.drop):
        await session.delete(by_id[item_id])
    await session.flush()

    # Els titols els deriva el requisit vigent, no el que hi havia escrit.
    current_by_id = {r.id: r for r in current}
    for entry in checklist_plan.rebind:
        item = by_id[entry.item_id]
        requirement = current_by_id.get(entry.requirement_id)
        if requirement is not None and item.source == ChecklistItemSource.REQUIREMENT:
            item.title = checklist_title(requirement)[:500]
            item.category = requirement.category
            item.mandatory = requirement.obligation == RequirementObligation.MANDATORY

    created_checklist = await workspace_service.generate_checklist(
        session, workspace, user_id=user_id
    )
    sync = await sync_dossier(session, workspace, user_id=user_id)

    run = await workspace_service.current_requirement_run(session, workspace.tender_id)
    if run is not None:
        workspace.current_requirement_run_id = run.id
    compliance = await workspace_service.current_compliance_run(
        session, workspace.company_id, workspace.tender_id
    )
    if compliance is not None:
        workspace.current_compliance_run_id = compliance.id

    await session.commit()
    await session.refresh(workspace)
    await reconcile_readiness(session, workspace)
    return ReconcileOutcome(
        carried=len(evidence_plan.rebind) + len(checklist_plan.rebind),
        historic=len(evidence_plan.supersede) + len(checklist_plan.supersede),
        discarded=len(evidence_plan.drop) + len(checklist_plan.drop),
        created_checklist=created_checklist,
        created_evidence=sync.suggested + sync.gaps,
    )
