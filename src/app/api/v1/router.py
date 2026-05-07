# project/src/app/api/v1/router.py
"""Aggregator de routers v1 — monta todos los endpoints bajo /api/v1.

Estructura:
    /api/v1/chat        → WebSocket + POST fallback (conversación)
    /api/v1/ingestion   → CSV upload + pipeline de procesamiento
    /api/v1/properties  → Admin: listar, buscar, ver propiedades
    /api/v1/leads       → Admin: listar leads, actualizar estado

Cada router se monta con su propio prefix. Este archivo es el único
punto donde se ensamblan todos los routers de la API.

Uso en main.py:
    from app.api.v1.router import api_v1_router
    app.include_router(api_v1_router, prefix="/api/v1")
"""

from __future__ import annotations

from fastapi import APIRouter

from src.app.api.v1 import chat
from src.app.api.v1 import ingestion
from src.app.api.v1 import properties
from src.app.api.v1 import leads


# Router raíz de v1 — se monta en /api/v1
api_v1_router = APIRouter()

# ── Montar sub-routers ─────────────────────────────────────────────

# Chat: WebSocket (/ws/chat/{sid}) + POST (/chat)
# Nota: WebSocket no usa prefix del router, se registra directamente
api_v1_router.include_router(chat.router)

# Ingestion: POST /ingestion (CSV upload)
api_v1_router.include_router(ingestion.router)

# Properties: GET /properties, GET /properties/{id}
api_v1_router.include_router(properties.router)

# Leads: GET /leads, GET /leads/{id}, PATCH /leads/{id}/status
api_v1_router.include_router(leads.router)


# ── Health Check ────────────────────────────────────────────────────

@api_v1_router.get("/health", tags=["health"])
async def health_check() -> dict:
    """Endpoint de health check para monitoreo.
    
    Usado por:
    - Docker healthcheck
    - Load balancers
    - Uptime monitors (UptimeRobot, Pingdom)
    
    Returns:
        status: "ok" siempre que FastAPI esté respondiendo.
        En V2 se añadirá verificación de DB, LLM, y servicios externos.
    """
    return {
        "status": "ok",
        "version": "1.2.0",
        "service": "margarita-ai-realty",
    }


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — api/v1/router.py")

    # Test 1: Router raíz existe
    assert api_v1_router is not None
    assert isinstance(api_v1_router, APIRouter)
    print("  ✅ api_v1_router instanciado")

    # Test 2: Todos los sub-routers están incluidos
    routes = api_v1_router.routes
    route_paths = [r.path for r in routes if hasattr(r, "path")]
    
    # Verificar que health está registrado
    assert "/health" in route_paths, f"Rutas disponibles: {route_paths}"
    print("  ✅ Health check registrado")

    # Test 3: Verificar que los routers importan correctamente
    assert chat.router is not None
    assert ingestion.router is not None
    assert properties.router is not None
    assert leads.router is not None
    print("  ✅ Todos los sub-routers importados")

    # Test 4: Health check retorna estructura correcta
    import asyncio
    result = asyncio.run(health_check())
    assert result["status"] == "ok"
    assert result["version"] == "1.2.0"
    assert result["service"] == "margarita-ai-realty"
    print("  ✅ Health check retorna estructura correcta")

    # Test 5: Contar rutas totales (incluyendo las de sub-routers)
    total_routes = len(routes)
    assert total_routes > 0, "Debe tener al menos la ruta de health"
    print(f"  ✅ Total rutas registradas: {total_routes}")

    print("\n🎉 Todos los smoke tests pasaron")
    print("   Nota: Las rutas de sub-routers se verifican en sus propios smoke tests")