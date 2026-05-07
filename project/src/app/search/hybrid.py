# src/app/search/hybrid.py
"""Hybrid Search — orquesta 4 capas + merge & rank.

Capa 4 del Hybrid Search Engine (orquestador final).

Arquitectura de capas:
  Capa 1: Filter Extractor (regex + keywords) → costo CERO
  Capa 1b: LLM Fallback (solo si Capa 1 retorna vacío)
  Capa 2: SQL Search (verdad estructural) → prioridad máxima
  Capa 3: sqlite-vec (verdad semántica) → solo si SQL vacío
  Capa 4: Merge & Rank → máx 3 propiedades, sin duplicados

Reglas de oro:
  1. Si SQL tiene resultados → úsalos. sqlite-vec no se invoca.
  2. Si SQL está vacío → sqlite-vec con post-filtering estricto.
  3. Si ninguno → bot informa honestamente y sugiere ajustar criterios.
  4. El LLM NUNCA inventa propiedades.
  5. Los filtros duros se aplican siempre en Python post-vector-search.
  6. El filter extractor NO llama al LLM si encuentra al menos 1 filtro.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.property import Property
from app.schemas.search import FilterQuery
from app.search.filter_extractor import extract_filters_regex
from app.search.filter_llm import extract_filters_with_llm, LLMFilterExtractionError
from app.search.sql_search import search_properties_sql
from app.search.vec_search import search_properties_vec

logger = get_logger()


async def hybrid_search(
    session: AsyncSession,
    tenant_id: str,
    user_query: str,
    language: str = "es",
    force_llm: bool = False,
) -> HybridSearchResult:
    """Orquesta búsqueda híbrida: regex → LLM fallback → SQL → sqlite-vec.
    
    Args:
        session: Sesión SQLAlchemy async.
        tenant_id: ID del tenant (aislamiento).
        user_query: Texto libre del usuario.
        language: "es" | "en".
        force_llm: Si True, salta regex y va directo a LLM (para testing).
    
    Returns:
        HybridSearchResult con propiedades + metadata de la búsqueda.
    """
    settings = get_settings()
    start_time = time.perf_counter()
    
    # ── Capa 1: Filter Extraction (regex) ─────────────────────────
    filters: FilterQuery
    
    if force_llm:
        filters = FilterQuery(raw_query=user_query, extracted_by="forced_llm")
    else:
        filters = extract_filters_regex(user_query)
    
    # Capa 1b: LLM Fallback (solo si regex vacío y no forzado)
    if filters.is_empty and not force_llm:
        logger.info(
            "filter_extraction_fallback",
            tenant_id=tenant_id,
            query=user_query[:100],
            reason="regex_empty",
        )
        try:
            filters = await extract_filters_with_llm(user_query, language)
        except LLMFilterExtractionError as exc:
            logger.error(
                "filter_llm_failed",
                tenant_id=tenant_id,
                error=str(exc),
            )
            # Si LLM fallback falla, continuamos con FilterQuery vacío
            # El SQL search retornará todas las propiedades disponibles (limitado)
            filters = FilterQuery(raw_query=user_query, extracted_by="llm_failed")
    
    logger.info(
        "filters_extracted",
        tenant_id=tenant_id,
        extracted_by=filters.extracted_by,
        is_empty=filters.is_empty,
        filters=filters.model_dump(exclude={"raw_query"}, exclude_none=True),
    )
    
    # ── Capa 2: SQL Search (verdad estructural) ───────────────────
    sql_results = await search_properties_sql(session, tenant_id, filters)
    
    if sql_results:
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        logger.info(
            "hybrid_search_sql_hit",
            tenant_id=tenant_id,
            sql_count=len(sql_results),
            duration_ms=round(duration_ms, 2),
        )
        
        return HybridSearchResult(
            properties=sql_results,
            source="sql",
            filters=filters,
            duration_ms=round(duration_ms, 2),
        )
    
    # ── Capa 3: sqlite-vec (verdad semántica) ─────────────────────
    logger.info(
        "hybrid_search_sql_empty",
        tenant_id=tenant_id,
        query=user_query[:100],
        reason="attempting_vector_search",
    )
    
    vec_results = await search_properties_vec(
        session,
        tenant_id,
        filters,
        limit=settings.max_properties_per_response,
    )
    
    duration_ms = (time.perf_counter() - start_time) * 1000
    
    if vec_results:
        logger.info(
            "hybrid_search_vec_hit",
            tenant_id=tenant_id,
            vec_count=len(vec_results),
            duration_ms=round(duration_ms, 2),
        )
        
        return HybridSearchResult(
            properties=vec_results,
            source="sqlite-vec",
            filters=filters,
            duration_ms=round(duration_ms, 2),
        )
    
    # ── Capa 4: Sin resultados ────────────────────────────────────
    logger.info(
        "hybrid_search_empty",
        tenant_id=tenant_id,
        query=user_query[:100],
        duration_ms=round(duration_ms, 2),
    )
    
    return HybridSearchResult(
        properties=[],
        source="none",
        filters=filters,
        duration_ms=round(duration_ms, 2),
    )


# ── Schema de resultado ───────────────────────────────────────────

class HybridSearchResult:
    """Resultado unificado del Hybrid Search Engine."""
    
    def __init__(
        self,
        properties: list[Property],
        source: str,  # "sql" | "sqlite-vec" | "none"
        filters: FilterQuery,
        duration_ms: float,
    ):
        self.properties = properties
        self.source = source
        self.filters = filters
        self.duration_ms = duration_ms
        self.count = len(properties)
    
    def to_dict(self) -> dict:
        """Serializa para logs/respuestas."""
        return {
            "count": self.count,
            "source": self.source,
            "duration_ms": self.duration_ms,
            "filters_extracted_by": self.filters.extracted_by,
            "properties": [
                {
                    "id": p.id,
                    "title": p.title,
                    "price_usd": p.price_usd,
                    "zone": p.location_zone,
                }
                for p in self.properties
            ],
        }


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    
    async def _test():
        print("🔥 Smoke Test — hybrid.py")
        
        # Test 1: HybridSearchResult schema
        mock_prop = MagicMock()
        mock_prop.id = "prop-001"
        mock_prop.title = "Apto Pampatar"
        mock_prop.price_usd = 150000
        mock_prop.location_zone = "Pampatar"
        
        fq = FilterQuery(
            zone="pampatar",
            raw_query="test",
            extracted_by="regex",
        )
        
        result = HybridSearchResult(
            properties=[mock_prop],
            source="sql",
            filters=fq,
            duration_ms=45.2,
        )
        assert result.count == 1
        assert result.source == "sql"
        assert result.duration_ms == 45.2
        print("  ✅ HybridSearchResult construido")
        
        # Test 2: to_dict serializa correctamente
        d = result.to_dict()
        assert d["count"] == 1
        assert d["source"] == "sql"
        assert d["properties"][0]["id"] == "prop-001"
        print("  ✅ to_dict serializa correctamente")
        
        # Test 3: Resultado vacío
        empty = HybridSearchResult(
            properties=[],
            source="none",
            filters=FilterQuery(raw_query="test", extracted_by="regex"),
            duration_ms=12.5,
        )
        assert empty.count == 0
        assert empty.source == "none"
        print("  ✅ Resultado vacío (no hay propiedades)")
        
        # Test 4: Filter extraction regex (sin DB)
        fq = extract_filters_regex("apto 3H en Pampatar hasta $200k")
        assert fq.zone == "pampatar"
        assert fq.bedrooms_min == 3
        assert fq.max_price_usd == 200000
        assert fq.extracted_by == "regex"
        print("  ✅ Filter extraction regex integrado")
        
        # Test 5: Filter extraction vacío → is_empty
        fq_empty = extract_filters_regex("hola, información por favor")
        assert fq_empty.is_empty is True
        print("  ✅ Filter extraction vacío detectado")
        
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())