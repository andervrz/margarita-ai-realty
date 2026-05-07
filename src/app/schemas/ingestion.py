# src/app/schemas/ingestion.py
"""Schemas Pydantic para ingestion de CSV."""

from typing import List

from pydantic import BaseModel, Field


class PropertyCSVRow(BaseModel):
    """Fila validada de CSV de propiedades."""
    
    title: str = Field(..., min_length=3)
    property_type: str = Field(..., pattern=r"^(venta|arriendo|vacacional|local|posada|hotel|planos|terreno)$")
    price_usd: float | None = Field(None, ge=0)
    price_bs: float | None = Field(None, ge=0)
    location_city: str | None = None
    location_zone: str | None = None
    location_address: str | None = None
    area_m2: float | None = Field(None, gt=0)
    bedrooms: int | None = Field(None, ge=0)
    bathrooms: int | None = Field(None, ge=0)
    parking_spots: int | None = Field(None, ge=0)
    vista_al_mar: int | None = Field(None, ge=0, le=1)
    frente_playa: int | None = Field(None, ge=0, le=1)
    uso_vacacional: int | None = Field(None, ge=0, le=1)
    tipo_especial: str | None = None
    capacidad_huespedes: int | None = Field(None, ge=0)
    amenities: str | None = None  # Comma-separated
    photos: str | None = None  # Comma-separated URLs
    description_es: str | None = None
    description_en: str | None = None


class IngestionResult(BaseModel):
    """Resultado del pipeline de ingestion."""
    
    filename: str
    total_rows: int
    valid_rows: int
    inserted_rows: int
    updated_rows: int
    skipped_rows: int
    failed_rows: int
    errors: List[str]
    status: str  # success | partial | failed


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — schemas/ingestion.py")
    
    row = PropertyCSVRow(
        title="Casa en Pampatar",
        property_type="venta",
        price_usd=150000,
        bedrooms=3,
        vista_al_mar=1,
    )
    assert row.property_type == "venta"
    assert row.vista_al_mar == 1
    
    # Validación de tipo inválido
    try:
        PropertyCSVRow(title="X", property_type="invalido")
        assert False, "Debería fallar"
    except Exception:
        pass  # Expected
    
    result = IngestionResult(
        filename="cat.csv",
        total_rows=10,
        valid_rows=9,
        inserted_rows=7,
        updated_rows=1,
        skipped_rows=1,
        failed_rows=1,
        errors=["Fila 5: precio inválido"],
        status="partial",
    )
    assert result.status == "partial"
    print("  ✅ PropertyCSVRow + IngestionResult válidos")
    print("\n🎉 Smoke test pasó")