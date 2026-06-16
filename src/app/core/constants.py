# src/app/core/constants.py
"""Enums y constantes del dominio inmobiliario.

Todos los estados, tipos y etapas del sistema centralizados aquí.
"""

from enum import Enum


class Language(str, Enum):
    """Idiomas soportados por el chatbot."""
    ES = "es"
    EN = "en"


class PropertyType(str, Enum):
    """Tipos de propiedad activos en el mercado de Margarita."""
    VENTA = "venta"
    ARRIENDO = "arriendo"
    VACACIONAL = "vacacional"
    LOCAL = "local"
    POSADA = "posada"
    HOTEL = "hotel"
    PLANOS = "planos"
    TERRENO = "terreno"


class PropertyStatus(str, Enum):
    """Estados de disponibilidad de una propiedad."""
    DISPONIBLE = "disponible"
    RESERVADA = "reservada"
    VENDIDA = "vendida"


class LeadStatus(str, Enum):
    """Estados del lead en el pipeline de ventas."""
    PENDIENTE = "pendiente"
    CONFIRMADO = "confirmado"
    CANCELADO = "cancelado"


class SearchSource(str, Enum):
    """Fuente del resultado de búsqueda."""
    SQL = "sql"
    VEC = "vec"
    HYBRID = "hybrid"
    FALLBACK = "fallback"
    VEC_UNAVAILABLE = "vec_unavailable"
    VEC_ERROR = "vec_error"
    NO_RESULTS = "no_results"
    LLM_BLOCKED = "llm_blocked"


class IngestionStatus(str, Enum):
    """Estados del proceso de ingestion de CSV."""
    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"

# ── Zonas de Margarita (para referencia en signals.py) ───────────

MARGARITA_ZONES = {
    "premium": [
        "pampatar", "paraíso", "paraiso", "casa de campo",
        "country club", "puerto real", "santa ana del norte",
    ],
    "beach": [
        "playa el agua", "el agua", "guacuco", "playa caribe",
        "playa parguito", "manzanillo",
    ],
    "sports": [
        "el yaque", "yaque",
    ],
    "exclusive_rural": [
        "sabana de guacuco", "rancho de chana", "cerro guayamuri",
        "las hernández", "chana",
    ],
    "commercial": [
        "porlamar", "av bolívar", "av 4 de mayo",
        "la asunción", "juan griego",
    ],
    "general": [
        "margarita", "nueva esparta", "isla", "perla del caribe",
    ],
}
