# project/alembic/versions/001_initial_schema.py
"""Initial schema — tablas relacionales + índices críticos.

Revision ID: 001
Revises: 
Create Date: 2026-05-07 15:00:00.000000

Nota: Las tablas vectoriales de sqlite-vec (property_embeddings_{tenant_id})
se crean dinámicamente en el pipeline de ingestion, no via Alembic.
Esto porque cada tenant tiene su propia tabla virtual.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tenants ───────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("plan", sa.Text, nullable=False, server_default="pro"),
        sa.Column("api_key_hash", sa.Text, nullable=False, unique=True),
        sa.Column("llm_model", sa.Text, nullable=True),
        sa.Column("llm_fallback_1", sa.Text, nullable=True),
        sa.Column("qualification_threshold", sa.Integer, server_default="75"),
        sa.Column("session_ttl_minutes", sa.Integer, server_default="30"),
        sa.Column("visit_duration_minutes", sa.Integer, server_default="60"),
        sa.Column("calendar_enabled", sa.Integer, server_default="1"),
        sa.Column("email_enabled", sa.Integer, server_default="1"),
        sa.Column("whatsapp_enabled", sa.Integer, server_default="1"),
        sa.Column("agent_email", sa.Text, nullable=True),
        sa.Column("agent_whatsapp", sa.Text, nullable=True),
        sa.Column("whatsapp_phone_id", sa.Text, nullable=True),
        sa.Column("allowed_origins", sa.Text, nullable=True),  # JSON array
        sa.Column("is_active", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    # ── properties ────────────────────────────────────────────────
    op.create_table(
        "properties",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("external_id", sa.Text, nullable=True),
        sa.Column("property_hash", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("property_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="disponible"),
        sa.Column("price_usd", sa.Float, nullable=True),
        sa.Column("price_bs", sa.Float, nullable=True),
        sa.Column("location_city", sa.Text, server_default="Porlamar"),
        sa.Column("location_zone", sa.Text, nullable=True),
        sa.Column("location_address", sa.Text, nullable=True),
        sa.Column("area_m2", sa.Float, nullable=True),
        sa.Column("bedrooms", sa.Integer, nullable=True),
        sa.Column("bathrooms", sa.Integer, nullable=True),
        sa.Column("parking_spots", sa.Integer, nullable=True),
        sa.Column("vista_al_mar", sa.Integer, server_default="0"),
        sa.Column("frente_playa", sa.Integer, server_default="0"),
        sa.Column("uso_vacacional", sa.Integer, server_default="0"),
        sa.Column("tipo_especial", sa.Text, nullable=True),
        sa.Column("capacidad_huespedes", sa.Integer, nullable=True),
        sa.Column("amenities", sa.Text, nullable=True),  # JSON array
        sa.Column("photos", sa.Text, nullable=True),  # JSON array
        sa.Column("description_es", sa.Text, nullable=True),
        sa.Column("description_en", sa.Text, nullable=True),
        sa.Column("raw_embed_text", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    # ── sessions ──────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("language", sa.Text, server_default="es"),
        sa.Column("qualification_score", sa.Integer, server_default="0"),
        sa.Column("is_booking_active", sa.Integer, server_default="0"),
        sa.Column("booking_step", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("last_active_at", sa.Text, nullable=False),
    )

    # ── messages ──────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),  # user | assistant
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
    )

    # ── leads ─────────────────────────────────────────────────────
    op.create_table(
        "leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("property_id", sa.String(36), sa.ForeignKey("properties.id"), nullable=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("phone", sa.Text, nullable=False),
        sa.Column("preferred_date", sa.Text, nullable=False),
        sa.Column("preferred_time", sa.Text, nullable=False),
        sa.Column("visit_duration_minutes", sa.Integer, server_default="60"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("qualification_score", sa.Integer, nullable=True),
        sa.Column("is_international", sa.Integer, server_default="0"),
        sa.Column("status", sa.Text, server_default="pendiente"),
        sa.Column("calendar_event_id", sa.Text, nullable=True),
        sa.Column("whatsapp_sent", sa.Integer, server_default="0"),
        sa.Column("email_sent", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    # ── ingestion_logs ────────────────────────────────────────────
    op.create_table(
        "ingestion_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("file_checksum", sa.Text, nullable=False),
        sa.Column("total_rows", sa.Integer, nullable=True),
        sa.Column("valid_rows", sa.Integer, nullable=True),
        sa.Column("inserted_rows", sa.Integer, nullable=True),
        sa.Column("updated_rows", sa.Integer, nullable=True),
        sa.Column("skipped_rows", sa.Integer, nullable=True),
        sa.Column("failed_rows", sa.Integer, nullable=True),
        sa.Column("errors", sa.Text, nullable=True),  # JSON array
        sa.Column("status", sa.Text, nullable=True),  # success | partial | failed
        sa.Column("created_at", sa.Text, nullable=False),
    )

    # ── Índices críticos ──────────────────────────────────────────
    op.create_index("idx_properties_tenant_status", "properties", ["tenant_id", "status"])
    op.create_index("idx_properties_tenant_type", "properties", ["tenant_id", "property_type"])
    op.create_index("idx_properties_external_id", "properties", ["tenant_id", "external_id"])
    op.create_index("idx_properties_hash", "properties", ["tenant_id", "property_hash"])
    op.create_index("idx_messages_session", "messages", ["session_id"])
    op.create_index("idx_messages_tenant_date", "messages", ["tenant_id", "created_at"])
    op.create_index("idx_leads_tenant_date", "leads", ["tenant_id", "created_at"])
    op.create_index("idx_sessions_tenant_active", "sessions", ["tenant_id", "last_active_at"])
    op.create_index("idx_ingestion_checksum", "ingestion_logs", ["tenant_id", "file_checksum"])


def downgrade() -> None:
    # Eliminar en orden inverso (foreign keys)
    op.drop_index("idx_ingestion_checksum", table_name="ingestion_logs")
    op.drop_index("idx_sessions_tenant_active", table_name="sessions")
    op.drop_index("idx_leads_tenant_date", table_name="leads")
    op.drop_index("idx_messages_tenant_date", table_name="messages")
    op.drop_index("idx_messages_session", table_name="messages")
    op.drop_index("idx_properties_hash", table_name="properties")
    op.drop_index("idx_properties_external_id", table_name="properties")
    op.drop_index("idx_properties_tenant_type", table_name="properties")
    op.drop_index("idx_properties_tenant_status", table_name="properties")

    op.drop_table("ingestion_logs")
    op.drop_table("leads")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("properties")
    op.drop_table("tenants")