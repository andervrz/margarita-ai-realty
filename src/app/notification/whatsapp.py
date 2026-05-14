# src/app/notifications/whatsapp.py
"""WhatsApp Notifications — envío de notificaciones via Meta Cloud API.

Responsabilidades:
  1. Construir mensaje de notificación de booking para el agente
  2. Enviar mensaje via Meta Cloud API (httpx async)
  3. Manejar errores de API: rate limits, números inválidos, auth
  4. Formatear mensaje con emojis y contexto de Margarita

Principios:
  - WhatsApp es prioridad 1: los agentes de Margarita viven en WhatsApp
  - Mensaje claro y accionable: el agente debe entender en 5 segundos
  - Flag internacional destacado: 🌍 para compradores del exterior
  - Timeout estricto: no bloquear el booking flow
"""

from __future__ import annotations

import httpx

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.models.lead import Lead
from src.app.db.models.property import Property
from src.app.db.models.tenant import Tenant

logger = get_logger(__name__)


# ── Excepciones ───────────────────────────────────────────────────

class WhatsAppAPIError(Exception):
    """Error en comunicación con Meta WhatsApp Cloud API."""
    pass



# ── Función principal ─────────────────────────────────────────────

async def send_booking_whatsapp(
    lead: Lead,
    tenant: Tenant,
    prop: Property | None = None,
) -> str:
    """Envía notificación de booking al agente por WhatsApp.
    
    Args:
        lead: Lead creado con datos de visita.
        tenant: Tenant con whatsapp_phone_id y agent_whatsapp.
        property: Propiedad asociada (opcional).
    
    Returns:
        message_id de WhatsApp si éxito.
    
    Raises:
        WhatsAppAPIError: Si la API de Meta falla.
    """
    settings = get_settings()
    
    if not tenant.whatsapp_phone_id or not tenant.agent_whatsapp:
        raise WhatsAppAPIError("Tenant no tiene configurado WhatsApp phone_id o agent_whatsapp")
    
    message = _build_whatsapp_message(lead, prop)
    
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}/"
        f"{tenant.whatsapp_phone_id}/messages"
    )
    
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": tenant.agent_whatsapp,
        "type": "text",
        "text": {"body": message},
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers=headers,
            json=payload,
            timeout=settings.external_api_timeout,
        )
        
        if response.status_code != 200:
            error_data = response.json() if response.content else {}
            raise WhatsAppAPIError(
                f"Meta API error {response.status_code}: {error_data.get('error', {}).get('message', response.text)}"
            )
        
        data = response.json()
        message_id = data.get("messages", [{}])[0].get("id", "unknown")
        
        logger.info(
            "whatsapp_notification_sent",
            lead_id=str(lead.id),
            tenant_id=str(tenant.id),
            message_id=message_id,
            recipient=tenant.agent_whatsapp,
        )
        
        return message_id


# ── Construcción de mensaje ─────────────────────────────────────

def _build_whatsapp_message(
    lead: Lead,
    prop: Property | None = None,
) -> str:
    """Construye mensaje formateado para WhatsApp del agente.
    
    Formato:
      🔔 Nueva solicitud de visita
      🏠 [Propiedad]
      👤 Nombre
      📧 Email
      📱 Teléfono
      📅 Fecha | 🕐 Hora | ⏱️ Duración
      📝 Notas
      🌍 [Flag internacional]
    """
    lines: list[str] = []
    
    # Header
    lines.append("🔔 *Nueva solicitud de visita*")
    lines.append("")
    
    # Propiedad
    if prop:
        lines.append(f"🏠 *{prop.title}*")
        if prop.location_zone:
            lines.append(f"📍 {prop.location_zone}")
        if prop.price_usd:
            lines.append(f"💰 ${prop.price_usd:,.0f} USD")
        lines.append("")
    
    # Datos del lead
    lines.append(f"👤 *Nombre:* {lead.name}")
    lines.append(f"📧 *Email:* {lead.email}")
    lines.append(f"📱 *Teléfono:* {lead.phone}")
    lines.append("")
    
    # Visita
    lines.append(f"📅 *Fecha:* {lead.preferred_date}")
    lines.append(f"🕐 *Hora:* {lead.preferred_time}")
    lines.append(f"⏱️ *Duración:* {lead.visit_duration_minutes} min")
    lines.append("")
    
    # Notas
    if lead.notes:
        lines.append(f"📝 *Notas:* {lead.notes}")
        lines.append("")
    
    # Score de calificación
    if lead.qualification_score:
        score_emoji = "🔥" if lead.qualification_score >= 75 else "⭐"
        lines.append(f"{score_emoji} *Score:* {lead.qualification_score}/100")
        lines.append("")
    
    # Flag internacional
    if lead.is_international:
        lines.append("🌍 *Comprador internacional*")
        lines.append("💡 Posible inversión turística / resguardo patrimonial")
        lines.append("")
    
    # Footer
    lines.append("✅ Confirmar visita en el panel de administración.")
    
    return "\n".join(lines)


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    
    async def _test():
        print("🔥 Smoke Test — notifications/whatsapp.py")
        
        # Test 1: Construcción mensaje completo
        mock_lead = MagicMock()
        mock_lead.name = "María González"
        mock_lead.email = "maria@test.com"
        mock_lead.phone = "+584141234567"
        mock_lead.preferred_date = "2026-06-15"
        mock_lead.preferred_time = "10:00"
        mock_lead.visit_duration_minutes = 90
        mock_lead.notes = "Verificar vista al mar"
        mock_lead.qualification_score = 85
        mock_lead.is_international = 1
        
        mock_prop = MagicMock()
        mock_prop.title = "Apartamento 3H/2B Pampatar"
        mock_prop.location_zone = "Pampatar"
        mock_prop.price_usd = 150000
        
        msg = _build_whatsapp_message(mock_lead, mock_prop)
        
        assert "Nueva solicitud de visita" in msg
        assert "María González" in msg
        assert "Pampatar" in msg
        assert "$150,000" in msg or "150000" in msg
        assert "90 min" in msg
        assert "Verificar vista al mar" in msg
        assert "Score:" in msg and "85" in msg
        assert "🌍" in msg
        assert "internacional" in msg
        print("  ✅ Mensaje completo construido")
        
        # Test 2: Mensaje sin propiedad
        msg_no_prop = _build_whatsapp_message(mock_lead, None)
        assert "Nueva solicitud de visita" in msg_no_prop
        assert "María González" in msg_no_prop
        assert "🏠 *" not in msg_no_prop  # No hay sección propiedad
        print("  ✅ Mensaje sin propiedad")
        
        # Test 3: Mensaje sin notas
        mock_lead.notes = None
        msg_no_notes = _build_whatsapp_message(mock_lead, mock_prop)
        assert "Notas:" not in msg_no_notes
        print("  ✅ Mensaje sin notas")
        
        # Test 4: Mensaje local (no internacional)
        mock_lead.is_international = 0
        mock_lead.notes = "Test"
        msg_local = _build_whatsapp_message(mock_lead, mock_prop)
        assert "🌍" not in msg_local
        assert "internacional" not in msg_local
        print("  ✅ Mensaje comprador local")
        
        # Test 5: Score bajo
        mock_lead.qualification_score = 45
        mock_lead.is_international = 1
        msg_low = _build_whatsapp_message(mock_lead, None)
        assert "⭐" in msg_low  # Emoji score bajo
        print("  ✅ Emoji score bajo (⭐)")
        
        # Test 6: Score alto
        mock_lead.qualification_score = 85
        msg_high = _build_whatsapp_message(mock_lead, None)
        assert "🔥" in msg_high  # Emoji score alto
        print("  ✅ Emoji score alto (🔥)")
        
        # Test 7: WhatsAppAPIError
        exc = WhatsAppAPIError("test error")
        assert str(exc) == "test error"
        print("  ✅ Excepción WhatsAppAPIError")
        
        # Test 8: Tenant sin phone_id
        mock_tenant = MagicMock()
        mock_tenant.whatsapp_phone_id = None
        mock_tenant.agent_whatsapp = "+584120000000"
        
        try:
            await send_booking_whatsapp(mock_lead, mock_tenant)
            assert False, "Debería fallar"
        except WhatsAppAPIError as exc:
            assert "no tiene configurado" in str(exc)
            print("  ✅ Rechaza tenant sin phone_id")
        
        # Test 9: Mock de envío exitoso
        mock_tenant_ok = MagicMock()
        mock_tenant_ok.whatsapp_phone_id = "123456789"
        mock_tenant_ok.agent_whatsapp = "+584120000000"
        mock_tenant_ok.id = "tenant-123"
        
        with patch("httpx.AsyncClient") as mock_client_class:
          mock_client = AsyncMock()
          mock_client_class.return_value.__aenter__.return_value = mock_client
          
          mock_response = MagicMock()
          mock_response.status_code = 200
          mock_response.json.return_value = {"messages": [{"id": "wamid.123"}]}
          mock_response.content = b'{"messages":[{"id":"wamid.123"}]}'
          mock_client.post = AsyncMock(return_value=mock_response)
          
          msg_id = await send_booking_whatsapp(mock_lead, mock_tenant_ok)
          assert msg_id == "wamid.123"
          print("  ✅ Envío mock exitoso")
      
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())
