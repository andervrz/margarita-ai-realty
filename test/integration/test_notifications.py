# tests/integration/test_notifications.py
"""Tests de integración de notificaciones (mock httpx + mock aiosmtplib)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_tenant_obj(tenant_dict: dict):
    class _T:
        pass
    obj = _T()
    for k, v in tenant_dict.items():
        setattr(obj, k, v)
    return obj


async def test_email_notification_sends(test_lead, test_tenant, test_property):
    """Email se envía con asunto y destinatario correctos."""
    from app.notifications.email import send_booking_email

    tenant_obj = _make_tenant_obj({
        "agent_email": "agente@test.com",
        "name": "Test Inmobiliaria",
    })

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await send_booking_email(
            lead=test_lead,
            tenant=tenant_obj,
            prop=test_property,
        )
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        # El mensaje enviado debe ir al agente
        message = call_args[0][0]
        assert "agente@test.com" in str(message)


async def test_whatsapp_notification_sends(test_lead, test_tenant, test_property):
    """WhatsApp payload correcto para Meta API."""
    from app.notifications.whatsapp import send_booking_whatsapp

    tenant_obj = _make_tenant_obj({
        "agent_whatsapp": "+584120000000",
        "whatsapp_phone_id": "123456789",
    })

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        await send_booking_whatsapp(
            lead=test_lead,
            tenant=tenant_obj,
            prop=test_property,
        )

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        # Verificar que el payload tiene el formato de Meta API
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json", {})
        assert payload.get("messaging_product") == "whatsapp"
        assert payload.get("to") == "+584120000000"


async def test_dispatcher_sends_both(test_lead, test_tenant, test_property):
    """Dispatcher invoca WhatsApp y Email en paralelo."""
    from app.notifications.dispatcher import dispatch_booking_notifications

    tenant_obj = _make_tenant_obj({
        "agent_email": "agente@test.com",
        "agent_whatsapp": "+584120000000",
        "whatsapp_phone_id": "123456789",
        "whatsapp_enabled": True,
        "email_enabled": True,
        "name": "Test",
    })

    whatsapp_called = []
    email_called = []

    async def mock_whatsapp(*args, **kwargs):
        whatsapp_called.append(True)

    async def mock_email(*args, **kwargs):
        email_called.append(True)

    with patch("app.notifications.dispatcher.send_booking_whatsapp", mock_whatsapp):
        with patch("app.notifications.dispatcher.send_booking_email", mock_email):
            result = await dispatch_booking_notifications(
                lead=test_lead,
                tenant=tenant_obj,
                prop=test_property,
            )

    assert len(whatsapp_called) == 1
    assert len(email_called) == 1


async def test_dispatcher_email_continues_if_whatsapp_fails(
    test_lead, test_tenant, test_property
):
    """Fallo en WhatsApp no bloquea envío de email."""
    from app.notifications.dispatcher import dispatch_booking_notifications

    tenant_obj = _make_tenant_obj({
        "agent_email": "agente@test.com",
        "agent_whatsapp": "+584120000000",
        "whatsapp_phone_id": "123456789",
        "whatsapp_enabled": True,
        "email_enabled": True,
        "name": "Test",
    })

    email_called = []

    async def mock_whatsapp_fail(*args, **kwargs):
        raise Exception("WhatsApp API caída")

    async def mock_email(*args, **kwargs):
        email_called.append(True)

    with patch("app.notifications.dispatcher.send_booking_whatsapp", mock_whatsapp_fail):
        with patch("app.notifications.dispatcher.send_booking_email", mock_email):
            # No debe lanzar excepción aunque WhatsApp falle
            result = await dispatch_booking_notifications(
                lead=test_lead,
                tenant=tenant_obj,
                prop=test_property,
            )

    # Email se envió aunque WhatsApp falló
    assert len(email_called) == 1
