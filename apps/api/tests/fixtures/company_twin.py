"""Besso SINTETIC del coneixement actual d'una empresa pilot, per als tests de 0051.

No es cap dada real: es la forma que l'anunci de l'Escala necessita per provar el
motor (un fet de FTTH propia amb cobertura completa en tres municipis que NO son
el de la licitacio, un servei d'accessos FTTH en revenda amb quatre majoristes,
una centraleta declarada sense capacitats, un ENS mitja declarat amb evidencia
pendent de verificar). Cap cost, marge, tarifa ni dada verificada.
"""

from __future__ import annotations

from typing import Any

from app.knowledge.coverage import AreaIn, CoverageIn
from app.models.credentials import CompanyCertification
from app.models.enums import (
    AreaKind,
    CoverageCompleteness,
    DeliveryMode,
    OperatingFactKey,
    PartnerRole,
)
from app.models.knowledge import (
    CompanyOperatingFact,
    CompanyPartner,
    CompanySolution,
    PartnerRoleRow,
)
from app.models.profile import CompanyService
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


async def build_company_twin(db, company) -> dict[str, Any]:
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
    return rows
