"""Fotografia del perfil corporatiu, carregada una vegada.

Les regles reben aquest objecte, no una sessio. Dos motius:

* **Rendiment.** Avaluar cinquanta requisits no pot fer cinquanta consultes per
  categoria. Tot es carrega en una passada i s'indexa en memoria.
* **Determinisme.** Una regla que no pot consultar la base de dades no pot
  dependre d'un ordre d'execucio ni d'un estat que canvia a mitja avaluacio.

El `fingerprint` inclou nomes el que pot alterar un compliment: imports,
noms, vigencies, tipus i evidencies. NO inclou notes, colors ni descripcions.
Si canvia una dada que pot canviar un veredicte, el run anterior queda obsolet;
si canvia una que no, no cal recalcular res.

La **completitud per seccio** es tan important com les dades: distingeix "no
tenim ISO 27001 registrada" de "l'empresa no te ISO 27001". Amb el perfil
incomplet la resposta honesta es `unknown`, no `does_not_satisfy`.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.documents import Document, DocumentLink
from app.models.enums import DocumentCategory, LinkedEntityType, ProjectStatus
from app.models.identity import Company
from app.models.people import CompanyPerson, PersonQualification
from app.models.profile import CompanyFinancialYear
from app.models.projects import CompanyProject
from app.services.completeness import compute_profile_completeness

#: Una seccio es "prou completa" per concloure una absencia a partir d'aquest
#: rati. Per sota, no tenir una dada registrada no demostra que no existeixi.
SECTION_COMPLETE_RATIO = 1.0

#: Documents que acrediten de veritat una execucio, davant d'un organ de
#: contractacio. Un contracte o una factura ajuden, pero no son el certificat.
STRONG_PROJECT_CATEGORIES = frozenset({DocumentCategory.PROJECT_EVIDENCE})
SUPPORTING_PROJECT_CATEGORIES = frozenset(
    {DocumentCategory.LEGAL, DocumentCategory.COMMERCIAL, DocumentCategory.FINANCIAL}
)


@dataclass(frozen=True, slots=True)
class DocumentRef:
    id: uuid.UUID
    name: str
    category: DocumentCategory
    expires_at: date | None


@dataclass(slots=True)
class ProfileSnapshot:
    """Tot el perfil corporatiu rellevant, indexat per a les regles."""

    company_id: uuid.UUID
    financial_years: list[CompanyFinancialYear] = field(default_factory=list)
    certifications: list[CompanyCertification] = field(default_factory=list)
    registrations: list[CompanyRegistration] = field(default_factory=list)
    insurances: list[CompanyInsurance] = field(default_factory=list)
    projects: list[CompanyProject] = field(default_factory=list)
    people: list[CompanyPerson] = field(default_factory=list)
    qualifications: list[PersonQualification] = field(default_factory=list)
    #: Documents que justifiquen cada entitat, per (tipus, id).
    documents_by_entity: dict[tuple[str, uuid.UUID], list[DocumentRef]] = field(
        default_factory=dict
    )
    #: Rati de completitud 0..1 per seccio, tal com el calcula el sistema existent.
    section_ratio: dict[str, float] = field(default_factory=dict)
    completeness_score: int = 0

    def is_section_complete(self, key: str) -> bool:
        """Nomes amb la seccio completa una absencia significa alguna cosa."""
        return self.section_ratio.get(key, 0.0) >= SECTION_COMPLETE_RATIO

    def documents_for(self, entity_type: LinkedEntityType, entity_id: uuid.UUID):
        return self.documents_by_entity.get((entity_type.value, entity_id), [])

    def project_cpv_codes(self, project: CompanyProject) -> set[str]:
        return {link.cpv_code for link in project.cpvs}

    def project_service_names(self, project: CompanyProject) -> set[str]:
        return {service.name.casefold().strip() for service in project.services}


async def load_profile_snapshot(session: AsyncSession, company: Company) -> ProfileSnapshot:
    """Carrega el perfil sencer en poques consultes."""
    from sqlalchemy.orm import selectinload

    company_id = company.id

    financial_years = list(
        (
            await session.scalars(
                sa.select(CompanyFinancialYear)
                .where(CompanyFinancialYear.company_id == company_id)
                .order_by(CompanyFinancialYear.year.desc())
            )
        ).all()
    )
    certifications = list(
        (
            await session.scalars(
                sa.select(CompanyCertification).where(
                    CompanyCertification.company_id == company_id
                )
            )
        ).all()
    )
    registrations = list(
        (
            await session.scalars(
                sa.select(CompanyRegistration).where(CompanyRegistration.company_id == company_id)
            )
        ).all()
    )
    insurances = list(
        (
            await session.scalars(
                sa.select(CompanyInsurance).where(CompanyInsurance.company_id == company_id)
            )
        ).all()
    )
    projects = list(
        (
            await session.scalars(
                sa.select(CompanyProject)
                .options(
                    selectinload(CompanyProject.cpvs), selectinload(CompanyProject.services)
                )
                .where(CompanyProject.company_id == company_id)
            )
        ).all()
    )
    people = list(
        (
            await session.scalars(
                sa.select(CompanyPerson)
                .options(selectinload(CompanyPerson.qualifications))
                .where(CompanyPerson.company_id == company_id)
            )
        ).all()
    )
    qualifications = [q for person in people for q in person.qualifications]

    # Documents justificatius, en una sola consulta per a tot el perfil.
    rows = (
        await session.execute(
            sa.select(DocumentLink, Document)
            .join(Document, Document.id == DocumentLink.document_id)
            .where(DocumentLink.company_id == company_id)
        )
    ).all()
    documents_by_entity: dict[tuple[str, uuid.UUID], list[DocumentRef]] = {}
    for link, document in rows:
        documents_by_entity.setdefault((link.entity_type.value, link.entity_id), []).append(
            DocumentRef(
                id=document.id,
                name=document.name,
                category=document.category,
                expires_at=document.expires_at,
            )
        )

    score, sections = await compute_profile_completeness(session, company)

    return ProfileSnapshot(
        company_id=company_id,
        financial_years=financial_years,
        certifications=certifications,
        registrations=registrations,
        insurances=insurances,
        projects=projects,
        people=people,
        qualifications=qualifications,
        documents_by_entity=documents_by_entity,
        section_ratio={section.key: section.ratio for section in sections},
        completeness_score=score,
    )


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{Decimal(value):.2f}"


def compute_profile_fingerprint(snapshot: ProfileSnapshot) -> str:
    """SHA-256 de tot el que pot alterar un veredicte de compliment.

    S'ordena abans d'agregar perque l'ordre de les consultes no ha d'afectar el
    resultat. Les notes i descripcions queden fora expressament: canviar-les no
    canvia si l'empresa compleix res.
    """
    parts: list[str] = []

    for year in snapshot.financial_years:
        parts.append(f"fy|{year.year}|{_money(year.revenue)}|{year.currency}")
    for cert in snapshot.certifications:
        parts.append(
            f"cert|{cert.name.strip().casefold()}|{cert.issued_at}|{cert.expires_at}"
            f"|{cert.verification_status}"
        )
    for reg in snapshot.registrations:
        parts.append(
            f"reg|{reg.type}|{reg.name.strip().casefold()}|{reg.issued_at}|{reg.expires_at}"
        )
    for ins in snapshot.insurances:
        # Els limits (per sinistre, anual, per victima...) i l'assegurat addicional
        # els llegeix la regla d'assegurances: afegir-ne o canviar-ne un ha de deixar
        # el compliment desactualitzat, com qualsevol altra dada que el decideix.
        limits = ",".join(
            sorted(
                f"{limit.kind}:{_money(limit.amount)}:{limit.currency}"
                for limit in (getattr(ins, "limits", None) or [])
            )
        )
        parts.append(
            f"ins|{ins.type}|{_money(ins.coverage_amount)}|{ins.currency}|{ins.expires_at}"
            f"|{getattr(ins, 'additional_insured', None)}|{limits}"
        )
    for project in snapshot.projects:
        cpvs = ",".join(sorted(link.cpv_code for link in project.cpvs))
        services = ",".join(sorted(s.name.strip().casefold() for s in project.services))
        parts.append(
            f"proj|{project.id}|{_money(project.amount)}|{project.currency}|{project.status}"
            f"|{project.start_date}|{project.end_date}|{cpvs}|{services}"
        )
    for person in snapshot.people:
        parts.append(f"person|{person.id}|{person.availability_status}")
    for qualification in snapshot.qualifications:
        parts.append(
            f"qual|{qualification.type}|{qualification.name.strip().casefold()}"
            f"|{qualification.expires_at}"
        )
    for (entity_type, entity_id), documents in snapshot.documents_by_entity.items():
        for document in documents:
            parts.append(f"doc|{entity_type}|{entity_id}|{document.id}|{document.category}")
    # La completitud entra al fingerprint: passar de perfil incomplet a complet
    # pot convertir un `unknown` en un `does_not_satisfy`.
    for key, ratio in snapshot.section_ratio.items():
        parts.append(f"sect|{key}|{ratio:.4f}")

    digest = hashlib.sha256()
    for part in sorted(parts):
        digest.update(part.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def project_evidence_strength(
    snapshot: ProfileSnapshot, project: CompanyProject
) -> tuple[str, DocumentRef | None]:
    """Politica explicita de que acredita un projecte.

    Un certificat de bona execucio es la prova que demana un organ de
    contractacio. Un contracte o una factura donen suport pero no acrediten
    l'execucio. Cap document no acredita res.
    """
    from app.models.enums import EvidenceStrength

    documents = snapshot.documents_for(LinkedEntityType.COMPANY_PROJECT, project.id)
    strong = next((d for d in documents if d.category in STRONG_PROJECT_CATEGORIES), None)
    if strong is not None:
        return EvidenceStrength.STRONG, strong
    supporting = next((d for d in documents if d.category in SUPPORTING_PROJECT_CATEGORIES), None)
    if supporting is not None:
        return EvidenceStrength.SUPPORTING, supporting
    return EvidenceStrength.MISSING, None


def is_completed(project: CompanyProject) -> bool:
    return project.status == ProjectStatus.COMPLETED
