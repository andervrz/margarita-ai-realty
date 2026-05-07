# src/app/db/engine.py
"""Engine async de SQLAlchemy con WAL mode y sqlite-vec.

Configuración:
- WAL (Write-Ahead Logging): lecturas concurrentes sin bloquear escrituras
- sqlite-vec: extensión vectorial cargada en cada conexión
- Foreign keys: activadas por defecto
"""


import sqlite_vec
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from collections.abc import AsyncGenerator
from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    """Callback ejecutado en cada nueva conexión SQLite."""
    cursor = dbapi_connection.cursor()
    
    # WAL mode: lecturas concurrentes sin bloquear escrituras
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    
    cursor.close()
    
    # Cargar extensión sqlite-vec para búsqueda vectorial
    dbapi_connection.enable_load_extension(True)
    sqlite_vec.load(dbapi_connection)
    dbapi_connection.enable_load_extension(False)

async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from sqlalchemy import text

    
    async def _test():
        print("🔥 Smoke Test — db/engine.py")
        
        # Test engine creado
        assert engine is not None
        print("  ✅ Engine async creado")
        
        # Test AsyncSessionLocal
        assert AsyncSessionLocal is not None
        print("  ✅ Session maker configurado")


        # Test conexión real
        async with AsyncSessionLocal() as session:
            wal = (await session.execute(text("PRAGMA journal_mode"))).scalar()
            fk = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
            vec = (await session.execute(text("SELECT vec_version()"))).scalar()
    
            assert wal == "wal", f"WAL no activo: {wal}"
            assert fk == 1, f"FK no activas: {fk}"
            assert vec is not None
            
            print(f"  ✅ WAL mode: {wal}")
            print(f"  ✅ Foreign keys: {fk}")
            print(f"  ✅ sqlite-vec: v{vec}")
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())