# alembic/env.py
"""Configuración async de Alembic para migraciones de SQLite con WAL mode.

Estructura del proyecto (relativa a este archivo):
    alembic/
        env.py          ← este archivo
    src/
        app/
            db/
                base.py
                models/

Comandos:
    uv run alembic revision --autogenerate -m "descripcion"
    uv run alembic upgrade head
    uv run alembic downgrade -1
    uv run alembic current
    uv run alembic history

El path a src/ se resuelve automáticamente si pyproject.toml
tiene pythonpath = ["src"] en [tool.pytest.ini_options].
Para alembic fuera de pytest, sys.path se configura aquí.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import event, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Path Setup ────────────────────────────────────────────────────
# Añade src/ al path para que los imports `from app.X import Y` funcionen.
# alembic/ está al mismo nivel que src/:
#   proyecto/
#     alembic/env.py   ← aquí estamos
#     src/app/         ← necesitamos esto

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_PATH = os.path.normpath(os.path.join(_HERE, "..", "src"))
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

# ── Alembic Config ────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Importar modelos para autogenerate ───────────────────────────
# TODOS los modelos deben importarse aquí para que Alembic detecte
# cambios de schema en `revision --autogenerate`.
# Importar solo Base no es suficiente — los modelos deben ejecutarse
# para registrarse en Base.metadata.

from app.db.base import Base  # noqa: E402

# Importar todos los modelos — orden no importa para metadata
from app.db.models.ingestion_log import IngestionLog  # noqa: E402, F401
from app.db.models.lead import Lead  # noqa: E402, F401
from app.db.models.message import Message  # noqa: E402, F401
from app.db.models.property import Property  # noqa: E402, F401
from app.db.models.session import Session  # noqa: E402, F401
from app.db.models.tenant import Tenant  # noqa: E402, F401

target_metadata = Base.metadata

# ── Database URL ──────────────────────────────────────────────────
# Leer desde Settings (que a su vez lee .env)
from app.core.config import get_settings  # noqa: E402

_settings = get_settings()
config.set_main_option("sqlalchemy.url", _settings.database_url)


# ── SQLite WAL Mode ───────────────────────────────────────────────

def _set_sqlite_wal_mode(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Activa WAL mode al abrir cada conexión SQLite.

    WAL permite lecturas concurrentes sin bloquear escrituras —
    crítico para FastAPI async con múltiples workers.
    Sin esto, SQLite usa DELETE mode que bloquea todo en cada write.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# ── Migraciones Offline ───────────────────────────────────────────

def run_migrations_offline() -> None:
    """Genera SQL de migraciones sin conectar a DB.

    Usa: alembic upgrade head --sql > migration.sql
    Útil para revisar cambios antes de aplicar o para entornos
    donde no se tiene acceso directo a la DB.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite no soporta ALTER COLUMN — batch emula con CREATE+INSERT+DROP
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ── Migraciones Online ────────────────────────────────────────────

def do_run_migrations(connection: Connection) -> None:
    """Ejecuta migraciones sobre una conexión síncrona.

    Alembic requiere conexión síncrona aunque usemos engine async.
    `run_sync()` ejecuta esta función en el contexto sync de SQLAlchemy.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Batch mode requerido para SQLite — emula ALTER TABLE
        render_as_batch=True,
        # Comparar tipos de columna para detectar cambios de tipo
        compare_type=True,
        # Comparar default values de columnas
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Ejecuta migraciones en modo online con engine async.

    Flujo:
      1. Crea engine async (NullPool para Alembic — sin connection pooling)
      2. Registra WAL mode event listener
      3. Conecta y ejecuta migraciones via run_sync
      4. Dispone el engine
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # NullPool: Alembic crea/destruye conexiones, no necesita pool
        poolclass=pool.NullPool,
    )

    # Registrar WAL mode para SQLite
    # El event listener solo tiene efecto en SQLite — PostgreSQL lo ignora
    if connectable.dialect.name == "sqlite":
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
    """Entry point para migraciones online — invocado por Alembic CLI."""
    asyncio.run(run_async_migrations())


# ── Entry Point ───────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
