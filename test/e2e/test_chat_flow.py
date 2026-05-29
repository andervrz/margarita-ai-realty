# tests/e2e/test_chat_flow.py
"""Tests E2E del flujo completo de chat vía POST y WebSocket."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def test_health_endpoint(http_client):
    """GET /health retorna 200 y status ok."""
    response = await http_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_root_endpoint(http_client):
    """GET / retorna 200 y nombre del servicio."""
    response = await http_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert data.get("status") == "running"


async def test_post_chat_returns_response(http_client):
    """POST /api/v1 retorna respuesta del bot."""
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Bienvenido al chatbot de Margarita."

    with patch("app.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = mock_resp

        response = await http_client.post(
            "/api/v1",
            json={"message": "hola, busco apartamento"},
            headers={"X-Session-Id": "e2e-session-001"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "content" in data
    assert "session_id" in data
    assert data["language"] in ("es", "en")


async def test_post_chat_empty_message_rejected(http_client):
    """POST con mensaje vacío → 422 (validación Pydantic)."""
    response = await http_client.post(
        "/api/v1",
        json={"message": ""},
    )
    assert response.status_code == 422


async def test_post_chat_message_too_long(http_client):
    """POST con mensaje > 2000 chars → 422."""
    response = await http_client.post(
        "/api/v1",
        json={"message": "x" * 2001},
    )
    assert response.status_code == 422


async def test_post_chat_session_continuity(http_client):
    """Mismo session_id entre requests mantiene contexto."""
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Respuesta."

    session_id = "continuity-session-001"

    with patch("app.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = mock_resp

        r1 = await http_client.post(
            "/api/v1",
            json={"message": "hola"},
            headers={"X-Session-Id": session_id},
        )
        r2 = await http_client.post(
            "/api/v1",
            json={"message": "busco apartamento"},
            headers={"X-Session-Id": session_id},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["session_id"] == session_id
    assert r2.json()["session_id"] == session_id


async def test_post_chat_qualification_score_in_response(http_client):
    """Respuesta incluye qualification_score."""
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Tenemos opciones."

    with patch("app.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = mock_resp

        response = await http_client.post(
            "/api/v1",
            json={"message": "tengo $200k para invertir en Pampatar"},
        )

    data = response.json()
    assert "qualification_score" in data
    assert isinstance(data["qualification_score"], int)
    assert data["qualification_score"] >= 0
