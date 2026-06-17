# src/app/chat/engine.py
"""Chat Engine — motor conversacional end-to-end.

Orquestador principal del flujo conversacional:
  1. Memory Load — restaura sesión desde RAM o DB
  2. Language Detection — detecta ES/EN del mensaje
  3. Hybrid Search — busca propiedades relevantes
  4. LLM Call — genera respuesta natural
  5. Qualification — calcula score del lead
  6. Response Assembly — ensambla respuesta + preguntas
  7. Persistence — guarda mensajes en DB

Principios:
  - El LLM genera lenguaje — Python decide lógica
  - SQLite tiene la verdad de propiedades
  - Fallo en un componente no rompe el flujo completo
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import logfire
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.language import detect_language, should_switch_language
from app.chat.memory import (
    SessionMemory,
    build_context_messages,
    get_session_memory,
    save_session_memory,
    update_session_activity,
)
from app.core.config import get_settings
from app.core.constants import LeadStatus
from app.core.logging import get_logger
from app.db.models.message import Message
from app.leads.services import create_lead_from_booking, update_lead_status
from app.leads.validator import (
    _validate_phone_value,
    sanitize_name,
    validate_email,
    validate_name,
)
from app.llm.client import LLMNoProviderAvailable, chat_completion
from app.llm.prompt.booking import get_booking_prompt
from app.llm.prompt.system_en import get_system_prompt_en
from app.llm.prompt.system_es import get_system_prompt_es
from app.llm.router import get_chat_model
from app.notification.dispatcher import dispatch_booking_notifications
from app.qualification.score import QualificationResult, calculate_qualification_score
from app.schemas.search import SearchResult
from app.search.hybrid import hybrid_search

logger = get_logger(__name__)

# ── Booking Steps ─────────────────────────────────────────────────
# DURATION eliminado — la duración la define el tenant, no el usuario

# Flujo simplificado: nombre y apellido → teléfono → correo → fecha (opcional) → confirmar.
# La fecha NO bloquea: si el usuario no la concreta, igual se cierra el booking.
BOOKING_STEPS_ES = ["nombre", "phone", "email", "date", "confirm"]
BOOKING_STEPS_EN = ["name", "phone", "email", "date", "confirm"]

# Intención explícita de visitar/conocer una propiedad → dispara el booking
# aunque el score de calificación no haya llegado al umbral.
_BOOKING_INTENT_ES = (
    "visita", "visitar", "agendar", "agenda", "cita", "verla", "verlo",
    "ver la propiedad", "conocer", "coordinar",
)
_BOOKING_INTENT_EN = (
    "visit", "schedule", "appointment", "see the property", "see it",
    "book a", "tour",
)

# Cierre del booking — el dueño/agente se comunicará con el interesado.
_BOOKING_CLOSING_ES = (
    "¡Listo! 🙌 El dueño o agente de la propiedad se comunicará contigo muy pronto "
    "para coordinar los detalles. ¡Gracias por tu interés! 🏝️"
)
_BOOKING_CLOSING_EN = (
    "All set! 🙌 The property owner or agent will contact you very soon to coordinate "
    "the details. Thanks for your interest! 🏝️"
)


def _has_booking_intent(text: str, language: str) -> bool:
    """Detecta si el usuario quiere visitar/conocer una propiedad."""
    keywords = _BOOKING_INTENT_ES if language == "es" else _BOOKING_INTENT_EN
    low = text.lower()
    return any(kw in low for kw in keywords)


def _build_suggestions(
    memory: SessionMemory,
    properties_in_focus: list[dict[str, Any]],
    language: str,
) -> list[str]:
    """Genera quick replies / chips contextuales para el widget.

    - Durante booking: sin chips (el usuario está dando sus datos).
    - Sin propiedades en foco (saludo/descubrimiento/caso sin criterios):
      chips de intención para destrabar la conversación.
    - Con propiedades en foco: ofrecer agendar visita.
    """
    if memory.is_booking_active:
        return []

    if not properties_in_focus:
        if language == "en":
            return ["🏠 Buy", "🔑 Rent", "See properties"]
        return ["🏠 Comprar", "🔑 Alquilar", "Ver propiedades"]

    if language == "en":
        return ["📅 Schedule a visit"]
    return ["📅 Agendar una visita"]


# ── ChatResponse ──────────────────────────────────────────────────

@dataclass
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
    properties: list[dict] = field(default_factory=list)  # cards para el cliente
    suggestions: list[str] = field(default_factory=list)  # quick replies / chips

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "qualification_score": self.qualification_score,
            "qualification_stage": self.qualification_stage,
            "is_booking_active": self.is_booking_active,
            "booking_step": self.booking_step,
            "properties_found": self.properties_found,
            "properties": self.properties,
            "suggestions": self.suggestions,
            "duration_ms": self.duration_ms,
            "language": self.language,
        }


@dataclass
class _ResponseAssembly:
    """Resultado interno del ensamblado de respuesta."""
    final_text: str
    # True cuando el booking se acaba de completar y hay que persistir el lead.
    booking_complete: bool = False


# ── Función Principal ─────────────────────────────────────────────

async def process_message(
    session_id: str,
    tenant_id: str,
    user_message: str,
    session: AsyncSession,
    tenant_name: str = "Inmobiliaria Margarita",
) -> ChatResponse:
    """
    Procesa mensaje del usuario end-to-end.

    Args:
        session_id: ID de sesión del usuario.
        tenant_id: ID del tenant (aislamiento).
        user_message: Texto del mensaje del usuario.
        session: Sesión SQLAlchemy async activa.
        tenant_name: Nombre del tenant para el system prompt.

    Returns:
        ChatResponse con respuesta y estado de sesión.
    """
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
            text="Tu mensaje está vacío. ¿En qué puedo ayudarte?",
            qualification_score=0,
            qualification_stage="explore",
            is_booking_active=False,
            booking_step=None,
            properties_found=0,
            duration_ms=0.0,
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
            old=memory.language,
            new=lang_result.detected,
        )
        memory.language = lang_result.detected

    language = memory.language

    # ── 3. Registrar mensaje usuario en RAM ────────────────────
    memory.messages.append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    update_session_activity(memory)

    # ── 4. Hybrid Search ───────────────────────────────────────
    # Modo booking: ya en el flujo de datos, o el usuario pide agendar y hay
    # una propiedad en foco. En ese caso NO buscamos (preserva el foco y evita
    # que mensajes como "Anderson Vasquez" devuelvan un top-3 por defecto) ni
    # llamamos al LLM (las preguntas del booking son deterministas).
    booking_mode = memory.is_booking_active or (
        _has_booking_intent(user_message, language) and bool(memory.last_properties)
    )

    if booking_mode:
        search_result = SearchResult(properties=[], source="no_results", total_found=0)
        logfire.info("search_skipped", reason="booking_mode", session_id=session_id)
    else:
        with logfire.span("chat.hybrid_search", session_id=session_id, query=user_message):
            try:
                search_result = await hybrid_search(
                    session=session,
                    tenant_id=tenant_id,
                    user_query=user_message,
                    session_id=session_id,
                    language=language,
                    max_results=settings.max_properties_per_response,  # campo correcto de Settings
                )
            except Exception as exc:
                logger.exception("hybrid_search_failed", session_id=session_id, error=str(exc))
                search_result = SearchResult(
                    properties=[],
                    source="no_results",  # "search_error" no es miembro del enum
                    total_found=0,
                )
            logfire.info(
                "search_result",
                source=str(search_result.source),
                properties_found=search_result.total_found,
            )

    # ── 4b. Carry-forward de propiedades en foco ───────────────
    # Si la búsqueda actual trajo propiedades, actualizamos el foco.
    # Si no (follow-up tipo "me gusta la de $400" o "agendar visita"),
    # reusamos las últimas mostradas para no perder el contexto.
    if search_result.properties:
        memory.last_properties = [
            p.model_dump() if hasattr(p, "model_dump") else dict(p)
            for p in search_result.properties
        ]
        properties_in_focus = memory.last_properties
        focus_is_fresh = True
    else:
        properties_in_focus = memory.last_properties
        focus_is_fresh = False

    # ── 5. Build LLM Context ───────────────────────────────────
    llm_messages = _build_llm_messages(
        memory=memory,
        properties=properties_in_focus,
        focus_is_fresh=focus_is_fresh,
        language=language,
        tenant_name=tenant_name,
        max_messages=settings.max_messages_in_context,
    )

    # ── 6. LLM Call ────────────────────────────────────────────
    # En modo booking el texto lo genera _advance_booking_flow (determinista),
    # así que nos saltamos el LLM: evita latencia/timeouts en mitad de la
    # captura de datos.
    if booking_mode:
        response_text = ""
    else:
        try:
            model = get_chat_model(tenant_plan="pro")
            with logfire.span("chat.llm_completion", model=model, language=language):
                response_text = await chat_completion(
                    messages=llm_messages,
                    model=model,
                    timeout=settings.llm_timeout,
                )
        except LLMNoProviderAvailable:
            logger.error("llm_provider_unavailable", session_id=session_id)
            response_text = _get_fallback_response(language, "llm_unavailable")
        except Exception as exc:
            logger.exception("llm_unexpected_error", session_id=session_id, error=str(exc))
            response_text = _get_fallback_response(language, "llm_unavailable")

    # ── 7. Lead Qualification ──────────────────────────────────
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
        language=language,
        user_message=user_message,
    )

    # ── 8b. Persistir lead si el booking se completó ───────────
    if response_data.booking_complete:
        with logfire.span("chat.finalize_booking", session_id=session_id):
            await _finalize_booking(
                session=session,
                tenant_id=tenant_id,
                memory=memory,
                qual_result=qual_result,
            )

    # ── 9. Registrar respuesta assistant en RAM ────────────────
    memory.messages.append({
        "role": "assistant",
        "content": response_data.final_text,
        "has_properties": search_result.total_found > 0,
        "property_count": search_result.total_found,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

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

    duration_ms = (time.perf_counter() - start_time) * 1000

    logger.info(
        "chat_message_processed",
        session_id=session_id,
        tenant_id=tenant_id,
        language=language,
        score=qual_result.total_score,
        stage=qual_result.stage,
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
        # Mantener las cards de las propiedades en foco para que no
        # desaparezcan en follow-ups donde la búsqueda no trae nada nuevo.
        properties=[dict(p) for p in properties_in_focus],
        suggestions=_build_suggestions(memory, properties_in_focus, language),
        duration_ms=round(duration_ms, 2),
        language=language,
    )


# ── Context Builder ───────────────────────────────────────────────

def _build_llm_messages(
    memory: SessionMemory,
    properties: list[dict[str, Any]],
    focus_is_fresh: bool,
    language: str,
    tenant_name: str,
    max_messages: int,
) -> list[dict[str, str]]:
    """
    Construye lista de mensajes en formato OpenAI para el LLM.

    Estructura:
      1. System prompt con contexto de propiedades
      2. Historial de conversación truncado

    Args:
        properties: Propiedades en foco (búsqueda actual o arrastradas).
        focus_is_fresh: True si vienen de la búsqueda del turno actual,
                        False si son las últimas mostradas (follow-up).
    """
    conversation_history = _format_conversation_history(
        build_context_messages(memory=memory, max_messages=max_messages)
    )
    properties_context = _format_properties_context(properties, focus_is_fresh)

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

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

    # Agregar historial reciente — solo role y content
    recent = build_context_messages(memory=memory, max_messages=max_messages)
    for msg in recent:
        role = msg.get("role", "")
        if role not in {"user", "assistant"}:
            continue
        content = str(msg.get("content", ""))[:4000]  # Truncar payloads largos
        messages.append({"role": role, "content": content})

    return messages


# ── Formatters ────────────────────────────────────────────────────

def _format_conversation_history(messages: list[dict[str, Any]]) -> str:
    """Formatea historial compacto para el system prompt."""
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
    properties: list[dict[str, Any]],
    focus_is_fresh: bool = True,
) -> str:
    """
    Formatea propiedades verificadas para el system prompt.
    El LLM solo puede hablar de propiedades que aparezcan aquí.

    Args:
        properties: Propiedades en foco (dicts o PropertyChatSummary).
        focus_is_fresh: True si son resultados nuevos de la búsqueda actual;
                        False si son las últimas mostradas y el usuario hace
                        follow-up sobre ellas (selección, agendar visita, etc.).
    """
    if not properties:
        return "No hay propiedades que coincidan con los criterios actuales."

    if focus_is_fresh:
        header = f"Encontradas {len(properties)} propiedades verificadas en el catálogo:"
    else:
        header = (
            "PROPIEDADES YA MOSTRADAS EN ESTA CONVERSACIÓN "
            "(el usuario puede estar refiriéndose a una de ellas — "
            "selección, detalles o agendar visita):"
        )

    lines: list[str] = [header]
    lines.append(
        "(Para el LISTADO usa solo: tipo · zona · precio. Los demás campos son "
        "SOLO para la vista de DETALLE cuando el usuario elige una propiedad.)"
    )

    for idx, prop in enumerate(properties, start=1):
        # properties puede contener modelos PropertyChatSummary (pydantic
        # los coerciona) o dicts; normalizar a dict para el acceso .get().
        if hasattr(prop, "model_dump"):
            prop = prop.model_dump()

        # Línea de cabecera: título · operación · zona · precio
        title = prop.get("title", "Propiedad")
        header_line = f"{idx}. {title}"
        if prop.get("property_type"):
            header_line += f" [{prop['property_type']}]"
        zone = prop.get("location_zone") or prop.get("location_city")
        if zone:
            header_line += f" · {zone}"
        if prop.get("price_usd"):
            header_line += f" · ${prop['price_usd']:,.0f} USD"
        lines.append(header_line)

        # Especificaciones (solo para vista de detalle)
        specs: list[str] = []
        if prop.get("bedrooms"):
            specs.append(f"{prop['bedrooms']} habitaciones")
        if prop.get("bathrooms"):
            specs.append(f"{prop['bathrooms']} baños")
        if prop.get("area_m2"):
            specs.append(f"{prop['area_m2']}m²")
        if prop.get("parking_spots"):
            specs.append(f"{prop['parking_spots']} estacionamiento(s)")
        if prop.get("capacidad_huespedes"):
            specs.append(f"capacidad {prop['capacidad_huespedes']} huéspedes")
        if prop.get("vista_al_mar"):
            specs.append("🌊 vista al mar")
        if prop.get("frente_playa"):
            specs.append("🏖️ frente playa")
        if prop.get("uso_vacacional"):
            specs.append("💰 uso vacacional/inversión")
        if specs:
            lines.append(f"   Detalle: {', '.join(specs)}")

        amenities = prop.get("amenities")
        if amenities:
            amenities_str = ", ".join(str(a) for a in amenities)
            lines.append(f"   Amenidades: {amenities_str}")

        description = prop.get("description_es") or prop.get("description_en")
        if description:
            lines.append(f"   Descripción: {description[:400]}")

    return "\n".join(lines)


# ── Response Assembly ─────────────────────────────────────────────

def _assemble_response(
    response_text: str,
    qual_result: QualificationResult,
    memory: SessionMemory,
    language: str,
    user_message: str = "",
) -> _ResponseAssembly:
    """
    Ensambla la respuesta final.

    Estados:
      - booking activo:  avanza el flujo de captura de datos
      - intención visita: activa booking (por keyword o score >= book)
      - qualify:         agrega pregunta de calificación si el LLM no preguntó
      - explore:         respuesta directa sin modificaciones
    """
    # Si ya está en booking flow — continuar el flujo (ignoramos el texto del
    # LLM: durante la captura de datos las preguntas son deterministas).
    if memory.is_booking_active:
        return _advance_booking_flow(memory, language, user_message)

    # Activar booking flow: por intención explícita de visita o por score alto.
    # Requiere que haya una propiedad en foco para no agendar "en el aire".
    wants_to_book = (
        qual_result.stage == "book" or _has_booking_intent(user_message, language)
    )
    if wants_to_book and memory.last_properties:
        memory.is_booking_active = True
        memory.booking_data = {}
        steps = BOOKING_STEPS_ES if language == "es" else BOOKING_STEPS_EN
        memory.booking_step = steps[0]

        # Solo la pregunta determinista (sin el texto del LLM, que podría
        # alucinar o re-preguntar).
        return _ResponseAssembly(final_text=get_booking_prompt(steps[0], language))

    # Agregar pregunta de calificación — solo si el LLM no preguntó ya.
    # El system prompt instruye al LLM a hacer preguntas de calificación,
    # así que anexar siempre la del scorer duplica la pregunta.
    if qual_result.stage == "qualify" and "?" not in response_text:
        question = _get_qualification_question(qual_result, language)
        if question:
            return _ResponseAssembly(final_text=f"{response_text}\n\n{question}")

    # Exploración libre
    return _ResponseAssembly(final_text=response_text)


def _advance_booking_flow(
    memory: SessionMemory,
    language: str,
    user_message: str,
) -> _ResponseAssembly:
    """Captura la respuesta del paso actual, valida y avanza.

    - nombre/phone/email: se validan; si fallan, se re-pregunta el mismo paso.
    - date: opcional, no bloquea (se guarda tal cual o 'Por confirmar').
    - confirm: el usuario confirma → se cierra y se marca para persistir el lead.
    """
    steps = BOOKING_STEPS_ES if language == "es" else BOOKING_STEPS_EN
    current_step = memory.booking_step or steps[0]

    try:
        idx = steps.index(current_step)
    except ValueError:
        memory.booking_step = steps[0]
        return _ResponseAssembly(final_text=get_booking_prompt(steps[0], language))

    # Paso de confirmación → el usuario ya confirmó: cerrar y persistir.
    if current_step == "confirm":
        memory.is_booking_active = False
        memory.booking_step = None
        closing = _BOOKING_CLOSING_ES if language == "es" else _BOOKING_CLOSING_EN
        return _ResponseAssembly(final_text=closing, booking_complete=True)

    # Capturar y validar la respuesta del paso actual
    error = _capture_booking_field(memory, current_step, user_message, language)
    if error:
        # Dato inválido — re-preguntar el mismo paso con el error
        prompt = get_booking_prompt(current_step, language, **memory.booking_data)
        return _ResponseAssembly(final_text=f"{error}\n\n{prompt}")

    # Avanzar al siguiente paso
    next_step = steps[idx + 1]
    memory.booking_step = next_step
    prompt = get_booking_prompt(next_step, language, **memory.booking_data)
    return _ResponseAssembly(final_text=prompt)


# Palabras que indican "no tengo fecha aún" — la fecha es opcional.
_DATE_SKIP_ES = ("no sé", "no se", "no tengo", "aún no", "aun no", "luego",
                 "después", "despues", "no estoy seguro", "flexible", "cualquiera")
_DATE_SKIP_EN = ("don't know", "dont know", "not sure", "later", "no date",
                 "flexible", "any", "whenever")


def _capture_booking_field(
    memory: SessionMemory,
    step: str,
    user_message: str,
    language: str,
) -> str | None:
    """Captura y valida el dato del paso actual.

    Returns:
        None si la captura fue válida; un mensaje de error (re-ask) si no.
    """
    text = user_message.strip()

    if step == "nombre" or step == "name":
        ok, err = validate_name(text, language)
        if not ok:
            return err
        memory.booking_data["name"] = sanitize_name(text)
        return None

    if step == "phone":
        try:
            # Normaliza a E.164: 0414... → +58414..., y valida formato.
            memory.booking_data["phone"] = _validate_phone_value(text)
        except ValueError:
            if language == "en":
                return "That phone doesn't look right. Example: 04141234567 or +584141234567 📱"
            return "Ese teléfono no parece válido. Ejemplo: 04141234567 o +584141234567 📱"
        return None

    if step == "email":
        ok, err = validate_email(text, language)
        if not ok:
            return err
        memory.booking_data["email"] = text
        return None

    if step == "date":
        # Opcional — nunca bloquea
        skips = _DATE_SKIP_ES if language == "es" else _DATE_SKIP_EN
        low = text.lower()
        if not text or any(s in low for s in skips):
            memory.booking_data["preferred_date"] = "Por confirmar"
        else:
            memory.booking_data["preferred_date"] = text[:100]
        return None

    return None


# ── Booking — Persistencia del Lead ───────────────────────────────

def _booking_notes(memory: SessionMemory) -> str | None:
    """Notas para el lead: propiedades que el usuario tenía en foco."""
    titles = [
        str(p.get("title")) for p in memory.last_properties if p.get("title")
    ]
    if not titles:
        return None
    return "Propiedades de interés: " + "; ".join(titles[:5])


async def _finalize_booking(
    session: AsyncSession,
    tenant_id: str,
    memory: SessionMemory,
    qual_result: QualificationResult,
) -> None:
    """Persiste el lead capturado y notifica al agente (best-effort)."""
    data = memory.booking_data
    if not all(data.get(k) for k in ("name", "email", "phone")):
        logger.warning(
            "booking_finalize_incomplete",
            session_id=memory.session_id,
            captured=list(data.keys()),
        )
        memory.booking_data = {}
        return

    property_id = None
    if len(memory.last_properties) == 1:
        property_id = memory.last_properties[0].get("id")

    try:
        lead = await create_lead_from_booking(
            session=session,
            session_id=memory.session_id,
            tenant_id=tenant_id,
            name=data["name"],
            email=data["email"],
            phone=data["phone"],
            preferred_date=data.get("preferred_date") or "Por confirmar",
            property_id=property_id,
            qualification_score=memory.qualification_score,
            is_international=bool(getattr(qual_result, "is_international", False)),
            notes=_booking_notes(memory),
        )
    except Exception as exc:
        logger.exception(
            "lead_persist_failed", session_id=memory.session_id, error=str(exc)
        )
        await session.rollback()
        memory.booking_data = {}
        return

    logger.info(
        "lead_persisted",
        lead_id=str(lead.id),
        session_id=memory.session_id,
        tenant_id=tenant_id,
        property_id=property_id,
    )

    await _notify_agent(session, tenant_id, lead, property_id)
    memory.booking_data = {}


async def _notify_agent(
    session: AsyncSession,
    tenant_id: str,
    lead: Any,
    property_id: str | None,
) -> None:
    """Notifica al agente por WhatsApp/email. Best-effort: no rompe el flujo."""
    try:
        from app.db.models.property import Property
        from app.db.models.tenant import Tenant

        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            logger.info("notify_skip_no_tenant_row", tenant_id=tenant_id)
            return

        prop = await session.get(Property, property_id) if property_id else None
        result = await dispatch_booking_notifications(lead, tenant, prop)

        await update_lead_status(
            session=session,
            lead_id=lead.id,
            tenant_id=tenant_id,
            new_status=LeadStatus.PENDIENTE.value,
            whatsapp_sent=result.whatsapp_success,
            email_sent=result.email_success,
        )
    except Exception as exc:
        logger.warning(
            "notify_agent_failed",
            tenant_id=tenant_id,
            lead_id=str(getattr(lead, "id", "?")),
            error=str(exc),
        )


# ── Qualification ─────────────────────────────────────────────────

def _get_qualification_question(
    qual_result: QualificationResult,
    language: str,
) -> str | None:
    """Genera pregunta suave de calificación según señales faltantes."""
    # Usar las preguntas sugeridas del scorer si están disponibles
    if qual_result.suggested_questions:
        return qual_result.suggested_questions[0]

    # Fallback genérico
    if language == "en":
        return (
            "Do you have a preferred area or budget in mind? "
            "It helps me show you the best options."
        )
    return (
        "¿Tienes alguna preferencia de zona o presupuesto? "
        "Me ayuda a mostrarte las mejores opciones."
    )


# ── Fallbacks ─────────────────────────────────────────────────────

def _get_fallback_response(language: str, reason: str) -> str:
    """Respuesta de fallback cuando el LLM no está disponible."""
    responses = {
        "es": {
            "llm_unavailable": (
                "Estoy teniendo problemas técnicos momentáneos. "
                "Por favor, intenta nuevamente en unos minutos. 📞"
            ),
        },
        "en": {
            "llm_unavailable": (
                "I'm experiencing temporary technical issues. "
                "Please try again in a few minutes. 📞"
            ),
        },
    }
    return (
        responses.get(language, responses["es"])
        .get(reason, responses["es"]["llm_unavailable"])
    )


# ── Persistencia ──────────────────────────────────────────────────

async def _persist_messages(
    session: AsyncSession,
    session_id: str,
    tenant_id: str,
    user_content: str,
    assistant_content: str,
) -> None:
    """Persiste par de mensajes (user + assistant) en DB."""
    now = datetime.now(timezone.utc).isoformat()

    user_msg = Message(
        session_id=session_id,
        tenant_id=tenant_id,
        role="user",
        content=user_content,
        created_at=now,
    )
    assistant_msg = Message(
        session_id=session_id,
        tenant_id=tenant_id,
        role="assistant",
        content=assistant_content,
        created_at=now,
    )

    session.add(user_msg)
    session.add(assistant_msg)
    await session.commit()


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from unittest.mock import MagicMock

    async def _test():
        print("🔥 Smoke Tests — chat/engine.py\n")

        # Test 1: ChatResponse serializable
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
        d = resp.to_dict()
        assert d["language"] == "es"
        assert d["qualification_score"] == 45
        print("✅ ChatResponse serializable")

        # Test 2: Fallback responses
        assert "técnicos" in _get_fallback_response("es", "llm_unavailable")
        assert "technical" in _get_fallback_response("en", "llm_unavailable")
        print("✅ Fallback ES y EN")

        # Test 3: Conversation history
        history = _format_conversation_history([
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "Bienvenido"},
        ])
        assert "Usuario: Hola" in history
        assert "Asistente: Bienvenido" in history
        print("✅ Conversation history formateada")

        # Test 4: Truncado de historial largo
        long_text = "a" * 300
        history_long = _format_conversation_history([
            {"role": "user", "content": long_text}
        ])
        assert "..." in history_long
        print("✅ Truncado de historial")

        # Test 5: Properties context vacío
        ctx = _format_properties_context([])
        assert "No hay propiedades" in ctx
        print("✅ Properties context vacío")

        # Test 6: Properties context con datos (búsqueda fresca)
        props = [{
            "title": "Apartamento Pampatar",
            "price_usd": 120000,
            "location_zone": "Pampatar",
            "bedrooms": 2,
            "bathrooms": 2,
            "vista_al_mar": True,
        }]
        ctx2 = _format_properties_context(props, focus_is_fresh=True)
        assert "Apartamento Pampatar" in ctx2
        assert "$120,000" in ctx2
        assert "vista al mar" in ctx2.lower()
        print("✅ Properties context con datos")

        # Test 6b: Properties context arrastrado (follow-up)
        ctx_carry = _format_properties_context(props, focus_is_fresh=False)
        assert "YA MOSTRADAS" in ctx_carry
        assert "Apartamento Pampatar" in ctx_carry
        print("✅ Properties context arrastrado (follow-up)")

        # Test 7: Response assembly — stage qualify
        memory = SessionMemory(session_id="s1", tenant_id="t1")
        qual = MagicMock()
        qual.stage = "qualify"
        qual.suggested_questions = ["¿Tienes presupuesto en mente?"]
        result = _assemble_response(
            response_text="Aquí tienes opciones",
            qual_result=qual,
            memory=memory,
            language="es",
        )
        assert "presupuesto" in result.final_text
        print("✅ Assembly con qualify question")

        # Test 8: Booking flow — activación (requiere propiedad en foco)
        memory_book = SessionMemory(session_id="s2", tenant_id="t1")
        memory_book.last_properties = [{"id": "p1", "title": "Apto Pampatar"}]
        qual_book = MagicMock()
        qual_book.stage = "book"
        qual_book.suggested_questions = []
        result_book = _assemble_response(
            response_text="Perfecto",
            qual_result=qual_book,
            memory=memory_book,
            language="es",
        )
        assert memory_book.is_booking_active is True
        assert memory_book.booking_step == "nombre"
        print("✅ Booking flow activado")

        # Test 8b: Booking por intención de visita (sin score alto)
        memory_intent = SessionMemory(session_id="s2b", tenant_id="t1")
        memory_intent.last_properties = [{"id": "p1", "title": "Apto"}]
        qual_intent = MagicMock()
        qual_intent.stage = "qualify"
        qual_intent.suggested_questions = []
        _assemble_response(
            response_text="Genial",
            qual_result=qual_intent,
            memory=memory_intent,
            language="es",
            user_message="quiero agendar una visita para verla",
        )
        assert memory_intent.is_booking_active is True
        print("✅ Booking activado por intención de visita")

        # Test 9: Booking flow — captura nombre y avanza (nombre → phone)
        memory_adv = SessionMemory(
            session_id="s3",
            tenant_id="t1",
            is_booking_active=True,
            booking_step="nombre",
        )
        result_adv = _advance_booking_flow(memory_adv, "es", "Juan Pérez")
        assert memory_adv.booking_step == "phone"
        assert memory_adv.booking_data["name"] == "Juan Pérez"
        print("✅ Booking captura nombre y avanza al siguiente paso")

        # Test 9b: Validación falla → re-pregunta el mismo paso
        memory_bad = SessionMemory(
            session_id="s3c",
            tenant_id="t1",
            is_booking_active=True,
            booking_step="email",
        )
        result_bad = _advance_booking_flow(memory_bad, "es", "esto-no-es-email")
        assert memory_bad.booking_step == "email"  # no avanzó
        assert "email" in result_bad.final_text.lower()
        print("✅ Booking re-pregunta ante dato inválido")

        # Test 9c: Booking flow — cierre con flag de persistencia
        memory_close = SessionMemory(
            session_id="s3b",
            tenant_id="t1",
            is_booking_active=True,
            booking_step="confirm",
        )
        result_close = _advance_booking_flow(memory_close, "es", "sí")
        assert memory_close.is_booking_active is False
        assert result_close.booking_complete is True
        assert "se comunicará contigo" in result_close.final_text
        print("✅ Booking flow cierra y marca persistencia del lead")

        # Test 10: Booking steps no incluyen DURATION
        assert "duration" not in BOOKING_STEPS_ES
        assert "duration" not in BOOKING_STEPS_EN
        print("✅ DURATION eliminado de booking steps")

        # Test 11: Fallback para idioma desconocido
        fallback = _get_fallback_response("fr", "llm_unavailable")
        assert "técnicos" in fallback  # fallback a ES
        print("✅ Fallback a ES para idioma desconocido")

        print("\n🎉 Todos los smoke tests pasaron ✅")

    asyncio.run(_test())
