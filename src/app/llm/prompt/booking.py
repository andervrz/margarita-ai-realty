# src/app/llm/prompts/booking.py
"""Prompts para recopilación de lead — booking flow step-by-step.

Flujo de 7 pasos: nombre → email → teléfono → fecha → hora → notas → confirmar.
DURATION eliminado — la duración la define el tenant en config, no el usuario.

Principios:
  - Un paso a la vez. No pedir todos los datos de golpe.
  - Validar amablemente si el dato es inválido.
  - Confirmar con resumen completo antes de guardar.
"""

from __future__ import annotations


# ── Prompts ES ────────────────────────────────────────────────────

BOOKING_STEP_PROMPTS_ES: dict[str, str] = {
    "nombre": """\
El usuario ha mostrado interés en agendar una visita.
Pregunta su nombre de forma cálida y natural. No pidas otros datos todavía.

Ejemplo: "¡Perfecto! ¿Me podrías decir tu nombre para coordinar la visita? 😊\"""",

    "email": """\
Ya tienes el nombre: {name}.
Ahora pide el email. Menciona que es para enviar confirmación y recordatorio.

Ejemplo: "Gracias {name}. ¿Cuál es tu email? Te enviaré la confirmación de la visita. 📧\"""",

    "phone": """\
Ya tienes: nombre={name}, email={email}.
Ahora pide el número de teléfono (WhatsApp preferible). El agente lo contactará ahí.

Ejemplo: "¿Y tu número de WhatsApp? El agente se comunicará contigo por ahí para confirmar. 📱\"""",

    "date": """\
Ya tienes: nombre={name}, email={email}, teléfono={phone}.
Ahora pide la fecha preferida para la visita. Sugiere días de lunes a sábado.

Ejemplo: "¿Qué día te funciona mejor? Puedo agendar de lunes a sábado. 📅\"""",

    "time": """\
Ya tienes: nombre={name}, email={email}, teléfono={phone}, fecha={preferred_date}.
Ahora pide la hora. Sugiere mañana (9:00-12:00) o tarde (14:00-17:00).

Ejemplo: "¿A qué hora? Las visitas suelen ser por la mañana (9:00-12:00) o tarde (14:00-17:00). 🕐\"""",

    "notes": """\
Ya tienes todos los datos de contacto y horario.
Pide notas opcionales: ¿algo específico que quiera ver? ¿Viene con alguien?

Ejemplo: "¿Hay algo especial que quieras ver en la visita? (opcional) 📝\"""",

    "confirm": """\
Tienes todos los datos:
- Nombre: {name}
- Email: {email}
- Teléfono: {phone}
- Fecha: {preferred_date}
- Hora: {preferred_time}
- Notas: {notes}

Pide confirmación final. El agente recibirá notificación por WhatsApp y email.

Ejemplo: "¿Todo correcto? Al confirmar, el agente recibirá la notificación y te contactará. ✅\"""",
}


# ── Prompts EN ────────────────────────────────────────────────────

BOOKING_STEP_PROMPTS_EN: dict[str, str] = {
    "name": """\
The user has shown interest in scheduling a visit.
Ask for their name in a warm, natural way. Don't ask for other data yet.

Example: "Great! Could you tell me your name to coordinate the visit? 😊\"""",

    "email": """\
You have: name={name}.
Now ask for email. Mention it's for confirmation and reminder.

Example: "Thanks {name}. What's your email? I'll send you the visit confirmation. 📧\"""",

    "phone": """\
You have: name={name}, email={email}.
Now ask for phone number (WhatsApp preferred). The agent will contact them there.

Example: "And your WhatsApp number? The agent will reach out to confirm. 📱\"""",

    "date": """\
You have: name={name}, email={email}, phone={phone}.
Now ask for preferred visit date. Suggest Monday through Saturday.

Example: "What day works best? I can schedule Monday through Saturday. 📅\"""",

    "time": """\
You have: name={name}, email={email}, phone={phone}, date={preferred_date}.
Now ask for time. Suggest morning (9:00-12:00) or afternoon (14:00-17:00).

Example: "What time? Visits are usually morning (9:00-12:00) or afternoon (14:00-17:00). 🕐\"""",

    "notes": """\
You have all contact and schedule details.
Ask for optional notes: anything specific to see? Coming with someone?

Example: "Anything special you'd like to see during the visit? (optional) 📝\"""",

    "confirm": """\
You have all details:
- Name: {name}
- Email: {email}
- Phone: {phone}
- Date: {preferred_date}
- Time: {preferred_time}
- Notes: {notes}

Ask for final confirmation. The agent will be notified via WhatsApp and email.

Example: "All correct? Once confirmed, the agent will be notified and contact you. ✅\"""",
}


# ── Función Principal ─────────────────────────────────────────────

def get_booking_prompt(
    step: str,
    language: str = "es",
    **kwargs: str,
) -> str:
    """Retorna prompt para un paso del booking flow.

    Args:
        step: Nombre del paso.
               ES: nombre|email|phone|date|time|notes|confirm
               EN: name|email|phone|date|time|notes|confirm
        language: "es" | "en".
        **kwargs: Variables para interpolación (name, email, phone, etc.).

    Returns:
        Prompt renderizado listo para el LLM.
        Si el paso no existe, retorna mensaje de error explícito.
    """
    if language == "es":
        template = BOOKING_STEP_PROMPTS_ES.get(step, "")
    else:
        template = BOOKING_STEP_PROMPTS_EN.get(step, "")

    if not template:
        return f"Error: paso de booking '{step}' no encontrado para idioma '{language}'."

    try:
        return template.format(**kwargs)
    except KeyError:
        # Variables faltantes — retornar template sin interpolar
        # El LLM puede manejar los placeholders sin llenar
        return template


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
        return (
            f"Resumen de tu visita:\n\n"
            f"👤 Nombre: {name}\n"
            f"📧 Email: {email}\n"
            f"📱 Teléfono: {phone}\n"
            f"📅 Fecha: {preferred_date}\n"
            f"🕐 Hora: {preferred_time}\n"
            f"⏱️ Duración: {duration} minutos\n"
            f"📝 Notas: {notes or 'Sin notas'}\n\n"
            f"¿Confirmamos? ✅"
        )
    return (
        f"Visit summary:\n\n"
        f"👤 Name: {name}\n"
        f"📧 Email: {email}\n"
        f"📱 Phone: {phone}\n"
        f"📅 Date: {preferred_date}\n"
        f"🕐 Time: {preferred_time}\n"
        f"⏱️ Duration: {duration} minutes\n"
        f"📝 Notes: {notes or 'None'}\n\n"
        f"Shall we confirm? ✅"
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — booking.py\n")

    # Test 1: Todos los pasos ES están presentes y son prompts reales
    steps_es = ["nombre", "email", "phone", "date", "time", "notes", "confirm"]
    for step in steps_es:
        prompt = get_booking_prompt(step, language="es")
        assert len(prompt) > 50, f"Paso '{step}' tiene prompt demasiado corto"
        assert "Error" not in prompt, f"Paso '{step}' retornó error"
        assert "Ejemplo:" in prompt, f"Paso '{step}' no tiene ejemplo"
    print(f"✅ {len(steps_es)} pasos ES disponibles con prompts reales")

    # Test 2: Todos los pasos EN están presentes y son prompts reales
    steps_en = ["name", "email", "phone", "date", "time", "notes", "confirm"]
    for step in steps_en:
        prompt = get_booking_prompt(step, language="en")
        assert len(prompt) > 50, f"Step '{step}' has prompt too short"
        assert "Error" not in prompt, f"Step '{step}' returned error"
        assert "Example:" in prompt, f"Step '{step}' missing example"
    print(f"✅ {len(steps_en)} pasos EN disponibles con prompts reales")

    # Test 3: DURATION no existe en los pasos — fue eliminado
    assert "duration" not in steps_es, "DURATION no debe estar en pasos ES"
    assert "duration" not in steps_en, "DURATION no debe estar en pasos EN"
    assert get_booking_prompt("duration", "es").startswith("Error:")
    print("✅ DURATION eliminado correctamente")

    # Test 4: Interpolación variables ES
    prompt_email_es = get_booking_prompt("email", language="es", name="María")
    assert "María" in prompt_email_es
    print("✅ Interpolación variables ES")

    # Test 5: Interpolación variables EN
    prompt_email_en = get_booking_prompt("email", language="en", name="John")
    assert "John" in prompt_email_en
    print("✅ Interpolación variables EN")

    # Test 6: Variables faltantes no rompen la función
    prompt_sin_vars = get_booking_prompt("confirm", language="es")
    assert len(prompt_sin_vars) > 50
    assert "Error" not in prompt_sin_vars
    print("✅ Variables faltantes manejadas con fallback")

    # Test 7: Paso inválido retorna error explícito
    invalid = get_booking_prompt("invalid_step", language="es")
    assert invalid.startswith("Error:")
    print("✅ Paso inválido retorna error explícito")

    # Test 8: Resumen ES
    summary_es = get_booking_summary(
        name="María González",
        email="maria@test.com",
        phone="+584141234567",
        preferred_date="2027-06-15",
        preferred_time="10:00",
        duration=90,
        notes="Verificar vista al mar",
        language="es",
    )
    assert "María González" in summary_es
    assert "90 minutos" in summary_es
    assert "Verificar vista al mar" in summary_es
    assert "¿Confirmamos?" in summary_es
    print("✅ Resumen ES completo")

    # Test 9: Resumen EN
    summary_en = get_booking_summary(
        name="John Doe",
        email="john@test.com",
        phone="+14155551234",
        preferred_date="2027-06-15",
        preferred_time="10:00",
        duration=60,
        language="en",
    )
    assert "John Doe" in summary_en
    assert "60 minutes" in summary_en
    assert "Shall we confirm?" in summary_en
    print("✅ Resumen EN completo")

    # Test 10: Resumen ES sin notas usa default
    summary_no_notes = get_booking_summary(
        name="Test",
        email="t@t.com",
        phone="+584141234567",
        preferred_date="2027-01-01",
        preferred_time="10:00",
    )
    assert "Sin notas" in summary_no_notes
    print("✅ Resumen sin notas usa default")

    print("\n🎉 Todos los smoke tests pasaron ✅")
