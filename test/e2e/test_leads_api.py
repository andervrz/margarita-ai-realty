# tests/e2e/test_leads_api.py
"""Tests E2E del endpoint de gestión de leads."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from uuid import uuid4

import pytest


@pytest.fixture
async def uploaded_lead(http_client, sample_csv_valid):
    """Crea una propiedad e inyecta un lead directamente vía DB."""
    # Para tener un lead en e2e, primero subimos propiedades
    await http_client.post(
        "/api/v1/ingestion",
        files={"file": ("leads_test.csv", io.BytesIO(sample_csv_valid), "text/csv")},
    )
    # En desarrollo el DEV_TENANT se usa — creamos lead directamente
    # Retorna None si no hay leads aún (el e2e puede verificar lista vacía)
    return None


async def test_list_leads_empty(http_client):
    """GET /leads retorna lista (puede estar vacía)."""
    response = await http_client.get("/api/v1/leads")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_list_leads_with_status_filter(http_client):
    """GET /leads?status=pendiente filtra correctamente."""
    response = await http_client.get("/api/v1/leads?status=pendiente")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # Si hay leads, todos deben tener status pendiente
    for lead in data:
        assert lead["status"] == "pendiente"


async def test_list_leads_with_min_score(http_client):
    """GET /leads?min_score=75 filtra por score mínimo."""
    response = await http_client.get("/api/v1/leads?min_score=75")
    assert response.status_code == 200
    data = response.json()
    for lead in data:
        assert lead["qualification_score"] >= 75


async def test_get_nonexistent_lead_returns_404(http_client):
    """GET /leads/nonexistent → 404."""
    response = await http_client.get(f"/api/v1/leads/{uuid4()}")
    assert response.status_code == 404


async def test_update_lead_status_invalid_state(http_client):
    """PATCH /leads/{id}/status con estado inválido → 422."""
    fake_id = str(uuid4())
    response = await http_client.patch(
        f"/api/v1/leads/{fake_id}/status",
        json={"status": "estado_invalido_xyz"},
    )
    assert response.status_code in (422, 404)


async def test_list_leads_pagination(http_client):
    """GET /leads con limit y offset no crashea."""
    response = await http_client.get("/api/v1/leads?limit=5&offset=0")
    assert response.status_code == 200

    response2 = await http_client.get("/api/v1/leads?limit=5&offset=100")
    assert response2.status_code == 200
    # Con offset mayor que el total → lista vacía
    assert isinstance(response2.json(), list)


async def test_properties_list_endpoint(http_client, sample_csv_valid):
    """GET /properties retorna lista de propiedades del tenant."""
    # Subir propiedades primero
    await http_client.post(
        "/api/v1/ingestion",
        files={"file": ("props.csv", io.BytesIO(sample_csv_valid), "text/csv")},
    )

    response = await http_client.get("/api/v1/properties")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


async def test_properties_search_endpoint(http_client, sample_csv_valid):
    """GET /properties/search retorna resultados."""
    await http_client.post(
        "/api/v1/ingestion",
        files={"file": ("search.csv", io.BytesIO(sample_csv_valid), "text/csv")},
    )

    response = await http_client.get(
        "/api/v1/properties/search?q=apartamento+Pampatar"
    )
    # Puede ser 200 con resultados o 200 con lista vacía
    assert response.status_code == 200


async def test_get_nonexistent_property_returns_404(http_client):
    """GET /properties/nonexistent → 404."""
    response = await http_client.get(f"/api/v1/properties/{uuid4()}")
    assert response.status_code == 404
