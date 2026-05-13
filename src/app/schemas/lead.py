# src/app/schemas/lead.py
"""Schemas Pydantic para Lead y Booking."""

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.app.core.constants import LeadStatus


class BookingData(BaseModel):
    """Datos recolectados durante el flujo de booking conversacional."""

    name: str = Field(..., min_length=2, description="Nombre completo del lead")
    email: EmailStr = Field(..., description="Email de contacto")
    phone: str = Field(
        ...,
        pattern=r"^\+?[\d\s\-]{7,15}$",
        description="Teléfono con código de país",
    )
    preferred_date: str = Field(..., description="Fecha preferida (YYYY-MM-DD)")
    preferred_time: str = Field(..., description="Hora preferida (HH:MM 24h)")
    visit_duration_minutes: int = Field(default=60, ge=15, le=180)
    notes: str | None = Field(None, description="Notas adicionales del usuario")
    property_id: str | None = Field(None, description="ID de propiedad seleccionada")

    @field_validator("preferred_date")
    @classmethod
    def validate_future_date(cls, v: str) -> str:
        try:
            parsed = date.fromisoformat(v)
        except ValueError:
            raise ValueError("Fecha debe ser formato YYYY-MM-DD")
        if parsed <= date.today():
            raise ValueError("La fecha de visita debe ser futura")
        return v

    @field_validator("preferred_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("Hora debe ser formato HH:MM (24h), ejemplo: 10:00")
        return v


class LeadCreate(BookingData):
    """Extiende BookingData con campos de contexto para persistencia en DB."""

    session_id: str
    tenant_id: str
    qualification_score: int | None = None
    is_international: bool = False


class LeadResponse(BaseModel):
    """Respuesta pública de un lead — construida desde ORM."""

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
    is_international: bool
    status: LeadStatus
    calendar_event_id: str | None
    whatsapp_sent: bool
    email_sent: bool
    created_at: str


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — schemas/lead.py")

    # Test fecha pasada — debe fallar
    try:
        BookingData(
            name="Test",
            email="test@test.com",
            phone="+584141234567",
            preferred_date="2020-01-01",
            preferred_time="10:00",
        )
        assert False, "Debería haber fallado"
    except Exception:
        print("  ✅ Fecha pasada rechazada correctamente")

    # Test hora inválida — debe fallar
    try:
        BookingData(
            name="Test",
            email="test@test.com",
            phone="+584141234567",
            preferred_date="2027-01-01",
            preferred_time="25:00",
        )
        assert False, "Debería haber fallado"
    except Exception:
        print("  ✅ Hora inválida rechazada correctamente")

    # Test email inválido — debe fallar
    try:
        BookingData(
            name="Test",
            email="no-es-un-email",
            phone="+584141234567",
            preferred_date="2027-01-01",
            preferred_time="10:00",
        )
        assert False, "Debería haber fallado"
    except Exception:
        print("  ✅ Email inválido rechazado correctamente")

    # Test teléfono inválido — debe fallar
    try:
        BookingData(
            name="Test",
            email="test@test.com",
            phone="abc",
            preferred_date="2027-01-01",
            preferred_time="10:00",
        )
        assert False, "Debería haber fallado"
    except Exception:
        print("  ✅ Teléfono inválido rechazado correctamente")

    # Happy path — BookingData completo
    booking = BookingData(
        name="María González",
        email="maria@test.com",
        phone="+584141234567",
        preferred_date="2027-06-15",
        preferred_time="10:00",
        visit_duration_minutes=90,
        notes="Interesada en vista al mar",
        property_id="prop-123",
    )
    assert booking.name == "María González"
    assert booking.visit_duration_minutes == 90
    assert booking.notes == "Interesada en vista al mar"
    print("  ✅ BookingData válido — happy path")

    # Happy path — LeadCreate hereda validaciones de BookingData
    lead = LeadCreate(
        session_id="sess-123",
        tenant_id="tenant-123",
        name="Carlos Rodríguez",
        email="carlos@test.com",
        phone="+584241234567",
        preferred_date="2027-07-20",
        preferred_time="14:30",
        qualification_score=85,
        is_international=False,
    )
    assert lead.qualification_score == 85
    assert lead.is_international is False
    assert lead.session_id == "sess-123"
    print("  ✅ LeadCreate válido — herencia de BookingData correcta")

    # Verificar que LeadCreate hereda validators de BookingData
    try:
        LeadCreate(
            session_id="sess-123",
            tenant_id="tenant-123",
            name="Test",
            email="test@test.com",
            phone="+584141234567",
            preferred_date="2020-01-01",  # fecha pasada
            preferred_time="10:00",
        )
        assert False, "Debería haber fallado"
    except Exception:
        print("  ✅ LeadCreate hereda validación de fecha de BookingData")

    print("\n🎉 Todos los smoke tests pasaron")
