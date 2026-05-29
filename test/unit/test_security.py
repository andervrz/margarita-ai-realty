# tests/unit/test_security.py
"""Tests unitarios de funciones de seguridad."""

from __future__ import annotations

import pytest


def test_hash_api_key_deterministic():
    """hash_api_key produce el mismo hash para la misma key."""
    from app.core.security import hash_api_key
    h1 = hash_api_key("test-key-12345")
    h2 = hash_api_key("test-key-12345")
    assert h1 == h2


def test_hash_api_key_length():
    """hash_api_key produce SHA-256 de 64 hex chars."""
    from app.core.security import hash_api_key
    h = hash_api_key("any-key")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_api_key_different_keys_different_hashes():
    """Claves distintas producen hashes distintos."""
    from app.core.security import hash_api_key
    h1 = hash_api_key("key-a")
    h2 = hash_api_key("key-b")
    assert h1 != h2


def test_hash_api_key_empty_string():
    """hash_api_key maneja string vacío sin error."""
    from app.core.security import hash_api_key
    h = hash_api_key("")
    assert len(h) == 64


def test_verify_api_key_correct():
    """verify_api_key retorna True con la key correcta."""
    from app.core.security import hash_api_key, verify_api_key
    raw = "secret-key-abc"
    stored_hash = hash_api_key(raw)
    assert verify_api_key(raw, stored_hash) is True


def test_verify_api_key_wrong():
    """verify_api_key retorna False con key incorrecta."""
    from app.core.security import hash_api_key, verify_api_key
    stored_hash = hash_api_key("real-key")
    assert verify_api_key("wrong-key", stored_hash) is False


def test_verify_api_key_uses_constant_time():
    """verify_api_key usa secrets.compare_digest (no ==)."""
    import inspect
    from app.core import security
    source = inspect.getsource(security.verify_api_key)
    assert "compare_digest" in source, "Debe usar secrets.compare_digest"


def test_generate_api_key_unique():
    """generate_api_key produce claves únicas cada llamada."""
    from app.core.security import generate_api_key
    keys = {generate_api_key() for _ in range(10)}
    assert len(keys) == 10


def test_generate_api_key_length():
    """generate_api_key produce clave con longitud suficiente."""
    from app.core.security import generate_api_key
    key = generate_api_key()
    assert len(key) >= 32
