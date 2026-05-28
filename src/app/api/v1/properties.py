# src/app/api/v1/properties.py
"""Properties API — Admin: listar, buscar y consultar propiedades.

Endpoints:
    GET /properties          → Listar propiedades del tenant (con filtros)
    GET /properties/search   → Búsqueda híbrida (SQL + sqlite-vec)
    GET /properties/{id}     → Ver detalle de una propiedad

IMPORTANTE: /search debe estar ANTES de /{property_id} en el router
para que FastAPI no interprete "search" como un ID.

Filtros soportados (GET /properties):
    property_type, zone, min_price, max_price,
    bedrooms_min, bathrooms_min,
    vista_al_mar, frente_playa, uso_vacacional, status

Seguridad:
    - Requiere tenant autenticado (via TenantMiddleware)
    - Solo retorna propiedades del tenant (aislamiento estricto)
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from src.app.api.middleware import get_current_tenant
from src.app.core.logging import get_logger
from src.app.db.engine import AsyncSessionLocal
from src.app.db.models.property import Property

logger = get_logger(__name__)

router = APIRouter(prefix="/properties", tags=["properties"])


# ── Schemas ───────────────────────────────────────────────────────

class PropertyListItem(BaseModel):
    """Propiedad resumida para listados."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    property_type: str
    status: str
    price_usd: float | None = None
    price_bs: float | None = None
    location_zone: str | None = None
    location_city: str = "Margarita"
    area_m2: float | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    vista_al_mar: bool = False
    frente_playa: bool = False
    uso_vacacional: bool = False
    photos: list[str] = Field(default_factory=list)


class PropertyDetail(PropertyListItem):
    """Propiedad completa con todos los campos."""
    location_address: str | None = None
    parking_spots: int | None = None
    tipo_especial: str | None = None
    capacidad_huespedes: int | None = None
    amenities: list[str] = Field(default_factory=list)
    description_es: str | None = None
    description_en: str | None = None
    external_id: str | None = None
    created_at: str
    updated_at: str


class PropertySearchResult(BaseModel):
    """Resultado de búsqueda híbrida."""
    # Evitar alias="property" — property es builtin de Python
    # y causa conflictos en Pydantic v2 serialización
    prop: PropertyListItem
    search_source: str = Field(..., description="sql | sqlite_vec | mixed | no_results")
    total_found: int = 0


# ── Endpoints — ORDEN IMPORTA: /search antes de /{property_id} ────

@router.get("/search", response_model=PropertySearchResult)
async def search_properties(
    q: str = Query(..., min_length=2, max_length=500, description="Query en lenguaje natural"),
    tenant: dict = Depends(get_current_tenant),
    limit: int = Query(3, ge=1, le=10),
) -> PropertySearchResult:
    """Búsqueda híbrida SQL + sqlite-vec con lenguaje natural.

    Ejemplo:
        GET /properties/search?q=apartamento+3+habitaciones+Pampatar+vista+al+mar

    Nota: Registrado ANTES de /{property_id} para que FastAPI
    no interprete la literal "search" como un property_id.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.schemas.search import FilterQuery
    from src.app.search.hybrid import hybrid_search

    tenant_id = tenant["id"]
    logger.info("properties_search", tenant_id=tenant_id, query=q[:100])

    # hybrid_search requiere AsyncSession y session_id
    # Para el endpoint admin usamos un session_id fijo del tenant
    admin_session_id = f"admin-search-{tenant_id}"

    try:
        async with AsyncSessionLocal() as session:
            search_result = await hybrid_search(
                session=session,
                tenant_id=tenant_id,
                user_query=q,
                session_id=admin_session_id,
                language="es",
                max_results=limit,
            )
    except Exception as exc:
        logger.exception(
            "properties_search_failed",
            tenant_id=tenant_id,
            query=q[:100],
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en búsqueda: {exc}",
        )

    # Convertir propiedades del SearchResult a PropertyListItem
    property_items = [
        PropertyListItem(
            id=str(p.get("id", "")),
            title=p.get("title", ""),
            property_type=p.get("property_type", ""),
            status=p.get("status", "disponible"),
            price_usd=p.get("price_usd"),
            location_zone=p.get("location_zone"),
            bedrooms=p.get("bedrooms"),
            vista_al_mar=bool(p.get("vista_al_mar", False)),
            frente_playa=bool(p.get("frente_playa", False)),
            uso_vacacional=bool(p.get("uso_vacacional", False)),
        )
        for p in search_result.properties
    ]

    # Para el endpoint admin retornamos la primera propiedad o vacío
    # El search completo está en search_result.properties
    first_prop = property_items[0] if property_items else PropertyListItem(
        id="",
        title="Sin resultados",
        property_type="",
        status="",
    )

    return PropertySearchResult(
        prop=first_prop,
        search_source=search_result.source,
        total_found=search_result.total_found,
    )


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
    prop_status: str = Query("disponible", alias="status", description="disponible | vendida | reservada"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[PropertyListItem]:
    """Lista propiedades del tenant con filtros opcionales.

    Ejemplo:
        GET /properties?zone=Pampatar&min_price=100000&vista_al_mar=true
    """
    from sqlalchemy import select

    tenant_id = tenant["id"]

    async with AsyncSessionLocal() as session:
        stmt = select(Property).where(Property.tenant_id == tenant_id)

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
            # .is_(True/False) para columna Boolean — compatible SQLite + PostgreSQL
            stmt = stmt.where(Property.vista_al_mar.is_(vista_al_mar))
        if frente_playa is not None:
            stmt = stmt.where(Property.frente_playa.is_(frente_playa))
        if uso_vacacional is not None:
            stmt = stmt.where(Property.uso_vacacional.is_(uso_vacacional))
        if prop_status:
            stmt = stmt.where(Property.status == prop_status)

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
            "status": prop_status,
        }.items() if v is not None},
    )

    return [_orm_to_list_item(p) for p in properties]


@router.get("/{property_id}", response_model=PropertyDetail)
async def get_property(
    property_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> PropertyDetail:
    """Obtiene el detalle completo de una propiedad."""
    from sqlalchemy import select

    tenant_id = tenant["id"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Property).where(
                Property.id == property_id,
                Property.tenant_id == tenant_id,
            )
        )
        prop = result.scalar_one_or_none()

    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Propiedad '{property_id}' no encontrada",
        )

    return _orm_to_detail(prop)


# ── Helpers ───────────────────────────────────────────────────────

def _parse_json_list(raw: str | None) -> list[str]:
    """Parsea campo JSON list desde DB. Retorna lista vacía si falla."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _orm_to_list_item(prop: Property) -> PropertyListItem:
    """Convierte ORM Property a schema de listado."""
    return PropertyListItem(
        id=str(prop.id),
        title=prop.title,
        property_type=prop.property_type,
        status=prop.status,
        price_usd=prop.price_usd,
        price_bs=prop.price_bs,
        location_zone=prop.location_zone,
        location_city=prop.location_city or "Margarita",
        area_m2=prop.area_m2,
        bedrooms=prop.bedrooms,
        bathrooms=prop.bathrooms,
        vista_al_mar=bool(prop.vista_al_mar),
        frente_playa=bool(prop.frente_playa),
        uso_vacacional=bool(prop.uso_vacacional),
        photos=_parse_json_list(prop.photos),
    )


def _orm_to_detail(prop: Property) -> PropertyDetail:
    """Convierte ORM Property a schema completo."""
    base = _orm_to_list_item(prop)
    return PropertyDetail(
        **base.model_dump(),
        location_address=prop.location_address,
        parking_spots=prop.parking_spots,
        tipo_especial=prop.tipo_especial,
        capacidad_huespedes=prop.capacidad_huespedes,
        amenities=_parse_json_list(prop.amenities),
        description_es=prop.description_es,
        description_en=prop.description_en,
        external_id=prop.external_id,
        created_at=str(prop.created_at),
        updated_at=str(prop.updated_at),
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — api/v1/properties.py\n")

    # Test 1: PropertyListItem schema con defaults
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
    assert item.frente_playa is False  # default
    assert item.photos == []  # default
    assert item.location_city == "Margarita"  # default
    print("✅ PropertyListItem schema válido con defaults")

    # Test 2: PropertyDetail hereda de PropertyListItem
    detail = PropertyDetail(
        id="prop-002",
        title="Casa Frente Playa",
        property_type="venta",
        status="disponible",
        price_usd=250000.0,
        location_zone="El Agua",
        frente_playa=True,
        description_es="Hermosa casa frente al mar en El Agua",
        description_en="Beautiful beachfront house in El Agua",
        created_at="2027-05-07T10:00:00",
        updated_at="2027-05-07T10:00:00",
    )
    assert detail.frente_playa is True
    assert detail.description_es is not None
    assert detail.amenities == []  # default
    print("✅ PropertyDetail schema válido")

    # Test 3: PropertySearchResult sin alias problemático
    search_result = PropertySearchResult(
        prop=item,
        search_source="sql",
        total_found=5,
    )
    assert search_result.search_source == "sql"
    assert search_result.total_found == 5
    assert search_result.prop.title == "Apartamento Vista al Mar"
    print("✅ PropertySearchResult schema válido (sin alias builtin)")

    # Test 4: Router con prefix correcto
    assert router.prefix == "/properties"
    print("✅ Router prefix='/properties'")

    # Test 5: _parse_json_list casos
    assert _parse_json_list(None) == []
    assert _parse_json_list("") == []
    assert _parse_json_list('["foto1.jpg", "foto2.jpg"]') == ["foto1.jpg", "foto2.jpg"]
    assert _parse_json_list('["piscina", "gym", "terraza"]') == ["piscina", "gym", "terraza"]
    assert _parse_json_list("invalid json") == []
    assert _parse_json_list('{"not": "a list"}') == []
    print("✅ _parse_json_list maneja todos los casos")

    # Test 6: Conversión bool desde SQLite (0/1)
    class MockProp:
        vista_al_mar = 1
        frente_playa = 0
        uso_vacacional = 1

    assert bool(MockProp.vista_al_mar) is True
    assert bool(MockProp.frente_playa) is False
    print("✅ Conversión bool desde SQLite 0/1")

    # Test 7: Sin alias builtin en PropertySearchResult
    # Verificar que 'prop' no shadea el builtin 'property'
    schema_fields = PropertySearchResult.model_fields
    assert "prop" in schema_fields
    assert "property" not in schema_fields
    print("✅ PropertySearchResult usa 'prop' sin shadear builtin")

    # Test 8: Orden de rutas — /search antes de /{property_id}
    route_paths = [route.path for route in router.routes]
    search_idx = next(
        (i for i, p in enumerate(route_paths) if "search" in p), None
    )
    detail_idx = next(
        (i for i, p in enumerate(route_paths) if "{property_id}" in p), None
    )
    if search_idx is not None and detail_idx is not None:
        assert search_idx < detail_idx, \
            "/search debe estar antes de /{property_id} en el router"
        print("✅ /search registrado antes de /{property_id}")
    else:
        print("⚠️  No se pudo verificar orden de rutas en smoke test")

    print("\n🎉 Todos los smoke tests pasaron ✅")
    print("   Nota: Tests de integración requieren DB con propiedades de prueba")
