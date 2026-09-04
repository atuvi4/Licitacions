"""Dependencies compartides: sessio, usuari autenticat i acces per empresa.

Aqui viu el control multi-tenant: cap endpoint de negoci pot accedir a dades
sense passar per `get_company_context`, que exigeix una CompanyMembership.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AuthenticationError, NotFoundError
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models.identity import Company, CompanyMembership, User

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(settings.SESSION_COOKIE_NAME)


async def get_current_user(request: Request, session: SessionDep) -> User:
    token = _extract_token(request)
    if not token:
        raise AuthenticationError("Cal iniciar sessio.")
    payload = decode_access_token(token)
    if not payload:
        raise AuthenticationError("Sessio no valida o caducada.")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise AuthenticationError("Sessio no valida.") from None

    user = (
        await session.execute(sa.select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("Sessio no valida.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass(slots=True)
class CompanyContext:
    """Tenant resolt per a la peticio actual."""

    user: User
    company: Company
    membership: CompanyMembership

    @property
    def company_id(self) -> uuid.UUID:
        return self.company.id

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


async def get_company_context(
    session: SessionDep,
    user: CurrentUser,
    company_id: Annotated[uuid.UUID, Path()],
) -> CompanyContext:
    """Resol l'empresa i comprova que l'usuari hi pertany.

    Si no hi pertany es retorna 404 i no 403: no volem confirmar l'existencia
    d'empreses d'altres tenants (prevencio d'IDOR i enumeracio).
    """
    row = (
        await session.execute(
            sa.select(Company, CompanyMembership)
            .join(CompanyMembership, CompanyMembership.company_id == Company.id)
            .where(Company.id == company_id, CompanyMembership.user_id == user.id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Empresa no trobada.")
    company, membership = row
    return CompanyContext(user=user, company=company, membership=membership)


CompanyCtx = Annotated[CompanyContext, Depends(get_company_context)]
