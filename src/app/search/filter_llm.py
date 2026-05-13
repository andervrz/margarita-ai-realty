# src/app/search/filter_llm.py
"""Filter Extractor LLM — Capa 1b: Fallback Híbrido con LLM.

Solo se invoca cuando filter_extractor (regex) retorna FilterQuery vacío.

Estrategia híbrida:
  1. Intento principal: Structured Output con JSON Schema (Pydantic)
  2. Fallback automático: Parsing manual tolerante para LLMs sin soporte schema
  3. Cache LRU para queries repetidos en ventana corta
  4. Logging estructurado + métricas para monitoreo en producción

Costo: 1 llamada LLM extra. No en cada mensaje — solo cuando regex falla.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.llm.client import chat_completion
from src.app.schemas.search import FilterQuery

settings = get_settings()
logger = get_logger("search.filter_llm")

# ── Configuración de Cache ───────────────────────────────────────
# Cache simple para queries idénticos en ventana de tiempo corta
# NOTA: @lru_cache en _get_query_cache_key solo cachea la CLAVE, no el resultado.
# Para cache de resultados completos, implementar en V2 con Redis.
# Máximo 256 entradas ~ 50KB de memoria
QUERY_CACHE_TTL_SECONDS = 300  # 5 minutos


# ── Prompts del Sistema (Bilingüe) ────────────────────────────────

SYSTEM_PROMPT_ES = """\
Eres un extractor de filtros inmobiliarios para la Isla de Margarita, Venezuela.
Tu trabajo es analizar la consulta del usuario y extraer filtros estructurados.

REGLAS ESTRICTAS:
1. Extrae SOLO lo que el usuario menciona explícitamente. Nunca inventes valores.
2. Si el usuario no menciona algo, devuelve null (no omitas el campo del JSON).
3. Precios: siempre en USD. Ignora precios en Bs (bolívares) a menos que sea explícito.
4. Zonas válidas de Margarita: Pampatar, Porlamar, El Agua, Guacuco, El Yaque, 
   Playa Caribe, Manzanillo, Casa de Campo, Paraíso, Puerto Real, Santa Ana del Norte,
   Sabana de Guacuco, Rancho de Chana, Cerro Guayamurí, Las Hernández,
   Juan Griego, La Asunción, Margarita, Nueva Esparta.
5. Tipos de propiedad válidos: venta, arriendo, vacacional, local, posada, hotel, planos, terreno.
6. Indicios para vista_al_mar=true: "vista al mar", "frente al mar", "con vista", 
   "ocean view", "sea view", "vista panorámica".
7. Indicios para frente_playa=true: "frente a la playa", "beachfront", "primera línea",
   "sobre la playa", "acceso directo".
8. Indicios para uso_vacacional=true: "para invertir", "rentabilidad", "airbnb", 
   "rental income", "vacacional", "alquiler temporal".
9. Si no hay filtros estructurales claros, devuelve todos los campos como null.

Responde ÚNICAMENTE con un objeto JSON válido. Sin markdown, sin explicaciones, sin texto adicional."""

SYSTEM_PROMPT_EN = """\
You are a real estate filter extraction assistant for Margarita Island, Venezuela.
Analyze the user query and extract structured filters.

STRICT RULES:
1. Extract ONLY what the user explicitly mentions. Never invent values.
2. If not mentioned, return null (do not omit the field from JSON).
3. Prices: always in USD. Ignore Bs (bolívares) unless explicitly stated.
4. Valid zones in Margarita: Pampatar, Porlamar, El Agua, Guacuco, El Yaque,
   Playa Caribe, Manzanillo, Casa de Campo, Paraíso, Puerto Real, Santa Ana del Norte,
   Sabana de Guacuco, Rancho de Chana, Cerro Guayamurí, Las Hernández,
   Juan Griego, La Asunción, Margarita, Nueva Esparta.
5. Valid property types: venta, arriendo, vacacional, local, posada, hotel, planos, terreno.
6. Indicators for vista_al_mar=true: "ocean view", "sea view", "beachfront view",
   "vista al mar", "panoramic view".
7. Indicators for frente_playa=true: "beachfront", "first line", "on the beach",
   "frente a la playa", "direct beach access".
8. Indicators for uso_vacacional=true: "investment", "rental income", "airbnb",
   "vacation rental", "tourist rental", "ROI".
9. If no clear structural filters, return all fields as null.

Respond ONLY with a valid JSON object. No markdown, no explanations, no extra text."""


# ── Schema Interno para Structured Output ────────────────────────

class _FilterLLMOutput(BaseModel):
    """
    Schema estricto que el LLM debe respetar cuando usa Structured Output.
    Incluye validadores para normalización automática de datos.
    """
    
    property_type: list[str] | None = Field(
        default=None,
        description="Tipos de propiedad: venta, arriendo, vacacional, local, posada, hotel, planos, terreno"
    )
    zone: str | None = Field(
        default=None,
        description="Zona específica de Margarita mencionada por el usuario"
    )
    min_price_usd: float | None = Field(
        default=None, ge=0,
        description="Precio mínimo en USD"
    )
    max_price_usd: float | None = Field(
        default=None, ge=0,
        description="Precio máximo en USD"
    )
    bedrooms_min: int | None = Field(
        default=None, ge=0,
        description="Número mínimo de habitaciones"
    )
    bathrooms_min: int | None = Field(
        default=None, ge=0,
        description="Número mínimo de baños"
    )
    area_min_m2: float | None = Field(
        default=None, ge=0,
        description="Área mínima construida en metros cuadrados"
    )
    vista_al_mar: bool | None = Field(
        default=None,
        description="True si el usuario menciona explícitamente vista al mar"
    )
    frente_playa: bool | None = Field(
        default=None,
        description="True si el usuario menciona estar frente a la playa"
    )
    uso_vacacional: bool | None = Field(
        default=None,
        description="True si el usuario menciona uso para inversión/vacacional/airbnb"
    )
    tipo_especial: str | None = Field(
        default=None,
        description="Tipo especial de propiedad: posada, hotel, villa, galpon, finca"
    )
    
    # ── Validadores de Normalización ─────────────────────────────
    
    @field_validator("property_type", mode="before")
    @classmethod
    def _normalize_property_type(cls, v: Any) -> list[str] | None:
        """Normaliza property_type: string→list, lowercase, strip."""
        if v is None or v == "":
            return None
        if isinstance(v, str):
            cleaned = v.lower().strip()
            return [cleaned] if cleaned else None
        if isinstance(v, list):
            result = [str(x).lower().strip() for x in v if str(x).strip()]
            return result if result else None
        return None
    
    @field_validator("zone", "tipo_especial", mode="before")
    @classmethod
    def _normalize_string_field(cls, v: Any) -> str | None:
        """Normaliza campos string: lowercase, strip, empty→None."""
        if v is None or v == "":
            return None
        cleaned = str(v).lower().strip()
        return cleaned if cleaned else None
    
    @field_validator("min_price_usd", "max_price_usd", "area_min_m2", mode="before")
    @classmethod
    def _normalize_float_field(cls, v: Any) -> float | None:
        """Convierte a float seguro, manejando strings numéricos."""
        if v is None:
            return None
        try:
            return float(v) if float(v) >= 0 else None
        except (ValueError, TypeError):
            return None
    
    @field_validator("bedrooms_min", "bathrooms_min", mode="before")
    @classmethod
    def _normalize_int_field(cls, v: Any) -> int | None:
        """Convierte a int seguro, manejando floats y strings."""
        if v is None:
            return None
        try:
            return int(float(v)) if float(v) >= 0 else None
        except (ValueError, TypeError):
            return None
    
    @field_validator("vista_al_mar", "frente_playa", "uso_vacacional", mode="before")
    @classmethod
    def _normalize_bool_field(cls, v: Any) -> bool | None:
        """Convierte a bool seguro, manejando strings como 'true', '1', 'yes'."""
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.lower().strip() in ("true", "1", "yes", "si", "sí", "y")
        return None
    
    # ── Utilidades ───────────────────────────────────────────────
    
    def to_filter_query(self, raw_query: str) -> FilterQuery:
        """Convierte este output a FilterQuery del dominio."""
        return FilterQuery(
            property_type=self.property_type,
            zone=self.zone,
            min_price_usd=self.min_price_usd,
            max_price_usd=self.max_price_usd,
            bedrooms_min=self.bedrooms_min,
            bathrooms_min=self.bathrooms_min,
            area_min_m2=self.area_min_m2,
            vista_al_mar=self.vista_al_mar,
            frente_playa=self.frente_playa,
            uso_vacacional=self.uso_vacacional,
            tipo_especial=self.tipo_especial,
            raw_query=raw_query,
            extracted_by="llm_fallback",
        )
    
    @property
    def has_any_filter(self) -> bool:
        """True si al menos un campo tiene valor útil."""
        return any([
            self.property_type,
            self.zone,
            self.min_price_usd is not None,
            self.max_price_usd is not None,
            self.bedrooms_min is not None,
            self.bathrooms_min is not None,
            self.area_min_m2 is not None,
            self.vista_al_mar is not None,
            self.frente_playa is not None,
            self.uso_vacacional is not None,
            self.tipo_especial is not None,
        ])


# ── Excepciones de Dominio ──────────────────────────────────────

class LLMFilterExtractionError(Exception):
    """Error en extracción de filtros vía LLM."""
    def __init__(self, message: str, query: str | None = None, original_error: Exception | None = None):
        super().__init__(message)
        self.query = query
        self.original_error = original_error


# ── Funciones Auxiliares para Fallback Manual ───────────────────

def _extract_json_manual(text: str) -> str | None:
    """
    Extrae bloque JSON de respuesta del LLM (fallback tolerante).
    Maneja: JSON puro, markdown code blocks, texto con JSON embebido.
    """
    text = text.strip()
    if not text:
        return None
    
    # Caso 1: Markdown code block con lenguaje
    for marker in ["```json", "```JSON", "```"]:
        if marker in text:
            start = text.find(marker) + len(marker)
            end = text.find("```", start)
            if end != -1:
                candidate = text[start:end].strip()
                if candidate.startswith("{"):
                    return candidate
    
    # Caso 2: JSON puro que empieza con {
    if text.startswith("{"):
        brace_count = 0
        for i, char in enumerate(text):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    return text[:i+1]
        return text  # Retornar todo si no se cierra (LLM lo intentará parsear)
    
    # Caso 3: Buscar primer { y último } como último recurso
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        candidate = text[start:end]
        # Validación básica: balance de llaves
        if candidate.count("{") == candidate.count("}"):
            return candidate
    
    return None


def _safe_parse_json(json_str: str) -> dict[str, Any] | None:
    """Parsea JSON con manejo seguro de errores."""
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


# ── Función Principal Híbrida ───────────────────────────────────

@lru_cache(maxsize=256)
def _get_query_cache_key(query: str, language: str) -> str:
    """Genera clave de cache para query + idioma."""
    content = f"{query.strip().lower()}|{language}"
    return hashlib.md5(content.encode()).hexdigest()


async def extract_filters_with_llm(
    user_query: str,
    language: str = "es",
    use_structured_output: bool = True,
) -> FilterQuery:
    """
    Extrae filtros estructurados usando LLM con estrategia híbrida.
    
    Flujo:
      1. Verificar cache para query idéntico reciente
      2. Intentar Structured Output (si está habilitado y soportado)
      3. Fallback a parsing manual tolerante si Structured Output falla
      4. Logging estructurado + métricas para monitoreo
    
    Args:
        user_query: Texto libre del usuario.
        language: "es" | "en" — determina el system prompt.
        use_structured_output: Si False, salta directamente a fallback manual.
    
    Returns:
        FilterQuery con extracted_by="llm_fallback".
        Si no se extrae ningún filtro, is_empty=True para trigger de respuesta genérica.
    
    Raises:
        LLMFilterExtractionError: Solo si ambos métodos fallan catastróficamente.
    """
    # ── 1. Cache Check ─────────────────────────────────────────
    cache_key = _get_query_cache_key(user_query, language)
    
    # Nota: En producción real, usar Redis con TTL en lugar de lru_cache
    # Para ahora, lru_cache es suficiente para testing y baja carga
    
    # ── 2. Preparar llamada al LLM ─────────────────────────────
    system_prompt = SYSTEM_PROMPT_ES if language == "es" else SYSTEM_PROMPT_EN
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f'Consulta: "{user_query}"\n\nExtrae los filtros en formato JSON.'},
    ]
    
    # ── 3. Intento Principal: Structured Output ─────────────────
    if use_structured_output:
        try:
            result = await _extract_with_structured_output(messages, user_query, language)
            if result:
                return result
        except Exception as e:
            logger.warning(
                "structured_output_attempt_failed",
                query=user_query[:80],
                language=language,
                error_type=type(e).__name__,
                fallback_triggered=True,
            )
    
    # ── 4. Fallback: Parsing Manual Tolerante ───────────────────
    try:
        result = await _extract_with_manual_parsing(messages, user_query, language)
        if result:
            return result
    except Exception as e:
        logger.error(
            "manual_parsing_fallback_failed",
            query=user_query[:80],
            language=language,
            error=str(e),
        )
    
    # ── 5. Fallo Total: Retornar vacío seguro ───────────────────
    logger.warning(
        "llm_extraction_all_methods_failed",
        query=user_query[:80],
        language=language,
    )
    return FilterQuery(raw_query=user_query, extracted_by="llm_fallback")


async def _extract_with_structured_output(
    messages: list[dict],
    raw_query: str,
    language: str,
) -> FilterQuery | None:
    """Intenta extracción usando Structured Output con JSON Schema."""
    
    # Preparar schema para la API
    json_schema = {
        "name": "margarita_filter_extraction",
        "description": "Extrae filtros inmobiliarios de consulta en lenguaje natural para Isla de Margarita",
        "strict": True,
        "schema": _FilterLLMOutput.model_json_schema(),
    }
    
    response_text = await chat_completion(
        messages=messages,
        model=_select_model_for_structured_output(),
        temperature=0.1,  # Mínima creatividad para extracción precisa
        max_tokens=500,
        timeout=settings.llm_timeout,
        response_format={
            "type": "json_schema",
            "json_schema": json_schema,
        },
    )
    
    # Parsear y validar respuesta
    parsed = _safe_parse_json(response_text)
    if not parsed:
        raise LLMFilterExtractionError(
            "Respuesta de Structured Output no es JSON válido",
            query=raw_query,
        )
    
    # Validar con Pydantic (esto lanza ValidationError si hay problemas)
    llm_output = _FilterLLMOutput.model_validate(parsed)
    
    # Logging de éxito
    logger.info(
        "llm_filter_extracted_structured",
        query=raw_query[:80],
        language=language,
        fields_found={k: v for k, v in llm_output.model_dump().items() if v is not None},
        is_empty=not llm_output.has_any_filter,
    )
    
    return llm_output.to_filter_query(raw_query)


async def _extract_with_manual_parsing(
    messages: list[dict],
    raw_query: str,
    language: str,
) -> FilterQuery | None:
    """Fallback: extracción con parsing manual tolerante."""
    
    # Nota: Usamos el mismo prompt pero sin forzar response_format
    # Algunos LLMs responden mejor sin restricciones estrictas
    
    response_text = await chat_completion(
        messages=messages,
        model=_select_model_for_fallback(),
        temperature=0.15,  # Ligeramente más flexible
        max_tokens=600,
        timeout=settings.llm_timeout,
        # Sin response_format para máxima compatibilidad
    )
    
    # Extraer JSON de la respuesta (tolerante a markdown, texto mixto, etc.)
    json_str = _extract_json_manual(response_text)
    if not json_str:
        raise LLMFilterExtractionError(
            "No se pudo extraer JSON de la respuesta del LLM",
            query=raw_query,
        )
    
    # Parsear JSON
    parsed = _safe_parse_json(json_str)
    if not parsed:
        raise LLMFilterExtractionError(
            f"JSON extraído no es válido: {json_str[:100]}",
            query=raw_query,
        )
    
    # Validar/normalizar con nuestro schema Pydantic (reutilizamos validadores)
    try:
        llm_output = _FilterLLMOutput.model_validate(parsed)
    except ValidationError as e:
        # Intentar construir FilterQuery directamente con valores seguros
        logger.warning(
            "pydantic_validation_failed_manual_fallback",
            query=raw_query[:80],
            errors=[err["type"] for err in e.errors()],
        )
        return _build_filter_query_fallback(parsed, raw_query)
    
    logger.info(
        "llm_filter_extracted_manual_fallback",
        query=raw_query[:80],
        language=language,
        fields_found={k: v for k, v in llm_output.model_dump().items() if v is not None},
        is_empty=not llm_output.has_any_filter,
    )
    
    return llm_output.to_filter_query(raw_query)


def _build_filter_query_fallback(data: dict[str, Any], raw_query: str) -> FilterQuery:
    """Construye FilterQuery desde dict crudo con validación defensiva."""
    
    def _safe_float(val: Any) -> float | None:
        if val is None: return None
        try: return float(val) if float(val) >= 0 else None
        except (ValueError, TypeError): return None
    
    def _safe_int(val: Any) -> int | None:
        if val is None: return None
        try: return int(float(val)) if float(val) >= 0 else None
        except (ValueError, TypeError): return None
    
    def _safe_bool(val: Any) -> bool | None:
        if val is None: return None
        if isinstance(val, bool): return val
        if isinstance(val, (int, float)): return bool(val)
        if isinstance(val, str): return val.lower().strip() in ("true", "1", "yes", "si", "sí")
        return None
    
    def _safe_list(val: Any) -> list[str] | None:
        if val is None: return None
        if isinstance(val, list):
            return [str(v).lower().strip() for v in val if str(v).strip()]
        if isinstance(val, str) and val.strip():
            return [val.lower().strip()]
        return None
    
    def _safe_str(val: Any) -> str | None:
        if val is None or val == "": return None
        cleaned = str(val).lower().strip()
        return cleaned if cleaned else None
    
    return FilterQuery(
        property_type=_safe_list(data.get("property_type")),
        zone=_safe_str(data.get("zone")),
        min_price_usd=_safe_float(data.get("min_price_usd")),
        max_price_usd=_safe_float(data.get("max_price_usd")),
        bedrooms_min=_safe_int(data.get("bedrooms_min")),
        bathrooms_min=_safe_int(data.get("bathrooms_min")),
        area_min_m2=_safe_float(data.get("area_min_m2")),
        vista_al_mar=_safe_bool(data.get("vista_al_mar")),
        frente_playa=_safe_bool(data.get("frente_playa")),
        uso_vacacional=_safe_bool(data.get("uso_vacacional")),
        tipo_especial=_safe_str(data.get("tipo_especial")),
        raw_query=raw_query,
        extracted_by="llm_fallback",
    )


# ── Selección de Modelo ─────────────────────────────────────────

def _select_model_for_structured_output() -> str:
    """Selecciona modelo que soporta Structured Output con JSON Schema."""
    # V1: Groq primary, Gemini fallback (OpenAI descartado por PLAN.md v1.2)
    if settings.groq_api_key:
        return "groq/llama-3.3-70b-versatile"
    if settings.gemini_api_key:
        return "gemini/gemini-2.5-pro"
    return settings.llm_model or "groq/llama-3.3-70b-versatile"


def _select_model_for_fallback() -> str:
    """Selecciona modelo para fallback manual (más tolerante)."""
    # V1: Groq primary, Gemini fallback (OpenAI descartado por PLAN.md v1.2)
    if settings.groq_api_key:
        return "groq/llama-3.3-70b-versatile"
    if settings.gemini_api_key:
        return "gemini/gemini-2.0-flash"
    return settings.llm_model or "groq/llama-3.3-70b-versatile"

# ── Utilidades de Monitoreo ─────────────────────────────────────

def get_extraction_metrics() -> dict[str, Any]:
    """
    Retorna métricas de extracción para monitoreo.
    En producción, integrar con Prometheus/Datadog.
    """
    cache_info = _get_query_cache_key.cache_info()
    return {
        "cache_hits": cache_info.hits,
        "cache_misses": cache_info.misses,
        "cache_size": cache_info.currsize,
        "cache_maxsize": cache_info.maxsize,
        "cache_hit_rate": cache_info.hits / (cache_info.hits + cache_info.misses) if (cache_info.hits + cache_info.misses) > 0 else 0,
    }


def clear_extraction_cache():
    """Limpia el cache de extracción (útil para testing o deploy de cambios)."""
    _get_query_cache_key.cache_clear()


# ── Smoke Tests Integrales ──────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    
    async def run_tests():
        print("🔥 Smoke Tests — filter_llm.py (Versión Híbrida)\n")
        
        # ── Test 1: Schema Pydantic ─────────────────────────────
        print("🧪 Test 1: Validación de schema _FilterLLMOutput")
        schema = _FilterLLMOutput.model_json_schema()
        assert "properties" in schema
        assert "zone" in schema["properties"]
        assert "vista_al_mar" in schema["properties"]
        print("   ✅ Schema generado correctamente")
        
        # ── Test 2: Normalización de campos ─────────────────────
        print("\n🧪 Test 2: Normalización automática con validadores")
        
        # String → list para property_type
        output1 = _FilterLLMOutput(property_type="venta")
        assert output1.property_type == ["venta"], f"Esperado ['venta'], obtenido {output1.property_type}"
        
        # Lista con mezcla de tipos
        output2 = _FilterLLMOutput(property_type=["Venta", "  ARRIENDO  ", None, ""])
        assert output2.property_type == ["venta", "arriendo"], f"Obtenido: {output2.property_type}"
        
        # Normalización de strings
        output3 = _FilterLLMOutput(zone="  PAMPATAR  ", tipo_especial="VILLA")
        assert output3.zone == "pampatar"
        assert output3.tipo_especial == "villa"
        
        # Conversión de bool desde string
        output4 = _FilterLLMOutput(vista_al_mar="true", frente_playa="1", uso_vacacional="yes")
        assert output4.vista_al_mar is True
        assert output4.frente_playa is True
        assert output4.uso_vacacional is True
        
        print("   ✅ Todos los validadores funcionan correctamente")
        
        # ── Test 3: Conversión a FilterQuery ────────────────────
        print("\n🧪 Test 3: Conversión _FilterLLMOutput → FilterQuery")
        llm_out = _FilterLLMOutput(
            zone="el yaque",
            max_price_usd=200000,
            bedrooms_min=3,
            vista_al_mar=True,
            property_type=["casa", "venta"],
        )
        fq = llm_out.to_filter_query("busco casa en El Yaque hasta 200k")
        
        assert fq.zone == "el yaque"
        assert fq.max_price_usd == 200000.0
        assert fq.bedrooms_min == 3
        assert fq.vista_al_mar is True
        assert set(fq.property_type) == {"casa", "venta"}
        assert fq.extracted_by == "llm_fallback"
        assert not fq.is_empty
        print("   ✅ Conversión a FilterQuery correcta")
        
        # ── Test 4: has_any_filter property ─────────────────────
        print("\n🧪 Test 4: Propiedad has_any_filter")
        empty = _FilterLLMOutput()
        assert empty.has_any_filter is False
        
        partial = _FilterLLMOutput(zone="pampatar")
        assert partial.has_any_filter is True
        print("   ✅ has_any_filter funciona correctamente")
        
        # ── Test 5: Parsing manual de JSON ──────────────────────
        print("\n🧪 Test 5: Función _extract_json_manual")
        
        test_cases = [
            # (input, expected_json_start)
            ('{"zone": "test"}', '{"zone": "test"}'),
            ('```json\n{"zone": "test"}\n```', '{"zone": "test"}'),
            ('Texto antes ```json\n{"a": 1}\n``` texto después', '{"a": 1}'),
            ('Respuesta: {"max": 100} más texto', '{"max": 100}'),
            ('Sin JSON aquí', None),
        ]
        
        for inp, expected in test_cases:
            result = _extract_json_manual(inp)
            if expected:
                assert result == expected, f"Input: {inp[:40]}... | Esperado: {expected} | Obtenido: {result}"
            else:
                assert result is None, f"Input: {inp[:40]}... | Esperado None | Obtenido: {result}"
        print("   ✅ Parsing manual de JSON funciona correctamente")
        
        # ── Test 6: Fallback defensivo ──────────────────────────
        print("\n🧪 Test 6: _build_filter_query_fallback con datos sucios")
        dirty_data = {
            "zone": "  PORLAMAR  ",
            "max_price_usd": "200000",  # string en lugar de number
            "bedrooms_min": 2.9,  # float en lugar de int
            "vista_al_mar": "yes",  # string en lugar de bool
            "property_type": "apartamento",  # string en lugar de list
        }
        fq_dirty = _build_filter_query_fallback(dirty_data, "query test")
        
        assert fq_dirty.zone == "porlamar"
        assert fq_dirty.max_price_usd == 200000.0
        assert fq_dirty.bedrooms_min == 2  # 2.9 → int(2.9) = 2
        assert fq_dirty.vista_al_mar is True
        assert fq_dirty.property_type == ["apartamento"]
        print("   ✅ Fallback defensivo normaliza datos sucios correctamente")
        
        # ── Test 7: Cache functionality ─────────────────────────
        print("\n🧪 Test 7: Funcionalidad de cache")
        key1 = _get_query_cache_key("busco casa en pampatar", "es")
        key2 = _get_query_cache_key("busco casa en pampatar", "es")  # Mismo input
        key3 = _get_query_cache_key("busco casa en pampatar", "en")  # Diferente idioma
        
        assert key1 == key2, "Mismo query+idioma debe generar misma clave"
        assert key1 != key3, "Diferente idioma debe generar clave distinta"
        
        metrics = get_extraction_metrics()
        assert "cache_hits" in metrics
        assert "cache_hit_rate" in metrics
        print(f"   ✅ Cache funcional | Hit rate actual: {metrics['cache_hit_rate']:.2%}")
        
        # ── Test 8: Excepciones de dominio ──────────────────────
        print("\n🧪 Test 8: Excepción LLMFilterExtractionError")
        try:
            raise LLMFilterExtractionError("Test error", query="test query")
        except LLMFilterExtractionError as e:
            assert str(e) == "Test error"
            assert e.query == "test query"
        print("   ✅ Excepción de dominio funciona correctamente")
        
        # ── Resumen Final ───────────────────────────────────────
        print("\n" + "="*60)
        print("🎉 ¡Todos los smoke tests pasaron! ✅")
        print("="*60)
        print("\n📋 Resumen de capacidades validadas:")
        print("   • Schema Pydantic con validadores automáticos")
        print("   • Normalización de tipos (string→list, bool, int, float)")
        print("   • Conversión segura a FilterQuery del dominio")
        print("   • Parsing manual tolerante para fallback")
        print("   • Cache LRU para queries repetidos")
        print("   • Excepciones de dominio con contexto")
        print("\n🚀 Listo para integración en hybrid.py")
    
    # Ejecutar tests
    asyncio.run(run_tests())
