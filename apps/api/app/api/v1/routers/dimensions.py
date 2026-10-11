"""Dimensionament: HTTP (S3-S6).

Tot penja d'una candidatura i d'una empresa. El router no calcula res: qui
detecta una contradiccio, qui porta el testimoni de CAS i qui exigeix un motiu
per decidir es `services/dimensions.py`.

L'unica cosa que val la pena llegir aqui es que hi ha **quatre** escriptures
diferents —corregir metadades, afegir una font, decidir i arxivar— i que cap
d'elles no es pot fer passar per una altra. Decidir te el seu endpoint perque
exigeix explicar-se, i si es pogues fer amb el PATCH de metadades el motiu
seria esquivable.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from sqlalchemy.orm import attributes

from app.api.deps import CompanyCtx, SessionDep
from app.models.enums import AuditAction
from app.schemas.dimensions import (
    DimensionArchive,
    DimensionCreate,
    DimensionDecide,
    DimensionRead,
    DimensionUpdate,
    ObservationCreate,
    ObservationRead,
)
from app.schemas.provenance import read_of
from app.services import audit
from app.services import bid_workspace as workspace_service
from app.services import dimensions as service
from app.services.source_refs import resolve_source_ref
from app.services.workspace_gate import assert_workspace_go

router = APIRouter(prefix="/workspaces/{workspace_id}/dimensions", tags=["dimensionament"])


async def _read(session, dimension, observations=None) -> DimensionRead:
    """La fitxa d'una dimensio, amb les seves fonts i les cites resoltes."""
    if observations is None:
        observations = (await service.observations_of(session, [dimension.id])).get(
            dimension.id, []
        )
    reads: list[ObservationRead] = []
    for observation in observations:
        read = ObservationRead.model_validate(observation)
        if observation.source_ref_id is not None:
            from app.models.provenance import TenderSourceRef

            ref = await session.get(TenderSourceRef, observation.source_ref_id)
            if ref is not None:
                read.source_ref = read_of(await resolve_source_ref(session, ref))
        reads.append(read)

    # Deixa la col.leccio per carregada amb el que ja hem llegit en una sola
    # consulta: sense aixo, llegir-la dispararia una consulta per dimensio
    # (i, en async, un `MissingGreenlet`).
    attributes.set_committed_value(dimension, "observations", observations)
    dimension_read = DimensionRead.model_validate(dimension)
    dimension_read.observations = reads
    return dimension_read


@router.get("", response_model=list[DimensionRead], summary="Dimensionament")
async def list_dimensions(
    workspace_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    include_archived: bool = False,
):
    """Lectura per a qualsevol membre: aqui no hi ha res intern."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    rows = await service.list_dimensions(
        session, workspace, include_archived=include_archived
    )
    observations = await service.observations_of(session, [row.id for row in rows])
    return [await _read(session, row, observations.get(row.id, [])) for row in rows]


@router.post(
    "",
    response_model=DimensionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix una dimensio",
)
async def create_dimension(
    workspace_id: uuid.UUID,
    payload: DimensionCreate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Crea el concepte, sense valor i sense decidir.

    Les dimensions **no** es generen soles: ni dels requisits, ni dels numeros
    que apareguin als plecs, ni de cap model. Que es una dimensio ho diu una
    persona; el sistema nomes sap detectar despres que dues fonts es
    contradiuen.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_go(session, workspace)

    dimension = await service.create_dimension(
        session, workspace=workspace, user=ctx.user, fields=payload.model_dump()
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="workspace_dimension",
        entity_id=dimension.id,
        summary=f"candidatura={workspace_id} dimensio={dimension.key}",
    )
    await session.commit()
    return await _read(session, dimension, [])


@router.patch(
    "/{dimension_id}", response_model=DimensionRead, summary="Corregeix una dimensio"
)
async def update_dimension(
    workspace_id: uuid.UUID,
    dimension_id: uuid.UUID,
    payload: DimensionUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Nomes metadades: clau, etiqueta, unitat i ambit.

    L'estat, el valor de treball i el motiu no s'hi poden tocar. Decidir es una
    altra cosa i te el seu endpoint.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_go(session, workspace)
    dimension = await service.load_dimension(session, workspace, dimension_id)

    fields = {
        name: getattr(payload, name)
        for name in service.EDITABLE_FIELDS
        if name in payload.model_fields_set
    }
    changed = await service.update_dimension(
        session,
        workspace=workspace,
        dimension=dimension,
        expected_revision=payload.expected_revision,
        fields=fields,
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_dimension",
            entity_id=dimension.id,
            summary=f"dimensio={dimension.id} camps={','.join(sorted(fields)) or '-'}",
        )
    await session.commit()
    return await _read(session, dimension)


@router.post(
    "/{dimension_id}/observations",
    response_model=DimensionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registra que una font diu un valor",
)
async def add_observation(
    workspace_id: uuid.UUID,
    dimension_id: uuid.UUID,
    payload: ObservationCreate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Append-only: una observacio no s'edita ni s'esborra mai.

    No diu "el valor es 151", diu "aquesta font deia 151". Reescriure-la seria
    canviar el que algu va llegir, i llavors el registre deixaria de servir per
    entendre d'on ve una discrepancia.

    Si despres d'afegir-la hi ha dos numeros diferents, la dimensio passa a
    `disputed` tota sola. Es l'unic que el sistema decideix: que hi ha un
    problema, mai quina xifra es la bona.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_go(session, workspace)
    dimension = await service.load_dimension(session, workspace, dimension_id)

    observation = await service.add_observation(
        session,
        workspace=workspace,
        dimension=dimension,
        user=ctx.user,
        expected_revision=payload.expected_revision,
        source_kind=payload.source_kind,
        value_text=payload.value_text,
        value_num=payload.value_num,
        note=payload.note,
        source_ref_id=payload.source_ref_id,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="workspace_dimension_observation",
        entity_id=observation.id,
        summary=f"dimensio={dimension.id} font={payload.source_kind}",
    )
    await session.commit()
    return await _read(session, dimension)


@router.post(
    "/{dimension_id}/decide",
    response_model=DimensionRead,
    summary="Fixa el valor de treball",
)
async def decide_dimension(
    workspace_id: uuid.UUID,
    dimension_id: uuid.UUID,
    payload: DimensionDecide,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Una persona tria el valor que fara servir la candidatura, i diu per que.

    El motiu es obligatori. Les observacions no s'esborren —ni les que
    contradeien el valor triat—: son justament el que explica la decisio.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_go(session, workspace)
    dimension = await service.load_dimension(session, workspace, dimension_id)

    await service.decide_dimension(
        session,
        dimension=dimension,
        user=ctx.user,
        expected_revision=payload.expected_revision,
        working_value_text=payload.working_value_text,
        working_value_num=payload.working_value_num,
        reason=payload.reason,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="workspace_dimension",
        entity_id=dimension.id,
        summary=f"dimensio={dimension.id} decidida={payload.working_value_text}",
    )
    await session.commit()
    return await _read(session, dimension)


@router.post(
    "/{dimension_id}/archive",
    response_model=DimensionRead,
    summary="Arxiva una dimensio",
)
async def archive_dimension(
    workspace_id: uuid.UUID,
    dimension_id: uuid.UUID,
    payload: DimensionArchive,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """La treu de la llista activa. No hi ha esborrat dur.

    Les observacions es conserven: arxivar vol dir "aixo ja no compta", no "no
    va existir". Idempotent.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    await assert_workspace_go(session, workspace)
    dimension = await service.load_dimension(session, workspace, dimension_id)

    changed = await service.archive_dimension(
        session, dimension=dimension, expected_revision=payload.expected_revision
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_dimension",
            entity_id=dimension.id,
            summary=f"dimensio={dimension.id} arxivada",
        )
    await session.commit()
    return await _read(session, dimension)
