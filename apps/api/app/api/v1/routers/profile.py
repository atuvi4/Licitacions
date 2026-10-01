"""Routers CRUD de serveis, territoris i exercicis economics."""

from app.api.v1.crud import build_crud_router
from app.knowledge import access
from app.models.enums import LinkedEntityType
from app.models.profile import CompanyFinancialYear, CompanyService, CompanyTerritory
from app.schemas.profile import (
    FinancialYearCreate,
    FinancialYearRead,
    FinancialYearUpdate,
    ServiceCreate,
    ServiceRead,
    ServiceUpdate,
    TerritoryCreate,
    TerritoryRead,
    TerritoryUpdate,
)
from app.services import knowledge_coverage
from app.services.knowledge_evidence import require_evidence_if_verified


async def _service_coverage(session, instance, extra) -> None:
    """La cobertura d'un servei: un canvi material la torna a pendent de verificar,
    llevat que la mateixa peticio demani `verified` (llavors mana l'evidencia)."""
    payload = extra.get("coverage")
    if payload is None:
        return
    asked = knowledge_coverage.asked_verified(instance)
    if await knowledge_coverage.apply_coverage(session, instance, payload):
        knowledge_coverage.unverify_for_coverage_change(instance, asked=asked)


async def _delete_service_coverage(session, instance) -> None:
    await knowledge_coverage.delete_coverage_for_entity(
        session, knowledge_coverage.ENTITY_TYPE_BY_MODEL[type(instance)], instance.id
    )


services_router = build_crud_router(
    model=CompanyService,
    create_schema=ServiceCreate,
    update_schema=ServiceUpdate,
    read_schema=ServiceRead,
    prefix="/services",
    tag="serveis",
    resource_name="servei",
    order_by=[CompanyService.name],
    entity_type=LinkedEntityType.COMPANY_SERVICE,
    conflict_message="Ja existeix un servei amb aquest nom.",
    # Verificat nomes amb evidencia enllacada (coneixement de l'empresa, fase 2).
    validate=require_evidence_if_verified,
    # Qui nomes consulta no modifica coneixement; eliminar, propietari o administracio.
    authorize_write=access.assert_can_edit,
    authorize_delete=access.assert_can_delete,
    # Cobertura territorial (0047): viu a `knowledge_coverage_areas`, no a la fila.
    side_fields=_service_coverage,
    side_field_names=frozenset({"coverage"}),
    pre_delete=_delete_service_coverage,
)

territories_router = build_crud_router(
    model=CompanyTerritory,
    create_schema=TerritoryCreate,
    update_schema=TerritoryUpdate,
    read_schema=TerritoryRead,
    prefix="/territories",
    tag="territoris",
    resource_name="territori",
    order_by=[CompanyTerritory.priority.desc(), CompanyTerritory.name],
    conflict_message="Ja existeix aquest territori per a l'empresa.",
)

financial_years_router = build_crud_router(
    model=CompanyFinancialYear,
    create_schema=FinancialYearCreate,
    update_schema=FinancialYearUpdate,
    read_schema=FinancialYearRead,
    prefix="/financial-years",
    tag="solvencia",
    resource_name="exercici",
    order_by=[CompanyFinancialYear.year.desc()],
    entity_type=LinkedEntityType.COMPANY_FINANCIAL_YEAR,
    conflict_message="Ja hi ha un exercici registrat per a aquest any.",
)
