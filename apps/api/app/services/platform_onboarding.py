"""Alta d'una empresa client i del seu propietari, feta per l'administracio de la plataforma.

Es la primera peca de l'alta real: no hi ha registre public ni invitacions per
correu (no hi ha cap enviament de correus). Un administrador de plataforma
(`users.is_superuser`) crea, en una sola transaccio:

* l'empresa (nomes cal el nom legal);
* l'usuari propietari, actiu, amb una contrasenya temporal aleatoria i
  `must_change_password`: no pot fer res mes fins que la canvia;
* la pertinenca `owner`, per defecte.

La contrasenya temporal nomes es retorna a la resposta: no es desa en clar, no
va al registre d'auditoria ni a cap log.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.models.enums import AuditAction, MembershipRole
from app.models.identity import Company, CompanyMembership, User
from app.services import audit

TEMPORARY_PASSWORD_LENGTH = max(16, MIN_PASSWORD_LENGTH)
# Sense caracters que es confonen en llegir-los o dictar-los (0/O, 1/l/I).
_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


class OwnerEmailInUseError(ConflictError):
    code = "owner_email_in_use"


class TaxIdInUseError(ConflictError):
    code = "tax_id_in_use"


@dataclass(slots=True)
class OnboardedCompany:
    company: Company
    owner: User
    temporary_password: str


def temporary_password() -> str:
    """Aleatoria, llegible, i que passa la mateixa regla que un canvi de contrasenya."""
    while True:
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(TEMPORARY_PASSWORD_LENGTH))
        if any(c.isdigit() for c in candidate) and any(c.isalpha() for c in candidate):
            return candidate


def normalize_tax_id(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = "".join(value.split()).replace("-", "").upper()
    return cleaned or None


async def onboard_company(
    session: AsyncSession,
    *,
    admin: User,
    legal_name: str,
    trade_name: str | None,
    tax_id: str | None,
    owner_email: str,
    owner_full_name: str,
) -> OnboardedCompany:
    email = owner_email.strip().lower()
    tax = normalize_tax_id(tax_id)

    taken = await session.scalar(sa.select(User.id).where(sa.func.lower(User.email) == email))
    if taken is not None:
        raise OwnerEmailInUseError("Ja hi ha un usuari amb aquest correu.")
    if tax is not None:
        existing = await session.scalar(
            sa.select(Company.id).where(sa.func.upper(Company.tax_id) == tax)
        )
        if existing is not None:
            raise TaxIdInUseError("Ja hi ha una empresa amb aquest NIF.")

    password = temporary_password()
    owner = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=owner_full_name,
        is_active=True,
        must_change_password=True,
    )
    company = Company(legal_name=legal_name, trade_name=trade_name or None, tax_id=tax)
    session.add_all([owner, company])
    try:
        await session.flush()
    except IntegrityError:
        # Dues altes simultanies amb el mateix correu: la unicitat de la base mana.
        await session.rollback()
        raise OwnerEmailInUseError("Ja hi ha un usuari amb aquest correu.") from None
    session.add(
        CompanyMembership(
            user_id=owner.id, company_id=company.id, role=MembershipRole.OWNER, is_default=True
        )
    )
    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=company.id,
        user_id=admin.id,
        entity_type="companies",
        entity_id=company.id,
        summary="Empresa donada d'alta per la plataforma",
    )
    await audit.record(
        session,
        action=AuditAction.CREATE,
        company_id=company.id,
        user_id=admin.id,
        entity_type="users",
        entity_id=owner.id,
        summary="Propietari donat d'alta amb contrasenya temporal",
    )
    await session.commit()
    await session.refresh(company)
    await session.refresh(owner)
    return OnboardedCompany(company=company, owner=owner, temporary_password=password)
