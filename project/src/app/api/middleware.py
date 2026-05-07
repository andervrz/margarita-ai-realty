# project/src/app/api/middleware.py
"""Middleware stack — TenantMiddleware + RateLimitMiddleware.

Orden de ejecución en FastAPI:
    1. CORSMiddleware (configurado en main.py)
    2. RateLimitMiddleware (capa externa, protege contra abuso)
    3. TenantMiddleware (resuelve tenant, inyecta en request.state)

Flujo de autenticación:
    Request → X-API-Key header → SHA-256 hash → lookup en tabla tenants
    → Verifica Origin en allowed_origins → Inyecta tenant en request.state
    → Si falla: retorna 401/403 antes de llegar al endpoint

En desarrollo (APP_ENV=development):
    - Se omite validación de API key
    - Se usa DEV_TENANT hardcodeado
    - Se acepta cualquier Origin
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.logging import logger
from app.core.security import hash_api_key_sha256

# ── DEV TENANT (Fases 0-8) ─────────────────────────────────────────
# Hardcodeado para eliminar fricción durante desarrollo.
# Se activa cuando APP_ENV != "production".

_DEV_TENANT = {
    "id": "dev-tenant-001",
    "name": "Dev Inmobiliaria Margarita",
    "slug": "dev-inmobiliaria-margarita",
    "plan": "pro",
    "api_key_hash": "dev",
    "qualification_threshold": 75,
    "session_ttl_minutes": 30,
    "visit_duration_minutes": 60,
    "calendar_enabled": True,
    "email_enabled": True,
    "whatsapp_enabled": True,
    "agent_email": "dev@example.com",
    "agent_whatsapp": "+584120000000",
    "allowed_origins": ["*"],
    "is_active": True,
}


# ── TenantMiddleware ───────────────────────────────────────────────

class TenantMiddleware(BaseHTTPMiddleware):
    """Resuelve el tenant desde X-API-Key y valida Origin.
    
    Inyecta el objeto tenant en request.state.tenant para que los
    endpoints y dependencies lo consuman sin repetir lógica.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Bypass en desarrollo
        if settings.APP_ENV == "development":
            request.state.tenant = _DEV_TENANT
            logger.debug("tenant_middleware_dev", tenant_id=_DEV_TENANT["id"])
            return await call_next(request)

        # Producción: validar API key
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            logger.warning("tenant_middleware_no_api_key", path=request.url.path)
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing X-API-Key header"},
            )

        # Hash del API key para lookup
        key_hash = hash_api_key_sha256(api_key)

        # Buscar tenant en base de datos
        tenant = await _lookup_tenant(request, key_hash)
        if not tenant:
            logger.warning("tenant_middleware_invalid_key", key_hash_prefix=key_hash[:8])
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key"},
            )

        # Verificar que tenant esté activo
        if not tenant.get("is_active"):
            logger.warning("tenant_middleware_inactive", tenant_id=tenant["id"])
            return JSONResponse(
                status_code=403,
                content={"detail": "Tenant inactive"},
            )

        # Validar Origin
        origin = request.headers.get("Origin", "")
        allowed = tenant.get("allowed_origins", [])
        if allowed and origin and origin not in allowed:
            logger.warning(
                "tenant_middleware_origin_denied",
                tenant_id=tenant["id"],
                origin=origin,
                allowed=allowed,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin not allowed"},
            )

        # Inyectar tenant en request.state
        request.state.tenant = tenant
        logger.info(
            "tenant_middleware_resolved",
            tenant_id=tenant["id"],
            plan=tenant["plan"],
            origin=origin or "none",
        )

        return await call_next(request)


async def _lookup_tenant(request: Request, key_hash: str) -> dict | None:
    """Busca tenant por api_key_hash en la base de datos.
    
    Nota: Esta función requiere acceso a la sesión de DB.
    En V1 se usa un query directo; en V2 puede cachearse en Redis.
    """
    from sqlalchemy import select
    from app.db.models.tenats import Tenant
    from app.db.engine import async_session_maker

    async with async_session_maker() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.api_key_hash == key_hash)
        )
        tenant = result.scalar_one_or_none()
        if tenant is None:
            return None
        # Convertir ORM a dict para request.state (evita detached instance)
        return {
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "plan": tenant.plan,
            "api_key_hash": tenant.api_key_hash,
            "qualification_threshold": tenant.qualification_threshold,
            "session_ttl_minutes": tenant.session_ttl_minutes,
            "visit_duration_minutes": tenant.visit_duration_minutes,
            "calendar_enabled": bool(tenant.calendar_enabled),
            "email_enabled": bool(tenant.email_enabled),
            "whatsapp_enabled": bool(tenant.whatsapp_enabled),
            "agent_email": tenant.agent_email,
            "agent_whatsapp": tenant.agent_whatsapp,
            "allowed_origins": _parse_origins(tenant.allowed_origins),
            "is_active": bool(tenant.is_active),
        }


def _parse_origins(origins_raw: str | None) -> list[str]:
    """Parsea allowed_origins desde JSON string o retorna wildcard."""
    if not origins_raw:
        return ["*"]
    import json
    try:
        parsed = json.loads(origins_raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return ["*"]


# ── RateLimitMiddleware ────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting simple por tenant_id + IP.
    
    Implementación en memoria (single-worker V1).
    En V2 migrar a Redis para multi-worker.
    
    Estrategia: Sliding window de 1 minuto.
    - Límite por tenant: 60 req/min
    - Límite por IP: 120 req/min (más permisivo, evita bloquear oficinas)
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._requests: dict[str, list[float]] = {}  # key: "tenant:id" o "ip:1.2.3.4"
        self._window_seconds = 60
        self._limit_per_tenant = 60
        self._limit_per_ip = 120

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # En desarrollo: sin rate limiting
        if settings.APP_ENV == "development":
            return await call_next(request)

        now = time.time()
        client_ip = _get_client_ip(request)

        # Check límite por IP (más estricto, aplica a todos)
        ip_key = f"ip:{client_ip}"
        if not self._allow_request(ip_key, now, self._limit_per_ip):
            logger.warning("rate_limit_ip_exceeded", ip=client_ip)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        # Check límite por tenant (si ya resuelto por TenantMiddleware)
        tenant = getattr(request.state, "tenant", None)
        if tenant:
            tenant_key = f"tenant:{tenant['id']}"
            if not self._allow_request(tenant_key, now, self._limit_per_tenant):
                logger.warning(
                    "rate_limit_tenant_exceeded",
                    tenant_id=tenant["id"],
                    ip=client_ip,
                )
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Tenant rate limit exceeded."},
                )

        return await call_next(request)

    def _allow_request(self, key: str, now: float, limit: int) -> bool:
        """Sliding window: conserva solo requests dentro del window."""
        timestamps = self._requests.get(key, [])
        # Filtrar timestamps expirados
        valid = [ts for ts in timestamps if (now - ts) < self._window_seconds]
        if len(valid) >= limit:
            self._requests[key] = valid  # Actualizar lista limpia
            return False
        valid.append(now)
        self._requests[key] = valid
        return True


def _get_client_ip(request: Request) -> str:
    """Extrae IP real considerando proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Helper para endpoints ──────────────────────────────────────────

def get_current_tenant(request: Request) -> dict:
    """Dependency para FastAPI — extrae tenant de request.state.
    
    Uso en endpoints:
        @router.get("/leads")
        async def list_leads(tenant: dict = Depends(get_current_tenant)):
            ...
    """
    tenant = getattr(request.state, "tenant", None)
    if not tenant:
        raise RuntimeError("TenantMiddleware no ejecutó antes del endpoint")
    return tenant


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — api/middleware.py")

    # Test 1: DEV_TENANT tiene estructura completa
    assert _DEV_TENANT["id"] == "dev-tenant-001"
    assert _DEV_TENANT["plan"] == "pro"
    assert _DEV_TENANT["allowed_origins"] == ["*"]
    print("  ✅ DEV_TENANT estructura correcta")

    # Test 2: hash_api_key_sha256 es determinístico
    from app.core.security import hash_api_key_sha256
    h1 = hash_api_key_sha256("test-key-123")
    h2 = hash_api_key_sha256("test-key-123")
    assert h1 == h2
    assert len(h1) == 64
    print("  ✅ hash_api_key_sha256 determinístico")

    # Test 3: _parse_origins
    assert _parse_origins('["https://example.com"]') == ["https://example.com"]
    assert _parse_origins(None) == ["*"]
    assert _parse_origins("invalid json") == ["*"]
    print("  ✅ _parse_origins correcto")

    # Test 4: RateLimit sliding window
    rl = RateLimitMiddleware(None)
    now = time.time()
    key = "test:window"
    # 50 requests deben pasar
    for i in range(50):
        assert rl._allow_request(key, now + i * 0.01, limit=60)
    # Request 61 debe fallar
    assert not rl._allow_request(key, now, limit=60)
    print("  ✅ RateLimit sliding window funciona")

    # Test 5: RateLimit cleanup de timestamps viejos
    old_key = "test:old"
    rl._requests[old_key] = [now - 120, now - 90]  # Expirados
    assert rl._allow_request(old_key, now, limit=1)  # Limpia viejos, permite
    print("  ✅ RateLimit limpia timestamps expirados")

    # Test 6: _get_client_ip con X-Forwarded-For
    class FakeRequest:
        headers = {"X-Forwarded-For": "203.0.113.42, 10.0.0.1"}
        client = None
    assert _get_client_ip(FakeRequest()) == "203.0.113.42"
    print("  ✅ _get_client_ip extrae IP real")

    print("\n🎉 Todos los smoke tests pasaron")