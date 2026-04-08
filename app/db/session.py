from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.base import Base

_settings = None


def _get_engine():
    global _settings
    if _settings is None:
        _settings = get_settings()
    return create_async_engine(
        _settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


async_engine = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    global async_engine, AsyncSessionLocal
    async_engine = _get_engine()
    AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

    # Import models so they register with Base metadata
    import app.models.db  # noqa: F401

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with AsyncSessionLocal() as session:
        yield session
