# src/app/db/models/tenant.py
"""Modelo Tenant — cliente inmobiliario (multi-tenant)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from src.app.core.constants import Plan
from src.app.db.base import Base


class Tenant(Base):
    """Cliente inmobiliario con configuración aislada."""
    
    __tablename__ = "tenants"
    
    __table_args__ = (
    CheckConstraint(
        "plan IN ('basic', 'standard', 'pro')",
        name="ck_tenant_plan"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String, default="pro")
    api_key_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    # LLM — override por tenant (V1: llm_model opcional, fallbacks para V2)
    llm_model: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_fallback_1: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_fallback_2: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # Features flags
    calendar_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    whatsapp_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Contacto agente
    agent_email: Mapped[str | None] = mapped_column(String, nullable=True)
    agent_whatsapp: Mapped[str | None] = mapped_column(String, nullable=True)
    whatsapp_phone_id: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # Configuración
    qualification_threshold: Mapped[int] = mapped_column(Integer, default=75)
    session_ttl_minutes: Mapped[int] = mapped_column(Integer, default=30)
    visit_duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    allowed_origins: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON array
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, 
        default=lambda: datetime.now(timezone.utc).isoformat(),
    )



# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — tenant.py")
    
    t = Tenant(
        name="Esparta Inmuebles",
        slug="esparta-inmuebles",
        api_key_hash="abc123hash",
    )
    assert t.name == "Esparta Inmuebles"
    assert t.plan == "pro"
    assert t.calendar_enabled == 1
    assert t.visit_duration_minutes == 60
    assert t.is_active == 1
    assert len(t.id) == 36  # UUID
    print(f"  ✅ Tenant creado: {t.name} (plan={t.plan})")
    print("\n🎉 Smoke test pasó")
