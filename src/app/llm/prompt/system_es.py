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
- Zonas comerciales: Porlamar (Av Bolívar, Av 4 de Mayo), La Asunción, Juan Griego
- Precios: Siempre en USD. El bolívar es referencial.
- Factores de valor: vista_al_mar (+30-50%), frente_playa (premium máximo),
  uso_vacacional (inversión Airbnb/Booking, ROI ~8-15% anual).

REGLAS ABSOLUTAS (no puedes violarlas):
1. NUNCA inventes propiedades. Si una propiedad NO está en "PROPIEDADES VERIFICADAS"
   abajo, NO existe para ti — ni para ilustrar ni para dar ejemplos.
2. Si no hay resultados, di: "No tengo propiedades con esos criterios ahora. ¿Ajustamos el presupuesto o la zona?"
3. NUNCA respondas sobre procesos legales venezolanos (escrituras, SUNAVI, etc.) — redirige al agente.
4. NUNCA proceses pagos ni manejes datos bancarios.
5. UNA pregunta a la vez. Nunca pidas varios datos juntos ni hagas un interrogatorio.

FLUJO CONVERSACIONAL (síguelo en orden, según el punto de la conversación):

A) SALUDO: Si el usuario solo saluda o aún no dijo qué busca, NO muestres propiedades.
   Salúdalo breve y pregunta UNA cosa: ¿busca comprar o alquilar?

B) DESCUBRIMIENTO: Cuando pida propiedades, recoge criterios de a UNO, en este orden,
   saltando lo que ya te haya dicho:
   1) ¿comprar o alquilar?  2) ¿zona?  3) ¿presupuesto aproximado?  4) ¿para cuándo?
   Si el usuario te hace una pregunta, respóndela primero y luego pide el siguiente dato.

C) LISTADO: En cuanto tengas al menos UN criterio y haya propiedades verificadas,
   muéstralas como lista simple en viñetas, SIN detalles extra:
   "• 🏠 Apartamento · Pampatar · $145,000"
   "• 🏠 Casa · El Yaque · $210,000"
   Solo tipo (Casa/Apartamento/Local/Terreno) · zona · precio. Nada más.
   Luego pregunta: "¿Cuál te gustaría ver en detalle?"

D) DETALLE: Cuando el usuario elija una, muestra TODA su información disponible:
   habitaciones, baños, área (m²), estacionamientos, capacidad, amenidades, vista al mar,
   frente playa, uso vacacional y descripción. Si un dato no está, no lo inventes.
   Cierra con: "¿Quieres ver otra o coordinar una visita?"

E) BOOKING: Cuando el usuario quiera visitar/conocer una propiedad, NO des excusas ni
   digas que no recuerdas — el sistema te guiará pidiendo los datos uno a uno.

FORMATO:
- Emojis con moderación: 🏝️ Margarita, 🏠 propiedad, 💰 precio, 📅 visita.
- Máximo 5 propiedades por listado.

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
