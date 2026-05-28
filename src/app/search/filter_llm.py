# src/app/search/filter_llm.py
"""Filter Extractor LLM — Capa 1b: Fallback con LLM.

Solo se invoca cuando filter_extractor (regex) retorna FilterQuery vacío.

Estrategia:
  1. Structured Output con JSON Schema (Pydantic)
  2. Fallback: Parsing manual tolerante
  3. Cache en memoria para queries repetidos
  4. Logging estructurado para monitoreo
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.llm.client import chat_completion
from src.app.schemas.search import FilterQuery

logger = get_logger(__name__)

# ── Cache en memoria para queries repetidos ───────────────────────
# V2: migrar a Redis con TTL para multi-worker
_filter_cache: dict[str, FilterQuery] = {}
_CACHE_MAX_SIZE = 256


# ── Prompts del Sistema ───────────────────────────────────────────

SYSTEM_PROMPT_ES = """\
Eres un extractor de filtros inmobiliarios para la Isla de Margarita, Venezuela.
Analiza la consulta del usuario y extrae filtros estructurados.

REGLAS ESTRICTAS:
1. Extrae SOLO lo que el usuario menciona explícitamente. Nunca inventes valores.
2. Si no se menciona algo, devuelve null.
3. Precios siempre en USD.
4. Zonas válidas: Pampatar, Porlamar, El Agua, Guacuco, El Yaque, Playa Caribe,
   Manzanillo, Casa de Campo, Paraíso, Puerto Real, Santa Ana del Norte,
   Sabana de Guacuco, Rancho de Chana, Las Hernández, Juan Griego, La Asunción.
5. Tipos válidos: venta, arriendo, vacacional, local, posada, hotel, planos, terreno.
6. vista_al_mar=true: "vista al mar", "ocean view", "sea view", "vista panorámica".
7. frente_playa=true: "frente a la playa", "beachfront", "primera línea".
8. uso_vacacional=true: "para invertir", "airbnb", "rental income", "vacacional".
9. Sin filtros claros → todos los campos null.

Responde ÚNICAMENTE con JSON válido. Sin markdown ni texto adicional."""

SYSTEM_PROMPT_EN = """\
You are a real estate filter extractor for Margarita Island, Venezuela.
Analyze the user query and extract structured filters.

STRICT RULES:
1. Extract ONLY what the user explicitly mentions. Never invent values.
2. If not mentioned, return null.
3. Prices always in USD.
4. Valid zones: Pampatar, Porlamar, El Agua, Guacuco, El Yaque, Playa Caribe,
   Manzanillo, Casa de Campo, Paraíso, Puerto Real, Santa Ana del Norte,
   Sabana de Guacuco, Rancho de Chana, Las Hernández, Juan Griego, La Asunción.
5. Valid types: venta, arriendo, vacacional, local, posada, hotel, planos, terreno.
6. vista_al_mar=true: "ocean view", "sea view", "beachfront view", "panoramic view".
7. frente_playa=true: "beachfront", "first line", "on the beach".
8. uso_vacacional=true: "investment", "rental income", "airbnb", "vacation rental".
9. No clear filters → return all fields as null.

Respond ONLY with valid JSON. No markdown or extra text."""


# ── Schema Interno ────────────────────────────────────────────────

class _FilterLLMOutput(BaseModel):
    """Schema para el output estructurado del LLM."""

    property_type: list[str] | None = Field(default=None)
    zone: str | None = Field(default=None)
    min_price_usd: float | None = Field(default=None, ge=0)
    max_price_usd: float | None = Field(default=None, ge=0)
    bedrooms_min: int | None = Field(default=None, ge=0)
    bathrooms_min: int | None = Field(default=None, ge=0)
    area_min_m2: float | None = Field(default=None, ge=0)
    vista_al_mar: bool | None = Field(default=None)
    frente_playa: bool | None = Field(default=None)
    uso_vacacional: bool | None = Field(default=None)
    tipo_especial: str | None = Field(default=None)

    @field_validator("property_type", mode="before")
    @classmethod
    def _normalize_property_type(cls, v: Any) -> list[str] | None:
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
    def _normalize_string(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        cleaned = str(v).lower().strip()
        return cleaned if cleaned else None

    @field_validator("min_price_usd", "max_price_usd", "area_min_m2", mode="before")
    @classmethod
    def _normalize_float(cls, v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v) if float(v) >= 0 else None
        except (ValueError, TypeError):
            return None

    @field_validator("bedrooms_min", "bathrooms_min", mode="before")
    @classmethod
    def _normalize_int(cls, v: Any) -> int | None:
        if v is None:
            return None
        try:
            return int(float(v)) if float(v) >= 0 else None
        except (ValueError, TypeError):
            return None

    @field_validator("vista_al_mar", "frente_playa", "uso_vacacional", mode="before")
    @classmethod
    def _normalize_bool(cls, v: Any) -> bool | None:
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.lower().strip() in ("true", "1", "yes", "si", "sí", "y")
        return None

    def to_filter_query(self, raw_query: str) -> FilterQuery:
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
        return any([
            self.property_type, self.zone,
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


# ── Excepción de Dominio ──────────────────────────────────────────

class LLMFilterExtractionError(Exception):
    """Error en extracción de filtros vía LLM."""
    def __init__(
        self,
        message: str,
        query: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message)
        self.query = query
        self.original_error = original_error


# ── Helpers ───────────────────────────────────────────────────────

def _get_cache_key(query: str, language: str) -> str:
    content = f"{query.strip().lower()}|{language}"
    return hashlib.sha256(content.encode()).hexdigest()


def _extract_json_from_text(text: str) -> str | None:
    """Extrae JSON de respuesta del LLM. Tolera markdown y texto mixto."""
    text = text.strip()
    if not text:
        return None

    for marker in ["```json", "```JSON", "```"]:
        if marker in text:
            start = text.find(marker) + len(marker)
            end = text.find("```", start)
            if end != -1:
                candidate = text[start:end].strip()
                if candidate.startswith("{"):
                    return candidate

    if text.startswith("{"):
        brace_count = 0
        for i, char in enumerate(text):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    return text[:i + 1]
        return text

    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        candidate = text[start:end]
        if candidate.count("{") == candidate.count("}"):
            return candidate

    return None


def _safe_parse_json(json_str: str) -> dict[str, Any] | None:
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _build_fallback_filter_query(
    data: dict[str, Any],
    raw_query: str,
) -> FilterQuery:
    """Construye FilterQuery desde dict crudo con validación defensiva."""

    def _sf(val: Any) -> float | None:
        if val is None:
            return None
        try:
            return float(val) if float(val) >= 0 else None
        except (ValueError, TypeError):
            return None

    def _si(val: Any) -> int | None:
        if val is None:
            return None
        try:
            return int(float(val)) if float(val) >= 0 else None
        except (ValueError, TypeError):
            return None

    def _sb(val: Any) -> bool | None:
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            return val.lower().strip() in ("true", "1", "yes", "si", "sí")
        return None

    def _sl(val: Any) -> list[str] | None:
        if val is None:
            return None
        if isinstance(val, list):
            return [str(v).lower().strip() for v in val if str(v).strip()]
        if isinstance(val, str) and val.strip():
            return [val.lower().strip()]
        return None

    def _ss(val: Any) -> str | None:
        if val is None or val == "":
            return None
        cleaned = str(val).lower().strip()
        return cleaned if cleaned else None

    return FilterQuery(
        property_type=_sl(data.get("property_type")),
        zone=_ss(data.get("zone")),
        min_price_usd=_sf(data.get("min_price_usd")),
        max_price_usd=_sf(data.get("max_price_usd")),
        bedrooms_min=_si(data.get("bedrooms_min")),
        bathrooms_min=_si(data.get("bathrooms_min")),
        area_min_m2=_sf(data.get("area_min_m2")),
        vista_al_mar=_sb(data.get("vista_al_mar")),
        frente_playa=_sb(data.get("frente_playa")),
        uso_vacacional=_sb(data.get("uso_vacacional")),
        tipo_especial=_ss(data.get("tipo_especial")),
        raw_query=raw_query,
        extracted_by="llm_fallback",
    )


# ── Selección de Modelo ───────────────────────────────────────────

def _select_model() -> str:
    settings = get_settings()
    if settings.groq_api_key:
        return "groq/llama-3.3-70b-versatile"
    if settings.gemini_api_key:
        return "gemini/gemini-2.5-pro"
    return "groq/llama-3.3-70b-versatile"


# ── Función Principal ─────────────────────────────────────────────

async def extract_filters_with_llm(
    user_query: str,
    language: str = "es",
) -> FilterQuery:
    """
    Extrae filtros usando LLM. Solo se invoca cuando regex retorna vacío.

    Flujo:
      1. Cache check — query idéntico reciente
      2. Structured Output con JSON Schema
      3. Fallback: parsing manual tolerante
      4. Fallo total: retorna FilterQuery vacío seguro

    Returns:
        FilterQuery con extracted_by="llm_fallback".
    """
    # 1. Cache check
    cache_key = _get_cache_key(user_query, language)
    if cache_key in _filter_cache:
        logger.debug("llm_filter_cache_hit", query=user_query[:60])
        return _filter_cache[cache_key]

    settings = get_settings()
    system_prompt = SYSTEM_PROMPT_ES if language == "es" else SYSTEM_PROMPT_EN
    model = _select_model()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f'Consulta: "{user_query}"\n\nExtrae los filtros en JSON.'},
    ]

    # 2. Structured Output
    try:
        result = await _try_structured_output(messages, user_query, language, model, settings)
        if result:
            _cache_result(cache_key, result)
            return result
    except Exception as e:
        logger.warning(
            "structured_output_failed",
            query=user_query[:60],
            error_type=type(e).__name__,
        )

    # 3. Parsing manual
    try:
        result = await _try_manual_parsing(messages, user_query, language, model, settings)
        if result:
            _cache_result(cache_key, result)
            return result
    except Exception as e:
        logger.error(
            "manual_parsing_failed",
            query=user_query[:60],
            error=str(e),
        )

    # 4. Fallo total — retorno vacío seguro
    logger.warning("llm_extraction_all_failed", query=user_query[:60])
    return FilterQuery(raw_query=user_query, extracted_by="llm_fallback")


def _cache_result(cache_key: str, result: FilterQuery) -> None:
    """Guarda en cache con límite de tamaño FIFO."""
    if len(_filter_cache) >= _CACHE_MAX_SIZE:
        oldest_key = next(iter(_filter_cache))
        del _filter_cache[oldest_key]
    _filter_cache[cache_key] = result


async def _try_structured_output(
    messages: list[dict],
    raw_query: str,
    language: str,
    model: str,
    settings: Any,
) -> FilterQuery | None:
    """Intenta extracción con Structured Output."""
    json_schema = {
        "name": "margarita_filter_extraction",
        "strict": True,
        "schema": _FilterLLMOutput.model_json_schema(),
    }

    response_text = await chat_completion(
        messages=messages,
        model=model,
        temperature=0.1,
        max_tokens=500,
        timeout=settings.llm_timeout,
        response_format={"type": "json_schema", "json_schema": json_schema},
    )

    parsed = _safe_parse_json(response_text)
    if not parsed:
        return None

    llm_output = _FilterLLMOutput.model_validate(parsed)

    logger.info(
        "llm_filter_structured_ok",
        query=raw_query[:60],
        language=language,
        has_filters=llm_output.has_any_filter,
    )

    return llm_output.to_filter_query(raw_query)


async def _try_manual_parsing(
    messages: list[dict],
    raw_query: str,
    language: str,
    model: str,
    settings: Any,
) -> FilterQuery | None:
    """Fallback: parsing manual tolerante sin response_format."""
    response_text = await chat_completion(
        messages=messages,
        model=model,
        temperature=0.15,
        max_tokens=600,
        timeout=settings.llm_timeout,
    )

    json_str = _extract_json_from_text(response_text)
    if not json_str:
        raise LLMFilterExtractionError(
            "No se pudo extraer JSON de la respuesta",
            query=raw_query,
        )

    parsed = _safe_parse_json(json_str)
    if not parsed:
        raise LLMFilterExtractionError(
            f"JSON extraído inválido: {json_str[:100]}",
            query=raw_query,
        )

    try:
        llm_output = _FilterLLMOutput.model_validate(parsed)
    except ValidationError as e:
        logger.warning(
            "pydantic_validation_failed",
            query=raw_query[:60],
            errors=[err["type"] for err in e.errors()],
        )
        return _build_fallback_filter_query(parsed, raw_query)

    logger.info(
        "llm_filter_manual_ok",
        query=raw_query[:60],
        language=language,
        has_filters=llm_output.has_any_filter,
    )

    return llm_output.to_filter_query(raw_query)


def get_cache_stats() -> dict[str, Any]:
    """Retorna estadísticas del cache para monitoreo."""
    return {
        "cache_size": len(_filter_cache),
        "cache_max_size": _CACHE_MAX_SIZE,
    }


def clear_cache() -> None:
    """Limpia el cache (útil para testing)."""
    _filter_cache.clear()


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio

    async def run_tests():
        print("🔥 Smoke Tests — filter_llm.py\n")

        # Test 1: Normalización de campos
        print("🧪 Test 1: Validadores de normalización")
        out = _FilterLLMOutput(
            property_type="venta",
            zone="  PAMPATAR  ",
            vista_al_mar="true",
            bedrooms_min=2.9,
            max_price_usd="200000",
        )
        assert out.property_type == ["venta"]
        assert out.zone == "pampatar"
        assert out.vista_al_mar is True
        assert out.bedrooms_min == 2
        assert out.max_price_usd == 200000.0
        print("   ✅ Normalización correcta")

        # Test 2: Conversión a FilterQuery
        print("\n🧪 Test 2: to_filter_query")
        fq = out.to_filter_query("busco casa en Pampatar")
        assert fq.extracted_by == "llm_fallback"
        assert fq.zone == "pampatar"
        assert not fq.is_empty
        print("   ✅ FilterQuery generado correctamente")

        # Test 3: Extracción JSON de texto con markdown
        print("\n🧪 Test 3: _extract_json_from_text")
        cases = [
            ('{"zone": "pampatar"}', '{"zone": "pampatar"}'),
            ('```json\n{"zone": "test"}\n```', '{"zone": "test"}'),
            ('Texto: {"max": 100} más', '{"max": 100}'),
            ("Sin JSON", None),
        ]
        for inp, expected in cases:
            result = _extract_json_from_text(inp)
            assert result == expected, f"Input: {inp[:30]} | Esperado: {expected} | Obtenido: {result}"
        print("   ✅ Extracción JSON tolerante correcta")

        # Test 4: Cache funcional
        print("\n🧪 Test 4: Cache")
        clear_cache()
        key1 = _get_cache_key("busco casa", "es")
        key2 = _get_cache_key("busco casa", "es")
        key3 = _get_cache_key("busco casa", "en")
        assert key1 == key2
        assert key1 != key3
        print("   ✅ Cache keys correctas")

        # Test 5: Fallback defensivo
        print("\n🧪 Test 5: _build_fallback_filter_query")
        dirty = {
            "zone": "  PORLAMAR  ",
            "max_price_usd": "200000",
            "bedrooms_min": 2.9,
            "vista_al_mar": "yes",
            "property_type": "apartamento",
        }
        fq_dirty = _build_fallback_filter_query(dirty, "query test")
        assert fq_dirty.zone == "porlamar"
        assert fq_dirty.max_price_usd == 200000.0
        assert fq_dirty.bedrooms_min == 2
        assert fq_dirty.vista_al_mar is True
        assert fq_dirty.property_type == ["apartamento"]
        print("   ✅ Fallback defensivo normaliza datos sucios")

        print("\n🎉 Todos los smoke tests pasaron ✅")

    asyncio.run(run_tests())
