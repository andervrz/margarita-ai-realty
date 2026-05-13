
# Generando filter_extractor.py
filter_extractor_code = """Filter Extractor — Capa 1: Regex + Keywords (costo cero).

Extrae filtros estructurales del texto del usuario usando patrones regex
y keywords del dominio inmobiliario de Margarita.

Si encuentra al menos 1 filtro estructural, retorna inmediatamente (sin LLM).
Si todos los filtros están vacíos, retorna FilterQuery vacío para trigger de LLM fallback.
"""

import re
from typing import Pattern

from src.app.core.constants import MARGARITA_ZONES
from src.app.schemas.search import FilterQuery


# ── Patrones de precios ───────────────────────────────────────────
PRICE_PATTERNS = {
    "max_price": [
        re.compile(r"(?:hasta|máximo|maximo|max|menos de|under|up to|below)\\s+\\$?([\\d.,]+)", re.I),
        re.compile(r"\\$?([\\d.,]+)\\s*(?:máximo|maximo|max|o menos|or less)", re.I),
    ],
    "min_price": [
        re.compile(r"(?:desde|mínimo|minimo|min|más de|more than|over|above|from)\\s+\\$?([\\d.,]+)", re.I),
        re.compile(r"\\$?([\\d.,]+)\\s*(?:mínimo|minimo|min|o más|or more)", re.I),
    ],
    "range_price": [
        re.compile(r"(?:entre|between)\\s+\\$?([\\d.,]+)\\s+(?:y|and|to|-)\\s+\\$?([\\d.,]+)", re.I),
    ],
}

# ── Patrones de habitaciones/baños ────────────────────────────────
ROOM_PATTERNS = {
    "bedrooms": [
        re.compile(r"(\\d+)\\s*(?:habitaciones?|hab\\.?|cuartos?|rooms?|dormitorios?|recámaras?|recamaras?)", re.I),
        re.compile(r"(?:apartamento|casa|villa)\\s+(\\d+)\\s*(?:h|hab)", re.I),
    ],
    "bathrooms": [
        re.compile(r"(\\d+)\\s*(?:baños?|banos?|bañ\\.?|baths?)", re.I),
    ],
}

# ── Patrones de área ──────────────────────────────────────────────
AREA_PATTERNS = [
    re.compile(r"(\\d+)\\s*(?:m2|metros?\\s*cuadrados?|sq\\s*m)", re.I),
    re.compile(r"(?:desde|mínimo|min)\\s+(\\d+)\\s*(?:m2|metros)", re.I),
]

# ── Keywords de tipo de propiedad ─────────────────────────────────
PROPERTY_TYPE_KEYWORDS = {
    "venta": ["venta", "comprar", "compra", "adquirir", "buy", "purchase", "for sale"],
    "arriendo": ["arriendo", "arrendar", "alquiler", "alquilar", "rent", "rental", "lease"],
    "vacacional": ["vacacional", "vacation", "temporada", "seasonal", "airbnb"],
    "local": ["local", "comercial", "oficina", "office", "commercial", "shop", "store"],
    "posada": ["posada", "hostel", "guesthouse", "pensión", "pension"],
    "hotel": ["hotel", "boutique hotel"],
    "planos": ["planos", "proyecto", "en construcción", "pre-venta", "preconstruction", "off-plan"],
    "terreno": ["terreno", "lote", "parcela", "land", "plot"],
}

# ── Keywords booleanas (flags) ────────────────────────────────────
BOOLEAN_FLAGS = {
    "vista_al_mar": {
        "positive": [
            "vista al mar", "ocean view", "sea view", "vista al océano",
            "frente al mar", "beachfront view", "panorámica", "panoramic",
        ],
        "negative": [
            "sin vista al mar", "no ocean view", "interior", "inside",
        ],
    },
    "frente_playa": {
        "positive": [
            "frente a la playa", "beachfront", "primera línea", "first line",
            "sobre la playa", "on the beach", "frente playa",
        ],
        "negative": [
            "a dos cuadras de la playa", "cerca de la playa", "near beach",
        ],
    },
    "uso_vacacional": {
        "positive": [
            "vacacional", "vacation", "airbnb", "rental income", "inversión turística",
            "turístico", "tourist", "para alquilar", "for rent",
        ],
        "negative": [
            "para vivir", "residencial", "residence", "para mi familia",
        ],
    },
}

# ── Zonas de Margarita (flattened para búsqueda) ──────────────────
ALL_ZONES = []
for category, zones in MARGARITA_ZONES.items():
    ALL_ZONES.extend([(z.lower(), category) for z in zones])


def _extract_price(text: str) -> tuple[float | None, float | None]:
    """Extrae min_price y max_price del texto."""
    min_price = None
    max_price = None

    # Rango: entre X y Y
    for pattern in PRICE_PATTERNS["range_price"]:
        match = pattern.search(text)
        if match:
            try:
                p1 = float(match.group(1).replace(".", "").replace(",", "."))
                p2 = float(match.group(2).replace(".", "").replace(",", "."))
                return min(p1, p2), max(p1, p2)
            except ValueError:
                continue

    # Máximo
    for pattern in PRICE_PATTERNS["max_price"]:
        match = pattern.search(text)
        if match:
            try:
                price_str = match.group(1).replace(".", "").replace(",", ".")
                max_price = float(price_str)
                break
            except ValueError:
                continue

    # Mínimo
    for pattern in PRICE_PATTERNS["min_price"]:
        match = pattern.search(text)
        if match:
            try:
                price_str = match.group(1).replace(".", "").replace(",", ".")
                min_price = float(price_str)
                break
            except ValueError:
                continue

    return min_price, max_price


def _extract_rooms(text: str) -> tuple[int | None, int | None]:
    """Extrae bedrooms y bathrooms."""
    bedrooms = None
    bathrooms = None

    for pattern in ROOM_PATTERNS["bedrooms"]:
        match = pattern.search(text)
        if match:
            bedrooms = int(match.group(1))
            break

    for pattern in ROOM_PATTERNS["bathrooms"]:
        match = pattern.search(text)
        if match:
            bathrooms = int(match.group(1))
            break

    return bedrooms, bathrooms


def _extract_area(text: str) -> float | None:
    """Extrae área mínima en m2."""
    for pattern in AREA_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _extract_property_types(text: str) -> list[str] | None:
    """Extrae tipos de propiedad mencionados."""
    text_lower = text.lower()
    found = []
    for prop_type, keywords in PROPERTY_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                found.append(prop_type)
                break
    return found if found else None


def _extract_zone(text: str) -> str | None:
    """Extrae zona de Margarita mencionada."""
    text_lower = text.lower()
    for zone_name, category in ALL_ZONES:
        if zone_name in text_lower:
            return zone_name
    return None


def _extract_boolean_flags(text: str) -> dict[str, bool]:
    """Extrae flags booleanos (vista_al_mar, frente_playa, uso_vacacional)."""
    text_lower = text.lower()
    result = {}

    for flag_name, keywords in BOOLEAN_FLAGS.items():
        positive = any(kw.lower() in text_lower for kw in keywords["positive"])
        negative = any(kw.lower() in text_lower for kw in keywords["negative"])

        if positive and not negative:
            result[flag_name] = True
        elif negative and not positive:
            result[flag_name] = False
        # Si ambos o ninguno → no se infiere (None)

    return result


def extract_filters(text: str) -> FilterQuery:
    """Extrae filtros estructurales del texto del usuario.

    Returns:
        FilterQuery con filtros encontrados. Si is_empty=True,
        el orquestador invoca LLM fallback.
    """
    min_price, max_price = _extract_price(text)
    bedrooms, bathrooms = _extract_rooms(text)
    area_min = _extract_area(text)
    property_types = _extract_property_types(text)
    zone = _extract_zone(text)
    flags = _extract_boolean_flags(text)

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
        raw_query=text,
        extracted_by="regex",
    )


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — search/filter_extractor.py")

    tests = [
        (
            "busco apartamento de 3 habitaciones en Pampatar hasta $200000 con vista al mar",
            {"zone": "pampatar", "bedrooms_min": 3, "max_price_usd": 200000.0, "vista_al_mar": True},
        ),
        (
            "casa en El Yaque de 2 baños desde 100000",
            {"zone": "el yaque", "bathrooms_min": 2, "min_price_usd": 100000.0},
        ),
        (
            "algo bonito cerca del mar",
            {"vista_al_mar": True},  # "cerca del mar" no matchea, pero "mar" sí en positive
        ),
        (
            "local comercial en Porlamar de 50m2",
            {"zone": "porlamar", "property_type": ["local"], "area_min_m2": 50.0},
        ),
        (
            "quiero ver opciones",
            {},  # Vacío → trigger LLM fallback
        ),
    ]

    for query, expected in tests:
        result = extract_filters(query)
        print(f"\\n📝 Query: {query}")
        print(f"   Zone: {result.zone}, Beds: {result.bedrooms_min}, Price max: {result.max_price_usd}")
        print(f"   Vista al mar: {result.vista_al_mar}, Type: {result.property_type}")
        print(f"   is_empty: {result.is_empty}")

        for key, val in expected.items():
            actual = getattr(result, key)
            assert actual == val, f"Expected {key}={val}, got {actual}"
        print("   ✅ OK")

    print("\\n🎉 Todos los smoke tests pasaron")
