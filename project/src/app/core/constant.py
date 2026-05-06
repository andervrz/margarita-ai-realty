# src/app/core/constants.py
"""Enums y constantes del dominio inmobiliario.

Todos los estados, tipos y etapas del sistema centralizados aquí.
"""

from enum import Enum


class Plan(str, Enum):
    """Planes de suscripción disponibles."""
    STARTER = "starter"
    STANDARD = "standard"
    PRO = "pro"


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


class QualificationStage(str, Enum):
    """Etapa del lead qualification engine."""
    EXPLORE = "explore"
    QUALIFY = "qualify"
    BOOK = "book"


class BookingStep(str, Enum):
    """Pasos secuenciales del flujo de agendamiento."""
    NAME = "name"
    EMAIL = "email"
    PHONE = "phone"
    DATE = "date"
    TIME = "time"
    DURATION = "duration"
    NOTES = "notes"
    CONFIRM = "confirm"


class NotificationChannel(str, Enum):
    """Canales de notificación al agente."""
    WHATSAPP = "whatsapp"
    EMAIL = "email"


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