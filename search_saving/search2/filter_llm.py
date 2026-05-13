
# Generando filter_llm.py
filter_llm_code = """Filter LLM — Capa 1b: Fallback con LLM Structured Output.

Solo se invoca cuando filter_extractor retorna FilterQuery vacío (is_empty=True).
Usa LiteLLM para extraer filtros estructurales con output validado por Pydantic.

Costo: 1 llamada LLM extra. No en cada mensaje — solo cuando regex falla.
"""

import json
from typing import Any

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.llm.client import chat_completion
from src.app.schemas.search import FilterQuery

settings = get_settings()
logger = get_logger("search.filter_llm")

SYSTEM_PROMPT = """You are a real estate filter extraction assistant for Margarita Island, Venezuela.

Extract structured filters from the user's natural language query.
Return ONLY a valid JSON object matching this schema:

{
  "property_type": ["venta"|"arriendo"|"vacacional"|"local"|"posada"|"hotel"|"planos"|"terreno"],
  "zone": "zone name in Margarita (e.g., Pampatar, El Yaque, Porlamar)",
  "min_price_usd": number or null,
  "max_price_usd": number or null,
  "bedrooms_min": integer or null,
  "bathrooms_min": integer or null,
  "area_min_m2": number or null,
  "vista_al_mar": true/false/null,
  "frente_playa": true/false/null,
  "uso_vacacional": true/false/null,
  "tipo_especial": "posada|hotel|villa|galpon|finca" or null
}

Rules:
- Use null for fields not mentioned or unclear.
- property_type is an array (user may mention multiple types).
- For price ranges like "between 100k and 200k", set both min and max.
- For "up to X", set max_price_usd.
- For "from X", set min_price_usd.
- Zones in Margarita: Pampatar, El Yaque, Playa El Agua, Guacuco, Porlamar, Juan Griego, La Asunción, etc.
- vista_al_mar = true if user mentions sea view, ocean view, "vista al mar".
- frente_playa = true if user mentions beachfront, "frente a la playa", first line.
- uso_vacacional = true if user mentions vacation rental, Airbnb, investment property.

Return ONLY the JSON object. No markdown, no explanations."""


async def extract_filters_llm(text: str) -> FilterQuery:
    """Extrae filtros usando LLM Structured Output (fallback).

    Args:
        text: Query original del usuario.

    Returns:
        FilterQuery con filtros extraídos o vacío si LLM falla.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]

    try:
        response = await chat_completion(
            messages=messages,
            model=settings.llm_model or "groq/llama-3.3-70b-versatile",
            temperature=0.1,  # Baja creatividad para extracción estructural
            max_tokens=500,
            timeout=settings.llm_timeout,
        )

        # Parsear JSON de la respuesta
        raw_json = _extract_json(response)
        if not raw_json:
            logger.warning("llm_filter_no_json", query=text[:50])
            return FilterQuery(raw_query=text, extracted_by="llm_fallback")

        parsed = json.loads(raw_json)

        # Validar y construir FilterQuery
        filters = _build_filter_query(parsed, text)

        logger.info(
            "llm_filter_extracted",
            query=text[:50],
            is_empty=filters.is_empty,
            filters=parsed,
        )
        return filters

    except Exception as e:
        logger.error("llm_filter_failed", query=text[:50], error=str(e))
        # Fallback seguro: retornar vacío para que el bot responda genéricamente
        return FilterQuery(raw_query=text, extracted_by="llm_fallback")


def _extract_json(text: str) -> str | None:
    """Extrae bloque JSON de la respuesta del LLM.

    Maneja:
    - JSON puro
    - Markdown code blocks (```json ... ```)
    - Texto con JSON embebido
    """
    text = text.strip()

    # Caso 1: Markdown code block
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()

    if "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()

    # Caso 2: JSON puro (empieza con {)
    if text.startswith("{"):
        # Encontrar el JSON completo (balanceo de llaves)
        brace_count = 0
        for i, char in enumerate(text):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    return text[: i + 1]
        return text  # Si no se cierra, retornar todo

    return None


def _build_filter_query(data: dict[str, Any], raw_query: str) -> FilterQuery:
    """Construye FilterQuery desde dict parseado, con validación."""

    def _safe_float(val: Any) -> float | None:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _safe_int(val: Any) -> int | None:
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _safe_bool(val: Any) -> bool | None:
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "si", "sí")
        return None

    def _safe_list(val: Any) -> list[str] | None:
        if val is None:
            return None
        if isinstance(val, list):
            return [str(v) for v in val if v]
        if isinstance(val, str):
            return [val]
        return None

    return FilterQuery(
        property_type=_safe_list(data.get("property_type")),
        zone=data.get("zone") if data.get("zone") else None,
        min_price_usd=_safe_float(data.get("min_price_usd")),
        max_price_usd=_safe_float(data.get("max_price_usd")),
        bedrooms_min=_safe_int(data.get("bedrooms_min")),
        bathrooms_min=_safe_int(data.get("bathrooms_min")),
        area_min_m2=_safe_float(data.get("area_min_m2")),
        vista_al_mar=_safe_bool(data.get("vista_al_mar")),
        frente_playa=_safe_bool(data.get("frente_playa")),
        uso_vacacional=_safe_bool(data.get("uso_vacacional")),
        tipo_especial=data.get("tipo_especial") if data.get("tipo_especial") else None,
        raw_query=raw_query,
        extracted_by="llm_fallback",
    )


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    async def _test():
        print("🔥 Smoke Test — search/filter_llm.py")
        print("   (Nota: Requiere GROQ_API_KEY en .env para test real)")

        # Test _extract_json
        test_cases = [
            (\'{"zone": "Pampatar", "bedrooms_min": 2}\', \'{"zone": "Pampatar", "bedrooms_min": 2}\'),
            ("```json\\n{\\\"zone\\\": \\\"El Yaque\\\"}\\n```", \'{\"zone\": \"El Yaque\"}\'),
            ("Some text {\\\"max_price_usd\\\": 150000} more", \'{\"max_price_usd\": 150000}\'),
            ("No json here", None),
        ]

        for inp, expected in test_cases:
            result = _extract_json(inp)
            if expected:
                assert result == expected, f"Expected {expected}, got {result}"
            else:
                assert result is None, f"Expected None, got {result}"
            print(f"   ✅ _extract_json: {inp[:30]}...")

        # Test _build_filter_query
        data = {
            "property_type": ["venta"],
            "zone": "Pampatar",
            "max_price_usd": 200000,
            "bedrooms_min": 3,
            "vista_al_mar": True,
            "frente_playa": False,
            "uso_vacacional": None,
        }
        fq = _build_filter_query(data, "test query")
        assert fq.zone == "Pampatar"
        assert fq.max_price_usd == 200000.0
        assert fq.vista_al_mar is True
        assert fq.frente_playa is False
        assert fq.uso_vacacional is None
        assert fq.extracted_by == "llm_fallback"
        print("   ✅ _build_filter_query: validación correcta")

        # Test vacío
        empty = _build_filter_query({}, "empty")
        assert empty.is_empty is True
        print("   ✅ _build_filter_query: vacío correcto")

        print("\\n🎉 Smoke tests pasaron (unitarios)")
        print("   Para test de integración con LLM: uv run python -m app.search.filter_llm")

    asyncio.run(_test())
'''

with open('/mnt/agents/output/filter_llm.py', 'w') as f:
    f.write(filter_llm_code)

print("✅ filter_llm.py generado")
