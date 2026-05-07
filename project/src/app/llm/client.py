# src/app/llm/client.py
"""LLM Client — wrapper async de LiteLLM con retry, timeout y fallback.

Gateway unificado para múltiples providers (Groq primary, Gemini fallback).
Maneja errores de rate limit, timeout y conexión con reintentos controlados.

Principios:
  - Una sola API para todos los providers (formato OpenAI via LiteLLM)
  - Fallback automático provider → provider
  - Timeout estricto en cada llamada
  - Sin bloqueo del event loop: todo async/await
"""

from __future__ import annotations

import asyncio
from typing import Any

import litellm
from litellm import acompletion

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger()

# ── Configuración de LiteLLM ─────────────────────────────────────

# Reducir logging verbose de LiteLLM (solo errores)
litellm.set_verbose = False


# ── Excepciones de dominio ───────────────────────────────────────

class LLMError(Exception):
    """Error base del LLM layer."""
    def __init__(self, model: str, detail: str):
        self.model = model
        self.detail = detail
        super().__init__(f"[{model}] {detail}")


class LLMRateLimitError(LLMError):
    """Rate limit alcanzado en provider."""
    pass


class LLMTimeoutError(LLMError):
    """Timeout en llamada LLM."""
    pass


class LLMProviderError(LLMError):
    """Error genérico del provider (5xx, auth, etc.)."""
    pass


class LLMNoProviderAvailable(Exception):
    """Todos los providers fallaron."""
    pass


# ── Función principal ─────────────────────────────────────────────

async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    timeout: int | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1000,
    response_format: dict[str, Any] | None = None,
    retry_count: int = 2,
    **kwargs: Any,
) -> str:
    """Llama al LLM con retry, timeout y fallback automático.
    
    Args:
        messages: Lista de mensajes OpenAI-format [{"role": "...", "content": "..."}]
        model: String LiteLLM (ej: "groq/llama-3.3-70b-versatile").
               Si None, usa el modelo default del tenant/plan.
        timeout: Segundos máximos de espera. Si None, usa settings.llm_timeout.
        temperature: Creatividad (0.0-1.0). Default 0.7 para chatbot.
        max_tokens: Límite de tokens en respuesta.
        response_format: Schema para Structured Output (JSON).
        retry_count: Reintentos por provider antes de fallback.
        **kwargs: Args adicionales para LiteLLM.
    
    Returns:
        Texto de respuesta del LLM (content del assistant message).
    
    Raises:
        LLMNoProviderAvailable: Si todos los providers fallan.
    """
    settings = get_settings()
    
    # Resolver modelo y timeout
    model = model or _get_default_model()
    timeout = timeout or settings.llm_timeout
    
    # Lista de providers a intentar (primary → fallback chain)
    providers = _build_provider_chain(model, settings)
    
    last_error: Exception | None = None
    
    for provider_model in providers:
        for attempt in range(retry_count + 1):
            try:
                logger.info(
                    "llm_call_start",
                    model=provider_model,
                    attempt=attempt + 1,
                    max_attempts=retry_count + 1,
                    messages_count=len(messages),
                )
                
                response = await asyncio.wait_for(
                    acompletion(
                        model=provider_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=timeout,
                        response_format=response_format,
                        **kwargs,
                    ),
                    timeout=timeout + 5,  # Buffer para overhead de LiteLLM
                )
                
                content = response.choices[0].message.content
                
                logger.info(
                    "llm_call_success",
                    model=provider_model,
                    attempt=attempt + 1,
                    content_length=len(content) if content else 0,
                )
                
                return content or ""
                
            except asyncio.TimeoutError:
                logger.warning(
                    "llm_call_timeout",
                    model=provider_model,
                    attempt=attempt + 1,
                    timeout=timeout,
                )
                last_error = LLMTimeoutError(provider_model, f"timeout after {timeout}s")
                
            except litellm.RateLimitError as exc:
                logger.warning(
                    "llm_call_rate_limit",
                    model=provider_model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                last_error = LLMRateLimitError(provider_model, str(exc))
                
                # Backoff exponencial antes de retry
                if attempt < retry_count:
                    wait = 2 ** attempt  # 1s, 2s
                    await asyncio.sleep(wait)
                    
            except litellm.AuthenticationError as exc:
                logger.error(
                    "llm_call_auth_error",
                    model=provider_model,
                    error=str(exc),
                )
                last_error = LLMProviderError(provider_model, f"auth failed: {exc}")
                # Auth error: no reintentar, ir directo a fallback
                break
                
            except Exception as exc:
                logger.error(
                    "llm_call_error",
                    model=provider_model,
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                last_error = LLMProviderError(provider_model, str(exc))
        
        # Si falló este provider, intentar siguiente en chain
        logger.info(
            "llm_provider_failed",
            model=provider_model,
            error_type=type(last_error).__name__ if last_error else "unknown",
            next_provider=providers[providers.index(provider_model) + 1] if provider_model in providers and providers.index(provider_model) < len(providers) - 1 else "none",
        )
    
    # Todos los providers fallaron
    logger.error(
        "llm_all_providers_failed",
        attempted_models=providers,
        last_error=str(last_error) if last_error else "unknown",
    )
    raise LLMNoProviderAvailable(
        f"Todos los providers fallaron. Último error: {last_error}"
    )


# ── Helpers privados ──────────────────────────────────────────────

def _get_default_model() -> str:
    """Retorna modelo default del plan/tenant."""
    settings = get_settings()
    # V1: Pro plan usa Groq primary
    # En V2: resolver desde tenant config
    if settings.groq_api_key:
        return "groq/llama-3.3-70b-versatile"
    if settings.gemini_api_key:
        return "gemini/gemini-2.5-pro"
    return "groq/llama-3.3-70b-versatile"  # Fallback final


def _build_provider_chain(primary_model: str, settings: Any) -> list[str]:
    """Construye cadena de providers: primary → fallback_1 → ..."""
    chain = [primary_model]
    
    # Si primary es Groq, añadir Gemini fallback
    if primary_model.startswith("groq/") and settings.gemini_api_key:
        # ⚠️ VERIFICAR string exacto en docs.litellm.ai antes de deploy
        chain.append("gemini/gemini-2.5-pro")
    
    # Si primary es Gemini, añadir Groq fallback
    elif primary_model.startswith("gemini/") and settings.groq_api_key:
        chain.append("groq/llama-3.3-70b-versatile")
    
    return chain


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, patch
    
    async def _test():
        print("🔥 Smoke Test — llm/client.py")
        
        # Test 1: Excepciones de dominio
        err = LLMError("groq/test", "fail")
        assert err.model == "groq/test"
        assert "fail" in str(err)
        print("  ✅ LLMError base")
        
        err_rl = LLMRateLimitError("groq/test", "rate limit")
        assert isinstance(err_rl, LLMError)
        print("  ✅ LLMRateLimitError herencia")
        
        err_to = LLMTimeoutError("groq/test", "timeout")
        assert isinstance(err_to, LLMError)
        print("  ✅ LLMTimeoutError herencia")
        
        err_pr = LLMProviderError("groq/test", "provider down")
        assert isinstance(err_pr, LLMError)
        print("  ✅ LLMProviderError herencia")
        
        err_np = LLMNoProviderAvailable("all failed")
        assert "all failed" in str(err_np)
        print("  ✅ LLMNoProviderAvailable")
        
        # Test 2: _get_default_model con Groq key
        with patch.object(get_settings(), "groq_api_key", "test-key"):
            with patch.object(get_settings(), "gemini_api_key", ""):
                model = _get_default_model()
                assert model == "groq/llama-3.3-70b-versatile"
                print("  ✅ Default model: Groq")
        
        # Test 3: _get_default_model con solo Gemini
        with patch.object(get_settings(), "groq_api_key", ""):
            with patch.object(get_settings(), "gemini_api_key", "test-key"):
                model = _get_default_model()
                assert model == "gemini/gemini-2.5-pro"
                print("  ✅ Default model: Gemini fallback")
        
        # Test 4: _build_provider_chain Groq → Gemini
        settings_mock = type("S", (), {"gemini_api_key": "test", "groq_api_key": "test"})()
        chain = _build_provider_chain("groq/llama-3.3-70b-versatile", settings_mock)
        assert len(chain) == 2
        assert chain[0].startswith("groq/")
        assert chain[1].startswith("gemini/")
        print("  ✅ Provider chain: Groq → Gemini")
        
        # Test 5: _build_provider_chain Gemini → Groq
        chain2 = _build_provider_chain("gemini/gemini-2.5-pro", settings_mock)
        assert len(chain2) == 2
        assert chain2[0].startswith("gemini/")
        assert chain2[1].startswith("groq/")
        print("  ✅ Provider chain: Gemini → Groq")
        
        # Test 6: Chain sin fallback (sin API key alternativa)
        settings_empty = type("S", (), {"gemini_api_key": "", "groq_api_key": "test"})()
        chain3 = _build_provider_chain("groq/llama-3.3-70b-versatile", settings_empty)
        assert len(chain3) == 1
        print("  ✅ Provider chain: solo primary (sin fallback)")
        
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())