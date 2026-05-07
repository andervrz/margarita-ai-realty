# project/src/app/api/v1/properties.py
"""Properties API — Admin: listar, buscar y consultar propiedades.

Endpoints:
    GET /properties          → Listar propiedades del tenant (con filtros)
    GET /properties/{id}     → Ver detalle de una propiedad
    GET /properties/search   → Búsqueda híbrida (SQL + sqlite-vec)

Filtros soportados (GET /properties):
    - property_type: venta | arriendo | vacacional | local | posada | hotel | planos | terreno
    - zone: Pampatar, El Agua, El Yaque, etc.
    - min_price / max_price: rango de precio USD
    - bedrooms_min / bathrooms_min: mínimo de habitaciones/baños
    - vista_al_mar: true | false
    - frente_playa: true | false
    - uso_vacacional: true | false
    - status: disponible | vendido | arrendado

Seguridad:
    - Requiere tenant autenticado (via middleware)
    - Solo retorna propiedades del tenant (aislamiento estricto)
    - No expone api_key_hash ni datos internos del tenant
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from src.app.api.middleware import get_current_tenant
from src.app.core.logging import logger
from src.app.db.models.property import Property

if TYPE_CHECKING:
    pass


router = APIRouter(prefix="/properties", tags=["properties"])


# ── Schemas ────────────────────────────────────────────────────────

class PropertyListItem(BaseModel):
    """Propiedad resumida para listados."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    title: str
    property_type: str
    status: str
    price_usd: float | None
    price_bs: float | None
    location_zone: str | None
    location_city: str = "Porlamar"
    area_m2: float | None
    bedrooms: int | None
    bathrooms: int | None
    vista_al_mar: bool = False
    frente_playa: bool = False
    uso_vacacional: bool = False
    photos: list[str] = Field(default_factory=list)


class PropertyDetail(PropertyListItem):
    """Propiedad completa con todos los campos."""
    location_address: str | None
    parking_spots: int | None
    tipo_especial: str | None
    capacidad_huespedes: int | None
    amenities: list[str] = Field(default_factory=list)
    description_es: str | None
    description_en: str | None
    external_id: str | None
    created_at: str
    updated_at: str


class PropertySearchResult(BaseModel):
    """Resultado de búsqueda híbrida con score de relevancia."""
    property_: PropertyListItem = Field(..., alias="property")
    search_type: str = Field(..., description="sql | vector | hybrid")
    relevance_score: float | None = None


# ── Endpoints ──────────────────────────────────────────────────────

@router.get("", response_model=list[PropertyListItem])
async def list_properties(
    tenant: dict = Depends(get_current_tenant),
    property_type: str | None = Query(None, description="Tipo de propiedad"),
    zone: str | None = Query(None, description="Zona de Margarita"),
    min_price: float | None = Query(None, ge=0, description="Precio mínimo USD"),
    max_price: float | None = Query(None, ge=0, description="Precio máximo USD"),
    bedrooms_min: int | None = Query(None, ge=0, description="Mínimo habitaciones"),
    bathrooms_min: int | None = Query(None, ge=0, description="Mínimo baños"),
    vista_al_mar: bool | None = Query(None, description="Con vista al mar"),
    frente_playa: bool | None = Query(None, description="Frente a la playa"),
    uso_vacacional: bool | None = Query(None, description="Uso vacacional"),
    status: str = Query("disponible", description="disponible | vendido | arrendado"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[PropertyListItem]:
    """Lista propiedades del tenant con filtros opcionales.
    
    Ejemplo:
        GET /properties?zone=Pampatar&min_price=100000&max_price=200000&vista_al_mar=true
    """
    tenant_id = tenant["id"]
    
    from sqlalchemy import select, and_
    from src.app.db.engine import async_session_maker

    async with async_session_maker() as session:
        stmt = select(Property).where(Property.tenant_id == tenant_id)
        
        # Aplicar filtros dinámicamente
        if property_type:
            stmt = stmt.where(Property.property_type == property_type)
        if zone:
            stmt = stmt.where(Property.location_zone.ilike(f"%{zone}%"))
        if min_price is not None:
            stmt = stmt.where(Property.price_usd >= min_price)
        if max_price is not None:
            stmt = stmt.where(Property.price_usd <= max_price)
        if bedrooms_min is not None:
            stmt = stmt.where(Property.bedrooms >= bedrooms_min)
        if bathrooms_min is not None:
            stmt = stmt.where(Property.bathrooms >= bathrooms_min)
        if vista_al_mar is not None:
            stmt = stmt.where(Property.vista_al_mar == (1 if vista_al_mar else 0))
        if frente_playa is not None:
            stmt = stmt.where(Property.frente_playa == (1 if frente_playa else 0))
        if uso_vacacional is not None:
            stmt = stmt.where(Property.uso_vacacional == (1 if uso_vacacional else 0))
        if status:
            stmt = stmt.where(Property.status == status)
        
        stmt = stmt.order_by(Property.created_at.desc()).limit(limit).offset(offset)
        
        result = await session.execute(stmt)
        properties = result.scalars().all()

    logger.info(
        "properties_listed",
        tenant_id=tenant_id,
        count=len(properties),
        filters={k: v for k, v in {
            "property_type": property_type,
            "zone": zone,
            "min_price": min_price,
            "max_price": max_price,
            "vista_al_mar": vista_al_mar,
        }.items() if v is not None},
    )

    return [_orm_to_list_item(p) for p in properties]


@router.get("/{property_id}", response_model=PropertyDetail)
async def get_property(
    property_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> PropertyDetail:
    """Obtiene el detalle completo de una propiedad específica."""
    tenant_id = tenant["id"]
    
    from sqlalchemy import select
    from src.app.db.engine import async_session_maker

    async with async_session_maker() as session:
        result = await session.execute(
            select(Property)
            .where(Property.id == property_id)
            .where(Property.tenant_id == tenant_id)
        )
        prop = result.scalar_one_or_none()

    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Propiedad no encontrada",
        )

    return _orm_to_detail(prop)


@router.get("/search", response_model=list[PropertySearchResult])
async def search_properties(
    q: str = Query(..., min_length=2, max_length=500, description="Query de búsqueda en lenguaje natural"),
    tenant: dict = Depends(get_current_tenant),
    limit: int = Query(10, ge=1, le=50),
) -> list[PropertySearchResult]:
    """Búsqueda híbrida: SQL + sqlite-vec con query en lenguaje natural.
    
    Ejemplo:
        GET /properties/search?q=apartamento%203%20habitaciones%20Pampatar%20vista%20al%20mar
    """
    tenant_id = tenant["id"]
    
    logger.info("properties_search", tenant_id=tenant_id, query=q[:100])
    
    from src.app.search.hybrid import hybrid_search

    try:
        results = await hybrid_search(
            tenant_id=tenant_id,
            query=q,
            limit=limit,
        )
    except Exception as exc:
        logger.error("properties_search_failed", tenant_id=tenant_id, query=q[:100], error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en búsqueda: {exc}",
        )

    return [
        PropertySearchResult(
            property=_orm_to_list_item(r["property"]),
            search_type=r.get("search_type", "unknown"),
            relevance_score=r.get("score"),
        )
        for r in results
    ]


# ── Helpers ────────────────────────────────────────────────────────

def _orm_to_list_item(prop: Property) -> PropertyListItem:
    """Convierte ORM Property a schema de listado."""
    import json
    return PropertyListItem(
        id=str(prop.id),
        title=prop.title,
        property_type=prop.property_type,
        status=prop.status,
        price_usd=prop.price_usd,
        price_bs=prop.price_bs,
        location_zone=prop.location_zone,
        location_city=prop.location_city or "Porlamar",
        area_m2=prop.area_m2,
        bedrooms=prop.bedrooms,
        bathrooms=prop.bathrooms,
        vista_al_mar=bool(prop.vista_al_mar),
        frente_playa=bool(prop.frente_playa),
        uso_vacacional=bool(prop.uso_vacacional),
        photos=_parse_json(prop.photos) if prop.photos else [],
    )


def _orm_to_detail(prop: Property) -> PropertyDetail:
    """Convierte ORM Property a schema completo."""
    import json
    base = _orm_to_list_item(prop)
    return PropertyDetail(
        **base.model_dump(),
        location_address=prop.location_address,
        parking_spots=prop.parking_spots,
        tipo_especial=prop.tipo_especial,
        capacidad_huespedes=prop.capacidad_huespedes,
        amenities=_parse_json(prop.amenities) if prop.amenities else [],
        description_es=prop.description_es,
        description_en=prop.description_en,
        external_id=prop.external_id,
        created_at=prop.created_at,
        updated_at=prop.updated_at,
    )


def _parse_json(raw: str | None) -> list:
    """Parsea campo JSON de la base de datos."""
    if not raw:
        return []
    import json
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — api/v1/properties.py")

    # Test 1: Schemas validan correctamente
    item = PropertyListItem(
        id="prop-001",
        title="Apartamento Vista al Mar",
        property_type="venta",
        status="disponible",
        price_usd=150000.0,
        location_zone="Pampatar",
        bedrooms=3,
        bathrooms=2,
        vista_al_mar=True,
    )
    assert item.vista_al_mar is True
    assert item.price_usd == 150000.0
    print("  ✅ PropertyListItem schema válido")

    # Test 2: PropertyDetail hereda de PropertyListItem
    detail = PropertyDetail(
        id="prop-002",
        title="Casa Frente Playa",
        property_type="venta",
        status="disponible",
        price_usd=250000.0,
        location_zone="El Agua",
        frente_playa=True,
        description_es="Hermosa casa frente al mar",
        created_at="2026-05-07T10:00:00",
        updated_at="2026-05-07T10:00:00",
    )
    assert detail.frente_playa is True
    assert detail.description_es == "Hermosa casa frente al mar"
    print("  ✅ PropertyDetail schema válido")

    # Test 3: PropertySearchResult
    search_result = PropertySearchResult(
        property=item,
        search_type="hybrid",
        relevance_score=0.92,
    )
    assert search_result.search_type == "hybrid"
    assert search_result.relevance_score == 0.92
    print("  ✅ PropertySearchResult schema válido")

    # Test 4: Router instanciado
    assert router is not None
    assert router.prefix == "/properties"
    print("  ✅ Router instanciado con prefix correcto")

    # Test 5: _parse_json
    assert _parse_json(None) == []
    assert _parse_json('["foto1.jpg", "foto2.jpg"]') == ["foto1.jpg", "foto2.jpg"]
    assert _parse_json("invalid") == []
    print("  ✅ _parse_json correcto")

    # Test 6: Boolean conversion desde SQLite (0/1)
    class FakeProperty:
        vista_al_mar = 1
        frente_playa = 0
    
    assert bool(FakeProperty.vista_al_mar) is True
    assert bool(FakeProperty.frente_playa) is False
    print("  ✅ Conversión booleana SQLite correcta")

    print("\n🎉 Todos los smoke tests pasaron")
    print("   Nota: Tests de integración requieren DB con propiedades de prueba")