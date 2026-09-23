"""Opcions de solucio: HTTP (S3-S7).

Dues coses que val la pena llegir aqui.

**Hi ha dos PATCH i no un.** El public (`PATCH …/{id}`) no te ni un lloc on
posar el cost; l'intern (`PATCH …/{id}/internal-economics`) nomes accepta cost i
notes de dins, i nomes per als rols economics. Amb un sol endpoint, un
`technical` que enviés el formulari sencer amb el camp buit netejaria un cost
que ni tan sols pot veure.

**La redaccio es fa en un sol lloc.** `_read()` rep `internal` i es l'unica
funcio que decideix si el bloc `internal_economics` existeix. Cap resposta
d'aquest fitxer construeix la seva propia: si cadascuna ho decidis pel seu
compte, un dia una se n'oblidaria.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CompanyCtx, SessionDep
from app.models.dimensions import WorkspaceDimension
from app.models.enums import AuditAction
from app.models.solution_options import WorkspaceSolutionOption
from app.schemas.solution_options import (
    OptionDimensionRead,
    OptionDimensionWrite,
    OptionInternalRead,
    SolutionOptionArchive,
    SolutionOptionCreate,
    SolutionOptionInternalUpdate,
    SolutionOptionListRead,
    SolutionOptionPublicUpdate,
    SolutionOptionRead,
    SolutionOptionSelectionWrite,
)
from app.services import audit
from app.services import bid_workspace as workspace_service
from app.services import solution_option_dimensions as link_service
from app.services import solution_options as service

router = APIRouter(
    prefix="/workspaces/{workspace_id}/solution-options", tags=["opcions de solucio"]
)


def _read(
    option: WorkspaceSolutionOption,
    *,
    internal: bool,
    effective_ids: set[uuid.UUID] | None = None,
    dimensions: list[WorkspaceDimension] | None = None,
) -> SolutionOptionRead:
    """La fitxa d'una opcio, amb el cost nomes si qui mira el pot veure.

    Quan `internal` es fals el bloc no hi es, i aixo NO es el mateix que
    enviar-lo amb el cost a `null`: aquell `null` voldria dir "encara no s'ha
    escrit", que es una informacio que aquest rol tampoc no ha de poder deduir.
    Tampoc no s'hi posa un `0`, que seria una xifra falsa.
    """
    read = SolutionOptionRead.model_validate(option)
    read.internal_economics = (
        OptionInternalRead(
            unit_cost_ex_vat=option.unit_cost_ex_vat,
            internal_note=option.internal_note,
        )
        if internal
        else None
    )
    if effective_ids is not None and option.requirement_id is not None:
        read.requirement_is_effective = option.requirement_id in effective_ids
    # Es passen ja carregades: llegir-les des de l'objecte dispararia una
    # consulta per opcio, i amb vint opcions serien vint-i-una consultes.
    read.dimensions = [
        OptionDimensionRead.model_validate(dimension) for dimension in (dimensions or [])
    ]
    return read


@router.get("", response_model=SolutionOptionListRead, summary="Opcions i cotitzacions")
async def list_options(
    workspace_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
    include_archived: bool = False,
):
    """Lectura per a qualsevol membre. El cost, nomes per a qui el pot veure."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    internal = service.can_view_internal_economics(ctx.membership.role)
    rows = await service.list_options(
        session, workspace, include_archived=include_archived
    )
    effective_ids = await service.effective_requirement_ids(session, workspace)
    linked = await link_service.dimensions_of(session, [row.id for row in rows])
    return SolutionOptionListRead(
        can_view_internal_economics=internal,
        items=[
            _read(
                row,
                internal=internal,
                effective_ids=effective_ids,
                dimensions=linked.get(row.id, []),
            )
            for row in rows
        ],
    )


@router.get(
    "/{option_id}", response_model=SolutionOptionRead, summary="Detall d'una opcio"
)
async def get_option(
    workspace_id: uuid.UUID,
    option_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    option = await service.load_option(session, workspace, option_id)
    effective_ids = await service.effective_requirement_ids(session, workspace)
    linked = await link_service.dimensions_of(session, [option.id])
    return _read(
        option,
        internal=service.can_view_internal_economics(ctx.membership.role),
        effective_ids=effective_ids,
        dimensions=linked.get(option.id, []),
    )


@router.post(
    "",
    response_model=SolutionOptionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Afegeix una opcio",
)
async def create_option(
    workspace_id: uuid.UUID,
    payload: SolutionOptionCreate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Neix sense decidir. Triar-la es una accio a part i d'una persona."""
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)

    body = payload.model_dump(exclude={"internal_economics"})
    option = await service.create_option(
        session,
        workspace=workspace,
        role=ctx.membership.role,
        fields=body,
        internal=(
            payload.internal_economics.model_dump()
            if payload.internal_economics is not None
            else None
        ),
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.CREATE,
        entity_type="workspace_solution_option",
        entity_id=option.id,
        summary=f"candidatura={workspace_id} proveidor={option.supplier_name or '-'}",
    )
    await session.commit()
    return _read(
        option,
        internal=service.can_view_internal_economics(ctx.membership.role),
        effective_ids=await service.effective_requirement_ids(session, workspace),
        dimensions=(await link_service.dimensions_of(session, [option.id])).get(option.id, []),
    )


@router.patch(
    "/{option_id}", response_model=SolutionOptionRead, summary="Edita la fitxa publica"
)
async def update_option(
    workspace_id: uuid.UUID,
    option_id: uuid.UUID,
    payload: SolutionOptionPublicUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Proveidor, producte, configuracio i cotitzacio. Mai el cost.

    El schema el prohibeix (`extra="forbid"`), de manera que enviar-hi
    `unit_cost_ex_vat` es un 422 i no un camp que s'ignori: qui l'envia ha de
    saber que no s'ha desat.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    option = await service.load_option(session, workspace, option_id)

    fields = {
        name: getattr(payload, name)
        for name in service.PUBLIC_FIELDS
        if name in payload.model_fields_set
    }
    changed = await service.update_public(
        session,
        workspace=workspace,
        option=option,
        expected_revision=payload.expected_revision,
        fields=fields,
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_solution_option",
            entity_id=option.id,
            summary=f"opcio={option.id} camps={','.join(sorted(fields)) or '-'}",
        )
    await session.commit()
    return _read(
        option,
        internal=service.can_view_internal_economics(ctx.membership.role),
        effective_ids=await service.effective_requirement_ids(session, workspace),
        dimensions=(await link_service.dimensions_of(session, [option.id])).get(option.id, []),
    )


@router.patch(
    "/{option_id}/internal-economics",
    response_model=SolutionOptionRead,
    summary="Edita el cost intern",
)
async def update_internal_economics(
    workspace_id: uuid.UUID,
    option_id: uuid.UUID,
    payload: SolutionOptionInternalUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Nomes cost i notes de dins, i nomes per als rols economics."""
    service.assert_can_edit(ctx.membership.role)
    service.assert_can_write_internal(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    option = await service.load_option(session, workspace, option_id)

    sent = payload.model_fields_set
    changed = await service.update_internal(
        session,
        option=option,
        role=ctx.membership.role,
        expected_revision=payload.expected_revision,
        unit_cost_ex_vat=(
            payload.unit_cost_ex_vat if "unit_cost_ex_vat" in sent else service.UNSET
        ),
        internal_note=payload.internal_note if "internal_note" in sent else service.UNSET,
    )
    if changed:
        # El QUE ha canviat, mai el VALOR: escriure la xifra aqui la deixaria
        # llegible per a qui no pot veure el cost a la fitxa.
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_solution_option",
            entity_id=option.id,
            summary=f"opcio={option.id} economia interna actualitzada",
        )
    await session.commit()
    return _read(
        option,
        internal=True,
        effective_ids=await service.effective_requirement_ids(session, workspace),
        dimensions=(await link_service.dimensions_of(session, [option.id])).get(option.id, []),
    )


@router.post(
    "/{option_id}/selection",
    response_model=SolutionOptionRead,
    summary="Tria o descarta l'opcio",
)
async def set_selection(
    workspace_id: uuid.UUID,
    option_id: uuid.UUID,
    payload: SolutionOptionSelectionWrite,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Descartar exigeix motiu; triar no auto-descarta cap germana.

    Que aquesta sigui la triada no diu res de les altres. Posar-les a
    `rejected` seria escriure una decisio que ningu no ha pres.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    option = await service.load_option(session, workspace, option_id)

    await service.set_selection(
        session,
        option=option,
        user=ctx.user,
        expected_revision=payload.expected_revision,
        selection=payload.selection,
        reason=payload.reason,
    )
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.UPDATE,
        entity_type="workspace_solution_option",
        entity_id=option.id,
        summary=f"opcio={option.id} tria={payload.selection.value}",
    )
    await session.commit()
    return _read(
        option,
        internal=service.can_view_internal_economics(ctx.membership.role),
        effective_ids=await service.effective_requirement_ids(session, workspace),
        dimensions=(await link_service.dimensions_of(session, [option.id])).get(option.id, []),
    )


@router.post(
    "/{option_id}/archive", response_model=SolutionOptionRead, summary="Arxiva l'opcio"
)
async def archive_option(
    workspace_id: uuid.UUID,
    option_id: uuid.UUID,
    payload: SolutionOptionArchive,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """La treu de la llista activa. No hi ha esborrat dur. Idempotent."""
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    option = await service.load_option(session, workspace, option_id)

    changed = await service.archive_option(
        session, option=option, expected_revision=payload.expected_revision
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_solution_option",
            entity_id=option.id,
            summary=f"opcio={option.id} arxivada",
        )
    await session.commit()
    return _read(
        option,
        internal=service.can_view_internal_economics(ctx.membership.role),
        effective_ids=await service.effective_requirement_ids(session, workspace),
        dimensions=(await link_service.dimensions_of(session, [option.id])).get(option.id, []),
    )


@router.get(
    "/{option_id}/dimensions",
    response_model=list[OptionDimensionRead],
    summary="De quines magnituds depen",
)
async def list_option_dimensions(
    workspace_id: uuid.UUID,
    option_id: uuid.UUID,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Lectura per a qualsevol membre: aqui no hi ha res economic."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    option = await service.load_option(session, workspace, option_id)
    linked = await link_service.dimensions_of(session, [option.id])
    return [
        OptionDimensionRead.model_validate(dimension)
        for dimension in linked.get(option.id, [])
    ]


@router.put(
    "/{option_id}/dimensions/{dimension_id}",
    response_model=SolutionOptionRead,
    summary="Declara que l'opcio depen d'aquesta magnitud",
)
async def attach_dimension(
    workspace_id: uuid.UUID,
    option_id: uuid.UUID,
    dimension_id: uuid.UUID,
    payload: OptionDimensionWrite,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Enllacar NO copia cap xifra.

    Que la magnitud valgui 181 avui no escriu 181 a `quantity`, ni ara ni quan
    canviï. La dependencia diu de que depen l'opcio; quina quantitat hi posa,
    ho diu una persona.

    `PUT` i no `POST` perque es idempotent: enviar-ho dues vegades deixa una
    sola relacio i la segona vegada no mou el testimoni.

    Es pot dependre d'una magnitud encara sense decidir o en disputa: saber de
    que depen una opcio es justament el que fa evident que cal decidir-ho.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    option = await service.load_option(session, workspace, option_id)

    changed = await link_service.attach_dimension(
        session,
        workspace=workspace,
        option=option,
        dimension_id=dimension_id,
        user=ctx.user,
        expected_revision=payload.expected_revision,
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_solution_option",
            entity_id=option.id,
            summary=f"opcio={option.id} depen de la magnitud {dimension_id}",
        )
    await session.commit()
    return _read(
        option,
        internal=service.can_view_internal_economics(ctx.membership.role),
        effective_ids=await service.effective_requirement_ids(session, workspace),
        dimensions=(await link_service.dimensions_of(session, [option.id])).get(option.id, []),
    )


@router.delete(
    "/{option_id}/dimensions/{dimension_id}",
    response_model=SolutionOptionRead,
    summary="Treu la dependencia",
)
async def detach_dimension(
    workspace_id: uuid.UUID,
    option_id: uuid.UUID,
    dimension_id: uuid.UUID,
    expected_revision: int,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Nomes desapareix la relacio: ni l'opcio ni la magnitud es toquen.

    Tambe es pot desenganxar una magnitud arxivada: es justament com es neteja
    el rastre d'una que ja no compta.

    El testimoni va per query i no al cos: un `DELETE` amb cos es legal pero
    incomode —el client del repo no en sap enviar i hi ha intermediaris que el
    descarten—, i aixi no cal tocar el client per a una sola ruta.
    """
    service.assert_can_edit(ctx.membership.role)
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    option = await service.load_option(session, workspace, option_id)

    changed = await link_service.detach_dimension(
        session,
        workspace=workspace,
        option=option,
        dimension_id=dimension_id,
        expected_revision=expected_revision,
    )
    if changed:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type="workspace_solution_option",
            entity_id=option.id,
            summary=f"opcio={option.id} ja no depen de la magnitud {dimension_id}",
        )
    await session.commit()
    return _read(
        option,
        internal=service.can_view_internal_economics(ctx.membership.role),
        effective_ids=await service.effective_requirement_ids(session, workspace),
        dimensions=(await link_service.dimensions_of(session, [option.id])).get(option.id, []),
    )
