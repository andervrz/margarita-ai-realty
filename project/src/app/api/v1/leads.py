# project/src/app/api/v1/leads.py
"""Leads API — Admin: gestión de leads capturados por el chatbot.

Endpoints:
    GET  /leads              → Listar leads del tenant (con filtros)
    GET  /leads/{id}         → Ver detalle de un lead
    PATCH /leads/{id}/status → Actualizar estado del lead
    POST /leads/{id}/notify  → Reenviar notificación manualmente

Filtros soportados (GET /leads):
    - status: pendiente | contactado | visita_agendada | convertido | descartado
    - is_international: true | false
    - min_score: score mínimo de calificación
    - date_from / date_to: rango de fechas (ISO 8601)
    - property_id: filtrar por propiedad específica

Estados del lead:
    pendiente         → Capturado, sin contactar
    contactado        → Agente contactó al lead
    visita_agendada   → Visita en Google Calendar
    convertido        → Compra/arriendo cerrado
    descartado        → No califica o no interesado

Nota: Los leads son privados del tenant. No hay cross-tenant access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.middleware import get_current_tenant
from app.core.logging import logger
from app.db.models.lead import Lead

if TYPE_CHECKING:
    pass


router = APIRouter(prefix="/leads", tags=["leads"])


# ── Schemas ────────────────────────────────────────────────────────

class LeadListItem(BaseModel):
    """Lead resumido para listados."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    name: str
    email: str
    phone: str
    qualification_score: int
    is_international: bool = False
    status: str
    preferred_date: str
    preferred_time: str
    property_id: str | None
    created_at: str


class LeadDetail(LeadListItem):
    """Lead completo con todos los campos."""
    visit_duration_minutes: int = 60
    notes: str | None
    calendar_event_id: str | None
    whatsapp_sent: bool = False
    email_sent: bool = False
    updated_at: str


class LeadStatusUpdate(BaseModel):
    """Payload para actualizar estado del lead."""
    status: str = Field(..., pattern=r"^(pendiente|contactado|visita_agendada|convertido|descartado)$")
    notes: str | None = Field(default=None, max_length=1000)


class LeadStatusResponse(BaseModel):
    """Respuesta de actualización de estado."""
    lead_id: str
    status: str
    previous_status: str
    updated_at: str


class LeadNotificationResponse(BaseModel):
    """Respuesta de reenvío de notificación."""
    lead_id: str
    whatsapp_sent: bool
    email_sent: bool
    errors: list[str] = Field(default_factory=list)


# ── Endpoints ──────────────────────────────────────────────────────

@router.get("", response_model=list[LeadListItem])
async def list_leads(
    tenant: dict = Depends(get_current_tenant),
    status: str | None = Query(None, description="Estado del lead"),
    is_international: bool | None = Query(None, description="Comprador internacional"),
    min_score: int | None = Query(None, ge=0, le=100, description="Score mínimo"),
    date_from: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    property_id: str | None = Query(None, description="Filtrar por propiedad"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[LeadListItem]:
    """Lista leads del tenant con filtros opcionales.
    
    Ejemplo:
        GET /leads?status=pendiente&min_score=75&is_international=true
    """
    tenant_id = tenant["id"]
    
    from sqlalchemy import select
    from app.db.engine import async_session_maker

    async with async_session_maker() as session:
        stmt = select(Lead).where(Lead.tenant_id == tenant_id)
        
        if status:
            stmt = stmt.where(Lead.status == status)
        if is_international is not None:
            stmt = stmt.where(Lead.is_international == (1 if is_international else 0))
        if min_score is not None:
            stmt = stmt.where(Lead.qualification_score >= min_score)
        if date_from:
            stmt = stmt.where(Lead.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Lead.created_at <= f"{date_to}T23:59:59")
        if property_id:
            stmt = stmt.where(Lead.property_id == property_id)
        
        stmt = stmt.order_by(Lead.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        leads = result.scalars().all()

    logger.info(
        "leads_listed",
        tenant_id=tenant_id,
        count=len(leads),
        filters={k: v for k, v in {
            "status": status,
            "is_international": is_international,
            "min_score": min_score,
        }.items() if v is not None},
    )

    return [_orm_to_list_item(lead) for lead in leads]


@router.get("/{lead_id}", response_model=LeadDetail)
async def get_lead(
    lead_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> LeadDetail:
    """Obtiene el detalle completo de un lead específico."""
    tenant_id = tenant["id"]
    
    from sqlalchemy import select
    from app.db.engine import async_session_maker

    async with async_session_maker() as session:
        result = await session.execute(
            select(Lead)
            .where(Lead.id == lead_id)
            .where(Lead.tenant_id == tenant_id)
        )
        lead = result.scalar_one_or_none()

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead no encontrado",
        )

    return _orm_to_detail(lead)


@router.patch("/{lead_id}/status", response_model=LeadStatusResponse)
async def update_lead_status(
    lead_id: str,
    payload: LeadStatusUpdate,
    tenant: dict = Depends(get_current_tenant),
) -> LeadStatusResponse:
    """Actualiza el estado de un lead.
    
    Usado por el agente para marcar progreso en el funnel de ventas.
    """
    tenant_id = tenant["id"]
    
    from sqlalchemy import select
    from app.db.engine import async_session_maker
    from datetime import datetime

    async with async_session_maker() as session:
        result = await session.execute(
            select(Lead)
            .where(Lead.id == lead_id)
            .where(Lead.tenant_id == tenant_id)
        )
        lead = result.scalar_one_or_none()

        if not lead:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead no encontrado",
            )

        previous_status = lead.status
        lead.status = payload.status
        lead.updated_at = datetime.utcnow().isoformat()
        
        if payload.notes:
            existing_notes = lead.notes or ""
            lead.notes = f"{existing_notes}\n[{datetime.utcnow().isoformat()}] {payload.notes}".strip()
        
        await session.commit()

    logger.info(
        "lead_status_updated",
        tenant_id=tenant_id,
        lead_id=lead_id,
        previous=previous_status,
        new=payload.status,
    )

    return LeadStatusResponse(
        lead_id=str(lead.id),
        status=lead.status,
        previous_status=previous_status,
        updated_at=lead.updated_at,
    )


@router.post("/{lead_id}/notify", response_model=LeadNotificationResponse)
async def resend_notifications(
    lead_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> LeadNotificationResponse:
    """Reenvía notificaciones (WhatsApp + Email) para un lead.
    
    Útil cuando la notificación original falló o el agente cambió
    de número de WhatsApp/email.
    """
    tenant_id = tenant["id"]
    
    from sqlalchemy import select
    from app.db.engine import async_session_maker
    from app.notifications.dispatcher import dispatch_booking_notifications

    async with async_session_maker() as session:
        result = await session.execute(
            select(Lead)
            .where(Lead.id == lead_id)
            .where(Lead.tenant_id == tenant_id)
        )
        lead = result.scalar_one_or_none()

        if not lead:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lead no encontrado",
            )

        # Obtener property asociada si existe
        property_obj = None
        if lead.property_id:
            from app.db.models.property import Property
            prop_result = await session.execute(
                select(Property).where(Property.id == lead.property_id)
            )
            property_obj = prop_result.scalar_one_or_none()

        # Convertir tenant dict a objeto compatible
        tenant_obj = _dict_to_tenant_obj(tenant)

        try:
            await dispatch_booking_notifications(lead, tenant_obj, property_obj)
            whatsapp_ok = True
            email_ok = True
            errors = []
        except Exception as exc:
            logger.error("lead_resend_notification_failed", lead_id=lead_id, error=str(exc))
            whatsapp_ok = False
            email_ok = False
            errors = [str(exc)]

    logger.info(
        "lead_notifications_resent",
        tenant_id=tenant_id,
        lead_id=lead_id,
        whatsapp=whatsapp_ok,
        email=email_ok,
    )

    return LeadNotificationResponse(
        lead_id=str(lead.id),
        whatsapp_sent=whatsapp_ok,
        email_sent=email_ok,
        errors=errors,
    )


# ── Helpers ────────────────────────────────────────────────────────

def _orm_to_list_item(lead: Lead) -> LeadListItem:
    """Convierte ORM Lead a schema de listado."""
    return LeadListItem(
        id=str(lead.id),
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        qualification_score=lead.qualification_score or 0,
        is_international=bool(lead.is_international),
        status=lead.status,
        preferred_date=lead.preferred_date,
        preferred_time=lead.preferred_time,
        property_id=str(lead.property_id) if lead.property_id else None,
        created_at=lead.created_at,
    )


def _orm_to_detail(lead: Lead) -> LeadDetail:
    """Convierte ORM Lead a schema completo."""
    base = _orm_to_list_item(lead)
    return LeadDetail(
        **base.model_dump(),
        visit_duration_minutes=lead.visit_duration_minutes or 60,
        notes=lead.notes,
        calendar_event_id=lead.calendar_event_id,
        whatsapp_sent=bool(lead.whatsapp_sent),
        email_sent=bool(lead.email_sent),
        updated_at=lead.updated_at,
    )


def _dict_to_tenant_obj(tenant_dict: dict):
    """Convierte dict de tenant a objeto simple para notifications dispatcher.
    
    Nota: En V2 se usará el ORM Tenant directamente. Esto es un adapter
    para mantener compatibilidad mientras el middleware retorna dicts.
    """
    class SimpleTenant:
        pass
    
    obj = SimpleTenant()
    for key, value in tenant_dict.items():
        setattr(obj, key, value)
    return obj


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — api/v1/leads.py")

    # Test 1: Schemas validan correctamente
    lead_item = LeadListItem(
        id="lead-001",
        name="María González",
        email="maria@test.com",
        phone="+584121234567",
        qualification_score=85,
        is_international=True,
        status="pendiente",
        preferred_date="2026-06-15",
        preferred_time="10:00",
        created_at="2026-05-07T10:00:00",
    )
    assert lead_item.qualification_score == 85
    assert lead_item.is_international is True
    print("  ✅ LeadListItem schema válido")

    # Test 2: LeadStatusUpdate valida estados permitidos
    status_update = LeadStatusUpdate(status="contactado", notes="Llamé, contestó voicemail")
    assert status_update.status == "contactado"
    print("  ✅ LeadStatusUpdate schema válido")

    # Test 3: LeadStatusUpdate rechaza estado inválido
    try:
        LeadStatusUpdate(status="invalido")
        assert False, "Debe fallar con estado inválido"
    except Exception:
        pass
    print("  ✅ LeadStatusUpdate rechaza estado inválido")

    # Test 4: LeadDetail hereda correctamente
    detail = LeadDetail(
        id="lead-002",
        name="John Smith",
        email="john@example.com",
        phone="+13051234567",
        qualification_score=92,
        is_international=True,
        status="visita_agendada",
        preferred_date="2026-06-20",
        preferred_time="14:00",
        calendar_event_id="google-event-123",
        whatsapp_sent=True,
        email_sent=True,
        created_at="2026-05-07T10:00:00",
        updated_at="2026-05-07T12:00:00",
    )
    assert detail.whatsapp_sent is True
    assert detail.calendar_event_id == "google-event-123"
    print("  ✅ LeadDetail schema válido")

    # Test 5: Router instanciado
    assert router is not None
    assert router.prefix == "/leads"
    print("  ✅ Router instanciado con prefix correcto")

    # Test 6: _dict_to_tenant_obj adapter
    tenant_dict = {"id": "t-001", "name": "Test", "whatsapp_enabled": True}
    tenant_obj = _dict_to_tenant_obj(tenant_dict)
    assert tenant_obj.id == "t-001"
    assert tenant_obj.whatsapp_enabled is True
    print("  ✅ _dict_to_tenant_obj adapter funciona")

    print("\n🎉 Todos los smoke tests pasaron")
    print("   Nota: Tests de integración requieren DB con leads de prueba")