# src/app/schemas/property.py
"""Schemas Pydantic para Property."""

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class PropertyCreate(BaseModel):
    """Datos para crear una propiedad (desde CSV o API)."""
    
    title: str = Field(..., min_length=3)
    property_type: str
    price_usd: float | None = Field(None, ge=0)
    price_bs: float | None = Field(None, ge=0)
    location_city: str | None = None
    location_zone: str | None = None
    location_address: str | None = None
    area_m2: float | None = Field(None, gt=0)
    bedrooms: int | None = Field(None, ge=0)
    bathrooms: int | None = Field(None, ge=0)
    parking_spots: int | None = Field(None, ge=0)
    vista_al_mar: bool = False
    frente_playa: bool = False
    uso_vacacional: bool = False
    tipo_especial: str | None = None
    capacidad_huespedes: int | None = Field(None, ge=0)
    amenities: List[str] | None = None
    photos: List[str] | None = None
    description_es: str | None = None
    description_en: str | None = None


class PropertyResponse(BaseModel):
    """Respuesta pública de una propiedad."""
    
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    title: str
    property_type: str
    status: str
    price_usd: float | None
    price_bs: float | None
    location_city: str | None
    location_zone: str | None
    location_address: str | None
    area_m2: float | None
    bedrooms: int | None
    bathrooms: int | None
    parking_spots: int | None
    vista_al_mar: int
    frente_playa: int
    uso_vacacional: int
    tipo_especial: str | None
    capacidad_huespedes: int | None
    amenities: str | None
    photos: str | None
    description_es: str | None
    description_en: str | None


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — schemas/property.py")
    
    p_create = PropertyCreate(
n        title="Casa en Pampatar",
        property_type="venta",
        price_usd=200000,
        vista_al_mar=True,
        bedrooms=3,
    )
    assert p_create.vista_al_mar is True
    assert p_create.price_usd == 200000
    
    p_resp = PropertyResponse(
        id="prop-123",
        tenant_id="tenant-123",
        title="Casa en Pampatar",
        property_type="venta",
        status="disponible",
        price_usd=200000,
        vista_al_mar=1,
        bedrooms=3,
    )
    assert p_resp.vista_al_mar == 1
    print(f"  ✅ PropertyCreate + PropertyResponse válidos")
    print("\n🎉 Smoke test pasó")