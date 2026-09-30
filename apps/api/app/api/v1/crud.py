"""Fabrica de routers CRUD amb aillament per empresa.

Totes les entitats corporatives comparteixen exactament el mateix cicle de vida:
llistar / crear / obtenir / actualitzar / esborrar, sempre filtrant per
`company_id`. Centralitzar-ho aqui evita repetir (i oblidar) el filtre de
tenant a cada router, que es la principal font d'IDOR en aquest tipus d'app.
"""

# NOTA: aquest modul NO pot usar `from __future__ import annotations`.
# FastAPI resol les anotacions en temps d'execucio contra els globals del modul,
# i aqui els tipus dels payloads son parametres locals de la fabrica.
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CompanyContext, CompanyCtx, SessionDep
from app.core.errors import ConflictError, NotFoundError
from app.models.enums import AuditAction, LinkedEntityType
from app.services import audit
from app.services.entities import delete_links_for_entity


async def get_owned_or_404(
    session: AsyncSession, model: Any, item_id: uuid.UUID, company_id: uuid.UUID
) -> Any:
    """Carrega una fila comprovant sempre la pertinenca al tenant."""
    instance = (
        await session.execute(
            sa.select(model).where(model.id == item_id, model.company_id == company_id)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise NotFoundError("Recurs no trobat.")
    return instance


def apply_changes(instance: Any, payload: BaseModel, actor_id: uuid.UUID | None) -> Any:
    """Aplica nomes els camps enviats explicitament (semantica PATCH)."""
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(instance, field, value)
    if hasattr(instance, "updated_by"):
        instance.updated_by = actor_id
    return instance


def build_crud_router(
    *,
    model: Any,
    create_schema: type[BaseModel],
    update_schema: type[BaseModel],
    read_schema: type[BaseModel],
    prefix: str,
    tag: str,
    resource_name: str,
    order_by: Sequence[Any] | None = None,
    entity_type: LinkedEntityType | None = None,
    conflict_message: str = "Ja existeix un registre equivalent.",
    pre_delete: Callable[[AsyncSession, Any], Awaitable[None]] | None = None,
    validate: Callable[[AsyncSession, Any, uuid.UUID], Awaitable[None]] | None = None,
) -> APIRouter:
    """`validate(session, instance, company_id)` s'executa abans de desar una alta o
    una modificacio, amb els canvis ja aplicats. Serveix per a regles que no es
    poden expressar a l'esquema (p. ex. "verificat nomes amb evidencia")."""
    router = APIRouter(prefix=prefix, tags=[tag])
    ordering = list(order_by) if order_by is not None else [model.created_at.desc()]

    @router.get("", response_model=list[read_schema], summary=f"Llista {resource_name}")
    async def list_items(session: SessionDep, ctx: CompanyCtx):
        rows = (
            await session.execute(
                sa.select(model).where(model.company_id == ctx.company_id).order_by(*ordering)
            )
        ).scalars().all()
        return list(rows)

    @router.post(
        "",
        response_model=read_schema,
        status_code=status.HTTP_201_CREATED,
        summary=f"Crea {resource_name}",
    )
    async def create_item(payload: create_schema, session: SessionDep, ctx: CompanyCtx):  # type: ignore[valid-type]
        instance = model(
            company_id=ctx.company_id,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            **payload.model_dump(exclude_unset=True),
        )
        session.add(instance)
        if validate is not None:
            await validate(session, instance, ctx.company_id)
        await _commit(session, conflict_message)
        await session.refresh(instance)
        await audit.record(
            session,
            action=AuditAction.CREATE,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            entity_type=model.__tablename__,
            entity_id=instance.id,
        )
        await session.commit()
        return instance

    @router.get("/{item_id}", response_model=read_schema, summary=f"Obte {resource_name}")
    async def get_item(item_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
        return await get_owned_or_404(session, model, item_id, ctx.company_id)

    @router.patch("/{item_id}", response_model=read_schema, summary=f"Actualitza {resource_name}")
    async def update_item(
        item_id: uuid.UUID,
        payload: update_schema,  # type: ignore[valid-type]
        session: SessionDep,
        ctx: CompanyCtx,
    ):
        instance = await get_owned_or_404(session, model, item_id, ctx.company_id)
        apply_changes(instance, payload, ctx.user_id)
        if validate is not None:
            await validate(session, instance, ctx.company_id)
        await _commit(session, conflict_message)
        await session.refresh(instance)
        await audit.record(
            session,
            action=AuditAction.UPDATE,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            entity_type=model.__tablename__,
            entity_id=instance.id,
        )
        await session.commit()
        return instance

    @router.delete(
        "/{item_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary=f"Esborra {resource_name}",
    )
    async def delete_item(item_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx) -> Response:
        instance = await get_owned_or_404(session, model, item_id, ctx.company_id)
        if pre_delete is not None:
            await pre_delete(session, instance)
        if entity_type is not None:
            # Els enllacos d'evidencia son polimorfics: no hi ha FK que els
            # esborri, els netegem explicitament.
            await delete_links_for_entity(session, entity_type, instance.id)
        await session.delete(instance)
        await audit.record(
            session,
            action=AuditAction.DELETE,
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            entity_type=model.__tablename__,
            entity_id=item_id,
        )
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


async def _commit(session: AsyncSession, conflict_message: str) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(conflict_message) from exc


__all__ = ["CompanyContext", "apply_changes", "build_crud_router", "get_owned_or_404"]
