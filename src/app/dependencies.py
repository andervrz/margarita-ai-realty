# src/app/dependencies.py
"""Dependency Injection — AsyncSession, Tenant y utilidades para endpoints.

Centraliza las dependencias reutilizables de FastAPI:
    - get_db_session:      AsyncSession de SQLAlchemy
    - get_current_tenant:  Tenant desde request.state (inyectado por middleware)
    - require_plan:        Restricción de endpoints por plan de pago
    - get_db_and_tenant:   Combinada DB + tenant

Uso en endpoints:
    @router.get("/leads")
    async def list_leads(
        db: AsyncSession = Depends(get_db_session),
        tenant: dict = Depends(get_current_tenant),
    ):
        ...

Nota: get_current_tenant también existe en api/middleware.py para uso
interno del middleware. Esta versión es la dependency oficial para
endpoints — misma lógica, con HTTPException tipadas correctamente.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import get_settings
from src.app.core.logging import get_logger

logger = get_logger(__name__)


# ── DEV TENANT ────────────────────────────────────────────────────
# Mirror de middleware.py — duplicado intencionalmente para evitar
# imports circulares. V2: centralizar en src/app/core/tenant.py

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


# ── Database Session ──────────────────────────────────────────────

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency que provee AsyncSession de SQLAlchemy.

    Se usa con Depends() en endpoints que necesitan acceso a DB.
    La sesión se cierra automáticamente al final del request.

    Para writes:
        async with db.begin():
            db.add(obj)

    Para reads:
        result = await db.execute(select(...))
    """
    from src.app.db.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


# ── Tenant ────────────────────────────────────────────────────────

def get_current_tenant(request: Request) -> dict:
    """Dependency que extrae el tenant resuelto de request.state.

    El tenant es inyectado por TenantMiddleware antes del endpoint.
    En development (app_env=development) retorna DEV_TENANT directamente.

    Raises:
        HTTPException 401: Si no hay tenant en request.state
        HTTPException 403: Si el tenant está inactivo
    """
    settings = get_settings()

    # Bypass en desarrollo — sin fricción de API key
    if settings.app_env == "development":
        return _DEV_TENANT

    tenant = getattr(request.state, "tenant", None)
    if not tenant:
        logger.warning("dependency_no_tenant", path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado. Falta X-API-Key header.",
        )

    if not tenant.get("is_active"):
        logger.warning(
            "dependency_tenant_inactive",
            tenant_id=tenant.get("id"),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant inactivo.",
        )

    return tenant


# ── Plan Restriction ──────────────────────────────────────────────

def require_plan(*allowed_plans: str):
    """Factory de dependency que restringe endpoints por plan de pago.

    Uso:
        @router.get("/calendar")
        async def calendar(tenant = Depends(require_plan("pro", "standard"))):
            ...

    Args:
        *allowed_plans: Planes permitidos ("basic", "standard", "pro").

    Raises:
        HTTPException 403: Si el plan del tenant no está en allowed_plans.
    """
    async def _check_plan(
        tenant: dict = Depends(get_current_tenant),
    ) -> dict:
        plan = tenant.get("plan", "basic")
        if plan not in allowed_plans:
            logger.warning(
                "dependency_plan_denied",
                tenant_id=tenant.get("id"),
                plan=plan,
                required=list(allowed_plans),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Esta función requiere plan: {', '.join(allowed_plans)}. "
                    f"Tu plan actual: {plan}."
                ),
            )
        return tenant

    return _check_plan


# ── Chat Session ──────────────────────────────────────────────────

async def get_chat_session_id(request: Request) -> str:
    """Dependency que resuelve o genera el session_id del chat.

    Busca session_id en:
      1. Header X-Session-Id (POST fallback)
      2. Query param session_id (compatibilidad)
      3. Genera UUID v4 si no existe

    Retorna solo el ID — la sesión real se carga en el endpoint
    con get_session_memory() para tener acceso a la DB session.
    """
    session_id = (
        request.headers.get("X-Session-Id")
        or request.query_params.get("session_id")
    )
    if not session_id:
        session_id = str(uuid.uuid4())
    return session_id


# ── Combined Dependency ───────────────────────────────────────────

async def get_db_and_tenant(
    db: AsyncSession = Depends(get_db_session),
    tenant: dict = Depends(get_current_tenant),
) -> tuple[AsyncSession, dict]:
    """Dependency combinada DB + tenant.

    Uso:
        @router.get("/leads")
        async def list_leads(deps = Depends(get_db_and_tenant)):
            db, tenant = deps
            ...
    """
    return db, tenant


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import inspect
    from unittest.mock import MagicMock
    from fastapi import HTTPException

    print("🔥 Smoke Tests — app/dependencies.py\n")

    # Test 1: DEV_TENANT tiene estructura completa
    required_keys = [
        "id", "name", "slug", "plan", "is_active",
        "calendar_enabled", "email_enabled", "whatsapp_enabled",
        "qualification_threshold", "session_ttl_minutes",
        "visit_duration_minutes",
    ]
    for key in required_keys:
        assert key in _DEV_TENANT, f"Falta '{key}' en _DEV_TENANT"
    assert _DEV_TENANT["plan"] == "pro"
    assert _DEV_TENANT["is_active"] is True
    print("✅ _DEV_TENANT estructura completa")

    # Test 2: get_current_tenant en development retorna DEV_TENANT
    mock_request = MagicMock()
    mock_request.url.path = "/test"
    mock_request.state.tenant = None

    # Patch settings para simular development
    from unittest.mock import patch
    with patch("src.app.dependencies.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(app_env="development")
        tenant = get_current_tenant(mock_request)
        assert tenant["id"] == "dev-tenant-001"
    print("✅ get_current_tenant en development retorna DEV_TENANT")

    # Test 3: get_current_tenant en producción sin tenant → 401
    with patch("src.app.dependencies.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(app_env="production")
        mock_request.state.tenant = None
        try:
            get_current_tenant(mock_request)
            assert False, "Debería lanzar HTTPException 401"
        except HTTPException as exc:
            assert exc.status_code == 401
    print("✅ get_current_tenant sin tenant → 401")

    # Test 4: get_current_tenant con tenant inactivo → 403
    with patch("src.app.dependencies.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(app_env="production")
        mock_request.state.tenant = {"id": "t-001", "is_active": False}
        try:
            get_current_tenant(mock_request)
            assert False, "Debería lanzar HTTPException 403"
        except HTTPException as exc:
            assert exc.status_code == 403
    print("✅ get_current_tenant con tenant inactivo → 403")

    # Test 5: get_db_session es async generator
    assert inspect.isasyncgenfunction(get_db_session)
    print("✅ get_db_session es AsyncGenerator correcto")

    # Test 6: require_plan permite plan correcto
    check_pro = require_plan("pro", "standard")
    tenant_pro = {"id": "t-001", "plan": "pro", "is_active": True}
    result = asyncio.run(check_pro(tenant_pro))
    assert result["plan"] == "pro"
    print("✅ require_plan permite plan autorizado")

    # Test 7: require_plan rechaza plan incorrecto
    tenant_basic = {"id": "t-002", "plan": "basic", "is_active": True}
    try:
        asyncio.run(check_pro(tenant_basic))
        assert False, "Debería rechazar plan basic"
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "pro" in exc.detail
    print("✅ require_plan rechaza plan no autorizado con mensaje claro")

    # Test 8: get_chat_session_id genera UUID cuando no hay session
    mock_req_no_session = MagicMock()
    mock_req_no_session.headers.get.return_value = None
    mock_req_no_session.query_params.get.return_value = None
    session_id = asyncio.run(get_chat_session_id(mock_req_no_session))
    assert len(session_id) == 36  # UUID v4
    print(f"✅ get_chat_session_id genera UUID: {session_id[:8]}...")

    # Test 9: get_chat_session_id usa header X-Session-Id
    mock_req_with_session = MagicMock()
    mock_req_with_session.headers.get.return_value = "existing-session-123"
    mock_req_with_session.query_params.get.return_value = None
    session_id_2 = asyncio.run(get_chat_session_id(mock_req_with_session))
    assert session_id_2 == "existing-session-123"
    print("✅ get_chat_session_id usa X-Session-Id header cuando existe")

    print("\n🎉 Todos los smoke tests pasaron ✅")
    print("   Nota: Tests de integración con DB requieren FastAPI TestClient")
