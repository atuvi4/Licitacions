"""Autenticacio per sessio amb cookie httpOnly.

Decisio: JWT signat al backend, transportat en una cookie `HttpOnly` +
`SameSite=Lax` (i `Secure` fora de local). El frontend mai manipula el token,
per tant no es pot exfiltrar amb XSS des de JavaScript. No hi ha OAuth ni SSO
en aquesta iteracio.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.errors import AuthenticationError
from app.core.security import create_access_token, hash_password, needs_rehash, verify_password
from app.models.enums import AuditAction
from app.models.identity import User
from app.schemas.auth import LoginRequest, PasswordChangeRequest, UserRead
from app.schemas.common import Message
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash real d'una contrasenya aleatoria, usat quan l'email no existeix: iguala
# el cost de la verificacio i impedeix enumerar usuaris pel temps de resposta.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        path="/",
    )


@router.post("/login", response_model=UserRead, summary="Inicia sessio")
async def login(payload: LoginRequest, response: Response, session: SessionDep) -> User:
    email = payload.email.lower().strip()
    user = (
        await session.execute(sa.select(User).where(sa.func.lower(User.email) == email))
    ).scalar_one_or_none()

    password_ok = verify_password(payload.password, user.hashed_password if user else _DUMMY_HASH)

    if user is None or not password_ok or not user.is_active:
        await audit.record(
            session,
            action=AuditAction.LOGIN_FAILED,
            user_id=user.id if user else None,
            summary="Intent de login fallit",
        )
        await session.commit()
        # Missatge unic: no revelem si l'email existeix o si el compte esta inactiu.
        raise AuthenticationError("Credencials no valides.")

    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(payload.password)

    user.last_login_at = datetime.now(UTC)
    await audit.record(
        session, action=AuditAction.LOGIN, user_id=user.id, summary="Login correcte"
    )
    await session.commit()
    await session.refresh(user)

    _set_session_cookie(response, create_access_token(user.id))
    return user


@router.post("/logout", response_model=Message, summary="Tanca sessio")
async def logout(response: Response) -> Message:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
    )
    return Message(message="Sessio tancada.")


@router.get("/me", response_model=UserRead, summary="Usuari autenticat i les seves empreses")
async def me(user: CurrentUser) -> User:
    return user


@router.post(
    "/password",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    summary="Canvia la contrasenya",
)
async def change_password(
    payload: PasswordChangeRequest, user: CurrentUser, session: SessionDep, response: Response
) -> Message:
    if not verify_password(payload.current_password, user.hashed_password):
        raise AuthenticationError("La contrasenya actual no es correcta.")
    user.hashed_password = hash_password(payload.new_password)
    await audit.record(
        session, action=AuditAction.UPDATE, user_id=user.id, summary="Canvi de contrasenya"
    )
    await session.commit()
    # Es renova la sessio per invalidar el token anterior a la practica.
    _set_session_cookie(response, create_access_token(user.id))
    return Message(message="Contrasenya actualitzada.")
