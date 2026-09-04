"""Agregacions per al dashboard.

Nomes compta i llegeix dades reals. No inventa cap metrica.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.cpv import CompanyCPVPreference
from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.documents import Document, DocumentLink
from app.models.identity import Company
from app.models.people import CompanyPerson, PersonQualification
from app.models.profile import CompanyFinancialYear, CompanyService, CompanyTerritory
from app.models.projects import CompanyProject
from app.schemas.common import compute_temporal_status
from app.schemas.dashboard import (
    CompletenessSection,
    DashboardSummary,
    EntityCounts,
    ExpiryAlert,
    ProfileCompleteness,
)
from app.services.completeness import compute_profile_completeness


async def _count(session: AsyncSession, model, company_id: uuid.UUID) -> int:
    stmt = sa.select(sa.func.count()).select_from(model).where(model.company_id == company_id)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def build_summary(
    session: AsyncSession, company: Company, *, alert_window_days: int | None = None
) -> DashboardSummary:
    company_id = company.id
    window = alert_window_days or settings.EXPIRY_WARNING_DAYS

    score, sections = await compute_profile_completeness(session, company)

    people_count = await _count(session, CompanyPerson, company_id)
    qualifications_count = int(
        (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(PersonQualification)
                .join(CompanyPerson, CompanyPerson.id == PersonQualification.person_id)
                .where(CompanyPerson.company_id == company_id)
            )
        ).scalar_one()
        or 0
    )
    counts = EntityCounts(
        services=await _count(session, CompanyService, company_id),
        territories=await _count(session, CompanyTerritory, company_id),
        financial_years=await _count(session, CompanyFinancialYear, company_id),
        certifications=await _count(session, CompanyCertification, company_id),
        registrations=await _count(session, CompanyRegistration, company_id),
        insurances=await _count(session, CompanyInsurance, company_id),
        people=people_count,
        qualifications=qualifications_count,
        cpv_preferences=await _count(session, CompanyCPVPreference, company_id),
        projects=await _count(session, CompanyProject, company_id),
        documents=await _count(session, Document, company_id),
        document_links=await _count(session, DocumentLink, company_id),
    )

    return DashboardSummary(
        company=company,  # type: ignore[arg-type]
        completeness=ProfileCompleteness(
            score=score,
            sections=[
                CompletenessSection(
                    key=section.key,
                    label=section.label,
                    weight=section.weight,
                    ratio=round(section.ratio, 4),
                    points=section.points,
                    status=section.status,
                    detail=section.detail,
                    requires_evidence=section.requires_evidence,
                )
                for section in sections
            ],
        ),
        counts=counts,
        expiry_alerts=await collect_expiry_alerts(session, company_id, window_days=window),
    )


async def collect_expiry_alerts(
    session: AsyncSession, company_id: uuid.UUID, *, window_days: int = 60
) -> list[ExpiryAlert]:
    """Acreditacions caducades o a punt de caducar dins la finestra indicada."""
    today = date.today()
    limit = today + timedelta(days=window_days)
    alerts: list[ExpiryAlert] = []

    def add(entity_type: str, entity_id: uuid.UUID, label: str, expires_at: date) -> None:
        alerts.append(
            ExpiryAlert(
                entity_type=entity_type,
                entity_id=entity_id,
                label=label,
                expires_at=expires_at,
                days_left=(expires_at - today).days,
                temporal_status=compute_temporal_status(expires_at),
            )
        )

    cert_rows = (
        await session.execute(
            sa.select(CompanyCertification.id, CompanyCertification.name, CompanyCertification.expires_at)
            .where(
                CompanyCertification.company_id == company_id,
                CompanyCertification.expires_at.is_not(None),
                CompanyCertification.expires_at <= limit,
            )
            .order_by(CompanyCertification.expires_at)
        )
    ).all()
    for row in cert_rows:
        add("company_certification", row.id, row.name, row.expires_at)

    insurance_rows = (
        await session.execute(
            sa.select(
                CompanyInsurance.id,
                CompanyInsurance.type,
                CompanyInsurance.provider,
                CompanyInsurance.expires_at,
            )
            .where(
                CompanyInsurance.company_id == company_id,
                CompanyInsurance.expires_at.is_not(None),
                CompanyInsurance.expires_at <= limit,
            )
            .order_by(CompanyInsurance.expires_at)
        )
    ).all()
    for row in insurance_rows:
        label = f"{row.type.value if hasattr(row.type, 'value') else row.type}"
        if row.provider:
            label = f"{label} - {row.provider}"
        add("company_insurance", row.id, label, row.expires_at)

    registration_rows = (
        await session.execute(
            sa.select(CompanyRegistration.id, CompanyRegistration.name, CompanyRegistration.expires_at)
            .where(
                CompanyRegistration.company_id == company_id,
                CompanyRegistration.expires_at.is_not(None),
                CompanyRegistration.expires_at <= limit,
            )
            .order_by(CompanyRegistration.expires_at)
        )
    ).all()
    for row in registration_rows:
        add("company_registration", row.id, row.name, row.expires_at)

    qualification_rows = (
        await session.execute(
            sa.select(
                PersonQualification.id,
                PersonQualification.name,
                PersonQualification.expires_at,
                CompanyPerson.first_name,
                CompanyPerson.last_name,
            )
            .join(CompanyPerson, CompanyPerson.id == PersonQualification.person_id)
            .where(
                CompanyPerson.company_id == company_id,
                PersonQualification.expires_at.is_not(None),
                PersonQualification.expires_at <= limit,
            )
            .order_by(PersonQualification.expires_at)
        )
    ).all()
    for row in qualification_rows:
        person = " ".join(part for part in (row.first_name, row.last_name) if part)
        add("person_qualification", row.id, f"{row.name} ({person})", row.expires_at)

    document_rows = (
        await session.execute(
            sa.select(Document.id, Document.name, Document.expires_at)
            .where(
                Document.company_id == company_id,
                Document.expires_at.is_not(None),
                Document.expires_at <= limit,
            )
            .order_by(Document.expires_at)
        )
    ).all()
    for row in document_rows:
        add("document", row.id, row.name, row.expires_at)

    alerts.sort(key=lambda alert: alert.expires_at)
    return alerts
