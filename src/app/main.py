# src/app/main.py
"""FastAPI application factory — punto de entrada de la aplicación.

Responsabilidades:
    1. Crear instancia FastAPI con metadata y lifespan
    2. Configurar middleware stack (CORS → RateLimit → Tenant)
    3. Montar routers de API v1
    4. Inicializar logging estructurado (structlog)
    5. Registrar exception handlers del dominio
    6. Gestionar lifecycle: startup → shutdown

Lifespan (startup):
    - setup_logging(): configura structlog
    - Alembic head check: verifica que las tablas existen
    - Session cleanup background task: limpia sesiones expiradas por TTL

Lifespan (shutdown):
    - Cancela tareas de background
    - Cierra el engine de SQLAlchemy

Middleware stack (orden de ejecución — de afuera hacia adentro):
    1. CORSMiddleware      → allow_origins (dev: wildcard)
    2. RateLimitMiddleware → protección contra abuso por IP/tenant
    3. TenantMiddleware    → resuelve tenant desde X-API-Key

En desarrollo (app_env=development):
    - API key omitida → DEV_TENANT hardcodeado
    - CORS wildcard
    - Sin rate limiting
    - Docs en /docs y /redoc habilitados
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.app.api.middleware import RateLimitMiddleware, TenantMiddleware
from src.app.api.v1.router import api_v1_router
from src.app.chat.memory import cleanup_expired_sessions
from src.app.core.config import get_settings
from src.app.core.logging import get_logger, setup_logging
from src.app.exceptions import DomainError, domain_exception_handler

logger = get_logger(__name__)


# ── Lifespan Manager ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Ciclo de vida de la aplicación.

    Startup:
      1. setup_logging()   → structlog configurado
      2. DB init check     → verifica que las tablas existen
      3. Cleanup task      → background task para sesiones TTL

    Shutdown:
      1. Cancela tareas de background
      2. Dispone el engine de SQLAlchemy
    """
    settings = get_settings()

    # ── Startup ───────────────────────────────────────────────────
    setup_logging()

    logger.info(
        "app_startup",
        env=settings.app_env,
        version="1.2.0",
        service="margarita-ai-realty",
    )

    # Verificar que las tablas existen (Alembic debe haber corrido)
    await _check_db_tables()

    # Background task — limpieza de sesiones expiradas por TTL
    cleanup_task = asyncio.create_task(
        cleanup_expired_sessions(),  # Lee settings internamente
        name="session_cleanup",
    )

    logger.info(
        "session_cleanup_task_started",
        ttl_minutes=settings.session_ttl_minutes,
        interval_seconds=settings.session_cleanup_interval_seconds,
    )

    yield

    # ── Shutdown ──────────────────────────────────────────────────
    logger.info("app_shutdown")

    if not cleanup_task.done():
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    # Disponer engine SQLAlchemy — cierra pool de conexiones
    from src.app.db.engine import engine
    await engine.dispose()

    logger.info("app_shutdown_complete")


# ── DB Init Check ─────────────────────────────────────────────────

async def _check_db_tables() -> None:
    """Verifica que las tablas principales existen en DB.

    No crea tablas — eso es responsabilidad de Alembic.
    Solo verifica en startup para detectar configuración incorrecta.
    """
    from sqlalchemy import text
    from src.app.db.engine import engine

    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='tenants'")
            )
            exists = result.scalar_one_or_none()
            if not exists:
                logger.warning(
                    "db_tables_not_found",
                    hint="Run: uv run alembic upgrade head",
                )
            else:
                logger.info("db_tables_ok")
    except Exception as exc:
        logger.warning(
            "db_check_failed",
            error=str(exc),
            hint="Run: uv run alembic upgrade head",
        )


# ── App Factory ───────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Crea y configura la instancia de FastAPI.

    Returns:
        FastAPI configurada con middleware, routers, exception handlers
        y lifespan.
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="1.2.0",
        description=(
            "Chatbot conversacional para inmobiliarias "
            "en Isla de Margarita, Venezuela"
        ),
        # Docs solo en desarrollo
        docs_url="/docs" if settings.app_env == "development" else None,
        redoc_url="/redoc" if settings.app_env == "development" else None,
        openapi_url=(
            "/openapi.json" if settings.app_env == "development" else None
        ),
        lifespan=lifespan,
    )

    # ── Exception Handlers ────────────────────────────────────────
    # DomainError → JSONResponse con status_code y detail correctos
    app.add_exception_handler(DomainError, domain_exception_handler)

    # ── Middleware Stack ──────────────────────────────────────────
    # IMPORTANTE: FastAPI aplica middleware en orden inverso al que
    # se añaden — el último en añadirse es el primero en ejecutarse.
    # Para el orden deseado CORS → RateLimit → Tenant:
    #   add TenantMiddleware primero, CORSMiddleware último.

    # 3. Tenant (se ejecuta último de los tres — más cercano al endpoint)
    app.add_middleware(TenantMiddleware)

    # 2. Rate Limiting
    app.add_middleware(RateLimitMiddleware)

    # 1. CORS (se ejecuta primero — capa más externa)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.app_env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "X-API-Key", "X-Session-Id"],
        expose_headers=["X-Session-Id"],
    )

    # ── Routers ───────────────────────────────────────────────────
    # api_v1_router ya incluye chat.router que tiene:
    #   - @router.websocket("/ws/chat/{session_id}")
    #   - @router.post("")  (POST fallback)
    # No registrar websocket_chat por separado — se duplicaría.
    app.include_router(api_v1_router, prefix="/api/v1")

    # ── Root Endpoints ────────────────────────────────────────────

    @app.get("/", tags=["root"], include_in_schema=False)
    async def root() -> dict:
        """Endpoint raíz — verifica que el servicio está activo."""
        s = get_settings()
        return {
            "service": s.app_name,
            "version": "1.2.0",
            "status": "running",
            "env": s.app_env,
        }

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        """Health check para load balancers y uptime monitors."""
        return {"status": "ok", "service": "margarita-ai-realty"}

    return app


# ── Instancia Global ──────────────────────────────────────────────
# Importada por uvicorn:
#   uvicorn src.app.main:app --reload --port 8000
#
# O con el string de módulo según pyproject.toml pythonpath:
#   uvicorn app.main:app --reload --port 8000

app = create_app()


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import inspect

    print("🔥 Smoke Tests — app/main.py\n")

    settings = get_settings()

    # Test 1: create_app retorna FastAPI instance
    test_app = create_app()
    assert isinstance(test_app, FastAPI)
    print("✅ create_app retorna FastAPI instance")

    # Test 2: Title y versión correctos
    assert test_app.title == settings.app_name
    assert test_app.version == "1.2.0"
    print(f"✅ App title: {test_app.title} v{test_app.version}")

    # Test 3: Rutas principales registradas
    routes = [r.path for r in test_app.routes if hasattr(r, "path")]
    assert "/" in routes, f"/ no encontrado. Rutas: {routes}"
    assert "/health" in routes, f"/health no encontrado. Rutas: {routes}"
    # WebSocket registrado dentro de api_v1_router → chat.router
    ws_routes = [r.path for r in test_app.routes if hasattr(r, "path") and "ws" in r.path]
    print(f"✅ Rutas registradas: {len(routes)} total")
    if ws_routes:
        print(f"   WebSocket routes: {ws_routes}")

    # Test 4: Exception handler registrado para DomainError
    exception_handlers = test_app.exception_handlers
    assert DomainError in exception_handlers, \
        "DomainError handler no registrado"
    print("✅ DomainError exception handler registrado")

    # Test 5: Settings usa snake_case — no SCREAMING_SNAKE
    assert hasattr(settings, "app_env"), "settings.app_env debe existir"
    assert hasattr(settings, "app_name"), "settings.app_name debe existir"
    assert hasattr(settings, "session_ttl_minutes"), "settings.session_ttl_minutes debe existir"
    assert not hasattr(settings, "APP_ENV"), "settings.APP_ENV no debe existir"
    print(f"✅ Settings snake_case: app_env={settings.app_env}")

    # Test 6: Lifespan es async context manager
    assert inspect.isasyncgenfunction(lifespan)
    print("✅ Lifespan es async context manager")

    # Test 7: cleanup_expired_sessions no recibe argumentos
    assert inspect.iscoroutinefunction(cleanup_expired_sessions)
    sig = inspect.signature(cleanup_expired_sessions)
    assert len(sig.parameters) == 0, \
        "cleanup_expired_sessions no debe recibir argumentos — lee settings internamente"
    print("✅ cleanup_expired_sessions sin argumentos (lee settings internamente)")

    # Test 8: Middleware en orden correcto
    # FastAPI invierte el orden — el primero en ejecutarse es el último en añadirse
    # Verificamos que los tres middleware están presentes
    middleware_types = [
        type(m.cls).__name__ if hasattr(m, "cls") else type(m).__name__
        for m in test_app.user_middleware
    ]
    middleware_names = str(middleware_types)
    assert "CORSMiddleware" in middleware_names
    assert "RateLimitMiddleware" in middleware_names
    assert "TenantMiddleware" in middleware_names
    print(f"✅ Middleware stack completo: {len(test_app.user_middleware)} middlewares")

    # Test 9: Docs disponibles solo en development
    if settings.app_env == "development":
        assert test_app.docs_url == "/docs"
        assert test_app.redoc_url == "/redoc"
        print("✅ Docs habilitados en development")
    else:
        assert test_app.docs_url is None
        assert test_app.redoc_url is None
        print("✅ Docs deshabilitados en producción")

    # Test 10: app global es instancia de FastAPI
    assert isinstance(app, FastAPI)
    print("✅ Instancia global 'app' disponible para uvicorn")

    print("\n🎉 Todos los smoke tests pasaron ✅")
    print("\n   Para ejecutar el servidor:")
    print("   uv run uvicorn src.app.main:app --reload --port 8000")
