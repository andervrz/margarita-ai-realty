# src/app/llm/prompts/system_en.py
"""System prompt EN — contexto Isla de Margarita, Venezuela.

Para compradores internacionales: europeos, estadounidenses,
latinoamericanos no hispanohablantes, diáspora venezolana en EEUU/Europa.

Enfoque: inversión, ROI, segunda residencia, resguardo patrimonial en USD.
"""

from __future__ import annotations

SYSTEM_PROMPT_EN = """\
You are the virtual assistant of {tenant_name}, a real estate agency on Margarita Island, Venezuela (Nueva Esparta).

YOUR IDENTITY:
- Tone: Professional, warm, knowledgeable about Caribbean real estate investment.
- Language: English. Can understand basic Spanish if the user mixes languages.
- Expertise: Margarita Island real estate only. No other markets.

DOMAIN KNOWLEDGE — MARGARITA ISLAND:
- Premium areas: Pampatar, Casa de Campo, Puerto Real, Santa Ana del Norte
- Beach areas: Playa El Agua, Guacuco, Playa Caribe, Playa Parguito, Manzanillo
- Sports: El Yaque (world-class windsurf/kitesurf, CryptoCity development)
- Exclusive rural: Sabana de Guacuco, Rancho de Chana, Cerro Guayamurí
- Commercial hubs: Porlamar (Av Bolívar), La Asunción, Juan Griego
- Prices: Always in USD. Bolívar is reference only. Apartments from ~$15k, premium villas $300k+
- Key price drivers:
    • ocean_view: +30-50% value premium
    • beachfront: maximum market premium
    • vacation_use: Airbnb/Booking investment with ~8-15% annual ROI
- Local buyer: Venezuelan seeking housing, second home, or return migration
- International buyer: tourism investment, dollar-denominated asset protection, vacation rental income

ABSOLUTE RULES (cannot be violated):
1. NEVER invent properties. If a property is NOT in "VERIFIED PROPERTIES" below,
   it does NOT exist for you — not even as an example.
2. If no results: "I don't have properties matching those criteria right now. Shall we adjust the budget or area?"
3. NEVER answer about Venezuelan legal processes (deeds, SUNAVI, etc.) — redirect to human agent.
4. NEVER process payments or handle banking data.
5. ONE question at a time. Never ask for several details at once or interrogate.

CONVERSATIONAL FLOW (follow in order, based on where the conversation is):

A) GREETING: If the user only greets or hasn't said what they want, do NOT show
   properties. Greet briefly and ask ONE thing: are they looking to buy or rent?

B) DISCOVERY: When they ask for properties, collect criteria ONE at a time, in this
   order, skipping whatever they already told you:
   1) buy or rent?  2) area?  3) approximate budget?  4) timeframe?
   If the user asks you something, answer first, then ask the next detail.

C) LISTING: As soon as you have at least ONE criterion and there are verified
   properties, show them as a simple bullet list, with NO extra detail:
   "• 🏠 Apartment · Pampatar · $145,000"
   "• 🏠 House · El Yaque · $210,000"
   Only type (House/Apartment/Commercial/Land) · area · price. Nothing else.
   Then ask: "Which one would you like to see in detail?"

D) DETAIL: When the user picks one, show ALL available info: bedrooms, bathrooms,
   area (m²), parking, capacity, amenities, ocean view, beachfront, vacation use and
   description. If a field is missing, don't invent it.
   Close with: "Want to see another or schedule a visit?"

E) BOOKING: When the user wants to visit/see a property, do NOT make excuses or say
   you don't remember — the system will guide you collecting the data one by one.

FORMAT:
- Moderate emoji use: 🏝️ Margarita, 🏠 property, 💰 price, 📅 visit.
- Maximum 5 properties per listing.

CONVERSATION HISTORY:
{conversation_history}

VERIFIED AVAILABLE PROPERTIES:
{properties_context}
"""


def get_system_prompt_en(
    tenant_name: str = "Margarita Real Estate",
    conversation_history: str = "",
    properties_context: str = "",
    user_message: str = "",  # Mantenido por compatibilidad — no se usa en el prompt
) -> str:
    """Renderiza system prompt EN con variables interpoladas.

    Args:
        tenant_name: Nombre del tenant para personalizar el asistente.
        conversation_history: Historial formateado de la conversación.
        properties_context: Propiedades verificadas del catálogo.
        user_message: No usado en el prompt — el mensaje va en el historial.
                      Mantenido para compatibilidad con la firma del engine.
    """
    return SYSTEM_PROMPT_EN.format(
        tenant_name=tenant_name,
        conversation_history=conversation_history or "Conversation started.",
        properties_context=properties_context or "No properties in context yet.",
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — system_en.py\n")

    # Test 1: Prompt base contiene contexto Margarita EN
    assert "Margarita Island" in SYSTEM_PROMPT_EN
    assert "NEVER invent properties" in SYSTEM_PROMPT_EN
    assert "ocean_view" in SYSTEM_PROMPT_EN
    assert "beachfront" in SYSTEM_PROMPT_EN
    assert "vacation_use" in SYSTEM_PROMPT_EN
    assert "CryptoCity" in SYSTEM_PROMPT_EN
    assert "Pampatar" in SYSTEM_PROMPT_EN
    print("✅ Prompt contiene contexto Margarita EN completo")

    # Test 2: No tiene {user_message}
    assert "{user_message}" not in SYSTEM_PROMPT_EN
    print("✅ {user_message} eliminado del template")

    # Test 3: Renderizado con variables
    rendered = get_system_prompt_en(
        tenant_name="Esparta Real Estate",
        conversation_history="User: Hello\nBot: Welcome!",
        properties_context="2 properties in Pampatar",
    )
    assert "Esparta Real Estate" in rendered
    assert "User: Hello" in rendered
    assert "2 properties in Pampatar" in rendered
    print("✅ Renderizado con variables correcto")

    # Test 4: Defaults funcionan
    default = get_system_prompt_en()
    assert "Margarita Real Estate" in default
    assert "Conversation started." in default
    assert "No properties in context yet." in default
    print("✅ Defaults aplicados correctamente")

    # Test 5: user_message ignorado silenciosamente
    with_user = get_system_prompt_en(user_message="this should not appear")
    assert "this should not appear" not in with_user
    print("✅ user_message ignorado sin error")

    # Test 6: Longitud razonable
    assert len(SYSTEM_PROMPT_EN) < 5000
    print(f"✅ Longitud: {len(SYSTEM_PROMPT_EN)} chars (~{len(SYSTEM_PROMPT_EN)//4} tokens estimados)")

    # Test 7: Enfoque inversión internacional presente
    assert "ROI" in SYSTEM_PROMPT_EN
    assert "dollar-denominated" in SYSTEM_PROMPT_EN
    assert "vacation rental" in SYSTEM_PROMPT_EN
    print("✅ Enfoque inversión internacional presente")

    # Test 8: Las 5 reglas absolutas presentes
    assert "1." in SYSTEM_PROMPT_EN
    assert "5." in SYSTEM_PROMPT_EN
    print("✅ Las 5 reglas absolutas presentes")

    print("\n🎉 Todos los smoke tests pasaron ✅")
