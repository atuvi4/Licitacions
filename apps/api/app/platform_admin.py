"""Qui administra la plataforma (`users.is_superuser`): concedir, retirar, llistar.

    python -m app.platform_admin list
    python -m app.platform_admin grant <correu> --confirm <correu>
    python -m app.platform_admin revoke <correu> --confirm <correu>

Es l'unica manera de fer-ho: no hi ha cap endpoint que doni aquest rol, perque
qui el te pot donar d'alta empreses. `--confirm` ha de repetir el correu, per
no concedir-lo a qui no toca per un error de copiar i enganxar. No toca
contrasenyes ni pertinences; queda al registre d'auditoria.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuditAction
from app.models.identity import User
from app.services import audit


async def _find(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(
        sa.select(User).where(sa.func.lower(User.email) == email.strip().lower())
    )


async def set_platform_admin(session: AsyncSession, email: str, *, value: bool) -> User:
    user = await _find(session, email)
    if user is None:
        raise LookupError(f"No hi ha cap usuari {email}.")
    if user.is_superuser == value:
        return user
    user.is_superuser = value
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        entity_type="users",
        entity_id=user.id,
        summary=(
            "Administracio de plataforma concedida"
            if value
            else "Administracio de plataforma retirada"
        ),
    )
    await session.commit()
    return user


async def list_platform_admins(session: AsyncSession) -> list[User]:
    return list(
        (await session.scalars(sa.select(User).where(User.is_superuser).order_by(User.email))).all()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.platform_admin", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="llista qui administra la plataforma")
    for name in ("grant", "revoke"):
        command = commands.add_parser(name)
        command.add_argument("email")
        command.add_argument("--confirm", required=True, help="repeteix el correu")
    args = parser.parse_args(argv)
    if args.command != "list" and args.confirm.strip().lower() != args.email.strip().lower():
        print("--confirm no coincideix amb el correu: no s'ha fet res.", file=sys.stderr)
        return 2

    from app.db.session import SessionFactory, engine  # noqa: PLC0415

    async def run() -> int:
        try:
            async with SessionFactory() as session:
                if args.command == "list":
                    admins = await list_platform_admins(session)
                    for user in admins:
                        print(f"{user.email}\t{'actiu' if user.is_active else 'inactiu'}")
                    if not admins:
                        print("(cap)")
                    return 0
                try:
                    user = await set_platform_admin(
                        session, args.email, value=args.command == "grant"
                    )
                except LookupError as exc:
                    print(f"{exc} No s'ha fet res.", file=sys.stderr)
                    return 1
                state = "administra" if user.is_superuser else "ja no administra"
                print(f"{user.email} {state} la plataforma.")
                return 0
        finally:
            await engine.dispose()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
