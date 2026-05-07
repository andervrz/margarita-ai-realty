# src/app/chat/engine.py
"""Chat Engine — motor conversacional end-to-end.

Orquestador principal del flujo conversacional. Coordina:
  1. Language Detection (ES/EN)
  2. Memory Load (RAM session + TTL check + DB restore)
  3. Filter Extraction (regex → LLM fallback)
  4. Hybrid Search (SQL → sqlite-vec)
  5. Context Build (truncado inteligente)
  6. LLM Call (Groq → Gemini fallback)
  7. Lead Qualifier (rule-based score)
  8. Response Assembly (explore / qualify / book)
  9. Memory Save (RAM + DB async)

Principios:
  - El LLM nunca decide, nunca inventa datos, nunca valida
  - Python valida todo: scores, thresholds, datos de lead
  - SQLite tiene la verdad estructural
  - Async-first, sin excepción
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.message import Message
from app.db.models.session import Session
from app.llm.client import chat_completion, LLMNoProviderAvailable
from app.llm.prompts.system_es import get_system_prompt_es
from app.llm.prompts.system_en import get_system_prompt_en
from app.llm.prompts.booking import get_booking_prompt, get_booking_summary
from app.llm.router import get_chat_model
from app.qualification.scorer import calculate_qualification_score, QualificationResult
from app.schemas.search import FilterQuery
from app.search.hybrid import hybrid_search, HybridSearchResult
from app.chat.memory import (
    SessionMemory,
    get_session_memory,
    save_session_memory,
    update_session_activity,
)

logger = get_logger()


# ── Función principal ─────────────────────────────────────────────

async def process_message(
    session_id: str,
    tenant_id: str,
    user_message: str,
    session: AsyncSession,
) -> ChatResponse:
    """Procesa un mensaje del usuario y retorna respuesta del bot.
    
    Args:
        session_id: ID de sesión (cookie del usuario).
        tenant_id: ID del tenant (aislamiento).
        user_message: Texto libre del usuario.
        session: Sesión SQLAlchemy async.
    
    Returns:
        ChatResponse con texto, estado de calificación, y flags de booking.
    """
    start_time = time.perf_counter()
    settings = get_settings()
    
    # ── 1. Language Detection ───────────────────────────────────
    language = _detect_language(user_message)
    
    # ── 2. Memory Load ──────────────────────────────────────────
    memory = await get_session_memory(session, session_id, tenant_id)
    memory.messages.append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    update_session_activity(memory)
    
    # ── 3. Filter Extraction ────────────────────────────────────
    # (Integrado en hybrid_search, pero logueamos resultado)
    
    # ── 4. Hybrid Search ────────────────────────────────────────
    search_result: HybridSearchResult = await hybrid_search(
        session=session,
        tenant_id=tenant_id,
        user_query=user_message,
        language=language,
    )
    
    # ── 5. Context Build ────────────────────────────────────────
    context_messages = _build_context_messages(
        memory=memory,
        search_result=search_result,
        language=language,
        tenant_name="Esparta Inmuebles",  # TODO: resolver desde tenant config
    )
    
    # ── 6. LLM Call ─────────────────────────────────────────────
    try:
        model = get_chat_model(tenant_plan="pro")  # TODO: resolver desde tenant
        response_text = await chat_completion(
            messages=context_messages,
            model=model,
            timeout=settings.llm_timeout,
        )
    except LLMNoProviderAvailable:
        response_text = _get_fallback_response(language, "llm_unavailable")
        logger.error(
            "llm_all_providers_failed",
            session_id=session_id,
            tenant_id=tenant_id,
        )
    
    # ── 7. Lead Qualifier ─────────────────────────────────────
    qual_result = calculate_qualification_score(
        messages=memory.messages,
        current_query=user_message,
        language=language,
    )
    memory.qualification_score = qual_result.total_score
    
    # ── 8. Response Assembly ──────────────────────────────────
    response_data = _assemble_response(
        response_text=response_text,
        qual_result=qual_result,
        memory=memory,
        search_result=search_result,
        language=language,
    )
    
    # ── 9. Memory Save ────────────────────────────────────────
    memory.messages.append({
        "role": "assistant",
        "content": response_data.final_text,
        "has_properties": search_result.count > 0,
        "property_count": search_result.count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    await save_session_memory(session, memory)
    
    # Persistir mensajes en DB
    await _persist_messages(session, session_id, tenant_id, user_message, response_data.final_text)
    
    duration_ms = (time.perf_counter() - start_time) * 1000
    
    logger.info(
        "chat_message_processed",
        session_id=session_id,
        tenant_id=tenant_id,
        language=language,
        qual_score=qual_result.total_score,
        qual_stage=qual_result.stage,
        search_source=search_result.source,
        search_count=search_result.count,
        duration_ms=round(duration_ms, 2),
    )
    
    return ChatResponse(
        text=response_data.final_text,
        qualification_score=qual_result.total_score,
        qualification_stage=qual_result.stage,
        is_booking_active=memory.is_booking_active,
        booking_step=memory.booking_step,
        properties_found=search_result.count,
        duration_ms=round(duration_ms, 2),
    )


# ── Response dataclass ──────────────────────────────────────────

class ChatResponse:
    """Respuesta del chat engine al caller (WebSocket/HTTP)."""
    
    def __init__(
        self,
        text: str,
        qualification_score: int,
        qualification_stage: str,
        is_booking_active: bool,
        booking_step: str | None,
        properties_found: int,
        duration_ms: float,
    ):
        self.text = text
        self.qualification_score = qualification_score
        self.qualification_stage = qualification_stage
        self.is_booking_active = is_booking_active
        self.booking_step = booking_step
        self.properties_found = properties_found
        self.duration_ms = duration_ms
    
    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "qualification_score": self.qualification_score,
            "qualification_stage": self.qualification_stage,
            "is_booking_active": self.is_booking_active,
            "booking_step": self.booking_step,
            "properties_found": self.properties_found,
            "duration_ms": self.duration_ms,
        }


class _ResponseAssembly:
    """Resultado interno del assembly de respuesta."""
    
    def __init__(self, final_text: str):
        self.final_text = final_text


# ── Helpers privados ────────────────────────────────────────────

def _detect_language(text: str) -> str:
    """Detección simple de idioma: ES por default, EN si hay señales claras."""
    # Señales de inglés
    en_markers = ["the", "and", "looking for", "house", "apartment", "buy", "rent", "investment"]
    text_lower = text.lower()
    
    # Si más del 30% de palabras son marcadores EN → inglés
    words = text_lower.split()
    if not words:
        return "es"
    
    en_count = sum(1 for w in words if w in en_markers)
    if en_count / len(words) > 0.3:
        return "en"
    
    return "es"


def _build_context_messages(
    memory: SessionMemory,
    search_result: HybridSearchResult,
    language: str,
    tenant_name: str,
) -> list[dict[str, str]]:
    """Construye mensajes para el LLM: system + context + properties + user."""
    settings = get_settings()
    
    # System prompt
    conversation_history = _format_conversation_history(memory.messages[-settings.max_messages_in_context:])
    properties_context = _format_properties_context(search_result)
    
    if language == "es":
        system_prompt = get_system_prompt_es(
            tenant_name=tenant_name,
            conversation_history=conversation_history,
            properties_context=properties_context,
            user_message="",  # Se añade al final como user message
        )
    else:
        system_prompt = get_system_prompt_en(
            tenant_name=tenant_name,
            conversation_history=conversation_history,
            properties_context=properties_context,
            user_message="",
        )
    
    # Construir lista de mensajes OpenAI-format
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    
    # Añadir historial reciente (últimos N mensajes, excluyendo el actual)
    recent = memory.messages[-settings.max_messages_in_context:-1]
    for msg in recent:
        if msg.get("has_properties") and msg["role"] == "assistant":
            # Referencia compacta para turnos anteriores con propiedades
            content = f"[Presenté {msg.get('property_count', 0)} propiedades en turno anterior]"
        else:
            content = msg["content"]
        
        messages.append({"role": msg["role"], "content": content})
    
    # User message actual (el que estamos procesando)
    # Ya está en memory.messages[-1], pero lo añadimos explícitamente
    user_msg = memory.messages[-1]
    messages.append({"role": "user", "content": user_msg["content"]})
    
    return messages


def _format_conversation_history(messages: list[dict]) -> str:
    """Formatea historial para inyección en prompt."""
    lines: list[str] = []
    for msg in messages:
        role = "Usuario" if msg["role"] == "user" else "Asistente"
        content = msg["content"]
        if len(content) > 200:
            content = content[:200] + "..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "Conversación iniciada."


def _format_properties_context(result: HybridSearchResult) -> str:
    """Formatea propiedades verificadas para inyección en prompt."""
    if not result.properties:
        return "No hay propiedades que coincidan con los criterios actuales."
    
    lines: list[str] = []
    lines.append(f"Encontradas {result.count} propiedades verificadas:")
    
    for i, prop in enumerate(result.properties, 1):
        # Formato compacto para contexto LLM
        desc = f"{i}. {prop.title}"
        if prop.price_usd:
            desc += f" — ${prop.price_usd:,.0f} USD"
        if prop.location_zone:
            desc += f" ({prop.location_zone})"
        if prop.bedrooms:
            desc += f" | {prop.bedrooms}H"
        if prop.bathrooms:
            desc += f"/{prop.bathrooms}B"
        if prop.area_m2:
            desc += f" | {prop.area_m2}m²"
        if prop.vista_al_mar:
            desc += " | 🌊 Vista al mar"
        if prop.frente_playa:
            desc += " | 🏖️ Frente playa"
        if prop.uso_vacacional:
            desc += " | 💰 Ideal inversión"
        
        lines.append(desc)
        
        # Descripción corta si existe
        if prop.description_es and len(prop.description_es) < 150:
            lines.append(f"   {prop.description_es}")
    
    return "\n".join(lines)


def _assemble_response(
    response_text: str,
    qual_result: QualificationResult,
    memory: SessionMemory,
    search_result: HybridSearchResult,
    language: str,
) -> _ResponseAssembly:
    """Ensambla respuesta final según etapa de calificación."""
    
    # Si booking está activo, manejar flujo de pasos
    if memory.is_booking_active:
        return _handle_booking_flow(memory, response_text, language)
    
    # Según stage de calificación
    if qual_result.stage == "book":
        # Activar booking flow
        memory.is_booking_active = True
        memory.booking_step = "nombre"
        
        # Añadir pregunta de booking a la respuesta del LLM
        booking_prompt = get_booking_prompt("nombre", language)
        final = f"{response_text}\n\n{booking_prompt}"
        return _ResponseAssembly(final)
    
    elif qual_result.stage == "qualify":
        # Añadir pregunta de calificación suave
        question = _get_qualification_question(qual_result, language)
        final = f"{response_text}\n\n{question}" if question else response_text
        return _ResponseAssembly(final)
    
    # Stage "explore" → respuesta del LLM sin modificaciones
    return _ResponseAssembly(response_text)


def _handle_booking_flow(
    memory: SessionMemory,
    response_text: str,
    language: str,
) -> _ResponseAssembly:
    """Maneja el flujo de booking paso a paso."""
    current_step = memory.booking_step or "nombre"
    
    # TODO: Validar datos del paso actual con Pydantic
    # TODO: Extraer datos del mensaje del usuario (regex/LLM)
    # TODO: Avanzar al siguiente paso
    
    # Por ahora, paso simple: avanzar secuencialmente
    steps_es = ["nombre", "email", "phone", "date", "time", "duration", "notes", "confirm"]
    steps_en = ["name", "email", "phone", "date", "time", "duration", "notes", "confirm"]
    steps = steps_es if language == "es" else steps_en
    
    try:
        current_idx = steps.index(current_step)
        if current_idx < len(steps) - 1:
            next_step = steps[current_idx + 1]
            memory.booking_step = next_step
            
            # Generar prompt del siguiente paso
            # TODO: Pasar datos recolectados como kwargs
            next_prompt = get_booking_prompt(next_step, language)
            final = f"{response_text}\n\n{next_prompt}"
            return _ResponseAssembly(final)
        else:
            # Confirmación final
            memory.is_booking_active = False
            memory.booking_step = None
            return _ResponseAssembly(response_text)
    except ValueError:
        # Paso inválido, resetear
        memory.booking_step = steps[0]
        return _ResponseAssembly(response_text)


def _get_qualification_question(
    qual_result: QualificationResult,
    language: str,
) -> str | None:
    """Genera pregunta de calificación según señales faltantes."""
    # TODO: Implementar basado en qual_result.missing_signals
    if language == "es":
        return "¿Tienes alguna preferencia de zona o presupuesto en mente? Me ayuda a mostrarte mejores opciones."
    return "Do you have any preferred area or budget in mind? It helps me show better options."


def _get_fallback_response(language: str, reason: str) -> str:
    """Respuesta de fallback cuando LLM no está disponible."""
    responses = {
        "es": {
            "llm_unavailable": "Estoy teniendo problemas técnicos momentáneos. Por favor, déjame tu número de contacto y un agente te llamará pronto. 📞",
        },
        "en": {
            "llm_unavailable": "I'm experiencing temporary technical issues. Please leave your contact number and an agent will call you soon. 📞",
        },
    }
    return responses.get(language, responses["es"]).get(reason, responses["es"]["llm_unavailable"])


async def _persist_messages(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
    user_content: str,
    assistant_content: str,
) -> None:
    """Guarda mensajes en SQLite para historial persistente."""
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


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    
    async def _test():
        print("🔥 Smoke Test — chat/engine.py")
        
        # Test 1: Language detection ES
        assert _detect_language("Hola, busco apartamento") == "es"
        print("  ✅ Detecta ES")
        
        # Test 2: Language detection EN
        assert _detect_language("Looking for a house with ocean view") == "en"
        print("  ✅ Detecta EN")
        
        # Test 3: Language detection ES por default
        assert _detect_language("xyz abc def") == "es"
        print("  ✅ Default ES")
        
        # Test 4: ChatResponse schema
        resp = ChatResponse(
            text="Test response",
            qualification_score=45,
            qualification_stage="qualify",
            is_booking_active=False,
            booking_step=None,
            properties_found=2,
            duration_ms=123.4,
        )
        d = resp.to_dict()
        assert d["text"] == "Test response"
        assert d["qualification_stage"] == "qualify"
        assert d["properties_found"] == 2
        print("  ✅ ChatResponse schema + serialización")
        
        # Test 5: _ResponseAssembly
        assembly = _ResponseAssembly("Hello world")
        assert assembly.final_text == "Hello world"
        print("  ✅ _ResponseAssembly")
        
        # Test 6: Fallback response ES
        fallback = _get_fallback_response("es", "llm_unavailable")
        assert "problemas técnicos" in fallback
        print("  ✅ Fallback ES")
        
        # Test 7: Fallback response EN
        fallback_en = _get_fallback_response("en", "llm_unavailable")
        assert "technical issues" in fallback_en
        print("  ✅ Fallback EN")
        
        # Test 8: _format_conversation_history
        msgs = [
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "Bienvenido"},
        ]
        hist = _format_conversation_history(msgs)
        assert "Usuario: Hola" in hist
        assert "Asistente: Bienvenido" in hist
        print("  ✅ Formato historial")
        
        # Test 9: Truncado en historial
        long_msg = {"role": "user", "content": "a" * 300}
        hist_long = _format_conversation_history([long_msg])
        assert "..." in hist_long
        print("  ✅ Truncado historial >200 chars")
        
        # Test 10: _format_properties_context vacío
        empty_result = MagicMock()
        empty_result.properties = []
        empty_result.count = 0
        ctx = _format_properties_context(empty_result)
        assert "No hay propiedades" in ctx
        print("  ✅ Properties context vacío")
        
        print("\n🎉 Todos los smoke tests pasaron")
    
    asyncio.run(_test())