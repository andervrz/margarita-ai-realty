# src/app/chat/language.py
"""Language Detection — detección ES/EN para chatbot inmobiliario Margarita.

Arquitectura:
  - Detección heurística determinística (sin LLM)
  - O(1) membership checks con dicts
  - Single-pass weighted token scoring
  - Fallback seguro a ES (mercado principal)
  - Preparado para FR/PT en V2
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from src.app.core.logging import get_logger

logger = get_logger(__name__)

# ── Configuración ─────────────────────────────────────────────────

SUPPORTED_LANGUAGES = {"es", "en"}
THRESHOLD_RATIO: float = 0.25
MIN_WORDS_FOR_DETECTION: int = 3
HIGH_CONFIDENCE_RATIO: float = 0.50
MEDIUM_CONFIDENCE_RATIO: float = 0.30
MIN_SCORE_DELTA: int = 2

# ── Weighted Markers ──────────────────────────────────────────────
# Peso 2 = señal fuerte / Peso 1 = señal normal

ES_MARKERS: dict[str, int] = {
    # Funcionales
    "el": 1, "la": 1, "los": 1, "las": 1,
    "de": 1, "del": 1, "que": 1, "para": 1,
    "con": 1, "por": 1, "como": 1,
    # Intención
    "busco": 2, "quiero": 2, "necesito": 2,
    "muestrame": 2, "muéstrame": 2, "dime": 1,
    # Inmobiliario
    "apartamento": 2, "apartamentos": 2, "apto": 2,
    "casa": 2, "casaquinta": 2,
    "habitacion": 2, "habitaciones": 2,
    "bano": 2, "banos": 2,
    "precio": 2, "presupuesto": 2,
    "venta": 2, "alquiler": 2, "arriendo": 2,
    "comprar": 2, "vender": 2,
    "vista": 1, "playa": 2, "mar": 1,
    "dolares": 1, "bolivares": 1,
}

EN_MARKERS: dict[str, int] = {
    # Funcionales
    "the": 1, "a": 1, "an": 1, "of": 1,
    "in": 1, "on": 1, "for": 1, "with": 1, "and": 1,
    # Intención
    "looking": 2, "want": 2, "need": 2,
    "show": 2, "tell": 1,
    # Inmobiliario
    "apartment": 2, "apartments": 2, "apt": 2,
    "house": 2, "home": 2, "villa": 2,
    "bedroom": 2, "bedrooms": 2,
    "bathroom": 2, "bathrooms": 2,
    "price": 2, "budget": 2,
    "sale": 2, "rent": 2, "buy": 2, "sell": 2,
    "beach": 2, "beachfront": 2,
    "ocean": 2, "sea": 1, "view": 1,
    "usd": 1, "dollars": 1,
}

TOKEN_REGEX = re.compile(r"\b[\wáéíóúñü]+\b", re.UNICODE)

# ── Result Object ─────────────────────────────────────────────────

ConfidenceLevel = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class LanguageResult:
    """Resultado de detección de idioma."""
    detected: str
    confidence: ConfidenceLevel
    es_score: int
    en_score: int
    total_words: int
    signal_words: int
    is_mixed: bool = False


# ── API Pública ───────────────────────────────────────────────────

def detect_language(text: str) -> LanguageResult:
    """
    Detecta idioma principal del mensaje.

    Estrategia:
      1. Normalizar unicode
      2. Tokenizar con regex
      3. Weighted scoring
      4. Ratio analysis
      5. Mixed-language detection
      6. Fallback seguro a ES
    """
    if not text or not text.strip():
        return _empty_result()

    cleaned = _normalize_text(text)
    words = TOKEN_REGEX.findall(cleaned)
    total_words = len(words)

    if total_words < MIN_WORDS_FOR_DETECTION:
        return LanguageResult(
            detected="es",
            confidence="low",
            es_score=0,
            en_score=0,
            total_words=total_words,
            signal_words=0,
            is_mixed=False,
        )

    es_score = 0
    en_score = 0

    for word in words:
        es_score += ES_MARKERS.get(word, 0)
        en_score += EN_MARKERS.get(word, 0)

    signal_words = es_score + en_score

    # Sin señales → fallback ES
    if signal_words == 0:
        return LanguageResult(
            detected="es",
            confidence="low",
            es_score=0,
            en_score=0,
            total_words=total_words,
            signal_words=0,
            is_mixed=False,
        )

    es_ratio = es_score / total_words
    en_ratio = en_score / total_words
    score_delta = abs(es_score - en_score)
    is_mixed = es_score > 0 and en_score > 0

    if es_score >= en_score:
        detected = "es"
        dominant_ratio = es_ratio
    else:
        detected = "en"
        dominant_ratio = en_ratio

    if dominant_ratio >= HIGH_CONFIDENCE_RATIO and score_delta >= MIN_SCORE_DELTA:
        confidence: ConfidenceLevel = "high"
    elif dominant_ratio >= MEDIUM_CONFIDENCE_RATIO:
        confidence = "medium"
    else:
        confidence = "low"

    logger.debug(
        "language_detected",
        detected=detected,
        confidence=confidence,
        es_score=es_score,
        en_score=en_score,
        total_words=total_words,
        is_mixed=is_mixed,
    )

    return LanguageResult(
        detected=detected,
        confidence=confidence,
        es_score=es_score,
        en_score=en_score,
        total_words=total_words,
        signal_words=signal_words,
        is_mixed=is_mixed,
    )


def get_language_code(result: LanguageResult) -> str:
    """Shortcut helper."""
    return result.detected


def should_switch_language(
    current_language: str,
    new_result: LanguageResult,
) -> bool:
    """
    Decide si debe cambiar el idioma de sesión.

    Reglas:
      - Nunca cambiar con confidence != high
      - Diferencia mínima de score requerida
      - Evita language-flapping
    """
    if current_language not in SUPPORTED_LANGUAGES:
        return False
    if new_result.detected == current_language:
        return False
    if new_result.confidence != "high":
        return False
    if abs(new_result.es_score - new_result.en_score) < MIN_SCORE_DELTA:
        return False
    return True


# ── Helpers Privados ──────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Normaliza unicode y lowercase. Preserva tildes para matching."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFKC", text)
    return text


def _empty_result() -> LanguageResult:
    return LanguageResult(
        detected="es",
        confidence="low",
        es_score=0,
        en_score=0,
        total_words=0,
        signal_words=0,
        is_mixed=False,
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — chat/language.py\n")

    # Test 1: Español
    r = detect_language("Hola, busco apartamento en Pampatar con vista al mar")
    assert r.detected == "es", f"Esperado es, obtenido {r.detected}"
    assert r.es_score > r.en_score
    print(f"✅ ES detectado (confidence={r.confidence}, es={r.es_score}, en={r.en_score})")

    # Test 2: Inglés
    r = detect_language("Looking for a beachfront house with ocean view")
    assert r.detected == "en", f"Esperado en, obtenido {r.detected}"
    assert r.en_score > r.es_score
    print(f"✅ EN detectado (confidence={r.confidence}, es={r.es_score}, en={r.en_score})")

    # Test 3: Texto corto
    r = detect_language("Hola")
    assert r.detected == "es"
    assert r.confidence == "low"
    print("✅ Texto corto → low confidence")

    # Test 4: Texto vacío
    r = detect_language("")
    assert r.detected == "es"
    assert r.total_words == 0
    print("✅ Vacío → fallback ES")

    # Test 5: Mixed ES dominante
    r = detect_language("Hola looking for casa en Pampatar with vista al mar")
    assert r.detected == "es"
    assert r.is_mixed is True
    print(f"✅ Mixed ES dominante (es={r.es_score}, en={r.en_score})")

    # Test 6: Mixed EN dominante
    r = detect_language("Hello quiero buy a beachfront house in Pampatar")
    assert r.detected == "en"
    print(f"✅ Mixed EN dominante (es={r.es_score}, en={r.en_score})")

    # Test 7: should_switch_language
    high_en = LanguageResult(
        detected="en", confidence="high",
        es_score=1, en_score=8,
        total_words=10, signal_words=9,
    )
    assert should_switch_language("es", high_en) is True

    medium_en = LanguageResult(
        detected="en", confidence="medium",
        es_score=2, en_score=4,
        total_words=10, signal_words=6,
    )
    assert should_switch_language("es", medium_en) is False
    print("✅ Lógica de switch correcta")

    # Test 8: Sin señales
    r = detect_language("qwerty asdf zxcv")
    assert r.detected == "es"
    assert r.signal_words == 0
    print("✅ Sin señales → fallback ES")

    # Test 9: get_language_code
    assert get_language_code(r) == "es"
    print("✅ get_language_code OK")

    print("\n🎉 Todos los smoke tests pasaron ✅")
