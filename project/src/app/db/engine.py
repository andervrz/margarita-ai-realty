# src/app/db/engine.py
"""Engine async de SQLAlchemy con WAL mode y sqlite-vec.

Configuración:
- WAL (Write-Ahead Logging): lecturas concurrentes sin bloquear escrituras
- sqlite-vec: extensión vectorial cargada en cada conexión
- Foreign keys: activadas por defecto
"""

from requests import session

import sqlite_vec
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


async def get_async_session():
    """Dependency injection: yield de sesión async."""
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
            # Verificar WAL mode
            result = await session.execute(text("PRAGMA journal_mode"))
            result = await session.execute(text("PRAGMA foreign_keys"))
            result = await session.execute(text("SELECT vec_version()"))
            mode = result.scalar()
            assert mode == "wal", f"WAL no activo: {mode}"
            print(f"  ✅ WAL mode activo: {mode}")
            
            # Verificar foreign keys
            result = await session.execute("PRAGMA foreign_keys")
            fk = result.scalar()
            assert fk == 1, f"FK no activas: {fk}"
            print(f"  ✅ Foreign keys activas: {fk}")
            
            # Verificar sqlite-vec cargado
            result = await session.execute("SELECT vec_version()")
            version = result.scalar()
            assert version is not None
            print(f"  ✅ sqlite-vec cargado: v{version}")
        
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())