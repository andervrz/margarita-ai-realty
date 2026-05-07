# src/app/db/models/lead.py
"""Modelo Lead — prospecto calificado con datos de visita."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Index, func
from sqlalchemy import Boolean
from src.app.db.base import Base
from src.app.core.constants import LeadStatus

class Lead(Base):
    """Lead capturado al momento de agendar visita."""
    
    __tablename__ = "leads"

    __table_args__ = (
    Index("idx_leads_tenant_date", "tenant_id", "created_at"),
    CheckConstraint(
        "status IN ('pendiente', 'confirmado', 'cancelado')",
        name="ck_lead_status"
    ),
)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    property_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("properties.id"), nullable=True
    )
    
    # Datos de contacto
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)
    
    # Visita
    preferred_date: Mapped[str] = mapped_column(String, nullable=False)
    preferred_time: Mapped[str] = mapped_column(String, nullable=False)
    visit_duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Calificación
    qualification_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_international: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=LeadStatus.PENDIENTE.value)

    
    # Tracking
    calendar_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    whatsapp_sent: Mapped[int] = mapped_column(Integer, default=0)
    email_sent: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, 
        default=lambda: datetime.now(timezone.utc).isoformat(),
    )


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — lead.py")
    
    l = Lead(
        session_id="session-123",
        tenant_id="tenant-123",
        name="María González",
        email="maria@email.com",
        phone="+584141234567",
        preferred_date="2026-06-15",
        preferred_time="10:00",
        visit_duration_minutes=90,
        qualification_score=85,
    )
    assert l.name == "María González"
    assert l.visit_duration_minutes == 90
    assert l.status == "pendiente"
    assert l.is_international == 0
    print(f"  ✅ Lead creado: {l.name} (score={l.qualification_score})")
    print("\n🎉 Smoke test pasó")