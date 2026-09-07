"""Configuracio de la suite de tests.

Els tests corren sobre SQLite en memoria (cap dependencia de Docker) i amb el
backend d'storage local en un directori temporal. Aixo es possible perque el
model nomes fa servir tipus SQLAlchemy portables.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

# Ha d'anar ABANS d'importar res de `app`: la configuracio es cacheja.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-in-production")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.identity import Company, CompanyMembership, User  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.enums import MembershipRole  # noqa: E402
from app.services.storage import LocalFilesystemStorage, set_storage  # noqa: E402

TEST_PASSWORD = "TestPassw0rd!"


@pytest.fixture
async def engine():
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


@pytest.fixture(autouse=True)
def storage(tmp_path):
    backend = LocalFilesystemStorage(tmp_path / "storage")
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
