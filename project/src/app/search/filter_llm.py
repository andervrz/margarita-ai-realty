"""Filter Extractor LLM Fallback — extracción estructurada de filtros vía LiteLLM.

Solo se invoca cuando el extractor regex no encuentra filtros estructurales.
Usa Structured Output (JSON Schema) para garantizar tipo de retorno.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.llm.client import chat_completion
from app.schemas.search import FilterQuery


# ── Prompt System para LLM Fallback ───────────────────────────────

SYSTEM_PROMPT_ES = """\
Eres un extractor de filtros inmobiliarios para la Isla de Margarita, Venezuela.
Tu trabajo es analizar la consulta del usuario y extraer filtros estructurados.

REGLAS:
1. Extrae SOLO lo que el usuario menciona explícitamente. No inventes valores.
2. Si el usuario no menciona algo, devuelve null (no omitas el campo).
3. Precios: siempre en USD. Ignora precios en Bs (bolívares).
4. Zonas válidas: Pampatar, Porlamar, El Agua, Guacuco, El Yaque, Playa Caribe, 
   Manzanillo, Casa de Campo, Paraíso, Puerto Real, Santa Ana del Norte,
   Sabana de Guacuco, Rancho de Chana, Cerro Guayamurí, Las Hernández,
   Juan Griego, La Asunción.
5. Tipos de propiedad: venta, arriendo, vacacional, local, posada, hotel, planos, terreno.
6. "vista al mar", "frente al mar", "con vista" → vista_al_mar: true
7. "para invertir", "rentabilidad", "airbnb" → uso_vacacional: true
8. Si no hay filtros estructurales claros, devuelve todos los campos como null.

Responde ÚNICAMENTE en el formato JSON Schema solicitado."""

SYSTEM_PROMPT_EN = """\
You are a real estate filter extractor for Margarita Island, Venezuela.
Analyze the user query and extract structured filters.

RULES:
1. Extract ONLY what the user explicitly mentions. Do not invent values.
2. If not mentioned, return null (do not omit the field).
3. Prices: always in USD. Ignore Bs (bolívares) prices.
4. Valid zones: Pampatar, Porlamar, El Agua, Guacuco, El Yaque, Playa Caribe,
   Manzanillo, Casa de Campo, Paraíso, Puerto Real, Santa Ana del Norte,
   Sabana de Guacuco, Rancho de Chana, Cerro Guayamurí, Las Hernández,
   Juan Griego, La Asunción.
5. Property types: venta, arriendo, vacacional, local, posada, hotel, planos, terreno.
6. "ocean view", "sea view", "beachfront" → vista_al_mar: true
7. "investment", "rental income", "airbnb" → uso_vacacional: true
8. If no clear structural filters, return all fields as null.

Respond ONLY in the requested JSON Schema format."""


# ── Schema interno para Structured Output ─────────────────────────

class _FilterLLMOutput(BaseModel):
    """Schema que el LLM debe respetar (Structured Output)."""
    
    property_type: list[str] | None = Field(
        default=None,
        description="Tipos de propiedad mencionados: venta, arriendo, vacacional, local, posada, hotel, planos, terreno"
    )
    zone: str | None = Field(
        default=None,
        description="Zona específica de Margarita mencionada"
    )
    min_price_usd: float | None = Field(
        default=None,
        description="Precio mínimo en USD"
    )
    max_price_usd: float | None = Field(
        default=None,
        description="Precio máximo en USD"
    )
    bedrooms_min: int | None = Field(
        default=None,
        description="Mínimo de habitaciones"
    )
    bathrooms_min: int | None = Field(
        default=None,
        description="Mínimo de baños"
    )
    area_min_m2: float | None = Field(
        default=None,
        description="Área mínima en m²"
    )
    vista_al_mar: bool | None = Field(
        default=None,
        description="True si menciona vista al mar"
    )
    frente_playa: bool | None = Field(
        default=None,
        description="True si menciona frente a la playa"
    )
    uso_vacacional: bool | None = Field(
        default=None,
        description="True si menciona inversión/vacacional/airbnb"
    )
    tipo_especial: str | None = Field(
        default=None,
        description="Tipo especial: posada, hotel, villa, galpon, finca"
    )
    
    @field_validator("property_type", mode="before")
    @classmethod
    def _normalize_property_type(cls, v: Any) -> list[str] | None:
        if v is None:
            return None
        if isinstance(v, str):
            return [v.lower().strip()]
        if isinstance(v, list):
            return [str(x).lower().strip() for x in v]
        return None
    
    @field_validator("zone", "tipo_especial", mode="before")
    @classmethod
    def _normalize_string(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        return str(v).lower().strip()


# ── Función principal ─────────────────────────────────────────────

async def extract_filters_with_llm(
    user_query: str,
    language: str = "es",
) -> FilterQuery:
    """Extrae filtros estructurados usando LLM Structured Output.
    
    Args:
        user_query: Texto libre del usuario.
        language: "es" | "en" — determina el system prompt.
    
    Returns:
        FilterQuery con extracted_by="llm_fallback".
    
    Raises:
        LLMFilterExtractionError: Si el LLM falla o retorna formato inválido.
    """
    settings = get_settings()
    
    system_prompt = SYSTEM_PROMPT_ES if language == "es" else SYSTEM_PROMPT_EN
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f'Consulta del usuario: "{user_query}"\n\nExtrae los filtros estructurados.'},
    ]
    
    json_schema = {
        "name": "filter_extraction",
        "description": "Extrae filtros inmobiliarios de consulta en lenguaje natural",
        "strict": True,
        "schema": _FilterLLMOutput.model_json_schema(),
    }
    
    try:
        response_text = await chat_completion(
            messages=messages,
            model=settings.groq_api_key and "groq/llama-3.3-70b-versatile" or "gemini/gemini-2.5-pro",
            timeout=settings.llm_timeout,
            response_format={
                "type": "json_schema",
                "json_schema": json_schema,
            },
        )
    except Exception as exc:
        raise LLMFilterExtractionError(
            f"LLM falló en extracción de filtros: {exc}"
        ) from exc
    
    try:
        parsed = json.loads(response_text)
        llm_output = _FilterLLMOutput.model_validate(parsed)
    except (json.JSONDecodeError, Exception) as exc:
        raise LLMFilterExtractionError(
            f"Respuesta LLM no es JSON válido: {response_text[:200]}"
        ) from exc
    
    return FilterQuery(
        property_type=llm_output.property_type,
        zone=llm_output.zone,
        min_price_usd=llm_output.min_price_usd,
        max_price_usd=llm_output.max_price_usd,
        bedrooms_min=llm_output.bedrooms_min,
        bathrooms_min=llm_output.bathrooms_min,
        area_min_m2=llm_output.area_min_m2,
        vista_al_mar=llm_output.vista_al_mar,
        frente_playa=llm_output.frente_playa,
        uso_vacacional=llm_output.uso_vacacional,
        tipo_especial=llm_output.tipo_especial,
        raw_query=user_query,
        extracted_by="llm_fallback",
    )


# ── Excepción de dominio ──────────────────────────────────────────

class LLMFilterExtractionError(Exception):
    """Fallo en extracción de filtros por LLM."""
    pass


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    
    async def _test():
        print("🔥 Smoke Test — filter_llm.py")
        
        # Test 1: Schema interno
        schema = _FilterLLMOutput.model_json_schema()
        assert "properties" in schema
        assert "zone" in schema["properties"]
        assert "vista_al_mar" in schema["properties"]
        print("  ✅ _FilterLLMOutput schema construido")
        
        # Test 2: Validación + normalización
        output = _FilterLLMOutput(
            zone="Pampatar",
            max_price_usd=200000,
            bedrooms_min=3,
            vista_al_mar=True,
        )
        assert output.zone == "pampatar"
        assert output.max_price_usd == 200000
        assert output.vista_al_mar is True
        assert output.frente_playa is None
        print("  ✅ _FilterLLMOutput validación + normalización")
        
        # Test 3: Mapeo a FilterQuery
        fq = FilterQuery(
            zone=output.zone,
            max_price_usd=output.max_price_usd,
            bedrooms_min=output.bedrooms_min,
            vista_al_mar=output.vista_al_mar,
            raw_query="busco apto en Pampatar",
            extracted_by="llm_fallback",
        )
        assert fq.extracted_by == "llm_fallback"
        assert not fq.is_empty
        print("  ✅ Mapeo a FilterQuery correcto")
        
        # Test 4: Normalización property_type
        output2 = _FilterLLMOutput(property_type="venta")
        assert output2.property_type == ["venta"]
        print("  ✅ Normalización property_type string→list")
        
        # Test 5: Excepción
        exc = LLMFilterExtractionError("test error")
        assert str(exc) == "test error"
        print("  ✅ LLMFilterExtractionError instanciada")
        
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())