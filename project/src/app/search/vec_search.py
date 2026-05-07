# src/app/search/vec_search.py
"""Vector Search — búsqueda semántica con sqlite-vec + post-filtering Python.

Capa 3 del Hybrid Search Engine.
Solo se invoca cuando SQL Search (Capa 2) retorna vacío.

Flujo:
  1. Genera embedding del query con sentence-transformers (local, sin API)
  2. Ejecuta similarity search en tabla vectorial del tenant
  3. Aplica post-filtering estricto en Python (precio, estado, habitaciones)
  4. Retorna máx 3 propiedades ordenadas por similitud

Reglas de oro:
  - Los filtros duros (precio, estado, bedrooms) NUNCA se omiten
  - sqlite-vec complementa, nunca reemplaza, la verdad estructural
  - Una tabla virtual por tenant: property_embeddings_{tenant_id}
"""

from __future__ import annotations

import sqlite3
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.property import Property
from app.schemas.search import FilterQuery


# ── Configuración ─────────────────────────────────────────────────

settings = get_settings()


def _get_vector_table_name(tenant_id: str) -> str:
    """Nombre de tabla vectorial para un tenant (aislamiento estricto)."""
    safe_id = tenant_id.replace("-", "_").replace(" ", "")
    return f"property_embeddings_{safe_id}"


def _get_embedding_model():
    """Lazy load del modelo sentence-transformers."""
    # Import lazy para no cargar en import del módulo (120MB)
    from sentence_transformers import SentenceTransformer
    
    if not hasattr(_get_embedding_model, "_model"):
        _get_embedding_model._model = SentenceTransformer(
            settings.embedding_model,
            device="cpu",  # Margarita: sin GPU en VPS inicial
        )
    return _get_embedding_model._model


def _generate_embedding(text_query: str) -> list[float]:
    """Genera vector de embedding para un texto."""
    model = _get_embedding_model()
    embedding = model.encode(text_query, convert_to_numpy=True)
    return embedding.tolist()


# ── Post-filtering estricto ───────────────────────────────────────

def _apply_hard_filters(
    properties: list[Property],
    filters: FilterQuery,
) -> list[Property]:
    """Filtra propiedades por criterios estructurales (Python).
    
    sqlite-vec retorna resultados por similitud semántica, pero
    los filtros duros (precio, habitaciones, etc.) deben aplicarse
    después para garantizar verdad estructural.
    """
    filtered: list[Property] = []
    
    for prop in properties:
        # Filtro: precio mínimo
        if filters.min_price_usd is not None:
            if prop.price_usd is None or prop.price_usd < filters.min_price_usd:
                continue
        
        # Filtro: precio máximo
        if filters.max_price_usd is not None:
            if prop.price_usd is None or prop.price_usd > filters.max_price_usd:
                continue
        
        # Filtro: habitaciones mínimas
        if filters.bedrooms_min is not None:
            if prop.bedrooms is None or prop.bedrooms < filters.bedrooms_min:
                continue
        
        # Filtro: baños mínimos
        if filters.bathrooms_min is not None:
            if prop.bathrooms is None or prop.bathrooms < filters.bathrooms_min:
                continue
        
        # Filtro: área mínima
        if filters.area_min_m2 is not None:
            if prop.area_m2 is None or prop.area_m2 < filters.area_min_m2:
                continue
        
        # Filtro: vista al mar
        if filters.vista_al_mar is True:
            if prop.vista_al_mar != 1:
                continue
        
        # Filtro: frente playa
        if filters.frente_playa is True:
            if prop.frente_playa != 1:
                continue
        
        # Filtro: uso vacacional
        if filters.uso_vacacional is True:
            if prop.uso_vacacional != 1:
                continue
        
        # Filtro: tipo especial
        if filters.tipo_especial:
            if prop.tipo_especial is None or filters.tipo_especial.lower() not in prop.tipo_especial.lower():
                continue
        
        filtered.append(prop)
    
    return filtered


# ── Función principal ─────────────────────────────────────────────

async def search_properties_vec(
    session: AsyncSession,
    tenant_id: str,
    filters: FilterQuery,
    limit: int = 3,
) -> list[Property]:
    """Búsqueda semántica con sqlite-vec + post-filtering.
    
    Args:
        session: Sesión SQLAlchemy async.
        tenant_id: ID del tenant (determina tabla vectorial).
        filters: Filtros estructurales para post-filtering.
        limit: Máximo de resultados a retornar.
    
    Returns:
        Lista de Property ordenadas por similitud semántica.
        Lista vacía si no hay coincidencias o si la tabla vectorial no existe.
    """
    vector_table = _get_vector_table_name(tenant_id)
    
    # Verificar si tabla vectorial existe
    check_stmt = text("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name=:table_name
    """)
    result = await session.execute(check_stmt, {"table_name": vector_table})
    if not result.scalar():
        # Tabla no existe → tenant sin embeddings aún
        return []
    
    # Generar embedding del query
    query_embedding = _generate_embedding(filters.raw_query)
    
    # sqlite-vec query: KNN búsqueda
    # La tabla virtual vec0 soporta: SELECT rowid, distance FROM table WHERE embedding MATCH ?
    # Pero necesitamos JOIN con properties para obtener datos completos
    
    # Construir query vectorial con parámetros seguros
    embedding_str = ",".join(str(x) for x in query_embedding)
    
    vec_stmt = text(f"""
        SELECT 
            v.property_id,
            v.distance
        FROM {vector_table} AS v
        WHERE v.embedding MATCH :embedding
        ORDER BY v.distance
        LIMIT :limit
    """)
    
    try:
        result = await session.execute(
            vec_stmt,
            {
                "embedding": embedding_str,
                "limit": limit * 3,  # Traer más para post-filtering
            }
        )
        vec_results = result.all()
    except Exception:
        # sqlite-vec puede fallar si embedding dims no coinciden
        return []
    
    if not vec_results:
        return []
    
    # Cargar propiedades completas desde SQLite
    property_ids = [r.property_id for r in vec_results]
    
    # Verificar que las propiedades existen y pertenecen al tenant
    props_stmt = select(Property).where(
        Property.id.in_(property_ids),
        Property.tenant_id == tenant_id,
        Property.status == "disponible",
    )
    
    props_result = await session.execute(props_stmt)
    properties = props_result.scalars().all()
    
    # Crear mapa id → property para preservar orden de similitud
    prop_map = {p.id: p for p in properties}
    
    # Ordenar según resultados vectoriales (más similar primero)
    ordered_props: list[Property] = []
    for row in vec_results:
        if row.property_id in prop_map:
            ordered_props.append(prop_map[row.property_id])
    
    # Post-filtering estricto
    filtered = _apply_hard_filters(ordered_props, filters)
    
    # Limitar a resultados finales
    return filtered[:limit]


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    
    async def _test():
        print("🔥 Smoke Test — vec_search.py")
        
        # Test 1: Nombre de tabla vectorial
        name = _get_vector_table_name("dev-tenant-001")
        assert name == "property_embeddings_dev_tenant_001"
        print(f"  ✅ Tabla vectorial: {name}")
        
        # Test 2: Lazy load del modelo (sin cargar en import)
        assert not hasattr(_get_embedding_model, "_model")
        print("  ✅ Lazy load: modelo no cargado en import")
        
        # Test 3: Generar embedding
        emb = _generate_embedding("apartamento con vista al mar en Pampatar")
        assert len(emb) == settings.embedding_dims
        assert all(isinstance(x, float) for x in emb)
        print(f"  ✅ Embedding generado: {len(emb)} dims")
        
        # Test 4: Post-filtering con precio
        from unittest.mock import MagicMock
        
        mock_prop = MagicMock()
        mock_prop.price_usd = 150000
        mock_prop.bedrooms = 3
        mock_prop.vista_al_mar = 1
        mock_prop.frente_playa = 0
        mock_prop.bathrooms = 2
        mock_prop.area_m2 = 120
        mock_prop.tipo_especial = None
        
        filters = FilterQuery(
            max_price_usd=200000,
            bedrooms_min=2,
            vista_al_mar=True,
            raw_query="test",
            extracted_by="regex",
        )
        
        result = _apply_hard_filters([mock_prop], filters)
        assert len(result) == 1
        print("  ✅ Post-filtering: propiedad pasa filtros")
        
        # Test 5: Post-filtering rechaza por precio
        filters2 = FilterQuery(
            max_price_usd=100000,
            raw_query="test",
            extracted_by="regex",
        )
        result2 = _apply_hard_filters([mock_prop], filters2)
        assert len(result2) == 0
        print("  ✅ Post-filtering: rechaza por precio")
        
        # Test 6: Post-filtering rechaza por vista_al_mar
        filters3 = FilterQuery(
            vista_al_mar=True,
            raw_query="test",
            extracted_by="regex",
        )
        mock_prop2 = MagicMock()
        mock_prop2.price_usd = 50000
        mock_prop2.bedrooms = 2
        mock_prop2.vista_al_mar = 0
        mock_prop2.frente_playa = 0
        mock_prop2.bathrooms = 1
        mock_prop2.area_m2 = 80
        mock_prop2.tipo_especial = None
        
        result3 = _apply_hard_filters([mock_prop2], filters3)
        assert len(result3) == 0
        print("  ✅ Post-filtering: rechaza por vista_al_mar")
        
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())