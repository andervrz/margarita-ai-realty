# scripts/provision_tenant.py
"""Provisiona un tenant en la base de datos (PostgreSQL/Neon o SQLite).

A diferencia de scripts/seed_db.py (que además carga 35 propiedades demo),
esto SOLO crea un tenant y muestra su X-API-Key una única vez. Pensado para
preparar el entorno de producción en Neon (APP_ENV=production no tiene bypass).

Uso:
    DATABASE_URL=postgresql://...neon... \\
        uv run python scripts/provision_tenant.py \\
        --name "Esparta Inmuebles" --slug esparta-inmuebles --plan pro

    # con una API key fija en vez de generada:
    ... --api-key mi-key-secreta

    # regenerar la key de un slug existente:
    ... --slug esparta-inmuebles --force

Crea la extensión pgvector y las tablas faltantes por conveniencia; aun así,
en producción conviene haber corrido antes `alembic upgrade head`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Asegurar que src/ esté en el path cuando se corre directamente
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Provisiona un tenant y emite su API key.")
    p.add_argument("--name", required=True, help="Nombre del tenant")
    p.add_argument("--slug", required=True, help="Slug único (kebab-case)")
    p.add_argument("--plan", default="pro", choices=["basic", "standard", "pro"])
    p.add_argument("--api-key", default=None, help="API key fija (por defecto se genera)")
    p.add_argument(
        "--force", action="store_true",
        help="Regenerar la API key si el slug ya existe",
    )
    return p.parse_args()


async def main() -> None:
    args = _parse_args()

    from sqlalchemy import select, text

    from app.core.logging import setup_logging
    from app.core.security import generate_api_key, hash_api_key
    from app.db.base import Base
    from app.db.engine import AsyncSessionLocal, engine
    from app.db.models.tenant import Tenant

    setup_logging()

    print("🏝️  Provisión de tenant — Margarita AI Realty")
    print("=" * 50)
    print(f"   DB: {engine.dialect.name}")

    # Asegurar schema (extensión pgvector en Postgres + tablas faltantes)
    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    api_key = args.api_key or generate_api_key()
    now = datetime.now(timezone.utc).isoformat()

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Tenant).where(Tenant.slug == args.slug)
        )).scalar_one_or_none()

        if existing and not args.force:
            print(f"⚠️  Ya existe un tenant con slug '{args.slug}' (id={existing.id}).")
            print("   No se modificó. Usa --force para regenerar su API key.")
            await engine.dispose()
            return

        if existing:
            existing.api_key_hash = hash_api_key(api_key)
            existing.updated_at = now
            await session.commit()
            print(f"🔑 API key regenerada para '{existing.name}' (id={existing.id}).")
        else:
            tenant = Tenant(
                id=str(uuid4()),
                name=args.name,
                slug=args.slug,
                plan=args.plan,
                api_key_hash=hash_api_key(api_key),
                qualification_threshold=75,
                session_ttl_minutes=30,
                visit_duration_minutes=60,
                calendar_enabled=False,
                email_enabled=False,
                whatsapp_enabled=False,
                allowed_origins='["*"]',
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(tenant)
            await session.commit()
            print(f"✅ Tenant creado: {tenant.name} (id={tenant.id})")

    print()
    print("🔑 X-API-Key (guárdala — solo se muestra ahora):")
    print(f"   {api_key}")
    print("   Úsala en el header de cada request:  X-API-Key: <esa key>")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
