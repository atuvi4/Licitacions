"""Completitud del perfil d'empresa.

Determinista i explicable. Sense IA i sense heuristiques ocultes: cada area te
un pes fix, una regla escrita que qualsevol pot verificar a ma, i un detall
que explica per que te la puntuacio que te.

Dos principis:

1. **Cap area pot dominar el resultat.** Tenir nomes les dades generals
   omplertes dona 12 punts sobre 100, no un perfil "gairebe complet".
2. **Dada sense evidencia = parcial.** Una certificacio, un registre, una
   polissa, una titulacio o un projecte que no tenen document justificatiu no
   son acreditables davant d'un organ de contractacio, i per tant no compten
   com a completats del tot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cpv import CompanyCPVPreference
from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.documents import Document, DocumentLink
from app.models.enums import LinkedEntityType
from app.models.identity import Company
from app.models.people import CompanyPerson, PersonQualification
from app.models.profile import (
    CompanyContractPreferences,
    CompanyFinancialYear,
    CompanyService,
    CompanyTerritory,
)
from app.models.projects import CompanyProject

# Camps de "Dades generals" que compten per a la completitud.
GENERAL_FIELDS = (
    "legal_name",
    "trade_name",
    "tax_id",
    "legal_form",
    "incorporation_date",
    "email",
    "phone",
    "country",
    "province",
    "city",
    "postal_code",
    "address",
)

# Llindars a partir dels quals una area es considera completa.
TARGET_SERVICES = 3
TARGET_TERRITORIES = 2
TARGET_CPVS = 3
TARGET_FINANCIAL_YEARS = 3
TARGET_PROJECTS = 3
TARGET_DOCUMENTS = 5

CONTRACT_THRESHOLD_FIELDS = (
    "minimum_interesting_contract",
    "preferred_contract_min",
    "preferred_contract_max",
    "maximum_recommended_contract",
    "maximum_assumable_contract",
)

# Pes de cada area. Han de sumar 100.
SECTIONS: tuple[tuple[str, str, int, bool], ...] = (
    # (clau, etiqueta, pes, necessita evidencia documental)
    ("general", "Dades generals", 12, False),
    ("services", "Serveis", 10, False),
    ("territories", "Ambit geografic", 8, False),
    ("cpv", "CPV", 8, False),
    ("solvency", "Solvencia economica", 12, False),
    ("certifications", "Certificacions", 10, True),
    ("registrations", "Registres i classificacions", 8, True),
    ("insurances", "Assegurances", 8, True),
    ("team", "Equip huma", 8, True),
    ("experience", "Experiencia", 10, True),
    ("documents", "Documents", 6, False),
)

assert sum(weight for _, _, weight, _ in SECTIONS) == 100


@dataclass(slots=True)
class SectionResult:
    key: str
    label: str
    weight: int
    ratio: float
    detail: str
    requires_evidence: bool = False

    @property
    def status(self) -> str:
        if self.ratio <= 0:
            return "empty"
        if self.ratio >= 1:
            return "complete"
        return "partial"

    @property
    def points(self) -> float:
        return round(self.weight * self.ratio, 2)


async def _count(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _with_evidence(
    session: AsyncSession, company_id: uuid.UUID, entity_type: LinkedEntityType
) -> int:
    """Quantes entitats d'aquest tipus tenen com a minim un document enllacat."""
    return await _count(
        session,
        sa.select(sa.func.count(sa.distinct(DocumentLink.entity_id))).where(
            DocumentLink.company_id == company_id,
            DocumentLink.entity_type == entity_type,
        ),
    )


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _accredited_ratio(total: int, with_evidence: int) -> float:
    """0 si no hi ha res; 0,5 per tenir la dada i fins a 0,5 mes per l'evidencia."""
    if total == 0:
        return 0.0
    return 0.5 + 0.5 * min(with_evidence / total, 1.0)


def _evidence_detail(noun: str, total: int, with_evidence: int) -> str:
    if total == 0:
        return f"cap {noun} registrat"
    missing = total - with_evidence
    if missing <= 0:
        return f"{total} {noun}(s), tots amb evidencia documental"
    return f"{total} {noun}(s), {missing} sense evidencia documental"


async def compute_profile_completeness(
    session: AsyncSession, company: Company
) -> tuple[int, list[SectionResult]]:
    """Retorna (percentatge 0-100, desglossament per arees)."""
    company_id: uuid.UUID = company.id
    results: dict[str, tuple[float, str]] = {}

    # --- Dades generals ---
    filled = sum(1 for field in GENERAL_FIELDS if _has_value(getattr(company, field, None)))
    results["general"] = (
        filled / len(GENERAL_FIELDS),
        f"{filled} de {len(GENERAL_FIELDS)} camps informats",
    )

    # --- Serveis ---
    active_services = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(CompanyService)
        .where(CompanyService.company_id == company_id, CompanyService.active.is_(True)),
    )
    results["services"] = (
        min(active_services / TARGET_SERVICES, 1.0),
        f"{active_services} servei(s) actiu(s) de {TARGET_SERVICES} recomanats",
    )

    # --- Ambit geografic ---
    territories = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(CompanyTerritory)
        .where(
            CompanyTerritory.company_id == company_id,
            CompanyTerritory.willing_to_work.is_(True),
        ),
    )
    results["territories"] = (
        min(territories / TARGET_TERRITORIES, 1.0),
        f"{territories} territori(s) on es vol treballar",
    )

    # --- CPV ---
    cpvs = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(CompanyCPVPreference)
        .where(
            CompanyCPVPreference.company_id == company_id,
            CompanyCPVPreference.active.is_(True),
            CompanyCPVPreference.excluded.is_(False),
        ),
    )
    results["cpv"] = (
        min(cpvs / TARGET_CPVS, 1.0),
        f"{cpvs} CPV actiu(s) d'interes de {TARGET_CPVS} recomanats",
    )

    # --- Solvencia economica ---
    years = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(CompanyFinancialYear)
        .where(
            CompanyFinancialYear.company_id == company_id,
            CompanyFinancialYear.revenue.is_not(None),
        ),
    )
    preferences = (
        await session.execute(
            sa.select(CompanyContractPreferences).where(
                CompanyContractPreferences.company_id == company_id
            )
        )
    ).scalar_one_or_none()
    thresholds = (
        sum(1 for field in CONTRACT_THRESHOLD_FIELDS if getattr(preferences, field, None) is not None)
        if preferences
        else 0
    )
    results["solvency"] = (
        0.6 * min(years / TARGET_FINANCIAL_YEARS, 1.0)
        + 0.4 * (thresholds / len(CONTRACT_THRESHOLD_FIELDS)),
        f"{years} exercici(s) amb facturacio, "
        f"{thresholds}/{len(CONTRACT_THRESHOLD_FIELDS)} llindars de contracte",
    )

    # --- Certificacions (necessiten evidencia) ---
    certifications = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(CompanyCertification)
        .where(CompanyCertification.company_id == company_id),
    )
    certified = await _with_evidence(session, company_id, LinkedEntityType.COMPANY_CERTIFICATION)
    results["certifications"] = (
        _accredited_ratio(certifications, certified),
        _evidence_detail("certificacio", certifications, certified),
    )

    # --- Registres (necessiten evidencia) ---
    registrations = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(CompanyRegistration)
        .where(CompanyRegistration.company_id == company_id),
    )
    registered = await _with_evidence(session, company_id, LinkedEntityType.COMPANY_REGISTRATION)
    results["registrations"] = (
        _accredited_ratio(registrations, registered),
        _evidence_detail("registre", registrations, registered),
    )

    # --- Assegurances (necessiten evidencia) ---
    insurances = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(CompanyInsurance)
        .where(CompanyInsurance.company_id == company_id),
    )
    insured = await _with_evidence(session, company_id, LinkedEntityType.COMPANY_INSURANCE)
    results["insurances"] = (
        _accredited_ratio(insurances, insured),
        _evidence_detail("polissa", insurances, insured),
    )

    # --- Equip huma (necessita titulacions, i les titulacions evidencia) ---
    people = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(CompanyPerson)
        .where(CompanyPerson.company_id == company_id),
    )
    qualifications = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(PersonQualification)
        .join(CompanyPerson, CompanyPerson.id == PersonQualification.person_id)
        .where(CompanyPerson.company_id == company_id),
    )
    accredited_qualifications = await _with_evidence(
        session, company_id, LinkedEntityType.PERSON_QUALIFICATION
    )
    if people == 0:
        team_ratio = 0.0
        team_detail = "cap persona registrada"
    else:
        team_ratio = 0.4
        if qualifications:
            team_ratio += 0.3 + 0.3 * min(accredited_qualifications / qualifications, 1.0)
        team_detail = (
            f"{people} persona(es), {qualifications} titulacio(ns), "
            f"{accredited_qualifications} amb evidencia"
        )
    results["team"] = (team_ratio, team_detail)

    # --- Experiencia (necessita evidencia) ---
    projects = await _count(
        session,
        sa.select(sa.func.count())
        .select_from(CompanyProject)
        .where(CompanyProject.company_id == company_id),
    )
    projects_accredited = await _with_evidence(
        session, company_id, LinkedEntityType.COMPANY_PROJECT
    )
    if projects == 0:
        experience_ratio = 0.0
        experience_detail = "cap projecte registrat"
    else:
        experience_ratio = 0.5 * min(projects / TARGET_PROJECTS, 1.0) + 0.5 * min(
            projects_accredited / projects, 1.0
        )
        experience_detail = (
            f"{projects} projecte(s), {projects - projects_accredited} sense evidencia"
            if projects_accredited < projects
            else f"{projects} projecte(s), tots amb evidencia"
        )
    results["experience"] = (experience_ratio, experience_detail)

    # --- Documents ---
    documents = await _count(
        session,
        sa.select(sa.func.count()).select_from(Document).where(Document.company_id == company_id),
    )
    results["documents"] = (
        min(documents / TARGET_DOCUMENTS, 1.0),
        f"{documents} document(s) a la biblioteca",
    )

    sections = [
        SectionResult(
            key=key,
            label=label,
            weight=weight,
            ratio=results[key][0],
            detail=results[key][1],
            requires_evidence=requires_evidence,
        )
        for key, label, weight, requires_evidence in SECTIONS
    ]

    score = round(sum(section.points for section in sections))
    return min(score, 100), sections
