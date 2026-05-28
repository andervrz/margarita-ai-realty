# src/app/api/middleware.py
"""Middleware stack — TenantMiddleware + RateLimitMiddleware.

Orden de ejecución en FastAPI (registrar en este orden en main.py):
    1. CORSMiddleware       → allow_origins por tenant
    2. TenantMiddleware     → X-API-Key → resuelve tenant
    3. RateLimitMiddleware  → sliding window por tenant/IP

Flujo de autenticación:
    Request → X-API-Key header → SHA-256 hash → lookup tabla tenants
    → Verifica Origin en allowed_origins → Inyecta en request.state.tenant
    → Si falla: retorna 401/403 antes de llegar al endpoint

Desarrollo (app_env=development):
    - Se omite validación de API key
    - Se usa DEV_TENANT hardcodeado (PLAN.md v1.2 decisión #36)
    - Se acepta cualquier Origin
    - Sin rate limiting
"""

from __future__ import annotations

import json
import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.core.security import hash_api_key

logger = get_logger(__name__)


# ── DEV TENANT (Fases 0-8) ────────────────────────────────────────
# Hardcodeado para eliminar fricción durante desarrollo.
# Multi-tenant completo se activa en Fase 9 (producción).
# PLAN.md v1.2 decisión #36.

_DEV_TENANT: dict = {
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
    "whatsapp_phone_id": None,
    "llm_model": None,
    "llm_fallback_1": None,
    "llm_fallback_2": None,
    "allowed_origins": ["*"],
    "is_active": True,
}


# ── TenantMiddleware ──────────────────────────────────────────────

class TenantMiddleware(BaseHTTPMiddleware):
    """Resuelve el tenant desde X-API-Key header y valida Origin.

    Inyecta tenant dict en request.state.tenant para que endpoints
    y dependencies lo consuman sin repetir lógica de autenticación.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_settings()

        # Bypass completo en desarrollo
        if settings.app_env == "development":
            request.state.tenant = _DEV_TENANT
            logger.debug("tenant_dev_bypass", tenant_id=_DEV_TENANT["id"])
            return await call_next(request)

        # Producción: validar X-API-Key
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            logger.warning("tenant_no_api_key", path=request.url.path)
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing X-API-Key header"},
            )

        # SHA-256 del API key para lookup en DB
        key_hash = hash_api_key(api_key)

        tenant = await _lookup_tenant(request, key_hash)
        if not tenant:
            logger.warning(
                "tenant_invalid_key",
                key_hash_prefix=key_hash[:8],
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key"},
            )

        if not tenant.get("is_active"):
            logger.warning("tenant_inactive", tenant_id=tenant["id"])
            return JSONResponse(
                status_code=403,
                content={"detail": "Tenant account is inactive"},
            )

        # Validar Origin contra allowed_origins del tenant
        origin = request.headers.get("Origin", "")
        allowed = tenant.get("allowed_origins", ["*"])
        if (
            allowed
            and "*" not in allowed
            and origin
            and origin not in allowed
        ):
            logger.warning(
                "tenant_origin_denied",
                tenant_id=tenant["id"],
                origin=origin,
                allowed=allowed,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin not allowed"},
            )

        request.state.tenant = tenant
        logger.info(
            "tenant_resolved",
            tenant_id=tenant["id"],
            plan=tenant["plan"],
            origin=origin or "none",
        )

        return await call_next(request)


async def _lookup_tenant(request: Request, key_hash: str) -> dict | None:
    """Busca tenant por api_key_hash en DB.

    Convierte ORM a dict para evitar detached instance en request.state.
    """
    from sqlalchemy import select

    from src.app.db.engine import AsyncSessionLocal
    from src.app.db.models.tenant import Tenant

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.api_key_hash == key_hash)
        )
        tenant = result.scalar_one_or_none()
        if tenant is None:
            return None

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
            "whatsapp_phone_id": tenant.whatsapp_phone_id,
            "llm_model": tenant.llm_model,
            "llm_fallback_1": tenant.llm_fallback_1,
            "llm_fallback_2": tenant.llm_fallback_2,
            "allowed_origins": _parse_origins(tenant.allowed_origins),
            "is_active": bool(tenant.is_active),
        }


def _parse_origins(origins_raw: str | None) -> list[str]:
    """Parsea allowed_origins desde JSON string almacenado en DB."""
    if not origins_raw:
        return ["*"]
    try:
        parsed = json.loads(origins_raw)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return ["*"]


# ── RateLimitMiddleware ───────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting por tenant_id e IP — sliding window de 1 minuto.

    Implementación en memoria (single-worker V1).
    V2: migrar a Redis para multi-worker deployment.

    Límites:
      - Por tenant: 60 req/min (protege costos de LLM)
      - Por IP: 120 req/min (más permisivo — evita bloquear oficinas con NAT)
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._requests: dict[str, list[float]] = {}
        self._window_seconds = 60
        self._limit_per_tenant = 60
        self._limit_per_ip = 120

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_settings()

        # Sin rate limiting en desarrollo
        if settings.app_env == "development":
            return await call_next(request)

        now = time.time()
        client_ip = _get_client_ip(request)

        # Límite por IP — aplica a todos los requests
        ip_key = f"ip:{client_ip}"
        if not self._allow_request(ip_key, now, self._limit_per_ip):
            logger.warning("rate_limit_ip_exceeded", ip=client_ip)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={"Retry-After": str(self._window_seconds)},
            )

        # Límite por tenant — aplica si TenantMiddleware ya resolvió el tenant
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
                    headers={"Retry-After": str(self._window_seconds)},
                )

        return await call_next(request)

    def _allow_request(self, key: str, now: float, limit: int) -> bool:
        """Sliding window: conserva solo timestamps dentro de la ventana."""
        timestamps = self._requests.get(key, [])
        # Limpiar timestamps expirados
        valid = [ts for ts in timestamps if (now - ts) < self._window_seconds]
        if len(valid) >= limit:
            self._requests[key] = valid
            return False
        valid.append(now)
        self._requests[key] = valid
        return True


def _get_client_ip(request: Request) -> str:
    """Extrae IP real considerando proxies con X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# ── Helper para Endpoints ─────────────────────────────────────────

def get_current_tenant(request: Request) -> dict:
    """FastAPI dependency — extrae tenant de request.state.

    Uso en endpoints:
        @router.get("/leads")
        async def list_leads(tenant: dict = Depends(get_current_tenant)):
            tenant_id = tenant["id"]
            ...
    """
    tenant = getattr(request.state, "tenant", None)
    if not tenant:
        raise RuntimeError(
            "TenantMiddleware no se ejecutó antes del endpoint. "
            "Verificar orden de registro de middlewares en main.py."
        )
    return tenant


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    print("🔥 Smoke Tests — api/middleware.py\n")

    # Test 1: DEV_TENANT tiene estructura completa
    required_keys = [
        "id", "name", "slug", "plan", "api_key_hash",
        "qualification_threshold", "session_ttl_minutes",
        "visit_duration_minutes", "calendar_enabled",
        "email_enabled", "whatsapp_enabled",
        "agent_email", "agent_whatsapp",
        "allowed_origins", "is_active",
    ]
    for key in required_keys:
        assert key in _DEV_TENANT, f"Falta key '{key}' en _DEV_TENANT"
    assert _DEV_TENANT["id"] == "dev-tenant-001"
    assert _DEV_TENANT["plan"] == "pro"
    assert _DEV_TENANT["allowed_origins"] == ["*"]
    assert _DEV_TENANT["is_active"] is True
    print("✅ _DEV_TENANT tiene estructura completa")

    # Test 2: hash_api_key es la función correcta (no hash_api_key_sha256)
    from src.app.core.security import hash_api_key
    h1 = hash_api_key("test-key-123")
    h2 = hash_api_key("test-key-123")
    assert h1 == h2
    assert len(h1) == 64
    print("✅ hash_api_key determinístico y 64 chars")

    # Test 3: _parse_origins con JSON válido
    origins = _parse_origins('["https://example.com", "https://app.example.com"]')
    assert origins == ["https://example.com", "https://app.example.com"]
    print("✅ _parse_origins con JSON válido")

    # Test 4: _parse_origins con None → wildcard
    assert _parse_origins(None) == ["*"]
    print("✅ _parse_origins None → ['*']")

    # Test 5: _parse_origins con JSON inválido → wildcard
    assert _parse_origins("not valid json") == ["*"]
    assert _parse_origins("") == ["*"]
    print("✅ _parse_origins JSON inválido → ['*']")

    # Test 6: RateLimit — sliding window básico
    rl = RateLimitMiddleware(None)
    now = time.time()
    key = "test:basic"
    for i in range(60):
        assert rl._allow_request(key, now + i * 0.001, limit=60), \
            f"Request {i+1} debería pasar"
    # Request 61 debe fallar
    assert not rl._allow_request(key, now + 0.1, limit=60), \
        "Request 61 debería ser rechazado"
    print("✅ RateLimit sliding window: 60 pasan, 61 rechazado")

    # Test 7: RateLimit — cleanup de timestamps expirados
    old_key = "test:expired"
    rl._requests[old_key] = [now - 120, now - 90, now - 65]  # Todos expirados
    assert rl._allow_request(old_key, now, limit=1), \
        "Debe permitir después de limpiar expirados"
    assert len(rl._requests[old_key]) == 1  # Solo el nuevo timestamp
    print("✅ RateLimit limpia timestamps expirados correctamente")

    # Test 8: RateLimit — ventana deslizante (no fija)
    sliding_key = "test:sliding"
    now2 = time.time()
    # Llenar ventana
    for i in range(3):
        rl._allow_request(sliding_key, now2 + i, limit=3)
    assert not rl._allow_request(sliding_key, now2 + 3, limit=3), \
        "Debe rechazar cuando ventana llena"
    # Avanzar más allá de la ventana (61 segundos después del primer request)
    assert rl._allow_request(sliding_key, now2 + 61, limit=3), \
        "Debe permitir cuando los primeros timestamps expiraron"
    print("✅ RateLimit ventana deslizante funciona correctamente")

    # Test 9: _get_client_ip con X-Forwarded-For
    class FakeRequest:
        headers = {"X-Forwarded-For": "203.0.113.42, 10.0.0.1"}
        client = None

    assert _get_client_ip(FakeRequest()) == "203.0.113.42"
    print("✅ _get_client_ip extrae primera IP de X-Forwarded-For")

    # Test 10: _get_client_ip sin proxy
    class FakeRequestDirect:
        headers = {}

        class client:
            host = "192.168.1.1"

    assert _get_client_ip(FakeRequestDirect()) == "192.168.1.1"
    print("✅ _get_client_ip sin proxy usa request.client.host")

    # Test 11: _get_client_ip sin cliente
    class FakeRequestNoClient:
        headers = {}
        client = None

    assert _get_client_ip(FakeRequestNoClient()) == "unknown"
    print("✅ _get_client_ip sin cliente retorna 'unknown'")

    # Test 12: settings.app_env es snake_case (no SCREAMING_SNAKE)
    settings = get_settings()
    assert hasattr(settings, "app_env"), "Settings debe tener 'app_env' en snake_case"
    assert not hasattr(settings, "APP_ENV"), "Settings NO debe tener 'APP_ENV' en SCREAMING_SNAKE"
    print(f"✅ settings.app_env en snake_case: '{settings.app_env}'")

    print("\n🎉 Todos los smoke tests pasaron ✅")
