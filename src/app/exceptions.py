# src/app/exceptions.py
"""Excepciones custom del dominio — jerarquía unificada de errores.

Todas las excepciones heredan de DomainError para facilitar catch
unificado. Cada excepción lleva status_code HTTP, detail legible,
y context dict para structlog.

Jerarquía:
    DomainError (base)
    ├── LLMError
    │   ├── LLMRateLimitError    429 — retry después
    │   ├── LLMTimeoutError      504 — timeout de provider
    │   └── LLMContentError      500 — respuesta malformada
    ├── SearchError
    │   ├── VectorSearchError    500 — sqlite-vec falló
    │   └── FilterError          400 — filtros inválidos
    ├── IngestionError
    │   ├── ParseError           400 — CSV malformado
    │   └── CSVValidationError   400 — filas inválidas
    ├── CalendarError            500 — Google Calendar API
    ├── NotificationError
    │   ├── WhatsAppError        502 — Meta API falló
    │   └── EmailError           502 — SMTP falló
    ├── BookingError
    │   └── BookingValidationError  400 — datos inválidos
    └── SecurityError
        ├── TenantNotFoundError  401 — API key inválida
        ├── OriginNotAllowedError 403 — CORS origin bloqueado
        └── RateLimitExceededError 429 — rate limit

Registro en main.py:
    app.add_exception_handler(DomainError, domain_exception_handler)

NOTA: No usar el nombre 'ValidationError' — colisiona con pydantic.ValidationError.
      Se usa 'CSVValidationError' para filas inválidas del CSV.
"""

from __future__ import annotations

from typing import Any


# ── Base ──────────────────────────────────────────────────────────

class DomainError(Exception):
    """Excepción base para todos los errores del dominio.

    Attributes:
        status_code: HTTP status recomendado para la respuesta.
        detail:      Mensaje legible para el usuario final.
        context:     Metadata para structlog (tenant_id, etc.).
    """

    status_code: int = 500
    default_detail: str = "Error interno del servidor"

    def __init__(
        self,
        detail: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail or self.default_detail
        self.context = context or {}
        super().__init__(self.detail)

    def to_dict(self) -> dict[str, Any]:
        """Serializa para respuestas HTTP JSON."""
        return {
            "error": {
                "type": self.__class__.__name__,
                "detail": self.detail,
                "status_code": self.status_code,
            }
        }

    def log_context(self) -> dict[str, Any]:
        """Retorna contexto completo para structlog."""
        return {
            "error_type": self.__class__.__name__,
            "error_detail": self.detail,
            "status_code": self.status_code,
            **self.context,
        }


# ── LLM Errors ────────────────────────────────────────────────────

class LLMError(DomainError):
    """Error genérico del layer LLM."""
    status_code = 500
    default_detail = "Error en el servicio de lenguaje"


class LLMRateLimitError(LLMError):
    """Provider respondió 429 — rate limit alcanzado."""
    status_code = 429
    default_detail = (
        "Demasiadas solicitudes al servicio de IA. "
        "Por favor, intenta de nuevo en unos segundos."
    )

    def __init__(
        self,
        model: str | None = None,
        retry_after: int | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["model"] = model
        context["retry_after_seconds"] = retry_after
        super().__init__(context=context, **kwargs)


class LLMTimeoutError(LLMError):
    """Provider no respondió dentro del timeout configurado."""
    status_code = 504
    default_detail = (
        "El servicio de IA tardó demasiado en responder. Intenta de nuevo."
    )

    def __init__(
        self,
        model: str | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["model"] = model
        context["timeout_seconds"] = timeout
        super().__init__(context=context, **kwargs)


class LLMContentError(LLMError):
    """Respuesta del LLM malformada, vacía o inesperada."""
    status_code = 500
    default_detail = "El servicio de IA retornó una respuesta inválida."


# ── Search Errors ─────────────────────────────────────────────────

class SearchError(DomainError):
    """Error genérico del motor de búsqueda."""
    status_code = 500
    default_detail = "Error en la búsqueda de propiedades"


class VectorSearchError(SearchError):
    """sqlite-vec falló o no está inicializado para el tenant."""
    status_code = 500
    default_detail = (
        "El servicio de búsqueda semántica no está disponible. "
        "Intenta con términos más específicos."
    )


class FilterError(SearchError):
    """Filtros extraídos del query son inválidos o incompatibles."""
    status_code = 400
    default_detail = "Los filtros de búsqueda son inválidos. Revisa tu consulta."


# ── Ingestion Errors ──────────────────────────────────────────────

class IngestionError(DomainError):
    """Error genérico del pipeline de ingestion de CSV."""
    status_code = 500
    default_detail = "Error procesando el archivo de propiedades"


class ParseError(IngestionError):
    """CSV/Excel no se pudo parsear (encoding, formato, headers)."""
    status_code = 400
    default_detail = (
        "El archivo no tiene formato válido. "
        "Asegúrate de que sea CSV con headers en la primera fila."
    )


class CSVValidationError(IngestionError):
    """Filas del CSV no pasaron validación de Pydantic.

    Nota: Nombre CSVValidationError para evitar colisión con pydantic.ValidationError.
    """
    status_code = 400
    default_detail = "Algunas filas del archivo contienen datos inválidos."

    def __init__(
        self,
        row_errors: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["row_errors"] = row_errors or []
        context["error_count"] = len(row_errors or [])
        super().__init__(context=context, **kwargs)


# ── Calendar Error ────────────────────────────────────────────────

class CalendarError(DomainError):
    """Google Calendar API falló al crear el evento de visita."""
    status_code = 500
    default_detail = (
        "No se pudo agendar la visita en el calendario. "
        "El agente te contactará manualmente para confirmar."
    )


# ── Notification Errors ───────────────────────────────────────────

class NotificationError(DomainError):
    """Error genérico en el canal de notificación al agente."""
    status_code = 500
    default_detail = "Error enviando notificación al agente"


class WhatsAppError(NotificationError):
    """Meta WhatsApp Cloud API falló."""
    status_code = 502
    default_detail = (
        "No se pudo enviar la notificación por WhatsApp. "
        "El agente será notificado por email."
    )

    def __init__(
        self,
        phone_number: str | None = None,
        api_status: int | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["phone_number"] = phone_number
        context["api_status_code"] = api_status
        super().__init__(context=context, **kwargs)


class EmailError(NotificationError):
    """aiosmtplib / SMTP falló al enviar el email al agente."""
    status_code = 502
    default_detail = "No se pudo enviar el email al agente."


# ── Booking Errors ────────────────────────────────────────────────

class BookingError(DomainError):
    """Error genérico en el flujo de agendamiento de visita."""
    status_code = 400
    default_detail = "Error en el proceso de agendamiento"


class BookingValidationError(BookingError):
    """Datos del lead no pasaron validación (fecha pasada, email inválido, etc.)."""
    status_code = 400
    default_detail = (
        "Los datos de la visita no son válidos. "
        "Revisa la fecha, hora y datos de contacto."
    )

    def __init__(
        self,
        field: str | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["invalid_field"] = field
        super().__init__(context=context, **kwargs)


# ── Security Errors ───────────────────────────────────────────────

class SecurityError(DomainError):
    """Error de autenticación o autorización."""
    status_code = 401
    default_detail = "No autorizado"


class TenantNotFoundError(SecurityError):
    """API key no corresponde a ningún tenant activo."""
    status_code = 401
    default_detail = "API key inválida. Verifica tu configuración."


class OriginNotAllowedError(SecurityError):
    """Origin del request no está en allowed_origins del tenant."""
    status_code = 403
    default_detail = "Este dominio no está autorizado para usar el chatbot."


class RateLimitExceededError(SecurityError):
    """Límite de requests por tenant/IP excedido."""
    status_code = 429
    default_detail = "Demasiadas solicitudes. Por favor, espera un momento."

    def __init__(
        self,
        limit: int | None = None,
        window: int | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["rate_limit_per_window"] = limit
        context["window_seconds"] = window
        super().__init__(context=context, **kwargs)


# ── Global Exception Handler ──────────────────────────────────────

async def domain_exception_handler(request: Any, exc: DomainError) -> Any:
    """Handler global para DomainError en FastAPI.

    Registrar en main.py:
        app.add_exception_handler(DomainError, domain_exception_handler)

    Convierte DomainError en JSON response con status_code correcto
    y loggea el contexto completo con structlog.
    """
    from fastapi.responses import JSONResponse

    from src.app.core.logging import get_logger

    log = get_logger(__name__)
    log.error(
        "domain_error_handled",
        path=str(request.url.path),
        **exc.log_context(),
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — app/exceptions.py\n")

    # Test 1: DomainError base
    err = DomainError(detail="Algo falló", context={"tenant_id": "t-001"})
    assert err.status_code == 500
    assert err.detail == "Algo falló"
    assert err.context["tenant_id"] == "t-001"
    assert str(err) == "Algo falló"
    print("✅ DomainError base")

    # Test 2: to_dict serializa correctamente
    d = err.to_dict()
    assert d["error"]["type"] == "DomainError"
    assert d["error"]["status_code"] == 500
    assert d["error"]["detail"] == "Algo falló"
    print("✅ to_dict serialización")

    # Test 3: log_context incluye metadata y context
    ctx = err.log_context()
    assert ctx["error_type"] == "DomainError"
    assert ctx["tenant_id"] == "t-001"
    assert ctx["status_code"] == 500
    print("✅ log_context con metadata")

    # Test 4: DomainError con default_detail
    err_default = DomainError()
    assert err_default.detail == "Error interno del servidor"
    print("✅ DomainError usa default_detail")

    # Test 5: LLMRateLimitError
    llm_err = LLMRateLimitError(model="groq/llama-3.3-70b", retry_after=30)
    assert llm_err.status_code == 429
    assert llm_err.context["model"] == "groq/llama-3.3-70b"
    assert llm_err.context["retry_after_seconds"] == 30
    assert isinstance(llm_err, LLMError)
    assert isinstance(llm_err, DomainError)
    print("✅ LLMRateLimitError con contexto y herencia")

    # Test 6: LLMTimeoutError
    timeout_err = LLMTimeoutError(model="gemini-2.5-pro", timeout=30)
    assert timeout_err.status_code == 504
    assert timeout_err.context["timeout_seconds"] == 30
    print("✅ LLMTimeoutError")

    # Test 7: CSVValidationError — nombre correcto sin colisión Pydantic
    csv_err = CSVValidationError(
        detail="3 filas inválidas",
        row_errors=[
            {"row": 3, "field": "price_usd", "error": "must be number"},
            {"row": 7, "field": "property_type", "error": "invalid type"},
        ],
    )
    assert csv_err.status_code == 400
    assert csv_err.context["error_count"] == 2
    assert len(csv_err.context["row_errors"]) == 2
    # Verificar que NO colisiona con pydantic.ValidationError
    from pydantic import ValidationError as PydanticValidationError
    assert not isinstance(csv_err, PydanticValidationError)
    print("✅ CSVValidationError sin colisión con pydantic.ValidationError")

    # Test 8: WhatsAppError
    wa_err = WhatsAppError(phone_number="+584120000000", api_status=400)
    assert wa_err.status_code == 502
    assert wa_err.context["phone_number"] == "+584120000000"
    assert wa_err.context["api_status_code"] == 400
    print("✅ WhatsAppError con contexto")

    # Test 9: BookingValidationError
    book_err = BookingValidationError(field="preferred_date")
    assert book_err.status_code == 400
    assert book_err.context["invalid_field"] == "preferred_date"
    print("✅ BookingValidationError con field")

    # Test 10: RateLimitExceededError
    rate_err = RateLimitExceededError(limit=60, window=60)
    assert rate_err.status_code == 429
    assert rate_err.context["rate_limit_per_window"] == 60
    assert rate_err.context["window_seconds"] == 60
    print("✅ RateLimitExceededError con límites")

    # Test 11: Jerarquía completa
    assert issubclass(LLMRateLimitError, LLMError)
    assert issubclass(LLMRateLimitError, DomainError)
    assert issubclass(CSVValidationError, IngestionError)
    assert issubclass(CSVValidationError, DomainError)
    assert issubclass(WhatsAppError, NotificationError)
    assert issubclass(WhatsAppError, DomainError)
    assert issubclass(TenantNotFoundError, SecurityError)
    assert issubclass(TenantNotFoundError, DomainError)
    print("✅ Jerarquía de herencia completa correcta")

    # Test 12: domain_exception_handler es callable async
    import inspect
    assert inspect.iscoroutinefunction(domain_exception_handler)
    print("✅ domain_exception_handler es async")

    print("\n🎉 Todos los smoke tests pasaron ✅")
    print("   Nota: domain_exception_handler requiere FastAPI app para test de integración")
