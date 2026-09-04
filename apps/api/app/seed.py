"""Seed de desenvolupament.

Dades COMPLETAMENT FICTICIES. Cap dada real de cap empresa.

    python -m app.seed             # crea el que falti i restaura el que hagi derivat
    python -m app.seed --reset     # esborra els tenants demo i els torna a muntar
    python -m app.seed --dry-run   # diu que faria, sense tocar res

## Idempotencia

Abans, aquest fitxer comprovava si existia l'usuari demo i, si hi era, no feia
res. Aixo evita duplicats pero no es idempotencia: si algu (o un test) canviava
el nom comercial de l'empresa demo, el seed no ho corregia mai. Va passar
exactament aixo — el nom va acabar sent "Smoke 9b235f49".

Ara cada entitat es declara amb una **clau natural** i un conjunt de valors, i
el seed fa un upsert: crea el que falta i **retorna als valors declarats** el
que hagi derivat. Executar-lo dues vegades seguides deixa la base de dades
exactament igual; executar-lo despres d'una modificacio manual la desfa.

El que el seed NO fa es esborrar files que no hagi creat ell: si hi ha un
servei de mes, l'ha de treure algu que sapiga d'on surt. Per a aixo hi ha
`--reset`, que buida els tenants demo sencers i els reconstrueix.

Les dades publiques (PLACSP: licitacions, organismes, raw store) no es toquen
mai, ni tan sols amb `--reset`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.cpv import CompanyCPVPreference, CPVCode
from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.documents import Document, DocumentLink
from app.models.enums import (
    AvailabilityStatus,
    ClientType,
    DocumentCategory,
    InsuranceType,
    LinkedEntityType,
    MembershipRole,
    ProjectStatus,
    QualificationType,
    RegistrationType,
    RelationshipType,
    SpecializationLevel,
    TerritoryType,
    VerificationStatus,
)
from app.models.identity import Company, CompanyMembership, User
from app.models.people import CompanyPerson, PersonQualification
from app.models.profile import (
    CompanyContractPreferences,
    CompanyFinancialYear,
    CompanyService,
    CompanyTerritory,
)
from app.models.projects import CompanyProject, ProjectCPV
from app.services.files import compute_sha256
from app.services.storage import build_storage_key, get_storage
from app.tenant_ops import assert_local_not_production, destroy_tenant, session_scope

DEMO_EMAIL = "demo@licitacions.dev"
DEMO_PASSWORD = "Demo12345!"
DEMO_COMPANY = "Empresa Demo Telecom SL"

OTHER_EMAIL = "altre@licitacions.dev"
OTHER_PASSWORD = "Altre12345!"
OTHER_COMPANY = "Altra Empresa Demo SL"

TODAY = date.today()


@dataclass(slots=True)
class Report:
    """Que ha fet el seed. Serveix per demostrar que la segona passada no fa res."""

    created: dict[str, int] = field(default_factory=dict)
    restored: dict[str, int] = field(default_factory=dict)

    def note(self, label: str, was_created: bool, changed: bool) -> None:
        target = self.created if was_created else self.restored
        if was_created or changed:
            target[label] = target.get(label, 0) + 1

    @property
    def clean(self) -> bool:
        return not self.created and not self.restored

    def render(self) -> str:
        if self.clean:
            return "  Res a fer: la base de dades ja coincideix amb el seed."
        lines = []
        if self.created:
            lines.append("  Creat:")
            lines += [f"    {label}: {count}" for label, count in sorted(self.created.items())]
        if self.restored:
            lines.append("  Restaurat (havia derivat del seed):")
            lines += [f"    {label}: {count}" for label, count in sorted(self.restored.items())]
        return "\n".join(lines)


def _fake_pdf(title: str) -> bytes:
    """PDF minim valid, generat aqui mateix. No conte dades reals."""
    content = f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 120] /Contents 4 0 R
   /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 90 >> stream
BT /F1 12 Tf 20 60 Td ({title}) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
trailer << /Root 1 0 R >>
%%EOF
"""
    return content.encode("latin-1", errors="replace")


async def _upsert(
    session: AsyncSession,
    model: type,
    key: dict[str, Any],
    values: dict[str, Any],
    report: Report,
    label: str,
    on_create: dict[str, Any] | None = None,
) -> Any:
    """Crea la fila si no hi es; si hi es, la torna als valors declarats.

    La segona meitat es la que importa: es el que fa que el seed **repari** la
    deriva en comptes de limitar-se a no duplicar-la.

    `on_create` es per als camps que nomes tenen sentit en crear la fila. Son,
    sobretot, les dates relatives a avui: una certificacio que "caduca d'aqui a
    40 dies" es util per veure l'avis del dashboard, pero si es recalcules a
    cada execucio el seed reescriuria dades cada dia que passes i deixaria de
    ser idempotent. Amb `--reset` es tornen a generar fresques.
    """
    existing = (await session.execute(sa.select(model).filter_by(**key))).scalar_one_or_none()
    if existing is None:
        instance = model(**key, **values, **(on_create or {}))
        session.add(instance)
        await session.flush()
        report.note(label, True, True)
        return instance

    changed = False
    for name, value in values.items():
        if getattr(existing, name) != value:
            setattr(existing, name, value)
            changed = True
    if changed:
        await session.flush()
    report.note(label, False, changed)
    return existing


async def _upsert_user(
    session: AsyncSession, email: str, full_name: str, password: str, report: Report
) -> User:
    """L'usuari no pot passar per `_upsert`: el hash es obligatori en crear-lo.

    I, a diferencia de la resta de camps, la contrasenya NO es restaura a cada
    execucio: si algu l'ha canviada a proposit en el seu entorn, tornar-la a
    posar seria una sorpresa desagradable.
    """
    existing = (
        await session.execute(sa.select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is None:
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        report.note("usuari", True, True)
        return user

    changed = False
    for name, value in (("full_name", full_name), ("is_active", True)):
        if getattr(existing, name) != value:
            setattr(existing, name, value)
            changed = True
    if changed:
        await session.flush()
    report.note("usuari", False, changed)
    return existing


async def seed(*, dry_run: bool = False) -> Report:
    """Aplica el seed obrint la seva propia sessio."""
    async with session_scope() as session:
        return await seed_into(session, dry_run=dry_run)


async def seed_into(session: AsyncSession, *, dry_run: bool = False) -> Report:
    """Aplica el seed sobre una sessio ja oberta.

    Separat de `seed()` perque els tests el puguin executar contra la seva
    propia base de dades: sense aixo, comprovar que el seed es idempotent
    exigiria una PostgreSQL engegada.
    """
    report = Report()
    user = await _upsert_user(session, DEMO_EMAIL, "Usuari Demo", DEMO_PASSWORD, report)
    other_user = await _upsert_user(
        session, OTHER_EMAIL, "Usuari d'una altra empresa", OTHER_PASSWORD, report
    )

    company = await _upsert(
        session,
        Company,
        {"legal_name": DEMO_COMPANY},
        {
            "trade_name": "Demo Telecom",
            "tax_id": "B00000000",
            "legal_form": "Societat Limitada",
            "incorporation_date": date(2011, 4, 12),
            "website": "https://demo-telecom.example",
            "email": "contacte@demo-telecom.example",
            "phone": "+34 900 000 000",
            "country": "ES",
            "autonomous_region": "Catalunya",
            "province": "Girona",
            "city": "Girona",
            "postal_code": "17001",
            "address": "Carrer Fictici 1",
            "description": "Empresa de demostracio. Totes les dades son inventades.",
            "employee_count": 38,
        },
        report,
        "empresa",
    )
    # Segona empresa: serveix per validar l'aillament multi-tenant a ma.
    other_company = await _upsert(
        session,
        Company,
        {"legal_name": OTHER_COMPANY},
        {"trade_name": "Altra Demo", "country": "ES", "city": "Barcelona"},
        report,
        "empresa",
    )

    for member, target in ((user, company), (other_user, other_company)):
        await _upsert(
            session,
            CompanyMembership,
            {"user_id": member.id, "company_id": target.id},
            {"role": MembershipRole.OWNER, "is_default": True},
            report,
            "pertinenca",
        )

    # ------------------------------------------------------------- serveis
    services: dict[str, CompanyService] = {}
    for name, category, description, level in (
        (
            "Fibra optica",
            "Infraestructura",
            "Desplegament i fusio de fibra optica.",
            SpecializationLevel.EXPERT,
        ),
        ("Cablejat estructurat", "Infraestructura", None, SpecializationLevel.ADVANCED),
        ("CCTV i videovigilancia", "Seguretat", None, SpecializationLevel.INTERMEDIATE),
        ("Manteniment de xarxes", "Serveis recurrents", None, SpecializationLevel.ADVANCED),
    ):
        services[name] = await _upsert(
            session,
            CompanyService,
            {"company_id": company.id, "name": name},
            {
                "category": category,
                "description": description,
                "specialization_level": level,
                "created_by": user.id,
                "updated_by": user.id,
            },
            report,
            "servei",
        )

    # ----------------------------------------------------------- territoris
    # Els codis son NUTS-2021 reals: permeten que el pre-fit compari per
    # codi (jerarquic) i no nomes pel nom.
    for name, territory_type, code, priority in (
        ("Girona", TerritoryType.PROVINCE, "ES512", 10),
        ("Barcelona", TerritoryType.PROVINCE, "ES511", 8),
        ("Catalunya", TerritoryType.AUTONOMOUS_REGION, "ES51", 6),
    ):
        await _upsert(
            session,
            CompanyTerritory,
            {"company_id": company.id, "name": name, "territory_type": territory_type},
            {
                "code": code,
                "priority": priority,
                "willing_to_work": True,
                "created_by": user.id,
            },
            report,
            "territori",
        )

    # ------------------------------------------------------------ exercicis
    for offset, revenue in ((1, "2450000.00"), (2, "2110000.00"), (3, "1875000.00")):
        await _upsert(
            session,
            CompanyFinancialYear,
            {"company_id": company.id, "year": TODAY.year - offset},
            {"revenue": Decimal(revenue), "created_by": user.id},
            report,
            "exercici",
        )

    await _upsert(
        session,
        CompanyContractPreferences,
        {"company_id": company.id},
        {
            "minimum_interesting_contract": Decimal("30000.00"),
            "preferred_contract_min": Decimal("80000.00"),
            "preferred_contract_max": Decimal("600000.00"),
            "maximum_recommended_contract": Decimal("900000.00"),
            "maximum_assumable_contract": Decimal("1200000.00"),
            "created_by": user.id,
        },
        report,
        "preferencies de contracte",
    )

    # ------------------------------------------------------- acreditacions
    certifications = {}
    for name, issuer, number, issued_offset, expires_offset, status in (
        (
            "ISO 9001:2015",
            "Entitat Certificadora Demo",
            "ISO9001-DEMO-001",
            500,
            230,
            VerificationStatus.VERIFIED,
        ),
        # Caduca aviat a proposit: genera un avis al dashboard.
        (
            "ISO 14001:2015",
            "Entitat Certificadora Demo",
            None,
            700,
            40,
            VerificationStatus.PENDING_VERIFICATION,
        ),
    ):
        certifications[name] = await _upsert(
            session,
            CompanyCertification,
            {"company_id": company.id, "name": name},
            {
                "issuer": issuer,
                "certificate_number": number,
                "verification_status": status,
                "created_by": user.id,
            },
            report,
            "certificacio",
            on_create={
                "issued_at": TODAY - timedelta(days=issued_offset),
                "expires_at": TODAY + timedelta(days=expires_offset),
            },
        )

    await _upsert(
        session,
        CompanyRegistration,
        {"company_id": company.id, "name": "ROLECE - Inscripcio"},
        {
            "type": RegistrationType.ROLECE,
            "registration_number": "ROLECE-DEMO-0001",
            "issuer": "Ministeri (demo)",
            "created_by": user.id,
        },
        report,
        "registre",
        on_create={"issued_at": TODAY - timedelta(days=800)},
    )

    insurance = await _upsert(
        session,
        CompanyInsurance,
        {"company_id": company.id, "policy_number": "RC-DEMO-778899"},
        {
            "type": InsuranceType.CIVIL_LIABILITY,
            "provider": "Asseguradora Demo",
            "coverage_description": "Responsabilitat civil general i d'explotacio.",
            "coverage_amount": Decimal("1000000.00"),
            "created_by": user.id,
        },
        report,
        "assegurança",
        on_create={
            "starts_at": TODAY - timedelta(days=200),
            "expires_at": TODAY + timedelta(days=165),
        },
    )

    # ----------------------------------------------------------------- equip
    person = await _upsert(
        session,
        CompanyPerson,
        {"company_id": company.id, "first_name": "Marta", "last_name": "Exemple Fictici"},
        {
            "role": "Cap de projectes",
            "department": "Enginyeria",
            "professional_profile": (
                "Perfil de demostracio, no correspon a cap persona real."
            ),
            "years_experience": 12,
            "availability_status": AvailabilityStatus.AVAILABLE,
            "created_by": user.id,
        },
        report,
        "persona",
    )
    qualification = await _upsert(
        session,
        PersonQualification,
        {"person_id": person.id, "name": "Enginyeria de Telecomunicacions"},
        {
            "type": QualificationType.DEGREE,
            "issuer": "Universitat Demo",
            "issued_at": date(2010, 6, 30),
            "created_by": user.id,
        },
        report,
        "titulacio",
    )

    # ------------------------------------------------------------ projectes
    projects: dict[str, CompanyProject] = {}
    for name, values, dates_on_create, service_names in (
        (
            "Desplegament FTTH nucli urba (demo)",
            {
                "client_name": "Ajuntament Demo",
                "client_type": ClientType.PUBLIC,
                "sector": "Administracio local",
                "description": "Projecte fictici de desplegament de fibra.",
                "amount": Decimal("281430.00"),
                "location": "Girona",
                "status": ProjectStatus.COMPLETED,
                "created_by": user.id,
            },
            {
                "start_date": date(TODAY.year - 1, 2, 1),
                "end_date": date(TODAY.year - 1, 11, 15),
            },
            ("Fibra optica", "Cablejat estructurat"),
        ),
        (
            "Renovacio de xarxa interna (demo)",
            {
                "client_name": "Consell Comarcal Demo",
                "client_type": ClientType.PUBLIC,
                "sector": "Administracio local",
                "amount": Decimal("194000.00"),
                "location": "Banyoles",
                "status": ProjectStatus.COMPLETED,
                "created_by": user.id,
            },
            {
                "start_date": date(TODAY.year - 2, 5, 10),
                "end_date": date(TODAY.year - 2, 12, 20),
            },
            ("Cablejat estructurat", "Manteniment de xarxes"),
        ),
        (
            "Videovigilancia poligon industrial (demo)",
            {
                "client_name": "Empresa Privada Demo SA",
                "client_type": ClientType.PRIVATE,
                "amount": Decimal("64500.00"),
                "location": "Figueres",
                "status": ProjectStatus.COMPLETED,
                "created_by": user.id,
            },
            {
                "start_date": date(TODAY.year - 1, 6, 1),
                "end_date": date(TODAY.year - 1, 9, 30),
            },
            ("CCTV i videovigilancia",),
        ),
    ):
        project = await _upsert(
            session,
            CompanyProject,
            {"company_id": company.id, "name": name},
            values,
            report,
            "projecte",
            on_create=dates_on_create,
        )
        wanted = [services[service_name] for service_name in service_names]
        # La relacio es lazy i estem en sessio async: cal carregar-la
        # explicitament abans de comparar-la, o saltaria `MissingGreenlet`.
        await session.refresh(project, ["services"])
        if {item.id for item in project.services} != {item.id for item in wanted}:
            project.services = wanted
            report.note("serveis del projecte", False, True)
        projects[name] = project

    await session.flush()

    # ------------------------------------------------------------------ CPV
    # CPV reals que corresponen als serveis declarats de l'empresa demo
    # (fibra optica, cablejat estructurat, CCTV i manteniment de xarxes).
    for code, description in (
        ("45314320", "Treballs d'instal-lacio de cablejat informatic"),
        ("45314300", "Instal-lacio d'infraestructura de cablejat"),
        ("32562000", "Cables de fibra optica"),
        ("32412110", "Xarxa Internet"),
        ("32400000", "Xarxes"),
        ("35125300", "Cameres de seguretat"),
        ("51300000", "Serveis d'instal-lacio d'equips de comunicacions"),
        ("50330000", "Manteniment d'equips de telecomunicacions"),
        ("72700000", "Serveis de xarxes informatiques"),
        ("45000000", "Treballs de construccio"),
    ):
        await _upsert(
            session,
            CPVCode,
            {"code": code},
            {"description": description},
            report,
            "codi CPV del cataleg",
        )

    for code, priority, excluded, notes in (
        ("45314320", 10, False, None),
        ("45314300", 10, False, None),
        ("32562000", 9, False, None),
        ("32412110", 8, False, None),
        ("32400000", 8, False, None),
        ("51300000", 7, False, None),
        ("50330000", 6, False, None),
        ("72700000", 5, False, None),
        ("35125300", 3, False, None),
        # CPV descartat expressament: serveix per veure la diferencia entre
        # "ara no ens interessa" (active=False) i "no el volem mai".
        ("45000000", 0, True, "Obra civil pura: fora del nostre abast."),
    ):
        await _upsert(
            session,
            CompanyCPVPreference,
            {"company_id": company.id, "cpv_code": code},
            {"priority": priority, "excluded": excluded, "active": True, "notes": notes},
            report,
            "CPV de l'empresa",
        )

    # Amb quins CPV es va executar cada projecte.
    for project_name, code in (
        ("Desplegament FTTH nucli urba (demo)", "45314320"),
        ("Desplegament FTTH nucli urba (demo)", "32412110"),
        ("Renovacio de xarxa interna (demo)", "45314320"),
        ("Videovigilancia poligon industrial (demo)", "35125300"),
    ):
        await _upsert(
            session,
            ProjectCPV,
            {"project_id": projects[project_name].id, "cpv_code": code},
            {},
            report,
            "CPV del projecte",
        )

    # ------------------------------------------------------------ evidencies
    storage = get_storage()
    evidences = [
        (
            "Certificat ISO 9001 (demo).pdf",
            _fake_pdf("Certificat ISO 9001 - DOCUMENT DE DEMOSTRACIO"),
            DocumentCategory.CERTIFICATION,
            LinkedEntityType.COMPANY_CERTIFICATION,
            certifications["ISO 9001:2015"].id,
            RelationshipType.CERTIFICATE,
            certifications["ISO 9001:2015"].expires_at,
        ),
        (
            "Certificat de bona execucio - Ajuntament Demo.pdf",
            _fake_pdf("Certificat de bona execucio Ajuntament - DEMOSTRACIO"),
            DocumentCategory.PROJECT_EVIDENCE,
            LinkedEntityType.COMPANY_PROJECT,
            projects["Desplegament FTTH nucli urba (demo)"].id,
            RelationshipType.CERTIFICATE_OF_GOOD_EXECUTION,
            None,
        ),
        (
            "Certificat de bona execucio - Consell Comarcal Demo.pdf",
            _fake_pdf("Certificat de bona execucio Consell Comarcal - DEMOSTRACIO"),
            DocumentCategory.PROJECT_EVIDENCE,
            LinkedEntityType.COMPANY_PROJECT,
            projects["Renovacio de xarxa interna (demo)"].id,
            RelationshipType.CERTIFICATE_OF_GOOD_EXECUTION,
            None,
        ),
        (
            "Titol universitari (demo).pdf",
            _fake_pdf("Titol universitari - DOCUMENT DE DEMOSTRACIO"),
            DocumentCategory.CV,
            LinkedEntityType.PERSON_QUALIFICATION,
            qualification.id,
            RelationshipType.DIPLOMA,
            None,
        ),
        (
            "Polissa de responsabilitat civil (demo).pdf",
            _fake_pdf("Polissa RC - DOCUMENT DE DEMOSTRACIO"),
            DocumentCategory.INSURANCE,
            LinkedEntityType.COMPANY_INSURANCE,
            insurance.id,
            RelationshipType.POLICY,
            insurance.expires_at,
        ),
    ]

    for title, payload, category, entity_type, entity_id, relationship, expires in evidences:
        digest = compute_sha256(payload)
        # El SHA-256 es la clau natural: el contingut es deterministic, aixi
        # que tornar a executar el seed no genera cap document nou.
        existing = (
            await session.execute(
                sa.select(Document).where(
                    Document.company_id == company.id, Document.sha256 == digest
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            key = build_storage_key(company.id, "pdf")
            # En simulacio no es puja res: un `rollback` desfaria la fila
            # pero deixaria l'objecte al bucket per sempre.
            if not dry_run:
                await storage.upload(key, payload, "application/pdf")
            document = await _upsert(
                session,
                Document,
                {"company_id": company.id, "sha256": digest},
                {
                    "name": title,
                    "original_filename": title,
                    "storage_key": key,
                    "mime_type": "application/pdf",
                    "file_size": len(payload),
                    "category": category,
                    "expires_at": expires,
                    "created_by": user.id,
                    "updated_by": user.id,
                },
                report,
                "document",
            )
        else:
            document = existing
            # La fila hi es: nomes cal assegurar-se que el binari tambe.
            if not await storage.exists(document.storage_key):
                if not dry_run:
                    await storage.upload(document.storage_key, payload, "application/pdf")
                report.note("binari de document reposat", False, True)
            for name, value in (("name", title), ("category", category)):
                if getattr(document, name) != value:
                    setattr(document, name, value)
                    report.note("document", False, True)

        await _upsert(
            session,
            DocumentLink,
            {
                "document_id": document.id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "relationship_type": relationship,
            },
            {"company_id": company.id, "created_by": user.id},
            report,
            "enllac dada -> evidencia",
        )

    if dry_run:
        await session.rollback()
    else:
        await session.commit()

    return report


async def reset() -> None:
    """Buida els tenants demo i els torna a muntar des de zero.

    Nomes toca dades de tenant: les licitacions, els organismes i el raw store
    de PLACSP son publics i no es toquen mai.
    """
    assert_local_not_production()
    async with session_scope() as session:
        for legal_name in (DEMO_COMPANY, OTHER_COMPANY):
            company_id = (
                await session.execute(
                    sa.select(Company.id).where(Company.legal_name == legal_name)
                )
            ).scalar_one_or_none()
            if company_id is None:
                continue
            report = await destroy_tenant(
                session, company_id, require_ephemeral=False
            )
            print(
                f"  {legal_name}: {report.total_rows} files i "
                f"{report.storage_objects_deleted} objectes esborrats"
            )
            if not report.clean:
                print(f"  ATENCIO: han quedat orfes: {report.leftovers}")

        for email in (DEMO_EMAIL, OTHER_EMAIL):
            await session.execute(sa.delete(User).where(User.email == email))
        await session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Esborra els tenants demo sencers abans de tornar-los a crear.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Diu que faria i no desa res.",
    )
    args = parser.parse_args(argv)

    if args.reset and args.dry_run:
        print("--reset i --dry-run no tenen sentit junts.", file=sys.stderr)
        return 2

    if args.reset:
        assert_local_not_production()
        print("Esborrant els tenants demo...")
        asyncio.run(reset())

    report = asyncio.run(seed(dry_run=args.dry_run))
    print("Seed en simulacio (no s'ha desat res)." if args.dry_run else "Seed completat.")
    print(report.render())
    if not args.dry_run:
        print(f"\n  Empresa: {DEMO_COMPANY}")
        print(f"  Usuari:  {DEMO_EMAIL}")
        print(f"  Clau:    {DEMO_PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
