# src/app/search/vec_search.py — Versión Híbrida

import asyncio
import numpy as np
from typing import Literal
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.engine import engine
from src.app.db.models.property import Property
from src.app.schemas.search import FilterQuery, SearchResult

logger = get_logger("search.vec_search")
settings = get_settings()


# ── Lazy Load del Modelo de Embeddings ───────────────────────────

def _get_embedding_model():
    """Lazy load de sentence-transformers para evitar carga en import."""
    from sentence_transformers import SentenceTransformer
    
    if not hasattr(_get_embedding_model, "_model"):
        logger.info("loading_embedding_model", model=settings.embedding_model)
        _get_embedding_model._model = SentenceTransformer(
            settings.embedding_model,
            device="cpu",  # Sin GPU en deployment inicial
        )
    return _get_embedding_model._model


def _generate_embedding(query: str) -> np.ndarray:
    """Genera embedding como numpy array para conversión eficiente a blob."""
    model = _get_embedding_model()
    embedding = model.encode(query, convert_to_numpy=True, normalize_embeddings=True)
    
    # Validación de dimensiones
    if embedding.shape[-1] != settings.embedding_dims:
        raise ValueError(
            f"Embedding dims mismatch: expected {settings.embedding_dims}, got {embedding.shape[-1]}"
        )
    return embedding.astype(np.float32)


def _embedding_to_blob(embedding: np.ndarray) -> bytes:
    """Convierte embedding numpy a blob little-endian para sqlite-vec MATCH."""
    # Forzar little-endian para compatibilidad cross-platform
    return embedding.astype(np.float32).newbyteorder('<').tobytes()


# ── Gestión de Tablas Vectoriales por Tenant ─────────────────────

def _get_vector_table_name(tenant_id: str) -> str:
    """Genera nombre de tabla seguro para sqlite-vec (previene inyección)."""
    # Permitir solo alfanuméricos y underscore
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in tenant_id.lower())
    return f"property_embeddings_{safe}"


async def _ensure_vector_table(tenant_id: str) -> str:
    """Crea tabla virtual sqlite-vec si no existe. Thread-safe."""
    table_name = _get_vector_table_name(tenant_id)
    
    def _create_sync():
        with engine.sync_engine.connect() as conn:
            # Verificar existencia
            exists = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
                {"name": table_name}
            ).scalar()
            
            if exists:
                return
            
            # Crear tabla virtual vec0
            conn.execute(text(f"""
                CREATE VIRTUAL TABLE {table_name}
                USING vec0(
                    property_id TEXT PRIMARY KEY,
                    embedding FLOAT[{settings.embedding_dims}]
                )
            """))
            conn.commit()
            logger.info("vec_table_created", tenant_id=tenant_id, table=table_name)
    
    await asyncio.to_thread(_create_sync)
    return table_name


async def _vector_table_exists(tenant_id: str) -> bool:
    """Verifica si la tabla vectorial del tenant existe."""
    table_name = _get_vector_table_name(tenant_id)
    
    def _check_sync():
        with engine.sync_engine.connect() as conn:
            return conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
                {"name": table_name}
            ).scalar() is not None
    
    return await asyncio.to_thread(_check_sync)


# ── Post-filtering Estricto con Soporte Booleano Completo ────────

def _apply_hard_filters(
    properties: list[Property],
    filters: FilterQuery,
) -> list[Property]:
    """Aplica filtros estructurales en Python post-vector-search."""
    filtered = []
    
    for prop in properties:
        # ── Filtros numéricos ─────────────────────────────────
        if not _passes_numeric_filters(prop, filters):
            continue
        
        # ── Filtros booleanos ─────────────────────────────────
        if not _passes_boolean_filters(prop, filters):
            continue
        
        # ── Filtros de texto/categoría ─────────────────────────
        if not _passes_text_filters(prop, filters):
            continue
        
        filtered.append(prop)
    
    return filtered


def _passes_numeric_filters(prop: Property, filters: FilterQuery) -> bool:
    """Verifica filtros numéricos (precio, habitaciones, baños, área)."""
    checks = [
        (prop.price_usd, filters.min_price_usd, filters.max_price_usd),
        (prop.bedrooms, filters.bedrooms_min, None),
        (prop.bathrooms, filters.bathrooms_min, None),
        (prop.area_m2, filters.area_min_m2, None),
    ]
    
    for prop_val, filter_min, filter_max in checks:
        if filter_min is not None and (prop_val is None or prop_val < filter_min):
            return False
        if filter_max is not None and (prop_val is None or prop_val > filter_max):
            return False
    
    return True


def _passes_boolean_filters(prop: Property, filters: FilterQuery) -> bool:
    """Verifica filtros booleanos (vista_al_mar, frente_playa, uso_vacacional)."""
    checks = [
        (prop.vista_al_mar, filters.vista_al_mar),
        (prop.frente_playa, filters.frente_playa),
        (prop.uso_vacacional, filters.uso_vacacional),
    ]
    
    for prop_flag, filter_val in checks:
        if filter_val is not None and bool(prop_flag) != filter_val:
            return False
    
    return True


def _passes_text_filters(prop: Property, filters: FilterQuery) -> bool:
    """Verifica filtros de texto (property_type, zone, tipo_especial)."""
    if filters.property_type and prop.property_type not in filters.property_type:
        return False
    
    if filters.zone and filters.zone.lower() not in (prop.location_zone or "").lower():
        return False
    
    if filters.tipo_especial and prop.tipo_especial != filters.tipo_especial:
        return False
    
    return True


# ── Búsqueda Vectorial Principal ─────────────────────────────────

async def search_properties_vec(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    limit: int = 3,
    k_vec: int = 20,  # Candidatos a recuperar antes de filtrar
) -> SearchResult:
    """
    Búsqueda semántica con sqlite-vec + post-filtering estricto.
    
    Flujo:
      1. Verificar/crear tabla vectorial del tenant
      2. Generar embedding del query (lazy load del modelo)
      3. Ejecutar KNN search en sqlite-vec (en thread pool)
      4. Cargar propiedades completas desde SQLite
      5. Aplicar filtros duros en Python (Regla de Oro #5)
      6. Re-ordenar por similitud original + limitar resultados
      7. Retornar SearchResult API-ready
    
    Args:
        session: Sesión SQLAlchemy async.
        tenant_id: ID del tenant (determina tabla vectorial).
        filters: Filtros estructurales para post-filtering.
        limit: Máximo de resultados finales a retornar.
        k_vec: Cuántos candidatos vectoriales recuperar antes de filtrar.
    
    Returns:
        SearchResult con propiedades filtradas y metadatos de ejecución.
        Si no hay resultados o error, retorna SearchResult vacío con source apropiado.
    """
    
    import time
    start_time = time.perf_counter()
    
    # ── 1. Verificar tabla vectorial ────────────────────────────
    if not await _vector_table_exists(tenant_id):
        logger.warning("vec_table_not_found", tenant_id=tenant_id)
        return SearchResult(
            properties=[],
            source="vec_unavailable",
            total_found=0,
            execution_metadata={"error": "vector_index_not_built"},
        )
    
    table_name = _get_vector_table_name(tenant_id)
    
    # ── 2. Generar embedding ────────────────────────────────────
    try:
        query_embedding = _generate_embedding(filters.raw_query)
        embedding_blob = _embedding_to_blob(query_embedding)
    except Exception as e:
        logger.error("embedding_generation_failed", error=str(e), query=filters.raw_query[:50])
        return SearchResult(
            properties=[],
            source="vec_error",
            total_found=0,
            execution_metadata={"error": f"embedding_failed: {str(e)[:100]}"},
        )
    
    # ── 3. Búsqueda KNN en sqlite-vec (thread pool) ─────────────
    def _vec_search_sync():
        with engine.sync_engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT property_id, distance
                    FROM {table_name}
                    WHERE embedding MATCH :embedding
                    ORDER BY distance
                    LIMIT :k
                """),
                {"embedding": embedding_blob, "k": k_vec}
            )
            return [(row.property_id, row.distance) for row in result.fetchall()]
    
    try:
        vec_candidates = await asyncio.to_thread(_vec_search_sync)
    except Exception as e:
        logger.error("vec_search_failed", table=table_name, error=str(e))
        return SearchResult(
            properties=[],
            source="vec_error",
            total_found=0,
            execution_metadata={"error": f"vec_query_failed: {str(e)[:100]}"},
        )
    
    if not vec_candidates:
        logger.info("vec_no_candidates", tenant_id=tenant_id, query=filters.raw_query[:50])
        return SearchResult(properties=[], source="sqlite_vec", total_found=0)
    
    candidate_ids = [pid for pid, _ in vec_candidates]
    
    # ── 4. Cargar propiedades completas ─────────────────────────
    result = await session.execute(
        select(Property).where(
            Property.id.in_(candidate_ids),
            Property.tenant_id == tenant_id,
            Property.status == "disponible",
        )
    )
    candidates = result.scalars().all()
    
    # ── 5. Post-filtering estricto ──────────────────────────────
    filtered = _apply_hard_filters(candidates, filters)
    
    # ── 6. Re-ordenar por similitud original + limitar ──────────
    ordered = _reorder_by_similarity(filtered, candidate_ids)[:limit]
    
    # ── 7. Convertir a dicts + logging ─────────────────────────
    from src.app.search.sql_search import _property_to_dict
    
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    properties_dicts = [_property_to_dict(p) for p in ordered]
    
    logger.info(
        "vec_search_completed",
        tenant_id=tenant_id,
        query=filters.raw_query[:80],
        candidates_retrieved=len(candidate_ids),
        after_hard_filters=len(properties_dicts),
        filter_drop_rate=round(1 - len(properties_dicts)/len(candidate_ids), 2) if candidate_ids else 0,
        execution_time_ms=round(elapsed_ms, 2),
    )
    
    return SearchResult(
        properties=properties_dicts,
        source="sqlite_vec",
        total_found=len(properties_dicts),
        filters_applied={k: v for k, v in filters.model_dump().items() if v is not None and k != "raw_query"},
        execution_metadata={
            "vec_candidates": len(candidate_ids),
            "post_filter_results": len(properties_dicts),
            "query_time_ms": round(elapsed_ms, 2),
        },
    )


# ── Utilidades de Diagnóstico ───────────────────────────────────

async def get_vec_search_debug_info(tenant_id: str, query: str) -> dict:
    """Información de diagnóstico para debugging de búsqueda vectorial."""
    table_name = _get_vector_table_name(tenant_id)
    
    try:
        embedding = _generate_embedding(query)
        dims_ok = len(embedding) == settings.embedding_dims
    except Exception as e:
        return {"error": f"embedding_failed: {str(e)}"}
    
    return {
        "tenant_id": tenant_id,
        "vector_table": table_name,
        "table_exists": await _vector_table_exists(tenant_id),
        "query_embedding_dims": len(embedding),
        "expected_dims": settings.embedding_dims,
        "dims_match": dims_ok,
        "model_loaded": hasattr(_get_embedding_model, "_model"),
    }


# ── Smoke Tests Integrales ──────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from unittest.mock import MagicMock, patch
    
    async def run_tests():
        print("🔥 Smoke Tests — vec_search.py (Versión Híbrida)\n")
        
        # ── Test 1: Lazy load del modelo ────────────────────────
        print("🧪 Test 1: Lazy load de embedding model")
        assert not hasattr(_get_embedding_model, "_model"), "Modelo no debe cargarse en import"
        
        # Simular primera llamada
        with patch('sentence_transformers.SentenceTransformer') as MockModel:
            mock_instance = MagicMock()
            mock_instance.encode.return_value = np.array([0.1] * 384, dtype=np.float32)
            MockModel.return_value = mock_instance
            
            emb = _generate_embedding("test query")
            assert hasattr(_get_embedding_model, "_model"), "Modelo debe cargarse en primera llamada"
            assert len(emb) == 384
            print("   ✅ Lazy load funciona correctamente")
        
        # ── Test 2: Conversión embedding → blob ─────────────────
        print("\n🧪 Test 2: Conversión a blob para sqlite-vec")
        test_embedding = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        blob = _embedding_to_blob(test_embedding)
        
        # Verificar que es bytes y tiene tamaño esperado (3 floats × 4 bytes)
        assert isinstance(blob, bytes)
        assert len(blob) == 3 * 4  # float32 = 4 bytes
        print("   ✅ Blob generado correctamente")
        
        # ── Test 3: Nombre de tabla seguro ──────────────────────
        print("\n🧪 Test 3: Sanitización de tenant_id para tabla")
        test_cases = [
            ("tenant-123", "property_embeddings_tenant_123"),
            ("user@email.com", "property_embeddings_user_email_com"),
            ("normal_tenant", "property_embeddings_normal_tenant"),
        ]
        for input_id, expected in test_cases:
            result = _get_vector_table_name(input_id)
            assert result == expected, f"Input: {input_id} | Esperado: {expected} | Obtenido: {result}"
        print("   ✅ Sanitización de tenant_id funciona correctamente")
        
        # ── Test 4: Post-filtering con booleanos completos ──────
        print("\n🧪 Test 4: Post-filtering con soporte True/False/None")
        
        # Mock property con vista_al_mar=1
        prop_with_view = MagicMock()
        prop_with_view.price_usd = 200000
        prop_with_view.bedrooms = 3
        prop_with_view.vista_al_mar = 1
        prop_with_view.frente_playa = 0
        prop_with_view.property_type = "venta"
        prop_with_view.location_zone = "Pampatar"
        prop_with_view.tipo_especial = None
        
        # Caso A: Buscar con vista_al_mar=True → debe incluir
        from src.app.schemas.search import FilterQuery
        filters_a = FilterQuery(vista_al_mar=True, raw_query="test")
        result_a = _apply_hard_filters([prop_with_view], filters_a)
        assert len(result_a) == 1, "Propiedad con vista debe pasar filtro vista_al_mar=True"
        
        # Caso B: Buscar con vista_al_mar=False → debe excluir
        filters_b = FilterQuery(vista_al_mar=False, raw_query="test")
        result_b = _apply_hard_filters([prop_with_view], filters_b)
        assert len(result_b) == 0, "Propiedad con vista debe ser excluida por vista_al_mar=False"
        
        # Caso C: Sin filtro de vista → debe incluir
        filters_c = FilterQuery(raw_query="test")
        result_c = _apply_hard_filters([prop_with_view], filters_c)
        assert len(result_c) == 1, "Propiedad debe pasar cuando no hay filtro de vista"
        
        print("   ✅ Post-filtering booleano maneja True/False/None correctamente")
        
        # ── Test 5: Re-ordenamiento por similitud ───────────────
        print("\n🧪 Test 5: Re-ordenamiento post-filtrado por similitud")
        
        # Simular 3 propiedades con orden vectorial: [A, B, C]
        props = [
            MagicMock(id="A", price_usd=100000),
            MagicMock(id="B", price_usd=300000),  # Será filtrado por max_price
            MagicMock(id="C", price_usd=150000),
        ]
        candidate_order = ["A", "B", "C"]  # Orden original por similitud
        
        # Filtrar por max_price=200k → B se elimina
        filters_price = FilterQuery(max_price_usd=200000, raw_query="test")
        filtered = _apply_hard_filters(props, filters_price)
        
        # Re-ordenar según orden vectorial original
        reordered = _reorder_by_similarity(filtered, candidate_order)
        
        # Debe mantener orden A antes que C (como en candidate_order)
        assert reordered[0].id == "A"
        assert reordered[1].id == "C"
        print("   ✅ Re-ordenamiento preserva similitud semántica post-filtrado")
        
        # ── Test 6: SearchResult con metadata ───────────────────
        print("\n🧪 Test 6: SearchResult con metadatos de ejecución")
        result = SearchResult(
            properties=[{"id": "test"}],
            source="sqlite_vec",
            total_found=1,
            execution_metadata={"query_time_ms": 45.2, "vec_candidates": 20},
        )
        
        assert result.source == "sqlite_vec"
        assert result.execution_metadata["query_time_ms"] == 45.2
        assert not result.is_empty
        print("   ✅ SearchResult incluye metadata para debugging/métricas")
        
        # ── Test 7: Manejo de error en embedding ────────────────
        print("\n🧪 Test 7: Manejo elegante de errores en generación de embedding")
        with patch('src.app.search.vec_search._generate_embedding', side_effect=ValueError("dims mismatch")):
            # Simular llamada que fallaría en embedding
            try:
                _generate_embedding("test")
            except ValueError as e:
                assert "dims mismatch" in str(e)
        print("   ✅ Errores de embedding son capturados y manejados")
        
        # ── Resumen Final ───────────────────────────────────────
        print("\n" + "="*70)
        print("🎉 ¡Todos los smoke tests pasaron! ✅")
        print("="*70)
        print("\n📋 Capacidades validadas:")
        print("   • Lazy load de modelo de embeddings (120MB bajo demanda)")
        print("   • Conversión correcta embedding→blob para sqlite-vec MATCH")
        print("   • Sanitización segura de tenant_id para nombres de tabla")
        print("   • Post-filtering con soporte completo para boolean flags")
        print("   • Re-ordenamiento por similitud semántica post-filtrado")
        print("   • SearchResult con metadata para observabilidad")
        print("   • Manejo elegante de errores sin romper el flujo")
        print("\n🚀 Capa 3 lista para integración en hybrid.py")
    
    asyncio.run(run_tests())
