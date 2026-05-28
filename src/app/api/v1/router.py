# src/app/api/v1/router.py
"""Aggregator de routers v1 — monta todos los endpoints bajo /api/v1.

Estructura:
    /api/v1/ws/chat/{session_id}  → WebSocket conversacional
    /api/v1/chat                  → POST fallback
    /api/v1/ingestion             → CSV upload + pipeline
    /api/v1/properties            → Admin: propiedades
    /api/v1/leads                 → Admin: leads

Uso en main.py:
    from src.app.api.v1.router import api_v1_router
    app.include_router(api_v1_router, prefix="/api/v1")
"""

from __future__ import annotations

from fastapi import APIRouter

from src.app.api.v1 import chat, ingestion, leads, properties

# Router raíz de v1 — se monta en /api/v1 desde main.py
api_v1_router = APIRouter()

# ── Sub-routers ───────────────────────────────────────────────────

# Chat: WebSocket /ws/chat/{session_id} + POST /chat
api_v1_router.include_router(chat.router)

# Ingestion: POST /ingestion, GET /ingestion, GET /ingestion/{id}
api_v1_router.include_router(ingestion.router)

# Properties: GET /properties, GET /properties/search, GET /properties/{id}
# Nota: /search está registrado antes de /{id} en properties.router
api_v1_router.include_router(properties.router)

# Leads: GET /leads, GET /leads/{id}, PATCH /leads/{id}/status, POST /leads/{id}/notify
api_v1_router.include_router(leads.router)


# ── Health Check ──────────────────────────────────────────────────

@api_v1_router.get("/health", tags=["health"])
async def health_check() -> dict:
    """Health check para monitoreo y Docker healthcheck.

    V1: solo verifica que FastAPI responde.
    V2: verificará DB, LLM providers y servicios externos.
    """
    return {
        "status": "ok",
        "version": "1.2.0",
        "service": "margarita-ai-realty",
    }


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio

    print("🔥 Smoke Tests — api/v1/router.py\n")

    # Test 1: Router raíz instanciado
    assert api_v1_router is not None
    assert isinstance(api_v1_router, APIRouter)
    print("✅ api_v1_router instanciado")

    # Test 2: Sub-routers importados correctamente
    assert chat.router is not None
    assert ingestion.router is not None
    assert properties.router is not None
    assert leads.router is not None
    print("✅ Todos los sub-routers importados")

    # Test 3: Health check en las rutas
    route_paths = [r.path for r in api_v1_router.routes if hasattr(r, "path")]
    assert "/health" in route_paths, f"Rutas disponibles: {route_paths}"
    print("✅ /health registrado en api_v1_router")

    # Test 4: Health check retorna estructura correcta
    result = asyncio.run(health_check())
    assert result["status"] == "ok"
    assert result["version"] == "1.2.0"
    assert result["service"] == "margarita-ai-realty"
    print("✅ Health check retorna estructura correcta")

    # Test 5: Total de rutas > 0
    total = len(api_v1_router.routes)
    assert total > 0
    print(f"✅ {total} rutas registradas en api_v1_router")

    print("\n🎉 Todos los smoke tests pasaron ✅")
    print("   Nota: Rutas de sub-routers verificadas en sus propios smoke tests")
