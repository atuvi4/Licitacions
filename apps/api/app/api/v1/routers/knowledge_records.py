"""Declaracio directa de coneixement actual (fase 2B del motor d'analisi).

Les certificacions i els serveis es declaren amb els seus CRUD. La resta (fets
operatius, solucions, capacitats, productes, tercers i els seus papers) entra
per `/companies/{company_id}/knowledge/records/{kind}`, sempre pel servei de
coneixement: neix `declared` i `pending_verification`, i nomes es verifica amb
evidencia enllacada (`/verify`).

Una ruta per tipus, amb el seu esquema, perque l'OpenAPI digui que demana cada
un; l'esborrat es una sola ruta amb el tipus a la URL.

Llegir, qualsevol membre. Declarar, editar i retirar, tothom menys qui nomes
consulta. Eliminar, propietari o administracio (`app/knowledge/access.py`).
"""

# NOTA: com `crud.py`, aquest modul NO pot usar `from __future__ import annotations`:
# FastAPI resol les anotacions contra els globals del modul i aqui els esquemes
# son parametres de la fabrica.
import uuid

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.api.deps import CompanyCtx, SessionDep
from app.knowledge import access
from app.models.enums import AuditAction
from app.schemas.knowledge import RECORD_SCHEMAS, KnowledgeRecordsRead
from app.services import audit
from app.services import company_knowledge as service

router = APIRouter(prefix="/knowledge/records", tags=["coneixement de l'empresa"])


@router.get("", response_model=KnowledgeRecordsRead, summary="Files declarables")
async def list_records(session: SessionDep, ctx: CompanyCtx):
    """Les files tal com son, per editar-les. L'estat efectiu el diu `/snapshot`."""
    return await service.list_records(session, ctx.company_id)


def _register(
    kind: str,
    declare_schema: type[BaseModel],
    update_schema: type[BaseModel],
    read_schema: type[BaseModel],
) -> None:
    label = kind.replace("_", " ")

    @router.post(
        f"/{kind}",
        response_model=read_schema,
        status_code=status.HTTP_201_CREATED,
        summary=f"Declara {label}",
    )
    async def declare(payload: declare_schema, session: SessionDep, ctx: CompanyCtx):  # type: ignore[valid-type]
        access.assert_can_edit(ctx.membership.role)
        record = await service.declare_record(
            session, company_id=ctx.company_id, kind=kind, payload=payload, user=ctx.user
        )
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.CREATE,
            entity_type=f"knowledge_{kind}",
            entity_id=record.id,
            summary="declarada",
        )
        await session.commit()
        await session.refresh(record)
        return record

    @router.patch(f"/{kind}/{{record_id}}", response_model=read_schema, summary=f"Modifica {label}")
    async def update(
        record_id: uuid.UUID,
        payload: update_schema,  # type: ignore[valid-type]
        session: SessionDep,
        ctx: CompanyCtx,
    ):
        access.assert_can_edit(ctx.membership.role)
        record, unverified = await service.update_record(
            session, company_id=ctx.company_id, kind=kind, record_id=record_id, payload=payload
        )
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.UPDATE,
            entity_type=f"knowledge_{kind}",
            entity_id=record.id,
            summary="modificada; torna a pendent de verificar" if unverified else "modificada",
        )
        await session.commit()
        await session.refresh(record)
        return record


for _kind, (_declare, _update, _read) in RECORD_SCHEMAS.items():
    _register(_kind, _declare, _update, _read)


@router.delete(
    "/{kind}/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Esborra una dada declarada",
)
async def delete(kind: str, record_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx) -> Response:
    access.assert_can_delete(ctx.membership.role)
    await service.delete_record(session, company_id=ctx.company_id, kind=kind, record_id=record_id)
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=AuditAction.DELETE,
        entity_type=f"knowledge_{kind}",
        entity_id=record_id,
        summary="esborrada",
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
