"""Orquestracio del calcul de compliment.

L'unic modul d'aquesta capa que toca la base de dades. Les regles son funcions
pures sobre una fotografia del perfil, aixi que es poden provar sense sessio.

Cap crida a cap LLM: la IA ja va fer la seva feina estructurant el plec a 4B.
Decidir si 2.450.000 >= 450.000 es una comparacio, i una comparacio ha de ser
reproduible i auditable.

Immutabilitat, com a la resta del sistema: un run completat no es modifica.
Canviar el perfil, els requisits o la versio de regles obliga a un run nou; els
anteriors queden consultables tal com es van calcular.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.compliance.alternatives import (
    EXPLANATION_BY_OUTCOME,
    GroupResolution,
    effective_result,
    resolve_groups,
)
from app.compliance.engine import (
    applies_to_lot,
    assess_requirement,
    derive_blocker_status,
    not_applicable,
    summarise,
)
from app.compliance.profile import compute_profile_fingerprint, load_profile_snapshot
from app.compliance.rules import (
    RULES_VERSION,
    ExplanationCode,
    RuleCode,
    RuleOutcome,
    needs_review,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.compliance import (
    AssessmentEvidence,
    CompanyComplianceRun,
    CompanyRequirementAssessment,
)
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    BlockerStatus,
    ComplianceRunStatus,
    ReviewStatus,
)
from app.models.identity import Company
from app.models.matching import CompanyTenderMatch
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.tenders import Tender, TenderLot
from app.services.tender_lifecycle import latest_revision

logger = logging.getLogger(__name__)


class NoRequirementsError(ValidationError):
    """No hi ha cap analisi de requisits vigent per comparar-hi."""

    code = "no_requirements_to_assess"


class ComplianceInProgressError(ConflictError):
    code = "compliance_in_progress"


@dataclass(frozen=True, slots=True)
class ComplianceOutcome:
    run: CompanyComplianceRun
    reused: bool


async def current_run_for(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> CompanyComplianceRun | None:
    return await session.scalar(
        sa.select(CompanyComplianceRun).where(
            CompanyComplianceRun.company_id == company_id,
            CompanyComplianceRun.tender_id == tender_id,
            CompanyComplianceRun.is_current.is_(True),
        )
    )


def requirements_fingerprint(extraction_run: RequirementExtractionRun) -> str:
    """Identifica l'extraccio avaluada.

    N'hi ha prou amb el run: 4B ja garanteix que un run es immutable i que el
    seu propi `input_fingerprint` cobreix els textos analitzats.
    """
    return f"{extraction_run.id}:{extraction_run.input_fingerprint}"[:64]


async def _prefit_score(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> int | None:
    """Pre-fit vigent. Es guarda per traçabilitat; NO es fusiona amb el resultat."""
    return await session.scalar(
        sa.select(CompanyTenderMatch.score).where(
            CompanyTenderMatch.company_id == company_id,
            CompanyTenderMatch.tender_id == tender_id,
        )
    )


async def analyze_company_compliance(
    session: AsyncSession,
    company: Company,
    tender: Tender,
    *,
    force: bool = False,
    tender_lot_id: uuid.UUID | None = None,
) -> ComplianceOutcome:
    """Avalua els requisits vigents d'una licitacio contra el perfil d'una empresa."""
    started = time.monotonic()

    extraction_run = await session.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.tender_id == tender.id,
            RequirementExtractionRun.is_current.is_(True),
        )
    )
    if extraction_run is None:
        raise NoRequirementsError(
            "Aquesta licitacio no te cap analisi de requisits vigent. Analitza'n els plecs primer."
        )

    requirements = list(
        (
            await session.scalars(
                sa.select(Requirement)
                .options(selectinload(Requirement.evidence))
                .where(Requirement.run_id == extraction_run.id)
                .order_by(Requirement.category, Requirement.created_at)
            )
        ).all()
    )

    snapshot = await load_profile_snapshot(session, company)
    profile_fingerprint = compute_profile_fingerprint(snapshot)
    req_fingerprint = requirements_fingerprint(extraction_run)

    if not force:
        current = await current_run_for(session, company.id, tender.id)
        if (
            current is not None
            and current.status == ComplianceRunStatus.COMPLETED
            and current.company_profile_fingerprint == profile_fingerprint
            and current.requirements_fingerprint == req_fingerprint
            and current.rules_version == RULES_VERSION
        ):
            return ComplianceOutcome(run=current, reused=True)

    prefit = await _prefit_score(session, company.id, tender.id)
    run = await _claim_run(
        session,
        company_id=company.id,
        tender=tender,
        extraction_run=extraction_run,
        profile_fingerprint=profile_fingerprint,
        req_fingerprint=req_fingerprint,
        prefit=prefit,
    )

    deadline = tender.deadline_at.date() if tender.deadline_at else None
    # Si l'expedient no te lots, un abast desconegut no es ambigu.
    lot_count = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderLot).where(TenderLot.tender_id == tender.id)
    )
    tender_has_lots = bool(lot_count)
    rows: list[tuple] = []
    #: (requisit, veredicte propi) abans de resoldre les disjuncions.
    evaluated: list[tuple] = []

    for requirement in requirements:
        # Un requisit que una persona ha rebutjat no s'avalua.
        if requirement.review_status == ReviewStatus.REJECTED:
            continue

        applicable = applies_to_lot(
            requirement, tender_lot_id, tender_has_lots=tender_has_lots
        )
        if applicable is False:
            outcome = not_applicable()
        elif applicable is None:
            # Diu que aplica a un lot pero no sabem a quin: no s'endevina.
            outcome = needs_review(
                RuleCode.NOT_APPLICABLE,
                ExplanationCode.SCOPE_AMBIGUOUS,
                scope=str(requirement.scope),
            )
        else:
            outcome = assess_requirement(
                requirement, snapshot, deadline=deadline, tender=tender
            )

        evaluated.append((requirement, outcome))

    # Segona passada: les vies alternatives no es jutgen una a una. "A o B" es
    # una sola exigencia, i deixar que una branca bloquegi mentre l'altra encara
    # pot acreditar-la diria a una empresa que no es pot presentar a una
    # licitacio a la qual si que pot.
    groups = resolve_groups(
        [
            (
                requirement.alternative_group,
                requirement.id,
                outcome.result,
                getattr(requirement, "alternative_group_complete", None),
            )
            for requirement, outcome in evaluated
            if getattr(requirement, "alternative_group", None)
        ]
    )

    for requirement, outcome in evaluated:
        group_key = getattr(requirement, "alternative_group", None)
        resolution = groups.get(group_key) if group_key else None
        effective = effective_result(outcome.result, resolution)
        if resolution is not None and effective != outcome.result:
            outcome = _with_group(outcome, group_key, resolution, effective)

        blocker = derive_blocker_status(
            obligation=requirement.obligation,
            effective_result=effective,
            requirement_review_status=requirement.review_status,
            assessment_reviewed=False,
            modality=getattr(requirement, "modality", None),
        )

        assessment = CompanyRequirementAssessment(
            company_id=company.id,
            assessment_run_id=run.id,
            requirement_id=requirement.id,
            tender_id=tender.id,
            tender_lot_id=requirement.tender_lot_id,
            automatic_result=outcome.result,
            assessment_mode=outcome.mode,
            rule_code=outcome.rule_code,
            explanation_code=outcome.explanation_code,
            explanation_params=outcome.params or None,
            company_value_json=outcome.company_value,
            requirement_value_json=requirement.value_json,
            obligation=requirement.obligation,
            blocker_status=blocker,
            evidence_strength=outcome.strength,
            calculated_at=datetime.now(UTC),
        )
        session.add(assessment)
        for ref in outcome.evidence:
            assessment.evidence.append(
                AssessmentEvidence(
                    company_id=company.id,
                    entity_type=ref.kind,
                    entity_id=ref.entity_id,
                    document_id=ref.document_id,
                    label=ref.label[:300],
                    strength=ref.strength,
                )
            )
        rows.append((requirement.obligation, outcome.result, blocker))

    summary = summarise(rows, prefit_score=prefit)

    run.requirements_total = len(rows)
    run.mandatory_total = summary.mandatory_total
    run.satisfies_count = summary.satisfies
    run.does_not_satisfy_count = summary.does_not_satisfy
    run.unknown_count = summary.unknown
    run.needs_review_count = summary.needs_review
    run.not_applicable_count = summary.not_applicable
    run.potential_blockers_count = summary.potential_blockers
    run.confirmed_blockers_count = summary.confirmed_blockers
    run.overall_result = summary.eligibility
    run.recommendation = summary.recommendation
    run.status = ComplianceRunStatus.COMPLETED
    run.completed_at = datetime.now(UTC)
    run.duration_ms = int((time.monotonic() - started) * 1000)

    previous = await current_run_for(session, company.id, tender.id)
    if previous is not None and previous.id != run.id:
        previous.is_current = False
        await session.flush()
    run.is_current = True

    await session.commit()
    await session.refresh(run)

    logger.info(
        "compliance.run.done",
        extra={
            "run_id": str(run.id),
            "company_id": str(company.id),
            "tender_id": str(tender.id),
            "eligibility": str(run.overall_result),
            "recommendation": str(run.recommendation),
            "requirements": run.requirements_total,
            "potential_blockers": run.potential_blockers_count,
            "confirmed_blockers": run.confirmed_blockers_count,
            "rules_version": RULES_VERSION,
        },
    )
    return ComplianceOutcome(run=run, reused=False)


async def _claim_run(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender: Tender,
    extraction_run: RequirementExtractionRun,
    profile_fingerprint: str,
    req_fingerprint: str,
    prefit: int | None,
) -> CompanyComplianceRun:
    """Reserva el calcul. L'index unic parcial impedeix el doble clic."""
    revision = await latest_revision(session, tender.id)
    run = CompanyComplianceRun(
        company_id=company_id,
        tender_id=tender.id,
        # Contra quin estat de la licitacio s'ha avaluat: els llindars
        # percentuals es resolen contra el pressupost, i el pressupost es part
        # de l'estat, no dels requisits.
        tender_revision_id=revision.id if revision else None,
        requirement_extraction_run_id=extraction_run.id,
        status=ComplianceRunStatus.RUNNING,
        rules_version=RULES_VERSION,
        company_profile_fingerprint=profile_fingerprint,
        requirements_fingerprint=req_fingerprint,
        started_at=datetime.now(UTC),
        prefit_score=prefit,
        is_current=False,
    )
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ComplianceInProgressError(
            "Aquest calcul de compliment ja s'esta executant. Torna-ho a provar d'aqui a un moment."
        ) from None
    await session.refresh(run)
    return run


async def is_run_stale(
    session: AsyncSession, run: CompanyComplianceRun, company: Company
) -> bool:
    """Cert si el perfil o els requisits han canviat des del calcul."""
    snapshot = await load_profile_snapshot(session, company)
    if compute_profile_fingerprint(snapshot) != run.company_profile_fingerprint:
        return True
    extraction_run = await session.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.tender_id == run.tender_id,
            RequirementExtractionRun.is_current.is_(True),
        )
    )
    if extraction_run is None:
        return False
    return requirements_fingerprint(extraction_run) != run.requirements_fingerprint


async def review_assessment(
    session: AsyncSession,
    assessment: CompanyRequirementAssessment,
    *,
    reviewed_result: AssessmentResult | None,
    review_status: ReviewStatus,
    note: str | None,
    user_id: uuid.UUID,
) -> CompanyRequirementAssessment:
    """Registra la revisio humana SENSE tocar el resultat automatic.

    Els dos conviuen: `automatic_result` es el que va calcular el motor i
    `reviewed_result` el que ha dit la persona. Sense aquesta separacio no es
    podria auditar mai si les regles encertaven.

    Recalcula el bloqueig: una revisio humana SI que pot confirmar-lo, perque
    ara hi ha algu que respon del veredicte.
    """
    assessment.reviewed_result = reviewed_result
    assessment.review_status = review_status
    assessment.review_note = note
    assessment.reviewed_by = user_id
    assessment.reviewed_at = datetime.now(UTC)

    requirement = await session.get(Requirement, assessment.requirement_id)
    assessment.blocker_status = derive_blocker_status(
        obligation=assessment.obligation,
        effective_result=assessment.effective_result,
        requirement_review_status=(
            requirement.review_status if requirement else ReviewStatus.UNREVIEWED
        ),
        assessment_reviewed=review_status in (ReviewStatus.VERIFIED, ReviewStatus.REJECTED),
    )

    await session.flush()
    await _recount_run(session, assessment.assessment_run_id)
    await session.commit()
    await session.refresh(assessment)
    return assessment


async def _recount_run(session: AsyncSession, run_id: uuid.UUID) -> None:
    """Refa els agregats del run despres d'una revisio."""
    run = await session.get(CompanyComplianceRun, run_id)
    if run is None:
        return
    assessments = (
        await session.scalars(
            sa.select(CompanyRequirementAssessment).where(
                CompanyRequirementAssessment.assessment_run_id == run_id
            )
        )
    ).all()
    rows = [(a.obligation, a.effective_result, a.blocker_status) for a in assessments]
    summary = summarise(rows, prefit_score=run.prefit_score)

    run.satisfies_count = summary.satisfies
    run.does_not_satisfy_count = summary.does_not_satisfy
    run.unknown_count = summary.unknown
    run.needs_review_count = summary.needs_review
    run.not_applicable_count = summary.not_applicable
    run.potential_blockers_count = summary.potential_blockers
    run.confirmed_blockers_count = summary.confirmed_blockers
    run.overall_result = summary.eligibility
    run.recommendation = summary.recommendation


async def load_run_or_404(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> CompanyComplianceRun:
    run = await current_run_for(session, company_id, tender_id)
    if run is None:
        raise NotFoundError("Aquesta licitacio encara no te cap analisi de compliment.")
    return run


#: Es reexporta perque l'API i els tests no hagin de coneixer el paquet intern.
__all__ = [
    "RULES_VERSION",
    "BlockerStatus",
    "ComplianceInProgressError",
    "ComplianceOutcome",
    "NoRequirementsError",
    "analyze_company_compliance",
    "current_run_for",
    "is_run_stale",
    "load_run_or_404",
    "review_assessment",
]


def _with_group(
    outcome: RuleOutcome,
    group_key: str,
    resolution: GroupResolution,
    effective: AssessmentResult,
) -> RuleOutcome:
    """El veredicte d'una branca, reescrit pel que ha decidit la disjuncio.

    Es conserva el que deia la branca tota sola (`branch_result`): sense aixo,
    una persona que obri l'assessment no podria saber si el requisit s'acredita
    per aquesta via o per una altra, ni per que no bloqueja.
    """
    return replace(
        outcome,
        result=effective,
        rule_code=RuleCode.ALTERNATIVE_GROUP,
        explanation_code=EXPLANATION_BY_OUTCOME[resolution.outcome],
        params={
            **(outcome.params or {}),
            "alternative_group": group_key,
            "branch_result": str(outcome.result),
            "group_outcome": resolution.outcome.value,
            "group_branches": resolution.branches,
            # Sense aixo, un `incomplete` no diria si es perque falta resoldre
            # una branca o perque no consta que les tinguem totes.
            "group_complete": resolution.complete,
        },
        mode=(
            AssessmentMode.REVIEW_REQUIRED
            if effective == AssessmentResult.NEEDS_REVIEW
            else outcome.mode
        ),
    )
