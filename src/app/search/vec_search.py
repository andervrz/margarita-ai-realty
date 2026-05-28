# src/app/search/vec_search.py
"""Vec Search — Capa 3: Búsqueda semántica con sqlite-vec.

Solo se invoca cuando SQL Search retorna vacío.

Flujo:
  1. Verificar tabla vectorial del tenant
  2. Generar embedding del query (lazy load, thread-safe)
  3. KNN search en sqlite-vec (thread pool)
  4. Cargar propiedades completas desde SQLite
  5. Post-filtering estricto en Python
  6. Re-ordenar por similitud original
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.engine import engine
from src.app.db.models.property import Property
from src.app.schemas.property import PropertyChatSummary
from src.app.schemas.search import FilterQuery, SearchResult

logger = get_logger(__name__)

# ── Lazy Load Thread-Safe del Modelo ─────────────────────────────

_embedding_model = None
_embedding_model_lock = asyncio.Lock()


async def _get_embedding_model_async():
    """Carga el modelo de embeddings (lazy singleton thread-safe)."""
    global _embedding_model
    if _embedding_model is None:
        async with _embedding_model_lock:
            if _embedding_model is None:
                settings = get_settings()
                logger.info("loading_embedding_model", model=settings.embedding_model)
                from sentence_transformers import SentenceTransformer
                _embedding_model = await asyncio.to_thread(
                    SentenceTransformer,
                    settings.embedding_model,
                    device="cpu",
                )
    return _embedding_model


async def _generate_embedding(query: str):
    """Genera embedding como numpy array. Corre en thread pool."""
    import numpy as np
    settings = get_settings()
    model = await _get_embedding_model_async()
    embedding = await asyncio.to_thread(
        lambda: model.encode(query, convert_to_numpy=True, normalize_embeddings=True)
    )
    if embedding.shape[-1] != settings.embedding_dims:
        raise ValueError(
            f"Embedding dims mismatch: expected {settings.embedding_dims}, "
            f"got {embedding.shape[-1]}"
        )
    return embedding.astype(np.float32)


def _embedding_to_blob(embedding) -> bytes:
    """Convierte embedding a blob little-endian para sqlite-vec."""
    return embedding.astype("float32").newbyteorder("<").tobytes()


# ── Gestión de Tablas Vectoriales ────────────────────────────────

def _get_vector_table_name(tenant_id: str) -> str:
    """Genera nombre de tabla seguro para sqlite-vec."""
    safe = "".join(
        c if c.isalnum() or c == "_" else "_"
        for c in tenant_id.lower()
    )
    return f"property_embeddings_{safe}"


async def ensure_vector_table(tenant_id: str) -> str:
    """Crea tabla virtual sqlite-vec si no existe."""
    settings = get_settings()
    table_name = _get_vector_table_name(tenant_id)

    def _create_sync():
        with engine.sync_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
                {"name": table_name},
            ).scalar()
            if exists:
                return
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
                {"name": table_name},
            ).scalar() is not None

    return await asyncio.to_thread(_check_sync)


# ── Post-Filtering en Python ──────────────────────────────────────

def _passes_numeric_filters(prop: Property, filters: FilterQuery) -> bool:
    checks = [
        (prop.price_usd, filters.min_price_usd, filters.max_price_usd),
        (prop.bedrooms, filters.bedrooms_min, None),
        (prop.bathrooms, filters.bathrooms_min, None),
        (prop.area_m2, filters.area_min_m2, None),
    ]
    for prop_val, f_min, f_max in checks:
        if f_min is not None and (prop_val is None or prop_val < f_min):
            return False
        if f_max is not None and (prop_val is None or prop_val > f_max):
            return False
    return True


def _passes_boolean_filters(prop: Property, filters: FilterQuery) -> bool:
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
    if filters.property_type and prop.property_type not in filters.property_type:
        return False
    if filters.zone and filters.zone.lower() not in (prop.location_zone or "").lower():
        return False
    if filters.tipo_especial and prop.tipo_especial != filters.tipo_especial:
        return False
    return True


def _apply_hard_filters(
    properties: list[Property],
    filters: FilterQuery,
) -> list[Property]:
    """Aplica todos los filtros duros en Python post-vector-search."""
    return [
        p for p in properties
        if _passes_numeric_filters(p, filters)
        and _passes_boolean_filters(p, filters)
        and _passes_text_filters(p, filters)
    ]


def _reorder_by_similarity(
    properties: list[Property],
    candidate_order: list[str],
) -> list[Property]:
    """Re-ordena por similitud vectorial original."""
    rank = {pid: idx for idx, pid in enumerate(candidate_order)}
    return sorted(properties, key=lambda p: rank.get(p.id, float("inf")))


# ── Función Principal ─────────────────────────────────────────────

async def search_properties_vec(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    limit: int = 3,
    k_vec: int = 20,
) -> SearchResult:
    """
    Búsqueda semántica con sqlite-vec + post-filtering estricto.

    Args:
        session: Sesión SQLAlchemy async.
        tenant_id: ID del tenant.
        filters: Filtros para post-filtering.
        limit: Máximo de resultados finales.
        k_vec: Candidatos vectoriales antes de filtrar.

    Returns:
        SearchResult con propiedades o vacío con source apropiado.
    """
    start = time.perf_counter()

    # Guard: query vacío no tiene sentido semántico
    if not filters.raw_query.strip():
        logger.warning("vec_search_empty_query", tenant_id=tenant_id)
        return SearchResult(properties=[], source="sqlite_vec", total_found=0)

    # 1. Verificar tabla vectorial
    if not await _vector_table_exists(tenant_id):
        logger.warning("vec_table_not_found", tenant_id=tenant_id)
        return SearchResult(
            properties=[],
            source="vec_unavailable",
            total_found=0,
        )

    table_name = _get_vector_table_name(tenant_id)

    # 2. Generar embedding
    try:
        query_embedding = await _generate_embedding(filters.raw_query)
        embedding_blob = _embedding_to_blob(query_embedding)
    except Exception as e:
        logger.error(
            "embedding_generation_failed",
            error=str(e),
            query=filters.raw_query[:50],
        )
        return SearchResult(properties=[], source="vec_error", total_found=0)

    # 3. KNN search en sqlite-vec (thread pool — operación síncrona)
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
                {"embedding": embedding_blob, "k": k_vec},
            )
            return [(row.property_id, row.distance) for row in result.fetchall()]

    try:
        vec_candidates = await asyncio.to_thread(_vec_search_sync)
    except Exception as e:
        logger.error("vec_search_failed", table=table_name, error=str(e))
        return SearchResult(properties=[], source="vec_error", total_found=0)

    if not vec_candidates:
        return SearchResult(properties=[], source="sqlite_vec", total_found=0)

    candidate_ids = [pid for pid, _ in vec_candidates]

    # 4. Cargar propiedades completas desde SQLite
    result = await session.execute(
        select(Property).where(
            Property.id.in_(candidate_ids),
            Property.tenant_id == tenant_id,
            Property.status == "disponible",
        )
    )
    candidates = list(result.scalars().all())

    # 5. Post-filtering estricto en Python
    filtered = _apply_hard_filters(candidates, filters)

    # 6. Re-ordenar por similitud + limitar
    ordered = _reorder_by_similarity(filtered, candidate_ids)[:limit]

    elapsed_ms = (time.perf_counter() - start) * 1000

    logger.info(
        "vec_search_completed",
        tenant_id=tenant_id,
        query=filters.raw_query[:60],
        candidates=len(candidate_ids),
        after_filter=len(ordered),
        elapsed_ms=round(elapsed_ms, 2),
    )

    return SearchResult(
        properties=[
            PropertyChatSummary.model_validate(p).model_dump()
            for p in ordered
        ],
        source="sqlite_vec",
        total_found=len(ordered),
        query_text=filters.raw_query,
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from unittest.mock import MagicMock, patch

    async def run_tests():
        print("🔥 Smoke Tests — vec_search.py\n")

        # Test 1: Nombre de tabla seguro
        print("🧪 Test 1: Sanitización de tenant_id")
        assert _get_vector_table_name("tenant-123") == "property_embeddings_tenant_123"
        assert _get_vector_table_name("user@email.com") == "property_embeddings_user_email_com"
        assert _get_vector_table_name("normal") == "property_embeddings_normal"
        print("   ✅ Sanitización correcta")

        # Test 2: Conversión a blob
        print("\n🧪 Test 2: Embedding → blob")
        import numpy as np
        emb = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        blob = _embedding_to_blob(emb)
        assert isinstance(blob, bytes)
        assert len(blob) == 3 * 4
        print("   ✅ Blob correcto (3 floats × 4 bytes)")

        # Test 3: Post-filtering booleano
        print("\n🧪 Test 3: Post-filtering booleano")
        prop = MagicMock()
        prop.price_usd = 200000
        prop.bedrooms = 3
        prop.bathrooms = 2
        prop.area_m2 = 85
        prop.vista_al_mar = True
        prop.frente_playa = False
        prop.uso_vacacional = True
        prop.property_type = "venta"
        prop.location_zone = "Pampatar"
        prop.tipo_especial = None

        from src.app.schemas.search import FilterQuery

        f_true = FilterQuery(vista_al_mar=True, raw_query="test")
        assert len(_apply_hard_filters([prop], f_true)) == 1

        f_false = FilterQuery(vista_al_mar=False, raw_query="test")
        assert len(_apply_hard_filters([prop], f_false)) == 0

        f_none = FilterQuery(raw_query="test")
        assert len(_apply_hard_filters([prop], f_none)) == 1
        print("   ✅ Filtros True/False/None correctos")

        # Test 4: Re-ordenamiento por similitud
        print("\n🧪 Test 4: Re-ordenamiento por similitud")
        props = [MagicMock(id="A"), MagicMock(id="B"), MagicMock(id="C")]
        reordered = _reorder_by_similarity(
            [props[0], props[2]],  # B fue filtrado
            ["A", "B", "C"],       # orden vectorial original
        )
        assert reordered[0].id == "A"
        assert reordered[1].id == "C"
        print("   ✅ Orden vectorial preservado post-filtrado")

        print("\n🎉 Todos los smoke tests pasaron ✅")

    asyncio.run(run_tests())
