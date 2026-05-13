# src/app/qualification/signals.py
"""Lead Qualification Signals — señales de compromiso y pesos para scoring.

Define las señales detectables en conversaciones de usuarios interesados
en propiedades de la Isla de Margarita, Venezuela.

Cada señal tiene:
  - Puntos asignados (peso en el score total)
  - Patrones regex por idioma (ES/EN)
  - Keywords específicas del mercado local

Principios:
  - Rule-based V1: simple, predecible, auditable, cero costo LLM
  - Señales específicas de Margarita: vista_al_mar, uso_vacacional, comprador internacional
  - Regex cubre 80% de casos sin costo
  - El scorer (scorer.py) consume estas señales para calcular score final
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.app.core.logging import get_logger
from src.app.core.config import get_settings

logger = get_logger(__name__)


# ── Señales y configuración ───────────────────────────────────────
   

@dataclass(frozen=True)
class SignalConfig:
    """Configuración de una señal de calificación."""
   name: str
    points: int
    patterns_es: tuple[str, ...] = ()
    patterns_en: tuple[str, ...] = ()
    keywords_es: tuple[str, ...] = ()
    keywords_en: tuple[str, ...] = ()
    margarita_keywords: tuple[str, ...] = ()


# ── Zonas de Margarita (para señal zone_specified) ────────────────

MARGARITA_ZONES = [
    "pampatar", "porlamar", "el agua", "guacuco", "el yaque",
    "playa caribe", "playa parguito", "manzanillo",
    "casa de campo", "country club", "paraiso", "paraíso",
    "puerto real", "santa ana del norte",
    "sabana de guacuco", "rancho de chana", "cerro guayamuri",
    "las hernández", "las hernandez", "chana",
    "juan griego", "la asunción", "la asuncion",
]


# ── Definición de señales ─────────────────────────────────────────
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
        margarita_keywords=MARGARITA_ZONES,
        patterns_es=[
            r"(?:en|cerca de|por la zona de|sector)\s+[A-Za-záéíóúñ]+",
        ],
        patterns_en=[
            r"(?:in|near|around|close to)\s+[A-Za-z]+",
        ],
    ),
    
    "property_type_clear": SignalConfig(
        name="property_type_clear",
        points=10,
        keywords_es=[
            "apartamento", "apto", "casa", "villa", "local",
            "planos", "arriendo", "alquiler", "venta", "comprar",
            "alquilar", "posada", "hotel", "terreno", "vacacional",
        ],
        keywords_en=[
            "apartment", "house", "villa", "office", "commercial",
            "rent", "buy", "purchase", "lease", "hostel", "hotel", "land",
        ],
    ),
    
    "specific_property_queried": SignalConfig(
        name="specific_property_queried",
        points=20,
        # Lógica: activado en chat engine cuando usuario hace follow-up de resultado
        # No se detecta por regex — se marca explícitamente por el engine
        patterns_es=[],
        patterns_en=[],
    ),
    
    "payment_method_asked": SignalConfig(
        name="payment_method_asked",
        points=15,
        keywords_es=[
            "crédito", "hipotecario", "financiamiento", "contado",
            "cuotas", "enganche", "inicial", "banco", "efectivo",
            "transferencia", "zelle", "criptomoneda", "cripto",
        ],
        keywords_en=[
            "mortgage", "financing", "credit", "cash", "installments",
            "down payment", "bank", "wire transfer", "crypto", "zelle",
        ],
    ),
    
    "time_urgency_expressed": SignalConfig(
        name="time_urgency_expressed",
        points=15,
        keywords_es=[
            "urgente", "pronto", "este mes", "inmediato", "ya",
            "cuanto antes", "disponible", "mudarse", "mudanza",
        ],
        keywords_en=[
            "urgent", "soon", "this month", "immediately", "asap",
            "available", "move in", "moving", "right away",
        ],
    ),
    
    "engagement_depth": SignalConfig(
        name="engagement_depth",
        points=5,
        # Lógica: len([m for m in messages if m.role == "user"]) > 5
        # Calculado por el scorer, no por regex
        patterns_es=[],
        patterns_en=[],
    ),
    
    "international_buyer_signal": SignalConfig(
        name="international_buyer_signal",
        points=15,  # Ajustado de 10 a 15 por análisis de mercado
        keywords_es=[
            "inversión", "roi", "retorno", "desde el exterior",
            "viviendo fuera", "diáspora", "invertir", "dólares",
            "airbnb", "booking", "rentabilidad", "turismo",
        ],
        keywords_en=[
            "investment", "roi", "return", "from abroad",
            "living outside", "invest", "portfolio", "rental income",
            "airbnb", "booking", "yield", "passive income",
        ],
    ),
}


# ── Umbrales de calificación ──────────────────────────────────────

def get_stage_from_score(score: int, threshold_book: int = 75, threshold_qualify: int = 40) -> str:
    if score >= threshold_book:
        return "book"
    elif score >= threshold_qualify:
        return "qualify"
    return "explore"


# ── Preguntas de calificación por señal faltante ──────────────────

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


# ── Funciones de detección ────────────────────────────────────────

def detect_signal(signal_name: str, text: str, language: str = "es") -> bool:
    config = SIGNALS.get(signal_name)
    if not config:
        logger.warning("unknown_signal", signal_name=signal_name)
        return False

    text_lower = text.lower()

    # Verificar patterns del idioma principal primero
    primary_patterns = config.patterns_es if language == "es" else config.patterns_en
    for pattern in primary_patterns:
        if re.search(pattern, text_lower):
            return True

    # Verificar patterns del idioma secundario (mercado bilingüe Margarita)
    secondary_patterns = config.patterns_en if language == "es" else config.patterns_es
    for pattern in secondary_patterns:
        if re.search(pattern, text_lower):
            return True

    # Keywords primarias
    primary_kw = config.keywords_es if language == "es" else config.keywords_en
    for kw in primary_kw:
        if kw.lower() in text_lower:
            return True

    # Keywords secundarias
    secondary_kw = config.keywords_en if language == "es" else config.keywords_es
    for kw in secondary_kw:
        if kw.lower() in text_lower:
            return True

    # Zonas de Margarita (agnósticas al idioma)
    for zone in config.margarita_keywords:
        if zone.lower() in text_lower:
            return True

    return False


def detect_all_signals(
    text: str,
    language: str = "es",
) -> dict[str, bool]:
    """Detecta todas las señales en un texto.
    
    Returns:
        Dict {signal_name: detected}.
    """
    return {
        name: detect_signal(name, text, language)
        for name in SIGNALS.keys()
    }


def get_signal_points(signal_name: str) -> int:
    """Retorna puntos de una señal."""
    config = SIGNALS.get(signal_name)
    return config.points if config else 0


def get_qualification_question(
    missing_signal_type: str,
    language: str = "es",
) -> str | None:
    """Retorna pregunta de calificación para una señal faltante."""
    question = QUALIFICATION_QUESTIONS.get(missing_signal_type)
    if question:
        return question.get(language)
    return None


def get_stage_from_score(score: int) -> str:
    """Retorna etapa de calificación según score."""
    if score >= THRESHOLDS["book"]:
        return "book"
    elif score >= THRESHOLDS["qualify"]:
        return "qualify"
    return "explore"


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — qualification/signals.py")
    
    # Test 1: Detectar budget_mentioned ES
    assert detect_signal("budget_mentioned", "Busco algo hasta $200000", "es") is True
    assert detect_signal("budget_mentioned", "Cuánto cuesta el apartamento", "es") is True
    assert detect_signal("budget_mentioned", "Hola, información por favor", "es") is False
    print("  ✅ budget_mentioned ES")
    
    # Test 2: Detectar budget_mentioned EN
    assert detect_signal("budget_mentioned", "Looking for something up to $200k", "en") is True
    assert detect_signal("budget_mentioned", "What is the price range", "en") is True
    print("  ✅ budget_mentioned EN")
    
    # Test 3: Detectar zone_specified con zonas Margarita
    assert detect_signal("zone_specified", "Busco en Pampatar", "es") is True
    assert detect_signal("zone_specified", "Something in El Yaque", "en") is True
    assert detect_signal("zone_specified", "Cerca de playa caribe", "es") is True
    print("  ✅ zone_specified con zonas Margarita")
    
    # Test 4: Detectar property_type_clear
    assert detect_signal("property_type_clear", "Quiero una casa", "es") is True
    assert detect_signal("property_type_clear", "Looking for an apartment", "en") is True
    assert detect_signal("property_type_clear", "Necesito alquilar", "es") is True
    print("  ✅ property_type_clear")
    
    # Test 5: Detectar payment_method_asked
    assert detect_signal("payment_method_asked", "Puedo pagar con Zelle", "es") is True
    assert detect_signal("payment_method_asked", "Do you accept crypto", "en") is True
    print("  ✅ payment_method_asked")
    
    # Test 6: Detectar time_urgency_expressed
    assert detect_signal("time_urgency_expressed", "Necesito algo urgente", "es") is True
    assert detect_signal("time_urgency_expressed", "I need to move in asap", "en") is True
    print("  ✅ time_urgency_expressed")
    
    # Test 7: Detectar international_buyer_signal
    assert detect_signal("international_buyer_signal", "Busco inversión con buen ROI", "es") is True
    assert detect_signal("international_buyer_signal", "Looking for rental income", "en") is True
    assert detect_signal("international_buyer_signal", "Vivo en el exterior y quiero invertir", "es") is True
    print("  ✅ international_buyer_signal")
    
    # Test 8: detect_all_signals
    all_signals = detect_all_signals("Busco apartamento en Pampatar hasta $150k para invertir", "es")
    assert all_signals["budget_mentioned"] is True
    assert all_signals["zone_specified"] is True
    assert all_signals["property_type_clear"] is True
    assert all_signals["international_buyer_signal"] is True
    assert all_signals["payment_method_asked"] is False
    print("  ✅ detect_all_signals integrado")
    
    # Test 9: Puntos por señal
    assert get_signal_points("budget_mentioned") == 20
    assert get_signal_points("zone_specified") == 15
    assert get_signal_points("engagement_depth") == 5
    print("  ✅ get_signal_points correcto")
    
    # Test 10: Umbrales
    assert get_stage_from_score(80) == "book"
    assert get_stage_from_score(50) == "qualify"
    assert get_stage_from_score(20) == "explore"
    assert get_stage_from_score(75) == "book"  # boundary
    assert get_stage_from_score(40) == "qualify"  # boundary
    print("  ✅ get_stage_from_score umbrales correctos")
    
    # Test 11: Preguntas de calificación
    q_es = get_qualification_question("budget_missing", "es")
    assert "presupuesto" in q_es
    q_en = get_qualification_question("zone_missing", "en")
    assert "area" in q_en
    print("  ✅ QUALIFICATION_QUESTIONS ES/EN")
    
    # Test 12: Signal desconocido
    assert detect_signal("unknown_signal", "test") is False
    assert get_signal_points("unknown") == 0
    print("  ✅ Manejo signal desconocido")
    
    print("\n🎉 Todos los smoke tests pasaron")
