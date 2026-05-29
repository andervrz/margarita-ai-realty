# tests/unit/test_language.py
"""Tests unitarios de detección de idioma ES/EN."""

from __future__ import annotations

import pytest


def test_spanish_simple():
    """Texto español claro → es, confidence alta."""
    from app.chat.language import detect_language
    r = detect_language("busco apartamento en Pampatar con vista al mar")
    assert r.detected == "es"
    assert r.es_score > r.en_score


def test_english_simple():
    """Texto inglés claro → en, confidence alta."""
    from app.chat.language import detect_language
    r = detect_language("looking for a beachfront house with ocean view")
    assert r.detected == "en"
    assert r.en_score > r.es_score


def test_empty_text_returns_es_fallback():
    """Texto vacío → es, low confidence."""
    from app.chat.language import detect_language
    r = detect_language("")
    assert r.detected == "es"
    assert r.confidence == "low"
    assert r.total_words == 0


def test_short_text_low_confidence():
    """Texto muy corto → low confidence."""
    from app.chat.language import detect_language
    r = detect_language("Hola")
    assert r.detected == "es"
    assert r.confidence == "low"


def test_mixed_language_es_dominant():
    """Spanglish con más ES → es."""
    from app.chat.language import detect_language
    r = detect_language("Hola, busco casa en Pampatar with vista al mar")
    assert r.detected == "es"
    assert r.is_mixed is True


def test_mixed_language_en_dominant():
    """Spanglish con más EN → en."""
    from app.chat.language import detect_language
    r = detect_language("Hello, I want to buy a beachfront house, quiero ver opciones")
    assert r.detected == "en"
    assert r.is_mixed is True


def test_no_markers_returns_es_fallback():
    """Texto sin palabras de señal → es fallback."""
    from app.chat.language import detect_language
    r = detect_language("qwerty asdf zxcv")
    assert r.detected == "es"
    assert r.signal_words == 0


def test_should_switch_high_confidence_different_lang():
    """Cambio de idioma permitido solo con high confidence."""
    from app.chat.language import LanguageResult, should_switch_language
    high_en = LanguageResult(
        detected="en", confidence="high",
        es_score=1, en_score=8,
        total_words=12, signal_words=9,
    )
    assert should_switch_language("es", high_en) is True


def test_should_not_switch_medium_confidence():
    """No cambia idioma con medium confidence."""
    from app.chat.language import LanguageResult, should_switch_language
    medium_en = LanguageResult(
        detected="en", confidence="medium",
        es_score=3, en_score=5,
        total_words=10, signal_words=8,
    )
    assert should_switch_language("es", medium_en) is False


def test_should_not_switch_same_language():
    """No cambia si el idioma detectado es el mismo."""
    from app.chat.language import LanguageResult, should_switch_language
    same_es = LanguageResult(
        detected="es", confidence="high",
        es_score=8, en_score=1,
        total_words=10, signal_words=9,
    )
    assert should_switch_language("es", same_es) is False


def test_real_estate_spanish_keywords():
    """Keywords inmobiliarios en español detectados."""
    from app.chat.language import detect_language
    r = detect_language("quiero comprar apartamento con vista al mar en Margarita")
    assert r.detected == "es"
    assert r.es_score >= 6


def test_real_estate_english_keywords():
    """Keywords inmobiliarios en inglés detectados."""
    from app.chat.language import detect_language
    r = detect_language("I want to buy an apartment with ocean view")
    assert r.detected == "en"
    assert r.en_score >= 6
