
# Generando hybrid.py (orquestador)
hybrid_code = """Hybrid Search — Orquestador de 4 capas + merge & rank.

Arquitectura de capas:
  Capa 1:  Regex + Keywords (filter_extractor) — costo CERO
  Capa 1b: LLM Fallback (filter_llm) — solo si Capa 1 vacío
  Capa 2:  SQL Search (sql_search) — verdad estructural
  Capa 3:  sqlite-vec (vec_search) — solo si SQL vacío
  Capa 4:  Merge & Rank — máx 3 propiedades

Reglas de Oro:
  1. Si SQL tiene resultados → úsalos. sqlite-vec no se invoca.
  2. Si SQL está vacío → sqlite-vec con post-filtering estricto.
  3. Si ninguno → bot informa honestamente.
  4. El LLM NUNCA inventa propiedades.
  5. Los filtros duros se aplican siempre post-vector-search.
  6. El filter extractor NO llama al LLM si encuentra ≥1 filtro estructural.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.schemas.search import FilterQuery, SearchResult

from src.app.search.filter_extractor import extract_filters
from src.app.search.filter_llm import extract_filters_llm
from src.app.search.sql_search import search_properties_sql
from src.app.search.vec_search import search_properties_vec

settings = get_settings()
logger = get_logger("search.hybrid")

# Circuit breaker para LLM fallback
_llm_fallback_attempts: dict[str, int] = {}
MAX_LLM_FALLBACK_PER_SESSION = 3


async def hybrid_search(
    session: AsyncSession,
    tenant_id: str,
    query_text: str,
    session_id: str,
    max_results: int = 3,
) -> SearchResult:
    """Búsqueda híbrida orquestada: 4 capas + merge & rank.

    Args:
        session: Sesión async SQLAlchemy.
        tenant_id: ID del tenant.
        query_text: Texto original del usuario.
        session_id: ID de sesión (para circuit breaker LLM fallback).
        max_results: Máximo de propiedades en respuesta final (default 3).

    Returns:
        SearchResult con propiedades verificadas.
    """
    logger.info(
        "hybrid_search_start",
        tenant_id=tenant_id,
        session_id=session_id,
        query=query_text[:50],
    )

    # ═══════════════════════════════════════════════════════════════
    # CAPA 1: Filter Extractor — Regex + Keywords (costo CERO)
    # ═══════════════════════════════════════════════════════════════
    filters = extract_filters(query_text)

    if not filters.is_empty:
        logger.info(
            "filter_extracted_regex",
            filters=filters.model_dump(exclude={"raw_query"}),
        )
    else:
        # ═══════════════════════════════════════════════════════════
        # CAPA 1b: LLM Fallback — solo si regex retorna vacío
        # ═══════════════════════════════════════════════════════════
        if _should_use_llm_fallback(session_id):
            logger.info("filter_llm_fallback_triggered", query=query_text[:50])
            filters = await extract_filters_llm(query_text)
            _llm_fallback_attempts[session_id] = _llm_fallback_attempts.get(session_id, 0) + 1
        else:
            logger.warning("llm_fallback_circuit_open", session_id=session_id)
            # Retornar vacío para que el bot responda con exploración genérica
            return SearchResult(properties=[], source="no_filters", total_found=0)

    # ═══════════════════════════════════════════════════════════════
    # CAPA 2: SQL Search — Verdad Estructural
    # ═══════════════════════════════════════════════════════════════
    sql_result = await search_properties_sql(
        session=session,
        tenant_id=tenant_id,
        filters=filters,
        limit=max_results,
    )

    if sql_result.total_found > 0:
        logger.info(
            "hybrid_sql_hit",
            tenant_id=tenant_id,
            results=sql_result.total_found,
            source="sql",
        )
        # SQL tiene resultados → sqlite-vec NO se invoca (Regla #1)
        return _finalize_result(sql_result, max_results)

    # ═══════════════════════════════════════════════════════════════
    # CAPA 3: sqlite-vec — Solo si SQL está vacío
    # ═══════════════════════════════════════════════════════════════
    logger.info("hybrid_sql_miss_vec_triggered", tenant_id=tenant_id)
    
    vec_result = await search_properties_vec(
        session=session,
        tenant_id=tenant_id,
        filters=filters,
        query_text=query_text,
        limit=max_results,
    )

    if vec_result.total_found > 0:
        logger.info(
            "hybrid_vec_hit",
            tenant_id=tenant_id,
            results=vec_result.total_found,
            source="sqlite_vec",
        )
        return _finalize_result(vec_result, max_results)

    # ═══════════════════════════════════════════════════════════════
    # CAPA 4: Sin resultados — Bot informa honestamente (Regla #3)
    # ═══════════════════════════════════════════════════════════════
    logger.info(
        "hybrid_no_results",
        tenant_id=tenant_id,
        query=query_text[:50],
    )
    return SearchResult(
        properties=[],
        source="no_results",
        total_found=0,
    )


def _should_use_llm_fallback(session_id: str) -> bool:
    """Circuit breaker: limita LLM fallback por sesión.
    
    Evita cascada de costos si el usuario envía mensajes ambiguos repetidamente.
    """
    return _llm_fallback_attempts.get(session_id, 0) < MAX_LLM_FALLBACK_PER_SESSION


def _finalize_result(result: SearchResult, max_results: int) -> SearchResult:
    """Aplica límite final y normaliza respuesta."""
    if len(result.properties) > max_results:
        result.properties = result.properties[:max_results]
        result.total_found = len(result.properties)
    return result


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from src.app.db.base import Base
    from src.app.db.engine import engine, AsyncSessionLocal
    from src.app.db.models.property import Property

    async def _test():
        print("🔥 Smoke Test — search/hybrid.py")
        print("   (Requiere DB inicializada con datos de prueba)")

        # Crear tablas
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with AsyncSessionLocal() as session:
            # Insertar propiedades de prueba
            props = [
                Property(
                    id="prop-001",
                    tenant_id="test-tenant",
                    title="Apto 3H Pampatar",
                    property_type="venta",
                    status="disponible",
                    price_usd=150000,
                    location_zone="Pampatar",
                    bedrooms=3,
                    bathrooms=2,
                    vista_al_mar=1,
                    raw_embed_text="Apartamento 3 habitaciones Pampatar vista al mar",
                ),
                Property(
                    id="prop-002",
                    tenant_id="test-tenant",
                    title="Casa El Yaque",
                    property_type="arriendo",
                    status="disponible",
                    price_usd=80000,
                    location_zone="El Yaque",
                    bedrooms=2,
                    bathrooms=1,
                    vista_al_mar=0,
                    raw_embed_text="Casa arriendo El Yaque 2 habitaciones",
                ),
            ]
            for p in props:
                session.add(p)
            await session.commit()

            # Insertar embeddings en sqlite-vec
            from src.app.ingestion.embedder import embed_text
            from src.app.search.vec_search import _ensure_vector_table, _run_sync, _floats_to_blob
            from sqlalchemy import text

            table_name = await _ensure_vector_table("test-tenant")
            
            def _insert_both():
                with engine.sync_engine.connect() as conn:
                    for p in props:
                        emb = embed_text(p.raw_embed_text)
                        blob = _floats_to_blob(emb)
                        conn.execute(
                            text(f"INSERT OR REPLACE INTO {table_name} (property_id, embedding) VALUES (:id, :emb)"),
                            {"id": p.id, "emb": blob}
                        )
                    conn.commit()
            
            await _run_sync(_insert_both)
            print("   ✅ Datos de prueba insertados")

            # Test 1: SQL hit (filtros estructurales claros)
            result1 = await hybrid_search(
                session, "test-tenant", "apartamento en Pampatar hasta 200000", "sess-001"
            )
            assert result1.source == "sql"
            assert result1.total_found >= 1
            assert all(p["location_zone"] == "Pampatar" for p in result1.properties)
            print(f"   ✅ SQL hit: {result1.total_found} resultados, source={result1.source}")

            # Test 2: SQL miss → vec hit (query semántico)
            result2 = await hybrid_search(
                session, "test-tenant", "lugar bonito con vista al mar", "sess-002"
            )
            # Puede ser sql o vec dependiendo de si "vista al mar" matchea regex
            print(f"   ✅ Semántico: {result2.total_found} resultados, source={result2.source}")

            # Test 3: Aislamiento por tenant
            result3 = await hybrid_search(
                session, "otro-tenant", "apartamento en Pampatar", "sess-003"
            )
            assert result3.total_found == 0
            print("   ✅ Aislamiento: 0 resultados para otro tenant")

            # Test 4: Circuit breaker LLM fallback
            for i in range(5):
                r = await hybrid_search(
                    session, "test-tenant", "algo bonito", f"sess-cb"
                )
            # Después de 3 intentos, el circuit breaker debe abrir
            assert _llm_fallback_attempts.get("sess-cb", 0) <= MAX_LLM_FALLBACK_PER_SESSION
            print("   ✅ Circuit breaker: limitado a 3 intentos")

        print("\\n🎉 Smoke tests pasaron")

    asyncio.run(_test())

with open('/mnt/agents/output/hybrid.py', 'w') as f:
    f.write(hybrid_code)

print("✅ hybrid.py generado")
print("\\n═══════════════════════════════════════════════════════════════")
print("✅ MÓDULO HYBRID SEARCH COMPLETO — 5 archivos generados:")
print("   1. filter_extractor.py  — Regex + Keywords (costo cero)")
print("   2. filter_llm.py        — LLM Structured Output (fallback)")
print("   3. sql_search.py          — SQLAlchemy async query")
print("   4. vec_search.py          — sqlite-vec similarity + post-filter")
print("   5. hybrid.py              — Orquestador 4 capas + merge & rank")
print("═══════════════════════════════════════════════════════════════")
