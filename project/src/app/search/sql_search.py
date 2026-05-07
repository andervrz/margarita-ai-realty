# src/app/search/sql_search.py
"""SQL Search — búsqueda determinística en SQLite con SQLAlchemy async.

Capa 2 del Hybrid Search Engine.
Ejecuta queries SQL con filtros duros (tenant_id, status, precio, zona, etc.).
Retorna propiedades verificadas que existen en la base de datos.

Si SQL retorna resultados → se usan directamente (saltamos sqlite-vec).
Si SQL está vacío → se delega a vec_search.py (Capa 3).

Reglas:
  1. Siempre filtra por tenant_id + status='disponible'
  2. Aplica filtros estructurales del FilterQuery
  3. Limita a max_properties_per_response (default 3)
  4. Nunca inventa propiedades — solo retorna lo que SQLite confirma
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.property import Property
from app.schemas.search import FilterQuery


async def search_properties_sql(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
) -> list[Property]:
    """Busca propiedades usando SQL determinístico.
    
    Args:
        session: Sesión SQLAlchemy async activa.
        tenant_id: ID del tenant (aislamiento obligatorio).
        filters: Filtros estructurados extraídos por regex o LLM.
    
    Returns:
        Lista de objetos Property (máx 3 por defecto).
        Lista vacía si no hay coincidencias.
    """
    settings = get_settings()
    limit = settings.max_properties_per_response
    
    # Query base: tenant + disponible
    stmt = select(Property).where(
        Property.tenant_id == tenant_id,
        Property.status == "disponible",
    )
    
    # Aplicar filtros estructurales si existen
    if filters.property_type:
        # Buscar en property_type (puede ser 'venta', 'arriendo', etc.)
        # O en tipo_especial si es posada/hotel/villa
        stmt = stmt.where(
            (Property.property_type.in_(filters.property_type)) |
            (Property.tipo_especial.in_(filters.property_type))
        )
    
    if filters.zone:
        # Búsqueda parcial en location_zone (case-insensitive en Python ya normalizado)
        stmt = stmt.where(
            Property.location_zone.ilike(f"%{filters.zone}%")
        )
    
    if filters.min_price_usd is not None:
        stmt = stmt.where(Property.price_usd >= filters.min_price_usd)
    
    if filters.max_price_usd is not None:
        stmt = stmt.where(Property.price_usd <= filters.max_price_usd)
    
    if filters.bedrooms_min is not None:
        stmt = stmt.where(Property.bedrooms >= filters.bedrooms_min)
    
    if filters.bathrooms_min is not None:
        stmt = stmt.where(Property.bathrooms >= filters.bathrooms_min)
    
    if filters.area_min_m2 is not None:
        stmt = stmt.where(Property.area_m2 >= filters.area_min_m2)
    
    if filters.vista_al_mar is True:
        stmt = stmt.where(Property.vista_al_mar == 1)
    
    if filters.frente_playa is True:
        stmt = stmt.where(Property.frente_playa == 1)
    
    if filters.uso_vacacional is True:
        stmt = stmt.where(Property.uso_vacacional == 1)
    
    if filters.tipo_especial:
        stmt = stmt.where(
            Property.tipo_especial.ilike(f"%{filters.tipo_especial}%")
        )
    
    # Ordenar por relevancia: precio ascendente (mejor valor primero)
    # Si hay vista_al_mar, priorizar (podría ser un ORDER BY complejo)
    stmt = stmt.order_by(Property.price_usd.asc())
    
    # Limitar resultados
    stmt = stmt.limit(limit)
    
    result = await session.execute(stmt)
    properties = result.scalars().all()
    
    return list(properties)


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from app.db.engine import AsyncSessionLocal
    
    async def _test():
        print("🔥 Smoke Test — sql_search.py")
        
        # Test 1: Verificar que la función es async
        import inspect
        assert inspect.iscoroutinefunction(search_properties_sql)
        print("  ✅ Función es async")
        
        # Test 2: Verificar que retorna lista
        # Nota: Requiere DB con datos para test real
        # Aquí solo verificamos la firma y estructura
        assert callable(search_properties_sql)
        print("  ✅ Firma correcta")
        
        # Test 3: FilterQuery vacío debe generar query base
        fq = FilterQuery(raw_query="test", extracted_by="regex")
        assert fq.is_empty is True
        print("  ✅ FilterQuery vacío detectado")
        
        # Test 4: FilterQuery con filtros
        fq = FilterQuery(
            zone="pampatar",
            max_price_usd=200000,
            bedrooms_min=3,
            vista_al_mar=True,
            raw_query="test",
            extracted_by="regex",
        )
        assert fq.zone == "pampatar"
        assert not fq.is_empty
        print("  ✅ FilterQuery con filtros estructurales")
        
        print("\n🎉 Smoke tests pasaron (requiere DB para test de integración)")
    
    asyncio.run(_test())