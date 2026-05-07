# src/app/chat/memory.py
"""Sistema de Memoria — RAM session + TTL/LRU + background cleanup + DB restore.

Arquitectura de memoria híbrida:
  - RAM: sesiones activas en dict global (single-worker V1)
  - TTL: time-to-live por sesión. Expiradas se eliminan
  - DB: historial persistente en SQLite. Se carga si sesión expiró de RAM
  - Cleanup: background task asyncio que limpia cada 5 minutos

Reglas:
  - Sin cleanup la RAM crece indefinidamente
  - Session store es dict global — single-worker en V1
  - Redis para multi-worker → V2
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.message import Message
from app.db.models.session import Session as SessionModel

logger = get_logger()

# ── Session Store Global (single-worker V1) ───────────────────────

session_store: dict[str, SessionMemory] = {}


# ── Dataclass de memoria de sesión ────────────────────────────────

@dataclass
class SessionMemory:
    """Estado de sesión en RAM."""
    
    session_id: str
    tenant_id: str
    language: str = "es"
    messages: list[dict[str, Any]] = field(default_factory=list)
    qualification_score: int = 0
    is_booking_active: bool = False
    booking_step: str | None = None
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def touch(self) -> None:
        """Actualiza timestamp de última actividad."""
        self.last_active = datetime.now(timezone.utc)


# ── Funciones públicas ────────────────────────────────────────────

async def get_session_memory(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
) -> SessionMemory:
    """Obtiene o crea memoria de sesión.
    
    Flujo:
      1. Busca en RAM (session_store)
      2. Si existe y no expiró → retorna
      3. Si expiró o no existe → carga desde SQLite
      4. Si no existe en DB → crea nueva
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    
    # 1. Verificar RAM
    if session_id in session_store:
        mem = session_store[session_id]
        if _is_expired(mem, settings.session_ttl_minutes, now):
            logger.info(
                "session_ram_expired",
                session_id=session_id,
                tenant_id=tenant_id,
                inactive_minutes=settings.session_ttl_minutes,
            )
            del session_store[session_id]
        else:
            return mem
    
    # 2. Cargar desde DB
    mem = await _load_from_db(session, session_id, tenant_id)
    if mem:
        session_store[session_id] = mem
        logger.info(
            "session_restored_from_db",
            session_id=session_id,
            tenant_id=tenant_id,
            message_count=len(mem.messages),
        )
        return mem
    
    # 3. Crear nueva
    new_mem = SessionMemory(
        session_id=session_id,
        tenant_id=tenant_id,
    )
    session_store[session_id] = new_mem
    
    # Persistir en DB
    await _create_db_session(session, session_id, tenant_id)
    
    logger.info(
        "session_created",
        session_id=session_id,
        tenant_id=tenant_id,
    )
    return new_mem


async def save_session_memory(
    session: AsyncSession,
    memory: SessionMemory,
) -> None:
    """Guarda estado de sesión en DB (score, booking, etc.)."""
    memory.touch()
    
    result = await session.execute(
        select(SessionModel).where(SessionModel.id == memory.session_id)
    )
    db_session = result.scalar_one_or_none()
    
    if db_session:
        db_session.qualification_score = memory.qualification_score
        db_session.is_booking_active = 1 if memory.is_booking_active else 0
        db_session.booking_step = memory.booking_step
        db_session.last_active_at = memory.last_active.isoformat()
        db_session.language = memory.language
        await session.commit()
        
        logger.debug(
            "session_saved",
            session_id=memory.session_id,
            score=memory.qualification_score,
        )


def update_session_activity(memory: SessionMemory) -> None:
    """Actualiza timestamp de actividad (llamar en cada mensaje)."""
    memory.touch()


async def cleanup_expired_sessions(ttl_minutes: int | None = None) -> None:
    """Limpia sesiones expiradas del store RAM.
    
    Llama desde lifespan de FastAPI como background task.
    """
    settings = get_settings()
    ttl = ttl_minutes or settings.session_ttl_minutes
    
    while True:
        await asyncio.sleep(settings.session_cleanup_interval_seconds)
        
        now = datetime.now(timezone.utc)
        expired: list[str] = []
        
        for sid, mem in session_store.items():
            if _is_expired(mem, ttl, now):
                expired.append(sid)
        
        for sid in expired:
            del session_store[sid]
        
        if expired:
            logger.info(
                "session_cleanup",
                expired_count=len(expired),
                active_count=len(session_store),
                ttl_minutes=ttl,
            )


def get_active_session_count() -> int:
    """Retorna número de sesiones activas en RAM."""
    return len(session_store)


def build_context_messages(
    memory: SessionMemory,
    max_messages: int | None = None,
) -> list[dict[str, Any]]:
    """Construye lista de mensajes para contexto LLM con truncado inteligente.
    
    Solo el turno actual lleva datos completos.
    Turnos anteriores con propiedades se reemplazan por referencia compacta.
    """
    settings = get_settings()
    limit = max_messages or settings.max_messages_in_context
    
    context: list[dict[str, Any]] = []
    messages = memory.messages[-limit:]
    
    for msg in messages:
        if msg.get("has_properties") and msg["role"] == "assistant":
            # Referencia compacta para turnos anteriores
            content = f"[Presenté {msg.get('property_count', 0)} propiedades en turno anterior]"
        else:
            content = msg["content"]
        
        context.append({
            "role": msg["role"],
            "content": content,
            "timestamp": msg.get("timestamp", ""),
        })
    
    return context


# ── Helpers privados ─────────────────────────────────────────────

def _is_expired(
    memory: SessionMemory,
    ttl_minutes: int,
    now: datetime,
) -> bool:
    """Verifica si una sesión expiró según TTL."""
    delta = now - memory.last_active
    return delta > timedelta(minutes=ttl_minutes)


async def _load_from_db(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
) -> SessionMemory | None:
    """Carga sesión y mensajes históricos desde SQLite."""
    result = await session.execute(
        select(SessionModel).where(
            SessionModel.id == session_id,
            SessionModel.tenant_id == tenant_id,
        )
    )
    db_session = result.scalar_one_or_none()
    
    if not db_session:
        return None
    
    # Cargar últimos mensajes
    msgs_result = await session.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(50)
    )
    db_messages = msgs_result.scalars().all()
    
    # Reconstruir mensajes en orden cronológico
    messages: list[dict[str, Any]] = []
    for msg in reversed(db_messages):
        messages.append({
            "role": msg.role,
            "content": msg.content,
            "timestamp": msg.created_at,
        })
    
    return SessionMemory(
        session_id=session_id,
        tenant_id=tenant_id,
        language=db_session.language or "es",
        messages=messages,
        qualification_score=db_session.qualification_score or 0,
        is_booking_active=bool(db_session.is_booking_active),
        booking_step=db_session.booking_step,
        last_active=datetime.fromisoformat(db_session.last_active_at),
    )


async def _create_db_session(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
) -> None:
    """Crea registro de sesión en DB."""
    now = datetime.now(timezone.utc).isoformat()
    db_session = SessionModel(
        id=session_id,
        tenant_id=tenant_id,
        language="es",
        qualification_score=0,
        is_booking_active=0,
        created_at=now,
        last_active_at=now,
    )
    session.add(db_session)
    await session.commit()


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    
    async def _test():
        print("🔥 Smoke Test — chat/memory.py")
        
        # Test 1: SessionMemory dataclass
        mem = SessionMemory(session_id="test-123", tenant_id="tenant-1")
        assert mem.session_id == "test-123"
        assert mem.language == "es"
        assert mem.qualification_score == 0
        assert mem.is_booking_active is False
        print("  ✅ SessionMemory creado")
        
        # Test 2: touch() actualiza last_active
        old_time = mem.last_active
        await asyncio.sleep(0.01)
        mem.touch()
        assert mem.last_active > old_time
        print("  ✅ touch() actualiza timestamp")
        
        # Test 3: _is_expired
        mem_expired = SessionMemory(
            session_id="expired",
            tenant_id="t1",
            last_active=datetime.now(timezone.utc) - timedelta(minutes=31),
        )
        assert _is_expired(mem_expired, 30, datetime.now(timezone.utc)) is True
        print("  ✅ Expiración detectada (>30min)")
        
        # Test 4: _is_expired no expirado
        mem_fresh = SessionMemory(
            session_id="fresh",
            tenant_id="t1",
            last_active=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        assert _is_expired(mem_fresh, 30, datetime.now(timezone.utc)) is False
        print("  ✅ No expirado (<30min)")
        
        # Test 5: build_context_messages con truncado
        mem_msgs = SessionMemory(
            session_id="ctx",
            tenant_id="t1",
            messages=[
                {"role": "user", "content": "Hola"},
                {"role": "assistant", "content": "Bienvenido", "has_properties": True, "property_count": 2},
                {"role": "user", "content": "Gracias"},
            ],
        )
        ctx = build_context_messages(mem_msgs, max_messages=10)
        assert len(ctx) == 3
        assert "[Presenté 2 propiedades" in ctx[1]["content"]
        print("  ✅ Context build con referencias compactas")
        
        # Test 6: build_context_messages limita mensajes
        many_msgs = [{"role": "user", "content": f"msg {i}"} for i in range(25)]
        mem_many = SessionMemory(session_id="many", tenant_id="t1", messages=many_msgs)
        ctx_limited = build_context_messages(mem_many, max_messages=5)
        assert len(ctx_limited) == 5
        print("  ✅ Context build limita a max_messages")
        
        # Test 7: get_active_session_count
        initial_count = get_active_session_count()
        session_store["test-session-1"] = SessionMemory("test-session-1", "t1")
        assert get_active_session_count() == initial_count + 1
        del session_store["test-session-1"]
        print("  ✅ get_active_session_count")
        
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())