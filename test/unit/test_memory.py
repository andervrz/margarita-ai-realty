# tests/unit/test_memory.py
"""Tests unitarios del sistema de memoria de sesiones."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def test_session_memory_defaults():
    """SessionMemory crea con valores por defecto correctos."""
    from app.chat.memory import SessionMemory
    mem = SessionMemory(session_id="s1", tenant_id="t1")
    assert mem.language == "es"
    assert mem.qualification_score == 0
    assert mem.is_booking_active is False
    assert mem.booking_step is None
    assert mem.messages == []


def test_touch_updates_last_active():
    """touch() actualiza last_active."""
    from app.chat.memory import SessionMemory
    mem = SessionMemory(session_id="s1", tenant_id="t1")
    old = mem.last_active
    import asyncio; asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.01))
    mem.touch()
    assert mem.last_active >= old


def test_is_expired_ttl_exceeded():
    """Sesión expirada según TTL."""
    from app.chat.memory import SessionMemory, _is_expired
    expired_mem = SessionMemory(
        session_id="e", tenant_id="t1",
        last_active=datetime.now(timezone.utc) - timedelta(minutes=31),
    )
    assert _is_expired(expired_mem, 30, datetime.now(timezone.utc)) is True


def test_is_expired_ttl_not_exceeded():
    """Sesión activa no marcada como expirada."""
    from app.chat.memory import SessionMemory, _is_expired
    fresh_mem = SessionMemory(
        session_id="f", tenant_id="t1",
        last_active=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    assert _is_expired(fresh_mem, 30, datetime.now(timezone.utc)) is False


def test_build_context_messages_respects_limit():
    """build_context_messages trunca al límite máximo."""
    from app.chat.memory import SessionMemory, build_context_messages
    mem = SessionMemory(
        session_id="c", tenant_id="t1",
        messages=[{"role": "user", "content": f"msg {i}"} for i in range(25)],
    )
    ctx = build_context_messages(mem, max_messages=5)
    assert len(ctx) == 5
    # Verifica que son los últimos mensajes
    assert ctx[-1]["content"] == "msg 24"


def test_build_context_no_timestamp():
    """build_context_messages no incluye timestamp en el output."""
    from app.chat.memory import SessionMemory, build_context_messages
    mem = SessionMemory(
        session_id="c", tenant_id="t1",
        messages=[{"role": "user", "content": "test", "timestamp": "2027-01-01"}],
    )
    ctx = build_context_messages(mem, max_messages=10)
    assert len(ctx) == 1
    assert "timestamp" not in ctx[0]
    assert set(ctx[0].keys()) == {"role", "content"}


def test_build_context_compacts_old_property_messages():
    """Mensajes anteriores con propiedades se compactan."""
    from app.chat.memory import SessionMemory, build_context_messages
    mem = SessionMemory(
        session_id="c", tenant_id="t1",
        messages=[
            {"role": "user", "content": "busco apto"},
            {
                "role": "assistant",
                "content": "Encontré 2 propiedades...",
                "has_properties": True,
                "property_count": 2,
            },
            {"role": "user", "content": "dame más info"},
        ],
    )
    ctx = build_context_messages(mem, max_messages=10)
    # El mensaje del assistant (no el último) debe estar compactado
    assert len(ctx) == 3
    assert "[Presenté 2 propiedades" in ctx[1]["content"]


def test_build_context_last_message_not_compacted():
    """El último mensaje assistant NO se compacta."""
    from app.chat.memory import SessionMemory, build_context_messages
    mem = SessionMemory(
        session_id="c", tenant_id="t1",
        messages=[
            {
                "role": "assistant",
                "content": "Encontré estas propiedades",
                "has_properties": True,
                "property_count": 3,
            },
        ],
    )
    ctx = build_context_messages(mem, max_messages=10)
    # Es el único mensaje y es el último → no se compacta
    assert "Encontré estas propiedades" in ctx[0]["content"]
