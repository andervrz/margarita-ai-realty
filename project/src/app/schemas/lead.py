# src/app/schemas/lead.py
"""Schemas Pydantic para Lead y Booking."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class BookingData(BaseModel):
    """Datos recolectados durante el flujo de booking."""
    
    name: str = Field(..., min_length=2, description="Nombre completo del lead")
    email: EmailStr = Field(..., description="Email de contacto")
    phone: str = Field(
        ..., 
        pattern=r"^\+?[\d\s\-]{7,15}$",
        description="Teléfono con código de país"
    )
    preferred_date: str = Field(..., description="Fecha preferida (YYYY-MM-DD)")
    preferred_time: str = Field(..., description="Hora preferida (HH:MM)")
    visit_duration_minutes: int = Field(default=60, ge=15, le=180)
    notes: str | None = Field(None, description="Notas adicionales")
    property_id: str | None = Field(None, description="ID de propiedad seleccionada")


class LeadCreate(BaseModel):
    """Datos para crear un lead en la base de datos."""
    
    session_id: str
    tenant_id: str
    property_id: str | None = None
    name: str = Field(..., min_length=2)
    email: EmailStr
    phone: str = Field(..., pattern=r"^\+?[\d\s\-]{7,15}$")
    preferred_date: str
    preferred_time: str
    visit_duration_minutes: int = Field(default=60, ge=15, le=180)
    notes: str | None = None
    qualification_score: int | None = None
    is_international: int = 0


class LeadResponse(BaseModel):
    """Respuesta pública de un lead."""
    
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    tenant_id: str
    property_id: str | None
    name: str
    email: str
    phone: str
    preferred_date: str
    preferred_time: str
    visit_duration_minutes: int
    notes: str | None
    qualification_score: int | None
    is_international: int
    status: str
    calendar_event_id: str | None
    whatsapp_sent: int
    email_sent: int
    created_at: str


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — schemas/lead.py")
    
    booking = BookingData(
        name="María González",
        email="maria@test.com",
        phone="+584141234567",
        preferred_date="2026-06-15",
        preferred_time="10:00",
        visit_duration_minutes=90,
    )
    assert booking.visit_duration_minutes == 90
    
    lead = LeadCreate(
        session_id="sess-123",
        tenant_id="tenant-123",
        name="María González",
        email="maria@test.com",
        phone="+584141234567",
        preferred_date="2026-06-15",
        preferred_time="10:00",
        qualification_score=85,
    )
    assert lead.qualification_score == 85
    print("  ✅ BookingData + LeadCreate válidos")
    print("\n🎉 Smoke test pasó")