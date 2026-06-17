# test/unit/test_validator_phone.py
"""Tests de validación + normalización de teléfonos venezolanos e internacionales."""

from __future__ import annotations

import pytest

from app.leads.validator import _validate_phone_value, validate_phone


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Locales venezolanos (todos los prefijos móviles) → +58
        ("04121234567", "+584121234567"),
        ("04141234567", "+584141234567"),
        ("04161234567", "+584161234567"),
        ("04241234567", "+584241234567"),
        ("04261234567", "+584261234567"),
        ("04221234567", "+584221234567"),
        # Con espacios / guiones
        ("0414 123 4567", "+584141234567"),
        ("0414-123-4567", "+584141234567"),
        # Ya en formato VE
        ("+584141234567", "+584141234567"),
        ("+58 414 1234567", "+584141234567"),
        ("584141234567", "+584141234567"),
        # Internacional
        ("+14155551234", "+14155551234"),
    ],
)
def test_phone_normalization(raw, expected):
    assert _validate_phone_value(raw) == expected
    ok, err = validate_phone(raw)
    assert ok is True
    assert err is None


@pytest.mark.parametrize(
    "raw",
    [
        "abc",
        "123",            # muy corto
        "0212",           # incompleto
        "+5821234567",    # VE pero no móvil (no empieza en 4XX)
    ],
)
def test_phone_invalid(raw):
    ok, err = validate_phone(raw)
    assert ok is False
    assert err is not None
