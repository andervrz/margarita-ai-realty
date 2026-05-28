# src/app/chat/memory.py
"""Sistema de Memoria — RAM + TTL + DB restore + concurrency-safe.

Arquitectura:
  - RAM: sesiones activas en dict global (single-worker V1)
  - TTL: time-to-live por sesión con cleanup background task
  - DB: historial persistente en SQLite — se restaura si sesión expira
  - Locks: por sesión y global para prevenir race conditions

Migración V2: reemplazar session_store por Redis con TTL nativo.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logging import get_logger
from src.app.db.models.message import Message
from src.app.db.models.session import Session as SessionModel

logger = get_logger(__name__)

# ── Global State (single-worker V1) ──────────────────────────────

session_store: dict[str, SessionMemory] = {}
_store_lock = asyncio.Lock()
_session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_cleanup_running = False


# ── SessionMemory ─────────────────────────────────────────────────

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
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_active: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def touch(self) -> None:
        """Actualiza timestamp de actividad."""
        self.last_active = datetime.now(timezone.utc)

    def add_message(self, role: str, content: str, **extra: Any) -> None:
        """Agrega mensaje con cap de memoria para evitar crecimiento infinito."""
        from src.app.core.config import get_settings
        settings = get_settings()

        self.touch()
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **extra,
        })

        # Cap en RAM — los mensajes más antiguos se eliminan de RAM
        # pero permanecen en DB (no se pierden)
        max_mem = settings.max_messages_in_context * 2
        if len(self.messages) > max_mem:
            overflow = len(self.messages) - max_mem
            del self.messages[:overflow]


# ── API Pública ───────────────────────────────────────────────────

async def get_session_memory(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
) -> SessionMemory:
    """
    Obtiene o crea memoria de sesión (concurrency-safe).

    Flujo:
      1. Lock por sesión
      2. Verificar RAM
      3. Si expiró → eliminar y restaurar desde DB
      4. Si no existe → crear nueva
    """
    async with _session_locks[session_id]:
        from src.app.core.config import get_settings
        settings = get_settings()
        now = datetime.now(timezone.utc)

        # 1. RAM lookup
        async with _store_lock:
            mem = session_store.get(session_id)
            if mem:
                if _is_expired(mem, settings.session_ttl_minutes, now):
                    logger.info(
                        "session_expired_ram",
                        session_id=session_id,
                        tenant_id=tenant_id,
                    )
                    del session_store[session_id]
                else:
                    mem.touch()
                    return mem

        # 2. Restore desde DB
        restored = await _load_from_db(session, session_id, tenant_id)
        if restored:
            async with _store_lock:
                session_store[session_id] = restored
            logger.info(
                "session_restored_from_db",
                session_id=session_id,
                tenant_id=tenant_id,
                messages=len(restored.messages),
            )
            return restored

        # 3. Crear nueva
        new_mem = SessionMemory(session_id=session_id, tenant_id=tenant_id)
        async with _store_lock:
            session_store[session_id] = new_mem

        await _create_db_session(session, session_id, tenant_id)
        logger.info("session_created", session_id=session_id, tenant_id=tenant_id)
        return new_mem


async def save_session_memory(
    session: AsyncSession,
    memory: SessionMemory,
) -> None:
    """Persiste estado de sesión en DB."""
    memory.touch()

    result = await session.execute(
        select(SessionModel).where(
            SessionModel.id == memory.session_id,
            SessionModel.tenant_id == memory.tenant_id,
        )
    )
    db_session = result.scalar_one_or_none()

    if not db_session:
        logger.warning(
            "session_missing_during_save",
            session_id=memory.session_id,
        )
        return

    db_session.language = memory.language
    db_session.qualification_score = memory.qualification_score
    db_session.is_booking_active = memory.is_booking_active  # Boolean directo
    db_session.booking_step = memory.booking_step
    db_session.last_active_at = memory.last_active.isoformat()  # str para el modelo

    await session.commit()

    logger.debug(
        "session_saved",
        session_id=memory.session_id,
        score=memory.qualification_score,
    )


async def delete_session_memory(session_id: str) -> None:
    """Elimina sesión del store RAM explícitamente."""
    async with _store_lock:
        session_store.pop(session_id, None)
    logger.info("session_removed_from_ram", session_id=session_id)


def get_active_session_count() -> int:
    """Número de sesiones activas en RAM."""
    return len(session_store)


def build_context_messages(
    memory: SessionMemory,
    max_messages: int | None = None,
) -> list[dict[str, Any]]:
    """
    Construye lista de mensajes para contexto LLM con truncado inteligente.

    - Solo incluye role y content (sin timestamp — la API del LLM no lo acepta)
    - Mensajes anteriores con propiedades se compactan en referencia
    - El último mensaje assistant conserva contenido completo
    """
    from src.app.core.config import get_settings
    settings = get_settings()

    limit = max_messages or settings.max_messages_in_context
    selected = memory.messages[-limit:]
    context: list[dict[str, Any]] = []

    for idx, msg in enumerate(selected):
        content = msg.get("content", "")
        is_last = idx == len(selected) - 1

        # Compactar resultados de búsqueda de turnos anteriores
        # El turno actual conserva el contenido completo
        if (
            not is_last
            and msg.get("has_properties")
            and msg["role"] == "assistant"
        ):
            content = (
                f"[Presenté {msg.get('property_count', 0)} propiedades anteriormente]"
            )

        context.append({
            "role": msg["role"],
            "content": content,
            # No incluir timestamp — la API de Groq/Gemini solo acepta role y content
        })

    return context


def update_session_activity(memory: SessionMemory) -> None:
    """Actualiza timestamp de actividad (llamar en cada mensaje)."""
    memory.touch()


# ── Cleanup Background Task ───────────────────────────────────────

async def cleanup_expired_sessions() -> None:
    """
    Background task para limpiar sesiones expiradas.
    Llamar desde lifespan de FastAPI como asyncio.create_task().
    """
    global _cleanup_running
    from src.app.core.config import get_settings

    if _cleanup_running:
        logger.warning("cleanup_already_running")
        return

    _cleanup_running = True
    settings = get_settings()

    logger.info(
        "session_cleanup_started",
        interval_seconds=settings.session_cleanup_interval_seconds,
    )

    try:
        while True:
            await asyncio.sleep(settings.session_cleanup_interval_seconds)

            now = datetime.now(timezone.utc)
            expired_ids: list[str] = []

            async with _store_lock:
                # Snapshot seguro — no iterar y modificar simultáneamente
                items = list(session_store.items())
                for sid, mem in items:
                    if _is_expired(mem, settings.session_ttl_minutes, now):
                        expired_ids.append(sid)
                for sid in expired_ids:
                    session_store.pop(sid, None)

            if expired_ids:
                logger.info(
                    "expired_sessions_cleaned",
                    expired_count=len(expired_ids),
                    remaining=len(session_store),
                )

    except asyncio.CancelledError:
        logger.info("session_cleanup_cancelled")
        raise
    except Exception as e:
        logger.exception("session_cleanup_failed", error=str(e))
    finally:
        _cleanup_running = False


# ── Helpers Privados ──────────────────────────────────────────────

def _is_expired(
    memory: SessionMemory,
    ttl_minutes: int,
    now: datetime,
) -> bool:
    """Verifica si sesión expiró según TTL."""
    return now - memory.last_active > timedelta(minutes=ttl_minutes)


async def _load_from_db(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
) -> SessionMemory | None:
    """Restaura sesión y mensajes desde SQLite."""
    from src.app.core.config import get_settings
    settings = get_settings()

    result = await session.execute(
        select(SessionModel).where(
            SessionModel.id == session_id,
            SessionModel.tenant_id == tenant_id,
        )
    )
    db_session = result.scalar_one_or_none()
    if not db_session:
        return None

    # Cargar últimos mensajes en orden cronológico
    msgs_result = await session.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
        .limit(settings.max_messages_in_context * 2)
    )
    db_messages = msgs_result.scalars().all()

    messages: list[dict[str, Any]] = [
        {
            "role": msg.role,
            "content": msg.content,
            "timestamp": msg.created_at
            if isinstance(msg.created_at, str)
            else msg.created_at.isoformat(),
        }
        for msg in db_messages
    ]

    # Parsear last_active — puede ser str o datetime según el modelo
    last_active = db_session.last_active_at
    if isinstance(last_active, str):
        last_active = datetime.fromisoformat(last_active)
    if last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=timezone.utc)

    return SessionMemory(
        session_id=session_id,
        tenant_id=tenant_id,
        language=db_session.language or "es",
        messages=messages,
        qualification_score=db_session.qualification_score or 0,
        is_booking_active=bool(db_session.is_booking_active),
        booking_step=db_session.booking_step,
        last_active=last_active,
    )


async def _create_db_session(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
) -> None:
    """Crea registro de sesión en DB (idempotente)."""
    existing = await session.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    if existing.scalar_one_or_none():
        return  # Ya existe — no duplicar

    now = datetime.now(timezone.utc)
    db_session = SessionModel(
        id=session_id,
        tenant_id=tenant_id,
        language="es",
        qualification_score=0,
        is_booking_active=False,  # Boolean directo
        created_at=now.isoformat(),
        last_active_at=now.isoformat(),
    )
    session.add(db_session)
    await session.commit()
    logger.debug("db_session_created", session_id=session_id)


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio

    async def _test():
        print("🔥 Smoke Tests — chat/memory.py\n")

        # Test 1: SessionMemory básico
        mem = SessionMemory(session_id="s1", tenant_id="t1")
        assert mem.session_id == "s1"
        assert mem.language == "es"
        assert mem.qualification_score == 0
        assert mem.is_booking_active is False
        print("✅ SessionMemory creado")

        # Test 2: touch() actualiza last_active
        old = mem.last_active
        await asyncio.sleep(0.01)
        mem.touch()
        assert mem.last_active > old
        print("✅ touch() actualiza timestamp")

        # Test 3: _is_expired
        expired = SessionMemory(
            session_id="e",
            tenant_id="t1",
            last_active=datetime.now(timezone.utc) - timedelta(minutes=31),
        )
        assert _is_expired(expired, 30, datetime.now(timezone.utc)) is True
        print("✅ Expiración detectada")

        # Test 4: No expirado
        fresh = SessionMemory(
            session_id="f",
            tenant_id="t1",
            last_active=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        assert _is_expired(fresh, 30, datetime.now(timezone.utc)) is False
        print("✅ No expirado")

        # Test 5: build_context_messages con compactación
        mem_ctx = SessionMemory(
            session_id="c",
            tenant_id="t1",
            messages=[
                {"role": "user", "content": "Hola"},
                {
                    "role": "assistant",
                    "content": "Bienvenido",
                    "has_properties": True,
                    "property_count": 2,
                },
                {"role": "user", "content": "Gracias"},
            ],
        )
        ctx = build_context_messages(mem_ctx, max_messages=10)
        assert len(ctx) == 3
        # El mensaje del assistant (índice 1) NO es el último → se compacta
        assert "[Presenté 2 propiedades" in ctx[1]["content"]
        # Verificar que no hay timestamp en el contexto
        assert "timestamp" not in ctx[0]
        print("✅ Context build con compactación correcta")

        # Test 6: Límite de mensajes
        many = SessionMemory(
            session_id="m",
            tenant_id="t1",
            messages=[{"role": "user", "content": f"msg {i}"} for i in range(25)],
        )
        ctx_lim = build_context_messages(many, max_messages=5)
        assert len(ctx_lim) == 5
        print("✅ Límite de mensajes respetado")

        # Test 7: get_active_session_count
        initial = get_active_session_count()
        session_store["test-s"] = SessionMemory("test-s", "t1")
        assert get_active_session_count() == initial + 1
        del session_store["test-s"]
        print("✅ get_active_session_count")

        # Test 8: El último mensaje assistant NO se compacta
        mem_last = SessionMemory(
            session_id="last",
            tenant_id="t1",
            messages=[
                {
                    "role": "assistant",
                    "content": "Encontré estas propiedades",
                    "has_properties": True,
                    "property_count": 3,
                },
            ],
        )
        ctx_last = build_context_messages(mem_last, max_messages=10)
        # Solo hay un mensaje y es el último → NO se compacta
        assert "Encontré estas propiedades" in ctx_last[0]["content"]
        print("✅ Último mensaje assistant preserva contenido completo")

        print("\n🎉 Todos los smoke tests pasaron ✅")

    asyncio.run(_test())
