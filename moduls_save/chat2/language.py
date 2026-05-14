# src/app/chat/language.py
"""Language Detection — detección de idioma ES/EN para respuestas del chatbot.

Responsabilidades:
  1. Detectar idioma del mensaje del usuario
  2. Normalizar a código de idioma ("es" | "en")
  3. Mantener consistencia de idioma en la sesión (no cambiar mid-conversation)
  4. Fallback a "es" si la detección es ambigua

Principios:
  - Simple y rápido: heurística por palabras clave, no LLM
  - Determinístico: mismo input → mismo output siempre
  - Bilingüe ES/EN desde v1 (mercado venezolano + compradores internacionales)
  - Francés → V2 con primer cliente europeo de ese segmento
"""

from __future__ import annotations

from dataclasses import dataclass

from src.app.core.logging import get_logger

logger = get_logger()


# ── Palabras clave por idioma ─────────────────────────────────────

ES_MARKERS: set[str] = {
    # Artículos, preposiciones, conectores
    "el", "la", "los", "las", "un", "una", "de", "del", "al", "en", "con",
    "por", "para", "que", "y", "o", "pero", "si", "como", "más", "muy",
    # Verbos comunes inmobiliarios
    "busco", "busca", "quiero", "quiera", "necesito", "tengo", "sería",
    "encuentra", "muestra", "dime", "cuánto", "cuanto", "dónde", "donde",
    # Términos inmobiliarios ES
    "apartamento", "apto", "casa", "villa", "habitación", "habitaciones",
    "baño", "baños", "precio", "presupuesto", "zona", "sector", "metro",
    "metros", "cuadrado", "cuadrados", "venta", "arriendo", "alquiler",
    "comprar", "vender", "rentar", "vista", "mar", "playa", "frente",
    "invertir", "inversión", "dinero", "dólares", "bolívares",
}

EN_MARKERS: set[str] = {
    # Artículos, preposiciones, conectores
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "and",
    "or", "but", "if", "how", "what", "where", "when", "which", "more",
    "very", "so", "than", "then", "that", "this", "these", "those",
    # Verbos comunes inmobiliarios
    "looking", "want", "need", "have", "would", "find", "show", "tell",
    "how", "much", "where", "is", "are", "do", "does", "can", "could",
    # Términos inmobiliarios EN
    "apartment", "apt", "house", "home", "villa", "bedroom", "bedrooms",
    "bathroom", "bathrooms", "price", "budget", "area", "zone", "square",
    "meter", "meters", "buy", "sell", "rent", "sale", "view", "ocean",
    "sea", "beach", "beachfront", "invest", "investment", "money",
    "dollars", "usd",
}


# ── Configuración de detección ────────────────────────────────────

THRESHOLD_RATIO: float = 0.25  # Mínimo 25% de palabras en un idioma para decidir
MIN_WORDS_FOR_DETECTION: int = 3  # Mínimo palabras para hacer detección confiable


# ── Dataclass de resultado ────────────────────────────────────────

@dataclass(frozen=True)
class LanguageResult:
    """Resultado de detección de idioma."""
    detected: str  # "es" | "en"
    confidence: str  # "high" | "medium" | "low"
    es_count: int
    en_count: int
    total_words: int


# ── Función pública ───────────────────────────────────────────────

def detect_language(text: str) -> LanguageResult:
    """Detecta idioma de un texto basado en palabras clave.
    
    Args:
        text: Texto del usuario (mensaje de chat).
    
    Returns:
        LanguageResult con idioma detectado y metadatos.
    """
    if not text or not text.strip():
        return LanguageResult(
            detected="es",
            confidence="low",
            es_count=0,
            en_count=0,
            total_words=0,
        )
    
    # Normalizar: lowercase, quitar puntuación básica
    cleaned = _clean_text(text)
    words = cleaned.split()
    
    if len(words) < MIN_WORDS_FOR_DETECTION:
        # Texto muy corto: fallback a ES (mercado principal)
        return LanguageResult(
            detected="es",
            confidence="low",
            es_count=0,
            en_count=0,
            total_words=len(words),
        )
    
    # Contar palabras en cada idioma
    es_count = sum(1 for w in words if w in ES_MARKERS)
    en_count = sum(1 for w in words if w in EN_MARKERS)
    
    # Calcular ratios
    total = len(words)
    es_ratio = es_count / total
    en_ratio = en_count / total
    
    # Determinar idioma
    detected = "es"
    confidence = "low"
    
    if es_ratio > en_ratio and es_ratio >= THRESHOLD_RATIO:
        detected = "es"
        confidence = "high" if es_ratio >= 0.5 else "medium"
    elif en_ratio > es_ratio and en_ratio >= THRESHOLD_RATIO:
        detected = "en"
        confidence = "high" if en_ratio >= 0.5 else "medium"
    elif es_count > 0 or en_count > 0:
        # Hay señales pero debajo del threshold
        detected = "es" if es_count >= en_count else "en"
        confidence = "low"
    # else: ninguna señal → default ES (ya seteado arriba)
    
    logger.debug(
        "language_detected",
        detected=detected,
        confidence=confidence,
        es_ratio=round(es_ratio, 2),
        en_ratio=round(en_ratio, 2),
        total_words=total,
    )
    
    return LanguageResult(
        detected=detected,
        confidence=confidence,
        es_count=es_count,
        en_count=en_count,
        total_words=total,
    )


def get_language_code(result: LanguageResult) -> str:
    """Retorna código de idioma simple (shortcut)."""
    return result.detected


def should_switch_language(
    current_language: str,
    new_result: LanguageResult,
) -> bool:
    """Determina si se debe cambiar el idioma de la sesión.
    
    Evita cambios de idioma por mensajes ambiguos o cortos.
    Solo cambia si la confianza es "high" y el idioma es diferente.
    """
    if new_result.confidence != "high":
        return False
    if new_result.detected == current_language:
        return False
    # Cambio confirmado
    return True


# ── Helpers privados ─────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Limpia texto para análisis: lowercase, quita puntuación común."""
    cleaned = text.lower()
    # Quitar puntuación frecuente
    for char in ".,;:!?¿¡\"'()[]{}-–—/\\@#$%&*+=<>~`|":
        cleaned = cleaned.replace(char, " ")
    # Normalizar espacios múltiples
    cleaned = " ".join(cleaned.split())
    return cleaned


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — chat/language.py")
    
    # Test 1: ES simple
    result = detect_language("Hola, busco apartamento en Pampatar")
    assert result.detected == "es"
    assert result.confidence == "high"
    assert result.es_count > result.en_count
    print(f"  ✅ ES detectado (confidence={result.confidence}, es={result.es_count}, en={result.en_count})")
    
    # Test 2: EN simple
    result_en = detect_language("Looking for a house with ocean view")
    assert result_en.detected == "en"
    assert result_en.confidence == "high"
    assert result_en.en_count > result_en.es_count
    print(f"  ✅ EN detectado (confidence={result_en.confidence}, es={result_en.es_count}, en={result_en.en_count})")
    
    # Test 3: Texto corto → ES default
    result_short = detect_language("Hola")
    assert result_short.detected == "es"
    assert result_short.confidence == "low"
    assert result_short.total_words == 1
    print("  ✅ Texto corto → ES default (low confidence)")
    
    # Test 4: Vacío → ES default
    result_empty = detect_language("")
    assert result_empty.detected == "es"
    assert result_empty.confidence == "low"
    print("  ✅ Vacío → ES default")
    
    # Test 5: Mixto pero mayoría ES
    result_mix = detect_language("Hola looking for casa en Pampatar with vista al mar")
    assert result_mix.detected == "es"
    print(f"  ✅ Mixto mayoría ES (es={result_mix.es_count}, en={result_mix.en_count})")
    
    # Test 6: Mixto pero mayoría EN
    result_mix_en = detect_language("Hello quiero buy a house in Pampatar")
    assert result_mix_en.detected == "en"
    print(f"  ✅ Mixto mayoría EN (es={result_mix_en.es_count}, en={result_mix_en.en_count})")
    
    # Test 7: should_switch_language
    assert should_switch_language("es", LanguageResult("en", "high", 5, 0, 10)) is True
    assert should_switch_language("es", LanguageResult("en", "medium", 5, 0, 10)) is False
    assert should_switch_language("es", LanguageResult("es", "high", 10, 0, 10)) is False
    print("  ✅ should_switch_language lógica correcta")
    
    # Test 8: get_language_code shortcut
    assert get_language_code(result) == "es"
    print("  ✅ get_language_code shortcut")
    
    # Test 9: _clean_text
    cleaned = _clean_text("Hello!!! ¿Cómo estás? (test)")
    assert "!!!" not in cleaned
    assert "¿" not in cleaned
    assert "cómo" in cleaned  # tilde se mantiene (no está en lista de remoción)
    print(f"  ✅ _clean_text: '{cleaned}'")
    
    # Test 10: Números y símbolos no afectan
    result_nums = detect_language("Apartamento $150,000 en Pampatar 3H/2B")
    assert result_nums.detected == "es"
    print("  ✅ Números/símbolos no afectan detección")
    
    print("\n🎉 Todos los smoke tests pasaron")