# src/app/schemas/tenant.py
"""Schemas Pydantic para Tenant."""

from pydantic import BaseModel, ConfigDict


class TenantResponse(BaseModel):
    """Respuesta pública de un tenant (sin datos sensibles)."""
    
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    plan: str
    calendar_enabled: int
    email_enabled: int
    whatsapp_enabled: int
    agent_email: str | None
    agent_whatsapp: str | None
    qualification_threshold: int
    session_ttl_minutes: int
    visit_duration_minutes: int
    is_active: int


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — schemas/tenant.py")
    
    t = TenantResponse(
        id="tenant-123",
        name="Esparta Inmuebles",
        slug="esparta",
        plan="pro",
        calendar_enabled=1,
        email_enabled=1,
        whatsapp_enabled=0,
        agent_email="agente@esparta.com",
        agent_whatsapp="+584141234567",
        qualification_threshold=75,
        session_ttl_minutes=30,
        visit_duration_minutes=60,
        is_active=1,
    )
    assert t.name == "Esparta Inmuebles"
    assert t.plan == "pro"
    print(f"  ✅ TenantResponse: {t.name} (plan={t.plan})")
    print("\n🎉 Smoke test pasó")