"""Registre d'entitats enllacables amb documents.

Aquest modul es el que compensa la falta de FK dels enllacos polimorfics:
abans de crear un `DocumentLink` es comprova aqui que l'entitat existeix i
pertany a l'empresa correcta.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.documents import DocumentLink
from app.models.enums import LinkedEntityType
from app.models.identity import Company
from app.models.people import CompanyPerson, PersonQualification
from app.models.profile import CompanyFinancialYear, CompanyService
from app.models.projects import CompanyProject

# Entitats que tenen `company_id` directament.
_DIRECT_MODELS = {
    LinkedEntityType.COMPANY_SERVICE: CompanyService,
    LinkedEntityType.COMPANY_FINANCIAL_YEAR: CompanyFinancialYear,
    LinkedEntityType.COMPANY_CERTIFICATION: CompanyCertification,
    LinkedEntityType.COMPANY_REGISTRATION: CompanyRegistration,
    LinkedEntityType.COMPANY_INSURANCE: CompanyInsurance,
    LinkedEntityType.COMPANY_PERSON: CompanyPerson,
    LinkedEntityType.COMPANY_PROJECT: CompanyProject,
}


async def assert_entity_in_company(
    session: AsyncSession,
    entity_type: LinkedEntityType,
    entity_id: uuid.UUID,
    company_id: uuid.UUID,
) -> None:
    """Llanca NotFoundError si l'entitat no existeix o es d'una altra empresa.

    Es retorna 404 (i no 403) deliberadament: no revelem l'existencia de
    recursos d'altres tenants.
    """
    if entity_type == LinkedEntityType.COMPANY:
        stmt = sa.select(Company.id).where(Company.id == entity_id, Company.id == company_id)
    elif entity_type == LinkedEntityType.PERSON_QUALIFICATION:
        stmt = (
            sa.select(PersonQualification.id)
            .join(CompanyPerson, CompanyPerson.id == PersonQualification.person_id)
            .where(PersonQualification.id == entity_id, CompanyPerson.company_id == company_id)
        )
    else:
        model = _DIRECT_MODELS.get(entity_type)
        if model is None:  # pragma: no cover - enum tancat
            raise NotFoundError("Tipus d'entitat no suportat.")
        stmt = sa.select(model.id).where(model.id == entity_id, model.company_id == company_id)

    found = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if found is None:
        raise NotFoundError("L'entitat a enllacar no existeix en aquesta empresa.")


async def delete_links_for_entity(
    session: AsyncSession,
    entity_type: LinkedEntityType,
    entity_id: uuid.UUID,
) -> None:
    """Neteja els enllacos quan s'esborra l'entitat (no hi ha ON DELETE CASCADE)."""
    await session.execute(
        sa.delete(DocumentLink).where(
            DocumentLink.entity_type == entity_type,
            DocumentLink.entity_id == entity_id,
        )
    )


async def delete_links_for_person_qualifications(
    session: AsyncSession, person_id: uuid.UUID
) -> None:
    """Cas especial: en esborrar una persona cauen les seves titulacions."""
    qualification_ids = (
        await session.execute(
            sa.select(PersonQualification.id).where(PersonQualification.person_id == person_id)
        )
    ).scalars().all()
    if qualification_ids:
        await session.execute(
            sa.delete(DocumentLink).where(
                DocumentLink.entity_type == LinkedEntityType.PERSON_QUALIFICATION,
                DocumentLink.entity_id.in_(qualification_ids),
            )
        )
