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
from app.core.errors import EndpointMovedError, NotFoundError
from app.models.audit import AuditAction
from app.models.enums import AnalysisRunStatus, RequirementOrigin
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
    ReviewRequest,
)
from app.services import audit, manual_requirement, manual_tender, source_refs
from app.services.requirement_analysis import (
    analysis_input,
)
from app.services.source_refs import resolve_source_ref
from app.tenders.visibility import visible_to_user

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
async def read_analysis(tender_id: uuid.UUID, session: SessionDep, user: CurrentUser):
    await _load_tender(session, tender_id, user.id)
    # El conjunt EFECTIU: l'analisi vigent mes els requisits escrits a ma. Les
    # dues fonts es mantenen separades —`origin` ho diu a cada fila— perque fer
    # veure que un manual surt de l'ultima run seria atribuir a un model una
    # cosa que ha escrit una persona.
    effective = await manual_requirement.effective_requirements(session, tender_id)
    run = effective.run
    if run is None and not effective.manual:
        return RequirementAnalysisRead()

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
    criteria_reads = []
    candidate_reads = []
    for criterion in criteria:
        read = _criterion_read(criterion, decoration)
        if counts_as_award_criterion(criterion.adjudication_basis_json):
            criteria_reads.append(read)
        else:
            candidate_reads.append(read)

    # Obsolet: els documents han canviat des que es va fer l'analisi. Amb la
    # mateixa definicio d'entrada que l'analisi (sostre de pagines inclos).
    # `is_stale` parla NOMES de la run extreta i del corpus documental. Un
    # requisit manual no hi entra: una afirmacio humana no caduca perque s'hagi
    # tornat a llegir un PDF, i crear-ne un tampoc no fa que l'analisi cobreixi
    # els documents d'ara.
    is_stale = False
    if run is not None:
        current = await analysis_input(session, tender_id)
        is_stale = bool(current.documents) and current.fingerprint != run.input_fingerprint

    return RequirementAnalysisRead(
        run=ExtractionRunRead.model_validate(run) if run is not None else None,
        requirements=requirement_reads,
        award_criteria=criteria_reads,
        award_candidates=candidate_reads,
        is_stale=is_stale,
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


@router.get(
    "/requirements/{requirement_id}",
    response_model=RequirementRead,
    summary="Detall d'un requisit",
)
async def get_requirement(requirement_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    requirement = await session.scalar(
        sa.select(Requirement)
        .options(selectinload(Requirement.evidence))
        .where(Requirement.id == requirement_id)
    )
    if requirement is None:
        raise NotFoundError("Requisit no trobat.")
    decoration = await _decorate_evidence(session, [requirement])
    read = RequirementRead.model_validate(requirement)
    read.evidence = _evidence_reads(requirement, decoration)
    return read


# LEGACY (S-R1): escriu la revisio a la fila d'extraccio, que es GLOBAL i
# compartida entre empreses. Es conserva per compatibilitat; la revisio nova
# es per empresa i viu a `/companies/{company_id}/tenders/{tender_id}/review`.
@router.post(
    "/requirements/{requirement_id}/review",
    response_model=RequirementRead,
    summary="Revisa un requisit",
)
async def review_requirement(
    requirement_id: uuid.UUID,
    payload: ReviewRequest,
    session: SessionDep,
    user: CurrentUser,
):
    """Registra la revisio humana SENSE tocar el que va dir el model.

    La correccio va a `manual_override_json`; els camps extrets es queden com
    estaven. Aixi es pot auditar sempre si l'extractor encertava.
    """
    requirement = await session.scalar(
        sa.select(Requirement)
        .options(selectinload(Requirement.evidence))
        .where(Requirement.id == requirement_id)
    )
    if requirement is None:
        raise NotFoundError("Requisit no trobat.")

    requirement.review_status = payload.review_status
    requirement.review_note = payload.note
    if payload.manual_override is not None:
        requirement.manual_override_json = payload.manual_override
    requirement.reviewed_by = user.id
    requirement.reviewed_at = sa.func.now()
    await session.commit()
    await session.refresh(requirement)

    decoration = await _decorate_evidence(session, [requirement])
    read = RequirementRead.model_validate(requirement)
    read.evidence = _evidence_reads(requirement, decoration)
    return read


def _criterion_read(criterion: AwardCriterion, decoration: dict) -> AwardCriterionRead:
    read = AwardCriterionRead.model_validate(criterion)
    read.evidence = _evidence_reads(criterion, decoration)
    return read


@router.get(
    "/award-criteria/{criterion_id}",
    response_model=AwardCriterionRead,
    summary="Detall d'un criteri d'adjudicacio",
)
async def get_award_criterion(criterion_id: uuid.UUID, session: SessionDep, _: CurrentUser):
    criterion = await session.scalar(
        sa.select(AwardCriterion)
        .options(selectinload(AwardCriterion.evidence))
        .where(AwardCriterion.id == criterion_id)
    )
    if criterion is None:
        raise NotFoundError("Criteri no trobat.")
    decoration = await _decorate_evidence(session, [criterion])
    return _criterion_read(criterion, decoration)


# LEGACY (S-R1): escriu la revisio a la fila d'extraccio, que es GLOBAL i
# compartida entre empreses. Es conserva per compatibilitat; la revisio nova
# es per empresa i viu a `/companies/{company_id}/tenders/{tender_id}/review`.
@router.post(
    "/award-criteria/{criterion_id}/review",
    response_model=AwardCriterionRead,
    summary="Revisa un criteri d'adjudicacio",
)
async def review_award_criterion(
    criterion_id: uuid.UUID,
    payload: ReviewRequest,
    session: SessionDep,
    user: CurrentUser,
):
    criterion = await session.scalar(
        sa.select(AwardCriterion)
        .options(selectinload(AwardCriterion.evidence))
        .where(AwardCriterion.id == criterion_id)
    )
    if criterion is None:
        raise NotFoundError("Criteri no trobat.")

    criterion.review_status = payload.review_status
    criterion.review_note = payload.note
    if payload.manual_override is not None:
        criterion.manual_override_json = payload.manual_override
    criterion.reviewed_by = user.id
    criterion.reviewed_at = sa.func.now()
    await session.commit()
    await session.refresh(criterion)

    decoration = await _decorate_evidence(session, [criterion])
    return _criterion_read(criterion, decoration)


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
