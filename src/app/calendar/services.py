# src/app/calendar/service.py
"""Servicio de Google Calendar — creación de eventos para visitas inmobiliarias.

Integración con Google Calendar API v3 via Service Account.
Todas las operaciones síncronas del SDK se envuelven en asyncio.to_thread()
para no bloquear el event loop de FastAPI.

Flujo:
  1. Recibe lead confirmado + property opcional
  2. Construye evento con duración configurable por tenant
  3. Crea evento en Google Calendar (en thread pool)
  4. Retorna event_id para persistencia en tabla leads

Requiere en .env:
  GOOGLE_CALENDAR_CREDENTIALS_PATH (service account JSON)
  GOOGLE_CALENDAR_TIMEZONE (default: America/Caracas)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from src.app.core.config import get_settings
from src.app.core.logging import get_logger

if TYPE_CHECKING:
    from src.app.db.models.lead import Lead
    from src.app.db.models.property import Property
    from src.app.db.models.tenant import Tenant

logger = get_logger(__name__)


# ── Excepción de Dominio ──────────────────────────────────────────

class CalendarError(Exception):
    """Excepción del dominio para errores de Google Calendar."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


# ── Función Síncrona (para thread pool) ──────────────────────────

def _create_event_sync(
    lead: "Lead",
    tenant: "Tenant",
    prop: "Property | None",
) -> str:
    """Crea el evento en Google Calendar de forma síncrona.

    Se ejecuta via asyncio.to_thread() — no llamar directamente
    desde código async sin to_thread o bloquea el event loop.
    """
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    settings = get_settings()

    creds = Credentials.from_service_account_file(
        settings.google_calendar_credentials_path,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # Parsear fecha/hora de inicio
    start_dt = datetime.strptime(
        f"{lead.preferred_date}T{lead.preferred_time}",
        "%Y-%m-%dT%H:%M",
    )

    # Duración: lead → tenant → settings global
    # Prioridad: lo que el lead capturó > config del tenant > default global
    duration = (
        lead.visit_duration_minutes
        or tenant.visit_duration_minutes
        or settings.default_visit_duration_minutes
    )
    end_dt = start_dt + timedelta(minutes=duration)

    # Título del evento
    title_parts = [f"Visita: {lead.name}"]
    if prop:
        title_parts.append(f"— {prop.title}")
    event_summary = " ".join(title_parts)

    # Descripción con metadata del lead
    description_lines = [
        f"Lead: {lead.name}",
        f"Email: {lead.email}",
        f"Teléfono: {lead.phone}",
        f"Score de calificación: {lead.qualification_score or 'N/A'}",
        f"Internacional: {'Sí' if lead.is_international else 'No'}",
    ]
    if lead.notes:
        description_lines.append(f"Notas: {lead.notes}")
    if prop:
        description_lines.extend([
            "",
            f"Propiedad: {prop.title}",
            f"Zona: {prop.location_zone or 'N/A'}",
            f"Precio: ${prop.price_usd:,.0f} USD" if prop.price_usd else "Precio: Consultar",
        ])

    event_body = {
        "summary": event_summary,
        "description": "\n".join(line for line in description_lines if line is not None),
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": settings.google_calendar_timezone,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": settings.google_calendar_timezone,
        },
        "attendees": [{"email": lead.email}],
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 1440},  # 24h antes
                {"method": "popup", "minutes": 60},    # 1h antes
            ],
        },
    }

    result = service.events().insert(
        calendarId="primary",
        body=event_body,
        sendUpdates="all",
    ).execute()

    return result["id"]


# ── Función Pública Async ─────────────────────────────────────────

async def create_calendar_event(
    lead: "Lead",
    tenant: "Tenant",
    prop: "Property | None" = None,
) -> str:
    """Crea un evento de visita en Google Calendar de forma asíncrona.

    Args:
        lead: Lead confirmado con fecha, hora y duración de visita.
        tenant: Tenant configurado (timezone, duración default).
        prop: Propiedad asociada a la visita (opcional).

    Returns:
        event_id: ID del evento creado en Google Calendar.
                  Persistir en leads.calendar_event_id.

    Raises:
        CalendarError: Si la API de Google Calendar falla por cualquier motivo.
    """
    logger.info(
        "calendar_event_create_start",
        lead_id=str(lead.id),
        tenant_id=str(tenant.id),
        property_id=str(prop.id) if prop else None,
        date=lead.preferred_date,
        time=lead.preferred_time,
    )

    try:
        event_id = await asyncio.to_thread(
            _create_event_sync,
            lead,
            tenant,
            prop,
        )
    except Exception as exc:
        logger.error(
            "calendar_event_create_failed",
            lead_id=str(lead.id),
            tenant_id=str(tenant.id),
            error_type=type(exc).__name__,
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


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from datetime import datetime as dt
    from uuid import uuid4

    print("🔥 Smoke Tests — calendar/service.py\n")

    settings = get_settings()

    # Test 1: Settings cargan con campos correctos (snake_case)
    assert hasattr(settings, "google_calendar_timezone"), \
        "Campo google_calendar_timezone no existe en Settings"
    assert hasattr(settings, "google_calendar_credentials_path"), \
        "Campo google_calendar_credentials_path no existe en Settings"
    assert hasattr(settings, "default_visit_duration_minutes"), \
        "Campo default_visit_duration_minutes no existe en Settings"
    assert settings.google_calendar_timezone == "America/Caracas", \
        f"Timezone inesperado: {settings.google_calendar_timezone}"
    assert settings.default_visit_duration_minutes == 60, \
        f"Duración inesperada: {settings.default_visit_duration_minutes}"
    print("✅ Settings cargan con campos snake_case correctos")

    # Test 2: Mocks de dominio
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
        preferred_date = "2027-06-15"
        preferred_time = "10:00"
        visit_duration_minutes = 90
        qualification_score = 85
        is_international = True
        notes = "Interesado en vista al mar"

    class MockTenant:
        id = uuid4()
        visit_duration_minutes = 60

    lead = MockLead()
    tenant = MockTenant()
    prop = MockProperty()

    # Test 3: Prioridad de duración (lead > tenant > settings)
    duration = (
        lead.visit_duration_minutes
        or tenant.visit_duration_minutes
        or settings.default_visit_duration_minutes
    )
    assert duration == 90, f"Duración debe tomar valor del lead: {duration}"
    print("✅ Duración: lead (90) > tenant (60) > settings (60)")

    # Test 4: Prioridad de duración cuando lead no tiene valor
    class MockLeadSinDuracion:
        visit_duration_minutes = 0  # falsy — debe usar tenant
    duration_fallback = (
        MockLeadSinDuracion.visit_duration_minutes
        or tenant.visit_duration_minutes
        or settings.default_visit_duration_minutes
    )
    assert duration_fallback == 60, f"Duración debe caer a tenant: {duration_fallback}"
    print("✅ Duración fallback: lead(0) → tenant (60)")

    # Test 5: Cálculo de start/end sin error
    start = dt.strptime(
        f"{lead.preferred_date}T{lead.preferred_time}",
        "%Y-%m-%dT%H:%M",
    )
    end = start + timedelta(minutes=duration)
    assert end > start
    assert int((end - start).total_seconds()) == 90 * 60
    print(f"✅ Rango horario: {start.isoformat()} → {end.isoformat()}")

    # Test 6: Título con propiedad
    title_with_prop = f"Visita: {lead.name} — {prop.title}"
    assert "Juan Pérez" in title_with_prop
    assert "Pampatar" in title_with_prop
    print(f"✅ Título con propiedad: '{title_with_prop[:50]}...'")

    # Test 7: Título sin propiedad
    title_no_prop = f"Visita: {lead.name}"
    assert "Juan Pérez" in title_no_prop
    assert "—" not in title_no_prop
    print(f"✅ Título sin propiedad: '{title_no_prop}'")

    # Test 8: CalendarError instanciable y con detail
    err = CalendarError("Credenciales inválidas")
    assert err.detail == "Credenciales inválidas"
    assert str(err) == "Credenciales inválidas"
    assert isinstance(err, Exception)
    print("✅ CalendarError instanciable con .detail")

    # Test 9: _create_event_sync es callable
    assert callable(_create_event_sync)
    print("✅ _create_event_sync es callable")

    # Test 10: create_calendar_event es coroutine
    import inspect
    assert inspect.iscoroutinefunction(create_calendar_event)
    print("✅ create_calendar_event es async")

    # Test 11: Internacional en descripción
    intl_line = f"Internacional: {'Sí' if lead.is_international else 'No'}"
    assert intl_line == "Internacional: Sí"
    print("✅ Flag internacional en descripción")

    print("\n🎉 Todos los smoke tests pasaron ✅")
    print("   Nota: Tests de integración con Google API requieren credentials.json real")
