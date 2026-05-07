# project/src/app/dependencies.py
"""Dependency Injection — AsyncSession, Tenant, y utilidades para endpoints.

Este módulo centraliza las dependencias reutilizables de FastAPI:
    - get_db_session: AsyncSession de SQLAlchemy (auto-commit/rollback)
    - get_current_tenant: Tenant resuelto desde request.state (middleware)
    - get_current_session: Sesión de chat en memoria (RAM + TTL)
    - require_plan: Decorador para restringir endpoints por plan de pago

Uso en endpoints:
    @router.get("/leads")
    async def list_leads(
        db: AsyncSession = Depends(get_db_session),
        tenant: dict = Depends(get_current_tenant),
    ):
        ...

Nota: get_current_tenant también está definido en api/middleware.py para
uso interno del middleware. Esta versión es la dependency oficial para
endpoints, con manejo de errores más específico.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings
from app.core.logging import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ── DEV TENANT (mirror de middleware.py) ───────────────────────────
# Duplicado intencionalmente para evitar imports circulares entre
# middleware.py y dependencies.py. En V2 se centraliza en un módulo
# compartido (app/core/tenant.py).

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


# ── Database Session ───────────────────────────────────────────────

async def get_db_session() -> AsyncSession:
    """Dependency que provee una AsyncSession de SQLAlchemy.
    
    Se usa con Depends() en endpoints que necesitan acceso a DB.
    La sesión se cierra automáticamente al final del request.
    
    Para writes usar:
        async with db_session.begin():
            db_session.add(obj)
    
    Para reads usar:
        result = await db_session.execute(select(...))
    """
    from app.db.engine import async_session_maker

    async with async_session_maker() as session:
        yield session


# ── Tenant ─────────────────────────────────────────────────────────

def get_current_tenant(request: Request) -> dict:
    """Dependency que extrae el tenant de request.state.
    
    El tenant es inyectado por TenantMiddleware en cada request.
    En desarrollo retorna DEV_TENANT sin validación.
    
    Raises:
        HTTPException 401: Si no hay tenant en request.state (middleware no ejecutó)
        HTTPException 403: Si el tenant está inactivo
    """
    # En desarrollo: bypass
    if settings.APP_ENV == "development":
        return _DEV_TENANT

    tenant = getattr(request.state, "tenant", None)
    if not tenant:
        logger.warning("dependency_no_tenant", path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado. Falta API key o middleware no ejecutó.",
        )

    if not tenant.get("is_active"):
        logger.warning("dependency_tenant_inactive", tenant_id=tenant.get("id"))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant inactivo.",
        )

    return tenant


# ── Chat Session ───────────────────────────────────────────────────

async def get_current_session(
    request: Request,
    tenant: dict = Depends(get_current_tenant),
) -> dict:
    """Dependency que recupera o crea una sesión de chat en memoria.
    
    Busca session_id en:
        1. Header X-Session-Id (POST fallback)
        2. Query param session_id (compatibilidad)
        3. Genera UUID v4 si no existe
    
    Returns:
        dict con session_id y metadata de la sesión.
    """
    from app.chat.memory import get_or_create_session

    session_id = (
        request.headers.get("X-Session-Id")
        or request.query_params.get("session_id")
        or None
    )

    if session_id:
        session = await get_or_create_session(session_id, tenant["id"])
        return {
            "session_id": session_id,
            "is_new": False,
            "language": session.language,
            "qualification_score": session.qualification_score,
            "is_booking_active": session.is_booking_active,
        }

    # Nueva sesión
    import uuid
    new_id = str(uuid.uuid4())
    session = await get_or_create_session(new_id, tenant["id"])
    return {
        "session_id": new_id,
        "is_new": True,
        "language": session.language,
        "qualification_score": 0,
        "is_booking_active": False,
    }


# ── Plan Restriction ───────────────────────────────────────────────

def require_plan(*allowed_plans: str):
    """Factory de dependencies que restringe endpoints por plan de pago.
    
    Uso:
        @router.get("/calendar")
        async def calendar_endpoint(tenant: dict = Depends(require_plan("pro", "standard"))):
            ...  # Solo accesible para plan Pro o Standard
    
    Args:
        *allowed_plans: Planes permitidos ("basic", "standard", "pro")
    
    Raises:
        HTTPException 403: Si el plan del tenant no está en allowed_plans
    """
    async def _check_plan(tenant: dict = Depends(get_current_tenant)) -> dict:
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
                detail=f"Esta función requiere plan {', '.join(allowed_plans)}. Tu plan: {plan}.",
            )
        return tenant
    return _check_plan


# ── Combined Dependencies ──────────────────────────────────────────

async def get_db_and_tenant(
    db: AsyncSession = Depends(get_db_session),
    tenant: dict = Depends(get_current_tenant),
) -> tuple[AsyncSession, dict]:
    """Dependency combinada que provee DB + tenant en un solo Depends().
    
    Uso:
        @router.get("/leads")
        async def list_leads(deps: tuple = Depends(get_db_and_tenant)):
            db, tenant = deps
            ...
    
    Nota: FastAPI >= 0.95 soporta múltiples Depends() en parámetros,
    pero esta función es útil cuando se necesitan ambos juntos frecuentemente.
    """
    return db, tenant


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from unittest.mock import MagicMock

    print("🔥 Smoke Test — app/dependencies.py")

    # Test 1: DEV_TENANT estructura completa
    assert _DEV_TENANT["id"] == "dev-tenant-001"
    assert _DEV_TENANT["plan"] == "pro"
    print("  ✅ DEV_TENANT estructura correcta")

    # Test 2: get_current_tenant en dev retorna DEV_TENANT
    mock_request = MagicMock()
    mock_request.url.path = "/test"
    tenant = get_current_tenant(mock_request)
    assert tenant["id"] == "dev-tenant-001"
    print("  ✅ get_current_tenant (dev) retorna DEV_TENANT")

    # Test 3: require_plan permite plan correcto
    check_pro = require_plan("pro", "standard")
    # Simular tenant con plan pro
    mock_tenant_pro = {"id": "t-001", "plan": "pro", "is_active": True}
    result = asyncio.run(check_pro(mock_tenant_pro))
    assert result["plan"] == "pro"
    print("  ✅ require_plan permite plan autorizado")

    # Test 4: require_plan rechaza plan incorrecto
    mock_tenant_basic = {"id": "t-002", "plan": "basic", "is_active": True}
    try:
        asyncio.run(check_pro(mock_tenant_basic))
        assert False, "Debe fallar con plan basic"
    except HTTPException as exc:
        assert exc.status_code == 403
    print("  ✅ require_plan rechaza plan no autorizado")

    # Test 5: get_db_session es async generator
    import inspect
    assert inspect.isasyncgenfunction(get_db_session)
    print("  ✅ get_db_session es async generator")

    # Test 6: get_current_session retorna estructura esperada
    # Nota: Requiere que chat.memory funcione; test simplificado
    assert callable(get_current_session)
    print("  ✅ get_current_session es callable")

    print("\n🎉 Todos los smoke tests pasaron")
    print("   Nota: Tests de integración requieren FastAPI TestClient")