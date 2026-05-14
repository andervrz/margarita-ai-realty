# src/app/chat/engine.py
"""Chat Engine — motor conversacional end-to-end.

Orquestador principal del flujo conversacional.

Mejoras aplicadas:
  - Imports corregidos y consistentes
  - ChatResponse migrado a dataclass
  - Integración real con language.py
  - Manejo seguro de errores DB/LLM
  - Persistencia robusta con rollback
  - Context truncation defensivo
  - Compatibilidad con SearchResult real
  - Mejor observabilidad
  - Menos lógica duplicada
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.chat.language import (
    detect_language,
    should_switch_language,
)
from src.app.chat.memory import (
    SessionMemory,
    build_context_messages,
    get_session_memory,
    save_session_memory,
    update_session_activity,
)
from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.models.message import Message
from src.app.llm.client import (
    LLMNoProviderAvailable,
    chat_completion,
)
from src.app.llm.prompts.booking import (
    get_booking_prompt,
)
from src.app.llm.prompts.system_en import get_system_prompt_en
from src.app.llm.prompts.system_es import get_system_prompt_es
from src.app.llm.router import get_chat_model
from src.app.qualification.scorer import (
    QualificationResult,
    calculate_qualification_score,
)
from src.app.schemas.search import SearchResult
from src.app.search.hybrid import hybrid_search

logger = get_logger("chat.engine")


# ── ChatResponse ──────────────────────────────────────────────────

@dataclass(slots=True)
class ChatResponse:
    """Respuesta serializable del chat engine."""

    text: str
    qualification_score: int
    qualification_stage: str
    is_booking_active: bool
    booking_step: str | None
    properties_found: int
    duration_ms: float
    language: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "qualification_score": self.qualification_score,
            "qualification_stage": self.qualification_stage,
            "is_booking_active": self.is_booking_active,
            "booking_step": self.booking_step,
            "properties_found": self.properties_found,
            "duration_ms": self.duration_ms,
            "language": self.language,
        }


@dataclass(slots=True)
class _ResponseAssembly:
    """Resultado interno del ensamblado final."""

    final_text: str


# ── Función principal ─────────────────────────────────────────────

async def process_message(
    session_id: str,
    tenant_id: str,
    user_message: str,
    session: AsyncSession,
) -> ChatResponse:
    """Procesa mensaje del usuario end-to-end."""

    start_time = time.perf_counter()
    settings = get_settings()

    logger.info(
        "chat_processing_started",
        session_id=session_id,
        tenant_id=tenant_id,
    )

    # ── Validación básica ──────────────────────────────────────

    user_message = user_message.strip()

    if not user_message:
        return ChatResponse(
            text="Tu mensaje está vacío.",
            qualification_score=0,
            qualification_stage="explore",
            is_booking_active=False,
            booking_step=None,
            properties_found=0,
            duration_ms=0,
            language="es",
        )

    # ── 1. Memory Load ─────────────────────────────────────────

    memory = await get_session_memory(
        session=session,
        session_id=session_id,
        tenant_id=tenant_id,
    )

    # ── 2. Language Detection ──────────────────────────────────

    lang_result = detect_language(user_message)

    if should_switch_language(memory.language, lang_result):
        logger.info(
            "language_switched",
            session_id=session_id,
            old_language=memory.language,
            new_language=lang_result.detected,
        )
        memory.language = lang_result.detected

    language = memory.language

    # ── 3. Registrar mensaje usuario ───────────────────────────

    now_iso = datetime.now(timezone.utc).isoformat()

    memory.messages.append(
        {
            "role": "user",
            "content": user_message,
            "timestamp": now_iso,
        }
    )

    update_session_activity(memory)

    # ── 4. Hybrid Search ───────────────────────────────────────

    try:
        search_result: SearchResult = await hybrid_search(
            session=session,
            tenant_id=tenant_id,
            user_query=user_message,
            session_id=session_id,
            language=language,
            max_results=settings.max_search_results,
        )

    except Exception as exc:
        logger.exception(
            "hybrid_search_failed",
            session_id=session_id,
            error=str(exc),
        )

        search_result = SearchResult(
            properties=[],
            source="search_error",
            total_found=0,
            filters_applied={},
            execution_metadata={"error": str(exc)},
        )

    # ── 5. Build Context ───────────────────────────────────────

    llm_messages = _build_context_messages(
        memory=memory,
        search_result=search_result,
        language=language,
        tenant_name="Esparta Inmuebles",
    )

    # ── 6. LLM Call ────────────────────────────────────────────

    try:
        model = get_chat_model(tenant_plan="pro")

        response_text = await chat_completion(
            messages=llm_messages,
            model=model,
            timeout=settings.llm_timeout,
        )

    except LLMNoProviderAvailable:
        logger.error(
            "llm_provider_unavailable",
            session_id=session_id,
        )

        response_text = _get_fallback_response(
            language=language,
            reason="llm_unavailable",
        )

    except Exception as exc:
        logger.exception(
            "llm_unexpected_error",
            session_id=session_id,
            error=str(exc),
        )

        response_text = _get_fallback_response(
            language=language,
            reason="llm_unavailable",
        )

    # ── 7. Qualification ───────────────────────────────────────

    qual_result: QualificationResult = calculate_qualification_score(
        messages=memory.messages,
        current_query=user_message,
        language=language,
    )

    memory.qualification_score = qual_result.total_score

    # ── 8. Response Assembly ───────────────────────────────────

    response_data = _assemble_response(
        response_text=response_text,
        qual_result=qual_result,
        memory=memory,
        search_result=search_result,
        language=language,
    )

    # ── 9. Registrar respuesta assistant ───────────────────────

    memory.messages.append(
        {
            "role": "assistant",
            "content": response_data.final_text,
            "has_properties": search_result.total_found > 0,
            "property_count": search_result.total_found,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

    # ── 10. Persistencia ───────────────────────────────────────

    try:
        await save_session_memory(session, memory)

        await _persist_messages(
            session=session,
            session_id=session_id,
            tenant_id=tenant_id,
            user_content=user_message,
            assistant_content=response_data.final_text,
        )

    except SQLAlchemyError as exc:
        logger.exception(
            "chat_persistence_failed",
            session_id=session_id,
            error=str(exc),
        )

        await session.rollback()

    # ── Finalización ───────────────────────────────────────────

    duration_ms = (time.perf_counter() - start_time) * 1000

    logger.info(
        "chat_message_processed",
        session_id=session_id,
        tenant_id=tenant_id,
        language=language,
        qualification_score=qual_result.total_score,
        qualification_stage=qual_result.stage,
        search_source=search_result.source,
        properties_found=search_result.total_found,
        duration_ms=round(duration_ms, 2),
    )

    return ChatResponse(
        text=response_data.final_text,
        qualification_score=qual_result.total_score,
        qualification_stage=qual_result.stage,
        is_booking_active=memory.is_booking_active,
        booking_step=memory.booking_step,
        properties_found=search_result.total_found,
        duration_ms=round(duration_ms, 2),
        language=language,
    )


# ── Context Builder ───────────────────────────────────────────────

def _build_context_messages(
    memory: SessionMemory,
    search_result: SearchResult,
    language: str,
    tenant_name: str,
) -> list[dict[str, str]]:
    """Construye mensajes OpenAI-format."""

    settings = get_settings()

    conversation_history = _format_conversation_history(
        build_context_messages(
            memory=memory,
            max_messages=settings.max_messages_in_context,
        )
    )

    properties_context = _format_properties_context(search_result)

    if language == "en":
        system_prompt = get_system_prompt_en(
            tenant_name=tenant_name,
            conversation_history=conversation_history,
            properties_context=properties_context,
            user_message="",
        )
    else:
        system_prompt = get_system_prompt_es(
            tenant_name=tenant_name,
            conversation_history=conversation_history,
            properties_context=properties_context,
            user_message="",
        )

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]

    recent_messages = build_context_messages(
        memory=memory,
        max_messages=settings.max_messages_in_context,
    )

    for msg in recent_messages:
        role = msg.get("role")
        content = str(msg.get("content", ""))[:4000]

        if role not in {"user", "assistant"}:
            continue

        messages.append(
            {
                "role": role,
                "content": content,
            }
        )

    return messages


# ── Formatters ────────────────────────────────────────────────────

def _format_conversation_history(
    messages: list[dict[str, Any]],
) -> str:
    """Formatea historial compacto para prompts."""

    if not messages:
        return "Conversación iniciada."

    lines: list[str] = []

    for msg in messages:
        role = "Usuario" if msg["role"] == "user" else "Asistente"

        content = str(msg.get("content", ""))

        if len(content) > 200:
            content = content[:200] + "..."

        lines.append(f"{role}: {content}")

    return "\n".join(lines)


def _format_properties_context(
    result: SearchResult,
) -> str:
    """Formatea propiedades verificadas."""

    if not result.properties:
        return "No hay propiedades que coincidan con los criterios actuales."

    lines: list[str] = []

    lines.append(
        f"Encontradas {result.total_found} propiedades verificadas:"
    )

    for idx, prop in enumerate(result.properties, start=1):

        title = prop.get("title", "Propiedad")

        line = f"{idx}. {title}"

        if prop.get("price_usd"):
            line += f" — ${prop['price_usd']:,.0f} USD"

        if prop.get("location_zone"):
            line += f" ({prop['location_zone']})"

        if prop.get("bedrooms"):
            line += f" | {prop['bedrooms']}H"

        if prop.get("bathrooms"):
            line += f"/{prop['bathrooms']}B"

        if prop.get("area_m2"):
            line += f" | {prop['area_m2']}m²"

        if prop.get("vista_al_mar"):
            line += " | 🌊 Vista al mar"

        if prop.get("frente_playa"):
            line += " | 🏖️ Frente playa"

        if prop.get("uso_vacacional"):
            line += " | 💰 Ideal inversión"

        lines.append(line)

        description = prop.get("description_es")

        if description and len(description) < 150:
            lines.append(f"   {description}")

    return "\n".join(lines)


# ── Response Assembly ─────────────────────────────────────────────

def _assemble_response(
    response_text: str,
    qual_result: QualificationResult,
    memory: SessionMemory,
    search_result: SearchResult,
    language: str,
) -> _ResponseAssembly:
    """Ensambla respuesta final."""

    if memory.is_booking_active:
        return _handle_booking_flow(
            memory=memory,
            response_text=response_text,
            language=language,
        )

    if qual_result.stage == "book":

        memory.is_booking_active = True
        memory.booking_step = "nombre"

        booking_prompt = get_booking_prompt(
            step="nombre",
            language=language,
        )

        return _ResponseAssembly(
            final_text=f"{response_text}\n\n{booking_prompt}"
        )

    if qual_result.stage == "qualify":

        question = _get_qualification_question(
            qual_result=qual_result,
            language=language,
        )

        if question:
            return _ResponseAssembly(
                final_text=f"{response_text}\n\n{question}"
            )

    return _ResponseAssembly(final_text=response_text)


def _handle_booking_flow(
    memory: SessionMemory,
    response_text: str,
    language: str,
) -> _ResponseAssembly:
    """Flujo simplificado de booking."""

    steps_es = [
        "nombre",
        "email",
        "phone",
        "date",
        "time",
        "duration",
        "notes",
        "confirm",
    ]

    steps_en = [
        "name",
        "email",
        "phone",
        "date",
        "time",
        "duration",
        "notes",
        "confirm",
    ]

    steps = steps_es if language == "es" else steps_en

    current_step = memory.booking_step or steps[0]

    try:
        idx = steps.index(current_step)

        if idx >= len(steps) - 1:
            memory.is_booking_active = False
            memory.booking_step = None

            return _ResponseAssembly(final_text=response_text)

        next_step = steps[idx + 1]

        memory.booking_step = next_step

        prompt = get_booking_prompt(
            step=next_step,
            language=language,
        )

        return _ResponseAssembly(
            final_text=f"{response_text}\n\n{prompt}"
        )

    except ValueError:

        memory.booking_step = steps[0]

        return _ResponseAssembly(final_text=response_text)


# ── Qualification ─────────────────────────────────────────────────

def _get_qualification_question(
    qual_result: QualificationResult,
    language: str,
) -> str | None:
    """Pregunta suave de calificación."""

    if language == "en":
        return (
            "Do you have any preferred area or budget in mind? "
            "It helps me show better options."
        )

    return (
        "¿Tienes alguna preferencia de zona o presupuesto en mente? "
        "Me ayuda a mostrarte mejores opciones."
    )


# ── Fallbacks ─────────────────────────────────────────────────────

def _get_fallback_response(
    language: str,
    reason: str,
) -> str:
    """Fallback cuando LLM falla."""

    responses = {
        "es": {
            "llm_unavailable": (
                "Estoy teniendo problemas técnicos momentáneos. "
                "Por favor, intenta nuevamente en unos minutos. 📞"
            )
        },
        "en": {
            "llm_unavailable": (
                "I'm experiencing temporary technical issues. "
                "Please try again in a few minutes. 📞"
            )
        },
    }

    return responses.get(language, responses["es"]).get(
        reason,
        responses["es"]["llm_unavailable"],
    )


# ── Persistencia ──────────────────────────────────────────────────

async def _persist_messages(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
    user_content: str,
    assistant_content: str,
) -> None:
    """Persistencia de mensajes."""

    user_msg = Message(
        session_id=session_id,
        tenant_id=tenant_id,
        role="user",
        content=user_content,
    )

    assistant_msg = Message(
        session_id=session_id,
        tenant_id=tenant_id,
        role="assistant",
        content=assistant_content,
    )

    session.add(user_msg)
    session.add(assistant_msg)

    await session.commit()


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":

    import asyncio
    from unittest.mock import MagicMock

    async def _test():

        print("🔥 Smoke Test — chat/engine.py\n")

        # ── Test 1 ────────────────────────────────────────────

        resp = ChatResponse(
            text="Hola",
            qualification_score=45,
            qualification_stage="qualify",
            is_booking_active=False,
            booking_step=None,
            properties_found=2,
            duration_ms=100.5,
            language="es",
        )

        assert resp.to_dict()["language"] == "es"

        print("✅ ChatResponse serializable")

        # ── Test 2 ────────────────────────────────────────────

        fallback = _get_fallback_response(
            "es",
            "llm_unavailable",
        )

        assert "problemas técnicos" in fallback

        print("✅ Fallback ES")

        # ── Test 3 ────────────────────────────────────────────

        history = _format_conversation_history(
            [
                {
                    "role": "user",
                    "content": "Hola",
                },
                {
                    "role": "assistant",
                    "content": "Bienvenido",
                },
            ]
        )

        assert "Usuario: Hola" in history

        print("✅ Conversation history")

        # ── Test 4 ────────────────────────────────────────────

        long_text = "a" * 300

        history_long = _format_conversation_history(
            [
                {
                    "role": "user",
                    "content": long_text,
                }
            ]
        )

        assert "..." in history_long

        print("✅ Truncado historial")

        # ── Test 5 ────────────────────────────────────────────

        empty_result = MagicMock()
        empty_result.properties = []
        empty_result.total_found = 0

        ctx = _format_properties_context(empty_result)

        assert "No hay propiedades" in ctx

        print("✅ Properties context vacío")

        # ── Test 6 ────────────────────────────────────────────

        mock_result = MagicMock()
        mock_result.total_found = 1
        mock_result.properties = [
            {
                "title": "Apartamento Pampatar",
                "price_usd": 120000,
                "bedrooms": 2,
                "bathrooms": 2,
            }
        ]

        ctx2 = _format_properties_context(mock_result)

        assert "Apartamento Pampatar" in ctx2

        print("✅ Properties context con data")

        # ── Test 7 ────────────────────────────────────────────

        memory = SessionMemory(
            session_id="s1",
            tenant_id="t1",
        )

        qual = MagicMock()
        qual.stage = "qualify"

        result = _assemble_response(
            response_text="Aquí tienes opciones",
            qual_result=qual,
            memory=memory,
            search_result=mock_result,
            language="es",
        )

        assert "presupuesto" in result.final_text

        print("✅ Qualification assembly")

        # ── Test 8 ────────────────────────────────────────────

        memory_booking = SessionMemory(
            session_id="s2",
            tenant_id="t1",
            is_booking_active=True,
            booking_step="nombre",
        )

        booking_result = _handle_booking_flow(
            memory=memory_booking,
            response_text="Perfecto",
            language="es",
        )

        assert memory_booking.booking_step == "email"

        print("✅ Booking flow")

        print("\n🎉 Todos los smoke tests pasaron")

    asyncio.run(_test())