# src/app/search/filter_extractor.py
"""Filter Extractor — Capa 1: Regex + Keywords (costo cero).

Extrae filtros estructurales del texto del usuario usando patrones regex
y keywords del dominio inmobiliario de Margarita.

Flujo:
  1. Normaliza query (lowercase + remoción de tildes)
  2. Aplica patrones regex por categoría (precio, zona, habitaciones, etc.)
  3. Busca keywords de zonas de Margarita y tipos de propiedad
  4. Evalúa flags booleanos con lógica positivo/negativo para evitar falsos positivos
  5. Retorna FilterQuery con extracted_by="regex"

Si no encuentra ningún filtro estructural, retorna FilterQuery vacío
(is_empty=True), lo que dispara el LLM fallback en hybrid.py.

✅ Combina:
   - Normalización robusta de texto (Módulo 2)
   - Parsing de precios con soporte para "k"/"mil" (Módulo 2)
   - Lógica booleana con contexto negativo (Módulo 1)
   - Autocontención + mantenibilidad (ambos)
"""

from __future__ import annotations

import re
from typing import Pattern

from src.app.schemas.search import FilterQuery


# ── Configuración de Zonas de Margarita ───────────────────────────
# Lista canónica con tildes originales para retorno
MARGARITA_ZONES_CANONICAL = [
    "pampatar", "porlamar", "el agua", "guacuco", "el yaque",
    "playa caribe", "playa parguito", "manzanillo",
    "casa de campo", "country club", "paraíso", "paraiso",
    "puerto real", "santa ana del norte",
    "sabana de guacuco", "rancho de chana", "cerro guayamuri",
    "las hernández", "las hernandez", "chana",
    "juan griego", "la asunción", "la asuncion",
    "margarita", "nueva esparta",
]

# Versión normalizada para matching (sin tildes, lowercase)
def _normalize_zone(z: str) -> str:
    return (
        z.lower()
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    )

MARGARITA_ZONES_NORMALIZED = [_normalize_zone(z) for z in MARGARITA_ZONES_CANONICAL]


# ── Tipos de Propiedad ────────────────────────────────────────────
PROPERTY_TYPES_CANONICAL = [
    "venta", "arriendo", "vacacional", "local", "comercial",
    "posada", "hotel", "planos", "terreno",
    "apartamento", "casa", "villa", "townhouse",
]

# Mapeo de sinónimos → tipo canónico
PROPERTY_TYPE_SYNONYMS: dict[str, str] = {
    "alquiler": "arriendo",
    "renta": "arriendo",
    "rent": "arriendo",
    "apto": "apartamento",
    "depto": "apartamento",
    "locales": "local",
    "oficina": "local",
    "shop": "local",
    "lote": "terreno",
    "parcela": "terreno",
    "land": "terreno",
    "pre-venta": "planos",
    "preconstruction": "planos",
    "off-plan": "planos",
}


# ── Patrones Regex para Precios ───────────────────────────────────
PRICE_PATTERNS: list[tuple[Pattern, str]] = [
    # Rango: "entre $100k y $200k", "between 100000 and 200000"
    (re.compile(r"entre\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?\s+(?:y|and|-)\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?", re.I), "range"),
    
    # Máximo: "hasta $200k", "max 200000", "menos de 150 mil"
    (re.compile(r"(?:hasta|máximo|maximo|max|menos de|under|up to|below)\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd|dólares)?", re.I), "max"),
    
    # Máximo postfix: "$200k máximo", "200000 o menos"
    (re.compile(r"[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?\s*(?:máximo|maximo|max|o menos|or less)", re.I), "max"),
    
    # Mínimo: "desde $150k", "mínimo 100000", "más de 50 mil"
    (re.compile(r"(?:desde|mínimo|minimo|min|más de|more than|over|above|from)\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd|dólares)?", re.I), "min"),
    
    # Mínimo postfix: "$150k mínimo", "100000 o más"
    (re.compile(r"[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?\s*(?:mínimo|minimo|min|o más|or more)", re.I), "min"),
    
    # Precio exacto suelto (fallback): "$200000", "200k usd"
    (re.compile(r"[\$]\s*([\d\.,]+)\s*(?:k|mil|usd|dólares)?\b", re.I), "exact"),
]

# ── Patrones para Habitaciones y Baños ───────────────────────────
ROOM_PATTERNS = {
    "bedrooms": [
        re.compile(r"(\d+)\s*(?:habitaciones?|hab\.?|cuartos?|rooms?|dormitorios?|recámaras?|recamaras?|h\b)", re.I),
        re.compile(r"(?:apartamento|casa|villa|apto)\s+(\d+)\s*(?:h|hab)", re.I),
    ],
    "bathrooms": [
        re.compile(r"(\d+)\s*(?:baños?|banos?|bañ\.?|baths?|b\s*\b)", re.I),
    ],
}

# ── Patrones para Área ───────────────────────────────────────────
AREA_PATTERNS = [
    re.compile(r"(\d+)\s*(?:m2|m²|metros?\s*cuadrados?|sq\s*m|mt2)", re.I),
    re.compile(r"(?:desde|mínimo|min)\s+(\d+)\s*(?:m2|metros)", re.I),
]


# ── Keywords para Flags Booleanos ────────────────────────────────
BOOLEAN_FLAGS: dict[str, dict[str, list[str]]] = {
    "vista_al_mar": {
        "positive": [
            "vista al mar", "vista del mar", "frente al mar", "vista al océano",
            "ocean view", "sea view", "vista panorámica", "panoramic view",
            "con vista", "vistas al mar", "mirando al mar",
        ],
        "negative": [
            "sin vista al mar", "no vista al mar", "vista interior", "interior view",
            "sin vistas", "no ocean view", "hacia la calle", "back view",
        ],
    },
    "frente_playa": {
        "positive": [
            "frente a la playa", "frente playa", "beachfront", "primera línea",
            "primera linea", "sobre la playa", "on the beach", "acceso directo a playa",
            "playa privada", "beach access", "a pie de playa",
        ],
        "negative": [
            "cerca de la playa", "a cuadras de la playa", "near beach", "walking distance",
            "zona playa", "sector playa", "no frente a playa",
        ],
    },
    "uso_vacacional": {
        "positive": [
            "vacacional", "vacation", "airbnb", "rental income", "inversión turística",
            "inversion turística", "rentabilidad", "roi", "para alquilar", "for rent",
            "alquiler temporal", "temporada", "turístico", "tourist rental",
            "ingresos por alquiler", "passive income",
        ],
        "negative": [
            "para vivir", "residencial", "residence", "para mi familia", "vivienda principal",
            "uso permanente", "long term", "no vacacional", "primary home",
        ],
    },
}


# ── Funciones Auxiliares ─────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Normaliza texto para matching: lowercase + remoción de tildes y caracteres especiales."""
    normalized = text.lower().strip()
    # Remover tildes y ñ
    normalized = (
        normalized
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ü", "u")
        .replace("ñ", "n")
    )
    # Remover signos de puntuación que puedan interferir
    normalized = re.sub(r'[^\w\s]', ' ', normalized)
    return re.sub(r'\s+', ' ', normalized).strip()


def _parse_price_number(match_str: str) -> float | None:
    """
    Convierte string de precio a número float.
    Soporta: "200000", "200.000", "200,000", "200k", "200 mil", "$200k usd"
    """
    if not match_str:
        return None
    
    original = match_str.strip().lower()
    
    # Detectar multiplicador: "k" o "mil" → ×1000
    multiplier = 1000 if any(k in original for k in ["k", "mil"]) else 1
    
    # Limpiar: quitar símbolos, letras y espacios
    clean = re.sub(r'[^\d,.]', '', original)
    
    # Normalizar separadores: "200.000" → "200000", "200,000" → "200.000"
    if "." in clean and "," in clean:
        # Formato europeo: 1.234,56 → quitar puntos, coma a punto
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean and clean.count(",") > 1:
        # Formato US con comas de miles: 200,000 → quitar comas
        clean = clean.replace(",", "")
    elif "." in clean and clean.count(".") > 1:
        # Múltiples puntos → asumir separador de miles
        clean = clean.replace(".", "")
    elif "," in clean:
        # Una sola coma → asumir decimal
        clean = clean.replace(",", ".")
    
    try:
        value = float(clean) * multiplier
        return value if value > 0 else None
    except (ValueError, TypeError):
        return None


def _extract_price(query_norm: str) -> tuple[float | None, float | None]:
    """Extrae min_price y max_price del query normalizado. Retorna (min, max)."""
    min_price: float | None = None
    max_price: float | None = None
    
    for pattern, ptype in PRICE_PATTERNS:
        matches = pattern.findall(query_norm)
        for match in matches:
            if ptype == "range" and isinstance(match, tuple) and len(match) == 2:
                val_min = _parse_price_number(match[0])
                val_max = _parse_price_number(match[1])
                if val_min:
                    min_price = val_min
                if val_max:
                    max_price = val_max
            else:
                match_str = match[0] if isinstance(match, tuple) else match
                val = _parse_price_number(match_str)
                if not val:
                    continue
                if ptype == "min":
                    min_price = val if min_price is None else min(min_price, val)
                elif ptype == "max":
                    max_price = val if max_price is None else max(max_price, val)
                elif ptype == "exact" and min_price is None and max_price is None:
                    # Fallback: si no hay contexto, asumir como máximo
                    max_price = val
    
    # Validar consistencia: min no puede ser > max
    if min_price and max_price and min_price > max_price:
        min_price, max_price = max_price, min_price
    
    return min_price, max_price


def _extract_rooms(query_norm: str) -> tuple[int | None, int | None]:
    """Extrae bedrooms y bathrooms mínimos."""
    bedrooms: int | None = None
    bathrooms: int | None = None
    
    for pattern in ROOM_PATTERNS["bedrooms"]:
        match = pattern.search(query_norm)
        if match:
            try:
                bedrooms = int(match.group(1))
                break
            except (ValueError, IndexError):
                continue
    
    for pattern in ROOM_PATTERNS["bathrooms"]:
        match = pattern.search(query_norm)
        if match:
            try:
                bathrooms = int(match.group(1))
                break
            except (ValueError, IndexError):
                continue
    
    return bedrooms, bathrooms


def _extract_area(query_norm: str) -> float | None:
    """Extrae área mínima en m²."""
    for pattern in AREA_PATTERNS:
        match = pattern.search(query_norm)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _extract_zone(query_norm: str) -> str | None:
    """Extrae zona de Margarita mencionada (substring matching)."""
    for i, zone_norm in enumerate(MARGARITA_ZONES_NORMALIZED):
        if zone_norm in query_norm:
            return MARGARITA_ZONES_CANONICAL[i]
    return None


def _extract_property_types(query_norm: str) -> list[str] | None:
    """Extrae tipos de propiedad mencionados, mapeando sinónimos a canónicos."""
    found: set[str] = set()
    
    # Primero buscar sinónimos explícitos
    for synonym, canonical in PROPERTY_TYPE_SYNONYMS.items():
        if synonym in query_norm:
            found.add(canonical)
    
    # Luego buscar tipos canónicos directos
    for ptype in PROPERTY_TYPES_CANONICAL:
        if ptype in query_norm:
            found.add(ptype)
    
    return sorted(list(found)) if found else None


def _extract_boolean_flags(query_norm: str) -> dict[str, bool | None]:
    """
    Extrae flags booleanos con lógica positivo/negativo para evitar falsos positivos.
    
    Reglas:
    - Si hay keyword positivo Y NO negativo → True
    - Si hay keyword negativo Y NO positivo → False  
    - Si hay ambos o ninguno → None (indeciso, delegar a LLM)
    """
    result: dict[str, bool | None] = {}
    
    for flag_name, keywords in BOOLEAN_FLAGS.items():
        positive = any(kw in query_norm for kw in keywords["positive"])
        negative = any(kw in query_norm for kw in keywords["negative"])
        
        if positive and not negative:
            result[flag_name] = True
        elif negative and not positive:
            result[flag_name] = False
        else:
            result[flag_name] = None  # Indeciso → fallback a LLM
    
    return result


# ── Función Pública Principal ─────────────────────────────────────

def extract_filters(query: str) -> FilterQuery:
    """
    Extrae filtros estructurados del texto del usuario usando regex + keywords.
    
    Args:
        query: Texto libre del usuario (ej: "apto 3 hab en Pampatar hasta $200k con vista al mar")
    
    Returns:
        FilterQuery con:
        - Filtros extraídos si se encontró al menos 1 criterio estructural
        - is_empty=True si no se extrajo nada → trigger para LLM fallback
        - extracted_by="regex" para trazabilidad
    """
    query_norm = _normalize_text(query)
    
    # Extracción por categoría
    min_price, max_price = _extract_price(query_norm)
    bedrooms, bathrooms = _extract_rooms(query_norm)
    area_min = _extract_area(query_norm)
    zone = _extract_zone(query_norm)
    property_types = _extract_property_types(query_norm)
    flags = _extract_boolean_flags(query_norm)
    
    # Construir resultado
    result = FilterQuery(
        property_type=property_types,
        zone=zone,
        min_price_usd=min_price,
        max_price_usd=max_price,
        bedrooms_min=bedrooms,
        bathrooms_min=bathrooms,
        area_min_m2=area_min,
        vista_al_mar=flags.get("vista_al_mar"),
        frente_playa=flags.get("frente_playa"),
        uso_vacacional=flags.get("uso_vacacional"),
        raw_query=query,
        extracted_by="regex",
    )
    
    return result


# ── Smoke Tests Integrales ────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — filter_extractor.py (Versión Híbrida)\n")
    
    test_cases = [
        # (query, expected_fields)
        (
            "busco apartamento de 3 habitaciones en Pampatar hasta $200k con vista al mar",
            {"zone": "pampatar", "bedrooms_min": 3, "max_price_usd": 200000.0, "vista_al_mar": True, "property_type": ["apartamento"]}
        ),
        (
            "casa en El Yaque de 2 baños desde 100 mil usd",
            {"zone": "el yaque", "bathrooms_min": 2, "min_price_usd": 100000.0, "property_type": ["casa"]}
        ),
        (
            "local comercial en Porlamar de 50m2 para invertir",
            {"zone": "porlamar", "property_type": ["local", "comercial"], "area_min_m2": 50.0, "uso_vacacional": True}
        ),
        (
            "apto entre $100.000 y $150.000 con 2 habitaciones",
            {"min_price_usd": 100000.0, "max_price_usd": 150000.0, "bedrooms_min": 2}
        ),
        (
            "posada frente a la playa en Guacuco para airbnb",
            {"zone": "guacuco", "property_type": ["posada"], "frente_playa": True, "uso_vacacional": True}
        ),
        (
            "quiero algo bonito cerca del mar pero SIN vista al mar",
            {"vista_al_mar": False}  # Contexto negativo debe prevalecer
        ),
        (
            "Busco un apto en alquiler temporal",
            {"property_type": ["apartamento", "arriendo"]}  # Sinónimos normalizados
        ),
        (
            "Hola, quiero información general",
            {}  # Vacío → is_empty=True
        ),
        (
            "Terreno en La Asunción desde $50k hasta $80.000",
            {"zone": "la asunción", "property_type": ["terreno"], "min_price_usd": 50000.0, "max_price_usd": 80000.0}
        ),
        (
            "Villa 4h 3b con piscina y vista panorámica en Playa Caribe",
            {"zone": "playa caribe", "property_type": ["villa"], "bedrooms_min": 4, "bathrooms_min": 3, "vista_al_mar": True}
        ),
    ]
    
    passed = 0
    failed = 0
    
    for i, (query, expected) in enumerate(test_cases, 1):
        print(f"🧪 Test #{i}: \"{query[:60]}{'...' if len(query) > 60 else ''}\"")
        result = extract_filters(query)
        
        # Validar campos esperados
        all_ok = True
        for key, expected_val in expected.items():
            actual_val = getattr(result, key, None)
            if actual_val != expected_val:
                print(f"   ❌ {key}: esperado {expected_val}, obtenido {actual_val}")
                all_ok = False
        
        # Validar is_empty para queries vacíos
        if not expected and not result.is_empty:
            print(f"   ❌ is_empty: esperado True, obtenido {result.is_empty}")
            all_ok = False
        
        if all_ok:
            print(f"   ✅ OK | Zone: {result.zone}, Price: ${result.min_price_usd}-${result.max_price_usd}, Beds: {result.bedrooms_min}")
            passed += 1
        else:
            failed += 1
        print()
    
    # Resumen final
    print(f"📊 Resultados: {passed} passed, {failed} failed de {len(test_cases)} tests")
    if failed == 0:
        print("🎉 ¡Todos los smoke tests pasaron! ✅")
    else:
        print("⚠️ Revisar fallos antes de deploy")
        exit(1)