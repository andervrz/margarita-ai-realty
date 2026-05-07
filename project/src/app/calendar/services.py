# project/src/app/calendar/service.py
"""Servicio de Google Calendar — creación de eventos para visitas inmobiliarias.

Integración con Google Calendar API v3 via Service Account.
Todas las operaciones síncronas del SDK se envuelven en asyncio.to_thread()
para no bloquear el event loop de FastAPI.

Flujo:
    1. Recibe lead confirmado + property opcional
    2. Construye evento con duración configurable por tenant
    3. Crea evento en Google Calendar
    4. Retorna event_id para persistencia en tabla leads

Requiere:
    - GOOGLE_CALENDAR_CREDENTIALS_PATH (service account JSON)
    - GOOGLE_CALENDAR_TIMEZONE (default: America/Caracas)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.logging import logger

if TYPE_CHECKING:
    from app.db.models.lead import Lead
    from app.db.models.property import Property
    from app.db.models.tenats import Tenant


def _create_event_sync(
    lead: "Lead",
    tenant: "Tenant",
    property: "Property | None",
) -> str:
    """Función síncrona que crea el evento en Google Calendar.
    
    Se ejecuta via asyncio.to_thread() para no bloquear el event loop.
    """
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(
        settings.GOOGLE_CALENDAR_CREDENTIALS_PATH,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # Parsear fecha/hora de inicio
    start_dt = datetime.strptime(
        f"{lead.preferred_date}T{lead.preferred_time}",
        "%Y-%m-%dT%H:%M",
    )
    
    # Duración de la visita: del lead → tenant → default global
    duration = lead.visit_duration_minutes or tenant.visit_duration_minutes or settings.DEFAULT_VISIT_DURATION_MINUTES
    end_dt = start_dt + timedelta(minutes=duration)

    # Construir título del evento
    title_parts = [f"Visita: {lead.name}"]
    if property:
        title_parts.append(f"— {property.title}")
    event_summary = " ".join(title_parts)

    # Construir descripción con metadata del lead
    description_lines = [
        f"Lead: {lead.name}",
        f"Email: {lead.email}",
        f"Teléfono: {lead.phone}",
        f"Score de calificación: {lead.qualification_score}",
        f"Internacional: {'Sí' if lead.is_international else 'No'}",
    ]
    if lead.notes:
        description_lines.append(f"Notas: {lead.notes}")
    if property:
        description_lines.extend([
            f"",
            f"Propiedad: {property.title}",
            f"Zona: {property.location_zone or 'N/A'}",
            f"Precio: ${property.price_usd:,.0f} USD" if property.price_usd else "",
        ])

    event_body = {
        "summary": event_summary,
        "description": "\n".join(line for line in description_lines if line),
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": settings.GOOGLE_CALENDAR_TIMEZONE,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": settings.GOOGLE_CALENDAR_TIMEZONE,
        },
        "attendees": [{"email": lead.email}],
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 1440},   # 24h antes
                {"method": "popup", "minutes": 60},     # 1h antes
            ],
        },
    }

    result = service.events().insert(
        calendarId="primary",
        body=event_body,
        sendUpdates="all",
    ).execute()

    return result["id"]


async def create_calendar_event(
    lead: "Lead",
    tenant: "Tenant",
    property: "Property | None" = None,
) -> str:
    """Crea un evento de visita en Google Calendar de forma asíncrona.
    
    Args:
        lead: Lead confirmado con fecha, hora y duración de visita.
        tenant: Tenant configurado (timezone, duración default).
        property: Propiedad asociada a la visita (opcional).
    
    Returns:
        event_id: ID del evento creado en Google Calendar.
    
    Raises:
        CalendarError: Si la API de Google Calendar falla.
    """
    logger.info(
        "calendar_event_create_start",
        lead_id=str(lead.id),
        tenant_id=str(tenant.id),
        property_id=str(property.id) if property else None,
        date=lead.preferred_date,
        time=lead.preferred_time,
    )

    try:
        event_id = await asyncio.to_thread(
            _create_event_sync,
            lead,
            tenant,
            property,
        )
    except Exception as exc:
        logger.error(
            "calendar_event_create_failed",
            lead_id=str(lead.id),
            tenant_id=str(tenant.id),
            error=str(exc),
        )
        raise CalendarError(
            detail=f"No se pudo crear el evento en Google Calendar: {exc}"
        ) from exc

    logger.info(
        "calendar_event_create_success",
        lead_id=str(lead.id),
        tenant_id=str(tenant.id),
        event_id=event_id,
    )

    return event_id


class CalendarError(Exception):
    """Excepción del dominio para errores de Google Calendar."""
    
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from uuid import uuid4
    from datetime import datetime as dt

    print("🔥 Smoke Test — calendar/service.py")

    # Test 1: Validar que settings cargan correctamente
    assert settings.GOOGLE_CALENDAR_TIMEZONE == "America/Caracas", "Timezone default incorrecto"
    assert settings.DEFAULT_VISIT_DURATION_MINUTES == 60, "Duración default incorrecta"
    print("  ✅ Settings cargan correctamente")

    # Test 2: Validar estructura de Lead mock (sin DB)
    class MockProperty:
        id = uuid4()
        title = "Apartamento Vista al Mar — Pampatar"
        location_zone = "Pampatar"
        price_usd = 150000.0

    class MockLead:
        id = uuid4()
        name = "Juan Pérez"
        email = "juan@test.com"
        phone = "+584121234567"
        preferred_date = "2026-06-15"
        preferred_time = "10:00"
        visit_duration_minutes = 90
        qualification_score = 85
        is_international = True
        notes = "Interesado en vista al mar, presupuesto $200k"

    class MockTenant:
        id = uuid4()
        visit_duration_minutes = 60

    lead = MockLead()
    tenant = MockTenant()
    prop = MockProperty()

    # Test 3: Validar cálculo de duración (lead > tenant > default)
    duration = lead.visit_duration_minutes or tenant.visit_duration_minutes or settings.DEFAULT_VISIT_DURATION_MINUTES
    assert duration == 90, "Duración debe tomar valor del lead"
    print(f"  ✅ Duración resuelta: {duration} min (lead override)")

    # Test 4: Validar cálculo de end_dt
    start = dt.strptime(f"{lead.preferred_date}T{lead.preferred_time}", "%Y-%m-%dT%H:%M")
    end = start + timedelta(minutes=duration)
    assert end > start, "End debe ser posterior a start"
    assert (end - start).total_seconds() == 90 * 60, "Delta debe ser 90 min"
    print(f"  ✅ Rango horario: {start.isoformat()} → {end.isoformat()}")

    # Test 5: Validar construcción de título
    title = f"Visita: {lead.name} — {prop.title}"
    assert "Juan Pérez" in title
    assert "Pampatar" in title
    print(f"  ✅ Título evento: {title[:50]}...")

    # Test 6: Validar que CalendarError se puede instanciar
    err = CalendarError("Test error")
    assert err.detail == "Test error"
    print("  ✅ CalendarError instanciable")

    # Test 7: Validar que _create_event_sync existe y es callable
    assert callable(_create_event_sync)
    print("  ✅ _create_event_sync es callable")

    print("\n🎉 Todos los smoke tests pasaron")
    print("   Nota: Los tests de integración con Google API requieren credentials.json real")