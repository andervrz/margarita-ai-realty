# src/app/llm/client.py
"""LLM Client — wrapper async de LiteLLM con retry, timeout y fallback.

Gateway unificado para Groq (primary) y Gemini (fallback).
Maneja rate limits, timeouts y errores de conexión con reintentos controlados.

Principios:
  - Una sola API para todos los providers (formato OpenAI via LiteLLM)
  - Fallback automático Groq → Gemini cuando Groq falla
  - Timeout estricto en cada llamada — no bloquea el event loop
  - Backoff exponencial en rate limits
"""

from __future__ import annotations

import asyncio
from typing import Any

import litellm
from litellm import acompletion

from src.app.core.config import get_settings
from src.app.core.logging import get_logger

logger = get_logger(__name__)

# Reducir logging verbose de LiteLLM — solo errores críticos
litellm.set_verbose = False


# ── Excepciones de Dominio ────────────────────────────────────────

class LLMError(Exception):
    """Error base del LLM layer."""
    def __init__(self, model: str, detail: str):
        self.model = model
        self.detail = detail
        super().__init__(f"[{model}] {detail}")


class LLMRateLimitError(LLMError):
    """Rate limit alcanzado en el provider."""
    pass


class LLMTimeoutError(LLMError):
    """Timeout en llamada al LLM."""
    pass


class LLMProviderError(LLMError):
    """Error genérico del provider (5xx, auth, conexión, etc.)."""
    pass


class LLMNoProviderAvailable(Exception):
    """Todos los providers del chain fallaron."""
    pass


# ── Función Principal ─────────────────────────────────────────────

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
        messages: Lista de mensajes OpenAI-format.
        model: String LiteLLM (ej: "groq/llama-3.3-70b-versatile").
               Si None, usa el modelo default según API keys disponibles.
        timeout: Segundos máximos. Si None, usa settings.llm_timeout.
        temperature: Creatividad 0.0-1.0. Default 0.7 para chatbot.
        max_tokens: Límite de tokens en respuesta.
        response_format: Schema para Structured Output (JSON).
                         Solo se pasa a LiteLLM si no es None.
        retry_count: Reintentos por provider antes de pasar al fallback.
        **kwargs: Args adicionales para LiteLLM.

    Returns:
        Texto de respuesta del LLM.

    Raises:
        LLMNoProviderAvailable: Si todos los providers del chain fallan.
    """
    settings = get_settings()

    effective_model = model or _get_default_model(settings)
    effective_timeout = timeout or settings.llm_timeout

    providers = _build_provider_chain(effective_model, settings)

    last_error: Exception | None = None

    for provider_idx, provider_model in enumerate(providers):
        is_last_provider = provider_idx == len(providers) - 1

        for attempt in range(retry_count + 1):
            try:
                logger.info(
                    "llm_call_start",
                    model=provider_model,
                    attempt=attempt + 1,
                    max_attempts=retry_count + 1,
                    messages_count=len(messages),
                )

                # Construir kwargs para LiteLLM
                # response_format solo se incluye si no es None
                # — algunos providers rechazan el parámetro cuando es None
                call_kwargs: dict[str, Any] = {
                    "model": provider_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout": effective_timeout,
                    **kwargs,
                }
                if response_format is not None:
                    call_kwargs["response_format"] = response_format

                response = await asyncio.wait_for(
                    acompletion(**call_kwargs),
                    timeout=effective_timeout + 5,  # Buffer para overhead de LiteLLM
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
                    timeout=effective_timeout,
                )
                last_error = LLMTimeoutError(
                    provider_model, f"timeout after {effective_timeout}s"
                )

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
                    wait_seconds = 2 ** attempt  # 1s, 2s
                    logger.info(
                        "llm_rate_limit_backoff",
                        model=provider_model,
                        wait_seconds=wait_seconds,
                    )
                    await asyncio.sleep(wait_seconds)

            except litellm.AuthenticationError as exc:
                logger.error(
                    "llm_call_auth_error",
                    model=provider_model,
                    error=str(exc),
                )
                last_error = LLMProviderError(provider_model, f"auth failed: {exc}")
                # Auth error: no tiene sentido reintentar el mismo provider
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

        # Provider agotó sus intentos
        if not is_last_provider:
            next_model = providers[provider_idx + 1]
            logger.info(
                "llm_provider_failed_trying_next",
                failed_model=provider_model,
                next_model=next_model,
                error_type=type(last_error).__name__ if last_error else "unknown",
            )
        else:
            logger.error(
                "llm_last_provider_failed",
                failed_model=provider_model,
                error_type=type(last_error).__name__ if last_error else "unknown",
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


# ── Helpers Privados ──────────────────────────────────────────────

def _get_default_model(settings: Any) -> str:
    """Retorna modelo default según API keys disponibles."""
    if settings.groq_api_key:
        return "groq/llama-3.3-70b-versatile"
    if settings.gemini_api_key:
        return "gemini/gemini-2.5-pro"
    # Fallback final — fallará en runtime si no hay key, pero al menos es predecible
    return "groq/llama-3.3-70b-versatile"


def _build_provider_chain(primary_model: str, settings: Any) -> list[str]:
    """Construye cadena de providers: primary → fallback si hay API key."""
    chain = [primary_model]

    # Groq como primary → Gemini como fallback
    if primary_model.startswith("groq/") and settings.gemini_api_key:
        chain.append("gemini/gemini-2.5-pro")

    # Gemini como primary → Groq como fallback
    elif primary_model.startswith("gemini/") and settings.groq_api_key:
        chain.append("groq/llama-3.3-70b-versatile")

    return chain


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    async def _test():
        print("🔥 Smoke Tests — llm/client.py\n")

        # Test 1: Jerarquía de excepciones
        err = LLMError("groq/test", "fallo")
        assert err.model == "groq/test"
        assert "fallo" in str(err)
        assert "[groq/test] fallo" == str(err)
        print("✅ LLMError base con formato correcto")

        assert isinstance(LLMRateLimitError("m", "r"), LLMError)
        assert isinstance(LLMTimeoutError("m", "t"), LLMError)
        assert isinstance(LLMProviderError("m", "p"), LLMError)
        print("✅ Jerarquía de excepciones correcta")

        err_np = LLMNoProviderAvailable("todos fallaron")
        assert "todos fallaron" in str(err_np)
        print("✅ LLMNoProviderAvailable")

        # Test 2: _get_default_model con Groq
        s_groq = SimpleNamespace(groq_api_key="key", gemini_api_key="")
        assert _get_default_model(s_groq) == "groq/llama-3.3-70b-versatile"
        print("✅ Default model: Groq cuando hay GROQ_API_KEY")

        # Test 3: _get_default_model fallback a Gemini
        s_gemini = SimpleNamespace(groq_api_key="", gemini_api_key="key")
        assert _get_default_model(s_gemini) == "gemini/gemini-2.5-pro"
        print("✅ Default model: Gemini cuando solo hay GEMINI_API_KEY")

        # Test 4: Provider chain Groq → Gemini
        s_both = SimpleNamespace(groq_api_key="key", gemini_api_key="key")
        chain = _build_provider_chain("groq/llama-3.3-70b-versatile", s_both)
        assert len(chain) == 2
        assert chain[0].startswith("groq/")
        assert chain[1].startswith("gemini/")
        print("✅ Provider chain: Groq → Gemini")

        # Test 5: Provider chain Gemini → Groq
        chain2 = _build_provider_chain("gemini/gemini-2.5-pro", s_both)
        assert len(chain2) == 2
        assert chain2[0].startswith("gemini/")
        assert chain2[1].startswith("groq/")
        print("✅ Provider chain: Gemini → Groq")

        # Test 6: Provider chain sin fallback (sin API key alternativa)
        s_only_groq = SimpleNamespace(groq_api_key="key", gemini_api_key="")
        chain3 = _build_provider_chain("groq/llama-3.3-70b-versatile", s_only_groq)
        assert len(chain3) == 1
        print("✅ Provider chain: solo primary cuando no hay fallback key")

        # Test 7: chat_completion exitoso (mock LiteLLM)
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Encontré 2 propiedades en Pampatar."

        with patch("src.app.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.return_value = mock_response
            result = await chat_completion(
                messages=[{"role": "user", "content": "Busco apartamento"}],
                model="groq/llama-3.3-70b-versatile",
            )
            assert result == "Encontré 2 propiedades en Pampatar."
            assert mock_ac.called
        print("✅ chat_completion exitoso con mock")

        # Test 8: response_format=None NO se pasa a LiteLLM
        call_kwargs_captured = {}

        async def mock_acompletion(**kwargs):
            call_kwargs_captured.update(kwargs)
            return mock_response

        with patch("src.app.llm.client.acompletion", side_effect=mock_acompletion):
            await chat_completion(
                messages=[{"role": "user", "content": "test"}],
                model="groq/llama-3.3-70b-versatile",
                response_format=None,
            )
        assert "response_format" not in call_kwargs_captured
        print("✅ response_format=None no se pasa a LiteLLM")

        # Test 9: response_format no-None SÍ se pasa a LiteLLM
        call_kwargs_captured.clear()
        schema = {"type": "json_object"}

        with patch("src.app.llm.client.acompletion", side_effect=mock_acompletion):
            await chat_completion(
                messages=[{"role": "user", "content": "test"}],
                model="groq/llama-3.3-70b-versatile",
                response_format=schema,
            )
        assert call_kwargs_captured.get("response_format") == schema
        print("✅ response_format no-None se pasa correctamente a LiteLLM")

        # Test 10: LLMNoProviderAvailable cuando todos fallan
        with patch(
            "src.app.llm.client.acompletion",
            side_effect=Exception("provider down"),
        ):
            try:
                await chat_completion(
                    messages=[{"role": "user", "content": "test"}],
                    model="groq/llama-3.3-70b-versatile",
                    retry_count=0,
                )
                assert False, "Debería haber lanzado LLMNoProviderAvailable"
            except LLMNoProviderAvailable:
                pass
        print("✅ LLMNoProviderAvailable cuando todos los providers fallan")

        print("\n🎉 Todos los smoke tests pasaron ✅")

    asyncio.run(_test())
