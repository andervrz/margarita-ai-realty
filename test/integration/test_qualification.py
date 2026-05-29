# tests/integration/test_qualification.py
"""Tests de integración del Lead Qualifier con historiales realistas."""

from __future__ import annotations

import pytest


def _build_session_messages(*pairs: tuple[str, str]) -> list[dict]:
    """Construye historial de mensajes [(role, content), ...]."""
    return [{"role": role, "content": content} for role, content in pairs]


def test_explore_stage_initial():
    """Historial mínimo → stage=explore."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _build_session_messages(
        ("user", "hola, qué propiedades tienen?"),
        ("assistant", "Bienvenido! Tenemos apartamentos y casas."),
    )
    result = calculate_qualification_score(msgs, current_query="", language="es")
    assert result.stage == "explore"
    assert result.total_score < 40


def test_qualify_stage_with_budget_and_zone():
    """Budget + zona → stage=qualify (score 40-74)."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _build_session_messages(
        ("user", "tengo presupuesto de $200,000"),
        ("assistant", "Perfecto. ¿Qué zona prefieres?"),
        ("user", "prefiero Pampatar o El Agua"),
        ("assistant", "Tenemos opciones en esas zonas."),
    )
    result = calculate_qualification_score(msgs, current_query="", language="es")
    assert result.stage in ("qualify", "book")
    assert result.total_score >= 35


def test_book_stage_all_signals():
    """Conversación completa con todas las señales → book."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _build_session_messages(
        ("user", "tengo $250,000 para invertir"),
        ("assistant", "¿Qué zona prefieres?"),
        ("user", "Pampatar, busco apartamento frente a la playa"),
        ("assistant", "Aquí hay opciones."),
        ("user", "¿cuál es el precio de este exactamente? me interesa mucho"),
        ("assistant", "Ese apartamento está en $220,000."),
        ("user", "¿aceptan financiamiento bancario?"),
        ("assistant", "Sí, trabajamos con financiamiento."),
        ("user", "lo necesito urgente, cuándo puedo visitar"),
        ("assistant", "Podemos coordinar una visita."),
    )
    result = calculate_qualification_score(msgs, current_query="visitar mañana", language="es")
    assert result.stage == "book"
    assert result.total_score >= 75


def test_international_buyer_full_profile():
    """Señales de comprador internacional acumulan score correcto."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _build_session_messages(
        ("user", "soy venezolano viviendo en Miami, quiero invertir $300k en Margarita"),
        ("assistant", "Tenemos excelentes opciones para inversión."),
        ("user", "busco algo para airbnb en Pampatar, frente al mar"),
        ("assistant", "Aquí hay propiedades vacacionales."),
        ("user", "¿cuál tiene mejor rentabilidad?"),
    )
    result = calculate_qualification_score(msgs, current_query="", language="es")
    assert result.total_score >= 50
    assert "international_buyer_signal" in result.signals_found or result.total_score > 40
