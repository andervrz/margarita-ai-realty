# tests/unit/test_llm_router.py
"""Tests unitarios del router de modelos LLM."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_pro_plan_primary_model():
    """Plan pro → Groq como primary."""
    from app.llm.router import resolve_model_route
    route = resolve_model_route("pro")
    assert "groq" in route.primary.lower()


def test_pro_plan_has_fallback():
    """Plan pro → tiene fallback_1 configurado."""
    from app.llm.router import resolve_model_route
    route = resolve_model_route("pro")
    assert route.fallback_1 is not None


def test_basic_plan_no_fallback():
    """Plan basic → sin fallback."""
    from app.llm.router import resolve_model_route
    route = resolve_model_route("basic")
    assert route.fallback_1 is None
    assert len(route.fallback_chain) == 1


def test_standard_plan_primary():
    """Plan standard → Groq primary."""
    from app.llm.router import resolve_model_route
    route = resolve_model_route("standard")
    assert "groq" in route.primary.lower()


def test_unknown_plan_falls_back_to_pro():
    """Plan desconocido → fallback a Pro."""
    from app.llm.router import PLAN_MODELS, resolve_model_route
    route = resolve_model_route("enterprise_xyz")
    assert route.primary == PLAN_MODELS["pro"]["primary"]


def test_tenant_override_model():
    """Override de tenant tiene prioridad sobre plan."""
    from app.llm.router import resolve_model_route
    route = resolve_model_route("pro", tenant_override_model="gemini/gemini-2.5-pro")
    assert route.primary == "gemini/gemini-2.5-pro"


def test_get_chat_model_shortcut():
    """get_chat_model retorna string del modelo primary."""
    from app.llm.router import get_chat_model
    model = get_chat_model("pro")
    assert isinstance(model, str)
    assert "/" in model  # formato "provider/model"


def test_provider_has_key_groq():
    """_provider_has_key detecta Groq API key."""
    from app.llm.router import _provider_has_key
    s_with_key = SimpleNamespace(groq_api_key="test-key", gemini_api_key="")
    s_without  = SimpleNamespace(groq_api_key="", gemini_api_key="")
    assert _provider_has_key("groq/llama-3.3-70b-versatile", s_with_key) is True
    assert _provider_has_key("groq/llama-3.3-70b-versatile", s_without) is False


def test_provider_has_key_gemini():
    """_provider_has_key detecta Gemini API key."""
    from app.llm.router import _provider_has_key
    s = SimpleNamespace(groq_api_key="", gemini_api_key="gemini-key")
    assert _provider_has_key("gemini/gemini-2.5-pro", s) is True


def test_fallback_chain_is_tuple():
    """fallback_chain es tuple (frozen dataclass)."""
    from app.llm.router import resolve_model_route
    route = resolve_model_route("pro")
    assert isinstance(route.fallback_chain, tuple)


def test_validate_plan_models_no_crash():
    """validate_plan_models ejecuta sin error."""
    from app.llm.router import validate_plan_models
    warnings = validate_plan_models()
    assert isinstance(warnings, list)
