
# Generando sql_search.py
sql_search_code = """SQL Search — Capa 2: Búsqueda determinística con SQLAlchemy async.

Aplica filtros duros (precio, zona, habitaciones, status) sobre SQLite.
Si retorna resultados, sqlite-vec NO se invoca (Regla de Oro #1).

Todas las queries filtran por tenant_id y status='disponible'.
"""

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.models.property import Property
from app.schemas.search import FilterQuery, SearchResult

logger = get_logger("search.sql_search")


async def search_properties_sql(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    limit: int = 10,
) -> SearchResult:
    """Busca propiedades usando filtros estructurales en SQL.

    Args:
        session: Sesión async de SQLAlchemy.
        tenant_id: ID del tenant (aislamiento obligatorio).
        filters: Filtros extraídos del texto del usuario.
        limit: Máximo de resultados (default 10, se reduce a 3 en hybrid).

    Returns:
        SearchResult con propiedades encontradas o lista vacía.
    """
    query = select(Property).where(
        and_(
            Property.tenant_id == tenant_id,
            Property.status == "disponible",
        )
    )

    # ── Aplicar filtros dinámicamente ───────────────────────────────

    if filters.property_type:
        query = query.where(Property.property_type.in_(filters.property_type))

    if filters.zone:
        # Búsqueda case-insensitive parcial en location_zone
        query = query.where(Property.location_zone.ilike(f"%{filters.zone}%"))

    if filters.min_price_usd is not None:
        query = query.where(Property.price_usd >= filters.min_price_usd)

    if filters.max_price_usd is not None:
        query = query.where(Property.price_usd <= filters.max_price_usd)

    if filters.bedrooms_min is not None:
        query = query.where(Property.bedrooms >= filters.bedrooms_min)

    if filters.bathrooms_min is not None:
        query = query.where(Property.bathrooms >= filters.bathrooms_min)

    if filters.area_min_m2 is not None:
        query = query.where(Property.area_m2 >= filters.area_min_m2)

    if filters.vista_al_mar is not None:
        query = query.where(Property.vista_al_mar == (1 if filters.vista_al_mar else 0))

    if filters.frente_playa is not None:
        query = query.where(Property.frente_playa == (1 if filters.frente_playa else 0))

    if filters.uso_vacacional is not None:
        query = query.where(Property.uso_vacacional == (1 if filters.uso_vacacional else 0))

    if filters.tipo_especial:
        query = query.where(Property.tipo_especial == filters.tipo_especial)

    # Ordenar por precio ascendente (mejor UX: más baratas primero)
    query = query.order_by(Property.price_usd.asc().nullslast())
    query = query.limit(limit)

    # Ejecutar
    result = await session.execute(query)
    properties = result.scalars().all()

    # Convertir a dicts para respuesta uniforme
    properties_dicts = [_property_to_dict(p) for p in properties]

    logger.info(
        "sql_search_executed",
        tenant_id=tenant_id,
        filters=filters.model_dump(exclude={"raw_query"}),
        results_found=len(properties_dicts),
    )

    return SearchResult(
        properties=properties_dicts,
        source="sql",
        total_found=len(properties_dicts),
    )


def _property_to_dict(property_obj: Property) -> dict:
    """Convierte modelo ORM Property a dict plano."""
    return {
        "id": property_obj.id,
        "tenant_id": property_obj.tenant_id,
        "title": property_obj.title,
        "property_type": property_obj.property_type,
        "status": property_obj.status,
        "price_usd": property_obj.price_usd,
        "price_bs": property_obj.price_bs,
        "location_city": property_obj.location_city,
        "location_zone": property_obj.location_zone,
        "location_address": property_obj.location_address,
        "area_m2": property_obj.area_m2,
        "bedrooms": property_obj.bedrooms,
        "bathrooms": property_obj.bathrooms,
        "parking_spots": property_obj.parking_spots,
        "vista_al_mar": bool(property_obj.vista_al_mar),
        "frente_playa": bool(property_obj.frente_playa),
        "uso_vacacional": bool(property_obj.uso_vacacional),
        "tipo_especial": property_obj.tipo_especial,
        "capacidad_huespedes": property_obj.capacidad_huespedes,
        "amenities": property_obj.amenities,
        "photos": property_obj.photos,
        "description_es": property_obj.description_es,
        "description_en": property_obj.description_en,
    }


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from app.db.engine import AsyncSessionLocal
    from app.db.base import Base
    from app.db.engine import engine

    async def _test():
        print("🔥 Smoke Test — search/sql_search.py")
        print("   (Requiere DB inicializada con datos de prueba)")

        # Crear tablas en memoria para test
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with AsyncSessionLocal() as session:
            # Insertar propiedad de prueba
            test_prop = Property(
                tenant_id="test-tenant",
                title="Apto Pampatar",
                property_type="venta",
                status="disponible",
                price_usd=150000,
                location_zone="Pampatar",
                bedrooms=3,
                bathrooms=2,
                vista_al_mar=1,
            )
            session.add(test_prop)
            await session.commit()

            # Test 1: Filtro por zona
            filters = FilterQuery(zone="Pampatar", raw_query="test")
            result = await search_properties_sql(session, "test-tenant", filters)
            assert result.source == "sql"
            assert result.total_found >= 1
            assert result.properties[0]["location_zone"] == "Pampatar"
            print(f"   ✅ Filtro por zona: {result.total_found} resultados")

            # Test 2: Filtro por precio + habitaciones
            filters2 = FilterQuery(
                max_price_usd=200000,
                bedrooms_min=2,
                raw_query="test",
            )
            result2 = await search_properties_sql(session, "test-tenant", filters2)
            assert all(p["price_usd"] <= 200000 for p in result2.properties)
            assert all(p["bedrooms"] >= 2 for p in result2.properties if p["bedrooms"])
            print(f"   ✅ Filtro compuesto: {result2.total_found} resultados")

            # Test 3: Sin resultados (tenant diferente)
            filters3 = FilterQuery(zone="Pampatar", raw_query="test")
            result3 = await search_properties_sql(session, "otro-tenant", filters3)
            assert result3.total_found == 0
            print("   ✅ Aislamiento por tenant: 0 resultados para otro tenant")

            # Test 4: Filtro vacío (solo tenant)
            filters4 = FilterQuery(raw_query="test")
            result4 = await search_properties_sql(session, "test-tenant", filters4)
            assert result4.total_found >= 1
            print(f"   ✅ Filtro vacío: {result4.total_found} resultados (todas disponibles)")

        print("\\n🎉 Smoke tests pasaron")

    asyncio.run(_test())


with open('/mnt/agents/output/sql_search.py', 'w') as f:
    f.write(sql_search_code)

print("✅ sql_search.py generado")
