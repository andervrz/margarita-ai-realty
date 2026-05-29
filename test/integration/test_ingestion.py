# tests/integration/test_ingestion.py
"""Tests de integración del pipeline de ingestion CSV → SQLite + sqlite-vec."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models.property import Property
from app.db.models.ingestion_log import IngestionLog


async def test_pipeline_inserts_properties(
    db_session, test_tenant, sample_csv_valid
):
    """Pipeline completo inserta propiedades correctamente."""
    from app.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline()
    result = await pipeline.process_csv(
        session=db_session,
        tenant_id=test_tenant.id,
        file_content=sample_csv_valid,
        filename="test.csv",
    )

    assert result.inserted_rows == 3
    assert result.failed_rows == 0

    # Verificar en DB
    props = (await db_session.execute(
        select(Property).where(Property.tenant_id == test_tenant.id)
    )).scalars().all()
    assert len(props) == 3


async def test_pipeline_idempotent_same_file(
    db_session, test_tenant, sample_csv_valid
):
    """Segunda subida del mismo archivo → skipped (sin duplicados)."""
    from app.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline()
    result1 = await pipeline.process_csv(
        session=db_session,
        tenant_id=test_tenant.id,
        file_content=sample_csv_valid,
        filename="test.csv",
    )
    result2 = await pipeline.process_csv(
        session=db_session,
        tenant_id=test_tenant.id,
        file_content=sample_csv_valid,
        filename="test.csv",
    )

    assert result1.inserted_rows == 3
    # Segunda subida: 0 insertadas, 3 skipped
    assert result2.inserted_rows == 0
    assert result2.skipped_rows == 3


async def test_pipeline_saves_ingestion_log(
    db_session, test_tenant, sample_csv_valid
):
    """Pipeline guarda IngestionLog con estadísticas correctas."""
    from app.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline()
    await pipeline.process_csv(
        session=db_session,
        tenant_id=test_tenant.id,
        file_content=sample_csv_valid,
        filename="propiedades.csv",
    )

    logs = (await db_session.execute(
        select(IngestionLog).where(IngestionLog.tenant_id == test_tenant.id)
    )).scalars().all()
    assert len(logs) >= 1
    log = logs[0]
    assert log.filename == "propiedades.csv"
    assert log.status in ("success", "partial")
    assert log.total_rows >= 3


async def test_pipeline_tenant_isolation(
    db_session, test_tenant, test_tenant_2, sample_csv_valid
):
    """Propiedades de un tenant no se mezclan con las de otro."""
    from app.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline()
    await pipeline.process_csv(
        session=db_session,
        tenant_id=test_tenant.id,
        file_content=sample_csv_valid,
        filename="t1.csv",
    )

    # Tenant 2 no tiene propiedades
    props_t2 = (await db_session.execute(
        select(Property).where(Property.tenant_id == test_tenant_2.id)
    )).scalars().all()
    assert len(props_t2) == 0


async def test_pipeline_partial_invalid_csv(
    db_session, test_tenant, sample_csv_with_errors
):
    """CSV con filas inválidas → inserta válidas, registra errores."""
    from app.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline()
    result = await pipeline.process_csv(
        session=db_session,
        tenant_id=test_tenant.id,
        file_content=sample_csv_with_errors,
        filename="partial.csv",
    )

    # Al menos una fila válida (PROP001)
    assert result.valid_rows >= 1
    assert result.status in ("success", "partial", "failed")
