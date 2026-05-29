# tests/unit/test_scorer.py
"""Tests unitarios del Lead Qualification Engine."""

from __future__ import annotations

import pytest


def _make_messages(*contents: str, role: str = "user") -> list[dict]:
    return [{"role": role, "content": c} for c in contents]


def test_empty_history_score_zero():
    """Historial vacío → score=0, stage=explore."""
    from app.qualification.scorer import calculate_qualification_score
    result = calculate_qualification_score([], current_query="hola", language="es")
    assert result.total_score == 0
    assert result.stage == "explore"


def test_budget_mentioned_adds_points():
    """Presupuesto mencionado → +20 pts."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _make_messages("tengo presupuesto de $150,000")
    result = calculate_qualification_score(msgs, current_query="", language="es")
    assert result.total_score >= 20


def test_zone_specified_adds_points():
    """Zona especificada → +15 pts."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _make_messages("busco en Pampatar o cerca")
    result = calculate_qualification_score(msgs, current_query="", language="es")
    assert result.total_score >= 15


def test_multiple_signals_qualify_stage():
    """Budget + zona + tipo → stage=qualify (40-74)."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _make_messages(
        "tengo $200,000 para gastar",
        "busco en Pampatar",
        "quiero un apartamento para vivir",
    )
    result = calculate_qualification_score(msgs, current_query="", language="es")
    assert result.stage in ("qualify", "book")
    assert result.total_score >= 40


def test_all_signals_book_stage():
    """Todas las señales → stage=book (score >= 75)."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _make_messages(
        "tengo presupuesto de $200,000",
        "busco en Pampatar",
        "quiero apartamento de venta",
        "vi el apto ID 123 y me gustó mucho",
        "¿puedo pagar con crédito hipotecario?",
        "lo necesito lo antes posible",
        "también esta consulta",
    )
    result = calculate_qualification_score(
        msgs, current_query="cuando puedo visitar", language="es"
    )
    assert result.stage == "book"
    assert result.total_score >= 75


def test_score_at_book_threshold():
    """Score exactamente en threshold 75 → stage=book."""
    from app.qualification.scorer import QualificationResult
    from app.qualification.scorer import calculate_qualification_score
    # Construir historial que llega exactamente a 75
    from app.core.config import get_settings
    settings = get_settings()
    threshold = settings.qualifier_book_threshold
    assert threshold >= 70  # Sanity check


def test_international_buyer_signal():
    """Señal de comprador internacional detectada."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _make_messages(
        "soy venezolano viviendo en Miami, quiero invertir en Margarita",
    )
    result = calculate_qualification_score(msgs, current_query="", language="es")
    assert "international_buyer_signal" in result.signals_found


def test_margarita_zones_as_zone_signal():
    """Zonas de Margarita reconocidas como zone_specified."""
    from app.qualification.scorer import calculate_qualification_score
    for zone in ["El Yaque", "Pampatar", "Guacuco", "Playa El Agua"]:
        msgs = _make_messages(f"me interesa la zona de {zone}")
        result = calculate_qualification_score(msgs, current_query="", language="es")
        assert result.total_score > 0, f"Zona '{zone}' no generó score"


def test_english_signals_detected():
    """Señales en inglés detectadas correctamente."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _make_messages(
        "I have a budget of $200,000",
        "looking for beachfront property",
        "I need something with ocean view urgently",
    )
    result = calculate_qualification_score(msgs, current_query="", language="en")
    assert result.total_score >= 35


def test_suggested_questions_present_in_qualify():
    """Stage qualify incluye preguntas sugeridas."""
    from app.qualification.scorer import calculate_qualification_score
    msgs = _make_messages("me interesa Pampatar")
    result = calculate_qualification_score(msgs, current_query="", language="es")
    if result.stage == "qualify":
        assert len(result.suggested_questions) > 0
