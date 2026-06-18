# src/app/search/hybrid.py
"""Hybrid Search — Orquestador de 4 Capas.

Flujo:
  1. Capa 1:  Regex extractor (costo CERO)
  2. Capa 1b: LLM fallback (solo si regex vacío + circuit breaker permite)
  3. Capa 2:  SQL search (verdad estructural, prioridad máxima)
  4. Capa 3:  pgvector (solo si SQL vacío)
  5. Capa 4:  Sin resultados → respuesta honesta con sugerencias

Reglas de Oro:
  - SQL con resultados → NO invocar pgvector
  - LLM nunca inventa propiedades — solo extrae filtros
  - Circuit breaker previene cascada de costos por queries ambiguos
"""

from __future__ import annotations

import time
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.schemas.search import FilterQuery, SearchResult
from app.search.filter_extractor import extract_filters as extract_filters_regex
from app.search.filter_llm import LLMFilterExtractionError, extract_filters_with_llm
from app.search.sql_search import search_properties_sql
from app.search.vec_search import search_properties_vec

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


# Frases con las que el usuario pide ver/explorar el catálogo sin dar filtros.
# Solo en estos casos una query "sin filtros" debe devolver el top-N por defecto.
_BROWSE_INTENT = (
    # ES
    "ver propiedad", "ver propiedades", "muestrame", "muéstrame", "muestra",
    "mostrar", "que tienes", "qué tienes", "que hay", "qué hay", "todas",
    "todo", "opciones", "mas opciones", "más opciones", "otras opciones",
    "disponible", "disponibles", "catalogo", "catálogo", "lista", "listado",
    # EN
    "show", "see propert", "what do you have", "all propert", "options",
    "more options", "available", "list", "browse", "anything",
)


def _has_browse_intent(text: str) -> bool:
    """True si el usuario pide explorar el catálogo (sin filtros concretos)."""
    low = text.lower()
    return any(kw in low for kw in _BROWSE_INTENT)


# ── Función Principal ─────────────────────────────────────────────

async def hybrid_search(
    session: AsyncSession,
    tenant_id: str,
    user_query: str,
    session_id: str,
    language: str = "es",
    max_results: int = 3,
    sticky_operation: list[str] | None = None,
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

    # Heredar la operación recordada (venta/arriendo) de turnos anteriores. NO
    # cuenta como criterio específico: por sí sola no dispara el listado.
    if not filters.property_type and sticky_operation:
        filters.property_type = sticky_operation
        logger.info(
            "sticky_operation_applied",
            session_id=session_id,
            operation=sticky_operation,
        )

    logger.info(
        "filters_extracted",
        method=extraction_method,
        has_specific=filters.has_specific_criteria,
        filters={
            k: v for k, v in filters.model_dump().items()
            if v is not None and k not in ("raw_query", "extracted_by")
        },
    )

    # ── Gate: solo se lista con un criterio ESPECÍFICO ────────────
    # La operación sola (venta/arriendo) NO basta para listar — evitaba el
    # "top-3 más baratas" arbitrario en cada turno. Y NO usamos un LLM para
    # inventar filtros desde texto vago: si el regex no extrajo nada específico
    # (zona/precio/habitaciones/tipo de vivienda/flags), devolvemos vacío y el
    # chat-LLM guía al usuario a concretar. El foco previo lo conserva el engine.
    if not filters.has_specific_criteria:
        logger.info(
            "no_specific_filters_skip_search",
            session_id=session_id,
            query=user_query[:80],
        )
        return SearchResult(
            properties=[],
            source="no_results",
            total_found=0,
            query_text=user_query,
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

    # ── Capa 3: pgvector (fallback semántico) ───────────────────
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


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio

    async def run_tests():
        print("🔥 Smoke Tests — hybrid.py\n")

        # Test 1: Circuit breaker
        print("🧪 Test 1: Circuit breaker")
        _llm_fallback_counts.clear()
        _llm_fallback_last_reset.clear()
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

        print("\n🎉 Todos los smoke tests pasaron ✅")

    asyncio.run(run_tests())
