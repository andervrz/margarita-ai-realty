# tests/integration/test_chat_engine.py
"""Tests de integración del motor conversacional."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def test_process_message_returns_chat_response(
    db_session, test_tenant, test_property
):
    """process_message retorna ChatResponse con campos correctos."""
    from app.chat.engine import process_message

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Encontré propiedades en Pampatar."

    with patch("app.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = mock_response

        response = await process_message(
            session_id="test-session-001",
            tenant_id=test_tenant.id,
            user_message="busco apartamento en Pampatar",
            session=db_session,
            tenant_name=test_tenant.name,
        )

    assert response.text is not None
    assert len(response.text) > 0
    assert response.language in ("es", "en")
    assert response.qualification_score >= 0
    assert response.qualification_stage in ("explore", "qualify", "book")


async def test_process_message_detects_spanish(
    db_session, test_tenant
):
    """Motor detecta español en el mensaje."""
    from app.chat.engine import process_message

    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Bienvenido al chatbot."

    with patch("app.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = mock_resp

        response = await process_message(
            session_id="sess-lang-es",
            tenant_id=test_tenant.id,
            user_message="hola, quiero información sobre propiedades",
            session=db_session,
        )

    assert response.language == "es"


async def test_process_message_detects_english(
    db_session, test_tenant
):
    """Motor detecta inglés en el mensaje."""
    from app.chat.engine import process_message

    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Welcome to the chatbot."

    with patch("app.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = mock_resp

        response = await process_message(
            session_id="sess-lang-en",
            tenant_id=test_tenant.id,
            user_message="looking for a beachfront apartment",
            session=db_session,
        )

    assert response.language == "en"


async def test_process_message_persists_messages(
    db_session, test_tenant
):
    """Mensajes se persisten en DB tras process_message."""
    from sqlalchemy import select
    from app.chat.engine import process_message
    from app.db.models.message import Message

    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = "Respuesta del bot."

    session_id = "sess-persist-001"
    with patch("app.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
        mock_ac.return_value = mock_resp
        await process_message(
            session_id=session_id,
            tenant_id=test_tenant.id,
            user_message="mensaje de prueba",
            session=db_session,
        )

    messages = (await db_session.execute(
        select(Message).where(Message.session_id == session_id)
    )).scalars().all()

    assert len(messages) == 2  # user + assistant
    roles = {m.role for m in messages}
    assert "user" in roles
    assert "assistant" in roles


async def test_process_message_empty_returns_fast(
    db_session, test_tenant
):
    """Mensaje vacío retorna sin llamar al LLM."""
    from app.chat.engine import process_message

    with patch("app.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
        response = await process_message(
            session_id="sess-empty",
            tenant_id=test_tenant.id,
            user_message="   ",
            session=db_session,
        )
        mock_ac.assert_not_called()

    assert response is not None
