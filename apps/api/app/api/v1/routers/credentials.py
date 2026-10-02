"""Routers CRUD de certificacions, registres i assegurances."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.crud import build_crud_router
from app.knowledge import access
from app.models.credentials import (
    CompanyCertification,
    CompanyInsurance,
    CompanyInsuranceLimit,
    CompanyRegistration,
)
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
from app.services.knowledge_evidence import (
    require_evidence_if_verified,
    unverify_for_dependent_change,
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
    # Verificada nomes amb evidencia enllacada (coneixement de l'empresa, fase 2).
    validate=require_evidence_if_verified,
    # Qui nomes consulta no modifica coneixement; eliminar, propietari o administracio.
    authorize_write=access.assert_can_edit,
    authorize_delete=access.assert_can_delete,
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
    # Coneixement actual (0052): verificat nomes amb evidencia; qui nomes consulta no
    # el modifica.
    validate=require_evidence_if_verified,
    authorize_write=access.assert_can_edit,
    authorize_delete=access.assert_can_delete,
)


async def _insurance_limits(
    session: AsyncSession, insurance: CompanyInsurance, extra: dict[str, Any]
) -> None:
    """El conjunt COMPLET de limits de la fitxa (pas 6), dins la mateixa transaccio que
    la polissa: el CRUD fa un sol commit despres d'aplicar-ho tot, i si res falla no en
    queda res. Es substitueix tipus per tipus (mai esborrar i tornar a inserir el mateix
    tipus: la unicitat polissa + tipus ho prohibiria). Un canvi al conjunt d'una polissa
    verificada la torna a pendent de verificar."""
    wanted = extra.get("limits")
    if wanted is None:
        return
    actor = insurance.updated_by
    by_kind = {limit.kind: limit for limit in insurance.limits}
    wanted_kinds = {item.kind for item in wanted}
    changed = False
    for kind, limit in by_kind.items():
        if kind not in wanted_kinds:
            insurance.limits.remove(limit)
            changed = True
    for item in wanted:
        currency = item.currency or insurance.currency or "EUR"
        limit = by_kind.get(item.kind)
        if limit is None:
            insurance.limits.append(
                CompanyInsuranceLimit(
                    company_id=insurance.company_id,
                    kind=item.kind,
                    amount=item.amount,
                    currency=currency,
                    notes=item.notes,
                    created_by=actor,
                    updated_by=actor,
                )
            )
            changed = True
        elif (limit.amount, limit.currency, limit.notes) != (item.amount, currency, item.notes):
            limit.amount, limit.currency, limit.notes = item.amount, currency, item.notes
            limit.updated_by = actor
            changed = True
    if changed:
        unverify_for_dependent_change(insurance)


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
    validate=require_evidence_if_verified,
    authorize_write=access.assert_can_edit,
    authorize_delete=access.assert_can_delete,
    # La fitxa sencera (polissa + conjunt de limits) en una sola transaccio.
    side_fields=_insurance_limits,
    side_field_names=frozenset({"limits"}),
)
