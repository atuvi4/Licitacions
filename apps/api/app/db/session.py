"""Gestio del motor i les sessions asincrones de SQLAlchemy."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

_connect_args: dict = {}
_engine_kwargs: dict = {"pool_pre_ping": True}

if settings.sqlalchemy_url.startswith("sqlite"):
    # SQLite nomes s'usa a la suite de tests.
    _engine_kwargs = {}

engine = create_async_engine(
    settings.sqlalchemy_url,
    echo=False,
    future=True,
    connect_args=_connect_args,
    **_engine_kwargs,
)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI: una sessio per request, amb rollback en cas d'error."""
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
