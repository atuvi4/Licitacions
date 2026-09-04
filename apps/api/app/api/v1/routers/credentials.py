"""Routers CRUD de certificacions, registres i assegurances."""

from app.api.v1.crud import build_crud_router
from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.enums import LinkedEntityType
from app.schemas.credentials import (
    CertificationCreate,
    CertificationRead,
    CertificationUpdate,
    InsuranceCreate,
    InsuranceRead,
    InsuranceUpdate,
    RegistrationCreate,
    RegistrationRead,
    RegistrationUpdate,
)

certifications_router = build_crud_router(
    model=CompanyCertification,
    create_schema=CertificationCreate,
    update_schema=CertificationUpdate,
    read_schema=CertificationRead,
    prefix="/certifications",
    tag="certificacions",
    resource_name="certificacio",
    order_by=[CompanyCertification.name],
    entity_type=LinkedEntityType.COMPANY_CERTIFICATION,
)

registrations_router = build_crud_router(
    model=CompanyRegistration,
    create_schema=RegistrationCreate,
    update_schema=RegistrationUpdate,
    read_schema=RegistrationRead,
    prefix="/registrations",
    tag="registres",
    resource_name="registre",
    order_by=[CompanyRegistration.name],
    entity_type=LinkedEntityType.COMPANY_REGISTRATION,
)

insurances_router = build_crud_router(
    model=CompanyInsurance,
    create_schema=InsuranceCreate,
    update_schema=InsuranceUpdate,
    read_schema=InsuranceRead,
    prefix="/insurances",
    tag="assegurances",
    resource_name="assegurança",
    order_by=[CompanyInsurance.expires_at.desc()],
    entity_type=LinkedEntityType.COMPANY_INSURANCE,
)
