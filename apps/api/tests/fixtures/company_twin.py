"""Besso SINTETIC del coneixement actual d'una empresa pilot, per als tests de 0051.

No es cap dada real: es la forma que l'anunci de l'Escala necessita per provar el
motor (un fet de FTTH propia amb cobertura completa en tres municipis que NO son
el de la licitacio, un servei d'accessos FTTH en revenda amb quatre majoristes,
una centraleta declarada sense capacitats, un ENS mitja declarat amb evidencia
pendent de verificar). Cap cost, marge, tarifa ni dada verificada.

**Qualificacio i mitjans** (0052, `qualification=True`): tres exercicis, dues
referencies d'execucio completades (una plurianual amb desglossament anual), una
RC amb limits suficients per al plec, un
registre d'operadors amb abast PARCIAL (dues de les tres linies), quatre persones
amb perfils (anonimes: "Persona A"...), un OTDR i un OLTS calibrats, una
fusionadora i 20 IPv4 assignables. Tot declarat, sense evidencia (els tests
n'enllacen quan cal). Imports rodons inventats; anys relatius a avui.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.knowledge.coverage import AreaIn, CoverageIn
from app.models.credentials import (
    CompanyCertification,
    CompanyInsurance,
    CompanyInsuranceLimit,
    CompanyRegistration,
)
from app.models.enums import (
    AreaKind,
    AvailabilityStatus,
    ClientType,
    CoverageCompleteness,
    DeliveryMode,
    EquipmentAccessMode,
    EquipmentType,
    InsuranceLimitKind,
    InsuranceType,
    OperatingFactKey,
    PartnerRole,
    ProjectStatus,
    RegistrationType,
)
from app.models.knowledge import (
    CompanyOperatingFact,
    CompanyPartner,
    CompanySolution,
    PartnerRoleRow,
)
from app.models.people import CompanyPerson
from app.models.profile import CompanyFinancialYear, CompanyService
from app.models.projects import CompanyProject, CompanyProjectAnnualAmount
from app.models.resources import CompanyEquipment
from app.services import knowledge_coverage

#: Cobertura FTTH propia: tres municipis que no son l'Escala.
OWN_FTTH_COVERAGE = CoverageIn(
    completeness=CoverageCompleteness.COMPLETE,
    areas=(
        AreaIn(AreaKind.MUNICIPALITY, "Banyoles", "17015", "INE"),
        AreaIn(AreaKind.MUNICIPALITY, "Mont-ras", "17110", "INE"),
        AreaIn(AreaKind.MUNICIPALITY, "Santa Cristina d'Aro", "17181", "INE"),
    ),
)
FTTH_WHOLESALERS = ("Adamo", "Sewan", "Vera", "Cablemóvil")
SERVICES: tuple[tuple[str, str, DeliveryMode | None], ...] = (
    ("Centraleta cloud i veu IP", "voice_pbx", DeliveryMode.OWN),
    ("Troncals SIP i numeracio", "voice_trunk", DeliveryMode.OWN),
    ("Accessos FTTH", "access_ftth", DeliveryMode.RESALE),
    ("Xarxa dedicada entre seus", "dedicated_network", DeliveryMode.OWN),
    ("Radioenllacos", "radio_link", DeliveryMode.OWN),
    ("Electronica de xarxa", "network_switching", DeliveryMode.OWN),
    ("Desplegament i certificacio de fibra", "fiber_deployment", DeliveryMode.OWN),
    ("Obra civil", "civil_works", DeliveryMode.PARTNER),
    ("Suport, operacio i SLA", "support_ops", DeliveryMode.OWN),
)


async def build_company_twin(db, company, *, qualification: bool = True) -> dict[str, Any]:
    """Crea el coneixement actual del besso. Retorna les files per id."""
    rows: dict[str, Any] = {"facts": {}, "services": {}, "partners": {}, "roles": {}}

    own_ftth = CompanyOperatingFact(
        company_id=company.id, key=OperatingFactKey.OWN_FTTH_INFRASTRUCTURE, value=True
    )
    cnmc = CompanyOperatingFact(
        company_id=company.id, key=OperatingFactKey.TELECOM_OPERATOR_CNMC, value=True,
        detail="serveis registrats a confirmar",
    )  # fmt: skip
    nrn = CompanyOperatingFact(
        company_id=company.id, key=OperatingFactKey.OWN_NUMBER_RANGE_NRN, value=True
    )
    tier3 = CompanyOperatingFact(
        company_id=company.id, key=OperatingFactKey.DATACENTER_TIER3, value=True
    )
    support = CompanyOperatingFact(
        company_id=company.id, key=OperatingFactKey.SUPPORT_24X7, value=True
    )
    db.add_all([own_ftth, cnmc, nrn, tier3, support])
    await db.flush()
    await knowledge_coverage.apply_coverage(db, own_ftth, OWN_FTTH_COVERAGE)
    await db.flush()
    for fact in (own_ftth, cnmc, nrn, tier3, support):
        rows["facts"][str(fact.key)] = fact

    for name, line, mode in SERVICES:
        service = CompanyService(
            company_id=company.id,
            name=name,
            service_line_code=line,
            delivery_mode=mode,
            active=True,
        )
        db.add(service)
        rows["services"][line] = service
    await db.flush()

    for name in FTTH_WHOLESALERS:
        partner = CompanyPartner(company_id=company.id, name=name)
        db.add(partner)
        await db.flush()
        role = PartnerRoleRow(
            company_id=company.id,
            partner_id=partner.id,
            role=PartnerRole.WHOLESALE_OPERATOR,
            service_line_code="access_ftth",
        )
        db.add(role)
        rows["partners"][name] = partner
        rows["roles"][name] = role
    cpd = CompanyPartner(company_id=company.id, name="Proveidor de CPD")
    civil = CompanyPartner(company_id=company.id, name="Soci d'obra civil")
    db.add_all([cpd, civil])
    await db.flush()
    db.add(PartnerRoleRow(company_id=company.id, partner_id=cpd.id, role=PartnerRole.SUPPLIER))
    civil_role = PartnerRoleRow(
        company_id=company.id,
        partner_id=civil.id,
        role=PartnerRole.SUBCONTRACTOR,
        service_line_code="civil_works",
        territory="Una altra provincia",
    )
    db.add(civil_role)
    rows["roles"]["civil_works"] = civil_role

    pbx = CompanySolution(
        company_id=company.id, name="3CX", service_line_code="voice_pbx", vendor="3CX"
    )
    db.add(pbx)
    rows["solution"] = pbx

    ens = CompanyCertification(
        company_id=company.id,
        name="ENS categoria Mitjana",
        standard_code="ENS",
        level="medium",
        scope_service_lines=["voice_pbx", "voice_trunk", "access_ftth", "dedicated_network",
                             "radio_link", "network_switching", "support_ops"],
    )  # fmt: skip
    db.add(ens)
    await db.flush()
    rows["certification"] = ens
    if qualification:
        rows["qualification"] = await build_qualification_twin(db, company, rows)
    return rows


#: Volum de negoci dels tres darrers exercicis tancats (antic -> recent). Inventat.
TURNOVER = (Decimal("80000"), Decimal("120000"), Decimal("150000"))
#: Perfils de les quatre persones (anonimes). Una cobreix dos perfils.
PEOPLE = (
    ("Persona A", ["project_manager", "network_security_specialist"]),
    ("Persona B", ["voip_specialist"]),
    ("Persona C", ["fiber_certification_specialist"]),
    ("Persona D", ["field_technician"]),
)


async def build_qualification_twin(db, company, rows: dict[str, Any]) -> dict[str, Any]:
    """Qualificacio i mitjans del besso (0052). Tot declarat i pendent de verificar."""
    today = date.today()
    out: dict[str, Any] = {"years": {}, "people": {}, "equipment": {}}
    for offset, revenue in zip((3, 2, 1), TURNOVER, strict=True):
        year = CompanyFinancialYear(
            company_id=company.id, year=today.year - offset, revenue=revenue
        )
        db.add(year)
        out["years"][today.year - offset] = year

    network = CompanyProject(
        company_id=company.id,
        name="Xarxa municipal (referencia sintetica A)",
        client_type=ClientType.PUBLIC,
        amount=Decimal("60000"),
        start_date=date(today.year - 2, 3, 1),
        end_date=date(today.year - 1, 2, 28),
        status=ProjectStatus.COMPLETED,
    )
    network.services = [rows["services"]["dedicated_network"], rows["services"]["access_ftth"]]
    # Plurianual amb desglossament executat per any (0052).
    network.annual_amounts = [
        CompanyProjectAnnualAmount(
            company_id=company.id, year=today.year - 2, amount=Decimal("20000")
        ),
        CompanyProjectAnnualAmount(
            company_id=company.id, year=today.year - 1, amount=Decimal("40000")
        ),
    ]
    pbx = CompanyProject(
        company_id=company.id,
        name="Centraleta (referencia sintetica B)",
        client_type=ClientType.PRIVATE,
        amount=Decimal("30000"),
        start_date=date(today.year - 2, 1, 15),
        end_date=date(today.year - 2, 6, 30),
        status=ProjectStatus.COMPLETED,
    )
    pbx.services = [rows["services"]["voice_pbx"]]
    db.add_all([network, pbx])
    out["projects"] = {"network": network, "pbx": pbx}

    rc = CompanyInsurance(
        company_id=company.id,
        type=InsuranceType.CIVIL_LIABILITY,
        provider="Asseguradora sintetica",
        starts_at=today - timedelta(days=100),
        expires_at=today + timedelta(days=265),
    )
    db.add(rc)
    await db.flush()
    for kind, amount in (
        (InsuranceLimitKind.PER_CLAIM, "600000"),
        (InsuranceLimitKind.ANNUAL_AGGREGATE, "600000"),
        (InsuranceLimitKind.EMPLOYER_PER_VICTIM, "300000"),
    ):
        db.add(
            CompanyInsuranceLimit(
                company_id=company.id, insurance_id=rc.id, kind=kind, amount=Decimal(amount)
            )
        )
    out["insurance"] = rc

    registry = CompanyRegistration(
        company_id=company.id,
        name="Registre d'operadors (sintetic)",
        type=RegistrationType.SECTOR_OPERATOR_REGISTRY,
        issuer="CNMC",
        scope_service_lines=["access_ftth", "voice_trunk"],
    )
    db.add(registry)
    out["registration"] = registry

    for name, profiles in PEOPLE:
        person = CompanyPerson(
            company_id=company.id,
            first_name=name,
            profile_codes=profiles,
            availability_status=AvailabilityStatus.AVAILABLE,
        )
        db.add(person)
        out["people"][name] = person

    for key, (kind, until) in {
        "otdr": (EquipmentType.OTDR, today + timedelta(days=180)),
        "olts": (EquipmentType.OLTS, today + timedelta(days=90)),
        "splicer": (EquipmentType.FUSION_SPLICER, None),
    }.items():
        unit = CompanyEquipment(
            company_id=company.id,
            equipment_type=kind,
            serial_reference=f"SINT-{key.upper()}-1",
            access_mode=EquipmentAccessMode.OWNED,
            calibration_required=until is not None,
            calibration_valid_until=until,
        )
        db.add(unit)
        out["equipment"][key] = unit

    ipv4 = CompanyOperatingFact(
        company_id=company.id,
        key=OperatingFactKey.IPV4_ASSIGNABLE_COUNT,
        value=True,
        value_number=Decimal("20"),
    )
    db.add(ipv4)
    rows["facts"][str(ipv4.key)] = ipv4
    out["ipv4"] = ipv4
    await db.flush()
    return out
