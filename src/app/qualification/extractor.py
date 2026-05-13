# src/app/qualification/extractor.py
"""Lead Qualification Extractor — historial de conversación → señales encontradas.

Analiza el historial completo de mensajes de una sesión para extraer
todas las señales de calificación acumuladas.

Responsabilidades:
  1. Procesar historial de mensajes (user + assistant)
  2. Detectar señales en cada mensaje del usuario
  3. Agregar señales encontradas (evitar duplicados)
  4. Calcular engagement_depth (cantidad de mensajes del usuario)
  5. Marcar specific_property_queried (follow-up de propiedades)

Principios:
  - Análisis retrospectivo: mira TODO el historial, no solo el último mensaje
  - Agregación: una señal detectada en cualquier mensaje cuenta
  - Engagement depth: métrica de compromiso acumulado
  - Sin LLM: regex y keywords puro (costo CERO)
"""

from __future__ import annotations
from typing import Any

from dataclasses import dataclass, field

from src.app.core.logging import get_logger
from src.app.qualification.signals import (
    SIGNALS,
    detect_signal,
    get_signal_points,
    MARGARITA_ZONES,
)

logger = get_logger(__name__)


# ── Dataclass de resultado ────────────────────────────────────────

@dataclass
class ExtractedSignals:
    """Señales extraídas de un historial de conversación."""
    
    # Señales booleanas (True si detectada en cualquier mensaje)
    budget_mentioned: bool = False
    zone_specified: bool = False
    property_type_clear: bool = False
    specific_property_queried: bool = False
    payment_method_asked: bool = False
    time_urgency_expressed: bool = False
    engagement_depth: bool = False  # True si > 5 mensajes user
    international_buyer_signal: bool = False
    
    # Metadatos
    total_user_messages: int = 0
    signals_found: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, bool | int | list[str]]:
        """Serializa para logs/respuestas."""
        return {
            "budget_mentioned": self.budget_mentioned,
            "zone_specified": self.zone_specified,
            "property_type_clear": self.property_type_clear,
            "specific_property_queried": self.specific_property_queried,
            "payment_method_asked": self.payment_method_asked,
            "time_urgency_expressed": self.time_urgency_expressed,
            "engagement_depth": self.engagement_depth,
            "international_buyer_signal": self.international_buyer_signal,
            "total_user_messages": self.total_user_messages,
            "signals_found": self.signals_found,
        }


# ── Función principal ─────────────────────────────────────────────

def extract_signals_from_history(
    messages: list[dict[str, Any]],
    language: str = "es",
) -> ExtractedSignals:
    """Extrae señales de calificación de un historial de mensajes.
    
    Args:
        messages: Lista de mensajes {"role": "user"|"assistant", "content": str, ...}.
        language: Idioma predominante de la sesión.
    
    Returns:
        ExtractedSignals con todas las señales agregadas.
    """
    result = ExtractedSignals()
    
    # Filtrar solo mensajes del usuario
    user_messages = [m for m in messages if m.get("role") == "user"]
    result.total_user_messages = len(user_messages)
    
    # Engagement depth: más de 5 mensajes del usuario
    if len(user_messages) > 5:
        result.engagement_depth = True
        result.signals_found.append("engagement_depth")
    
    # Detectar señales en cada mensaje del usuario
    for msg in user_messages:
        text = msg.get("content", "")
        if not text:
            continue
        
        # Detectar cada señal (excepto las que requieren lógica especial)
        _detect_standard_signals(text, language, result)
        
        # Detectar follow-up de propiedad específica
        _detect_property_followup(msg, messages, result)
    
    logger.debug(
        "signals_extracted",
        total_user_messages=result.total_user_messages,
        signals_found=result.signals_found,
        language=language,
    )
    
    return result


# ── Helpers privados ─────────────────────────────────────────────

def _detect_standard_signals(
    text: str,
    language: str,
    result: ExtractedSignals,
) -> None:
    """Detecta señales estándar (budget, zone, type, etc.)."""
    signals_to_check = [
        "budget_mentioned",
        "zone_specified",
        "property_type_clear",
        "payment_method_asked",
        "time_urgency_expressed",
        "international_buyer_signal",
    ]
    
    for signal_name in signals_to_check:
        if getattr(result, signal_name):
            continue  # Ya detectada, no repetir
        
        if detect_signal(signal_name, text, language):
            setattr(result, signal_name, True)
            result.signals_found.append(signal_name)


def _detect_property_followup(
    current_msg: dict[str, Any],
    all_messages: list[dict[str, Any]],
    result: ExtractedSignals,
) -> None:
    """Detecta si el usuario hace follow-up de una propiedad mostrada.
    
    Lógica: si en los últimos 3 turnos el assistant mostró propiedades,
    y el usuario actual pregunta sobre una de ellas (menciona título,
    zona, o características específicas), se marca como follow-up.
    """
    if result.specific_property_queried:
        return  # Ya detectado
    
    # Buscar últimos mensajes del assistant con propiedades
    recent_assistant_msgs = [
        m for m in all_messages
        if m.get("role") == "assistant"
        and m.get("has_properties")
    ][-3:]  # Últimos 3
    
    if not recent_assistant_msgs:
        return
    
    # Extraer zonas y características mencionadas en propiedades previas
    user_text = current_msg.get("content", "").lower()
    
    for ast_msg in recent_assistant_msgs:
        # Si el usuario menciona la misma zona o características
        # de propiedades mostradas → follow-up
        prop_count = ast_msg.get("property_count", 0)
        if prop_count > 0:
            # Heurística simple: si el mensaje es corto y menciona
            # "esa", "esa propiedad", "la primera", "la segunda", etc.
            followup_markers = [
                "esa", "esa propiedad", "la primera", "la segunda",
                "la tercera", "me interesa", "cuánto cuesta esa",
                "dime más", "más información", "fotos", "visita",
                "that one", "the first", "the second", "interested",
                "more info", "pictures", "visit",
            ]
            
            for marker in followup_markers:
                if marker in user_text:
                    result.specific_property_queried = True
                    result.signals_found.append("specific_property_queried")
                    return
            
            # También: si menciona zona que estaba en propiedades mostradas
            # (ya cubierto por zone_specified, pero refuerza intención)
            for zone in MARGARITA_ZONES:
                if zone in user_text:
                    # Verificar que esta zona no fue mencionada antes
                    # (nueva mención = posible follow-up)
                    pass  # Lógica compleja: omitir por simplicidad V1


def get_missing_signals(
    extracted: ExtractedSignals,
) -> list[str]:
    """Retorna lista de señales FALTANTES para calificación completa.
    
    Útil para generar preguntas de calificación dirigidas.
    """
    missing: list[str] = []
    
    if not extracted.budget_mentioned:
        missing.append("budget_missing")
    if not extracted.zone_specified:
        missing.append("zone_missing")
    if not extracted.property_type_clear:
        missing.append("type_missing")
    
    return missing


def calculate_raw_score(extracted: ExtractedSignals) -> int:
    """Calcula score bruto sumando puntos de señales detectadas.
    
    Nota: No aplica umbrales. Solo suma puntos.
    El scorer.py aplica lógica adicional (engagement, etc.).
    """
    score = 0
    
    signal_points_map = {
        "budget_mentioned": get_signal_points("budget_mentioned"),
        "zone_specified": get_signal_points("zone_specified"),
        "property_type_clear": get_signal_points("property_type_clear"),
        "specific_property_queried": get_signal_points("specific_property_queried"),
        "payment_method_asked": get_signal_points("payment_method_asked"),
        "time_urgency_expressed": get_signal_points("time_urgency_expressed"),
        "engagement_depth": get_signal_points("engagement_depth"),
        "international_buyer_signal": get_signal_points("international_buyer_signal"),
    }
    
    for signal_name, points in signal_points_map.items():
        if getattr(extracted, signal_name):
            score += points
    
    return score


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — qualification/extractor.py")
    
    # Test 1: Historial vacío
    empty = extract_signals_from_history([])
    assert empty.total_user_messages == 0
    assert empty.budget_mentioned is False
    assert empty.engagement_depth is False
    print("  ✅ Historial vacío")
    
    # Test 2: Un mensaje con budget
    msgs = [{"role": "user", "content": "Busco algo hasta $200k"}]
    result = extract_signals_from_history(msgs, "es")
    assert result.budget_mentioned is True
    assert result.total_user_messages == 1
    assert "budget_mentioned" in result.signals_found
    print("  ✅ Budget detectado")
    
    # Test 3: Múltiples mensajes, señales acumuladas
    msgs_multi = [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "Bienvenido"},
        {"role": "user", "content": "Busco en Pampatar"},
        {"role": "assistant", "content": "Encontré opciones"},
        {"role": "user", "content": "Quiero una casa para invertir"},
    ]
    result_multi = extract_signals_from_history(msgs_multi, "es")
    assert result_multi.zone_specified is True
    assert result_multi.property_type_clear is True
    assert result_multi.international_buyer_signal is True
    assert result_multi.total_user_messages == 3
    print("  ✅ Señales acumuladas de múltiples mensajes")
    
    # Test 4: Engagement depth (> 5 mensajes user)
    msgs_deep = [{"role": "user", "content": f"msg {i}"} for i in range(7)]
    result_deep = extract_signals_from_history(msgs_deep)
    assert result_deep.engagement_depth is True
    assert "engagement_depth" in result_deep.signals_found
    print("  ✅ Engagement depth detectado (>5 mensajes)")
    
    # Test 5: Specific property queried (follow-up)
    msgs_followup = [
        {"role": "user", "content": "Busco apartamento en El Yaque"},
        {"role": "assistant", "content": "Opciones", "has_properties": True, "property_count": 2},
        {"role": "user", "content": "Me interesa la primera, cuánto cuesta?"},
    ]
    result_followup = extract_signals_from_history(msgs_followup, "es")
    assert result_followup.specific_property_queried is True
    print("  ✅ Specific property queried (follow-up)")
    
    # Test 6: Sin follow-up
    msgs_no_followup = [
        {"role": "user", "content": "Busco casa"},
        {"role": "assistant", "content": "Opciones", "has_properties": True, "property_count": 2},
        {"role": "user", "content": "Gracias, bye"},
    ]
    result_no_followup = extract_signals_from_history(msgs_no_followup)
    assert result_no_followup.specific_property_queried is False
    print("  ✅ No follow-up cuando no hay interés")
    
    # Test 7: get_missing_signals
    extracted = ExtractedSignals(
        budget_mentioned=True,
        zone_specified=False,
        property_type_clear=True,
    )
    missing = get_missing_signals(extracted)
    assert "zone_missing" in missing
    assert "budget_missing" not in missing
    print("  ✅ get_missing_signals correcto")
    
    # Test 8: calculate_raw_score
    extracted_full = ExtractedSignals(
        budget_mentioned=True,      # 20 pts
        zone_specified=True,        # 15 pts
        property_type_clear=True,   # 10 pts
        specific_property_queried=True,  # 20 pts
        international_buyer_signal=True, # 15 pts
    )
    score = calculate_raw_score(extracted_full)
    expected = 20 + 15 + 10 + 20 + 15  # = 80
    assert score == expected
    print(f"  ✅ calculate_raw_score: {score} pts")
    
    # Test 9: Score con engagement
    extracted_eng = ExtractedSignals(
        budget_mentioned=True,  # 20
        engagement_depth=True,  # 5
    )
    score_eng = calculate_raw_score(extracted_eng)
    assert score_eng == 25
    print("  ✅ Score con engagement depth")
    
    # Test 10: Serialización to_dict
    d = extracted_full.to_dict()
    assert d["budget_mentioned"] is True
    assert d["total_user_messages"] == 0
    assert isinstance(d["signals_found"], list)
    print("  ✅ to_dict serialización")
    
    print("\n🎉 Todos los smoke tests pasaron")
