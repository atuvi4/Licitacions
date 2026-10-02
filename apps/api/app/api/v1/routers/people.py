"""Equip huma: persones i titulacions.

Les titulacions pengen de la persona i no tenen `company_id` propi: el tenant
es deriva sempre de la persona, que si el te. Per aixo no fan servir la
fabrica CRUD generica.
"""

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Response, status

from app.api.deps import CompanyCtx, SessionDep
from app.api.v1.crud import apply_changes, build_crud_router, get_owned_or_404
from app.knowledge import access
from app.models.enums import AuditAction, LinkedEntityType
from app.models.people import CompanyPerson, PersonQualification
from app.schemas.people import (
    PersonCreate,
    PersonRead,
    PersonUpdate,
    QualificationCreate,
    QualificationRead,
    QualificationUpdate,
)
from app.services import audit
from app.services.entities import delete_links_for_entity, delete_links_for_person_qualifications
from app.services.knowledge_evidence import require_evidence_if_verified


async def _cleanup_person(session, person: CompanyPerson) -> None:
    await delete_links_for_person_qualifications(session, person.id)


people_router = build_crud_router(
    model=CompanyPerson,
    create_schema=PersonCreate,
    update_schema=PersonUpdate,
    read_schema=PersonRead,
    prefix="/people",
    tag="equip",
    resource_name="persona",
    order_by=[CompanyPerson.last_name, CompanyPerson.first_name],
    entity_type=LinkedEntityType.COMPANY_PERSON,
    pre_delete=_cleanup_person,
    # Coneixement actual (0052): verificat nomes amb evidencia; qui nomes consulta no
    # el modifica.
    validate=require_evidence_if_verified,
    authorize_write=access.assert_can_edit,
    authorize_delete=access.assert_can_delete,
)

qualifications_router = APIRouter(prefix="/people/{person_id}/qualifications", tags=["equip"])


async def _load_qualification(
    session, qualification_id: uuid.UUID, person_id: uuid.UUID
) -> PersonQualification:
    from app.core.errors import NotFoundError

    qualification = (
        await session.execute(
            sa.select(PersonQualification).where(
                PersonQualification.id == qualification_id,
                PersonQualification.person_id == person_id,
            )
        )
    ).scalar_one_or_none()
    if qualification is None:
        raise NotFoundError("Titulacio no trobada.")
    return qualification


@qualifications_router.get("", response_model=list[QualificationRead], summary="Llista titulacions")
async def list_qualifications(person_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    await get_owned_or_404(session, CompanyPerson, person_id, ctx.company_id)
    rows = (
        await session.execute(
            sa.select(PersonQualification)
            .where(PersonQualification.person_id == person_id)
            .order_by(PersonQualification.name)
        )
    ).scalars().all()
    return list(rows)


@qualifications_router.post(
    "",
    response_model=QualificationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crea titulacio",
)
async def create_qualification(
    person_id: uuid.UUID, payload: QualificationCreate, session: SessionDep, ctx: CompanyCtx
):
    await get_owned_or_404(session, CompanyPerson, person_id, ctx.company_id)
    qualification = PersonQualification(
        person_id=person_id,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        **payload.model_dump(exclude_unset=True),
    )
    session.add(qualification)
    await session.commit()
    await session.refresh(qualification)
    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="person_qualifications",
        entity_id=qualification.id,
    )
    await session.commit()
    return qualification


@qualifications_router.patch(
    "/{qualification_id}", response_model=QualificationRead, summary="Actualitza titulacio"
)
async def update_qualification(
    person_id: uuid.UUID,
    qualification_id: uuid.UUID,
    payload: QualificationUpdate,
    session: SessionDep,
    ctx: CompanyCtx,
):
    await get_owned_or_404(session, CompanyPerson, person_id, ctx.company_id)
    qualification = await _load_qualification(session, qualification_id, person_id)
    apply_changes(qualification, payload, ctx.user_id)
    await session.commit()
    await session.refresh(qualification)
    return qualification


@qualifications_router.delete(
    "/{qualification_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Esborra titulacio"
)
async def delete_qualification(
    person_id: uuid.UUID, qualification_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx
) -> Response:
    await get_owned_or_404(session, CompanyPerson, person_id, ctx.company_id)
    qualification = await _load_qualification(session, qualification_id, person_id)
    await delete_links_for_entity(
        session, LinkedEntityType.PERSON_QUALIFICATION, qualification.id
    )
    await session.delete(qualification)
    await audit.record(
        session,
        action=AuditAction.DELETE,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        entity_type="person_qualifications",
        entity_id=qualification_id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
