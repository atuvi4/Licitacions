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

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.profiles import profile_for_run
from app.compliance import consolidation
from app.compliance.alternatives import (
    EXPLANATION_BY_OUTCOME,
    GroupResolution,
    effective_result,
    resolve_groups,
)
from app.compliance.certifications import POTENTIAL_ONLY_KEY
from app.compliance.engine import (
    applies_to_lot,
    assess_requirement_traced,
    derive_blocker_status,
    not_applicable,
    summarise,
)
from app.compliance.normalize import Normalization, normalize_requirement
from app.compliance.profile import compute_profile_fingerprint, load_profile_snapshot
from app.compliance.resolution import (
    BRANCH_EXPLANATION_KEY,
    BRANCH_MODE_KEY,
    BRANCH_RESULT_KEY,
    BRANCH_RULE_KEY,
    CARRIED_REVIEW_KEY,
    DROPPED_REVIEW_KEY,
    OWN_EXPLANATION_KEY,
    REVIEW_BASIS_KEY,
    SUBSTITUTION_KEY,
    RowState,
    agreed_with_rewrite,
    count_rows,
    group_rewrites,
    is_classification,
    own_obligation,
    own_result,
    substitution_map,
    substitution_params,
)
from app.compliance.rules import (
    RULES_VERSION,
    ExplanationCode,
    RuleCode,
    RuleOutcome,
    needs_review,
)
from app.compliance.specs import is_effectively_mandatory
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
    ContractType,
    MatchStatus,
    ReviewStatus,
)
from app.models.identity import Company
from app.models.matching import CompanyTenderMatch
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.tenders import Tender, TenderLot
from app.services import manual_requirement
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


async def _prefit(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> tuple[int | None, MatchStatus | None]:
    """Pre-fit vigent: puntuacio i classificacio. Es guarden per traçabilitat; NO es
    fusionen amb l'elegibilitat, pero la classificacio decideix si una licitacio
    elegible es candidata (D2: un `low_fit` amb 60 punts o mes no ho es)."""
    row = (
        await session.execute(
            sa.select(CompanyTenderMatch.score, CompanyTenderMatch.status).where(
                CompanyTenderMatch.company_id == company_id,
                CompanyTenderMatch.tender_id == tender_id,
            )
        )
    ).first()
    if row is None:
        return None, None
    return row[0], row[1]


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

    # El conjunt EFECTIU: l'extraccio vigent mes els requisits escrits a ma.
    # Un requisit manual obligatori existeix igual que un d'extret i el motor
    # n'ha de llegir els camps estructurats igual; que l'hagi escrit una
    # persona no el fa complert tot sol.
    requirements = list(
        (
            await session.scalars(
                sa.select(Requirement)
                .options(selectinload(Requirement.evidence))
                .where(
                    sa.or_(
                        Requirement.run_id == extraction_run.id,
                        manual_requirement.manual_filter(tender.id),
                    )
                )
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

    prefit, prefit_status = await _prefit(session, company.id, tender.id)
    run = await _claim_run(
        session,
        company_id=company.id,
        tender=tender,
        extraction_run=extraction_run,
        profile_fingerprint=profile_fingerprint,
        req_fingerprint=req_fingerprint,
        prefit=prefit,
        prefit_status=prefit_status,
    )
    # Recalcular sobre la MATEIXA analisi no esborra el que una persona ha
    # revisat: les revisions del run vigent passen al nou, requisit a requisit.
    carried_reviews = await _reviews_to_carry(
        session, company.id, tender.id, extraction_run.id, tender_lot_id=tender_lot_id
    )

    deadline = tender.deadline_at.date() if tender.deadline_at else None
    # Si l'expedient no te lots, un abast desconegut no es ambigu.
    lot_count = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderLot).where(TenderLot.tender_id == tender.id)
    )
    tender_has_lots = bool(lot_count)
    #: (requisit, avaluacio) tal com es desen, per al recompte final.
    created: list[tuple[Requirement, CompanyRequirementAssessment]] = []
    #: (requisit, veredicte propi) abans de resoldre les disjuncions.
    evaluated: list[tuple] = []
    #: Normalitzacio de lectura de cada requisit (per agrupar-ne els duplicats).
    normalizations: dict[uuid.UUID, Normalization | None] = {}
    #: El perfil del run: decideix si el valor es de format lliure (v1,
    #: product-v2) i s'ha de llegir amb el normalitzador. Els manuals, mai.
    run_profile = profile_for_run(extraction_run.extractor_version).name

    for requirement in requirements:
        # Un requisit que una persona ha rebutjat no s'avalua.
        if requirement.review_status == ReviewStatus.REJECTED:
            continue
        profile = run_profile if requirement.run_id == extraction_run.id else None

        applicable = applies_to_lot(
            requirement, tender_lot_id, tender_has_lots=tender_has_lots
        )
        if applicable is False:
            outcome = not_applicable()
            normalizations[requirement.id] = normalize_requirement(requirement, profile)
        elif applicable is None:
            # Diu que aplica a un lot pero no sabem a quin: no s'endevina.
            outcome = needs_review(
                RuleCode.NOT_APPLICABLE,
                ExplanationCode.SCOPE_AMBIGUOUS,
                scope=str(requirement.scope),
            )
            normalizations[requirement.id] = normalize_requirement(requirement, profile)
        else:
            outcome, normalizations[requirement.id] = assess_requirement_traced(
                requirement, snapshot, deadline=deadline, tender=tender, profile=profile
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

    # v6: en serveis, la classificacio acreditada substitueix la solvencia del mateix
    # abast (art. 77.1.b LCSP). Aqui, amb els resultats automatics; despres del
    # traspas de revisions, `finalize_rows` ho torna a decidir amb el veredicte huma.
    services = tender.contract_type == ContractType.SERVICES
    states = [_state(requirement, outcome) for requirement, outcome in evaluated]
    substitutions = substitution_map(states) if services else {}

    resolved: list[tuple] = []
    for requirement, outcome in evaluated:
        group_key = getattr(requirement, "alternative_group", None)
        resolution = groups.get(group_key) if group_key else None
        effective = effective_result(outcome.result, resolution)
        if resolution is not None and group_rewrites(
            outcome.result, effective, resolution.outcome
        ):
            # Una disjuncio que no s'acredita tambe ho deixa escrit a cada via que no
            # compleix (v6): el recompte en refa l'unic bloqueig a partir d'aqui.
            outcome = _with_group(outcome, group_key, resolution, effective)
        elif services and resolution is None:
            if is_classification(
                requirement.category, outcome.rule_code, outcome.params
            ) and outcome.result not in (
                AssessmentResult.SATISFIES,
                AssessmentResult.NOT_APPLICABLE,
            ):
                outcome = _optional_in_services(outcome)
            elif requirement.id in substitutions and outcome.result not in (
                AssessmentResult.SATISFIES,
                AssessmentResult.NOT_APPLICABLE,
            ):
                outcome = _with_substitution(outcome, substitutions[requirement.id])
                effective = outcome.result

        blocker = derive_blocker_status(
            obligation=requirement.obligation,
            effective_result=effective,
            requirement_review_status=requirement.review_status,
            assessment_reviewed=False,
            modality=getattr(requirement, "modality", None),
            confirmable_by_source=not (outcome.params or {}).get(POTENTIAL_ONLY_KEY),
        )
        resolved.append((requirement, outcome, blocker))

    # Tercera passada: duplicats de la mateixa exigencia (`consolidation`). Cada
    # requisit conserva la seva avaluacio; el grup nomes canvia el recompte i
    # deixa escrit a quin canonic pertany cada membre.
    duplicates = consolidation.build_groups(
        (requirement, normalizations.get(requirement.id)) for requirement, _ in evaluated
    )
    results_by_group: dict[str, set] = {}
    for requirement, outcome, _blocker in resolved:
        group = duplicates.get(requirement.id)
        if group is not None:
            results_by_group.setdefault(group.key, set()).add(outcome.result)

    for requirement, outcome, blocker in resolved:
        group = duplicates.get(requirement.id)
        if group is not None:
            outcome = replace(
                outcome,
                params={
                    **(outcome.params or {}),
                    consolidation.PARAMS_KEY: consolidation.params_for(
                        group,
                        requirement.id,
                        divergent=len(results_by_group[group.key]) > 1,
                    ),
                },
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
        previous_review = carried_reviews.get(requirement.id)
        if previous_review is not None:
            _carry_review(assessment, previous_review, requirement)
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
        created.append((requirement, assessment))

    # El recompte es el de `_recount_run`: amb el resultat EFECTIU (la revisio humana
    # conservada, si n'hi ha), les disjuncions i la substitucio resoltes amb ell, i el
    # bloqueig que en resulta. Les files especials es desen tal com compten.
    rows = count_rows(created, services=services, persist=True)

    # Una exigencia = un recompte: els duplicats d'un grup en fan una sola fila.
    summary_rows = consolidation.resolve_rows(rows)
    summary = summarise(summary_rows, prefit_score=prefit, prefit_status=prefit_status)

    run.requirements_total = len(summary_rows)
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
    prefit_status: MatchStatus | None = None,
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
        prefit_status=prefit_status,
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


#: `REVIEW_BASIS_KEY`, `CARRIED_REVIEW_KEY` i `DROPPED_REVIEW_KEY` son de
#: `app.compliance.resolution`: el recompte tambe hi escriu (una confirmacio que ja no
#: parla del resultat que es mostra). Es reexporten des d'aqui.
#: El que fa que un requisit demani una altra cosa, o que compti d'una altra manera.
#: `equivalence_allowed` hi es perque la regla de certificacions el llegeix.
_REQUIREMENT_IDENTITY_FIELDS = (
    "category",
    "title",
    "operator",
    "value_json",
    "obligation",
    "modality",
    "scope",
    "tender_lot_id",
    "alternative_group",
    "equivalence_allowed",
)
#: Versio de la llista de camps de l'empremta, desada a cada base. Una base d'una altra
#: versio es tracta com una revisio d'abans de l'empremta: canviar la llista no ha de
#: descartar totes les revisions de cop.
REQUIREMENT_IDENTITY_VERSION = 1


def requirement_identity(requirement: Requirement) -> str:
    """Empremta del que demana un requisit: si canvia, una revisio d'abans ja no hi parla."""
    payload = json.dumps(
        {field: getattr(requirement, field, None) for field in _REQUIREMENT_IDENTITY_FIELDS},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _reviews_to_carry(
    session: AsyncSession,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    extraction_run_id: uuid.UUID,
    *,
    tender_lot_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, CompanyRequirementAssessment]:
    """Les avaluacions revisades del run vigent, per requisit, si es la mateixa analisi.

    Una revisio parla d'un requisit concret: si l'analisi de requisits ha canviat
    (un run nou de requisits, uns altres ids), no hi ha res a conservar i el run
    nou comenca net, com abans. Sobre la mateixa analisi, un "Recalcula" (perfil
    nou, una polissa afegida) no pot fer perdre el que una persona ja ha dit.
    Si es conserva cada revisio ho decideix `_carry_review`.

    Nomes es llegeix el run vigent: una revisio que no s'ha conservat (la fila torna
    a estar sense revisar) ja no passa al run seguent, ni l'avis que en parlava.
    """
    # El run no desa per a quin lot s'ha calculat: no se sap si les revisions del
    # vigent parlen d'aquest lot, i en un calcul d'un sol lot no es traspassen. Com
    # que aquest calcul passa a ser el vigent, el recalcul sencer seguent tampoc no
    # recupera les revisions d'abans (nomes per API: la web no envia mai el lot).
    if tender_lot_id is not None:
        return {}
    previous = await current_run_for(session, company_id, tender_id)
    if previous is None or previous.requirement_extraction_run_id != extraction_run_id:
        return {}
    reviewed = (
        await session.scalars(
            sa.select(CompanyRequirementAssessment).where(
                CompanyRequirementAssessment.assessment_run_id == previous.id,
                sa.or_(
                    CompanyRequirementAssessment.review_status != ReviewStatus.UNREVIEWED,
                    CompanyRequirementAssessment.reviewed_result.is_not(None),
                ),
            )
        )
    ).all()
    return {assessment.requirement_id: assessment for assessment in reviewed}


def _review_basis(previous: CompanyRequirementAssessment) -> dict:
    """Sobre que es va pronunciar la persona: la base desada en revisar, o el mateix run.

    Una revisio conservada porta la base del run on es va fer, no la del darrer
    salt: si no, un segon «Recalcula» compararia el motor amb ell mateix i l'avis
    de canvi desapareixeria sense que ningu hi hagues tornat. Les revisions
    d'abans d'aquesta traca no en tenen: la base es l'avaluacio que es va revisar.
    """
    stored = (previous.explanation_params or {}).get(REVIEW_BASIS_KEY)
    if isinstance(stored, dict) and stored.get("automatic_result"):
        basis = dict(stored)
        if basis.get("identity_version") != REQUIREMENT_IDENTITY_VERSION:
            # Una empremta d'una altra llista de camps no es comparable amb la d'ara.
            basis["requirement"] = None
        return basis
    return {
        "run": str(previous.assessment_run_id),
        "automatic_result": str(previous.automatic_result),
        "requirement": None,
    }


def _same_requirement(
    previous: CompanyRequirementAssessment, basis: dict, requirement: Requirement
) -> bool:
    """El requisit encara demana el que demanava quan es va revisar.

    Un requisit manual s'edita sobre la mateixa fila: comparar nomes l'id
    conservaria un «compleix» dit sobre 450.000 EUR en un requisit que ara en
    demana 9 milions.
    """
    if (
        previous.requirement_value_json != requirement.value_json
        # L'obligatorietat amb que es va calcular: el recompte pot haver desat la
        # fila d'una altra manera (una classificacio en serveis, informativa).
        or own_obligation(previous) != requirement.obligation
        or previous.tender_lot_id != requirement.tender_lot_id
    ):
        return False
    fingerprint = basis.get("requirement")
    return fingerprint is None or fingerprint == requirement_identity(requirement)


def _carry_review(
    assessment: CompanyRequirementAssessment,
    previous: CompanyRequirementAssessment,
    requirement: Requirement,
) -> None:
    """Copia la revisio humana al run nou i en refa el bloqueig com `review_assessment`.

    El resultat automatic nou es queda tal com l'ha calculat el motor. Una revisio
    amb veredicte propi es conserva encara que el motor hagi canviat: la persona
    mana fins que hi torni, i la traca (`automatic_changed`) li ho diu. Amb una
    excepcio: si el motor ha passat a dir «no compleix» DESPRES de la revisio, en un
    requisit que pot bloquejar, i la persona no havia dit aixo, la revisio no es
    conserva. Es va fer sense saber-ho, i conservar-la amagaria un incompliment nou
    darrere d'un «compleix»: ni la capcalera, ni els comptadors, ni els avisos de la
    decisio no el veurien. Si el requisit no pot bloquejar (puntuable, via
    alternativa, condicional), no hi ha res a amagar i es conserva amb l'avis. Al
    reves tampoc: un «no compleix» huma es conserva encara que el motor ara digui
    que si, perque es la lectura prudent.

    Una confirmacio (sense veredicte propi) diu «el sistema te rao» sobre UN
    resultat: si el resultat ha canviat, no parla del nou i no es conserva, perque
    un «compleix» confirmat no pot acabar sent un bloqueig confirmat. Si el
    requisit ha canviat, tampoc. El que no es conserva queda escrit
    (`DROPPED_REVIEW_KEY`), amb el veredicte i el motiu d'abans.
    """
    basis = _review_basis(previous)
    if basis.get("own_result"):
        # El que deia la fila tota sola, abans i ara: el resultat que es mostra pot venir
        # d'un grup o d'una substitucio que depenen del que diuen altres files (v6).
        automatic, reference = str(own_result(assessment)), basis["own_result"]
    else:
        automatic, reference = str(assessment.automatic_result), basis["automatic_result"]
    automatic_changed = reference != automatic
    verdict = str(previous.reviewed_result) if previous.reviewed_result else None
    # Una confirmacio d'un resultat que nomes es mostrava per un grup o una substitucio,
    # o un veredicte que nomes el reafirmava, parla d'un resultat MOSTRAT: si es conserva
    # ho decideix el recompte, amb el que la fila hi torna a mostrar
    # (`resolution._drop_stale_confirmation`). Comparar-ne el resultat propi amagaria que
    # el grup o la substitucio ja no el donen.
    shown_only = agreed_with_rewrite(previous.reviewed_result, basis) or (
        verdict is None
        and basis.get("own_result")
        and basis["own_result"] != basis["automatic_result"]
    )
    if not _same_requirement(previous, basis, requirement):
        reason = "requirement_changed"
    elif shown_only:
        reason = None
        automatic_changed = False
    elif automatic_changed and verdict is None:
        reason = "automatic_changed"
    elif (
        automatic_changed
        and automatic == AssessmentResult.DOES_NOT_SATISFY
        and verdict != automatic
        and is_effectively_mandatory(requirement)
    ):
        reason = "automatic_now_fails"
    else:
        reason = None
    if reason is not None:
        assessment.explanation_params = {
            **(assessment.explanation_params or {}),
            DROPPED_REVIEW_KEY: {
                "from_run": basis.get("run"),
                "reviewed_against": basis["automatic_result"],
                "reviewed_result": verdict,
                "review_note": previous.review_note,
                "reason": reason,
            },
        }
        return

    if basis.get("requirement") is None:
        # Una revisio d'abans de l'empremta: a partir d'ara, el requisit tal com es avui.
        basis["requirement"] = requirement_identity(requirement)
        basis["identity_version"] = REQUIREMENT_IDENTITY_VERSION
    assessment.reviewed_result = previous.reviewed_result
    assessment.review_status = previous.review_status
    assessment.review_note = previous.review_note
    assessment.reviewed_by = previous.reviewed_by
    assessment.reviewed_at = previous.reviewed_at
    assessment.blocker_status = derive_blocker_status(
        obligation=assessment.obligation,
        effective_result=assessment.effective_result,
        requirement_review_status=requirement.review_status,
        assessment_reviewed=previous.review_status
        in (ReviewStatus.VERIFIED, ReviewStatus.REJECTED),
        modality=getattr(requirement, "modality", None),
        confirmable_by_source=not (assessment.explanation_params or {}).get(POTENTIAL_ONLY_KEY),
    )
    assessment.explanation_params = {
        **(assessment.explanation_params or {}),
        REVIEW_BASIS_KEY: basis,
        CARRIED_REVIEW_KEY: {
            "from_run": basis.get("run"),
            "reviewed_against": basis["automatic_result"],
            # Si el motor ara diu el mateix que la persona, no hi ha res a tornar a mirar.
            "automatic_changed": automatic_changed and automatic != verdict,
        },
    }


async def is_run_stale(
    session: AsyncSession, run: CompanyComplianceRun, company: Company
) -> bool:
    """Cert si el perfil, els requisits o les REGLES han canviat des del calcul.

    Les regles hi entren perque canviar-les canvia el veredicte sense que ningu
    hagi tocat cap dada: un run calculat amb una versio anterior pot dir
    "compleix" alla on la versio d'ara diria "cal revisar". El run historic no
    es reescriu —es el que algu va veure— pero deixa de poder passar per vigent.
    """
    if run.rules_version != RULES_VERSION:
        return True
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

    Canviar el veredicte del motor **exigeix motiu**. Confirmar el que ja deia
    no: alla la persona no contradiu res. Pero dir "compleix" alla on el sistema
    deia "no ho puc comprovar" es una afirmacio nova, i qui la fa n'ha de deixar
    la rao escrita al costat.
    """
    if (
        reviewed_result is not None
        and reviewed_result != assessment.automatic_result
        and not (note or "").strip()
    ):
        raise ValidationError(
            "Cal escriure el motiu: aquesta revisio canvia el que deia el sistema."
        )

    assessment.reviewed_result = reviewed_result
    assessment.review_status = review_status
    assessment.review_note = note
    assessment.reviewed_by = user_id
    assessment.reviewed_at = datetime.now(UTC)

    requirement = await session.get(Requirement, assessment.requirement_id)
    # Una revisio nova es la base dels recalculs que vinguin: sobre aquest resultat
    # automatic i aquest requisit. L'avis d'una revisio anterior (conservada o no)
    # ja esta ates. Un dict nou: la columna JSON no veu els canvis in situ.
    params = {
        key: value
        for key, value in (assessment.explanation_params or {}).items()
        if key not in (CARRIED_REVIEW_KEY, DROPPED_REVIEW_KEY)
    }
    params[REVIEW_BASIS_KEY] = {
        "run": str(assessment.assessment_run_id),
        "automatic_result": str(assessment.automatic_result),
        # v6: el de la fila sola, per comparar-lo en recalcular (`_carry_review`).
        "own_result": str(own_result(assessment)),
        "requirement": requirement_identity(requirement) if requirement else None,
        "identity_version": REQUIREMENT_IDENTITY_VERSION,
    }
    assessment.explanation_params = params
    assessment.blocker_status = derive_blocker_status(
        obligation=assessment.obligation,
        effective_result=assessment.effective_result,
        requirement_review_status=(
            requirement.review_status if requirement else ReviewStatus.UNREVIEWED
        ),
        assessment_reviewed=review_status in (ReviewStatus.VERIFIED, ReviewStatus.REJECTED),
        # La mateixa llei que el motor: una branca alternativa o condicional no
        # bloqueja tota sola, encara que una persona la marqui "no compleix".
        modality=getattr(requirement, "modality", None) if requirement else None,
        confirmable_by_source=not params.get(POTENTIAL_ONLY_KEY),
    )

    await session.flush()
    # El recompte torna a resoldre les disjuncions i la substitucio per classificacio
    # amb aquest veredicte (v6): el bloqueig d'un grup o d'una solvencia substituida
    # el decideix el conjunt, no aquesta fila sola.
    await _recount_run(session, assessment.assessment_run_id)
    await session.commit()
    await session.refresh(assessment)
    return assessment


async def resolved_rows(session: AsyncSession, run: CompanyComplianceRun) -> list[tuple]:
    """Les files del recompte d'un run, una per avaluacio i abans de consolidar.

    Un run de la versio vigent les resol com el calcul (`count_rows`: disjuncions i
    substitucio per classificacio, amb els veredictes humans) i les desa tal com
    compten. Un run d'una versio anterior es compta com es va calcular (les files
    desades): aplicar-hi les regles d'ara barrejaria dues versions en un sol run.
    """
    assessments = (
        await session.scalars(
            sa.select(CompanyRequirementAssessment).where(
                CompanyRequirementAssessment.assessment_run_id == run.id
            )
        )
    ).all()
    if run.rules_version != RULES_VERSION:
        return [
            (
                consolidation.group_marker(a.requirement_id, a.explanation_params),
                a.obligation,
                a.effective_result,
                a.blocker_status,
            )
            for a in assessments
        ]
    requirement_ids = [a.requirement_id for a in assessments]
    requirements = {
        requirement.id: requirement
        for requirement in (
            await session.scalars(sa.select(Requirement).where(Requirement.id.in_(requirement_ids)))
        ).all()
    } if requirement_ids else {}
    tender = await session.get(Tender, run.tender_id)
    services = tender is not None and tender.contract_type == ContractType.SERVICES
    return count_rows(
        [(requirements.get(a.requirement_id), a) for a in assessments],
        services=services,
        persist=True,
    )


async def _recount_run(session: AsyncSession, run_id: uuid.UUID) -> None:
    """Refa els agregats del run despres d'una revisio.

    v6: les disjuncions i la substitucio per classificacio es tornen a resoldre amb
    els veredictes humans i les files especials es desen tal com compten
    (`resolved_rows`); la resta de files conserven el seu. Un run d'una versio
    anterior es recompta com abans.
    """
    run = await session.get(CompanyComplianceRun, run_id)
    if run is None:
        return
    counted = await resolved_rows(session, run)
    # Els duplicats d'una exigencia continuen sent una sola fila del recompte.
    rows = consolidation.resolve_rows(counted)
    summary = summarise(rows, prefit_score=run.prefit_score, prefit_status=run.prefit_status)

    # Tambe els totals: el recompte pot haver desat una altra obligatorietat (una
    # classificacio que deixa de ser, o torna a ser, d'un contracte de serveis).
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

    Es conserva el que deia la branca tota sola (`branch_result` i, des de la v6, el
    motiu, la regla i el mode): sense aixo, una persona que obri l'assessment no
    podria saber si el requisit s'acredita per aquesta via o per una altra, ni per que
    no bloqueja (o per que el grup sencer si), i el recompte no podria tornar-li el
    veredicte propi si el grup canvia (`resolution.apply_outcome`).
    """
    return replace(
        outcome,
        result=effective,
        rule_code=RuleCode.ALTERNATIVE_GROUP,
        explanation_code=EXPLANATION_BY_OUTCOME[resolution.outcome],
        params={
            **(outcome.params or {}),
            "alternative_group": group_key,
            BRANCH_RESULT_KEY: str(outcome.result),
            BRANCH_EXPLANATION_KEY: outcome.explanation_code,
            BRANCH_RULE_KEY: outcome.rule_code,
            BRANCH_MODE_KEY: str(outcome.mode),
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


def _with_substitution(outcome: RuleOutcome, classification_id: uuid.UUID) -> RuleOutcome:
    """Solvencia d'un contracte de serveis acreditada per la classificacio (v6).

    Es conserva el veredicte propi (resultat, motiu, regla i mode): si una persona diu
    despres que la classificacio no s'acredita, el recompte el torna a la fila.
    """
    return replace(
        outcome,
        result=AssessmentResult.SATISFIES,
        rule_code=RuleCode.CLASSIFICATION_SUBSTITUTION,
        explanation_code=ExplanationCode.CLASSIFICATION_SUBSTITUTES_SOLVENCY,
        params={
            **(outcome.params or {}),
            SUBSTITUTION_KEY: substitution_params(
                outcome.result,
                outcome.explanation_code,
                outcome.rule_code,
                outcome.mode,
                classification_id,
            ),
        },
        mode=AssessmentMode.AUTOMATIC,
    )


def _optional_in_services(outcome: RuleOutcome) -> RuleOutcome:
    """Classificacio d'un contracte de serveis que no s'acredita: no es exigible (v6).

    El resultat no es toca (es el que es); el motiu diu que la solvencia s'acredita
    pels altres mitjans i que no bloqueja.
    """
    return replace(
        outcome,
        explanation_code=ExplanationCode.CLASSIFICATION_OPTIONAL_IN_SERVICES,
        params={**(outcome.params or {}), OWN_EXPLANATION_KEY: outcome.explanation_code},
    )


def _state(requirement: Requirement, outcome: RuleOutcome) -> RowState:
    """Una fila abans de desar-la: nomes el resultat automatic."""
    return RowState(
        key=requirement.id,
        category=requirement.category,
        tender_lot_id=requirement.tender_lot_id,
        obligation=requirement.obligation,
        modality=getattr(requirement, "modality", None),
        requirement_verified=requirement.review_status == ReviewStatus.VERIFIED,
        alternative_group=getattr(requirement, "alternative_group", None),
        group_complete=getattr(requirement, "alternative_group_complete", None),
        own=outcome.result,
        classification=is_classification(
            requirement.category, outcome.rule_code, outcome.params
        ),
    )
