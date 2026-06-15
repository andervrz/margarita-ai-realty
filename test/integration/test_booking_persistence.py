# test/integration/test_booking_persistence.py
"""Tests del flujo de booking simplificado + persistencia del lead."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.chat.engine import _advance_booking_flow, _finalize_booking
from app.chat.memory import SessionMemory
from app.leads.services import get_leads_by_session
from app.notification.dispatcher import NotificationResult
from app.qualification.score import calculate_qualification_score


# ── Máquina de pasos (sin DB) ─────────────────────────────────────

def test_booking_step_machine_collects_all_fields():
    """nombre → phone → email → date → confirm, capturando cada respuesta."""
    memory = SessionMemory(
        session_id="s1", tenant_id="t1",
        is_booking_active=True, booking_step="nombre",
    )

    _advance_booking_flow(memory, "es", "Juan Pérez")
    assert memory.booking_step == "phone"

    _advance_booking_flow(memory, "es", "+58 414 1234567")
    assert memory.booking_step == "email"

    _advance_booking_flow(memory, "es", "juan@test.com")
    assert memory.booking_step == "date"

    _advance_booking_flow(memory, "es", "el viernes")
    assert memory.booking_step == "confirm"

    assert memory.booking_data == {
        "name": "Juan Pérez",
        "phone": "+584141234567",
        "email": "juan@test.com",
        "preferred_date": "el viernes",
    }

    result = _advance_booking_flow(memory, "es", "sí, confirmo")
    assert result.booking_complete is True
    assert memory.is_booking_active is False
    assert "se comunicará contigo" in result.final_text


def test_booking_invalid_email_reasks_same_step():
    memory = SessionMemory(
        session_id="s2", tenant_id="t1",
        is_booking_active=True, booking_step="email",
    )
    result = _advance_booking_flow(memory, "es", "no-soy-un-email")
    assert memory.booking_step == "email"  # no avanzó
    assert "email" not in memory.booking_data
    assert "@" in result.final_text or "correo" in result.final_text.lower()


def test_booking_date_is_optional_non_blocking():
    memory = SessionMemory(
        session_id="s3", tenant_id="t1",
        is_booking_active=True, booking_step="date",
    )
    _advance_booking_flow(memory, "es", "no sé aún")
    assert memory.booking_step == "confirm"  # avanzó igual
    assert memory.booking_data["preferred_date"] == "Por confirmar"


# ── Persistencia (con DB) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_booking_persists_lead(
    db_session, test_tenant, test_chat_session, test_property
):
    memory = SessionMemory(
        session_id=test_chat_session.id, tenant_id=test_tenant.id,
    )
    memory.booking_data = {
        "name": "María González",
        "email": "maria@test.com",
        "phone": "+584141234567",
        "preferred_date": "el viernes",
    }
    memory.last_properties = [
        {"id": test_property.id, "title": test_property.title}
    ]
    qual = calculate_qualification_score([], "", "es")

    with patch(
        "app.chat.engine.dispatch_booking_notifications", new_callable=AsyncMock
    ) as mock_disp:
        mock_disp.return_value = NotificationResult(
            whatsapp_success=True, email_success=True,
            whatsapp_error=None, email_error=None, duration_ms=1.0,
        )
        await _finalize_booking(db_session, test_tenant.id, memory, qual)

    leads = await get_leads_by_session(db_session, test_chat_session.id, test_tenant.id)
    assert len(leads) == 1
    lead = leads[0]
    assert lead.name == "María González"
    assert lead.email == "maria@test.com"
    assert lead.property_id == test_property.id
    assert lead.preferred_date == "el viernes"
    assert test_property.title in (lead.notes or "")
    # booking_data se limpia tras persistir
    assert memory.booking_data == {}
    # se intentó notificar al agente
    mock_disp.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_booking_skips_when_incomplete(
    db_session, test_tenant, test_chat_session
):
    """Sin email/teléfono no se persiste lead (no rompe)."""
    memory = SessionMemory(
        session_id=test_chat_session.id, tenant_id=test_tenant.id,
    )
    memory.booking_data = {"name": "Solo Nombre"}
    qual = calculate_qualification_score([], "", "es")

    await _finalize_booking(db_session, test_tenant.id, memory, qual)

    leads = await get_leads_by_session(db_session, test_chat_session.id, test_tenant.id)
    assert len(leads) == 0
    assert memory.booking_data == {}
