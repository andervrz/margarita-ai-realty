# src/app/api/v1/ingestion.py
"""Ingestion API — CSV upload + pipeline de procesamiento.

Endpoints:
    POST /ingestion          → Subir CSV, ejecutar pipeline
    GET  /ingestion          → Listar ingestions del tenant
    GET  /ingestion/{id}     → Ver detalle de una ingestion

Flujo de upload:
    1. Cliente envía multipart/form-data con archivo CSV
    2. Server calcula SHA-256 del archivo (idempotencia)
    3. Si checksum ya existe → retorna ingestion anterior sin re-procesar
    4. Parsea CSV → valida con PropertyCSVRow → upsert en DB
    5. Genera embeddings sqlite-vec para propiedades nuevas/actualizadas
    6. Guarda IngestionLog con estadísticas completas
    7. Retorna resumen: inserted, updated, skipped, failed

Formato CSV esperado:
    external_id, title, property_type, price_usd, price_bs,
    location_city, location_zone, location_address, area_m2,
    bedrooms, bathrooms, parking_spots, vista_al_mar, frente_playa,
    uso_vacacional, tipo_especial, capacidad_huespedes,
    amenities, photos, description_es, description_en

Primera fila: headers. Encoding: UTF-8 con fallback a latin-1.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from src.app.api.middleware import get_current_tenant
from src.app.core.logging import get_logger
from src.app.db.engine import AsyncSessionLocal
from src.app.db.models.ingestion_log import IngestionLog
from src.app.ingestion.hasher import file_checksum
from src.app.ingestion.parser import parse_properties_csv
from src.app.ingestion.pipeline import IngestionPipeline

logger = get_logger(__name__)

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


# ── Schemas ───────────────────────────────────────────────────────

class IngestionResponse(BaseModel):
    """Resumen de una ingestion completada."""
    ingestion_id: str
    filename: str
    file_checksum: str
    status: str = Field(..., description="success | partial | failed | skipped")
    total_rows: int
    valid_rows: int
    inserted_rows: int
    updated_rows: int
    skipped_rows: int
    failed_rows: int
    errors: list[str] = Field(default_factory=list)


class IngestionListItem(BaseModel):
    """Item resumido de la lista de ingestions."""
    ingestion_id: str
    filename: str
    status: str
    created_at: str
    total_rows: int
    inserted_rows: int
    updated_rows: int


# ── Endpoints ─────────────────────────────────────────────────────

@router.post(
    "",
    response_model=IngestionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_csv(
    file: UploadFile = File(..., description="CSV o Excel con propiedades"),
    tenant: dict = Depends(get_current_tenant),
) -> IngestionResponse:
    """Sube un CSV de propiedades y ejecuta el pipeline de ingestion.

    Idempotente: el mismo archivo (mismo checksum) retorna el resultado
    de la ingestion anterior sin re-procesar.
    """
    tenant_id = tenant["id"]
    filename = file.filename or "upload.csv"

    logger.info("ingestion_upload_start", tenant_id=tenant_id, filename=filename)

    # Leer contenido del archivo
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Archivo vacío",
        )

    # Checksum para idempotencia
    checksum = file_checksum(content)
    logger.info(
        "ingestion_checksum_calculated",
        tenant_id=tenant_id,
        checksum_prefix=checksum[:16],
    )

    # Verificar si ya se procesó este archivo
    existing = await _find_existing_ingestion(tenant_id, checksum)
    if existing:
        logger.info(
            "ingestion_duplicate_skipped",
            tenant_id=tenant_id,
            existing_id=existing["id"],
        )
        return IngestionResponse(
            ingestion_id=existing["id"],
            filename=filename,
            file_checksum=checksum,
            status=existing["status"],
            total_rows=existing["total_rows"],
            valid_rows=existing["valid_rows"],
            inserted_rows=existing["inserted_rows"],
            updated_rows=existing["updated_rows"],
            skipped_rows=existing["skipped_rows"],
            failed_rows=existing["failed_rows"],
            errors=existing["errors"],
        )

    # Ejecutar pipeline completo
    pipeline = IngestionPipeline()

    try:
        async with AsyncSessionLocal() as session:
            result = await pipeline.process_csv(
                session=session,
                tenant_id=tenant_id,
                file_content=content,
                filename=filename,
            )
    except Exception as exc:
        logger.exception(
            "ingestion_pipeline_failed",
            tenant_id=tenant_id,
            filename=filename,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en pipeline de ingestion: {exc}",
        )

    logger.info(
        "ingestion_complete",
        tenant_id=tenant_id,
        filename=filename,
        status=result.status,
        inserted=result.inserted_rows,
        updated=result.updated_rows,
        skipped=result.skipped_rows,
        failed=result.failed_rows,
    )

    return IngestionResponse(
        ingestion_id=result.filename,  # pipeline retorna IngestionResult con filename
        filename=result.filename,
        file_checksum=checksum,
        status=result.status,
        total_rows=result.total_rows,
        valid_rows=result.valid_rows,
        inserted_rows=result.inserted_rows,
        updated_rows=result.updated_rows,
        skipped_rows=result.skipped_rows,
        failed_rows=result.failed_rows,
        errors=result.errors,
    )


@router.get("", response_model=list[IngestionListItem])
async def list_ingestions(
    tenant: dict = Depends(get_current_tenant),
    limit: int = 20,
    offset: int = 0,
) -> list[IngestionListItem]:
    """Lista las ingestions del tenant ordenadas por fecha descendente."""
    from sqlalchemy import select

    tenant_id = tenant["id"]

    async with AsyncSessionLocal() as session:
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
            created_at=str(log.created_at),
            total_rows=log.total_rows or 0,
            inserted_rows=log.inserted_rows or 0,
            updated_rows=log.updated_rows or 0,
        )
        for log in logs
    ]


@router.get("/{ingestion_id}", response_model=IngestionResponse)
async def get_ingestion(
    ingestion_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> IngestionResponse:
    """Obtiene el detalle de una ingestion específica."""
    from sqlalchemy import select

    tenant_id = tenant["id"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IngestionLog)
            .where(
                IngestionLog.id == ingestion_id,
                IngestionLog.tenant_id == tenant_id,
            )
        )
        log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ingestion '{ingestion_id}' no encontrada",
        )

    return IngestionResponse(
        ingestion_id=str(log.id),
        filename=log.filename,
        file_checksum=log.file_checksum or "",
        status=log.status,
        total_rows=log.total_rows or 0,
        valid_rows=log.valid_rows or 0,
        inserted_rows=log.inserted_rows or 0,
        updated_rows=log.updated_rows or 0,
        skipped_rows=log.skipped_rows or 0,
        failed_rows=log.failed_rows or 0,
        errors=log.errors_list,  # property definido en el modelo
    )


# ── Helpers Privados ──────────────────────────────────────────────

async def _find_existing_ingestion(
    tenant_id: str,
    checksum: str,
) -> dict | None:
    """Busca ingestion previa con mismo checksum (idempotencia)."""
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IngestionLog)
            .where(
                IngestionLog.tenant_id == tenant_id,
                IngestionLog.file_checksum == checksum,
            )
            .order_by(IngestionLog.created_at.desc())
            .limit(1)
        )
        log = result.scalar_one_or_none()

    if not log:
        return None

    return {
        "id": str(log.id),
        "status": log.status,
        "total_rows": log.total_rows or 0,
        "valid_rows": log.valid_rows or 0,
        "inserted_rows": log.inserted_rows or 0,
        "updated_rows": log.updated_rows or 0,
        "skipped_rows": log.skipped_rows or 0,
        "failed_rows": log.failed_rows or 0,
        "errors": log.errors_list,
    }


def _parse_errors(errors_raw: str | None) -> list[str]:
    """Parsea errores desde JSON string almacenado en DB."""
    if not errors_raw:
        return []
    try:
        parsed = json.loads(errors_raw)
        if isinstance(parsed, list):
            return [str(e) for e in parsed]
    except (json.JSONDecodeError, ValueError):
        pass
    return []


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — api/v1/ingestion.py\n")

    # Test 1: IngestionResponse schema
    resp = IngestionResponse(
        ingestion_id="test-001",
        filename="propiedades.csv",
        file_checksum="a" * 64,
        status="success",
        total_rows=100,
        valid_rows=98,
        inserted_rows=50,
        updated_rows=30,
        skipped_rows=18,
        failed_rows=2,
        errors=["Fila 5: precio inválido", "Fila 23: tipo desconocido"],
    )
    assert resp.total_rows == 100
    assert resp.status == "success"
    assert len(resp.errors) == 2
    print("✅ IngestionResponse schema válido")

    # Test 2: IngestionListItem schema
    item = IngestionListItem(
        ingestion_id="test-002",
        filename="casas.csv",
        status="partial",
        created_at="2027-05-07T15:00:00",
        total_rows=50,
        inserted_rows=30,
        updated_rows=10,
    )
    assert item.status == "partial"
    assert item.inserted_rows == 30
    print("✅ IngestionListItem schema válido")

    # Test 3: Router con prefix correcto
    assert router.prefix == "/ingestion"
    print("✅ Router prefix='/ingestion'")

    # Test 4: _parse_errors casos
    assert _parse_errors(None) == []
    assert _parse_errors("") == []
    assert _parse_errors('["error 1", "error 2"]') == ["error 1", "error 2"]
    assert _parse_errors("invalid json") == []
    assert _parse_errors('["single error"]') == ["single error"]
    print("✅ _parse_errors maneja todos los casos")

    # Test 5: file_checksum importado correctamente
    from src.app.ingestion.hasher import file_checksum
    data = b"test csv content para margarita"
    c1 = file_checksum(data)
    c2 = file_checksum(data)
    assert c1 == c2, "Debe ser determinístico"
    assert len(c1) == 64, f"SHA-256 debe ser 64 chars, got {len(c1)}"
    assert c1 != file_checksum(b"otro contenido"), "Archivos distintos → hashes distintos"
    print("✅ file_checksum determinístico y correcto")

    # Test 6: parse_properties_csv importado correctamente
    from src.app.ingestion.parser import parse_properties_csv
    assert callable(parse_properties_csv)
    print("✅ parse_properties_csv importado correctamente")

    # Test 7: IngestionPipeline importado correctamente
    from src.app.ingestion.pipeline import IngestionPipeline
    pipeline = IngestionPipeline()
    assert hasattr(pipeline, "process_csv")
    import inspect
    assert inspect.iscoroutinefunction(pipeline.process_csv)
    print("✅ IngestionPipeline con process_csv async")

    # Test 8: IngestionLog importado correctamente
    from src.app.db.models.ingestion_log import IngestionLog
    assert hasattr(IngestionLog, "errors_list"), \
        "IngestionLog debe tener property errors_list"
    print("✅ IngestionLog con errors_list property")

    # Test 9: AsyncSessionLocal importado correctamente
    from src.app.db.engine import AsyncSessionLocal
    assert AsyncSessionLocal is not None
    print("✅ AsyncSessionLocal importado correctamente")

    print("\n🎉 Todos los smoke tests pasaron ✅")
    print("   Nota: Tests de integración requieren DB y archivo CSV real")
