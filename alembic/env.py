# alembic/env.py
"""Configuración async de Alembic — SQLite (WAL) o PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_SRC_PATH = os.path.join(_ROOT, "src")

# Insert root so `src.app.*` imports work
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import via src.app.* so models register to the same Base as the app
from src.app.db.base import Base  # noqa: E402
from src.app.db.models.ingestion_log import IngestionLog  # noqa: E402, F401
from src.app.db.models.lead import Lead  # noqa: E402, F401
from src.app.db.models.message import Message  # noqa: E402, F401
from src.app.db.models.property import Property  # noqa: E402, F401
from src.app.db.models.session import Session  # noqa: E402, F401
from src.app.db.models.tenant import Tenant  # noqa: E402, F401

target_metadata = Base.metadata

from src.app.core.config import get_settings  # noqa: E402

_settings = get_settings()

# Build the database URL — prefer env var (Replit sets DATABASE_URL)
_raw_url = os.environ.get("DATABASE_URL", _settings.database_url)

if _raw_url.startswith("postgresql://") or _raw_url.startswith("postgres://"):
    _db_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )
    if "?sslmode=" in _db_url:
        _db_url = _db_url.split("?sslmode=")[0]
    _is_sqlite = False
else:
    _db_url = _raw_url
    _is_sqlite = True

config.set_main_option("sqlalchemy.url", _db_url)


def _set_sqlite_wal_mode(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=_is_sqlite,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    if _is_sqlite and connectable.dialect.name == "sqlite":
        from sqlalchemy import event as sa_event
        sa_event.listen(
            connectable.sync_engine,
            "connect",
            _set_sqlite_wal_mode,
        )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
