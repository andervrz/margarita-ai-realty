# tests/unit/test_config.py
"""Tests unitarios de Settings y configuración."""

from __future__ import annotations

import os

import pytest


def test_settings_loads_with_defaults():
    """Settings carga con valores default sin .env."""
    from app.core.config import get_settings
    s = get_settings()
    assert s.app_name is not None
    assert len(s.app_name) > 0


def test_settings_app_env_valid():
    """app_env tiene valor esperado."""
    from app.core.config import get_settings
    s = get_settings()
    assert s.app_env in ("development", "production", "testing")


def test_settings_database_url_format():
    """DATABASE_URL tiene driver correcto para async."""
    from app.core.config import get_settings
    s = get_settings()
    assert "aiosqlite" in s.database_url or "asyncpg" in s.database_url


def test_settings_llm_timeout_positive():
    """LLM_TIMEOUT debe ser positivo."""
    from app.core.config import get_settings
    s = get_settings()
    assert s.llm_timeout > 0


def test_settings_max_properties_positive():
    """MAX_PROPERTIES_PER_RESPONSE debe ser positivo."""
    from app.core.config import get_settings
    s = get_settings()
    assert s.max_properties_per_response > 0


def test_settings_embedding_dims_valid():
    """EMBEDDING_DIMS debe coincidir con el modelo declarado."""
    from app.core.config import get_settings
    s = get_settings()
    assert s.embedding_dims in (384, 768)


def test_settings_qualifier_thresholds_ordered():
    """QUALIFY_THRESHOLD < BOOK_THRESHOLD."""
    from app.core.config import get_settings
    s = get_settings()
    assert s.qualifier_qualify_threshold < s.qualifier_book_threshold


def test_settings_session_ttl_positive():
    """SESSION_TTL_MINUTES > 0."""
    from app.core.config import get_settings
    s = get_settings()
    assert s.session_ttl_minutes > 0


def test_settings_visit_duration_positive():
    """DEFAULT_VISIT_DURATION_MINUTES > 0."""
    from app.core.config import get_settings
    s = get_settings()
    assert s.default_visit_duration_minutes > 0


def test_settings_snake_case_fields():
    """Campos accesibles en snake_case — no SCREAMING_SNAKE."""
    from app.core.config import get_settings
    s = get_settings()
    # Verificar campos clave en snake_case
    for field in ["app_env", "app_name", "database_url", "llm_timeout",
                  "session_ttl_minutes", "max_properties_per_response"]:
        assert hasattr(s, field), f"Settings debe tener campo '{field}'"
    # Verificar que NO existen en SCREAMING_SNAKE
    for field in ["APP_ENV", "DATABASE_URL", "LLM_TIMEOUT"]:
        assert not hasattr(s, field), f"Settings NO debe tener '{field}'"


def test_get_settings_returns_same_instance():
    """get_settings() es idempotente (cached)."""
    from app.core.config import get_settings
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
