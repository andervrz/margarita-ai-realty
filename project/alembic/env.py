# project/alembic/env.py
"""Configuración async de Alembic para migraciones de SQLite.

Este archivo configura el entorno de Alembic para trabajar con:
    - SQLAlchemy 2.0 async (create_async_engine, AsyncSession)
    - SQLite con WAL mode + sqlite-vec
    - Modelos declarativos de app.db.models

Comandos útiles:
    alembic revision --autogenerate -m "descripcion"
    alembic upgrade head
    alembic downgrade -1
    alembic current
    alembic history
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Path setup ────────────────────────────────────────────────────
# Asegurar que app/ esté en el path para importar modelos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "project", "src"))

# ── Configuración de Alembic ──────────────────────────────────────
config = context.config

# Interpretar el archivo de config de logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Metadata de modelos ────────────────────────────────────────────
from app.db.base import Base
from app.db.models.tenats import Tenant
from app.db.models.property import Property
from app.db.models.session import Session
from app.db.models.messages import Message
from app.db.models.lead import Lead
from app.db.models.ingestion import IngestionLog

target_metadata = Base.metadata

# ── URL de la base de datos ───────────────────────────────────────
from app.core.config import settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


# ── Funciones de migración ─────────────────────────────────────────

def run_migrations_offline() -> None:
    """Ejecuta migraciones en modo offline (genera SQL sin conectar a DB).
    
    Usado con: alembic upgrade head --sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Ejecuta las migraciones sobre una conexión síncrona.
    
    Alembic requiere conexión síncrona incluso con engine async.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite no soporta ALTER COLUMN; render_as_batch lo emula
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Ejecuta migraciones en modo online (conectado a DB).
    
    Crea un engine async, obtiene conexión síncrona via run_sync,
    y ejecuta las migraciones.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point para migraciones online."""
    asyncio.run(run_async_migrations())


# ── Entry point ────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()