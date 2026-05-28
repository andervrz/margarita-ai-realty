# src/app/api/v1/leads.py
"""Leads API — Admin: gestión de leads capturados por el chatbot.

Endpoints:
    GET   /leads              → Listar leads del tenant (con filtros)
    GET   /leads/{id}         → Ver detalle de un lead
    PATCH /leads/{id}/status  → Actualizar estado del lead
    POST  /leads/{id}/notify  → Reenviar notificación manualmente

Filtros soportados (GET /leads):
    status, is_international, min_score, date_from, date_to, property_id

Estados del lead:
    pendiente       → Capturado, sin contactar
    confirmado      → Agente confirmó la visita
    cancelado       → Cancelado por cualquier motivo

Nota: Leads son privados del tenant. Sin cross-tenant access.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from src.app.api.middleware import get_current_tenant
from src.app.core.logging import get_logger
from src.app.db.engine import AsyncSessionLocal
from src.app.db.models.lead import Lead

logger = get_logger(__name__)

router = APIRouter(prefix="/leads", tags=["leads"])


# ── Schemas ───────────────────────────────────────────────────────

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
    visit_duration_minutes: int = 60
    property_id: str | None = None
    created_at: str


class LeadDetail(LeadListItem):
    """Lead completo con todos los campos."""
    notes: str | None = None
    calendar_event_id: str | None = None
    whatsapp_sent: bool = False
    email_sent: bool = False
    updated_at: str


class LeadStatusUpdate(BaseModel):
    """Payload para actualizar estado del lead."""
    status: str = Field(
        ...,
        pattern=r"^(pendiente|confirmado|cancelado)$",
        description="Estado válido según LeadStatus enum",
    )
    notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Nota adicional del agente (se adjunta al historial)",
    )


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


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("", response_model=list[LeadListItem])
async def list_leads(
    tenant: dict = Depends(get_current_tenant),
    lead_status: str | None = Query(None, alias="status", description="Estado del lead"),
    is_international: bool | None = Query(None, description="Comprador internacional"),
    min_score: int | None = Query(None, ge=0, le=100, description="Score mínimo"),
    date_from: str | None = Query(None, description="Fecha desde YYYY-MM-DD"),
    date_to: str | None = Query(None, description="Fecha hasta YYYY-MM-DD"),
    property_id: str | None = Query(None, description="Filtrar por propiedad"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[LeadListItem]:
    """Lista leads del tenant con filtros opcionales.

    Ejemplo:
        GET /leads?status=pendiente&min_score=75&is_international=true
    """
    from sqlalchemy import select

    tenant_id = tenant["id"]

    async with AsyncSessionLocal() as session:
        stmt = select(Lead).where(Lead.tenant_id == tenant_id)

        if lead_status:
            stmt = stmt.where(Lead.status == lead_status)
        if is_international is not None:
            # .is_(True/False) para columna Boolean — compatible SQLite + PostgreSQL
            stmt = stmt.where(Lead.is_international.is_(is_international))
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
            "status": lead_status,
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
    """Obtiene el detalle completo de un lead."""
    from sqlalchemy import select

    tenant_id = tenant["id"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.tenant_id == tenant_id,
            )
        )
        lead = result.scalar_one_or_none()

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead '{lead_id}' no encontrado",
        )

    return _orm_to_detail(lead)


@router.patch("/{lead_id}/status", response_model=LeadStatusResponse)
async def update_lead_status_endpoint(
    lead_id: str,
    payload: LeadStatusUpdate,
    tenant: dict = Depends(get_current_tenant),
) -> LeadStatusResponse:
    """Actualiza el estado de un lead.

    El agente usa este endpoint para marcar progreso en el funnel:
    pendiente → confirmado → cancelado
    """
    from sqlalchemy import select

    tenant_id = tenant["id"]
    now_iso = datetime.now(timezone.utc).isoformat()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.tenant_id == tenant_id,
            )
        )
        lead = result.scalar_one_or_none()

        if not lead:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Lead '{lead_id}' no encontrado",
            )

        previous_status = lead.status
        lead.status = payload.status
        lead.updated_at = now_iso  # Actualización explícita — onupdate no funciona en async

        # Adjuntar nota al historial de notas si se provee
        if payload.notes:
            existing_notes = lead.notes or ""
            note_entry = f"[{now_iso}] {payload.notes}"
            lead.notes = f"{existing_notes}\n{note_entry}".strip() if existing_notes else note_entry

        await session.commit()

    logger.info(
        "lead_status_updated",
        tenant_id=tenant_id,
        lead_id=lead_id,
        previous_status=previous_status,
        new_status=payload.status,
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
    from sqlalchemy import select

    from src.app.notifications.dispatcher import dispatch_booking_notifications

    tenant_id = tenant["id"]
    now_iso = datetime.now(timezone.utc).isoformat()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.tenant_id == tenant_id,
            )
        )
        lead = result.scalar_one_or_none()

        if not lead:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Lead '{lead_id}' no encontrado",
            )

        # Cargar propiedad asociada si existe
        prop = None
        if lead.property_id:
            from src.app.db.models.property import Property
            prop_result = await session.execute(
                select(Property).where(Property.id == lead.property_id)
            )
            prop = prop_result.scalar_one_or_none()

        # Adapter dict → objeto para el dispatcher
        tenant_obj = _dict_to_tenant_obj(tenant)

        errors: list[str] = []
        whatsapp_ok = False
        email_ok = False

        try:
            notification_result = await dispatch_booking_notifications(
                lead=lead,
                tenant=tenant_obj,
                prop=prop,
            )
            whatsapp_ok = notification_result.whatsapp_success
            email_ok = notification_result.email_success
            if notification_result.whatsapp_error:
                errors.append(f"WhatsApp: {notification_result.whatsapp_error}")
            if notification_result.email_error:
                errors.append(f"Email: {notification_result.email_error}")

        except Exception as exc:
            logger.exception(
                "lead_resend_notification_failed",
                lead_id=lead_id,
                error=str(exc),
            )
            errors.append(str(exc))

        # Actualizar flags de tracking en el lead
        if whatsapp_ok:
            lead.whatsapp_sent = True
        if email_ok:
            lead.email_sent = True
        if whatsapp_ok or email_ok:
            lead.updated_at = now_iso
            await session.commit()

    logger.info(
        "lead_notifications_resent",
        tenant_id=tenant_id,
        lead_id=lead_id,
        whatsapp=whatsapp_ok,
        email=email_ok,
        errors=errors,
    )

    return LeadNotificationResponse(
        lead_id=str(lead.id),
        whatsapp_sent=whatsapp_ok,
        email_sent=email_ok,
        errors=errors,
    )


# ── Helpers ───────────────────────────────────────────────────────

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
        visit_duration_minutes=lead.visit_duration_minutes or 60,
        property_id=str(lead.property_id) if lead.property_id else None,
        created_at=str(lead.created_at),
    )


def _orm_to_detail(lead: Lead) -> LeadDetail:
    """Convierte ORM Lead a schema completo."""
    base = _orm_to_list_item(lead)
    return LeadDetail(
        **base.model_dump(),
        notes=lead.notes,
        calendar_event_id=lead.calendar_event_id,
        whatsapp_sent=bool(lead.whatsapp_sent),
        email_sent=bool(lead.email_sent),
        updated_at=str(lead.updated_at),
    )


def _dict_to_tenant_obj(tenant_dict: dict) -> object:
    """Adapter: convierte dict de tenant a objeto simple para el dispatcher.

    El dispatcher espera un objeto con atributos, no un dict.
    En V2 se usará el ORM Tenant directamente cuando el middleware
    retorne objetos en lugar de dicts.
    """
    class _TenantAdapter:
        pass

    obj = _TenantAdapter()
    for key, value in tenant_dict.items():
        setattr(obj, key, value)
    return obj


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — api/v1/leads.py\n")

    # Test 1: LeadListItem schema
    item = LeadListItem(
        id="lead-001",
        name="María González",
        email="maria@test.com",
        phone="+584121234567",
        qualification_score=85,
        is_international=True,
        status="pendiente",
        preferred_date="2027-06-15",
        preferred_time="10:00",
        created_at="2027-05-07T10:00:00",
    )
    assert item.qualification_score == 85
    assert item.is_international is True
    assert item.visit_duration_minutes == 60  # default
    print("✅ LeadListItem schema válido")

    # Test 2: LeadStatusUpdate e
