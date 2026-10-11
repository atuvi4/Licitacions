"""Analisi de plecs: llancar-la, llegir-la i revisar-la.

Endpoints publics globals, com el feed de licitacions i la capa documental: cal
sessio iniciada, no pertinenca a cap empresa. Els requisits deriven de documents
publics i no pertanyen a cap tenant.

L'endpoint nomes tradueix HTTP: tota la logica viu a
`app.services.requirement_analysis`, que no sap res de FastAPI. Quan calgui
moure l'analisi a un worker no s'haura de tocar.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Query, status
from sqlalchemy.orm import selectinload

from app.ai.contract_v3 import counts_as_award_criterion
from app.api.deps import CompanyCtx, CurrentUser, SessionDep
from app.core.errors import EndpointMovedError, NotFoundError, PermissionDeniedError
from app.models.audit import AuditAction
from app.models.enums import AnalysisRunStatus, MembershipRole, RequirementOrigin
from app.models.identity import CompanyMembership
from app.models.requirements import (
    AwardCriterion,
    Requirement,
    RequirementExtractionRun,
)
from app.models.tender_documents import TenderDocumentExtraction
from app.models.tenders import Tender, TenderDocument
from app.schemas.provenance import (
    DocumentFileRefCreate,
    ExtractedPageRefCreate,
    TenderSourceRefRead,
    read_of,
)
from app.schemas.requirements import (
    AwardCriterionRead,
    EvidenceRead,
    ExtractionRunRead,
    ManualRequirementCreate,
    ManualRequirementResult,
    ManualRequirementUpdate,
    RequirementAnalysisRead,
    RequirementCitationLink,
    RequirementRead,
    RequirementRunAdoptionIn,
    RequirementRunAdoptionRead,
)
from app.services import (
    audit,
    manual_requirement,
    manual_tender,
    review_runs,
    source_refs,
    tender_structure,
    workspace_dossier,
)
from app.services.requirement_analysis import current_run_for
from app.services.source_refs import resolve_source_ref, resolve_source_refs
from app.tenders.visibility import visible_to_company, visible_to_user
from app.workspace.dossier import ANALYSIS_STALE

router = APIRouter(tags=["analisi de plecs"])


async def _load_tender(
    session: SessionDep, tender_id: uuid.UUID, user_id: uuid.UUID
) -> Tender:
    """La licitacio, NOMES si aquest usuari la pot veure.

    Aquestes rutes no pengen de cap `company_id`, aixi que la variant que toca
    es `visible_to_user`: public, o d'alguna empresa de la qual l'usuari es
    membre. Sense aquest filtre, els requisits d'una licitacio oberta a ma per
    una altra empresa es podrien llegir sencers amb nomes el seu UUID.
    """
    tender = await session.scalar(
        sa.select(Tender).where(Tender.id == tender_id, visible_to_user(user_id))
    )
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


async def _decorate_evidence(session: SessionDep, items: list) -> dict[uuid.UUID, dict]:
    """Revisio i nom del document de cada cita, per pintar-la sense mes crides."""
    extraction_ids = {e.extraction_id for item in items for e in item.evidence}
    document_ids = {
        e.tender_document_id for item in items for e in item.evidence if e.tender_document_id
    }
    revisions: dict[uuid.UUID, int] = {}
    names: dict[uuid.UUID, str] = {}

    if extraction_ids:
        rows = (
            await session.execute(
                sa.select(
                    TenderDocumentExtraction.id, TenderDocumentExtraction.revision_number
                ).where(TenderDocumentExtraction.id.in_(extraction_ids))
            )
        ).all()
        revisions = dict(rows)
    if document_ids:
        rows = (
            await session.execute(
                sa.select(TenderDocument.id, TenderDocument.name).where(
                    TenderDocument.id.in_(document_ids)
                )
            )
        ).all()
        names = dict(rows)
    return {"revisions": revisions, "names": names}


def _evidence_reads(item, decoration: dict) -> list[EvidenceRead]:
    reads = []
    for evidence in item.evidence:
        read = EvidenceRead.model_validate(evidence)
        read.extraction_revision = decoration["revisions"].get(evidence.extraction_id)
        read.document_name = decoration["names"].get(evidence.tender_document_id)
        reads.append(read)
    return reads


@router.post(
    "/tenders/{tender_id}/analysis/requirements",
    summary="(Retirat) L'analisi ja no s'executa dins de la peticio",
    status_code=410,
)
async def run_analysis(tender_id: uuid.UUID, _: CurrentUser):
    """Analitzar dura minuts i no pot mantenir una peticio HTTP oberta."""
    raise EndpointMovedError(
        "L'analisi s'executa en segon pla. Demana-la a "
        "/companies/{company_id}/tenders/{tender_id}/job-requests/analyze-requirements.",
        details={"tender_id": str(tender_id)},
    )


@router.get(
    "/tenders/{tender_id}/analysis/requirements",
    response_model=RequirementAnalysisRead,
    summary="Requisits i criteris de l'analisi vigent",
)
async def read_analysis(
    tender_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    company_id: uuid.UUID | None = Query(default=None),
):
    await _load_tender(session, tender_id, user.id)
    # El conjunt EFECTIU: l'analisi vigent mes els requisits escrits a ma. Les
    # dues fonts es mantenen separades —`origin` ho diu a cada fila— perque fer
    # veure que un manual surt de l'ultima run seria atribuir a un model una
    # cosa que ha escrit una persona. La run es la que llegeix l'empresa de l'usuari
    # (el seu punter o la canonica, 3B-2 §6.5); sense empresa resolta, la global.
    company_id = await _reading_company(session, user, tender_id, company_id)
    effective = await manual_requirement.effective_requirements(session, tender_id, company_id)
    run = effective.run
    canonical = await current_run_for(session, tender_id)
    # «Hi ha analisi?» es respon amb el mateix calcul que el dossier (3B-2 §7.1.3):
    # sense run, els requisits escrits a ma son una analisi (`current`); amb run,
    # `stale` si ja no parla dels plecs d'ara (`run_is_outdated`, la regla del 409).
    analysis_state = await workspace_dossier.requirement_analysis_state(session, tender_id, run)
    pointers = {
        "effective_run_id": run.id if run is not None else None,
        "canonical_run_id": canonical.id if canonical is not None else None,
        "adoption_available": company_id is not None
        and canonical is not None
        and (run is None or run.id != canonical.id),
        "analysis_state": analysis_state,
    }
    if run is None and not effective.manual:
        return RequirementAnalysisRead(**pointers)

    requirements = (
        await session.scalars(
            sa.select(Requirement)
            .options(selectinload(Requirement.evidence))
            .where(Requirement.id.in_([r.id for r in effective.all]))
            .order_by(Requirement.category, Requirement.created_at)
        )
    ).all() if effective.all else []
    criteria = (
        await session.scalars(
            sa.select(AwardCriterion)
            .options(selectinload(AwardCriterion.evidence))
            .where(AwardCriterion.run_id == (run.id if run else None))
            .order_by(AwardCriterion.points.desc().nullslast(), AwardCriterion.name)
        )
    ).all() if run is not None else []

    decoration = await _decorate_evidence(session, list(requirements) + list(criteria))

    # La provinenca dels manuals son cites, no `RequirementEvidence`: dos
    # emmagatzematges amb garanties diferents i un sol vocabulari a l'API.
    citations = await manual_requirement.source_refs_of(
        session, [r.id for r in requirements if r.origin == RequirementOrigin.MANUAL]
    )

    requirement_reads = []
    for requirement in requirements:
        read = RequirementRead.model_validate(requirement)
        read.evidence = _evidence_reads(requirement, decoration)
        if requirement.origin == RequirementOrigin.MANUAL:
            # `confidence` es de l'extractor. Un requisit escrit per una
            # persona no en te, i ensenyar-ne el valor de la columna seria
            # atribuir a una IA una afirmacio humana.
            read.confidence = None
            read.citations = [
                read_of(await resolve_source_ref(session, ref))
                for ref in citations.get(requirement.id, [])
            ]
        requirement_reads.append(read)

    # v3 guarda a la mateixa taula candidats, desitjables i text informatiu. La
    # fitxa pinta `award_criteria` com a criteris que puntuen: nomes hi van els
    # de v1/v2 i els v3 formals. La resta, a `award_candidates`.
    # Cites dels criteris (formals i candidats): UNA consulta per a tots, mes la
    # resolucio en bloc. La visibilitat ja l'ha decidit `_load_tender`.
    criterion_refs = await tender_structure.source_refs_of(
        session, AwardCriterion, [c.id for c in criteria]
    )
    resolved = await resolve_source_refs(
        session, [ref for refs in criterion_refs.values() for ref in refs]
    )

    criteria_reads = []
    candidate_reads = []
    for criterion in criteria:
        read = _criterion_read(criterion, decoration)
        read.citations = [
            read_of(resolved[ref.id]) for ref in criterion_refs.get(criterion.id, [])
        ]
        if counts_as_award_criterion(criterion.adjudication_basis_json):
            criteria_reads.append(read)
        else:
            candidate_reads.append(read)

    # Obsolet: els documents han canviat des que es va fer l'analisi. Amb la
    # mateixa definicio d'entrada que l'analisi (sostre de pagines inclos).
    # `is_stale` parla NOMES de la run extreta i del corpus documental. Un
    # requisit manual no hi entra: una afirmacio humana no caduca perque s'hagi
    # tornat a llegir un PDF, i crear-ne un tampoc no fa que l'analisi cobreixi
    # els documents d'ara. Una run que va llegir un plec ja retirat de la font
    # tambe es obsoleta. Es el mateix `stale` d'`analysis_state` (`run_is_outdated`,
    # la regla del 409): no es calcula dues vegades.
    is_stale = analysis_state == ANALYSIS_STALE

    return RequirementAnalysisRead(
        run=ExtractionRunRead.model_validate(run) if run is not None else None,
        requirements=requirement_reads,
        award_criteria=criteria_reads,
        award_candidates=candidate_reads,
        is_stale=is_stale,
        **pointers,
    )


# --------------------------------------------------------------------------- #
# Punter per empresa (3B-2 §6.5)                                                #
# --------------------------------------------------------------------------- #

#: Qui pot decidir quina analisi es la base de l'empresa: una decisio de licitacio.
ADOPTION_ROLES = frozenset({MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.BID_MANAGER})


async def _membership(
    session: SessionDep, user, company_id: uuid.UUID | None
) -> CompanyMembership | None:
    """La pertinenca de l'usuari a l'empresa demanada, o la seva per defecte.

    Amb `company_id`, ha de ser-ne membre (si no, 404: no es confirma l'existencia
    de l'empresa). Sense, la marcada per defecte o, si nomes en te una, aquella; si
    no, cap. L'Administracio Tuvi no entra mai a les dades d'una empresa.
    """
    if user.is_superuser:
        if company_id is not None:
            raise NotFoundError("Empresa no trobada.")
        return None
    query = sa.select(CompanyMembership).where(CompanyMembership.user_id == user.id)
    if company_id is not None:
        membership = await session.scalar(query.where(CompanyMembership.company_id == company_id))
        if membership is None:
            raise NotFoundError("Empresa no trobada.")
        return membership
    memberships = list((await session.scalars(query)).all())
    defaults = [m for m in memberships if m.is_default]
    if len(defaults) == 1:
        return defaults[0]
    return memberships[0] if len(memberships) == 1 else None


async def _company_sees(session: SessionDep, company_id: uuid.UUID, tender_id: uuid.UUID) -> bool:
    return (
        await session.scalar(
            sa.select(Tender.id).where(Tender.id == tender_id, visible_to_company(company_id))
        )
    ) is not None


async def _reading_company(
    session: SessionDep, user, tender_id: uuid.UUID, company_id: uuid.UUID | None
) -> uuid.UUID | None:
    """L'empresa des de la qual es llegeix la fitxa, si n'hi ha una de resolta que
    pugui veure la licitacio. Si no, la vista global (`None`)."""
    membership = await _membership(session, user, company_id)
    if membership is None or not await _company_sees(session, membership.company_id, tender_id):
        if company_id is not None:
            raise NotFoundError("Licitacio no trobada.")
        return None
    return membership.company_id


@router.post(
    "/tenders/{tender_id}/analysis/requirements/adopt",
    response_model=RequirementRunAdoptionRead,
    summary="L'empresa accepta un run de requisits com la seva base",
)
async def adopt_requirement_run(
    tender_id: uuid.UUID,
    payload: RequirementRunAdoptionIn,
    session: SessionDep,
    user: CurrentUser,
):
    """Mou el punter de l'empresa (`company_tender_requirement_runs`) a `run_id`.

    Nomes una analisi real, acabada i vigent (o demanada per la mateixa empresa).
    No escriu cap fila global: la revisio de l'empresa s'haura de reconciliar pel
    flux habitual si representava una altra run (`reconciliation_required`).
    """
    membership = await _membership(session, user, payload.company_id)
    if membership is None:
        raise NotFoundError("Empresa no trobada.")
    if not await _company_sees(session, membership.company_id, tender_id):
        raise NotFoundError("Licitacio no trobada.")
    if membership.role not in ADOPTION_ROLES:
        raise PermissionDeniedError(
            "El teu rol no pot decidir quina analisi es la base de l'empresa."
        )
    company_id = membership.company_id
    adoption = await review_runs.adopt_requirement_run(
        session,
        company_id=company_id,
        tender_id=tender_id,
        run_id=payload.run_id,
        user_id=user.id,
    )
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=company_id,
        user_id=user.id,
        entity_type="company_tender_requirement_run",
        entity_id=payload.run_id,
        summary=f"licitacio={tender_id} run={payload.run_id} anterior={adoption.previous_run_id}",
    )
    await session.commit()
    return RequirementRunAdoptionRead(
        effective_run_id=payload.run_id,
        previous_run_id=adoption.previous_run_id,
        reconciliation_required=adoption.reconciliation_required,
    )


@router.get(
    "/tenders/{tender_id}/analysis/runs",
    response_model=list[ExtractionRunRead],
    summary="Historial d'analisis",
)
async def list_runs(
    tender_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
):
    await _load_tender(session, tender_id, user.id)
    runs = (
        await session.scalars(
            sa.select(RequirementExtractionRun)
            .where(RequirementExtractionRun.tender_id == tender_id)
            .order_by(RequirementExtractionRun.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [ExtractionRunRead.model_validate(run) for run in runs]


async def _visible_requirement(
    session: SessionDep, requirement_id: uuid.UUID, user_id: uuid.UUID
) -> Requirement:
    """El requisit, NOMES si l'usuari pot veure la seva licitacio (si no, 404)."""
    requirement = await session.scalar(
        sa.select(Requirement)
        .join(Tender, Tender.id == Requirement.tender_id)
        .options(selectinload(Requirement.evidence))
        .where(Requirement.id == requirement_id, visible_to_user(user_id))
    )
    if requirement is None:
        raise NotFoundError("Requisit no trobat.")
    return requirement


@router.get(
    "/requirements/{requirement_id}",
    response_model=RequirementRead,
    summary="Detall d'un requisit",
)
async def get_requirement(requirement_id: uuid.UUID, session: SessionDep, user: CurrentUser):
    requirement = await _visible_requirement(session, requirement_id, user.id)
    decoration = await _decorate_evidence(session, [requirement])
    read = RequirementRead.model_validate(requirement)
    read.evidence = _evidence_reads(requirement, decoration)
    return read


# RETIRAT (S-R1): escrivia la revisio a la fila d'extraccio, que es GLOBAL i
# compartida entre empreses. La revisio es per empresa i viu a
# `/companies/{company_id}/tenders/{tender_id}/review`. Es comprova la
# visibilitat PRIMER (no visible = el mateix 404 que un id inexistent) i despres
# es respon 410 sense escriure res.
@router.post(
    "/requirements/{requirement_id}/review",
    summary="(Retirat) La revisio es per empresa",
    status_code=410,
)
async def review_requirement(requirement_id: uuid.UUID, session: SessionDep, user: CurrentUser):
    await _visible_requirement(session, requirement_id, user.id)
    raise EndpointMovedError(
        "La revisio d'un requisit es per empresa. Fes-la a "
        "/companies/{company_id}/tenders/{tender_id}/review/requirements.",
        details={"requirement_id": str(requirement_id)},
    )


async def _visible_criterion(
    session: SessionDep, criterion_id: uuid.UUID, user_id: uuid.UUID
) -> AwardCriterion:
    """El criteri, NOMES si l'usuari pot veure la licitacio a la qual pertany.

    Mateixa regla que `_load_tender`; si no, 404 com si no existis (sense filtrar
    ni l'existencia ni cap dada del criteri d'una licitacio privada d'una altra
    empresa).
    """
    criterion = await session.scalar(
        sa.select(AwardCriterion)
        .join(Tender, Tender.id == AwardCriterion.tender_id)
        .options(selectinload(AwardCriterion.evidence))
        .where(AwardCriterion.id == criterion_id, visible_to_user(user_id))
    )
    if criterion is None:
        raise NotFoundError("Criteri no trobat.")
    return criterion


def _criterion_read(criterion: AwardCriterion, decoration: dict) -> AwardCriterionRead:
    read = AwardCriterionRead.model_validate(criterion)
    read.evidence = _evidence_reads(criterion, decoration)
    return read


@router.get(
    "/award-criteria/{criterion_id}",
    response_model=AwardCriterionRead,
    summary="Detall d'un criteri d'adjudicacio",
)
async def get_award_criterion(criterion_id: uuid.UUID, session: SessionDep, user: CurrentUser):
    criterion = await _visible_criterion(session, criterion_id, user.id)
    decoration = await _decorate_evidence(session, [criterion])
    return _criterion_read(criterion, decoration)


# RETIRAT (S-R1): vegeu `review_requirement`. Mateix contracte: visibilitat
# primer (404 indistingible), despres 410 sense cap escriptura.
@router.post(
    "/award-criteria/{criterion_id}/review",
    summary="(Retirat) La revisio es per empresa",
    status_code=410,
)
async def review_award_criterion(criterion_id: uuid.UUID, session: SessionDep, user: CurrentUser):
    await _visible_criterion(session, criterion_id, user.id)
    raise EndpointMovedError(
        "La revisio d'un criteri d'adjudicacio es per empresa. Fes-la a "
        "/companies/{company_id}/tenders/{tender_id}/review/award-criteria.",
        details={"criterion_id": str(criterion_id)},
    )


__all__ = ["router", "AnalysisRunStatus"]


# --------------------------------------------------------------------------- #
# Requisits escrits a ma (S3-S5)                                                #
# --------------------------------------------------------------------------- #
#
# Company-scoped i NOMES sobre una licitacio oberta a ma i de la propia
# empresa. El motiu no es de permisos sino de model: `Requirement` es
# tender-level i no te `owner_company_id`, aixi que deixar escriure'n sobre una
# licitacio PUBLICA faria que el que escriu una empresa aparegues a totes les
# altres. Mentre no hi hagi tenancy a `Requirement`, la porta es aquesta.


async def _editable_tender(session, company_id: uuid.UUID, tender_id: uuid.UUID):
    return await manual_tender.load_editable_tender(session, company_id, tender_id)


async def _requirement_read(session, requirement) -> RequirementRead:
    """La fitxa d'un requisit, amb la seva provinenca."""
    read = RequirementRead.model_validate(requirement)
    if requirement.origin == RequirementOrigin.MANUAL:
        read.confidence = None
        refs = (await manual_requirement.source_refs_of(session, [requirement.id])).get(
            requirement.id, []
        )
        read.citations = [
            read_of(await resolve_source_ref(session, ref)) for ref in refs
        ]
    return read


@router.post(
    "/companies/{company_id}/tenders/{tender_id}/requirements",
    response_model=RequirementRead,
    status_code=status.HTTP_201_CREATED,
    summary="Escriu un requisit a ma",
)
async def create_manual_requirement(
    tender_id: uuid.UUID,
    payload: ManualRequirementCreate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Afegeix un requisit que l'analisi no ha trobat, o que no hi podia ser.

    No crea cap `RequirementExtractionRun` i no en pot crear: una run modela
    una execucio d'analisi amb el seu proveidor i el seu corpus, i aqui no n'hi
    ha cap. El requisit neix `unreviewed`: que l'hagi escrit una persona no vol
    dir que estigui comprovat, i citar-ne la font tampoc no el verifica.
    """
    manual_requirement.assert_can_edit(ctx.membership.role)
    tender = await _editable_tender(session, ctx.company_id, tender_id)

    fields = payload.model_dump(exclude={"source_ref_ids"})
    requirement = await manual_requirement.create_manual_requirement(
        session, tender=tender, user=ctx.user, fields=fields
    )
    for ref_id in payload.source_ref_ids:
        await manual_requirement.link_source_ref(
            session, requirement=requirement, source_ref_id=ref_id, user=ctx.user
        )

    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="requirement",
        entity_id=requirement.id,
        summary=f"licitacio={tender_id} requisit={requirement.id}",
    )
    await session.commit()
    await session.refresh(requirement)
    return await _requirement_read(session, requirement)


@router.patch(
    "/companies/{company_id}/tenders/{tender_id}/requirements/{requirement_id}",
    response_model=ManualRequirementResult,
    summary="Corregeix un requisit escrit a ma",
)
async def update_manual_requirement(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: ManualRequirementUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Nomes els escrits a ma.

    Un requisit EXTRET no s'edita per aqui, i no es cap oblit: seria reescriure
    la sortida d'una execucio concreta, i llavors la run ja no diria el que va
    produir. Els extrets tenen el seu cami, que es la revisio.

    Un PATCH que no canvia res no es un canvi: no mou l'autor, no deixa
    auditoria i no fa caducar l'abast tecnic.
    """
    manual_requirement.assert_can_edit(ctx.membership.role)
    tender = await _editable_tender(session, ctx.company_id, tender_id)
    requirement = await manual_requirement.load_manual_requirement(
        session, tender, requirement_id
    )

    fields = {
        name: getattr(payload, name)
        for name in manual_requirement.EDITABLE_FIELDS
        if name in payload.model_fields_set
    }
    changed = await manual_requirement.update_manual_requirement(
        session, tender=tender, requirement=requirement, user=ctx.user, fields=fields
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="requirement",
            entity_id=requirement.id,
            summary=f"requisit={requirement.id} camps={','.join(sorted(fields)) or '-'}",
        )
    await session.commit()
    await session.refresh(requirement)
    return ManualRequirementResult(
        changed=changed, requirement=await _requirement_read(session, requirement)
    )


@router.post(
    "/companies/{company_id}/tenders/{tender_id}/requirements/{requirement_id}/archive",
    response_model=ManualRequirementResult,
    summary="Arxiva un requisit escrit a ma",
)
async def archive_manual_requirement(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """El treu del conjunt efectiu sense esborrar-lo.

    No hi ha esborrat dur: les seves cites i la feina que va generar segueixen
    existint, i arxivar vol dir "ja no compta", no "no va existir". Idempotent.
    """
    manual_requirement.assert_can_edit(ctx.membership.role)
    tender = await _editable_tender(session, ctx.company_id, tender_id)
    requirement = await manual_requirement.load_manual_requirement(
        session, tender, requirement_id
    )

    changed = await manual_requirement.archive_manual_requirement(
        session, requirement=requirement, user=ctx.user
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="requirement",
            entity_id=requirement.id,
            summary=f"requisit={requirement.id} arxivat",
        )
    await session.commit()
    await session.refresh(requirement)
    return ManualRequirementResult(
        changed=changed, requirement=await _requirement_read(session, requirement)
    )


@router.post(
    "/companies/{company_id}/tenders/{tender_id}/requirements/{requirement_id}/citations",
    response_model=RequirementRead,
    summary="Enllaça una cita a un requisit",
)
async def link_requirement_citation(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    payload: RequirementCitationLink,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """La cita ha de ser de la MATEIXA licitacio.

    Una FK sola no ho demostra: dues licitacions poden tenir els mateixos bytes,
    i deixar que un requisit d'una citi la font de l'altra faria que la prova
    apuntes a un expedient que no es el seu.
    """
    manual_requirement.assert_can_edit(ctx.membership.role)
    tender = await _editable_tender(session, ctx.company_id, tender_id)
    requirement = await manual_requirement.load_manual_requirement(
        session, tender, requirement_id
    )

    await manual_requirement.link_source_ref(
        session, requirement=requirement, source_ref_id=payload.source_ref_id, user=ctx.user
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="requirement",
        entity_id=requirement.id,
        summary=f"requisit={requirement.id} cita+={payload.source_ref_id}",
    )
    await session.commit()
    return await _requirement_read(session, requirement)


@router.delete(
    "/companies/{company_id}/tenders/{tender_id}/requirements/{requirement_id}"
    "/citations/{source_ref_id}",
    response_model=RequirementRead,
    summary="Treu una cita d'un requisit",
)
async def unlink_requirement_citation(
    tender_id: uuid.UUID,
    requirement_id: uuid.UUID,
    source_ref_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Treu l'ENLLAC. La cita no s'esborra.

    Pot sostenir-ne d'altres, i era certa el dia que es va crear. L'unic que
    canvia es que aquest requisit ja no s'hi recolza.
    """
    manual_requirement.assert_can_edit(ctx.membership.role)
    tender = await _editable_tender(session, ctx.company_id, tender_id)
    requirement = await manual_requirement.load_manual_requirement(
        session, tender, requirement_id
    )

    removed = await manual_requirement.unlink_source_ref(
        session, requirement=requirement, source_ref_id=source_ref_id
    )
    if removed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="requirement",
            entity_id=requirement.id,
            summary=f"requisit={requirement.id} cita-={source_ref_id}",
        )
    await session.commit()
    return await _requirement_read(session, requirement)


# --------------------------------------------------------------------------- #
# Crear cites (S3-S5.1)                                                         #
# --------------------------------------------------------------------------- #
#
# S3-S4 va deixar el servei fet i sense endpoint a proposit: no hi havia cap
# consumidor i una UX que despres s'hagues de retirar es pitjor que cap. Ara
# n'hi ha un de real —els requisits escrits a ma— i per tant s'exposa.
#
# El router no valida res pel seu compte: auth, schema, servei canonic i
# read-model. Tota la comprovacio de la cadena viu a `source_refs.py`, i
# duplicar-la voldria dir que un dia una copia es quedaria enrere.


async def _source_ref_read(session, ref) -> TenderSourceRefRead:
    return read_of(await resolve_source_ref(session, ref))


@router.post(
    "/companies/{company_id}/tenders/{tender_id}/source-refs/extracted-page",
    response_model=TenderSourceRefRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cita una pagina d'un plec llegit",
)
async def create_extracted_page_source_ref(
    tender_id: uuid.UUID,
    payload: ExtractedPageRefCreate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """La cita mes forta: de quins bytes, amb quin parser i de quina pagina.

    El `quote` NO s'accepta del client: si hi ha posicions, es deriva del text
    real d'aquella pagina i es comprova que hi capiga. Una cita que digui una
    cosa que el document no diu es pitjor que no tenir-ne cap.
    """
    manual_requirement.assert_can_edit(ctx.membership.role)
    tender = await manual_tender.load_editable_tender(session, ctx.company_id, tender_id)

    ref = await source_refs.create_extracted_page_ref(
        session,
        tender_id=tender.id,
        extraction_id=payload.extraction_id,
        page_id=payload.page_id,
        user=ctx.user,
        location_label=payload.location_label,
        char_start=payload.char_start,
        char_end=payload.char_end,
    )
    await session.commit()
    return await _source_ref_read(session, ref)


@router.post(
    "/companies/{company_id}/tenders/{tender_id}/source-refs/document-file",
    response_model=TenderSourceRefRead,
    status_code=status.HTTP_201_CREATED,
    summary="Cita una versio d'un document sense pagines",
)
async def create_document_file_source_ref(
    tender_id: uuid.UUID,
    payload: DocumentFileRefCreate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Per a un XLSX o un DOCX: guardats, sense cap pagina i citables igual.

    Que no en sapiguem llegir el text es una limitacio nostra, no un fet del
    document. `location_label` diu on mirar i qui ho llegeixi hi anira.
    """
    manual_requirement.assert_can_edit(ctx.membership.role)
    tender = await manual_tender.load_editable_tender(session, ctx.company_id, tender_id)

    ref = await source_refs.create_document_file_ref(
        session,
        tender_id=tender.id,
        file_id=payload.tender_document_file_id,
        user=ctx.user,
        location_label=payload.location_label,
        quote=payload.quote,
    )
    await session.commit()
    return await _source_ref_read(session, ref)
