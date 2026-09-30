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
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.models.enums import AuditAction, CompanyPlatformStatus, MembershipRole
from app.models.identity import Company, CompanyMembership, User
from app.services import audit

TEMPORARY_PASSWORD_LENGTH = max(16, MIN_PASSWORD_LENGTH)
#: Durada per defecte d'un pilot nou.
DEFAULT_PILOT_DAYS = 60
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


def today() -> date:
    return datetime.now(UTC).date()


def pilot_end(requested: date | None, *, starts: date) -> date:
    """La fi d'un pilot: la demanada o 60 dies; mai abans d'avui ni de l'inici."""
    end = requested or starts + timedelta(days=DEFAULT_PILOT_DAYS)
    if end < today() or end < starts:
        raise ValidationError("La fi del pilot no pot ser anterior a avui.")
    return end


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
    initial_status: CompanyPlatformStatus = CompanyPlatformStatus.PILOT,
    pilot_ends_at: date | None = None,
) -> OnboardedCompany:
    email = owner_email.strip().lower()
    tax = normalize_tax_id(tax_id)
    if tax is None:
        raise ValidationError("Cal el NIF de l'empresa.")
    if initial_status == CompanyPlatformStatus.PAUSED:
        raise ValidationError("Una empresa nova comenca en pilot o activa.")
    if initial_status != CompanyPlatformStatus.PILOT and pilot_ends_at is not None:
        raise ValidationError("La fi del pilot nomes s'indica per a un pilot.")
    starts = today()
    is_pilot = initial_status == CompanyPlatformStatus.PILOT
    ends = pilot_end(pilot_ends_at, starts=starts) if is_pilot else None

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
    company = Company(
        legal_name=legal_name,
        trade_name=trade_name or None,
        tax_id=tax,
        platform_status=initial_status,
        pilot_started_at=starts if ends else None,
        pilot_ends_at=ends,
        status_changed_at=datetime.now(UTC),
        onboarded_by=admin.id,
    )
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
        summary=(
            f"Empresa donada d'alta en pilot fins al {ends.isoformat()}"
            if ends
            else "Empresa donada d'alta com a activa"
        ),
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
