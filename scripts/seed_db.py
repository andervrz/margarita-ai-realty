# seeds/seed_database.py
"""Script de seed — carga 35 propiedades reales de Margarita en la DB de prueba.

Uso:
    uv run python seeds/seed_database.py

Crea:
    - Tenant de demo (Esparta Inmuebles)
    - 35 propiedades basadas en listados reales 2025-2026
    - Embeddings sqlite-vec para todas las propiedades
    - Ingestion log del proceso

Idempotente: si el tenant/propiedades ya existen, los actualiza.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Asegurar que src/ esté en el path cuando se corre directamente
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


DEMO_API_KEY = "demo-esparta-2025"

CSV_PATH = Path(__file__).parent / "propiedades_margarita.csv"


async def main() -> None:
    from app.core.config import get_settings
    from app.core.logging import setup_logging
    from app.core.security import generate_api_key, hash_api_key
    from app.db.base import Base
    from app.db.engine import AsyncSessionLocal, engine
    from app.db.models.tenant import Tenant
    from app.ingestion.pipeline import IngestionPipeline
    from sqlalchemy import select, event
    import sqlite_vec

    setup_logging()
    settings = get_settings()

    print("🏝️  Margarita AI Realty — Seed Database")
    print("=" * 50)

    # ── Crear tablas si no existen ────────────────────────────────
    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        dbapi_conn.enable_load_extension(True)
        sqlite_vec.load(dbapi_conn)
        dbapi_conn.enable_load_extension(False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Tablas verificadas")

    # ── Crear/actualizar tenant de demo ───────────────────────────
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Tenant).where(Tenant.slug == "esparta-inmuebles")
        )).scalar_one_or_none()

        if existing:
            tenant = existing
            print(f"✅ Tenant existente: {tenant.name}")
        else:
            from datetime import datetime, timezone
            from uuid import uuid4

            now = datetime.now(timezone.utc).isoformat()
            tenant = Tenant(
                id=str(uuid4()),
                name="Esparta Inmuebles",
                slug="esparta-inmuebles",
                plan="pro",
                api_key_hash=hash_api_key(DEMO_API_KEY),
                qualification_threshold=75,
                session_ttl_minutes=30,
                visit_duration_minutes=60,
                calendar_enabled=True,
                email_enabled=True,
                whatsapp_enabled=True,
                whatsapp_phone_id="5804129876543",
                agent_email="contacto@espartainmuebles.com",
                agent_whatsapp="+584129876543",
                allowed_origins='["*"]',
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(tenant)
            await session.commit()
            print(f"✅ Tenant creado: {tenant.name}")
            print(f"   API Key: {DEMO_API_KEY}")

    # ── Cargar CSV de propiedades ─────────────────────────────────
    if not CSV_PATH.exists():
        print(f"❌ CSV no encontrado: {CSV_PATH}")
        print("   Ejecutar primero: genera el CSV con las propiedades")
        sys.exit(1)

    csv_content = CSV_PATH.read_bytes()
    print(f"\n📄 Cargando CSV: {CSV_PATH.name} ({len(csv_content)} bytes)")

    # ── Ejecutar pipeline ─────────────────────────────────────────
    pipeline = IngestionPipeline()

    async with AsyncSessionLocal() as session:
        result = await pipeline.process_csv(
            session=session,
            tenant_id=tenant.id,
            file_content=csv_content,
            filename=CSV_PATH.name,
        )

    print(f"\n📊 Resultado del pipeline:")
    print(f"   Total filas:    {result.total_rows}")
    print(f"   Válidas:        {result.valid_rows}")
    print(f"   Insertadas:     {result.inserted_rows}")
    print(f"   Actualizadas:   {result.updated_rows}")
    print(f"   Omitidas:       {result.skipped_rows}")
    print(f"   Fallidas:       {result.failed_rows}")
    print(f"   Estado:         {result.status}")

    if result.errors:
        print(f"\n⚠️  Errores ({len(result.errors)}):")
        for err in result.errors[:5]:
            print(f"   - {err}")

    print(f"\n✅ Seed completado")
    print(f"\n🚀 Para probar el chatbot:")
    print(f"   1. uv run uvicorn app.main:app --reload --port 8000")
    print(f"   2. Abrir demo/index.html en el browser")
    print(f"   3. API Key para pruebas: {DEMO_API_KEY}")
    print(f"\n📡 WebSocket: ws://localhost:8000/api/v1/ws/chat/{{session_id}}")
    print(f"📮 POST:      http://localhost:8000/api/v1")
    print(f"📚 Docs:      http://localhost:8000/docs")


if __name__ == "__main__":
    asyncio.run(main())
