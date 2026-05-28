# src/app/search/hybrid.py
"""Hybrid Search — Orquestador de 4 Capas.

Flujo:
  1. Capa 1:  Regex extractor (costo CERO)
  2. Capa 1b: LLM fallback (solo si regex vacío + circuit breaker permite)
  3. Capa 2:  SQL search (verdad estructural, prioridad máxima)
  4. Capa 3:  sqlite-vec (solo si SQL vacío)
  5. Capa 4:  Sin resultados → respuesta honesta con sugerencias

Reglas de Oro:
  - SQL con resultados → NO invocar sqlite-vec
  - LLM nunca inventa propiedades — solo extrae filtros
  - Circuit breaker previene cascada de costos por queries ambiguos
"""

from __future__ import annotations

import time
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.schemas.search import FilterQuery, SearchResult
from src.app.search.filter_extractor import extract_filters as extract_filters_regex
from src.app.search.filter_llm import LLMFilterExtractionError, extract_filters_with_llm
from src.app.search.sql_search import search_properties_sql
from src.app.search.vec_search import search_properties_vec

logger = get_logger(__name__)

# ── Circuit Breaker — Control de costos LLM ───────────────────────
# V1: dict en memoria (single-worker)
# V2: migrar a Redis con TTL para multi-worker

_llm_fallback_counts: defaultdict[str, int] = defaultdict(int)
_llm_fallback_last_reset: dict[str, float] = {}

LLM_FALLBACK_LIMIT_PER_SESSION = 3
LLM_FALLBACK_WINDOW_SECONDS = 300  # 5 minutos
MAX_TRACKED_SESSIONS = 10_000


def _should_allow_llm_fallback(session_id: str) -> bool:
    """
    Circuit breaker: limita llamadas LLM por sesión.
    Thread-safe para single-worker asyncio en V1.
    """
    current_time = time.time()
    last_reset = _llm_fallback_last_reset.get(session_id, 0)

    # Resetear ventana si expiró
    if current_time - last_reset > LLM_FALLBACK_WINDOW_SECONDS:
        _llm_fallback_counts[session_id] = 0
        _llm_fallback_last_reset[session_id] = current_time

    # Limpiar memoria si excedemos el límite
    if len(_llm_fallback_counts) > MAX_TRACKED_SESSIONS:
        _cleanup_old_sessions(current_time)

    return _llm_fallback_counts[session_id] < LLM_FALLBACK_LIMIT_PER_SESSION


def _cleanup_old_sessions(current_time: float) -> None:
    """Limpia session_ids expirados para controlar memoria."""
    expired = [
        sid for sid, last_reset in _llm_fallback_last_reset.items()
        if current_time - last_reset > LLM_FALLBACK_WINDOW_SECONDS * 2
    ]
    for sid in expired:
        _llm_fallback_counts.pop(sid, None)
        _llm_fallback_last_reset.pop(sid, None)


def _enrich_result(
    result: SearchResult,
    extraction_method: str,
    total_duration_ms: float,
) -> SearchResult:
    """Agrega metadata de orquestación al resultado."""
    return SearchResult(
        properties=result.properties,
        source=result.source,
        total_found=result.total_found,
        query_text=result.query_text,
    )


def _generate_fallback_suggestions(
    filters: FilterQuery,
    language: str,
) -> list[str]:
    """Genera sugerencias contextualizadas cuando no hay resultados."""
    suggestions = []

    if filters.zone:
        suggestions.append(
            f"Propiedades en {filters.zone.title()}"
            if language == "es"
            else f"Properties in {filters.zone.title()}"
        )
    if filters.max_price_usd:
        suggestions.append(
            f"Opciones hasta ${filters.max_price_usd:,.0f}"
            if language == "es"
            else f"Options under ${filters.max_price_usd:,.0f}"
        )
    if filters.property_type:
        type_label = filters.property_type[0]
        suggestions.append(
            f"{type_label.title()}s disponibles"
            if language == "es"
            else f"Available {type_label}s"
        )

    if not suggestions:
        suggestions = (
            ["Apartamentos en Pampatar", "Casas con vista al mar", "Propiedades hasta $200,000"]
            if language == "es"
            else ["Apartments in Pampatar", "Houses with ocean view", "Properties under $200,000"]
        )

    return suggestions[:3]


# ── Función Principal ─────────────────────────────────────────────

async def hybrid_search(
    session: AsyncSession,
    tenant_id: str,
    user_query: str,
    session_id: str,
    language: str = "es",
    max_results: int = 3,
) -> SearchResult:
    """
    Orquesta búsqueda híbrida de 4 capas.

    Args:
        session: Sesión SQLAlchemy async.
        tenant_id: ID del tenant.
        user_query: Texto libre del usuario.
        session_id: ID de sesión (para circuit breaker).
        language: "es" | "en".
        max_results: Máximo de propiedades a retornar.

    Returns:
        SearchResult con propiedades y metadatos.
    """
    start_time = time.perf_counter()
    extraction_method = "regex"

    logger.info(
        "hybrid_search_started",
        tenant_id=tenant_id,
        session_id=session_id,
        query=user_query[:80],
        language=language,
    )

    # ── Capa 1: Regex (costo CERO) ────────────────────────────────
    filters = extract_filters_regex(user_query)

    # ── Capa 1b: LLM fallback ─────────────────────────────────────
    if filters.is_empty:
        if _should_allow_llm_fallback(session_id):
            # Incrementar contador ANTES de llamar al LLM
            _llm_fallback_counts[session_id] += 1

            logger.info(
                "llm_fallback_triggered",
                session_id=session_id,
                attempt=_llm_fallback_counts[session_id],
            )

            try:
                filters = await extract_filters_with_llm(user_query, language=language)
                extraction_method = "llm_fallback"
            except LLMFilterExtractionError as e:
                logger.warning(
                    "llm_fallback_error",
                    session_id=session_id,
                    error=str(e)[:100],
                )
                filters = FilterQuery(raw_query=user_query, extracted_by="llm_fallback")
                extraction_method = "llm_error"
        else:
            logger.warning(
                "llm_fallback_blocked",
                session_id=session_id,
                total_attempts=_llm_fallback_counts[session_id],
            )
            return SearchResult(
                properties=[],
                source="llm_blocked",
                total_found=0,
                query_text=user_query,
            )

    logger.info(
        "filters_extracted",
        method=extraction_method,
        is_empty=filters.is_empty,
        filters={
            k: v for k, v in filters.model_dump().items()
            if v is not None and k not in ("raw_query", "extracted_by")
        },
    )

    # ── Capa 2: SQL (verdad estructural) ──────────────────────────
    sql_start = time.perf_counter()
    sql_result = await search_properties_sql(
        session=session,
        tenant_id=tenant_id,
        filters=filters,
        limit=max_results,
        return_as_dict=True,
    )
    sql_ms = (time.perf_counter() - sql_start) * 1000

    if not sql_result.is_empty:
        total_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "hybrid_search_sql_hit",
            tenant_id=tenant_id,
            results=sql_result.total_found,
            extraction_method=extraction_method,
            sql_ms=round(sql_ms, 2),
            total_ms=round(total_ms, 2),
        )
        return sql_result

    # ── Capa 3: sqlite-vec (fallback semántico) ───────────────────
    logger.info(
        "sql_empty_triggering_vec",
        tenant_id=tenant_id,
        sql_ms=round(sql_ms, 2),
    )

    vec_start = time.perf_counter()
    vec_result = await search_properties_vec(
        session=session,
        tenant_id=tenant_id,
        filters=filters,
        limit=max_results,
    )
    vec_ms = (time.perf_counter() - vec_start) * 1000

    if not vec_result.is_empty:
        total_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "hybrid_search_vec_hit",
            tenant_id=tenant_id,
            results=vec_result.total_found,
            extraction_method=extraction_method,
            vec_ms=round(vec_ms, 2),
            total_ms=round(total_ms, 2),
        )
        return vec_result

    # ── Capa 4: Sin resultados ────────────────────────────────────
    total_ms = (time.perf_counter() - start_time) * 1000
    logger.info(
        "hybrid_search_no_results",
        tenant_id=tenant_id,
        query=user_query[:80],
        extraction_method=extraction_method,
        total_ms=round(total_ms, 2),
    )

    return SearchResult(
        properties=[],
        source="no_results",
        total_found=0,
        query_text=user_query,
    )


# ── Utilidades ────────────────────────────────────────────────────

def get_circuit_breaker_stats() -> dict:
    """Retorna estado del circuit breaker para monitoreo."""
    return {
        "tracked_sessions": len(_llm_fallback_counts),
        "limit_per_session": LLM_FALLBACK_LIMIT_PER_SESSION,
        "window_seconds": LLM_FALLBACK_WINDOW_SECONDS,
        "sessions": dict(_llm_fallback_counts),
    }


def reset_circuit_breaker() -> None:
    """Resetea el circuit breaker (útil para testing)."""
    _llm_fallback_counts.clear()
    _llm_fallback_last_reset.clear()


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, patch

    async def run_tests():
        print("🔥 Smoke Tests — hybrid.py\n")

        # Test 1: Circuit breaker
        print("🧪 Test 1: Circuit breaker")
        reset_circuit_breaker()
        sid = "test-session"

        for i in range(3):
            assert _should_allow_llm_fallback(sid) is True
            _llm_fallback_counts[sid] += 1

        assert _should_allow_llm_fallback(sid) is False
        print("   ✅ Limita a 3 intentos por sesión")

        # Test 2: Reset de ventana
        print("\n🧪 Test 2: Reset de ventana")
        _llm_fallback_last_reset[sid] = time.time() - LLM_FALLBACK_WINDOW_SECONDS - 1
        assert _should_allow_llm_fallback(sid) is True  # ventana expiró → reset
        print("   ✅ Ventana de tiempo se resetea correctamente")

        # Test 3: Sugerencias por idioma
        print("\n🧪 Test 3: Sugerencias contextualizadas")
        from src.app.schemas.search import FilterQuery

        f_es = FilterQuery(zone="pampatar", max_price_usd=200000, raw_query="test")
        sugs_es = _generate_fallback_suggestions(f_es, "es")
        assert any("Pampatar" in s for s in sugs_es)
        assert any("$200,000" in s for s in sugs_es)
        print("   ✅ Sugerencias ES correctas")

        f_en = FilterQuery(zone="pampatar", raw_query="test")
        sugs_en = _generate_fallback_suggestions(f_en, "en")
        assert any("Pampatar" in s for s in sugs_en)
        print("   ✅ Sugerencias EN correctas")

        # Test 4: Stats del circuit breaker
        print("\n🧪 Test 4: Stats del circuit breaker")
        stats = get_circuit_breaker_stats()
        assert "tracked_sessions" in stats
        assert "limit_per_session" in stats
        print(f"   ✅ Stats: {stats['tracked_sessions']} sesiones tracked")

        print("\n🎉 Todos los smoke tests pasaron ✅")

    asyncio.run(run_tests())
