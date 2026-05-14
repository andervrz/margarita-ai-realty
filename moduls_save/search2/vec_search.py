
# Generando vec_search.py
vec_search_code = """Vector Search — Capa 3: sqlite-vec similarity search + post-filtering.

Solo se invoca cuando SQL retorna vacío (Regla de Oro #1).
Genera embedding del query, busca en sqlite-vec, y aplica filtros duros
en Python post-query (Regla de Oro #5: filtros duros nunca se omiten).

Crea tabla virtual property_embeddings_{tenant_id} si no existe.
"""

import sqlite3
from typing import List

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.engine import engine
from src.app.ingestion.embedder import embed_text
from src.app.schemas.search import FilterQuery, SearchResult

settings = get_settings()
logger = get_logger("search.vec_search")


def _get_vector_table_name(tenant_id: str) -> str:
    """Genera nombre de tabla vectorial para un tenant.
    
    Sanitiza tenant_id para evitar inyección SQL en nombres de tabla.
    """
    # Solo permitir caracteres seguros: alfanuméricos y underscore
    safe_id = "".join(c for c in tenant_id if c.isalnum() or c == "_")
    return f"property_embeddings_{safe_id}"


async def _ensure_vector_table(tenant_id: str) -> str:
    """Crea tabla virtual sqlite-vec si no existe. Retorna nombre de tabla.
    
    Usa conexión sync del engine para ejecutar DDL sqlite-vec.
    """
    table_name = _get_vector_table_name(tenant_id)
    
    def _create_table_sync():
        with engine.sync_engine.connect() as conn:
            # Verificar si tabla existe
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
                {"name": table_name}
            )
            if result.scalar():
                return
            
            # Crear tabla virtual vec0
            conn.execute(text(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {table_name}
                USING vec0(
                    property_id TEXT PRIMARY KEY,
                    embedding FLOAT[{settings.embedding_dims}]
                )
            """))
            conn.commit()
            logger.info("vec_table_created", tenant_id=tenant_id, table=table_name)
    
    await _run_sync(_create_table_sync)
    return table_name


async def _run_sync(func):
    """Ejecuta función síncrona en thread pool."""
    import asyncio
    return await asyncio.to_thread(func)


async def search_properties_vec(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    query_text: str,
    limit: int = 10,
    k_vec: int = 20,  # Buscar más en vec para luego filtrar
) -> SearchResult:
    """Búsqueda vectorial con post-filtering estricto.
    
    Args:
        session: Sesión async SQLAlchemy.
        tenant_id: ID del tenant.
        filters: Filtros estructurales (aplicados post-query en Python).
        query_text: Texto original del usuario para embedding.
        limit: Máximo de resultados finales.
        k_vec: Cuántos candidatos vectoriales buscar antes de filtrar.
    
    Returns:
        SearchResult con propiedades filtradas.
    """
    # 1. Asegurar tabla existe
    table_name = await _ensure_vector_table(tenant_id)
    
    # 2. Generar embedding del query
    query_embedding = embed_text(query_text)
    
    # 3. Buscar en sqlite-vec (operación síncrona sobre DB)
    candidate_ids = await _vec_search_sync(table_name, query_embedding, k_vec)
    
    if not candidate_ids:
        logger.info("vec_search_no_candidates", tenant_id=tenant_id, query=query_text[:50])
        return SearchResult(properties=[], source="sqlite_vec", total_found=0)
    
    # 4. Cargar propiedades completas desde SQLite
    from src.app.db.models.property import Property
    
    result = await session.execute(
        select(Property).where(
            Property.id.in_(candidate_ids),
            Property.tenant_id == tenant_id,
            Property.status == "disponible",
        )
    )
    candidates = result.scalars().all()
    
    # 5. Post-filtering estricto en Python (Regla de Oro #5)
    filtered = _apply_hard_filters(candidates, filters)
    
    # 6. Re-ordenar por similitud original (mantener orden de vec search)
    id_to_score = {pid: idx for idx, pid in enumerate(candidate_ids)}
    filtered.sort(key=lambda p: id_to_score.get(p.id, 999))
    
    # 7. Limitar resultados
    final = filtered[:limit]
    
    # Convertir a dicts
    from src.app.search.sql_search import _property_to_dict
    properties_dicts = [_property_to_dict(p) for p in final]
    
    logger.info(
        "vec_search_executed",
        tenant_id=tenant_id,
        candidates=len(candidate_ids),
        after_filter=len(properties_dicts),
        query=query_text[:50],
    )
    
    return SearchResult(
        properties=properties_dicts,
        source="sqlite_vec",
        total_found=len(properties_dicts),
    )


async def _vec_search_sync(table_name: str, embedding: List[float], k: int) -> List[str]:
    """Ejecuta búsqueda vectorial en sqlite-vec (síncrono).
    
    Args:
        table_name: Nombre de tabla virtual sqlite-vec.
        embedding: Vector de query (lista de floats).
        k: Top-k resultados.
    
    Returns:
        Lista de property_ids ordenados por similitud.
    """
    def _search():
        with engine.sync_engine.connect() as conn:
            # Convertir embedding a formato blob de sqlite-vec
            embedding_blob = _floats_to_blob(embedding)
            
            result = conn.execute(
                text(f"""
                    SELECT property_id, distance
                    FROM {table_name}
                    WHERE embedding MATCH :embedding
                    ORDER BY distance
                    LIMIT :k
                """),
                {"embedding": embedding_blob, "k": k}
            )
            return [row[0] for row in result.fetchall()]
    
    return await _run_sync(_search)


def _floats_to_blob(floats: List[float]) -> bytes:
    """Convierte lista de floats a bytes para sqlite-vec MATCH.
    
    sqlite-vec espera un blob de little-endian floats (f4).
    """
    arr = np.array(floats, dtype=np.float32)
    return arr.tobytes()


def _apply_hard_filters(properties, filters: FilterQuery) -> list:
    """Aplica filtros duros en Python post-vector-search.
    
    Esto garantiza que filtros de precio, habitaciones, etc.
    se respeten incluso si el vector search retornó candidatos
    que no cumplen todos los criterios.
    """
    filtered = []
    
    for prop in properties:
        # Filtro: property_type
        if filters.property_type and prop.property_type not in filters.property_type:
            continue
        
        # Filtro: zone (case-insensitive substring)
        if filters.zone and filters.zone.lower() not in (prop.location_zone or "").lower():
            continue
        
        # Filtro: min_price_usd
        if filters.min_price_usd is not None and (prop.price_usd is None or prop.price_usd < filters.min_price_usd):
            continue
        
        # Filtro: max_price_usd
        if filters.max_price_usd is not None and (prop.price_usd is None or prop.price_usd > filters.max_price_usd):
            continue
        
        # Filtro: bedrooms_min
        if filters.bedrooms_min is not None and (prop.bedrooms is None or prop.bedrooms < filters.bedrooms_min):
            continue
        
        # Filtro: bathrooms_min
        if filters.bathrooms_min is not None and (prop.bathrooms is None or prop.bathrooms < filters.bathrooms_min):
            continue
        
        # Filtro: area_min_m2
        if filters.area_min_m2 is not None and (prop.area_m2 is None or prop.area_m2 < filters.area_min_m2):
            continue
        
        # Filtro: vista_al_mar
        if filters.vista_al_mar is not None and bool(prop.vista_al_mar) != filters.vista_al_mar:
            continue
        
        # Filtro: frente_playa
        if filters.frente_playa is not None and bool(prop.frente_playa) != filters.frente_playa:
            continue
        
        # Filtro: uso_vacacional
        if filters.uso_vacacional is not None and bool(prop.uso_vacacional) != filters.uso_vacacional:
            continue
        
        # Filtro: tipo_especial
        if filters.tipo_especial and prop.tipo_especial != filters.tipo_especial:
            continue
        
        filtered.append(prop)
    
    return filtered


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from src.app.db.base import Base
    from src.app.db.engine import engine, AsyncSessionLocal
    from src.app.db.models.property import Property

    async def _test():
        print("🔥 Smoke Test — search/vec_search.py")
        print("   (Requiere DB inicializada y modelo de embeddings descargado)")

        # Crear tablas
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with AsyncSessionLocal() as session:
            # Insertar propiedad de prueba
            test_prop = Property(
                id="prop-test-001",
                tenant_id="test-tenant",
                title="Villa frente al mar en Pampatar",
                property_type="venta",
                status="disponible",
                price_usd=300000,
                location_zone="Pampatar",
                bedrooms=4,
                bathrooms=3,
                vista_al_mar=1,
                frente_playa=1,
                raw_embed_text="Villa frente al mar en Pampatar con 4 habitaciones vista al mar",
            )
            session.add(test_prop)
            await session.commit()

            # Insertar embedding en sqlite-vec
            from src.app.ingestion.embedder import embed_text
            embedding = embed_text(test_prop.raw_embed_text)
            
            table_name = await _ensure_vector_table("test-tenant")
            
            def _insert_embedding():
                with engine.sync_engine.connect() as conn:
                    blob = _floats_to_blob(embedding)
                    conn.execute(
                        text(f"INSERT OR REPLACE INTO {table_name} (property_id, embedding) VALUES (:id, :emb)"),
                        {"id": "prop-test-001", "emb": blob}
                    )
                    conn.commit()
            
            await _run_sync(_insert_embedding)
            print("   ✅ Embedding insertado en sqlite-vec")

            # Test búsqueda vectorial
            filters = FilterQuery(raw_query="villa con vista al mar")
            result = await search_properties_vec(
                session, "test-tenant", filters, "villa con vista al mar", limit=5
            )
            print(f"   ✅ Vec search: {result.total_found} resultados, source={result.source}")
            
            if result.total_found > 0:
                assert result.properties[0]["vista_al_mar"] is True
                print("   ✅ Post-filtering: vista_al_mar preservado")

            # Test con filtro duro que excluye resultado
            filters_strict = FilterQuery(
                max_price_usd=200000,  # La villa cuesta 300k
                raw_query="villa barata"
            )
            result_strict = await search_properties_vec(
                session, "test-tenant", filters_strict, "villa barata", limit=5
            )
            assert result_strict.total_found == 0
            print("   ✅ Post-filtering estricto: 0 resultados (precio excluye)")

        print("\\n🎉 Smoke tests pasaron")

    asyncio.run(_test())

with open('/mnt/agents/output/vec_search.py', 'w') as f:
    f.write(vec_search_code)

print("✅ vec_search.py generado")
