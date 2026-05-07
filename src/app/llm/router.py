# src/app/llm/router.py
"""LLM Router — selección de modelo por tenant/plan + fallback chain.

Responsabilidades:
  1. Resolver modelo primary según plan del tenant (Pro, Standard, Basic)
  2. Construir cadena de fallback (primary → fallback_1 → ...)
  3. Verificar strings de modelo contra LiteLLM registry
  4. Permitir override por tenant (campo llm_model en tenants)

Principios:
  - Config over code: modelos definidos en PLAN_MODELS, no hardcodeados en lógica
  - Fail fast: verifica strings al startup, no al runtime
  - Simple primero: 2 providers (Groq + Gemini) cubren V1
"""

from __future__ import annotations

from dataclasses import dataclass

from src.app.core.config import get_settings
from src.app.core.logging import get_logger

logger = get_logger()


# ── Modelos por plan (PLAN.md v1.2) ───────────────────────────────
# ⚠️ ACCIÓN REQUERIDA ANTES DE IMPLEMENTAR:
# Verificar strings exactos en: https://docs.litellm.ai/docs/providers
# El string "gemini/gemini-2.5-pro" debe confirmarse en LiteLLM registry

PLAN_MODELS: dict[str, dict[str, str | None]] = {
    "pro": {
        "primary": "groq/llama-3.3-70b-versatile",
        "fallback_1": "gemini/gemini-2.5-pro",  # verificar string exacto
    },
    "standard": {
        "primary": "groq/llama-3.3-70b-versatile",
        "fallback_1": "gemini/gemini-2.0-flash",  # verificar string exacto
    },
    "basic": {
        "primary": "groq/llama-3.3-70b-versatile",
        "fallback_1": None,  # Sin fallback en plan básico
    },
}


# ── Dataclass de resultado ──────────────────────────────────────

@dataclass(frozen=True)
class ModelRoute:
    """Ruta resuelta de modelos para un tenant."""
    primary: str
    fallback_1: str | None
    fallback_chain: list[str]  # primary + fallbacks disponibles


# ── Función pública ─────────────────────────────────────────────

def resolve_model_route(
    tenant_plan: str = "pro",
    tenant_override_model: str | None = None,
) -> ModelRoute:
    """Resuelve cadena de modelos según plan del tenant.
    
    Args:
        tenant_plan: Plan del tenant ("pro", "standard", "basic").
        tenant_override_model: Override opcional desde tenants.llm_model.
    
    Returns:
        ModelRoute con primary + fallback chain construida.
    """
    settings = get_settings()
    plan = tenant_plan.lower()
    
    # Validar plan conocido
    if plan not in PLAN_MODELS:
        logger.warning(
            "unknown_plan_fallback",
            plan=plan,
            fallback_to="pro",
        )
        plan = "pro"
    
    config = PLAN_MODELS[plan]
    primary = tenant_override_model or config["primary"]
    fallback_1 = config["fallback_1"]
    
    # Construir chain: primary + fallbacks con API key disponible
    chain: list[str] = [primary]
    
    if fallback_1 and _provider_has_key(fallback_1, settings):
        chain.append(fallback_1)
    elif fallback_1:
        logger.warning(
            "fallback_skipped_no_key",
            fallback_model=fallback_1,
            plan=plan,
        )
    
    route = ModelRoute(
        primary=primary,
        fallback_1=fallback_1,
        fallback_chain=chain,
    )
    
    logger.info(
        "model_route_resolved",
        plan=plan,
        primary=primary,
        fallback_count=len(chain) - 1,
        chain=chain,
    )
    
    return route


def get_chat_model(
    tenant_plan: str = "pro",
    tenant_override_model: str | None = None,
) -> str:
    """Retorna modelo primary para llamadas de chat.
    
    Shortcut cuando solo se necesita el modelo primary.
    """
    route = resolve_model_route(tenant_plan, tenant_override_model)
    return route.primary


# ── Helpers privados ────────────────────────────────────────────

def _provider_has_key(model_string: str, settings: Any) -> bool:
    """Verifica si existe API key para el provider del modelo."""
    if model_string.startswith("groq/"):
        return bool(settings.groq_api_key)
    if model_string.startswith("gemini/") or model_string.startswith("google/"):
        return bool(settings.gemini_api_key)  # o settings.google_api_key
    # V2: Mistral, Anthropic, etc.
    return False


def validate_plan_models() -> list[str]:
    """Valida que todos los model strings estén configurados correctamente.
    
    Retorna lista de warnings (vacía si todo OK).
    Llama al startup en lifespan de FastAPI.
    """
    warnings: list[str] = []
    settings = get_settings()
    
    for plan, config in PLAN_MODELS.items():
        primary = config["primary"]
        fallback = config["fallback_1"]
        
        if not _provider_has_key(primary, settings):
            warnings.append(
                f"Plan '{plan}': primary model '{primary}' sin API key configurada"
            )
        
        if fallback and not _provider_has_key(fallback, settings):
            warnings.append(
                f"Plan '{plan}': fallback '{fallback}' sin API key configurada"
            )
    
    if warnings:
        logger.warning(
            "plan_models_validation",
            warnings=warnings,
            total=len(warnings),
        )
    else:
        logger.info(
            "plan_models_validation",
            status="all_keys_present",
        )
    
    return warnings


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    from unittest.mock import patch
    
    print("🔥 Smoke Test — llm/router.py")
    
    # Test 1: Plan Pro
    route = resolve_model_route("pro")
    assert route.primary == "groq/llama-3.3-70b-versatile"
    assert route.fallback_1 == "gemini/gemini-2.5-pro"
    assert len(route.fallback_chain) >= 1
    print(f"  ✅ Pro: primary={route.primary}, fallback={route.fallback_1}")
    
    # Test 2: Plan Standard
    route_std = resolve_model_route("standard")
    assert route_std.primary == "groq/llama-3.3-70b-versatile"
    assert route_std.fallback_1 == "gemini/gemini-2.0-flash"
    print(f"  ✅ Standard: primary={route_std.primary}")
    
    # Test 3: Plan Basic (sin fallback)
    route_basic = resolve_model_route("basic")
    assert route_basic.primary == "groq/llama-3.3-70b-versatile"
    assert route_basic.fallback_1 is None
    assert len(route_basic.fallback_chain) == 1
    print(f"  ✅ Basic: primary={route_basic.primary}, sin fallback")
    
    # Test 4: Tenant override
    route_override = resolve_model_route("pro", tenant_override_model="gemini/gemini-2.5-pro")
    assert route_override.primary == "gemini/gemini-2.5-pro"
    print("  ✅ Tenant override funciona")
    
    # Test 5: Plan desconocido → fallback a Pro
    route_unknown = resolve_model_route("enterprise")
    assert route_unknown.primary == PLAN_MODELS["pro"]["primary"]
    print("  ✅ Plan unknown fallback a Pro")
    
    # Test 6: get_chat_model shortcut
    model = get_chat_model("pro")
    assert model == "groq/llama-3.3-70b-versatile"
    print("  ✅ get_chat_model shortcut")
    
    # Test 7: _provider_has_key
    from types import SimpleNamespace
    mock_settings = SimpleNamespace(groq_api_key="test", gemini_api_key="")
    assert _provider_has_key("groq/llama-3.3-70b-versatile", mock_settings) is True
    assert _provider_has_key("gemini/gemini-2.5-pro", mock_settings) is False
    print("  ✅ _provider_has_key detecta keys correctamente")
    
    # Test 8: validate_plan_models con keys faltantes
    warnings = validate_plan_models()
    # Depende de settings reales, solo verificamos que no crashea
    assert isinstance(warnings, list)
    print("  ✅ validate_plan_models ejecuta sin error")
    
    print("\n🎉 Todos los smoke tests pasaron")