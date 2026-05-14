# src/app/notifications/dispatcher.py
"""Notification Dispatcher — envío async multi-canal con timeouts y logging.

Responsabilidades:
  1. Disparar WhatsApp (prioridad 1) y Email (prioridad 2) en paralelo
  2. Aplicar timeouts estrictos por canal
  3. Manejar fallos graceful: un canal fallido no detiene el otro
  4. Logging de éxito/fallo por canal
  5. Retornar resultado consolidado para tracking en DB

Principios:
  - asyncio.gather(return_exceptions=True): paralelismo sin propagación de errores
  - Timeouts estrictos: cada canal tiene límite máximo de espera
  - Fallo en uno no detiene el otro: WhatsApp caído → Email igual se envía
  - Observabilidad: cada canal loggeado independientemente
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
import time

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.models.lead import Lead
from src.app.db.models.property import Property
from src.app.db.models.tenant import Tenant
from src.app.notifications.whatsapp import send_booking_whatsapp
from src.app.notifications.email import send_booking_email

logger = get_logger()


# ── Dataclass de resultado ────────────────────────────────────────

@dataclass(frozen=True)
class NotificationResult:
    """Resultado del dispatch multi-canal."""
    whatsapp_success: bool
    email_success: bool
    whatsapp_error: str | None
    email_error: str | None
    duration_ms: float


# ── Función principal ─────────────────────────────────────────────

async def dispatch_booking_notifications(
    lead: Lead,
    tenant: Tenant,
    prop: Property | None = None,
) -> NotificationResult:
    """Dispara notificaciones de booking al agente por WhatsApp + Email.
    
    Args:
        lead: Lead creado con datos de visita.
        tenant: Configuración del tenant (flags de canales, contactos).
        prop: Propiedad asociada (opcional, para contexto en mensaje).
    
    Returns:
        NotificationResult con estado de cada canal.
    """
    settings = get_settings()
    start_time = time.monotonic()
    
    # Construir lista de tareas según configuración del tenant
    tasks: list[asyncio.Task] = []
    task_labels: list[str] = []
    
    if tenant.whatsapp_enabled and tenant.agent_whatsapp:
        tasks.append(
            asyncio.create_task(
                _send_whatsapp_with_timeout(
                    lead, tenant, prop, settings.external_api_timeout
                )
            )
        )
        task_labels.append("whatsapp")
    
    if tenant.email_enabled and tenant.agent_email:
        tasks.append(
            asyncio.create_task(
                _send_email_with_timeout(
                    lead, tenant, prop, settings.external_api_timeout
                )
            )
        )
        task_labels.append("email")
    
    # Si ningún canal habilitado, retornar early
    if not tasks:
        logger.warning(
            "notification_no_channels",
            lead_id=str(lead.id),
            tenant_id=str(tenant.id),
            whatsapp_enabled=tenant.whatsapp_enabled,
            email_enabled=tenant.email_enabled,
        )
        return NotificationResult(
            whatsapp_success=False,
            email_success=False,
            whatsapp_error=None,
            email_error=None,
            duration_ms=0.0,
        )
    
    # Ejecutar en paralelo, capturando excepciones
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Procesar resultados
    whatsapp_success = False
    email_success = False
    whatsapp_error: str | None = None
    email_error: str | None = None
    
    for label, result in zip(task_labels, results):
        if isinstance(result, Exception):
            logger.error(
                "notification_failed",
                channel=label,
                lead_id=str(lead.id),
                tenant_id=str(tenant.id),
                error_type=type(result).__name__,
                error=str(result),
            )
            if label == "whatsapp":
                whatsapp_error = str(result)
            else:
                email_error = str(result)
        else:
            logger.info(
                "notification_sent",
                channel=label,
                lead_id=str(lead.id),
                tenant_id=str(tenant.id),
            )
            if label == "whatsapp":
                whatsapp_success = True
            else:
                email_success = True
    
    duration_ms = (time.monotonic() - start_time) * 1000
    
    logger.info(
        "notification_dispatch_complete",
        lead_id=str(lead.id),
        tenant_id=str(tenant.id),
        whatsapp_success=whatsapp_success,
        email_success=email_success,
        duration_ms=round(duration_ms, 2),
    )
    
    return NotificationResult(
        whatsapp_success=whatsapp_success,
        email_success=email_success,
        whatsapp_error=whatsapp_error,
        email_error=email_error,
        duration_ms=round(duration_ms, 2),
    )


# ── Helpers con timeout ───────────────────────────────────────────

async def _send_whatsapp_with_timeout(
    lead: Lead,
    tenant: Tenant,
    prop: Property | None,
    timeout: int,
) -> None:
    """Envía WhatsApp con timeout estricto."""
    await asyncio.wait_for(
        send_booking_whatsapp(lead, tenant, prop),
        timeout=timeout,
    )


async def _send_email_with_timeout(
    lead: Lead,
    tenant: Tenant,
    prop: Property | None,
    timeout: int,
) -> None:
    """Envía Email con timeout estricto."""
    await asyncio.wait_for(
        send_booking_email(lead, tenant, prop),
        timeout=timeout,
    )


# ── Función de conveniencia ─────────────────────────────────────

async def send_single_notification(
    channel: str,
    lead: Lead,
    tenant: Tenant,
    prop: Property | None = None,
) -> tuple[bool, str | None]:
    """Envía notificación por un solo canal (para reintentos).
    
    Args:
        channel: "whatsapp" | "email".
    
    Returns:
        (success, error_message).
    """
    settings = get_settings()
    
    try:
        if channel == "whatsapp":
            await _send_whatsapp_with_timeout(lead, tenant, prop, settings.external_api_timeout)
            return True, None
        elif channel == "email":
            await _send_email_with_timeout(lead, tenant, prop, settings.external_api_timeout)
            return True, None
        else:
            return False, f"Unknown channel: {channel}"
    except Exception as exc:
        logger.error(
            "notification_single_failed",
            channel=channel,
            lead_id=str(lead.id),
            tenant_id=str(tenant.id),
            error=str(exc),
        )
        return False, str(exc)


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    
    async def _test():
        print("🔥 Smoke Test — notifications/dispatcher.py")
        
        # Test 1: NotificationResult dataclass
        result = NotificationResult(
            whatsapp_success=True,
            email_success=False,
            whatsapp_error=None,
            email_error="timeout",
            duration_ms=123.4,
        )
        assert result.whatsapp_success is True
        assert result.email_success is False
        assert result.email_error == "timeout"
        print("  ✅ NotificationResult construido")
        
        # Test 2: dispatch sin canales habilitados
        mock_tenant = MagicMock()
        mock_tenant.whatsapp_enabled = 0
        mock_tenant.email_enabled = 0
        mock_tenant.agent_whatsapp = None
        mock_tenant.agent_email = None
        
        mock_lead = MagicMock()
        mock_lead.id = "lead-123"
        
        result_empty = await dispatch_booking_notifications(mock_lead, mock_tenant)
        assert result_empty.whatsapp_success is False
        assert result_empty.email_success is False
        assert result_empty.whatsapp_error == "No channels enabled"
        print("  ✅ Sin canales habilitados → early return")
        
        # Test 3: dispatch con ambos canales (mock)
        mock_tenant_full = MagicMock()
        mock_tenant_full.whatsapp_enabled = 1
        mock_tenant_full.email_enabled = 1
        mock_tenant_full.agent_whatsapp = "+584120000000"
        mock_tenant_full.agent_email = "agent@test.com"
        mock_tenant_full.id = "tenant-123"
        
        with patch("src.app.notifications.dispatcher._send_whatsapp_with_timeout", new_callable=AsyncMock) as mock_wa, \
             patch("src.app.notifications.dispatcher._send_email_with_timeout", new_callable=AsyncMock) as mock_email:
            
            result_full = await dispatch_booking_notifications(mock_lead, mock_tenant_full)
            assert result_full.whatsapp_success is True
            assert result_full.email_success is True
            assert mock_wa.called
            assert mock_email.called
            print("  ✅ Ambos canales ejecutados en paralelo")
        
        # Test 4: Un canal falla, el otro continúa
        with patch("src.app.notifications.dispatcher._send_whatsapp_with_timeout", side_effect=Exception("WA down")), \
             patch("src.app.notifications.dispatcher._send_email_with_timeout", new_callable=AsyncMock) as mock_email_ok:
            
            result_partial = await dispatch_booking_notifications(mock_lead, mock_tenant_full)
            assert result_partial.whatsapp_success is False
            assert result_partial.email_success is True
            assert "WA down" in (result_partial.whatsapp_error or "")
            print("  ✅ Fallo WhatsApp → Email continúa")
        
        # Test 5: send_single_notification
        with patch("src.app.notifications.dispatcher._send_whatsapp_with_timeout", new_callable=AsyncMock):
            success, error = await send_single_notification("whatsapp", mock_lead, mock_tenant_full)
            assert success is True
            assert error is None
            print("  ✅ send_single_notification whatsapp")
        
        # Test 6: Canal desconocido
        success, error = await send_single_notification("sms", mock_lead, mock_tenant_full)
        assert success is False
        assert "Unknown channel" in (error or "")
        print("  ✅ Rechaza canal desconocido")
        
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())
