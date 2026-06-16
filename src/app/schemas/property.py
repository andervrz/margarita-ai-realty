# src/app/schemas/property.py
"""Schemas Pydantic para Property."""
import json
from pydantic import field_validator
from pydantic import BaseModel, ConfigDict, Field
from app.core.constants import PropertyType, PropertyStatus

class PropertyChatSummary(BaseModel):
    """Propiedad para respuesta del chat.

    Incluye campos para dos vistas:
      - Listado (bullets): title, property_type, location_zone, price_usd.
      - Detalle (al elegir): bedrooms, bathrooms, area_m2, parking_spots,
        capacidad_huespedes, amenities, description_es/en.
    """
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    property_type: PropertyType
    price_usd: float | None = None
    location_city: str | None = None
    location_zone: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    area_m2: float | None = None
    parking_spots: int | None = None
    capacidad_huespedes: int | None = None
    vista_al_mar: bool = False
    frente_playa: bool = False
    uso_vacacional: bool = False
    tipo_especial: str | None = None
    status: PropertyStatus

    amenities: list[str] | None = None
    description_es: str | None = None
    description_en: str | None = None

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

    # PropertyChatSummary con amenities como JSON string (viene de DB)
    summary = PropertyChatSummary(
        id="prop-123",
        title="Casa en Pampatar",
        property_type="venta",
        status="disponible",
        amenities='["piscina", "gym"]',  # JSON string como llega de SQLite
    )
    assert summary.amenities == ["piscina", "gym"]  # debe deserializar
    assert summary.vista_al_mar is False
    print(" ✅ PropertyChatSummary + JSON validator correctos")
    print("\n🎉 Smoke test pasó")
