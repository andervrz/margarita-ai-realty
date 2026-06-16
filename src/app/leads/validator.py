# src/app/leads/validator.py
"""Lead Validator — validación de datos de contacto y visita con Pydantic v2.

Responsabilidades:
  1. Validar nombre (mínimo 2 caracteres, no solo números)
  2. Validar email (formato EmailStr)
  3. Validar teléfono (formato venezolano e internacional)
  4. Validar fecha (futura, no feriados, formato ISO)
  5. Validar hora (formato HH:MM, horario comercial)
  6. Validar duración de visita (> 0, default 60 min)
  7. Sanitizar notas (longitud máxima, sin scripts)

Principios:
  - Validate early, validate hard: nada sucio entra al core
  - Mensajes de error en ES/EN según idioma de sesión
  - Reutilizable en booking flow y API admin
"""

from __future__ import annotations


import re

from pydantic import EmailStr, TypeAdapter

from app.core.logging import get_logger

logger = get_logger(__name__)
_email_adapter = TypeAdapter(EmailStr)

# ── Constantes de validación ──────────────────────────────────────

MIN_NAME_LENGTH = 2
MAX_NAME_LENGTH = 100

PHONE_REGEX_VE = re.compile(r"^\+58\s?(4\d{2})\s?(\d{7})$")  # +584141234567
PHONE_REGEX_INTL = re.compile(r"^\+\d{1,3}\s?\d{6,14}$")  # +1 4155551234, +34 612345678

# ── Funciones de validación individuales ────────────────────────

# O más simple — extraer la validación a función pura
def _validate_name_value(v: str) -> str:
    """Lógica pura de validación, reutilizable."""
    v = v.strip()
    if len(v) < MIN_NAME_LENGTH:
        raise ValueError(f"Nombre debe tener al menos {MIN_NAME_LENGTH} caracteres")
    if len(v) > MAX_NAME_LENGTH:
        raise ValueError(f"Nombre no puede exceder {MAX_NAME_LENGTH} caracteres")
    if not re.search(r"[A-Za-záéíóúñÁÉÍÓÚÑ]", v):
        raise ValueError("Nombre debe contener al menos una letra")
    return v

def validate_name(name: str, language: str = "es") -> tuple[bool, str | None]:
    try:
        _validate_name_value(name)
        return True, None
    except ValueError as e:
        msg = str(e)
        return False, f"Nombre inválido: {msg}" if language == "es" else f"Invalid name: {msg}"



def validate_email(email: str, language: str = "es") -> tuple[bool, str | None]:
    """Valida email. Retorna (is_valid, error_message)."""
    try:
        _email_adapter.validate_python(email)
        return True, None
    except Exception:
        if language == "es":
            return False, "Email inválido. Ejemplo: nombre@email.com"
        return False, "Invalid email. Example: name@email.com"


def _validate_phone_value(v: str) -> str:
    """Lógica pura de validación + normalización de teléfono, reutilizable.

    Normaliza a formato internacional E.164:
      - Local venezolano  04141234567  → +584141234567
      - VE sin '+'        584141234567 → +584141234567
      - Internacional     +14155551234 → +14155551234

    No depende de fecha/hora — a diferencia de construir un LeadValidator completo.
    """
    v = v.strip().replace(" ", "").replace("-", "")

    # Local venezolano: 0XXXXXXXXXX (11 dígitos, ej: 0414/0412/0416/0424/0426) → +58XXXXXXXXXX
    if v.isdigit() and len(v) == 11 and v.startswith("0"):
        v = "+58" + v[1:]
    # VE sin '+': 58XXXXXXXXXX → +58XXXXXXXXXX
    elif v.startswith("58") and not v.startswith("+"):
        v = "+" + v

    # Formato venezolano (móvil: +58 4XX XXXXXXX)
    if v.startswith("+58"):
        if not PHONE_REGEX_VE.match(v):
            raise ValueError(
                "Teléfono venezolano inválido. Ejemplo: 04141234567 o +584141234567"
            )
        return v

    # Formato internacional
    if not v.startswith("+"):
        v = "+" + v  # Intentar agregar +

    if not PHONE_REGEX_INTL.match(v):
        raise ValueError("Teléfono internacional inválido. Formato: +14155551234")

    return v


def validate_phone(phone: str, language: str = "es") -> tuple[bool, str | None]:
    """Valida teléfono. Retorna (is_valid, error_message).

    Valida el teléfono de forma aislada (sin fecha/hora), evitando el bug de
    construir un LeadValidator con una fecha hardcodeada que caduca.
    """
    try:
        _validate_phone_value(phone)
        return True, None
    except ValueError as e:
        msg = str(e)
        if language == "es":
            return False, f"Teléfono inválido: {msg}"
        return False, f"Invalid phone: {msg}"


# ── Funciones de sanitización ────────────────────────────────────

def sanitize_name(name: str) -> str:
    """Sanitiza nombre: quita espacios extra, capitaliza."""
    return " ".join(name.strip().split()).title()


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — leads/validator.py")

    # Test 1: Validación individual nombre
    is_valid, error = validate_name("A", "es")
    assert is_valid is False
    assert "inválido" in error
    print("  ✅ validate_name rechaza nombre corto")

    ok, _ = validate_name("María González", "es")
    assert ok is True
    print("  ✅ validate_name acepta nombre válido")

    # Test 2: Validación individual email
    is_valid, error = validate_email("bad-email", "es")
    assert is_valid is False
    print("  ✅ validate_email rechaza email inválido")

    # Test 3: Teléfono — normalización y validación
    assert _validate_phone_value("04141234567") == "+584141234567"
    ok, _ = validate_phone("+14155551234")
    assert ok is True
    ok_bad, _ = validate_phone("+581234567")
    assert ok_bad is False
    print("  ✅ validate_phone / _validate_phone_value")

    # Test 4: Sanitización nombre
    assert sanitize_name("  maría  gonzález  ") == "María González"
    print("  ✅ sanitize_name")

    print("\n🎉 Todos los smoke tests pasaron")
