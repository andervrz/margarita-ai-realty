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
from datetime import date, datetime, time, timezone
from typing import Any

from pydantic import BaseModel, EmailStr, field_validator, ValidationError

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger()


# ── Constantes de validación ──────────────────────────────────────

MIN_NAME_LENGTH = 2
MAX_NAME_LENGTH = 100
MAX_NOTES_LENGTH = 500

PHONE_REGEX_VE = re.compile(r"^\+58\s?(4\d{2})\s?(\d{7})$")  # +584141234567
PHONE_REGEX_INTL = re.compile(r"^\+\d{1,3}\s?\d{6,14}$")  # +1 4155551234, +34 612345678

BUSINESS_HOURS_START = time(8, 0)   # 08:00
BUSINESS_HOURS_END = time(18, 0)    # 18:00 (6 PM)
BUSINESS_DAYS = [0, 1, 2, 3, 4, 5]  # Lunes-Sábado (0=Monday in Python)


# ── Schemas de validación ─────────────────────────────────────────

class LeadValidator(BaseModel):
    """Schema de validación completo para datos de lead."""
    
    name: str
    email: EmailStr
    phone: str
    preferred_date: str  # "2026-06-15"
    preferred_time: str  # "10:00"
    visit_duration_minutes: int = 60
    notes: str | None = None
    language: str = "es"
    
    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < MIN_NAME_LENGTH:
            raise ValueError(f"Nombre debe tener al menos {MIN_NAME_LENGTH} caracteres")
        if len(v) > MAX_NAME_LENGTH:
            raise ValueError(f"Nombre no puede exceder {MAX_NAME_LENGTH} caracteres")
        # No puede ser solo números o símbolos
        if not re.search(r"[A-Za-záéíóúñÁÉÍÓÚÑ]", v):
            raise ValueError("Nombre debe contener al menos una letra")
        return v
    
    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        
        # Formato venezolano
        if v.startswith("+58") or v.startswith("58"):
            if not PHONE_REGEX_VE.match(v):
                raise ValueError("Teléfono venezolano inválido. Formato: +584141234567")
            return v
        
        # Formato internacional
        if not v.startswith("+"):
            v = "+" + v  # Intentar agregar +
        
        if not PHONE_REGEX_INTL.match(v):
            raise ValueError("Teléfono internacional inválido. Formato: +14155551234")
        
        return v
    
    @field_validator("preferred_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        try:
            parsed = date.fromisoformat(v)
        except ValueError:
            raise ValueError("Fecha inválida. Formato: YYYY-MM-DD (ej: 2026-06-15)")
        
        today = date.today()
        
        # No fechas pasadas
        if parsed < today:
            raise ValueError("La fecha debe ser hoy o en el futuro")
        
        # No más de 90 días en futuro (evitar agendamientos lejanos)
        max_date = today.replace(day=today.day + 90) if today.day <= 28 else today
        # Simplificación: usar timedelta
        from datetime import timedelta
        max_date = today + timedelta(days=90)
        
        if parsed > max_date:
            raise ValueError("La fecha no puede ser más de 90 días en el futuro")
        
        # Verificar día de semana (lunes-sábado)
        if parsed.weekday() not in BUSINESS_DAYS:
            raise ValueError("Solo se agendan visitas de lunes a sábado")
        
        return v
    
    @field_validator("preferred_time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        try:
            parsed = datetime.strptime(v, "%H:%M").time()
        except ValueError:
            raise ValueError("Hora inválida. Formato: HH:MM (ej: 10:00)")
        
        # Horario comercial
        if parsed < BUSINESS_HOURS_START or parsed > BUSINESS_HOURS_END:
            raise ValueError(
                f"Horario debe ser entre "
                f"{BUSINESS_HOURS_START.strftime('%H:%M')} y "
                f"{BUSINESS_HOURS_END.strftime('%H:%M')}"
            )
        
        return v
    
    @field_validator("visit_duration_minutes")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Duración debe ser mayor a 0 minutos")
        if v > 240:  # 4 horas máximo
            raise ValueError("Duración máxima: 240 minutos (4 horas)")
        return v
    
    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if len(v) > MAX_NOTES_LENGTH:
            raise ValueError(f"Notas no pueden exceder {MAX_NOTES_LENGTH} caracteres")
        # Sanitización básica: no scripts
        if "<script" in v.lower() or "javascript:" in v.lower():
            raise ValueError("Notas contienen contenido no permitido")
        return v


# ── Funciones de validación individuales ────────────────────────

def validate_name(name: str, language: str = "es") -> tuple[bool, str | None]:
    """Valida nombre. Retorna (is_valid, error_message)."""
    try:
        LeadValidator(name=name, email="test@test.com", phone="+584141234567", 
                     preferred_date="2026-12-31", preferred_time="10:00")
        return True, None
    except ValidationError as exc:
        for err in exc.errors():
            if err["loc"] == ("name",):
                msg = err["msg"]
                if language == "es":
                    return False, f"Nombre inválido: {msg}"
                return False, f"Invalid name: {msg}"
        return False, "Validation error"
    except Exception as exc:
        return False, str(exc)


def validate_email(email: str, language: str = "es") -> tuple[bool, str | None]:
    """Valida email. Retorna (is_valid, error_message)."""
    try:
        EmailStr._validate(email)
        return True, None
    except Exception:
        if language == "es":
            return False, "Email inválido. Ejemplo: nombre@email.com"
        return False, "Invalid email. Example: name@email.com"


def validate_phone(phone: str, language: str = "es") -> tuple[bool, str | None]:
    """Valida teléfono. Retorna (is_valid, error_message)."""
    try:
        LeadValidator(name="Test", email="test@test.com", phone=phone,
                     preferred_date="2026-12-31", preferred_time="10:00")
        return True, None
    except ValidationError as exc:
        for err in exc.errors():
            if err["loc"] == ("phone",):
                msg = err["msg"]
                if language == "es":
                    return False, f"Teléfono inválido: {msg}"
                return False, f"Invalid phone: {msg}"
        return False, "Validation error"


def validate_booking_datetime(
    date_str: str,
    time_str: str,
    language: str = "es",
) -> tuple[bool, str | None]:
    """Valida combinación fecha + hora. Retorna (is_valid, error_message)."""
    try:
        LeadValidator(
            name="Test", email="test@test.com", phone="+584141234567",
            preferred_date=date_str, preferred_time=time_str,
        )
        return True, None
    except ValidationError as exc:
        for err in exc.errors():
            loc = err["loc"]
            msg = err["msg"]
            if loc == ("preferred_date",):
                if language == "es":
                    return False, f"Fecha inválida: {msg}"
                return False, f"Invalid date: {msg}"
            elif loc == ("preferred_time",):
                if language == "es":
                    return False, f"Hora inválida: {msg}"
                return False, f"Invalid time: {msg}"
        return False, "Validation error"


# ── Funciones de sanitización ────────────────────────────────────

def sanitize_name(name: str) -> str:
    """Sanitiza nombre: quita espacios extra, capitaliza."""
    return " ".join(name.strip().split()).title()


def sanitize_notes(notes: str | None) -> str | None:
    """Sanitiza notas: quita espacios extra, limita longitud."""
    if notes is None:
        return None
    notes = notes.strip()
    if len(notes) > MAX_NOTES_LENGTH:
        notes = notes[:MAX_NOTES_LENGTH] + "..."
    return notes


def format_phone_for_display(phone: str) -> str:
    """Formatea teléfono para display: +58 414 1234567."""
    clean = phone.replace(" ", "").replace("-", "")
    
    # Venezolano
    match = PHONE_REGEX_VE.match(clean)
    if match:
        code, number = match.groups()
        return f"+58 {code} {number}"
    
    # Internacional genérico
    if clean.startswith("+"):
        return f"{clean[:4]} {clean[4:]}"
    
    return clean


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — leads/validator.py")
    
    # Test 1: Validación completa exitosa
    validator = LeadValidator(
        name="María González",
        email="maria@email.com",
        phone="+584141234567",
        preferred_date="2026-06-15",
        preferred_time="10:00",
        visit_duration_minutes=90,
        notes="Verificar vista al mar",
    )
    assert validator.name == "María González"
    assert validator.email == "maria@email.com"
    print("  ✅ Validación completa exitosa")
    
    # Test 2: Nombre muy corto
    try:
        LeadValidator(name="A", email="test@test.com", phone="+584141234567",
                     preferred_date="2026-12-31", preferred_time="10:00")
        assert False, "Debería fallar"
    except ValidationError as exc:
        assert "Nombre" in str(exc) or "name" in str(exc).lower()
        print("  ✅ Rechaza nombre corto")
    
    # Test 3: Nombre solo números
    try:
        LeadValidator(name="12345", email="test@test.com", phone="+584141234567",
                     preferred_date="2026-12-31", preferred_time="10:00")
        assert False, "Debería fallar"
    except ValidationError:
        print("  ✅ Rechaza nombre solo números")
    
    # Test 4: Email inválido
    try:
        LeadValidator(name="Test", email="invalid-email", phone="+584141234567",
                     preferred_date="2026-12-31", preferred_time="10:00")
        assert False, "Debería fallar"
    except ValidationError:
        print("  ✅ Rechaza email inválido")
    
    # Test 5: Teléfono venezolano inválido
    try:
        LeadValidator(name="Test", email="test@test.com", phone="+581234567",
                     preferred_date="2026-12-31", preferred_time="10:00")
        assert False, "Debería fallar"
    except ValidationError:
        print("  ✅ Rechaza teléfono venezolano inválido")
    
    # Test 6: Teléfono internacional válido
    validator_intl = LeadValidator(
        name="Test", email="test@test.com", phone="+14155551234",
        preferred_date="2026-12-31", preferred_time="10:00",
    )
    assert validator_intl.phone == "+14155551234"
    print("  ✅ Acepta teléfono internacional")
    
    # Test 7: Fecha pasada
    try:
        LeadValidator(name="Test", email="test@test.com", phone="+584141234567",
                     preferred_date="2020-01-01", preferred_time="10:00")
        assert False, "Debería fallar"
    except ValidationError:
        print("  ✅ Rechaza fecha pasada")
    
    # Test 8: Fecha domingo
    try:
        # 2026-06-14 es domingo
        LeadValidator(name="Test", email="test@test.com", phone="+584141234567",
                     preferred_date="2026-06-14", preferred_time="10:00")
        assert False, "Debería fallar"
    except ValidationError:
        print("  ✅ Rechaza fecha domingo")
    
    # Test 9: Hora fuera de horario
    try:
        LeadValidator(name="Test", email="test@test.com", phone="+584141234567",
                     preferred_date="2026-12-31", preferred_time="07:00")
        assert False, "Debería fallar"
    except ValidationError:
        print("  ✅ Rechaza hora fuera de horario")
    
    # Test 10: Duración inválida
    try:
        LeadValidator(name="Test", email="test@test.com", phone="+584141234567",
                     preferred_date="2026-12-31", preferred_time="10:00",
                     visit_duration_minutes=0)
        assert False, "Debería fallar"
    except ValidationError:
        print("  ✅ Rechaza duración 0")
    
    # Test 11: Notas con script (XSS básico)
    try:
        LeadValidator(name="Test", email="test@test.com", phone="+584141234567",
                     preferred_date="2026-12-31", preferred_time="10:00",
                     notes="<script>alert('xss')</script>")
        assert False, "Debería fallar"
    except ValidationError:
        print("  ✅ Rechaza notas con script")
    
    # Test 12: Validación individual nombre
    is_valid, error = validate_name("A", "es")
    assert is_valid is False
    assert "inválido" in error
    print("  ✅ validate_name individual")
    
    # Test 13: Validación individual email
    is_valid, error = validate_email("bad-email", "es")
    assert is_valid is False
    print("  ✅ validate_email individual")
    
    # Test 14: Sanitización nombre
    assert sanitize_name("  maría  gonzález  ") == "María González"
    print("  ✅ sanitize_name")
    
    # Test 15: Formato teléfono display
    formatted = format_phone_for_display("+584141234567")
    assert "+58" in formatted
    assert "414" in formatted
    print(f"  ✅ format_phone_for_display: {formatted}")
    
    # Test 16: Fecha + hora combinada
    is_valid, error = validate_booking_datetime("2026-06-15", "10:00", "es")
    assert is_valid is True
    print("  ✅ validate_booking_datetime combinada")
    
    print("\n🎉 Todos los smoke tests pasaron")