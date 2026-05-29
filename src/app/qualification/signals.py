# src/app/qualification/signals.py
"""Lead Qualification Signals — señales de compromiso y pesos para scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SignalConfig:
    """Configuración de una señal de calificación."""
    name: str
    points: int
    patterns_es: tuple = ()
    patterns_en: tuple = ()
    keywords_es: tuple = ()
    keywords_en: tuple = ()
    margarita_keywords: tuple = ()


MARGARITA_ZONES = [
    "pampatar", "porlamar", "el agua", "guacuco", "el yaque",
    "playa caribe", "playa parguito", "manzanillo",
    "casa de campo", "country club", "paraiso", "paraíso",
    "puerto real", "santa ana del norte",
    "sabana de guacuco", "rancho de chana", "cerro guayamuri",
    "las hernández", "las hernandez", "chana",
    "juan griego", "la asunción", "la asuncion",
]


SIGNALS: dict[str, SignalConfig] = {
    "budget_mentioned": SignalConfig(
        name="budget_mentioned",
        points=20,
        patterns_es=(
            r"\$[\d,\.]+",
            r"[\d,\.]+\s*(?:dólares|usd|bs)",
            r"(?:hasta|máximo|mínimo|entre)\s+[\d,\.]+",
            r"(?:precio|presupuesto|costo)\s+(?:de|máximo|mínimo)",
            r"(?:cuánto|cuanto)\s+(?:cuesta|vale|sale)",
        ),
        patterns_en=(
            r"\$[\d,\.]+",
            r"[\d,\.]+\s*(?:dollars|usd)",
            r"(?:up to|max|between|around)\s+[\d,\.]+",
            r"(?:price|budget|cost)\s+(?:of|range|limit)",
        ),
    ),

    "zone_specified": SignalConfig(
        name="zone_specified",
        points=15,
        margarita_keywords=tuple(MARGARITA_ZONES),
        patterns_es=(
            r"(?:en|cerca de|por la zona de|sector)\s+[A-Za-záéíóúñ]+",
        ),
        patterns_en=(
            r"(?:in|near|around|close to)\s+[A-Za-z]+",
        ),
    ),

    "property_type_clear": SignalConfig(
        name="property_type_clear",
        points=10,
        keywords_es=(
            "apartamento", "apto", "casa", "villa", "local",
            "planos", "arriendo", "alquiler", "venta", "comprar",
            "alquilar", "posada", "hotel", "terreno", "vacacional",
        ),
        keywords_en=(
            "apartment", "house", "villa", "office", "commercial",
            "rent", "buy", "purchase", "lease", "hostel", "hotel", "land",
        ),
    ),

    "specific_property_queried": SignalConfig(
        name="specific_property_queried",
        points=20,
        patterns_es=(),
        patterns_en=(),
    ),

    "payment_method_asked": SignalConfig(
        name="payment_method_asked",
        points=15,
        keywords_es=(
            "crédito", "hipotecario", "financiamiento", "contado",
            "cuotas", "enganche", "inicial", "banco", "efectivo",
            "transferencia", "zelle", "criptomoneda", "cripto",
        ),
        keywords_en=(
            "mortgage", "financing", "credit", "cash", "installments",
            "down payment", "bank", "wire transfer", "crypto", "zelle",
        ),
    ),

    "time_urgency_expressed": SignalConfig(
        name="time_urgency_expressed",
        points=15,
        keywords_es=(
            "urgente", "pronto", "este mes", "inmediato", "ya",
            "cuanto antes", "disponible", "mudarse", "mudanza",
        ),
        keywords_en=(
            "urgent", "soon", "this month", "immediately", "asap",
            "available", "move in", "moving", "right away",
        ),
    ),

    "engagement_depth": SignalConfig(
        name="engagement_depth",
        points=5,
        patterns_es=(),
        patterns_en=(),
    ),

    "international_buyer_signal": SignalConfig(
        name="international_buyer_signal",
        points=15,
        keywords_es=(
            "inversión", "roi", "retorno", "desde el exterior",
            "viviendo fuera", "diáspora", "invertir", "dólares",
            "airbnb", "booking", "rentabilidad", "turismo",
        ),
        keywords_en=(
            "investment", "roi", "return", "from abroad",
            "living outside", "invest", "portfolio", "rental income",
            "airbnb", "booking", "yield", "passive income",
        ),
    ),
}


def get_stage_from_score(
    score: int,
    threshold_book: int = 75,
    threshold_qualify: int = 40,
) -> str:
    """Retorna etapa de calificación según score."""
    if score >= threshold_book:
        return "book"
    elif score >= threshold_qualify:
        return "qualify"
    return "explore"


QUALIFICATION_QUESTIONS = {
    "budget_missing": {
        "es": "¿Tienes un presupuesto aproximado en mente? Esto me ayuda a mostrarte las mejores opciones.",
        "en": "Do you have an approximate budget in mind? This helps me show you the best options.",
    },
    "zone_missing": {
        "es": "¿Hay alguna zona de la isla que prefieras? Por ejemplo Pampatar, El Agua o El Yaque.",
        "en": "Is there an area of the island you prefer? For example Pampatar, El Agua or El Yaque.",
    },
    "type_missing": {
        "es": "¿Buscas para vivir, para invertir o como propiedad vacacional?",
        "en": "Are you looking for a residence, an investment, or a vacation property?",
    },
}


def detect_signal(signal_name: str, text: str, language: str = "es") -> bool:
    config = SIGNALS.get(signal_name)
    if not config:
        logger.warning("unknown_signal", signal_name=signal_name)
        return False

    text_lower = text.lower()

    primary_patterns = config.patterns_es if language == "es" else config.patterns_en
    for pattern in primary_patterns:
        if re.search(pattern, text_lower):
            return True

    secondary_patterns = config.patterns_en if language == "es" else config.patterns_es
    for pattern in secondary_patterns:
        if re.search(pattern, text_lower):
            return True

    primary_kw = config.keywords_es if language == "es" else config.keywords_en
    for kw in primary_kw:
        if kw.lower() in text_lower:
            return True

    secondary_kw = config.keywords_en if language == "es" else config.keywords_es
    for kw in secondary_kw:
        if kw.lower() in text_lower:
            return True

    for zone in config.margarita_keywords:
        if zone.lower() in text_lower:
            return True

    return False


def detect_all_signals(text: str, language: str = "es") -> dict[str, bool]:
    return {
        name: detect_signal(name, text, language)
        for name in SIGNALS.keys()
    }


def get_signal_points(signal_name: str) -> int:
    config = SIGNALS.get(signal_name)
    return config.points if config else 0


def get_qualification_question(missing_signal_type: str, language: str = "es") -> str | None:
    question = QUALIFICATION_QUESTIONS.get(missing_signal_type)
    if question:
        return question.get(language)
    return None
