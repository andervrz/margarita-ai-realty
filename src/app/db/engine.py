# src/app/db/engine.py
"""Engine async de SQLAlchemy — SQLite (dev) o PostgreSQL (prod/Replit).

Detecta automáticamente el tipo de base de datos desde DATABASE_URL.
- SQLite: WAL mode + sqlite-vec (si disponible)
- PostgreSQL: asyncpg driver, sin extensiones especiales
"""


import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from collections.abc import AsyncGenerator
from app.core.config import get_settings

settings = get_settings()

_raw_url = os.environ.get("DATABASE_URL", settings.database_url)

# Replit provides postgresql:// — convert to asyncpg scheme
if _raw_url.startswith("postgresql://") or _raw_url.startswith("postgres://"):
    _db_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )
    # Remove sslmode param (asyncpg handles SSL differently)
    if "?sslmode=" in _db_url:
        _db_url = _db_url.split("?sslmode=")[0]
    _is_sqlite = False
elif _raw_url.startswith("sqlite"):
    _db_url = _raw_url
    _is_sqlite = True
else:
    _db_url = _raw_url
    _is_sqlite = False

engine = create_async_engine(
    _db_url,
    echo=settings.app_env == "development",
)

if _is_sqlite:
    from sqlalchemy import event
    try:
        import sqlite_vec

        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection, connection_record):
            """Callback ejecutado en cada nueva conexión SQLite."""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            dbapi_connection.enable_load_extension(True)
            sqlite_vec.load(dbapi_connection)
            dbapi_connection.enable_load_extension(False)

    except ImportError:
        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite_basic(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
