# src/app/search/filter_extractor.py
"""Filter Extractor — regex + keywords → FilterQuery.

Capa 1 del Hybrid Search Engine.
Extrae filtros estructurados de texto libre usando patrones regex y keywords
predefinidos. Costo CERO (sin LLM).

Flujo:
  1. Normaliza query (lowercase, quita tildes opcional)
  2. Aplica patrones regex por categoría (precio, zona, habitaciones, etc.)
  3. Busca keywords de zonas de Margarita y tipos de propiedad
  4. Retorna FilterQuery con extracted_by="regex"

Si no encuentra ningún filtro estructural, retorna FilterQuery vacío
(is_empty=True), lo que dispara el LLM fallback en hybrid.py.
"""

from __future__ import annotations

import re
from typing import Any

from app.schemas.search import FilterQuery


# ── Zonas de Margarita (keywords para matching) ───────────────────

MARGARITA_ZONES = [
    "pampatar", "porlamar", "el agua", "guacuco", "el yaque",
    "playa caribe", "playa parguito", "manzanillo",
    "casa de campo", "country club", "paraíso", "paraiso",
    "puerto real", "santa ana del norte",
    "sabana de guacuco", "rancho de chana", "cerro guayamuri",
    "las hernández", "las hernandez", "chana",
    "juan griego", "la asunción", "la asuncion",
    "margarita", "nueva esparta",
]

# Normalizadas para matching (sin tildes, lowercase)
MARGARITA_ZONES_NORMALIZED = [
    z.lower()
    .replace("á", "a").replace("é", "e").replace("í", "i")
    .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    for z in MARGARITA_ZONES
]


# ── Tipos de propiedad ────────────────────────────────────────────

PROPERTY_TYPES = [
    "venta", "arriendo", "alquiler", "vacacional",
    "local", "locales", "comercial",
    "posada", "hotel", "planos", "terreno",
    "apartamento", "apto", "casa", "villa", "townhouse",
]

# Mapeo sinónimos → tipo canónico
PROPERTY_TYPE_SYNONYMS: dict[str, str] = {
    "alquiler": "arriendo",
    "apto": "apartamento",
    "locales": "local",
}


# ── Patrones Regex ────────────────────────────────────────────────

PRICE_PATTERNS = [
    # "$200k", "$200,000", "200000 usd"
    (r"(?:hasta|max|máximo|menos de|under|up to)\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd|dólares)?", "max"),
    # "desde $150k", "mínimo 100000"
    (r"(?:desde|min|mínimo|más de|over|from)\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd|dólares)?", "min"),
    # "entre $100k y $200k"
    (r"entre\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?\s*y\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?", "range"),
    # "$150.000" o "150000" suelto (interpretar como max si no hay contexto)
    (r"[\$]\s*([\d\.,]+)\s*(?:k|mil|usd|dólares)?", "exact"),
]

BEDROOM_PATTERNS = [
    (r"(\d+)\s*(?:hab|habitaciones|h|cuartos|rooms)", "exact"),
    (r"(?:de|con)\s*(\d+)\s*(?:hab|habitaciones|h)", "exact"),
]

BATHROOM_PATTERNS = [
    (r"(\d+)\s*(?:baños|banos|b|baths)", "exact"),
]

AREA_PATTERNS = [
    (r"(\d+)\s*(?:m2|m²|metros|mt2)", "exact"),
]


# ── Keywords booleanas ────────────────────────────────────────────

VISTA_AL_MAR_KEYWORDS = [
    "vista al mar", "vista del mar", "frente al mar", "frente a la playa",
    "beachfront", "ocean view", "sea view", "con vista", "vista panorámica",
]

FRENTE_PLAYA_KEYWORDS = [
    "frente playa", "primera linea", "primera línea", "sobre la playa",
    "beachfront", "right on the beach",
]

USO_VACACIONAL_KEYWORDS = [
    "vacacional", "vacation", "airbnb", "rental", "inversión", "inversion",
    "invertir", "rentabilidad", "roi", "alquiler temporal", "temporada",
]


# ── Funciones de extracción ───────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Normaliza texto para matching: lowercase, sin tildes."""
    return (
        text.lower()
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    )


def _parse_price_number(match_str: str) -> float | None:
    """Convierte string de precio a número float."""
    if not match_str:
        return None
    
    # Quitar símbolos y espacios
    clean = match_str.replace("$", "").replace(",", "").replace(".", "").strip()
    
    # Detectar "k" o "mil" → multiplicar por 1000
    multiplier = 1
    if "k" in match_str.lower() or "mil" in match_str.lower():
        multiplier = 1000
        clean = clean.replace("k", "").replace("mil", "")
    
    try:
        value = float(clean) * multiplier
        return value
    except ValueError:
        return None


def _extract_price(query_norm: str) -> tuple[float | None, float | None]:
    """Extrae min_price y max_price del query. Retorna (min, max)."""
    min_price: float | None = None
    max_price: float | None = None
    
    for pattern, ptype in PRICE_PATTERNS:
        matches = re.findall(pattern, query_norm)
        for match in matches:
            if ptype == "min":
                val = _parse_price_number(match[0] if isinstance(match, tuple) else match)
                if val:
                    min_price = val
            elif ptype == "max":
                val = _parse_price_number(match[0] if isinstance(match, tuple) else match)
                if val:
                    max_price = val
            elif ptype == "range":
                # match es tuple (min, max)
                if isinstance(match, tuple) and len(match) == 2:
                    val_min = _parse_price_number(match[0])
                    val_max = _parse_price_number(match[1])
                    if val_min:
                        min_price = val_min
                    if val_max:
                        max_price = val_max
            elif ptype == "exact":
                val = _parse_price_number(match[0] if isinstance(match, tuple) else match)
                # Si no hay contexto min/max, asumir max
                if val and max_price is None:
                    max_price = val
    
    return min_price, max_price


def _extract_bedrooms(query_norm: str) -> int | None:
    """Extrae número mínimo de habitaciones."""
    for pattern, _ in BEDROOM_PATTERNS:
        match = re.search(pattern, query_norm)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _extract_bathrooms(query_norm: str) -> int | None:
    """Extrae número mínimo de baños."""
    for pattern, _ in BATHROOM_PATTERNS:
        match = re.search(pattern, query_norm)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _extract_area(query_norm: str) -> float | None:
    """Extrae área mínima en m²."""
    for pattern, _ in AREA_PATTERNS:
        match = re.search(pattern, query_norm)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _extract_zone(query_norm: str) -> str | None:
    """Extrae zona de Margarita mencionada."""
    for zone in MARGARITA_ZONES_NORMALIZED:
        if zone in query_norm:
            # Retornar versión canónica (con tildes originales)
            idx = MARGARITA_ZONES_NORMALIZED.index(zone)
            return MARGARITA_ZONES[idx].lower()
    return None


def _extract_property_types(query_norm: str) -> list[str] | None:
    """Extrae tipos de propiedad mencionados."""
    found: set[str] = set()
    
    for ptype in PROPERTY_TYPES:
        if ptype in query_norm:
            # Mapear sinónimo → canónico
            canonical = PROPERTY_TYPE_SYNONYMS.get(ptype, ptype)
            found.add(canonical)
    
    return list(found) if found else None


def _extract_boolean(query_norm: str, keywords: list[str]) -> bool | None:
    """Extrae flag booleano si algún keyword aparece en el query."""
    for kw in keywords:
        if kw in query_norm:
            return True
    return None


# ── Función pública ───────────────────────────────────────────────

def extract_filters_regex(query: str) -> FilterQuery:
    """Extrae filtros estructurados usando regex + keywords.
    
    Args:
        query: Texto libre del usuario.
    
    Returns:
        FilterQuery con extracted_by="regex". Si no encuentra filtros,
        retorna FilterQuery vacío (is_empty=True).
    """
    query_norm = _normalize_text(query)
    
    min_price, max_price = _extract_price(query_norm)
    bedrooms = _extract_bedrooms(query_norm)
    bathrooms = _extract_bathrooms(query_norm)
    area = _extract_area(query_norm)
    zone = _extract_zone(query_norm)
    property_types = _extract_property_types(query_norm)
    
    vista_al_mar = _extract_boolean(query_norm, VISTA_AL_MAR_KEYWORDS)
    frente_playa = _extract_boolean(query_norm, FRENTE_PLAYA_KEYWORDS)
    uso_vacacional = _extract_boolean(query_norm, USO_VACACIONAL_KEYWORDS)
    
    return FilterQuery(
        property_type=property_types,
        zone=zone,
        min_price_usd=min_price,
        max_price_usd=max_price,
        bedrooms_min=bedrooms,
        bathrooms_min=bathrooms,
        area_min_m2=area,
        vista_al_mar=vista_al_mar,
        frente_playa=frente_playa,
        uso_vacacional=uso_vacacional,
        raw_query=query,
        extracted_by="regex",
    )


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — filter_extractor.py")
    
    # Test 1: Precio máximo
    fq = extract_filters_regex("Busco apto hasta $200k en Pampatar")
    assert fq.max_price_usd == 200000.0
    assert fq.zone == "pampatar"
    print("  ✅ Precio + zona extraídos")
    
    # Test 2: Habitaciones + vista al mar
    fq = extract_filters_regex("Casa 4 habitaciones con vista al mar en El Yaque")
    assert fq.bedrooms_min == 4
    assert fq.vista_al_mar is True
    assert fq.zone == "el yaque"
    print("  ✅ Habitaciones + vista al mar + zona")
    
    # Test 3: Tipo de propiedad + uso vacacional
    fq = extract_filters_regex("Quiero una posada para invertir en Guacuco")
    assert "posada" in (fq.property_type or [])
    assert fq.uso_vacacional is True
    assert fq.zone == "guacuco"
    print("  ✅ Tipo propiedad + uso vacacional + zona")
    
    # Test 4: Rango de precios
    fq = extract_filters_regex("Apartamentos entre $100000 y $150000")
    assert fq.min_price_usd == 100000.0
    assert fq.max_price_usd == 150000.0
    print("  ✅ Rango de precios")
    
    # Test 5: Query vacío → is_empty
    fq = extract_filters_regex("Hola, quiero información")
    assert fq.is_empty is True
    assert fq.extracted_by == "regex"
    print("  ✅ Query sin filtros → is_empty=True")
    
    # Test 6: Sinónimos
    fq = extract_filters_regex("Busco un apto en alquiler temporal")
    assert "apartamento" in (fq.property_type or [])
    assert "arriendo" in (fq.property_type or [])
    print("  ✅ Sinónimos normalizados")
    
    print("\n🎉 Todos los smoke tests pasaron")