# src/app/db/engine.py
"""Engine async de SQLAlchemy — SQLite (dev) o PostgreSQL (prod/Replit).

Detecta automáticamente el tipo de base de datos desde DATABASE_URL.
- SQLite: WAL mode (PRAGMAs). La búsqueda vectorial requiere PostgreSQL.
- PostgreSQL: asyncpg driver + pgvector. Búsqueda semántica disponible.
"""


import os
from collections.abc import AsyncGenerator
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# Parámetros de query que son específicos de libpq y que asyncpg no acepta.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding"}


def normalize_database_url(raw_url: str) -> tuple[str, bool]:
    """Normaliza DATABASE_URL al driver async correcto.

    - postgres:// | postgresql://  →  postgresql+asyncpg:// (Replit/Neon)
    - Elimina params solo-libpq (sslmode, channel_binding) que asyncpg rechaza,
      preservando el resto del query string.

    Returns:
        (url_normalizada, is_sqlite)
    """
    if raw_url.startswith("sqlite"):
        return raw_url, True

    if raw_url.startswith(("postgresql://", "postgres://")):
        url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
            "postgres://", "postgresql+asyncpg://", 1
        )
        parts = urlsplit(url)
        if parts.query:
            kept = [
                (k, v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k not in _LIBPQ_ONLY_PARAMS
            ]
            url = urlunsplit(parts._replace(query=urlencode(kept)))
        return url, False

    return raw_url, False


_db_url, _is_sqlite = normalize_database_url(
    os.environ.get("DATABASE_URL", settings.database_url)
)

engine = create_async_engine(
    _db_url,
    echo=settings.app_env == "development",
    pool_pre_ping=True,   # detecta conexiones muertas (Neon cierra idle)
    pool_recycle=300,     # recicla conexiones cada 5 min
)

if _is_sqlite:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, connection_record):
        """WAL + foreign keys en cada nueva conexión SQLite (dev).

        Nota: la búsqueda vectorial usa pgvector y solo opera sobre
        PostgreSQL; SQLite es únicamente para desarrollo sin vector search.
        """
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
