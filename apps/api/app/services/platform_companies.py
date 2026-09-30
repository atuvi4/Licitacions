"""Consola de l'Administracio Tuvi: llistat d'empreses clients, fitxa i estat.

Tot de nomes lectura excepte el canvi d'estat. La "vista de seguiment" de
l'espai del client son RESUMS (recomptes, les ultimes candidatures i
l'activitat registrada): mai documents, mai edicio, i no passa per les rutes
d'empresa, que continuen tancades per a l'Administracio Tuvi.

Els agregats es fan per a totes les empreses en poques consultes agrupades: el
nombre de clients d'un pilot cap a la pagina.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models.audit import AuditLog
from app.models.enums import AuditAction, CompanyPlatformStatus, MembershipRole, WorkspaceStatus
from app.models.identity import Company, CompanyMembership, User
from app.models.tenders import Buyer, Tender
from app.models.workspace import BidWorkspace
from app.services import audit
from app.services.platform_onboarding import pilot_end, today

#: Fases de candidatura tal com les mostra el seguiment. «Adjudicada» no existeix
#: a les dades: no es compta ni s'inventa.
CLOSED = frozenset({WorkspaceStatus.NO_GO_APPROVED, WorkspaceStatus.WITHDRAWN})
PREPARING = frozenset({
    WorkspaceStatus.GO_APPROVED,
    WorkspaceStatus.PREPARING_BID,
    WorkspaceStatus.READY_FOR_FINAL_REVIEW,
    WorkspaceStatus.APPROVED_FOR_SUBMISSION,
})
#: Nomes per classificar (lectura). L'estat de presentada el governa `transitions.py`;
#: per aixo es pren pel valor i no pel nom.
PRESENTED = frozenset({WorkspaceStatus("submitted")})

RECENT_LIMIT = 5
STATUS_LABELS = {
    CompanyPlatformStatus.PILOT: "pilot",
    CompanyPlatformStatus.ACTIVE: "activa",
    CompanyPlatformStatus.PAUSED: "pausada",
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _latest(*values: datetime | None) -> datetime | None:
    present = [v for v in (_utc(v) for v in values) if v is not None]
    return max(present) if present else None


def _person(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {"id": user.id, "full_name": user.full_name, "email": user.email}


@dataclass(slots=True)
class _Aggregates:
    owners: dict[uuid.UUID, dict[str, Any]]
    users: dict[uuid.UUID, int]
    tenders: dict[uuid.UUID, int]
    last_audit: dict[uuid.UUID, datetime]
    last_login: dict[uuid.UUID, datetime]


async def _aggregates(session: AsyncSession, company_ids: list[uuid.UUID]) -> _Aggregates:
    if not company_ids:
        return _Aggregates({}, {}, {}, {}, {})
    owners: dict[uuid.UUID, dict[str, Any]] = {}
    rows = await session.execute(
        sa.select(CompanyMembership.company_id, CompanyMembership.created_at, User)
        .join(User, User.id == CompanyMembership.user_id)
        .where(
            CompanyMembership.company_id.in_(company_ids),
            CompanyMembership.role == MembershipRole.OWNER,
        )
        .order_by(CompanyMembership.created_at)
    )
    for company_id, assigned_at, user in rows:
        # El primer propietari assignat: el «propietari inicial».
        owners.setdefault(
            company_id,
            {**_person(user), "assigned_at": assigned_at, "last_login_at": user.last_login_at},
        )

    users = dict((await session.execute(
        sa.select(CompanyMembership.company_id, sa.func.count())
        .where(CompanyMembership.company_id.in_(company_ids))
        .group_by(CompanyMembership.company_id)
    )).all())
    tenders = dict((await session.execute(
        sa.select(BidWorkspace.company_id, sa.func.count())
        .where(BidWorkspace.company_id.in_(company_ids))
        .group_by(BidWorkspace.company_id)
    )).all())
    last_audit = dict((await session.execute(
        sa.select(AuditLog.company_id, sa.func.max(AuditLog.created_at))
        .where(AuditLog.company_id.in_(company_ids))
        .group_by(AuditLog.company_id)
    )).all())
    last_login = dict((await session.execute(
        sa.select(CompanyMembership.company_id, sa.func.max(User.last_login_at))
        .join(User, User.id == CompanyMembership.user_id)
        .where(CompanyMembership.company_id.in_(company_ids))
        .group_by(CompanyMembership.company_id)
    )).all())
    return _Aggregates(owners, users, tenders, last_audit, last_login)


def _matches(row: dict[str, Any], query: str) -> bool:
    owner = row["owner"] or {}
    haystack = " ".join(
        str(value or "")
        for value in (row["legal_name"], row["trade_name"], row["tax_id"],
                      owner.get("full_name"), owner.get("email"))
    ).lower()
    return query in haystack


async def list_companies(
    session: AsyncSession,
    *,
    status: CompanyPlatformStatus | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    companies = (await session.scalars(sa.select(Company))).all()
    counts = {"all": len(companies), "pilot": 0, "active": 0, "paused": 0}
    for company in companies:
        counts[company.platform_status.value] += 1

    agg = await _aggregates(session, [c.id for c in companies])
    rows = []
    for company in companies:
        if status is not None and company.platform_status != status:
            continue
        rows.append({
            "id": company.id,
            "legal_name": company.legal_name,
            "trade_name": company.trade_name,
            "tax_id": company.tax_id,
            "platform_status": company.platform_status,
            "pilot_ends_at": company.pilot_ends_at,
            "owner": agg.owners.get(company.id),
            "users_count": agg.users.get(company.id, 0),
            "tenders_count": agg.tenders.get(company.id, 0),
            "last_activity_at": _latest(
                agg.last_audit.get(company.id), agg.last_login.get(company.id)
            ),
            "created_at": company.created_at,
        })
    needle = (query or "").strip().lower()
    if needle:
        rows = [row for row in rows if _matches(row, needle)]
    # Primer les que s'han mogut fa menys; les que mai han tingut activitat, al final.
    epoch = datetime.min.replace(tzinfo=UTC)
    rows.sort(
        key=lambda row: (row["last_activity_at"] or epoch, _utc(row["created_at"])), reverse=True
    )
    return {"items": rows, "counts": counts}


async def _load(session: AsyncSession, company_id: uuid.UUID) -> Company:
    company = await session.get(Company, company_id)
    if company is None:
        raise NotFoundError("Empresa no trobada.")
    return company


async def get_company(session: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    company = await _load(session, company_id)
    agg = await _aggregates(session, [company.id])
    onboarded_by = await session.get(User, company.onboarded_by) if company.onboarded_by else None

    workspaces = (await session.execute(
        sa.select(BidWorkspace.status, BidWorkspace.deadline_at, BidWorkspace.updated_at,
                  Tender.title, Tender.deadline_at, Buyer.name)
        .join(Tender, Tender.id == BidWorkspace.tender_id)
        .outerjoin(Buyer, Buyer.id == Tender.buyer_id)
        .where(BidWorkspace.company_id == company.id)
        .order_by(BidWorkspace.updated_at.desc())
    )).all()
    statuses = [row[0] for row in workspaces]
    recent_tenders = [
        {
            "title": title,
            "buyer_name": buyer,
            "status": ws_status,
            "deadline_at": ws_deadline or t_deadline,
        }
        for ws_status, ws_deadline, _updated, title, t_deadline, buyer in workspaces[:RECENT_LIMIT]
    ]

    activity_rows = (await session.execute(
        sa.select(AuditLog, User)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(AuditLog.company_id == company.id)
        .order_by(AuditLog.created_at.desc())
        .limit(RECENT_LIMIT)
    )).all()
    activity = [
        {
            "at": entry.created_at,
            "action": entry.action,
            "entity_type": entry.entity_type,
            "summary": entry.summary,
            "actor": _person(actor),
            "by_platform": bool(actor and actor.is_superuser),
        }
        for entry, actor in activity_rows
    ]

    since = datetime.now(UTC) - timedelta(days=7)
    members = (await session.scalars(
        sa.select(User).join(CompanyMembership, CompanyMembership.user_id == User.id)
        .where(CompanyMembership.company_id == company.id)
    )).all()
    active_recently = sum(1 for user in members if (_utc(user.last_login_at) or since) > since)

    return {
        "id": company.id,
        "legal_name": company.legal_name,
        "trade_name": company.trade_name,
        "tax_id": company.tax_id,
        "created_at": company.created_at,
        "onboarded_by": _person(onboarded_by),
        "status": {
            "platform_status": company.platform_status,
            "pilot_started_at": company.pilot_started_at,
            "pilot_ends_at": company.pilot_ends_at,
            "status_changed_at": company.status_changed_at,
        },
        "owner": agg.owners.get(company.id),
        "client_space": {
            "tenders": {
                "total": len(statuses),
                "active": sum(1 for s in statuses if s not in CLOSED),
                "preparing": sum(1 for s in statuses if s in PREPARING),
                "submitted": sum(1 for s in statuses if s in PRESENTED),
            },
            "recent_tenders": recent_tenders,
            "recent_activity": activity,
            "users": {"total": len(members), "active_last_7_days": active_recently},
        },
    }


async def change_status(
    session: AsyncSession,
    *,
    admin: User,
    company_id: uuid.UUID,
    status: CompanyPlatformStatus,
    pilot_ends_at: date | None,
) -> dict[str, Any]:
    """Pilot, activa o pausada. Allargar un pilot es pilot -> pilot amb una fi nova.

    `paused` encara no bloqueja l'acces del client: es registra i s'audita.
    """
    company = await _load(session, company_id)
    previous = company.platform_status

    if status == CompanyPlatformStatus.PILOT:
        if previous == CompanyPlatformStatus.PILOT:
            if pilot_ends_at is None:
                raise ValidationError("Per allargar el pilot cal la data de fi nova.")
            starts = company.pilot_started_at or today()
        else:
            starts = today()
        ends = pilot_end(pilot_ends_at, starts=starts)
        if previous == CompanyPlatformStatus.PILOT and ends == company.pilot_ends_at:
            return await get_company(session, company.id)
        company.pilot_started_at = starts
        company.pilot_ends_at = ends
    else:
        if pilot_ends_at is not None:
            raise ValidationError("La fi del pilot nomes s'indica per a un pilot.")
        if previous == status:
            return await get_company(session, company.id)

    company.platform_status = status
    company.status_changed_at = datetime.now(UTC)
    summary = f"Estat del client: {STATUS_LABELS[previous]} -> {STATUS_LABELS[status]}"
    if status == CompanyPlatformStatus.PILOT:
        summary += f" (fi del pilot {company.pilot_ends_at.isoformat()})"
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        company_id=company.id,
        user_id=admin.id,
        entity_type="companies",
        entity_id=company.id,
        summary=summary,
    )
    await session.commit()
    return await get_company(session, company.id)
