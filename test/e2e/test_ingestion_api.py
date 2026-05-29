# tests/e2e/test_ingestion_api.py
"""Tests E2E del endpoint de ingestion CSV."""

from __future__ import annotations

import io

import pytest


async def test_upload_csv_returns_201(http_client, sample_csv_valid):
    """POST /ingestion con CSV válido → 201."""
    response = await http_client.post(
        "/api/v1/ingestion",
        files={"file": ("propiedades.csv", io.BytesIO(sample_csv_valid), "text/csv")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] in ("success", "partial")
    assert data["inserted_rows"] >= 0
    assert "ingestion_id" in data
    assert "file_checksum" in data


async def test_upload_csv_idempotent(http_client, sample_csv_valid):
    """Subir el mismo CSV dos veces → segunda retorna 201 con skipped."""
    # Primera subida
    r1 = await http_client.post(
        "/api/v1/ingestion",
        files={"file": ("test.csv", io.BytesIO(sample_csv_valid), "text/csv")},
    )
    assert r1.status_code == 201

    # Segunda subida — mismo archivo
    r2 = await http_client.post(
        "/api/v1/ingestion",
        files={"file": ("test.csv", io.BytesIO(sample_csv_valid), "text/csv")},
    )
    assert r2.status_code == 201
    data2 = r2.json()
    # Segunda subida: mismo ingestion_id o skipped
    assert data2["inserted_rows"] == 0 or data2["ingestion_id"] == r1.json()["ingestion_id"]


async def test_upload_empty_file_returns_400(http_client):
    """Archivo vacío → 400."""
    response = await http_client.post(
        "/api/v1/ingestion",
        files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
    )
    assert response.status_code == 400


async def test_list_ingestions(http_client, sample_csv_valid):
    """GET /ingestion lista las ingestions del tenant."""
    # Crear una ingestion primero
    await http_client.post(
        "/api/v1/ingestion",
        files={"file": ("list_test.csv", io.BytesIO(sample_csv_valid), "text/csv")},
    )

    response = await http_client.get("/api/v1/ingestion")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


async def test_get_ingestion_detail(http_client, sample_csv_valid):
    """GET /ingestion/{id} retorna detalle correcto."""
    # Crear ingestion
    r = await http_client.post(
        "/api/v1/ingestion",
        files={"file": ("detail_test.csv", io.BytesIO(sample_csv_valid), "text/csv")},
    )
    ingestion_id = r.json()["ingestion_id"]

    # Obtener detalle
    response = await http_client.get(f"/api/v1/ingestion/{ingestion_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["ingestion_id"] == ingestion_id


async def test_get_nonexistent_ingestion_returns_404(http_client):
    """GET /ingestion/nonexistent → 404."""
    response = await http_client.get("/api/v1/ingestion/nonexistent-id-999")
    assert response.status_code == 404
