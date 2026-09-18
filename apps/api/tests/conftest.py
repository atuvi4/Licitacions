"""Configuracio de la suite de tests.

Per defecte els tests corren sobre SQLite en memoria (cap dependencia de Docker)
i amb el backend d'storage local en un directori temporal. Aixo es possible
perque el model nomes fa servir tipus SQLAlchemy portables.

Amb `--postgres` corren contra PostgreSQL, i nomes contra la base de test
(`TEST_DATABASE_URL`, que ha de ser `licitacions_test`): cada test en un schema
propi que es crea i s'esborra al seu voltant. Les regles son a `pg_harness.py`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

# La base de desenvolupament de qui executa els tests, si n'hi ha. Nomes es
# guarda per poder-la REFUSAR: els tests no hi poden apuntar mai.
_DEVELOPER_DATABASE_URL = os.environ.get("DATABASE_URL")

# Ha d'anar ABANS d'importar res de `app`: la configuracio es cacheja.
# L'aplicacio dels tests no ha de veure MAI una base real, encara que l'entorn
# de qui els executa en tingui una configurada: per aixo es sobreescriu, no es
# posa per defecte. Els tests treballen amb el motor de la fixture `engine`.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-in-production")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import MembershipRole  # noqa: E402
from app.models.identity import Company, CompanyMembership, User  # noqa: E402
from app.services.storage import LocalFilesystemStorage, set_storage  # noqa: E402
from tests.pg_harness import (  # noqa: E402
    DEFAULT_TEST_DATABASE,
    UnsafeTestDatabaseError,
    isolated_schema_engine,
    validate_test_database_url,
    verify_test_database,
)

TEST_PASSWORD = "TestPassw0rd!"

#: La DSN de la base de test ja validada. Nomes existeix amb `--postgres`.
PG_TEST_URL = pytest.StashKey[object]()


def pytest_addoption(parser):
    parser.addoption(
        "--postgres",
        action="store_true",
        default=False,
        help="Executa la suite contra PostgreSQL (TEST_DATABASE_URL, nomes licitacions_test).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "postgres: nomes te sentit contra PostgreSQL; s'omet sense --postgres"
    )
    if not config.getoption("--postgres"):
        return
    expected = os.environ.get("TEST_DATABASE_NAME", DEFAULT_TEST_DATABASE)
    try:
        url = validate_test_database_url(
            os.environ.get("TEST_DATABASE_URL"),
            expected=expected,
            developer_urls=[_DEVELOPER_DATABASE_URL],
            forbidden=[settings.POSTGRES_DB],
        )
        # Nomes lectura: confirma la base abans que cap test hi pugui escriure.
        asyncio.run(verify_test_database(url, expected))
    except UnsafeTestDatabaseError as exc:
        raise pytest.UsageError(f"PostgreSQL de test refusat: {exc}") from None
    except Exception as exc:  # connexio impossible: fallar, mai caure a SQLite
        raise pytest.UsageError(
            f"No es pot connectar a la base de test PostgreSQL: {type(exc).__name__}: {exc}"
        ) from None
    config.stash[PG_TEST_URL] = (url, expected)


def pytest_collection_modifyitems(config, items):
    if config.getoption("--postgres"):
        return
    skip = pytest.mark.skip(reason="nomes amb --postgres")
    for item in items:
        if item.get_closest_marker("postgres") is not None:
            item.add_marker(skip)


@pytest.fixture
async def engine(request):
    pg = request.config.stash.get(PG_TEST_URL, None)
    if request.config.getoption("--postgres"):
        # Sense cap cami de retorn a SQLite: si s'ha demanat PostgreSQL i no hi
        # ha una base validada, el test falla.
        assert pg is not None, "--postgres sense base de test validada"
        url, expected = pg
        async with isolated_schema_engine(
            url, expected=expected, metadata=Base.metadata
        ) as pg_engine:
            yield pg_engine
        return

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignora les claus foranes si no s'activa el pragma a cada connexio.
    # Sense aixo, `ON DELETE CASCADE` no faria res i els tests donarien per bo
    # un esborrat que a PostgreSQL si que arrossega les files filles (o que,
    # pitjor, en deixaria d'orfes sense que ningu se n'adones).
    @sa.event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
async def session(db) -> AsyncSession:
    """Alies de `db`. Els tests de la capa documental parlen de `session`."""
    return db


@pytest.fixture
async def tender_document(session):
    """Un TenderDocument minim, amb la seva Source i el seu Tender.

    Els plecs son globals: aquesta fixture NO crea cap Company.
    """
    from app.models.enums import TenderDocumentType
    from app.models.sources import Source
    from app.models.tenders import Tender, TenderDocument

    source = Source(code=f"test-{uuid.uuid4().hex[:8]}", name="Font de prova")
    session.add(source)
    await session.flush()

    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        title="Servei de manteniment integral dels equipaments municipals",
    )
    session.add(tender)
    await session.flush()

    document = TenderDocument(
        tender_id=tender.id,
        name="PCAP.pdf",
        document_type=TenderDocumentType.OTHER,
        url="https://example.com/pcap.pdf",
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


@pytest.fixture
async def second_tender_document(session, tender_document):
    """Un altre TenderDocument, del mateix Tender, amb una URL diferent."""
    from app.models.enums import TenderDocumentType
    from app.models.tenders import TenderDocument

    document = TenderDocument(
        tender_id=tender_document.tender_id,
        name="PCAP-copia.pdf",
        document_type=TenderDocumentType.OTHER,
        url="https://example.com/altra-ruta/pcap-copia.pdf",
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


class CountingStorage(LocalFilesystemStorage):
    """Storage local que porta el compte de les pujades.

    Serveix per demostrar una absencia: que deduplicar per SHA-256 evita de
    veritat una segona pujada, no nomes una segona fila a la base de dades.
    """

    def __init__(self, root):
        super().__init__(root)
        self.uploads: list[str] = []

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        self.uploads.append(key)
        await super().upload(key, data, content_type)


# Segueix sent autouse: tots els tests han de tenir un backend temporal, tant si
# el demanen com si no. Els que el demanen, a mes, en poden comptar les pujades.
@pytest.fixture(autouse=True)
def storage(tmp_path):
    backend = CountingStorage(tmp_path / "storage")
    set_storage(backend)
    yield backend
    set_storage(None)


@pytest.fixture
async def client_factory(session_factory):
    """Permet obrir diversos clients independents (cookies separades) contra la mateixa BD."""

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    opened: list[AsyncClient] = []

    def _make() -> AsyncClient:
        http_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        opened.append(http_client)
        return http_client

    yield _make

    for http_client in opened:
        await http_client.aclose()
    app.dependency_overrides.clear()


@pytest.fixture
async def client(client_factory) -> AsyncClient:
    return client_factory()


async def create_user_with_company(
    session: AsyncSession, *, email: str, company_name: str
) -> tuple[User, Company]:
    user = User(
        email=email,
        hashed_password=hash_password(TEST_PASSWORD),
        full_name="Test User",
        is_active=True,
    )
    company = Company(legal_name=company_name, country="ES")
    session.add_all([user, company])
    await session.flush()
    session.add(
        CompanyMembership(
            user_id=user.id, company_id=company.id, role=MembershipRole.OWNER, is_default=True
        )
    )
    await session.commit()
    return user, company


async def satisfy_final_gate(db, workspace, user) -> None:
    """El que S6 afegeix al cami real cap a la revisio final.

    Des d'S6, "a punt" ja no es nomes el dossier: tambe cal una oferta economica
    amb preu (C-15) i que algu hagi revisat quins requisits necessiten solucio
    tecnica (C-14). Els tests que porten una candidatura fins al final han de
    passar per aqui, com hi passaria una persona.
    """
    from decimal import Decimal

    from app.models.enums import PricingBasis
    from app.services import economic_offer, technical_scope

    await economic_offer.create_offer(db, workspace, user=user)
    # Es torna a llegir amb `get_offer`: el que retorna `create_offer` no porta
    # les colleccions carregades i tocar-les aqui seria una carrega mandrosa.
    offer = await economic_offer.get_offer(db, workspace)
    lot = offer.lots[0]
    section = await economic_offer.create_section(
        db, offer, lot, title="Oferta", expected_revision=offer.revision, user=user
    )
    await db.refresh(offer)
    await economic_offer.create_line(
        db,
        workspace,
        offer,
        section,
        expected_revision=offer.revision,
        user=user,
        concept="Servei",
        quantity=Decimal("1"),
        pricing_basis=PricingBasis.ONE_OFF,
        offered_unit_price_ex_vat=Decimal("1000"),
        vat_rate=Decimal("21"),
    )
    # L'abast tecnic es revisa l'ULTIM: confirmar-lo abans de crear res deixaria
    # l'empremta enrere i la revisio quedaria caducada tot seguit.
    await technical_scope.confirm(db, workspace, user=user)


@pytest.fixture
async def tenant_a(db) -> tuple[User, Company]:
    return await create_user_with_company(
        db, email=f"a-{uuid.uuid4().hex[:8]}@empresa-a.com", company_name="Empresa A"
    )


@pytest.fixture
async def tenant_b(db) -> tuple[User, Company]:
    return await create_user_with_company(
        db, email=f"b-{uuid.uuid4().hex[:8]}@empresa-b.com", company_name="Empresa B"
    )


async def login(client: AsyncClient, email: str, password: str = TEST_PASSWORD) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


@pytest.fixture
async def client_a(client_factory, tenant_a) -> AsyncClient:
    user, _ = tenant_a
    http_client = client_factory()
    await login(http_client, user.email)
    return http_client


@pytest.fixture
async def client_b(client_factory, tenant_b) -> AsyncClient:
    user, _ = tenant_b
    http_client = client_factory()
    await login(http_client, user.email)
    return http_client
