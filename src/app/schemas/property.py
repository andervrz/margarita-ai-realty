# src/app/schemas/property.py
"""Schemas Pydantic para Property."""
import json
from pydantic import field_validator
from pydantic import BaseModel, ConfigDict, Field
from src.app.core.constants import PropertyType, PropertyStatus

class PropertyCreate(BaseModel):
    """Datos para crear una propiedad (desde CSV o API)."""
    
    title: str = Field(..., min_length=3)
    property_type: PropertyType
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
    amenities: list[str] | None = None
    photos: list[str] | None = None
    description_es: str | None = None
    description_en: str | None = None
    


class PropertyResponse(BaseModel):
    """Respuesta pública de una propiedad."""
    
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    title: str
    created_at: str
    property_type: PropertyType
    status: PropertyStatus
    price_usd: float | None
    price_bs: float | None
    location_city: str | None
    location_zone: str | None
    location_address: str | None
    area_m2: float | None
    bedrooms: int | None
    bathrooms: int | None
    parking_spots: int | None
    vista_al_mar: bool
    frente_playa: bool
    uso_vacacional: bool
    tipo_especial: str | None
    capacidad_huespedes: int | None
    amenities: list[str] | None = None
    photos: list[str] | None = None
    description_es: str | None
    description_en: str | None
     
    @field_validator("amenities", "photos", mode="before")
    @classmethod
    def parse_json_list(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return None
        return v


class PropertyChatSummary(BaseModel):
    """Propiedad resumida para respuesta del chat — campos mínimos para el widget."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    property_type: PropertyType
    price_usd: float | None = None
    location_zone: str | None = None
    bedrooms: int | None = None
    vista_al_mar: bool = False
    frente_playa: bool = False
    status: PropertyStatus
    
    amenities: list[str] | None = None

    @field_validator("amenities", mode="before")
    @classmethod
    def parse_json_list(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, ValueError):
                return None
        return v


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — schemas/property.py")
    
    p_create = PropertyCreate(
        title="Casa en Pampatar",
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
        vista_al_mar=True,
        bedrooms=3,
    )
    
# Test PropertyChatSummary con amenities como JSON string (viene de DB)
    summary = PropertyChatSummary(
        id="prop-123",
        title="Casa en Pampatar",
        property_type="venta",
        status="disponible",
        amenities='["piscina", "gym"]',  # JSON string como llega de SQLite
    )
    assert p_resp.vista_al_mar == True
    assert summary.amenities == ["piscina", "gym"]  # debe deserializar
    assert summary.vista_al_mar is False
    print(" ✅ PropertyChatSummary + JSON validator correctos")
    print("\n🎉 Smoke test pasó")
