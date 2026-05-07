# src/app/qualification/scorer.py
"""Lead Qualification Scorer — calcula score final y determina etapa.

Consume las señales extraídas por extractor.py y aplica:
  1. Suma ponderada de puntos por señal detectada
  2. Bonificaciones/penalizaciones contextuales
  3. Determinación de etapa: explore | qualify | book
  4. Generación de preguntas de calificación si aplica

Principios:
  - Python valida, no el LLM
  - Umbrales configurables por tenant (desde settings)
  - Score 0-100 (escala normalizada)
  - Determinístico: mismas señales → mismo score siempre
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.qualification.extractor import (
    ExtractedSignals,
    extract_signals_from_history,
    get_missing_signals,
)
from app.qualification.signals import get_stage_from_score, THRESHOLDS

logger = get_logger()


# ── Dataclass de resultado ────────────────────────────────────────

@dataclass(frozen=True)
class QualificationResult:
    """Resultado completo de calificación de un lead."""
    
    total_score: int
    stage: str  # "explore" | "qualify" | "book"
    signals_found: list[str]
    missing_signals: list[str]
    is_international: bool
    suggested_questions: list[str]
    raw_signals: dict  # Para debugging/auditoría


# ── Función principal ─────────────────────────────────────────────

def calculate_qualification_score(
    messages: list[dict[str, any]],
    current_query: str,
    language: str = "es",
) -> QualificationResult:
    """Calcula score de calificación y determina etapa.
    
    Args:
        messages: Historial completo de mensajes de la sesión.
        current_query: Mensaje actual del usuario (para contexto adicional).
        language: Idioma de la sesión.
    
    Returns:
        QualificationResult con score, stage y metadatos.
    """
    settings = get_settings()
    
    # 1. Extraer señales del historial
    extracted = extract_signals_from_history(messages, language)
    
    # 2. Calcular score base
    base_score = _calculate_base_score(extracted)
    
    # 3. Aplicar modificadores contextuales
    modifiers = _calculate_modifiers(extracted, current_query, language)
    
    # 4. Score final (clamp 0-100)
    total_score = max(0, min(100, base_score + modifiers))
    
    # 5. Determinar etapa
    stage = get_stage_from_score(total_score)
    
    # 6. Señales faltantes y preguntas sugeridas
    missing = get_missing_signals(extracted)
    questions = _generate_questions(missing, language)
    
    # 7. Detectar comprador internacional
    is_international = extracted.international_buyer_signal
    
    logger.info(
        "qualification_scored",
        total_score=total_score,
        stage=stage,
        signals_found=extracted.signals_found,
        missing_signals=missing,
        is_international=is_international,
        language=language,
    )
    
    return QualificationResult(
        total_score=total_score,
        stage=stage,
        signals_found=extracted.signals_found.copy(),
        missing_signals=missing,
        is_international=is_international,
        suggested_questions=questions,
        raw_signals=extracted.to_dict(),
    )


# ── Helpers privados ─────────────────────────────────────────────

def _calculate_base_score(extracted: ExtractedSignals) -> int:
    """Calcula score base sumando puntos de señales detectadas."""
    from app.qualification.signals import get_signal_points
    
    score = 0
    
    signal_map = {
        "budget_mentioned": extracted.budget_mentioned,
        "zone_specified": extracted.zone_specified,
        "property_type_clear": extracted.property_type_clear,
        "specific_property_queried": extracted.specific_property_queried,
        "payment_method_asked": extracted.payment_method_asked,
        "time_urgency_expressed": extracted.time_urgency_expressed,
        "engagement_depth": extracted.engagement_depth,
        "international_buyer_signal": extracted.international_buyer_signal,
    }
    
    for signal_name, detected in signal_map.items():
        if detected:
            score += get_signal_points(signal_name)
    
    return score


def _calculate_modifiers(
    extracted: ExtractedSignals,
    current_query: str,
    language: str,
) -> int:
    """Calcula modificadores contextuales al score.
    
    Bonificaciones:
      +5: Menciona múltiples zonas (comparando opciones)
      +5: Pregunta sobre visita/agendamiento explícitamente
      +10: Menciona capacidad de compra inmediata (contado, transferencia lista)
    
    Penalizaciones:
      -5: Mensaje muy corto (< 3 palabras, poco compromiso)
      -10: Solo saludos sin contenido en 3+ mensajes
    """
    modifiers = 0
    query_lower = current_query.lower()
    
    # Bonificación: múltiples zonas mencionadas
    from app.qualification.signals import MARGARITA_ZONES
    zones_mentioned = sum(1 for z in MARGARITA_ZONES if z in query_lower)
    if zones_mentioned >= 2:
        modifiers += 5
        logger.debug("modifier_multiple_zones", zones=zones_mentioned)
    
    # Bonificación: intención de visita explícita
    visit_keywords_es = ["visita", "agendar", "cita", "ver la propiedad", "cuándo puedo ver"]
    visit_keywords_en = ["visit", "schedule", "appointment", "see the property", "when can i see"]
    visit_keywords = visit_keywords_es if language == "es" else visit_keywords_en
    
    if any(kw in query_lower for kw in visit_keywords):
        modifiers += 5
        logger.debug("modifier_visit_intent")
    
    # Bonificación: compra inmediata
    immediate_keywords_es = ["contado", "transferencia lista", "tengo el dinero", "puedo pagar ya"]
    immediate_keywords_en = ["cash ready", "can pay now", "have the money", "immediate purchase"]
    immediate_keywords = immediate_keywords_es if language == "es" else immediate_keywords_en
    
    if any(kw in query_lower for kw in immediate_keywords):
        modifiers += 10
        logger.debug("modifier_immediate_purchase")
    
    # Penalización: mensaje muy corto
    words = query_lower.split()
    if len(words) < 3:
        modifiers -= 5
        logger.debug("modifier_too_short", words=len(words))
    
    # Penalización: solo saludos repetidos
    greeting_only = ["hola", "buenos días", "buenas tardes", "hi", "hello", "hey"]
    if len(words) <= 2 and any(g in query_lower for g in greeting_only):
        modifiers -= 10
        logger.debug("modifier_greeting_only")
    
    return modifiers


def _generate_questions(
    missing_signals: list[str],
    language: str,
) -> list[str]:
    """Genera preguntas de calificación para señales faltantes."""
    from app.qualification.signals import get_qualification_question
    
    questions: list[str] = []
    
    for missing in missing_signals:
        question = get_qualification_question(missing, language)
        if question:
            questions.append(question)
    
    return questions


def should_trigger_booking(
    result: QualificationResult,
    tenant_threshold: int | None = None,
) -> bool:
    """Determina si se debe activar el booking flow.
    
    Args:
        result: Resultado de calificación.
        tenant_threshold: Umbral configurable por tenant (default 75).
    
    Returns:
        True si score >= threshold y no está ya en booking.
    """
    settings = get_settings()
    threshold = tenant_threshold or settings.qualifier_book_threshold
    
    return result.total_score >= threshold and result.stage == "book"


def get_qualification_summary(
    result: QualificationResult,
    language: str = "es",
) -> str:
    """Genera resumen legible de la calificación (para logs/debug)."""
    if language == "es":
        return (
            f"Score: {result.total_score}/100 | "
            f"Etapa: {result.stage} | "
            f"Señales: {', '.join(result.signals_found) or 'ninguna'} | "
            f"Internacional: {'sí' if result.is_international else 'no'}"
        )
    return (
        f"Score: {result.total_score}/100 | "
        f"Stage: {result.stage} | "
        f"Signals: {', '.join(result.signals_found) or 'none'} | "
        f"International: {'yes' if result.is_international else 'no'}"
    )


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — qualification/scorer.py")
    
    # Test 1: Score 0 (vacío)
    result_empty = calculate_qualification_score([], "Hola", "es")
    assert result_empty.total_score == 0
    assert result_empty.stage == "explore"
    assert result_empty.is_international is False
    print(f"  ✅ Score 0: {result_empty.total_score} (explore)")
    
    # Test 2: Score con budget + zone + type
    msgs = [
        {"role": "user", "content": "Busco apartamento en Pampatar hasta $150k"},
    ]
    result_basic = calculate_qualification_score(msgs, "Quiero algo para vivir", "es")
    assert result_basic.total_score >= 45  # 20 + 15 + 10 = 45
    assert result_basic.stage in ["explore", "qualify"]
    assert "budget_mentioned" in result_basic.signals_found
    assert "zone_specified" in result_basic.signals_found
    print(f"  ✅ Score básico: {result_basic.total_score} ({result_basic.stage})")
    
    # Test 3: Score alto (book)
    msgs_high = [
        {"role": "user", "content": "Busco casa en El Yaque para invertir"},
        {"role": "assistant", "content": "Opciones disponibles"},
        {"role": "user", "content": "Me interesa la primera, tengo $200k contado"},
        {"role": "assistant", "content": "Excelente opción"},
        {"role": "user", "content": "Cuándo puedo visitar? Quiero agendar"},
    ]
    result_high = calculate_qualification_score(msgs_high, "Quiero visitar este fin de semana", "es")
    assert result_high.total_score >= 75
    assert result_high.stage == "book"
    assert result_high.is_international is True  # "invertir" → international signal
    print(f"  ✅ Score alto: {result_high.total_score} ({result_high.stage})")
    
    # Test 4: Modificador múltiples zonas
    msgs_zones = [
        {"role": "user", "content": "Busco en Pampatar o El Yaque"},
    ]
    result_zones = calculate_qualification_score(msgs_zones, "Quiero comparar Pampatar y El Yaque", "es")
    # Budget no mencionado, pero zona sí + múltiples zonas bonus
    assert result_zones.total_score >= 15  # zone (15) + multiple zones bonus (5)
    print(f"  ✅ Múltiples zonas: {result_zones.total_score}")
    
    # Test 5: Penalización mensaje corto
    result_short = calculate_qualification_score([], "ok", "es")
    assert result_short.total_score <= 0  # 0 base - 5 corto
    print(f"  ✅ Penalización corto: {result_short.total_score}")
    
    # Test 6: should_trigger_booking
    result_book = QualificationResult(
        total_score=80,
        stage="book",
        signals_found=["budget", "zone"],
        missing_signals=[],
        is_international=False,
        suggested_questions=[],
        raw_signals={},
    )
    assert should_trigger_booking(result_book) is True
    assert should_trigger_booking(result_book, tenant_threshold=85) is False
    print("  ✅ should_trigger_booking")
    
    # Test 7: QualificationResult frozen
    assert result_book.total_score == 80
    try:
        result_book.total_score = 90
        assert False, "Debería ser frozen"
    except AttributeError:
        pass
    print("  ✅ QualificationResult inmutable")
    
    # Test 8: get_qualification_summary ES
    summary = get_qualification_summary(result_book, "es")
    assert "Score: 80/100" in summary
    assert "Etapa: book" in summary
    print(f"  ✅ Summary ES: {summary}")
    
    # Test 9: get_qualification_summary EN
    summary_en = get_qualification_summary(result_book, "en")
    assert "Stage: book" in summary_en
    print(f"  ✅ Summary EN: {summary_en}")
    
    # Test 10: Suggested questions
    result_qualify = calculate_qualification_score(
        [{"role": "user", "content": "Busco casa"}],
        "Quiero algo grande",
        "es"
    )
    assert len(result_qualify.suggested_questions) > 0
    assert any("presupuesto" in q for q in result_qualify.suggested_questions)
    print(f"  ✅ Preguntas sugeridas: {len(result_qualify.suggested_questions)}")
    
    print("\n🎉 Todos los smoke tests pasaron")