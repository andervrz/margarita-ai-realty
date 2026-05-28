# src/app/llm/router.py
"""LLM Router — selección de modelo por tenant/plan.

Responsabilidades:
  1. Resolver modelo primary según plan del tenant (Pro, Standard, Basic)
  2. Construir cadena de fallback según API keys disponibles
  3. Permitir override por tenant (campo llm_model en tabla tenants)
  4. Validar en startup que los models strings tienen API keys

Principios:
  - Config over code: modelos en PLAN_MODELS, no hardcodeados en lógica
  - Fail fast: validar al startup, no al runtime
  - Simple en V1: 2 providers (Groq primary, Gemini fallback)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.app.core.config import get_settings
from src.app.core.logging import get_logger

logger = get_logger(__name__)


# ── Modelos por Plan ──────────────────────────────────────────────
# ⚠️ VERIFICAR strings exactos en: https://docs.litellm.ai/docs/providers
# Antes de primer deploy en producción

PLAN_MODELS: dict[str, dict[str, str | None]] = {
    "pro": {
        "primary": "groq/llama-3.3-70b-versatile",
        "fallback_1": "gemini/gemini-2.5-pro",  # ← verificar string en LiteLLM registry
    },
    "standard": {
        "primary": "groq/llama-3.3-70b-versatile",
        "fallback_1": "gemini/gemini-2.0-flash",  # ← verificar string en LiteLLM registry
    },
    "basic": {
        "primary": "groq/llama-3.3-70b-versatile",
        "fallback_1": None,  # Sin fallback en plan básico
    },
}


# ── Dataclass de Resultado ────────────────────────────────────────

@dataclass(frozen=True)
class ModelRoute:
    """Ruta resuelta de modelos para un tenant/plan."""
    primary: str
    fallback_1: str | None
    fallback_chain: tuple[str, ...]  # tuple para compatibilidad con frozen=True


# ── API Pública ───────────────────────────────────────────────────

def resolve_model_route(
    tenant_plan: str = "pro",
    tenant_override_model: str | None = None,
) -> ModelRoute:
    """Resuelve cadena de modelos según plan y configuración del tenant.

    Args:
        tenant_plan: Plan del tenant ("pro", "standard", "basic").
        tenant_override_model: Override desde tenants.llm_model (opcional).

    Returns:
        ModelRoute con primary + fallback chain construida.
    """
    settings = get_settings()
    plan = tenant_plan.lower()

    if plan not in PLAN_MODELS:
        logger.warning(
            "unknown_plan_using_pro",
            plan=plan,
        )
        plan = "pro"

    config = PLAN_MODELS[plan]
    primary = tenant_override_model or config["primary"]
    fallback_1 = config["fallback_1"]

    # Construir chain: solo incluir fallbacks con API key disponible
    chain: list[str] = [primary]

    if fallback_1:
        if _provider_has_key(fallback_1, settings):
            chain.append(fallback_1)
        else:
            logger.warning(
                "fallback_skipped_no_api_key",
                fallback_model=fallback_1,
                plan=plan,
            )

    route = ModelRoute(
        primary=primary,
        fallback_1=fallback_1,
        fallback_chain=tuple(chain),
    )

    logger.info(
        "model_route_resolved",
        plan=plan,
        primary=primary,
        chain=list(chain),
        fallback_count=len(chain) - 1,
    )

    return route


def get_chat_model(
    tenant_plan: str = "pro",
    tenant_override_model: str | None = None,
) -> str:
    """Retorna modelo primary para llamadas de chat.

    Shortcut cuando solo se necesita el modelo primary.
    """
    return resolve_model_route(tenant_plan, tenant_override_model).primary


def validate_plan_models() -> list[str]:
    """Valida que todos los model strings tienen API keys configuradas.

    Llamar desde lifespan de FastAPI en startup.
    Retorna lista de warnings (vacía si todo está OK).
    """
    settings = get_settings()
    warnings: list[str] = []

    for plan, config in PLAN_MODELS.items():
        primary = config["primary"]
        fallback = config["fallback_1"]

        if not _provider_has_key(primary, settings):
            warnings.append(
                f"Plan '{plan}': primary '{primary}' sin API key configurada"
            )

        if fallback and not _provider_has_key(fallback, settings):
            warnings.append(
                f"Plan '{plan}': fallback '{fallback}' sin API key — fallback deshabilitado"
            )

    if warnings:
        logger.warning(
            "plan_models_validation_warnings",
            warnings=warnings,
            count=len(warnings),
        )
    else:
        logger.info("plan_models_validation_ok", plans=list(PLAN_MODELS.keys()))

    return warnings


# ── Helpers Privados ──────────────────────────────────────────────

def _provider_has_key(model_string: str, settings: Any) -> bool:
    """Verifica si existe API key para el provider del modelo."""
    if model_string.startswith("groq/"):
        return bool(settings.groq_api_key)
    if model_string.startswith("gemini/") or model_string.startswith("google/"):
        return bool(settings.gemini_api_key)
    # V2: Mistral, Anthropic, etc.
    logger.debug("unknown_provider_in_model_string", model=model_string)
    return False


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    from types import SimpleNamespace

    print("🔥 Smoke Tests — llm/router.py\n")

    # Test 1: Plan Pro
    route = resolve_model_route("pro")
    assert route.primary == "groq/llama-3.3-70b-versatile"
    assert route.fallback_1 == "gemini/gemini-2.5-pro"
    assert isinstance(route.fallback_chain, tuple)
    print(f"✅ Pro: primary={route.primary}, fallback={route.fallback_1}")

    # Test 2: Plan Standard
    route_std = resolve_model_route("standard")
    assert route_std.primary == "groq/llama-3.3-70b-versatile"
    assert route_std.fallback_1 == "gemini/gemini-2.0-flash"
    print(f"✅ Standard: primary={route_std.primary}, fallback={route_std.fallback_1}")

    # Test 3: Plan Basic sin fallback
    route_basic = resolve_model_route("basic")
    assert route_basic.primary == "groq/llama-3.3-70b-versatile"
    assert route_basic.fallback_1 is None
    assert len(route_basic.fallback_chain) == 1
    print(f"✅ Basic: primary={route_basic.primary}, sin fallback")

    # Test 4: Tenant override del primary
    route_override = resolve_model_route(
        "pro", tenant_override_model="gemini/gemini-2.5-pro"
    )
    assert route_override.primary == "gemini/gemini-2.5-pro"
    print("✅ Tenant override aplicado correctamente")

    # Test 5: Plan desconocido → fallback a Pro
    route_unknown = resolve_model_route("enterprise")
    assert route_unknown.primary == PLAN_MODELS["pro"]["primary"]
    print("✅ Plan desconocido → fallback a Pro")

    # Test 6: get_chat_model shortcut
    model = get_chat_model("pro")
    assert model == "groq/llama-3.3-70b-versatile"
    print("✅ get_chat_model shortcut")

    # Test 7: _provider_has_key
    s_groq = SimpleNamespace(groq_api_key="key", gemini_api_key="")
    assert _provider_has_key("groq/llama-3.3-70b-versatile", s_groq) is True
    assert _provider_has_key("gemini/gemini-2.5-pro", s_groq) is False
    print("✅ _provider_has_key detecta keys correctamente")

    # Test 8: _provider_has_key para Gemini
    s_gemini = SimpleNamespace(groq_api_key="", gemini_api_key="key")
    assert _provider_has_key("gemini/gemini-2.5-pro", s_gemini) is True
    assert _provider_has_key("groq/llama-3.3-70b-versatile", s_gemini) is False
    print("✅ _provider_has_key Gemini")

    # Test 9: Chain con fallback omitido por falta de key
    from unittest.mock import patch
    with patch("src.app.llm.router.get_settings") as mock_settings:
        mock_settings.return_value = SimpleNamespace(
            groq_api_key="key",
            gemini_api_key="",  # Sin Gemini key
        )
        route_no_fallback = resolve_model_route("pro")
        assert len(route_no_fallback.fallback_chain) == 1  # solo primary
        assert route_no_fallback.fallback_chain[0].startswith("groq/")
    print("✅ Fallback omitido cuando falta API key")

    # Test 10: validate_plan_models no crashea
    warnings = validate_plan_models()
    assert isinstance(warnings, list)
    print(f"✅ validate_plan_models ejecuta sin error ({len(warnings)} warnings)")

    # Test 11: ModelRoute es inmutable (frozen=True)
    try:
        route.primary = "otro-modelo"
        assert False, "Debería ser inmutable"
    except (AttributeError, TypeError):
        pass
    print("✅ ModelRoute es inmutable (frozen=True)")

    print("\n🎉 Todos los smoke tests pasaron ✅")
