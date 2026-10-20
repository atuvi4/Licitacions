"""Orquestracio del workspace de candidatura.

L'unic modul d'aquesta capa que toca la base de dades. Les regles (plantilles de
checklist, quan cal un motiu escrit) son funcions pures a `app.workspace`.

Cap crida a cap LLM, ni aqui ni a sota. Un workspace reuneix el que ja han
calculat 4A (pre-fit), 4B (requisits) i 4C (compliment) i hi afegeix el que
nomes pot posar una persona: la decisio.

Immutabilitat: `WorkspaceDecision` no s'actualitza mai. `record_decision`
insereix; no hi ha cap `update_decision` perque no ha d'existir.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from app.compliance import consolidation
from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.models.compliance import CompanyComplianceRun, CompanyRequirementAssessment
from app.models.enums import (
    AnalysisBasis,
    AssessmentResult,
    AuditAction,
    BlockerStatus,
    ChecklistItemSource,
    ChecklistItemStatus,
    EligibilityResult,
    MembershipRole,
    RequirementObligation,
    WorkspaceDecisionType,
    WorkspaceStatus,
    WorkspaceTaskStatus,
)
from app.models.identity import CompanyMembership, User
from app.models.matching import CompanyTenderMatch
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Tender, TenderLot
from app.models.workspace import (
    BidWorkspace,
    WorkspaceChecklistItem,
    WorkspaceDecision,
    WorkspaceNote,
    WorkspaceTask,
)
from app.services import audit
from app.services.tender_analysis import AnalysisStatus, current_analysis, package_state
from app.services.tender_lifecycle import (
    ChangeLog,
    is_actionable,
    load_change_log,
    load_change_logs,
    lot_is_gone,
    revision_of,
)
from app.tenders.changes import ChangeImpact
from app.tenders.deadline import days_until_deadline, deadline_has_passed
from app.workspace.analysis_snapshot import build_snapshot
from app.workspace.checklist import (
    applies_to_workspace_lot,
    checklist_title,
    generates_checklist_item,
)
from app.workspace.decision import (
    AnalysisDecisionWarnings,
    DecisionWarnings,
    requires_reason,
)
from app.workspace.transitions import (
    CLOSED_STATUSES,
    TransitionActor,
    WorkspaceTransitionError,
    apply_transition,
    status_for,
)

#: Qui pot signar un GO o un NO-GO. Un `viewer` pot llegir-ho tot i no decideix
#: res; un `technical` prepara la candidatura pero no compromet l'empresa.
DECISION_ROLES = frozenset(
    {MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.BID_MANAGER}
)

#: Candidatura que ja no es oberta (`transitions.CLOSED_STATUSES`, el mateix
#: conjunt que `isOpenWorkspace` del web).
CLOSED_WORKSPACE_STATUSES = CLOSED_STATUSES


class WorkspaceExistsError(ValidationError):
    """Ja hi ha workspace per a aquesta empresa, licitacio i lot."""

    code = "workspace_already_exists"


#: La base dels avisos que obliguen motiu: l'analisi vigent del plec (v3).
DECISION_WARNING_BASIS = "analysis_v3"


class DecisionReasonRequiredError(ValidationError):
    """GO amb avisos oberts sense justificacio escrita."""

    code = "decision_reason_required"

    def __init__(self, message: str, warnings: list[str]) -> None:
        super().__init__(
            message, details={"warnings": warnings, "basis": DECISION_WARNING_BASIS}
        )


class NotAssignableError(ValidationError):
    """La persona assignada no pertany a aquesta empresa."""

    code = "assignee_not_in_company"


class WorkspaceTransitionRejectedError(ConflictError):
    """Transicio d'estat impossible o sense potestat per fer-la.

    Tradueix l'error pur de `app.workspace.transitions` a un error d'API. El
    modul de transicions no depen de FastAPI a proposit: ha de poder-se provar
    sol.
    """

    code = "workspace_transition_rejected"


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


async def get_workspace(
    session: AsyncSession, company_id: uuid.UUID, workspace_id: uuid.UUID
) -> BidWorkspace:
    """Sempre filtrat per empresa: un tenant no pot adrecar el d'un altre.

    404 i no 403 quan es d'una altra empresa, com a la resta de l'API: confirmar
    l'existencia d'un workspace alie ja seria una fuita.
    """
    workspace = await session.scalar(
        sa.select(BidWorkspace).where(
            BidWorkspace.id == workspace_id, BidWorkspace.company_id == company_id
        )
    )
    if workspace is None:
        raise NotFoundError("Workspace no trobat.")
    return workspace


async def find_workspace(
    session: AsyncSession,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    tender_lot_id: uuid.UUID | None = None,
) -> BidWorkspace | None:
    condition = (
        BidWorkspace.tender_lot_id == tender_lot_id
        if tender_lot_id is not None
        else BidWorkspace.tender_lot_id.is_(None)
    )
    return await session.scalar(
        sa.select(BidWorkspace).where(
            BidWorkspace.company_id == company_id,
            BidWorkspace.tender_id == tender_id,
            condition,
        )
    )


def _current_compliance(company_id: uuid.UUID) -> tuple[sa.ColumnElement[bool], ...]:
    """El compliment vigent d'una empresa. Un sol lloc ho defineix."""
    return (
        CompanyComplianceRun.company_id == company_id,
        CompanyComplianceRun.is_current.is_(True),
    )


async def current_compliance_run(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> CompanyComplianceRun | None:
    return await session.scalar(
        sa.select(CompanyComplianceRun).where(
            *_current_compliance(company_id),
            CompanyComplianceRun.tender_id == tender_id,
        )
    )


async def current_compliance_runs(
    session: AsyncSession, company_id: uuid.UUID, tender_ids: set[uuid.UUID]
) -> dict[uuid.UUID, CompanyComplianceRun]:
    """`current_compliance_run` de cop per a diverses licitacions: una consulta."""
    if not tender_ids:
        return {}
    runs: dict[uuid.UUID, CompanyComplianceRun] = {}
    for run in (
        await session.scalars(
            sa.select(CompanyComplianceRun).where(
                *_current_compliance(company_id),
                CompanyComplianceRun.tender_id.in_(tender_ids),
            )
        )
    ).all():
        # Com `session.scalar`: si n'hi hagues mes d'un de vigent, el primer.
        runs.setdefault(run.tender_id, run)
    return runs


async def current_requirement_run(
    session: AsyncSession, tender_id: uuid.UUID, company_id: uuid.UUID | None = None
) -> RequirementExtractionRun | None:
    """La run de requisits que llegeix l'empresa: el seu punter o la canonica
    (3B-2 §6.5). Sense empresa, la canonica global."""
    from app.services import manual_requirement

    return await manual_requirement.effective_run(session, tender_id, company_id)


@dataclass(frozen=True, slots=True)
class ComplianceCounters:
    """Recompte human-readable del compliment vigent.

    Es calcula sobre els assessments i no sobre els comptadors del run perque
    un workspace pot ser d'un lot, i perque calen dues xifres que el run no
    desglossa: quants OBLIGATORIS estan en `unknown` i quants en `needs_review`.
    """

    #: Totals sobre TOTS els requisits avaluats.
    total: int = 0
    satisfies: int = 0
    does_not_satisfy: int = 0
    unknown: int = 0
    needs_review: int = 0
    not_applicable: int = 0
    #: Desglossament NOMES dels obligatoris: es el que decideix un Go/No-Go, i
    #: es el que ha de quadrar a la pantalla. `mandatory_total` es la suma dels
    #: cinc camps `mandatory_*` de sota.
    mandatory_total: int = 0
    mandatory_satisfies: int = 0
    mandatory_does_not_satisfy: int = 0
    mandatory_unknown: int = 0
    mandatory_needs_review: int = 0
    mandatory_not_applicable: int = 0
    potential_blockers: int = 0
    confirmed_blockers: int = 0

    @property
    def mandatory_unresolved(self) -> int:
        return self.mandatory_unknown + self.mandatory_needs_review


async def compliance_counters(
    session: AsyncSession, run: CompanyComplianceRun | None, tender_lot_id: uuid.UUID | None
) -> ComplianceCounters:
    if run is None:
        return ComplianceCounters()

    # `effective_result` es una propietat de Python; a SQL es el revisat si n'hi
    # ha i si no l'automatic. Ha de coincidir amb el model, no aproximar-lo.
    effective = sa.func.coalesce(
        CompanyRequirementAssessment.reviewed_result,
        CompanyRequirementAssessment.automatic_result,
    )
    conditions = [CompanyRequirementAssessment.assessment_run_id == run.id]
    if tender_lot_id is not None:
        conditions.append(
            sa.or_(
                CompanyRequirementAssessment.tender_lot_id == tender_lot_id,
                CompanyRequirementAssessment.tender_lot_id.is_(None),
            )
        )

    # Una fila per avaluacio i no un GROUP BY: els duplicats d'una mateixa
    # exigencia (`app.compliance.consolidation`, marcats a `explanation_params`)
    # compten un sol cop, igual que als comptadors del run.
    assessed = (
        await session.execute(
            sa.select(
                CompanyRequirementAssessment.requirement_id,
                effective,
                CompanyRequirementAssessment.obligation,
                CompanyRequirementAssessment.blocker_status,
                CompanyRequirementAssessment.explanation_params,
            ).where(*conditions)
        )
    ).all()
    rows = [
        (result, obligation, blocker, 1)
        for obligation, result, blocker in consolidation.resolve_rows(
            (
                consolidation.group_marker(requirement_id, params),
                RequirementObligation(obligation),
                AssessmentResult(result),
                BlockerStatus(blocker),
            )
            for requirement_id, result, obligation, blocker, params in assessed
        )
    ]

    totals = dict.fromkeys(
        (
            "total",
            "satisfies",
            "does_not_satisfy",
            "unknown",
            "needs_review",
            "not_applicable",
            "mandatory_total",
            "mandatory_satisfies",
            "mandatory_does_not_satisfy",
            "mandatory_unknown",
            "mandatory_needs_review",
            "mandatory_not_applicable",
            "potential_blockers",
            "confirmed_blockers",
        ),
        0,
    )
    for result, obligation, blocker, count in rows:
        result = AssessmentResult(result)
        mandatory = RequirementObligation(obligation) == RequirementObligation.MANDATORY
        totals["total"] += count
        totals[result.value] += count
        if mandatory:
            totals["mandatory_total"] += count
            totals[f"mandatory_{result.value}"] += count
        blocker_status = BlockerStatus(blocker)
        if blocker_status == BlockerStatus.POTENTIAL:
            totals["potential_blockers"] += count
        elif blocker_status == BlockerStatus.CONFIRMED:
            totals["confirmed_blockers"] += count

    return ComplianceCounters(**totals)


@dataclass(frozen=True, slots=True)
class WorkspaceState:
    """Tot el que la pantalla necessita saber, sense fusionar res.

    Pre-fit, elegibilitat, recomanacio automatica i decisio humana viatgen com a
    quatre camps diferents. Barrejar-los en un sol semafor es exactament el que
    aquesta iteracio evita.
    """

    workspace: BidWorkspace
    tender: Tender
    lot: TenderLot | None
    compliance_run: CompanyComplianceRun | None
    requirement_run: RequirementExtractionRun | None
    counters: ComplianceCounters
    prefit_score: int | None
    prefit_status: str | None
    latest_decision: WorkspaceDecision | None
    analysis_outdated: bool
    outdated_reasons: tuple[str, ...]
    deadline_at: datetime | None
    days_remaining: int | None
    deadline_passed: bool
    #: La revisio vigent de la licitacio. `None` si encara no se n'ha enregistrat
    #: cap: no vol dir que no hagi canviat, vol dir que no ho sabem.
    tender_revision: TenderRevision | None = None
    #: Canvis materials publicats DESPRES de l'ultima decisio humana.
    changes_since_decision: tuple[TenderChangeEvent, ...] = ()
    #: Cert quan l'expedient ja no admet presentacio (cancel.lat o equivalent).
    tender_actionable: bool = True
    #: Cert quan el lot d'aquesta candidatura ja no es al feed.
    lot_removed: bool = False
    #: Per que la decisio humana no es pot donar per vigent. Buit = ho es.
    #: Codis, no frases: la interficie en deriva el text.
    decision_reasons: tuple[str, ...] = ()
    #: L'analisi v3 vigent del plec i si esta caducada. `None` = no s'ha
    #: carregat (`load_state(..., with_analysis=False)`): les llistes no la
    #: calculen perque `is_stale` es car.
    analysis: AnalysisStatus | None = None
    #: Els avisos v3 que obliguen motiu per a un GO. `None` si no s'ha
    #: carregat l'analisi.
    analysis_warnings: AnalysisDecisionWarnings | None = None

    @property
    def decision_outdated(self) -> bool:
        """Si la decisio humana no es pot donar per vigent.

        Es DERIVAT i no una bandera desada. Una bandera s'hauria de mantenir
        sincronitzada des de la ingestió, i el dia que algu s'oblides de posar-la
        el sistema diria que una decisio es vigent quan no ho es. Aixo es la
        mena de mentida que aquesta fase existeix per evitar.

        La decisio en si no es toca mai: segueix dient qui, quan i sobre quin
        estat, i ningu la torna a signar. El que caduca es la seva vigencia.
        """
        return bool(self.decision_reasons)

    @property
    def decision_basis_unknown(self) -> bool:
        """La decisio es d'abans que la licitacio tingues historia.

        No es pot afirmar que hagi quedat obsoleta, pero tampoc que segueixi
        vigent: no sabem sobre quin estat es va prendre. Entre les dues
        mentides, cap: es diu que no se sap i s'obliga a mirar-s'ho. Es el cas
        de tota decisio anterior a aquesta fase.
        """
        return "revision_basis_unknown" in self.decision_reasons

    @property
    def workspace_actionable(self) -> bool:
        """Si encara te sentit preparar aquesta candidatura.

        Dues maneres de deixar de tenir-ne: que caigui l'expedient sencer o que
        caigui NOMES el lot al qual ens presentavem. La segona es tan definitiva
        com la primera per a qui hi treballava, i abans era invisible.
        """
        return self.tender_actionable and not self.lot_removed

    @property
    def warnings(self) -> DecisionWarnings:
        return DecisionWarnings(
            confirmed_blockers=self.counters.confirmed_blockers,
            potential_blockers=self.counters.potential_blockers,
            mandatory_unknown=self.counters.mandatory_unknown,
            mandatory_needs_review=self.counters.mandatory_needs_review,
            deadline_passed=self.deadline_passed,
            missing_compliance=self.compliance_run is None,
            undetermined_eligibility=self.eligibility_undetermined,
            unreliable_analysis=not self.analysis_basis.is_trustworthy,
            outdated_analysis=self.analysis_outdated,
        )

    @property
    def analysis_basis(self) -> AnalysisBasis:
        """Sobre que s'ha construit el que ensenya aquesta candidatura.

        Sense cap run de requisits no hi ha analisi de cap mena, i tampoc es
        pot dir que sigui de fiar: es tracta igual que una simulacio.
        """
        return analysis_basis_of(self.requirement_run)

    @property
    def eligibility_undetermined(self) -> bool:
        """Cert quan no hi ha veredicte, que NO es el mateix que no complir."""
        if self.compliance_run is None:
            return True
        return self.compliance_run.overall_result == EligibilityResult.UNDETERMINED


def _days_remaining(deadline: datetime | None, now: datetime | None = None) -> int | None:
    """Dies fins al termini. Calculat, no persistit: dema seria mentida."""
    return days_until_deadline(deadline, now)


# --------------------------------------------------------------------------- #
# Derivacions pures: les comparteixen `load_state` i la lectura per lots         #
# --------------------------------------------------------------------------- #


def analysis_basis_of(run: RequirementExtractionRun | None) -> AnalysisBasis:
    """Sobre que s'ha construit l'analisi: sense run, res de fiar (com una simulacio)."""
    if run is None:
        return AnalysisBasis.PROVIDER_NOT_CONFIGURED
    return AnalysisBasis.for_provider(run.provider)


def effective_deadline(tender: Tender, lot: TenderLot | None) -> datetime | None:
    """El termini autoritatiu: el del lot si el workspace n'es d'un; si no, el
    de l'expedient. El del workspace nomes es una fotografia."""
    return effective_deadline_of(tender.deadline_at, lot.deadline_at if lot else None)


def effective_deadline_of(
    tender_deadline: datetime | None, lot_deadline: datetime | None
) -> datetime | None:
    """`effective_deadline` a partir dels dos terminis, per a qui nomes llegeix columnes."""
    return lot_deadline or tender_deadline


def prefit_score_of(match: CompanyTenderMatch | None, workspace: BidWorkspace) -> int | None:
    """El pre-fit d'ara si n'hi ha; si no, el que es va capturar en obrir."""
    return match.score if match else workspace.prefit_score


def outdated_reasons(
    workspace: BidWorkspace,
    *,
    tender: Tender,
    lot: TenderLot | None,
    compliance: CompanyComplianceRun | None,
    requirements: RequirementExtractionRun | None,
    match: CompanyTenderMatch | None,
    log: ChangeLog,
    prefit_basis: TenderRevision | None,
    requirements_basis: TenderRevision | None,
    compliance_basis: TenderRevision | None,
) -> tuple[str, ...]:
    """Per que el que ensenya la candidatura ja no es al dia. Buit = ho es.

    Pura: les revisions de base (`*_basis`) son les dels identificadors que
    porten el pre-fit, la run de requisits i el compliment, ja llegides.
    """
    reasons: list[str] = []
    if requirements is not None and requirements.id != workspace.current_requirement_run_id:
        reasons.append("requirement_run_changed")
    if compliance is not None and compliance.id != workspace.current_compliance_run_id:
        reasons.append("compliance_run_changed")
    if compliance is None and workspace.current_compliance_run_id is not None:
        reasons.append("compliance_run_missing")

    # El pre-fit te component de calendari i d'import: el que el fa obsolet es
    # exactament el que la matriu marca com a `prefit_affecting`.
    if match is not None and log.is_outdated(prefit_basis, ChangeImpact.PREFIT_AFFECTING):
        reasons.append("prefit_outdated")

    # Els requisits surten dels PLECS. Que es mogui un import no els canvia:
    # "el 50% del pressupost base" segueix dient el mateix. Nomes els
    # `requirements_affecting` —documents— obliguen a tornar a llegir.
    if requirements is not None and log.is_outdated(
        requirements_basis, ChangeImpact.REQUIREMENTS_AFFECTING
    ):
        reasons.append("requirements_outdated")

    # El compliment si que depen dels imports: els llindars percentuals es
    # resolen contra el pressupost. Ni el perfil ni els requisits han canviat i
    # tots dos fingerprints segueixen iguals, pero el veredicte pot ser un altre.
    if compliance is not None and log.is_outdated(
        compliance_basis, ChangeImpact.COMPLIANCE_AFFECTING
    ):
        reasons.append("compliance_outdated")

    if not is_actionable(tender):
        reasons.append("tender_not_actionable")
    if lot_is_gone(lot):
        reasons.append("lot_removed")
    return tuple(reasons)


async def load_state(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    now: datetime | None = None,
    with_analysis: bool = False,
) -> WorkspaceState:
    """L'estat que veu la pantalla.

    Amb `with_analysis` tambe llegeix l'analisi v3 vigent (`current_analysis`,
    la mateixa font que `GET /analysis/current`). Un workspace de lot usa el
    run de l'expedient: l'analisi v3 es per empresa i licitacio.
    """
    tender = await session.get(Tender, workspace.tender_id)
    if tender is None:  # pragma: no cover - FK ON DELETE CASCADE ho impedeix
        raise NotFoundError("Licitacio no trobada.")
    lot = (
        await session.get(TenderLot, workspace.tender_lot_id)
        if workspace.tender_lot_id
        else None
    )

    compliance = await current_compliance_run(session, workspace.company_id, workspace.tender_id)
    requirements = await current_requirement_run(
        session, workspace.tender_id, workspace.company_id
    )
    counters = await compliance_counters(session, compliance, workspace.tender_lot_id)

    match = await session.scalar(
        sa.select(CompanyTenderMatch).where(
            CompanyTenderMatch.company_id == workspace.company_id,
            CompanyTenderMatch.tender_id == workspace.tender_id,
        )
    )

    # El termini autoritatiu es el del lot si el workspace n'es d'un; si no, el
    # de l'expedient. El del workspace nomes es una fotografia.
    deadline = effective_deadline(tender, lot)
    days = _days_remaining(deadline, now)

    # La historia material de la licitacio, llegida un sol cop. Cada artefacte
    # hi pregunta el mateix amb una base diferent: que ha passat des que em vaig
    # calcular que m'afecti A MI. La matriu d'impacte dona la resposta exacta i
    # per aixo un canvi de titol no desperta ningu.
    log = await load_change_log(session, tender.id)
    revision = log.latest

    decision = await latest_decision(session, workspace.id)
    decided_on = await revision_of(
        session, decision.tender_revision_id if decision else None
    )
    since = log.since(decided_on)
    decision_reasons: list[str] = []
    if decision is not None:
        if log.basis_unknown(decided_on):
            # Decisio d'abans que existissin les revisions. No es toca ni es
            # torna a signar: nomes deixa de comptar com a vigent fins que algu
            # se la miri, perque afirmar que ho es seria inventar-s'ho.
            decision_reasons.append("revision_basis_unknown")
        elif since:
            decision_reasons.append("tender_changed_since_decision")

    # Les bases de cada artefacte, i la derivacio pura (`outdated_reasons`), la
    # mateixa que fa servir la lectura per lots de les llistes.
    reasons = outdated_reasons(
        workspace,
        tender=tender,
        lot=lot,
        compliance=compliance,
        requirements=requirements,
        match=match,
        log=log,
        prefit_basis=await revision_of(session, match.tender_revision_id if match else None),
        requirements_basis=(
            await revision_of(session, requirements.tender_revision_id)
            if requirements is not None
            else None
        ),
        compliance_basis=(
            await revision_of(session, compliance.tender_revision_id)
            if compliance is not None
            else None
        ),
    )

    deadline_passed = deadline_has_passed(deadline, now)
    analysis_status: AnalysisStatus | None = None
    analysis_warnings: AnalysisDecisionWarnings | None = None
    if with_analysis:
        analysis_status = await current_analysis(session, workspace.company_id, workspace.tender_id)
        analysis_warnings = AnalysisDecisionWarnings.from_status(
            analysis_status, deadline_passed=deadline_passed
        )

    return WorkspaceState(
        workspace=workspace,
        tender=tender,
        tender_revision=revision,
        changes_since_decision=since,
        tender_actionable=is_actionable(tender),
        lot_removed=lot_is_gone(lot),
        decision_reasons=tuple(decision_reasons),
        lot=lot,
        compliance_run=compliance,
        requirement_run=requirements,
        counters=counters,
        prefit_score=prefit_score_of(match, workspace),
        prefit_status=match.status.value if match else None,
        latest_decision=decision,
        analysis_outdated=bool(reasons),
        outdated_reasons=reasons,
        deadline_at=deadline,
        days_remaining=days,
        deadline_passed=deadline_passed,
        analysis=analysis_status,
        analysis_warnings=analysis_warnings,
    )


def _decision_order() -> tuple[sa.UnaryExpression, ...]:
    """De la decisio mes recent a la mes antiga. Un sol lloc ho defineix."""
    return (WorkspaceDecision.decided_at.desc(), WorkspaceDecision.id.desc())


async def latest_decision(
    session: AsyncSession, workspace_id: uuid.UUID
) -> WorkspaceDecision | None:
    return await session.scalar(
        sa.select(WorkspaceDecision)
        .where(WorkspaceDecision.workspace_id == workspace_id)
        .order_by(*_decision_order())
        .limit(1)
    )


async def latest_decisions(
    session: AsyncSession, workspace_ids: set[uuid.UUID]
) -> dict[uuid.UUID, WorkspaceDecision]:
    """`latest_decision` de cop per a diverses candidatures: una consulta.

    Amb el mateix ordre global, la primera de cada candidatura es la seva mes
    recent, exactament la que tria `latest_decision`.
    """
    if not workspace_ids:
        return {}
    latest: dict[uuid.UUID, WorkspaceDecision] = {}
    for decision in (
        await session.scalars(
            sa.select(WorkspaceDecision)
            .where(WorkspaceDecision.workspace_id.in_(workspace_ids))
            .order_by(*_decision_order())
        )
    ).all():
        latest.setdefault(decision.workspace_id, decision)
    return latest


async def decision_history(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[WorkspaceDecision]:
    """Historial complet, del mes recent al mes antic. Res no se n'esborra."""
    return list(
        (
            await session.scalars(
                sa.select(WorkspaceDecision)
                .where(WorkspaceDecision.workspace_id == workspace_id)
                .order_by(*_decision_order())
            )
        ).all()
    )


# --------------------------------------------------------------------------- #
# Lectura per lots: les llistes                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class WorkspaceListState:
    """El que una fila de llistat necessita, llegit per lots.

    Un subconjunt de `WorkspaceState` amb la MATEIXA derivacio (`effective_deadline`,
    `outdated_reasons`, `analysis_basis_of`, `prefit_score_of`): el que no surt a
    cap fila (comptadors, historia des de la decisio, analisi v3) no es llegeix.
    """

    workspace: BidWorkspace
    tender: Tender
    buyer_name: str | None
    lot: TenderLot | None
    compliance_run: CompanyComplianceRun | None
    requirement_run: RequirementExtractionRun | None
    prefit_score: int | None
    latest_decision: WorkspaceDecision | None
    outdated_reasons: tuple[str, ...]
    deadline_at: datetime | None
    days_remaining: int | None
    deadline_passed: bool

    @property
    def analysis_outdated(self) -> bool:
        return bool(self.outdated_reasons)

    @property
    def analysis_basis(self) -> AnalysisBasis:
        return analysis_basis_of(self.requirement_run)


async def load_list_states(
    session: AsyncSession,
    company_id: uuid.UUID,
    workspaces: list[BidWorkspace],
    *,
    now: datetime | None = None,
) -> list[WorkspaceListState]:
    """Les files de `GET /workspaces` amb un nombre fix de consultes, sigui quin sigui N.

    Cada peca es llegeix un sol cop per a totes les candidatures (`IN (...)`) i la
    derivacio es la de `load_state`. Totes han de ser de `company_id`: el compliment,
    la run de requisits efectiva i el pre-fit son de l'empresa.
    """
    from app.services import manual_requirement

    if not workspaces:
        return []
    if any(workspace.company_id != company_id for workspace in workspaces):
        raise ValueError("Totes les candidatures han de ser de la mateixa empresa.")

    tender_ids = {workspace.tender_id for workspace in workspaces}
    # `Tender.buyer` es `lazy="joined"`: el nom de l'organisme ve amb la licitacio.
    tenders = {
        tender.id: tender
        for tender in (
            await session.scalars(sa.select(Tender).where(Tender.id.in_(tender_ids)))
        ).all()
    }
    lot_ids = {workspace.tender_lot_id for workspace in workspaces if workspace.tender_lot_id}
    lots = (
        {
            lot.id: lot
            for lot in (
                await session.scalars(sa.select(TenderLot).where(TenderLot.id.in_(lot_ids)))
            ).all()
        }
        if lot_ids
        else {}
    )
    compliance = await current_compliance_runs(session, company_id, tender_ids)
    requirements = await manual_requirement.effective_runs(session, tender_ids, company_id)
    matches = {
        match.tender_id: match
        for match in (
            await session.scalars(
                sa.select(CompanyTenderMatch)
                .where(
                    CompanyTenderMatch.company_id == company_id,
                    CompanyTenderMatch.tender_id.in_(tender_ids),
                )
                # La fila nomes en llegeix la puntuacio i la revisio de base: ni la
                # licitacio (ja carregada) ni els lots del pre-fit no cal tornar-los
                # a llegir. Si algu hi accedis, la licitacio surt del mapa d'identitat.
                .options(
                    lazyload(CompanyTenderMatch.tender),
                    lazyload(CompanyTenderMatch.lot_matches),
                )
            )
        ).all()
    }
    logs = await load_change_logs(session, tender_ids)
    decisions = await latest_decisions(session, {workspace.id for workspace in workspaces})

    # Les revisions de base del pre-fit, dels requisits i del compliment, d'un cop.
    basis_ids = {
        artefact.tender_revision_id
        for artefact in (*matches.values(), *requirements.values(), *compliance.values())
        if artefact.tender_revision_id is not None
    }
    revisions = (
        {
            revision.id: revision
            for revision in (
                await session.scalars(
                    sa.select(TenderRevision).where(TenderRevision.id.in_(basis_ids))
                )
            ).all()
        }
        if basis_ids
        else {}
    )

    def basis(artefact) -> TenderRevision | None:
        if artefact is None or artefact.tender_revision_id is None:
            return None
        return revisions.get(artefact.tender_revision_id)

    states: list[WorkspaceListState] = []
    for workspace in workspaces:
        tender = tenders.get(workspace.tender_id)
        if tender is None:  # pragma: no cover - FK ON DELETE CASCADE ho impedeix
            raise NotFoundError("Licitacio no trobada.")
        lot = lots.get(workspace.tender_lot_id) if workspace.tender_lot_id else None
        run = compliance.get(workspace.tender_id)
        requirement_run = requirements.get(workspace.tender_id)
        match = matches.get(workspace.tender_id)
        deadline = effective_deadline(tender, lot)
        states.append(
            WorkspaceListState(
                workspace=workspace,
                tender=tender,
                buyer_name=tender.buyer.name if tender.buyer_id and tender.buyer else None,
                lot=lot,
                compliance_run=run,
                requirement_run=requirement_run,
                prefit_score=prefit_score_of(match, workspace),
                latest_decision=decisions.get(workspace.id),
                outdated_reasons=outdated_reasons(
                    workspace,
                    tender=tender,
                    lot=lot,
                    compliance=run,
                    requirements=requirement_run,
                    match=match,
                    log=logs[workspace.tender_id],
                    prefit_basis=basis(match),
                    requirements_basis=basis(requirement_run),
                    compliance_basis=basis(run),
                ),
                deadline_at=deadline,
                days_remaining=_days_remaining(deadline, now),
                deadline_passed=deadline_has_passed(deadline, now),
            )
        )
    return states


async def open_within_deadline(
    session: AsyncSession, workspaces: list[BidWorkspace], *, now: datetime | None = None
) -> list[BidWorkspace]:
    """Les candidatures obertes (no tancades) i amb `deadline_passed` fals, en l'ordre rebut.

    El mateix termini que `WorkspaceSummary.deadline_passed` (`effective_deadline` +
    `deadline_has_passed`), llegit amb dues consultes per a totes: nomes els terminis.
    """
    candidates = [w for w in workspaces if w.status not in CLOSED_WORKSPACE_STATUSES]
    if not candidates:
        return []
    tender_deadlines = {
        row.id: row.deadline_at
        for row in (
            await session.execute(
                sa.select(Tender.id, Tender.deadline_at).where(
                    Tender.id.in_({w.tender_id for w in candidates})
                )
            )
        ).all()
    }
    lot_ids = {w.tender_lot_id for w in candidates if w.tender_lot_id}
    lot_deadlines = (
        {
            row.id: row.deadline_at
            for row in (
                await session.execute(
                    sa.select(TenderLot.id, TenderLot.deadline_at).where(
                        TenderLot.id.in_(lot_ids)
                    )
                )
            ).all()
        }
        if lot_ids
        else {}
    )
    return [
        workspace
        for workspace in candidates
        if workspace.tender_id in tender_deadlines
        and not deadline_has_passed(
            effective_deadline_of(
                tender_deadlines[workspace.tender_id],
                lot_deadlines.get(workspace.tender_lot_id) if workspace.tender_lot_id else None,
            ),
            now,
        )
    ]


# --------------------------------------------------------------------------- #
# Creacio                                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class WorkspaceCreation:
    workspace: BidWorkspace
    created: bool
    checklist_items_created: int


async def create_workspace(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender: Tender,
    user_id: uuid.UUID,
    tender_lot_id: uuid.UUID | None = None,
) -> WorkspaceCreation:
    """Obre la candidatura, o retorna la que ja hi havia.

    Idempotent a proposit: el boto de la fitxa de licitacio no ha de poder crear
    dos workspaces per la mateixa cosa ni que es premi dues vegades. Qui vulgui
    distingir els dos casos te `created`.
    """
    existing = await find_workspace(session, company_id, tender.id, tender_lot_id)
    if existing is not None:
        return WorkspaceCreation(workspace=existing, created=False, checklist_items_created=0)

    if tender_lot_id is not None:
        lot = await session.get(TenderLot, tender_lot_id)
        if lot is None or lot.tender_id != tender.id:
            raise NotFoundError("Lot no trobat en aquesta licitacio.")

    compliance = await current_compliance_run(session, company_id, tender.id)
    requirements = await current_requirement_run(session, tender.id, company_id)
    prefit = await session.scalar(
        sa.select(CompanyTenderMatch.score).where(
            CompanyTenderMatch.company_id == company_id,
            CompanyTenderMatch.tender_id == tender.id,
        )
    )

    workspace = BidWorkspace(
        company_id=company_id,
        tender_id=tender.id,
        tender_lot_id=tender_lot_id,
        status=WorkspaceStatus.ANALYSIS,
        current_compliance_run_id=compliance.id if compliance else None,
        current_requirement_run_id=requirements.id if requirements else None,
        prefit_score=prefit,
        prefit_captured_at=datetime.now(UTC) if prefit is not None else None,
        deadline_at=tender.deadline_at,
        created_by=user_id,
    )
    session.add(workspace)
    try:
        await session.flush()
    except IntegrityError:
        # Cursa entre dues peticions: guanya la que ha arribat primera i la
        # segona obre el workspace que ja existeix.
        await session.rollback()
        again = await find_workspace(session, company_id, tender.id, tender_lot_id)
        if again is None:  # pragma: no cover - nomes si la restriccio canvia
            raise
        return WorkspaceCreation(workspace=again, created=False, checklist_items_created=0)

    created_items = await generate_checklist(session, workspace, user_id=user_id)
    await session.commit()
    await session.refresh(workspace)
    return WorkspaceCreation(
        workspace=workspace, created=True, checklist_items_created=created_items
    )


# --------------------------------------------------------------------------- #
# Checklist                                                                     #
# --------------------------------------------------------------------------- #


async def relevant_requirements(
    session: AsyncSession, workspace: BidWorkspace
) -> list[Requirement]:
    """Els requisits VIGENTS que afecten aquest workspace.

    Conjunt efectiu: l'extraccio vigent mes els manuals no arxivats. La
    checklist en surt, i per tant un requisit escrit a ma genera feina com
    qualsevol altre; un d'arxivat deixa de generar-ne de nova, pero la que ja hi
    havia no es toca —`generate_checklist` no esborra mai res.
    """
    from app.services import manual_requirement

    effective = await manual_requirement.effective_requirements(
        session, workspace.tender_id, workspace.company_id
    )
    requirements = sorted(
        effective.all, key=lambda r: (str(r.category), r.title or "")
    )
    return [r for r in requirements if applies_to_workspace_lot(r, workspace.tender_lot_id)]


async def generate_checklist(
    session: AsyncSession, workspace: BidWorkspace, *, user_id: uuid.UUID | None
) -> int:
    """Crea els items que falten. **Mai n'esborra ni n'actualitza cap.**

    Regenerar despres d'una analisi nova ha de ser segur: si algu ja ha marcat
    un item com a completat o l'ha assignat, aquesta funcio no el toca. Nomes
    afegeix el que encara no hi es, identificat pel requisit d'origen.
    """
    existing = set(
        (
            await session.scalars(
                sa.select(WorkspaceChecklistItem.requirement_id).where(
                    WorkspaceChecklistItem.workspace_id == workspace.id,
                    WorkspaceChecklistItem.requirement_id.is_not(None),
                )
            )
        ).all()
    )

    created = 0
    for requirement in await relevant_requirements(session, workspace):
        if requirement.id in existing or not generates_checklist_item(requirement):
            continue
        session.add(
            WorkspaceChecklistItem(
                company_id=workspace.company_id,
                workspace_id=workspace.id,
                requirement_id=requirement.id,
                title=checklist_title(requirement)[:500],
                category=requirement.category,
                mandatory=requirement.obligation == RequirementObligation.MANDATORY,
                status=ChecklistItemStatus.PENDING,
                source=ChecklistItemSource.REQUIREMENT,
                created_by=user_id,
            )
        )
        created += 1

    await session.flush()
    return created


async def regenerate_checklist(
    session: AsyncSession, workspace: BidWorkspace, *, user_id: uuid.UUID | None
) -> int:
    """Reconcilia la checklist amb l'analisi vigent i actualitza les referencies.

    Despres d'aixo el workspace deixa d'estar `analysis_outdated` pel que fa als
    requisits: ja treballa contra el run nou. El que NO fa es tocar la decisio
    humana ni els items que ja existien.
    """
    created = await generate_checklist(session, workspace, user_id=user_id)
    run = await current_requirement_run(session, workspace.tender_id, workspace.company_id)
    if run is not None:
        workspace.current_requirement_run_id = run.id
    compliance = await current_compliance_run(
        session, workspace.company_id, workspace.tender_id
    )
    if compliance is not None:
        workspace.current_compliance_run_id = compliance.id
    await session.commit()
    await session.refresh(workspace)
    return created


async def checklist_items(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[WorkspaceChecklistItem]:
    return list(
        (
            await session.scalars(
                sa.select(WorkspaceChecklistItem)
                .where(WorkspaceChecklistItem.workspace_id == workspace_id)
                .order_by(
                    WorkspaceChecklistItem.mandatory.desc(),
                    WorkspaceChecklistItem.category,
                    WorkspaceChecklistItem.title,
                )
            )
        ).all()
    )


# --------------------------------------------------------------------------- #
# Persones                                                                      #
# --------------------------------------------------------------------------- #


async def assert_assignable(
    session: AsyncSession, company_id: uuid.UUID, user_id: uuid.UUID | None
) -> None:
    """Nomes es pot assignar feina a qui pertany a l'empresa.

    Sense aixo, un identificador d'usuari d'un altre tenant podria acabar dins
    d'una fila d'aquesta empresa nomes per haver-lo escrit al cos de la peticio.
    """
    if user_id is None:
        return
    member = await session.scalar(
        sa.select(CompanyMembership.id).where(
            CompanyMembership.company_id == company_id,
            CompanyMembership.user_id == user_id,
        )
    )
    if member is None:
        raise NotAssignableError("Aquesta persona no pertany a l'empresa.")


async def company_members(session: AsyncSession, company_id: uuid.UUID) -> list[User]:
    return list(
        (
            await session.scalars(
                sa.select(User)
                .join(CompanyMembership, CompanyMembership.user_id == User.id)
                .where(CompanyMembership.company_id == company_id)
                .order_by(User.full_name, User.email)
            )
        ).all()
    )


def assert_can_decide(role: MembershipRole) -> None:
    """La porta humana: qui signa ha de tenir-hi autoritat.

    403 i no 404: aqui l'usuari SI que te acces a l'empresa i al workspace; el
    que no te es la potestat de comprometre-la. Amagar-ho seria confus.
    """
    if role not in DECISION_ROLES:
        raise PermissionDeniedError(
            "El teu rol no pot aprovar ni descartar candidatures."
        )


# --------------------------------------------------------------------------- #
# Decisio humana                                                                #
# --------------------------------------------------------------------------- #


def set_status(
    workspace: BidWorkspace,
    target: WorkspaceStatus,
    *,
    actor: TransitionActor,
) -> WorkspaceStatus:
    """**L'unica porta cap a `BidWorkspace.status`.**

    No escriu res: valida i retorna. Qui assigna es qui la crida, i el test
    estructural comprova que a tot el servei nomes hi ha una assignacio i que
    passa per aqui.

    Cada canvi d'estat ha de dir QUI el fa. `TransitionActor.SYSTEM` no pot
    aprovar ni descartar res, i `submitted` no l'assoleix cap actor existent.
    """
    try:
        return apply_transition(workspace.status, target, actor=actor)
    except WorkspaceTransitionError as exc:
        raise WorkspaceTransitionRejectedError(
            exc.message,
            details={
                "current": exc.current.value,
                "target": exc.target.value,
                "actor": actor.value,
            },
        ) from exc


async def record_decision(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    decision: WorkspaceDecisionType,
    user: User,
    role: MembershipRole,
    reason: str | None,
    now: datetime | None = None,
) -> WorkspaceDecision:
    """Registra la decisio humana. Insereix; no sobreescriu res.

    L'estat del workspace s'actualitza a partir de la decisio, i aquest es
    l'UNIC cami del codi que hi pot posar `go_approved` o `no_go_approved`.

    Una sola transaccio: decisio, canvi d'estat i auditoria es confirmen amb un
    sol `commit`. Si qualsevol pas d'escriptura falla es fa `rollback` i no
    queda res escrit. Els avisos que obliguen motiu i la fotografia surten de
    l'analisi v3 vigent (el mateix `(run, stale)` que `GET /analysis/current`),
    mai del compliment antic: una decisio nova no escriu fotografia legacy.
    """
    assert_can_decide(role)

    state = await load_state(session, workspace, now=now, with_analysis=True)
    analysis = state.analysis
    warnings = state.analysis_warnings
    assert analysis is not None and warnings is not None
    cleaned = (reason or "").strip() or None
    if requires_reason(decision, warnings) and cleaned is None:
        raise DecisionReasonRequiredError(
            "Cal escriure el motiu: hi ha avisos oberts de l'analisi del plec.",
            warnings.codes(),
        )
    # La fotografia es construeix amb el MATEIX objecte d'avisos que ha decidit
    # si calia motiu: `snapshot.warnings` no pot divergir del que es veia.
    # 3B-2 §4.4: i l'estat del paquet documental del plec en aquell moment.
    package = await package_state(session, workspace.tender_id)
    snapshot = build_snapshot(analysis, warnings, package)

    try:
        record = WorkspaceDecision(
            # L'id es fixa aqui perque l'auditoria el necessita abans del commit.
            id=uuid.uuid4(),
            company_id=workspace.company_id,
            workspace_id=workspace.id,
            decision=decision,
            decided_by=user.id,
            decided_at=now or datetime.now(UTC),
            reason=cleaned,
            analysis_run_id=analysis.run.id if analysis.run is not None else None,
            analysis_snapshot_json=snapshot.to_json_dict(),
            # Sense fotografia legacy: aquestes columnes nomes les tenen les
            # decisions anteriors a v3. Els comptadors son NOT NULL, per aixo 0.
            compliance_run_id=None,
            requirement_run_id=None,
            automated_recommendation=None,
            open_warnings=None,
            confirmed_blockers=0,
            potential_blockers=0,
            mandatory_unknown=0,
            mandatory_needs_review=0,
            # Fets, no fotografia del motor antic.
            prefit_score=state.prefit_score,
            deadline_passed=state.deadline_passed,
            tender_revision_id=state.tender_revision.id if state.tender_revision else None,
        )
        session.add(record)
        workspace.status = set_status(
            workspace, status_for(decision), actor=TransitionActor.HUMAN_DECISION
        )
        await audit.record(
            session,
            action=AuditAction.UPDATE,
            company_id=workspace.company_id,
            user_id=user.id,
            entity_type="workspace_decision",
            entity_id=record.id,
            summary=f"Decisio {decision.value} sobre la candidatura",
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(record)
    # `updated_at` te `onupdate` al servidor: despres d'un UPDATE el seu valor
    # nomes el sap la base de dades i cal tornar-lo a llegir explicitament.
    await session.refresh(workspace)
    return record


# --------------------------------------------------------------------------- #
# Tasques i notes                                                               #
# --------------------------------------------------------------------------- #


async def workspace_tasks(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[WorkspaceTask]:
    return list(
        (
            await session.scalars(
                sa.select(WorkspaceTask)
                .where(WorkspaceTask.workspace_id == workspace_id)
                .order_by(WorkspaceTask.status, WorkspaceTask.due_at, WorkspaceTask.created_at)
            )
        ).all()
    )


async def workspace_notes(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[WorkspaceNote]:
    return list(
        (
            await session.scalars(
                sa.select(WorkspaceNote)
                .where(WorkspaceNote.workspace_id == workspace_id)
                .order_by(WorkspaceNote.created_at.desc())
            )
        ).all()
    )


def mark_completion(
    item: WorkspaceChecklistItem | WorkspaceTask,
    status: ChecklistItemStatus | WorkspaceTaskStatus,
) -> None:
    """Manté `completed_at` coherent amb l'estat, sense inventar-se dates."""
    done = status in (ChecklistItemStatus.COMPLETED, WorkspaceTaskStatus.COMPLETED)
    if done and item.completed_at is None:
        item.completed_at = datetime.now(UTC)
    elif not done:
        item.completed_at = None

