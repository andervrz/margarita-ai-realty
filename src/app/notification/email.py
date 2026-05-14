# src/app/notifications/email.py
"""Email Notifications — envío de notificaciones via SMTP async (aiosmtplib).

Responsabilidades:
  1. Construir email HTML de notificación de booking para el agente
  2. Enviar via aiosmtplib (async, no bloquea event loop)
  3. Manejar errores de SMTP: auth, connection, timeout
  4. Formato HTML responsive con datos del lead y propiedad

Principios:
  - Email es prioridad 2 (backup de WhatsApp)
  - HTML limpio y accionable: el agente puede reenviar o archivar
  - Texto plano alternativo para clientes que no renderizan HTML
  - Timeout estricto: no bloquear el booking flow
"""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
import uuid

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.models.lead import Lead
from src.app.db.models.property import Property
from src.app.db.models.tenant import Tenant

logger = get_logger(__name__)

# ── Excepciones ───────────────────────────────────────────────────

class EmailSMTPError(Exception):
    """Error en comunicación SMTP."""
    pass


# ── Configuración ─────────────────────────────────────────────────




# ── Función principal ─────────────────────────────────────────────

async def send_booking_email(
    lead: Lead,
    tenant: Tenant,
    prop: Property | None = None,
) -> str:
    """Envía notificación de booking al agente por email.
    
    Args:
        lead: Lead creado con datos de visita.
        tenant: Tenant con agent_email y config SMTP.
        property: Propiedad asociada (opcional).
    
    Returns:
        Message-ID del email enviado.
    
    Raises:
        EmailSMTPError: Si la conexión SMTP falla.
    """
    settings = get_settings()
    if not tenant.agent_email:
        raise EmailSMTPError("Tenant no tiene configurado agent_email")
    
    if not settings.smtp_user or not settings.smtp_password:
        raise EmailSMTPError("SMTP no configurado en variables de entorno")
    
    # Construir mensaje
    msg = _build_email_message(lead, tenant, prop)
    
    try:
        async with aiosmtplib.SMTP(
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            use_tls=False,  # Gmail usa STARTTLS
        ) as smtp:
            await smtp.starttls()
            await smtp.login(settings.smtp_user, settings.smtp_password)
            
            response = await smtp.send_message(msg)
            
            message_id = msg["Message-ID"] or "unknown"
            
            logger.info(
                "email_notification_sent",
                lead_id=str(lead.id),
                tenant_id=str(tenant.id),
                recipient=tenant.agent_email,
                message_id=message_id,
                smtp_response=str(response),
            )
            
            return message_id
            
    except aiosmtplib.SMTPAuthenticationError as exc:
        logger.error(
            "email_auth_failed",
            lead_id=str(lead.id),
            tenant_id=str(tenant.id),
            smtp_user=settings.smtp_user,
            error=str(exc),
        )
        raise EmailSMTPError(f"SMTP auth failed: {exc}") from exc
        
    except aiosmtplib.SMTPException as exc:
        logger.error(
            "email_smtp_error",
            lead_id=str(lead.id),
            tenant_id=str(tenant.id),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise EmailSMTPError(f"SMTP error: {exc}") from exc
        
    except Exception as exc:
        logger.error(
            "email_unexpected_error",
            lead_id=str(lead.id),
            tenant_id=str(tenant.id),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise EmailSMTPError(f"Unexpected email error: {exc}") from exc


# ── Construcción de email ───────────────────────────────────────

def _build_email_message(
    lead: Lead,
    tenant: Tenant,
    prop: Property | None = None,
) -> MIMEMultipart:
    """Construye mensaje MIME multipart (HTML + texto plano)."""
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = _build_subject(lead, prop)
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    msg["To"] = tenant.agent_email
    msg["Message-ID"] = f"<{uuid.uuid4()}@{settings.smtp_host}>"

  
    # Texto plano
    text_body = _build_text_body(lead, prop)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    
    # HTML
    html_body = _build_html_body(lead, prop)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    msg["Message-ID"] = f"<{uuid.uuid4()}@{settings.smtp_host}>"
    return msg


def _build_subject(lead: Lead, prop: Property | None = None) -> str:
    """Construye asunto del email."""
    prop_info = f" — {prop.title}" if prop else ""
    intl_flag = " [🌍 Internacional]" if lead.is_international else ""
    return f"🔔 Nueva visita{prop_info}{intl_flag} — {lead.name}"


def _build_text_body(lead: Lead, prop: Property | None = None) -> str:
    """Construye cuerpo en texto plano."""
    lines: list[str] = [
        "NUEVA SOLICITUD DE VISITA",
        "=" * 40,
        "",
    ]
    
    if prop:
        lines.extend([
            f"PROPIEDAD: {prop.title}",
            f"ZONA: {prop.location_zone or 'N/A'}",
            f"PRECIO: ${prop.price_usd:,.0f} USD" if prop.price_usd else "PRECIO: Consultar",
            "",
        ])
    
    lines.extend([
        f"NOMBRE: {lead.name}",
        f"EMAIL: {lead.email}",
        f"TELÉFONO: {lead.phone}",
        "",
        f"FECHA: {lead.preferred_date}",
        f"HORA: {lead.preferred_time}",
        f"DURACIÓN: {lead.visit_duration_minutes} minutos",
        "",
    ])
    
    if lead.notes:
        lines.extend([
            f"NOTAS: {lead.notes}",
            "",
        ])
    
    lines.extend([
        f"SCORE: {lead.qualification_score}/100" if lead.qualification_score else "SCORE: N/A",
        f"INTERNACIONAL: {'Sí' if lead.is_international else 'No'}",
        "",
        "—",
        "Enviado por Chatbot Inmobiliario Margarita",
    ])
    
    return "\n".join(lines)


def _build_html_body(lead: Lead, prop: Property | None = None) -> str:
    """Construye cuerpo HTML responsive."""
    
    # Propiedad card
    property_card = ""
    if prop:
        price_str = f"${prop.price_usd:,.0f} USD" if prop.price_usd else "Consultar"
        property_card = f"""
        <div style="background:#f8f9fa;border-radius:8px;padding:16px;margin:16px 0;">
            <h3 style="margin:0 0 8px 0;color:#2c3e50;">🏠 {prop.title}</h3>
            <p style="margin:4px 0;color:#666;">📍 {prop.location_zone or 'Zona no especificada'}</p>
            <p style="margin:4px 0;color:#27ae60;font-weight:bold;">💰 {price_str}</p>
        </div>
        """
    
    # Score badge
    score_color = "#27ae60" if (lead.qualification_score or 0) >= 75 else "#f39c12"
    score_emoji = "🔥" if (lead.qualification_score or 0) >= 75 else "⭐"
    score_html = f"""
    <span style="background:{score_color};color:white;padding:4px 12px;border-radius:12px;font-size:14px;">
        {score_emoji} Score: {lead.qualification_score or 'N/A'}/100
    </span>
    """ if lead.qualification_score else ""
    
    # Flag internacional
    intl_html = """
    <div style="background:#e8f4f8;border-left:4px solid #3498db;padding:12px;margin:16px 0;">
        <p style="margin:0;color:#2980b9;">🌍 <strong>Comprador internacional</strong></p>
        <p style="margin:4px 0 0 0;font-size:14px;color:#666;">Posible inversión turística / resguardo patrimonial</p>
    </div>
    """ if lead.is_international else ""
    
    # Notas
    notes_html = f"""
    <tr>
        <td style="padding:8px 0;color:#666;">📝 Notas:</td>
        <td style="padding:8px 0;">{lead.notes}</td>
    </tr>
    """ if lead.notes else ""
    
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nueva Visita — {lead.name}</title>
</head>
<body style="font-family:Arial,sans-serif;background:#f4f4f4;margin:0;padding:20px;">
    <div style="max-width:600px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
        
        <!-- Header -->
        <div style="background:#2c3e50;color:white;padding:24px;text-align:center;">
            <h1 style="margin:0;font-size:24px;">🔔 Nueva Solicitud de Visita</h1>
            <p style="margin:8px 0 0 0;opacity:0.9;">Chatbot Inmobiliario Margarita</p>
        </div>
        
        <!-- Content -->
        <div style="padding:24px;">
            {property_card}
            
            <!-- Lead Info -->
            <table style="width:100%;border-collapse:collapse;">
                <tr>
                    <td style="padding:8px 0;color:#666;width:120px;">👤 Nombre:</td>
                    <td style="padding:8px 0;font-weight:bold;">{lead.name}</td>
                </tr>
                <tr>
                    <td style="padding:8px 0;color:#666;">📧 Email:</td>
                    <td style="padding:8px 0;">{lead.email}</td>
                </tr>
                <tr>
                    <td style="padding:8px 0;color:#666;">📱 Teléfono:</td>
                    <td style="padding:8px 0;">{lead.phone}</td>
                </tr>
                <tr><td colspan="2" style="height:16px;"></td></tr>
                <tr>
                    <td style="padding:8px 0;color:#666;">📅 Fecha:</td>
                    <td style="padding:8px 0;">{lead.preferred_date}</td>
                </tr>
                <tr>
                    <td style="padding:8px 0;color:#666;">🕐 Hora:</td>
                    <td style="padding:8px 0;">{lead.preferred_time}</td>
                </tr>
                <tr>
                    <td style="padding:8px 0;color:#666;">⏱️ Duración:</td>
                    <td style="padding:8px 0;">{lead.visit_duration_minutes} minutos</td>
                </tr>
                {notes_html}
            </table>
            
            {intl_html}
            
            <!-- Score -->
            <div style="margin-top:20px;text-align:center;">
                {score_html}
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background:#f8f9fa;padding:16px;text-align:center;font-size:12px;color:#999;">
            <p>Enviado por Chatbot Inmobiliario Margarita • {lead.created_at[:10] if hasattr(lead, 'created_at') else ''}</p>
        </div>
    </div>
</body>
</html>"""



# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    
    async def _test():
        print("🔥 Smoke Test — notifications/email.py")
        
        # Test 1: Construcción asunto
        mock_lead = MagicMock()
        mock_lead.name = "María González"
        mock_lead.is_international = True
        mock_lead.qualification_score = 85
        
        mock_prop = MagicMock()
        mock_prop.title = "Apto Pampatar"
        
        subject = _build_subject(mock_lead, mock_prop)
        assert "Nueva visita" in subject
        assert "Apto Pampatar" in subject
        assert "🌍" in subject
        assert "María González" in subject
        print(f"  ✅ Asunto: {subject}")
        
        # Test 2: Asunto sin propiedad
        subject_no_prop = _build_subject(mock_lead, None)
        assert "🌍" in subject_no_prop
        assert "Apto" not in subject_no_prop
        print("  ✅ Asunto sin propiedad")
        
        # Test 3: Asunto local
        mock_lead.is_international = 0
        subject_local = _build_subject(mock_lead, mock_prop)
        assert "🌍" not in subject_local
        print("  ✅ Asunto comprador local")
        
        # Test 4: Texto plano
        text = _build_text_body(mock_lead, mock_prop)
        assert "María González" in text
        assert "Pampatar" in text
        assert "NUEVA SOLICITUD" in text
        print("  ✅ Texto plano construido")
        
        # Test 5: HTML body
        html = _build_html_body(mock_lead, mock_prop)
        assert "<html>" in html
        assert "María González" in html
        assert "🔥" in html  # Score alto
        assert "internacional" in html
        assert "<table" in html
        print("  ✅ HTML body construido")
        
        # Test 6: HTML sin propiedad
        html_no_prop = _build_html_body(mock_lead, None)
        assert "🏠" not in html_no_prop  # No hay card propiedad
        print("  ✅ HTML sin propiedad")
        
        # Test 7: HTML sin notas
        mock_lead.notes = None
        html_no_notes = _build_html_body(mock_lead, mock_prop)
        assert "Notas:" not in html_no_notes
        print("  ✅ HTML sin notas")
        
        # Test 8: Email MIME multipart
        mock_tenant = MagicMock()
        mock_tenant.agent_email = "agente@test.com"
        
        with patch.object(settings, "smtp_user", "bot@test.com"), \
             patch.object(settings, "smtp_from_name", "Chatbot Margarita"):
            
            msg = _build_email_message(mock_lead, mock_tenant, mock_prop)
            assert msg["To"] == "agente@test.com"
            assert "Chatbot Margarita" in msg["From"]
            assert "Nueva visita" in msg["Subject"]
            assert msg.is_multipart()
            print("  ✅ MIME multipart construido")
        
        # Test 9: Score bajo en HTML
        mock_lead.qualification_score = 45
        mock_lead.is_international = 0
        html_low = _build_html_body(mock_lead, mock_prop)
        assert "⭐" in html_low
        assert "🔥" not in html_low
        print("  ✅ Score bajo (⭐) en HTML")
        
        # Test 10: Excepción EmailSMTPError
        exc = EmailSMTPError("SMTP down")
        assert str(exc) == "SMTP down"
        print("  ✅ EmailSMTPError")
        
        # Test 11: Tenant sin email
        mock_tenant_no_email = MagicMock()
        mock_tenant_no_email.agent_email = None
        
        try:
            await send_booking_email(mock_lead, mock_tenant_no_email)
            assert False, "Debería fallar"
        except EmailSMTPError as exc:
            assert "no tiene configurado" in str(exc)
            print("  ✅ Rechaza tenant sin email")
        
        # Test 12: Mock envío SMTP exitoso
        mock_tenant_ok = MagicMock()
        mock_tenant_ok.agent_email = "agente@test.com"
        mock_tenant_ok.id = "tenant-123"
        
        with patch("aiosmtplib.SMTP") as mock_smtp_class:
            mock_smtp = AsyncMock()
            mock_smtp_class.return_value.__aenter__ = AsyncMock(return_value=mock_smtp)
            mock_smtp_class.return_value.__aexit__ = AsyncMock(return_value=False)
            
            with patch.object(settings, "smtp_user", "bot@test.com"), \
                 patch.object(settings, "smtp_password", "secret"):
                
                msg_id = await send_booking_email(mock_lead, mock_tenant_ok)
                assert msg_id is not None
                assert mock_smtp.login.called
                assert mock_smtp.send_message.called
                print("  ✅ Envío SMTP mock exitoso")
        
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())
