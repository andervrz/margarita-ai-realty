# tests/conftest.py
"""Fixtures compartidas para toda la suite de tests.

Jerarquía:
  async_engine → db_session → test_tenant → http_client
                                          → test_property
                                          → test_lead
"""

from __future__ import annotations

import os

# Los tests E2E (http_client) usan el engine real de la app. Forzamos una DB
# de test aislada ANTES de importar cualquier módulo de app, para no tocar la
# DB de desarrollo. app.db.engine prioriza esta env var sobre settings.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./.pytest_app.db"

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_api_key
from app.db.base import Base
from app.db.models.ingestion_log import IngestionLog
from app.db.models.lead import Lead
from app.db.models.message import Message
from app.db.models.property import Property
from app.db.models.session import Session as SessionModel
from app.db.models.tenant import Tenant


# ── Engine en Memoria ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def async_engine():
    """SQLite en memoria con WAL + foreign_keys.

    Nota: la búsqueda vectorial usa pgvector (solo PostgreSQL); en los tests
    sobre SQLite la columna ``embedding`` existe pero no se hace KNN.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def configure(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """AsyncSession conectada al engine en memoria."""
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


# ── Tenants ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_tenant(db_session: AsyncSession) -> Tenant:
    """Tenant Pro con todas las features habilitadas."""
    now = datetime.now(timezone.utc).isoformat()
    tenant = Tenant(
        id=str(uuid4()),
        name="Test Inmobiliaria Margarita",
        slug="test-margarita",
        plan="pro",
        api_key_hash=hash_api_key("test-api-key-12345"),
        qualification_threshold=75,
        session_ttl_minutes=30,
        visit_duration_minutes=60,
        calendar_enabled=True,
        email_enabled=True,
        whatsapp_enabled=True,
        whatsapp_phone_id="123456789",
        agent_email="agente@test.com",
        agent_whatsapp="+584120000000",
        allowed_origins='["https://test-margarita.com"]',
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(tenant)
    await db_session.commit()
    return tenant


@pytest_asyncio.fixture
async def test_tenant_2(db_session: AsyncSession) -> Tenant:
    """Segundo tenant para tests de aislamiento cross-tenant."""
    now = datetime.now(timezone.utc).isoformat()
    tenant = Tenant(
        id=str(uuid4()),
        name="Otro Tenant",
        slug="otro-tenant",
        plan="basic",
        api_key_hash=hash_api_key("other-api-key-99999"),
        qualification_threshold=75,
        session_ttl_minutes=30,
        visit_duration_minutes=60,
        calendar_enabled=False,
        email_enabled=False,
        whatsapp_enabled=False,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(tenant)
    await db_session.commit()
    return tenant


# ── Properties ────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_property(db_session: AsyncSession, test_tenant: Tenant) -> Property:
    """Propiedad disponible para tests de búsqueda."""
    now = datetime.now(timezone.utc).isoformat()
    prop = Property(
        id=str(uuid4()),
        tenant_id=test_tenant.id,
        external_id="PROP-TEST-001",
        title="Apartamento Vista al Mar Pampatar",
        property_type="venta",
        status="disponible",
        price_usd=150000,
        location_zone="Pampatar",
        location_city="Margarita",
        bedrooms=3,
        bathrooms=2,
        area_m2=85,
        vista_al_mar=True,
        frente_playa=False,
        uso_vacacional=True,
        description_es="Hermoso apartamento con vista al mar en Pampatar.",
        description_en="Beautiful sea-view apartment in Pampatar.",
        raw_embed_text=(
            "Apartamento venta Pampatar 3 habitaciones 2 baños "
            "85m2 vista al mar uso vacacional"
        ),
        created_at=now,
        updated_at=now,
    )
    db_session.add(prop)
    await db_session.commit()
    return prop


@pytest_asyncio.fixture
async def test_property_2(db_session: AsyncSession, test_tenant: Tenant) -> Property:
    """Segunda propiedad para tests de listados y filtros."""
    now = datetime.now(timezone.utc).isoformat()
    prop = Property(
        id=str(uuid4()),
        tenant_id=test_tenant.id,
        external_id="PROP-TEST-002",
        title="Casa Frente Playa El Agua",
        property_type="venta",
        status="disponible",
        price_usd=280000,
        location_zone="El Agua",
        location_city="Margarita",
        bedrooms=4,
        bathrooms=3,
        area_m2=120,
        vista_al_mar=True,
        frente_playa=True,
        uso_vacacional=True,
        description_es="Casa exclusiva frente al mar.",
        raw_embed_text=(
            "Casa venta El Agua 4 habitaciones 3 baños "
            "120m2 frente playa vista al mar"
        ),
        created_at=now,
        updated_at=now,
    )
    db_session.add(prop)
    await db_session.commit()
    return prop


# ── Chat Sessions ─────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_chat_session(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> SessionModel:
    """Sesión de chat activa para tests de memoria y leads."""
    now = datetime.now(timezone.utc).isoformat()
    session = SessionModel(
        id=str(uuid4()),
        tenant_id=test_tenant.id,
        language="es",
        qualification_score=0,
        is_booking_active=False,
        created_at=now,
        last_active_at=now,
    )
    db_session.add(session)
    await db_session.commit()
    return session


# ── Leads ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_lead(
    db_session: AsyncSession,
    test_tenant: Tenant,
    test_chat_session: SessionModel,
    test_property: Property,
) -> Lead:
    """Lead confirmado para tests de notificaciones y calendar."""
    now = datetime.now(timezone.utc).isoformat()
    lead = Lead(
        id=str(uuid4()),
        session_id=test_chat_session.id,
        tenant_id=test_tenant.id,
        property_id=test_property.id,
        name="María González",
        email="maria@test.com",
        phone="+584141234567",
        preferred_date="2027-06-15",
        preferred_time="10:00",
        visit_duration_minutes=90,
        notes="Interesada en vista al mar. Viene con su esposo.",
        qualification_score=85,
        is_international=False,
        status="pendiente",
        whatsapp_sent=False,
        email_sent=False,
        created_at=now,
        updated_at=now,
    )
    db_session.add(lead)
    await db_session.commit()
    return lead


# ── HTTP Client (E2E) ─────────────────────────────────────────────

@pytest_asyncio.fixture
async def http_client() -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient para tests e2e — corre contra el engine real de la app.

    En development el middleware usa DEV_TENANT (id=dev-tenant-001) sin auth.
    Creamos el schema y sembramos ese tenant en la DB de la app para que las
    inserciones con FK (properties/leads → tenants) no fallen. Schema limpio
    por test (function-scoped).
    """
    from app.api.middleware import _DEV_TENANT
    from app.db.engine import AsyncSessionLocal, engine
    from app.main import create_app

    # Schema fresco en el engine real de la app
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Sembrar DEV_TENANT (allowed_origins es String JSON; añadir timestamps)
    now = datetime.now(timezone.utc).isoformat()
    async with AsyncSessionLocal() as session:
        session.add(Tenant(
            id=_DEV_TENANT["id"],
            name=_DEV_TENANT["name"],
            slug=_DEV_TENANT["slug"],
            plan=_DEV_TENANT["plan"],
            api_key_hash=_DEV_TENANT["api_key_hash"],
            qualification_threshold=_DEV_TENANT["qualification_threshold"],
            session_ttl_minutes=_DEV_TENANT["session_ttl_minutes"],
            visit_duration_minutes=_DEV_TENANT["visit_duration_minutes"],
            calendar_enabled=_DEV_TENANT["calendar_enabled"],
            email_enabled=_DEV_TENANT["email_enabled"],
            whatsapp_enabled=_DEV_TENANT["whatsapp_enabled"],
            agent_email=_DEV_TENANT["agent_email"],
            agent_whatsapp=_DEV_TENANT["agent_whatsapp"],
            allowed_origins='["*"]',
            is_active=True,
            created_at=now,
            updated_at=now,
        ))
        await session.commit()

    app = create_app()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "dev"},
        ) as client:
            yield client
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


# ── CSV Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def sample_csv_valid() -> bytes:
    """CSV válido con 3 propiedades de Margarita."""
    return (
        b"external_id,title,property_type,status,price_usd,location_zone,"
        b"bedrooms,bathrooms,area_m2,vista_al_mar,frente_playa,uso_vacacional,"
        b"description_es\n"
        b"PROP001,Apto Vista Mar Pampatar,venta,disponible,150000,Pampatar,"
        b"3,2,85,true,false,true,Hermoso apartamento con vista al mar\n"
        b"PROP002,Casa Frente Playa El Agua,venta,disponible,280000,El Agua,"
        b"4,3,120,true,true,true,Casa exclusiva frente al mar\n"
        b"PROP003,Local Porlamar,local,disponible,95000,Porlamar,"
        b"0,1,60,false,false,false,Local comercial activo\n"
    )


@pytest.fixture
def sample_csv_with_errors() -> bytes:
    """CSV con algunas filas inválidas — para test de partial ingestion."""
    return (
        b"external_id,title,property_type,status,price_usd,location_zone,bedrooms\n"
        b"PROP001,Propiedad Valida,venta,disponible,100000,Pampatar,2\n"
        b"PROP002,,venta,disponible,50000,Porlamar,1\n"
        b"PROP003,Precio Invalido,venta,disponible,NO_NUMBER,El Agua,3\n"
    )


# ── Mock LiteLLM ──────────────────────────────────────────────────

@pytest.fixture
def mock_llm_response_factory():
    """Factory para crear mock responses de LiteLLM."""
    from unittest.mock import MagicMock

    def _make_response(content: str = "Encontré propiedades verificadas en Pampatar."):
        response = MagicMock()
        response.choices[0].message.content = content
        return response

    return _make_response
