# src/app/search/vec_search.py
"""Vec Search — Capa 3: Búsqueda semántica con pgvector (PostgreSQL).

Solo se invoca cuando SQL Search retorna vacío.

Flujo:
  1. Generar embedding del query (desde embedder.py — lazy load, thread-safe)
  2. KNN search con pgvector (cosine distance ORDER BY)
  3. Post-filtering estricto en Python
  4. Limitar resultados finales
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.property import Property
from app.ingestion.embedder import embed_text
from app.schemas.property import PropertyChatSummary
from app.schemas.search import FilterQuery, SearchResult

logger = get_logger(__name__)


# ── Post-Filtering en Python ──────────────────────────────────────
# Lógica idéntica al original — se aplica sobre los candidatos
# que pgvector devuelve ordenados por similitud coseno.

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
    """Aplica todos los filtros duros en Python post-vector-search.
    Preserva el orden de similitud que pgvector ya garantiza.
    """
    return [
        p for p in properties
        if _passes_numeric_filters(p, filters)
        and _passes_boolean_filters(p, filters)
        and _passes_text_filters(p, filters)
    ]


# ── Función Principal ─────────────────────────────────────────────

async def search_properties_vec(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    limit: int = 3,
    k_vec: int = 20,
) -> SearchResult:
    """
    Búsqueda semántica con pgvector + post-filtering estricto.

    pgvector retorna candidatos ordenados por distancia coseno.
    Python aplica filtros duros (precio, zona, booleanos) sobre esos
    candidatos preservando el orden de similitud.

    Args:
        session: Sesión SQLAlchemy async.
        tenant_id: ID del tenant.
        filters: Filtros estructurados extraídos del query.
        limit: Máximo de resultados finales a retornar.
        k_vec: Candidatos vectoriales a recuperar antes de filtrar.

    Returns:
        SearchResult con propiedades ordenadas por similitud.
    """
    start = time.perf_counter()

    if not filters.raw_query.strip():
        logger.warning("vec_search_empty_query", tenant_id=tenant_id)
        return SearchResult(
            properties=[], source="vec_unavailable", total_found=0
        )

    # 1. Generar embedding del query del usuario
    try:
        query_embedding = await embed_text(filters.raw_query)
    except Exception as e:
        logger.error(
            "embedding_generation_failed",
            error=str(e),
            query=filters.raw_query[:50],
        )
        return SearchResult(properties=[], source="vec_error", total_found=0)

    # 2. KNN search con pgvector — ORDER BY cosine_distance retorna
    #    propiedades ya ordenadas de más a menos similar al query.
    try:
        stmt = (
            select(Property)
            .where(Property.tenant_id == tenant_id)
            .where(Property.status == "disponible")
            .where(Property.embedding.isnot(None))
            .order_by(Property.embedding.cosine_distance(query_embedding))
            .limit(k_vec)
        )
        result = await session.execute(stmt)
        candidates = list(result.scalars().all())
    except Exception as e:
        logger.error(
            "vec_search_query_failed",
            error=str(e),
            tenant_id=tenant_id,
        )
        return SearchResult(properties=[], source="vec_error", total_found=0)

    # 3. Post-filtering estricto + límite final
    #    El orden de similitud queda preservado por la list comprehension.
    filtered = _apply_hard_filters(candidates, filters)
    ordered = filtered[:limit]

    elapsed_ms = (time.perf_counter() - start) * 1000

    if not ordered:
        source = "no_results" if candidates else "vec_unavailable"
        logger.info(
            "vec_search_empty_result",
            tenant_id=tenant_id,
            query=filters.raw_query[:60],
            candidates=len(candidates),
            source=source,
            elapsed_ms=round(elapsed_ms, 2),
        )
        return SearchResult(properties=[], source=source, total_found=0)

    logger.info(
        "vec_search_completed",
        tenant_id=tenant_id,
        query=filters.raw_query[:60],
        candidates=len(candidates),
        after_filter=len(ordered),
        elapsed_ms=round(elapsed_ms, 2),
    )

    return SearchResult(
        properties=[
            PropertyChatSummary.model_validate(p).model_dump()
            for p in ordered
        ],
        source="vec",
        total_found=len(ordered),
        query_text=filters.raw_query,
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from unittest.mock import MagicMock

    async def run_tests():
        print("🔥 Smoke Tests — vec_search.py (pgvector)\n")

        # Test 1: Post-filtering booleano
        print("🧪 Test 1: Filtros booleanos")
        prop = MagicMock()
        prop.price_usd = 200_000
        prop.bedrooms = 3
        prop.bathrooms = 2
        prop.area_m2 = 85
        prop.vista_al_mar = True
        prop.frente_playa = False
        prop.uso_vacacional = True
        prop.property_type = "venta"
        prop.location_zone = "Pampatar"
        prop.tipo_especial = None

        f_true  = FilterQuery(vista_al_mar=True,  raw_query="test")
        f_false = FilterQuery(vista_al_mar=False, raw_query="test")
        f_none  = FilterQuery(raw_query="test")

        assert len(_apply_hard_filters([prop], f_true))  == 1
        assert len(_apply_hard_filters([prop], f_false)) == 0
        assert len(_apply_hard_filters([prop], f_none))  == 1
        print("   ✅ True/False/None correctos")

        # Test 2: Filtros numéricos
        print("\n🧪 Test 2: Filtros numéricos")
        f_precio = FilterQuery(max_price_usd=150_000, raw_query="test")
        assert len(_apply_hard_filters([prop], f_precio)) == 0  # 200k > 150k

        f_ok = FilterQuery(max_price_usd=250_000, raw_query="test")
        assert len(_apply_hard_filters([prop], f_ok)) == 1
        print("   ✅ Precio máximo correcto")

        # Test 3: Orden preservado post-filter
        print("\n🧪 Test 3: Orden de similitud preservado")
        p1, p2, p3 = MagicMock(id="A"), MagicMock(id="B"), MagicMock(id="C")
        for p in [p1, p2, p3]:
            p.price_usd = 100_000; p.bedrooms = 2; p.bathrooms = 1
            p.area_m2 = 60; p.vista_al_mar = False; p.frente_playa = False
            p.uso_vacacional = False; p.property_type = "venta"
            p.location_zone = "Porlamar"; p.tipo_especial = None

        # pgvector ya retorna [A, B, C] en orden — filtrar B
        p2.price_usd = 999_999
        result = _apply_hard_filters(
            [p1, p2, p3],
            FilterQuery(max_price_usd=200_000, raw_query="test")
        )
        assert [p.id for p in result] == ["A", "C"]
        print("   ✅ Orden A→C preservado (B filtrado)")

        print("\n🎉 Todos los smoke tests pasaron ✅")

    asyncio.run(run_tests())
