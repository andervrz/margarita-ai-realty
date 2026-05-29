# alembic/versions/001_initial_schema.py
"""Initial schema — tablas relacionales + índices críticos.

Revision ID: 001
Revises:
Create Date: 2026-05-07 15:00:00.000000

Nota: Las tablas vectoriales de sqlite-vec (property_embeddings_{tenant_id})
se crean dinámicamente en el pipeline de ingestion, no via Alembic.
Cada tenant tiene su propia tabla virtual — no es posible definirlas
estáticamente en una migración.

Decisiones de tipos:
  - Boolean → sa.Boolean (mapeado a INTEGER en SQLite, nativo en PostgreSQL)
  - Precios  → sa.Numeric(12, 2) — evita problemas de precisión float
  - Fechas   → sa.Text en formato ISO 8601 (portabilidad SQLite ↔ PostgreSQL)
  - UUIDs    → sa.String(36) — UUID v4 como string
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# ── Alembic revision metadata ────────────────────────────────────
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── upgrade ───────────────────────────────────────────────────────

def upgrade() -> None:

    # ── tenants ───────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("plan", sa.Text, nullable=False, server_default="pro"),
        sa.Column("api_key_hash", sa.Text, nullable=False, unique=True),
        # LLM model overrides por tenant
        sa.Column("llm_model", sa.Text, nullable=True),
        sa.Column("llm_fallback_1", sa.Text, nullable=True),
        sa.Column("llm_fallback_2", sa.Text, nullable=True),
        # Configuración operacional
        sa.Column("qualification_threshold", sa.Integer, server_default="75"),
        sa.Column("session_ttl_minutes", sa.Integer, server_default="30"),
        sa.Column("visit_duration_minutes", sa.Integer, server_default="60"),
        # Feature flags — Boolean (INTEGER 0/1 en SQLite)
        sa.Column("calendar_enabled", sa.Boolean, server_default="1"),
        sa.Column("email_enabled", sa.Boolean, server_default="1"),
        sa.Column("whatsapp_enabled", sa.Boolean, server_default="1"),
        sa.Column("is_active", sa.Boolean, server_default="1"),
        # Contacto del agente
        sa.Column("agent_email", sa.Text, nullable=True),
        sa.Column("agent_whatsapp", sa.Text, nullable=True),
        sa.Column("whatsapp_phone_id", sa.Text, nullable=True),
        # CORS — JSON array de origins permitidos
        sa.Column("allowed_origins", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    # ── properties ────────────────────────────────────────────────
    op.create_table(
        "properties",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Idempotencia en re-subidas de CSV
        sa.Column("external_id", sa.Text, nullable=True),
        sa.Column("property_hash", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("property_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="disponible"),
        # Precios — Numeric para evitar errores de precisión float
        sa.Column("price_usd", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("price_bs", sa.Numeric(precision=16, scale=2), nullable=True),
        # Ubicación
        sa.Column("location_city", sa.Text, server_default="Porlamar"),
        sa.Column("location_zone", sa.Text, nullable=True),
        sa.Column("location_address", sa.Text, nullable=True),
        # Características físicas
        sa.Column("area_m2", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("bedrooms", sa.Integer, nullable=True),
        sa.Column("bathrooms", sa.Integer, nullable=True),
        sa.Column("parking_spots", sa.Integer, nullable=True),
        # Flags específicos de Margarita — Boolean
        sa.Column("vista_al_mar", sa.Boolean, server_default="0"),
        sa.Column("frente_playa", sa.Boolean, server_default="0"),
        sa.Column("uso_vacacional", sa.Boolean, server_default="0"),
        # Campos especiales Margarita
        sa.Column("tipo_especial", sa.Text, nullable=True),
        sa.Column("capacidad_huespedes", sa.Integer, nullable=True),
        # JSON arrays almacenados como Text
        sa.Column("amenities", sa.Text, nullable=True),
        sa.Column("photos", sa.Text, nullable=True),
        # Descripciones bilingüe
        sa.Column("description_es", sa.Text, nullable=True),
        sa.Column("description_en", sa.Text, nullable=True),
        # Texto concatenado para sqlite-vec embeddings
        sa.Column("raw_embed_text", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    # ── sessions ──────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.Text, server_default="es"),
        sa.Column("qualification_score", sa.Integer, server_default="0"),
        sa.Column("is_booking_active", sa.Boolean, server_default="0"),
        sa.Column("booking_step", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("last_active_at", sa.Text, nullable=False),
    )

    # ── messages ──────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Denormalizado para queries eficientes sin JOIN
        sa.Column("role", sa.Text, nullable=False),  # user | assistant
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
    )

    # ── leads ─────────────────────────────────────────────────────
    op.create_table(
        "leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "property_id",
            sa.String(36),
            sa.ForeignKey("properties.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Datos de contacto del lead
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("phone", sa.Text, nullable=False),
        # Agenda de visita
        sa.Column("preferred_date", sa.Text, nullable=False),  # ISO date YYYY-MM-DD
        sa.Column("preferred_time", sa.Text, nullable=False),  # HH:MM
        sa.Column("visit_duration_minutes", sa.Integer, server_default="60"),
        sa.Column("notes", sa.Text, nullable=True),
        # Calificación y segmentación
        sa.Column("qualification_score", sa.Integer, nullable=True),
        sa.Column("is_international", sa.Boolean, server_default="0"),
        sa.Column("status", sa.Text, server_default="pendiente"),
        # Integración con servicios externos
        sa.Column("calendar_event_id", sa.Text, nullable=True),
        sa.Column("whatsapp_sent", sa.Boolean, server_default="0"),
        sa.Column("email_sent", sa.Boolean, server_default="0"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    # ── ingestion_logs ────────────────────────────────────────────
    op.create_table(
        "ingestion_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text, nullable=False),
        # SHA-256 del archivo — clave para idempotencia
        sa.Column("file_checksum", sa.Text, nullable=False),
        # Estadísticas del pipeline
        sa.Column("total_rows", sa.Integer, nullable=True),
        sa.Column("valid_rows", sa.Integer, nullable=True),
        sa.Column("inserted_rows", sa.Integer, nullable=True),
        sa.Column("updated_rows", sa.Integer, nullable=True),
        sa.Column("skipped_rows", sa.Integer, nullable=True),
        sa.Column("failed_rows", sa.Integer, nullable=True),
        # JSON array de errores con row number
        sa.Column("errors", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=True),  # success | partial | failed | skipped
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    # ── Índices críticos ──────────────────────────────────────────
    # El orden importa: columnas más selectivas primero

    # Properties — queries frecuentes del chat engine
    op.create_index(
        "idx_properties_tenant_status",
        "properties",
        ["tenant_id", "status"],
    )
    op.create_index(
        "idx_properties_tenant_type",
        "properties",
        ["tenant_id", "property_type"],
    )
    op.create_index(
        "idx_properties_external_id",
        "properties",
        ["tenant_id", "external_id"],
    )
    op.create_index(
        "idx_properties_hash",
        "properties",
        ["tenant_id", "property_hash"],
    )

    # Messages — carga de historial de sesión
    op.create_index(
        "idx_messages_session",
        "messages",
        ["session_id"],
    )
    op.create_index(
        "idx_messages_tenant_date",
        "messages",
        ["tenant_id", "created_at"],
    )

    # Leads — filtros del admin panel
    op.create_index(
        "idx_leads_tenant_status",
        "leads",
        ["tenant_id", "status"],
    )
    op.create_index(
        "idx_leads_tenant_date",
        "leads",
        ["tenant_id", "created_at"],
    )

    # Sessions — cleanup por TTL y lookup por tenant
    op.create_index(
        "idx_sessions_tenant_active",
        "sessions",
        ["tenant_id", "last_active_at"],
    )

    # Ingestion — idempotencia por checksum
    op.create_index(
        "idx_ingestion_checksum",
        "ingestion_logs",
        ["tenant_id", "file_checksum"],
    )


# ── downgrade ─────────────────────────────────────────────────────

def downgrade() -> None:
    # Eliminar índices primero
    op.drop_index("idx_ingestion_checksum", table_name="ingestion_logs")
    op.drop_index("idx_sessions_tenant_active", table_name="sessions")
    op.drop_index("idx_leads_tenant_date", table_name="leads")
    op.drop_index("idx_leads_tenant_status", table_name="leads")
    op.drop_index("idx_messages_tenant_date", table_name="messages")
    op.drop_index("idx_messages_session", table_name="messages")
    op.drop_index("idx_properties_hash", table_name="properties")
    op.drop_index("idx_properties_external_id", table_name="properties")
    op.drop_index("idx_properties_tenant_type", table_name="properties")
    op.drop_index("idx_properties_tenant_status", table_name="properties")

    # Eliminar tablas en orden inverso (foreign keys)
    op.drop_table("ingestion_logs")
    op.drop_table("leads")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("properties")
    op.drop_table("tenants")
