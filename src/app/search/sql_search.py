# src/app/search/sql_search.py
"""SQL Search — Capa 2: Búsqueda determinística con SQLAlchemy async.

Reglas de Oro:
  1. Siempre filtra por tenant_id + status='disponible'
  2. Si retorna resultados → sqlite-vec NO se invoca
  3. Boolean flags manejan True/False/None explícitamente
  4. Costo CERO de LLM — SQL puro con índices
"""

from __future__ import annotations

import time
from typing import Literal, overload

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BinaryExpression

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.models.property import Property
from src.app.schemas.property import PropertyChatSummary
from src.app.schemas.search import FilterQuery, SearchResult

logger = get_logger(__name__)


# ── Helpers de Filtros ────────────────────────────────────────────

def _build_boolean_filter(
    column,
    value: bool | None,
) -> BinaryExpression | None:
    """
    Construye filtro booleano para columna Boolean de SQLAlchemy.
    Usa .is_(True/False) para compatibilidad SQLite + PostgreSQL.
    """
    if value is True:
        return column.is_(True)
    elif value is False:
        return column.is_(False)
    return None


def _build_range_filter(
    column,
    min_val: float | int | None,
    max_val: float | int | None,
) -> list[BinaryExpression]:
    """Construye filtros de rango numérico independientes."""
    filters = []
    if min_val is not None:
        filters.append(column >= min_val)
    if max_val is not None:
        filters.append(column <= max_val)
    return filters


# ── Función Principal ─────────────────────────────────────────────

@overload
async def search_properties_sql(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    *,
    limit: int | None = None,
    return_as_dict: Literal[True],
) -> SearchResult: ...

@overload
async def search_properties_sql(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    *,
    limit: int | None = None,
    return_as_dict: Literal[False],
) -> list[Property]: ...

async def search_properties_sql(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    *,
    limit: int | None = None,
    return_as_dict: bool = True,
) -> SearchResult | list[Property]:
    """
    Busca propiedades con filtros estructurales en SQL.

    Args:
        session: Sesión async de SQLAlchemy.
        tenant_id: ID del tenant (aislamiento obligatorio).
        filters: Filtros extraídos por regex o LLM.
        limit: Override del límite global.
        return_as_dict: True → SearchResult | False → list[Property]

    Returns:
        SearchResult o list[Property] según return_as_dict.
    """
    settings = get_settings()
    effective_limit = limit if limit is not None else settings.max_properties_per_response

    # Query base — tenant + disponible siempre obligatorios
    stmt = select(Property).where(
        and_(
            Property.tenant_id == tenant_id,
            Property.status == "disponible",
        )
    )

    # 1. Property type con OR logic (property_type OR tipo_especial)
    if filters.property_type:
        stmt = stmt.where(
            or_(
                Property.property_type.in_(filters.property_type),
                Property.tipo_especial.in_(filters.property_type),
            )
        )

    # 2. Zona con búsqueda parcial case-insensitive
    if filters.zone:
        stmt = stmt.where(Property.location_zone.ilike(f"%{filters.zone}%"))

    # 3. Rangos de precio
    for expr in _build_range_filter(
        Property.price_usd, filters.min_price_usd, filters.max_price_usd
    ):
        stmt = stmt.where(expr)

    # 4. Habitaciones y baños mínimos
    if filters.bedrooms_min is not None:
        stmt = stmt.where(Property.bedrooms >= filters.bedrooms_min)
    if filters.bathrooms_min is not None:
        stmt = stmt.where(Property.bathrooms >= filters.bathrooms_min)

    # 5. Área mínima
    if filters.area_min_m2 is not None:
        stmt = stmt.where(Property.area_m2 >= filters.area_min_m2)

    # 6. Boolean flags — True/False/None con .is_()
    for flag_value, column in [
        (filters.vista_al_mar, Property.vista_al_mar),
        (filters.frente_playa, Property.frente_playa),
        (filters.uso_vacacional, Property.uso_vacacional),
    ]:
        expr = _build_boolean_filter(column, flag_value)
        if expr is not None:
            stmt = stmt.where(expr)

    # 7. Tipo especial adicional si no cubierto por OR logic
    if filters.tipo_especial and filters.property_type != [filters.tipo_especial]:
        stmt = stmt.where(Property.tipo_especial.ilike(f"%{filters.tipo_especial}%"))

    # Ordenamiento y límite
    stmt = stmt.order_by(Property.price_usd.asc().nullslast())
    stmt = stmt.limit(effective_limit)

    # Ejecución
    start = time.perf_counter()
    result = await session.execute(stmt)
    properties = result.scalars().all()
    elapsed_ms = (time.perf_counter() - start) * 1000

    logger.info(
        "sql_search_executed",
        tenant_id=tenant_id,
        results_found=len(properties),
        limit=effective_limit,
        elapsed_ms=round(elapsed_ms, 2),
        filters={
            k: v for k, v in filters.model_dump().items()
            if v is not None and k not in ("raw_query", "extracted_by")
        },
    )

    if not return_as_dict:
        return list(properties)

    # Serializar con PropertyChatSummary — fuente única de verdad
    return SearchResult(
        properties=[
            PropertyChatSummary.model_validate(p).model_dump()
            for p in properties
        ],
        source="sql",
        total_found=len(properties),
        query_text=filters.raw_query,
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio

    from src.app.db.base import Base
    from src.app.db.engine import AsyncSessionLocal, engine

    async def run_tests():
        print("🔥 Smoke Tests — sql_search.py\n")

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with AsyncSessionLocal() as session:
            # Seed
            props = [
                Property(
                    tenant_id="t1", title="Apto Pampatar", property_type="venta",
                    status="disponible", price_usd=150000, location_zone="Pampatar",
                    bedrooms=3, bathrooms=2, area_m2=85,
                    vista_al_mar=True, frente_playa=False, uso_vacacional=True,
                ),
                Property(
                    tenant_id="t1", title="Posada El Yaque", property_type="arriendo",
                    tipo_especial="posada", status="disponible", price_usd=80000,
                    location_zone="El Yaque", bedrooms=5, bathrooms=4, area_m2=200,
                    vista_al_mar=True, frente_playa=True, uso_vacacional=True,
                ),
                Property(
                    tenant_id="t1", title="Local Porlamar", property_type="venta",
                    status="disponible", price_usd=200000, location_zone="Porlamar",
                    bedrooms=0, bathrooms=1, area_m2=50,
                    vista_al_mar=False, frente_playa=False, uso_vacacional=False,
                ),
                Property(
                    tenant_id="t2", title="Otro Tenant", property_type="venta",
                    status="disponible", price_usd=100000, location_zone="Pampatar",
                ),
            ]
            session.add_all(props)
            await session.commit()

            # Test 1: Filtro por zona
            print("🧪 Test 1: Filtro por zona")
            f1 = FilterQuery(zone="pampatar", raw_query="test")
            r1 = await search_properties_sql(session, "t1", f1)
            assert r1.total_found >= 1
            assert all("pampatar" in (p.get("location_zone") or "").lower() for p in r1.properties)
            print(f"   ✅ {r1.total_found} propiedades en Pampatar")

            # Test 2: Filtro booleano negativo
            print("\n🧪 Test 2: vista_al_mar=False")
            f2 = FilterQuery(vista_al_mar=False, raw_query="test")
            r2 = await search_properties_sql(session, "t1", f2)
            assert all(not p.get("vista_al_mar") for p in r2.properties)
            print(f"   ✅ {r2.total_found} propiedades sin vista al mar")

            # Test 3: OR logic para tipo_especial
            print("\n🧪 Test 3: OR logic posada")
            f3 = FilterQuery(property_type=["posada"], raw_query="test")
            r3 = await search_properties_sql(session, "t1", f3)
            assert r3.total_found >= 1
            print(f"   ✅ Posada encontrada via tipo_especial: {r3.total_found}")

            # Test 4: Aislamiento por tenant
            print("\n🧪 Test 4: Aislamiento tenant")
            f4 = FilterQuery(zone="pampatar", raw_query="test")
            r4 = await search_properties_sql(session, "t2", f4)
            assert r4.total_found == 1
            print("   ✅ Aislamiento por tenant correcto")

            # Test 5: Retorno ORM
            print("\n🧪 Test 5: return_as_dict=False")
            f5 = FilterQuery(raw_query="test")
            r5 = await search_properties_sql(session, "t1", f5, return_as_dict=False)
            assert isinstance(r5, list)
            assert all(isinstance(p, Property) for p in r5)
            print(f"   ✅ Retorno ORM: {len(r5)} objetos Property")

        print("\n🎉 Todos los smoke tests pasaron ✅")

    asyncio.run(run_tests())
