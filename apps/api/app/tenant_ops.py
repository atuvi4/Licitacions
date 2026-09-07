"""Operacions sobre un tenant sencer: crear-ne un d'efimer, radiografiar-lo i
destruir-lo sense deixar orfes.

Existeix per una rao concreta: els smoke tests necessiten fer CRUD real contra
PostgreSQL i l'object storage, i abans ho feien **sobre el tenant demo**. El
resultat era previsible: el nom comercial de l'empresa demo va acabar sent
"Smoke 9b235f49" i hi van quedar un servei, un projecte i un document
residuals. Un test que embruta les dades que hauria de deixar intactes no es un
test, es una font de soroll.

A partir d'ara cada execucio es munta el seu propi tenant, hi treballa i el
destrueix. Aquest modul es l'unica peca que sap com fer-ho.

    python -m app.tenant_ops create-ephemeral      # imprimeix JSON amb credencials
    python -m app.tenant_ops snapshot <company_id>
    python -m app.tenant_ops destroy <company_id> [--user <user_id>]
    python -m app.tenant_ops list

No forma part de l'API de producte: cap d'aquestes operacions s'exposa per HTTP.
Crear empreses i usuaris des de fora es una funcionalitat que encara no toca, i
obrir un endpoint per comoditat dels tests seria exactament la mena de porta que
despres ningu no tanca.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import secrets
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.models.documents import Document
from app.models.enums import MembershipRole
from app.models.identity import Company, CompanyMembership, User
from app.services.storage import get_storage

# Marques que identifiquen un entorn on NO es pot mutar res.
PRODUCTION_MARKERS = frozenset({"production", "prod", "prd", "live", "produccio"})

# Prefix obligatori del nom d'una empresa efimera. Serveix de segon cinturo:
# `destroy` es nega a esborrar res que no el porti, tret que s'hi insisteixi.
EPHEMERAL_PREFIX = "Smoke Test"
# `.example` esta reservat per RFC 2606: no es pot registrar mai, aixi que cap
# correu d'aquests pot arribar enlloc. `.invalid` i `.test` serien igual de
# correctes semanticament, pero `email-validator` els rebutja com a TLD especial
# i l'usuari efimer no podria ni iniciar sessio.
EPHEMERAL_EMAIL_DOMAIN = "smoke.example"

# Taules propietat d'un tenant que no tenen `company_id` propi: hi pengen a
# traves d'una altra taula. Es declaren a ma perque no hi ha manera fiable
# d'endevinar el cami, pero `_assert_tables_classified()` avisa si algun dia
# n'apareix una de nova sense classificar.
INDIRECT_TABLES: dict[str, tuple[str, str, str]] = {
    # taula                  -> (columna propia, taula pare, columna del pare)
    "person_qualifications": ("person_id", "company_people", "id"),
    "project_cpvs": ("project_id", "company_projects", "id"),
    "project_services": ("project_id", "company_projects", "id"),
    "service_cpvs": ("service_id", "company_services", "id"),
}

# Taules publiques i globals: no pertanyen a cap tenant i no s'han de tocar mai.
PUBLIC_TABLES = frozenset(
    {
        "buyers",
        "companies",
        "cpv_codes",
        "ingestion_runs",
        "source_records",
        "sources",
        # Els requisits i els criteris d'adjudicacio deriven de documents
        # publics: son globals i s'extreuen una sola vegada per licitacio.
        "award_criteria",
        "award_criterion_evidence",
        "requirement_evidence",
        "requirement_extraction_runs",
        "requirements",
        "tender_cpvs",
        "tender_documents",
        # La capa documental dels plecs es publica i global: els mateixos bytes
        # es comparteixen entre tenants i no pengen de cap empresa.
        "tender_document_contents",
        "tender_document_extractions",
        "tender_document_files",
        "tender_document_pages",
        "tender_lot_cpvs",
        "tender_lots",
        "tenders",
        "users",
    }
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Sessio amb motor propi, creat i destruit dins d'aquesta crida.

    No es reutilitza `app.db.session.SessionFactory` a proposit. Aquell motor
    manté un pool de connexions lligades al bucle d'esdeveniments que les va
    obrir; una eina de linia d'ordres o un smoke test fan diverses crides
    `asyncio.run()` seguides, cadascuna amb un bucle nou, i la segona es
    trobaria connexions d'un bucle ja tancat. Amb `NullPool` i un motor per
    crida el problema no existeix, i el cost (una connexio) es irrellevant per
    a operacions que es fan un cop.
    """
    engine = create_async_engine(settings.sqlalchemy_url, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


class ProductionGuardError(RuntimeError):
    """Es llenca quan s'intenta mutar dades en un entorn de produccio."""


def _is_production(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in PRODUCTION_MARKERS


def assert_local_not_production() -> None:
    """Barrera per a les escriptures directes a la base de dades.

    Qui escriu es aquest proces, aixi que el seu `ENVIRONMENT` es el senyal
    que mana. Es comprova abans de crear res, no despres.
    """
    if _is_production(settings.ENVIRONMENT):
        raise ProductionGuardError(
            f"ENVIRONMENT={settings.ENVIRONMENT!r}: aquestes operacions muten dades "
            "i no s'executen mai contra produccio."
        )


def assert_mutations_allowed(remote_environment: str | None = None) -> None:
    """Barra el pas si l'entorn local o el remot son de produccio.

    Es comproven totes dues bandes a proposit. El nom de la base de dades no
    es una garantia de res: qualsevol pot apuntar un `DATABASE_URL` que digui
    "dev" cap a una instancia real. El que mana es el que declara el proces
    que executa la mutacio i el que respon l'API que s'esta provant.
    """
    assert_local_not_production()
    if remote_environment is None:
        raise ProductionGuardError(
            "No s'ha pogut llegir l'entorn del backend (GET /health). "
            "Sense saber contra que s'esta executant, no es muta res."
        )
    if _is_production(remote_environment):
        raise ProductionGuardError(
            f"El backend declara ENVIRONMENT={remote_environment!r}: es produccio."
        )


def _load_all_models() -> None:
    """Assegura que `Base.metadata` conte totes les taules.

    Sense aixo, la classificacio automatica nomes veuria els models que aquest
    modul importa explicitament i la neteja es deixaria taules enrere.
    """
    importlib.import_module("app.models")


def _direct_tables() -> list[str]:
    """Taules amb `company_id`, llegides de la metadata i no d'una llista a ma."""
    _load_all_models()
    return sorted(
        name for name, table in Base.metadata.tables.items() if "company_id" in table.c
    )


def _assert_tables_classified() -> None:
    """Cap taula pot quedar sense classificar.

    Si algu afegeix una taula de tenant nova i no la classifica, la neteja la
    deixaria enrere silenciosament. Millor petar aqui.
    """
    _load_all_models()
    known = set(_direct_tables()) | set(INDIRECT_TABLES) | PUBLIC_TABLES
    unknown = set(Base.metadata.tables) - known
    if unknown:
        raise RuntimeError(
            "Taules sense classificar a app/tenant_ops.py: "
            + ", ".join(sorted(unknown))
            + ". Digues si son de tenant (i com s'hi enllacen) o publiques."
        )


@dataclass(slots=True)
class EphemeralTenant:
    company_id: str
    user_id: str
    email: str
    password: str
    legal_name: str

    def as_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


@dataclass(slots=True)
class TenantSnapshot:
    """Radiografia d'un tenant, per comparar-la abans i despres d'un test."""

    company_id: str
    legal_name: str
    trade_name: str | None
    rows: dict[str, int] = field(default_factory=dict)
    document_hashes: list[str] = field(default_factory=list)
    storage_keys: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(self.rows.values())

    def diff(self, other: TenantSnapshot) -> list[str]:
        """Diferencies llegibles. Llista buida vol dir que res no ha canviat."""
        problems: list[str] = []
        if self.legal_name != other.legal_name:
            problems.append(f"nom social: {self.legal_name!r} -> {other.legal_name!r}")
        if self.trade_name != other.trade_name:
            problems.append(f"nom comercial: {self.trade_name!r} -> {other.trade_name!r}")
        for table in sorted(set(self.rows) | set(other.rows)):
            before, after = self.rows.get(table, 0), other.rows.get(table, 0)
            if before != after:
                problems.append(f"{table}: {before} -> {after} files")
        if set(self.document_hashes) != set(other.document_hashes):
            problems.append("el conjunt de documents ha canviat")
        if set(self.storage_keys) != set(other.storage_keys):
            problems.append("els objectes de l'storage han canviat")
        return problems


async def snapshot(session: AsyncSession, company_id: uuid.UUID) -> TenantSnapshot:
    """Compta les files del tenant i llista els seus documents i objectes."""
    _assert_tables_classified()
    company = (
        await session.execute(sa.select(Company).where(Company.id == company_id))
    ).scalar_one_or_none()
    if company is None:
        raise LookupError(f"No existeix cap empresa amb id {company_id}")

    rows: dict[str, int] = {}
    for name in _direct_tables():
        table = Base.metadata.tables[name]
        rows[name] = (
            await session.execute(
                sa.select(sa.func.count()).select_from(table).where(
                    table.c.company_id == company_id
                )
            )
        ).scalar_one()

    for name, (own_column, parent_name, parent_column) in INDIRECT_TABLES.items():
        table = Base.metadata.tables[name]
        parent = Base.metadata.tables[parent_name]
        rows[name] = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(table)
                .where(
                    table.c[own_column].in_(
                        sa.select(parent.c[parent_column]).where(
                            parent.c.company_id == company_id
                        )
                    )
                )
            )
        ).scalar_one()

    documents = (
        await session.execute(
            sa.select(Document.sha256, Document.storage_key).where(
                Document.company_id == company_id
            )
        )
    ).all()

    return TenantSnapshot(
        company_id=str(company_id),
        legal_name=company.legal_name,
        trade_name=company.trade_name,
        rows=rows,
        document_hashes=sorted(digest for digest, _ in documents),
        storage_keys=sorted(key for _, key in documents),
    )


async def create_ephemeral(
    session: AsyncSession, *, label: str | None = None
) -> EphemeralTenant:
    """Crea una empresa i un usuari nous, aillats i identificables.

    No reutilitza res: cada execucio te el seu propi tenant, de manera que dues
    execucions simultanies no es trepitgen i cap d'elles pot tocar dades demo.
    """
    marker = uuid.uuid4().hex[:12]
    legal_name = f"{EPHEMERAL_PREFIX} {label or marker}"
    email = f"smoke-{marker}@{EPHEMERAL_EMAIL_DOMAIN}"
    # Contrasenya d'un sol us: no queda escrita enlloc mes que a la sortida.
    password = f"Sm0ke-{secrets.token_urlsafe(12)}!"

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name="Usuari efimer de smoke test",
        is_active=True,
    )
    company = Company(
        legal_name=legal_name,
        trade_name=f"{EPHEMERAL_PREFIX} {marker}",
        country="ES",
        city="Girona",
        description="Tenant efimer creat per un smoke test. S'esborra en acabar.",
    )
    session.add_all([user, company])
    await session.flush()
    session.add(
        CompanyMembership(
            user_id=user.id,
            company_id=company.id,
            role=MembershipRole.OWNER,
            is_default=True,
        )
    )
    await session.commit()

    return EphemeralTenant(
        company_id=str(company.id),
        user_id=str(user.id),
        email=email,
        password=password,
        legal_name=legal_name,
    )


@dataclass(slots=True)
class DestroyReport:
    company_id: str
    legal_name: str
    rows_deleted: dict[str, int] = field(default_factory=dict)
    storage_objects_deleted: int = 0
    storage_objects_failed: list[str] = field(default_factory=list)
    leftovers: dict[str, int] = field(default_factory=dict)
    user_deleted: bool = False

    @property
    def clean(self) -> bool:
        return not self.leftovers and not self.storage_objects_failed

    @property
    def total_rows(self) -> int:
        return sum(self.rows_deleted.values())


async def destroy_tenant(
    session: AsyncSession,
    company_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    require_ephemeral: bool = True,
) -> DestroyReport:
    """Esborra un tenant sencer i comprova que no queda cap orfe.

    L'ordre importa: primer es llegeixen les claus de l'storage (despres del
    DELETE ja no es podrien saber), despres cau la fila de `companies` — que
    arrossega tot el que hi penja per `ON DELETE CASCADE` — i finalment
    s'esborren els objectes. Si un objecte no es pot esborrar queda registrat
    com a fallada, no s'amaga: mes val un orfe conegut que un de silencios.
    """
    _assert_tables_classified()
    company = (
        await session.execute(sa.select(Company).where(Company.id == company_id))
    ).scalar_one_or_none()
    if company is None:
        raise LookupError(f"No existeix cap empresa amb id {company_id}")

    if require_ephemeral and not company.legal_name.startswith(EPHEMERAL_PREFIX):
        raise ValueError(
            f"{company.legal_name!r} no es un tenant efimer (hauria de comencar per "
            f"{EPHEMERAL_PREFIX!r}). Si de veritat el vols esborrar, passa "
            "--allow-any-tenant."
        )

    before = await snapshot(session, company_id)
    storage_keys = list(before.storage_keys)

    await session.execute(sa.delete(Company).where(Company.id == company_id))

    report = DestroyReport(
        company_id=str(company_id),
        legal_name=company.legal_name,
        rows_deleted={name: count for name, count in before.rows.items() if count},
    )

    if user_id is not None:
        result = await session.execute(sa.delete(User).where(User.id == user_id))
        report.user_deleted = bool(result.rowcount)

    await session.commit()

    # --- Comprovacio: no pot quedar cap fila amb aquest company_id enlloc ---
    for name in _direct_tables():
        table = Base.metadata.tables[name]
        remaining = (
            await session.execute(
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.company_id == company_id)
            )
        ).scalar_one()
        if remaining:
            report.leftovers[name] = remaining

    still_there = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Company).where(Company.id == company_id)
        )
    ).scalar_one()
    if still_there:
        report.leftovers["companies"] = still_there

    if user_id is not None:
        remaining_user = (
            await session.execute(
                sa.select(sa.func.count()).select_from(User).where(User.id == user_id)
            )
        ).scalar_one()
        if remaining_user:
            report.leftovers["users"] = remaining_user

    # --- Objectes de l'storage: el CASCADE de PostgreSQL no els coneix ---
    storage = get_storage()
    for key in storage_keys:
        try:
            await storage.delete(key)
        except Exception:  # noqa: BLE001 - volem saber quin ha fallat, no petar
            report.storage_objects_failed.append(key)
            continue
        if await storage.exists(key):
            report.storage_objects_failed.append(key)
        else:
            report.storage_objects_deleted += 1

    return report


async def list_ephemeral(session: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    """Tenants efimers que hagin sobreviscut a una execucio interrompuda."""
    rows = (
        await session.execute(
            sa.select(Company.id, Company.legal_name)
            .where(Company.legal_name.like(f"{EPHEMERAL_PREFIX}%"))
            .order_by(Company.created_at)
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def _create(args: argparse.Namespace) -> int:
    assert_mutations_allowed(args.remote_environment)
    async with session_scope() as session:
        tenant = await create_ephemeral(session, label=args.label)
    print(tenant.as_json())
    return 0


async def _snapshot(args: argparse.Namespace) -> int:
    async with session_scope() as session:
        data = await snapshot(session, uuid.UUID(args.company_id))
    print(json.dumps(asdict(data), indent=2))
    return 0


async def _destroy(args: argparse.Namespace) -> int:
    assert_mutations_allowed(args.remote_environment)
    async with session_scope() as session:
        report = await destroy_tenant(
            session,
            uuid.UUID(args.company_id),
            user_id=uuid.UUID(args.user) if args.user else None,
            require_ephemeral=not args.allow_any_tenant,
        )
    print(json.dumps(asdict(report), indent=2))
    return 0 if report.clean else 1


async def _list(_: argparse.Namespace) -> int:
    async with session_scope() as session:
        tenants = await list_ephemeral(session)
    if not tenants:
        print("Cap tenant efimer pendent.")
        return 0
    print(f"{len(tenants)} tenant(s) efimer(s) pendents d'esborrar:")
    for company_id, name in tenants:
        print(f"  {company_id}  {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remote-environment",
        default="local",
        help="Entorn declarat pel backend (GET /health). Cal per poder mutar.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-ephemeral", help="Crea empresa i usuari efimers.")
    create.add_argument("--label", default=None)
    create.set_defaults(handler=_create)

    shot = subparsers.add_parser("snapshot", help="Compta les files d'un tenant.")
    shot.add_argument("company_id")
    shot.set_defaults(handler=_snapshot)

    destroy = subparsers.add_parser("destroy", help="Esborra un tenant i verifica la neteja.")
    destroy.add_argument("company_id")
    destroy.add_argument("--user", default=None, help="Usuari efimer a esborrar tambe.")
    destroy.add_argument(
        "--allow-any-tenant",
        action="store_true",
        help="Permet esborrar un tenant que no sigui efimer. Aneu amb compte.",
    )
    destroy.set_defaults(handler=_destroy)

    listing = subparsers.add_parser("list", help="Llista tenants efimers pendents.")
    listing.set_defaults(handler=_list)

    args = parser.parse_args(argv)
    try:
        return asyncio.run(args.handler(args))
    except ProductionGuardError as error:
        print(f"BLOQUEJAT: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
