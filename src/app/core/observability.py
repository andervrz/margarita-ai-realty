# src/app/core/observability.py
"""Observabilidad y trazabilidad con Pydantic Logfire.

Filosofía:
    - Token-opcional: sin LOGFIRE_TOKEN no se envía nada a la nube, así que
      dev/tests/CI funcionan sin cuenta. Con token, trazas completas.
    - Auto-instrumentación: FastAPI (spans por request), httpx (las llamadas
      LLM de litellm), SQLAlchemy y asyncpg (queries a Neon).
    - Puente structlog → Logfire: los eventos structlog ya existentes
      (chat_message_processed, hybrid_search_failed, …) caen dentro de las
      trazas sin tocar el código que los emite.

Doc: https://pydantic.dev/docs/logfire/get-started
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_configured = False


def configure_observability(app=None) -> bool:
    """Configura Logfire e instrumenta la app. Idempotente.

    Args:
        app: instancia FastAPI a instrumentar (opcional).

    Returns:
        True si Logfire quedó activo (configurado sin error).
    """
    global _configured
    if _configured:
        return True

    settings = get_settings()

    try:
        import logfire
    except ImportError:
        logger.warning("logfire_not_installed", hint="uv add logfire")
        return False

    # send_to_logfire="if-token-present": solo exporta a la nube si hay token
    # (vía arg o env LOGFIRE_TOKEN). Sin token → no-op de red, sigue trazando
    # local si logfire_console=True.
    logfire.configure(
        service_name=settings.logfire_service_name,
        environment=settings.app_env,
        token=settings.logfire_token or None,
        send_to_logfire="if-token-present",
        console=False if not settings.logfire_console else None,
    )

    # ── Auto-instrumentación ──────────────────────────────────────
    if app is not None:
        _safe(lambda: logfire.instrument_fastapi(app), "fastapi")

    # httpx captura las llamadas salientes del LLM (litellm usa httpx)
    _safe(lambda: logfire.instrument_httpx(), "httpx")

    # SQLAlchemy + asyncpg → spans por query contra Neon
    from app.db.engine import engine
    _safe(lambda: logfire.instrument_sqlalchemy(engine=engine.sync_engine), "sqlalchemy")
    _safe(lambda: logfire.instrument_asyncpg(), "asyncpg")

    _configured = True
    logger.info(
        "observability_configured",
        service=settings.logfire_service_name,
        env=settings.app_env,
        cloud_export=bool(settings.logfire_token),
        console=settings.logfire_console,
    )
    return True


def _safe(fn, name: str) -> None:
    """Ejecuta una instrumentación; un fallo no debe tumbar el arranque."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — instrumentación es best-effort
        logger.warning("instrumentation_failed", target=name, error=str(exc))
