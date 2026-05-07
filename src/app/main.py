# project/src/app/main.py
"""FastAPI application factory — punto de entrada de la aplicación.

Responsabilidades:
    1. Crear instancia FastAPI con metadata y lifespan
    2. Configurar middleware stack (CORS → RateLimit → Tenant)
    3. Montar routers de API v1
    4. Inicializar logging estructurado (structlog)
    5. Gestionar lifecycle: startup (DB, cleanup task) → shutdown (cleanup)

Lifespan:
    - Startup: setup_logging() + session cleanup background task
    - Shutdown: cancela tareas pendientes, cierra conexiones

Middleware stack (orden de ejecución):
    1. CORSMiddleware      → allow_origins por tenant (dev: wildcard)
    2. RateLimitMiddleware → protección contra abuso
    3. TenantMiddleware    → resuelve tenant desde X-API-Key

Nota: En desarrollo (APP_ENV=development) se omite validación de API key
y se usa DEV_TENANT hardcodeado para eliminar fricción.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.app.api.middleware import RateLimitMiddleware, TenantMiddleware
from src.app.api.v1.router import api_v1_router
from src.app.chat.memory import cleanup_expired_sessions
from src.app.core.config import settings
from src.app.core.logging import logger, setup_logging


# ── Lifespan Manager ───────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gestiona el ciclo de vida de la aplicación.
    
    Startup:
        - Configura logging estructurado
        - Inicia background task de limpieza de sesiones expiradas
    
    Shutdown:
        - Cancela tareas de background
        - Limpia recursos
    """
    # ── Startup ────────────────────────────────────────────────────
    setup_logging()
    logger.info("app_startup", env=settings.APP_ENV, version="1.2.0")

    # Iniciar background task de cleanup de sesiones
    cleanup_task = asyncio.create_task(
        cleanup_expired_sessions(settings.SESSION_TTL_MINUTES),
        name="session_cleanup",
    )
    logger.info("session_cleanup_task_started", ttl_minutes=settings.SESSION_TTL_MINUTES)

    yield

    # ── Shutdown ───────────────────────────────────────────────────
    logger.info("app_shutdown")
    
    if not cleanup_task.done():
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
    logger.info("session_cleanup_task_stopped")


# ── App Factory ────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Crea y configura la instancia de FastAPI.
    
    Returns:
        FastAPI: Aplicación configurada con middleware, routers y lifespan.
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.2.0",
        description="Chatbot conversacional para inmobiliarias en Isla de Margarita, Venezuela",
        docs_url="/docs" if settings.APP_ENV == "development" else None,
        redoc_url="/redoc" if settings.APP_ENV == "development" else None,
        openapi_url="/openapi.json" if settings.APP_ENV == "development" else None,
        lifespan=lifespan,
    )

    # ── Middleware Stack ─────────────────────────────────────────
    
    # 1. CORS — permite requests desde dominios del cliente
    # En dev: wildcard. En prod: se valida por tenant en TenantMiddleware.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.APP_ENV == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Session-Id"],
    )
    logger.debug("middleware_cors_added")

    # 2. Rate Limiting — protección contra abuso
    app.add_middleware(RateLimitMiddleware)
    logger.debug("middleware_rate_limit_added")

    # 3. Tenant Resolution — autenticación y aislamiento
    app.add_middleware(TenantMiddleware)
    logger.debug("middleware_tenant_added")

    # ── Routers ────────────────────────────────────────────────────
    
    # API v1 — todos los endpoints bajo /api/v1
    app.include_router(api_v1_router, prefix="/api/v1")
    logger.debug("router_api_v1_mounted")

    # WebSocket — se registra directamente (no via include_router)
    # El endpoint /ws/chat/{session_id} está en api/v1/chat.py
    from src.app.api.v1.chat import websocket_chat
    app.add_websocket_route("/ws/chat/{session_id}", websocket_chat)
    logger.debug("websocket_route_added")

    # ── Root Endpoint ──────────────────────────────────────────────
    
    @app.get("/", tags=["root"])
    async def root() -> dict:
        """Endpoint raíz — verifica que el servicio está activo."""
        return {
            "service": settings.APP_NAME,
            "version": "1.2.0",
            "status": "running",
            "env": settings.APP_ENV,
        }

    # ── Health Check (también en /api/v1/health via router) ───────
    
    @app.get("/health", tags=["health"])
    async def health() -> dict:
        """Health check simple para monitoreo externo."""
        return {"status": "ok", "service": "margarita-ai-realty"}

    logger.info("app_created", routers=["/api/v1/*", "/ws/chat/{session_id}", "/", "/health"])
    return app


# ── Instancia global ───────────────────────────────────────────────
# Se importa por uvicorn: uvicorn app.main:app
app = create_app()


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    print("🔥 Smoke Test — app/main.py")

    # Test 1: create_app retorna FastAPI instance
    test_app = create_app()
    assert isinstance(test_app, FastAPI)
    print("  ✅ create_app retorna FastAPI instance")

    # Test 2: App tiene título y versión
    assert test_app.title == settings.APP_NAME
    assert "1.2.0" in str(test_app.version) or test_app.version == "1.2.0"
    print(f"  ✅ App title: {test_app.title}")

    # Test 3: Routers montados
    routes = [r.path for r in test_app.routes if hasattr(r, "path")]
    assert "/api/v1/health" in routes or "/health" in routes
    assert "/ws/chat/{session_id}" in routes
    assert "/" in routes
    print(f"  ✅ Rutas registradas: {len(routes)} endpoints")

    # Test 4: Middleware stack configurado
    middleware_classes = [type(m).__name__ for m in test_app.user_middleware]
    assert "CORSMiddleware" in middleware_classes
    assert "RateLimitMiddleware" in middleware_classes
    assert "TenantMiddleware" in middleware_classes
    print(f"  ✅ Middleware stack: {middleware_classes}")

    # Test 5: Settings cargan correctamente
    assert settings.APP_ENV in ("development", "production", "testing")
    assert settings.SESSION_TTL_MINUTES > 0
    assert settings.DEFAULT_VISIT_DURATION_MINUTES > 0
    print(f"  ✅ Settings: env={settings.APP_ENV}, ttl={settings.SESSION_TTL_MINUTES}min")

    # Test 6: Lifespan es callable
    import inspect
    assert inspect.isasyncgenfunction(lifespan)
    print("  ✅ Lifespan es async context manager")

    print("\n🎉 Todos los smoke tests pasaron")
    print("   Para ejecutar: uvicorn app.main:app --reload --port 8000")