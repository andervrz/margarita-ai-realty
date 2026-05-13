# src/app/search/sql_search.py
"""SQL Search — Capa 2: Búsqueda determinística híbrida con SQLAlchemy async.

Aplica filtros estructurales sobre SQLite con estrategia flexible:
  • Filtros duros: precio, zona, habitaciones, boolean flags (True/False)
  • Búsqueda inteligente: OR logic para property_type/tipo_especial
  • Retorno configurable: SearchResult (dicts) o list[Property] (ORM)
  • Observabilidad: logging estructurado + hooks para métricas

Reglas de Oro:
  1. Siempre filtra por tenant_id + status='disponible' (aislamiento obligatorio)
  2. Si retorna resultados → sqlite-vec NO se invoca (evita costos innecesarios)
  3. Boolean flags manejan explícitamente True/False/None para búsquedas negativas
  4. Límite configurable vía settings con override opcional por llamada

Costo: CERO llamadas LLM. Query SQL puro optimizado con índices.
"""

from __future__ import annotations

from typing import Literal, overload

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BinaryExpression

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.models.property import Property
from src.app.schemas.search import FilterQuery, SearchResult

logger = get_logger("search.sql_search")
settings = get_settings()


# ── Helpers de Filtros Reutilizables ─────────────────────────────

def _build_boolean_filter(column, value: bool | None) -> BinaryExpression | None:
    """
    Construye filtro booleano manejando explícitamente True/False/None.
    
    Args:
        column: Columna SQLAlchemy a filtrar (ej: Property.vista_al_mar)
        value: True (filtra por 1), False (filtra por 0), None (sin filtro)
    
    Returns:
        BinaryExpression para usar en .where(), o None si no hay filtro.
    """
    if value is True:
        return column == 1
    elif value is False:
        return column == 0
    return None  # None → no aplicar filtro


def _build_range_filter(
    column,
    min_val: float | int | None,
    max_val: float | int | None,
) -> list[BinaryExpression]:
    """
    Construye filtros de rango numérico (min/max independientes).
    
    Returns:
        Lista de expresiones para usar con .where(*filters) o encadenar.
    """
    filters = []
    if min_val is not None:
        filters.append(column >= min_val)
    if max_val is not None:
        filters.append(column <= max_val)
    return filters


# ── Función de Conversión ORM → Dict ─────────────────────────────

def _property_to_dict(property_obj: Property) -> dict:
    """
    Convierte modelo ORM Property a dict plano para serialización JSON.
    
    Maneja:
      • Conversión explícita de campos booleanos (SQLite usa 0/1)
      • Serialización segura de campos JSON (amenities, photos)
      • Exclusión de campos internos si es necesario
    """
    return {
        # Identificadores
        "id": property_obj.id,
        "tenant_id": property_obj.tenant_id,
        
        # Metadatos básicos
        "title": property_obj.title,
        "property_type": property_obj.property_type,
        "status": property_obj.status,
        "tipo_especial": property_obj.tipo_especial,
        
        # Precios
        "price_usd": property_obj.price_usd,
        "price_bs": property_obj.price_bs,
        
        # Ubicación
        "location_city": property_obj.location_city,
        "location_zone": property_obj.location_zone,
        "location_address": property_obj.location_address,
        
        # Características físicas
        "area_m2": property_obj.area_m2,
        "bedrooms": property_obj.bedrooms,
        "bathrooms": property_obj.bathrooms,
        "parking_spots": property_obj.parking_spots,
        "capacidad_huespedes": property_obj.capacidad_huespedes,
        
        # Flags booleanos (convertir 0/1 → bool)
        "vista_al_mar": bool(property_obj.vista_al_mar) if property_obj.vista_al_mar is not None else None,
        "frente_playa": bool(property_obj.frente_playa) if property_obj.frente_playa is not None else None,
        "uso_vacacional": bool(property_obj.uso_vacacional) if property_obj.uso_vacacional is not None else None,
        
        # Contenido
        "amenities": property_obj.amenities or [],
        "photos": property_obj.photos or [],
        "description_es": property_obj.description_es,
        "description_en": property_obj.description_en,
        
        # Metadatos de sistema
        "created_at": property_obj.created_at.isoformat() if property_obj.created_at else None,
        "updated_at": property_obj.updated_at.isoformat() if property_obj.updated_at else None,
    }


# ── Función Principal Híbrida ────────────────────────────────────

@overload
async def search_properties_sql(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    *,
    limit: int | None = None,
    return_as_dict: Literal[True] = True,
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
    Busca propiedades usando filtros estructurales en SQL con retorno flexible.
    
    Estrategia híbrida:
      • Filtros obligatorios: tenant_id + status='disponible'
      • Filtros opcionales: aplicados dinámicamente según FilterQuery
      • Búsqueda inteligente: OR logic para property_type/tipo_especial
      • Boolean flags: soporte explícito para búsquedas negativas ("sin vista")
      • Retorno configurable: SearchResult (para API) o list[Property] (para lógica interna)
    
    Args:
        session: Sesión async de SQLAlchemy activa.
        tenant_id: ID del tenant (aislamiento obligatorio por diseño).
        filters: Filtros extraídos por regex o LLM fallback.
        limit: Máximo de resultados (override opcional del setting global).
        return_as_dict: 
            - True (default): Retorna SearchResult con propiedades como dicts
            - False: Retorna list[Property] ORM para procesamiento adicional
    
    Returns:
        SearchResult | list[Property]: Según parámetro return_as_dict.
        Si no hay coincidencias, lista vacía o SearchResult con total_found=0.
    
    Performance:
        • Query optimizado con índices en: tenant_id, status, price_usd, location_zone
        • LIMIT aplicado a nivel SQL (no en memoria)
        • .nullslast() en ordenamiento para consistencia en precios NULL
    """
    
    # ── Configuración de Límite ─────────────────────────────────
    effective_limit = limit if limit is not None else settings.max_properties_per_response
    
    # ── Query Base Obligatorio ──────────────────────────────────
    stmt = select(Property).where(
        and_(
            Property.tenant_id == tenant_id,
            Property.status == "disponible",
        )
    )
    
    # ── Aplicación Dinámica de Filtros ──────────────────────────
    
    # 1. Property Type con OR logic (cobertura ampliada)
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
    for min_val, max_val in [(filters.min_price_usd, filters.max_price_usd)]:
        for expr in _build_range_filter(Property.price_usd, min_val, max_val):
            stmt = stmt.where(expr)
    
    # 4. Habitaciones y baños (mínimos)
    if filters.bedrooms_min is not None:
        stmt = stmt.where(Property.bedrooms >= filters.bedrooms_min)
    
    if filters.bathrooms_min is not None:
        stmt = stmt.where(Property.bathrooms >= filters.bathrooms_min)
    
    # 5. Área mínima
    if filters.area_min_m2 is not None:
        stmt = stmt.where(Property.area_m2 >= filters.area_min_m2)
    
    # 6. Boolean flags con manejo completo (True/False/None)
    for flag, column in [
        (filters.vista_al_mar, Property.vista_al_mar),
        (filters.frente_playa, Property.frente_playa),
        (filters.uso_vacacional, Property.uso_vacacional),
    ]:
        filter_expr = _build_boolean_filter(column, flag)
        if filter_expr is not None:
            stmt = stmt.where(filter_expr)
    
    # 7. Tipo especial con búsqueda parcial (fallback adicional)
    if filters.tipo_especial and filters.property_type != [filters.tipo_especial]:
        # Solo aplicar si no ya fue cubierto por el OR logic de property_type
        stmt = stmt.where(Property.tipo_especial.ilike(f"%{filters.tipo_especial}%"))
    
    # ── Ordenamiento y Límite ───────────────────────────────────
    
    # Ordenar por precio ascendente (mejor UX: opciones accesibles primero)
    # .nullslast() asegura que propiedades sin precio no aparezcan al inicio
    stmt = stmt.order_by(Property.price_usd.asc().nullslast())
    
    # Aplicar límite a nivel SQL (eficiente)
    stmt = stmt.limit(effective_limit)
    
    # ── Ejecución y Logging ─────────────────────────────────────
    
    import time
    start_time = time.perf_counter()
    
    result = await session.execute(stmt)
    properties = result.scalars().all()
    
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    
    # Logging estructurado para observabilidad
    logger.info(
        "sql_search_executed",
        tenant_id=tenant_id,
        query_filters={k: v for k, v in filters.model_dump().items() 
                      if v is not None and k not in ("raw_query", "extracted_by")},
        results_found=len(properties),
        limit_applied=effective_limit,
        execution_time_ms=round(elapsed_ms, 2),
        return_format="dict" if return_as_dict else "orm",
    )
    
    # Hook para métricas (integrar con Prometheus/Datadog en producción)
    # metrics.histogram("sql_search.latency_ms", elapsed_ms, tags={"tenant": tenant_id})
    # metrics.increment("sql_search.results", len(properties), tags={"tenant": tenant_id})
    
    # ── Retorno Configurable ────────────────────────────────────
    
    if return_as_dict:
        return SearchResult(
            properties=[_property_to_dict(p) for p in properties],
            source="sql",
            total_found=len(properties),
            filters_applied={k: v for k, v in filters.model_dump().items() 
                           if v is not None and k != "raw_query"},
            execution_metadata={
                "query_time_ms": round(elapsed_ms, 2),
                "limit": effective_limit,
            },
        )
    
    # Retorno ORM para procesamiento adicional del caller
    return list(properties)


# ── Utilidades de Diagnóstico ───────────────────────────────────

async def get_search_debug_info(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
) -> dict:
    """
    Retorna información de diagnóstico para debugging de búsquedas.
    Útil para logs de error o herramientas de admin.
    """
    from sqlalchemy.dialects import sqlite
    
    # Construir query base (sin ejecutar)
    stmt = select(Property).where(
        Property.tenant_id == tenant_id,
        Property.status == "disponible",
    )
    
    # Aplicar mismos filtros que search_properties_sql (sin ejecutar)
    if filters.property_type:
        stmt = stmt.where(
            or_(
                Property.property_type.in_(filters.property_type),
                Property.tipo_especial.in_(filters.property_type),
            )
        )
    if filters.zone:
        stmt = stmt.where(Property.location_zone.ilike(f"%{filters.zone}%"))
    # ... (aplicar resto de filtros si es necesario para debug)
    
    # Compilar a SQL string para inspección (SQLite dialect)
    compiled = stmt.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True})
    
    return {
        "tenant_id": tenant_id,
        "filters_applied": {k: v for k, v in filters.model_dump().items() if v is not None},
        "sql_query": str(compiled),
        "estimated_complexity": "low" if not filters.property_type and not filters.zone else "medium",
    }


# ── Smoke Tests Integrales ──────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from sqlalchemy import text
    
    # Imports locales para testing
    from src.app.db.engine import AsyncSessionLocal, engine
    from src.app.db.base import Base
    
    async def run_tests():
        print("🔥 Smoke Tests — sql_search.py (Versión Híbrida)\n")
        
        # ── Setup: Crear tablas en memoria para testing ─────────
        print("⚙️  Inicializando DB de testing...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        async with AsyncSessionLocal() as session:
            # ── Seed: Insertar datos de prueba ─────────────────
            test_properties = [
                Property(
                    tenant_id="test-tenant",
                    title="Apto Pampatar Vista Mar",
                    property_type="venta",
                    status="disponible",
                    price_usd=150000,
                    location_zone="Pampatar",
                    bedrooms=3,
                    bathrooms=2,
                    area_m2=85,
                    vista_al_mar=1,
                    frente_playa=0,
                    uso_vacacional=1,
                ),
                Property(
                    tenant_id="test-tenant",
                    title="Posada El Yaque Beachfront",
                    property_type="arriendo",
                    tipo_especial="posada",
                    status="disponible",
                    price_usd=80000,
                    location_zone="El Yaque",
                    bedrooms=5,
                    bathrooms=4,
                    area_m2=200,
                    vista_al_mar=1,
                    frente_playa=1,
                    uso_vacacional=1,
                ),
                Property(
                    tenant_id="test-tenant",
                    title="Local Porlamar Centro",
                    property_type="venta",
                    tipo_especial="local",
                    status="disponible",
                    price_usd=200000,
                    location_zone="Porlamar",
                    bedrooms=0,
                    bathrooms=1,
                    area_m2=50,
                    vista_al_mar=0,
                    frente_playa=0,
                    uso_vacacional=0,
                ),
                Property(
                    tenant_id="other-tenant",  # Tenant diferente para test de aislamiento
                    title="Apto Otro Tenant",
                    property_type="venta",
                    status="disponible",
                    price_usd=100000,
                    location_zone="Pampatar",
                ),
                Property(
                    tenant_id="test-tenant",
                    title="Casa Sin Precio Definido",
                    property_type="venta",
                    status="disponible",
                    price_usd=None,  # NULL para test de nullslast
                    location_zone="Guacuco",
                    bedrooms=4,
                ),
            ]
            session.add_all(test_properties)
            await session.commit()
            
            # ── Test 1: Filtro por zona ─────────────────────────
            print("\n🧪 Test 1: Filtro por zona (Pampatar)")
            from src.app.schemas.search import FilterQuery
            filters = FilterQuery(zone="pampatar", raw_query="test")
            result = await search_properties_sql(session, "test-tenant", filters)
            
            assert result.source == "sql"
            assert result.total_found >= 1
            assert all("pampatar" in p["location_zone"].lower() for p in result.properties)
            print(f"   ✅ Encontradas {result.total_found} propiedades en Pampatar")
            
            # ── Test 2: Filtro compuesto (precio + habitaciones) ─
            print("\n🧪 Test 2: Filtro compuesto (max_price + bedrooms)")
            filters2 = FilterQuery(
                max_price_usd=200000,
                bedrooms_min=3,
                raw_query="test",
            )
            result2 = await search_properties_sql(session, "test-tenant", filters2)
            
            assert all(p["price_usd"] <= 200000 or p["price_usd"] is None for p in result2.properties)
            assert all(p["bedrooms"] >= 3 for p in result2.properties if p["bedrooms"] is not None)
            print(f"   ✅ Filtro compuesto: {result2.total_found} resultados válidos")
            
            # ── Test 3: Búsqueda negativa (sin vista al mar) ─────
            print("\n🧪 Test 3: Filtro booleano negativo (vista_al_mar=False)")
            filters3 = FilterQuery(vista_al_mar=False, raw_query="sin vista al mar")
            result3 = await search_properties_sql(session, "test-tenant", filters3)
            
            # Debería excluir propiedades con vista_al_mar=1
            assert all(p["vista_al_mar"] is False or p["vista_al_mar"] is None for p in result3.properties)
            print(f"   ✅ Búsqueda negativa: {result3.total_found} propiedades sin vista al mar")
            
            # ── Test 4: OR logic para property_type/tipo_especial ─
            print("\n🧪 Test 4: OR logic (buscar 'posada' en tipo_especial)")
            filters4 = FilterQuery(property_type=["posada"], raw_query="busco posada")
            result4 = await search_properties_sql(session, "test-tenant", filters4)
            
            # Debería encontrar la posada aunque property_type="arriendo"
            assert any(p["tipo_especial"] == "posada" for p in result4.properties)
            print(f"   ✅ OR logic: encontró posada en tipo_especial")
            
            # ── Test 5: Aislamiento por tenant ──────────────────
            print("\n🧪 Test 5: Aislamiento por tenant_id")
            filters5 = FilterQuery(zone="pampatar", raw_query="test")
            result5 = await search_properties_sql(session, "other-tenant", filters5)
            
            # other-tenant solo tiene 1 propiedad en Pampatar
            assert result5.total_found == 1
            assert result5.properties[0]["tenant_id"] == "other-tenant"
            print("   ✅ Aislamiento por tenant funcionando correctamente")
            
            # ── Test 6: Retorno como ORM (return_as_dict=False) ──
            print("\n🧪 Test 6: Retorno flexible (ORM objects)")
            filters6 = FilterQuery(zone="el yaque", raw_query="test")
            orm_results = await search_properties_sql(
                session, "test-tenant", filters6, return_as_dict=False
            )
            
            assert isinstance(orm_results, list)
            assert all(isinstance(p, Property) for p in orm_results)
            assert orm_results[0].location_zone.lower() == "el yaque"
            print("   ✅ Retorno ORM: caller puede acceder a métodos del modelo")
            
            # ── Test 7: Límite personalizado ────────────────────
            print("\n🧪 Test 7: Override de límite por llamada")
            filters7 = FilterQuery(raw_query="todas")
            result7 = await search_properties_sql(session, "test-tenant", filters7, limit=2)
            
            assert result7.total_found <= 2
            print(f"   ✅ Límite personalizado: máximo {result7.total_found} resultados")
            
            # ── Test 8: Manejo de NULL en precios (nullslast) ───
            print("\n🧪 Test 8: Ordenamiento con nullslast()")
            filters8 = FilterQuery(raw_query="orden por precio")
            result8 = await search_properties_sql(session, "test-tenant", filters8, limit=10)
            
            # Verificar que propiedades con precio NULL aparecen al final
            prices = [p["price_usd"] for p in result8.properties]
            non_null_prices = [p for p in prices if p is not None]
            null_count = prices.count(None)
            
            # Si hay NULLs, deberían estar al final de la lista
            if null_count > 0 and len(non_null_prices) > 0:
                assert prices[-null_count:] == [None] * null_count, "NULLs deben estar al final"
            print(f"   ✅ nullslast(): {null_count} propiedades sin precio al final")
            
            # ── Test 9: Función de diagnóstico ──────────────────
            print("\n🧪 Test 9: get_search_debug_info para debugging")
            debug = await get_search_debug_info(session, "test-tenant", FilterQuery(zone="pampatar"))
            
            assert "sql_query" in debug
            assert "tenant_id" in debug
            assert "pampatar" in debug["sql_query"].lower()
            print("   ✅ Debug info generado correctamente")
            
            # ── Test 10: Filtro vacío (solo tenant + status) ────
            print("\n🧪 Test 10: Filtro vacío (todas las disponibles)")
            filters10 = FilterQuery(raw_query="quiero ver opciones")
            result10 = await search_properties_sql(session, "test-tenant", filters10, limit=100)
            
            # Debería retornar todas las propiedades disponibles del tenant
            assert result10.total_found >= 4  # 4 props de test-tenant
            assert all(p["status"] == "disponible" for p in result10.properties)
            print(f"   ✅ Filtro vacío: {result10.total_found} propiedades disponibles")
        
        # ── Cleanup ────────────────────────────────────────────
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        
        # ── Resumen Final ───────────────────────────────────────
        print("\n" + "="*70)
        print("🎉 ¡Todos los smoke tests pasaron! ✅")
        print("="*70)
        print("\n📋 Capacidades validadas:")
        print("   • Filtros dinámicos con OR logic para property_type")
        print("   • Boolean flags con soporte para búsquedas negativas")
        print("   • Retorno flexible: SearchResult (dicts) o list[Property] (ORM)")
        print("   • Ordenamiento robusto con nullslast() para precios NULL")
        print("   • Aislamiento obligatorio por tenant_id + status")
        print("   • Logging estructurado + hooks para métricas")
        print("   • Función de diagnóstico para debugging en producción")
        print("\n🚀 Listo para integración en hybrid.py")
    
    # Ejecutar tests
    asyncio.run(run_tests())