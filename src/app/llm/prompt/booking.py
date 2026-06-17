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

# Estos textos se muestran DIRECTAMENTE al usuario (no son instrucciones para el LLM).
# Se interpolan con los datos ya capturados (name, phone, email, preferred_date).
BOOKING_STEP_PROMPTS_ES: dict[str, str] = {
    "nombre": "¡Perfecto! 😊 Para coordinar la visita, ¿me das tu nombre y apellido?",

    "phone": "Gracias, {name}. ¿Cuál es tu número de WhatsApp? El agente te escribirá por ahí. 📱",

    "email": "¿Y tu correo electrónico? Es para enviarte la confirmación. 📧",

    "date": "¿Qué día te gustaría la visita? 📅 (opcional — si aún no lo tienes claro, no pasa nada)",

    "confirm": (
        "Te confirmo tus datos:\n"
        "👤 {name}\n📱 {phone}\n📧 {email}\n📅 {preferred_date}\n\n"
        "¿Está todo bien? Al confirmar, el dueño o agente de la propiedad se comunicará contigo. ✅"
    ),
}


# ── Prompts EN ────────────────────────────────────────────────────

# Shown DIRECTLY to the user (not instructions for the LLM).
BOOKING_STEP_PROMPTS_EN: dict[str, str] = {
    "name": "Great! 😊 To coordinate the visit, could you tell me your first and last name?",

    "phone": "Thanks, {name}. What's your WhatsApp number? The agent will reach out there. 📱",

    "email": "And your email? It's to send you the confirmation. 📧",

    "date": "What day would you like the visit? 📅 (optional — if you're not sure yet, that's fine)",

    "confirm": (
        "Let me confirm your details:\n"
        "👤 {name}\n📱 {phone}\n📧 {email}\n📅 {preferred_date}\n\n"
        "All good? Once confirmed, the property owner or agent will reach out to you. ✅"
    ),
}


# ── Función Principal ─────────────────────────────────────────────

class _SafeDict(dict):
    """Dict para format_map: claves faltantes → cadena vacía (no leak de {placeholder})."""

    def __missing__(self, key: str) -> str:
        return ""


def get_booking_prompt(
    step: str,
    language: str = "es",
    **kwargs: str,
) -> str:
    """Retorna el texto (user-facing) para un paso del booking flow.

    Args:
        step: Nombre del paso.
               ES: nombre|phone|email|date|confirm
               EN: name|phone|email|date|confirm
        language: "es" | "en".
        **kwargs: Variables para interpolación (name, phone, email, preferred_date).

    Returns:
        Texto listo para mostrar al usuario.
        Si el paso no existe, retorna mensaje de error explícito.
    """
    if language == "es":
        template = BOOKING_STEP_PROMPTS_ES.get(step, "")
    else:
        template = BOOKING_STEP_PROMPTS_EN.get(step, "")

    if not template:
        return f"Error: paso de booking '{step}' no encontrado para idioma '{language}'."

    # format_map con _SafeDict: variables faltantes quedan vacías sin romper.
    return template.format_map(_SafeDict(**kwargs))


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

    # Test 1: Todos los pasos ES están presentes y son preguntas reales (user-facing)
    steps_es = ["nombre", "phone", "email", "date", "confirm"]
    for step in steps_es:
        prompt = get_booking_prompt(step, language="es")
        assert len(prompt) > 30, f"Paso '{step}' tiene prompt demasiado corto"
        assert "Error" not in prompt, f"Paso '{step}' retornó error"
        assert "{" not in prompt, f"Paso '{step}' dejó un placeholder sin interpolar"
    print(f"✅ {len(steps_es)} pasos ES disponibles con preguntas reales")

    # Test 2: Todos los pasos EN están presentes y son preguntas reales (user-facing)
    steps_en = ["name", "phone", "email", "date", "confirm"]
    for step in steps_en:
        prompt = get_booking_prompt(step, language="en")
        assert len(prompt) > 30, f"Step '{step}' has prompt too short"
        assert "Error" not in prompt, f"Step '{step}' returned error"
        assert "{" not in prompt, f"Step '{step}' left an un-interpolated placeholder"
    print(f"✅ {len(steps_en)} pasos EN disponibles con preguntas reales")

    # Test 3: time/notes/duration ya no existen — flujo simplificado
    for removed in ("time", "notes", "duration"):
        assert removed not in steps_es, f"'{removed}' no debe estar en pasos ES"
        assert get_booking_prompt(removed, "es").startswith("Error:")
    print("✅ time/notes/duration eliminados correctamente")

    # Test 4: Interpolación variables ES (paso phone usa {name})
    prompt_phone_es = get_booking_prompt("phone", language="es", name="María")
    assert "María" in prompt_phone_es
    print("✅ Interpolación variables ES")

    # Test 5: Interpolación variables EN (paso phone usa {name})
    prompt_phone_en = get_booking_prompt("phone", language="en", name="John")
    assert "John" in prompt_phone_en
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
