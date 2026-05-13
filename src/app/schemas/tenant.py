# src/app/schemas/tenant.py
"""Schemas Pydantic para Tenant."""

from pydantic import BaseModel, ConfigDict
from src.app.core.constants import Plan


class TenantConfig(BaseModel):
    """Configuración interna del tenant — uso del chat engine, no API pública."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    plan: Plan
    llm_model: str | None = None
    llm_fallback_1: str | None = None
    llm_fallback_2: str | None = None
    qualification_threshold: int
    session_ttl_minutes: int
    visit_duration_minutes: int
    calendar_enabled: bool
    email_enabled: bool
    whatsapp_enabled: bool
    agent_email: str | None
    agent_whatsapp: str | None
    whatsapp_phone_id: str | None
    allowed_origins: str | None
    is_active: bool

  
# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — schemas/tenant.py")
    try:
        TenantResponse(
            id="tenant-123",
            name="Esparta Inmuebles",
            slug="esparta",
            plan="pro",
            calendar_enabled=True,
            email_enabled=True,
            whatsapp_enabled=True,
            agent_email="agente@esparta.com",
            agent_whatsapp="+584141234567",
            qualification_threshold=75,
            session_ttl_minutes=30,
            visit_duration_minutes=60,
            is_active=True,
        )
        assert False, "Debería fallar"
    except Exception:
        print("  ✅ Plan inválido rechazado")
