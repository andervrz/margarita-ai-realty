# project/src/app/exceptions.py
"""Excepciones custom del dominio — jerarquía unificada de errores.

Todas las excepciones heredan de DomainError para facilitar el catch
unificado en los endpoints. Cada excepción lleva:
    - status_code: HTTP status asociado
    - detail: mensaje legible para el usuario
    - context: dict con metadata para logs (tenant_id, session_id, etc.)

Jerarquía:
    DomainError (base)
    ├── LLMError              → Errores de LiteLLM / providers
    │   ├── LLMRateLimitError   → 429, retry después
    │   ├── LLMTimeoutError     → 504, timeout de provider
    │   └── LLMContentError     → 500, respuesta malformada
    ├── SearchError           → Errores de búsqueda
    │   ├── VectorSearchError   → sqlite-vec falló
    │   └── FilterError         → Filtros inválidos
    ├── IngestionError        → Errores de CSV upload
    │   ├── ParseError          → CSV malformado
    │   └── ValidationError     → Filas inválidas
    ├── CalendarError         → Google Calendar API
    ├── NotificationError     → WhatsApp / Email
    │   ├── WhatsAppError
    │   └── EmailError
    ├── BookingError          → Flujo de agendamiento
    └── SecurityError         → Auth / permisos

Uso en endpoints:
    try:
        result = await chat_engine.process(...)
    except LLMRateLimitError as exc:
        # Fallback a Gemini automático
        result = await fallback_llm(...)
    except DomainError as exc:
        # Cualquier error de dominio → HTTP response estructurada
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
"""

from __future__ import annotations

from typing import Any


# ── Base Domain Error ──────────────────────────────────────────────

class DomainError(Exception):
    """Excepción base para todos los errores del dominio.
    
    Attributes:
        status_code: Código HTTP recomendado para la respuesta
        detail: Mensaje legible para el usuario final
        context: Metadata adicional para logs estructurados
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
        """Serializa el error para respuestas HTTP JSON."""
        return {
            "error": {
                "type": self.__class__.__name__,
                "detail": self.detail,
                "status_code": self.status_code,
            }
        }

    def log_context(self) -> dict[str, Any]:
        """Retorna contexto para structlog."""
        return {
            "error_type": self.__class__.__name__,
            "error_detail": self.detail,
            "status_code": self.status_code,
            **self.context,
        }


# ── LLM Errors ───────────────────────────────────────────────────

class LLMError(DomainError):
    """Error genérico del layer LLM."""
    status_code = 500
    default_detail = "Error en el servicio de lenguaje"


class LLMRateLimitError(LLMError):
    """Provider respondió 429 o rate limit."""
    status_code = 429
    default_detail = "Demasiadas solicitudes al servicio de IA. Por favor, intenta de nuevo en unos segundos."

    def __init__(
        self,
        model: str | None = None,
        retry_after: int | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["model"] = model
        context["retry_after"] = retry_after
        super().__init__(context=context, **kwargs)


class LLMTimeoutError(LLMError):
    """Provider no respondió dentro del timeout."""
    status_code = 504
    default_detail = "El servicio de IA tardó demasiado en responder. Intenta de nuevo."

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
    """Respuesta del LLM malformada o vacía."""
    status_code = 500
    default_detail = "El servicio de IA retornó una respuesta inválida."


# ── Search Errors ──────────────────────────────────────────────────

class SearchError(DomainError):
    """Error genérico del motor de búsqueda."""
    status_code = 500
    default_detail = "Error en la búsqueda de propiedades"


class VectorSearchError(SearchError):
    """sqlite-vec falló o no está disponible."""
    status_code = 500
    default_detail = "El servicio de búsqueda semántica no está disponible. Intenta con términos más específicos."


class FilterError(SearchError):
    """Filtros extraídos son inválidos o incompatibles."""
    status_code = 400
    default_detail = "Los filtros de búsqueda son inválidos. Revisa tu consulta."


# ── Ingestion Errors ───────────────────────────────────────────────

class IngestionError(DomainError):
    """Error genérico del pipeline de ingestion."""
    status_code = 500
    default_detail = "Error procesando el archivo de propiedades"


class ParseError(IngestionError):
    """CSV/Excel no se pudo parsear."""
    status_code = 400
    default_detail = "El archivo no tiene formato válido. Asegúrate de que sea CSV con headers."


class ValidationError(IngestionError):
    """Filas del CSV no pasaron validación."""
    status_code = 400
    default_detail = "Algunas filas del archivo contienen datos inválidos."

    def __init__(
        self,
        row_errors: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["row_errors"] = row_errors or []
        super().__init__(context=context, **kwargs)


# ── Calendar Errors ────────────────────────────────────────────────

class CalendarError(DomainError):
    """Google Calendar API falló."""
    status_code = 500
    default_detail = "No se pudo agendar la visita en el calendario. El agente te contactará manualmente."


# ── Notification Errors ────────────────────────────────────────────

class NotificationError(DomainError):
    """Error genérico de notificación."""
    status_code = 500
    default_detail = "Error enviando notificación al agente"


class WhatsAppError(NotificationError):
    """Meta WhatsApp Cloud API falló."""
    status_code = 502
    default_detail = "No se pudo enviar la notificación por WhatsApp. El agente será notificado por email."

    def __init__(
        self,
        phone_number: str | None = None,
        api_status: int | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["phone_number"] = phone_number
        context["api_status"] = api_status
        super().__init__(context=context, **kwargs)


class EmailError(NotificationError):
    """SMTP / aiosmtplib falló."""
    status_code = 502
    default_detail = "No se pudo enviar el email al agente."


# ── Booking Errors ─────────────────────────────────────────────────

class BookingError(DomainError):
    """Error en el flujo de agendamiento de visita."""
    status_code = 400
    default_detail = "Error en el proceso de agendamiento"


class BookingValidationError(BookingError):
    """Datos del lead no pasaron validación (fecha pasada, teléfono inválido, etc.)."""
    status_code = 400
    default_detail = "Los datos de la visita no son válidos. Revisa la fecha y hora."

    def __init__(
        self,
        field: str | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["invalid_field"] = field
        super().__init__(context=context, **kwargs)


# ── Security Errors ────────────────────────────────────────────────

class SecurityError(DomainError):
    """Error de autenticación o autorización."""
    status_code = 401
    default_detail = "No autorizado"


class TenantNotFoundError(SecurityError):
    """API key no corresponde a ningún tenant."""
    status_code = 401
    default_detail = "API key inválida. Verifica tu configuración."


class OriginNotAllowedError(SecurityError):
    """Origin del request no está en allowed_origins del tenant."""
    status_code = 403
    default_detail = "Este dominio no está autorizado para usar el chatbot."


class RateLimitExceededError(SecurityError):
    """Límite de requests excedido."""
    status_code = 429
    default_detail = "Demasiadas solicitudes. Por favor, espera un momento."

    def __init__(
        self,
        limit: int | None = None,
        window: int | None = None,
        **kwargs: Any,
    ) -> None:
        context = kwargs.pop("context", {})
        context["rate_limit"] = limit
        context["window_seconds"] = window
        super().__init__(context=context, **kwargs)


# ── Global Exception Handler (para main.py) ───────────────────────

async def domain_exception_handler(request, exc: DomainError):
    """Handler global para DomainError en FastAPI.
    
    Se registra en main.py con:
        app.add_exception_handler(DomainError, domain_exception_handler)
    
    Convierte cualquier DomainError en una respuesta JSON estructurada
    con el status_code correcto y loggea el contexto.
    """
    from fastapi.responses import JSONResponse
    from app.core.logging import logger

    logger.error(
        "domain_error",
        **exc.log_context(),
        path=request.url.path,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — app/exceptions.py")

    # Test 1: DomainError base
    err = DomainError(detail="Algo falló", context={"tenant_id": "t-001"})
    assert err.status_code == 500
    assert err.detail == "Algo falló"
    assert err.context["tenant_id"] == "t-001"
    print("  ✅ DomainError base funciona")

    # Test 2: to_dict serializa correctamente
    d = err.to_dict()
    assert d["error"]["type"] == "DomainError"
    assert d["error"]["status_code"] == 500
    print("  ✅ to_dict serializa correctamente")

    # Test 3: log_context incluye metadata
    ctx = err.log_context()
    assert "error_type" in ctx
    assert ctx["tenant_id"] == "t-001"
    print("  ✅ log_context incluye metadata")

    # Test 4: LLMRateLimitError con retry info
    llm_err = LLMRateLimitError(model="groq/llama-3.3-70b", retry_after=30)
    assert llm_err.status_code == 429
    assert llm_err.context["model"] == "groq/llama-3.3-70b"
    assert llm_err.context["retry_after"] == 30
    print("  ✅ LLMRateLimitError con contexto específico")

    # Test 5: LLMTimeoutError
    timeout_err = LLMTimeoutError(model="gemini-2.5-pro", timeout=30)
    assert timeout_err.status_code == 504
    assert timeout_err.context["timeout_seconds"] == 30
    print("  ✅ LLMTimeoutError con timeout info")

    # Test 6: ValidationError con row_errors
    val_err = ValidationError(
        detail="Fila 3 inválida",
        row_errors=[{"row": 3, "field": "price_usd", "error": "must be number"}],
    )
    assert val_err.status_code == 400
    assert len(val_err.context["row_errors"]) == 1
    print("  ✅ ValidationError con row_errors")

    # Test 7: WhatsAppError con phone_number
    wa_err = WhatsAppError(phone_number="+584120000000", api_status=400)
    assert wa_err.status_code == 502
    assert wa_err.context["phone_number"] == "+584120000000"
    print("  ✅ WhatsAppError con phone context")

    # Test 8: BookingValidationError con field
    book_err = BookingValidationError(field="preferred_date")
    assert book_err.status_code == 400
    assert book_err.context["invalid_field"] == "preferred_date"
    print("  ✅ BookingValidationError con field context")

    # Test 9: RateLimitExceededError
    rate_err = RateLimitExceededError(limit=60, window=60)
    assert rate_err.status_code == 429
    assert rate_err.context["rate_limit"] == 60
    print("  ✅ RateLimitExceededError con limit info")

    # Test 10: Jerarquía de herencia
    assert issubclass(LLMRateLimitError, LLMError)
    assert issubclass(LLMError, DomainError)
    assert issubclass(WhatsAppError, NotificationError)
    assert issubclass(NotificationError, DomainError)
    print("  ✅ Jerarquía de herencia correcta")

    # Test 11: domain_exception_handler es callable
    assert callable(domain_exception_handler)
    print("  ✅ domain_exception_handler es callable")

    print("\n🎉 Todos los smoke tests pasaron")
    print("   Nota: domain_exception_handler requiere FastAPI app para test de integración")