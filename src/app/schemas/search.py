# src/app/schemas/search.py
"""Schemas Pydantic para búsqueda híbrida."""

from pydantic import BaseModel, Field
from src.app.schemas.property import PropertyChatSummary
from src.app.core.constants import SearchSource


class FilterQuery(BaseModel):
    """Filtros estructurales extraídos del texto del usuario."""
    
    property_type: list[str] | None = None
    zone: str | None = None
    min_price_usd: float | None = None
    max_price_usd: float | None = None
    min_price_bs: float | None = None
    max_price_bs: float | None = None
    bedrooms_min: int | None = None
    bathrooms_min: int | None = None
    area_min_m2: float | None = None
    area_max_m2: float | None = None
    vista_al_mar: bool | None = None
    frente_playa: bool | None = None
    uso_vacacional: bool | None = None
    tipo_especial: str | None = None
    
    # Metadata del extractor
    raw_query: str = Field(default="", description="Query original del usuario")
    extracted_by: str = Field(default="regex", description="regex | llm_fallback")
    
   
    @property
    def is_empty(self) -> bool:
        """True si no hay filtros estructurales extraídos."""
        return all(
            v is None for v in [
                self.property_type, self.zone,
                self.min_price_usd, self.max_price_usd,
                self.min_price_bs, self.max_price_bs,
                self.bedrooms_min, self.bathrooms_min,
                self.area_min_m2, self.area_max_m2,
                self.vista_al_mar, self.frente_playa,
                self.uso_vacacional, self.tipo_especial,
            ]
        )


class SearchResult(BaseModel):
    """Resultado de búsqueda híbrida."""
    
    properties: list[PropertyChatSummary]
    source: SearchSource
    total_found: int
    query_text: str | None = None


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — schemas/search.py")
    
    # FilterQuery vacío
    fq_empty = FilterQuery(raw_query="hola")
    assert fq_empty.is_empty is True
    assert fq_empty.extracted_by == "regex"
    
    # FilterQuery con filtros
    fq = FilterQuery(
        zone="Pampatar",
        max_price_usd=200000,
        bedrooms_min=3,
        vista_al_mar=True,
        raw_query="apto en Pampatar",
    )
    assert fq.is_empty is False
    assert fq.zone == "Pampatar"
    
    # SearchResult
    sr = SearchResult(
        properties=[{"id": "p1", "title": "Apto"}],
        source="sql",
        total_found=1,
    )
    assert sr.source == "sql"
    print("  ✅ FilterQuery + SearchResult válidos")
    print("\n🎉 Smoke test pasó")
