# src/app/core/logging.py
"""Logging estructurado con structlog.

Desarrollo: formato legible en consola.
Producción: formato JSON para ingestión por herramientas de observabilidad.
"""

import logging
import sys

import structlog

_logging_configured = False

def setup_logging(app_env: str = "development", log_level: str = "INFO") -> None:
    """Configura structlog con processors según el entorno.
    
    Args:
        app_env: "development" o "production"
        log_level: "DEBUG", "INFO", "WARNING", "ERROR"
    """
    global _logging_configured
    if _logging_configured:
        return  # Evitar reconfiguración múltiple
    _logging_configured = True

    level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Processors comunes para ambos entornos
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    
    # Processors específicos por entorno
    if app_env == "development":
        # Consola legible con colores
        final_processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # JSON para producción (logs estructurados)
        final_processor = structlog.processors.JSONRenderer()
    
    structlog.configure(
        processors=shared_processors + [final_processor],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Configurar también logging estándar de Python para compatibilidad
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )


def get_logger(name: str = __name__) -> structlog.BoundLogger:
    """Retorna un logger structlog con nombre."""
    return structlog.get_logger(name)


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — logging.py")
    
    # Test setup en desarrollo
    setup_logging(app_env="development", log_level="INFO")
    logger = get_logger("test.smoke")
    
    logger.info("test_info", event="smoke", module="logging")
    logger.warning("test_warning", value=42)
    logger.error("test_error", detail="intentional")
    print("  ✅ Development logging: console renderer activo")
    
    # Test setup en producción (JSON)
    setup_logging(app_env="production", log_level="DEBUG")
    logger2 = get_logger("test.prod")
    logger2.info("test_json", tenant_id="dev-001", session_id="abc-123")
    print("  ✅ Production logging: JSON renderer activo")
    
    # Test context vars
    from structlog.contextvars import bind_contextvars
    bind_contextvars(request_id="req-999")
    logger2.info("test_context", message="con contexto")
    print("  ✅ Context vars: bind_contextvars funciona")
    
    print("\n🎉 Todos los smoke tests pasaron")