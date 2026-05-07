# project/src/app/api/v1/ingestion.py
"""Ingestion API — CSV upload + pipeline de procesamiento.

Endpoints:
    POST /ingestion        → Subir CSV/Excel, procesar pipeline
    GET  /ingestion/{id}   → Ver estado de una ingestion
    GET  /ingestion        → Listar ingestions del tenant

Flujo de upload:
    1. Cliente envía multipart/form-data con archivo CSV
    2. Server calcula checksum SHA-256 del archivo
    3. Si checksum ya existe → retorna ingestion existente (idempotencia)
    4. Si es nuevo → parsea filas → valida con PropertyCSVRow → upsert DB
    5. Genera embeddings sqlite-vec para propiedades nuevas/actualizadas
    6. Guarda ingestion_log con diff completo
    7. Retorna resumen: inserted, updated, skipped, failed

Formato CSV esperado (columnas):
    external_id, title, property_type, price_usd, price_bs, location_city,
    location_zone, location_address, area_m2, bedrooms, bathrooms,
    parking_spots, vista_al_mar, frente_playa, uso_vacacional,
    tipo_especial, capacidad_huespedes, amenities, photos,
    description_es, description_en

Nota: La primera fila debe ser headers. Encoding: UTF-8 (con fallback a latin-1).
"""

from __future__ import annotations

import hashlib
import io
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.api.middleware import get_current_tenant
from app.core.config import settings
from app.core.logging import logger
from app.ingestion.hasher import file_checksum
from app.ingestion.parser import parse_csv
from app.ingestion.pipeline import run_ingestion_pipeline

if TYPE_CHECKING:
    pass


router = APIRouter(prefix="/ingestion", tags=["ingestion"])


# ── Schemas ────────────────────────────────────────────────────────

class IngestionResponse(BaseModel):
    """Resumen de una ingestion completada."""
    ingestion_id: str
    filename: str
    file_checksum: str
    status: str = Field(..., description="success | partial | failed")
    total_rows: int
    inserted_rows: int
    updated_rows: int
    skipped_rows: int
    failed_rows: int
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float


class IngestionListItem(BaseModel):
    """Item de la lista de ingestions."""
    ingestion_id: str
    filename: str
    status: str
    created_at: str
    total_rows: int


# ── Endpoints ──────────────────────────────────────────────────────

@router.post(
    "",
    response_model=IngestionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_csv(
    file: UploadFile = File(..., description="Archivo CSV o Excel con propiedades"),
    tenant: dict = Depends(get_current_tenant),
) -> IngestionResponse:
    """Sube un CSV de propiedades y ejecuta el pipeline de ingestion.
    
    Idempotente: si el mismo archivo se sube dos veces (mismo checksum),
    la segunda retorna el resultado de la primera sin re-procesar.
    """
    tenant_id = tenant["id"]
    filename = file.filename or "unknown"
    
    logger.info("ingestion_upload_start", tenant_id=tenant_id, filename=filename)

    # Leer contenido del archivo
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío")

    # Calcular checksum para idempotencia
    checksum = file_checksum(content)
    logger.info("ingestion_checksum", tenant_id=tenant_id, checksum=checksum[:16])

    # Verificar si ya existe ingestion con mismo checksum
    existing = await _find_existing_ingestion(tenant_id, checksum)
    if existing:
        logger.info("ingestion_duplicate", tenant_id=tenant_id, checksum=checksum[:16])
        return IngestionResponse(
            ingestion_id=existing["id"],
            filename=filename,
            file_checksum=checksum,
            status=existing["status"],
            total_rows=existing["total_rows"],
            inserted_rows=existing["inserted_rows"],
            updated_rows=existing["updated_rows"],
            skipped_rows=existing["skipped_rows"],
            failed_rows=existing["failed_rows"],
            errors=existing.get("errors", []),
            duration_seconds=0.0,
        )

    # Parsear CSV
    try:
        rows = parse_csv(io.BytesIO(content))
    except Exception as exc:
        logger.error("ingestion_parse_failed", tenant_id=tenant_id, error=str(exc))
        raise HTTPException(status_code=400, detail=f"Error parseando CSV: {exc}")

    if not rows:
        raise HTTPException(status_code=400, detail="CSV no contiene filas válidas")

    # Ejecutar pipeline
    ingestion_id = str(uuid4())
    logger.info("ingestion_pipeline_start", tenant_id=tenant_id, ingestion_id=ingestion_id, rows=len(rows))

    try:
        result = await run_ingestion_pipeline(
            ingestion_id=ingestion_id,
            tenant_id=tenant_id,
            filename=filename,
            file_checksum=checksum,
            rows=rows,
        )
    except Exception as exc:
        logger.error("ingestion_pipeline_failed", tenant_id=tenant_id, ingestion_id=ingestion_id, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Error en pipeline: {exc}")

    logger.info(
        "ingestion_pipeline_complete",
        tenant_id=tenant_id,
        ingestion_id=ingestion_id,
        inserted=result["inserted_rows"],
        updated=result["updated_rows"],
        skipped=result["skipped_rows"],
        failed=result["failed_rows"],
    )

    return IngestionResponse(
        ingestion_id=ingestion_id,
        filename=filename,
        file_checksum=checksum,
        status=result["status"],
        total_rows=result["total_rows"],
        inserted_rows=result["inserted_rows"],
        updated_rows=result["updated_rows"],
        skipped_rows=result["skipped_rows"],
        failed_rows=result["failed_rows"],
        errors=result.get("errors", []),
        duration_seconds=result.get("duration_seconds", 0.0),
    )


@router.get("", response_model=list[IngestionListItem])
async def list_ingestions(
    tenant: dict = Depends(get_current_tenant),
    limit: int = 20,
    offset: int = 0,
) -> list[IngestionListItem]:
    """Lista las ingestions del tenant ordenadas por fecha descendente."""
    tenant_id = tenant["id"]
    
    from sqlalchemy import select
    from app.db.models.ingestion import IngestionLog
    from app.db.engine import async_session_maker

    async with async_session_maker() as session:
        result = await session.execute(
            select(IngestionLog)
            .where(IngestionLog.tenant_id == tenant_id)
            .order_by(IngestionLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        logs = result.scalars().all()

    return [
        IngestionListItem(
            ingestion_id=str(log.id),
            filename=log.filename,
            status=log.status,
            created_at=log.created_at,
            total_rows=log.total_rows or 0,
        )
        for log in logs
    ]


@router.get("/{ingestion_id}", response_model=IngestionResponse)
async def get_ingestion(
    ingestion_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> IngestionResponse:
    """Obtiene el detalle de una ingestion específica."""
    tenant_id = tenant["id"]
    
    from sqlalchemy import select
    from app.db.models.ingestion import IngestionLog
    from app.db.engine import async_session_maker

    async with async_session_maker() as session:
        result = await session.execute(
            select(IngestionLog)
            .where(IngestionLog.id == ingestion_id)
            .where(IngestionLog.tenant_id == tenant_id)
        )
        log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(status_code=404, detail="Ingestion no encontrada")

    return IngestionResponse(
        ingestion_id=str(log.id),
        filename=log.filename,
        file_checksum=log.file_checksum,
        status=log.status,
        total_rows=log.total_rows or 0,
        inserted_rows=log.inserted_rows or 0,
        updated_rows=log.updated_rows or 0,
        skipped_rows=log.skipped_rows or 0,
        failed_rows=log.failed_rows or 0,
        errors=_parse_errors(log.errors),
        duration_seconds=0.0,
    )


# ── Helpers ────────────────────────────────────────────────────────

async def _find_existing_ingestion(tenant_id: str, checksum: str) -> dict | None:
    """Busca ingestion previa con mismo checksum para idempotencia."""
    from sqlalchemy import select
    from app.db.models.ingestion import IngestionLog
    from app.db.engine import async_session_maker

    async with async_session_maker() as session:
        result = await session.execute(
            select(IngestionLog)
            .where(IngestionLog.tenant_id == tenant_id)
            .where(IngestionLog.file_checksum == checksum)
            .order_by(IngestionLog.created_at.desc())
        )
        log = result.scalar_one_or_none()
        if not log:
            return None
        return {
            "id": str(log.id),
            "status": log.status,
            "total_rows": log.total_rows or 0,
            "inserted_rows": log.inserted_rows or 0,
            "updated_rows": log.updated_rows or 0,
            "skipped_rows": log.skipped_rows or 0,
            "failed_rows": log.failed_rows or 0,
            "errors": _parse_errors(log.errors),
        }


def _parse_errors(errors_raw: str | None) -> list[str]:
    """Parsea errores desde JSON string."""
    if not errors_raw:
        return []
    import json
    try:
        parsed = json.loads(errors_raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return []


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — api/v1/ingestion.py")

    # Test 1: Schemas validan correctamente
    resp = IngestionResponse(
        ingestion_id="test-001",
        filename="propiedades.csv",
        file_checksum="a" * 64,
        status="success",
        total_rows=100,
        inserted_rows=50,
        updated_rows=30,
        skipped_rows=20,
        failed_rows=0,
        duration_seconds=2.5,
    )
    assert resp.total_rows == 100
    assert resp.status == "success"
    print("  ✅ IngestionResponse schema válido")

    # Test 2: IngestionListItem
    item = IngestionListItem(
        ingestion_id="test-002",
        filename="casas.csv",
        status="partial",
        created_at="2026-05-07T15:00:00",
        total_rows=50,
    )
    assert item.status == "partial"
    print("  ✅ IngestionListItem schema válido")

    # Test 3: Router instanciado
    assert router is not None
    assert router.prefix == "/ingestion"
    print("  ✅ Router instanciado con prefix correcto")

    # Test 4: _parse_errors
    assert _parse_errors(None) == []
    assert _parse_errors('["error 1", "error 2"]') == ["error 1", "error 2"]
    assert _parse_errors("invalid") == []
    print("  ✅ _parse_errors correcto")

    # Test 5: file_checksum es determinístico
    from app.ingestion.hasher import file_checksum
    data = b"test content"
    c1 = file_checksum(data)
    c2 = file_checksum(data)
    assert c1 == c2
    assert len(c1) == 64
    print("  ✅ file_checksum determinístico")

    print("\n🎉 Todos los smoke tests pasaron")
    print("   Nota: Tests de integración requieren DB y archivo CSV real")