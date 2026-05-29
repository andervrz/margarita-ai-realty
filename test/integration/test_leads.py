# tests/integration/test_leads.py
"""Tests de integración de captura y gestión de leads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models.lead import Lead


async def test_create_lead_persists(db_session, test_tenant, test_chat_session):
    """Lead válido se persiste en DB con todos los campos."""
    from app.leads.service import create_lead
    from app.schemas.lead import LeadCreate

    lead_data = LeadCreate(
        name="Carlos Rodríguez",
        email="carlos@test.com",
        phone="+584161234567",
        preferred_date="2027-08-15",
        preferred_time="14:00",
        visit_duration_minutes=60,
        notes="Interesado en vista al mar",
    )

    lead = await create_lead(
        session=db_session,
        lead_data=lead_data,
        session_id=test_chat_session.id,
        tenant_id=test_tenant.id,
        qualification_score=80,
    )

    assert lead.id is not None
    assert lead.name == "Carlos Rodríguez"
    assert lead.email == "carlos@test.com"
    assert lead.status == "pendiente"
    assert lead.qualification_score == 80

    # Verificar en DB
    db_lead = (await db_session.execute(
        select(Lead).where(Lead.id == lead.id)
    )).scalar_one_or_none()
    assert db_lead is not None
    assert db_lead.name == "Carlos Rodríguez"


def test_lead_create_invalid_email():
    """Email inválido → ValidationError antes de DB."""
    from app.schemas.lead import LeadCreate
    with pytest.raises(ValidationError) as exc_info:
        LeadCreate(
            name="Test",
            email="not-an-email",
            phone="+584161234567",
            preferred_date="2027-08-15",
            preferred_time="14:00",
        )
    assert "email" in str(exc_info.value).lower()


def test_lead_create_past_date():
    """Fecha en el pasado → ValidationError."""
    from app.schemas.lead import LeadCreate
    with pytest.raises(ValidationError):
        LeadCreate(
            name="Test",
            email="test@test.com",
            phone="+584161234567",
            preferred_date="2020-01-01",  # pasado
            preferred_time="10:00",
        )


def test_lead_create_invalid_time_format():
    """Hora en formato incorrecto → ValidationError."""
    from app.schemas.lead import LeadCreate
    with pytest.raises(ValidationError):
        LeadCreate(
            name="Test",
            email="test@test.com",
            phone="+584161234567",
            preferred_date="2027-08-15",
            preferred_time="25:00",  # hora inválida
        )


async def test_update_lead_status(db_session, test_lead):
    """update_lead_status cambia el estado y actualiza updated_at."""
    from app.leads.service import update_lead_status

    original_updated_at = test_lead.updated_at
    updated = await update_lead_status(
        session=db_session,
        lead_id=test_lead.id,
        tenant_id=test_lead.tenant_id,
        new_status="confirmado",
    )

    assert updated.status == "confirmado"
    assert updated.updated_at != original_updated_at


async def test_lead_visit_duration_persists(
    db_session, test_tenant, test_chat_session
):
    """visit_duration_minutes se persiste correctamente."""
    from app.leads.service import create_lead
    from app.schemas.lead import LeadCreate

    lead = await create_lead(
        session=db_session,
        lead_data=LeadCreate(
            name="Test",
            email="t@t.com",
            phone="+584161234567",
            preferred_date="2027-08-15",
            preferred_time="10:00",
            visit_duration_minutes=90,
        ),
        session_id=test_chat_session.id,
        tenant_id=test_tenant.id,
        qualification_score=75,
    )

    db_lead = (await db_session.execute(
        select(Lead).where(Lead.id == lead.id)
    )).scalar_one()
    assert db_lead.visit_duration_minutes == 90
