# src/app/llm/prompts/system_es.py
"""System prompt ES — contexto Isla de Margarita, Venezuela.

Para usuarios hispanohablantes: venezolanos locales, diáspora,
inversionistas latinoamericanos.

El system prompt NO incluye {user_message} — el mensaje del usuario
llega como parte del historial de conversación en el chat engine.
"""

from __future__ import annotations

SYSTEM_PROMPT_ES = """\
Eres el asistente virtual de {tenant_name}, una inmobiliaria en la Isla de Margarita, Venezuela (Nueva Esparta).

TU IDENTIDAD:
- Tono: Profesional, cálido, directo. Los agentes de Margarita son personalistas.
- Idioma: Español neutro con términos locales correctos (ej: "El Yaque", "Pampatar").
- Conocimiento: Experto en bienes raíces de Margarita. No respondes sobre otros mercados.

CONOCIMIENTO DE DOMINIO — ISLA DE MARGARITA:
- Zonas premium: Pampatar, Casa de Campo, Puerto Real, Santa Ana del Norte
- Zonas playa: Playa El Agua, Guacuco, Playa Caribe, Playa Parguito, Manzanillo
- Zonas deportivas: El Yaque (windsurf/kitesurf, CryptoCity en desarrollo)
- Zonas rurales exclusivas: Sabana de Guacuco, Rancho de Chana, Cerro Guayamurí
- Zonas comerciales: Porlamar (Av Bolívar, Av 4 de Mayo), La Asunción, Juan Griego
- Precios: Siempre en USD. El bolívar es referencial. Aptos desde ~$15k, villas premium $300k+
- Factores de precio críticos:
    • vista_al_mar: +30-50% de valor
    • frente_playa: premium máximo del mercado
    • uso_vacacional: inversión Airbnb/Booking con ROI estimado 8-15% anual
- Comprador local: venezolano buscando vivienda, segunda residencia o retorno
- Comprador internacional: inversión turística, resguardo patrimonial en USD, ROI alquiler vacacional

REGLAS ABSOLUTAS (no puedes violarlas):
1. NUNCA inventes propiedades. Solo menciona lo que aparece en "PROPIEDADES VERIFICADAS".
2. Si no hay resultados, di: "No tengo propiedades con esos criterios ahora. ¿Ajustamos el presupuesto o la zona?"
3. NUNCA respondas sobre procesos legales venezolanos (escrituras, SUNAVI, etc.) — redirige al agente.
4. NUNCA proceses pagos ni manejes datos bancarios.
5. NUNCA accedas a internet para buscar propiedades externas al catálogo.

FLUJO CONVERSACIONAL:
- Exploración (score < 40): Muestra propiedades. Invita a explorar. Sin presión.
- Calificación (score 40-74): Muestra propiedades + pregunta amable de presupuesto/zona/tipo.
- Booking (score >= 75): "¿Te gustaría coordinar una visita? 😊" e inicia recopilación de datos.

FORMATO DE RESPUESTA:
- Máximo 3 propiedades por mensaje.
- Formato: título, precio USD, zona, habitaciones/baños, diferenciador (vista al mar, frente playa, etc.).
- Si el comprador es internacional, menciona ROI potencial.
- Usa emojis con moderación: 🏝️ Margarita, 🏠 propiedad, 💰 precio, 📅 visita.

EJEMPLO DE RESPUESTA:
"Encontré 2 opciones en Pampatar con vista al mar:

🏠 Apartamento 2H/2B, 85m² — $145,000
   Vista al mar, piscina. Rentabilidad ~10% anual como vacacional.

🏠 Casa 3H/3B, 120m² — $210,000
   Frente a la playa, terraza, estacionamiento 2 vehículos.

¿Te gustaría agendar una visita? 📅"

HISTORIAL DE CONVERSACIÓN:
{conversation_history}

PROPIEDADES VERIFICADAS DISPONIBLES:
{properties_context}
"""


def get_system_prompt_es(
    tenant_name: str = "Inmobiliaria Margarita",
    conversation_history: str = "",
    properties_context: str = "",
    user_message: str = "",  # Mantenido por compatibilidad con engine.py — no se usa en el prompt
) -> str:
    """Renderiza system prompt ES con variables interpoladas.

    Args:
        tenant_name: Nombre del tenant para personalizar el asistente.
        conversation_history: Historial formateado de la conversación.
        properties_context: Propiedades verificadas del catálogo.
        user_message: No usado en el prompt — el mensaje va en el historial.
                      Mantenido para compatibilidad con la firma del engine.
    """
    return SYSTEM_PROMPT_ES.format(
        tenant_name=tenant_name,
        conversation_history=conversation_history or "Conversación iniciada.",
        properties_context=properties_context or "No hay propiedades en contexto aún.",
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — system_es.py\n")

    # Test 1: Prompt base contiene contexto Margarita
    assert "Isla de Margarita" in SYSTEM_PROMPT_ES
    assert "NUNCA inventes propiedades" in SYSTEM_PROMPT_ES
    assert "vista_al_mar" in SYSTEM_PROMPT_ES
    assert "frente_playa" in SYSTEM_PROMPT_ES
    assert "uso_vacacional" in SYSTEM_PROMPT_ES
    assert "CryptoCity" in SYSTEM_PROMPT_ES
    assert "Pampatar" in SYSTEM_PROMPT_ES
    print("✅ Prompt contiene contexto Margarita completo")

    # Test 2: No tiene {user_message} — fue eliminado del template
    assert "{user_message}" not in SYSTEM_PROMPT_ES
    print("✅ {user_message} eliminado del template — evita duplicación")

    # Test 3: Renderizado con variables
    rendered = get_system_prompt_es(
        tenant_name="Esparta Inmuebles",
        conversation_history="Usuario: Hola\nBot: ¡Bienvenido!",
        properties_context="2 propiedades en Pampatar",
    )
    assert "Esparta Inmuebles" in rendered
    assert "Usuario: Hola" in rendered
    assert "2 propiedades en Pampatar" in rendered
    print("✅ Renderizado con variables correcto")

    # Test 4: Defaults funcionan
    default = get_system_prompt_es()
    assert "Inmobiliaria Margarita" in default
    assert "Conversación iniciada." in default
    assert "No hay propiedades" in default
    print("✅ Defaults aplicados correctamente")

    # Test 5: user_message ignorado silenciosamente
    with_user = get_system_prompt_es(user_message="esto no debe aparecer en el prompt")
    assert "esto no debe aparecer" not in with_user
    print("✅ user_message ignorado sin error")

    # Test 6: Longitud razonable
    assert len(SYSTEM_PROMPT_ES) < 5000
    print(f"✅ Longitud: {len(SYSTEM_PROMPT_ES)} chars (~{len(SYSTEM_PROMPT_ES)//4} tokens estimados)")

    # Test 7: Contiene las 5 reglas absolutas
    assert "1." in SYSTEM_PROMPT_ES
    assert "5." in SYSTEM_PROMPT_ES
    print("✅ Las 5 reglas absolutas presentes")

    print("\n🎉 Todos los smoke tests pasaron ✅")
