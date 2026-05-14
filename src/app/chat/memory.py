# src/app/chat/memory.py
"""Sistema de Memoria — RAM session + TTL/LRU + DB restore + concurrency-safe.

Mejoras V1 hardened:
  • Lock por session_id (previene race conditions websocket)
  • Lock global para session_store
  • Cleanup seguro sin mutación concurrente
  • Cap de mensajes en RAM
  • Restore robusto timezone-aware
  • Persistencia defensiva
  • Session lifecycle consistente
  • Preparado para migración Redis V2
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.models.message import Message
from src.app.db.models.session import Session as SessionModel

logger = get_logger("chat.memory")
settings = get_settings()


# ════════════════════════════════════════════════════════════════
# GLOBAL STATE (single-worker V1)
# ════════════════════════════════════════════════════════════════

session_store: dict[str, "SessionMemory"] = {}

# Lock global del store
_store_lock = asyncio.Lock()

# Lock individual por sesión
_session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# Protección cleanup singleton
_cleanup_running = False


# ════════════════════════════════════════════════════════════════
# SESSION MEMORY
# ════════════════════════════════════════════════════════════════

@dataclass(slots=True)
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

    def add_message(
        self,
        role: str,
        content: str,
        **extra: Any,
    ) -> None:
        """Agrega mensaje con cap de memoria."""

        self.touch()

        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **extra,
        })

        # Evitar crecimiento infinito RAM
        max_mem = settings.max_messages_in_memory

        if len(self.messages) > max_mem:
            overflow = len(self.messages) - max_mem
            del self.messages[:overflow]


# ════════════════════════════════════════════════════════════════
# PUBLIC API
# ════════════════════════════════════════════════════════════════

async def get_session_memory(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
) -> SessionMemory:
    """
    Obtiene o crea memoria de sesión de forma concurrency-safe.
    """

    async with _session_locks[session_id]:

        now = datetime.now(timezone.utc)

        # ── RAM Lookup ─────────────────────────────────────────

        async with _store_lock:

            mem = session_store.get(session_id)

            if mem:

                if _is_expired(
                    mem,
                    settings.session_ttl_minutes,
                    now,
                ):

                    logger.info(
                        "session_expired_ram",
                        session_id=session_id,
                        tenant_id=tenant_id,
                    )

                    del session_store[session_id]

                else:
                    mem.touch()
                    return mem

        # ── Restore DB ─────────────────────────────────────────

        restored = await _load_from_db(
            session=session,
            session_id=session_id,
            tenant_id=tenant_id,
        )

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

        # ── Create New ─────────────────────────────────────────

        new_mem = SessionMemory(
            session_id=session_id,
            tenant_id=tenant_id,
        )

        async with _store_lock:
            session_store[session_id] = new_mem

        await _create_db_session(
            session=session,
            session_id=session_id,
            tenant_id=tenant_id,
        )

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
    """
    Persistencia defensiva de estado de sesión.
    """

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
            tenant_id=memory.tenant_id,
        )

        return

    db_session.language = memory.language

    db_session.qualification_score = memory.qualification_score

    db_session.is_booking_active = (
        1 if memory.is_booking_active else 0
    )

    db_session.booking_step = memory.booking_step

    db_session.last_active_at = memory.last_active

    await session.commit()

    logger.debug(
        "session_saved",
        session_id=memory.session_id,
        qualification_score=memory.qualification_score,
    )


async def delete_session_memory(session_id: str) -> None:
    """
    Elimina sesión explícitamente del store RAM.
    """

    async with _store_lock:

        if session_id in session_store:
            del session_store[session_id]

    logger.info(
        "session_removed_from_ram",
        session_id=session_id,
    )


def get_active_session_count() -> int:
    """Número de sesiones activas."""
    return len(session_store)


def build_context_messages(
    memory: SessionMemory,
    max_messages: int | None = None,
) -> list[dict[str, Any]]:
    """
    Construye contexto compacto para LLM.
    """

    limit = (
        max_messages
        or settings.max_messages_in_context
    )

    selected = memory.messages[-limit:]

    context: list[dict[str, Any]] = []

    for idx, msg in enumerate(selected):

        content = msg.get("content", "")

        # Compactación de resultados antiguos
        if (
            idx != len(selected) - 1
            and msg.get("has_properties")
            and msg["role"] == "assistant"
        ):
            content = (
                f"[Presenté "
                f"{msg.get('property_count', 0)} "
                f"propiedades anteriormente]"
            )

        context.append({
            "role": msg["role"],
            "content": content,
        })

    return context


# ════════════════════════════════════════════════════════════════
# CLEANUP TASK
# ════════════════════════════════════════════════════════════════

async def cleanup_expired_sessions() -> None:
    """
    Background cleanup seguro.
    """

    global _cleanup_running

    if _cleanup_running:
        logger.warning("cleanup_already_running")
        return

    _cleanup_running = True

    logger.info(
        "session_cleanup_started",
        interval_seconds=settings.session_cleanup_interval_seconds,
    )

    try:

        while True:

            await asyncio.sleep(
                settings.session_cleanup_interval_seconds
            )

            now = datetime.now(timezone.utc)

            expired_ids: list[str] = []

            async with _store_lock:

                # snapshot seguro
                items = list(session_store.items())

                for sid, mem in items:

                    if _is_expired(
                        mem,
                        settings.session_ttl_minutes,
                        now,
                    ):
                        expired_ids.append(sid)

                for sid in expired_ids:
                    session_store.pop(sid, None)

            if expired_ids:

                logger.info(
                    "expired_sessions_cleaned",
                    expired_count=len(expired_ids),
                    remaining_active=len(session_store),
                )

    except asyncio.CancelledError:

        logger.info("session_cleanup_cancelled")
        raise

    except Exception as e:

        logger.exception(
            "session_cleanup_failed",
            error=str(e),
        )

    finally:
        _cleanup_running = False


# ════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ════════════════════════════════════════════════════════════════

def _is_expired(
    memory: SessionMemory,
    ttl_minutes: int,
    now: datetime,
) -> bool:
    """
    TTL check.
    """

    return (
        now - memory.last_active
        > timedelta(minutes=ttl_minutes)
    )


async def _load_from_db(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
) -> SessionMemory | None:
    """
    Restore de sesión desde SQLite.
    """

    result = await session.execute(
        select(SessionModel).where(
            SessionModel.id == session_id,
            SessionModel.tenant_id == tenant_id,
        )
    )

    db_session = result.scalar_one_or_none()

    if not db_session:
        return None

    msgs_result = await session.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(settings.max_messages_in_memory)
    )

    db_messages = msgs_result.scalars().all()

    messages: list[dict[str, Any]] = []

    for msg in reversed(db_messages):

        timestamp = msg.created_at

        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()

        messages.append({
            "role": msg.role,
            "content": msg.content,
            "timestamp": timestamp,
        })

    last_active = db_session.last_active_at

    if isinstance(last_active, str):
        last_active = datetime.fromisoformat(last_active)

    if last_active.tzinfo is None:
        last_active = last_active.replace(
            tzinfo=timezone.utc
        )

    return SessionMemory(
        session_id=session_id,
        tenant_id=tenant_id,
        language=db_session.language or "es",
        messages=messages,
        qualification_score=db_session.qualification_score or 0,
        is_booking_active=bool(
            db_session.is_booking_active
        ),
        booking_step=db_session.booking_step,
        last_active=last_active,
    )


async def _create_db_session(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
) -> None:
    """
    Creación idempotente defensiva.
    """

    existing = await session.execute(
        select(SessionModel).where(
            SessionModel.id == session_id
        )
    )

    if existing.scalar_one_or_none():
        return

    now = datetime.now(timezone.utc)

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

    logger.debug(
        "db_session_created",
        session_id=session_id,
    )