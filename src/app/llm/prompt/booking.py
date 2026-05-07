# src/app/llm/prompts/booking.py
"""Prompts para recopilación de lead — booking flow step-by-step.

Flujo de 7 pasos: nombre → email → teléfono → fecha → hora → notas → confirmar.
Cada paso tiene su propio prompt para el LLM, que guía al usuario
conversacionalmente mientras Python valida con Pydantic v2.

Principios:
  - Un paso a la vez. No pedir todos los datos de golpe.
  - Validar amablemente: si el email es inválido, pedir de nuevo sin frustrar.
  - Confirmar antes de guardar: resumen completo al final.
"""

from __future__ import annotations


# ── Prompts por paso ──────────────────────────────────────────────

BOOKING_STEP_PROMPTS_ES: dict[str, str] = {
    "nombre": """El usuario ha mostrado interés en agendar una visita. 
Pregunta su nombre de forma cálida y natural. No pidas otros datos todavía.

Ejemplo: "¡Perfecto! ¿Me podrías decir tu nombre para coordinar la visita? 😊"""",

    "email": """Ya tienes el nombre: {name}.
Ahora pide el email. Menciona que es para enviar confirmación y recordatorio.

Ejemplo: "Gracias {name}. ¿Cuál es tu email? Te enviaré la confirmación de la visita. 📧"""",

    "phone": """Ya tienes: nombre={name}, email={email}.
Ahora pide el número de teléfono (WhatsApp preferible). Menciona que el agente lo contactará ahí.

Ejemplo: "¿Y tu número de WhatsApp? El agente se comunicará contigo por ahí para confirmar. 📱"""",

    "date": """Ya tienes: nombre={name}, email={email}, teléfono={phone}.
Ahora pide la fecha preferida para la visita. Sugiere días útiles o fines de semana.

Ejemplo: "¿Qué día te funciona mejor? Puedo agendar de lunes a sábado. 📅"""",

    "time": """Ya tienes: nombre={name}, email={email}, teléfono={phone}, fecha={preferred_date}.
Ahora pide la hora. Sugiere horarios de mañana (9:00-12:00) o tarde (14:00-17:00).

Ejemplo: "¿A qué hora? Las visitas suelen ser por la mañana (9:00-12:00) o tarde (14:00-17:00). 🕐"""",

    "duration": """Ya tienes: nombre={name}, email={email}, teléfono={phone}, fecha={preferred_date}, hora={preferred_time}.
Confirma la duración estimada de la visita (default 60 min, internacionales 90 min).

Ejemplo: "La visita durará aproximadamente {duration} minutos. ¿Te funciona? ⏱️"""",

    "notes": """Ya tienes todos los datos de contacto y horario.
Pide notas opcionales: ¿hay algo específico que quiera ver? ¿Viene con alguien? ¿Necesita traslado?

Ejemplo: "¿Hay algo especial que quieras ver en la visita? ¿Alguna preferencia? (opcional) 📝"""",

    "confirm": """Tienes todos los datos:
- Nombre: {name}
- Email: {email}
- Teléfono: {phone}
- Fecha: {preferred_date}
- Hora: {preferred_time}
- Duración: {duration} minutos
- Notas: {notes}

Pide confirmación final antes de guardar. Menciona que el agente recibirá notificación por WhatsApp y email.

Ejemplo: "¿Todo correcto? Al confirmar, el agente recibirá la notificación y te contactará. ✅"""",
}


BOOKING_STEP_PROMPTS_EN: dict[str, str] = {
    "nombre": "name",  # Mismo flujo, traducido abajo
    "email": "email",
    "phone": "phone",
    "date": "date",
    "time": "time",
    "duration": "duration",
    "notes": "notes",
    "confirm": "confirm",
}


# ── Prompts EN completos ──────────────────────────────────────────

BOOKING_STEP_PROMPTS_EN_FULL: dict[str, str] = {
    "name": """The user has shown interest in scheduling a visit.
Ask for their name in a warm, natural way. Don't ask for other data yet.

Example: "Great! Could you tell me your name to coordinate the visit? 😊"""",

    "email": """You have: name={name}.
Now ask for email. Mention it's for confirmation and reminder.

Example: "Thanks {name}. What's your email? I'll send you the visit confirmation. 📧"""",

    "phone": """You have: name={name}, email={email}.
Now ask for phone number (WhatsApp preferred). Mention the agent will contact them there.

Example: "And your WhatsApp number? The agent will reach out there to confirm. 📱"""",

    "date": """You have: name={name}, email={email}, phone={phone}.
Now ask for preferred visit date. Suggest weekdays or weekends.

Example: "What day works best? I can schedule Monday through Saturday. 📅"""",

    "time": """You have: name={name}, email={email}, phone={phone}, date={preferred_date}.
Now ask for time. Suggest morning (9:00-12:00) or afternoon (14:00-17:00).

Example: "What time? Visits are usually morning (9:00-12:00) or afternoon (14:00-17:00). 🕐"""",

    "duration": """You have: name={name}, email={email}, phone={phone}, date={preferred_date}, time={preferred_time}.
Confirm estimated visit duration (default 60 min, international 90 min).

Example: "The visit will last approximately {duration} minutes. Does that work? ⏱️"""",

    "notes": """You have all contact and schedule details.
Ask for optional notes: anything specific to see? Coming with someone? Need pickup?

Example: "Anything special you'd like to see? Any preferences? (optional) 📝"""",

    "confirm": """You have all details:
- Name: {name}
- Email: {email}
- Phone: {phone}
- Date: {preferred_date}
- Time: {preferred_time}
- Duration: {duration} minutes
- Notes: {notes}

Ask for final confirmation before saving. Mention the agent will get notified via WhatsApp and email.

Example: "All correct? Once confirmed, the agent will be notified and contact you. ✅"""",
}


def get_booking_prompt(
    step: str,
    language: str = "es",
    **kwargs: str,
) -> str:
    """Retorna prompt para un paso específico del booking flow.
    
    Args:
        step: Nombre del paso (nombre|email|phone|date|time|duration|notes|confirm).
        language: "es" | "en".
        **kwargs: Variables para interpolación (name, email, etc.).
    
    Returns:
        Prompt renderizado listo para enviar al LLM.
    """
    if language == "es":
        prompt_template = BOOKING_STEP_PROMPTS_ES.get(step, "")
    else:
        prompt_template = BOOKING_STEP_PROMPTS_EN_FULL.get(step, "")
    
    if not prompt_template:
        return f"Error: paso '{step}' no encontrado."
    
    # Interpolar variables
    try:
        return prompt_template.format(**kwargs)
    except KeyError as exc:
        # Si falta variable, retornar template sin interpolar (fallback)
        return prompt_template


def get_booking_summary(
    name: str,
    email: str,
    phone: str,
    preferred_date: str,
    preferred_time: str,
    duration: int = 60,
    notes: str = "",
    language: str = "es",
) -> str:
    """Genera resumen de booking para confirmación final."""
    if language == "es":
        return f"""\
Resumen de tu visita:

👤 Nombre: {name}
📧 Email: {email}
📱 Teléfono: {phone}
📅 Fecha: {preferred_date}
🕐 Hora: {preferred_time}
⏱️ Duración: {duration} minutos
📝 Notas: {notes or 'Sin notas'}

¿Confirmamos? ✅"""
    else:
        return f"""\
Visit summary:

👤 Name: {name}
📧 Email: {email}
📱 Phone: {phone}
📅 Date: {preferred_date}
🕐 Time: {preferred_time}
⏱️ Duration: {duration} minutes
📝 Notes: {notes or 'None'}

Shall we confirm? ✅"""


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — booking.py")
    
    # Test 1: Prompts ES tienen todos los pasos
    steps_es = ["nombre", "email", "phone", "date", "time", "duration", "notes", "confirm"]
    for step in steps_es:
        prompt = get_booking_prompt(step, language="es")
        assert len(prompt) > 50
        assert "Ejemplo:" in prompt or "error" not in prompt.lower()
    print(f"  ✅ {len(steps_es)} pasos ES disponibles")
    
    # Test 2: Prompts EN tienen todos los pasos
    steps_en = ["name", "email", "phone", "date", "time", "duration", "notes", "confirm"]
    for step in steps_en:
        prompt = get_booking_prompt(step, language="en")
        assert len(prompt) > 50
    print(f"  ✅ {len(steps_en)} pasos EN disponibles")
    
    # Test 3: Interpolación variables ES
    prompt = get_booking_prompt(
        "email",
        language="es",
        name="María",
    )
    assert "María" in prompt
    print("  ✅ Interpolación variables ES")
    
    # Test 4: Interpolación variables EN
    prompt_en = get_booking_prompt(
        "email",
        language="en",
        name="John",
    )
    assert "John" in prompt_en
    print("  ✅ Interpolación variables EN")
    
    # Test 5: Resumen ES
    summary = get_booking_summary(
        name="María González",
        email="maria@test.com",
        phone="+584141234567",
        preferred_date="2026-06-15",
        preferred_time="10:00",
        duration=90,
        notes="Verificar vista al mar",
    )
    assert "María González" in summary
    assert "90 minutos" in summary
    assert "Verificar vista al mar" in summary
    print("  ✅ Resumen ES completo")
    
    # Test 6: Resumen EN
    summary_en = get_booking_summary(
        name="John Doe",
        email="john@test.com",
        phone="+14155551234",
        preferred_date="2026-06-15",
        preferred_time="10:00",
        duration=60,
        language="en",
    )
    assert "John Doe" in summary_en
    assert "60 minutes" in summary_en
    print("  ✅ Resumen EN completo")
    
    # Test 7: Paso inválido
    invalid = get_booking_prompt("invalid_step")
    assert "Error" in invalid
    print("  ✅ Manejo paso inválido")
    
    print("\n🎉 Todos los smoke tests pasaron")