# src/app/db/models/property.py
"""Modelo Property — propiedad inmobiliaria."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, CheckConstraint, ForeignKey, Integer, String, Text, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Index, Boolean, func
from src.app.db.base import Base


class Property(Base):
    """Propiedad del catálogo de un tenant."""
    
    __tablename__ = "properties"

    __table_args__ = (
        Index("idx_properties_tenant_status", "tenant_id", "status"),
        Index("idx_properties_tenant_type", "tenant_id", "property_type"),
        Index("idx_properties_external_id", "tenant_id", "external_id"),
        Index("idx_properties_hash", "tenant_id", "property_hash"),
        CheckConstraint("status IN ('disponible', 'reservada', 'vendida')",name="ck_property_status"),
        CheckConstraint("property_type IN ('venta', 'arriendo', 'vacacional', 'local', 'posada', 'hotel', 'planos', 'terreno')",name="ck_property_type"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    
    # Idempotencia
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    property_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # Básicos
    title: Mapped[str] = mapped_column(String, nullable=False)
    property_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=PropertyStatus.DISPONIBLE.value)
    
    # Precios
    price_usd: Mapped[float | None] = mapped_column(Numeric(precision=12, scale=2), nullable=True)
    price_bs: Mapped[float | None] = mapped_column(Numeric(precision=16, scale=2), nullable=True))
    
    # Ubicación
    location_city: Mapped[str | None] = mapped_column(String, nullable=True)
    location_zone: Mapped[str | None] = mapped_column(String, nullable=True)
    location_address: Mapped[str | None] = mapped_column(String, nullable=True)
    
    # Características
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bathrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parking_spots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Específicos Margarita
    vista_al_mar: Mapped[int] = mapped_column(Boolean, default=0)
    frente_playa: Mapped[int] = mapped_column(Boolean, default=0)
    uso_vacacional: Mapped[int] = mapped_column(Boolean, default=0)
    tipo_especial: Mapped[str | None] = mapped_column(String, nullable=True)
    capacidad_huespedes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Contenido
    amenities: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    photos: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON URLs
    description_es: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_embed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, 
        default=lambda: datetime.now(timezone.utc).isoformat(),
    )



# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — property.py")
    
    p = Property(
        tenant_id="tenant-123",
        title="Apartamento 3H/2B en Pampatar",
        property_type="venta",
        price_usd=150000,
        location_zone="Pampatar",
        vista_al_mar=1,
        bedrooms=3,
    )
    assert p.title == "Apartamento 3H/2B en Pampatar"
    assert p.vista_al_mar == True
    assert p.frente_playa == False  # default
    assert p.status == "disponible"
    print(f"  ✅ Property creada: {p.title} (${p.price_usd})")
    print("\n🎉 Smoke test pasó")
