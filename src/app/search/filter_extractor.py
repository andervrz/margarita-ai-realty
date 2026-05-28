# src/app/search/filter_extractor.py
"""Filter Extractor — Capa 1: Regex + Keywords (costo cero).

Extrae filtros estructurales del texto del usuario usando patrones regex
y keywords del dominio inmobiliario de Margarita.

Flujo:
  1. Normaliza query (lowercase + remoción de tildes)
  2. Aplica patrones regex por categoría (precio, zona, habitaciones, etc.)
  3. Busca keywords de zonas de Margarita y tipos de propiedad
  4. Evalúa flags booleanos con lógica positivo/negativo
  5. Retorna FilterQuery con extracted_by="regex"

Si no encuentra filtros estructurales, retorna FilterQuery vacío
(is_empty=True), lo que dispara el LLM fallback en hybrid.py.
"""

from __future__ import annotations

import re

from src.app.schemas.search import FilterQuery


# ── Zonas de Margarita ────────────────────────────────────────────

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


def _normalize_zone(z: str) -> str:
    return (
        z.lower()
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    )


MARGARITA_ZONES_NORMALIZED = [_normalize_zone(z) for z in MARGARITA_ZONES_CANONICAL]


# ── Tipos de Propiedad ────────────────────────────────────────────

PROPERTY_TYPES_CANONICAL = [
    "venta", "arriendo", "vacacional", "local",
    "posada", "hotel", "planos", "terreno",
    "apartamento", "casa", "villa", "townhouse",
]

PROPERTY_TYPE_SYNONYMS: dict[str, str] = {
    "alquiler": "arriendo",
    "renta": "arriendo",
    "rent": "arriendo",
    "apto": "apartamento",
    "depto": "apartamento",
    "comercial": "local",
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


# ── Patrones de Precio ────────────────────────────────────────────

PRICE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"entre\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?\s+(?:y|and|-)\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?", re.I), "range"),
    (re.compile(r"(?:hasta|máximo|maximo|max|menos de|under|up to|below)\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd|dólares)?", re.I), "max"),
    (re.compile(r"[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?\s*(?:máximo|maximo|max|o menos|or less)", re.I), "max"),
    (re.compile(r"(?:desde|mínimo|minimo|min|más de|more than|over|above|from)\s*[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd|dólares)?", re.I), "min"),
    (re.compile(r"[\$]?\s*([\d\.,]+)\s*(?:k|mil|usd)?\s*(?:mínimo|minimo|min|o más|or more)", re.I), "min"),
    (re.compile(r"[\$]\s*([\d\.,]+)\s*(?:k|mil|usd|dólares)?\b", re.I), "exact"),
]

ROOM_PATTERNS = {
    "bedrooms": [
        re.compile(r"(\d+)\s*(?:habitaciones?|hab\.?|cuartos?|rooms?|dormitorios?|recámaras?|recamaras?|h\b)", re.I),
        re.compile(r"(?:apartamento|casa|villa|apto)\s+(\d+)\s*(?:h|hab)", re.I),
    ],
    "bathrooms": [
        re.compile(r"(\d+)\s*(?:baños?|banos?|bañ\.?|baths?|b\s*\b)", re.I),
    ],
}

AREA_PATTERNS = [
    re.compile(r"(\d+)\s*(?:m2|m²|metros?\s*cuadrados?|sq\s*m|mt2)", re.I),
    re.compile(r"(?:desde|mínimo|min)\s+(\d+)\s*(?:m2|metros)", re.I),
]


# ── Keywords Booleanos — normalizados al inicio del módulo ────────

BOOLEAN_FLAGS_RAW: dict[str, dict[str, list[str]]] = {
    "vista_al_mar": {
        "positive": [
            "vista al mar", "vista del mar", "frente al mar", "vista al oceano",
            "ocean view", "sea view", "vista panoramica", "panoramic view",
            "con vista", "vistas al mar", "mirando al mar",
        ],
        "negative": [
            "sin vista al mar", "no vista al mar", "vista interior", "interior view",
            "sin vistas", "no ocean view", "hacia la calle", "back view",
        ],
    },
    "frente_playa": {
        "positive": [
            "frente a la playa", "frente playa", "beachfront", "primera linea",
            "sobre la playa", "on the beach", "acceso directo a playa",
            "playa privada", "beach access", "a pie de playa",
        ],
        "negative": [
            "cerca de la playa", "a cuadras de la playa", "near beach", "walking distance",
            "zona playa", "sector playa", "no frente a playa",
        ],
    },
    "uso_vacacional": {
        "positive": [
            "vacacional", "vacation", "airbnb", "rental income", "inversion turistica",
            "rentabilidad", "roi", "para alquilar", "for rent",
            "alquiler temporal", "temporada", "turistico", "tourist rental",
            "ingresos por alquiler", "passive income",
        ],
        "negative": [
            "para vivir", "residencial", "residence", "para mi familia", "vivienda principal",
            "uso permanente", "long term", "no vacacional", "primary home",
        ],
    },
}


def _normalize_keyword(kw: str) -> str:
    return (
        kw.lower()
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    )


# Normalizar todos los keywords al inicio — evita re-normalizar en cada llamada
BOOLEAN_FLAGS_NORMALIZED: dict[str, dict[str, list[str]]] = {
    flag: {
        "positive": [_normalize_keyword(kw) for kw in data["positive"]],
        "negative": [_normalize_keyword(kw) for kw in data["negative"]],
    }
    for flag, data in BOOLEAN_FLAGS_RAW.items()
}


# ── Funciones Auxiliares ──────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Normaliza texto para matching: lowercase + sin tildes + sin puntuación."""
    normalized = text.lower().strip()
    normalized = (
        normalized
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ü", "u")
        .replace("ñ", "n")
    )
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _parse_price_number(match_str: str) -> float | None:
    """Convierte string de precio a float. Soporta formatos venezolanos y US."""
    if not match_str:
        return None

    original = match_str.strip().lower()
    has_multiplier = any(k in original for k in ["k", "mil"])
    multiplier = 1000 if has_multiplier else 1

    clean = re.sub(r"[^\d,.]", "", original)
    if not clean:
        return None

    has_dot = "." in clean
    has_comma = "," in clean

    if has_dot and has_comma:
        last_dot = clean.rfind(".")
        last_comma = clean.rfind(",")
        if last_comma > last_dot:
            clean = clean.replace(".", "").replace(",", ".")
        else:
            clean = clean.replace(",", "")
    elif has_comma and not has_dot:
        parts = clean.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            clean = clean.replace(",", ".")
        else:
            clean = clean.replace(",", "")
    elif has_dot and not has_comma:
        parts = clean.split(".")
        if len(parts) == 2 and len(parts[1]) == 3:
            clean = clean.replace(".", "")

    try:
        value = float(clean) * multiplier
        return value if value > 0 else None
    except (ValueError, TypeError):
        return None


def _extract_price(query_original: str) -> tuple[float | None, float | None]:
    """
    Extrae min_price y max_price del query ORIGINAL (sin normalizar).
    Usa el query original para preservar $, puntos y comas de precios venezolanos.
    """
    min_price: float | None = None
    max_price: float | None = None

    for pattern, ptype in PRICE_PATTERNS:
        matches = pattern.findall(query_original)
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
                    max_price = val

    if min_price and max_price and min_price > max_price:
        min_price, max_price = max_price, min_price

    return min_price, max_price


def _extract_rooms(query_norm: str) -> tuple[int | None, int | None]:
    """Extrae bedrooms y bathrooms mínimos del query normalizado."""
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
    """Extrae zona de Margarita (substring matching en texto normalizado)."""
    for i, zone_norm in enumerate(MARGARITA_ZONES_NORMALIZED):
        if zone_norm in query_norm:
            return MARGARITA_ZONES_CANONICAL[i]
    return None


def _extract_property_types(query_norm: str) -> list[str] | None:
    """Extrae tipos de propiedad, mapeando sinónimos a canónicos."""
    found: set[str] = set()

    for synonym, canonical in PROPERTY_TYPE_SYNONYMS.items():
        if synonym in query_norm:
            found.add(canonical)

    for ptype in PROPERTY_TYPES_CANONICAL:
        if ptype in query_norm:
            found.add(ptype)

    return sorted(list(found)) if found else None


def _extract_boolean_flags(query_norm: str) -> dict[str, bool | None]:
    """
    Extrae flags booleanos con lógica positivo/negativo.

    Reglas:
    - keyword positivo Y NO negativo → True
    - keyword negativo Y NO positivo → False
    - ambos o ninguno → None (delegar a LLM)
    """
    result: dict[str, bool | None] = {}

    for flag_name, keywords in BOOLEAN_FLAGS_NORMALIZED.items():
        positive = any(kw in query_norm for kw in keywords["positive"])
        negative = any(kw in query_norm for kw in keywords["negative"])

        if positive and not negative:
            result[flag_name] = True
        elif negative and not positive:
            result[flag_name] = False
        else:
            result[flag_name] = None

    return result


# ── Función Pública Principal ─────────────────────────────────────

def extract_filters(query: str) -> FilterQuery:
    """
    Extrae filtros estructurados del texto del usuario.

    Args:
        query: Texto libre del usuario.

    Returns:
        FilterQuery con filtros extraídos.
        is_empty=True si no se extrajo nada → trigger para LLM fallback.
    """
    query_norm = _normalize_text(query)

    # Precios desde query ORIGINAL — preserva $, puntos, comas
    min_price, max_price = _extract_price(query)

    # Todo lo demás desde query normalizado
    bedrooms, bathrooms = _extract_rooms(query_norm)
    area_min = _extract_area(query_norm)
    zone = _extract_zone(query_norm)
    property_types = _extract_property_types(query_norm)
    flags = _extract_boolean_flags(query_norm)

    return FilterQuery(
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


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — filter_extractor.py\n")

    test_cases = [
        (
            "busco apartamento de 3 habitaciones en Pampatar hasta $200k con vista al mar",
            {"zone": "pampatar", "bedrooms_min": 3, "max_price_usd": 200000.0,
             "vista_al_mar": True, "property_type": ["apartamento"]},
        ),
        (
            "casa en El Yaque de 2 baños desde 100 mil usd",
            {"zone": "el yaque", "bathrooms_min": 2, "min_price_usd": 100000.0,
             "property_type": ["casa"]},
        ),
        (
            "apto entre $100.000 y $150.000 con 2 habitaciones",
            {"min_price_usd": 100000.0, "max_price_usd": 150000.0, "bedrooms_min": 2},
        ),
        (
            "posada frente a la playa en Guacuco para airbnb",
            {"zone": "guacuco", "property_type": ["posada"],
             "frente_playa": True, "uso_vacacional": True},
        ),
        (
            "quiero algo bonito cerca del mar pero SIN vista al mar",
            {"vista_al_mar": False},
        ),
        (
            "Hola, quiero información general",
            {},
        ),
        (
            "Terreno en La Asunción desde $50k hasta $80.000",
            {"zone": "la asunción", "property_type": ["terreno"],
             "min_price_usd": 50000.0, "max_price_usd": 80000.0},
        ),
    ]

    passed = 0
    failed = 0

    for i, (query, expected) in enumerate(test_cases, 1):
        print(f"🧪 Test #{i}: \"{query[:60]}{'...' if len(query) > 60 else ''}\"")
        result = extract_filters(query)

        all_ok = True
        for key, expected_val in expected.items():
            actual_val = getattr(result, key, None)
            if actual_val != expected_val:
                print(f"   ❌ {key}: esperado {expected_val}, obtenido {actual_val}")
                all_ok = False

        if not expected and not result.is_empty:
            print(f"   ❌ is_empty: esperado True, obtenido {result.is_empty}")
            all_ok = False

        if all_ok:
            print(f"   ✅ OK | Zone: {result.zone}, Price: {result.min_price_usd}-{result.max_price_usd}, Beds: {result.bedrooms_min}")
            passed += 1
        else:
            failed += 1
        print()

    print(f"📊 Resultados: {passed} passed, {failed} failed de {len(test_cases)} tests")
    if failed == 0:
        print("🎉 Todos los smoke tests pasaron ✅")
    else:
        print("⚠️ Revisar fallos antes de continuar")
        exit(1)
